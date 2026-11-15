"""Lectura secuencial de las proyecciones aprobadas sin modificar la fuente."""

from .contracts import Entity, FULL_ENTITIES, RawValidationError


class SqlServerSource:
    def __init__(self, connection, consistency: str):
        if consistency not in {"snapshot", "stable-window"}:
            raise ValueError("Modo de consistencia inválido.")
        self.connection = connection
        self.consistency = consistency

    def begin(self) -> None:
        cursor = self.connection.cursor()
        try:
            isolation = "SNAPSHOT" if self.consistency == "snapshot" else "READ COMMITTED"
            cursor.execute(f"SET TRANSACTION ISOLATION LEVEL {isolation};")
        finally:
            cursor.close()

    def batches(self, entity: Entity, batch_size: int, limit: int | None):
        cursor = self.connection.cursor()
        try:
            cursor.arraysize = batch_size
            if limit is None:
                cursor.execute(entity.select_sql())
            else:
                cursor.execute(entity.select_sql(limited=True), limit)
            expected = [c.name for c in entity.columns]
            if cursor.description is None or [c[0] for c in cursor.description] != expected:
                raise RawValidationError("Las columnas recibidas difieren del contrato.")
            while rows := cursor.fetchmany(batch_size):
                yield [tuple(row) for row in rows]
        finally:
            cursor.close()

    def count_entity(self, entity: Entity) -> int:
        if entity not in FULL_ENTITIES:
            raise ValueError("Entidad no autorizada.")
        cursor = self.connection.cursor()
        try:
            cursor.execute(f"SELECT COUNT_BIG(*) FROM [{entity.schema}].[{entity.table}];")
            return int(cursor.fetchone()[0])
        finally:
            cursor.close()

    def finish(self) -> None:
        self.connection.rollback()  # Finaliza la transacción de solo lectura.
