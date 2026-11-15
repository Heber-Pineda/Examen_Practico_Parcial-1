"""Regresiones de períodos con proveedor y DW simulados, sin HTTP ni SQL reales.

Los importes son fixtures: mayo/histórico reproducen la evidencia del usuario;
los demás valores son sintéticos y no son resultados de consultas reales.
"""
from datetime import date
from decimal import Decimal
from types import SimpleNamespace
import unittest
from unittest.mock import Mock

from backend.domain import SalesFilters, calendar_months
from backend.repository import ApiProblem
from generative_ai.assistant import AssistantService
from generative_ai.periods import PeriodError, parse_period
from generative_ai.provider import ProviderSettings

MAY = (date(2016, 5, 1), date(2016, 6, 1))
APRIL = (date(2016, 4, 1), date(2016, 5, 1))
PUB = {"id": "10bc721a-eeb1-4778-b494-8f520d2464cf"}


def plan(**changes):
    return {"intent": "metrics", "metrics": ["extended_amount"], "date_from": None, "date_to": None,
            "customer_query": None, "product_query": None, "customer_role": "buyer", **changes}


class SalesFixture:
    def __init__(self, has_sales=True):
        self.coverage_value = {"date_from": "2013-01-02", "date_to": "2016-05-31",
                               "observed_months": list(calendar_months(date(2013, 1, 1), date(2016, 5, 31)))}
        self.reader = SimpleNamespace(query=Mock(side_effect=self.query))
        self.has_sales = has_sales
        self.bounds = []
        self.expected = None

    def consistent(self, read, expected=None):
        self.expected = expected
        return {"publication": PUB, **read(self.reader)}

    def coverage(self, reader):
        return self.coverage_value

    def query(self, sql, parameters):
        bounds = tuple(value for value in parameters if type(value) is date)
        self.bounds.append(bounds)
        amount = {MAY: "5704232.71", APRIL: "4000000.00"}.get(bounds, "198043439.45")
        row = {"line_count": 1 if self.has_sales else 0, "missing_base": 0, "base_sum": Decimal("100.00"),
               "extended_sum": Decimal(amount), "profit_sum": Decimal("25.00"), "invoice_count": 1, "units": 2}
        if " AS year" in sql:
            row.update(year=bounds[0].year, month=bounds[0].month)
        return [row]


def service_fixture(generated=None, ids=("E1",), provider_name="ollama", has_sales=True):
    analytics = SalesFixture(has_sales)
    settings = ProviderSettings(provider=provider_name, model="isolated-model", api_key="isolated-test-key")
    provider = SimpleNamespace(settings=settings, generate=Mock(side_effect=[
        plan() if generated is None else generated, {"evidence_ids": list(ids)}]))
    return AssistantService(analytics, provider), analytics, provider


class PeriodParserTests(unittest.TestCase):
    def test_explicit_months_years_and_inclusive_ranges(self):
        cases = [
            ("Importe de mayo de 2016", "single", (MAY,)),
            ("Importe de mayo 2016", "single", (MAY,)),
            ("Ventas en 2016, mayo", "single", (MAY,)),
            ("Ventas de 2016-05", "single", (MAY,)),
            ("Ventas de 05/2016", "single", (MAY,)),
            ("Ventas durante 2015", "single", ((date(2015, 1, 1), date(2016, 1, 1)),)),
            ("Ventas entre abril y mayo de 2016", "range", ((date(2016, 4, 1), date(2016, 6, 1)),)),
            ("Ventas del 2016-04-01 al 2016-05-31", "range", ((date(2016, 4, 1), date(2016, 6, 1)),)),
            ("Ventas del 1 de abril al 31 de mayo de 2016", "range", ((date(2016, 4, 1), date(2016, 6, 1)),)),
            ("Ventas de 2013-2015", "range", ((date(2013, 1, 1), date(2016, 1, 1)),)),
            ("Compara abril y mayo de 2016", "comparison", (APRIL, MAY)),
            ("Diferencia entre abril y mayo de 2016", "comparison", (APRIL, MAY)),
            ("Mayo de 2016 vs abril de 2016", "comparison", (MAY, APRIL)),
            ("Compara 2014 y 2015", "comparison", ((date(2014, 1, 1), date(2015, 1, 1)),
                                                     (date(2015, 1, 1), date(2016, 1, 1)))),
        ]
        for question, mode, expected in cases:
            with self.subTest(question=question):
                parsed = parse_period(question)
                self.assertEqual(parsed.mode, mode)
                self.assertEqual(tuple((w.start, w.until) for w in parsed.windows), expected)

    def test_ambiguous_invalid_or_relative_periods_never_become_history(self):
        for question in ("Ventas de mayo", "Ventas del último trimestre", "Ventas de 31/02/2016",
                         "Ventas de 2016-13", "Ventas entre mayo y abril de 2016", "Compara mayo de 2016",
                         "Ventas del 1 al 31 de mayo de 2016", "Ventas de abril y mayo de 2016",
                         "Ventas en 2016 o 2015", "Ventas desde mayo de 2016", "Ventas de 2015 a la actualidad",
                         "Ventas del año 2015 al año2016", "Ventas de mayo2016 y junio2017",
                         "Ventas el 2016-04-01 o el 2016-05-01"):
            with self.subTest(question=question), self.assertRaises(PeriodError):
                parse_period(question)

    def test_without_explicit_period_and_full_history_are_distinct(self):
        for question in ("Importe extendido", "Ventas del cliente 2015", "¿Qué período cubre el histórico?"):
            self.assertIsNone(parse_period(question))
        self.assertEqual(parse_period("Importe de todo el histórico").mode, "history")


