# Reporte de Sesión + Diseño Completo de model.py
## SAP AI Security Anomaly Detection Hackathon · TEC de Monterrey × SAP
**Fecha:** 3–4 Mayo 2026  
**Autor:** Cloud Integration Engineer  
**Sesión:** #3 de trabajo en este proyecto  
**Estado al cierre:** ✅ Pipeline estable · ✅ ALERTS persistiendo en HANA · ⏳ model.py en diseño

---

## 1. Resumen ejecutivo de la sesión

Esta sesión tuvo tres logros principales:

1. **Bug crítico resuelto:** `DBADMIN.ALERTS` estaba vacía porque `pipeline_loop.py` llamaba a `enviar_alerta()` con `conn=None`. Se implementó `_abrir_conexion_hana()` dedicada para el bloque de alerting. Resultado: 8/8 alertas con `alerted=1` confirmadas.

2. **Schema real de HANA documentado:** Los nombres de columnas en HANA difieren significativamente de los nombres en el raw de la API. Se mapearon las 16 columnas de `RAW_LOGS_SISTEMA` y las 21 de `RAW_LOGS_LLM` con sus tipos exactos.

3. **Diseño completo de `model.py` finalizado:** Arquitectura, features, ETL, thresholding y contrato de integración completamente especificados antes de escribir código.

---

## 2. Estado del pipeline al cierre de sesión

### Cloud Foundry — sap-ai-soc-papoi

| Métrica | Valor |
|---|---|
| Ciclos completados desde el 1 Mayo | 460+ |
| Ventana de ingesta | Cada 30 min UTC |
| Ciclo de polling | Cada 2 min |
| Registros por ventana | ~3,900–5,700 |
| Amenazas detectadas por ventana | 3–5 (quick_filter) |
| MTTD medido | ~1 segundo (desde ingesta hasta HTTP 201) |
| Crashes desde el deploy | 0 |

### HANA — conteos al cierre

| Tabla | Registros |
|---|---|
| `DBADMIN.RAW_LOGS_SISTEMA` | 1,026,865 |
| `DBADMIN.RAW_LOGS_LLM` | 584,583 |
| `DBADMIN.ALERTS` (total) | 8 |
| `DBADMIN.ALERTS` (alerted=1) | 8 |
| `DBADMIN.ALERTS` (alerted=0) | 0 |

---

## 3. Bug corregido — persistencia en DBADMIN.ALERTS

### Síntoma

`SELECT COUNT(*) FROM DBADMIN.ALERTS` → 0, a pesar de que los logs de CF mostraban HTTP 201 confirmado en todas las alertas.

### Causa raíz

`pipeline_loop.py` línea 352 llamaba `enviar_alerta(..., conn=None)`. `alerting.py` está correctamente implementado con `if conn is not None` antes de cada operación HANA — con `conn=None` nunca tocaba la base de datos. `ingest_and_persist()` cierra su conexión HANA internamente antes de retornar, dejando sin conexión al bloque de alerting.

### Solución implementada

Se añadieron dos funciones a `pipeline_loop.py`:

```python
def _abrir_conexion_hana():
    """
    Abre una conexión HANA usando las credenciales de config.py.
    Retorna la conexión si tiene éxito, None si falla (sin lanzar excepción).
    """
    try:
        from config import HANA_HOST, HANA_PORT, HANA_USER, HANA_PASSWORD
        import hdbcli.dbapi as hdb
        conn = hdb.connect(
            address=HANA_HOST,
            port=int(HANA_PORT),
            user=HANA_USER,
            password=HANA_PASSWORD,
        )
        return conn
    except Exception as e:
        logger.warning(f"[HANA] No se pudo abrir conexión para alerting: {e}")
        return None


def _cerrar_conexion_hana(conn):
    """Cierra la conexión HANA silenciosamente."""
    if conn is not None:
        try:
            conn.close()
        except Exception:
            pass
```

Y el bloque DETECT+RESPOND fue modificado:

```python
if df_nuevos is not None and not df_nuevos.empty:
    conn_alerting = _abrir_conexion_hana()
    try:
        resumen_alertas = ejecutar_deteccion_y_alertas(
            df_nuevos      = df_nuevos,
            ventana_inicio = ventana_inicio,
            conn           = conn_alerting,   # ← ahora sí pasa conn real
        )
    finally:
        _cerrar_conexion_hana(conn_alerting)
```

### Verificación

