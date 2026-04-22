# Reporte Técnico Completo del Proyecto
## SAP AI Security Anomaly Detection Hackathon — TEC de Monterrey × SAP
**Fecha de reporte:** 22 de Abril 2026  
**Autor:** Cloud Integration Engineer  
**Estado:** Ingesta automatizada operativa · HANA en proceso de integración · Modelo ML y alerting pendientes

---

## 1. Contexto y objetivo del hackathon

### El reto
El hackathon pide construir un **Security Operations Center (SOC) en vivo** para sistemas SAP. No es un proyecto de IA aislado — es un pipeline completo de extremo a extremo que debe:

1. **Observar** logs de seguridad SAP en tiempo real
2. **Analizar** patrones y detectar comportamiento anormal con ML
3. **Detectar** anomalías con precisión, distinguiendo amenazas reales de ruido
4. **Responder** con alertas automáticas y reportes forenses

El enfoque de detección es **aprendizaje no supervisado** porque no hay etiquetas previas de "esto es un ataque" — los ataques son raros, nuevos y desconocidos. El sistema aprende lo normal y alerta cuando algo se desvía de ese patrón.

### Criterios de evaluación
| Criterio | Peso | Lo que mide |
|---|---|---|
| Operational Efficiency & Real-Time Response | **40%** | MTTD, Alert Latency, automatización end-to-end |
| SAP Ecosystem Integration & Tooling | **25%** | Uso real de BTP, Cloud Foundry, HANA, SAC |
| Architecture & MLOps Maturity | **20%** | Escalabilidad, robustez, separación de componentes |
| Business Impact & Strategic Analysis | **15%** | Reporte forense, storytelling ejecutivo |

El criterio #1 pesa el 40% y es el que más depende de que el pipeline de ingesta esté automatizado y que las alertas lleguen sin intervención humana.

### Fechas críticas
| Fecha | Hito | Estado |
|---|---|---|
| Abr 6 | Kick Off | ✅ |
| Abr 13 | API & Data Access | ✅ |
| Abr 20-21 | Pipeline de ingesta automatizado | ✅ |
| **Abr 27** | **Alerting Webhook disponible** | ⏳ |
| **May 4** | **Go Live en SAP BTP / Cloud Foundry** | ⏳ |
| May 12–14 | Primera Fase Eliminatoria | ⏳ |
| May 21 | Final Phase (First to Go Down) | ⏳ |

---

## 2. El ecosistema SAP — mapa completo de infraestructura

### La jerarquía de SAP BTP

```
SAP BTP Global Account (el contrato con SAP)
  └── Subaccount "Trial" (tu espacio de trabajo)
        ├── Cloud Foundry Environment
        │     └── Space "Dev"
        │           └── [AQUÍ corre tu app Python — cf push]
        └── SAP HANA Cloud (instancia de base de datos)
              ├── Tabla RAW_LOGS_SISTEMA
              └── Tabla RAW_LOGS_LLM
```

**SAP BTP (Business Technology Platform)** es el edificio completo donde viven todos los servicios. Es la plataforma cloud de SAP que conecta todo.

**Cloud Foundry** es el entorno de ejecución dentro de BTP. Cuando haces `cf push`, subes tu código Python aquí y Cloud Foundry lo mantiene corriendo 24/7, aunque tu laptop esté apagada. CF se encarga de instalar dependencias (`requirements.txt`), gestionar el proceso, y reiniciarlo si muere.

**SAP HANA Cloud** es la base de datos relacional en la nube de SAP. No es un archivo — es una instancia de base de datos completa con SQL, índices, y acceso concurrente. Tus datos de logs quedan guardados aquí permanentemente y cualquier miembro del equipo puede consultarlos con credenciales.

**SAP Analytics Cloud (SAC)** es la herramienta de visualización ejecutiva. Se conecta directamente a HANA como fuente de datos y permite crear dashboards que se actualizan en tiempo real conforme entran nuevos datos.

### Cómo se conectan todos

