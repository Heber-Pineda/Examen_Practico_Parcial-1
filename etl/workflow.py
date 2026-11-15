"""Flujo completo y recuperable: Raw inmutable, Clean, Ready y publicación."""

import hashlib

from .contracts import FULL_ENTITIES, CUSTOMERS, STOCK_ITEMS, INVOICES, INVOICE_LINES, Run, safe_error
from .pipeline import PipelineFailure, execute_pipeline
from .quality import (Issue, QualityBlocked, References, Totals, make_ready, ready_hash, validate_record)


def clean_snapshot(store, run, vector, batch_size, counts):
    refs, raw_totals, clean_totals = References(), Totals(), Totals()
    for entity in FULL_ENTITIES:
        statistics = dict(read=0, accepted=0, rejected=0, warning_issues_in_accepted=0)
        counts[entity.source_object] = statistics
        batches = store.entity_batches("raw", entity, run, batch_size)
        if entity == CUSTOMERS:
            # Catálogo documentado de 663 clientes; permite validar la autorreferencia.
            customers = [item for batch in batches for item in batch]
            refs.customer_ids = {row[0] for row, _ in customers if row and type(row[0]) is int}
            batches = (customers[i:i + batch_size] for i in range(0, len(customers), batch_size))
        manifest, seen = hashlib.sha256(), set()
        for batch in batches:
            accepted, rejected = [], []
            for row, raw_digest in batch:
                statistics["read"] += 1
                manifest.update(raw_digest)
                values, digest, issues = validate_record(entity, row, refs)
                if values is not None:
                    if digest != raw_digest:
                        issues.append(Issue("RAW_MANIFEST", "ERROR", "Payload Raw alterado respecto del hash de captura."))
                    if values[0] in seen:
                        issues.append(Issue("DQ-02", "ERROR", "Clave repetida; no se elige un ganador arbitrario."))
                    seen.add(values[0])
                    if entity == INVOICE_LINES:
                        raw_totals.add_line(values)
                if any(issue.severity == "ERROR" for issue in issues):
                    statistics["rejected"] += 1
                else:
                    statistics["accepted"] += 1
                    statistics["warning_issues_in_accepted"] += len(issues)
                    accepted.append((values, digest))
                    if entity == CUSTOMERS:
                        refs.customers[values[0]] = values
                    elif entity == STOCK_ITEMS:
                        refs.products[values[0]] = values
                    elif entity == INVOICES:
                        refs.invoices[values[0]] = values
                    else:
                        clean_totals.add_line(values)
                if issues:
                    rejected.append((row, issues))
            store.write_clean(run, entity, accepted, rejected)
        expected = vector[entity.source_object]
        if statistics["read"] != expected["rows"] or manifest.hexdigest() != expected["hash"]:
            store.write_clean(run, entity, [], [([], [Issue(
                "RAW_MANIFEST", "ERROR", "Raw truncado o manifiesto de captura incompatible.")])])
            raise QualityBlocked("No se acredita la integridad del snapshot Raw.")
        if statistics["read"] != statistics["accepted"] + statistics["rejected"]:
            raise QualityBlocked("Conteos Clean incompatibles.")
    counts["raw_profile"], counts["clean_profile"] = raw_totals.result(), clean_totals.result()
    if any(counts[e.source_object]["rejected"] for e in FULL_ENTITIES):
        raise QualityBlocked("Hay registros en cuarentena; no se publicará un universo parcial.")
    if raw_totals.result() != clean_totals.result():
        raise QualityBlocked("No se reconcilian Raw y Clean.")
    return refs, clean_totals.result()


def prepare_ready(store, run, refs, expected, batch_size):
    totals = Totals()
    for batch in store.entity_batches("clean", INVOICE_LINES, run, batch_size):
        ready = []
        for line, _ in batch:
            values = make_ready(line, refs.invoices[line[1]])
            totals.add_ready(values)
            ready.append((values, ready_hash(values)))
        store.write_ready(run, ready)
    if totals.result() != expected:
        raise QualityBlocked("Ready perdió, duplicó o alteró líneas o medidas.")
    return totals.result()


def execute_complete(source, store, run: Run, *, batch_size, consistency=None,
                     resume=False, emit=None):
    with store.exclusive():
        if resume:
            run = store.load_run(run.execution_id)
            published = store.publication(run.execution_id)
            if published:
                return {**published, "reused_publication": True,
                        "original_publication_changes": published["changes"],
                        "changes": dict(inserted=0, updated=0, unchanged=0, deleted=0, reactivated=0),
                        "request_attempt_id": str(run.attempt_id)}
        else:
            execute_pipeline(source, store, run, batch_size=batch_size,
                             consistency=consistency, entities=FULL_ENTITIES, emit=emit,
                             verify_source_counts=True)
        counts, stage = {}, "CLEAN"
        try:
            vector = store.raw_manifest(run)
            store.start_stage(run, stage, "CLEANING")
            store.reset_derived(run)
            refs, expected = clean_snapshot(store, run, vector, batch_size, counts)
            store.finish_stage(run, stage, counts, "CLEAN_COMPLETE")
            if emit:
                emit({"event": "clean_complete", "execution_id": str(run.execution_id), "counts": counts})
            stage, counts = "READY", {}
            store.start_stage(run, stage, "CLEAN_COMPLETE")
            counts.update(prepare_ready(store, run, refs, expected, batch_size))
            store.finish_stage(run, stage, counts, "READY_COMPLETE")
            if emit:
                emit({"event": "ready_complete", "execution_id": str(run.execution_id), "counts": counts})
            stage, counts = "PUBLISH", {}
            store.start_stage(run, stage, "PUBLISHING")
            return store.publish(run, refs, expected, vector, batch_size)
        except (Exception, KeyboardInterrupt) as exc:
            # Si el commit ocurrió pero se perdió su confirmación, no degradar PUBLISHED.
            unknown = False
            if stage == "PUBLISH":
                try:
                    recovered = store.publication(run.execution_id)
                    if recovered:
                        return {**recovered, "publication_recovered": True}
                except Exception:
                    unknown = True
            error = safe_error(exc)
            error["error_detail"] = (f"Fase {stage}: {exc}" if isinstance(exc, QualityBlocked) else
                                     f"Fase {stage}; revisar cuarentena y auditoría de la ejecución.")
            persisted = False
            if not unknown:
                try:
                    store.fail_stage(run, stage, counts, error, isinstance(exc, QualityBlocked))
                    persisted = True
                except Exception:
                    pass
            raise PipelineFailure({"status": "BLOCKED" if isinstance(exc, QualityBlocked) else "FAILED",
                                   "execution_id": str(run.execution_id), "attempt_id": str(run.attempt_id),
                                   "phase": stage, "audit_persisted": persisted, "counts_confirmed": False,
                                   "publication_outcome_unknown": unknown, "counts": counts,
                                   "interrupted": isinstance(exc, KeyboardInterrupt), **error}) from None
