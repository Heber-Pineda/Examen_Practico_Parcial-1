"""Contrato Ollama y asistente aislados: sin servidor Ollama, SQL ni HTTP reales."""
import asyncio
from datetime import date
from decimal import Decimal
import io
import json
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch
from urllib.error import HTTPError, URLError
from urllib.request import ProxyHandler

from backend.config import ApiSettings
from backend.domain import SalesFilters
from backend.repository import ApiProblem
from generative_ai.assistant import AssistantService, PLAN_SCHEMA, SELECT_SCHEMA
from generative_ai.provider import (NoRedirect, OllamaProvider, OpenAIProvider, ProviderSettings,
                                   create_provider, ollama_endpoint)

MODEL = "qwen2.5:3b-instruct"
PUB = {"id": "10bc721a-eeb1-4778-b494-8f520d2464cf", "published_at_utc": "2026-11-15T00:00:00Z"}
TOKEN = "isolated-ollama-test-token-0123456789abcdef"


def plan(**changes):
    return {"intent": "metrics", "metrics": ["extended_amount"], "date_from": None, "date_to": None,
            "customer_query": None, "product_query": None, "customer_role": "buyer", **changes}


def reply(value, **changes):
    result = {"model": MODEL, "done": True, "done_reason": "stop", "message": {
        "role": "assistant", "content": json.dumps(value, ensure_ascii=False)}}
    return io.BytesIO(json.dumps({**result, **changes}).encode("utf-8"))


def analytics_fixture():
    analytics, reader = Mock(), Mock()
    reader.query.return_value = [{"line_count": 2, "missing_base": 0, "base_sum": Decimal("100"),
        "extended_sum": Decimal("115.25"), "profit_sum": Decimal("25"), "invoice_count": 1, "units": 4}]
    analytics.coverage.return_value = {"date_from": "2013-01-02", "date_to": "2016-05-31", "observed_months": ["2016-05"]}
    analytics.consistent.side_effect = lambda operation, expected=None: {"publication": PUB, **operation(reader)}
    return analytics, reader


class OllamaConfigurationTests(unittest.TestCase):
    def test_factory_selects_provider_and_local_model_needs_no_openai_key(self):
        config = ProviderSettings.from_env({"GENAI_PROVIDER": "ollama", "GENAI_MODEL": MODEL})
        self.assertTrue(config.configured)
        self.assertEqual(config.timeout, 60)
        self.assertIsInstance(create_provider(config, Mock()), OllamaProvider)
        cloud = ProviderSettings.from_env({"GENAI_PROVIDER": "openai", "GENAI_MODEL": "gpt-4.1-mini", "OPENAI_API_KEY": "test-secret"})
        self.assertIsInstance(create_provider(cloud, Mock()), OpenAIProvider)
        self.assertEqual(cloud.timeout, 15)
        self.assertFalse(ProviderSettings(provider="openai", model="gpt-4.1-mini").configured)

    def test_timeout_and_generation_caps_remain_bounded_for_both_providers(self):
        self.assertTrue(ProviderSettings(provider="ollama", model=MODEL, timeout=120).configured)
        for provider, timeout in (("ollama", 121), ("ollama", 0), ("openai", 31)):
            with self.assertRaises(ValueError):
                ProviderSettings(provider=provider, timeout=timeout)
        for count in (199, 1601, 900.5):
            with self.assertRaises(ValueError):
                ProviderSettings(provider="ollama", max_output_tokens=count)

    def test_loopback_url_normalization_avoids_dns_and_allows_configured_ports(self):
        for source, expected in (("http://127.0.0.1:11434", "http://127.0.0.1:11434/api/chat"),
                                 ("http://localhost:12000/", "http://127.0.0.1:12000/api/chat"),
                                 ("http://[::1]:11434/", "http://[::1]:11434/api/chat")):
            self.assertEqual(ollama_endpoint(source), expected)

    def test_non_loopback_credentials_paths_and_ambiguous_addresses_are_rejected(self):
        invalid = ["http://example.com:11434", "http://192.168.1.2:11434", "http://0.0.0.0:11434",
                   "http://[::]:11434", "https://127.0.0.1:11434", "http://user:secret@127.0.0.1:11434",
                   "http://@127.0.0.1:11434", "http://localhost:11434/api/chat", "http://localhost:11434?x=1",
                   "http://localhost:11434/#other", "http://localhost:0", "http://localhost:65536",
                   "http://localhost:invalid", "http://127.0.0.1\\@evil.test", "http://2130706433:11434",
                   "http://127.0.0.1\n:11434", "http://[::1%25eth0]:11434"]
        for address in invalid:
            with self.subTest(address=address), self.assertRaises(ValueError):
                ProviderSettings(provider="ollama", model=MODEL, ollama_base_url=address)

    def test_invalid_optional_ollama_configuration_does_not_break_other_modules(self):
        with patch.dict("os.environ", {"GENAI_PROVIDER": "ollama", "GENAI_MODEL": MODEL, "OLLAMA_BASE_URL": "http://remote.example"}):
            assistant = AssistantService(Mock())
            self.assertEqual(assistant.status()["status"], "NOT_CONFIGURED")
            self.assertTrue(assistant.configuration_error)