```
API SAP (fuente de datos)
      │
      │ HTTPS · Bearer Token
      ▼
pipeline_loop.py (en tu laptop o en Cloud Foundry)
      │
      ├──► data/raw/logs_[timestamp].csv  (backup local siempre)
      │
      └──► SAP HANA Cloud
                │
                ├── RAW_LOGS_SISTEMA  (logs INFO, WARNING, ERROR, etc.)
                └── RAW_LOGS_LLM      (logs LLM_REQUEST, LLM_ERROR, etc.)
                          │
                          ▼
                SAP Analytics Cloud  (dashboards ejecutivos)
                          │
                          ▼
                  Viz Lead / Reporte forense final
```

---

## 3. La API del hackathon — fuente de todos los datos

### Endpoints disponibles

| Endpoint | Auth | Propósito |
|---|---|---|
| `GET /health` | ❌ No requerida | Liveness probe — verifica que el servidor vive |
| `GET /info` | ✅ Bearer Token | Metadata de ventana: total_pages, total_records, batch_size |
| `GET /logs/current?page=N` | ✅ Bearer Token | Logs de la ventana actual, paginados (500 registros/página) |

**URL base:** `https://sap-api-b4.674318.xyz`  
**Autenticación:** `Authorization: Bearer <team-token>`

### Comportamiento temporal — ventanas de 30 minutos

La API no sirve datos históricos. Siempre devuelve la ventana UTC de 30 minutos que está activa en el momento de la llamada:

| Minuto UTC | Ventana servida |
|---|---|
| 00 – 29 | HH:00:00 → HH:30:00 |
| 30 – 59 | HH:30:00 → HH+1:00:00 |

**Implicación crítica:** si no ingestas una ventana, esos ~5,729 registros desaparecen para siempre. El pipeline debe correr de forma continua cada 30 minutos.

### Estructura de respuesta de `/logs/current`

```json
{
  "request_time_utc": "2026-04-21T00:00:12.000Z",
  "window_start":     "2026-04-21T00:00:00+00:00",
  "window_end":       "2026-04-21T00:30:00+00:00",
  "total_records":    5551,
  "batch_size":       500,
  "current_page":     1,
  "total_pages":      12,
  "records_in_page":  500,
  "data": [ ... ]
}
```

### Schema real de los logs — 43 columnas, dos tipos

Los logs son de dos categorías con columnas distintas. Los nulos son por diseño de la API — no son errores.

**Columna discriminante:** `sap_function_log_type`

| Categoría | Valores | Columnas vacías |
|---|---|---|
| **Sistema** | `INFO`, `WARNING`, `ERROR`, `DEBUG`, `AUDIT`, `PERF`, `SECURITY` | Todas las `llm_*` |
| **LLM** | `LLM_REQUEST`, `LLM_ERROR`, `LLM_TIMEOUT` | `service_id`, `http_status_code`, `client_ip` |

**Columnas completas observadas:**
- Identificación: `_id`, `_index`, `_score`, `_ignored`
- Temporales: `@timestamp`, `@event_time_requested`, `@version`
- Clasificación: `sap_function_log_type`, `sap_source_type`, `sap_function_application`, `sap_app_env`, `sap_function_message`, `event_code_version`, `event_hash`
- Red/HTTP (Sistema): `service_id`, `http_status_code`, `client_ip`, `headers_http_host`, `headers_http_request_method`, `headers_content_type`, `heathers_request_path` *(typo en la API — viene con 'heathers')*
- Región: `region_id`, `region_name`, `region_code`, `macro_region`
- LLM modelo: `llm_model_id`, `llm_provider`, `llm_status`, `llm_error_message`, `llm_finish_reason`
- LLM prompt: `llm_prompt_id`, `llm_prompt_category`, `llm_prompt`, `llm_stream`
- LLM métricas: `llm_prompt_tokens`, `llm_completion_tokens`, `llm_total_tokens`, `llm_cost_usd`, `llm_response_time_ms`, `llm_response_size_bytes`, `llm_temperature`, `llm_top_p`
- SAP heredadas: `sap_llm_response_size`, `sap_llm_response_time`

**Dimensiones observadas:**
- ~5,500–5,729 registros por ventana de 30 min
- 12 páginas de 500 registros cada una
- 43 columnas totales

---

## 4. Estructura actual del repositorio

