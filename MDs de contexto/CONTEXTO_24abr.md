# Reporte de Actualización — Pipeline de Ingesta Continua + Deploy a Cloud Foundry
## SAP AI Security Anomaly Detection Hackathon — TEC de Monterrey × SAP
**Fecha:** 24 de Abril 2026  
**Autor:** Cloud Integration Engineer  
**Estado:** Pipeline desplegado en SAP BTP Cloud Foundry — ingesta continua operativa

---

## 1. Resumen ejecutivo

Se completó la migración del pipeline de ingesta de ejecución local a SAP BTP Cloud Foundry. El sistema ahora corre 24/7 en la nube sin depender de ninguna computadora del equipo. Adicionalmente, se rediseñó la arquitectura de ingesta para hacer polling cada 1-2 minutos en lugar de cada 30 minutos, reduciendo el MTTD (Mean Time to Detect) de ~30 minutos a ~1-2 minutos.

### Logros principales
- Pipeline corriendo en Cloud Foundry con polling continuo
- Deduplicación en memoria para evitar reprocesar registros
- UPSERT optimizado: SELECT previo + executemany() (16 segundos vs 9 minutos)
- Conexión a HANA via VCAP_SERVICES (sin credenciales en el código)
- Tabla ALERTS creada para registro único de amenazas detectadas
- Endpoint Flask de monitoreo (/health y /run) disponible

---

## 2. Arquitectura final del sistema

### Flujo de datos — polling cada 1-2 minutos

```
CADA 1-2 MINUTOS (pipeline_loop.py en Cloud Foundry):

  SAP API
    │  GET /info + GET /logs/current?page=1..12
    ▼
  fetch_current_window()                    ~30-60 seg
    │  Descarga ~5,500 registros completos de la ventana
    ▼
  Deduplicación en memoria                  ~instantáneo
  (ids_procesados set() en ingest.py)
    │
    ├── Ventana nueva → reset ids_procesados → df_nuevos = todo
    └── Misma ventana → df_nuevos = solo los nuevos
    ▼
  hana_esta_viva()                          ~1 seg
    │
    ├── HANA caída → log advertencia → continúa sin persistir
    │
    └── HANA viva
          │
          ▼
        SELECT log_id (verificación extra)  ~1-2 seg
        Filtrar en Python                   ~instantáneo
        executemany() INSERT                ~5-10 seg
          ├── DBADMIN.RAW_LOGS_SISTEMA
          └── DBADMIN.RAW_LOGS_LLM
          │
          ▼
        quick_filter(df_nuevos)             ← pendiente
          │
          ├── Amenaza obvia → tabla ALERTS + webhook
          └── Sin amenaza → continúa


CADA 30 MINUTOS (al detectar cambio de ventana):

  ETL sobre ventana completa en HANA        ← pendiente
    │
    ▼
  model.py (análisis profundo)              ← pendiente AI Specialist
    │
    ├── Anomalía → verificar ALERTS → webhook si es nueva
    └── Normal → datos para re-entrenamiento
```

### Infraestructura en SAP BTP

```
SAP BTP Global Account
  └── Subaccount (Trial)
        ├── Cloud Foundry Environment
        │     └── Space "Dev"
        │           └── App: sap-ai-soc
        │                 ├── pipeline_loop.py (entry point, polling continuo)
        │                 ├── server.py (endpoints /health y /run)
        │                 ├── Service Binding → HANA (VCAP_SERVICES)
        │                 └── Service Binding → XSUAA (pyuaa)
        │
        └── SAP HANA Cloud
              ├── DBADMIN.RAW_LOGS_SISTEMA  (logs de sistema)
              ├── DBADMIN.RAW_LOGS_LLM      (logs de LLM)
              └── DBADMIN.ALERTS            (registro de alertas)
```

### Manejo de credenciales en Cloud Foundry

```
CREDENCIAL              CÓMO LLEGA A LA APP             SEGURIDAD
────────────────────    ─────────────────────────────    ───────────────────
HANA host/port/user/pw  VCAP_SERVICES (automático)      Service Binding
BEARER_TOKEN            cf set-env (manual, una vez)     Solo en memoria CF
API_BASE_URL            cf set-env (manual, una vez)     Solo en memoria CF
JOB_SECRET_TOKEN        cf set-env (manual, una vez)     Solo en memoria CF

Ninguna credencial existe en el repositorio.
.env está en .gitignore — solo para desarrollo local.
manifest.yml no tiene sección env: con secretos.
```

---