```
DBADMIN.ALERTS COUNT(*)         → 8
DBADMIN.ALERTS WHERE alerted=1  → 8
DBADMIN.ALERTS WHERE alerted=0  → 0
```

Los `alert_id` en HANA coinciden exactamente con los `alert_id` en los logs de CF. Trazabilidad completa confirmada.

---

## 4. Aclaración sobre "HANA no disponible en este ciclo"

**No es un bug.** Cuando `nuevos=0`, el pipeline omite el bloque de HANA (no hay nada que insertar) y loguea el mensaje engañoso "HANA no disponible en este ciclo — datos en CSV". HANA está perfectamente operativa. El mensaje simplemente indica que el ciclo de inserción fue omitido por deduplicación. No requiere corrección urgente, pero debe documentarse para no confundir al equipo.

---

## 5. Límite de llamadas API desde el 4 de Mayo

A partir del 4 de Mayo, la API SAP limita a **100 llamadas máximo por 30 minutos por equipo**.

### Consumo actual por ventana

| Operación | Llamadas |
|---|---|
| `GET /info` (descubrir total_pages) | 1 |
| `GET /logs/current?page=N` (~10 páginas) | ~10 |
| `POST /alert` por amenaza (~3–5 por ventana) | ~3–5 |
| **Total por ventana de 30 min** | **~14–16** |

Estamos muy por debajo del límite. **Acción requerida para `model.py`:** imponer cap duro de máximo 5 alertas por ciclo largo para no acercarse al límite cuando ambos detectores operen simultáneamente.

---

## 6. Schema real de HANA — mapeo completo

### Diferencia crítica de nomenclatura

Los nombres de columnas en HANA son distintos a los nombres en el raw de la API SAP. El ETL que hace `ingest_and_persist()` transforma los nombres al insertarlos. Todo código que lea desde HANA debe usar los nombres de HANA, no los de la API.

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
| 13 | `HTTP_STATUS` | NVARCHAR | 10 | `http_status_code` |
| 14 | `CLIENT_IP` | NVARCHAR | 50 | `client_ip` |
| 15 | `REQUEST_METHOD` | NVARCHAR | 20 | `headers_http_request_method` |
| 16 | `REQUEST_PATH` | NVARCHAR | 500 | `heathers_request_path` (typo de API, corregido en HANA) |

**Nota importante:** `HTTP_STATUS` es `NVARCHAR`, no entero. Requiere `int(row['HTTP_STATUS'])` al leer.

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
| 14 | `LLM_STATUS` | NVARCHAR | 50 | `llm_status` |
| 15 | `LLM_ERROR_MESSAGE` | NVARCHAR | 1000 | `llm_error_message` |
| 16 | `LLM_PROMPT_CATEGORY` | NVARCHAR | 100 | `llm_prompt_category` |
| 17 | `LLM_TOTAL_TOKENS` | INTEGER | 10 | `llm_total_tokens` |
| 18 | `LLM_COST_USD` | DOUBLE | 15 | `llm_cost_usd` |
| 19 | `LLM_RESPONSE_TIME` | DOUBLE | 15 | `llm_response_time_ms` (**sin `_ms` en HANA, unidad sigue siendo ms**) |
| 20 | `LLM_TEMPERATURE` | DOUBLE | 15 | `llm_temperature` |
| 21 | `LLM_FINISH_REASON` | NVARCHAR | 50 | `llm_finish_reason` |

**Nota crítica:** `LLM_RESPONSE_TIME` está en milisegundos (confirmado: MIN=200ms, MAX=34,999ms). El umbral de `quick_filter` de 10,000ms es correcto.

---

## 7. Distribuciones reales de datos — base para decisiones de diseño

### RAW_LOGS_SISTEMA — HTTP_STATUS (16 valores distintos)

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

**Distribución por familia:** 2xx=71% · 4xx=13% · 5xx=8% · 3xx=7%

### RAW_LOGS_SISTEMA — LOG_TYPE (7 valores)

| LOG_TYPE | Count |
|---|---|
| INFO | 386,460 |
| WARNING | 190,377 |
| ERROR | 143,564 |
| AUDIT | 96,038 |
| DEBUG | 95,826 |
| PERF | 95,353 |
| SECURITY | 29,187 |

**SECURITY** representa el 2.8% del total — señal de anomalía bien definida.

### RAW_LOGS_LLM — LOG_TYPE (3 valores)

