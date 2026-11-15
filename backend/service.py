"""Consultas coherentes con la publicación actual, sin bloquear el ETL."""

from datetime import date, datetime, timezone
from decimal import Decimal
from threading import BoundedSemaphore
from uuid import UUID

from . import queries
from .domain import SalesFilters, common_months, empty_kpis, kpis, selected_months
from .repository import ApiProblem

CATALOG_LIMIT = 5000


def json_value(value):
    if isinstance(value, Decimal):
        return format(value, ".2f")
    if isinstance(value, date):
        return value.isoformat()
    return value


class AnalyticsService:
    def __init__(self, repository, max_concurrent=4):
        self.repository = repository
        self.slots = BoundedSemaphore(max_concurrent)

    @staticmethod
    def publication(reader):
        rows = reader.query(queries.PUBLICATION, ("sales_to_warehouse",))
        if not rows:
            raise ApiProblem(503, "DW_NOT_PUBLISHED", "Todavía no hay una publicación analítica disponible.")
        stamp = rows[0]["published_at_utc"]
        if isinstance(stamp, datetime):
            stamp = stamp.replace(tzinfo=timezone.utc).isoformat().replace("+00:00", "Z")
        return {"id": str(UUID(str(rows[0]["execution_id"]))), "published_at_utc": stamp}

    def consistent(self, operation, expected_publication=None):
        expected_id = str(UUID(str(expected_publication))) if expected_publication is not None else None
        if not self.slots.acquire(blocking=False):
            raise ApiProblem(503, "API_BUSY", "El panel está atendiendo otras consultas. Intenta nuevamente.")
        try:
            # READ COMMITTED no lee cambios sin confirmar. La segunda lectura del
            # checkpoint detecta una publicación entre consultas; no se filtra por
            # publication_id de cada hecho, que representa su última modificación.
            for _ in range(2):
                with self.repository.session() as reader:
                    before = self.publication(reader)
                    if expected_id is not None and before["id"] != expected_id:
                        raise ApiProblem(409, "PUBLICATION_CHANGED", "Hay una publicación más reciente. Actualiza el panel.")
                    result = operation(reader)
                    after = self.publication(reader)
                    if before["id"] == after["id"]:
                        return {"publication": before, **result}
            raise ApiProblem(409, "PUBLICATION_CHANGED", "Los datos se actualizaron durante la consulta. Actualiza el panel.")
        finally:
            self.slots.release()

    @staticmethod
    def coverage(reader):
        row = reader.query("SELECT MIN(InvoiceDate) AS date_from, MAX(InvoiceDate) AS date_to "
                           "FROM [dw].[FactVentas] WHERE EsEliminadoFuente = 0;")[0]
        months = reader.query("SELECT DISTINCT CONVERT(char(7), InvoiceDate, 126) AS period "
                              "FROM [dw].[FactVentas] WHERE EsEliminadoFuente = 0 ORDER BY period;")
        return {"date_from": json_value(row["date_from"]), "date_to": json_value(row["date_to"]),
                "observed_months": [r["period"] for r in months]}

    def catalogs(self):
        def read(reader):
            customers = reader.query(
                "SELECT TOP (?) d.CustomerID AS id, d.CustomerName AS name FROM [dw].[DimCliente] d "
                "WHERE d.CustomerID IS NOT NULL AND (d.EsEliminadoFuente = 0 OR EXISTS "
                "(SELECT 1 FROM [dw].[FactVentas] f WHERE f.EsEliminadoFuente = 0 "
                "AND (f.ClienteVentaKey = d.ClienteKey OR f.ClienteFacturacionKey = d.ClienteKey))) "
                "ORDER BY d.CustomerName, d.CustomerID;", (CATALOG_LIMIT + 1,))
            products = reader.query(
                "SELECT TOP (?) d.StockItemID AS id, d.StockItemName AS name FROM [dw].[DimProducto] d "
                "WHERE d.StockItemID IS NOT NULL AND (d.EsEliminadoFuente = 0 OR EXISTS "
                "(SELECT 1 FROM [dw].[FactVentas] f WHERE f.EsEliminadoFuente = 0 AND f.ProductoKey = d.ProductoKey)) "
                "ORDER BY d.StockItemName, d.StockItemID;", (CATALOG_LIMIT + 1,))
            return {"coverage": self.coverage(reader), "customers": customers[:CATALOG_LIMIT],
                    "products": products[:CATALOG_LIMIT],
                    "catalogs_truncated": len(customers) > CATALOG_LIMIT or len(products) > CATALOG_LIMIT}
        return self.consistent(read)

    def overview(self, filters: SalesFilters, role="buyer", expected_publication=None):
        def read(reader):
            coverage = self.coverage(reader)
            summary = kpis(reader.query(*queries.aggregate(filters))[0])
            monthly_rows = reader.query(*queries.aggregate(filters, "month"))
            monthly = {f"{r['year']:04d}-{r['month']:02d}": kpis(r) for r in monthly_rows}
            selected = selected_months(coverage, filters)
            observed = set(coverage["observed_months"])
            series = [{"period": p, "kpis": monthly.get(p, empty_kpis()) if p in observed else None,
                       "observed": p in observed} for p in selected]
            annual = {r["year"]: kpis(r) for r in reader.query(*queries.aggregate(filters, "year"))}
            months = common_months(coverage, filters)
            comparable = {r["year"]: kpis(r) for r in reader.query(*queries.aggregate(filters, "year", months))} if months else {}
            annual_rows = []
            for year in sorted({int(p[:4]) for p in selected if p in observed}):
                year_months = sorted(int(p[5:]) for p in selected if int(p[:4]) == year and p in observed)
                annual_rows.append({"year": year, "kpis": annual.get(year, empty_kpis()), "observed_months": year_months,
                                    "comparable_kpis": comparable.get(year, empty_kpis()) if months else None})
            products = reader.query(*queries.ranking(filters, "product"))
            customers = reader.query(*queries.ranking(filters, "customer", role))
            def rank(rows):
                return [{"id": r["id"], "name": r["name"] or "Sin etiqueta", "kpis": kpis(r)} for r in rows]
            return {"coverage": coverage, "kpis": summary, "monthly": series,
                    "annual": annual_rows, "comparison_months": months,
                    "comparison_partial_dates": bool((filters.date_from and filters.date_from.day != 1)
                                                       or (filters.date_to and filters.date_to.day != 1)),
                    "top_products": rank(products), "top_customers": rank(customers), "customer_role": role}
        return self.consistent(read, expected_publication)

    def details(self, filters, page, page_size, sort, expected_publication=None):
        query = queries.details(filters, page, page_size, sort)
        def read(reader):
            predicate, parameters = queries.where(filters)
            total = reader.query(f"SELECT COUNT_BIG(*) AS total FROM [dw].[FactVentas] f WHERE {predicate};", parameters)[0]["total"]
            rows = reader.query(*query)
            return {"page": page, "page_size": page_size, "total": int(total),
                    "total_pages": (int(total) + page_size - 1) // page_size,
                    "items": [{key: json_value(value) for key, value in row.items()} for row in rows]}
        return self.consistent(read, expected_publication)
