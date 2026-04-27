# Reporte Técnico Consolidado — Estado Actual del Proyecto
## SAP AI Security Anomaly Detection Hackathon · TEC de Monterrey × SAP
**Fecha de reporte:** 27 de Abril 2026  
**Autor:** Cloud Integration Engineer  
**Estado actual:** Pipeline en producción en SAP BTP Cloud Foundry · HANA operativo con datos acumulados · Alerting y modelo ML pendientes

---

## Parte 1 — Contexto del hackathon y criterios de éxito

### El reto

El hackathon pide construir un **Security Operations Center (SOC) en vivo** para sistemas SAP. No es un proyecto de IA aislada — es un pipeline completo de extremo a extremo que conecta datos, inteligencia y acción siguiendo cuatro pilares:

```
OBSERVE → ANALYZE → DETECT → RESPOND
```

El enfoque de detección es **aprendizaje no supervisado** porque no hay etiquetas previas de "esto es un ataque" — el sistema aprende qué es comportamiento normal y alerta cuando algo se desvía significativamente de ese patrón. Los ejemplos en la presentación oficial muestran dos tipos de anomalía: **Count Anomaly** (volumen inusual de logs en una ventana temporal) y **Categorization Anomaly** (aparición de templates de mensajes que el modelo nunca vio durante su entrenamiento).

### Criterios de evaluación

| Criterio | Peso | Métrica principal |
|---|---|---|
| Operational Efficiency & Real-Time Response | **40%** | MTTD (Mean Time to Detect), Alert Latency, automatización end-to-end |
| SAP Ecosystem Integration & Tooling | **25%** | Uso real de BTP, Cloud Foundry, HANA, SAC |
| Architecture & MLOps Maturity | **20%** | Escalabilidad, robustez, separación de componentes |
| Business Impact & Strategic Analysis | **15%** | Reporte forense, storytelling ejecutivo |

El criterio #1 (40%) es el que más depende de la infraestructura construida hasta ahora. El polling cada 1-2 minutos que tenemos implementado reduce el MTTD teórico de ~30 minutos a ~1-2 minutos — impacto directo en ese 40%.

### Fechas críticas

| Fecha | Hito | Estado |
|---|---|---|
| Abr 6 | Kick Off | ✅ |
| Abr 13 | API & Data Access | ✅ |
| Abr 20–21 | Pipeline local automatizado + HANA local | ✅ |
| Abr 22 | HANA corregido + preparación CF | ✅ |
| Abr 24 | Deploy a Cloud Foundry + polling continuo | ✅ |
| **Abr 27** | **Alerting Webhook disponible** | ⏳ HOY |
| **May 4** | **Go Live (deadline oficial CF)** | ⏳ |
| May 12–14 | Primera Fase Eliminatoria | ⏳ |
| May 21 | Final Phase | ⏳ |

---

## Parte 2 — Infraestructura SAP — mapa completo

### Jerarquía de SAP BTP

```
SAP BTP Global Account
  └── Subaccount "Trial"
        ├── Cloud Foundry Environment
        │     └── Space "Dev"
        │           └── App: sap-ai-soc  ← pipeline corriendo 24/7
        │                 ├── pipeline_loop.py (polling cada 1-2 min)
        │                 ├── server.py (endpoints /health y /run)
        │                 ├── Service Binding → HANA Cloud (VCAP_SERVICES)
        │                 └── Service Binding → XSUAA
        │
        └── SAP HANA Cloud (instancia hna1.prod, plan hana-free)
              ├── DBADMIN.RAW_LOGS_SISTEMA  ← logs INFO/WARNING/ERROR/SECURITY/etc.
              ├── DBADMIN.RAW_LOGS_LLM      ← logs LLM_REQUEST/LLM_ERROR/LLM_TIMEOUT
              └── DBADMIN.ALERTS            ← registro único de alertas detectadas
```

**SAP BTP** es la plataforma cloud completa de SAP. Todo vive aquí.

**Cloud Foundry** es el runtime de ejecución. `cf push` sube el código y CF lo mantiene corriendo 24/7, reiniciándolo si muere, sin necesidad de ninguna laptop encendida.

**SAP HANA Cloud** es la base de datos relacional en la nube. No es un archivo — es una instancia de base de datos completa con SQL, índices, transacciones y acceso concurrente. Todo el equipo puede consultar los datos acumulados con las mismas credenciales.

**VCAP_SERVICES** es un mecanismo de Cloud Foundry. Cuando haces un Service Binding entre la app y HANA, CF inyecta automáticamente las credenciales de conexión como variable de entorno JSON (`VCAP_SERVICES`). La app las lee desde ahí en vez de desde `.env`. Esto garantiza que las credenciales nunca toquen el código ni el repositorio.

**SAP Analytics Cloud (SAC)** — pendiente de conexión por el Viz Lead. Se conecta directamente a HANA como fuente de datos live para dashboards ejecutivos.

---

## Parte 3 — La API SAP — fuente de todos los datos

### Endpoints

| Endpoint | Auth | Propósito |
|---|---|---|
| `GET /health` | ❌ No | Liveness probe — responde `{"status":"ok"}` |
| `GET /info` | ✅ Bearer | Metadata de ventana: `total_pages`, `total_records`, `batch_size`, `window_start`, `window_end` |
| `GET /logs/current?page=N` | ✅ Bearer | Logs de la ventana activa, paginados (500 registros/página) |

**URL base confirmada:** `https://sap-api-b4.674318.xyz`  
**Autenticación:** `Authorization: Bearer <team-token>`  
**Batch size:** 500 registros por página (fijo por el servidor — no configurable)

