"""Pruebas críticas con dobles en memoria; nunca abren una conexión ODBC."""

from collections import deque
from contextlib import contextmanager
from datetime import date, datetime
from decimal import Decimal
import importlib.util
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch
from uuid import UUID

from backend.config import ApiSettings, LOCAL_SERVER
from backend.domain import SalesFilters, common_months, empty_kpis, kpis, selected_months
from backend import queries
from backend.repository import ApiProblem, Reader, SqlRepository, unavailable
from backend.service import AnalyticsService

TOKEN = "isolated-test-token-0123456789-abcdef"
PUB_A = "11111111-1111-4111-8111-111111111111"
PUB_B = "22222222-2222-4222-8222-222222222222"


def measures(**values):
    return dict(line_count=2, missing_base=0, base_sum=Decimal("100.00"),
                extended_sum=Decimal("115.00"), units=4, invoice_count=1,
                profit_sum=Decimal("25.00"), **values)


class ScriptedReader:
    def __init__(self, steps):
        self.steps = deque(steps)

    def query(self, sql, parameters=()):
        expected, result = self.steps.popleft()
        assert expected in sql, (expected, sql)
        return result


class MemoryRepository:
    def __init__(self, *scripts):
        self.scripts = deque(scripts)

    @contextmanager
    def session(self):
        reader = ScriptedReader(self.scripts.popleft())
        yield reader
        assert not reader.steps, reader.steps


def publication(identifier=PUB_A):
    return ("etl_checkpoint", [{"execution_id": identifier, "published_at_utc": datetime(2026, 11, 14, 12)}])


class ConfigurationTests(unittest.TestCase):
    def test_tls_exception_is_explicit_and_restricted(self):
        settings = ApiSettings(TOKEN)
        self.assertIn("Encrypt=yes;TrustServerCertificate=no;", settings.connection_string())
        local = ApiSettings(TOKEN, environment="local", trust_certificate=True)
        self.assertIn("TrustServerCertificate=yes;", local.connection_string())
        for args in ({"trust_certificate": True},
                     {"environment": "local", "trust_certificate": True, "server": "remote\\SQL"}):
            with self.assertRaises(ValueError):
                ApiSettings(TOKEN, **args)

    def test_api_environment_cannot_inherit_etl_tls_or_target(self):
        config = ApiSettings.from_env({"API_ACCESS_TOKEN": TOKEN, "ETL_TRUST_SERVER_CERTIFICATE": "true",
                                       "ETL_TARGET_SERVER": "different-server"})
        self.assertEqual(config.server, LOCAL_SERVER)
        self.assertFalse(config.trust_certificate)
        self.assertEqual(config.environment, "production")

    def test_authentication_and_secret_repr(self):
        settings = ApiSettings(TOKEN)
        self.assertTrue(settings.permits(TOKEN))
        self.assertFalse(settings.permits(TOKEN + "x"))
        self.assertFalse(settings.permits("á" * 32))
        self.assertNotIn(TOKEN, repr(settings))
        for value in ("", "short", "has spaces" * 8):
            with self.assertRaises(ValueError):
                ApiSettings(value)

    def test_oltp_and_connection_string_injection_are_blocked(self):
        for name in ("WideWorldImporters", "master", "db;UID=admin"):
            with self.assertRaises(ValueError):
                ApiSettings(TOKEN, database=name)
        connection = ApiSettings(TOKEN, server="server};UID=intruder").connection_string()
        self.assertIn("SERVER={server}};UID=intruder};", connection)
        self.assertIn("Trusted_Connection=yes;", connection)