| LOG_TYPE | Count |
|---|---|
| LLM_REQUEST | 413,532 |
| LLM_ERROR | 117,753 |
| LLM_TIMEOUT | 58,578 |

**Tasa de error+timeout:** (117,753 + 58,578) / 589,863 = **~30%**. El sistema LLM tiene problemas sistémicos persistentes. El modelo ML debe aprender este baseline anómalo como normal.

### RAW_LOGS_LLM — Rangos numéricos

| Métrica | MIN | MAX | AVG |
|---|---|---|---|
| `LLM_COST_USD` | 0.000007 | 0.13892 | 0.01247 |
| `LLM_RESPONSE_TIME` (ms) | 200.09 | 34,999.82 | 8,781.94 |
| `LLM_TOTAL_TOKENS` | 84 | 3,498 | — |

Todas las métricas LLM son heavy-tailed → transformación `log1p` obligatoria.

### Cardinalidades categóricas clave

| Variable | Cardinalidad | Implicación |
|---|---|---|
| `APPLICATION` (sistema) | 10 | OrdinalEncoder directo |
| `REGION_NAME` | 108 | OrdinalEncoder con max_categories=32 |
| `CLIENT_IP` | 105 | **No encodear raw** — construir features de comportamiento |
| `LOG_TYPE` sistema | 7 | OrdinalEncoder directo |
| `LLM_STATUS` | 3 | OrdinalEncoder directo |

---

## 8. Arquitectura de model.py — decisiones definitivas

### Principio rector

**No forzar logs de sistema y LLM en un solo vector.** Son poblaciones con columnas estructuralmente ausentes entre sí. Un modelo único aprendería el ruido de nulidad en lugar de patrones de anomalía.

### Tres modelos, dos niveles

```
Nivel 1 — Evento individual:
  ┌─────────────────────────────────────────────────────────────┐
  │  IF_sistema   → Isolation Forest sobre logs de Sistema      │
  │  IF_llm       → Isolation Forest sobre logs de LLM         │
  └─────────────────────────────────────────────────────────────┘

Nivel 2 — Comportamiento agregado (ventana 30 min):
  ┌─────────────────────────────────────────────────────────────┐
  │  LOF_ip       → Local Outlier Factor sobre tabla por IP     │
  │                 (~105 filas, una por IP única)              │
  └─────────────────────────────────────────────────────────────┘
```

### Justificación de algoritmos

**Isolation Forest** para niveles de evento:
- Diseñado para datos contaminados sin labels
- Complejidad O(t × ψ × log ψ) donde ψ=256 (submuestra) — muy rápido
- Tolera alta dimensionalidad y datos mixtos con OrdinalEncoder
- Con `n_jobs=-1` usa todos los cores disponibles en CF

**Local Outlier Factor** para tabla IP:
- 105 filas → LOF con n_neighbors=20 es computacionalmente trivial
- Detecta IPs cuyo comportamiento es anómalo respecto a sus pares, no globalmente
- Ideal para: IPs con conteos normales pero combinaciones inusuales de paths/apps/status

**Se descartan para este hackathon:**
- Autoencoder/Keras: dependencias extra, más hiperparámetros, deadline de 3 días
- One-Class SVM: cuadrático en n_samples, sensible a outliers en training
- DBSCAN: O(n²) memoria, muy sensible a parámetros en datos heterogéneos
- Elliptic Envelope: asume distribución Gaussiana unimodal — incorrecto para estos datos

### Configuración sklearn

```python
# Sistema — Isolation Forest
IsolationForest(
    n_estimators=200,
    max_samples=256,      # submuestra óptima según paper original
    contamination="auto",
    random_state=42,
    n_jobs=-1
)

# LLM — Isolation Forest
IsolationForest(
    n_estimators=200,
    max_samples=256,
    contamination="auto",
    random_state=42,
    n_jobs=-1
)

# IP behavior — Local Outlier Factor
LocalOutlierFactor(
    n_neighbors=20,       # seguro con 105 IPs
    contamination="auto",
    novelty=False         # fit_predict en cada ciclo (no novelty detection)
)
```

---

## 9. Feature Engineering — especificación completa

### 9.1 Features de Sistema (para IF_sistema)

#### Numéricas derivadas de HTTP_STATUS
```python
"status_family"    # int: http_status // 100  → valores: 2, 3, 4, 5
"is_4xx"          # int: 1 si status_family == 4, else 0
"is_5xx"          # int: 1 si status_family == 5, else 0
"is_401_or_403"   # int: 1 si HTTP_STATUS in ('401', '403'), else 0
"is_429"          # int: 1 si HTTP_STATUS == '429', else 0
```