### Comportamiento temporal

La API NO sirve datos históricos. Siempre devuelve la ventana UTC de 30 minutos activa en el momento de la llamada. La ventana se determina por el reloj del servidor:

```
Minutos UTC 00–29  →  ventana HH:00:00 → HH:30:00
Minutos UTC 30–59  →  ventana HH:30:00 → HH+1:00:00
```

**Consecuencia crítica:** si no ingestas una ventana, esos ~5,500 registros desaparecen para siempre. El polling continuo cada 1-2 minutos garantiza que capturamos todos los registros de cada ventana conforme aparecen.

### Estructura de respuesta de `/logs/current`

```json
{
  "request_time_utc": "2026-04-24T12:01:15.000Z",
  "window_start":     "2026-04-24T12:00:00+00:00",
  "window_end":       "2026-04-24T12:30:00+00:00",
  "total_records":    5551,
  "batch_size":       500,
  "current_page":     1,
  "total_pages":      12,
  "records_in_page":  500,
  "data": [ ... array de objetos JSON ... ]
}
```

### Schema de los logs — 43 columnas, dos tipos

**Columna discriminante:** `sap_function_log_type`

| Categoría | Valores | Columnas vacías |
|---|---|---|
| **Sistema** | `INFO`, `WARNING`, `ERROR`, `DEBUG`, `AUDIT`, `PERF`, `SECURITY` | Todas las `llm_*` |
| **LLM** | `LLM_REQUEST`, `LLM_ERROR`, `LLM_TIMEOUT` | `service_id`, `http_status_code`, `client_ip` |

Los nulos son por diseño de la API — no son errores de calidad. El sistema no intenta rellenarlos.

**Grupos de columnas:**
- Identificación: `_id` (primary key), `_index`, `_score`, `_ignored`
- Temporales: `@timestamp`, `@event_time_requested`, `@version`
- Clasificación: `sap_function_log_type`, `sap_source_type`, `sap_function_application`, `sap_app_env`, `sap_function_message`, `event_code_version`, `event_hash`
- Red/HTTP (Solo Sistema): `service_id`, `http_status_code`, `client_ip`, `headers_http_host`, `headers_http_request_method`, `headers_content_type`, `heathers_request_path` *(typo de la API — 'heathers' no 'headers', no corregir)*
- Región: `region_id`, `region_name`, `region_code`, `macro_region`
- LLM modelo (Solo LLM): `llm_model_id`, `llm_provider`, `llm_status`, `llm_error_message`, `llm_finish_reason`
- LLM prompt (Solo LLM): `llm_prompt_id`, `llm_prompt_category`, `llm_prompt`, `llm_stream`
- LLM métricas (Solo LLM): `llm_prompt_tokens`, `llm_completion_tokens`, `llm_total_tokens`, `llm_cost_usd`, `llm_response_time_ms`, `llm_response_size_bytes`, `llm_temperature`, `llm_top_p`
- SAP heredadas: `sap_llm_response_size`, `sap_llm_response_time`

---

## Parte 4 — Historia técnica completa: de cero hasta Cloud Foundry

Esta sección documenta el proceso exacto que siguió el CIE, incluyendo los problemas encontrados y cómo se resolvieron.

### Fase 1 — Setup inicial y primera conexión a la API (Abr 13–20)

**Lo que se hizo:**

Se creó el repositorio GitHub con la estructura de carpetas completa y se configuró el flujo de trabajo Git con las ramas `main` (estable) y `develop` (trabajo diario).

Se construyeron los primeros scripts de diagnóstico:
- `prueba_health.py` — verifica que el servidor SAP está vivo (`GET /health`, sin auth)
- `prueba_info.py` — verifica que el Bearer token es válido (`GET /info`) y muestra el tamaño de la ventana actual

Se confirmó la primera conexión exitosa:
```
Status: 200
Ventana: 2026-04-20T06:00:00 → 2026-04-20T06:30:00 UTC
Total registros: 5,729 | Páginas: 12
```

Se construyó `app/ingest.py` con el loop de paginación completo. La lógica crítica: primero llamar `GET /info` para saber cuántas páginas iterar, luego iterar `GET /logs/current?page=1..N` acumulando registros en una lista Python (no en DataFrame directamente — `pd.concat()` iterativo es O(n²), acumular en lista y hacer `pd.DataFrame()` una vez es O(n)).

Se construyó `pipeline_loop.py` con el loop de automatización sincronizado a ventanas UTC. El diseño clave: no se usa `time.sleep(1800)` fijo porque causa drift acumulativo. Se calcula el tiempo exacto hasta el próximo `:00` o `:30` UTC y se duerme ese tiempo preciso.

**Resultado:** primera extracción completa de 5,729 registros guardada en `data/raw/logs_20260420T060000.csv`. El loop corrió toda la noche del 20–21 de abril acumulando ventanas cada 30 minutos.

---

### Fase 2 — Integración con SAP HANA Cloud (Abr 21–22)

**Contexto: qué es una instancia de HANA y cómo se creó**

SAP HANA Cloud en BTP se crea desde el Service Marketplace del Cockpit. El plan correcto para trial es `hana-free`. La creación requiere un JSON de configuración:

```json
{
    "data": {
        "memory": 16,
        "systempassword": "<password-elegido>",
        "edition": "cloud"
    }
}
```

El aprovisionamiento tarda entre 5 y 15 minutos. Una vez completado, la instancia aparece como "Running" en el Cockpit.

Las credenciales de conexión se obtienen creando un **Service Key** en la instancia: Cockpit → instancia HANA → Service Bindings → Create Service Key. El sistema genera un JSON con `host`, `port`, `user` y `password`.

