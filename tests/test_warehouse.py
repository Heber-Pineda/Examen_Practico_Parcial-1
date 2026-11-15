"""Casos críticos con el mismo orquestador y algoritmo de publicación, sin SQL."""

from contextlib import contextmanager, redirect_stderr, redirect_stdout
import copy
import csv
from datetime import date
from decimal import Decimal
from io import StringIO
import json
import os
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from etl.cli import main
from etl.contracts import FULL_ENTITIES, CUSTOMERS, Run, prepare_payload
from etl.pipeline import PipelineFailure
from etl.quality import QualityBlocked, Totals, make_ready, ready_hash
from etl.warehouse import FactState, calendar_rows, fact_change, publish_facts
from etl.workflow import execute_complete
from etl.config import Settings
from etl.quality import References
from etl.warehouse_store import WarehouseStore

T0 = "2026-11-14T12:00:00.0000000"
TEND = "9999-12-31T23:59:59.9999999"


def fixture():
    return {
        "Customers": [(910001, "Cliente A", 910002, T0, TEND),
                      (910002, "Pagador B", 910002, T0, TEND)],
        "StockItems": [(920001, "Producto A", "Marca A", T0, TEND),
                       (920002, "Producto B", None, T0, TEND)],
        "Invoices": [(930001, 910001, 910002, date(2016, 5, 1), False, T0)],
        "InvoiceLines": [(940001, 930001, 920001, 2, Decimal("10.00"), Decimal("3.00"),
                          Decimal("23.00"), Decimal("6.00"), T0),
                         (940002, 930001, 920002, 1, Decimal("5.00"), Decimal("0.75"),
                          Decimal("5.75"), Decimal("1.00"), T0)],
    }


class Source:
    def __init__(self, data):
        self.data = data
        self.begun = False

    def begin(self):
        self.begun = True

    def finish(self):
        pass

    def count_entity(self, entity):
        return len(self.data[entity.table])

    def batches(self, entity, size, limit):
        rows = sorted(self.data[entity.table], key=lambda row: row[0])
        for offset in range(0, len(rows), size):
            yield rows[offset:offset + size]


