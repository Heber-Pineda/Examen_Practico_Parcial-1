"""Pruebas críticas con proveedor, DW y buzón aislados; nunca ejecutan SQL/HTTP externos."""
import asyncio
from contextlib import contextmanager, redirect_stderr, redirect_stdout
from datetime import date
from decimal import Decimal
import io
import json
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch
from urllib.error import HTTPError

from backend.config import ApiSettings
from backend.domain import SalesFilters
from backend.repository import ApiProblem
from generative_ai.assistant import AssistantService, entity_id
from generative_ai.provider import OpenAIProvider, ProviderSettings
from notifications.rules import evaluate
from notifications.worker import AlertWorker
from notifications.store import AlertStore

PUB = {"id": "10bc721a-eeb1-4778-b494-8f520d2464cf", "published_at_utc": "2026-11-15T00:00:00Z"}
TOKEN = "isolated-application-key-0123456789abcdef"


def plan(intent="metrics", **overrides):
    return {"intent": intent, "metrics": ["extended_amount"], "date_from": None, "date_to": None,
            "customer_query": None, "product_query": None, "customer_role": "buyer", **overrides}


class AnalyticsFixture:
    def __init__(self):
        self.reader = Mock()
        self.reader.query.return_value = [{"line_count": 2, "missing_base": 0, "base_sum": Decimal("100"),
            "extended_sum": Decimal("115.25"), "profit_sum": Decimal("25"), "invoice_count": 1, "units": 4}]
        self.calls = []

    def consistent(self, read, expected=None):
        self.calls.append(expected)
        return {"publication": PUB, **read(self.reader)}

    def coverage(self, reader):
        return {"date_from": "2013-01-02", "date_to": "2016-05-31", "observed_months": ["2016-05"]}


def provider_fixture(*answers):
    return SimpleNamespace(settings=ProviderSettings(model="isolated-model", api_key="isolated-secret"), generate=Mock(side_effect=list(answers)))


