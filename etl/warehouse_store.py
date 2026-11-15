"""Adaptador SQL: staging por ejecución y publicación atómica del DW."""

from contextlib import contextmanager
from datetime import date
from decimal import Decimal
import json
from uuid import UUID

from .contracts import FULL_ENTITIES, SCHEMA_VERSION, Run, timestamp_text, utc_now
from .quality import (QUALITY_VERSION, READY_COLUMNS, Issue, QualityBlocked, json_text, profile_result)
from .staging import RawStore
from .warehouse import FactState, calendar_rows, publish_facts

FULL_PIPELINE = "sales_to_warehouse"


class WarehouseStore(RawStore):
    def __init__(self, connection, settings):
        super().__init__(connection, settings, entities=FULL_ENTITIES,
                         pipeline_name=FULL_PIPELINE, rules_version=QUALITY_VERSION)

    def rows(self, sql, *parameters):
        cursor = self.connection.cursor()
        try:
            cursor.execute(sql, *parameters)
            return [tuple(row) for row in cursor.fetchall()]
        finally:
            cursor.close()

    @contextmanager
    def exclusive(self):
        cursor = self.connection.cursor()
        try:
            cursor.execute("SET NOCOUNT OFF; SET XACT_ABORT ON;")
            cursor.execute(
                "DECLARE @r int; EXEC @r = sys.sp_getapplock "
                "@Resource=N'WWI:sales_to_warehouse', @LockMode='Exclusive', "
                "@LockOwner='Session', @LockTimeout=0; SELECT @r;")
            while cursor.description is None:
                if not cursor.nextset():
                    raise QualityBlocked("No se obtuvo el estado del bloqueo ETL.")
            if cursor.fetchone()[0] < 0:
                raise QualityBlocked("Ya existe otra ejecución completa del ETL.")
            self.connection.commit()
        finally:
            cursor.close()
        try:
            yield
        finally:
            try:
                with self.transaction() as cursor:
                    cursor.execute("EXEC sys.sp_releaseapplock @Resource=N'WWI:sales_to_warehouse', @LockOwner='Session';")
            except Exception:
                pass  # Cerrar la conexión también libera el bloqueo de sesión.

    def publication(self, execution_id):
        rows = self.rows("SELECT result_json FROM [audit].[etl_publication] WHERE execution_id = ?;",
                         str(execution_id))
        return json.loads(rows[0][0]) if rows else None

    def load_run(self, execution_id):
        rows = self.rows(
            "SELECT source_snapshot_id, started_at_utc, pipeline_name, max_rows_per_table, "
            "schema_version, rules_version, source_instance, source_database "
            "FROM [audit].[etl_execution] WHERE execution_id = ?;", str(execution_id))
        if not rows:
            raise QualityBlocked("No existe la ejecución solicitada.")
        snapshot, started, pipeline, limit, schema, rules, server, database = rows[0]
        if (pipeline != FULL_PIPELINE or limit is not None or schema != SCHEMA_VERSION
                or rules != QUALITY_VERSION or server.casefold() != self.settings.source_server.casefold()
                or database.casefold() != self.settings.source_database.casefold()):
            raise QualityBlocked("La ejecución no es un corte completo del alcance y origen configurados.")
        return Run(execution_id=UUID(str(execution_id)), source_snapshot_id=UUID(str(snapshot)),
                   started_at_utc=started)

    def raw_manifest(self, run):
        rows = self.rows(
            "SELECT source_schema, source_table, rows_read, rows_inserted, is_complete, status, "
            "manifest_hash, CONVERT(varchar(27), observed_max_LastEditedWhen, 126), source_rows_expected "
            "FROM [audit].[etl_entity] WHERE execution_id = ?;", str(run.execution_id))
        vector = {}
        for schema, table, read, inserted, complete, status, digest, observed, expected in rows:
            if (not complete or status != "RAW_COMPLETE" or read != inserted
                    or expected is None or read != expected or digest is None):
                raise QualityBlocked("Raw incompleto o no reconciliado; se requiere recapturar un corte nuevo.")
            vector[f"{schema}.{table}"] = {
                "rows": int(read), "hash": bytes(digest).hex(),
                "observed_max_LastEditedWhen": timestamp_text(observed) if observed else None}
        if set(vector) != {entity.source_object for entity in FULL_ENTITIES}:
            raise QualityBlocked("Se requieren manifiestos completos de las cuatro entidades.")
        return vector

    def record_source_count(self, run, entity, expected):
        with self.transaction() as cursor:
            cursor.execute("UPDATE [audit].[etl_entity] SET source_rows_expected = ? "
                           "WHERE execution_id = ? AND source_table = ?;",
                           expected, str(run.execution_id), entity.table)

    @staticmethod
    def projection(entity):
        return ", ".join(
            f"CONVERT(varchar(27), [{c.name}], 126) AS [{c.name}]"
            if c.kind == "datetime2(7)" else f"[{c.name}]" for c in entity.columns)

    def entity_batches(self, zone, entity, run, batch_size):
        if zone not in {"raw", "clean"} or entity not in FULL_ENTITIES:
            raise ValueError("Proyección de staging no autorizada.")
        last = None
        while True:
            after = f" AND [{entity.key}] > ?" if last is not None else ""
            parameters = (batch_size, str(run.execution_id)) + ((last,) if last is not None else ())
            rows = self.rows(
                f"SELECT TOP (?) {self.projection(entity)}, payload_hash "
                f"FROM [{zone}].[{entity.table}] WHERE execution_id = ?{after} ORDER BY [{entity.key}];",
                *parameters)
            if not rows:
                break
            # Cursor cerrado antes de escribir: no requiere activar MARS.
            yield [(row[:-1], bytes(row[-1])) for row in rows]
            last = rows[-1][0]

    def reset_derived(self, run):
        # Solo derivados del lote a reintentar. Raw y las muestras nunca se borran.
        with self.transaction() as cursor:
            cursor.execute("DELETE FROM [ready].[Ventas] WHERE execution_id = ?;", str(run.execution_id))
            for entity in FULL_ENTITIES:
                cursor.execute(f"DELETE FROM [clean].[{entity.table}] WHERE execution_id = ?;", str(run.execution_id))

    def start_stage(self, run, stage, status):
        with self.transaction() as cursor:
            cursor.execute(
                "INSERT INTO [audit].[etl_stage] (execution_id, attempt_id, stage_name, started_at_utc, status) "
                "VALUES (?, ?, ?, ?, 'RUNNING');", str(run.execution_id), str(run.attempt_id), stage, utc_now())
            cursor.execute("UPDATE [audit].[etl_execution] SET status = ?, ended_at_utc = NULL, error_type = NULL, "
                           "error_code = NULL, error_detail = NULL WHERE execution_id = ?;", status, str(run.execution_id))

    def finish_stage(self, run, stage, counts, status):
        with self.transaction() as cursor:
            cursor.execute(
                "UPDATE [audit].[etl_stage] SET status = 'COMPLETE', ended_at_utc = ?, counts_json = ? "
                "WHERE execution_id = ? AND attempt_id = ? AND stage_name = ?;",
                utc_now(), json_text(counts), str(run.execution_id), str(run.attempt_id), stage)
            cursor.execute("UPDATE [audit].[etl_execution] SET status = ? WHERE execution_id = ?;",
                           status, str(run.execution_id))

    @staticmethod
    def issue_parameters(run, entity, row, issues):
        # Preservar también valores incompatibles (p.ej. float NaN) como texto.
        payload = json_text([v if isinstance(v, (str, int, date, Decimal, type(None))) else repr(v) for v in row])
        key = str(row[0])[:128] if row and row[0] is not None else None
        return [(str(run.execution_id), str(run.attempt_id), entity.source_object, key,
                 issue.rule, issue.severity, utc_now(), payload, issue.detail) for issue in issues]

    def insert_issues(self, cursor, run, entity, records):
        parameters = [p for row, issues in records for p in self.issue_parameters(run, entity, row, issues)]
        if parameters:
            cursor.executemany(
                "INSERT INTO [audit].[etl_quarantine] (execution_id, attempt_id, source_object, source_key, "
                "rule_id, severity, created_at_utc, payload_json, detail) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?);",
                parameters)

    def quarantine_source(self, run, entity, row):
        with self.transaction() as cursor:
            self.insert_issues(cursor, run, entity, [(row, [Issue(
                "RAW_CONTRACT", "ERROR", "Tipo o clave de origen incompatible; extracción detenida.")])])

    def write_clean(self, run, entity, accepted, issues):
        columns = ", ".join(f"[{c.name}]" for c in entity.columns)
        values_sql = ", ".join("CONVERT(datetime2(7), ?, 126)" if c.kind == "datetime2(7)" else "?"
                               for c in entity.columns)
        with self.transaction() as cursor:
            if accepted:
                cursor.executemany(
                    f"INSERT INTO [clean].[{entity.table}] (execution_id, rules_version, payload_hash, {columns}) "
                    f"VALUES (?, ?, ?, {values_sql});",
                    [(str(run.execution_id), QUALITY_VERSION, digest, *values) for values, digest in accepted])
            self.insert_issues(cursor, run, entity, issues)

    def write_ready(self, run, records):
        if records:
            with self.transaction() as cursor:
                cursor.executemany(
                    f"INSERT INTO [ready].[Ventas] (execution_id, payload_hash, {', '.join(READY_COLUMNS)}) "
                    f"VALUES ({', '.join(['?'] * (2 + len(READY_COLUMNS)))});",
                    [(str(run.execution_id), digest, *values) for values, digest in records])

    def ready_batches(self, run, batch_size):
        last = None
        while True:
            after = " AND InvoiceLineID > ?" if last is not None else ""
            params = (batch_size, str(run.execution_id)) + ((last,) if last is not None else ())
            rows = self.rows(f"SELECT TOP (?) {', '.join(READY_COLUMNS)}, payload_hash FROM [ready].[Ventas] "
                             f"WHERE execution_id = ?{after} ORDER BY InvoiceLineID;", *params)
            if not rows:
                break
            yield [(row[:-1], bytes(row[-1])) for row in rows]
            last = rows[-1][0]

    def fact_states(self):
        return {row[0]: FactState(bytes(row[1]), bool(row[2]), tuple(row[3:])) for row in self.rows(
            "SELECT InvoiceLineID, payload_hash, EsEliminadoFuente, FechaKey, ClienteVentaKey, "
            "ClienteFacturacionKey, ProductoKey FROM [dw].[FactVentas];")}

    def write_facts(self, run, inserts, updates):
        metadata_columns = ("FechaKey", "ClienteVentaKey", "ClienteFacturacionKey", "ProductoKey",
                            "payload_hash", "execution_id", "publication_id", "EsEliminadoFuente")
        columns = READY_COLUMNS + metadata_columns
        def parameters(record):
            values, digest, keys = record
            return (*values, *keys, digest, str(run.execution_id), str(run.execution_id), False)
        cursor = self.connection.cursor()
        try:
            if inserts:
                cursor.executemany(f"INSERT INTO [dw].[FactVentas] ({', '.join(columns)}) "
                                   f"VALUES ({', '.join(['?'] * len(columns))});", [parameters(r) for r in inserts])
            if updates:
                cursor.executemany("UPDATE [dw].[FactVentas] SET " + ", ".join(f"[{c}] = ?" for c in columns[1:])
                                   + " WHERE InvoiceLineID = ?;",
                                   [parameters(r)[1:] + (r[0][0],) for r in updates])
        finally:
            cursor.close()

    def delete_facts(self, run, keys):
        cursor = self.connection.cursor()
        try:
            cursor.executemany(
                "UPDATE [dw].[FactVentas] SET EsEliminadoFuente = 1, execution_id = ?, publication_id = ? "
                "WHERE InvoiceLineID = ? AND EsEliminadoFuente = 0;",
                [(str(run.execution_id), str(run.execution_id), key) for key in keys])
        finally:
            cursor.close()

    def dimensions(self, run, refs, batch_size):
        counts = {}
        cursor = self.connection.cursor()
        try:
            for table, clean, natural, key, assignments in (
                ("DimCliente", "Customers", "CustomerID", "ClienteKey",
                 "CustomerName = c.CustomerName, BillToCustomerIDActual = c.BillToCustomerID"),
                ("DimProducto", "StockItems", "StockItemID", "ProductoKey",
                 "StockItemName = c.StockItemName, Brand = c.Brand")):
                cursor.execute(f"UPDATE d SET {assignments}, payload_hash = c.payload_hash, "
                               "EsEliminadoFuente = 0, execution_id = ?, publication_id = ? "
                               f"FROM [dw].[{table}] d JOIN [clean].[{clean}] c ON c.{natural} = d.{natural} "
                               "WHERE c.execution_id = ? AND (d.payload_hash <> c.payload_hash OR d.EsEliminadoFuente = 1);",
                               str(run.execution_id), str(run.execution_id), str(run.execution_id))
                updated = cursor.rowcount
                source_attrs = ("CustomerName, BillToCustomerID" if table == "DimCliente" else "StockItemName, Brand")
                target_attrs = ("CustomerName, BillToCustomerIDActual" if table == "DimCliente" else "StockItemName, Brand")
                cursor.execute(f"INSERT INTO [dw].[{table}] ({natural}, {target_attrs}, payload_hash, "
                               "EsEliminadoFuente, execution_id, publication_id) "
                               f"SELECT c.{natural}, {', '.join('c.' + c for c in source_attrs.split(', '))}, "
                               f"c.payload_hash, 0, ?, ? FROM [clean].[{clean}] c WHERE c.execution_id = ? "
                               f"AND NOT EXISTS (SELECT 1 FROM [dw].[{table}] d WHERE d.{natural} = c.{natural});",
                               str(run.execution_id), str(run.execution_id), str(run.execution_id))
                inserted = cursor.rowcount
                cursor.execute(f"UPDATE d SET EsEliminadoFuente = 1, execution_id = ?, publication_id = ? "
                               f"FROM [dw].[{table}] d WHERE d.{key} <> 0 AND d.EsEliminadoFuente = 0 "
                               f"AND NOT EXISTS (SELECT 1 FROM [clean].[{clean}] c "
                               f"WHERE c.execution_id = ? AND c.{natural} = d.{natural});",
                               str(run.execution_id), str(run.execution_id), str(run.execution_id))
                counts[table] = dict(inserted=inserted, updated=updated, deleted=cursor.rowcount)
            buffer = []
            for calendar in calendar_rows(row[3] for row in refs.invoices.values()):
                buffer.append(calendar + (calendar[0],))
                if len(buffer) == batch_size:
                    self.insert_calendar(cursor, buffer)
                    buffer = []
            self.insert_calendar(cursor, buffer)
        finally:
            cursor.close()
        customers = dict(self.rows("SELECT CustomerID, ClienteKey FROM [dw].[DimCliente] WHERE CustomerID IS NOT NULL;"))
        products = dict(self.rows("SELECT StockItemID, ProductoKey FROM [dw].[DimProducto] WHERE StockItemID IS NOT NULL;"))
        return customers, products, counts

    @staticmethod
    def insert_calendar(cursor, rows):
        if rows:
            cursor.executemany(
                "INSERT INTO [dw].[DimFecha] (FechaKey, Fecha, Anio, Mes, Dia, Trimestre, DiaSemanaISO, EsFinDeSemana) "
                "SELECT ?, ?, ?, ?, ?, ?, ?, ? WHERE NOT EXISTS (SELECT 1 FROM [dw].[DimFecha] WHERE FechaKey = ?);", rows)

    def sql_profile(self, zone, run):
        if zone in {"raw", "clean"}:
            table, predicate, params = f"[{zone}].[InvoiceLines]", "execution_id = ?", (str(run.execution_id),)
            base = "CAST(CAST(Quantity AS decimal(10,0)) * UnitPrice AS decimal(29,2))"
        elif zone == "ready":
            table, predicate, params, base = "[ready].[Ventas]", "execution_id = ?", (str(run.execution_id),), "ImporteBaseLinea"
        elif zone == "dw":
            table, predicate, params, base = "[dw].[FactVentas]", "EsEliminadoFuente = 0", (), "ImporteBaseLinea"
        else:
            raise ValueError("Zona no autorizada.")
        result = self.rows(
            f"SELECT COUNT_BIG(*), COALESCE(SUM(CAST(Quantity AS bigint)),0), "
            f"COALESCE(SUM(CAST({base} AS decimal(38,2))),0), COUNT_BIG(*) - COUNT_BIG({base}), "
            "COALESCE(SUM(CAST(TaxAmount AS decimal(38,2))),0), "
            "COALESCE(SUM(CAST(ExtendedPrice AS decimal(38,2))),0), "
            "COALESCE(SUM(CAST(LineProfit AS decimal(38,2))),0), COUNT_BIG(DISTINCT InvoiceID) "
            f"FROM {table} WHERE {predicate};", *params)[0]
        return profile_result(*result)

    def reconcile(self, run, expected):
        profiles = {zone: self.sql_profile(zone, run) for zone in ("raw", "clean", "ready", "dw")}
        if any(profile != expected for profile in profiles.values()):
            raise QualityBlocked("No coinciden conteos o importes Raw/Clean/Ready/DW.")
        columns = ", ".join(READY_COLUMNS)
        ready = f"SELECT {columns} FROM [ready].[Ventas] WHERE execution_id = ?"
        fact = f"SELECT {columns} FROM [dw].[FactVentas] WHERE EsEliminadoFuente = 0"
        for query in (f"{ready} EXCEPT {fact}", f"{fact} EXCEPT {ready}"):
            if self.rows(f"SELECT COUNT_BIG(*) FROM ({query}) differences;", str(run.execution_id))[0][0]:
                raise QualityBlocked("Diferencias por línea entre Ready y DW, aunque los totales pudieran coincidir.")
        mismatches = self.rows(
            "SELECT COUNT_BIG(*) FROM [dw].[FactVentas] f "
            "LEFT JOIN [dw].[DimCliente] v ON v.ClienteKey = f.ClienteVentaKey "
            "LEFT JOIN [dw].[DimCliente] b ON b.ClienteKey = f.ClienteFacturacionKey "
            "LEFT JOIN [dw].[DimProducto] p ON p.ProductoKey = f.ProductoKey "
            "LEFT JOIN [dw].[DimFecha] d ON d.FechaKey = f.FechaKey "
            "WHERE f.EsEliminadoFuente = 0 AND (v.CustomerID IS NULL OR v.CustomerID <> f.CustomerID "
            "OR b.CustomerID IS NULL OR b.CustomerID <> f.BillToCustomerID "
            "OR p.StockItemID IS NULL OR p.StockItemID <> f.StockItemID "
            "OR d.Fecha IS NULL OR d.Fecha <> f.InvoiceDate);")[0][0]
        if mismatches:
            raise QualityBlocked("Roles o claves dimensionales inconsistentes.")
        # Comparar también atributos Tipo 1; una clave válida no prueba su contenido.
        # BIN2 y DATALENGTH evitan ocultar cambios de caso o espacios finales.
        customer_text = ("CustomerID, CustomerName COLLATE Latin1_General_100_BIN2 AS CustomerName, "
                         "DATALENGTH(CustomerName) AS CustomerName_bytes, ")
        product_text = ("StockItemID, StockItemName COLLATE Latin1_General_100_BIN2 AS StockItemName, "
                        "DATALENGTH(StockItemName) AS StockItemName_bytes, "
                        "Brand COLLATE Latin1_General_100_BIN2 AS Brand, DATALENGTH(Brand) AS Brand_bytes")
        dimensions = (
            (f"SELECT {customer_text}BillToCustomerID AS BillToCustomerIDActual FROM [clean].[Customers] WHERE execution_id = ?",
             f"SELECT {customer_text}BillToCustomerIDActual FROM [dw].[DimCliente] WHERE EsEliminadoFuente=0 AND ClienteKey<>0"),
            (f"SELECT {product_text} FROM [clean].[StockItems] WHERE execution_id = ?",
             f"SELECT {product_text} FROM [dw].[DimProducto] WHERE EsEliminadoFuente=0 AND ProductoKey<>0"),
        )
        for expected_dimension, actual_dimension in dimensions:
            for query in (f"{expected_dimension} EXCEPT {actual_dimension}",
                          f"{actual_dimension} EXCEPT {expected_dimension}"):
                if self.rows(f"SELECT COUNT_BIG(*) FROM ({query}) differences;", str(run.execution_id))[0][0]:
                    raise QualityBlocked("Los atributos de dimensión no coinciden con Clean.")
        return {"profiles": profiles, "line_differences": 0, "dimension_key_errors": 0,
                "dimension_value_differences": 0}

    def publish(self, run, refs, expected, vector, batch_size):
        with self.transaction() as cursor:
            prior = self.rows("SELECT c.execution_id, c.source_instance, c.source_database, c.schema_version, "
                              "c.rules_version, e.capture_ordinal FROM [audit].[etl_checkpoint] c WITH (UPDLOCK,HOLDLOCK) "
                              "JOIN [audit].[etl_execution] e ON e.execution_id = c.execution_id "
                              "WHERE c.pipeline_name = ?;", FULL_PIPELINE)
            if prior and (prior[0][1].casefold() != self.settings.source_server.casefold()
                          or prior[0][2].casefold() != self.settings.source_database.casefold()
                          or prior[0][3] != SCHEMA_VERSION or prior[0][4] != QUALITY_VERSION):
                raise QualityBlocked("Origen o contrato del checkpoint incompatible; no mezclar generaciones.")
            ordinal = self.rows("SELECT capture_ordinal FROM [audit].[etl_execution] WHERE execution_id = ?;",
                                str(run.execution_id))
            if not ordinal or (prior and ordinal[0][0] <= prior[0][5]):
                raise QualityBlocked("Raw anterior a la publicación actual; recapturar un corte completo.")
            customer_keys, product_keys, dimensions = self.dimensions(run, refs, batch_size)
            changes = publish_facts(self, run, customer_keys, product_keys, batch_size)
            reconciliation = self.reconcile(run, expected)
            result = {"status": "PUBLISHED", "execution_id": str(run.execution_id),
                      "attempt_id": str(run.attempt_id), "publication_id": str(run.execution_id),
                      "source_snapshot_id": str(run.source_snapshot_id),
                      "load_type": "incremental" if prior else "initial", "changes": changes,
                      "dimensions": dimensions, "reconciliation": reconciliation,
                      "audit_persisted": True, "counts_confirmed": True}
            cursor.execute("INSERT INTO [audit].[etl_publication] (execution_id, previous_execution_id, "
                           "published_at_utc, result_json) VALUES (?, ?, ?, ?);", str(run.execution_id),
                           str(prior[0][0]) if prior else None, utc_now(), json_text(result))
            checkpoint_values = (str(run.execution_id), str(run.source_snapshot_id), self.settings.source_server,
                                 self.settings.source_database, SCHEMA_VERSION, QUALITY_VERSION, json_text(vector), utc_now())
            if prior:
                cursor.execute("UPDATE [audit].[etl_checkpoint] SET execution_id=?, source_snapshot_id=?, "
                               "source_instance=?, source_database=?, schema_version=?, rules_version=?, "
                               "manifest_vector=?, published_at_utc=? WHERE pipeline_name=?;", *checkpoint_values, FULL_PIPELINE)
            else:
                cursor.execute("INSERT INTO [audit].[etl_checkpoint] (execution_id, source_snapshot_id, source_instance, "
                               "source_database, schema_version, rules_version, manifest_vector, published_at_utc, pipeline_name) "
                               "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?);", *checkpoint_values, FULL_PIPELINE)
            cursor.execute("UPDATE [audit].[etl_execution] SET status='PUBLISHED', ended_at_utc=? "
                           "WHERE execution_id=?;", utc_now(), str(run.execution_id))
            cursor.execute("UPDATE [audit].[etl_stage] SET status='COMPLETE', ended_at_utc=?, counts_json=? "
                           "WHERE execution_id=? AND attempt_id=? AND stage_name='PUBLISH';",
                           utc_now(), json_text(result), str(run.execution_id), str(run.attempt_id))
        return result

    def fail_stage(self, run, stage, counts, error, blocked):
        with self.transaction() as cursor:
            cursor.execute("UPDATE [audit].[etl_execution] SET status=?, ended_at_utc=?, error_type=?, "
                           "error_code=?, error_detail=? WHERE execution_id=?;",
                           "BLOCKED" if blocked else "FAILED", utc_now(), error["error_type"],
                           error["error_code"], error["error_detail"], str(run.execution_id))
            cursor.execute("UPDATE [audit].[etl_stage] SET status=?, ended_at_utc=?, counts_json=?, error_detail=? "
                           "WHERE execution_id=? AND attempt_id=? AND stage_name=?;",
                           "BLOCKED" if blocked else "FAILED", utc_now(), json_text(counts), error["error_detail"],
                           str(run.execution_id), str(run.attempt_id), stage)
