# Matriz de trazabilidad académica

Fuente: guía versión 1.0, noviembre de 2026, páginas físicas indicadas; RF-01 a RF-13 y RNF-01 a RNF-09 del informe. Las fuentes académicas se identifican en el [README](../../README.md).

RF-01 a RF-13 y RNF-01 a RNF-09 son los códigos del informe. G-01 a G-18 son identificadores locales para detallar obligaciones de la guía; no son numeración oficial del PDF.

## Criterio de estado

Los estados de la tabla se refieren a cumplimiento funcional, no a la existencia de un documento. Todos continúan PENDIENTES hasta presentar resultados verificables. Los componentes son responsabilidades lógicas; sus carpetas se crearán al implementar código.

## Requisitos funcionales del informe

| ID | Guía (p.) | Requisito | Componentes | Evidencia necesaria / criterio | Estado funcional |
| --- | --- | --- | --- | --- | --- |
| RF-01 | 4–6 | Extraer ventas, clientes y productos | ETL; datos | Conteos y linaje de las cuatro tablas; carga trazable | PENDIENTE |
| RF-02 | 6, 12 | Carga inicial e incremental | ETL; control operativo | Cambios nuevos/modificados, política de eliminaciones y tardíos, checkpoint e idempotencia | PENDIENTE |
| RF-03 | 3, 5 | Staging separado | staging; ETL | Raw, Clean y Ready; originales conservados y reglas identificables | PENDIENTE |
| RF-04 | 5–6, 12 | Limpieza, estandarización y validación | ETL; pruebas | Reglas, motivos de rechazo y resultados antes/después; nulos y duplicados | PENDIENTE |
| RF-05 | 6, 11 | Auditoría de ETL | ETL; control operativo | Identificador, inicio/fin, conteos, errores, estado y rangos incrementales | PENDIENTE |
| RF-06 | 6–7 | Construir y alimentar el DW | DW; ETL | Modelo estrella, grano, claves, SCD, integridad y conciliación | PENDIENTE |
| RF-07 | 7–8 | API analítica | API | Contratos, consultas autorizadas y resultados contrastables del DW | PENDIENTE |
| RF-08 | 7–8 | Dashboard de ventas | frontend; API | KPIs, filtros, históricos, detalle y concordancia con el DW | PENDIENTE |
| RF-09 | 8–9 | Predicción con Python | ML; API | Baseline, entrenamiento/validación/prueba, métricas, modelo y predicciones versionados | PENDIENTE |
| RF-10 | 9–10 | Asistente generativo verificable | GenAI; API; frontend | Respuestas con métricas, períodos, corte y pruebas de información insuficiente | PENDIENTE |
| RF-11 | 10 | Notificaciones por condiciones | notificaciones; frontend | Reglas aprobadas, eventos, entrega y pruebas de disparo/no disparo | PENDIENTE |
| RF-12 | 10–11 | Estado operativo visible | control operativo; API; frontend | Última carga, conteos/rechazos, errores, modelos y estados de alertas | PENDIENTE |
| RF-13 | 11–14 | Comprobación integral reproducible | pruebas; operación; documentación | Pruebas de extremo a extremo, manuales, evidencia y demo | PENDIENTE |

## Requisitos no funcionales del informe