**Problema 1: error de SSL con `sslHostNameInCertificate`**

El primer intento de conexión con `hana_client.py` fallaba con error:
```
"Se especificaron marcas no válidas"
```

La causa: el parámetro `sslHostNameInCertificate="*.hanacloud.ondemand.com"` no es compatible con instancias `hna1.prod` del trial. **Solución:** eliminar ese parámetro. La conexión sigue cifrada con `encrypt=True` y `sslValidateCertificate=False`.

```python
# Antes (causaba error):
conn = dbapi.connect(
    address=HANA_HOST, port=int(HANA_PORT),
    user=HANA_USER, password=HANA_PASSWORD,
    encrypt=True, sslValidateCertificate=False,
    sslHostNameInCertificate="*.hanacloud.ondemand.com"  # ← esto causaba el error
)

# Después (funciona):
conn = dbapi.connect(
    address=HANA_HOST, port=int(HANA_PORT),
    user=HANA_USER, password=HANA_PASSWORD,
    encrypt=True, sslValidateCertificate=False
)
```

**Problema 2: `CREATE TABLE IF NOT EXISTS` no compatible con HANA Cloud trial**

La sintaxis `IF NOT EXISTS` no está disponible en la versión de HANA Cloud del trial. Fallaba con error de sintaxis. **Solución:** verificación manual contra el catálogo del sistema antes de crear:

```python
# Verificar existencia antes de crear
cursor.execute(
    "SELECT COUNT(*) FROM TABLES WHERE SCHEMA_NAME = 'DBADMIN' AND TABLE_NAME = ?",
    (nombre_tabla,)
)
existe = cursor.fetchone()[0] > 0
if not existe:
    cursor.execute(sql_create)
```

**El problema del usuario PIPELINE — proceso específico del CIE**

Aquí ocurrió un problema particular. Al intentar conectarse a HANA desde la aplicación desplegada en Cloud Foundry, el Service Binding de CF usa un usuario técnico distinto a `DBADMIN`. Este usuario técnico no tenía permisos para ver las tablas creadas en el schema de `DBADMIN`.

Para resolverlo, el CIE creó un usuario adicional llamado `PIPELINE_USER` directamente desde la **SAP HANA Database Explorer** (accesible desde el Cockpit → HANA Cloud Central → Open in HANA Database Explorer → SQL Console).

El proceso exacto en SQL Console:

```sql
-- Paso 1: crear el usuario
CREATE USER PIPELINE_USER PASSWORD "<password-elegido>" NO FORCE_FIRST_PASSWORD_CHANGE;

-- Paso 2: otorgar permisos de conexión
GRANT CREATE SESSION TO PIPELINE_USER;

-- Paso 3: otorgar permisos sobre las tablas del schema DBADMIN
GRANT SELECT, INSERT, UPDATE ON DBADMIN.RAW_LOGS_SISTEMA TO PIPELINE_USER;
GRANT SELECT, INSERT, UPDATE ON DBADMIN.RAW_LOGS_LLM TO PIPELINE_USER;
GRANT SELECT, INSERT, UPDATE ON DBADMIN.ALERTS TO PIPELINE_USER;
```

Este usuario `PIPELINE_USER` se configuró como el usuario de la aplicación en `config.py` para el entorno de Cloud Foundry, mientras que el CIE continuó usando `DBADMIN` en local.

**Problema 3: rendimiento de inserción — de 9 minutos a 16 segundos**

La primera versión de `insert_logs()` usaba `MERGE INTO` fila por fila para evitar duplicados. Con 5,500 registros, cada `MERGE INTO` es una transacción SQL separada — resultado: ~9 minutos por ciclo. Completamente inviable para polling cada 1-2 minutos.

**Solución — SELECT previo + executemany() en batch:**

```python
# 1. Obtener IDs ya existentes en HANA (una sola query)
cursor.execute("SELECT log_id FROM DBADMIN.RAW_LOGS_SISTEMA")
ids_existentes = {row[0] for row in cursor.fetchall()}

# 2. Filtrar en Python (O(1) por lookup en set)
df_nuevos = df[~df["_id"].isin(ids_existentes)]

# 3. Insertar todos los nuevos en una sola operación
cursor.executemany(sql_insert, lista_de_tuplas)
conn.commit()
```

`executemany()` envía todas las filas al servidor en una sola operación de red — 5,500 inserciones en ~16 segundos vs ~9 minutos con MERGE individual.

**Índices UNIQUE creados en SQL Console para eficiencia:**

```sql
CREATE UNIQUE INDEX IDX_SIS_LOGID ON DBADMIN.RAW_LOGS_SISTEMA (log_id);
CREATE UNIQUE INDEX IDX_LLM_LOGID ON DBADMIN.RAW_LOGS_LLM (log_id);
```

**Tabla ALERTS creada:**

```sql
CREATE TABLE DBADMIN.ALERTS (
    alert_id         NVARCHAR(100) PRIMARY KEY,
    log_id           NVARCHAR(100),
    detected_at      TIMESTAMP,
    detection_source NVARCHAR(20),   -- 'quick_filter' o 'model_ml'
    alert_type       NVARCHAR(100),
    severity         NVARCHAR(10),   -- 'low', 'medium', 'high', 'critical'
    details          NVARCHAR(2000),
    window_start     TIMESTAMP,
    alerted          TINYINT DEFAULT 0  -- 0=no enviada, 1=enviada al webhook
);

CREATE INDEX IDX_ALERT_LOGID ON DBADMIN.ALERTS (log_id);
```