class MemoryWarehouse:
    """Doble transaccional; no interpreta ni ejecuta SQL."""
    def __init__(self):
        self.raw, self.clean, self.ready, self.metadata, self.manifests = {}, {}, {}, {}, {}
        self.publications, self.stages, self.issues = {}, {}, []
        self.customers, self.products, self.facts, self.dates = {}, {}, {}, {}
        self.checkpoint = None
        self.held = self.fail_before_commit = self.lose_commit_confirmation = False

    @contextmanager
    def exclusive(self):
        if self.held:
            raise QualityBlocked("Publicación concurrente.")
        self.held = True
        try:
            yield
        finally:
            self.held = False

    def begin_run(self, run, consistency, limit):
        self.metadata[run.execution_id] = dict(run=run, limit=limit, status="EXTRACTING", full=True,
                                             ordinal=len(self.metadata) + 1)
        self.raw[run.execution_id], self.manifests[run.execution_id] = {}, {}

    def start_entity(self, run, entity):
        self.raw[run.execution_id][entity.table] = []

    def write_batch(self, run, entity, prepared, progress):
        self.raw[run.execution_id][entity.table].extend(copy.deepcopy(prepared))

    def finish_entity(self, run, entity, progress):
        self.manifests[run.execution_id][entity.source_object] = copy.deepcopy(progress)

    def finish_run(self, run, status):
        self.metadata[run.execution_id]["status"] = status

    def record_source_count(self, run, entity, expected):
        pass  # El conteo se conserva también en Progress al finalizar la entidad.

    def fail_run(self, run, progress, active, error):
        self.metadata[run.execution_id]["status"] = "FAILED"

    def quarantine_source(self, run, entity, row):
        self.issues.append((run.execution_id, entity.table, row, "RAW_CONTRACT", "ERROR"))

    def load_run(self, execution_id):
        metadata = self.metadata[execution_id]
        if metadata["limit"] is not None or not metadata["full"]:
            raise QualityBlocked("La muestra no se puede publicar.")
        old = metadata["run"]
        return Run(execution_id=execution_id, source_snapshot_id=old.source_snapshot_id,
                   started_at_utc=old.started_at_utc)

    def publication(self, execution_id):
        return copy.deepcopy(self.publications.get(execution_id))

    def raw_manifest(self, run):
        manifests = self.manifests[run.execution_id]
        if len(manifests) != 4 or not all(m.is_complete for m in manifests.values()):
            raise QualityBlocked("Raw incompleto.")
        return {table: {"rows": m.rows_read, "hash": m.manifest_hash.hex(),
                        "observed_max_LastEditedWhen": m.observed_max} for table, m in manifests.items()}

    def reset_derived(self, run):
        self.clean[run.execution_id] = {e.table: [] for e in FULL_ENTITIES}
        self.ready[run.execution_id] = []

    def entity_batches(self, zone, entity, run, size):
        rows = getattr(self, zone)[run.execution_id][entity.table]
        for offset in range(0, len(rows), size):
            yield copy.deepcopy(rows[offset:offset + size])

    def start_stage(self, run, stage, status):
        self.stages[(run.execution_id, run.attempt_id, stage)] = "RUNNING"
        self.metadata[run.execution_id]["status"] = status

    def finish_stage(self, run, stage, counts, status):
        self.stages[(run.execution_id, run.attempt_id, stage)] = "COMPLETE"
        self.metadata[run.execution_id]["status"] = status

    def fail_stage(self, run, stage, counts, error, blocked):
        self.stages[(run.execution_id, run.attempt_id, stage)] = "BLOCKED" if blocked else "FAILED"
        self.metadata[run.execution_id]["status"] = "BLOCKED" if blocked else "FAILED"

    def write_clean(self, run, entity, accepted, issues):
        self.clean[run.execution_id][entity.table].extend(copy.deepcopy(accepted))
        for row, rules in issues:
            for rule in rules:
                self.issues.append((run.execution_id, entity.table, copy.deepcopy(row), rule.rule, rule.severity))

    def write_ready(self, run, records):
        self.ready[run.execution_id].extend(copy.deepcopy(records))

    def ready_batches(self, run, size):
        rows = self.ready[run.execution_id]
        for offset in range(0, len(rows), size):
            yield copy.deepcopy(rows[offset:offset + size])

    def fact_states(self):
        return {key: FactState(row["digest"], row["deleted"], row["keys"]) for key, row in self.facts.items()}

    def write_facts(self, run, inserts, updates):
        for values, digest, keys in inserts + updates:
            self.facts[values[0]] = dict(values=values, digest=digest, keys=keys, deleted=False)

    def delete_facts(self, run, keys):
        for key in keys:
            self.facts[key]["deleted"] = True

    def publish(self, run, refs, expected, vector, size):
        baseline = copy.deepcopy((self.customers, self.products, self.facts, self.dates,
                                  self.checkpoint, self.publications))
        committed = False
        try:
            if (self.checkpoint and self.metadata[run.execution_id]["ordinal"] <=
                    self.metadata[self.checkpoint]["ordinal"]):
                raise QualityBlocked("Raw anterior a la publicación actual; recapturar un corte completo.")
            for target, source in ((self.customers, refs.customers), (self.products, refs.products)):
                for natural, values in source.items():
                    key = target[natural]["key"] if natural in target else len(target) + 1
                    target[natural] = dict(key=key, values=values, deleted=False)
                for natural in target.keys() - source.keys():
                    target[natural]["deleted"] = True
            self.dates.update({r[0]: r for r in calendar_rows(v[3] for v in refs.invoices.values())})
            delta = publish_facts(self, run, {k: v["key"] for k, v in self.customers.items()},
                                  {k: v["key"] for k, v in self.products.items()}, size)
            active = {k: row["values"] for k, row in self.facts.items() if not row["deleted"]}
            wanted = {values[0]: values for values, _ in self.ready[run.execution_id]}
            if active != wanted:
                raise QualityBlocked("Diferencias por línea.")
            totals = Totals()
            for values in active.values():
                totals.add_ready(values)
            if totals.result() != expected:
                raise QualityBlocked("Reconciliación incorrecta.")
            if self.fail_before_commit:
                raise RuntimeError("Falla sintética antes del commit.")
            result = dict(status="PUBLISHED", execution_id=str(run.execution_id), changes=delta,
                          reconciliation=totals.result(), load_type="incremental" if self.checkpoint else "initial")
            self.checkpoint = run.execution_id
            self.publications[run.execution_id] = result
            self.metadata[run.execution_id]["status"] = "PUBLISHED"
            self.stages[(run.execution_id, run.attempt_id, "PUBLISH")] = "COMPLETE"
            committed = True
            if self.lose_commit_confirmation:
                raise RuntimeError("Confirmación de commit perdida sintéticamente.")
            return result
        except BaseException:
            if not committed:
                (self.customers, self.products, self.facts, self.dates,
                 self.checkpoint, self.publications) = baseline
            raise