## 3. Archivos modificados — detalle de cada cambio

### `app/config.py` — Lectura dual de credenciales

**Cambio:** se agregó la función `_load_hana_creds()` que detecta automáticamente el entorno de ejecución.

**Lógica:**
- Si `VCAP_SERVICES` existe → estamos en Cloud Foundry → lee credenciales HANA de ahí
- Si no existe → estamos en local → lee de `.env` como siempre

**Variables nuevas:**
- `JOB_SECRET_TOKEN` — token de seguridad para el endpoint /run de server.py

**Lo que no cambió:** `API_BASE_URL`, `BEARER_TOKEN`, `WEBHOOK_URL`, `validate_config()`, `get_headers()` — todo igual.

---

### `app/hana_client.py` — Tres cambios

**Cambio 1 — Constantes de tablas con schema explícito:**
```
Antes:  TABLA_SISTEMA = "RAW_LOGS_SISTEMA"
Ahora:  TABLA_SISTEMA = "DBADMIN.RAW_LOGS_SISTEMA"
```
Necesario porque en CF el usuario del Service Binding no es DBADMIN y buscaría las tablas en el schema incorrecto.

**Cambio 2 — Se eliminó `sslHostNameInCertificate` de `get_connection()`:**
El parámetro causaba error de SSL con instancias `hna1.prod`. La conexión sigue cifrada con `encrypt=True`.

**Cambio 3 — Se reemplazó `create_tables()` para compatibilidad con HANA Cloud:**
`CREATE TABLE IF NOT EXISTS` no es compatible con la versión de HANA Cloud del trial. Se reemplazó por verificación manual contra el catálogo del sistema.

**Cambio 4 — Se reemplazó `insert_logs()` por `upsert_logs()`:**

La función original usaba `executemany()` con INSERT directo — sin verificar duplicados.

Primera versión del reemplazo usaba MERGE INTO fila por fila — seguro contra duplicados pero lento (~9 minutos para 5,500 registros).

Versión final usa SELECT previo + executemany():
1. SELECT log_id de ambas tablas → obtener IDs existentes en HANA
2. Filtrar en Python → quedarse solo con los nuevos
3. executemany() INSERT → insertar todos en batch

Rendimiento: 16 segundos para 5,500 registros (vs 9 minutos con MERGE).

**Cambio 5 — Nueva función `hana_esta_viva()`:**
Verifica si HANA Cloud está accesible ejecutando `SELECT 1 FROM DUMMY`. Retorna True/False sin lanzar excepciones. Se usa antes de cada ciclo para decidir si persistir en HANA o continuar solo con CSV.

**Lo que no cambió:** funciones `_safe_str`, `_safe_float`, `_safe_int`, `_safe_ts`, bloque `if __name__ == "__main__"`.

---

### `app/ingest.py` — Deduplicación en memoria

**Cambio 1 — Import actualizado:**
```
Antes:  from hana_client import insert_logs, create_tables
Ahora:  from hana_client import upsert_logs, hana_esta_viva, create_tables
```

**Cambio 2 — Variables de estado de deduplicación:**
```python
ids_procesados = set()    # IDs ya procesados en esta ventana
ventana_anterior = None   # detecta cambio de ventana UTC
```

**Cambio 3 — `ingest_and_persist()` rediseñada:**
- Detecta cambio de ventana UTC → resetea `ids_procesados`
- Filtra registros nuevos comparando `_id` contra `ids_procesados`
- Solo los registros nuevos pasan al UPSERT en HANA
- Verifica `hana_esta_viva()` antes de intentar escribir
- Retorna `df_nuevos` para uso futuro del filtro rápido

**Lo que no cambió:** `fetch_current_window()`, `save_data()`, bloque `if __name__ == "__main__"`.

---

### `pipeline_loop.py` — Polling continuo

**Cambio 1 — Intervalo de polling:**
```
Antes:  Calcula tiempo hasta próxima ventana UTC (cada 30 min)
Ahora:  INTERVALO_POLLING = 1 * 60 (cada 1 minuto, configurable)
```

**Cambio 2 — `ejecutar_ciclo_ingesta()` actualizada:**
- Muestra campo "Nuevos" en los logs
- Espacio preparado para filtro rápido (comentado hasta que `quick_filter.py` exista)

**Cambio 3 — `main()` simplificada:**
- Sleep fijo de INTERVALO_POLLING en vez de calcular próxima ventana
- `calcular_segundos_hasta_proxima_ventana()` se mantiene para uso futuro del modelo ML