class MetricsAndSqlTests(unittest.TestCase):
    def test_six_metrics_keep_exact_decimal_and_ratio_of_totals(self):
        row = measures()
        row["base_sum"] = Decimal("9007199254740993.01")
        result = kpis(row)
        self.assertEqual(result["base_amount"], "9007199254740993.01")
        self.assertEqual(result["extended_amount"], "115.00")
        self.assertEqual(result["units"], 4)
        self.assertEqual(result["invoice_count"], 1)
        self.assertEqual(result["profit_amount"], "25.00")
        self.assertEqual(kpis(measures())["profit_margin"], "25.000000")

    def test_null_base_zero_and_empty_do_not_invent_margin(self):
        for base, missing, expected in ((0, 0, "DENOMINADOR_CERO"), (100, 1, "INCOMPLETO")):
            row = measures(); row.update(base_sum=base, missing_base=missing)
            result = kpis(row)
            self.assertIsNone(result["profit_margin"])
            self.assertEqual(result["margin_status"], expected)
            self.assertEqual(result["base_amount"], None if missing else "0.00")
            self.assertEqual(result["extended_amount"], "115.00")
        self.assertEqual(empty_kpis()["margin_status"], "SIN_DATOS")
        self.assertIsNone(empty_kpis()["profit_margin"])
        row = measures(); row["base_sum"] = 0.1
        with self.assertRaises(ValueError):
            kpis(row)

    def test_source_credit_signs_are_preserved(self):
        row = measures(); row.update(base_sum=Decimal("-100"), profit_sum=Decimal("-25"),
                                     extended_sum=Decimal("-115"), units=-4)
        self.assertEqual(kpis(row)["extended_amount"], "-115.00")
        self.assertEqual(kpis(row)["profit_margin"], "25.000000")

    def test_filter_parameters_include_zero_false_and_exclusive_date(self):
        filters = SalesFilters(date(2016, 5, 1), date(2016, 6, 1), 0, 5, 6, False)
        sql, values = queries.where(filters)
        self.assertIn("f.[InvoiceDate] < ?", sql)
        self.assertEqual(values, (date(2016, 5, 1), 20160501, date(2016, 6, 1), 20160601, 0, 5, 6, False))
        self.assertEqual(sql.count("?"), len(values))
        self.assertIn("EsEliminadoFuente = 0", sql)
        for bad in ({"customer_id": "1; DROP TABLE x"}, {"stock_item_id": True},
                    {"date_from": "2016-01-01"}, {"is_credit_note": "false"},
                    {"date_from": date(2016, 2, 1), "date_to": date(2016, 1, 1)}):
            with self.assertRaises(ValueError):
                SalesFilters(**bad)

    def test_aggregation_does_not_duplicate_or_filter_old_publications(self):
        sql, _ = queries.aggregate(SalesFilters())
        self.assertIn("COUNT_BIG(DISTINCT f.InvoiceID)", sql)
        self.assertIn("decimal(38,2)", sql)
        self.assertNotIn("JOIN", sql)
        self.assertNotIn("publication_id", sql)
        bill_to, values = queries.ranking(SalesFilters(), "customer", "bill_to")
        self.assertIn("d.ClienteKey = f.ClienteFacturacionKey", bill_to)
        self.assertIn("f.BillToCustomerID AS id", bill_to)
        self.assertEqual(values, (8,))

    def test_pagination_and_sort_are_bounded_and_whitelisted(self):
        sql, values = queries.details(SalesFilters(stock_item_id=7), 3, 25, "date_desc")
        self.assertEqual(values, (7, 50, 25))
        self.assertIn("InvoiceLineID DESC", sql)
        self.assertEqual(sql.count("?"), len(values))
        for page, size in ((0, 25), (1, 101)):
            with self.assertRaises(ValueError):
                queries.details(SalesFilters(), page, size, "date_desc")
        with self.assertRaises(KeyError):
            queries.details(SalesFilters(), 1, 25, "InvoiceID; DROP TABLE x")

    def test_historical_coverage_and_equivalent_months(self):
        coverage = {"date_from": "2013-01-02", "date_to": "2014-03-31",
                    "observed_months": ["2013-01", "2013-03", "2014-01"]}
        self.assertEqual(common_months(coverage, SalesFilters()), [1])
        self.assertEqual(selected_months(coverage, SalesFilters(date_to=date(2013, 1, 2))), [])
        self.assertEqual(selected_months(coverage, SalesFilters(date_to=date(2013, 3, 1))), ["2013-01", "2013-02"])
        self.assertEqual(selected_months(coverage, SalesFilters(date_from=date(2026, 1, 1))), [])


