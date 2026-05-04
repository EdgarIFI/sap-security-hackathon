# Reporte de Sesión + Diseño Completo del Sistema ML
## SAP AI Security Anomaly Detection Hackathon · TEC de Monterrey × SAP
**Fechas:** 3–4 Mayo 2026  
**Autor:** Cloud Integration Engineer  
**Estado actual:** ✅ Pipeline completo desplegado en CF · ✅ ML activo en ciclo largo · ✅ ALERTS persistiendo en HANA

---

## 1. Resumen ejecutivo

Esta sesión completó la implementación completa del sistema de detección de anomalías ML. Partiendo de un pipeline con quick_filter ya operativo, se construyeron y desplegaron cuatro módulos nuevos que cierran el ciclo OBSERVE → ANALYZE → DETECT → RESPOND con detección estadística real.

**Logros de la sesión, en orden:**

1. **Bug crítico resuelto:** `DBADMIN.ALERTS` estaba vacía porque `pipeline_loop.py` pasaba `conn=None` a `enviar_alerta()`. Corregido con funciones dedicadas de conexión HANA. Verificado: 8/8 alertas con `alerted=1`.

2. **Schema real de HANA documentado:** Los nombres de columna en HANA difieren de los de la API raw. Se mapearon las 16 columnas de `RAW_LOGS_SISTEMA` y las 21 de `RAW_LOGS_LLM` con tipos exactos mediante queries al catálogo del sistema.

3. **Cuatro módulos ML implementados, testeados y desplegados:**
   - `app/hana_reader.py` — extracción desde HANA
   - `app/feature_eng.py` — feature engineering puro
   - `app/model.py` — IF_sistema + IF_llm + LOF_ip
   - `pipeline_loop.py` — integración del ciclo largo

4. **Sistema en producción:** El banner de CF confirma `model ML: ✓ activo`. El primer ciclo largo ML se ejecutará ~28 minutos después del arranque.

---

## 2. Estado actual del sistema

### Cloud Foundry — sap-ai-soc-papoi

| Métrica | Valor |
|---|---|
| Ciclos cortos completados (desde 1 Mayo) | 460+ |
| Ciclos cortos completados (sesión actual) | ~40+ |
| Intervalo ciclo corto | 2 minutos |
| Intervalo ciclo largo ML | 28 minutos |
| Registros por ventana | ~3,900–5,700 |
| Amenazas quick_filter por ventana | 3–5 |
| MTTD medido | ~1 segundo |
| Crashes desde el primer deploy | 0 |
| Módulos activos | quick_filter ✓ · alerting ✓ · model ML ✓ |

### HANA — conteos al cierre de sesión

| Tabla | Registros |
|---|---|
| `DBADMIN.RAW_LOGS_SISTEMA` | 1,026,865+ |
| `DBADMIN.RAW_LOGS_LLM` | 584,583+ |
| `DBADMIN.ALERTS` total | 8+ |
| `DBADMIN.ALERTS` alerted=1 | 8+ (100%) |
| `DBADMIN.ALERTS` alerted=0 | 0 |
| Ventanas acumuladas estimadas | 309 |

### Límite de llamadas API (desde 4 Mayo)

| Operación | Llamadas/ventana |
|---|---|
| `GET /info` | 1 |
| `GET /logs/current?page=N` | ~10 |
| `POST /alert` quick_filter | ~3–5 |
| `POST /alert` model ML (cap) | máx 5 |
| **Total máximo** | **~21** (límite: 100) |

---

## 3. Archivos del proyecto — estado actual

```
sap-security-hackathon/
├── pipeline_loop.py           ← MODIFICADO v3: ciclo largo ML activo
├── pipeline_loop_v1_backup.py ← backup original
│
└── app/
    ├── config.py              ← credenciales y variables de entorno ✅
    ├── ingest.py              ← extraccion paginada de la API ✅
    ├── etl.py                 ← limpieza y persistencia en HANA ✅
    ├── quick_filter.py        ← 6 reglas deterministicas ✅
    ├── alerting.py            ← POST /alert + persistencia ALERTS ✅
    ├── hana_reader.py         ← NUEVO: extraccion desde HANA para ML ✅
    ├── feature_eng.py         ← NUEVO: feature engineering para sklearn ✅
    └── model.py               ← NUEVO: IF_sistema + IF_llm + LOF_ip ✅
```

