"""Calendario, decisiones de cambio y carga diferencial usados por el publicador."""

from dataclasses import dataclass
from datetime import date, timedelta

from .quality import QualityBlocked, ready_hash


@dataclass(frozen=True)
class FactState:
    payload_hash: bytes
    deleted: bool
    dimension_keys: tuple


def fact_change(previous: FactState | None, digest: bytes, keys: tuple) -> str:
    if previous is None:
        return "inserted"
    if previous.deleted or previous.payload_hash != digest or previous.dimension_keys != keys:
        return "updated"
    return "unchanged"


def calendar_rows(dates):
    dates = list(dates)
    if not dates:
        return
    current = date(min(dates).year, 1, 1)
    last = date(max(dates).year, 12, 31)
    while True:
        yield (current.year * 10000 + current.month * 100 + current.day, current,
               current.year, current.month, current.day, (current.month - 1) // 3 + 1,
               current.isoweekday(), current.isoweekday() >= 6)
        if current == last:
            break
        current += timedelta(days=1)


def publish_facts(store, run, customer_keys, product_keys, batch_size):
    previous = store.fact_states()
    seen = set()
    counts = dict(inserted=0, updated=0, unchanged=0, deleted=0, reactivated=0)
    for batch in store.ready_batches(run, batch_size):
        inserts, updates = [], []
        for values, digest in batch:
            if values[0] in seen or digest != ready_hash(values):
                raise QualityBlocked("Ready duplicado o alterado; publicación bloqueada.")
            seen.add(values[0])
            try:
                keys = (values[5].year * 10000 + values[5].month * 100 + values[5].day,
                        customer_keys[values[3]], customer_keys[values[4]], product_keys[values[2]])
            except KeyError:
                raise QualityBlocked("No se resolvieron las claves dimensionales de Ready.") from None
            old = previous.get(values[0])
            action = fact_change(old, digest, keys)
            counts[action] += 1
            if old is not None and old.deleted:
                counts["reactivated"] += 1  # Subconjunto de updated; vuelve al universo activo.
            if action == "inserted":
                inserts.append((values, digest, keys))
            elif action == "updated":
                updates.append((values, digest, keys))
        store.write_facts(run, inserts, updates)
    deleted = [key for key, state in previous.items() if not state.deleted and key not in seen]
    for offset in range(0, len(deleted), batch_size):
        store.delete_facts(run, deleted[offset:offset + batch_size])
    counts["deleted"] = len(deleted)
    old_active = sum(not state.deleted for state in previous.values())
    if old_active + counts["inserted"] + counts["reactivated"] - counts["deleted"] != len(seen):
        raise QualityBlocked("No se cumple la reconciliación de cardinalidad incremental.")
    return counts