Esta tabla es el mecanismo anti-duplicados para alertas. Tanto el filtro rápido (cada 1-2 min) como el modelo ML (cada 30 min) verifican si ya existe una alerta para un evento antes de crear una nueva. El campo `alerted` sirve para saber si ya se envió el webhook.

---

### Fase 3 — Deduplicación en memoria y polling continuo (Abr 23–24)

**Rediseño arquitectónico: de ventanas de 30 min a polling cada 1-2 min**

El diseño original del pipeline ejecutaba un ciclo completo cada 30 minutos, sincronizado al inicio de cada ventana UTC. Esto garantizaba no perder datos pero implicaba un MTTD máximo de ~30 minutos (si un ataque ocurría justo después del inicio de ventana, el sistema no lo detectaría hasta el siguiente ciclo).

El rediseño usa polling cada 1-2 minutos sobre la misma ventana activa. La ventana sigue siendo de 30 minutos, pero el sistema la consulta continuamente. En cada ciclo, la API devuelve todos los registros de la ventana activa — incluyendo los que ya insertamos anteriormente. El problema: ¿cómo evitar insertar el mismo registro dos veces?

**Deduplicación en dos capas:**

**Capa 1 — En memoria (rápida, O(1)):**
```python
# Variables de estado en ingest.py
ids_procesados = set()    # set de _id ya vistos en esta ventana
ventana_anterior = None   # para detectar cambio de ventana

def ingest_and_persist():
    global ids_procesados, ventana_anterior
    
    df, meta = fetch_current_window()
    window_start = meta["window_start"]
    
    # Si cambió la ventana, resetear el set de IDs
    if window_start != ventana_anterior:
        ids_procesados = set()
        ventana_anterior = window_start
    
    # Filtrar solo los registros que no hemos visto
    df_nuevos = df[~df["_id"].isin(ids_procesados)]
    
    # Actualizar el set con los IDs de este ciclo
    ids_procesados.update(df["_id"].tolist())
    
    # Solo insertar los nuevos en HANA
    if not df_nuevos.empty:
        upsert_logs(df_nuevos)
```

**Capa 2 — En HANA (segura, cubre reinicios):**
Si CF reinicia el proceso, `ids_procesados` se vacía. La segunda capa (SELECT de IDs existentes en HANA antes de insertar) detecta los duplicados que la capa 1 no vio por el reinicio.

**Verificación de que HANA está disponible antes de cada ciclo:**

```python
def hana_esta_viva() -> bool:
    """Verifica conexión ejecutando SELECT 1 FROM DUMMY. Retorna True/False."""
    try:
        conn = get_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT 1 FROM DUMMY")
        conn.close()
        return True
    except Exception:
        return False
```

Si HANA no está disponible, el ciclo guarda en CSV y continúa — no muere.

**Rendimiento medido en pruebas:**

```
OPERACIÓN                         TIEMPO
─────────────────────────────     ──────────
Extracción API (12 páginas)       30–60 seg
Deduplicación en memoria          < 1 seg
Verificación HANA (SELECT IDs)    1–2 seg
INSERT masivo (executemany)       5–10 seg
─────────────────────────────     ──────────
Ciclo con ventana nueva           ~1–1.5 min
Ciclos siguientes (pocos nuevos)  ~40–60 seg
Ciclo sin nuevos registros        ~10 seg

Comparación de métodos de inserción:
  MERGE fila por fila             ~9 min para 5,500 registros
  SELECT + executemany batch      ~16 seg para 5,500 registros
  Factor de mejora:               ~34x más rápido
```

---

### Fase 4 — Deploy a Cloud Foundry (Abr 24)

**Preparación del proyecto para CF:**

Se crearon dos archivos nuevos:

`runtime.txt` — especifica la versión de Python para el buildpack:
```
python-3.11.x
```

`server.py` — wrapper Flask con endpoints de monitoreo:
- `GET /health` → responde `{"status":"running"}` — usado como liveness probe
- `POST /run` → ejecuta un ciclo manual del pipeline (protegido con `JOB_SECRET_TOKEN`)

`requirements.txt` se simplificó para CF (solo lo necesario para el pipeline, sin dependencias de desarrollo como streamlit o matplotlib):
```
Flask
requests
pandas
numpy
hdbcli
python-dotenv
scikit-learn
```

`requirements-dev.txt` mantiene todas las dependencias para desarrollo local.

**Lectura dual de credenciales HANA en `config.py`:**

En local, las credenciales HANA vienen de `.env`. En CF, vienen de `VCAP_SERVICES` (JSON inyectado automáticamente por el Service Binding). La función `_load_hana_creds()` detecta automáticamente el entorno:

```python
def _load_hana_creds():
    """Lee credenciales HANA de VCAP_SERVICES (CF) o .env (local)."""
    vcap = os.getenv("VCAP_SERVICES")
    if vcap:
        # Estamos en Cloud Foundry
        servicios = json.loads(vcap)
        # HANA aparece bajo la clave "hana" o "hanatrial" según el plan
        hana_creds = servicios.get("hana", servicios.get("hanatrial", [{}]))[0]
        credenciales = hana_creds.get("credentials", {})
        return {
            "host":     credenciales.get("host"),
            "port":     credenciales.get("port", 443),
            "user":     credenciales.get("user"),
            "password": credenciales.get("password"),
        }
    else:
        # Estamos en local — usar .env
        return {
            "host":     os.getenv("HANA_HOST"),
            "port":     os.getenv("HANA_PORT", 443),
            "user":     os.getenv("HANA_USER"),
            "password": os.getenv("HANA_PASSWORD"),
        }
```

