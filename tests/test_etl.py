"""Pruebas aisladas: dobles en memoria, sin pyodbc ni motores de bases de datos."""

from contextlib import redirect_stderr, redirect_stdout
import copy
import csv
from dataclasses import replace
from datetime import date, datetime
from decimal import Decimal
import hashlib
from io import StringIO
import json
import os
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from etl.cli import connect, main
from etl.config import ConfigurationError, Settings
from etl.contracts import (
    ENTITIES, CUSTOMERS, INVOICES, INVOICE_LINES, Progress, RawValidationError, Run,
    prepare_payload, safe_error, timestamp_text,
)
from etl.extract import SqlServerSource
from etl.pipeline import PipelineFailure, execute_pipeline
from etl.staging import RawStore

ROOT = Path(__file__).resolve().parents[1]
T0 = "2026-11-14T12:00:00.1234567"


def invoice(key=930001):
    return (key, 910001, 910002, date(2016, 5, 1), False, T0)


def line(key=940001, price=Decimal("10.00"), quantity=2):
    return (key, 930001, 920001, quantity, price, Decimal("3.00"),
            Decimal("23.00"), Decimal("6.00"), T0)


class MemorySource:
    def __init__(self, data=None, fail_after=None):
        self.data = data if data is not None else {
            "Invoices": [invoice()], "InvoiceLines": [line(), line(940002)]}
        self.fail_after = fail_after
        self.begun = False
        self.finished = False

    def begin(self):
        self.begun = True

    def batches(self, entity, batch_size, limit):
        rows = self.data[entity.table][:limit]
        for offset in range(0, len(rows), batch_size):
            if self.fail_after == (entity.table, offset):
                raise RuntimeError("Falla de lectura sintética.")
            yield rows[offset:offset + batch_size]

    def finish(self):
        self.finished = True


class MemoryStore:
    def __init__(self, fail_batch=None, fail_audit=False):
        self.rows = {}
        self.progress = {}
        self.status = {}
        self.fail_batch = fail_batch
        self.fail_audit = fail_audit

    def begin_run(self, run, consistency, limit):
        self.status[run.execution_id] = "EXTRACTING"

    def start_entity(self, run, entity):
        self.rows[(run.execution_id, entity.table)] = {}

    def write_batch(self, run, entity, prepared, progress):
        key = (run.execution_id, entity.table)
        if self.fail_batch == (entity.table, progress.batches_committed):
            raise RuntimeError("Falla de escritura sintética.")
        candidate = copy.deepcopy(self.rows[key])
        for values, digest in prepared:
            if values[0] in candidate:
                raise ValueError("Duplicado sintético.")
            candidate[values[0]] = (values, digest)
        self.rows[key] = candidate
        self.progress[key] = copy.deepcopy(progress)

    def finish_entity(self, run, entity, progress):
        self.progress[(run.execution_id, entity.table)] = copy.deepcopy(progress)

    def finish_run(self, run, status):
        self.status[run.execution_id] = status

    def fail_run(self, run, progress, active, error):
        if self.fail_audit:
            raise RuntimeError("Auditoría sintéticamente indisponible.")
        self.status[run.execution_id] = "FAILED"
        for table, p in progress.items():
            p.rows_inserted = len(self.rows.get((run.execution_id, table), {}))


class FakeCursor:
    def __init__(self, connection):
        self.connection = connection
        self.description = connection.description
        self.closed = False
        self.rowcount = 1
        self.arraysize = 1
        self.fast_executemany = False

    def execute(self, sql, *params):
        self.connection.calls.append((sql, params))
        if self.connection.fail_update and sql.startswith("UPDATE"):
            raise RuntimeError("Falla sintética de auditoría.")
        self.connection.pending.append((sql, params))
        return self

    def executemany(self, sql, params):
        for item in params:
            self.connection.pending.append((sql, item))
            if self.connection.fail_insert:
                raise RuntimeError("Falla sintética después de insertar una fila.")

    def fetchone(self):
        return (self.connection.scalar,)

    def fetchmany(self, size):
        self.connection.fetch_sizes.append(size)
        chunk = self.connection.data[:size]
        self.connection.data = self.connection.data[size:]
        return chunk

    def close(self):
        self.closed = True


class FakeConnection:
    def __init__(self, data=(), entity=INVOICES, scalar=0):
        self.data = list(data)
        self.description = [(c.name,) for c in entity.columns]
        self.scalar = scalar
        self.calls, self.pending, self.committed = [], [], []
        self.fetch_sizes, self.cursors = [], []
        self.commits = self.rollbacks = 0
        self.fail_insert = self.fail_update = self.closed = False

    def cursor(self):
        cursor = FakeCursor(self)
        self.cursors.append(cursor)
        return cursor

    def commit(self):
        self.commits += 1
        self.committed.extend(self.pending)
        self.pending.clear()

    def rollback(self):
        self.rollbacks += 1
        self.pending.clear()

    def close(self):
        self.closed = True