class AssistantTests(unittest.TestCase):
    def test_physical_inventory_is_refused_even_if_the_model_would_return_sales(self):
        analytics = Mock()
        provider = provider_fixture(plan())
        result = AssistantService(analytics, provider).ask(
            "¿Cuál es el inventario físico disponible actualmente de cada producto?", SalesFilters())
        self.assertEqual(result["status"], "INSUFFICIENT_DATA")
        self.assertEqual(result["evidence"], [])
        self.assertEqual(result["paragraphs"], [])
        provider.generate.assert_not_called()
        analytics.consistent.assert_not_called()
        analytics.catalogs.assert_not_called()

    def test_acceptance_http_diagnostic_preserves_status_and_known_api_code_only(self):
        from generative_ai.__main__ import http_error_details
        body = json.dumps({"error": {"code": "AI_PROVIDER_TIMEOUT", "message": "isolated-secret"}}).encode()
        error = HTTPError("http://isolated-secret.invalid", 503, "isolated-secret", {}, io.BytesIO(body))
        details = http_error_details(error)
        self.assertEqual(details, {"error_type": "HTTPError", "http_status": 503, "error_code": "AI_PROVIDER_TIMEOUT"})
        self.assertNotIn("isolated-secret", json.dumps(details))

    def test_acceptance_http_diagnostic_keeps_status_when_the_body_is_unsafe_or_invalid(self):
        from generative_ai.__main__ import http_error_details
        for body in (b"isolated-secret", b'{"error":{"code":"isolated-secret"}}',
                     b'{"error":{"code":[]}}', b'{}', b'"' + b"x" * 9000, None):
            with self.subTest(body=body[:40] if body else None):
                error = HTTPError("http://example.invalid", 503, "private", {}, io.BytesIO(body) if body is not None else None)
                self.assertEqual(http_error_details(error), {"error_type": "HTTPError", "http_status": 503})

    def test_invalid_optional_provider_configuration_does_not_break_application(self):
        with patch.dict("os.environ", {"GENAI_TIMEOUT": "invalid"}):
            service = AssistantService(Mock())
            self.assertEqual(service.status()["status"], "NOT_CONFIGURED")

    def test_missing_provider_is_pending_without_simulating_or_querying(self):
        analytics = Mock()
        service = AssistantService(analytics, OpenAIProvider(ProviderSettings()))
        self.assertEqual(service.ask("Ventas", SalesFilters())["status"], "NOT_CONFIGURED")
        analytics.consistent.assert_not_called()

    def test_provider_uses_real_https_contract_without_exposing_key_or_arbitrary_tools(self):
        response = {"status": "completed", "output": [{"type": "message", "content": [{"type": "output_text", "text": '{"evidence_ids":["E1"]}'}]}]}
        transport = Mock(return_value=io.BytesIO(json.dumps(response).encode()))
        settings = ProviderSettings(model="isolated-model", api_key="isolated-secret")
        provider = OpenAIProvider(settings, transport)
        result = provider.generate("Only evidence", {"question": "Sales"}, {"type": "object"}, "test_schema")
        self.assertEqual(result["evidence_ids"], ["E1"])
        request = transport.call_args.args[0]
        payload = json.loads(request.data)
        self.assertEqual(request.full_url, "https://api.openai.com/v1/responses")
        self.assertFalse(payload["store"])
        self.assertTrue(payload["text"]["format"]["strict"])
        self.assertNotIn("tools", payload)
        self.assertNotIn("isolated-secret", json.dumps(payload) + repr(settings))

    def test_provider_errors_do_not_reveal_credentials_or_body(self):
        transport = Mock(side_effect=HTTPError("https://example.invalid", 401, "isolated-secret", {}, None))
        with self.assertRaises(ApiProblem) as caught:
            OpenAIProvider(ProviderSettings(model="test", api_key="isolated-secret"), transport).generate("test", {}, {}, "test")
        self.assertNotIn("isolated-secret", str(caught.exception))
        self.assertEqual(caught.exception.code, "AI_PROVIDER_AUTH")

    def test_answer_has_exact_dw_amount_period_and_publication_and_only_approved_sql(self):
        analytics = AnalyticsFixture()
        provider = provider_fixture(plan(date_from="2016-05-01", date_to="2016-06-01"), {"evidence_ids": ["E1"]})
        answer = AssistantService(analytics, provider).ask("Importe de mayo 2016", SalesFilters(), expected_publication=PUB["id"])
        self.assertEqual(answer["status"], "ANSWERED")
        self.assertEqual(answer["publication"], PUB)
        self.assertIn("115.25 u.m.", answer["paragraphs"][0])
        self.assertIn("2016-05-31", answer["paragraphs"][0])
        self.assertEqual(answer["paragraphs"], [r["statement"] for r in answer["evidence"]])
        sql, parameters = analytics.reader.query.call_args.args
        self.assertIn("[dw].[FactVentas]", sql)
        self.assertIn("EsEliminadoFuente = 0", sql)
        self.assertNotIn("Importe de mayo", sql)
        self.assertIn(date(2016, 6, 1), parameters)

    def test_unknown_evidence_is_rejected_instead_of_publishing_hallucination(self):
        service = AssistantService(AnalyticsFixture(), provider_fixture(plan(), {"evidence_ids": ["INVENTED"]}))
        with self.assertRaises(ApiProblem) as caught:
            service.ask("Ventas", SalesFilters())
        self.assertEqual(caught.exception.code, "AI_UNVERIFIED_ANSWER")

    def test_sql_field_or_unsupported_intent_never_reaches_repository(self):
        for response, expected in ((plan(sql="DELETE FROM dw.FactVentas"), "NEEDS_CLARIFICATION"),
                                   (plan("unsupported"), "INSUFFICIENT_DATA")):
            analytics = Mock()
            answer = AssistantService(analytics, provider_fixture(response)).ask("Instrucción no autorizada", SalesFilters())
            self.assertEqual(answer["status"], expected)
            analytics.consistent.assert_not_called()

    def test_future_period_is_insufficient_not_zero_sales(self):
        analytics = AnalyticsFixture()
        provider = provider_fixture(plan(date_from="2026-01-01", date_to="2026-02-01"))
        answer = AssistantService(analytics, provider).ask("Ventas enero 2026", SalesFilters())
        self.assertEqual(answer["status"], "INSUFFICIENT_DATA")
        analytics.reader.query.assert_not_called()
        self.assertEqual(provider.generate.call_count, 1)

    def test_entity_resolution_rejects_ambiguity_and_model_invented_names(self):
        rows = [{"id": 1, "name": "Cliente Uno"}, {"id": 2, "name": "Cliente Dos"}]
        self.assertEqual(entity_id("1", rows, "Ventas del cliente 1"), 1)
        for term, question in (("Cliente", "Ventas Cliente"), ("Cliente Uno", "Ventas generales")):
            with self.assertRaises(ValueError):
                entity_id(term, rows, question)
        with self.assertRaises(ValueError):
            entity_id("1", rows, "Ventas de 2015")

    def test_question_budget_is_bounded(self):
        service = AssistantService(Mock(), provider_fixture(*[plan("unsupported") for _ in range(12)]))
        for _ in range(12):
            service.ask("Inventario actual", SalesFilters())
        with self.assertRaises(ApiProblem) as caught:
            service.ask("Inventario actual", SalesFilters())
        self.assertEqual(caught.exception.status, 429)


