# Plataforma Integral de Ingeniería de Datos y Analítica Inteligente

Proyecto académico de ventas sobre WideWorldImporters. Stack aprobado: SQL Server 2022 Express, Python/SQL, FastAPI, React/TypeScript/Vite/ECharts, Scikit-learn, IA generativa y notificaciones.

El usuario confirmó el funcionamiento real de ETL/DW, API, dashboard y ML: publicación **PUBLISHED**, 228,265 líneas, 70,510 facturas, 663 clientes y 227 productos, con reconciliación sin diferencias. El entrenamiento real utilizó 41 meses y seleccionó `seasonal_naive` según el MAE de validación, con seis meses de prueba y pronósticos de junio a agosto de 2016.

Las alertas cuentan con validación manual confirmada: script SQL 005 ejecutado, regla académica `DEMO_SALES_MOM` activada, notificación entregada y marcada como leída, y consulta de duplicados con cero filas. Los reintentos reales y la programación automática siguen pendientes de validación. El asistente dispone de adaptadores OpenAI y Ollama; la comprobación real de integración con Ollama y las diez preguntas de aceptación siguen pendientes.

## API y dashboard de ventas

Requisitos: Python 3.13, ODBC Driver 17, Node.js compatible con `^20.19.0 || >=22.12.0` y npm, según `frontend/package.json`. Dependencias en `backend/requirements.txt` y `frontend/package.json`. Estos comandos son para ejecución manual; esta actualización documental no instala paquetes ni accede a SQL Server.

Desde la raíz, instalar las dependencias e iniciar la API local:

```powershell
.\.venv\Scripts\python.exe -m pip install -r .\backend\requirements.txt

$env:API_ENVIRONMENT = 'local'
$env:API_SQL_SERVER = 'HEBER-ENOC-PINE\SQLEXPRESS'
$env:API_SQL_DATABASE = 'WideWorldImporters_Analytics'
$env:API_ODBC_DRIVER = 'ODBC Driver 17 for SQL Server'
$env:API_TRUST_SERVER_CERTIFICATE = 'true'
$env:API_ACCESS_TOKEN = (& .\.venv\Scripts\python.exe -c "import secrets; print(secrets.token_urlsafe(32))")
Write-Host "Clave para ingresar al panel: $env:API_ACCESS_TOKEN"

.\.venv\Scripts\python.exe -B -m uvicorn backend.app:create_app --factory --host 127.0.0.1 --port 8000
```

En otra terminal, con la carpeta de trabajo en `frontend` (desde la raíz, entrar con `Set-Location .\frontend`):

```powershell
npm install
npm run dev
```

Desde esa misma carpeta también se ejecutan `npm test` y `npm run build`. Si la terminal permanece en la raíz del repositorio, los comandos equivalentes son `npm --prefix .\frontend install` y `npm --prefix .\frontend run dev`; no usar ese prefijo cuando ya se está dentro de `frontend`.

Abrir **http://127.0.0.1:5173** e ingresar la clave mostrada en la primera terminal. Se mantiene solo en memoria; recargar la página exige ingresarla nuevamente. No escribirla en variables `VITE_*`, URLs ni almacenamiento del navegador. Vite reenvía `/api` a la API local. La excepción TLS conserva `Encrypt=yes` y solo admite explícitamente la instancia indicada y `API_ENVIRONMENT=local`. Las variables `ETL_*` no sustituyen las de la API; `.env.example` no se carga automáticamente.

La API usa autenticación integrada de Windows. Para las consultas analíticas, su identidad de ejecución debe tener `SELECT` en `dw.FactVentas`, `dw.DimCliente`, `dw.DimProducto`, `audit.etl_checkpoint` y `audit.etl_publication`, sin permisos de escritura sobre el DW. El buzón añade los permisos restringidos descritos en «Alertas y notificaciones». El adaptador analítico solicita acceso ODBC de solo lectura; los cambios de estado del buzón utilizan procedimientos específicos. La separación real de permisos corresponde a SQL Server y debe verificarse con la identidad elegida. Ambos servidores escuchan únicamente en loopback: este arranque corresponde al laboratorio local.

Rutas protegidas: `GET /api/v1/catalogs`, `/api/v1/overview` y `/api/v1/sales`; documentación local en http://127.0.0.1:8000/docs. `GET /health` verifica el proceso sin abrir conexiones. Filtros: `date_from` inclusiva, `date_to` exclusiva, `customer_id` comprador, `bill_to_customer_id` facturado, `stock_item_id`, `is_credit_note`. El panel presenta «Hasta» inclusiva y convierte el límite. Detalle: `page`, `page_size` (1–100), `sort=date_desc|amount_desc`. `publication_id` exige el corte cargado; un HTTP 409 solicita actualizar el panel.

Los seis KPIs respetan el contrato: base, extendido, unidades, facturas distintas, utilidad y `100 × utilidad / base`. Los importes viajan como cadenas decimales exactas; el margen es NULL cuando no es calculable. Se preservan signos y créditos sin reinterpretarlos; no se asume moneda. Rankings y tendencias usan importe extendido. La comparación anual ofrece meses comunes y señala límites dentro de un mes. No se rellena con cero un mes sin cobertura del origen; las dimensiones Tipo 1 muestran sus nombres actuales.

