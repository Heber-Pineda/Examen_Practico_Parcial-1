# Plataforma Integral de Ingeniería de Datos y Analítica Inteligente

Proyecto académico de análisis de ventas sobre WideWorldImporters. Stack aprobado: SQL Server 2022 Express, ETL con Python/SQL, FastAPI, React/TypeScript/Vite, Apache ECharts, Scikit-learn, IA generativa mediante consultas controladas y notificaciones.

El repositorio contiene el descubrimiento y los contratos técnicos. ETL, DW, API, frontend, ML y automatización todavía no están implementados.

## Documentación del proyecto

| Contenido | Archivo |
|---|---|
| Fuente, diccionario, ERD y resultados iniciales | [database/README.md](database/README.md) |
| Arquitectura, responsabilidades y decisiones pendientes | [architecture.md](docs/architecture/architecture.md) |
| Modelo estrella, grano, claves y SCD | [dimensional-model.md](docs/architecture/dimensional-model.md) |
| Raw/Clean/Ready, calidad, auditoría y retención | [staging-design.md](docs/architecture/staging-design.md) |
| Carga inicial/incremental, recuperación y 22 casos de prueba | [incremental-loads.md](docs/architecture/incremental-loads.md) |
| Seis KPIs, consultas propuestas y 14 casos de prueba | [metrics-contract.md](docs/quality/metrics-contract.md) |
| Requisitos, criterios de aceptación y evidencias de la guía | [traceability.md](docs/requirements/traceability.md) |

Las carpetas y los nombres de documentos son una elección del proyecto. La guía exige contenidos, pruebas y resultados; su estructura de carpetas y sus tecnologías de referencia son recomendaciones. Las tecnologías indicadas arriba fueron seleccionadas para este proyecto.

## Estructura actual

```text
.
├── .gitignore
├── README.md
├── Figura_2_Arquitectura_Plataforma.png
├── Figura_2_Arquitectura_Plataforma.svg
├── database/
│   ├── README.md
│   ├── Diccionario_Datos_WideWorldImporters.csv
│   ├── Perfilado_Inicial_WideWorldImporters.csv
│   ├── ERD_WideWorldImporters.dbml
│   ├── ERD_WideWorldImporters_Origen_OLTP.png
│   ├── ERD_WideWorldImporters_Origen_OLTP.svg
│   └── normalized/
│       ├── Diccionario_Datos_WideWorldImporters.csv
│       └── Perfilado_Inicial_WideWorldImporters.csv
└── docs/
    ├── architecture/
    │   ├── architecture.md
    │   ├── dimensional-model.md
    │   ├── staging-design.md
    │   └── incremental-loads.md
    ├── quality/
    │   └── metrics-contract.md
    └── requirements/
        └── traceability.md
```

El código se organizará por responsabilidades ETL, backend, frontend, ML, GenAI, notificaciones, scripts y pruebas cuando existan archivos ejecutables. Las responsabilidades ya están definidas en arquitectura; no se mantienen directorios con README de reserva.

## Entorno y reproducción

Última comprobación documentada: 2026-11-14.

| Elemento | Resultado / pendiente |
|---|---|
| SQL Server | 2022 Express; WideWorldImporters en `HEBER-ENOC-PINE\SQLEXPRESS`, confirmado por el proyecto |
| Git | 2.49.0.windows.1 |
| Node.js / npm | 20.19.4 / 10.8.2; seleccionar una LTS mantenida antes del desarrollo |
| Python | Intérprete utilizable pendiente; accesos WindowsApps y shim python3.12 no acreditaron ejecución |
| ODBC | Driver 17 registrado y DLL presente; conexión no probada por el agente |
| Dependencias | Versiones, entornos y manifiestos pendientes; sin instalaciones realizadas |

El shim Python 3.12 apuntaba a `C:\python312\python.exe` inexistente; `py` no estaba en PATH. Se propone evaluar Python 3.12/3.13 y Node 24 LTS al preparar el entorno. [Calendario de soporte de Node.js](https://nodejs.org/en/about/previous-releases).

Dependencias previstas: pyodbc/SQLAlchemy, pandas/NumPy, FastAPI/Pydantic/Uvicorn y las bibliotecas del stack aprobado. Su disponibilidad y versiones no están verificadas para el proyecto.

Desde la raíz se pueden revisar los archivos y comprobar herramientas sin conectar a bases:

```powershell
git status --short --branch
git diff --check
node --version
npm --version
Get-Command python, python3, python3.12, py -ErrorAction SilentlyContinue
```

Los comandos de instalación, ejecución y recuperación se documentarán junto con el código. La guía exige README reproducible, manuales y una plantilla `.env.example` sin secretos para la entrega; esos artefactos ejecutables aún están pendientes.

## Fuentes académicas y seguridad

La guía versión 1.0, noviembre de 2026, y el informe Word están en la carpeta superior: `../Guia_Proyecto_Ingenieria_Datos_Analitica_Inteligente.pdf` y `../Informe_Examen_Practico_Parcial-1.docx`. El informe incluye descubrimiento, requisitos, KPIs y arquitectura. Su portada indica 26 de octubre de 2026, fecha pendiente de revisión académica. Ambos originales permanecen fuera de estos commits.

La base original y sus archivos temporales serán de solo lectura. Las pruebas de cambios usarán un entorno independiente autorizado. Credenciales y configuración local quedan excluidas de Git; ETL, entrenamiento y automatización se ejecutarán fuera de las solicitudes HTTP.