class ConfigurationTests(unittest.TestCase):
    def test_safe_defaults_and_integrated_authentication(self):
        settings = Settings.from_env({})
        self.assertNotEqual(settings.source_database, settings.target_database)
        self.assertIn("Trusted_Connection=yes", settings.connection_string(source=True))
        self.assertIn("Encrypt=yes;TrustServerCertificate=no", settings.connection_string(source=False))
        self.assertNotIn("PWD=", settings.connection_string(source=True))

    def test_target_protects_original_and_system_databases(self):
        for name in ("WideWorldImporters", "WIDEWORLDIMPORTERS", "master", "tempdb", "model", "msdb"):
            with self.subTest(name=name), self.assertRaises(ConfigurationError):
                Settings.from_env({"ETL_TARGET_DATABASE": name, "ETL_TARGET_SERVER": "other-server"})

    def test_source_target_collision_is_rejected(self):
        with self.assertRaises(ConfigurationError):
            Settings.from_env({"ETL_SOURCE_DATABASE": "LabSource", "ETL_TARGET_DATABASE": "labsource"})

    def test_invalid_settings_fail_before_connecting(self):
        invalid = [{"ETL_BATCH_SIZE": "0"}, {"ETL_BATCH_SIZE": "2001"},
                   {"ETL_BATCH_SIZE": "abc"}, {"ETL_QUERY_TIMEOUT": "0"},
                   {"ETL_ENCRYPT": "perhaps"}, {"ETL_TARGET_DATABASE": "bad;DROP"},
                   {"ETL_SOURCE_SERVER": "bad\nserver"}]
        for env in invalid:
            with self.subTest(env=env), self.assertRaises(ConfigurationError):
                Settings.from_env(env)

    def test_odbc_values_are_braced(self):
        settings = Settings.from_env({"ETL_SOURCE_SERVER": "test};UID=untrusted"})
        self.assertIn("SERVER={test}};UID=untrusted}", settings.connection_string(source=True))

    def test_target_connection_retains_confirmed_endpoint_and_tls_settings(self):
        settings = Settings.from_env({})
        self.assertEqual(settings.connection_string(source=False),
                         r"DRIVER={ODBC Driver 17 for SQL Server};"
                         r"SERVER={HEBER-ENOC-PINE\SQLEXPRESS};"
                         "DATABASE={WideWorldImporters_Analytics};Trusted_Connection=yes;"
                         "Encrypt=yes;TrustServerCertificate=no;"
                         "APP={WWI Sales Raw ETL};MARS_Connection=no;")
        diagnostics = settings.connection_diagnostics(source=False)
        self.assertFalse(diagnostics["readonly"])
        self.assertFalse(diagnostics["autocommit"])

    def test_diagnostics_hide_connection_string_fragments_in_identifiers(self):
        settings = Settings.from_env({"ETL_TARGET_SERVER": "bad;PWD=never-print-this"})
        diagnostics = settings.connection_diagnostics(source=False)
        self.assertEqual(diagnostics["server"], "<revisar valor en entorno>")
        self.assertNotIn("never-print-this", json.dumps(diagnostics))


