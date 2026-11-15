# Contrato de los seis indicadores comerciales

Versión 0.2 · 2026-11-14 · **PROPUESTA**, salvo fórmulas documentadas y observaciones expresamente CONFIRMADAS. Todo SQL de este archivo sigue **PROPUESTO, NO EJECUTADO Y NO VALIDADO EN EL MOTOR POR EL AGENTE**.

## Fuentes y alcance común

**CONFIRMADA:** tabla 4 del informe: base = suma de Quantity × UnitPrice; extendido = suma ExtendedPrice; unidades = suma Quantity; facturas = InvoiceID distintos; utilidad = suma LineProfit; margen = utilidad/base × 100. La confirmación acredita la definición documental, no una regla comercial aprobada ni un resultado calculado en esta fase.

### Verificaciones observadas

**CONFIRMADO por el usuario:** sobre 228.265 líneas se observaron 0 precios nulos, 0 precios iguales a cero, 0 cantidades menores o iguales a cero y 0 importes extendidos nulos. El detalle y su procedencia se registran en [verificaciones de origen](../../database/README.md#resultados-confirmados-en-sql-server). Estas comprobaciones no sustituyen las consultas KPI propuestas ni establecen reglas para datos futuros.

El informe actual documenta base 172.261.341,20, impuestos 25.782.098,25 y extendido 198.043.439,45; también documenta su conciliación agregada. Es evidencia de esos agregados en el descubrimiento, no de una identidad por línea, de todos los filtros o del cálculo de utilidad/margen en esta revisión.

No se deduce que todos los precios sean positivos: el control informado solo contó precios nulos e iguales a cero. Tampoco se han comunicado contadores nuevos de cantidades nulas, impuestos nulos o utilidades nulas. Se mantienen los controles de nulabilidad del diccionario y las defensas de las consultas.

**PROPUESTA — M-01:** universo común de líneas activas en el corte publicado del [modelo dimensional](../architecture/dimensional-model.md), con cabecera válida. Cada línea participa una sola vez. Las consultas de validación leen el estado vigente del origen en el mismo corte de referencia y no contienen filtros por metadatos técnicos del DW.

No mezclar todas las facturas de cabecera con facturas que poseen líneas elegibles. Una factura sin líneas no está representada en el hecho. Q-PRE-03 permite documentar esa diferencia, sin afirmar ahora que exista o que su conteo sea cero.

## Filtros admitidos y calendario

**PROPUESTA — M-02**, aplicable a los seis indicadores:

| Filtro | Campo fuente | Semántica |
|---|---|---|
| Fecha desde | `Sales.Invoices.InvoiceDate` | Inclusivo |
| Fecha hasta | `Sales.Invoices.InvoiceDate` | Exclusivo; desde menor que hasta cuando ambos existen |
| Cliente de venta | `Sales.Invoices.CustomerID` | ID natural exacto |
| Responsable de facturación | `Sales.Invoices.BillToCustomerID` | Rol separado del cliente de venta |
| Producto | `Sales.InvoiceLines.StockItemID` | ID natural exacto |
| Nota de crédito | `Sales.Invoices.IsCreditNote` | NULL del parámetro incluye ambos valores; 0/1 filtra explícitamente |

Los parámetros NULL significan filtro no aplicado. Agrupaciones admitidas: día/mes/año de InvoiceDate, cliente por rol y producto. Etiquetas de nombres y Brand pueden mostrarse a través de dimensiones; filtrar por esos atributos necesita una extensión explícita de estas consultas, manteniendo selección por IDs sin multiplicar filas.

Unidad monetaria: **PENDIENTE**. Estas cuatro tablas no aportan un campo de moneda en el contrato seleccionado; se exige confirmar homogeneidad antes de mezclar importes. No se asume USD ni HNL ni se convierten monedas. Quantity representa la unidad registrada por la fuente, sin convertir embalajes, peso o volumen.

La cobertura documentada es enero de 2013 a mayo de 2016. Un universo vacío devuelve sumas/conteos cero y estado SIN_DATOS; no acredita ventas cero fuera de cobertura. La continuidad mensual y el tratamiento de meses sin observaciones deben verificarse antes de alimentar ML. La fecha ETL no reemplaza InvoiceDate.

## Nulos, signos y precisión

**PROPUESTA — M-03:** ningún NULL monetario se convierte automáticamente en cero. Una medida incompleta devuelve NULL e indicador INCOMPLETO; las medidas independientes pueden seguir siendo calculables.

UnitPrice permite NULL según el diccionario. Una línea con ese caso se conserva, pero hace incompletos base y margen en cualquier agregado que la incluya. Los NULL inesperados de Quantity, ExtendedPrice, TaxAmount o LineProfit incumplen el contrato de calidad y bloquean publicación; las consultas también los exponen defensivamente para la medida afectada.

**PROPUESTA — M-04:** mantener signos del origen, incluidas cantidades o importes negativos. IsCreditNote no invierte signos. La inclusión y significado comercial de notas de crédito está **PENDIENTE**; el valor por defecto de los ejemplos no excluye registros.

Una cantidad menor o igual a cero sin regla de clasificación comercial aprobada se conservaría en Raw y pasaría a revisión bloqueante según DQ-15 propuesta; no se corregiría ni se publicaría un conjunto parcial. Si una regla aprobada acepta créditos/devoluciones, se conserva el signo y se aplica a todo el universo comparado. Esta política está PROPUESTA, no confirmada por la ausencia de esos casos en la muestra.

Un precio cero es numéricamente válido: conservarlo, advertir y revisar su significado, sin convertirlo en NULL o eliminar la línea. Un precio negativo también requiere revisión; su frecuencia no fue informada. Aunque no hubiera precios cero, un denominador agregado cero podría existir por compensación de signos; NULLIF y el estado DENOMINADOR_CERO siguen siendo necesarios.

### Impuestos, moneda y períodos incompletos

**PROPUESTO:** preservar TaxAmount, ExtendedPrice y LineProfit con su precisión y signo. ExtendedPrice se suma tal como está almacenado; no se vuelve a añadir TaxAmount al KPI extendido. Base más impuestos es un diagnóstico de conciliación, no una autorización para recalcular o sobrescribir importes ni deducir tasas/descuentos sin regla validada.

**PENDIENTE:** moneda, unidad de Quantity, política de redondeo, tolerancias e inclusión de créditos. Cero créditos en el descubrimiento no valida una política universal de signos. La implementación no deberá inferir reglas fiscales a partir de una coincidencia agregada.

**PROPUESTO:** mostrar cobertura y meses incluidos; comparar enero–mayo de 2016 con enero–mayo de otros años cuando proceda. No anualizar automáticamente, completar meses futuros con cero ni interpretar una caída anual como un resultado comercial si los períodos tienen distinta cobertura.

**PROPUESTA — M-05:** aritmética decimal exacta. Quantity convertido a decimal(10,0) multiplicado por UnitPrice decimal(18,2) produce decimal(29,2). Sumas monetarias en decimal(38,2); Quantity en bigint. No redondear cada línea antes de sumar ni usar float.

Para el porcentaje se proponen agregados intermedios decimal(28,2), multiplicación por decimal(5,2) con valor 100 y salida decimal(38,6). Un agregado fuera del rango intermedio produce error explícito y requiere revisar el contrato; no se convierte en NULL con TRY_CAST ni en cero. La política de presentación y tolerancia de porcentaje está pendiente de aprobación.

Estas propuestas consideran las reglas de [precisión decimal](https://learn.microsoft.com/en-us/sql/t-sql/data-types/precision-scale-and-length-transact-sql?view=sql-server-ver16) y [SUM](https://learn.microsoft.com/en-us/sql/t-sql/functions/sum-transact-sql?view=sql-server-ver16). El comportamiento real de las consultas todavía no se ha probado.

## Especificación por indicador

| ID | Indicador / propósito | Fórmula y campos fuente | Unidad | Grano y agregación | Nulos / vacío / cero | Riesgo y validación propuesta |
|---|---|---|---|---|---|---|
| KPI-01 | Importe base facturado; volumen monetario base | Σ(`Sales.InvoiceLines.Quantity` × `Sales.InvoiceLines.UnitPrice`) | Unidad monetaria pendiente | Base por línea; suma aditiva bajo M-01/M-02 | Alguna base NULL → NULL/INCOMPLETO; vacío → 0/SIN_DATOS | Duplicación por joins; Q-KPI-01 |
| KPI-02 | Importe extendido; importe almacenado en la línea | Σ `Sales.InvoiceLines.ExtendedPrice` | Unidad monetaria pendiente | Línea; suma aditiva bajo M-01/M-02 | Algún valor NULL → NULL/INCOMPLETO; vacío → 0/SIN_DATOS | No reconstruir automáticamente con base + impuesto; Q-KPI-02 |
| KPI-03 | Unidades vendidas; cantidad registrada | Σ `Sales.InvoiceLines.Quantity` | Unidades registradas, sin conversión | Línea; suma bigint bajo M-01/M-02 | NULL → NULL/INCOMPLETO; vacío → 0/SIN_DATOS | No interpretar como peso ni mezclar conversiones no definidas; Q-KPI-03 |
| KPI-04 | Facturas distintas; documentos con líneas elegibles | COUNT DISTINCT `Sales.InvoiceLines.InvoiceID` con cabecera válida | Facturas | Recalcular en cada conjunto filtrado bajo M-01/M-02 | ID nulo/cabecera inválida → corte no publicable; vacío → 0/SIN_DATOS | Una factura puede aparecer en varios productos; no sumar subtotales distintos; Q-KPI-04 |
| KPI-05 | Utilidad registrada; medida almacenada por la fuente | Σ `Sales.InvoiceLines.LineProfit` | Unidad monetaria pendiente | Línea; suma aditiva bajo M-01/M-02 | NULL → NULL/INCOMPLETO; vacío → 0/SIN_DATOS | No equivale automáticamente a utilidad contable neta; Q-KPI-05 |
| KPI-06 | Margen de utilidad; relación utilidad/base | 100 × Σ `Sales.InvoiceLines.LineProfit` / Σ(Quantity × UnitPrice de Sales.InvoiceLines) | Porcentaje | Cociente de sumas del mismo universo, no promedio de porcentajes; M-01/M-02 | Faltantes → NULL/INCOMPLETO; base total 0 → NULL/DENOMINADOR_CERO; vacío → NULL/SIN_DATOS | Sumar o promediar márgenes produce resultados distintos; Q-KPI-06 |

Los seis indicadores usan filtros idénticos, corte/publication_id compartido y período explícito. Responsable de aceptación funcional: **PENDIENTE de designación**. Se distinguen unidad del dato y formato visual.

## Riesgos de relaciones y reglas pendientes

- Línea → factura debe ser muchos-a-uno por InvoiceID; una cabecera duplicada multiplica todas sus medidas.
- Unir las dos funciones de cliente en una sola condición o aplicar la autorreferencia actual cambia el significado de las ventas.
- Unir históricos de dimensión sin resolver una única versión multiplica líneas. La propuesta inicial SCD Tipo 1 evita versiones múltiples; cambiarla requiere contrato nuevo.
- Brand y nombres no son claves únicas; los joins se hacen por IDs/claves sustitutas, no por texto.
- Un filtro de producto mantiene solo sus líneas, pero cuenta una vez cada factura que tenga al menos una línea elegible.
- La igualdad agregada base + impuesto = extendido observada en el informe no prueba identidad por línea ni define descuentos/redondeo.
- Confirmar moneda, signos/créditos, significado de unidades, faltantes, rol de cliente predeterminado y comparaciones de 2016 parcial.

## Casos de aceptación de métricas, pendientes de ejecución

Usar el fixture sintético F0 del contrato incremental. Los valores son expectativas matemáticas, no resultados de SQL.

| Caso | Condición | Resultado esperado |
|---|---|---|
| MET-01 | F0 completo | Base 25.00; extendido 28.75; unidades 3; factura 1; utilidad 7.00; margen 28 % |
| MET-02 | Solo producto 920001 / solo 920002 | Bases 20.00 / 5.00; facturas 1 / 1; márgenes 30 % / 20 % |
| MET-03 | Agregar ambos productos | Facturas globales 1; margen global 28 %, no promedio simple 25 % |
| MET-04 | UnitPrice NULL en 940001 | Base/margen NULL e INCOMPLETO; otras medidas conservan F0 |
| MET-05 | Ambos precios, impuestos, extendidos y utilidades a cero | Base 0 y margen NULL/DENOMINADOR_CERO; unidades 3 y factura 1 |
| MET-06 | Intervalo sin líneas elegibles | Sumas/conteo 0/SIN_DATOS; margen NULL; revisar cobertura antes de interpretar |
| MET-07 | Filtro por los dos roles o por fecha de cabecera | Selección idéntica en origen y DW; no sustituir autorreferencia del catálogo |
| MET-08 | Catálogo no resuelto, con política de desconocidos aceptada | Importes conservados bajo clave 0 e incidencia explícita |
| MET-09 | Cabecera duplicada o línea sin cabecera | Precontrol/carga bloqueados; no aceptar un resultado multiplicado o parcial |
| MET-10 | Cambiar solo UnitPrice del catálogo | Importe base de ventas permanece basado en el precio de sus líneas |
| MET-11 | Indicador de crédito con importes de prueba signados | Con regla comercial aprobada, aplicar filtro y conservar signos; sin regla para cantidad no positiva, revisión bloqueante propuesta |
| MET-12 | ExtendedPrice distinto de base + TaxAmount | Registrar diagnóstico; no reconstruir ni sobrescribir valores sin regla aprobada |
| MET-13 | Cantidad cero o negativa sin clasificación aprobada | Conservar Raw y bloquear publicación hasta resolver; no ocultar la fila ni tomar valor absoluto |
| MET-14 | Precio negativo y cantidad positiva en el entorno aislado | Advertir y revisar sin afirmar que ese caso fue medido en el origen; signos sin corrección automática |

## Protocolo para ejecutar en una fase futura

1. Obtener autorización de conexión/SQL y un corte consistente del origen y la publicación comparada. El original se consulta en modo de solo lectura; toda mutación, simulación o reinicio se realiza en un entorno de prueba independiente y autorizado.
2. Ejecutar los precontroles propuestos y revisar faltantes/duplicados antes de aceptar INNER JOIN.
3. Acreditar claves únicas y ausencia de líneas sin cabecera. Los faltantes de catálogos se evalúan según la política explícita de desconocidos, nunca mediante un join que pierda importes.
4. Ejecutar cada consulta completa en contexto WideWorldImporters, con parámetros aprobados e idénticos a los filtros DW.
5. Comparar por línea y por grupos, no solo totales. Conteos y sumas decimales completos deben coincidir exactamente; porcentaje requiere tolerancia aprobada.
6. Registrar ejecución, parámetros, corte, filas, faltantes, resultado origen, resultado DW, diferencias y explicación.

Los parámetros de los ejemplos son NULL para todo el conjunto. Debe validarse el intervalo antes de ejecutar. No se han medido tiempos, contrastado totales ni validado planes o sintaxis en SQL Server.

### Q-PRE-01 — Claves del universo (NO EJECUTADA)

```sql
SELECT 'Sales.Customers' AS Entidad, COUNT_BIG(*) AS Filas,
       COUNT_BIG(DISTINCT CustomerID) AS ClavesDistintas,
       COUNT_BIG(*) - COUNT_BIG(CustomerID) AS ClavesNulas
FROM Sales.Customers
UNION ALL
SELECT 'Warehouse.StockItems', COUNT_BIG(*),
       COUNT_BIG(DISTINCT StockItemID), COUNT_BIG(*) - COUNT_BIG(StockItemID)
FROM Warehouse.StockItems
UNION ALL
SELECT 'Sales.Invoices', COUNT_BIG(*),
       COUNT_BIG(DISTINCT InvoiceID), COUNT_BIG(*) - COUNT_BIG(InvoiceID)
FROM Sales.Invoices
UNION ALL
SELECT 'Sales.InvoiceLines', COUNT_BIG(*),
       COUNT_BIG(DISTINCT InvoiceLineID), COUNT_BIG(*) - COUNT_BIG(InvoiceLineID)
FROM Sales.InvoiceLines;
```

### Q-PRE-02 — Cinco relaciones (NO EJECUTADA)

```sql
SELECT 'Linea -> factura' AS Relacion, COUNT_BIG(*) AS SinReferencia
FROM Sales.InvoiceLines AS l
LEFT JOIN Sales.Invoices AS i ON i.InvoiceID = l.InvoiceID
WHERE i.InvoiceID IS NULL
UNION ALL
SELECT 'Linea -> producto', COUNT_BIG(*)
FROM Sales.InvoiceLines AS l
LEFT JOIN Warehouse.StockItems AS p ON p.StockItemID = l.StockItemID
WHERE p.StockItemID IS NULL
UNION ALL
SELECT 'Factura -> cliente venta', COUNT_BIG(*)
FROM Sales.Invoices AS i
LEFT JOIN Sales.Customers AS c ON c.CustomerID = i.CustomerID
WHERE c.CustomerID IS NULL
UNION ALL
SELECT 'Factura -> cliente facturacion', COUNT_BIG(*)
FROM Sales.Invoices AS i
LEFT JOIN Sales.Customers AS b ON b.CustomerID = i.BillToCustomerID
WHERE b.CustomerID IS NULL
UNION ALL
SELECT 'Cliente -> cliente facturacion actual', COUNT_BIG(*)
FROM Sales.Customers AS c
LEFT JOIN Sales.Customers AS b ON b.CustomerID = c.BillToCustomerID
WHERE b.CustomerID IS NULL;
```

### Q-PRE-03 — Cabeceras sin líneas (NO EJECUTADA)

```sql
SELECT COUNT_BIG(*) AS FacturasSinLineas
FROM Sales.Invoices AS i
WHERE NOT EXISTS
(
    SELECT 1
    FROM Sales.InvoiceLines AS l
    WHERE l.InvoiceID = i.InvoiceID
);
```

### Q-KPI-01 — Importe base facturado (NO EJECUTADA)

```sql
DECLARE @Desde date = NULL;
DECLARE @HastaExclusiva date = NULL;
DECLARE @CustomerID int = NULL;
DECLARE @BillToCustomerID int = NULL;
DECLARE @StockItemID int = NULL;
DECLARE @IsCreditNote bit = NULL;

;WITH Universo AS
(
    SELECT
        l.InvoiceLineID, l.InvoiceID, l.StockItemID,
        i.InvoiceDate, i.CustomerID, i.BillToCustomerID, i.IsCreditNote,
        l.Quantity, l.UnitPrice, l.TaxAmount, l.ExtendedPrice, l.LineProfit,
        CAST(CAST(l.Quantity AS decimal(10,0)) * l.UnitPrice
             AS decimal(29,2)) AS BaseLinea
    FROM Sales.InvoiceLines AS l
    INNER JOIN Sales.Invoices AS i ON i.InvoiceID = l.InvoiceID
    WHERE (@Desde IS NULL OR i.InvoiceDate >= @Desde)
      AND (@HastaExclusiva IS NULL OR i.InvoiceDate < @HastaExclusiva)
      AND (@CustomerID IS NULL OR i.CustomerID = @CustomerID)
      AND (@BillToCustomerID IS NULL OR i.BillToCustomerID = @BillToCustomerID)
      AND (@StockItemID IS NULL OR l.StockItemID = @StockItemID)
      AND (@IsCreditNote IS NULL OR i.IsCreditNote = @IsCreditNote)
)
SELECT
    CASE
        WHEN COUNT_BIG(*) = 0 THEN CAST(0 AS decimal(38,2))
        WHEN COUNT_BIG(*) <> COUNT_BIG(BaseLinea) THEN CAST(NULL AS decimal(38,2))
        ELSE SUM(CAST(BaseLinea AS decimal(38,2)))
    END AS ImporteBaseFacturado,
    COUNT_BIG(*) AS LineasIncluidas,
    COUNT_BIG(*) - COUNT_BIG(BaseLinea) AS LineasSinMedida,
    CASE
        WHEN COUNT_BIG(*) = 0 THEN 'SIN_DATOS'
        WHEN COUNT_BIG(*) <> COUNT_BIG(BaseLinea) THEN 'INCOMPLETO'
        ELSE 'CALCULABLE'
    END AS Estado
FROM Universo;
```

### Q-KPI-02 — Importe extendido (NO EJECUTADA)

```sql
DECLARE @Desde date = NULL;
DECLARE @HastaExclusiva date = NULL;
DECLARE @CustomerID int = NULL;
DECLARE @BillToCustomerID int = NULL;
DECLARE @StockItemID int = NULL;
DECLARE @IsCreditNote bit = NULL;

;WITH Universo AS
(
    SELECT
        l.InvoiceLineID, l.InvoiceID, l.StockItemID,
        i.InvoiceDate, i.CustomerID, i.BillToCustomerID, i.IsCreditNote,
        l.Quantity, l.UnitPrice, l.TaxAmount, l.ExtendedPrice, l.LineProfit,
        CAST(CAST(l.Quantity AS decimal(10,0)) * l.UnitPrice
             AS decimal(29,2)) AS BaseLinea
    FROM Sales.InvoiceLines AS l
    INNER JOIN Sales.Invoices AS i ON i.InvoiceID = l.InvoiceID
    WHERE (@Desde IS NULL OR i.InvoiceDate >= @Desde)
      AND (@HastaExclusiva IS NULL OR i.InvoiceDate < @HastaExclusiva)
      AND (@CustomerID IS NULL OR i.CustomerID = @CustomerID)
      AND (@BillToCustomerID IS NULL OR i.BillToCustomerID = @BillToCustomerID)
      AND (@StockItemID IS NULL OR l.StockItemID = @StockItemID)
      AND (@IsCreditNote IS NULL OR i.IsCreditNote = @IsCreditNote)
)
SELECT
    CASE
        WHEN COUNT_BIG(*) = 0 THEN CAST(0 AS decimal(38,2))
        WHEN COUNT_BIG(*) <> COUNT_BIG(ExtendedPrice) THEN CAST(NULL AS decimal(38,2))
        ELSE SUM(CAST(ExtendedPrice AS decimal(38,2)))
    END AS ImporteExtendido,
    COUNT_BIG(*) AS LineasIncluidas,
    COUNT_BIG(*) - COUNT_BIG(ExtendedPrice) AS LineasSinMedida,
    CASE
        WHEN COUNT_BIG(*) = 0 THEN 'SIN_DATOS'
        WHEN COUNT_BIG(*) <> COUNT_BIG(ExtendedPrice) THEN 'INCOMPLETO'
        ELSE 'CALCULABLE'
    END AS Estado
FROM Universo;
```

### Q-KPI-03 — Unidades vendidas (NO EJECUTADA)

```sql
DECLARE @Desde date = NULL;
DECLARE @HastaExclusiva date = NULL;
DECLARE @CustomerID int = NULL;
DECLARE @BillToCustomerID int = NULL;
DECLARE @StockItemID int = NULL;
DECLARE @IsCreditNote bit = NULL;

;WITH Universo AS
(
    SELECT
        l.InvoiceLineID, l.InvoiceID, l.StockItemID,
        i.InvoiceDate, i.CustomerID, i.BillToCustomerID, i.IsCreditNote,
        l.Quantity, l.UnitPrice, l.TaxAmount, l.ExtendedPrice, l.LineProfit,
        CAST(CAST(l.Quantity AS decimal(10,0)) * l.UnitPrice
             AS decimal(29,2)) AS BaseLinea
    FROM Sales.InvoiceLines AS l
    INNER JOIN Sales.Invoices AS i ON i.InvoiceID = l.InvoiceID
    WHERE (@Desde IS NULL OR i.InvoiceDate >= @Desde)
      AND (@HastaExclusiva IS NULL OR i.InvoiceDate < @HastaExclusiva)
      AND (@CustomerID IS NULL OR i.CustomerID = @CustomerID)
      AND (@BillToCustomerID IS NULL OR i.BillToCustomerID = @BillToCustomerID)
      AND (@StockItemID IS NULL OR l.StockItemID = @StockItemID)
      AND (@IsCreditNote IS NULL OR i.IsCreditNote = @IsCreditNote)
)
SELECT
    CASE
        WHEN COUNT_BIG(*) = 0 THEN CAST(0 AS bigint)
        WHEN COUNT_BIG(*) <> COUNT_BIG(Quantity) THEN CAST(NULL AS bigint)
        ELSE SUM(CAST(Quantity AS bigint))
    END AS UnidadesVendidas,
    COUNT_BIG(*) AS LineasIncluidas,
    COUNT_BIG(*) - COUNT_BIG(Quantity) AS LineasSinMedida,
    CASE
        WHEN COUNT_BIG(*) = 0 THEN 'SIN_DATOS'
        WHEN COUNT_BIG(*) <> COUNT_BIG(Quantity) THEN 'INCOMPLETO'
        ELSE 'CALCULABLE'
    END AS Estado
FROM Universo;
```

### Q-KPI-04 — Facturas distintas (NO EJECUTADA)

```sql
DECLARE @Desde date = NULL;
DECLARE @HastaExclusiva date = NULL;
DECLARE @CustomerID int = NULL;
DECLARE @BillToCustomerID int = NULL;
DECLARE @StockItemID int = NULL;
DECLARE @IsCreditNote bit = NULL;

;WITH Universo AS
(
    SELECT
        l.InvoiceLineID, l.InvoiceID, l.StockItemID,
        i.InvoiceDate, i.CustomerID, i.BillToCustomerID, i.IsCreditNote,
        l.Quantity, l.UnitPrice, l.TaxAmount, l.ExtendedPrice, l.LineProfit,
        CAST(CAST(l.Quantity AS decimal(10,0)) * l.UnitPrice
             AS decimal(29,2)) AS BaseLinea
    FROM Sales.InvoiceLines AS l
    INNER JOIN Sales.Invoices AS i ON i.InvoiceID = l.InvoiceID
    WHERE (@Desde IS NULL OR i.InvoiceDate >= @Desde)
      AND (@HastaExclusiva IS NULL OR i.InvoiceDate < @HastaExclusiva)
      AND (@CustomerID IS NULL OR i.CustomerID = @CustomerID)
      AND (@BillToCustomerID IS NULL OR i.BillToCustomerID = @BillToCustomerID)
      AND (@StockItemID IS NULL OR l.StockItemID = @StockItemID)
      AND (@IsCreditNote IS NULL OR i.IsCreditNote = @IsCreditNote)
)
SELECT
    COUNT_BIG(DISTINCT InvoiceID) AS FacturasDistintas,
    COUNT_BIG(*) AS LineasIncluidas,
    CASE WHEN COUNT_BIG(*) = 0 THEN 'SIN_DATOS'
         ELSE 'CALCULABLE' END AS Estado
FROM Universo;
```

### Q-KPI-05 — Utilidad registrada (NO EJECUTADA)

```sql
DECLARE @Desde date = NULL;
DECLARE @HastaExclusiva date = NULL;
DECLARE @CustomerID int = NULL;
DECLARE @BillToCustomerID int = NULL;
DECLARE @StockItemID int = NULL;
DECLARE @IsCreditNote bit = NULL;

;WITH Universo AS
(
    SELECT
        l.InvoiceLineID, l.InvoiceID, l.StockItemID,
        i.InvoiceDate, i.CustomerID, i.BillToCustomerID, i.IsCreditNote,
        l.Quantity, l.UnitPrice, l.TaxAmount, l.ExtendedPrice, l.LineProfit,
        CAST(CAST(l.Quantity AS decimal(10,0)) * l.UnitPrice
             AS decimal(29,2)) AS BaseLinea
    FROM Sales.InvoiceLines AS l
    INNER JOIN Sales.Invoices AS i ON i.InvoiceID = l.InvoiceID
    WHERE (@Desde IS NULL OR i.InvoiceDate >= @Desde)
      AND (@HastaExclusiva IS NULL OR i.InvoiceDate < @HastaExclusiva)
      AND (@CustomerID IS NULL OR i.CustomerID = @CustomerID)
      AND (@BillToCustomerID IS NULL OR i.BillToCustomerID = @BillToCustomerID)
      AND (@StockItemID IS NULL OR l.StockItemID = @StockItemID)
      AND (@IsCreditNote IS NULL OR i.IsCreditNote = @IsCreditNote)
)
SELECT
    CASE
        WHEN COUNT_BIG(*) = 0 THEN CAST(0 AS decimal(38,2))
        WHEN COUNT_BIG(*) <> COUNT_BIG(LineProfit) THEN CAST(NULL AS decimal(38,2))
        ELSE SUM(CAST(LineProfit AS decimal(38,2)))
    END AS UtilidadRegistrada,
    COUNT_BIG(*) AS LineasIncluidas,
    COUNT_BIG(*) - COUNT_BIG(LineProfit) AS LineasSinMedida,
    CASE
        WHEN COUNT_BIG(*) = 0 THEN 'SIN_DATOS'
        WHEN COUNT_BIG(*) <> COUNT_BIG(LineProfit) THEN 'INCOMPLETO'
        ELSE 'CALCULABLE'
    END AS Estado
FROM Universo;
```

### Q-KPI-06 — Margen de utilidad (NO EJECUTADA)

```sql
DECLARE @Desde date = NULL;
DECLARE @HastaExclusiva date = NULL;
DECLARE @CustomerID int = NULL;
DECLARE @BillToCustomerID int = NULL;
DECLARE @StockItemID int = NULL;
DECLARE @IsCreditNote bit = NULL;

;WITH Universo AS
(
    SELECT
        l.InvoiceLineID, l.InvoiceID, l.StockItemID,
        i.InvoiceDate, i.CustomerID, i.BillToCustomerID, i.IsCreditNote,
        l.Quantity, l.UnitPrice, l.TaxAmount, l.ExtendedPrice, l.LineProfit,
        CAST(CAST(l.Quantity AS decimal(10,0)) * l.UnitPrice
             AS decimal(29,2)) AS BaseLinea
    FROM Sales.InvoiceLines AS l
    INNER JOIN Sales.Invoices AS i ON i.InvoiceID = l.InvoiceID
    WHERE (@Desde IS NULL OR i.InvoiceDate >= @Desde)
      AND (@HastaExclusiva IS NULL OR i.InvoiceDate < @HastaExclusiva)
      AND (@CustomerID IS NULL OR i.CustomerID = @CustomerID)
      AND (@BillToCustomerID IS NULL OR i.BillToCustomerID = @BillToCustomerID)
      AND (@StockItemID IS NULL OR l.StockItemID = @StockItemID)
      AND (@IsCreditNote IS NULL OR i.IsCreditNote = @IsCreditNote)
),
Resumen AS
(
    SELECT
        COUNT_BIG(*) AS LineasIncluidas,
        COUNT_BIG(*) - COUNT_BIG(BaseLinea) AS LineasSinBase,
        COUNT_BIG(*) - COUNT_BIG(LineProfit) AS LineasSinUtilidad,
        SUM(BaseLinea) AS BaseTotal,
        SUM(CAST(LineProfit AS decimal(38,2))) AS UtilidadTotal
    FROM Universo
)
SELECT
    CASE
        WHEN LineasIncluidas = 0
          OR LineasSinBase > 0 OR LineasSinUtilidad > 0 OR BaseTotal = 0
        THEN CAST(NULL AS decimal(38,6))
        ELSE CAST(
            CAST(UtilidadTotal AS decimal(28,2)) * CAST(100 AS decimal(5,2))
            / NULLIF(CAST(BaseTotal AS decimal(28,2)), CAST(0 AS decimal(28,2)))
            AS decimal(38,6))
    END AS MargenUtilidadPorcentaje,
    LineasIncluidas, LineasSinBase, LineasSinUtilidad,
    CASE
        WHEN LineasIncluidas = 0 THEN 'SIN_DATOS'
        WHEN LineasSinBase > 0 OR LineasSinUtilidad > 0 THEN 'INCOMPLETO'
        WHEN BaseTotal = 0 THEN 'DENOMINADOR_CERO'
        ELSE 'CALCULABLE'
    END AS Estado
FROM Resumen;
```

## Referencias de consulta

[COUNT_BIG](https://learn.microsoft.com/en-us/sql/t-sql/functions/count-big-transact-sql?view=sql-server-ver16) admite DISTINCT y devuelve bigint. Los ejemplos solo usan columnas de las cuatro tablas documentadas y alias/cálculos derivados. No son migraciones, no crean objetos y no se han ejecutado.

Decisiones abiertas: [arquitectura y pendientes](../architecture/architecture.md#decisiones-y-pendientes-para-implementar). Pruebas futuras: [estrategia incremental](../architecture/incremental-loads.md) y [staging](../architecture/staging-design.md).