Cada respuesta comprueba el checkpoint antes y después de consultar con `READ COMMITTED`, y reintenta una vez si cambió la publicación. No filtra las ventas por `publication_id` de cada fila. Hay límites de concurrencia, tiempos y paginación. Las solicitudes HTTP no ejecutan ETL, entrenamientos ni automatizaciones.

Pruebas críticas aisladas y compilación, sin SQL:

```powershell
.\.venv\Scripts\python.exe -B -m unittest discover -s tests -p test_api.py -v
npm --prefix .\frontend test
npm --prefix .\frontend run build
```

Las dos pruebas HTTP de `test_api.py` usan un servicio simulado y requieren adicionalmente `httpx` (`.\.venv\Scripts\python.exe -m pip install httpx`); si falta, se informan como omitidas. Las pruebas HTTP nuevas de ML usan ASGI directamente y no necesitan httpx. Las pruebas aisladas no sustituyen la comprobación con datos reales.

## Machine Learning: ventas mensuales

**CONFIRMADO:** entrenamiento real completado con 41 meses históricos, de enero de 2013 a mayo de 2016. El método seleccionado fue `seasonal_naive` (referencia del mismo mes del año anterior), según el MAE de validación. Se reservaron seis meses para prueba y se generaron pronósticos para junio, julio y agosto de 2016. Los valores concretos de MAE/RMSE y la versión se consultan en los artefactos o endpoints del modelo; no se añaden cifras no proporcionadas por la ejecución real.

Objetivo: apoyar el análisis comercial con estimaciones globales del **importe extendido mensual**, `SUM(dw.FactVentas.ExtendedPrice)`, excluyendo `EsEliminadoFuente = 1`. Incluye los impuestos y signos registrados; no representa ventas netas ni se asume moneda. Los filtros del dashboard no modifican el modelo. El cierre debe ser un mes histórico completo elegido explícitamente; meses ausentes, nulos o series de menos de 36 meses bloquean el entrenamiento, sin imputación de ceros.

Para los 41 meses confirmados: entrenamiento enero 2013–mayo 2015 (29), validación junio–noviembre 2015 (6), prueba diciembre 2015–mayo 2016 (6). Se evalúan dos bloques de tres meses por segmento con ventana creciente: cada ajuste usa solo el prefijo conocido antes del bloque, incorporando observaciones anteriores al avanzar. El escalador se ajusta dentro del pipeline de cada origen. La prueba no elige hiperparámetros ni método.

