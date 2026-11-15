"""Persistencia Raw y auditoría en transacciones cortas del destino."""

from contextlib import contextmanager

from .config import Settings
from .contracts import (
    ENTITIES, FULL_ENTITIES, PIPELINE_NAME, RULES_VERSION, SCHEMA_VERSION,
    Entity, Progress, Run, utc_now,
)


def insert_sql(entity: Entity) -> str:
    if entity not in FULL_ENTITIES:
        raise ValueError("Entidad no autorizada.")
    metadata = ["execution_id", "attempt_id", "source_snapshot_id", "extracted_at_utc",
                "schema_version", "operation", "payload_hash"]
    columns = ", ".join(f"[{c}]" for c in metadata + [c.name for c in entity.columns])
    placeholders = ["?"] * len(metadata) + [
        "CONVERT(datetime2(7), ?, 126)" if c.kind == "datetime2(7)" else "?"
        for c in entity.columns
    ]
    return f"INSERT INTO [raw].[{entity.table}] ({columns}) VALUES ({', '.join(placeholders)});"


class RawStore:
    def __init__(self, connection, settings: Settings, *, entities=ENTITIES,
                 pipeline_name=PIPELINE_NAME, rules_version=RULES_VERSION):
        self.connection = connection
        self.settings = settings
        self.entities = entities
        self.pipeline_name = pipeline_name
        self.rules_version = rules_version

    @contextmanager
    def transaction(self):
        cursor = self.connection.cursor()
        try:
            yield cursor
            self.connection.commit()
        except BaseException:
            try:
                self.connection.rollback()
            except Exception:
                pass  # No ocultar el error original si se perdió la conexión.
            raise
        finally:
            cursor.close()

    def begin_run(self, run: Run, consistency: str, limit: int | None) -> None:
        with self.transaction() as cursor:
            cursor.execute(
                "INSERT INTO [audit].[etl_execution] "
                "(execution_id, attempt_id, source_snapshot_id, pipeline_name, schema_version, "
                "rules_version, source_system, source_instance, source_database, target_instance, "
                "target_database, consistency_mode, max_rows_per_table, started_at_utc, status) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'EXTRACTING');",
                str(run.execution_id), str(run.attempt_id), str(run.source_snapshot_id),
                self.pipeline_name, SCHEMA_VERSION, self.rules_version, "WideWorldImporters",
                self.settings.source_server, self.settings.source_database,
                self.settings.target_server, self.settings.target_database,
                consistency, limit, run.started_at_utc,
            )

    def start_entity(self, run: Run, entity: Entity) -> None:
        with self.transaction() as cursor:
            cursor.execute(
                "INSERT INTO [audit].[etl_entity] "
                "(execution_id, source_schema, source_table, target_schema, target_table, "
                "started_at_utc, status) VALUES (?, ?, ?, 'raw', ?, ?, 'EXTRACTING');",
                str(run.execution_id), entity.schema, entity.table, entity.table, utc_now(),
            )

    @staticmethod
    def update_progress(cursor, run: Run, entity: Entity, progress: Progress) -> None:
        cursor.execute(
            "UPDATE [audit].[etl_entity] SET rows_read = ?, rows_inserted = ?, "
            "batches_committed = ?, observed_min_LastEditedWhen = CONVERT(datetime2(7), ?, 126), "
            "observed_max_LastEditedWhen = CONVERT(datetime2(7), ?, 126), "
            "manifest_hash = ?, is_complete = ? WHERE execution_id = ? AND source_table = ?;",
            progress.rows_read, progress.rows_inserted, progress.batches_committed,
            progress.observed_min, progress.observed_max, progress.manifest_hash,
            progress.is_complete, str(run.execution_id), entity.table,
        )

    def write_batch(self, run: Run, entity: Entity, prepared: list, progress: Progress) -> None:
        captured = utc_now()
        metadata = (str(run.execution_id), str(run.attempt_id), str(run.source_snapshot_id),
                    captured, SCHEMA_VERSION, "SNAPSHOT")
        parameters = [metadata + (digest,) + values for values, digest in prepared]
        with self.transaction() as cursor:
            # Sin fast_executemany: memoria acotada y binding conservador de Decimal/NULL.
            cursor.fast_executemany = False
            cursor.executemany(insert_sql(entity), parameters)
            self.update_progress(cursor, run, entity, progress)

    def finish_entity(self, run: Run, entity: Entity, progress: Progress) -> None:
        with self.transaction() as cursor:
            cursor.execute(f"SELECT COUNT_BIG(*) FROM [raw].[{entity.table}] WHERE execution_id = ?;",
                           str(run.execution_id))
            if cursor.fetchone()[0] != progress.rows_read:
                raise ValueError("El conteo persistido Raw no coincide con el leído.")
            self.update_progress(cursor, run, entity, progress)
            cursor.execute(
                "UPDATE [audit].[etl_entity] SET status = ?, ended_at_utc = ? "
                "WHERE execution_id = ? AND source_table = ?;",
                "RAW_COMPLETE" if progress.is_complete else "RAW_PARTIAL",
                utc_now(), str(run.execution_id), entity.table,
            )

    def finish_run(self, run: Run, status: str) -> None:
        with self.transaction() as cursor:
            cursor.execute(
                "UPDATE [audit].[etl_execution] SET status = ?, ended_at_utc = ? "
                "WHERE execution_id = ?;", status, utc_now(), str(run.execution_id),
            )

    def fail_run(self, run: Run, progress: dict[str, Progress],
                 active: str | None, error: dict[str, str]) -> None:
        # Recontar el destino resuelve también un commit cuya confirmación se perdió,
        # siempre que esta conexión siga utilizable. Si falla, se informa por JSON.
        counts = {}
        with self.transaction() as cursor:
            for entity in self.entities:
                cursor.execute(
                    f"SELECT COUNT_BIG(*) FROM [raw].[{entity.table}] WHERE execution_id = ?;",
                    str(run.execution_id),
                )
                counts[entity.table] = cursor.fetchone()[0]
                cursor.execute(
                    "UPDATE [audit].[etl_entity] SET rows_read = ?, rows_inserted = ? "
                    "WHERE execution_id = ? AND source_table = ?;",
                    progress[entity.table].rows_read, counts[entity.table],
                    str(run.execution_id), entity.table,
                )
            if active is not None:
                cursor.execute(
                    "UPDATE [audit].[etl_entity] SET status = 'FAILED', is_complete = 0, "
                    "ended_at_utc = ? WHERE execution_id = ? AND source_table = ?;",
                    utc_now(), str(run.execution_id), active,
                )
            cursor.execute(
                "UPDATE [audit].[etl_execution] SET status = 'FAILED', ended_at_utc = ?, "
                "error_type = ?, error_code = ?, error_detail = ? WHERE execution_id = ?;",
                utc_now(), error["error_type"], error["error_code"], error["error_detail"],
                str(run.execution_id),
            )
            if cursor.rowcount != 1:
                raise ValueError("No existe la cabecera de auditoría de esta ejecución.")
        for table, count in counts.items():
            progress[table].rows_inserted = count