**Nota:** `HTTP_STATUS` es NVARCHAR en HANA → convertir con `int(val)` antes de usar.

#### Numéricas temporales
```python
"hour_utc"        # int: EVENT_TIMESTAMP.hour  → 0-23
```

#### Categóricas (OrdinalEncoder para IF — recomendado por sklearn para tree-based)
```python
"LOG_TYPE"        # 7 valores: INFO/WARNING/ERROR/AUDIT/DEBUG/PERF/SECURITY
"APPLICATION"     # 10 valores
"REGION_NAME"     # 108 valores → OrdinalEncoder(max_categories=32, handle_unknown='use_encoded_value', unknown_value=-1)
```

#### Columnas que NO se usan en el evento individual
- `CLIENT_IP` raw → solo en tabla agregada
- `REQUEST_PATH` raw → solo en tabla agregada (o features de estructura)
- `MESSAGE` raw → normalización de template es trabajo futuro post-hackathon
- `LOG_ID`, `INGESTED_AT` → metadatos, no features

### 9.2 Features de LLM (para IF_llm)

#### Numéricas transformadas (log1p por heavy-tail)
```python
"log1p_cost"          # log1p(LLM_COST_USD)         → rango: [0.000007, 0.139]
"log1p_response_time" # log1p(LLM_RESPONSE_TIME)     → rango: [200, 34999] ms
"log1p_total_tokens"  # log1p(LLM_TOTAL_TOKENS)      → rango: [84, 3498]
"hour_utc"            # int: EVENT_TIMESTAMP.hour
```

#### Categóricas (OrdinalEncoder para IF)
```python
"LLM_STATUS"          # 3 valores: success / error / timeout
"LLM_MODEL_ID"        # cardinalidad por confirmar con datos reales
"LLM_PROVIDER"        # cardinalidad por confirmar
"LOG_TYPE"            # 3 valores: LLM_REQUEST / LLM_ERROR / LLM_TIMEOUT
```

#### Columnas que NO se usan en evento individual
- `LLM_ERROR_MESSAGE` → texto libre, trabajo futuro
- `LLM_PROMPT_CATEGORY` → potencialmente útil, agregar en v2

### 9.3 Features de comportamiento por IP (para LOF_ip)

Una fila por IP única en la ventana activa. ~105 filas.

```python
"event_count"          # total eventos de esta IP en la ventana
"distinct_paths"       # COUNT(DISTINCT REQUEST_PATH)
"distinct_apps"        # COUNT(DISTINCT APPLICATION)
"ratio_4xx"           # count(status_family==4) / event_count
"ratio_5xx"           # count(status_family==5) / event_count
"ratio_security"      # count(LOG_TYPE=='SECURITY') / event_count
"has_401_or_403"      # int: 1 si algún evento fue 401 o 403
"has_429"             # int: 1 si algún evento fue 429
"n_distinct_status"   # COUNT(DISTINCT HTTP_STATUS) — IPs con muchos status distintos son sospechosas
```

**Encoding para LOF:** OneHotEncoder + RobustScaler (LOF necesita distancias métricas).

---

## 10. Estrategia de entrenamiento y thresholding

### Modo de operación (dos fases)

**Cold-start (primeras ventanas, sin historia suficiente):**
- Fit + predict sobre la ventana actual misma
- Threshold: IQR sobre los scores del batch
  - `Q3 + 1.5 × IQR` para marcar como anómalo
- Cap: máximo 5 alertas por ciclo del modelo

**Modo histórico (después de 20+ ventanas acumuladas):**
- Entrenar sobre historia rolling (últimas N ventanas, registros con reglas ya conocidas down-weighted)
- Scorear la ventana nueva
- Threshold: MAD (Median Absolute Deviation)
  - Modified Z-score: `|0.6745 × (score - median) / MAD| > 3.5`
- Cap: máximo 5 alertas por ciclo del modelo

### Detección de cold-start vs. modo histórico

```python
# En HANA, contar ventanas distintas acumuladas:
SELECT COUNT(DISTINCT DATE_TRUNC('MINUTE', EVENT_TIMESTAMP)) 
FROM DBADMIN.RAW_LOGS_SISTEMA;
# Si > 40 (20 ventanas × 2 timestamps por ventana) → modo histórico
```