class AssistantPeriodTests(unittest.TestCase):
    def test_may_overrides_null_historical_wrong_and_invented_model_dates_for_both_providers(self):
        for provider_name in ("openai", "ollama"):
            for first, until in ((None, None), ("2013-01-02", "2016-06-01"),
                                 ("2016-04-01", "2016-05-01"), ("2026-01-01", "2026-02-01")):
                with self.subTest(provider=provider_name, dates=(first, until)):
                    service, analytics, provider = service_fixture(plan(date_from=first, date_to=until), provider_name=provider_name)
                    result = service.ask("¿Cuál es el importe extendido de mayo de 2016?",
                                         SalesFilters(date(2013, 1, 1), date(2016, 6, 1)), expected_publication=PUB["id"])
                    self.assertEqual(result["status"], "ANSWERED")
                    self.assertEqual(analytics.bounds, [MAY])
                    self.assertIn("5704232.71 u.m.", result["paragraphs"][0])
                    self.assertNotIn("198043439.45", result["paragraphs"][0])
                    self.assertEqual(result["paragraphs"], [result["evidence"][0]["statement"]])
                    self.assertEqual((result["evidence"][0]["period_from"], result["evidence"][0]["period_to"]),
                                     ("2016-05-01", "2016-05-31"))
                    self.assertEqual(provider.generate.call_count, 2)
                    self.assertEqual(analytics.expected, PUB["id"])

    def test_dashboard_conflict_is_visible_and_non_date_filters_are_preserved(self):
        service, analytics, provider = service_fixture()
        result = service.ask("Importe extendido de mayo de 2016", SalesFilters(*APRIL, 7, 8, 9, False))
        self.assertEqual(analytics.bounds, [MAY])
        self.assertEqual(result["period_policy"], "QUESTION_OVERRIDES_DASHBOARD")
        self.assertIn("prevalecen", result["period_notice"])
        self.assertIn("dashboard", result["period_notice"])
        filters = result["effective_filters"]
        self.assertEqual((filters["customer_id"], filters["bill_to_customer_id"], filters["stock_item_id"], filters["is_credit_note"]),
                         (7, 8, 9, False))
        sql, parameters = analytics.reader.query.call_args.args
        self.assertEqual(parameters[-4:], (7, 8, 9, False))
        self.assertIn("[dw].[FactVentas]", sql)
        self.assertIn("EsEliminadoFuente = 0", sql)
        self.assertIn("f.[InvoiceDate] >= ?", sql)
        self.assertIn("f.[InvoiceDate] < ?", sql)
        self.assertNotIn("mayo", sql)
        self.assertEqual(provider.generate.call_args_list[0].args[1]["authoritative_period"]["requested_periods"][0]["date_from"], "2016-05-01")

    def test_no_explicit_period_keeps_dashboard_dates_and_ignores_model_dates(self):
        service, analytics, _ = service_fixture(plan(date_from="2013-01-01", date_to="2016-06-01"))
        result = service.ask("Importe extendido", SalesFilters(*MAY))
        self.assertEqual(analytics.bounds, [MAY])
        self.assertEqual(result["period_policy"], "DASHBOARD_OR_COVERAGE")

    def test_full_history_must_be_requested_and_keeps_customer_filter(self):
        service, analytics, _ = service_fixture(plan(date_from="2016-05-01", date_to="2016-06-01"))
        result = service.ask("Importe extendido de todo el histórico", SalesFilters(*MAY, customer_id=7))
        self.assertEqual(analytics.bounds, [(date(2013, 1, 2), date(2016, 6, 1))])
        self.assertEqual(result["effective_filters"]["customer_id"], 7)
        self.assertIn("todo el histórico", result["period_notice"])

    def test_april_may_comparison_queries_and_returns_both_periods_separately(self):
        for intent in ("metrics", "monthly"):
            with self.subTest(intent=intent):
                service, analytics, _ = service_fixture(plan(intent=intent), ids=("E1", "E2"))
                result = service.ask("Compara el importe extendido entre abril y mayo de 2016", SalesFilters(*MAY))
                self.assertEqual(analytics.bounds, [APRIL, MAY])
                self.assertEqual(result["status"], "ANSWERED")
                self.assertEqual(len(result["effective_periods"]), 2)
                self.assertIn("4000000.00", result["paragraphs"][0])
                self.assertIn("5704232.71", result["paragraphs"][1])
                self.assertEqual([(e["period_from"], e["period_to"]) for e in result["evidence"]],
                                 [("2016-04-01", "2016-04-30"), ("2016-05-01", "2016-05-31")])

    def test_model_may_not_select_only_one_side_of_a_comparison(self):
        service, _, _ = service_fixture(ids=("E1",))
        with self.assertRaises(ApiProblem) as caught:
            service.ask("Compara abril y mayo de 2016", SalesFilters())
        self.assertEqual(caught.exception.code, "AI_UNVERIFIED_ANSWER")

    def test_annual_and_range_filters_have_exact_exclusive_upper_bounds(self):
        for question, bounds in (("Importe extendido durante 2015", (date(2015, 1, 1), date(2016, 1, 1))),
                                 ("Importe extendido de abril a mayo de 2016", (date(2016, 4, 1), date(2016, 6, 1)))):
            with self.subTest(question=question):
                service, analytics, _ = service_fixture()
                result = service.ask(question, SalesFilters(*MAY))
                self.assertEqual(result["status"], "ANSWERED")
                self.assertEqual(analytics.bounds, [bounds])
                self.assertEqual(result["effective_periods"][0]["date_from"], bounds[0].isoformat())
                self.assertEqual(result["effective_periods"][0]["date_to"], bounds[1].isoformat())

    def test_future_incomplete_year_and_missing_comparison_period_do_not_use_partial_history(self):
        for question in ("Importe extendido de enero de 2026", "Importe extendido de 2016",
                         "Importe extendido de abril a junio de 2016", "Compara mayo y junio de 2016"):
            with self.subTest(question=question):
                service, analytics, provider = service_fixture()
                result = service.ask(question, SalesFilters(*MAY))
                self.assertEqual(result["status"], "INSUFFICIENT_DATA")
                analytics.reader.query.assert_not_called()
                self.assertEqual(result["evidence"], [])
                self.assertEqual(provider.generate.call_count, 1)

    def test_ambiguous_period_stops_before_provider_and_data_access(self):
        service, analytics, provider = service_fixture()
        result = service.ask("Importe extendido de mayo", SalesFilters(*MAY))
        self.assertEqual(result["status"], "NEEDS_CLARIFICATION")
        self.assertEqual(analytics.bounds, [])
        provider.generate.assert_not_called()

    def test_global_coverage_intent_cannot_replace_a_monthly_metric(self):
        service, analytics, provider = service_fixture(plan(intent="coverage"))
        result = service.ask("Importe extendido de mayo de 2016", SalesFilters())
        self.assertEqual(result["status"], "NEEDS_CLARIFICATION")
        analytics.reader.query.assert_not_called()
        self.assertEqual(provider.generate.call_count, 1)

    def test_empty_result_does_not_query_a_different_period_or_claim_zero(self):
        service, analytics, provider = service_fixture(has_sales=False)
        result = service.ask("Importe extendido de mayo de 2016", SalesFilters(customer_id=7))
        self.assertEqual(result["status"], "INSUFFICIENT_DATA")
        self.assertEqual(analytics.bounds, [MAY])
        self.assertEqual(result["evidence"], [])
        self.assertEqual(provider.generate.call_count, 1)


if __name__ == "__main__":
    unittest.main()
