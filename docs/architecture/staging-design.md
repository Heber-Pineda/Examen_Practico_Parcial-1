# Contrato de staging, calidad y publicación

Versión 0.2 · 2026-11-14 · **PROPUESTA** basada en las verificaciones de origen. Se especifica comportamiento futuro; no hay objetos SQL ni procesos creados.

## Alcance y proyecciones

**CONFIRMADA:** separación Raw, Clean y Ready aprobada. **PROPUESTA — S-01:** extraer solo la proyección necesaria de las cuatro entidades, conservando tipos y valores de origen.

| Entidad | Columnas de origen incluidas en la propuesta |
|---|---|
| Sales.Customers | CustomerID, CustomerName, BillToCustomerID, ValidFrom, ValidTo |
| Warehouse.StockItems | StockItemID, StockItemName, Brand, ValidFrom, ValidTo |
| Sales.Invoices | InvoiceID, CustomerID, BillToCustomerID, InvoiceDate, IsCreditNote, LastEditedWhen |
| Sales.InvoiceLines | InvoiceLineID, InvoiceID, StockItemID, Quantity, UnitPrice, TaxAmount, ExtendedPrice, LineProfit, LastEditedWhen |

Estas columnas existen en el diccionario local. La proyección no incorpora teléfono, dirección, fotos ni texto libre innecesario. Raw no equivale a una copia completa de toda WideWorldImporters.

