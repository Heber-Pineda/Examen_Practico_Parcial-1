"""SQL de lectura con valores parametrizados y proyecciones explícitas del DW."""

from .domain import SalesFilters

AGGREGATES = """
COUNT_BIG(*) AS line_count,
COUNT_BIG(*) - COUNT_BIG(f.ImporteBaseLinea) AS missing_base,
COALESCE(SUM(CAST(f.ImporteBaseLinea AS decimal(38,2))),0) AS base_sum,
COALESCE(SUM(CAST(f.ExtendedPrice AS decimal(38,2))),0) AS extended_sum,
COALESCE(SUM(CAST(f.Quantity AS bigint)),0) AS units,
COUNT_BIG(DISTINCT f.InvoiceID) AS invoice_count,
COALESCE(SUM(CAST(f.LineProfit AS decimal(38,2))),0) AS profit_sum
""".strip()

PUBLICATION = """SELECT c.execution_id, p.published_at_utc
FROM [audit].[etl_checkpoint] c
JOIN [audit].[etl_publication] p ON p.execution_id = c.execution_id
WHERE c.pipeline_name = ?;"""


def where(filters: SalesFilters):
    clauses, parameters = ["f.EsEliminadoFuente = 0"], []
    for column, operator, value in (
        ("InvoiceDate", ">=", filters.date_from), ("InvoiceDate", "<", filters.date_to),
        ("CustomerID", "=", filters.customer_id), ("BillToCustomerID", "=", filters.bill_to_customer_id),
        ("StockItemID", "=", filters.stock_item_id), ("IsCreditNote", "=", filters.is_credit_note)):
        if value is not None:
            clauses.append(f"f.[{column}] {operator} ?")
            parameters.append(value)
            if column == "InvoiceDate":
                clauses.append(f"f.FechaKey {operator} ?")
                parameters.append(value.year * 10000 + value.month * 100 + value.day)
    return " AND ".join(clauses), tuple(parameters)


def aggregate(filters, grouping=None, months=()):
    predicate, parameters = where(filters)
    if months:
        predicate += f" AND MONTH(f.InvoiceDate) IN ({','.join('?' for _ in months)})"
        parameters += tuple(months)
    groups = {None: ("", ""), "month": ("YEAR(f.InvoiceDate) AS year, MONTH(f.InvoiceDate) AS month, ",
                                             " GROUP BY YEAR(f.InvoiceDate), MONTH(f.InvoiceDate) ORDER BY year, month"),
              "year": ("YEAR(f.InvoiceDate) AS year, ", " GROUP BY YEAR(f.InvoiceDate) ORDER BY year")}
    prefix, suffix = groups[grouping]
    return f"SELECT {prefix}{AGGREGATES} FROM [dw].[FactVentas] f WHERE {predicate}{suffix};", parameters


def ranking(filters, kind, role="buyer", limit=8):
    if kind == "product":
        natural, key, table, dimkey, name = "StockItemID", "ProductoKey", "DimProducto", "ProductoKey", "StockItemName"
    elif kind == "customer" and role in {"buyer", "bill_to"}:
        natural, key = ("CustomerID", "ClienteVentaKey") if role == "buyer" else ("BillToCustomerID", "ClienteFacturacionKey")
        table, dimkey, name = "DimCliente", "ClienteKey", "CustomerName"
    else:
        raise ValueError("Agrupación no autorizada.")
    if not 1 <= limit <= 25:
        raise ValueError("Límite no autorizado.")
    predicate, parameters = where(filters)
    sql = (f"SELECT TOP (?) f.{natural} AS id, d.{name} AS name, {AGGREGATES} "
           f"FROM [dw].[FactVentas] f LEFT JOIN [dw].[{table}] d ON d.{dimkey} = f.{key} "
           f"WHERE {predicate} GROUP BY f.{natural}, d.{name} "
           f"ORDER BY extended_sum DESC, f.{natural};")
    return sql, (limit,) + parameters


def details(filters, page, page_size, sort):
    if not 1 <= page <= 100000 or not 1 <= page_size <= 100:
        raise ValueError("Paginación fuera de rango.")
    orders = {"date_desc": "f.InvoiceDate DESC, f.InvoiceLineID DESC",
              "amount_desc": "f.ExtendedPrice DESC, f.InvoiceLineID DESC"}
    order = orders[sort]
    predicate, parameters = where(filters)
    sql = ("SELECT f.InvoiceLineID AS invoice_line_id, f.InvoiceID AS invoice_id, f.InvoiceDate AS invoice_date, "
           "f.CustomerID AS customer_id, c.CustomerName AS customer_name, "
           "f.BillToCustomerID AS bill_to_customer_id, b.CustomerName AS bill_to_customer_name, "
           "f.StockItemID AS stock_item_id, p.StockItemName AS product_name, "
           "f.Quantity AS quantity, f.UnitPrice AS unit_price, f.ImporteBaseLinea AS base_amount, "
           "f.TaxAmount AS tax_amount, f.ExtendedPrice AS extended_amount, f.LineProfit AS profit_amount, "
           "f.IsCreditNote AS is_credit_note FROM [dw].[FactVentas] f "
           "LEFT JOIN [dw].[DimCliente] c ON c.ClienteKey = f.ClienteVentaKey "
           "LEFT JOIN [dw].[DimCliente] b ON b.ClienteKey = f.ClienteFacturacionKey "
           "LEFT JOIN [dw].[DimProducto] p ON p.ProductoKey = f.ProductoKey "
           f"WHERE {predicate} ORDER BY {order} OFFSET ? ROWS FETCH NEXT ? ROWS ONLY;")
    return sql, parameters + ((page - 1) * page_size, page_size)
