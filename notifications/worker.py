from datetime import date, datetime, timezone
from decimal import Decimal
import json

from .rules import evaluate, month_start, shift_month

MONTHLY_SQL = """SELECT TOP (2) CONVERT(char(7), InvoiceDate, 126) AS period,
SUM(CAST(ExtendedPrice AS decimal(38,2))) AS amount
FROM [dw].[FactVentas] WHERE EsEliminadoFuente=0 AND InvoiceDate < ?
GROUP BY CONVERT(char(7), InvoiceDate, 126) ORDER BY period DESC;"""


class AlertWorker:
    def __init__(self, analytics, store, threshold=Decimal("-10"), max_attempts=3):
        if not 1 <= max_attempts <= 10:
            raise ValueError("Se permiten entre 1 y 10 intentos.")
        self.analytics, self.store = analytics, store
        self.threshold, self.max_attempts = threshold, max_attempts

    def evaluate(self, through_month=None, mode="CONFIGURED"):
        ceiling = month_start(shift_month(through_month, 1)) if through_month else datetime.now(timezone.utc).date().replace(day=1)
        if ceiling > datetime.now(timezone.utc).date().replace(day=1):
            raise ValueError("Solo se evalúan meses históricos cerrados.")
        def read(reader):
            rows = reader.query(MONTHLY_SQL, (ceiling,))
            period = through_month or (rows[0]["period"] if rows else shift_month(ceiling.isoformat()[:7], -1))
            return {"result": evaluate(rows, period, self.threshold, mode)}
        snapshot = self.analytics.consistent(read)
        recorded = self.store.record(snapshot["result"], snapshot["publication"])
        return {**snapshot, "recorded": {key: str(value) if value is not None else None for key, value in recorded.items()}}

    def deliver(self, limit=20, retry_now=False, simulate_failure=False):
        if not 1 <= limit <= 100:
            raise ValueError("Límite de entrega fuera de rango.")
        outcomes = []
        for index in range(limit):
            event = self.store.claim(self.max_attempts, retry_now)
            if event is None:
                break
            try:
                if simulate_failure and index == 0:
                    raise RuntimeError("ACADEMIC_SIMULATED_FAILURE")
                delivered = self.store.deliver(event)
            except Exception:
                code = "ACADEMIC_SIMULATED_FAILURE" if simulate_failure and index == 0 else "DELIVERY_OPERATION_FAILED"
                delay = min(3600, 10 * (2 ** min(int(event["attempts"]) - 1, 8)))
                delivered = self.store.retry(event, code, self.max_attempts, delay)
            outcomes.append({key: str(value) for key, value in delivered.items()})
        return outcomes
