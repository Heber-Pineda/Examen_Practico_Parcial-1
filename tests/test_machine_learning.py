"""Casos críticos con series sintéticas, sin SQL ni instalaciones.

Los dobles de estimador verifican protocolo y persistencia; no simulan evidencia
de precisión de Ridge. La integración sklearn se ejecuta solo si está instalado.
"""

from contextlib import contextmanager, redirect_stderr, redirect_stdout
from copy import deepcopy
import asyncio
from datetime import date
from decimal import Decimal
import importlib.util
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

from backend.config import WarehouseSettings
from backend.forecast import ForecastService
from backend.repository import ApiProblem
from machine_learning.__main__ import main
from machine_learning.artifacts import ArtifactError, ReportStore
from machine_learning.series import Month, add_months, read_snapshot, validate_rows
from machine_learning.training import features, money, scores, train

PUBLICATION = {"id": "10bc721a-eeb1-4778-b494-8f520d2464cf", "published_at_utc": "2026-11-15T00:00:00Z"}


def history():
    return [Month(add_months("2013-01", i), Decimal(1000 + i * 25), 1) for i in range(41)]


def snapshot(values):
    return {"publication": PUBLICATION, "through_month": "2016-05", "sha256": "0" * 64,
            "history": [value.as_dict() for value in values]}


class PrefixEstimator:
    """Doble lineal determinista para observar exactamente los prefijos recibidos."""
    def __init__(self, records):
        self.records = records

    def fit(self, x, y):
        self.records.append((deepcopy(x), list(y)))
        self.slope = (y[-1] - y[0]) / (x[-1][0] - x[0][0])
        self.intercept = y[0] - self.slope * x[0][0]

    def predict(self, x):
        return [self.intercept + self.slope * row[0] for row in x]


def fake_factory(records=None):
    records = [] if records is None else records
    return lambda alpha: PrefixEstimator(records)


def fake_dump(model, file):
    Path(file).write_bytes(b"SYNTHETIC TEST ARTIFACT; NOT A TRAINED SKLEARN MODEL")