class OllamaTransportTests(unittest.TestCase):
    def setUp(self):
        self.settings = ProviderSettings(provider="ollama", model=MODEL, timeout=60, api_key="isolated-cloud-key")

    def test_chat_contract_uses_schema_no_stream_and_never_sends_cloud_credentials(self):
        transport = Mock(return_value=reply(plan()))
        result = OllamaProvider(self.settings, transport).generate("Contrato autorizado", {"question": "Ventas"}, PLAN_SCHEMA, "plan")
        self.assertEqual(result, plan())
        request = transport.call_args.args[0]
        body = json.loads(request.data)
        self.assertEqual(request.full_url, "http://127.0.0.1:11434/api/chat")
        self.assertEqual(request.get_method(), "POST")
        self.assertEqual(body["model"], MODEL)
        self.assertIs(body["stream"], False)
        self.assertEqual(body["format"], PLAN_SCHEMA)
        self.assertEqual(body["options"], {"temperature": 0, "num_predict": 900})
        self.assertEqual([m["role"] for m in body["messages"]], ["system", "user"])
        self.assertIn('"additionalProperties": false', body["messages"][0]["content"])
        self.assertIsNone(request.get_header("Authorization"))
        self.assertNotIn("isolated-cloud-key", request.data.decode() + repr(self.settings))
        self.assertNotIn("tools", body)
        self.assertEqual(transport.call_args.kwargs["timeout"], 60)

    def test_default_transport_disables_proxies_and_redirects(self):
        with patch("generative_ai.provider.build_opener") as build:
            OllamaProvider(self.settings)
            handlers = build.call_args.args
            proxy = next(item for item in handlers if isinstance(item, ProxyHandler))
            redirect = next(item for item in handlers if isinstance(item, NoRedirect))
            self.assertEqual(proxy.proxies, {})
            self.assertIsNone(redirect.redirect_request(None, None, 302, "redirect", {}, "http://remote.example"))
            build.return_value.open.assert_not_called()

    def test_http_failures_are_safe_and_do_not_fall_back_to_openai(self):
        for status, code in ((404, "AI_MODEL_NOT_AVAILABLE"), (302, "AI_PROVIDER_REDIRECT"),
                             (500, "AI_PROVIDER_UNAVAILABLE"), (504, "AI_PROVIDER_TIMEOUT")):
            transport = Mock(side_effect=HTTPError("http://127.0.0.1:11434", status, "private-response", {}, None))
            with self.subTest(status=status), self.assertRaises(ApiProblem) as caught:
                OllamaProvider(self.settings, transport).generate("test", {}, PLAN_SCHEMA, "test")
            self.assertEqual(caught.exception.code, code)
            self.assertNotIn("private-response", str(caught.exception))
            transport.assert_called_once()

    def test_timeout_and_disconnection_are_distinguished(self):
        for failure, code in ((TimeoutError(), "AI_PROVIDER_TIMEOUT"),
                              (URLError(TimeoutError()), "AI_PROVIDER_TIMEOUT"),
                              (URLError(ConnectionRefusedError()), "AI_PROVIDER_UNAVAILABLE")):
            with self.subTest(code=code), self.assertRaises(ApiProblem) as caught:
                OllamaProvider(self.settings, Mock(side_effect=failure)).generate("test", {}, PLAN_SCHEMA, "test")
            self.assertEqual(caught.exception.code, code)

    def test_invalid_json_types_extra_sql_and_missing_fields_fail_closed(self):
        candidates = [[], {"intent": "metrics"}, plan(sql="DELETE FROM dw.FactVentas"),
                      plan(intent="arbitrary_sql"), plan(metrics=["invented_metric"]), plan(metrics=["units"] * 7),
                      plan(date_from=2026), plan(customer_role="other"), plan(customer_query={"sql": "SELECT"})]
        for candidate in candidates:
            with self.subTest(candidate=candidate), self.assertRaises(ApiProblem) as caught:
                OllamaProvider(self.settings, Mock(return_value=reply(candidate))).generate("test", {}, PLAN_SCHEMA, "test")
            self.assertEqual(caught.exception.code, "AI_PROVIDER_INVALID_RESPONSE")

    def test_truncation_tools_fences_duplicate_keys_and_oversized_output_are_rejected(self):
        invalid = [reply(plan(), done=False), reply(plan(), done_reason="length"), reply(plan(), model="different-model"),
                   reply(plan(), error="private error"),
                   reply(plan(), message={"role": "assistant", "content": json.dumps(plan()), "tool_calls": [{"function": "sql"}]}),
                   reply(plan(), message={"role": "assistant", "content": '```json\n{}\n```'}),
                   reply(plan(), message={"role": "assistant", "content": '{"intent":"metrics","intent":"unsupported"}'}),
                   reply(plan(), message={"role": "assistant", "content": '{"x": NaN}'}),
                   io.BytesIO(b'x' * 131073)]
        for response in invalid:
            with self.subTest(response=response), self.assertRaises(ApiProblem) as caught:
                OllamaProvider(self.settings, Mock(return_value=response)).generate("test", {}, PLAN_SCHEMA, "test")
            self.assertEqual(caught.exception.code, "AI_PROVIDER_INVALID_RESPONSE")

    def test_openai_and_ollama_clients_cannot_be_used_with_the_wrong_configuration(self):
        cloud = ProviderSettings(provider="openai", model="isolated-model", api_key="test-cloud-key")
        for provider, config in ((OpenAIProvider, self.settings), (OllamaProvider, cloud)):
            transport = Mock()
            with self.assertRaises(ApiProblem):
                provider(config, transport).generate("test", {}, PLAN_SCHEMA, "test")
            transport.assert_not_called()