class WarehouseTests(unittest.TestCase):
    def setUp(self):
        self.store = MemoryWarehouse()

    def execute(self, data=None, run=None):
        return execute_complete(Source(fixture() if data is None else data), self.store, run or Run(),
                                batch_size=1, consistency="stable-window")

    def test_initial_load_roles_and_reconciliation(self):
        result = self.execute()
        self.assertEqual(result["status"], "PUBLISHED")
        self.assertEqual(result["changes"], dict(inserted=2, updated=0, unchanged=0, deleted=0, reactivated=0))
        self.assertEqual(result["reconciliation"], dict(lines=2, units=3, base_known="25.00", base_missing=0,
                         base="25.00", tax="3.75", extended="28.75", profit="7.00", invoices=1))
        fact = self.store.facts[940001]
        self.assertEqual(fact["keys"][1], self.store.customers[910001]["key"])
        self.assertEqual(fact["keys"][2], self.store.customers[910002]["key"])
        self.assertNotEqual(fact["keys"][1], fact["keys"][2])

    def test_repeated_snapshot_is_idempotent_and_keeps_keys(self):
        first = self.execute()
        keys = {k: v["key"] for k, v in self.store.customers.items()}
        second = self.execute()
        self.assertEqual(second["changes"], dict(inserted=0, updated=0, unchanged=2, deleted=0, reactivated=0))
        self.assertEqual(len(self.store.facts), 2)
        self.assertEqual(keys, {k: v["key"] for k, v in self.store.customers.items()})
        self.assertNotEqual(first["execution_id"], second["execution_id"])

    def test_published_execution_is_no_op_without_source(self):
        run = Run()
        self.execute(run=run)
        before = copy.deepcopy(self.store.facts)
        result = execute_complete(None, self.store, run, batch_size=1, resume=True)
        self.assertTrue(result["reused_publication"])
        self.assertEqual(result["changes"]["inserted"], 0)
        self.assertEqual(before, self.store.facts)
        self.assertEqual(len(self.store.publications), 1)

    def test_header_only_date_and_billing_changes_reprocess_both_lines(self):
        self.execute()
        data = fixture()
        data["Invoices"][0] = (930001, 910001, 910001, date(2016, 5, 2), False, T0)
        result = self.execute(data)
        self.assertEqual(result["changes"]["updated"], 2)
        self.assertEqual(self.store.facts[940001]["values"][4:6], (910001, date(2016, 5, 2)))

    def test_scd1_preserves_keys_and_does_not_replace_invoice_bill_to(self):
        self.execute()
        key = self.store.customers[910001]["key"]
        data = fixture()
        data["Customers"][0] = (910001, "Cliente actualizado", 910001, T0, TEND)
        data["StockItems"][0] = (920001, "Producto actualizado", "Otra marca", T0, TEND)
        result = self.execute(data)
        self.assertEqual(self.store.customers[910001]["key"], key)
        self.assertEqual(self.store.facts[940001]["values"][4], 910002)
        self.assertEqual(result["changes"]["updated"], 0)

    def test_backdated_modification_and_late_insert_are_detected(self):
        self.execute()
        data = fixture()
        data["InvoiceLines"][0] = (940001, 930001, 920001, 3, Decimal("10"), Decimal("4.50"),
                                   Decimal("34.50"), Decimal("9"), "2013-01-01T00:00:00.0000000")
        result = self.execute(data)
        self.assertEqual(result["changes"]["updated"], 1)
        self.assertEqual(result["reconciliation"]["base"], "35.00")
        data["InvoiceLines"].append((940003, 930001, 920001, 1, Decimal("4"), Decimal("0.60"),
                                      Decimal("4.60"), Decimal("1"), T0))
        result = self.execute(data)
        self.assertEqual(result["changes"]["inserted"], 1)
        self.assertEqual(len(self.store.facts), 3)

    def test_deletions_empty_cut_and_reappearance_preserve_identity(self):
        self.execute()
        data = fixture()
        data["InvoiceLines"].pop(0)
        self.assertEqual(self.execute(data)["changes"]["deleted"], 1)
        self.assertTrue(self.store.facts[940001]["deleted"])
        data["Invoices"], data["InvoiceLines"] = [], []
        empty = self.execute(data)
        self.assertEqual(empty["reconciliation"]["lines"], 0)
        self.assertEqual(empty["reconciliation"]["base"], "0.00")
        revived = self.execute()
        self.assertEqual(revived["changes"]["updated"], 2)
        self.assertEqual(revived["changes"]["reactivated"], 2)
        self.assertEqual(len(self.store.facts), 2)

    def test_null_price_and_null_brand_are_preserved(self):
        data = fixture()
        row = list(data["InvoiceLines"][0]); row[4] = None
        data["InvoiceLines"][0] = tuple(row)
        result = self.execute(data)
        self.assertIsNone(result["reconciliation"]["base"])
        self.assertEqual(result["reconciliation"]["extended"], "28.75")
        self.assertIsNone(self.store.products[920002]["values"][2])
        self.assertNotEqual(self.store.facts[940002]["keys"][3], 0)
        self.assertTrue(any(issue[3] == "DQ-06" for issue in self.store.issues))

    def test_credit_flag_and_signed_amounts_are_preserved(self):
        data = fixture()
        invoice = list(data["Invoices"][0]); invoice[4] = True; data["Invoices"][0] = tuple(invoice)
        line = list(data["InvoiceLines"][0]); line[5:8] = [Decimal("-3"), Decimal("-23"), Decimal("-6")]
        data["InvoiceLines"][0] = tuple(line)
        self.execute(data)
        values = self.store.facts[940001]["values"]
        self.assertTrue(values[6])
        self.assertEqual(values[9:12], (Decimal("-3"), Decimal("-23"), Decimal("-6")))
        self.assertTrue(any(issue[3] == "M-04" for issue in self.store.issues))

    def test_zero_negative_prices_and_extended_amount_are_not_rewritten(self):
        for price in (Decimal("0"), Decimal("-1")):
            with self.subTest(price=price):
                data = fixture()
                row = list(data["InvoiceLines"][0]); row[4] = price; row[6] = Decimal("99")
                data["InvoiceLines"][0] = tuple(row)
                result = self.execute(data)
                self.assertEqual(self.store.facts[940001]["values"][8], price)
                self.assertEqual(result["reconciliation"]["extended"], "104.75")
                self.assertTrue(any(issue[3] == "DQ-11" for issue in self.store.issues))

    def test_invalid_quantity_and_orphans_quarantine_and_keep_prior_publication(self):
        self.execute()
        baseline, checkpoint = copy.deepcopy(self.store.facts), self.store.checkpoint
        mutations = [("InvoiceLines", 3, 0), ("InvoiceLines", 1, 999),
                     ("InvoiceLines", 2, 999), ("Invoices", 1, 999), ("Customers", 2, 999)]
        for table, column, value in mutations:
            with self.subTest(table=table, column=column):
                data = fixture(); row = list(data[table][0]); row[column] = value; data[table][0] = tuple(row)
                with self.assertRaises(PipelineFailure) as caught:
                    self.execute(data)
                self.assertEqual(caught.exception.report["status"], "BLOCKED")
                self.assertEqual(self.store.facts, baseline)
                self.assertEqual(self.store.checkpoint, checkpoint)
                self.assertTrue(any(issue[4] == "ERROR" for issue in self.store.issues))

    def test_source_type_error_and_duplicate_are_quarantined_before_publication(self):
        for malformed in (True, False):
            with self.subTest(malformed=malformed):
                data = fixture()
                if malformed:
                    row = list(data["InvoiceLines"][0]); row[4] = 10.0; data["InvoiceLines"][0] = tuple(row)
                else:
                    data["InvoiceLines"].append(data["InvoiceLines"][0])
                with self.assertRaises(PipelineFailure):
                    self.execute(data)
                self.assertIsNone(self.store.checkpoint)
                self.assertEqual(self.store.facts, {})
                self.assertTrue(any(issue[3] == "RAW_CONTRACT" for issue in self.store.issues))

    def test_failure_rolls_back_dw_and_resume_reuses_raw(self):
        self.execute()
        baseline, checkpoint = copy.deepcopy(self.store.facts), self.store.checkpoint
        run = Run()
        data = fixture(); row = list(data["InvoiceLines"][0]); row[7] = Decimal("8"); data["InvoiceLines"][0] = tuple(row)
        self.store.fail_before_commit = True
        with self.assertRaises(PipelineFailure):
            self.execute(data, run)
        self.assertEqual(self.store.facts, baseline)
        self.assertEqual(self.store.checkpoint, checkpoint)
        raw = copy.deepcopy(self.store.raw[run.execution_id])
        self.store.fail_before_commit = False
        recovered = execute_complete(None, self.store, run, batch_size=1, resume=True)
        self.assertEqual(recovered["changes"]["updated"], 1)
        self.assertEqual(self.store.raw[run.execution_id], raw)
        self.assertIn("FAILED", self.store.stages.values())

    def test_lost_commit_confirmation_recovers_publication(self):
        self.store.lose_commit_confirmation = True
        result = self.execute()
        self.assertTrue(result["publication_recovered"])
        self.assertEqual(len(self.store.publications), 1)
        self.assertEqual(len(self.store.facts), 2)

    def test_old_failed_raw_cannot_roll_back_a_newer_publication(self):
        self.execute()
        old = Run()
        self.store.fail_before_commit = True
        with self.assertRaises(PipelineFailure):
            self.execute(run=old)
        self.store.fail_before_commit = False
        newer = fixture()
        row = list(newer["InvoiceLines"][0]); row[7] = Decimal("9"); newer["InvoiceLines"][0] = tuple(row)
        self.execute(newer)
        current = self.store.checkpoint
        state = copy.deepcopy(self.store.facts)
        with self.assertRaises(PipelineFailure) as caught:
            execute_complete(None, self.store, old, batch_size=1, resume=True)
        self.assertEqual(caught.exception.report["status"], "BLOCKED")
        self.assertIn("recapturar", caught.exception.report["error_detail"])
        self.assertEqual(current, self.store.checkpoint)
        self.assertEqual(state, self.store.facts)

    def test_partial_samples_are_preserved_and_cannot_be_resumed(self):
        sample = Run()
        self.store.raw[sample.execution_id] = {"Invoices": [("muestra original",)]}
        self.store.metadata[sample.execution_id] = dict(run=sample, limit=100, full=False, status="RAW_PARTIAL")
        before = copy.deepcopy(self.store.raw[sample.execution_id])
        self.execute()
        self.assertEqual(before, self.store.raw[sample.execution_id])
        with self.assertRaises(QualityBlocked):
            execute_complete(None, self.store, sample, batch_size=1, resume=True)

    def test_manifest_tampering_or_truncation_blocks_resume(self):
        for truncate in (True, False):
            with self.subTest(truncate=truncate):
                store = self.store = MemoryWarehouse()
                run = Run(); store.fail_before_commit = True
                with self.assertRaises(PipelineFailure):
                    self.execute(run=run)
                if truncate:
                    store.raw[run.execution_id]["InvoiceLines"].pop()
                else:
                    values, digest = store.raw[run.execution_id]["InvoiceLines"][0]
                    changed = list(values); changed[3] = 9
                    store.raw[run.execution_id]["InvoiceLines"][0] = (tuple(changed), digest)
                store.fail_before_commit = False
                with self.assertRaises(PipelineFailure) as caught:
                    execute_complete(None, store, run, batch_size=1, resume=True)
                self.assertEqual(caught.exception.report["status"], "BLOCKED")
                self.assertIsNone(store.checkpoint)

    def test_truncated_capture_is_detected_by_independent_source_count(self):
        source = Source(fixture())
        with patch.object(source, "count_entity", return_value=99):
            with self.assertRaises(PipelineFailure) as caught:
                execute_complete(source, self.store, Run(), batch_size=1, consistency="stable-window")
        self.assertEqual(caught.exception.report["phase"], "extract")
        self.assertIsNone(self.store.checkpoint)
        self.assertFalse(any(m.is_complete for manifests in self.store.manifests.values() for m in manifests.values()))

    def test_exclusive_execution_blocks_capture_when_lock_is_held(self):
        self.store.held = True
        source = Source(fixture())
        with self.assertRaises(QualityBlocked):
            execute_complete(source, self.store, Run(), batch_size=1, consistency="stable-window")
        self.assertFalse(source.begun)

    def test_dimension_resolution_change_updates_same_fact(self):
        digest = b"x" * 32
        self.assertEqual(fact_change(FactState(digest, False, (1, 0, 0, 0)), digest, (1, 2, 3, 4)), "updated")