```
sap-security-hackathon/          ← raíz del proyecto
│
├── pipeline_loop.py             ← orquestador del loop de 30 min ✅
├── pipeline.log                 ← log persistente del pipeline (autogenerado)
├── manifest.yml                 ← instrucciones de deploy para Cloud Foundry ✅
├── prueba_health.py             ← diagnóstico: ¿el servidor vive? ✅
├── prueba_info.py               ← diagnóstico: ¿el token es válido? ✅
├── requirements.txt             ✅
├── .env                         ← credenciales reales (NUNCA en Git) ✅
├── .env.examples                ← plantilla vacía (SÍ en Git) ✅
├── .gitignore                   ✅
├── README.md                    ✅
│
├── app/                         ← módulos del pipeline
│   ├── config.py                ← gestión de variables de entorno ✅
│   ├── ingest.py                ← extracción de API + guardado CSV + HANA ✅
│   ├── hana_client.py           ← conexión y escritura en SAP HANA Cloud ✅
│   ├── etl.py                   ← limpieza y transformación ✅
│   ├── explore.py               ← análisis exploratorio ✅
│   ├── alerting.py              ← webhook dispatch ⏳ (esperando URL Abr 27)
│   ├── main.py                  ← dashboard Streamlit ⏳
│   └── __init__.py
│
├── data/
│   ├── raw/
│   │   ├── logs_[timestamp].csv ← datos crudos por ventana (acumulados)
│   │   └── sample_10.csv        ← muestra para el equipo ✅
│   ├── processed/
│   │   ├── clean_full.csv       ← dataset completo limpio ✅
│   │   ├── clean_sistema.csv    ← solo logs de Sistema ✅
│   │   └── clean_llm.csv        ← solo logs de LLM ✅
│   └── SCHEMA.md                ← documentación de columnas ✅
│
├── docs/
│   └── TEAM_BRIEFING_cloud_integration_v2.md ✅
│
├── notebooks/                   ← exploración (AI Specialist)
└── tests/
```

**Ramas Git:**
- `main` → código estable, demo-ready
- `develop` → trabajo diario del equipo ← rama activa

---

## 5. Descripción técnica de cada archivo del pipeline

### `app/config.py` — gestión de configuración y credenciales

**Propósito:** es el único archivo que sabe de dónde vienen las credenciales. Todos los demás módulos importan desde aquí.

**Inputs:** archivo `.env` en la raíz del proyecto.

**Outputs:** variables Python (`API_BASE_URL`, `BEARER_TOKEN`, `HANA_HOST`, etc.) y funciones `validate_config()` y `get_headers()`.

**Lógica clave:**
- `load_dotenv(dotenv_path=ruta_absoluta)` — busca el `.env` usando la ruta absoluta calculada desde la ubicación de `config.py`, garantizando que lo encuentra sin importar desde dónde se importe el módulo.
- `validate_config()` — verifica que `API_BASE_URL` y `BEARER_TOKEN` están presentes antes de intentar cualquier llamada HTTP. Falla rápido con mensaje claro.
- `get_headers()` — construye el header de autenticación Bearer en cada llamada, no como variable estática.

**Conexión con SAP:** provee las credenciales de la API SAP y de SAP HANA a todos los módulos que las necesiten.

---

### `app/ingest.py` — extracción de datos y persistencia

**Propósito:** extraer todos los logs de la ventana actual y guardarlos en CSV y en HANA.

**Inputs:** 
- API SAP vía HTTP (endpoints `/info` y `/logs/current`)
- Credenciales de `config.py`

**Outputs:**
- `data/raw/logs_[timestamp].csv` — todos los registros de la ventana, crudos
- `data/raw/sample_10.csv` — 10 filas para uso del equipo
- Inserción en tablas `RAW_LOGS_SISTEMA` y `RAW_LOGS_LLM` de HANA (si HANA está configurado)
- Dict de resultado con metadatos del ciclo

**Funciones principales:**

`fetch_current_window()` — implementa el loop de paginación oficial de la API:
1. Llama `GET /info` para saber cuántas páginas hay
2. Itera `GET /logs/current?page=1..N` acumulando registros en lista Python
3. Convierte la lista a DataFrame con `pd.DataFrame(all_records)`
4. Retorna `(df, meta)`