class OllamaAssistantTests(unittest.TestCase):
    def service(self, *responses):
        analytics, reader = analytics_fixture()
        transport = Mock(side_effect=[reply(item) for item in responses])
        service = AssistantService(analytics, OllamaProvider(ProviderSettings(provider="ollama", model=MODEL, timeout=60), transport))
        return service, analytics, reader, transport

    def test_both_phases_render_only_verified_evidence_and_keep_publication_guard(self):
        service, analytics, reader, transport = self.service(plan(date_from="2016-05-01", date_to="2016-06-01"), {"evidence_ids": ["E1"]})
        answer = service.ask("Ventas de mayo 2016", SalesFilters(), expected_publication=PUB["id"])
        self.assertEqual(answer["status"], "ANSWERED")
        self.assertEqual(answer["provider"], "ollama")
        self.assertEqual(answer["model"], MODEL)
        self.assertEqual(answer["publication"], PUB)
        self.assertEqual(answer["paragraphs"], [r["statement"] for r in answer["evidence"]])
        self.assertIn("115.25 u.m.", answer["paragraphs"][0])
        self.assertIn(date(2016, 6, 1), reader.query.call_args.args[1])
        self.assertEqual(analytics.consistent.call_args.args[1], PUB["id"])
        self.assertEqual(transport.call_count, 2)
        self.assertEqual(json.loads(transport.call_args_list[1].args[0].data)["format"], SELECT_SCHEMA)
        self.assertEqual(service.status()["request_timeout_seconds"], 165)

    def test_invalid_plan_is_rejected_before_dw_access(self):
        service, analytics, reader, _ = self.service(plan(sql="SELECT password"))
        with self.assertRaises(ApiProblem):
            service.ask("Pregunta maliciosa", SalesFilters())
        analytics.consistent.assert_not_called()
        reader.query.assert_not_called()

    def test_unknown_evidence_never_becomes_an_answer(self):
        service, _, _, _ = self.service(plan(), {"evidence_ids": ["E999"]})
        with self.assertRaises(ApiProblem) as caught:
            service.ask("Ventas", SalesFilters())
        self.assertEqual(caught.exception.code, "AI_UNVERIFIED_ANSWER")

    def test_both_insufficient_data_cases_work_with_a_stubbed_local_model(self):
        for question, generated in (("Inventario actual", plan(intent="unsupported")),
                                    ("Ventas de enero de 2026", plan(date_from="2026-01-01", date_to="2026-02-01"))):
            service, _, reader, transport = self.service(generated)
            result = service.ask(question, SalesFilters())
            self.assertEqual(result["status"], "INSUFFICIENT_DATA")
            reader.query.assert_not_called()
            self.assertEqual(transport.call_count, 1)

    def test_selection_structure_is_validated_even_for_injected_test_providers(self):
        analytics, _ = analytics_fixture()
        provider = SimpleNamespace(settings=ProviderSettings(provider="ollama", model=MODEL),
                                   generate=Mock(side_effect=[plan(), {"evidence_ids": ["E1"], "answer": "Invented"}]))
        with self.assertRaises(ApiProblem) as caught:
            AssistantService(analytics, provider).ask("Ventas", SalesFilters())
        self.assertEqual(caught.exception.code, "AI_UNVERIFIED_ANSWER")