class FullContractTests(unittest.TestCase):
    def test_four_projections_match_dictionary_and_timestamp_precision(self):
        root = Path(__file__).resolve().parents[1]
        with (root / "database/normalized/Diccionario_Datos_WideWorldImporters.csv").open(encoding="utf-8-sig") as handle:
            rows = {(r["schema_name"], r["table_name"], r["column_name"]): r
                    for r in csv.DictReader(handle, delimiter=";")}
        for entity in FULL_ENTITIES:
            for column in entity.columns:
                source = rows[(entity.schema, entity.table, column.name)]
                self.assertEqual(column.kind.split("(")[0], source["data_type"])
                self.assertEqual(column.nullable, source["is_nullable"] == "SI")
        self.assertIn("[Warehouse].[StockItems]", FULL_ENTITIES[1].select_sql())
        self.assertIn("CONVERT(varchar(27), [ValidTo], 126)", CUSTOMERS.select_sql())

    def test_calendar_covers_complete_years_and_iso_weekdays(self):
        rows = list(calendar_rows([date(2016, 5, 1)]))
        self.assertEqual(len(rows), 366)
        self.assertEqual(rows[0][1], date(2016, 1, 1))
        self.assertEqual(rows[-1][1], date(2016, 12, 31))
        monday = next(r for r in rows if r[1] == date(2016, 5, 2))
        self.assertEqual(monday[6:], (1, False))

    def test_full_plan_never_imports_driver_and_has_four_queries(self):
        output = StringIO()
        with (patch.dict(os.environ, {}, clear=True), redirect_stdout(output),
              patch("etl.cli.importlib.import_module") as imported):
            self.assertEqual(main(["plan", "--full"]), 0)
        imported.assert_not_called()
        result = json.loads(output.getvalue())
        self.assertEqual(len(result["queries_for_review"]), 4)
        self.assertFalse(result["database_access"])

    def test_full_run_cannot_publish_a_limited_sample(self):
        with redirect_stderr(StringIO()), self.assertRaises(SystemExit), patch("etl.cli.importlib.import_module") as imported:
            main(["run", "--max-rows-per-table", "100"])
        imported.assert_not_called()

    def test_cli_resume_opens_only_target_and_returns_published_no_op(self):
        store = MemoryWarehouse()
        run = Run()
        execute_complete(Source(fixture()), store, run, batch_size=1, consistency="stable-window")
        output = StringIO()
        connection = SimpleNamespace(close=lambda: None)
        module = SimpleNamespace(drivers=lambda: ["ODBC Driver 17 for SQL Server"])
        with (patch.dict(os.environ, {}, clear=True), redirect_stdout(output),
              patch("etl.cli.connect", return_value=connection) as opened,
              patch("etl.cli.WarehouseStore", return_value=store),
              patch("etl.cli.importlib.import_module", return_value=module)):
            code = main(["run", "--allow-database-access", "--execution-id", str(run.execution_id)])
        self.assertEqual(code, 0)
        opened.assert_called_once()
        self.assertFalse(opened.call_args.kwargs["source"])
        self.assertTrue(json.loads(output.getvalue())["reused_publication"])

    def test_migration_never_drops_or_deletes_raw(self):
        sql = (Path(__file__).resolve().parents[1] / "database/scripts/003_complete_sales_pipeline.sql").read_text(encoding="utf-8")
        self.assertNotRegex(sql.upper(), r"(?:DELETE FROM|TRUNCATE TABLE|DROP TABLE)\s+\[RAW\]")
        self.assertIn("[CK_etl_execution_full]", sql)


