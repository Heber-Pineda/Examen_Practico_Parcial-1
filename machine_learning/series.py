"""Serie mensual real y validación sin imputaciones ni reglas de signo nuevas."""

from dataclasses import dataclass
from datetime import date
from decimal import Decimal
import hashlib
import json


MONTHLY_SQL = """SELECT CONVERT(char(7), InvoiceDate, 126) AS period,
SUM(CAST(ExtendedPrice AS decimal(38,2))) AS sales_amount,
COUNT_BIG(*) AS line_count,
COUNT_BIG(*) - COUNT_BIG(ExtendedPrice) AS missing_sales,
MIN(InvoiceDate) AS first_invoice_date, MAX(InvoiceDate) AS last_invoice_date
FROM [dw].[FactVentas]
WHERE EsEliminadoFuente = 0 AND InvoiceDate < ?
GROUP BY CONVERT(char(7), InvoiceDate, 126)
ORDER BY period;"""


def month_date(period):
    if not isinstance(period, str) or len(period) != 7:
        raise ValueError("Período inválido; usar YYYY-MM.")
    result = date.fromisoformat(period + "-01")
    if result.strftime("%Y-%m") != period:
        raise ValueError("Período inválido; usar YYYY-MM.")
    return result


def add_months(period, count):
    value = month_date(period)
    year, month = divmod(value.year * 12 + value.month - 1 + count, 12)
    return date(year, month + 1, 1).isoformat()[:7]


@dataclass(frozen=True)
class Month:
    period: str
    amount: Decimal
    line_count: int

    def as_dict(self):
        return {"period": self.period, "amount": format(self.amount, ".2f"), "line_count": self.line_count}


def validate_rows(rows, through_month):
    month_date(through_month)
    result = []
    for row in rows:
        period = row["period"]
        month_date(period)
        if row["sales_amount"] is None or isinstance(row["sales_amount"], float) or row["missing_sales"] != 0:
            raise ValueError("El importe mensual contiene nulos o tipos incompatibles.")
        amount = Decimal(row["sales_amount"])
        if not amount.is_finite() or type(row["line_count"]) is not int or row["line_count"] <= 0:
            raise ValueError("Importes o conteos mensuales inválidos.")
        if result and period != add_months(result[-1].period, 1):
            raise ValueError("La serie tiene meses ausentes, repetidos o desordenados; no se imputan ceros.")
        if period > through_month:
            raise ValueError("La serie excede el cierre solicitado.")
        result.append(Month(period, amount, row["line_count"]))
    if len(result) < 36:
        raise ValueError("Se requieren al menos 36 meses continuos: 24 de entrenamiento, 6 de validación y 6 de prueba.")
    if result[-1].period != through_month:
        raise ValueError("El cierre solicitado no tiene observaciones en el DW.")
    return result


def read_snapshot(analytics, settings, through_month):
    boundary = month_date(add_months(through_month, 1))
    def read(reader):
        rows = reader.query(MONTHLY_SQL, (boundary,))
        return {"rows": rows}
    observed = analytics.consistent(read)
    history = validate_rows(observed["rows"], through_month)
    snapshot = {"publication": observed["publication"], "server": settings.server,
                "database": settings.database, "table": "dw.FactVentas",
                "target": "SUM(ExtendedPrice)", "filter": "EsEliminadoFuente = 0",
                "through_month": through_month, "history": [m.as_dict() for m in history]}
    snapshot["sha256"] = hashlib.sha256(json.dumps(snapshot, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
    return history, snapshot