**Lo que no cambió:** manejo de errores, logging dual (consola + archivo), `KeyboardInterrupt`, funciones de hora Monterrey.

---

### `server.py` — NUEVO — Wrapper Flask

**Propósito:** entry point alternativo que expone endpoints HTTP.

**Endpoints:**
- `GET /` y `GET /health` — liveness probe, siempre retorna 200
- `POST /run` — ejecuta un ciclo completo del pipeline (protegido con token)

**Seguridad:** el endpoint `/run` valida el header `X-Job-Token` contra `JOB_SECRET_TOKEN`. Sin token válido retorna 401.

**Uso actual:** monitoreo y pruebas manuales. `pipeline_loop.py` es el entry point principal en CF.

---

### `manifest.yml` — Configuración de deploy

**Cambios:**
- `command: python pipeline_loop.py` (entry point principal)
- `health-check-type: process` (verifica que el proceso Python siga vivo)
- Sección `services:` con binding a HANA y XSUAA
- Se eliminó sección `env:` completa — credenciales se configuran con `cf set-env`

---

### `runtime.txt` — NUEVO

Especifica versión de Python para el buildpack de CF:
```
python-3.11.x
```

---

### `requirements.txt` — Dependencias para CF

Se redujo a solo las dependencias necesarias para el pipeline:
```
Flask
requests
pandas
numpy
hdbcli
python-dotenv
scikit-learn
```

Se eliminaron dependencias de desarrollo local (streamlit, matplotlib, pillow, pyarrow, pydeck, altair, protobuf).

Archivo `requirements-dev.txt` disponible para desarrollo local con todas las dependencias.

---

## 4. Cambios en SAP HANA Cloud

### Índices UNIQUE creados
```sql
CREATE UNIQUE INDEX IDX_SIS_LOGID ON RAW_LOGS_SISTEMA (log_id);
CREATE UNIQUE INDEX IDX_LLM_LOGID ON RAW_LOGS_LLM (log_id);
```
Necesarios para que la verificación de duplicados sea eficiente.

### Tabla ALERTS creada
```sql
CREATE TABLE ALERTS (
    alert_id         NVARCHAR(100) PRIMARY KEY,
    log_id           NVARCHAR(100),
    detected_at      TIMESTAMP,
    detection_source NVARCHAR(20),
    alert_type       NVARCHAR(100),
    severity         NVARCHAR(10),
    details          NVARCHAR(2000),
    window_start     TIMESTAMP,
    alerted          TINYINT DEFAULT 0
);

CREATE INDEX IDX_ALERT_LOGID ON ALERTS (log_id);
```
Registro único de alertas para evitar duplicados entre el filtro rápido y el modelo ML. `alerted = 0` significa no enviada, `1` significa ya enviada al webhook.

### Permisos para usuario de Cloud Foundry
Se otorgaron permisos SELECT, INSERT, UPDATE al usuario del Service Binding sobre las tablas del schema DBADMIN, ya que CF usa un usuario técnico distinto a DBADMIN.

### Tablas con schema explícito
Las constantes en `hana_client.py` usan `DBADMIN.RAW_LOGS_SISTEMA` en vez de `RAW_LOGS_SISTEMA` para garantizar que cualquier usuario encuentre las tablas correctas.

---

## 5. Pruebas realizadas

### Fase 1 — Conexión HANA local ✅
| Prueba | Resultado |
|---|---|
| `python app/hana_client.py` — conexión | ✅ Conexión establecida |
| `python app/hana_client.py` — crear tablas | ✅ Tablas creadas (con fix de IF NOT EXISTS) |
| `python pipeline_loop.py` — ingesta completa | ✅ Datos en HANA verificados con SQL |

### Fase 2 — Server Flask local ✅
| Prueba | Resultado |
|---|---|
| `GET /health` | ✅ 200 `{"status":"running"}` |
| `POST /run` sin token | ✅ 401 `{"error":"unauthorized"}` |
| `POST /run` token falso | ✅ 401 `{"error":"unauthorized"}` |
| `POST /run` token correcto | ✅ 200 Pipeline completo — datos en HANA |

### Fase 3 — Deduplicación y rendimiento ✅
| Prueba | Resultado |
|---|---|
| Ciclo 1 (ventana nueva, 5,389 registros) | ✅ 16 segundos — todos insertados |
| Ciclo 2 (misma ventana, 0 nuevos) | ✅ 10 segundos — nada que insertar |
| Cambio de ventana UTC | ✅ Reset automático de ids_procesados |
| Verificación duplicados en HANA | ✅ 0 duplicados (GROUP BY log_id HAVING COUNT > 1) |