`save_data(df, meta)` — usa rutas absolutas calculadas desde `__file__` para escribir siempre en `data/raw/` del proyecto, sin importar desde dónde se llame.

`ingest_and_persist()` — función orquestadora que llama a `fetch_current_window()` → `save_data()` → `insert_logs()` (HANA). HANA es opcional: si `HANA_HOST` no está configurado o falla, el pipeline continua solo con CSV.

**Detección de HANA disponible:** al importar el módulo, se evalúa `bool(HANA_HOST)`. Si es True, se importa `hana_client`. Si falla, se activa el modo solo-CSV sin detener el pipeline.

---

### `app/hana_client.py` — conexión y escritura en SAP HANA Cloud

**Propósito:** es el único módulo que habla directamente con HANA. Gestiona conexiones, crea tablas e inserta datos.

**Inputs:**
- Credenciales HANA de `config.py` (`HANA_HOST`, `HANA_PORT`, `HANA_USER`, `HANA_PASSWORD`)
- DataFrame con logs del ciclo actual

**Outputs:**
- Tablas `RAW_LOGS_SISTEMA` y `RAW_LOGS_LLM` en HANA (creadas si no existen)
- Registros insertados en esas tablas vía `executemany()`
- Dict `{"sistema": N, "llm": M}` con conteo de inserciones

**Función `get_connection()`:**
Usa `hdbcli.dbapi.connect()` con parámetros:
- `encrypt=True` — obligatorio para HANA Cloud
- `sslValidateCertificate=False` — necesario en trial (certificado wildcard SAP)
- `port=443` — puerto estándar de HANA Cloud

**Función `create_tables()`:**
Crea las dos tablas con `CREATE TABLE IF NOT EXISTS` — idempotente. Diseño de columnas basado en `data/SCHEMA.md`. Dos tablas separadas porque los tipos de log tienen columnas completamente distintas — una sola tabla tendría 50% de NULLs por fila.

**Función `insert_logs(df)`:**
- Separa el DataFrame en Sistema y LLM
- Construye listas de tuplas con funciones `_safe_*` que convierten NaN a None
- Usa `cursor.executemany()` — inserta todas las filas en una sola operación SQL (O(1) en llamadas al servidor, no O(n))
- `conn.commit()` al final — sin esto los datos no se persisten
- `finally` garantiza que cursor y conexión siempre se cierran

**Funciones auxiliares `_safe_str`, `_safe_float`, `_safe_int`, `_safe_ts`:**
Convierten valores del DataFrame a tipos que HANA acepta. El problema central es que pandas usa `float('nan')` para valores faltantes, pero HANA espera `None` para insertar NULL. Sin estas funciones, cada NaN causaría un error de tipo en el INSERT.

---

### `app/etl.py` — limpieza y transformación

**Propósito:** tomar datos crudos de `data/raw/` y producir datasets limpios para el modelo de ML.

**Inputs:** CSV más reciente de `data/raw/logs_*.csv`

**Outputs:**
- `data/processed/clean_full.csv` — dataset completo limpio
- `data/processed/clean_sistema.csv` — solo logs de Sistema
- `data/processed/clean_llm.csv` — solo logs de LLM
- Reporte de calidad en consola

**Transformaciones aplicadas:**
1. Elimina `_score`, `_ignored`, `_index` — campos internos de Elasticsearch sin valor analítico
2. Normaliza `@timestamp` y `@event_time_requested` a `datetime64 UTC`
3. Normaliza columnas numéricas LLM a `float64`
4. Normaliza `llm_stream` de "TRUE"/"FALSE" string a bool Python
5. Separa por tipo de log
6. Genera reporte de calidad que distingue nulos esperados (>90%) de inesperados (<90%)

**Principio de idempotencia:** correr el ETL N veces sobre los mismos datos produce exactamente el mismo resultado.

---

### `app/explore.py` — análisis exploratorio

**Propósito:** analizar el CSV más reciente y generar un reporte técnico del dataset para documentar el schema real.

**Inputs:** CSV más reciente de `data/raw/logs_*.csv`