class TlsConfigurationTests(unittest.TestCase):
    def test_secure_defaults(self):
        settings = Settings.from_env({})
        self.assertEqual(settings.environment, "production")
        self.assertEqual(settings.trust_server_certificate, "no")
        for source in (True, False):
            self.assertIn("Encrypt=yes;TrustServerCertificate=no;",
                          settings.connection_string(source=source))

    def test_explicit_local_opt_in_applies_to_both_connections(self):
        for value in ("true", "TRUE", "yes"):
            with self.subTest(value=value):
                settings = Settings.from_env({"ETL_ENVIRONMENT": "local",
                                              "ETL_TRUST_SERVER_CERTIFICATE": value})
                for source in (True, False):
                    self.assertIn("Encrypt=yes;TrustServerCertificate=yes;",
                                  settings.connection_string(source=source))
                    self.assertEqual(settings.connection_diagnostics(source=source)["readonly"], source)

    def test_false_and_legacy_no_keep_certificate_validation(self):
        for value in ("false", "FALSE", "no"):
            with self.subTest(value=value):
                settings = Settings.from_env({"ETL_ENVIRONMENT": "local",
                                              "ETL_TRUST_SERVER_CERTIFICATE": value})
                self.assertEqual(settings.trust_server_certificate, "no")

    def test_exception_rejects_any_other_source_or_target(self):
        for variable in ("ETL_SOURCE_SERVER", "ETL_TARGET_SERVER"):
            for server in (r"OTHER\SQLEXPRESS", r"localhost\SQLEXPRESS",
                           r"HEBER-ENOC-PINE\OTHER", r"tcp:HEBER-ENOC-PINE\SQLEXPRESS",
                           r"127.0.0.1\SQLEXPRESS"):
                with self.subTest(variable=variable, server=server), self.assertRaises(ConfigurationError):
                    Settings.from_env({"ETL_ENVIRONMENT": "local",
                                       "ETL_TRUST_SERVER_CERTIFICATE": "true", variable: server})

    def test_exception_rejects_production_and_missing_environment(self):
        for extra in ({}, {"ETL_ENVIRONMENT": "production"}):
            with self.subTest(extra=extra), self.assertRaises(ConfigurationError):
                Settings.from_env({"ETL_TRUST_SERVER_CERTIFICATE": "true", **extra})

    def test_encryption_cannot_be_disabled(self):
        for value in ("no", "false"):
            for trust in ("false", "true"):
                with self.subTest(value=value, trust=trust), self.assertRaises(ConfigurationError):
                    Settings.from_env({"ETL_ENVIRONMENT": "local", "ETL_ENCRYPT": value,
                                       "ETL_TRUST_SERVER_CERTIFICATE": trust})

    def test_invalid_boolean_and_environment_are_rejected(self):
        for env in ({"ETL_TRUST_SERVER_CERTIFICATE": "perhaps"}, {"ETL_ENVIRONMENT": "unknown"}):
            with self.subTest(env=env), self.assertRaises(ConfigurationError):
                Settings.from_env(env)

    def test_cli_blocks_policy_violation_before_importing_driver(self):
        for env in ({"ETL_TRUST_SERVER_CERTIFICATE": "true"},
                    {"ETL_ENVIRONMENT": "local", "ETL_TRUST_SERVER_CERTIFICATE": "true",
                     "ETL_TARGET_SERVER": r"OTHER\SQLEXPRESS"}):
            errors = StringIO()
            with (self.subTest(env=env), patch.dict(os.environ, env, clear=True),
                  redirect_stderr(errors), patch("etl.cli.importlib.import_module") as imported):
                code = main(["extract", "--allow-database-access", "--consistency", "stable-window",
                             "--confirm-stable-source", "--max-rows-per-table", "100"])
            self.assertEqual(code, 2)
            self.assertEqual(json.loads(errors.getvalue())["status"], "BLOCKED")
            imported.assert_not_called()

    def test_settings_copy_cannot_bypass_local_scope(self):
        settings = Settings.from_env({"ETL_ENVIRONMENT": "local", "ETL_TRUST_SERVER_CERTIFICATE": "true"})
        for change in ({"environment": "production"}, {"target_server": r"OTHER\SQLEXPRESS"},
                       {"source_server": r"OTHER\SQLEXPRESS"}):
            with self.subTest(change=change), self.assertRaises(ConfigurationError):
                replace(settings, **change)