**`manifest.yml` actualizado para CF:**

```yaml
applications:
  - name: sap-ai-soc
    random-route: true
    path: ./
    memory: 512M
    disk_quota: 1G
    instances: 1
    buildpacks:
      - python_buildpack
    command: python pipeline_loop.py
    health-check-type: process
    services:
      - nombre-instancia-hana   # verifica con: cf services
```

`health-check-type: process` (no `http`) porque la app es un loop infinito, no un servidor web. CF verifica que el proceso Python sigue corriendo.

**Proceso de deploy — comandos ejecutados:**

```bash
# 1. Login a Cloud Foundry
cf login -a https://api.cf.[region].hana.ondemand.com

# 2. Verificar target correcto
cf target
# debe mostrar: org=Trial, space=Dev

# 3. Verificar que el Service Binding de HANA está disponible
cf services
# debe aparecer la instancia HANA

# 4. Deploy
cf push

# 5. Configurar credenciales (NUNCA en el código o manifest)
cf set-env sap-ai-soc BEARER_TOKEN "token-real"
cf set-env sap-ai-soc API_BASE_URL "https://sap-api-b4.674318.xyz"
cf set-env sap-ai-soc JOB_SECRET_TOKEN "token-generado-para-proteger-/run"

# 6. Restage para aplicar las variables de entorno
cf restage sap-ai-soc

# 7. Verificar que arrancó correctamente
cf logs sap-ai-soc --recent

# 8. Verificar estado de la app
cf app sap-ai-soc
```

**Por qué `pipeline_loop.py` como entry point y no `server.py` + Job Scheduler:**

El Job Scheduling Service de BTP en plan free tiene un intervalo mínimo de 1 hora. El polling cada 1-2 minutos requiere un proceso continuo. `pipeline_loop.py` mantiene el control del timing internamente con `time.sleep(INTERVALO_POLLING)`.

**Pruebas de deploy realizadas:**

| Prueba | Resultado |
|---|---|
| `cf push` | ✅ App desplegada |
| `cf set-env` + `cf restage` | ✅ Credenciales configuradas |
| `cf logs --recent` | ✅ Pipeline corriendo con ciclos cada 1-2 min |
| Verificación en SQL Console HANA | ✅ Datos insertados correctamente |
| `POST /run` con token correcto | ✅ Ciclo manual ejecutado |
| `POST /run` sin token | ✅ 401 Unauthorized |

---

## Parte 5 — Estado actual del repositorio

### Estructura completa

```
sap-security-hackathon/
│
├── pipeline_loop.py         ← entry point CF · polling cada 1-2 min ✅
├── server.py                ← Flask · /health y /run · monitoreo manual ✅
├── manifest.yml             ← config deploy Cloud Foundry ✅
├── runtime.txt              ← python-3.11.x para buildpack CF ✅
├── requirements.txt         ← dependencias prod para CF ✅
├── requirements-dev.txt     ← dependencias completas local ✅
├── pipeline.log             ← historial de ejecuciones (autogenerado)
├── prueba_health.py         ← diagnóstico: servidor vivo ✅
├── prueba_info.py           ← diagnóstico: token válido ✅
├── .env                     ← credenciales locales (NUNCA en Git) ✅
├── .env.examples            ← plantilla vacía (sí en Git) ✅
├── .gitignore               ✅
├── README.md                ✅
│
├── app/
│   ├── config.py            ← lectura dual .env / VCAP_SERVICES ✅
│   ├── ingest.py            ← extracción + deduplicación + upsert ✅
│   ├── hana_client.py       ← conexión HANA + upsert_logs + hana_esta_viva ✅
│   ├── etl.py               ← limpieza y transformación ✅
│   ├── explore.py           ← análisis exploratorio ✅
│   ├── quick_filter.py      ← reglas determinísticas ⏳ PENDIENTE
│   ├── model.py             ← detección ML ⏳ PENDIENTE (AI Specialist)
│   ├── alerting.py          ← webhook dispatch ⏳ PENDIENTE (hoy Abr 27)
│   ├── main.py              ← dashboard Streamlit ⏳ PENDIENTE (Viz Lead)
│   └── __init__.py
│
├── data/
│   ├── raw/                 ← CSVs por ventana (no en Git, en .gitignore)
│   ├── processed/           ← datasets limpios (no en Git)
│   └── SCHEMA.md            ← documentación de 43 columnas ✅
│
├── docs/
│   ├── TEAM_BRIEFING_cloud_integration_v2.md ✅
│   ├── GIT_WORKFLOW_guide.md ✅
│   └── PROJECT_CONTEXT_shared.md ✅
│
├── notebooks/               ← exploración (AI Specialist)
└── tests/
```

### Estado de las tablas en HANA

| Tabla | Schema | Estado | Índices |
|---|---|---|---|
| `RAW_LOGS_SISTEMA` | `DBADMIN` | ✅ Datos acumulados | `IDX_SIS_LOGID` (UNIQUE) |
| `RAW_LOGS_LLM` | `DBADMIN` | ✅ Datos acumulados | `IDX_LLM_LOGID` (UNIQUE) |
| `ALERTS` | `DBADMIN` | ✅ Creada, vacía | `IDX_ALERT_LOGID` |

---

## Parte 6 — Descripción técnica de cada módulo

### `app/config.py`

**Responsabilidad única:** leer credenciales y configuración del entorno y exponerlas al resto del proyecto. Es el único archivo que sabe de dónde vienen las credenciales.

**Inputs:** archivo `.env` (local) o variable `VCAP_SERVICES` (Cloud Foundry).

