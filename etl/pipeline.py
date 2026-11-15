"""Orquestación independiente de HTTP, inyectable para pruebas sin ODBC."""

from dataclasses import replace
import hashlib

from .contracts import ENTITIES, Progress, RawValidationError, Run, prepare_payload, safe_error


class PipelineFailure(Exception):
    def __init__(self, report: dict):
        super().__init__("Fallo del componente Raw.")
        self.report = report


def summary(run: Run, status: str, progress: dict[str, Progress], **extra) -> dict:
    return {
        "execution_id": str(run.execution_id), "attempt_id": str(run.attempt_id),
        "source_snapshot_id": str(run.source_snapshot_id), "status": status,
        "scope": "Sales.Invoices,Sales.InvoiceLines",
        "entities": {
            table: {"rows_read": p.rows_read, "rows_inserted": p.rows_inserted,
                    "source_rows_expected": p.source_rows_expected,
                    "rows_updated": None, "rows_rejected": None,
                    "batches_committed": p.batches_committed,
                    "observed_min_LastEditedWhen": p.observed_min,
                    "observed_max_LastEditedWhen": p.observed_max,
                    "manifest_hash": p.manifest_hash.hex() if p.manifest_hash else None,
                    "is_complete": p.is_complete}
            for table, p in progress.items()
        }, **extra,
    }


def execute_pipeline(source, store, run: Run, *, batch_size: int,
                     consistency: str, limit: int | None = None, emit=None,
                     entities=ENTITIES, verify_source_counts=False) -> dict:
    if not 1 <= batch_size <= 2000 or (limit is not None and limit < 1):
        raise ValueError("Tamaño de lote o límite inválido.")
    progress = {entity.table: Progress() for entity in entities}
    active = None
    phase = "begin_run"
    try:
        store.begin_run(run, consistency, limit)
        phase = "source_transaction"
        source.begin()
        for entity in entities:
            active, phase = entity.table, "start_entity"
            current = progress[active]
            store.start_entity(run, entity)
            if verify_source_counts:
                phase = "count_source"
                current.source_rows_expected = source.count_entity(entity)
                phase = "record_source_count"
                store.record_source_count(run, entity, current.source_rows_expected)
            phase = "extract"
            manifest = hashlib.sha256()
            previous_key = None
            for batch in source.batches(entity, batch_size, limit):
                if len(batch) > batch_size:
                    raise RawValidationError("El origen excedió el tamaño de lote contratado.")
                current.rows_read += len(batch)
                prepared = []
                for row in batch:
                    try:
                        values, digest = prepare_payload(entity, row)
                        if previous_key is not None and values[0] <= previous_key:
                            raise RawValidationError("Clave duplicada o fuera de orden en Raw.")
                    except RawValidationError:
                        if hasattr(store, "quarantine_source"):
                            store.quarantine_source(run, entity, row)
                        raise
                    previous_key = values[0]
                    if entity in ENTITIES:
                        current.observe(values[-1])
                    prepared.append((values, digest))
                candidate = replace(current, rows_inserted=current.rows_inserted + len(batch),
                                    batches_committed=current.batches_committed + 1)
                phase = "write_batch"
                store.write_batch(run, entity, prepared, candidate)
                current.rows_inserted = candidate.rows_inserted
                current.batches_committed = candidate.batches_committed
                for _, digest in prepared:
                    manifest.update(digest)
                if emit is not None:
                    emit({"event": "batch_committed", "execution_id": str(run.execution_id),
                          "attempt_id": str(run.attempt_id), "entity": entity.source_object,
                          "rows_read": current.rows_read, "rows_inserted": current.rows_inserted,
                          "batches_committed": current.batches_committed})
                phase = "extract"
            if current.rows_read != current.rows_inserted:
                raise RawValidationError("Diferencia entre conteos leídos e insertados.")
            if verify_source_counts and current.rows_read != current.source_rows_expected:
                raise RawValidationError("La extracción no coincide con COUNT_BIG del mismo corte de origen.")
            current.manifest_hash = manifest.digest()
            current.is_complete = limit is None
            phase = "finish_entity"
            store.finish_entity(run, entity, current)
        active, phase = None, "finish_source"
        source.finish()
        status = "RAW_COMPLETE" if limit is None else "RAW_PARTIAL"
        phase = "finish_run"
        store.finish_run(run, status)
        return summary(run, status, progress, audit_persisted=True, counts_confirmed=True,
                       scope=",".join(e.source_object for e in entities))
    except (Exception, KeyboardInterrupt) as exc:
        if active is not None:
            progress[active].is_complete = False
            progress[active].manifest_hash = None
        try:
            source.finish()
        except Exception:
            pass
        error = safe_error(exc)
        error["error_detail"] = (
            f"Fase {phase}; entidad {active or 'ejecucion'}; "
            "extracción interrumpida. Revisar conteos y código del error."
        )
        audit_diagnostics = {}
        try:
            store.fail_run(run, progress, active, error)
            audit_persisted = True
        except Exception as audit_exc:
            audit_persisted = False
            audit_diagnostics["audit_error"] = safe_error(audit_exc)
        raise PipelineFailure(summary(
            run, "FAILED", progress, phase=phase, active_entity=active, **error,
            audit_persisted=audit_persisted, counts_confirmed=audit_persisted,
            interrupted=isinstance(exc, KeyboardInterrupt),
            scope=",".join(e.source_object for e in entities),
            **audit_diagnostics,
        )) from None