class ContractTests(unittest.TestCase):
    def test_projection_matches_real_dictionary_types_and_nullability(self):
        with (ROOT / "database/normalized/Diccionario_Datos_WideWorldImporters.csv").open(
                encoding="utf-8-sig", newline="") as handle:
            dictionary = {(r["schema_name"], r["table_name"], r["column_name"]): r
                          for r in csv.DictReader(handle, delimiter=";")}
        for entity in ENTITIES:
            for column in entity.columns:
                with self.subTest(entity=entity.table, column=column.name):
                    row = dictionary[("Sales", entity.table, column.name)]
                    self.assertEqual(column.nullable, row["is_nullable"] == "SI")
                    if row["data_type"] == "decimal":
                        expected = f"decimal({row['precision']},{row['scale']})"
                    elif row["data_type"] == "datetime2":
                        expected = f"datetime2({row['scale']})"
                    else:
                        expected = row["data_type"]
                    self.assertEqual(column.kind, expected)

    def test_ddl_raw_business_columns_match_dictionary(self):
        sql = (ROOT / "database/scripts/002_create_raw_and_audit.sql").read_text(encoding="utf-8")
        for entity in ENTITIES:
            section = sql.split(f"CREATE TABLE [raw].[{entity.table}] (", 1)[1].split("CONSTRAINT [PK_", 1)[0]
            for column in entity.columns:
                null = "NULL" if column.nullable else "NOT NULL"
                self.assertIn(f"[{column.name}] {column.kind} {null}", section)

    def test_exact_decimal_and_nullable_price_are_preserved(self):
        values, _ = prepare_payload(INVOICE_LINES, line(price=Decimal("1234567890123456.78")))
        self.assertEqual(values[4], Decimal("1234567890123456.78"))
        self.assertIsNone(prepare_payload(INVOICE_LINES, line(price=None))[0][4])

    def test_null_and_zero_have_different_hashes(self):
        self.assertNotEqual(prepare_payload(INVOICE_LINES, line(price=None))[1],
                            prepare_payload(INVOICE_LINES, line(price=Decimal("0")))[1])

    def test_hash_is_stable_for_equal_sql_decimal_values(self):
        self.assertEqual(prepare_payload(INVOICE_LINES, line(price=Decimal("10")))[1],
                         prepare_payload(INVOICE_LINES, line(price=Decimal("10.00")))[1])

    def test_seventh_fraction_is_preserved_and_changes_hash(self):
        a, b = invoice(), invoice()[:-1] + ("2026-11-14T12:00:00.1234568",)
        self.assertEqual(prepare_payload(INVOICES, a)[0][-1], T0)
        self.assertNotEqual(prepare_payload(INVOICES, a)[1], prepare_payload(INVOICES, b)[1])
        self.assertEqual(timestamp_text("2026-11-14T12:00:00"), "2026-11-14T12:00:00.0000000")

    def test_no_business_rules_are_invented_in_raw(self):
        for quantity in (0, -2):
            values, _ = prepare_payload(INVOICE_LINES, line(quantity=quantity, price=Decimal("-1.00")))
            self.assertEqual(values[3:5], (quantity, Decimal("-1.00")))

    def test_incompatible_values_fail_without_silent_rounding(self):
        rows = [line(price=10.0), line(price=Decimal("1.001")),
                line(price=Decimal("NaN")), line(price=Decimal("10000000000000000")),
                line(quantity=None), line(quantity=True)]
        for row in rows:
            with self.subTest(row=row), self.assertRaises(RawValidationError):
                prepare_payload(INVOICE_LINES, row)

    def test_datetime_objects_and_invalid_timestamps_are_rejected(self):
        for value in (datetime(2026, 1, 1), "2026-02-30T00:00:00.0000000",
                      "2026-01-01T00:00:00.12345678", T0 + "Z"):
            with self.subTest(value=value), self.assertRaises(RawValidationError):
                timestamp_text(value)


class ConnectionDiagnosticTests(unittest.TestCase):
    def test_08001_network_provider_is_not_labelled_as_tls(self):
        diagnostic = safe_error(RuntimeError(
            "08001", "[08001] [Microsoft][ODBC Driver 17 for SQL Server]"
            "Named Pipes Provider: no disponible (53) (SQLDriverConnect);"
            " [08001] [Microsoft][ODBC Driver 17 for SQL Server]Timeout (0)"))
        self.assertEqual(diagnostic["odbc_provider"], "Named Pipes Provider")
        self.assertEqual(diagnostic["odbc_native_code"], "53")
        self.assertNotIn("no disponible", json.dumps(diagnostic))

    def test_unstructured_08001_does_not_leak_or_guess_native_diagnostics(self):
        diagnostic = safe_error(RuntimeError(
            "08001", "PWD=never-print-this; SSL Provider (123456789) (SQLDriverConnect)"))
        self.assertEqual(diagnostic["odbc_provider"], "not_reported")
        self.assertEqual(diagnostic["odbc_native_code"], "not_reported")
        self.assertNotIn("never-print-this", json.dumps(diagnostic))
        self.assertNotIn("123456789", json.dumps(diagnostic))