### Por qué cap de 5 alertas

- Con límite de 100 llamadas/30min y `quick_filter` usando ~15 llamadas (ingesta + alertas)
- El modelo ML opera en ciclo largo de 28 min → potencialmente simultáneo
- Cap de 5 deja margen seguro: 15 (quick_filter) + 5 (model) + 10 (ingesta) = 30, muy por debajo de 100

---

## 11. Contrato de integración con pipeline_loop.py

### Función principal que debe exponer model.py

```python
def analizar_ventana(df: pd.DataFrame) -> list[dict]:
    """
    Detecta anomalías estadísticas en la ventana actual de logs.
    
    Parámetros
    ----------
    df : pd.DataFrame
        DataFrame con los registros de la ventana actual.
        Puede contener tanto logs de Sistema como LLM (con NaN donde no aplica).
        El modelo filtra internamente por LOG_TYPE.
    
    Retorna
    -------
    list[dict] — cada dict tiene exactamente:
        {
            "alert_type": str,    # "ml_sistema_anomaly" | "ml_llm_anomaly" | "ml_ip_anomaly"
            "severity":   str,    # "low" | "medium" | "high"
            "details":    str,    # descripción legible, máx 250 chars (deja margen para WHAT/WHEN)
            "log_id":     str,    # LOG_ID del registro más representativo de la anomalía
            "event_time": str,    # EVENT_TIMESTAMP ISO del registro
        }
    
    Nunca lanza excepciones — cualquier error retorna lista vacía.
    Cap interno: máximo 5 elementos en la lista retornada.
    """
```

### Nuevos alert_types para model.py

```python
"ml_sistema_anomaly"  # IF_sistema detectó record con score anómalo
"ml_llm_anomaly"      # IF_llm detectó record con score anómalo
"ml_ip_anomaly"       # LOF_ip detectó comportamiento de IP anómalo
```

Estos se registran con `source="model_ml"` en `DBADMIN.ALERTS`.

### Cómo se integra en pipeline_loop.py

```python
# En el bloque del ciclo largo (cada ~28 min):
if minutos_desde_largo >= (INTERVALO_POLLING_LARGO / 60):
    try:
        from model import analizar_ventana
        df_ventana = _cargar_ventana_para_modelo(conn_hana)  # query a HANA
        anomalias_ml = analizar_ventana(df_ventana)
        
        conn_alerting_ml = _abrir_conexion_hana()
        try:
            for anomalia in anomalias_ml:
                enviar_alerta(
                    **anomalia,
                    conn=conn_alerting_ml,
                    source="model_ml",
                )
        finally:
            _cerrar_conexion_hana(conn_alerting_ml)
    except Exception as e:
        logger.error(f"[CICLO-LARGO] Error en model.py: {e}")
    
    ultimo_ciclo_largo = ahora_utc
```

---

## 12. Queries de extracción desde HANA para model.py

### Extracción de Sistema (ventana actual)

```sql
SELECT
    LOG_ID,
    EVENT_TIMESTAMP,
    LOG_TYPE,
    HTTP_STATUS,
    CLIENT_IP,
    REQUEST_PATH,
    APPLICATION,
    REGION_NAME,
    MESSAGE
FROM DBADMIN.RAW_LOGS_SISTEMA
WHERE EVENT_TIMESTAMP >= ADD_SECONDS(NOW(), -1800)
ORDER BY EVENT_TIMESTAMP DESC
```

### Extracción de LLM (ventana actual)

```sql
SELECT
    LOG_ID,
    EVENT_TIMESTAMP,
    LOG_TYPE,
    LLM_STATUS,
    LLM_MODEL_ID,
    LLM_PROVIDER,
    LLM_COST_USD,
    LLM_RESPONSE_TIME,
    LLM_TOTAL_TOKENS,
    LLM_TEMPERATURE,
    LLM_ERROR_MESSAGE,
    LLM_PROMPT_CATEGORY
FROM DBADMIN.RAW_LOGS_LLM
WHERE EVENT_TIMESTAMP >= ADD_SECONDS(NOW(), -1800)
ORDER BY EVENT_TIMESTAMP DESC
```

### Extracción histórica para entrenamiento (rolling 24h)