| ID | Guía (p.) | Requisito | Componentes | Evidencia necesaria / criterio | Estado de cumplimiento |
| --- | --- | --- | --- | --- | --- |
| RNF-01 | 4, 9–11 | Seguridad | API; GenAI; todos | Secretos externos, mínimo privilegio, acceso, consultas parametrizadas y privacidad | PENDIENTE |
| RNF-02 | 5–7, 12 | Integridad | ETL; datos; pruebas | Claves y relaciones comprobadas; transacciones y reconciliación | PENDIENTE |
| RNF-03 | 8, 11 | Rendimiento | API; datos; pruebas | Tiempos medidos con contexto y volumen; objetivo acordado antes de evaluar | PENDIENTE |
| RNF-04 | 6, 10–12 | Confiabilidad | ETL; notificaciones; pruebas | Reintentos, deduplicación e idempotencia bajo fallas controladas | PENDIENTE |
| RNF-05 | 3, 5–6, 11 | Trazabilidad | ETL; control operativo; todos | Origen, ejecución, transformaciones, destino y versiones | PENDIENTE |
| RNF-06 | 7–8 | Usabilidad | frontend; pruebas | Filtros, unidades, navegación y estados claros; escritorio y móvil preferible | PENDIENTE |
| RNF-07 | 3, 12 | Mantenibilidad | todos; documentación | Módulos documentados y código reutilizable; evidencia parcial de estructura ya disponible | PENDIENTE |
| RNF-08 | 3–4, 11–12 | Reproducibilidad | operación; documentación; pruebas | Versiones, configuración de ejemplo y pasos de instalación/operación/recuperación | PENDIENTE |
| RNF-09 | 5, 8–10 | Calidad analítica | datos; ML; GenAI | KPIs verificables, evaluación independiente y explicaciones respaldadas | PENDIENTE |

## Obligaciones de la guía que deben comprobarse expresamente

| ID local | Guía (p.) | Obligación | Vínculo informe | Componentes | Evidencia necesaria / criterio | Estado |
| --- | --- | --- | --- | --- | --- | --- |
| G-01 | 4–5 | Inventario y perfilado completo | RF-01, RF-04, RNF-09 | datos; calidad | ERD/diccionario; nulos, cardinalidad, rangos, formatos y frecuentes; consultas y fecha | PENDIENTE |
| G-02 | 5 | Raw, Clean y Ready con linaje | RF-03, RNF-05 | staging; ETL | Separación de zonas, recepción preservada, integración y cuarentena | PENDIENTE |
| G-03 | 6 | Contrato incremental y watermark | RF-02, RNF-04 | ETL; control operativo | Checkpoint solo tras éxito; actualizaciones, eliminaciones, tardíos, solapamiento e idempotencia | PENDIENTE |
| G-04 | 5–6 | Catálogo de reglas de transformación | RF-04, RNF-02 | ETL; calidad | Nulos, supervivencia de duplicados, limpieza, tipos/precisión, estándares, outliers, reglas cruzadas, enriquecimiento, historia, errores y linaje | PENDIENTE |
| G-05 | 6–7 | Diseño dimensional demostrable | RF-06, RNF-02 | DW; arquitectura | Grano, fecha, claves naturales/sustitutas, desconocidos, SCD y clasificación de medidas; ratios recalculados | PENDIENTE |
| G-06 | 7–8 | Contrato y validación de cada KPI | RF-07, RF-08, RNF-09 | API; frontend; datos | Nombre, propósito, fórmula, unidad, grano, filtros, fuente y responsable; consulta independiente y tiempo de respuesta | PENDIENTE |
| G-07 | 7, 10 | Acceso apropiado para la demo | RNF-01 | API; frontend | Pruebas de acceso permitido/denegado y ausencia de secretos en interfaz | PENDIENTE |
| G-08 | 7–8 | Funciones mínimas de BI | RF-08, RNF-06 | frontend; API | 4–6 KPIs, fecha y dos dimensiones, tendencias/comparación, detalle paginado, drill-down, última actualización y estados | PENDIENTE |
| G-09 | 8–9 | Evaluación ML sin leakage | RF-09, RNF-09 | ML; pruebas | Problema/usuario/decisión, datos DW, sesgos, variables reproducibles, train/validación/test temporal y baseline; imputación ajustada con entrenamiento | PENDIENTE |
| G-10 | 8–9 | Versionado y uso del modelo | RF-09, RF-12 | ML; API; frontend | Modelo, parámetros, fecha, versión, métricas; predicciones con fecha/horizonte, exposición API o batch, visualización, límites y reentrenamiento | PENDIENTE |
| G-11 | 9–10 | Diez pruebas de GenAI | RF-10, RNF-09 | GenAI; pruebas | Diez preguntas con esperado, respaldo, obtenido y afirmaciones no verificadas; dos casos de información insuficiente | PENDIENTE |
| G-12 | 9–11 | Controles de GenAI | RF-10, RNF-01 | GenAI; API | Identidad, permisos, consultas limitadas, entradas/tamaño/frecuencia, privacidad, corte y distinción histórico/predicción/interpretación | PENDIENTE |
| G-13 | 10 | Una notificación completa | RF-11, RNF-04 | notificaciones; pruebas | Disparo/no disparo, duplicados, reintentos, fallas, generación y entrega; evento con fecha, tipo, severidad, entidad, valor, referencia, origen y estado | PENDIENTE |
| G-14 | 4, 10 | Frecuencia y simulación explícitas | RF-11, RF-12, RNF-05 | ETL; notificaciones; frontend | Latencia/frecuencia medible; eventos simulados identificados; referencia comercial histórica | PENDIENTE |
| G-15 | 11 | Monitoreo y dimensiones de calidad | RF-12, RNF-05 | control operativo; API; frontend | Ejecuciones, última carga, conteos/rechazos, duración, fallas, modelo y alertas; completitud, unicidad, validez, consistencia, integridad y oportunidad | PENDIENTE |
| G-16 | 11–12 | Documentación y recuperación | RF-13, RNF-08 | documentación; operación | RF/RNF, arquitectura, ERD, diccionarios origen/DW, dimensional, ETL/orquestación, catálogo de reglas, API/contratos, ML, GenAI, alertas, pruebas y manuales; reinicio/reintento/restauración/reconstrucción | PENDIENTE |
| G-17 | 12–14 | Código, pruebas y evidencias reproducibles | RF-13, RNF-08 | todos; pruebas; documentación | Código y configuración versionados, instrucciones de datos de prueba y .env.example; carga inicial/incremental, idempotencia, calidad, integridad, recuperación, reconciliación, BI, ML, IA, alertas y E2E con logs/resultados | PENDIENTE |
| G-18 | 13–14 | Video y demo funcional | RF-13 | todos; documentación | Video de cinco minutos; lote, DW/KPI, predicción versionada, pregunta IA y alerta visibles; explicar recuperación y señalar simulaciones | PENDIENTE |