class RecordingConnection:
    def __init__(self, fail_commit=False):
        self.pending, self.committed = [], []
        self.commits = self.rollbacks = self.open_cursors = 0
        self.fail_commit = fail_commit
        self.results = []

    def cursor(self):
        connection = self
        connection.open_cursors += 1
        class Cursor:
            rowcount = 1
            description = None
            def execute(self, sql, *parameters):
                if sql.count("?") != len(parameters):
                    raise AssertionError("Cantidad incorrecta de parámetros SQL.")
                connection.pending.append((sql, parameters))
                has_result = sql.startswith("SELECT") or "sp_getapplock" in sql
                self.result = connection.results.pop(0) if has_result and connection.results else []
                self.description = [("value",)] if has_result else None
                return self
            def executemany(self, sql, rows):
                for parameters in rows:
                    self.execute(sql, *parameters)
            def close(self):
                connection.open_cursors -= 1
            def fetchall(self):
                return self.result
            def fetchone(self):
                return self.result[0]
        return Cursor()

    def commit(self):
        if self.fail_commit:
            raise RuntimeError("Falla transaccional sintética.")
        self.commits += 1
        self.committed.extend(self.pending)
        self.pending.clear()

    def rollback(self):
        self.rollbacks += 1
        self.pending.clear()


