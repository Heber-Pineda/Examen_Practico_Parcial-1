"""Entrenamiento manual, independiente de HTTP y sin acceso implícito al DW."""

import argparse
from datetime import datetime, timezone
import importlib.util
import json
import sys

from backend.config import WarehouseSettings
from backend.repository import ApiProblem, SqlRepository
from backend.service import AnalyticsService
from .artifacts import ReportStore
from .series import month_date, read_snapshot
from .training import train


def main(argv=None):
    parser = argparse.ArgumentParser(description="Pronóstico global del importe extendido mensual.")
    parser.add_argument("command", choices=("plan", "train"))
    parser.add_argument("--through-month", required=True, help="Último mes cerrado del corte, YYYY-MM.")
    parser.add_argument("--allow-database-access", action="store_true")
    args = parser.parse_args(argv)
    phase = "configuration"
    try:
        closing = month_date(args.through_month)
        today = datetime.now(timezone.utc).date().replace(day=1)
        if closing >= today:
            raise ValueError("Selecciona un mes histórico cerrado, anterior al mes actual.")
        settings = WarehouseSettings.from_env(prefix="ML")
        if args.command == "plan":
            print(json.dumps({"status": "PLAN", "database_access": False, "through_month": args.through_month,
                              "target": "SUM(dw.FactVentas.ExtendedPrice), EsEliminadoFuente = 0",
                              "horizon": 3, "evaluation": "chronological; last 6 validation + last 6 test"}))
            return 0
        if not args.allow_database_access:
            raise ValueError("El entrenamiento real requiere --allow-database-access.")
        if not importlib.util.find_spec("sklearn"):
            raise ValueError("Falta scikit-learn. Instalar machine_learning/requirements.txt antes de entrenar.")
        phase = "read_dw"
        analytics = AnalyticsService(SqlRepository(settings), max_concurrent=1)
        history, snapshot = read_snapshot(analytics, settings, args.through_month)
        phase = "train"
        model, evaluation = train(history)
        phase = "verify_publication"
        analytics.consistent(lambda reader: {}, snapshot["publication"]["id"])
        phase = "publish_artifact"
        report = ReportStore().publish(model, evaluation, snapshot)
        print(json.dumps({"status": "TRAINED", "model_version": report["model_version"],
                          "source_publication": report["source_publication"], "months": len(history),
                          "trained_at_utc": report["trained_at_utc"], "selected_method": report["selected_method"],
                          "test_metrics": report["metrics"]["test"], "forecast": report["forecast"]}, ensure_ascii=False))
        return 0
    except ValueError as exc:
        print(json.dumps({"status": "BLOCKED", "phase": phase, "message": str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 2
    except Exception as exc:
        code = exc.code if isinstance(exc, ApiProblem) else type(exc).__name__
        print(json.dumps({"status": "FAILED", "phase": phase, "error_code": code,
                          "message": "El entrenamiento no publicó una nueva versión. Revisar configuración y dependencias."}), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