class OllamaHttpTests(unittest.IsolatedAsyncioTestCase):
    async def request(self, app, path, question=None, authorized=True):
        headers = [(b"host", b"testserver"), (b"content-type", b"application/json")]
        if authorized:
            headers.append((b"authorization", ("Bearer " + TOKEN).encode()))
        body = json.dumps({"question": question}).encode() if question is not None else b""
        scope = {"type": "http", "asgi": {"version": "3.0", "spec_version": "2.4"}, "http_version": "1.1",
                 "scheme": "http", "method": "POST" if question is not None else "GET", "path": path,
                 "raw_path": path.encode(), "query_string": b"", "root_path": "", "headers": headers,
                 "server": ("testserver", 80), "client": ("127.0.0.1", 12345)}
        messages = []; delivered = False
        async def receive():
            nonlocal delivered
            if not delivered:
                delivered = True
                return {"type": "http.request", "body": body, "more_body": False}
            await asyncio.Future()
        async def send(message):
            messages.append(message)
        await asyncio.wait_for(app(scope, receive, send), timeout=5)
        status = next(m["status"] for m in messages if m["type"] == "http.response.start")
        response = json.loads(b"".join(m.get("body", b"") for m in messages if m["type"] == "http.response.body"))
        return status, response

    async def test_factory_fastapi_auth_response_and_timeout_leave_health_available(self):
        from backend.app import create_app
        analytics, reader = analytics_fixture()
        local = {"GENAI_PROVIDER": "ollama", "GENAI_MODEL": MODEL, "GENAI_TIMEOUT": "60", "OLLAMA_BASE_URL": "http://localhost:11434"}
        with patch.dict("os.environ", local), patch("generative_ai.provider.build_opener") as build:
            build.return_value.open.side_effect = [reply(plan()), reply({"evidence_ids": ["E1"]}), TimeoutError()]
            app = create_app(ApiSettings(TOKEN), analytics)
            status, config = await self.request(app, "/api/v1/assistant/status")
            self.assertEqual((status, config["provider"]), (200, "ollama"))
            self.assertEqual(config["request_timeout_seconds"], 165)
            self.assertEqual((await self.request(app, "/api/v1/assistant/ask", "Ventas", authorized=False))[0], 401)
            build.return_value.open.assert_not_called()
            status, answer = await self.request(app, "/api/v1/assistant/ask", "Ventas")
            self.assertEqual((status, answer["status"]), (200, "ANSWERED"))
            self.assertEqual((await self.request(app, "/api/v1/assistant/ask", "Ventas"))[0], 503)
            self.assertEqual((await self.request(app, "/health"))[0], 200)


if __name__ == "__main__":
    unittest.main()