**CONFIRMADO:** Customers y StockItems son temporales, con Sales.Customers_Archive y Warehouse.StockItems_Archive. Invoices e InvoiceLines no son temporales. Los cuatro controles de calidad comunicados sobre 228.265 líneas dieron cero incumplimientos; se registran en [verificaciones de origen](../../database/README.md#resultados-confirmados-en-sql-server). Esto no elimina controles futuros ni convierte nulabilidad permitida en NOT NULL.

**PROPUESTO:** la carga principal sigue leyendo el estado actual de las cuatro entidades. El aprovechamiento auxiliar de archivos temporales se diseñará tras perfilar cobertura y columnas; sus versiones no se integran directamente con las líneas de Ready. El nombre del archivo queda registrado como procedencia si se autoriza su lectura.

La fuente original WideWorldImporters y sus archivos temporales permanecerán intactos. Las conexiones futuras al original serán de solo lectura; cualquier simulación de cambios, reinicio o prueba utilizará un entorno independiente y autorizado.

## Identidad y procedencia

Todos los nombres siguientes son **metadatos TÉCNICOS ETL propuestos**, no columnas de negocio del origen:

| Metadato | Contrato |
|---|---|
| execution_id | UUID de un lote lógico; estable durante sus reintentos |
| attempt_id | UUID nuevo por intento; no sustituye la identidad del lote |
| source_snapshot_id | Identificador del corte coherente común a las cuatro entidades |
| source_system / source_instance / source_database | Identificación del origen confirmado, sin credenciales |
| source_schema / source_table / source_key | Entidad y clave natural exacta del registro |
| extracted_at_utc | Momento técnico de captura en UTC |
| schema_version / rules_version | Versiones de proyección y catálogo de reglas |
| payload_hash | SHA-256 de la representación canónica de los campos fuente retenidos |
| operation | SNAPSHOT, I, U o D; clasificación técnica del cambio |
| publication_id | Identificador de una publicación analítica exitosa |
| rule_id / severity / error_detail | Regla, severidad y motivo de una incidencia |

Las fechas comerciales y LastEditedWhen no se convierten a UTC por suposición. La configuración temporal de los catálogos ya está confirmada; la correspondencia exacta de columnas de período se verificará al preparar lecturas. Tiempo de sistema, fecha de venta y fecha ETL se conservan separados.

operation es SNAPSHOT al recibir Raw completo. La clasificación I/U/D se agrega en la comparación y en Clean/Ready, sin modificar el registro Raw inmutable.

Canonicalización propuesta del hash: orden fijo de columnas por schema_version, UTF-8 Unicode, NULL con marca distinta de cadena vacía/cero, números decimales exactos con escala acordada y fechas ISO de precisión completa. Conservar espacios y mayúsculas; no concatenar con separadores ambiguos. Excluir metadatos de ejecución del hash del payload. Un cambio de schema_version exige una nueva línea base de comparación.

## Responsabilidad por zona

| Zona | Entrada | Comportamiento propuesto | Salida |
|---|---|---|---|
| Raw | Proyección fuente de un corte coherente | Inmutable por execution_id; valores y tipos originales, claves, hash y manifiesto completo | Evidencia recibida y reconstruible dentro de la retención |
| Clean | Raw | Validar tipos, claves y reglas; conservar valores aceptados y registrar incidencias | Entidades aceptadas, advertencias y cuarentena |
| Ready | Entidades Clean del mismo corte | Integrar líneas con cabeceras, resolver roles y claves, calcular base, comprobar grano | Conjunto candidato a publicar y resultados de reconciliación |

**PROPUESTA — S-02:** cada registro lógico Raw se identifica por execution_id, entidad y clave natural. Un reintento del mismo registro con igual hash es repetición técnica, no un nuevo dato de negocio. Una repetición conflictiva con payload diferente invalida el corte; no se elige arbitrariamente un ganador.

Los snapshots completos y manifiestos de claves se marcan completos únicamente tras terminar extracción y controles. Una extracción truncada o un filtro accidental no permite inferir eliminaciones.

## Catálogo mínimo de reglas propuesto

| ID | Regla | Acción propuesta |
|---|---|---|
| DQ-01 | Claves naturales obligatorias, tipos int/date/decimal y bit válidos | Cuarentena y bloqueo si incumplen |
| DQ-02 | Clave única por entidad dentro del mismo corte | Repetición idéntica técnica idempotente; conflicto bloqueante |
| DQ-03 | Toda línea elegible tiene cabecera única | Ausencia o multiplicidad: cuarentena crítica y bloqueo |
| DQ-04 | Fecha y roles obligatorios de la factura según diccionario | Ausencia o conversión fallida: bloqueo |
| DQ-05 | Quantity, TaxAmount, ExtendedPrice y LineProfit respetan nulabilidad/tipos documentados | Cuarentena crítica; no publicar un universo parcial |
| DQ-06 | UnitPrice puede ser NULL | Conservar línea; base NULL, advertencia y KPI base/margen incompletos |
| DQ-07 | Catálogo cliente/producto no resuelto con clave natural válida | Miembro técnico 0 e incidencia; política por aprobar |
| DQ-08 | Brand NULL permitido | Conservar NULL; no rechazar ni convertir producto en desconocido |
| DQ-09 | Precio cero/negativo y outliers monetarios | Advertencia y revisión; conservar valor y signo. Cero precios iguales a cero observados no demuestra ausencia de negativos |
| DQ-10 | Espacios, formatos y estandarización | Perfilar; cualquier corrección de negocio requiere regla/versionado y valor original |
| DQ-11 | Diferencia entre base + impuestos e importe extendido | Diagnóstico, no igualdad obligatoria por línea sin validar redondeos/reglas |
| DQ-12 | Integración mantiene una fila por InvoiceLineID | Bloquear joins que multipliquen o pierdan líneas |
| DQ-13 | BillTo de factura y relación actual de cliente se usan con semánticas distintas | Validar por separado las cinco relaciones; no sustituir roles |
| DQ-14 | Eliminación implica ausencia en snapshot completo del mismo alcance | Sin completitud acreditada: bloqueo de detección de bajas |
| DQ-15 | Quantity menor o igual a cero sin clasificación comercial aprobada | Conservar Raw y poner en cuarentena de revisión; bloquear publicación hasta resolver o aplicar una regla aprobada de crédito/devolución. No tomar valor absoluto |

DQ-15 es una política **PROPUESTA**, no una regla comercial confirmada por haber observado cero casos. Un precio cero sigue siendo un número válido; su significado comercial requiere revisión y puede producir un margen indefinido. Precios nulos mantienen DQ-06 aunque no se observaron en el perfilado; ExtendedPrice nulo mantiene DQ-05. No se elimina silenciosamente ningún registro para hacer coincidir totales.

**PENDIENTE:** predicados comerciales, tolerancias, unidad monetaria, supervivencia de duplicados de negocio y tratamiento de notas de crédito. No se rellenan numéricos con cero ni se recortan nombres por defecto. La tabla propone reglas técnicas y acciones para revisión, no valida datos actuales.

## Cuarentena y estados

**PROPUESTA — S-03:** conservar registro original, execution_id/attempt_id, entidad, clave, snapshot, regla fallida, severidad, fecha, valor esperado/observado y detalle. El número de incidencias puede exceder el de registros rechazados: un registro puede fallar varias reglas.

Las advertencias permanecen en aceptados y tienen conteo propio. No sumarlas otra vez como rechazos. Una corrección se reevalúa con rules_version identificada, manteniendo la evidencia del intento anterior.

Estado del lote propuesto:

```text
CREATED → EXTRACTING → RAW_COMPLETE → VALIDATED → READY → PUBLISHED
                        ↘ FAILED / BLOCKED ↗ reintento trazable
```

FAILED/BLOCKED nunca avanzan el checkpoint publicado. Una regla crítica incumplida bloquea todo el lote; mantener la publicación anterior permite evitar indicadores silenciosamente parciales. UnitPrice NULL permitido no es por sí solo un rechazo crítico.

## Auditoría y reconciliación

**PROPUESTA — S-04:** registrar pipeline_name, execution_id, attempt_id, publicación base, snapshot, inicio/fin, estado, versión de reglas, objetos origen/destino, conteos por entidad, marcas temporales observadas y error_detail. Son requisitos lógicos, no DDL.

Controles por corte:

- rows_read de registros Raw únicos = accepted_clean + rejected_unique. Duplicados de entrega se cuentan aparte; varias reglas de una fila no aumentan rejected_unique.
- warnings es un subconjunto o contador de incidencias de aceptados, no otro término de la igualdad anterior.
- ready_fact_rows = líneas Clean aceptadas, si las cabeceras son completas y únicas.
- Para cada entidad comparada: current = previous_active + inserted - deleted. updated y unchanged no cambian cardinalidad.
- La unión de líneas afectadas por cambios de línea, cabecera o resolución de dimensión se cuenta una sola vez.
- Comparar conjuntos de claves, hashes y valores por línea; los totales agregados pueden ocultar diferencias que se compensan.

Conciliar Quantity, importe base calculable, TaxAmount, ExtendedPrice, LineProfit y facturas distintas usando exactamente el mismo corte, filtros y política de nulos. Las claves desconocidas conservan medidas y cuentan como incidencia.

Los totales del informe son referencias históricas documentales, no resultados esperados rígidos de una fuente modificada. Las [consultas propuestas](../quality/metrics-contract.md) deben ejecutarse y contrastarse en una fase posterior.

## Publicación y recuperación

**PROPUESTA — S-05:** en una única transacción del destino publicar dimensiones, altas/actualizaciones/bajas de hechos, auditoría de éxito, publication_id y el vector de checkpoints. Si no caben en esa unidad de publicación, rediseñar la publicación antes de implementar, sin exponer fragmentos como un lote completo.

Raw y la evidencia de intentos fallidos se conservan fuera de la transacción final. Un rollback mantiene DW y checkpoints anteriores. Si se pierde la confirmación después de commit, consultar por execution_id antes de repetir; un lote ya publicado es un no-op.

La lectura de múltiples KPIs debe usar un mismo publication_id y una estrategia de consistencia aprobada. No se presupone configuración SNAPSHOT/RCSI ni se permite leer cambios sin confirmar.

## Retención propuesta

**PROPUESTA — S-06:** retención inicial por generaciones, condicionada a capacidad medida y aprobación:

| Zona / evidencia | Política candidata |
|---|---|
| Raw y manifiestos | Dos generaciones completas publicadas como mínimo: actual y base anterior; proteger todas las referencias de ejecuciones abiertas/fallidas |
| Clean / Ready | Generación publicada actual y anterior; conservar generaciones bloqueadas necesarias para diagnóstico |
| Cuarentena | Conservar mientras esté abierta; tras resolución, 90 días propuestos |
| Auditoría, incidencias y hashes | 90 días propuestos y todas las referencias activas |
| DW | Conservar historia comercial acordada; retención independiente de staging |
| CSV/ERD originales y documentos académicos | Fuera de esta política; no depurar ni alterar |
| WideWorldImporters original y sus archivos temporales | Fuera de la retención analítica; ninguna depuración o prueba sobre ellos |

Antes de cualquier depuración futura comprobar publicación exitosa, dependencias, referencias de reintento y recuperación. Si dos generaciones no caben, detener y revisar capacidad; no borrar una base todavía requerida. Esta fase no programa ni ejecuta eliminaciones.

La retención acota la reconstrucción desde payload Raw: después de expirar un payload, el hash y la auditoría mantienen trazabilidad, pero no permiten recrear por sí solos el contenido. La reproducibilidad de datasets futuros de ML necesitará una retención/versionado propia.

Referencias: [modelo](dimensional-model.md), [incrementales](incremental-loads.md), [decisiones pendientes](architecture.md#decisiones-y-pendientes-para-implementar). SQL Server Express requiere medición de espacio y recursos; no se ha comprobado capacidad disponible.
