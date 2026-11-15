"""Reglas técnicas y preparación dimensional; no corrige importes del origen."""

from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal, localcontext
import hashlib
import json

from .contracts import CUSTOMERS, STOCK_ITEMS, INVOICES, INVOICE_LINES, prepare_payload

QUALITY_VERSION = "sales-clean-v1"
READY_VERSION = "sales-ready-v1"
READY_COLUMNS = (
    "InvoiceLineID", "InvoiceID", "StockItemID", "CustomerID", "BillToCustomerID",
    "InvoiceDate", "IsCreditNote", "Quantity", "UnitPrice", "TaxAmount",
    "ExtendedPrice", "LineProfit", "ImporteBaseLinea",
)


class QualityBlocked(ValueError):
    pass


def json_text(value) -> str:
    def convert(item):
        if isinstance(item, Decimal):
            return str(item)
        if isinstance(item, date):
            return item.isoformat()
        if isinstance(item, bytes):
            return item.hex()
        raise TypeError("Tipo no serializable en evidencia.")
    return json.dumps(value, default=convert, ensure_ascii=True, separators=(",", ":"), allow_nan=False)


@dataclass(frozen=True)
class Issue:
    rule: str
    severity: str
    detail: str


@dataclass
class References:
    customer_ids: set = field(default_factory=set)
    customers: dict = field(default_factory=dict)
    products: dict = field(default_factory=dict)
    invoices: dict = field(default_factory=dict)


def validate_record(entity, row, refs: References):
    try:
        values, digest = prepare_payload(entity, row)
    except (ValueError, UnicodeError):
        return None, None, [Issue("DQ-01", "ERROR", "Tipo, nulabilidad o precisión incompatible con el contrato.")]
    issues = []
    if entity == CUSTOMERS and values[2] not in refs.customer_ids:
        issues.append(Issue("DQ-13", "ERROR", "Responsable actual del catálogo de clientes no encontrado."))
    if entity == INVOICES:
        if values[1] not in refs.customers or values[2] not in refs.customers:
            issues.append(Issue("DQ-07", "ERROR", "Rol comprador o facturado sin catálogo válido; política de desconocidos pendiente."))
        if values[4]:
            issues.append(Issue("M-04", "WARNING", "Nota de crédito conservada sin invertir signos; interpretación comercial pendiente."))
    if entity == INVOICE_LINES:
        if values[1] not in refs.invoices:
            issues.append(Issue("DQ-03", "ERROR", "Línea sin cabecera única y válida en el mismo corte."))
        if values[2] not in refs.products:
            issues.append(Issue("DQ-07", "ERROR", "Producto sin catálogo válido; política de desconocidos pendiente."))
        if values[3] <= 0:
            issues.append(Issue("DQ-15", "ERROR", "Cantidad no positiva: requiere clasificación comercial antes de publicar."))
        if values[4] is None:
            issues.append(Issue("DQ-06", "WARNING", "Precio NULL permitido; base y margen incompletos."))
        elif values[4] <= 0:
            issues.append(Issue("DQ-09", "WARNING", "Precio cero o negativo conservado sin corrección automática."))
        if values[4] is not None:
            with localcontext() as ctx:
                ctx.prec = 60
                if values[3] * values[4] + values[5] != values[6]:
                    issues.append(Issue("DQ-11", "WARNING", "Base más impuestos difiere del extendido; importes originales conservados."))
    if entity in (CUSTOMERS, STOCK_ITEMS):
        for value in values:
            if isinstance(value, str) and value != value.strip():
                issues.append(Issue("DQ-10", "WARNING", "Espacios externos conservados; no hay regla de corrección aprobada."))
                break
    return values, digest, issues


def ready_hash(values: tuple) -> bytes:
    return hashlib.sha256(json_text([READY_VERSION, READY_COLUMNS, values]).encode("utf-8")).digest()


def make_ready(line: tuple, invoice: tuple) -> tuple:
    if line[1] != invoice[0]:
        raise QualityBlocked("La cabecera no corresponde a la línea.")
    with localcontext() as ctx:
        ctx.prec = 60
        base = line[3] * line[4] if line[4] is not None else None
    return (line[0], line[1], line[2], invoice[1], invoice[2], invoice[3], invoice[4],
            *line[3:8], base)


def profile_result(lines, units, base_known, base_missing, tax, extended, profit, invoices):
    return {"lines": int(lines), "units": int(units), "base_known": format(base_known, ".2f"),
            "base_missing": int(base_missing), "base": None if base_missing else format(base_known, ".2f"),
            "tax": format(tax, ".2f"), "extended": format(extended, ".2f"),
            "profit": format(profit, ".2f"), "invoices": int(invoices)}


@dataclass
class Totals:
    lines: int = 0
    units: int = 0
    base_known: Decimal = Decimal("0.00")
    base_missing: int = 0
    tax: Decimal = Decimal("0.00")
    extended: Decimal = Decimal("0.00")
    profit: Decimal = Decimal("0.00")
    invoice_ids: set = field(default_factory=set)

    def add_line(self, values: tuple) -> None:
        with localcontext() as ctx:
            ctx.prec = 60
            base = values[3] * values[4] if values[4] is not None else None
        self.add(values[1], values[3], base, values[5], values[6], values[7])

    def add_ready(self, values: tuple) -> None:
        self.add(values[1], values[7], values[12], values[9], values[10], values[11])

    def add(self, invoice_id, quantity, base, tax, extended, profit):
        self.lines += 1
        self.units += quantity
        self.invoice_ids.add(invoice_id)
        with localcontext() as ctx:
            ctx.prec = 60
            if base is None:
                self.base_missing += 1
            else:
                self.base_known += base
            self.tax += tax
            self.extended += extended
            self.profit += profit

    def result(self) -> dict:
        return profile_result(self.lines, self.units, self.base_known, self.base_missing,
                              self.tax, self.extended, self.profit, len(self.invoice_ids))