### Fase 4 — Deploy a Cloud Foundry ✅
| Prueba | Resultado |
|---|---|
| `cf push` | ✅ App desplegada |
| `cf set-env` credenciales | ✅ Secretos configurados |
| `cf logs sap-ai-soc --recent` | ✅ Pipeline corriendo |

---

## 6. Rendimiento medido

```
OPERACIÓN                        TIEMPO
──────────────────────────────   ─────────
Extracción API (12 páginas)      30-60 seg
Deduplicación en memoria         <1 seg
Verificación HANA (SELECT IDs)   1-2 seg
INSERT masivo (executemany)      5-10 seg
──────────────────────────────   ─────────
Ciclo con ventana nueva          ~1-1.5 min
Ciclos siguientes (pocos nuevos) ~40-60 seg

Comparación UPSERT:
  MERGE fila por fila            ~9 min para 5,500 registros
  SELECT + executemany           ~16 seg para 5,500 registros
```

---

## 7. Estructura actual del repositorio

```
sap-security-hackathon/
│
├── pipeline_loop.py             ← entry point CF + desarrollo local ✅
├── server.py                    ← endpoints Flask /health y /run ✅
├── manifest.yml                 ← configuración de deploy CF ✅
├── runtime.txt                  ← versión Python para CF ✅
├── requirements.txt             ← dependencias para CF ✅
├── requirements-dev.txt         ← dependencias completas local ✅
├── pipeline.log                 ← log persistente (autogenerado)
├── .env                         ← credenciales locales (NUNCA en Git)
├── .env.examples                ← plantilla vacía (sí en Git)
├── .gitignore                   ✅
├── README.md                    ✅
│
├── app/
│   ├── config.py                ← lectura dual .env / VCAP_SERVICES ✅
│   ├── ingest.py                ← extracción + deduplicación + UPSERT ✅
│   ├── hana_client.py           ← conexión HANA + upsert_logs + hana_esta_viva ✅
│   ├── etl.py                   ← limpieza y transformación ✅
│   ├── explore.py               ← análisis exploratorio ✅
│   ├── quick_filter.py          ← reglas determinísticas ⏳ PENDIENTE
│   ├── model.py                 ← detección ML ⏳ PENDIENTE (AI Specialist)
│   ├── alerting.py              ← webhook dispatch ⏳ PENDIENTE (Abr 27)
│   ├── main.py                  ← dashboard Streamlit ⏳ PENDIENTE (Viz Lead)
│   └── __init__.py
│
├── data/
│   ├── raw/                     ← CSVs por ventana (no en Git)
│   ├── processed/               ← datasets limpios (no en Git)
│   └── SCHEMA.md                ✅
│
├── docs/                        ✅
├── notebooks/                   ← exploración (AI Specialist)
└── tests/
```

---

## 8. Lo que falta construir

### Inmediato (esta semana)
- [ ] Verificar que el pipeline en CF corre de manera estable 24+ horas
- [ ] Monitorear logs con `cf logs sap-ai-soc --recent`

### Antes del 27 de Abril
- [ ] Recibir webhook URL de los organizadores
- [ ] Implementar `app/alerting.py` con `enviar_alerta()`
- [ ] Implementar `app/quick_filter.py` con reglas determinísticas

### Pendiente del AI Specialist
- [ ] `app/model.py` — modelo de detección de anomalías
- [ ] Feature engineering sobre datos en HANA
- [ ] Cuando esté listo, se integra en `pipeline_loop.py` en el bloque de cada 30 min
- [ ] Agregar `scikit-learn` a `requirements.txt` si no está ya incluido

### Pendiente del Viz Lead
- [ ] `app/main.py` — dashboard Streamlit (desarrollo local)
- [ ] Dashboard en SAP Analytics Cloud conectado a HANA
- [ ] SAC se conecta directamente a `DBADMIN.RAW_LOGS_SISTEMA` y `DBADMIN.RAW_LOGS_LLM`

### Pendiente del Data Architect
- [ ] Verificar schema de tablas HANA
- [ ] Optimizar queries si el volumen crece
- [ ] Crear índices adicionales si el modelo ML los necesita

### Antes del 4 de Mayo (Go Live)
- [ ] Integrar todos los componentes (filtro rápido + modelo ML + alerting)
- [ ] Prueba end-to-end completa
- [ ] Verificar que MTTD cumple el objetivo del criterio #1