class PublicationTests(unittest.TestCase):
    def test_publication_uuid_case_matches_parsed_request(self):
        identifier = "10bc721a-eeb1-4778-b494-8f520d2464cf"
        for current, expected in ((identifier.upper(), UUID(identifier)),
                                  (identifier, identifier.upper()),
                                  (UUID(identifier), identifier.upper())):
            with self.subTest(current=current, expected=expected):
                repo = MemoryRepository([publication(current), publication(current)])
                result = AnalyticsService(repo).consistent(lambda reader: {"kpis": empty_kpis()}, expected)
                self.assertEqual(result["publication"]["id"], identifier)
                self.assertIn("kpis", result)

    def test_publication_case_only_change_does_not_retry(self):
        identifier = "10bc721a-eeb1-4778-b494-8f520d2464cf"
        steps = [publication(identifier.upper()), publication(identifier)]
        work = Mock(return_value={"kpis": empty_kpis()})
        result = AnalyticsService(MemoryRepository(steps, steps)).consistent(work)
        self.assertEqual(result["publication"]["id"], identifier)
        work.assert_called_once()

    def test_publication_replaced_during_pinned_request_still_returns_409(self):
        identifier = "10bc721a-eeb1-4778-b494-8f520d2464cf"
        repo = MemoryRepository([publication(identifier.upper()), publication(PUB_B)], [publication(PUB_B)])
        work = Mock(return_value={"kpis": empty_kpis()})
        with self.assertRaises(ApiProblem) as caught:
            AnalyticsService(repo).consistent(work, UUID(identifier))
        self.assertEqual(caught.exception.status, 409)
        self.assertEqual(caught.exception.code, "PUBLICATION_CHANGED")
        work.assert_called_once()

    def test_publication_refresh_recovers_current_kpis_and_sales(self):
        identifier = "10bc721a-eeb1-4778-b494-8f520d2464cf"
        coverage = [
            ("MIN(InvoiceDate)", [{"date_from": date(2016, 5, 1), "date_to": date(2016, 5, 31)}]),
            ("SELECT DISTINCT", [{"period": "2016-05"}]),
        ]
        repo = MemoryRepository(
            [publication(identifier.upper())],  # El panel conserva un corte realmente anterior.
            [publication(identifier.upper()), ("DimCliente", []), ("DimProducto", []),
             *coverage, publication(identifier)],
            [publication(identifier.upper()), *coverage, ("COUNT_BIG(*)", [measures()]),
             ("AS month", [dict(year=2016, month=5, **measures())]),
             ("AS year", [dict(year=2016, **measures())]),
             ("DimProducto", []), ("DimCliente", []), publication(identifier)],
            [publication(identifier.upper()), ("AS total", [{"total": 2}]),
             ("OFFSET", [{"invoice_line_id": 1}]), publication(identifier)],
        )
        service = AnalyticsService(repo)
        with self.assertRaises(ApiProblem) as caught:
            service.overview(SalesFilters(), expected_publication=UUID(PUB_A))
        self.assertEqual(caught.exception.status, 409)
        # Actualizar consulta /catalogs sin el UUID obsoleto y reutiliza el vigente
        # en /overview y /sales, como el callback refresh de App.tsx.
        current = service.catalogs()["publication"]["id"]
        overview = service.overview(SalesFilters(), expected_publication=UUID(current))
        sales = service.details(SalesFilters(), 1, 25, "date_desc", UUID(current))
        self.assertEqual(current, identifier)
        self.assertEqual(overview["publication"], sales["publication"])
        self.assertEqual(overview["kpis"]["base_amount"], "100.00")
        self.assertEqual(overview["kpis"]["extended_amount"], "115.00")
        self.assertEqual(overview["kpis"]["units"], 4)
        self.assertEqual(overview["kpis"]["invoice_count"], 1)
        self.assertEqual(overview["kpis"]["profit_amount"], "25.00")
        self.assertEqual(overview["kpis"]["profit_margin"], "25.000000")
        self.assertEqual(sales["total"], 2)
        self.assertEqual(sales["items"][0]["invoice_line_id"], 1)

    def test_publication_change_retries_whole_result(self):
        repo = MemoryRepository([publication(), publication(PUB_B)], [publication(PUB_B), publication(PUB_B)])
        work = Mock(side_effect=[{"value": "old"}, {"value": "new"}])
        result = AnalyticsService(repo).consistent(work)
        self.assertEqual(result["value"], "new")
        self.assertEqual(result["publication"]["id"], PUB_B)
        self.assertTrue(result["publication"]["published_at_utc"].endswith("Z"))
        self.assertEqual(work.call_count, 2)

    def test_browser_publication_mismatch_requires_refresh(self):
        service = AnalyticsService(MemoryRepository([publication(PUB_B)]))
        work = Mock()
        with self.assertRaises(ApiProblem) as caught:
            service.consistent(work, PUB_A)
        self.assertEqual(caught.exception.status, 409)
        work.assert_not_called()

    def test_busy_or_unpublished_returns_safe_503(self):
        service = AnalyticsService(MemoryRepository(), 1)
        service.slots.acquire()
        with self.assertRaises(ApiProblem) as caught:
            service.consistent(Mock())
        self.assertEqual(caught.exception.code, "API_BUSY")
        service.slots.release()
        with self.assertRaises(ApiProblem) as caught:
            AnalyticsService(MemoryRepository([("etl_checkpoint", [])])).consistent(Mock())
        self.assertEqual(caught.exception.code, "DW_NOT_PUBLISHED")

    def test_overview_distinguishes_unknown_months_and_zero_filtered_sales(self):
        repo = MemoryRepository([
            publication(),
            ("MIN(InvoiceDate)", [{"date_from": date(2013, 1, 2), "date_to": date(2014, 1, 31)}]),
            ("SELECT DISTINCT", [{"period": p} for p in ["2013-01", "2013-03", "2014-01"]]),
            ("COUNT_BIG(*)", [measures()]),
            ("AS month", [dict(year=2013, month=1, **measures())]),
            ("AS year", [dict(year=2013, **measures())]),
            ("IN (?)", [dict(year=2013, **measures())]),
            ("DimProducto", []), ("DimCliente", []), publication(),
        ])
        result = AnalyticsService(repo).overview(SalesFilters(customer_id=3))
        monthly = {row["period"]: row for row in result["monthly"]}
        self.assertIsNone(monthly["2013-02"]["kpis"])
        self.assertFalse(monthly["2013-02"]["observed"])
        self.assertEqual(monthly["2013-03"]["kpis"]["extended_amount"], "0.00")
        self.assertEqual(result["annual"][1]["year"], 2014)
        self.assertEqual(result["annual"][1]["kpis"]["line_count"], 0)
        self.assertEqual(result["kpis"]["invoice_count"], 1)

    def test_detail_keeps_exact_money_and_filtered_count(self):
        row = {"invoice_line_id": 9, "invoice_date": date(2016, 5, 31), "base_amount": Decimal("0.10")}
        repo = MemoryRepository([publication(), ("AS total", [{"total": 26}]),
                                 ("OFFSET", [row]), publication()])
        result = AnalyticsService(repo).details(SalesFilters(), 2, 25, "date_desc", PUB_A)
        self.assertEqual(result["total_pages"], 2)
        self.assertEqual(result["items"][0]["base_amount"], "0.10")
        self.assertEqual(result["items"][0]["invoice_date"], "2016-05-31")


