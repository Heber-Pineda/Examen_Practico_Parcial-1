import argparse
from decimal import Decimal
import json
import os
import sys
import time

from backend.config import WarehouseSettings
from backend.repository import ApiProblem, SqlRepository
from backend.service import AnalyticsService
from .store import AlertStore
from .worker import AlertWorker


def main(argv=None):
    parser = argparse.ArgumentParser(description="Alertas académicas batch, sin SQL Server Agent.")
    parser.add_argument("command", choices=("plan", "evaluate", "deliver", "run", "watch"))
    parser.add_argument("--through-month")
    parser.add_argument("--allow-database-access", action="store_true")
    demo = parser.add_mutually_exclusive_group()
    demo.add_argument("--demo-trigger", action="store_true")
    demo.add_argument("--demo-no-trigger", action="store_true")
    parser.add_argument("--simulate-delivery-failure", action="store_true")
    parser.add_argument("--retry-now", action="store_true")
    parser.add_argument("--limit", type=int, default=20)
    parser.add_argument("--interval-seconds", type=int, default=300)
    args = parser.parse_args(argv)
    try:
        settings = WarehouseSettings.from_env(prefix="ALERT")
        if args.command == "plan":
            print(json.dumps({"status": "PLAN", "database_access": False, "rule": "DEMO_SALES_MOM",
                              "threshold_pct": os.environ.get("ALERT_THRESHOLD_PCT", "-10"), "delivery": "in-app inbox"}))
            return 0
        if not args.allow_database_access:
            raise ValueError("Se requiere --allow-database-access para evaluar o entregar.")
        if not 10 <= args.interval_seconds <= 86400 or not 1 <= args.limit <= 100:
            raise ValueError("Intervalo o límite fuera de rango.")
        worker = AlertWorker(AnalyticsService(SqlRepository(settings), 1), AlertStore(settings),
                             Decimal(os.environ.get("ALERT_THRESHOLD_PCT", "-10")), int(os.environ.get("ALERT_MAX_ATTEMPTS", "3")))
        mode = "FORCED_DEMO_ON" if args.demo_trigger else "FORCED_DEMO_OFF" if args.demo_no_trigger else "CONFIGURED"
        while True:
            result = {"status": "OK", "is_academic_demo": True}
            try:
                if args.command != "deliver":
                    result["evaluation"] = worker.evaluate(args.through_month, mode)
                if args.command != "evaluate":
                    result["delivery"] = worker.deliver(args.limit, args.retry_now, args.simulate_delivery_failure)
            except Exception as exc:
                if args.command != "watch":
                    raise
                result = {"status": "FAILED", "is_academic_demo": True,
                          "error_code": exc.code if isinstance(exc, ApiProblem) else type(exc).__name__,
                          "message": "Ciclo fallido; se reintentará en el siguiente intervalo."}
            print(json.dumps(result, ensure_ascii=False), flush=True)
            if args.command != "watch":
                return 0
            time.sleep(args.interval_seconds)
    except KeyboardInterrupt:
        return 130
    except ValueError as exc:
        print(json.dumps({"status": "BLOCKED", "message": str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 2
    except Exception as exc:
        print(json.dumps({"status": "FAILED", "error_code": exc.code if isinstance(exc, ApiProblem) else type(exc).__name__,
                          "message": "No se completó el ciclo. Verificar script 005, configuración y permisos; los eventos permanecen recuperables."}), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