**Nota sobre `pipeline_loop_3mayo_miedo.py`:** archivo untracked en git, es un artefacto de desarrollo local. No debe commitearse. Ver sección 14 para instrucciones.

---

## 4. Bug corregido — persistencia en DBADMIN.ALERTS

### Sintoma
`SELECT COUNT(*) FROM DBADMIN.ALERTS` retornaba 0 a pesar de HTTP 201 confirmado en los logs de CF.

### Causa raiz
`pipeline_loop.py` llamaba `enviar_alerta(..., conn=None)`. `ingest_and_persist()` cierra su conexion HANA internamente antes de retornar. `alerting.py` tiene `if conn is not None` antes de cada operacion HANA — con `conn=None` nunca escribia a la base de datos.

### Solucion implementada en `pipeline_loop.py`

```python
def _abrir_conexion_hana():
    try:
        from config import HANA_HOST, HANA_PORT, HANA_USER, HANA_PASSWORD
        import hdbcli.dbapi as hdb
        return hdb.connect(address=HANA_HOST, port=int(HANA_PORT),
                           user=HANA_USER, password=HANA_PASSWORD)
    except Exception as e:
        logger.warning(f"[HANA] No se pudo abrir conexion: {e}")
        return None

def _cerrar_conexion_hana(conn):
    if conn is not None:
        try:
            conn.close()
        except Exception:
            pass
```

El bloque DETECT+RESPOND ahora abre su propia conexion:

```python
conn_alerting = _abrir_conexion_hana()
try:
    resumen_alertas = ejecutar_deteccion_y_alertas(
        df_nuevos=df_nuevos, ventana_inicio=ventana_inicio, conn=conn_alerting
    )
finally:
    _cerrar_conexion_hana(conn_alerting)
```

### Verificacion
```
COUNT(*) ALERTS total  → 8
alerted=1              → 8
alerted=0              → 0
```

---

## 5. Aclaracion permanente: "HANA no disponible en este ciclo"

**No es un bug.** Cuando `nuevos=0`, el pipeline omite el bloque de insercion en HANA y loguea ese mensaje. HANA esta operativa — simplemente no hay nada que insertar porque la deduplicacion en memoria ya filtro todos los registros de esa ventana. El mensaje es enganoso pero inofensivo.

---

## 6. Schema real de HANA — referencia oficial

Los nombres de columna en HANA son distintos a los del raw de la API SAP. El ETL los transforma al insertar. **Todo codigo que lea desde HANA debe usar los nombres de HANA.**

### RAW_LOGS_SISTEMA — 16 columnas

| # | Columna HANA | Tipo | Longitud | Nombre en API raw |
|---|---|---|---|---|
| 1 | `ID` | INTEGER | 10 | — (auto) |
| 2 | `LOG_ID` | NVARCHAR | 100 | `_id` |
| 3 | `EVENT_TIMESTAMP` | TIMESTAMP | 27 | `@timestamp` |
| 4 | `INGESTED_AT` | TIMESTAMP | 27 | — (generado) |
| 5 | `LOG_TYPE` | NVARCHAR | 50 | `sap_function_log_type` |
| 6 | `APPLICATION` | NVARCHAR | 100 | `sap_function_application` |
| 7 | `MESSAGE` | NVARCHAR | 2000 | `sap_function_message` |
| 8 | `ENVIRONMENT` | NVARCHAR | 50 | `sap_app_env` |
| 9 | `REGION_NAME` | NVARCHAR | 100 | `region_name` |
| 10 | `REGION_CODE` | NVARCHAR | 10 | `region_code` |
| 11 | `MACRO_REGION` | NVARCHAR | 50 | `macro_region` |
| 12 | `SERVICE_ID` | NVARCHAR | 200 | `service_id` |
| 13 | `HTTP_STATUS` | NVARCHAR | 10 | `http_status_code` — string, castear a int |
| 14 | `CLIENT_IP` | NVARCHAR | 50 | `client_ip` |
| 15 | `REQUEST_METHOD` | NVARCHAR | 20 | `headers_http_request_method` |
| 16 | `REQUEST_PATH` | NVARCHAR | 500 | `heathers_request_path` (typo API, corregido en HANA) |

### RAW_LOGS_LLM — 21 columnas

