"""Proyecciones reales, metadatos técnicos y representación exacta del payload."""

from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation, localcontext
import hashlib
import json
import re
from uuid import UUID, uuid4

SCHEMA_VERSION = "sales-raw-v1"
RULES_VERSION = "raw-types-v1"
PIPELINE_NAME = "sales_transactions_to_raw"


class RawValidationError(ValueError):
    """Incompatibilidad técnica; no aplica reglas comerciales de Clean."""


@dataclass(frozen=True)
class Column:
    name: str
    kind: str
    nullable: bool = False


@dataclass(frozen=True)
class Entity:
    table: str
    key: str
    columns: tuple[Column, ...]
    schema: str = "Sales"

    @property
    def source_object(self) -> str:
        return f"{self.schema}.{self.table}"

    @property
    def target_object(self) -> str:
        return f"raw.{self.table}"

    def select_sql(self, limited: bool = False) -> str:
        projection = ", ".join(
            f"CONVERT(varchar(27), [{c.name}], 126) AS [{c.name}]"
            if c.kind == "datetime2(7)" else f"[{c.name}]"
            for c in self.columns
        )
        top = "TOP (?) " if limited else ""
        return f"SELECT {top}{projection} FROM [{self.schema}].[{self.table}] ORDER BY [{self.key}];"


INVOICES = Entity("Invoices", "InvoiceID", (
    Column("InvoiceID", "int"), Column("CustomerID", "int"),
    Column("BillToCustomerID", "int"), Column("InvoiceDate", "date"),
    Column("IsCreditNote", "bit"), Column("LastEditedWhen", "datetime2(7)"),
))
INVOICE_LINES = Entity("InvoiceLines", "InvoiceLineID", (
    Column("InvoiceLineID", "int"), Column("InvoiceID", "int"),
    Column("StockItemID", "int"), Column("Quantity", "int"),
    Column("UnitPrice", "decimal(18,2)", nullable=True),
    Column("TaxAmount", "decimal(18,2)"), Column("ExtendedPrice", "decimal(18,2)"),
    Column("LineProfit", "decimal(18,2)"), Column("LastEditedWhen", "datetime2(7)"),
))
ENTITIES = (INVOICES, INVOICE_LINES)
CUSTOMERS = Entity("Customers", "CustomerID", (
    Column("CustomerID", "int"), Column("CustomerName", "nvarchar(200)"),
    Column("BillToCustomerID", "int"), Column("ValidFrom", "datetime2(7)"),
    Column("ValidTo", "datetime2(7)"),
))
STOCK_ITEMS = Entity("StockItems", "StockItemID", (
    Column("StockItemID", "int"), Column("StockItemName", "nvarchar(200)"),
    Column("Brand", "nvarchar(100)", nullable=True),
    Column("ValidFrom", "datetime2(7)"), Column("ValidTo", "datetime2(7)"),
), schema="Warehouse")
# El tamaño de texto destino es una capacidad conservadora de length_raw;
# no afirma que el metadato exportado represente caracteres del origen.
FULL_ENTITIES = (CUSTOMERS, STOCK_ITEMS, INVOICES, INVOICE_LINES)


def timestamp_text(value: object) -> str:
    # datetime de Python solo conserva microsegundos. El origen se lee como ISO.
    if not isinstance(value, str):
        raise RawValidationError("LastEditedWhen requiere texto ISO con precisión de origen.")
    match = re.fullmatch(r"(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2})(?:\.(\d{1,7}))?", value)
    if not match:
        raise RawValidationError("Formato incompatible de LastEditedWhen.")
    try:
        datetime.fromisoformat(value)  # Solo validez; no se usa su valor truncado.
    except ValueError:
        raise RawValidationError("Fecha inválida en LastEditedWhen.") from None
    return match[1] + "." + (match[2] or "").ljust(7, "0")


def prepare_payload(entity: Entity, row: tuple) -> tuple[tuple, bytes]:
    if entity not in FULL_ENTITIES or len(row) != len(entity.columns):
        raise RawValidationError("La proyección no coincide con el contrato.")
    values, canonical = [], []
    for column, value in zip(entity.columns, row, strict=True):
        if value is None:
            if not column.nullable:
                raise RawValidationError(f"NULL no permitido: {column.name}.")
            normalized = text = None
        elif column.kind == "int":
            if type(value) is not int or not -(2**31) <= value < 2**31:
                raise RawValidationError(f"Tipo o rango int inválido: {column.name}.")
            normalized = text = value
        elif column.kind == "bit":
            if type(value) not in (int, bool) or value not in (0, 1):
                raise RawValidationError(f"bit inválido: {column.name}.")
            normalized = text = bool(value)
        elif column.kind == "date":
            if type(value) is not date:
                raise RawValidationError(f"date inválido: {column.name}.")
            normalized, text = value, value.isoformat()
        elif column.kind == "decimal(18,2)":
            if not isinstance(value, Decimal) or not value.is_finite():
                raise RawValidationError(f"Decimal exacto requerido: {column.name}.")
            try:
                with localcontext() as ctx:
                    ctx.prec = 40
                    normalized = value.quantize(Decimal("0.01"))
                    if normalized != value or abs(value) >= Decimal("10000000000000000"):
                        raise InvalidOperation
            except InvalidOperation:
                raise RawValidationError(f"Precisión incompatible: {column.name}.") from None
            # Ceros con signo no representan valores distintos en decimal SQL.
            if normalized == 0:
                normalized = Decimal("0.00")
            text = format(normalized, ".2f")
        elif column.kind.startswith("nvarchar("):
            capacity = int(column.kind[9:-1])
            if not isinstance(value, str) or len(value.encode("utf-16-le")) // 2 > capacity:
                raise RawValidationError(f"Texto incompatible: {column.name}.")
            normalized = text = value
        else:
            normalized = text = timestamp_text(value)
        values.append(normalized)
        canonical.append([column.name, column.kind, text])
    payload = json.dumps([SCHEMA_VERSION, entity.source_object, canonical],
                         ensure_ascii=False, separators=(",", ":"), allow_nan=False)
    return tuple(values), hashlib.sha256(payload.encode("utf-8")).digest()