class InMemoryOutbox:
    """Doble del contrato SQL; la concurrencia/procedimientos requieren validación real."""
    def __init__(self):
        self.events = {}; self.inbox = {}; self.evaluations = []

    def record(self, result, publication):
        self.evaluations.append(result)
        if result["dedup_key"]:
            self.events.setdefault(result["dedup_key"], {"event_id": result["dedup_key"], "lease_token": "test-lease", "attempts": 0, "state": "PENDING"})
        return {"status": result["status"]}

    def claim(self, max_attempts, retry_now):
        for event in self.events.values():
            if event["state"] == "PENDING" or event["state"] == "RETRY" and retry_now:
                if event["attempts"] >= max_attempts:
                    continue
                event["attempts"] += 1; event["state"] = "PROCESSING"
                return event
        return None

    def deliver(self, event):
        self.inbox.setdefault(event["event_id"], event.copy())
        event["state"] = "DELIVERED"
        return {"status": "DELIVERED"}

    def retry(self, event, code, max_attempts, delay):
        event["state"] = "FAILED" if event["attempts"] >= max_attempts else "RETRY"
        return {"status": event["state"]}


class AlertTests(unittest.TestCase):
    def setUp(self):
        self.rows = [{"period": "2016-05", "amount": Decimal("80.00")}, {"period": "2016-04", "amount": Decimal("100.00")}]

    def test_rule_trigger_no_trigger_missing_reference_and_forced_demo(self):
        actual = evaluate(self.rows, "2016-05")
        self.assertEqual(actual["status"], "TRIGGERED")
        self.assertEqual(actual["change_pct"], "-20.000000")
        self.assertEqual(evaluate(self.rows, "2016-05", Decimal("-30"))["status"], "NOT_TRIGGERED")
        self.assertEqual(evaluate(self.rows[:1], "2016-05")["status"], "INSUFFICIENT_DATA")
        self.assertEqual(evaluate(self.rows, "2016-05", mode="FORCED_DEMO_OFF")["status"], "NOT_TRIGGERED")
        self.assertTrue(evaluate(self.rows, "2016-05", mode="FORCED_DEMO_ON")["is_demo"])

    def test_duplicate_evaluation_and_retry_deliver_only_one_inbox_item(self):
        store = InMemoryOutbox()
        result = evaluate(self.rows, "2016-05")
        store.record(result, PUB); store.record(result, {**PUB, "id": "different-publication"})
        self.assertEqual(len(store.events), 1)
        worker = AlertWorker(Mock(), store)
        self.assertEqual(worker.deliver(1, simulate_failure=True)[0]["status"], "RETRY")
        self.assertEqual(len(store.inbox), 0)
        self.assertEqual(worker.deliver(1, retry_now=True)[0]["status"], "DELIVERED")
        self.assertEqual(len(store.inbox), 1)
        self.assertEqual(worker.deliver(), [])

    def test_retry_limit_and_zero_reference_are_explicit(self):
        rows = [self.rows[0], {"period": "2016-04", "amount": Decimal("0")}]
        self.assertEqual(evaluate(rows, "2016-05")["status"], "INSUFFICIENT_DATA")
        store = InMemoryOutbox(); store.record(evaluate(self.rows, "2016-05"), PUB)
        worker = AlertWorker(Mock(), store, max_attempts=1)
        self.assertEqual(worker.deliver(1, simulate_failure=True)[0]["status"], "FAILED")
        self.assertEqual(worker.deliver(1, retry_now=True), [])

    def test_plan_and_missing_access_flag_never_construct_database_repository(self):
        from notifications.__main__ import main
        with patch("notifications.__main__.SqlRepository") as sql:
            with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
                self.assertEqual(main(["plan"]), 0)
                self.assertEqual(main(["run"]), 2)
            sql.assert_not_called()

    def test_checkpoint_conflict_is_reported_safely_by_restricted_writer(self):
        cursor = Mock(); cursor.fetchone.return_value = ("WideWorldImporters_Analytics",)
        cursor.execute.side_effect = [None, Exception("42000", "[SQL Server] Cambio de corte (51102); secret")]
        connection = Mock(); connection.cursor.return_value = cursor
        module = SimpleNamespace(connect=Mock(return_value=connection))
        with patch("notifications.store.importlib.import_module", return_value=module):
            with self.assertLogs("notifications", level="WARNING") as logs:
                with self.assertRaises(ApiProblem) as caught:
                    AlertStore(ApiSettings(TOKEN)).execute("read", (PUB["id"],))
        self.assertEqual(caught.exception.status, 409)
        self.assertNotIn("secret", str(caught.exception) + " ".join(logs.output))
        self.assertEqual(cursor.execute.call_args.args, ("EXEC ops.usp_notification_read ?;", PUB["id"]))

    def test_watch_recovers_from_a_failed_cycle_without_sleeping_in_test(self):
        from notifications.__main__ import main
        worker = Mock(); worker.evaluate.side_effect = [ApiProblem(503, "TEMPORARY", "temporary"), {"test": "recovered"}]
        worker.deliver.return_value = []
        with patch("notifications.__main__.AlertWorker", return_value=worker), patch("notifications.__main__.time.sleep", side_effect=[None, KeyboardInterrupt]):
            output = io.StringIO()
            with redirect_stdout(output):
                self.assertEqual(main(["watch", "--allow-database-access"]), 130)
        messages = [json.loads(line) for line in output.getvalue().splitlines()]
        self.assertEqual([m["status"] for m in messages], ["FAILED", "OK"])

    def test_acceptance_contains_ten_real_questions_and_two_without_data(self):
        cases = json.loads((Path(__file__).resolve().parents[1] / "generative_ai/acceptance_cases.json").read_text(encoding="utf-8"))
        self.assertEqual(len(cases), 10)
        self.assertEqual(sum(case["expected_status"] == "INSUFFICIENT_DATA" for case in cases), 2)