class IntegrityDiagnosticTests(unittest.TestCase):
    prefix = "[23000] [Microsoft][ODBC Driver 17 for SQL Server][SQL Server]"

    def test_native_constraint_and_index_names_without_message_or_values(self):
        cases = [
            (547, 'The INSERT conflicted with the CHECK constraint "CK_etl_entity_source".', "CK_etl_entity_source"),
            (547, 'The INSERT conflicted with the FOREIGN KEY constraint "FK_etl_entity_execution".', "FK_etl_entity_execution"),
            (2627, "Violation of PRIMARY KEY constraint 'PK_etl_entity'. Duplicate key (private-value).", "PK_etl_entity"),
            (2601, "Cannot insert duplicate key with unique index 'UQ_etl_execution_identity'.", "UQ_etl_execution_identity"),
        ]
        for native, message, constraint in cases:
            with self.subTest(native=native, constraint=constraint):
                error = RuntimeError("23000", self.prefix + message + f" PWD=never-print-this ({native}) (SQLExecute)")
                diagnostic = safe_error(error)
                self.assertEqual(diagnostic["odbc_native_code"], str(native))
                self.assertEqual(diagnostic["sqlserver_constraint"], constraint)
                self.assertNotIn("never-print-this", json.dumps(diagnostic))
                self.assertNotIn("private-value", json.dumps(diagnostic))

    def test_spanish_constraint_and_first_diagnostic_are_preserved(self):
        message = (self.prefix + 'INSERT en conflicto con la restricción CHECK "CK_etl_entity_source". '
                   '(547) (SQLExecDirectW); [23000] [Microsoft][ODBC Driver 17 for SQL Server]'
                   '[SQL Server]La instrucción terminó. (3621)')
        diagnostic = safe_error(RuntimeError("23000", message))
        self.assertEqual(diagnostic["odbc_native_code"], "547")
        self.assertEqual(diagnostic["sqlserver_constraint"], "CK_etl_entity_source")

    def test_unstructured_errors_and_unsafe_identifiers_are_not_exposed(self):
        for message in (
                "PWD=never-print-this; constraint 'CK_secret' (547) (SQLExecute)",
                self.prefix + 'CHECK constraint "CK_bad;PWD=never-print-this". (547) (SQLExecute)'):
            with self.subTest(message=message):
                diagnostic = safe_error(RuntimeError("23000", message))
                self.assertNotIn("never-print-this", json.dumps(diagnostic))
                self.assertNotIn("CK_secret", json.dumps(diagnostic))
                self.assertNotIn("CK_bad", json.dumps(diagnostic))

    def test_customers_audit_insert_matches_declared_migration_values(self):
        connection, run = FakeConnection(), Run()
        RawStore(connection, Settings.from_env({})).start_entity(run, CUSTOMERS)
        sql, parameters = connection.committed[0]
        self.assertIn("INSERT INTO [audit].[etl_entity]", sql)
        self.assertEqual(sql.count("?"), len(parameters))
        self.assertEqual(parameters[:4], (str(run.execution_id), "Sales", "Customers", "Customers"))
        self.assertIn("'raw'", sql)
        self.assertIn("'EXTRACTING'", sql)
        migration = (ROOT / "database/scripts/003_complete_sales_pipeline.sql").read_text(encoding="utf-8")
        self.assertIn("[source_schema] = N'Sales' AND [source_table] IN (N'Invoices', N'InvoiceLines', N'Customers')", migration)

    def test_failure_before_first_row_preserves_primary_and_secondary_diagnostics(self):
        primary = RuntimeError("23000", self.prefix + 'CHECK constraint "CK_etl_entity_source". (547) (SQLExecute)')
        secondary = RuntimeError("42S02", "[42S02] [Microsoft][ODBC Driver 17 for SQL Server][SQL Server]"
                                 "Invalid object name 'raw.Customers'; PWD=never-print-this (208) (SQLExecDirectW)")
        source = SimpleNamespace(begin=lambda: None, finish=lambda: None)
        store = SimpleNamespace(begin_run=lambda *a: None)
        def fail_start(*args):
            raise primary
        def fail_audit(*args):
            raise secondary
        store.start_entity, store.fail_run = fail_start, fail_audit
        with self.assertRaises(PipelineFailure) as caught:
            execute_pipeline(source, store, Run(), batch_size=500, consistency="stable-window",
                             entities=(CUSTOMERS,), verify_source_counts=True)
        result = caught.exception.report
        self.assertEqual(result["phase"], "start_entity")
        self.assertEqual(result["active_entity"], "Customers")
        self.assertEqual(result["entities"]["Customers"]["rows_read"], 0)
        self.assertEqual(result["odbc_native_code"], "547")
        self.assertEqual(result["sqlserver_constraint"], "CK_etl_entity_source")
        self.assertFalse(result["audit_persisted"])
        self.assertEqual(result["audit_error"]["error_code"], "42S02")
        self.assertEqual(result["audit_error"]["odbc_native_code"], "208")
        self.assertNotIn("never-print-this", json.dumps(result))

    def test_zero_row_failures_identify_the_exact_pre_extraction_operation(self):
        error = RuntimeError("23000", self.prefix + 'CHECK constraint "CK_etl_entity_counts". (547) (SQLExecute)')
        def fail(*args):
            raise error
        for phase in ("start_entity", "count_source", "record_source_count"):
            with self.subTest(phase=phase):
                source = SimpleNamespace(begin=lambda: None, finish=lambda: None, count_entity=lambda *a: 663)
                store = SimpleNamespace(begin_run=lambda *a: None, start_entity=lambda *a: None,
                                        record_source_count=lambda *a: None, fail_run=lambda *a: None)
                if phase == "count_source":
                    source.count_entity = fail
                else:
                    setattr(store, phase, fail)
                with self.assertRaises(PipelineFailure) as caught:
                    execute_pipeline(source, store, Run(), batch_size=500, consistency="stable-window",
                                     entities=(CUSTOMERS,), verify_source_counts=True)
                self.assertEqual(caught.exception.report["phase"], phase)
                self.assertTrue(caught.exception.report["audit_persisted"])
                self.assertNotIn("audit_error", caught.exception.report)


