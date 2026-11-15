# Contrato de cargas iniciales e incrementales

Versión 0.2 · 2026-11-14 · Diseño **PROPUESTO** consolidado. La configuración y el perfilado indicados como CONFIRMADOS proceden del usuario; el agente no ejecutó cargas, consultas ni pruebas.

## Evidencia disponible y límites

| Entidad | Clave documentada | Campos de cambio documentados | Decisión |
|---|---|---|---|
| Sales.Customers | CustomerID | ValidFrom, ValidTo; sin LastEditedWhen en el diccionario | Snapshot del catálogo por ejecución; comparación por clave/hash |
| Warehouse.StockItems | StockItemID | ValidFrom, ValidTo; sin LastEditedWhen en el diccionario | Snapshot del catálogo por ejecución; comparación por clave/hash |
| Sales.Invoices | InvoiceID | LastEditedWhen | Comparación por clave/hash; LastEditedWhen como candidato transaccional y marca observada |
| Sales.InvoiceLines | InvoiceLineID | LastEditedWhen | Comparación por clave/hash y dependencias de cabecera; candidato transaccional separado |

**CONFIRMADO:** campos en los CSV y configuración comunicada: Customers/StockItems son SYSTEM_VERSIONED_TEMPORAL_TABLE, asociados a Sales.Customers_Archive y Warehouse.StockItems_Archive; Invoices/InvoiceLines son NON_TEMPORAL_TABLE.

Rangos de LastEditedWhen comunicados a nivel de fecha: Invoices, 2013-01-02 a 2016-05-31; InvoiceLines, 2013-01-01 a 2016-05-31. No son el rango de InvoiceDate, ni prueba de que todas las modificaciones futuras actualicen el timestamp. Los valores completos para un watermark inicial deben obtenerse del corte autorizado, no de estas fechas resumidas ni del reloj de ejecución actual.