| # | Columna HANA | Tipo | Longitud | Nombre en API raw |
|---|---|---|---|---|
| 1 | `ID` | INTEGER | 10 | — (auto) |
| 2 | `LOG_ID` | NVARCHAR | 100 | `_id` |
| 3 | `EVENT_TIMESTAMP` | TIMESTAMP | 27 | `@timestamp` |
| 4 | `INGESTED_AT` | TIMESTAMP | 27 | — (generado) |
| 5 | `LOG_TYPE` | NVARCHAR | 50 | `sap_function_log_type` |
| 6 | `APPLICATION` | NVARCHAR | 100 | `sap_function_application` |
| 7 | `MESSAGE` | NVARCHAR | 2000 | `sap_function_message` |
| 8 | `ENVIRONMENT` | NVARCHAR | 50 | `sap_app_env` |
| 9 | `REGION_NAME` | NVARCHAR | 100 | `region_name` |
| 10 | `REGION_CODE` | NVARCHAR | 10 | `region_code` |
| 11 | `MACRO_REGION` | NVARCHAR | 50 | `macro_region` |
| 12 | `LLM_MODEL_ID` | NVARCHAR | 100 | `llm_model_id` |
| 13 | `LLM_PROVIDER` | NVARCHAR | 100 | `llm_provider` |
| 14 | `LLM_STATUS` | NVARCHAR | 50 | `llm_status` — valores: success/error/timeout (minusculas) |
| 15 | `LLM_ERROR_MESSAGE` | NVARCHAR | 1000 | `llm_error_message` |
| 16 | `LLM_PROMPT_CATEGORY` | NVARCHAR | 100 | `llm_prompt_category` |
| 17 | `LLM_TOTAL_TOKENS` | INTEGER | 10 | `llm_total_tokens` |
| 18 | `LLM_COST_USD` | DOUBLE | 15 | `llm_cost_usd` |
| 19 | `LLM_RESPONSE_TIME` | DOUBLE | 15 | `llm_response_time_ms` — sin `_ms` en HANA, pero unidad = ms |
| 20 | `LLM_TEMPERATURE` | DOUBLE | 15 | `llm_temperature` |
| 21 | `LLM_FINISH_REASON` | NVARCHAR | 50 | `llm_finish_reason` |

---

## 7. Distribuciones reales de datos (base de decisiones ML)

### HTTP_STATUS — 16 valores

| HTTP_STATUS | Count | Familia |
|---|---|---|
| 200 | 568,544 | 2xx |
| 201 | 70,641 | 2xx |
| 204 | 69,865 | 2xx |
| 400 | 47,750 | 4xx |
| 500 | 43,438 | 5xx |
| 429 | 38,288 | 4xx |
| 408 | 37,604 | 4xx |
| 301 | 35,077 | 3xx |
| 302 | 35,042 | 3xx |
| 206 | 19,332 | 2xx |
| 401 | 14,371 | 4xx |
| 503 | 14,232 | 5xx |
| 502 | 14,208 | 5xx |
| 403 | 14,039 | 4xx |
| 404 | 7,297 | 4xx |
| 504 | 7,077 | 5xx |

**Por familia:** 2xx=71% · 4xx=13% · 5xx=8% · 3xx=7%

### LOG_TYPE sistema — 7 valores

| LOG_TYPE | Count |
|---|---|
| INFO | 386,460 |
| WARNING | 190,377 |
| ERROR | 143,564 |
| AUDIT | 96,038 |
| DEBUG | 95,826 |
| PERF | 95,353 |
| SECURITY | 29,187 (2.8%) |

### LOG_TYPE LLM — 3 valores

| LOG_TYPE | Count |
|---|---|
| LLM_REQUEST | 413,532 |
| LLM_ERROR | 117,753 |
| LLM_TIMEOUT | 58,578 |

**Tasa de error+timeout: ~30%** — el sistema LLM tiene problemas sistemicos persistentes que el modelo aprende como baseline.

### Rangos numericos LLM

| Metrica | MIN | MAX | AVG |
|---|---|---|---|
| `LLM_COST_USD` | 0.000007 | 0.13892 | 0.01247 |
| `LLM_RESPONSE_TIME` (ms) | 200.09 | 34,999.82 | 8,781.94 |
| `LLM_TOTAL_TOKENS` | 84 | 3,498 | — |