**Outputs:** variables globales Python (`API_BASE_URL`, `BEARER_TOKEN`, `HANA_HOST`, etc.) y funciones `validate_config()` y `get_headers()`.

**Lógica clave:**
- `load_dotenv(dotenv_path=ruta_absoluta)` — usa la ruta absoluta calculada desde la ubicación de `config.py` para encontrar el `.env` sin importar desde dónde se importe el módulo.
- `_load_hana_creds()` — detecta automáticamente si está en CF (VCAP_SERVICES existe) o local (.env).
- `validate_config()` — verifica que `API_BASE_URL` y `BEARER_TOKEN` están presentes antes de cualquier llamada HTTP. Falla rápido con mensaje que muestra exactamente qué variable falta y dónde buscó el `.env`.
- `get_headers()` — construye el header Bearer en cada llamada, no como variable estática (para que funcione si el token se rota).

---

### `app/hana_client.py`

**Responsabilidad única:** toda la interacción con SAP HANA Cloud. Conexión, creación de tablas, inserción de datos. Ningún otro módulo habla directamente con HANA.

**Funciones principales:**

`get_connection()` — crea una conexión hdbcli con:
- `encrypt=True` — obligatorio para HANA Cloud
- `sslValidateCertificate=False` — necesario en trial (certificado wildcard)
- Sin `sslHostNameInCertificate` — eliminado porque causaba error con instancias `hna1.prod`

`create_tables()` — crea `RAW_LOGS_SISTEMA`, `RAW_LOGS_LLM` y `ALERTS` verificando contra el catálogo del sistema antes de ejecutar el CREATE (porque `IF NOT EXISTS` no es compatible con esta versión de HANA Cloud trial).

`upsert_logs(df)` — reemplaza a la antigua `insert_logs()`:
1. Llama `hana_esta_viva()` primero
2. Obtiene IDs existentes con un SELECT masivo
3. Filtra en Python con set lookup (O(1) por elemento)
4. Inserta solo los nuevos con `executemany()` en batch
5. Usa schema explícito `DBADMIN.RAW_LOGS_SISTEMA` para que funcione con cualquier usuario conectado

`hana_esta_viva()` — ejecuta `SELECT 1 FROM DUMMY`, retorna `True`/`False` sin lanzar excepciones. Si HANA está pausada por inactividad (plan trial se pausa si no hay actividad), esto lo detecta y el pipeline continúa sin HANA.

**Funciones auxiliares `_safe_*()`** — convierten valores pandas (que usan `float('nan')` para nulos) a `None` (que es lo que hdbcli espera para insertar NULL en HANA). Sin estas funciones, cada NaN causaría un error de tipo en el INSERT.

---

### `app/ingest.py`

**Responsabilidad:** extraer datos de la API, deduplicar, y persistir en CSV + HANA.

**Variables de estado (nivel módulo):**
```python
ids_procesados = set()    # IDs procesados en la ventana actual
ventana_anterior = None   # para detectar cambio de ventana
```

**`fetch_current_window()`** — loop paginado:
1. `GET /info` → descubrir `total_pages`
2. Iterar `GET /logs/current?page=1..N`, acumular en lista Python
3. `pd.DataFrame(all_records)` — conversión única al final
4. Manejo específico de 422 (página fuera de rango — ventana cambió durante extracción)

**`save_data(df, meta)`** — usa rutas absolutas calculadas desde `__file__` para que los archivos se creen siempre en el lugar correcto sin importar desde dónde se llame el módulo (problema que ocurría cuando `pipeline_loop.py` importaba `ingest.py` desde la raíz).

**`ingest_and_persist()`** — función orquestadora:
1. Llama `fetch_current_window()`
2. Detecta cambio de ventana → resetea `ids_procesados`
3. Filtra registros nuevos
4. Guarda CSV (siempre)
5. Si HANA está disponible → llama `upsert_logs(df_nuevos)`
6. Retorna dict con resultado del ciclo

---

### `pipeline_loop.py`

**Responsabilidad:** orquestar el loop de polling continuo.

**Cambio principal respecto a la versión original:** intervalo fijo de `INTERVALO_POLLING = 1 * 60` (60 segundos) en lugar del cálculo de tiempo hasta la próxima ventana UTC. Esto permite polling frecuente sobre la ventana activa.

La función `calcular_segundos_hasta_proxima_ventana()` se mantiene en el código pero está reservada para uso futuro del modelo ML (que necesita saber cuándo empieza una nueva ventana para correr el análisis profundo de la ventana completa).

**`ejecutar_ciclo_ingesta()`** — llama a `ingest_and_persist()` (que maneja todo internamente) y clasifica errores: recuperables (timeout, conexión, HTTP 5xx) retornan `False` para reintentar, fatales (HTTP 401) llaman `sys.exit(1)`.

---

### `server.py`

**Responsabilidad:** exponer endpoints HTTP para monitoreo y control manual.

**`GET /health`** — liveness probe. Retorna `{"status": "running"}`. Usado por CF para verificar que la app está viva, y disponible para pruebas externas.

**`POST /run`** — dispara un ciclo manual del pipeline. Protegido con token en header `X-Job-Token`. Útil para probar la conexión HANA sin esperar el próximo ciclo de polling.

---

## Parte 7 — Flujo completo del sistema — estado actual