**Outputs:**
- Reporte en consola: shape, tipos, nulos, distribuciones, rango temporal
- `data/processed/logs_sistema.csv` y `data/processed/logs_llm.csv`
- `data/raw/sample_10.csv` actualizado con 5 filas Sistema + 5 filas LLM

---

### `pipeline_loop.py` — orquestador de automatización

**Propósito:** correr `ingest_and_persist()` automáticamente cada vez que cambia la ventana UTC de 30 minutos, indefinidamente, sin intervención manual.

**Inputs:** ninguno explícito — lee configuración de `.env` vía `config.py`.

**Outputs:**
- Múltiples archivos `data/raw/logs_[timestamp].csv` (uno por ventana)
- Datos en HANA (si está configurado)
- `pipeline.log` — historial completo de todas las ejecuciones

**Lógica de sincronización:**
No usa `time.sleep(1800)` fijo (causaría drift acumulativo). Calcula exactamente cuántos segundos faltan para el próximo `:00` o `:30` UTC y duerme ese tiempo preciso. El +2 segundos de margen garantiza que el servidor ya cargó los datos de la nueva ventana.

**Primera ejecución inmediata:**
Al arrancar, ejecuta una ingesta inmediatamente antes de entrar al loop. Esto captura la ventana actual sin esperar hasta el próximo cambio.

**Manejo de errores:**
- Timeout, ConnectionError, HTTP 5xx → recuperables → loguea + espera 60s + reintenta
- HTTP 401 → fatal → loguea + `sys.exit(1)` (token inválido, no sirve reintentar)
- `KeyboardInterrupt` (Ctrl+C) → cierre limpio con reporte de ciclos completados

**Logging dual:**
Escribe simultáneamente en consola (monitoreo en tiempo real) y en `pipeline.log` (historial persistente). Cada mensaje muestra hora UTC y hora Monterrey (CDT = UTC-6) en paralelo.

**Importación de módulos:**
Agrega `app/` a `sys.path` con `insert(0, ...)` antes de cualquier import del proyecto, garantizando que Python encuentra `config.py`, `ingest.py` y `hana_client.py` en la carpeta correcta.

---

### `manifest.yml` — instrucciones de deploy para Cloud Foundry

**Propósito:** decirle a Cloud Foundry cómo desplegar y ejecutar la aplicación.

**Configuración clave:**
- `command: python pipeline_loop.py` — el mismo comando que usas localmente
- `health-check-type: process` — CF verifica que el proceso Python sigue vivo (no HTTP)
- `memory: 512M`, `disk_quota: 1G` — recursos suficientes para el pipeline con pandas
- `instances: 1` — una sola instancia (múltiples harían ingesta duplicada)
- Variables de entorno declaradas vacías — los valores reales se configuran con `cf set-env`

**Cómo usarlo:**
```bash
cf login
cf push
```
CF lee el `manifest.yml`, instala `requirements.txt`, y lanza `pipeline_loop.py`. A partir de ahí el pipeline corre en la nube sin depender de que la laptop esté encendida.

---

### `prueba_health.py` y `prueba_info.py` — scripts de diagnóstico

Scripts de verificación independientes usados antes de correr el pipeline. `prueba_health.py` verifica que el servidor responde (`GET /health`, sin auth). `prueba_info.py` verifica que el Bearer token es válido (`GET /info`) y muestra las dimensiones de la ventana actual.

---

## 6. Estado actual de la integración con SAP HANA

### Lo que ya está hecho
- Instancia de SAP HANA Cloud creada en SAP BTP Trial (plan `hana-free`)
- Credenciales de conexión obtenidas y configuradas en `.env` local
- `hana_client.py` implementado con las dos tablas (`RAW_LOGS_SISTEMA`, `RAW_LOGS_LLM`)
- `ingest.py` actualizado para llamar a `insert_logs()` en cada ciclo

### Lo que falta verificar
El paso inmediato pendiente es correr la prueba de conexión:

```bash
pip install hdbcli
pip freeze > requirements.txt
python app/hana_client.py
```

Si devuelve `✅ HANA lista para recibir datos`, la conexión está establecida y el pipeline escribe en HANA desde el próximo ciclo.

### Cómo accede el equipo a los datos en HANA