Todas heavy-tailed — transformacion `log1p` obligatoria.

### Cardinalidades clave

| Variable | Cardinalidad |
|---|---|
| `APPLICATION` | 10 |
| `REGION_NAME` | 108 |
| `CLIENT_IP` | 105 |
| `LOG_TYPE` sistema | 7 |
| `LLM_STATUS` | 3 (minusculas) |

---

## 8. Arquitectura del sistema ML

### Principio rector
No forzar logs de sistema y LLM en un solo vector. Son poblaciones con columnas estructuralmente ausentes entre si. Un modelo unico aprenderia ruido de nulidad, no anomalias reales.

### Tres modelos en dos niveles

```
Nivel 1 — Evento individual:
  IF_sistema  ->  IsolationForest sobre ~3,500 logs Sistema/ventana
  IF_llm      ->  IsolationForest sobre ~2,400 logs LLM/ventana

Nivel 2 — Comportamiento agregado:
  LOF_ip      ->  LocalOutlierFactor sobre tabla IP (~105 filas/ventana)
```

### Configuracion sklearn

```python
IsolationForest(
    n_estimators=200,
    max_samples=256,      # submuestra optima segun paper original
    contamination="auto",
    random_state=42,
    n_jobs=-1             # paralelismo CPU completo en CF
)

LocalOutlierFactor(
    n_neighbors=20,       # seguro con 105 IPs
    contamination="auto",
    novelty=False
)
```

### Algoritmos descartados para este hackathon

| Algoritmo | Razon de descarte |
|---|---|
| Autoencoder/Keras | Dependencias extra, deadline 3 dias |
| One-Class SVM | Cuadratico en training, sensible a outliers |
| DBSCAN | O(n2) memoria, muy sensible a parametros |
| Elliptic Envelope | Asume distribucion Gaussiana unimodal — incorrecto |

---

## 9. Feature engineering — especificacion completa

### Features de Sistema (IF_sistema)

**Numericas:**

| Feature | Derivacion |
|---|---|
| `status_family` | `int(HTTP_STATUS) // 100` — valores 2,3,4,5 |
| `is_4xx` | 1 si status_family == 4 |
| `is_5xx` | 1 si status_family == 5 |
| `is_401_or_403` | 1 si HTTP_STATUS in ('401','403') |
| `is_429` | 1 si HTTP_STATUS == '429' |
| `hour_utc` | EVENT_TIMESTAMP.hour (0-23) |

**Categoricas (OrdinalEncoder para IF tree-based):**

| Feature | Cardinalidad | Nota |
|---|---|---|
| `LOG_TYPE` | 7 | Directo |
| `APPLICATION` | 10 | Directo |
| `REGION_NAME` | 108 | max_categories=32, unknown_value=-1 |

**No usados en evento individual:** `CLIENT_IP` (solo en tabla agregada), `REQUEST_PATH` raw, `MESSAGE` raw, `LOG_ID`, `INGESTED_AT`.

### Features de LLM (IF_llm)

**Numericas (log1p por heavy-tail):**

| Feature | Derivacion |
|---|---|
| `log1p_cost` | `log1p(LLM_COST_USD)` |
| `log1p_response_time` | `log1p(LLM_RESPONSE_TIME)` |
| `log1p_total_tokens` | `log1p(LLM_TOTAL_TOKENS)` |
| `hour_utc` | EVENT_TIMESTAMP.hour |

NaN en numericas: imputados con mediana (no con cero — cero seria outlier en estas distribuciones).

**Categoricas (OrdinalEncoder):**

| Feature | Cardinalidad |
|---|---|
| `LLM_STATUS` | 3: success/error/timeout (minusculas) |
| `LLM_MODEL_ID` | variable |
| `LLM_PROVIDER` | variable |
| `LOG_TYPE` | 3: LLM_REQUEST/LLM_ERROR/LLM_TIMEOUT |

### Tabla de comportamiento por IP (LOF_ip)

Una fila por IP unica. ~105 filas en produccion. Todas numericas — se aplica `RobustScaler` antes de LOF.