El catálogo de reglas debe definir causa, acción, justificación y efectos; los ejemplos de la guía no se convierten automáticamente en reglas comerciales. La dimensión semiaditiva se explicará según su aplicabilidad al proceso de ventas, sin introducir inventario al alcance.

## Plan de pruebas y evidencias

Los casos concretos de carga, cambios, cabeceras, bajas, nulos e idempotencia están en [incrementales](../architecture/incremental-loads.md) (22 casos) y [métricas](../quality/metrics-contract.md) (14 casos y nueve consultas propuestas). Ninguno se ha ejecutado.

Cada evidencia futura identificará versión, ejecución, fecha, datos de prueba, resultado esperado/obtenido y diferencias justificadas. Deben demostrarse carga inicial e incremental, idempotencia, nulos/duplicados, integridad, recuperación, reconciliación, dashboard, ML, GenAI, alertas y extremo a extremo.

La entrega incluye documentación de APIs/contratos, modelo/experimentos ML, controles y pruebas GenAI, alertas, manuales de instalación/despliegue/operación/usuario, scripts ordenados y README reproducible con .env.example sin secretos. Se completarán junto con sus componentes y resultados reales; los contratos de diseño no los sustituyen.

La guía solicita video de cinco minutos y demo funcional. La rúbrica y el plan de ocho fases son sugeridos y ajustables por el docente. Los ejemplos de umbrales y las herramientas alternativas no son reglas comerciales ni dependencias obligatorias.

Documentos de referencia: [arquitectura](../architecture/architecture.md), [modelo dimensional](../architecture/dimensional-model.md), [staging y catálogo de reglas](../architecture/staging-design.md) y [evidencias de origen](../../database/README.md).
