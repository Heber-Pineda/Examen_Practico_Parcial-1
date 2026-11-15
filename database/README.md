# Fuente de datos, diccionario y evidencias iniciales

WideWorldImporters en SQL Server 2022 Express, instancia `HEBER-ENOC-PINE\SQLEXPRESS`, es la fuente confirmada del proceso de ventas. Se seleccionaron cuatro tablas y cinco relaciones, incluida la autorreferencia de clientes.

## Archivos de origen

| Archivo conservado | Uso |
|---|---|
| [Diccionario original](Diccionario_Datos_WideWorldImporters.csv) | Exportación original sin encabezados; 94 entradas de columnas |
| [Perfilado original](Perfilado_Inicial_WideWorldImporters.csv) | Exportación original sin encabezados; cuatro tablas |
| [ERD DBML](ERD_WideWorldImporters.dbml) | Fuente editable del modelo parcial OLTP |
| [ERD PNG](ERD_WideWorldImporters_Origen_OLTP.png) | Diagrama para consulta e informe |
| [ERD SVG](ERD_WideWorldImporters_Origen_OLTP.svg) | Versión vectorial del mismo diagrama |

Son archivos originales que se mantienen sin cambios. El ERD muestra las relaciones seleccionadas; no es un esquema completo ni DDL del origen.

## Copias con encabezados

Usar [el diccionario normalizado](normalized/Diccionario_Datos_WideWorldImporters.csv) y [el perfilado normalizado](normalized/Perfilado_Inicial_WideWorldImporters.csv) para interpretar las exportaciones. Conservan los valores, filas y orden originales: solo se añadió una cabecera después del BOM.

Formato: delimitador `;`, UTF-8 con BOM y finales CRLF. Se conservaron 94 filas/10 columnas del diccionario y 4 filas/4 columnas del perfilado. Retirando la cabecera y conservando el BOM se reconstruye cada original byte por byte. No se convierten SI/NO, tipos, espacios, signos o métricas.

### Diccionario

| Encabezado | Significado |
|---|---|
| schema_name / table_name / column_name | Esquema, tabla y columna de origen |
| data_type | Tipo SQL exportado |
| length_raw | Longitud exportada, con unidad pendiente de confirmar mediante la consulta original |
| precision / scale | Precisión y escala exportadas, también para tipos no numéricos |
| is_nullable | Permite NULL según esquema; SI/NO, no conteo de nulos observados |
| is_primary_key / is_foreign_key | Indicadores PK/FK documentados; SI/NO |

No interpretar length_raw automáticamente como caracteres ni convertir -1 sin recuperar la consulta original. Distribución de entradas: Customers 31, Invoices 25, InvoiceLines 13 y StockItems 25.

### Perfilado

| Encabezado | Significado según la tabla 5 del informe |
|---|---|
| source_table | Tabla calificada |
| row_count | Registros contados en el perfilado |
| distinct_key_count | Claves distintas contadas |
| rows_to_review | Registros que incumplieron los controles iniciales implementados |

| Tabla | Registros | Claves distintas | Registros a revisar |
|---|---:|---:|---:|
| Sales.Customers | 663 | 663 | 0 |
| Sales.Invoices | 70.510 | 70.510 | 0 |
| Sales.InvoiceLines | 228.265 | 228.265 | 0 |
| Warehouse.StockItems | 227 | 227 | 0 |

rows_to_review = 0 se limita a las reglas iniciales. No acredita calidad exhaustiva, ausencia de todos los nulos, duplicados de negocio u outliers. Las consultas, predicados y fecha exacta de extracción aún deben incorporarse a la evidencia reproducible.

## Resultados confirmados en SQL Server

Procedencia: verificaciones realizadas y comunicadas por el usuario sobre la base restaurada; el agente no las reejecutó. El informe conserva las evidencias originales, incluida la captura de restauración. La fecha/hora exacta y los scripts de las comprobaciones comunicadas no se han incorporado como evidencias ejecutables.

| Entidad | Tipo confirmado | Historial asociado |
|---|---|---|
| Sales.Customers | SYSTEM_VERSIONED_TEMPORAL_TABLE | Sales.Customers_Archive |
| Warehouse.StockItems | SYSTEM_VERSIONED_TEMPORAL_TABLE | Warehouse.StockItems_Archive |
| Sales.Invoices | NON_TEMPORAL_TABLE | No se informó tabla histórica de sistema |
| Sales.InvoiceLines | NON_TEMPORAL_TABLE | No se informó tabla histórica de sistema |

| Campo | Mínimo informado, a nivel de fecha | Máximo informado, a nivel de fecha |
|---|---|---|
| Sales.Invoices.LastEditedWhen | 2013-01-02 | 2016-05-31 |
| Sales.InvoiceLines.LastEditedWhen | 2013-01-01 | 2016-05-31 |

Los extremos no son timestamps completos, watermarks listos para usar ni el rango de InvoiceDate. Tampoco prueban cómo se escribirán las modificaciones futuras. La cobertura y retención de los archivos temporales siguen pendientes de perfilado.

Sobre 228.265 líneas observadas:

| Comprobación comunicada | Incumplimientos |
|---|---:|
| UnitPrice nulo | 0 |
| UnitPrice igual a cero | 0 |
| Quantity menor o igual a cero | 0 |
| ExtendedPrice nulo | 0 |

Estos resultados describen el estado observado. No se informó un conteo de precios negativos, cantidades nulas, impuestos nulos o utilidades nulas; no se deducen resultados para ellos ni garantías futuras.

La facturación documentada comprende enero de 2013 a mayo de 2016: 41 meses, con 2016 incompleto. El informe registra tres controles referenciales sin huérfanos y cero facturas marcadas como notas de crédito en el conjunto analizado; no sustituye la revisión de las cinco relaciones ni define la política de créditos futuros.

El informe documenta importe base acumulado 172.261.341,20, impuestos 25.782.098,25 e importe extendido 198.043.439,45. La conciliación agregada base + impuestos = extendido no prueba identidad por línea ni valida todas las consultas futuras.

Los resultados están limitados a su conjunto y momento de observación. La nulabilidad declarada, los controles medidos y las reglas comerciales futuras son conceptos diferentes.