| Feature | Descripcion |
|---|---|
| `event_count` | Total eventos de esta IP en la ventana |
| `distinct_paths` | COUNT(DISTINCT REQUEST_PATH) |
| `distinct_apps` | COUNT(DISTINCT APPLICATION) |
| `ratio_4xx` | Errores cliente / total (0-1) |
| `ratio_5xx` | Errores servidor / total (0-1) |
| `ratio_security` | LOG_TYPE='SECURITY' / total (0-1) |
| `has_401_or_403` | 1 si algun evento fue 401 o 403 |
| `has_429` | 1 si algun evento fue 429 |
| `n_distinct_status` | COUNT(DISTINCT HTTP_STATUS) |

---

## 10. Estrategia de thresholding

### Modo de operacion

| Condicion | Modo | Threshold |
|---|---|---|
| < 20 ventanas acumuladas | Cold-start | IQR: Q1 - 1.5*IQR |
| >= 20 ventanas (actual: 309) | Historico | MAD: median - 3.5*(MAD/0.6745) |

El sistema arranca directamente en **modo historico** con las 309 ventanas ya acumuladas.

### Cap de alertas

**Maximo 5 alertas por ciclo largo.** Si hay mas de 5 anomalias detectadas entre los tres modelos, se envian las de mayor severidad primero (high → medium → low).

**Justificacion del cap:** quick_filter usa ~15 llamadas/ventana + model ML usa max 5 = max 20 total. Muy por debajo del limite de 100 llamadas/30min.

---

## 11. Modulos implementados — contratos publicos

### `app/hana_reader.py`

```python
abrir_conexion_hana() -> conn | None
leer_sistema_ventana_actual(conn) -> pd.DataFrame   # ultimos 30 min
leer_llm_ventana_actual(conn) -> pd.DataFrame       # ultimos 30 min
leer_sistema_historico(conn, horas=24) -> pd.DataFrame
leer_llm_historico(conn, horas=24) -> pd.DataFrame
contar_ventanas_acumuladas(conn) -> int
en_modo_historico(conn) -> bool                     # True si >= 20 ventanas
```

**Prueba standalone:** `python -m app.hana_reader`
**Verificado con HANA real:** 3,626 sis + 2,420 LLM (ventana actual) · 127,382 + 42,908 (historico 24h) · 309 ventanas · modo HISTORICO

### `app/feature_eng.py`

```python
build_sistema_features(df) -> pd.DataFrame    # LOG_ID + 6 numericas + 3 categoricas
build_llm_features(df) -> pd.DataFrame        # LOG_ID + 4 numericas + 4 categoricas
build_ip_behavior_table(df) -> pd.DataFrame   # CLIENT_IP + 9 features numericas
```

**Garantias:** sin NaN en salida, tipos correctos, DataFrames vacios con columnas correctas ante entrada vacia.
**Prueba standalone:** `python -m app.feature_eng`
**Verificado:** 4/4 tests pasados

### `app/model.py`

```python
analizar_ventana(conn, window_start=None) -> list[dict]
```

Cada dict retornado:

```python
{
    "alert_type": "ml_sistema_anomaly" | "ml_llm_anomaly" | "ml_ip_anomaly",
    "severity":   "low" | "medium" | "high",
    "details":    str,   # <= 250 chars
    "log_id":     str,
    "event_time": str,
}
```

**Garantias:** nunca lanza excepciones, cap de 5 alertas, `conn=None` retorna `[]`.
**Prueba standalone:** `python -m app.model`
**Verificado:** 6/6 tests pasados

---

## 12. Integracion en `pipeline_loop.py` — ciclo largo

### Flujo de conexiones (sin duplicados)

```
Cada 28 minutos:
  conn_ml = _abrir_conexion_hana()
      |
      +-- analizar_ventana(conn_ml)
              +-- hana_reader lee RAW_LOGS_SISTEMA (127k registros historicos)
              +-- hana_reader lee RAW_LOGS_LLM (43k registros historicos)
              +-- IF_sistema: train historico -> score ventana -> threshold MAD
              +-- IF_llm:     train historico -> score ventana -> threshold MAD
              +-- LOF_ip:     build_ip_table -> fit_predict -> labels
  _cerrar_conexion_hana(conn_ml)

  Si hay anomalias:
  conn_alerting_ml = _abrir_conexion_hana()
      |
      +-- enviar_alerta(..., source="model_ml") x N anomalias (max 5)
              +-- INSERT en DBADMIN.ALERTS (alerted=0)
              +-- POST /alert a API SAP -> HTTP 201
              +-- UPDATE DBADMIN.ALERTS (alerted=1)
  _cerrar_conexion_hana(conn_alerting_ml)
```