class SourceTests(unittest.TestCase):
    def test_fetchmany_is_bounded_and_cursor_is_closed(self):
        connection = FakeConnection([invoice(i) for i in range(5)])
        source = SqlServerSource(connection, "snapshot")
        source.begin()
        batches = list(source.batches(INVOICES, 2, None))
        source.finish()
        self.assertEqual([len(b) for b in batches], [2, 2, 1])
        self.assertTrue(all(size == 2 for size in connection.fetch_sizes))
        self.assertTrue(all(c.closed for c in connection.cursors))
        self.assertEqual(connection.commits, 0)
        self.assertIn("SNAPSHOT", connection.calls[0][0])
        self.assertTrue(connection.calls[1][0].startswith("SELECT "))

    def test_sample_limit_is_a_parameter(self):
        connection = FakeConnection([invoice()])
        list(SqlServerSource(connection, "stable-window").batches(INVOICES, 10, 100))
        sql, params = connection.calls[0]
        self.assertIn("TOP (?)", sql)
        self.assertEqual(params, (100,))

    def test_projection_drift_aborts_and_closes_cursor(self):
        connection = FakeConnection()
        connection.description = [("UnexpectedColumn",)]
        with self.assertRaises(RawValidationError):
            list(SqlServerSource(connection, "snapshot").batches(INVOICES, 10, None))
        self.assertTrue(connection.cursors[-1].closed)


class TransactionTests(unittest.TestCase):
    def test_partial_insert_is_rolled_back(self):
        connection = FakeConnection()
        connection.fail_insert = True
        store = RawStore(connection, Settings.from_env({}))
        prepared = [prepare_payload(INVOICES, invoice(1)), prepare_payload(INVOICES, invoice(2))]
        with self.assertRaises(RuntimeError):
            store.write_batch(Run(), INVOICES, prepared, Progress(rows_read=2, rows_inserted=2))
        self.assertEqual(connection.commits, 0)
        self.assertEqual(connection.rollbacks, 1)
        self.assertEqual(connection.pending, [])
        self.assertEqual(connection.committed, [])

    def test_raw_and_audit_are_atomic_if_audit_fails(self):
        connection = FakeConnection()
        connection.fail_update = True
        store = RawStore(connection, Settings.from_env({}))
        with self.assertRaises(RuntimeError):
            store.write_batch(Run(), INVOICES, [prepare_payload(INVOICES, invoice())],
                              Progress(rows_read=1, rows_inserted=1))
        self.assertEqual(connection.committed, [])
        self.assertEqual(connection.pending, [])
        self.assertEqual(connection.rollbacks, 1)

    def test_successful_batch_keeps_decimal_null_and_timestamp_parameters(self):
        connection = FakeConnection()
        store = RawStore(connection, Settings.from_env({}))
        values, digest = prepare_payload(INVOICE_LINES, line(price=None))
        store.write_batch(Run(), INVOICE_LINES, [(values, digest)], Progress(rows_read=1, rows_inserted=1))
        sql, params = connection.committed[0]
        self.assertIn("CONVERT(datetime2(7), ?, 126)", sql)
        self.assertEqual(params[-1], T0)
        self.assertIsNone(params[7 + 4])
        self.assertIsInstance(params[7 + 5], Decimal)
        self.assertEqual(connection.commits, 1)

    def test_count_mismatch_prevents_completion(self):
        connection = FakeConnection(scalar=0)
        with self.assertRaises(ValueError):
            RawStore(connection, Settings.from_env({})).finish_entity(
                Run(), INVOICES, Progress(rows_read=1, rows_inserted=1, is_complete=True))
        self.assertEqual(connection.commits, 0)