```
CADA 1-2 MINUTOS (pipeline_loop.py en Cloud Foundry):

  SAP API
    │  GET /info → total_pages
    │  GET /logs/current?page=1..12 → ~5,500 registros JSON
    ▼
  fetch_current_window()                         ~30-60 seg
    │
    ▼
  Deduplicación capa 1 (ids_procesados set)      ~instantáneo
    │ ¿ventana nueva? → reset ids_procesados
    │ filtrar df_nuevos = solo IDs no vistos
    ▼
  save_data() → data/raw/logs_[timestamp].csv    ~1 seg
    │
    ▼
  hana_esta_viva()                               ~1 seg
    │
    ├── False → loguear + continuar solo con CSV
    │
    └── True
          │
          ▼
        Deduplicación capa 2                     ~1-2 seg
        (SELECT log_id → filtrar en Python)
          │
          ▼
        executemany() INSERT df_nuevos           ~5-10 seg
          ├── DBADMIN.RAW_LOGS_SISTEMA
          └── DBADMIN.RAW_LOGS_LLM
          │
          ▼
        quick_filter(df_nuevos)   ← ⏳ PENDIENTE
          │
          ├── Amenaza detectada
          │       │
          │       ▼
          │   INSERT DBADMIN.ALERTS
          │       │
          │       ▼
          │   alerting.py → Webhook  ← ⏳ PENDIENTE (hoy Abr 27)
          │
          └── Sin amenaza → continúa


CADA 30 MINUTOS (al detectar cambio de ventana):

  ETL sobre ventana completa en HANA  ← ⏳ PENDIENTE
    │
    ▼
  model.py (análisis profundo ML)     ← ⏳ PENDIENTE (AI Specialist)
    │
    ├── Anomalía → verificar ALERTS → webhook si es nueva
    └── Normal → datos para re-entrenamiento
```

---

## Parte 8 — Variables de entorno — referencia completa

### En local (`.env`)

```
# API del hackathon
API_BASE_URL=https://sap-api-b4.674318.xyz
BEARER_TOKEN=[token del equipo]

# SAP HANA Cloud
HANA_HOST=[xxx].hanacloud.ondemand.com
HANA_PORT=443
HANA_USER=DBADMIN
HANA_PASSWORD=[contraseña definida al crear la instancia]

# Seguridad server.py
JOB_SECRET_TOKEN=[token generado localmente]

# Alerting (llega hoy Abr 27)
WEBHOOK_URL=
```

### En Cloud Foundry (configuradas con `cf set-env`)

| Variable | Configuración | Origen |
|---|---|---|
| `HANA_HOST/PORT/USER/PASSWORD` | Automático via Service Binding | `VCAP_SERVICES` |
| `BEARER_TOKEN` | `cf set-env sap-ai-soc BEARER_TOKEN ...` | Manual, una sola vez |
| `API_BASE_URL` | `cf set-env sap-ai-soc API_BASE_URL ...` | Manual, una sola vez |
| `JOB_SECRET_TOKEN` | `cf set-env sap-ai-soc JOB_SECRET_TOKEN ...` | Manual, una sola vez |

Ninguna credencial existe en el repositorio. `.env` está en `.gitignore`.

---

## Parte 9 — Queries útiles para el equipo en SQL Console HANA

```sql
-- Conteos actuales
SELECT COUNT(*) AS total FROM DBADMIN.RAW_LOGS_SISTEMA;
SELECT COUNT(*) AS total FROM DBADMIN.RAW_LOGS_LLM;

-- Registros más recientes
SELECT TOP 10 log_id, event_timestamp, ingested_at, log_type, client_ip
FROM DBADMIN.RAW_LOGS_SISTEMA
ORDER BY ingested_at DESC;

-- Solo logs de seguridad (los más relevantes para detección)
SELECT TOP 20 log_id, event_timestamp, log_type, client_ip, http_status, request_path
FROM DBADMIN.RAW_LOGS_SISTEMA
WHERE log_type = 'SECURITY'
ORDER BY event_timestamp DESC;

-- Verificar que no hay duplicados
SELECT log_id, COUNT(*) AS veces
FROM DBADMIN.RAW_LOGS_SISTEMA
GROUP BY log_id
HAVING COUNT(*) > 1;

-- Volumen por ciclo de ingesta (cuántos registros entran por minuto)
SELECT
    CAST(ingested_at AS DATE) AS fecha,
    HOUR(ingested_at) AS hora,
    MINUTE(ingested_at) AS minuto,
    COUNT(*) AS registros_nuevos
FROM DBADMIN.RAW_LOGS_SISTEMA
GROUP BY CAST(ingested_at AS DATE), HOUR(ingested_at), MINUTE(ingested_at)
ORDER BY fecha DESC, hora DESC, minuto DESC;

-- Distribución de tipos de log LLM
SELECT log_type, llm_status, COUNT(*) AS total
FROM DBADMIN.RAW_LOGS_LLM
GROUP BY log_type, llm_status
ORDER BY total DESC;

-- Errores LLM con mensaje (útil para anomalías)
SELECT TOP 20 event_timestamp, llm_model_id, llm_provider, llm_error_message
FROM DBADMIN.RAW_LOGS_LLM
WHERE log_type = 'LLM_ERROR'
ORDER BY event_timestamp DESC;
```

### Monitoreo del pipeline en CF

```bash
# Ver logs en tiempo real
cf logs sap-ai-soc

# Ver últimos logs (sin seguir)
cf logs sap-ai-soc --recent

# Estado de la app (memoria, CPU, uptime)
cf app sap-ai-soc

# Variables de entorno configuradas
cf env sap-ai-soc

# Reiniciar la app si es necesario
cf restart sap-ai-soc
```

---

## Parte 10 — Decisiones de diseño documentadas