```sql
-- Sistema histórico
SELECT LOG_ID, EVENT_TIMESTAMP, LOG_TYPE, HTTP_STATUS,
       CLIENT_IP, REQUEST_PATH, APPLICATION, REGION_NAME
FROM DBADMIN.RAW_LOGS_SISTEMA
WHERE EVENT_TIMESTAMP >= ADD_SECONDS(NOW(), -86400)  -- 24 horas
  AND EVENT_TIMESTAMP < ADD_SECONDS(NOW(), -1800)    -- excluir ventana actual
ORDER BY EVENT_TIMESTAMP ASC

-- LLM histórico
SELECT LOG_ID, EVENT_TIMESTAMP, LOG_TYPE, LLM_STATUS,
       LLM_MODEL_ID, LLM_PROVIDER, LLM_COST_USD,
       LLM_RESPONSE_TIME, LLM_TOTAL_TOKENS, LLM_TEMPERATURE
FROM DBADMIN.RAW_LOGS_LLM
WHERE EVENT_TIMESTAMP >= ADD_SECONDS(NOW(), -86400)
  AND EVENT_TIMESTAMP < ADD_SECONDS(NOW(), -1800)
ORDER BY EVENT_TIMESTAMP ASC
```

---

## 13. Plan de implementación — próximos pasos

### Paso A — `app/hana_reader.py`

Módulo de extracción desde HANA. Responsabilidad única: abrir conexión, ejecutar queries, retornar DataFrames con tipos correctos. Testeable en aislamiento.

**Funciones a implementar:**
- `leer_sistema_ventana_actual(conn) → pd.DataFrame`
- `leer_llm_ventana_actual(conn) → pd.DataFrame`
- `leer_sistema_historico(conn, horas=24) → pd.DataFrame`
- `leer_llm_historico(conn, horas=24) → pd.DataFrame`
- `contar_ventanas_acumuladas(conn) → int` (para cold-start vs. histórico)

### Paso B — `app/feature_eng.py`

Transformaciones puras sin sklearn. Funciones que reciben DataFrame y retornan DataFrame con features listas. Testeables sin HANA ni modelo.

**Funciones a implementar:**
- `build_sistema_features(df_sistema) → pd.DataFrame`
- `build_llm_features(df_llm) → pd.DataFrame`
- `build_ip_behavior_table(df_sistema) → pd.DataFrame`

### Paso C — `app/model.py`

Los tres modelos + thresholding + función principal `analizar_ventana()`.

**Estructura interna:**
- `_entrenar_o_cargar_modelos(df_hist_sis, df_hist_llm)` → devuelve `(if_sis, if_llm)`
- `_threshold_iqr(scores)` → umbral cold-start
- `_threshold_mad(scores, score_history)` → umbral histórico
- `analizar_ventana(df) → list[dict]` → función pública, contrato del pipeline

### Paso D — Integración en `pipeline_loop.py`

Reemplazar el placeholder del ciclo largo con llamada real a `model.analizar_ventana()`.

---

## 14. MTTD formal — para el reporte de evaluación

**MTTD medido en ciclo #455 (1 Mayo 2026):**

```
@timestamp del evento en API:    2026-05-01T06:30:00 UTC (inicio ventana)
Ingesta completada:              2026-05-01T06:31:35 UTC
Detección completada:            2026-05-01T06:31:35 UTC  (< 1 seg después)
Primer HTTP 201 confirmado:      2026-05-01T06:31:36 UTC  (310ms de red)

MTTD = ~1 segundo desde detección hasta confirmación SAP
MTTD desde inicio de ventana = ~95 segundos (1 min 35 seg)
```

**Este número corresponde al criterio #1 (40% del score).** El objetivo del hackathon era ≤ 2 minutos. Estamos en < 2 segundos de latencia de alerting puro, y < 2 minutos desde el primer log de la ventana hasta confirmación SAP.

---

## 15. Archivos modificados en esta sesión

```
sap-security-hackathon/
├── pipeline_loop.py     ← MODIFICADO: _abrir_conexion_hana() + _cerrar_conexion_hana()
│                                       + conn=conn_alerting en bloque DETECT+RESPOND
├── REPORTE_SESION_03MAY2026_Y_DISEÑO_MODELO_ML.md  ← NUEVO (este archivo)
└── app/
    ├── hana_reader.py   ← PENDIENTE (Paso A)
    ├── feature_eng.py   ← PENDIENTE (Paso B)
    └── model.py         ← PENDIENTE (Paso C)
```

---

*Reporte generado al cierre de sesión — 3 Mayo 2026, ~19:00 CST*  
*Cloud Integration Engineer — SAP AI Security Anomaly Detection Hackathon*