class SeriesAndProtocolTests(unittest.TestCase):
    def test_extraction_is_read_only_bounded_and_pinned_to_publication(self):
        values = history()
        rows = [{"period": m.period, "sales_amount": m.amount, "line_count": 1, "missing_sales": 0} for m in values]
        reader = Mock(); reader.query.return_value = rows
        analytics = Mock()
        analytics.consistent.side_effect = lambda operation: {"publication": PUBLICATION, **operation(reader)}
        parsed, observed = read_snapshot(analytics, WarehouseSettings(), "2016-05")
        sql, parameters = reader.query.call_args.args
        self.assertIn("[dw].[FactVentas]", sql)
        self.assertIn("EsEliminadoFuente = 0", sql)
        self.assertIn("SUM(CAST(ExtendedPrice AS decimal(38,2)))", sql)
        self.assertEqual(parameters, (date(2016, 6, 1),))
        self.assertEqual(len(parsed), 41)
        self.assertEqual(observed["publication"], PUBLICATION)
        self.assertEqual(len(observed["sha256"]), 64)

    def test_missing_months_nulls_and_short_history_block_instead_of_imputation(self):
        rows = [{"period": m.period, "sales_amount": m.amount, "line_count": 1, "missing_sales": 0} for m in history()]
        for invalid in (rows[:35], rows[:10] + rows[11:], [rows[1], rows[0], *rows[2:]],
                        [*rows[:10], rows[9], *rows[11:]]):
            with self.assertRaises(ValueError):
                validate_rows(invalid, "2016-05")
        for amount, missing in ((None, 1), (Decimal("1"), 1), (1.5, 0), (Decimal("NaN"), 0)):
            invalid = deepcopy(rows); invalid[0].update(sales_amount=amount, missing_sales=missing)
            with self.assertRaises(ValueError):
                validate_rows(invalid, "2016-05")

    def test_split_and_every_fit_exclude_the_future_forecast_block(self):
        records = []
        _, result = train(history(), fake_factory(records))
        self.assertEqual(result["split"], {
            "train": {"from": "2013-01", "to": "2015-05", "months": 29},
            "validation": {"from": "2015-06", "to": "2015-11", "months": 6},
            "test": {"from": "2015-12", "to": "2016-05", "months": 6}})
        self.assertEqual([len(x) for x, y in records], [29, 32, 29, 32, 29, 32, 35, 38, 41])
        for x, y in records:
            self.assertEqual([row[0] for row in x], list(range(len(x))))
            self.assertEqual(y, [float(m.amount) for m in history()[:len(x)]])
        self.assertEqual([r["period"] for r in result["forecast"]], ["2016-06", "2016-07", "2016-08"])
        self.assertTrue(all(r["is_estimate"] for r in result["forecast"]))

    def test_test_targets_do_not_select_parameters_or_leak_into_first_test_prediction(self):
        values = history()
        _, original = train(values, fake_factory())
        altered = values[:35] + [Month(m.period, m.amount * 1000, 1) for m in values[35:]]
        _, changed = train(altered, fake_factory())
        for key in ("ridge_alpha", "selected_method", "validation_candidates"):
            self.assertEqual(original[key], changed[key])
        self.assertEqual(original["metrics"]["validation"], changed["metrics"]["validation"])
        self.assertEqual([r["ridge"] for r in original["metrics"]["test"]["predictions"][:3]],
                         [r["ridge"] for r in changed["metrics"]["test"]["predictions"][:3]])

    def test_baseline_is_selected_when_ridge_does_not_improve_validation(self):
        values = [Month(add_months("2013-01", i), Decimal(100 + i % 12), 1) for i in range(41)]
        _, result = train(values, fake_factory())
        self.assertEqual(result["selected_method"], "seasonal_naive")
        self.assertEqual(result["metrics"]["validation"]["seasonal_naive"]["mae"], 0)
        self.assertTrue(all(r["amount"] == r["seasonal_naive"] for r in result["forecast"]))

    def test_mae_rmse_and_signed_money(self):
        result = scores([100, 200], [110, 180])
        self.assertEqual(result["mae"], 15)
        self.assertAlmostEqual(result["rmse"], 250 ** 0.5)
        self.assertEqual(money(-1.235), "-1.24")
        with self.assertRaises(ValueError):
            scores([100], [float("nan")])

    def test_training_access_gate_and_plan_do_not_open_database(self):
        with patch("machine_learning.__main__.SqlRepository") as repository:
            with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
                self.assertEqual(main(["plan", "--through-month", "2016-05"]), 0)
                self.assertEqual(main(["train", "--through-month", "2016-05"]), 2)
            repository.assert_not_called()

    def test_training_does_not_need_http_secret_and_tls_remains_restricted(self):
        config = WarehouseSettings.from_env({"ML_ENVIRONMENT": "local", "ML_TRUST_SERVER_CERTIFICATE": "true"}, prefix="ML")
        self.assertIn("Encrypt=yes;TrustServerCertificate=yes;", config.connection_string())
        for env in ({"ML_TRUST_SERVER_CERTIFICATE": "true"},
                    {"ML_ENVIRONMENT": "local", "ML_TRUST_SERVER_CERTIFICATE": "true", "ML_SQL_SERVER": "remote"}):
            with self.assertRaises(ValueError):
                WarehouseSettings.from_env(env, prefix="ML")


class ArtifactAndApiTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.store = ReportStore(self.temp.name)
        self.model, self.evaluation = train(history(), fake_factory())

    def publish(self):
        return self.store.publish(self.model, self.evaluation, snapshot(history()), dump=fake_dump,
                                  versions={"test_fixture": "synthetic"})

    def test_atomic_publication_corruption_and_failed_training_preserve_previous_model(self):
        self.assertIsNone(self.store.load())
        original = self.publish()
        self.assertEqual(self.store.load()["model_version"], original["model_version"])
        with self.assertRaises(OSError):
            self.store.publish(self.model, self.evaluation, snapshot(history()), dump=Mock(side_effect=OSError), versions={})
        self.assertEqual(self.store.load()["model_version"], original["model_version"])
        file = Path(self.temp.name) / "versions" / original["model_version"] / "report.json"
        file.write_bytes(b"modified")
        with self.assertRaises(ArtifactError):
            self.store.load()

    def test_api_handles_not_trained_ready_stale_and_invalid_artifacts_without_loading_model(self):
        analytics = Mock()
        analytics.consistent.side_effect = lambda read, expected=None: {"publication": PUBLICATION, **read(None)}
        service = ForecastService(analytics, self.store)
        self.assertEqual(service.predictions()["status"], "NOT_TRAINED")
        analytics.consistent.assert_not_called()
        report = self.publish()
        self.assertEqual(service.predictions(PUBLICATION["id"])["status"], "READY")
        metrics = service.metrics(PUBLICATION["id"])
        self.assertEqual(metrics["model_version"], report["model_version"])
        self.assertIn("test", metrics["metrics"])
        self.assertNotIn("history", metrics)
        analytics.consistent.side_effect = lambda read, expected=None: {"publication": {**PUBLICATION, "id": "22222222-2222-4222-8222-222222222222"}, **read(None)}
        self.assertEqual(service.predictions()["status"], "STALE")
        (Path(self.temp.name) / "current.json").write_text("not-json", encoding="utf-8")
        with self.assertRaises(ApiProblem) as caught:
            service.predictions()
        self.assertEqual(caught.exception.code, "ML_ARTIFACT_INVALID")