class HttpIntegrationTests(unittest.IsolatedAsyncioTestCase):
    async def request(self, app, path, method="GET", body=None, authorized=True):
        headers = [(b"host", b"testserver"), (b"content-type", b"application/json")]
        if authorized:
            headers.append((b"authorization", ("Bearer " + TOKEN).encode()))
        payload = json.dumps(body).encode() if body is not None else b""
        scope = {"type": "http", "asgi": {"version": "3.0", "spec_version": "2.4"}, "http_version": "1.1",
                 "scheme": "http", "method": method, "path": path, "raw_path": path.encode(), "query_string": b"",
                 "root_path": "", "headers": headers, "server": ("testserver", 80), "client": ("127.0.0.1", 1234)}
        messages = []; delivered = False
        async def receive():
            nonlocal delivered
            if not delivered:
                delivered = True
                return {"type": "http.request", "body": payload, "more_body": False}
            await asyncio.Future()
        async def send(message):
            messages.append(message)
        await asyncio.wait_for(app(scope, receive, send), timeout=5)
        status = next(m["status"] for m in messages if m["type"] == "http.response.start")
        value = json.loads(b"".join(m.get("body", b"") for m in messages if m["type"] == "http.response.body"))
        return status, value

    async def test_new_routes_require_auth_validate_input_and_keep_existing_api(self):
        from backend.app import create_app
        analytics, forecasts, assistant, notifications = Mock(), Mock(), Mock(), Mock()
        assistant.status.return_value = {"status": "NOT_CONFIGURED"}
        assistant.ask.return_value = {"status": "INSUFFICIENT_DATA"}
        notifications.list.return_value = {"status": "READY", "items": [], "unread_count": 0}
        notifications.mark_read.return_value = {"status": "READ"}
        analytics.catalogs.return_value = {"test": "existing-dashboard"}
        app = create_app(ApiSettings(TOKEN), analytics, forecasts, assistant, notifications)
        for path, method, body in (("/api/v1/assistant/status", "GET", None),
                                   ("/api/v1/assistant/ask", "POST", {"question": "Ventas"}),
                                   ("/api/v1/notifications", "GET", None),
                                   ("/api/v1/notifications/" + PUB["id"] + "/read", "POST", {})):
            self.assertEqual((await self.request(app, path, method, body, False))[0], 401)
            self.assertEqual((await self.request(app, path, method, body))[0], 200)
        for body in ({"question": "X" * 1001}, {"question": "Ventas", "sql": "SELECT *"},
                     {"question": "Ventas", "date_from": "2016-06-01", "date_to": "2016-05-01"}):
            self.assertEqual((await self.request(app, "/api/v1/assistant/ask", "POST", body))[0], 422)
        self.assertEqual((await self.request(app, "/api/v1/catalogs"))[1], {"test": "existing-dashboard"})


if __name__ == "__main__":
    unittest.main()