def utc_now() -> datetime:
    # El destino datetime2 no guarda offset; estos campos sí son técnicos UTC.
    return datetime.now(timezone.utc).replace(tzinfo=None)


@dataclass(frozen=True)
class Run:
    execution_id: UUID = field(default_factory=uuid4)
    attempt_id: UUID = field(default_factory=uuid4)
    source_snapshot_id: UUID = field(default_factory=uuid4)
    started_at_utc: datetime = field(default_factory=utc_now)


@dataclass
class Progress:
    rows_read: int = 0
    rows_inserted: int = 0
    batches_committed: int = 0
    observed_min: str | None = None
    observed_max: str | None = None
    manifest_hash: bytes | None = None
    is_complete: bool = False
    source_rows_expected: int | None = None

    def observe(self, timestamp: str) -> None:
        self.observed_min = min(self.observed_min, timestamp) if self.observed_min else timestamp
        self.observed_max = max(self.observed_max, timestamp) if self.observed_max else timestamp


def safe_error(exc: BaseException) -> dict[str, str]:
    # Nunca propagar mensajes del driver: pueden incluir datos o cadenas de conexión.
    state = str(exc.args[0]) if exc.args else ""
    result = {"error_type": type(exc).__name__[:128],
              "error_code": state if re.fullmatch(r"[A-Z0-9]{5}", state) else "ETL_FAILURE",
              "error_detail": "Extracción interrumpida; revisar fase, conteos y código del error."}
    message = exc.args[1] if len(exc.args) > 1 and isinstance(exc.args[1], str) else ""
    if state == "08001":
        result.update(odbc_provider="not_reported", odbc_native_code="not_reported")
        # El proveedor es contexto informado por ODBC, no una causa raíz inferida.
        if re.match(r"\[08001\]\s*\[Microsoft\]\[ODBC Driver \d+ for SQL Server\]", message):
            providers = (
                ("SSL Provider", ("ssl provider", "proveedor de ssl")),
                ("TCP Provider", ("tcp provider", "proveedor de tcp")),
                ("Named Pipes Provider", ("named pipes provider", "proveedor de canalizaciones con nombre")),
                ("SQL Server Network Interfaces", ("sql server network interfaces",)),
            )
            for label, markers in providers:
                if any(marker in message.lower() for marker in markers):
                    result["odbc_provider"] = label
                    break
            native = re.search(r"\((-?\d{1,10})\)\s+\(SQLDriverConnectW?\)(?=;|$)", message)
            if native:
                result["odbc_native_code"] = native[1]
    # Solo metadatos de registros reconocidos de SQL Server. No imprimir el
    # mensaje completo, valores duplicados, sentencias SQL ni credenciales.
    server_record = rf"\[{re.escape(state)}\]\s*\[Microsoft\]\[ODBC Driver \d+ for SQL Server\]\s*\[SQL Server\]"
    if re.match(server_record, message):
        native = re.search(
            r"\((-?\d{1,10})\)\s+\((?:SQLExecute|SQLExecDirectW?|SQLPrepareW?|SQLFetch(?:Scroll)?|SQLDriverConnectW?)\)(?=\s*(?:;|$))",
            message)
        if native:
            result["odbc_native_code"] = native[1]
        if state == "23000":
            result["sqlserver_constraint"] = "not_reported"
            if native and native[1] in {"547", "2627", "2601"}:
                constraint = re.search(
                    r"(?:constraint|restricci[oó]n|index|[íi]ndice)\s+"
                    r"(?:(?:CHECK|FOREIGN\s+KEY|PRIMARY\s+KEY|UNIQUE(?:\s+KEY)?)\s+)?"
                    r"[\"']([A-Za-z_][A-Za-z0-9_]{0,127})[\"']", message, re.IGNORECASE)
                if constraint:
                    result["sqlserver_constraint"] = constraint[1]
    return result