---

## 9. Decisiones de diseño documentadas

### ¿Por qué polling cada 1-2 min en vez de cada 30 min?
La API genera datos continuamente pero borra cada 30 min UTC. El polling frecuente permite detectar amenazas tan pronto como aparecen en la API, reduciendo el MTTD de ~30 min a ~1-2 min. Impacto directo en el criterio #1 (40%).

### ¿Por qué SELECT + executemany() en vez de MERGE INTO?
MERGE INTO fila por fila tardaba ~9 minutos para 5,500 registros. SELECT previo + executemany() tarda ~16 segundos. Con `instances: 1` en CF no hay riesgo de concurrencia que justifique el costo del MERGE.

### ¿Por qué deduplicación en memoria + HANA (dos capas)?
La deduplicación en memoria (ids_procesados) es la capa rápida — evita enviar datos repetidos a HANA. El SELECT de IDs en HANA es la capa de seguridad — cubre el caso de reinicio de CF donde ids_procesados se vacía.

### ¿Por qué pipeline_loop.py como entry point en CF y no server.py + Job Scheduler?
El Job Scheduling Service en plan free tiene intervalo mínimo de 1 hora. El polling cada 1-2 minutos requiere un proceso continuo. pipeline_loop.py mantiene el control del timing internamente.

### ¿Por qué dos tablas separadas (Sistema y LLM)?
Los tipos de log tienen columnas completamente distintas. Una sola tabla tendría ~50% de NULLs por fila. Dos tablas optimizan espacio y simplifican queries del modelo ML.

### ¿Por qué tabla ALERTS separada?
El filtro rápido (cada 1-2 min) y el modelo ML (cada 30 min) pueden detectar la misma amenaza. La tabla ALERTS evita alertas duplicadas — ambos verifican si ya existe una alerta antes de crear una nueva.

### ¿Por qué schema explícito (DBADMIN.tabla)?
En CF el Service Binding usa un usuario técnico distinto a DBADMIN. Sin schema explícito, HANA busca las tablas en el schema del usuario de CF y no las encuentra.

---

## 10. Queries útiles para el equipo

### Verificar datos en HANA
```sql
-- Conteos totales
SELECT COUNT(*) AS total FROM DBADMIN.RAW_LOGS_SISTEMA;
SELECT COUNT(*) AS total FROM DBADMIN.RAW_LOGS_LLM;

-- Muestra de datos recientes
SELECT TOP 10 log_id, event_timestamp, ingested_at, log_type
FROM DBADMIN.RAW_LOGS_SISTEMA
ORDER BY ingested_at DESC;

-- Verificar que no hay duplicados
SELECT log_id, COUNT(*) AS veces
FROM DBADMIN.RAW_LOGS_SISTEMA
GROUP BY log_id
HAVING COUNT(*) > 1;

-- Registros por ciclo de ingesta
SELECT
    CAST(ingested_at AS DATE) AS fecha,
    HOUR(ingested_at) AS hora,
    MINUTE(ingested_at) AS minuto,
    COUNT(*) AS registros
FROM DBADMIN.RAW_LOGS_SISTEMA
GROUP BY CAST(ingested_at AS DATE), HOUR(ingested_at), MINUTE(ingested_at)
ORDER BY fecha DESC, hora DESC, minuto DESC;
```

### Monitorear pipeline en CF
```bash
# Ver logs recientes
cf logs sap-ai-soc --recent

# Ver logs en tiempo real
cf logs sap-ai-soc

# Ver estado de la app
cf app sap-ai-soc

# Ver variables de entorno (sin secretos visibles)
cf env sap-ai-soc
```

---

## 11. Notas importantes para el equipo

**pipeline_loop.py funciona en ambos entornos** — localmente lee de .env, en CF lee de VCAP_SERVICES. No necesita cambios para cambiar de entorno.

**server.py no se borró** — sigue disponible para pruebas manuales con POST a /run. Útil para disparar un ciclo sin esperar el polling.

**HANA trial se pausa por inactividad** — el pipeline con polling cada 1-2 min funciona como heartbeat y mantiene HANA activa. Si el pipeline se detiene por más de unas horas, verificar en BTP Cockpit.

**El `.env` nunca va a Git** — verificar siempre con `git status` antes de `git push`.

**INTERVALO_POLLING es configurable** — cambiar el valor en pipeline_loop.py para ajustar la frecuencia sin afectar ninguna otra función.

---

*Reporte generado el 24 de Abril 2026 — Cloud Integration Engineer*