@unittest.skipUnless(importlib.util.find_spec("fastapi"), "FastAPI no está instalado.")
class ForecastHttpTests(unittest.IsolatedAsyncioTestCase):
    async def get(self, app, path, authorized=True):
        resource, _, query = path.partition("?")
        headers = [(b"host", b"testserver")]
        if authorized:
            headers.append((b"authorization", b"Bearer isolated-ml-test-key-0123456789abcdef"))
        scope = {"type": "http", "asgi": {"version": "3.0", "spec_version": "2.4"},
                 "http_version": "1.1", "scheme": "http", "method": "GET", "path": resource,
                 "raw_path": resource.encode(), "query_string": query.encode(), "root_path": "",
                 "headers": headers, "server": ("testserver", 80), "client": ("127.0.0.1", 12345)}
        messages = []
        delivered = False
        async def receive():
            nonlocal delivered
            if not delivered:
                delivered = True
                return {"type": "http.request", "body": b"", "more_body": False}
            await asyncio.Future()
        async def send(message):
            messages.append(message)
        await asyncio.wait_for(app(scope, receive, send), timeout=5)
        status = next(message["status"] for message in messages if message["type"] == "http.response.start")
        body = json.loads(b"".join(message.get("body", b"") for message in messages if message["type"] == "http.response.body"))
        return status, body

    async def test_endpoints_authorize_validate_uuid_and_preserve_sales_api_without_database(self):
        from backend.app import create_app
        from backend.config import ApiSettings
        service, forecasts = Mock(), Mock()
        forecasts.predictions.return_value = {"status": "NOT_TRAINED", "message": "Isolated test"}
        forecasts.metrics.return_value = {"status": "NOT_TRAINED", "message": "Isolated test"}
        service.catalogs.return_value = {"test": "existing-dashboard"}
        app = create_app(ApiSettings("isolated-ml-test-key-0123456789abcdef"), service, forecasts)
        for resource, method in (("predictions", forecasts.predictions), ("metrics", forecasts.metrics)):
            path = "/api/v1/ml/" + resource
            self.assertEqual((await self.get(app, path, authorized=False))[0], 401)
            method.assert_not_called()
            status, body = await self.get(app, path + "?publication_id=" + PUBLICATION["id"].upper())
            self.assertEqual(status, 200)
            self.assertEqual(body["status"], "NOT_TRAINED")
            self.assertEqual(str(method.call_args.args[0]), PUBLICATION["id"])
            self.assertEqual((await self.get(app, path + "?publication_id=invalid"))[0], 422)
        self.assertEqual((await self.get(app, "/api/v1/catalogs")), (200, {"test": "existing-dashboard"}))


@unittest.skipUnless(importlib.util.find_spec("sklearn"), "Integración Ridge/joblib pendiente: scikit-learn no está instalado.")
class SklearnIntegrationTests(unittest.TestCase):
    def test_real_pipeline_is_reproducible_and_saved_model_predicts_same_values(self):
        import joblib
        model, result = train(history())
        _, repeated = train(history())
        self.assertEqual(result, repeated)
        self.assertEqual(model.named_steps["standardscaler"].n_samples_seen_, 41)
        with tempfile.TemporaryDirectory() as directory:
            store = ReportStore(directory)
            report = store.publish(model, result, snapshot(history()))
            loaded = joblib.load(Path(directory) / "versions" / report["model_version"] / "model.joblib")
            x = [features(row["period"], 41 + i) for i, row in enumerate(report["forecast"])]
            self.assertEqual([money(p) for p in loaded.predict(x)], [r["ridge"] for r in report["forecast"]])


if __name__ == "__main__":
    unittest.main()