class SqlAdapterTests(unittest.TestCase):
    def test_session_lock_is_released_on_failure_and_sets_transaction_options(self):
        connection = RecordingConnection()
        connection.results = [[(0,)]]
        store = WarehouseStore(connection, Settings.from_env({}))
        with self.assertRaises(QualityBlocked):
            with store.exclusive():
                raise QualityBlocked("Fallo aislado.")
        statements = "\n".join(sql for sql, _ in connection.committed)
        self.assertIn("SET NOCOUNT OFF; SET XACT_ABORT ON;", statements)
        self.assertIn("sp_getapplock", statements)
        self.assertIn("sp_releaseapplock", statements)
        self.assertEqual(connection.open_cursors, 0)

    def test_fact_dml_is_parameterized_and_does_not_commit_between_batches(self):
        connection = RecordingConnection()
        store = WarehouseStore(connection, Settings.from_env({}))
        values = (940001, 930001, 920001, 910001, 910002, date(2016, 5, 1), False,
                  2, Decimal("10"), Decimal("3"), Decimal("23"), Decimal("6"), Decimal("20"))
        record = (values, ready_hash(values), (20160501, 1, 2, 1))
        with store.transaction():
            store.write_facts(Run(), [record], [record])
            store.delete_facts(Run(), [940002])
            self.assertEqual(connection.commits, 0)
        self.assertEqual(connection.commits, 1)
        self.assertEqual(len(connection.committed), 3)
        self.assertEqual(connection.open_cursors, 0)

    def test_dimensions_preserve_parameter_types_and_participate_in_outer_transaction(self):
        connection = RecordingConnection()
        store = WarehouseStore(connection, Settings.from_env({}))
        refs = References(invoices={930001: fixture()["Invoices"][0]})
        with patch.object(store, "rows", side_effect=[[(910001, 1), (910002, 2)], [(920001, 1)]]):
            with store.transaction():
                customers, products, _ = store.dimensions(Run(), refs, 500)
                self.assertEqual(connection.commits, 0)
        self.assertEqual(customers[910002], 2)
        self.assertEqual(products[920001], 1)
        self.assertEqual(connection.commits, 1)

    def test_publication_and_checkpoint_commit_together_or_rollback(self):
        for fail in (False, True):
            with self.subTest(fail=fail):
                connection = RecordingConnection(fail_commit=fail)
                store = WarehouseStore(connection, Settings.from_env({}))
                with (patch.object(store, "rows", side_effect=[[], [(1,)]]),
                      patch.object(store, "dimensions", return_value=({}, {}, {})),
                      patch.object(store, "reconcile", return_value={"line_differences": 0}),
                      patch("etl.warehouse_store.publish_facts", return_value={"inserted": 0})):
                    if fail:
                        with self.assertRaises(RuntimeError):
                            store.publish(Run(), References(), {}, {}, 500)
                        self.assertEqual(connection.committed, [])
                        self.assertEqual(connection.rollbacks, 1)
                    else:
                        store.publish(Run(), References(), {}, {}, 500)
                        self.assertEqual(connection.commits, 1)
                        statements = "\n".join(sql for sql, _ in connection.committed)
                        self.assertIn("INSERT INTO [audit].[etl_publication]", statements)
                        self.assertIn("INSERT INTO [audit].[etl_checkpoint]", statements)
                        self.assertIn("status='PUBLISHED'", statements)

    def test_checkpoint_from_different_source_is_rejected_before_dw_writes(self):
        connection = RecordingConnection()
        store = WarehouseStore(connection, Settings.from_env({}))
        with (patch.object(store, "rows", return_value=[("id", "other-server", "WideWorldImporters", "sales-raw-v1", "sales-clean-v1", 1)]),
              patch.object(store, "dimensions") as dimensions):
            with self.assertRaises(QualityBlocked):
                store.publish(Run(), References(), {}, {}, 500)
        dimensions.assert_not_called()
        self.assertEqual(connection.committed, [])

    def test_sql_publisher_rejects_stale_capture_before_dimension_writes(self):
        connection = RecordingConnection()
        store = WarehouseStore(connection, Settings.from_env({}))
        prior = [("id", store.settings.source_server, store.settings.source_database,
                  "sales-raw-v1", "sales-clean-v1", 3)]
        with (patch.object(store, "rows", side_effect=[prior, [(2,)]]),
              patch.object(store, "dimensions") as dimensions):
            with self.assertRaises(QualityBlocked):
                store.publish(Run(), References(), {}, {}, 500)
        dimensions.assert_not_called()
        self.assertEqual(connection.committed, [])

    def test_keyset_pages_handle_negative_and_zero_keys_without_open_cursors(self):
        connection = RecordingConnection()
        store = WarehouseStore(connection, Settings.from_env({}))
        raw = [(key, "Cliente", key, T0, TEND, b"x" * 32) for key in (-1, 0, 2)]
        connection.results = [raw[:2], raw[2:], []]
        batches = []
        for batch in store.entity_batches("raw", CUSTOMERS, Run(), 2):
            self.assertEqual(connection.open_cursors, 0)
            batches.append(batch)
        self.assertEqual([r[0][0] for b in batches for r in b], [-1, 0, 2])
        self.assertEqual(connection.pending[1][1][-1], 0)

    def test_all_clean_and_ready_dml_parameter_counts_match(self):
        connection = RecordingConnection()
        store = WarehouseStore(connection, Settings.from_env({}))
        run = Run()
        for entity in FULL_ENTITIES:
            values, digest = prepare_payload(entity, fixture()[entity.table][0])
            store.write_clean(run, entity, [(values, digest)], [])
        values = make_ready(fixture()["InvoiceLines"][0], fixture()["Invoices"][0])
        store.write_ready(run, [(values, ready_hash(values))])
        self.assertEqual(connection.commits, 5)

    def test_dimension_content_mismatch_blocks_reconciliation(self):
        connection = RecordingConnection()
        store = WarehouseStore(connection, Settings.from_env({}))
        # Dos EXCEPT de hechos, control de claves, primer EXCEPT de dimensión.
        connection.results = [[(0,)], [(0,)], [(0,)], [(1,)]]
        expected = Totals().result()
        with patch.object(store, "sql_profile", return_value=expected):
            with self.assertRaises(QualityBlocked):
                store.reconcile(Run(), expected)
        self.assertIn("CustomerName_bytes", connection.pending[-1][0])


if __name__ == "__main__":
    unittest.main()