class PipelineTests(unittest.TestCase):
    def run_pipeline(self, source=None, store=None, limit=None, run=None):
        return execute_pipeline(source or MemorySource(), store or MemoryStore(), run or Run(),
                                batch_size=1, consistency="snapshot", limit=limit)

    def test_complete_extraction_has_two_entity_manifests_and_counts(self):
        source, store, run = MemorySource(), MemoryStore(), Run()
        result = self.run_pipeline(source, store, run=run)
        self.assertEqual(result["status"], "RAW_COMPLETE")
        self.assertEqual(result["entities"]["Invoices"]["rows_inserted"], 1)
        self.assertEqual(result["entities"]["InvoiceLines"]["rows_inserted"], 2)
        self.assertTrue(all(e["is_complete"] for e in result["entities"].values()))
        self.assertIsNone(result["entities"]["InvoiceLines"]["rows_rejected"])
        self.assertTrue(source.finished)
        self.assertEqual(store.status[run.execution_id], "RAW_COMPLETE")

    def test_sample_is_partial_even_when_source_has_fewer_rows_than_limit(self):
        result = self.run_pipeline(limit=100)
        self.assertEqual(result["status"], "RAW_PARTIAL")
        self.assertTrue(all(not e["is_complete"] for e in result["entities"].values()))

    def test_empty_complete_source_has_empty_manifests(self):
        result = self.run_pipeline(MemorySource({"Invoices": [], "InvoiceLines": []}))
        self.assertEqual(result["status"], "RAW_COMPLETE")
        for entity in result["entities"].values():
            self.assertEqual(entity["rows_read"], 0)
            self.assertEqual(entity["manifest_hash"], hashlib.sha256(b"").hexdigest())

    def test_failure_after_committed_batch_preserves_partial_raw_and_counts(self):
        store, run = MemoryStore(fail_batch=("InvoiceLines", 2)), Run()
        with self.assertRaises(PipelineFailure) as caught:
            self.run_pipeline(store=store, run=run)
        result = caught.exception.report
        self.assertEqual(result["status"], "FAILED")
        self.assertEqual(result["entities"]["InvoiceLines"]["rows_read"], 2)
        self.assertEqual(result["entities"]["InvoiceLines"]["rows_inserted"], 1)
        self.assertFalse(result["entities"]["InvoiceLines"]["is_complete"])
        self.assertEqual(len(store.rows[(run.execution_id, "InvoiceLines")]), 1)
        self.assertEqual(store.status[run.execution_id], "FAILED")

    def test_source_failure_is_audited_without_claiming_completion(self):
        source = MemorySource(fail_after=("InvoiceLines", 1))
        with self.assertRaises(PipelineFailure) as caught:
            self.run_pipeline(source)
        self.assertEqual(caught.exception.report["phase"], "extract")
        self.assertTrue(caught.exception.report["audit_persisted"])
        self.assertIn("InvoiceLines", caught.exception.report["error_detail"])
        self.assertTrue(source.finished)

    def test_audit_failure_does_not_hide_original_failure(self):
        store = MemoryStore(fail_batch=("InvoiceLines", 1), fail_audit=True)
        with self.assertRaises(PipelineFailure) as caught:
            self.run_pipeline(store=store)
        self.assertFalse(caught.exception.report["audit_persisted"])
        self.assertFalse(caught.exception.report["counts_confirmed"])
        self.assertEqual(caught.exception.report["error_type"], "RuntimeError")

    def test_duplicate_keys_across_batches_stop_the_run(self):
        source = MemorySource({"Invoices": [invoice()], "InvoiceLines": [line(), line()]})
        with self.assertRaises(PipelineFailure) as caught:
            self.run_pipeline(source)
        self.assertEqual(caught.exception.report["error_type"], "RawValidationError")
        self.assertEqual(caught.exception.report["entities"]["InvoiceLines"]["rows_inserted"], 1)

    def test_equal_data_new_runs_preserve_separate_generations_and_equal_manifests(self):
        store = MemoryStore()
        a = self.run_pipeline(store=store)
        b = self.run_pipeline(store=store)
        self.assertNotEqual(a["execution_id"], b["execution_id"])
        self.assertEqual(a["entities"]["InvoiceLines"]["manifest_hash"],
                         b["entities"]["InvoiceLines"]["manifest_hash"])
        self.assertEqual(len(store.rows), 4)