**Desde Python (AI Specialist, Data Architect):**
```python
from hdbcli import dbapi
conn = dbapi.connect(address=HANA_HOST, port=443, user=HANA_USER, 
                     password=HANA_PASSWORD, encrypt=True,
                     sslValidateCertificate=False)
cursor = conn.cursor()
cursor.execute("SELECT * FROM RAW_LOGS_SISTEMA WHERE log_type = 'SECURITY'")
df = pd.DataFrame(cursor.fetchall())
```

**Desde el Cockpit (SQL Console):**
En SAP BTP → HANA Cloud Central → Open in SAP HANA Database Explorer → New SQL Console → ejecutar queries directamente.

**Desde SAP Analytics Cloud (Viz Lead):**
SAC se conecta a HANA como fuente de datos live. Los dashboards leen directamente de las tablas sin exportar nada.

---

## 7. Variables de entorno requeridas

```
# API del hackathon
API_BASE_URL=https://sap-api-b4.674318.xyz
BEARER_TOKEN=[token del equipo]

# SAP HANA Cloud
HANA_HOST=[xxx].hanacloud.ondemand.com
HANA_PORT=443
HANA_USER=DBADMIN
HANA_PASSWORD=[contraseña definida al crear la instancia]

# Alerting (disponible Abr 27)
WEBHOOK_URL=
```

---

## 8. Flujo completo del sistema — de extremo a extremo

```
CADA 30 MINUTOS (automático):

  SAP API ──GET /info──► total_pages = 12
  SAP API ──GET /logs/current?page=1..12──► 5,551 registros JSON
                │
                ▼
          pd.DataFrame(all_records)
                │
                ├──► data/raw/logs_20260421T000000.csv  (backup siempre)
                │
                └──► SAP HANA Cloud
                       ├── INSERT INTO RAW_LOGS_SISTEMA (4,xxx filas)
                       └── INSERT INTO RAW_LOGS_LLM     (xxx filas)
                                    │
                                    ▼ (pendiente)
                           ETL + Modelo ML
                                    │
                                    ▼ (pendiente, Abr 27)
                           Webhook → SAP AI Security Team
                                    │
                                    ▼
                           Threat Resolved ✅
```

---

## 9. Lo que falta construir

### Inmediato
- Verificar conexión HANA con `python app/hana_client.py`
- Correr `pipeline_loop.py` con HANA activo y confirmar inserciones

### Antes del 27 de Abril
- Recibir webhook URL de los organizadores
- Implementar `alerting.py` con `send_alert()` funcional

### Antes del 4 de Mayo (Go Live)
- Instalar CF CLI: `brew install cloudfoundry/tap/cf-cli` (Mac) o equivalente
- Configurar variables en Cloud Foundry: `cf set-env sap-ai-soc BEARER_TOKEN ...`
- Deploy: `cf push`
- Verificar que el pipeline corre en la nube con `cf logs sap-ai-soc --recent`

### Pendiente del AI Specialist
- `app/model.py` — modelo de detección de anomalías sobre `clean_sistema.csv` y `clean_llm.csv`
- Feature engineering sobre columnas clave de seguridad

### Pendiente del Viz Lead
- `app/main.py` — dashboard Streamlit con datos de `data/processed/`
- Dashboard en SAP Analytics Cloud conectado a las tablas HANA

---

## 10. Reglas y protocolos del repositorio

**Seguridad crítica:** si `.env` o el Bearer token se suben a GitHub, el equipo queda penalizado con bloqueo al día siguiente. Verificar siempre con `git status` antes de `git push`.

**Flujo diario:**
```bash
git checkout develop
git pull origin develop
# ... trabajar ...
git status    # verificar que .env no aparece
git add .
git commit -m "tipo: descripción"
git push origin develop
```

**Datos:** los archivos `data/raw/` y `data/processed/` no van a Git (están en `.gitignore`). Son regenerables corriendo el pipeline.

**Ramas:** trabajar siempre en `develop`. `main` solo recibe código verificado y demo-ready antes de la eliminatoria.

---

*Reporte generado el 22 de Abril 2026 · Cloud Integration Engineer*  
*Actualizar conforme avance el proyecto*
