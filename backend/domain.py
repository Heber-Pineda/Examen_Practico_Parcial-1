"""Filtros y métricas exactas, sin dependencias HTTP ni acceso a datos."""

from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal, ROUND_HALF_UP, localcontext


@dataclass(frozen=True)
class SalesFilters:
    date_from: date | None = None
    date_to: date | None = None  # Exclusiva, según el contrato de métricas.
    customer_id: int | None = None
    bill_to_customer_id: int | None = None
    stock_item_id: int | None = None
    is_credit_note: bool | None = None

    def __post_init__(self):
        if any(value is not None and type(value) is not date for value in (self.date_from, self.date_to)):
            raise ValueError("Fecha de filtro inválida.")
        if self.date_from and self.date_to and self.date_from >= self.date_to:
            raise ValueError("La fecha desde debe ser anterior a hasta.")
        for value in (self.customer_id, self.bill_to_customer_id, self.stock_item_id):
            if value is not None and (type(value) is not int or not -(2**31) <= value < 2**31):
                raise ValueError("Identificador fuera del rango del origen.")
        if self.is_credit_note is not None and type(self.is_credit_note) is not bool:
            raise ValueError("Filtro de notas de crédito inválido.")


def decimal_value(value) -> Decimal:
    if isinstance(value, float):
        raise ValueError("El adaptador no debe convertir medidas decimales a float.")
    result = Decimal(value or 0)
    if not result.is_finite():
        raise ValueError("Medida decimal no finita.")
    return result


def kpis(row: dict) -> dict:
    lines, missing = int(row["line_count"]), int(row["missing_base"])
    base, profit = decimal_value(row["base_sum"]), decimal_value(row["profit_sum"])
    margin = None
    with localcontext() as ctx:
        ctx.prec = 60
        if lines and not missing and base != 0:
            margin = format((profit * 100 / base).quantize(Decimal("0.000001"), rounding=ROUND_HALF_UP), ".6f")
    return {
        "base_amount": None if missing else format(base, ".2f"),
        "extended_amount": format(decimal_value(row["extended_sum"]), ".2f"),
        "units": int(row["units"]), "invoice_count": int(row["invoice_count"]),
        "profit_amount": format(profit, ".2f"), "profit_margin": margin,
        "line_count": lines, "missing_base": missing,
        "base_status": "SIN_DATOS" if not lines else ("INCOMPLETO" if missing else "CALCULABLE"),
        "margin_status": ("SIN_DATOS" if not lines else "INCOMPLETO" if missing else
                          "DENOMINADOR_CERO" if base == 0 else "CALCULABLE"),
    }


def empty_kpis():
    return kpis(dict(line_count=0, missing_base=0, base_sum=0, profit_sum=0,
                     extended_sum=0, units=0, invoice_count=0))


def calendar_months(first: date, last: date):
    year, month = first.year, first.month
    while (year, month) <= (last.year, last.month):
        yield f"{year:04d}-{month:02d}"
        year, month = (year + 1, 1) if month == 12 else (year, month + 1)


def selected_months(coverage: dict, filters: SalesFilters):
    if not coverage["date_from"] or not coverage["date_to"]:
        return []
    first = max(date.fromisoformat(coverage["date_from"]), filters.date_from or date.min)
    if filters.date_to == date.min:
        return []
    last_included = filters.date_to - timedelta(days=1) if filters.date_to else date.max
    last = min(date.fromisoformat(coverage["date_to"]), last_included)
    if first > last:
        return []
    return list(calendar_months(first, last))


def common_months(coverage: dict, filters: SalesFilters):
    allowed = set(selected_months(coverage, filters))
    years = {}
    for period in coverage["observed_months"]:
        if period in allowed:
            year, month = map(int, period.split("-"))
            years.setdefault(year, set()).add(month)
    return sorted(set.intersection(*years.values())) if len(years) >= 2 else []