class OfflineCliTests(unittest.TestCase):
    def call_cli(self, args):
        output, errors = StringIO(), StringIO()
        with patch.dict(os.environ, {}, clear=True), redirect_stdout(output), redirect_stderr(errors):
            code = main(args)
        return code, output.getvalue(), errors.getvalue()

    def test_plan_never_imports_pyodbc_or_opens_connections(self):
        with patch("etl.cli.importlib.import_module", side_effect=AssertionError("Prohibido conectar")):
            code, output, errors = self.call_cli(["plan"])
        self.assertEqual(code, 0)
        self.assertFalse(json.loads(output)["database_access"])
        self.assertEqual(json.loads(output)["etl_environment_variables_present"], [])
        self.assertEqual(errors, "")

    def test_plan_displays_effective_environment_and_connection_options_offline(self):
        output = StringIO()
        overrides = {"ETL_SOURCE_SERVER": r"LabSource\INSTANCE",
                     "ETL_TARGET_SERVER": r"HEBER-ENOC-PINE\SQLEXPRESS",
                     "ETL_LOGIN_TIMEOUT": "5"}
        with (patch.dict(os.environ, overrides, clear=True), redirect_stdout(output),
              patch("etl.cli.importlib.import_module") as imported):
            code = main(["plan"])
        imported.assert_not_called()
        self.assertEqual(code, 0)
        result = json.loads(output.getvalue())
        self.assertEqual(result["source"]["server"], overrides["ETL_SOURCE_SERVER"])
        self.assertEqual(result["target"]["server"], overrides["ETL_TARGET_SERVER"])
        self.assertEqual(result["etl_environment_variables_present"], sorted(overrides))
        self.assertEqual(result["connection_options"]["target"]["login_timeout"], 5)
        self.assertEqual(result["connection_options"]["target"]["encrypt"], "yes")
        self.assertEqual(result["connection_options"]["target"]["trust_server_certificate"], "no")
        self.assertFalse(result["connection_options"]["target"]["readonly"])
        self.assertTrue(result["connection_options"]["source"]["readonly"])

    def test_target_08001_keeps_driver_diagnostics_without_leaking_message(self):
        class OperationalError(Exception):
            pass
        calls = []
        def failing_connect(value, **kwargs):
            calls.append((value, kwargs))
            raise OperationalError(
                "08001", "[08001] [Microsoft][ODBC Driver 17 for SQL Server]"
                "SSL Provider: detalle privado; PWD=never-print-this "
                "(-2146893019) (SQLDriverConnect)")
        module = SimpleNamespace(drivers=lambda: ["ODBC Driver 17 for SQL Server"], connect=failing_connect)
        with patch("etl.cli.importlib.import_module", return_value=module):
            code, output, errors = self.call_cli([
                "extract", "--allow-database-access", "--consistency", "stable-window",
                "--confirm-stable-source", "--max-rows-per-table", "100"])
        result = json.loads(errors)
        self.assertEqual((code, output), (1, ""))
        self.assertEqual(result["phase"], "target_connection")
        self.assertFalse(result["audit_persisted"])
        self.assertEqual(result["error_type"], "OperationalError")
        self.assertEqual(result["odbc_provider"], "SSL Provider")
        self.assertEqual(result["odbc_native_code"], "-2146893019")
        self.assertNotIn("never-print-this", errors)
        self.assertNotIn("detalle privado", errors)
        self.assertEqual(result["connection_options"]["database"], "WideWorldImporters_Analytics")
        self.assertEqual(calls[0][1], {"readonly": False, "autocommit": False, "timeout": 10})
        self.assertEqual(calls[0][0], Settings.from_env({}).connection_string(source=False))

    def test_extract_without_permission_never_imports_driver(self):
        with patch("etl.cli.importlib.import_module") as imported:
            code, _, errors = self.call_cli(["extract", "--consistency", "snapshot"])
        self.assertEqual(code, 2)
        imported.assert_not_called()
        self.assertEqual(json.loads(errors)["status"], "BLOCKED")

    def test_stable_window_requires_explicit_confirmation(self):
        with patch("etl.cli.importlib.import_module") as imported:
            code, _, _ = self.call_cli(["extract", "--allow-database-access", "--consistency", "stable-window"])
        self.assertEqual(code, 2)
        imported.assert_not_called()

    def test_missing_dependency_has_safe_actionable_message(self):
        with patch("etl.cli.importlib.import_module", side_effect=ModuleNotFoundError(name="pyodbc")):
            code, _, errors = self.call_cli(["extract", "--allow-database-access", "--consistency", "snapshot"])
        self.assertEqual(code, 1)
        self.assertIn("requirements.txt", json.loads(errors)["message"])

    def test_database_error_text_does_not_leak_secrets(self):
        module = SimpleNamespace(drivers=lambda: ["ODBC Driver 17 for SQL Server"])
        def failing_connect(*args, **kwargs):
            raise RuntimeError("08001", "PWD=never-print-this; Server=private")
        module.connect = failing_connect
        with patch("etl.cli.importlib.import_module", return_value=module):
            code, _, errors = self.call_cli(["extract", "--allow-database-access", "--consistency", "snapshot"])
        self.assertEqual(code, 1)
        self.assertNotIn("never-print-this", errors)
        self.assertEqual(json.loads(errors)["error_code"], "08001")

    def test_source_connection_is_read_only_and_identity_is_verified(self):
        connection = FakeConnection(scalar="WideWorldImporters")
        seen = {}
        def fake_connect(value, **kwargs):
            seen.update(kwargs)
            return connection
        result = connect(Settings.from_env({}), source=True, module=SimpleNamespace(connect=fake_connect))
        self.assertIs(result, connection)
        self.assertTrue(seen["readonly"])
        self.assertFalse(seen["autocommit"])
        self.assertEqual(connection.calls[0][0], "SELECT DB_NAME();")

    def test_wrong_effective_target_is_closed_before_writes(self):
        connection = FakeConnection(scalar="WideWorldImporters")
        with self.assertRaises(ConfigurationError):
            connect(Settings.from_env({}), source=False,
                    module=SimpleNamespace(connect=lambda *a, **k: connection))
        self.assertTrue(connection.closed)
        self.assertTrue(all(sql.startswith("SELECT") for sql, _ in connection.calls))


if __name__ == "__main__":
    unittest.main()