**PENDIENTE:** cobertura/retención de versiones, correspondencia de períodos, semántica y garantías de escritura de LastEditedWhen, límites de transacción y concurrencia. Evidencia: [verificaciones de origen](../../database/README.md#resultados-confirmados-en-sql-server).

No se asigna una estrategia universal basada en ValidFrom ni una marca única a todas las tablas. Se propone un modo de referencia verificable y una optimización condicional.

## I-01 — Modo de referencia recomendado

**PROPUESTA:** leer en cada ejecución la proyección completa de las cuatro entidades en un corte coherente, guardar Raw completo y comparar con el último corte publicado.

- Catálogos pequeños documentados (663 clientes, 227 productos): comparación completa por ID y payload para SCD Tipo 1, altas y bajas.
- Facturas: usar cambios de LastEditedWhen como señal; contrastar todas las claves/hashes del corte para detectar además cambios sin timestamp confiable y bajas.
- Líneas: señal LastEditedWhen y contraste completo por clave/hash; incorporar toda línea afectada por una cabecera cambiada.
- Cargar en el DW únicamente altas, modificaciones y bajas detectadas; los registros sin cambio son no-op.

Este modo realiza **lectura completa y carga incremental del destino**. No reduce la lectura a un delta del origen ni se presenta como captura de eventos. Se propone como estrategia equivalente de detección de cambios para el alcance académico, priorizando corrección sin alterar el OLTP.

El contraste completo es obligatorio en este modo; LastEditedWhen no sustituye la evidencia de diferencias. Los costos de lectura, espacio y duración están pendientes de medición antes de aprobar frecuencia.

Solo se conserva el estado observado en cada corte: una inserción y eliminación entre dos cortes puede ser invisible si no existe en ninguno. No se promete auditoría de todos los eventos intermedios ni reconstrucción temporal completa.

## I-02 — Consistencia del corte de origen

**PROPUESTA:** un source_snapshot_id común a las cuatro extracciones y un manifiesto por entidad con proyección, claves, conteos, hash, inicio/fin y marca de completitud.

Alternativas, pendientes de acceso autorizado:

1. Una transacción de lectura SNAPSHOT para las cuatro entidades si la configuración y los recursos ya están habilitados y verificados.
2. En un entorno académico aislado, una ventana estable con el generador de cambios detenido mientras se capturan las cuatro entidades.
3. Si ninguna alternativa puede acreditarse, bloquear la captura consistente y revisar el mecanismo; cuatro lecturas separadas no se declaran un único snapshot por conveniencia.

No se presupone ALLOW_SNAPSHOT_ISOLATION habilitado ni se propone cambiarlo sin autorización. SNAPSHOT ofrece consistencia transaccional y requiere esa opción de base. [Microsoft Learn](https://learn.microsoft.com/en-us/sql/t-sql/statements/set-transaction-isolation-level-transact-sql?view=sql-server-ver16)

No usar lecturas de datos sin confirmar. El snapshot es la versión realmente visible, no un filtro artificial por InvoiceDate. Antes de inferir bajas deben estar completos el alcance, las claves y las cuatro proyecciones.

## I-03 — Tablas temporales frente a timestamps transaccionales

**CONFIRMADO:** existen las dos asociaciones temporales identificadas por el usuario; no se necesita volver a suponer ni descubrir sus nombres. **PENDIENTE:** cobertura histórica, retención, columnas/intervalos y reglas de uso de versiones para SCD.

En un período temporal del motor, las marcas reflejan el inicio de transacción UTC, no la fecha comercial. Una transacción larga puede confirmar después del corte aunque su inicio sea anterior; filtrar solo por ese campo no garantiza capturarla. La correspondencia de las columnas documentadas con el período se comprobará al preparar la lectura. [Tablas temporales](https://learn.microsoft.com/en-us/sql/relational-databases/tables/temporal-tables?view=sql-server-ver16)

La tabla actual no incluye todas las versiones ni las filas ya eliminadas. Sales.Customers_Archive y Warehouse.StockItems_Archive pueden apoyar auditoría y evaluación SCD, después de perfilar su contenido; no se une su historia completa a las líneas ni se presupone cobertura de 2013–2016 por el solo nombre del archivo.

En facturas/líneas, NON_TEMPORAL_TABLE confirma que no tienen esa historia de sistema. LastEditedWhen sigue siendo una señal candidata, no un LSN, secuencia de commit o garantía de modificación/borrado. [Referencia de metadatos temporales](https://learn.microsoft.com/en-us/sql/relational-databases/system-catalog-views/sys-tables-transact-sql?view=sql-server-ver16)

## I-04 — Checkpoints y watermarks persistentes

**PROPUESTA:** estado por entidad, unido a un lote publicado de las cuatro tablas. Los nombres son metadatos técnicos propuestos:

| Dato persistente | Semántica |
|---|---|
| last_published_execution_id / publication_id | Último lote que completó publicación |
| source_snapshot_id | Corte fuente efectivamente publicado |
| previous_snapshot_id / manifest_hash | Base completa y manifiesto usados para comparar |
| schema_version / rules_version | Reglas y proyección de esa publicación |
| observed_max_LastEditedWhen | Máximo observado de facturas o líneas; puede disminuir si desaparece una fila |
| temporal_watermark | Marca temporal monotónica por entidad solo tras validar su uso; no decide corrección en modo de referencia |
| observed_ValidFrom / ValidTo | Rangos de diagnóstico de catálogos; no un cursor universal |
| watermark_start / watermark_end | Rango procesado si se utiliza el modo temporal optimizado |
| checkpoint_state | Candidato o publicado; nunca confundir un progreso de extracción con éxito analítico |

En modo de referencia, la marca decisiva es la generación/manifiesto publicado. Las marcas temporales se registran separadas y no excluyen registros del contraste.

Un watermark temporal validado puede tomar max(marca publicada anterior, máximo válido del corte). Una entidad vacía no permite reiniciarlo arbitrariamente a cero. Valores futuros, retrocesos o semántica no comprobada bloquean el uso optimizado; el modo de referencia sigue comparando estados completos.

No usar MAX(InvoiceID), MAX(InvoiceLineID) o InvoiceDate como única captura: no detectan modificaciones ni bajas. Conservar precisión datetime2(7); no reducir silenciosamente el séptimo decimal al serializar o guardar un cursor.

En la carga inicial calcular por separado las marcas precisas de cada tabla transaccional dentro del corte. No inicializarlas con 2016-05-31 a medianoche, con fin de día supuesto ni con la fecha actual: el usuario informó fechas resumidas, no el cursor exacto. Las marcas candidatas se vuelven publicadas junto con el lote exitoso.

## I-05 — Carga inicial

1. Verificar acceso, corte, proyección, reglas y destino autorizados; asignar execution_id, attempt_id y snapshot.
2. Extraer proyecciones completas; cerrar manifiestos solo tras comprobar conteos, claves y lectura completa.
3. Validar Clean, registrar cuarentena y bloquear ante reglas críticas.
4. Preparar DimFecha y mapas persistentes de claves naturales/sustitutas; aplicar miembros técnicos desconocidos según política aprobada.
5. Integrar Ready por línea con la única cabecera del mismo corte; resolver ambos roles de cliente y producto.
6. Calcular medidas y reconciliar claves, cardinalidad, faltantes y valores.
7. Publicar dimensiones, hechos, auditoría de éxito y vector de checkpoints en una transacción del destino.
8. Marcar PUBLISHED después de commit; no cambiar las marcas en errores o en una carga parcial.

El descubrimiento histórico orienta la revisión, pero sus conteos no son límites fijos de una fuente que pueda cambiar.

## I-06 — Deltas y dependencia de cabeceras

**PROPUESTA:** por entidad, comparar clave natural y payload canónico contra la generación publicada:

- I: clave nueva.
- U: misma clave, diferente payload.
- D: clave de la base anterior ausente en el snapshot completo actual.
- Sin cambio: misma clave y contenido; no-op.

El conjunto de InvoiceLineID afectados es la unión distinta de:

1. Altas, cambios y bajas de líneas.
2. Líneas actuales o previamente publicadas que pertenezcan a facturas modificadas.
3. Hechos con referencias técnicas desconocidas que ahora puedan resolverse.

Si cambia CustomerID, BillToCustomerID, InvoiceDate o IsCreditNote de la factura, recalcular sus líneas aunque LastEditedWhen de las líneas no cambie. Si una línea cambia de InvoiceID, actualizar la misma clave natural del hecho y sus dependencias.

Un cambio solo de nombre/Brand del catálogo actualiza SCD Tipo 1 conservando la clave sustituta. Un cambio de Customers.BillToCustomerID modifica la relación actual del catálogo, no el responsable de facturas que no cambiaron.

## I-07 — Eliminaciones y reapariciones

- Factura/línea ausente en corte completo: aplicar marca técnica EsEliminadoFuente según el modelo; los KPIs normales excluyen hechos eliminados.
- Catálogo ausente: conservar su clave sustituta y atributos para hechos existentes; registrar baja técnica.
- Cabecera ausente con líneas presentes: inconsistencia crítica, no una baja legítima que pueda ocultarse eliminando líneas del DW.
- Snapshot incompleto o cambio de filtros/proyección no homologado: no detectar bajas.
- Reaparición del mismo ID: verificar identidad y reactivar conforme al contrato aprobado; no asignar por defecto otra clave sustituta.
- No generar reversos monetarios, notas de crédito ficticias ni destrucción física de auditoría.

Cambios retroactivos alteran el estado analítico vigente y se registran. Reproducir un informe antiguo “como se conocía entonces” necesitaría versionar publicaciones; la propuesta SCD Tipo 1 no ofrece esa capacidad.

## I-08 — Modo temporal optimizado, pendiente

**PENDIENTE de aprobar y verificar:** reducir las lecturas transaccionales utilizando LastEditedWhen. Los catálogos conservarían su snapshot por ejecución.

Contrato candidato por tabla transaccional:

- Leer en el corte consistente la ventana inclusiva [W anterior − Δ, U].
- Orden/paginación estable por LastEditedWhen y clave natural; nunca saltar empates de timestamp.
- Conservar un manifiesto completo de claves para detectar bajas.
- Reconciliar payload completo con periodicidad aprobada y recuperar cualquier diferencia omitida por timestamps.
- Determinar Δ, U, zona horaria, precisión y latencia máxima tras estudiar escritura y transacciones.

Una ventana finita de solapamiento no garantiza capturar cambios retrodatados o transacciones de duración arbitraria. Un manifiesto solo de claves detecta bajas, pero no una modificación con la misma clave y timestamp antiguo. Si se reduce el contraste completo, debe declararse el retraso máximo tolerado hasta la reconciliación; no conserva automáticamente la garantía por corte del modo de referencia.

Si se exigiera captura exacta de eventos y no solo estados, evaluar un mecanismo adicional compatible con Express manteniendo intacto el original. Cualquier experimento que requiera habilitar captura o modificar configuración se limitaría al entorno independiente expresamente autorizado. Esta evaluación no habilita CDC ni modifica configuraciones.

## I-09 — Idempotencia y recuperación

Identidad de publicación: execution_id único. Identidad del hecho: InvoiceLineID único, incluso tras actualización. Identidad de dimensión: clave natural y clave sustituta persistente.

- Antes del commit final: un error revierte cambios analíticos y checkpoint; reintentar reutilizando Raw completo y el mismo execution_id, con attempt_id nuevo.
- Raw incompleto: no usarlo como snapshot completo. Registrar intento fallido y recapturar un nuevo corte con trazabilidad.
- Después de commit con confirmación perdida: comprobar execution_id publicado. Si existe, no repetir mutaciones.
- Concurrencia: una publicación activa por proceso/destino y comparación de publication_id base; si cambió, recalcular contra la base correcta.
- Repetir un delta sin cambio real no aumenta hechos, claves, importes ni notificaciones.

Las fallas de envío de alertas posteriores no deben revertir un DW ya publicado; reintentar el evento con identidad de publicación. El código de automatización permanece fuera de este lote.

## I-10 — Prueba reproducible aislada, propuesta

No hay fixture, base ni script creados. Los valores siguientes son **datos sintéticos especificados para una prueba futura**, no registros observados de WideWorldImporters. Todas las mutaciones y reinicios se harán en un entorno independiente y expresamente autorizado, nunca en la instancia/base original ni sus archivos temporales.

Antes de una prueba deberá verificarse la identidad de instancia/base de laboratorio y una lista permitida de destinos. La prueba se detiene si el destino de escritura coincide con el origen original. Los permisos de lectura y de simulación se separarán; aquí solo se documenta el requisito, sin configurar cuentas ni mecanismos.

El fixture es la proyección contractual de cuatro entidades, no un INSERT válido en las tablas completas: existen otros campos obligatorios fuera de esta proyección. Para probar contra un clon real habrá que preparar datos respetando todas sus restricciones y permisos, con nueva autorización.

### Estado base F0

- Customers: 910001 “Cliente A”, BillToCustomerID 910002; 910002 “Pagador B”, BillToCustomerID 910002.
- StockItems: 920001 “Producto A”, Brand “Marca A”; 920002 “Producto B”, Brand NULL.
- Invoice: InvoiceID 930001, CustomerID 910001, BillToCustomerID 910002, InvoiceDate 2016-05-01, IsCreditNote 0.
- Fecha técnica sintética T0: 2026-11-14T12:00:00.0000000. LastEditedWhen = T0 en factura y líneas. Para el fixture se conviene tiempo UTC; no acredita esa semántica en el origen real.
- Para repetir los casos se fija T1 = 2026-11-14T12:05:00.0000000; corte C0 = 12:01 y corte C1 = 12:06 del mismo día. La fecha antigua del caso INC-09 será 2026-11-13T10:00:00.0000000, ajustándola determinísticamente a W − Δ − 1 día si se evalúa un Δ mayor.
- Customers y StockItems: ValidFrom = T0 y ValidTo = 9999-12-31T23:59:59.9999999 como valores sintéticos; no prueban que el fixture sea system-versioned.

| InvoiceLineID | InvoiceID | StockItemID | Quantity | UnitPrice | TaxAmount | ExtendedPrice | LineProfit |
|---|---|---|---:|---:|---:|---:|---:|
| 940001 | 930001 | 920001 | 2 | 10.00 | 3.00 | 23.00 | 6.00 |
| 940002 | 930001 | 920002 | 1 | 5.00 | 0.75 | 5.75 | 1.00 |

Resultado matemático esperado de F0: base 25.00, extendido 28.75, unidades 3, facturas distintas 1, utilidad 7.00, margen 28 %. No es un resultado de ejecución.

### Casos independientes

Restablecer F0 y una publicación base antes de cada caso; conservar evidencia y claves sustitutas asignadas en esa base.

| ID local | Mutación / situación futura | Resultado esperado |
|---|---|---|
| INC-01 | Carga inicial F0 | Dos hechos activos, dos roles y KPIs F0 |
| INC-02 | Repetir F0 y el mismo lote; luego otro corte sin cambios | Sin duplicados ni cambios en claves/importes |
| INC-03 | Añadir línea 940003 a 930001, producto 920001, Quantity 1, UnitPrice 4.00, impuesto 0.60, extendido 4.60, utilidad 1.00, LastEditedWhen T1 > T0 | Tres hechos; base 29.00, extendido 33.35, unidades 4, una factura, utilidad 8.00 |
| INC-04 | En 940001 cambiar Quantity a 3, impuesto a 4.50, extendido a 34.50, utilidad a 9.00 y LastEditedWhen a T1 | Dos hechos; base 35.00, extendido 40.25, unidades 4 y utilidad 10.00 |
| INC-05 | Cambiar InvoiceDate solo en cabecera y su LastEditedWhen | Ambas líneas se mueven de fecha; mismos totales y grano |
| INC-06 | Cambiar CustomerID o BillToCustomerID solo en cabecera a otro cliente válido del fixture | Reasignar el rol afectado en ambas líneas; mismos importes |
| INC-07 | Cambiar solo Customers.BillToCustomerID | Actualizar catálogo; no cambiar el responsable de la factura |
| INC-08 | Cambiar nombre/Brand de catálogo | Misma clave sustituta, una fila Tipo 1; importes invariantes |
| INC-09 | Cambiar una línea con LastEditedWhen antiguo, anterior a cualquier solapamiento elegido | Modo de referencia detecta por hash; modo optimizado no se declara seguro por timestamp |
| INC-10 | Varias claves comparten LastEditedWhen, incluido empate con W | No omitir cambios; comparar/paginar con clave y ventana inclusiva |
| INC-11 | Transacción que confirma después del corte con fecha de inicio anterior | Capturar en corte posterior del modo de referencia; comprobar limitación de Δ |
| INC-12 | Eliminar 940001 en el fixture | Una activa; base 5.00, extendido 5.75, unidades 1, una factura, utilidad 1.00; tombstone conservado |
| INC-13 | Eliminar factura y ambas líneas en un corte completo | Cero activas; sumas/conteo cero con SIN_DATOS; margen NULL |
| INC-14 | Faltar cabecera con líneas presentes o un snapshot truncado | Bloquear publicación; conservar DW/checkpoints anteriores |
| INC-15 | Fallar antes del commit y reintentar | Rollback completo; un único resultado tras éxito |
| INC-16 | Perder confirmación después del commit y reintentar | Detectar execution_id ya publicado; no-op |
| INC-17 | UnitPrice NULL en 940001 | Base y margen NULL/INCOMPLETO; extendido 28.75, unidades 3, factura 1 y utilidad 7.00 |
| INC-18 | ID de catálogo sin resolver y llegada posterior de ese catálogo | Clave 0 e incidencia según política; luego resolver sin duplicar hechos |
| INC-19 | Filtro por producto 920001 o 920002 | Una factura en cada grupo; total global sigue siendo una, no dos |
| INC-20 | Reaparición de clave eliminada o cambio de schema_version | Comprobar identidad o rebaselinar; no reutilizar sin control un cursor incompatible |
| INC-21 | Quantity cero o negativa sin regla comercial aprobada | Raw conservado, cuarentena y publicación bloqueada según DQ-15 propuesta; sin corrección automática |
| INC-22 | En un clon temporal autorizado, cambiar cliente/producto y observar su archivo asociado | Mismo hecho y clave Tipo 1 en el diseño inicial; versiones usadas para auditoría, sin equiparar vigencia de sistema con InvoiceDate ni multiplicar importes |

Registrar F0, mutación, cortes, LastEditedWhen/ValidFrom relevantes, execution_id/attempt_id, manifests, I/U/D, claves, checkpoints antes/después, destino de laboratorio y KPIs esperados/obtenidos. Los 22 casos están **PENDIENTES de ejecución**. INC-22 exige un clon con comportamiento temporal real; las fechas sintéticas de F0 por sí solas no lo demuestran.

Referencias: [staging](staging-design.md), [modelo](dimensional-model.md), [métricas](../quality/metrics-contract.md), [decisiones y permisos](architecture.md#decisiones-y-pendientes-para-implementar).