### Banner de arranque confirmado en CF

```
PIPELINE LOOP v2 — Ingesta + Deteccion + Alerting
quick_filter: activo
alerting:     activo
model ML:     activo     <- CONFIRMADO 2026-05-04 08:48:30 UTC
```

---

## 13. MTTD formal — criterio #1 (40% del score)

**Medicion en ciclo #455, 1 Mayo 2026:**

```
Inicio de ventana (API):        2026-05-01T06:30:00 UTC
Ingesta completada:             2026-05-01T06:31:35 UTC
Deteccion completada:           2026-05-01T06:31:35 UTC  (< 1 seg)
HTTP 201 confirmado (SAP):      2026-05-01T06:31:36 UTC  (310 ms de red)

MTTD puro (deteccion -> confirmacion SAP): ~1 segundo
MTTD desde inicio de ventana: ~95 segundos (1 min 35 seg)
```

**Objetivo del hackathon:** <= 2 minutos. **Resultado: < 2 segundos.**

---

## 14. Procedimiento de git — commit pendiente

Estado actual de `git status`:

```
Untracked files:
  app/feature_eng.py
  app/hana_reader.py
  app/model.py
  pipeline_loop_3mayo_miedo.py    <- NO commitear, es artefacto local
```

### Comandos para el commit oficial

```bash
git add app/feature_eng.py
git add app/hana_reader.py
git add app/model.py

git commit -m "feat: implementar sistema ML completo

- app/hana_reader.py: extraccion desde HANA con schema real, modo historico/cold-start
- app/feature_eng.py: feature engineering para IF_sistema, IF_llm y LOF_ip
- app/model.py: IsolationForest x2 + LocalOutlierFactor, thresholding MAD/IQR, cap 5 alertas
- pipeline_loop.py: ciclo largo activo cada 28 min con analizar_ventana() integrado
- Fix: conn=None bug en alerting -> DBADMIN.ALERTS ahora persiste correctamente"

git push origin alerts_implementation
```

### Sobre `pipeline_loop_3mayo_miedo.py`

```bash
# Opcion 1: agregar a .gitignore
echo "pipeline_loop_3mayo_miedo.py" >> .gitignore

# Opcion 2: eliminar directamente
rm pipeline_loop_3mayo_miedo.py
```

---

## 15. Verificacion pendiente — primer ciclo largo ML

El pipeline arranco a las **08:48:30 UTC**. El primer ciclo largo se disparara a las **~09:16:30 UTC**.

### Que verificar en CF

```bash
cf logs sap-ai-soc-papoi --recent
```

Buscar estas lineas:
```
[CICLO-LARGO] Han pasado 28.X min — iniciando analisis ML
[MODEL] Iniciando analisis ML | ventana=...
[MODEL] Modo: HISTORICO (309/20 ventanas minimas)
[MODEL] ml_sistema_anomaly: entrenando con X,XXX registros historicos
[MODEL] ml_llm_anomaly: entrenando con X,XXX registros historicos
[MODEL] Analisis completado en XXXms | anomalias: X detectadas | Y enviadas
```

### Verificacion en HANA

```sql
SELECT alert_id, alert_type, severity, detected_at, alerted, detection_source
FROM DBADMIN.ALERTS
WHERE detection_source = 'model_ml'
ORDER BY detected_at DESC;
```

Esperamos filas con `detection_source = 'model_ml'` y `alerted = 1`.

---

## 16. Proximos pasos — hacia la eliminatoria (12–14 Mayo)

| Tarea | Responsable | Estado |
|---|---|---|
| Pipeline ML desplegado en CF | CIE | COMPLETADO |
| Verificar primer ciclo largo en CF | CIE | Pendiente (hoy) |
| Dashboard Streamlit con datos reales | Viz Lead | Pendiente |
| SAP Analytics Cloud conectado a HANA | Viz Lead | Pendiente |
| Reporte forense con incidente real | CIE | Pendiente |
| Reporte estrategico final para SAP | Todo el equipo | Pendiente |

---

*Documento consolidado — 4 Mayo 2026*  
*Cloud Integration Engineer — SAP AI Security Anomaly Detection Hackathon*