Se compara [Ridge](https://scikit-learn.org/1.8/modules/generated/sklearn.linear_model.Ridge.html), con índice mensual y seno/coseno anual, frente al mismo mes del año anterior. Se selecciona α entre 0.1, 1 y 10 por MAE de validación; si Ridge no supera la referencia, se sirve esta última, identificada expresamente. Se conservan MAE y RMSE de ambos métodos para validación y prueba, calculados antes del redondeo de presentación. El ajuste final usa todo el historial. El uso del pipeline respeta la [separación de ajuste y evaluación](https://scikit-learn.org/1.8/common_pitfalls.html#data-leakage).

Comandos para reproducir el entrenamiento desde la raíz. El entrenamiento real ya fue completado por el usuario; estos comandos no fueron ejecutados contra SQL Server por el agente:

```powershell
.\.venv\Scripts\python.exe -m pip install --only-binary=:all: -r .\machine_learning\requirements.txt

$env:ML_ENVIRONMENT = 'local'
$env:ML_SQL_SERVER = 'HEBER-ENOC-PINE\SQLEXPRESS'
$env:ML_SQL_DATABASE = 'WideWorldImporters_Analytics'
$env:ML_ODBC_DRIVER = 'ODBC Driver 17 for SQL Server'
$env:ML_TRUST_SERVER_CERTIFICATE = 'true'
$env:ML_QUERY_TIMEOUT = '30'

# Plan sin conexiones; el entrenamiento no necesita la clave HTTP de la API.
.\.venv\Scripts\python.exe -B -m machine_learning plan --through-month 2016-05

# Entrenamiento real y publicación de artefactos; SELECT únicamente.
.\.venv\Scripts\python.exe -B -m machine_learning train --through-month 2016-05 --allow-database-access
```

La configuración `ML_*` es independiente de `API_*` y aplica la misma restricción TLS local, siempre con cifrado. `--through-month 2016-05` produce estimaciones para **junio, julio y agosto de 2016**, no para los próximos meses del calendario actual. No se garantiza que Ridge supere la referencia ni se publican intervalos de confianza. Reentrenar manualmente después de un nuevo cierre o una nueva publicación; las solicitudes HTTP nunca entrenan.

El entrenador guarda `model.joblib`, `snapshot.json` y `report.json` en `artifacts/ml-sales/versions/<UUID>/`, y actualiza `current.json` de forma atómica. Se conservan corte del DW, hash de datos/código/modelo, variables, parámetro, particiones, métricas, predicciones de evaluación, fecha UTC y versiones de Python y librerías. El historial conserva importes decimales exactos; el ajuste ML usa punto flotante y las estimaciones se presentan con dos decimales. Un fallo conserva la versión activa anterior. El directorio está excluido de Git.

Reiniciar FastAPI una vez para cargar las nuevas rutas y abrir «Pronóstico mensual» en el dashboard. Después de entrenar, pulsar **Actualizar pronóstico**; entrenamientos posteriores no requieren reiniciar la API. Rutas protegidas: `GET /api/v1/ml/predictions` y `/api/v1/ml/metrics`, con `publication_id` opcional para exigir el corte del panel. El backend lee únicamente JSON, sin deserializar el modelo ni necesitar scikit-learn para servirlo. Si aún no se entrenó devuelve `NOT_TRAINED`; un artefacto inválido devuelve un error seguro; si el DW tiene otro corte devuelve `STALE` y se señala que la estimación corresponde al anterior. Los indicadores de ventas siguen funcionando de forma independiente.

`ML_ARTIFACT_DIR` permite cambiar el directorio, relativo a la raíz del repositorio; entrenador y API deben usar el mismo. Para reproducir: mismo snapshot/cierre, código y versiones de librerías registradas en el reporte. Pruebas críticas aisladas:

```powershell
.\.venv\Scripts\python.exe -B -m unittest discover -s tests -p test_machine_learning.py -v
npm --prefix .\frontend test
npm --prefix .\frontend run build
```

La prueba Ridge/joblib usa datos sintéticos y se omite si falta scikit-learn. Las demás verifican protocolo temporal, selección, errores, persistencia, acceso y estados sin SQL. Para el informe se utilizan las métricas y estimaciones de la ejecución real de ML confirmada por el usuario, no los resultados de las pruebas sintéticas.

## Asistente de IA generativa

**CONFIRMADO:** la aceptación real con Ollama 0.40.16 y `qwen2.5:3b-instruct`, registrada en `artifacts/genai-acceptance/20261115T215218955555Z.json`, obtuvo **6/10 aprobadas y 4/10 no aprobadas**. IA-02 respondió correctamente el importe extendido de mayo de 2016: **5,704,232.71 u.m.**, exclusivamente del 1 al 31 de mayo. Esa evidencia confirma ese caso mensual; no acredita una aceptación de 10/10.

**IMPLEMENTADO EN CÓDIGO:** selección de proveedor `ollama` u `openai`, preservando las dos fases y las consultas autorizadas. **PENDIENTE:** resolver y validar los cuatro casos no aprobados. Las pruebas aisladas usan transportes y datos simulados; no se presentan como respuestas reales. No hay dependencias nuevas: ambos adaptadores utilizan la biblioteca estándar de Python.

El adaptador Ollama utiliza [`POST /api/chat`](https://docs.ollama.com/api/chat), `stream: false` y el esquema JSON del contrato en [`format`](https://docs.ollama.com/capabilities/structured-outputs). La respuesta se valida también en Python: tipos, campos obligatorios, enumeraciones, cantidades y ausencia de propiedades adicionales. Se rechazan JSON duplicados o inválidos, generación truncada, otro modelo y llamadas a herramientas; la segunda fase solo admite IDs de evidencias existentes.

Comprobación manual del servicio local, sin SQL:

```powershell
Invoke-RestMethod -Uri 'http://127.0.0.1:11434/api/version'
ollama list
```

Si el servicio está detenido, iniciarlo con `ollama serve` en su propia terminal; no iniciar una segunda instancia cuando ya escucha en 11434. Para la integración, detener FastAPI con Ctrl+C en su terminal habitual, conservar sus variables `API_*` y ejecutar desde la raíz:

```powershell
$env:GENAI_PROVIDER = 'ollama'
$env:GENAI_MODEL = 'qwen2.5:3b-instruct'
$env:OLLAMA_BASE_URL = 'http://127.0.0.1:11434'
$env:GENAI_TIMEOUT = '60'
$env:GENAI_MAX_OUTPUT_TOKENS = '900'

.\.venv\Scripts\python.exe -B -m uvicorn backend.app:create_app --factory --host 127.0.0.1 --port 8000
```

Ollama local no necesita `OPENAI_API_KEY`; nunca se envía esa clave al adaptador local. `OLLAMA_BASE_URL` solo acepta HTTP en loopback, con puerto configurable y sin credenciales, rutas, consultas ni fragmentos. Admite `127.0.0.1`, `localhost` normalizado a `127.0.0.1` e IPv6 `::1`; se desactivan proxies y redirecciones únicamente para Ollama. Un error local no cambia automáticamente a OpenAI ni bloquea ventas, pronósticos o alertas.

`GENAI_TIMEOUT` es el límite por fase: 1–120 segundos para Ollama y 1–30 para OpenAI. Sin definirlo se usan 60 y 15 segundos, respectivamente. `GENAI_MAX_OUTPUT_TOKENS` conserva el rango 200–1600. El frontend y el ejecutor de aceptación adaptan su espera a `request_timeout_seconds` del estado del asistente, con un máximo de 300 segundos; los demás módulos conservan sus tiempos. Las respuestas inválidas, la desconexión, el modelo ausente y el timeout se muestran como errores seguros, sin publicar cifras inventadas.

En la terminal del frontend, ejecutar `npm run dev` desde `frontend` si aún no está iniciado. Iniciar sesión, pulsar «Revisar proveedor» y comprobar `ollama · qwen2.5:3b-instruct`; el estado READY solo confirma configuración, no conectividad ni aceptación. Formular una pregunta con datos, revisar cifras/período/evidencias y comprobar una pregunta fuera de cobertura. Los comandos reales los ejecuta el usuario; no se realizaron llamadas a Ollama ni a SQL Server durante el desarrollo de este adaptador.

La plantilla `.env.example` incluye valores locales de ejemplo y claves vacías; no se carga automáticamente. El adaptador **OpenAI existente** se conserva: [Responses API](https://developers.openai.com/api/docs/quickstart), con [salidas estructuradas](https://developers.openai.com/api/docs/guides/structured-outputs). Para elegirlo expresamente, configurar en la terminal habitual de FastAPI, sin escribir claves en archivos versionados:

```powershell
$env:GENAI_PROVIDER = 'openai'
$env:GENAI_MODEL = 'gpt-4.1-mini'
$env:GENAI_TIMEOUT = '15'
$providerSecret = Read-Host 'Clave de OpenAI' -AsSecureString
$env:OPENAI_API_KEY = [System.Net.NetworkCredential]::new('', $providerSecret).Password
Remove-Variable providerSecret

.\.venv\Scripts\python.exe -B -m uvicorn backend.app:create_app --factory --host 127.0.0.1 --port 8000
```

Conservar las variables `API_*` de conexión y la clave de acceso del panel. OpenAI requiere una cuenta con acceso y cuota de API; Ollama requiere su servicio local y el modelo descargado. React muestra configuración pendiente si falta modelo o configuración válida y, para OpenAI, su clave. La clave del proveedor permanece en el backend; el navegador utiliza únicamente su clave de acceso existente.

El modelo interpreta la pregunta como una intención de un catálogo cerrado: seis KPIs, cobertura, agregados mensuales/anuales y rankings de clientes/productos. El servidor valida fechas, roles e identidades, ejecuta las consultas parametrizadas existentes y entrega evidencias agregadas. Una segunda generación selecciona/ordena IDs de evidencias; el servidor compone cada frase visible con su texto y cifras verificados, sin aceptar prosa factual libre ni SQL del modelo. Se muestran período, filtros efectivos, fuente, consulta autorizada y corte. Los períodos sin cobertura se reconocen como información insuficiente; los filtros aplicados pueden desactivarse en el asistente.

Política de períodos: el backend interpreta determinísticamente mes/año, año completo y rangos absolutos (por ejemplo, «mayo de 2016», «2015» o «del 2016-04-01 al 2016-05-31»). El período de la pregunta prevalece sobre las fechas del dashboard; se avisa en la respuesta y se conservan los filtros de cliente, producto y documento. Sin período explícito se usan las fechas del dashboard o la cobertura disponible; las fechas del modelo no deciden el filtro. «Todo el histórico» debe solicitarse expresamente para reemplazar fechas aplicadas por toda la cobertura. Al comparar abril y mayo se consulta cada período por separado y se exige evidencia de ambos. Años ausentes, fechas inválidas o expresiones ambiguas/relativas requieren precisión. Si falta cobertura de algún mes solicitado (por ejemplo, el año 2016 completo), se devuelve información insuficiente; no se cambia silenciosamente a otro agregado ni a un período parcial. Las fechas finales escritas por el usuario son inclusivas y se convierten al límite exclusivo de las consultas.

Rutas protegidas: `GET /api/v1/assistant/status` y `POST /api/v1/assistant/ask`. Preguntas de hasta 1000 caracteres, máximo doce por minuto y dos simultáneas; tiempo y tokens del proveedor limitados. Al preguntar se envían al proveedor la pregunta, filtros y evidencias agregadas, sin credenciales ni filas de detalle. Las claves y los errores crudos del proveedor no se registran ni devuelven. No se inventan causas, inventarios actuales o cifras futuras.

Diez preguntas de aceptación en `generative_ai/acceptance_cases.json`, incluidas dos sin información disponible. Listarlas no realiza conexiones. La aceptación real llama a la API local y al proveedor, puede consumir cuota, y guarda esperado/obtenido, fuentes y afirmaciones no verificadas en `artifacts/genai-acceptance/`:

```powershell
.\.venv\Scripts\python.exe -B -m generative_ai cases
# En una terminal con API_ACCESS_TOKEN configurado:
.\.venv\Scripts\python.exe -B -m generative_ai acceptance --allow-http-access
```

Los comandos de aceptación usan el proveedor seleccionado en la API. Para Ollama se ejecutan desde otra terminal con el `API_ACCESS_TOKEN` vigente, una vez iniciado el servicio local y reiniciado FastAPI con las variables anteriores. Consultar su configuración con `Invoke-RestMethod -Uri 'http://127.0.0.1:8000/api/v1/assistant/status' -Headers @{ Authorization = "Bearer $env:API_ACCESS_TOKEN" }`. El reporte real citado registra PASS en IA-01, IA-02, IA-03, IA-05, IA-06 e IA-10. Las cuatro no aprobadas son:

- **IA-04, facturas distintas de enero de 2014:** respondió importe extendido con `sales_month_v1`, en lugar de contar facturas mediante `sales_metrics_v1`. Fallo funcional de interpretación/selección del indicador; las fechas son correctas.
- **IA-07, cinco clientes compradores de mayo de 2016:** devolvió `NEEDS_CLARIFICATION` por un plan no autorizado inequívocamente. El período solicitado es correcto; el reporte no conserva el plan ni la validación concreta que falló, por lo que no permite atribuirlo a un filtro específico.
- **IA-08, distribución mensual de 2015:** solo quedó `HTTPError`, sin estado HTTP ni código API. Es un fallo de ejecución HTTP cuya causa específica está pendiente; no demuestra por sí solo timeout ni indisponibilidad de Ollama.
- **IA-09, inventario físico disponible:** respondió importe base histórico de ventas. Fallo funcional de clasificación del alcance. Se agregó un rechazo determinista de preguntas explícitas de inventario físico, sin consultar el DW ni generar una respuesta de ventas; su validación real sigue pendiente.

No se reejecutó la aceptación durante esta revisión ni se modificó el reporte original. Los futuros errores HTTP conservan el estado y los códigos API conocidos, sin mensajes crudos, credenciales ni cuerpos de respuesta. Las dos preguntas sin información deben responder INSUFFICIENT_DATA; IA-10 ya lo hizo en esta ejecución. Un PASS requiere también revisión funcional de intención y filtros; `unverified_assertions: []` solo acredita coincidencia literal con las evidencias y no demuestra que respondan al indicador solicitado.

Pruebas aisladas del adaptador y regresión del asistente, sin llamar a Ollama/OpenAI ni a SQL Server:

```powershell
.\.venv\Scripts\python.exe -B -m unittest discover -s tests -p test_assistant_periods.py -v
.\.venv\Scripts\python.exe -B -m unittest discover -s tests -p test_ollama.py -v
.\.venv\Scripts\python.exe -B -m unittest discover -s tests -p test_intelligent_modules.py -k AssistantTests -v
.\frontend\node_modules\.bin\tsc.cmd --noEmit -p .\frontend\tsconfig.json
```

## Alertas y notificaciones

**CONFIRMADO:** el script `005_alerts_and_notifications.sql` fue ejecutado; `DEMO_SALES_MOM` se activó, la notificación se entregó al dashboard y se marcó como leída. La consulta de duplicados devolvió cero filas. Esta evidencia acredita la demostración manual observada; no valida reintentos ante fallos reales ni ejecución automática programada.

Canal implementado: **buzón interno compartido del dashboard**. Regla académica `DEMO_SALES_MOM`, versión 1: `100 × (importe extendido del mes − mes anterior) / mes anterior <= ALERT_THRESHOLD_PCT`, con referencia anterior positiva, meses consecutivos y comparación redondeada a seis decimales. El umbral predeterminado es −10%; es una regla de demostración, no una política comercial aprobada. El proceso usa el último mes histórico cerrado disponible, o `--through-month YYYY-MM` explícito; no confunde el histórico de 2016 con el mes actual.

El script aditivo 005 ya está aplicado en el laboratorio confirmado. Para preparar otro destino analítico, el comando de referencia es el siguiente; crea únicamente estructuras/procedimientos `ops` y roles restringidos, manteniendo intactos DW, ETL y ML:

```powershell
sqlcmd -S 'HEBER-ENOC-PINE\SQLEXPRESS' -E -N -C -b -i '.\database\scripts\005_alerts_and_notifications.sql'
```

Asignar a las identidades Windows elegidas los roles creados: `wwi_alert_worker` concede SELECT del hecho/checkpoint y EXECUTE de cuatro procedimientos de evaluación/entrega; `wwi_notification_api` concede SELECT del buzón/eventos y EXECUTE únicamente del procedimiento de lectura. El último se añade a los permisos analíticos ya usados por la API. Ejemplo para adaptar, **sin inventar una identidad**: `ALTER ROLE wwi_alert_worker ADD MEMBER [IDENTIDAD_WINDOWS];`. No usar permisos generales de escritura para el servicio. Los cambios de estado quedan encapsulados en procedimientos; las consultas analíticas siguen en el adaptador de solo lectura.

Para reproducir la demostración manual ya confirmada, configurar el proceso independiente en otra terminal. El comando de no activación sigue siendo un caso de comprobación pendiente de evidencia real:

```powershell
$env:ALERT_ENVIRONMENT = 'local'
$env:ALERT_SQL_SERVER = 'HEBER-ENOC-PINE\SQLEXPRESS'
$env:ALERT_SQL_DATABASE = 'WideWorldImporters_Analytics'
$env:ALERT_ODBC_DRIVER = 'ODBC Driver 17 for SQL Server'
$env:ALERT_TRUST_SERVER_CERTIFICATE = 'true'
$env:ALERT_THRESHOLD_PCT = '-10'
$env:ALERT_MAX_ATTEMPTS = '3'

.\.venv\Scripts\python.exe -B -m notifications plan
# Demostración con datos reales: ajusta el umbral a variación observada + 1 punto.
# Se conserva expresamente el modo FORCED_DEMO_ON en la evaluación y el evento.
.\.venv\Scripts\python.exe -B -m notifications run --through-month 2016-05 --demo-trigger --allow-database-access
# Caso de no activación: umbral = variación observada − 1 punto.
.\.venv\Scripts\python.exe -B -m notifications evaluate --through-month 2016-05 --demo-no-trigger --allow-database-access
```

Reiniciar FastAPI para cargar las nuevas rutas y abrir **Notificaciones**. Actualización automática del buzón cada 30 segundos o mediante su botón; «Marcar como leída» registra recepción/lectura. Repetir el comando de activación conserva una sola notificación para la misma regla, período, valores, umbral y modo, incluso si hay una publicación posterior con valores iguales. Cada evaluación sí se audita. El script opcional `006_verify_alerts.sql` permite comprobar evaluaciones, estados, intentos, entrega, lectura y ausencia de duplicados.

**IMPLEMENTADO, PENDIENTE DE VALIDACIÓN REAL:** se conserva un outbox con reserva temporal de tres minutos, unicidad por evento, entrega y estado en una transacción, reintentos con espera mínima de 10, 20, … segundos y límite configurable. Un fallo conserva el evento para recuperación. Los siguientes comandos preparan una demostración de reintento con falla simulada, **antes de entregar un evento pendiente**; no acreditan recuperación ante fallos reales:

```powershell
.\.venv\Scripts\python.exe -B -m notifications evaluate --through-month 2016-05 --demo-trigger --allow-database-access
.\.venv\Scripts\python.exe -B -m notifications deliver --simulate-delivery-failure --limit 1 --allow-database-access
.\.venv\Scripts\python.exe -B -m notifications deliver --retry-now --allow-database-access
```

La falla simulada se identifica como `ACADEMIC_SIMULATED_FAILURE`; no implica una falla real del servidor. Si ese evento ya está entregado, no se vuelve a enviar: usar un período distinto con datos para demostrar un intento nuevo.

**PENDIENTE DE VALIDACIÓN:** automatización independiente de HTTP y de SQL Server Agent. El proceso y el script están disponibles, pero no se ha confirmado su funcionamiento programado:

```powershell
.\.venv\Scripts\python.exe -B -m notifications watch --interval-seconds 300 --allow-database-access
```

Alternativamente, crear `.env.alerts` local (ignorado por Git) con las variables `ALERT_*` anteriores y programar `scripts/run-alerts.ps1 -ConfigFile RUTA_ABSOLUTA\.env.alerts` cada cinco minutos en el Programador de tareas de Windows, bajo la identidad con permisos. El script carga únicamente esas variables como valores literales del proceso. Ejemplo manual de registro desde la raíz:

```powershell
$projectPath = (Get-Location).Path
$action = New-ScheduledTaskAction -Execute 'powershell.exe' -Argument ('-NoProfile -NonInteractive -WindowStyle Hidden -File "{0}\scripts\run-alerts.ps1" -ConfigFile "{0}\.env.alerts"' -f $projectPath) -WorkingDirectory $projectPath
$trigger = New-ScheduledTaskTrigger -Once -At (Get-Date).AddMinutes(1) -RepetitionInterval (New-TimeSpan -Minutes 5)
Register-ScheduledTask -TaskName 'WWI-Alertas-Academicas' -Action $action -Trigger $trigger
```

Estos comandos de programación no fueron ejecutados por el agente y no constituyen evidencia de automatización validada. Las rutas protegidas del buzón son `GET /api/v1/notifications` y `POST /api/v1/notifications/{UUID}/read`. En destinos nuevos, sin script/permisos se muestra preparación pendiente; los errores del buzón no bloquean ventas ni pronósticos. Permanecen pendientes la comprobación real de no activación, los reintentos ante fallos reales, la programación automática y la revisión de permisos mínimos. La aceptación real de IA obtuvo 6/10; siguen pendientes de resolución y validación los cuatro casos descritos en la sección del asistente. No se mantienen como pendientes el DDL 005, la activación, entrega, lectura o la consulta de duplicados ya confirmados. Pruebas críticas locales desde la raíz: `.\.venv\Scripts\python.exe -B -m unittest discover -s tests -p test_intelligent_modules.py -v`, `npm --prefix .\frontend test` y `npm --prefix .\frontend run build`.

## Ejecución

Requisitos comprobados del ETL: Python 3.13.17, entorno `.venv`, pyodbc 5.5.0, Driver ODBC 17 y `sqlcmd` en Windows. Su dependencia Python está en [requirements.txt](requirements.txt). Para otro entorno: `.\.venv\Scripts\python.exe -m pip install --only-binary=:all: -r requirements.txt`.

Pruebas y plan sin conexiones:

```powershell
.\.venv\Scripts\python.exe -B -m unittest discover -s tests -v
.\.venv\Scripts\python.exe -B -m etl plan --full
```

Los siguientes comandos son **para ejecución manual del usuario**, desde la raíz. No fueron ejecutados por el agente. Los scripts 001/002 ya se aplicaron en el laboratorio confirmado; en un destino nuevo ejecutar primero esos dos en orden.

```powershell
sqlcmd -S 'HEBER-ENOC-PINE\SQLEXPRESS' -E -N -C -b -i '.\database\scripts\003_complete_sales_pipeline.sql'

$env:ETL_ENVIRONMENT = 'local'
$env:ETL_SOURCE_SERVER = 'HEBER-ENOC-PINE\SQLEXPRESS'
$env:ETL_TARGET_SERVER = 'HEBER-ENOC-PINE\SQLEXPRESS'
$env:ETL_SOURCE_DATABASE = 'WideWorldImporters'
$env:ETL_TARGET_DATABASE = 'WideWorldImporters_Analytics'
$env:ETL_ENCRYPT = 'yes'
$env:ETL_TRUST_SERVER_CERTIFICATE = 'true'
$env:ETL_BATCH_SIZE = '500'
$env:ETL_QUERY_TIMEOUT = '300'

.\.venv\Scripts\python.exe -B -m etl run --allow-database-access --consistency stable-window --confirm-stable-source

sqlcmd -S 'HEBER-ENOC-PINE\SQLEXPRESS' -E -N -C -b -i '.\database\scripts\004_verify_sales_pipeline.sql'
```

`stable-window` exige que las cuatro tablas permanezcan sin escrituras durante toda la captura. La confirmación es externa; el programa no congela la fuente. Alternativa: `--consistency snapshot`, únicamente si ALLOW_SNAPSHOT_ISOLATION ya está habilitado y verificado en el origen. El programa no cambia esa configuración.

La excepción de certificado es explícita y solo admite `ETL_ENVIRONMENT=local` con ambas conexiones a la instancia indicada. `Encrypt=yes` es obligatorio; confianza del certificado predeterminada `false`. En producción se mantiene validación. Los parámetros `sqlcmd -N -C` son la excepción explícita del mismo laboratorio local.

[.env.example](.env.example) enumera las variables; no se carga automáticamente. Autenticación integrada de Windows, sin contraseñas en código. La identidad debe poder leer las cuatro proyecciones del origen y leer/escribir staging, DW y auditoría del destino, además de ejecutar los procedimientos de application locks. Los permisos DDL se necesitan únicamente al aplicar los scripts.

## Recuperación y muestras

Para reutilizar un Raw completo tras un fallo en Clean/Ready/publicación, reemplazar el UUID por el informado:

```powershell
.\.venv\Scripts\python.exe -B -m etl run --allow-database-access --execution-id UUID_DE_LA_EJECUCION
```

Ese comando solo abre el destino: verifica origen lógico, versiones, cuatro manifiestos, conteos y hashes. Si ya está publicado devuelve un no-op. Cada intento de transformación tiene un UUID propio; Raw conserva el intento de captura original. Un Raw incompleto exige una nueva captura completa. Si se perdió la confirmación de commit, comprobar/reutilizar el mismo execution_id antes de recapturar.

Una secuencia técnica de captura impide publicar Raw anterior a un corte ya publicado. En ese caso se conserva la evidencia y se exige una captura nueva; no se hace retroceder el DW.

Las muestras anteriores permanecen intactas. `extract --max-rows-per-table 100` conserva el comando de muestra de facturas/líneas, siempre RAW_PARTIAL. `run` no admite límites ni convierte esas muestras en publicaciones. Reintentar solo reconstruye Clean/Ready del execution_id elegido; no borra Raw ni la evidencia de intentos. No hay purga automática: medir espacio y retención en Express antes de acumular generaciones.

## Comportamiento implementado

- Una captura completa de Customers, StockItems, Invoices e InvoiceLines por corte coherente. Proyecciones del diccionario, lectura por lotes, autenticación/TLS comunes a ambas conexiones. Fechas datetime2(7) conservadas como ISO exacto y sin atribuir zona horaria comercial.
- Raw inmutable por ejecución; claves, UUID, hashes y manifiestos. Conteo independiente del origen dentro del mismo corte, comparado con filas leídas y persistidas. Validación técnica antes de insertar; incompatibilidades conservadas en cuarentena y captura fallida.
- Clean revalida tipos, nulabilidad, duplicados, hashes y las cinco relaciones. Guarda originales, regla, severidad, entidad, clave e intento en `audit.etl_quarantine`. ERROR bloquea toda la publicación; WARNING conserva la fila. No hay ganador arbitrario para claves conflictivas.
- Cantidad no positiva queda en revisión bloqueante según DQ-15 hasta aprobar su clasificación comercial. Catálogos no resueltos bloquean: la política de usar miembros desconocidos aún no está aprobada. Los miembros técnicos 0 existen con clave natural NULL.
- Precio NULL y Brand NULL se conservan; base NULL implica medida incompleta. Precios cero/negativos, notas de crédito, espacios externos y diferencias base+impuestos/extendido se registran como advertencias. No se imputan ceros, recortan nombres, invierten signos ni reconstruyen ExtendedPrice.
- Ready integra una fila por InvoiceLineID, roles de comprador/facturado tomados de Invoices y base Quantity × UnitPrice en decimal(29,2). El precio actual del producto no sustituye al precio de venta.
- DW: DimFecha con años completos e ISO semanal; DimCliente única con dos roles; DimProducto; FactVentas por InvoiceLineID. SCD Tipo 1, claves sustitutas persistentes, bajas lógicas y reactivación sin duplicar el hecho. Los períodos temporales del catálogo no se interpretan como vigencia comercial.
- Lectura completa y carga diferencial del destino: insertados, modificados, sin cambios, eliminados y reactivados. Detecta cambios sin timestamp y de cabecera, y registros tardíos. Reactivados es subconjunto de modificados; activos actuales = activos anteriores + insertados + reactivados − eliminados.
- Publicación de dimensiones, hechos, checkpoint y auditoría en una transacción. Exclusión de ejecuciones mediante [application lock de sesión](https://learn.microsoft.com/en-us/sql/relational-databases/system-stored-procedures/sp-getapplock-transact-sql?view=sql-server-ver16). El checkpoint guarda generación, origen, versiones y vector de manifiestos; LastEditedWhen es observación y no filtro exclusivo.
- Reconciliación exacta de conteos, unidades, base conocida/faltante, impuestos, extendido, utilidad y facturas distintas entre las cuatro zonas. Comparación de valores por línea en ambos sentidos y control de claves dimensionales. Los totales solos no autorizan la publicación.
- `dw.vw_VentasActivas` excluye bajas y `dw.vw_KPIVentas` calcula los seis indicadores, con divisiones entre cero y base incompleta tratadas explícitamente. El checkpoint identifica el corte actual; publication_id en cada fila indica su última modificación, no un filtro para excluir filas sin cambios.

Solo se observa el estado de cada corte; inserciones y bajas ocurridas entre dos capturas pueden ser invisibles. Tipo 1 no reconstruye los atributos «como se conocían al vender». Moneda, unidad comercial y reglas de créditos siguen pendientes de aceptación funcional; no se asumen USD/HNL ni conversiones. Los meses fuera de cobertura no se rellenan con ceros.

## Archivos principales

```text
etl/
  config.py, contracts.py, extract.py, staging.py, pipeline.py, cli.py
  quality.py, warehouse.py, warehouse_store.py, workflow.py
backend/
  app.py, config.py, domain.py, queries.py, repository.py, service.py, forecast.py
  requirements.txt
frontend/
  package.json, vite.config.ts, index.html
  src/                   # React/TypeScript, ECharts y estilos responsive
  tests/model.test.cjs
machine_learning/
  __main__.py, series.py, training.py, artifacts.py, requirements.txt
database/scripts/
  001_create_analytics_database.sql
  002_create_raw_and_audit.sql
  003_complete_sales_pipeline.sql
  004_verify_sales_pipeline.sql
tests/
  test_etl.py
  test_warehouse.py
  test_api.py
  test_machine_learning.py
```

stdout contiene progreso/resumen JSON; stderr, errores seguros sin el mensaje crudo del driver. Se conservan SQLSTATE, proveedor y código nativo reconocidos. Salidas: 0 éxito, 1 fallo, 2 bloqueo/configuración y 130 interrupción. Una terminación abrupta puede dejar estado en curso; comprobar el execution_id antes de reintentar.

## Referencias del proyecto

| Contenido | Archivo |
|---|---|
| Fuente, diccionario, ERD y evidencias iniciales | [database/README.md](database/README.md) |
| Arquitectura y responsabilidades | [architecture.md](docs/architecture/architecture.md) |
| Modelo dimensional y SCD | [dimensional-model.md](docs/architecture/dimensional-model.md) |
| Staging y catálogo de reglas | [staging-design.md](docs/architecture/staging-design.md) |
| Contrato incremental y casos | [incremental-loads.md](docs/architecture/incremental-loads.md) |
| Seis indicadores y consultas de contraste | [metrics-contract.md](docs/quality/metrics-contract.md) |
| Requisitos y criterios académicos | [traceability.md](docs/requirements/traceability.md) |

Los documentos de diseño conservan su versión previa. La guía de noviembre de 2026 y el informe Word están en la carpeta superior y permanecen intactos. La publicación del ETL, el funcionamiento de API/dashboard, el entrenamiento real de ML y la demostración manual de alertas corresponden a la evidencia confirmada por el usuario. Las pruebas aisladas no sustituyen esa evidencia ni acreditan por sí solas precisión predictiva, reintentos reales, programación automática o integración con Ollama. Incorporar al informe las métricas y capturas de las ejecuciones realizadas, manteniendo explícitos los pendientes.