**¿Por qué polling cada 1-2 min en vez de sincronizar a ventanas de 30 min?**
La detección frecuente reduce el MTTD de ~30 minutos a ~1-2 minutos. Impacto directo en el criterio #1 (40% de la nota). La deduplicación resuelve el problema de consultar la misma ventana múltiples veces.

**¿Por qué SELECT + executemany() en vez de MERGE INTO fila por fila?**
MERGE fila por fila: ~9 minutos para 5,500 registros. SELECT + executemany batch: ~16 segundos. Factor ~34x de mejora. Con `instances: 1` en CF no hay riesgo de concurrencia que justifique el costo del MERGE.

**¿Por qué deduplicación en dos capas (memoria + HANA)?**
La capa en memoria (set de Python) es la rápida — O(1) por lookup. La capa HANA (SELECT de IDs) es la de seguridad — cubre el caso de reinicio de CF donde el set en memoria se vacía. Las dos capas juntas garantizan cero duplicados en cualquier escenario.

**¿Por qué `pipeline_loop.py` como entry point en CF y no `server.py` + Job Scheduler?**
El Job Scheduling Service de BTP en plan free tiene intervalo mínimo de 1 hora. El polling cada 1-2 minutos requiere un proceso continuo propio.

**¿Por qué dos tablas HANA en vez de una?**
Sistema y LLM tienen columnas completamente distintas. Una tabla tendría ~50% de NULLs por fila, desperdiciando espacio. Dos tablas optimizan queries del modelo ML y simplifican el schema.

**¿Por qué schema explícito `DBADMIN.tabla`?**
En CF el Service Binding usa un usuario técnico distinto a DBADMIN. Sin schema explícito, HANA busca las tablas en el schema del usuario técnico y no las encuentra.

**¿Por qué `PIPELINE_USER` adicional?**
El CIE creó este usuario como solución al problema de permisos entre el usuario administrador (DBADMIN, usado localmente) y el usuario técnico del Service Binding de CF. `PIPELINE_USER` tiene permisos SELECT/INSERT/UPDATE sobre las tres tablas de DBADMIN y fue creado directamente en SQL Console del HANA Database Explorer.

**¿Por qué tabla ALERTS separada?**
El filtro rápido (cada 1-2 min) y el modelo ML (cada 30 min) pueden detectar la misma amenaza independientemente. La tabla ALERTS con verificación antes de insertar garantiza que el webhook se dispare exactamente una vez por evento, sin duplicados.

---

## Parte 11 — Lo que falta construir

### HOY — Abr 27 (webhook disponible)

- [ ] Recibir URL del webhook de los organizadores
- [ ] Implementar `app/alerting.py` con función `enviar_alerta(alert_type, severity, details, log_id)`
- [ ] Integrar `alerting.py` en `pipeline_loop.py` — disparar cuando `quick_filter` detecte amenaza
- [ ] Agregar `WEBHOOK_URL` a variables de CF: `cf set-env sap-ai-soc WEBHOOK_URL "..."`

### Esta semana — quick_filter.py

El filtro rápido aplica reglas determinísticas sobre cada batch nuevo de registros. No es ML — son condiciones concretas basadas en el conocimiento del schema. Ejemplos de reglas:

```python
# Reglas de sistema
if http_status in ['401', '403'] and count_by_ip > 10:
    return alerta("brute_force", "high")

if request_path.contains('/cgi-bin') and http_status == '404':
    return alerta("path_scan", "medium")

if log_type == 'SECURITY':
    return alerta("security_event", "high")

# Reglas LLM
if log_type == 'LLM_ERROR' and llm_cost_usd > 1.0:
    return alerta("high_cost_error", "medium")

if llm_response_time_ms > 10000:
    return alerta("slow_response", "low")
```

### AI Specialist — model.py

Modelo de detección de anomalías sobre datos acumulados en HANA. Se ejecuta cada 30 minutos al detectar cambio de ventana. Lee de `DBADMIN.RAW_LOGS_SISTEMA` y `DBADMIN.RAW_LOGS_LLM`, produce resultados que van a `DBADMIN.ALERTS`.

### Viz Lead — main.py + SAC

Dashboard Streamlit para desarrollo y pruebas. Dashboard en SAP Analytics Cloud conectado directamente a las tablas HANA para presentación ejecutiva final.

### Data Architect — índices y optimización

```sql
-- Índices para queries del modelo ML
CREATE INDEX IDX_SIS_TIMESTAMP ON DBADMIN.RAW_LOGS_SISTEMA (event_timestamp);
CREATE INDEX IDX_SIS_TYPE      ON DBADMIN.RAW_LOGS_SISTEMA (log_type);
CREATE INDEX IDX_SIS_IP        ON DBADMIN.RAW_LOGS_SISTEMA (client_ip);
CREATE INDEX IDX_LLM_TIMESTAMP ON DBADMIN.RAW_LOGS_LLM (event_timestamp);
CREATE INDEX IDX_LLM_STATUS    ON DBADMIN.RAW_LOGS_LLM (llm_status);
```

### Antes del 4 de Mayo — integración completa

- [ ] `quick_filter.py` + `alerting.py` integrados en el ciclo de 1-2 min
- [ ] `model.py` integrado en el ciclo de 30 min
- [ ] Prueba end-to-end completa: log aparece en API → HANA → detección → webhook → SAP AI Security Team
- [ ] Medir MTTD real bajo condiciones de prueba
- [ ] Verificar que el sistema corre estable 24+ horas en CF

---

*Reporte generado el 27 de Abril 2026 — Cloud Integration Engineer*  
*Consolida todos los avances desde el Kick Off hasta hoy*