class ReadOnlyAdapterTests(unittest.TestCase):
    def test_connection_is_lazy_read_only_and_has_timeouts(self):
        config = ApiSettings(TOKEN)
        cursor = Mock()
        cursor.description = [("database_name",)]
        cursor.fetchall.return_value = [(config.database,)]
        connection = Mock(); connection.cursor.return_value = cursor
        module = SimpleNamespace(connect=Mock(return_value=connection))
        with patch("backend.repository.importlib.import_module", return_value=module):
            repository = SqlRepository(config)
            module.connect.assert_not_called()
            with repository.session() as reader:
                self.assertIsInstance(reader, Reader)
            module.connect.assert_called_once_with(config.connection_string(), readonly=True,
                                                   autocommit=True, timeout=5)
        self.assertEqual(connection.timeout, 15)
        connection.close.assert_called_once()

    def test_non_select_is_rejected_before_creating_cursor(self):
        connection = Mock()
        with self.assertRaises(ValueError):
            Reader(connection).query("DELETE FROM dw.FactVentas")
        connection.cursor.assert_not_called()

    def test_errors_hide_driver_message_credentials_and_sql(self):
        with self.assertLogs("analytics", level="WARNING") as logs:
            error = unavailable(Exception("08001", "PWD=secret;SERVER=private; SELECT secret"))
        self.assertEqual(error.status, 503)
        self.assertIn("08001", " ".join(logs.output))
        self.assertNotIn("secret", " ".join(logs.output) + str(error))
        self.assertNotIn("private", " ".join(logs.output) + str(error))


@unittest.skipUnless(importlib.util.find_spec("fastapi") and importlib.util.find_spec("httpx"),
                     "Pruebas HTTP: instalar dependencias de la API y httpx para ejecutarlas.")
class HttpContractTests(unittest.TestCase):
    def setUp(self):
        from fastapi.testclient import TestClient
        from backend.app import create_app
        self.service = Mock()
        self.client = TestClient(create_app(ApiSettings(TOKEN, environment="local"), self.service))

    def test_auth_denied_before_dw_and_health_does_not_connect(self):
        for headers in ({}, {"Authorization": "Bearer incorrect"}):
            response = self.client.get("/api/v1/catalogs", headers=headers)
            self.assertEqual(response.status_code, 401)
        self.assertEqual(self.client.get("/health").json()["database_checked"], False)
        self.service.catalogs.assert_not_called()

    def test_authorized_access_and_invalid_filters(self):
        headers = {"Authorization": "Bearer " + TOKEN}
        self.service.catalogs.return_value = {"test": "isolated"}
        response = self.client.get("/api/v1/catalogs", headers=headers)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers["Cache-Control"], "no-store")
        for path in ("sales?page_size=101", "sales?sort=bad", "overview?date_from=2016-05-01&date_to=2016-01-01"):
            self.assertEqual(self.client.get("/api/v1/" + path, headers=headers).status_code, 422)
        self.service.details.assert_not_called()
        self.service.overview.assert_not_called()


if __name__ == "__main__":
    unittest.main()
