"""Entradas plan (offline) y extract (acceso explícito, nunca DDL)."""

import argparse
from contextlib import ExitStack, closing
import importlib
import json
import os
import sys
from uuid import UUID

from .config import ConfigurationError, Settings
from .contracts import ENTITIES, FULL_ENTITIES, Run, safe_error
from .extract import SqlServerSource
from .pipeline import PipelineFailure, execute_pipeline
from .staging import RawStore
from .warehouse_store import WarehouseStore
from .workflow import execute_complete
from .quality import QualityBlocked


def emit(event: dict, *, error: bool = False) -> None:
    print(json.dumps(event, ensure_ascii=True), file=sys.stderr if error else sys.stdout, flush=True)


def positive(value: str) -> int:
    try:
        parsed = int(value)
    except ValueError:
        raise argparse.ArgumentTypeError("Debe ser un entero positivo.") from None
    if not 1 <= parsed <= 2**31 - 1:
        raise argparse.ArgumentTypeError("Debe estar entre 1 y 2147483647.")
    return parsed


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(description="Ventas: extracción Raw o flujo completo hasta DW; nunca ejecuta DDL.")
    commands = root.add_subparsers(dest="command", required=True)
    for command in ("plan", "extract", "run"):
        sub = commands.add_parser(command, help="Plan offline" if command == "plan" else "Extracción SQL")
        if command != "run":
            sub.add_argument("--max-rows-per-table", type=positive,
                             help="Muestra por tabla: siempre se marca RAW_PARTIAL.")
        else:
            sub.set_defaults(max_rows_per_table=None)
            sub.add_argument("--execution-id", type=UUID,
                             help="Reutilizar Raw completo del flujo; publicado implica no-op.")
        if command == "plan":
            sub.add_argument("--full", action="store_true", help="Plan offline de las cuatro tablas y DW.")
        else:
            sub.add_argument("--allow-database-access", action="store_true")
            sub.add_argument("--consistency", choices=("snapshot", "stable-window"), required=command == "extract")
            sub.add_argument("--confirm-stable-source", action="store_true",
                             help="Confirma una ventana sin escrituras durante toda la captura.")
    return root


def verify_database(connection, expected: str) -> None:
    cursor = connection.cursor()
    try:
        cursor.execute("SELECT DB_NAME();")
        row = cursor.fetchone()
        if row is None or str(row[0]).casefold() != expected.casefold():
            raise ConfigurationError("La base efectiva difiere de la configuración.")
    finally:
        cursor.close()
    connection.rollback()


def connect(settings: Settings, *, source: bool, module):
    connection = module.connect(
        settings.connection_string(source=source), autocommit=False, readonly=source,
        timeout=settings.login_timeout,
    )
    try:
        connection.timeout = settings.query_timeout
        verify_database(connection, settings.source_database if source else settings.target_database)
    except BaseException:
        connection.close()
        raise
    return connection


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    resume = args.command == "run" and args.execution_id is not None
    if args.command in {"extract", "run"} and not args.allow_database_access:
        emit({"status": "BLOCKED", "message": "Falta --allow-database-access; no se abrió conexión."}, error=True)
        return 2
    if args.command == "run" and not resume and args.consistency is None:
        emit({"status": "BLOCKED", "message": "La captura completa requiere --consistency."}, error=True)
        return 2
    if (args.command in {"extract", "run"} and not resume and args.consistency == "stable-window"
            and not args.confirm_stable_source):
        emit({"status": "BLOCKED", "message": "Falta confirmar la ventana estable del origen."}, error=True)
        return 2
    try:
        settings = Settings.from_env()
    except ConfigurationError as exc:
        emit({"status": "BLOCKED", "message": str(exc)}, error=True)
        return 2
    if args.command == "plan":
        if args.full and args.max_rows_per_table is not None:
            emit({"status": "BLOCKED", "message": "El flujo completo no publica muestras limitadas."}, error=True)
            return 2
        entities = FULL_ENTITIES if args.full else ENTITIES
        source_options = settings.connection_diagnostics(source=True)
        target_options = settings.connection_diagnostics(source=False)
        emit({"mode": "offline", "database_access": False,
              "source": {k: source_options[k] for k in ("server", "database")},
              "target": {k: target_options[k] for k in ("server", "database")},
              "driver": source_options["driver"], "batch_size": settings.batch_size,
              "connection_options": {"source": source_options, "target": target_options},
              "etl_environment_variables_present": sorted(k for k in os.environ
                                                          if k.startswith("ETL_")),
              "expected_status": "PUBLISHED" if args.full else ("RAW_PARTIAL" if args.max_rows_per_table else "RAW_COMPLETE"),
              "max_rows_per_table": args.max_rows_per_table,
              "queries_for_review": [e.select_sql(args.max_rows_per_table is not None) for e in entities]})
        return 0
    run = Run(execution_id=args.execution_id) if resume else Run()
    phase = "dependency"
    try:
        module = importlib.import_module("pyodbc")
        phase = "driver"
        if settings.driver not in module.drivers():
            raise ConfigurationError("El driver ODBC configurado no está registrado.")
        with ExitStack() as stack:
            phase = "target_connection"
            target = stack.enter_context(closing(connect(settings, source=False, module=module)))
            source = None
            if not resume:
                phase = "source_connection"
                connection = stack.enter_context(closing(connect(settings, source=True, module=module)))
                source = SqlServerSource(connection, args.consistency)
            if args.command == "run":
                phase = "workflow"
                result = execute_complete(source, WarehouseStore(target, settings), run,
                                          batch_size=settings.batch_size, consistency=args.consistency,
                                          resume=resume, emit=emit)
            else:
                phase = "extract"
                result = execute_pipeline(source, RawStore(target, settings), run,
                                          batch_size=settings.batch_size, consistency=args.consistency,
                                          limit=args.max_rows_per_table, emit=emit)
            phase = "close_connections"
        emit(result)
        return 0
    except PipelineFailure as exc:
        emit(exc.report, error=True)
        return 130 if exc.report.get("interrupted") else 1
    except QualityBlocked as exc:
        emit({"status": "BLOCKED", "execution_id": str(run.execution_id), "phase": phase,
              "message": str(exc), "audit_persisted": False}, error=True)
        return 2
    except (Exception, KeyboardInterrupt) as exc:
        message = "Fallo de acceso o cierre SQL; revisar la ejecución antes de repetir."
        if isinstance(exc, ModuleNotFoundError) and exc.name == "pyodbc":
            message = "Falta pyodbc; instalar requirements.txt dentro de .venv."
        diagnostics = {}
        if phase in {"source_connection", "target_connection"}:
            diagnostics["connection_options"] = settings.connection_diagnostics(
                source=phase == "source_connection")
        emit({"execution_id": str(run.execution_id), "attempt_id": str(run.attempt_id),
              "source_snapshot_id": str(run.source_snapshot_id), "status": "FAILED",
              "audit_persisted": False, "counts_confirmed": False,
              "phase": phase, "message": message, **diagnostics, **safe_error(exc)}, error=True)
        return 130 if isinstance(exc, KeyboardInterrupt) else 1
