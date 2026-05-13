# Architecture — Module Documentation

**SAP AI Security SOC · Pipeline Internals**

This document describes every module in the pipeline: what it does, its public interface, how it connects to other modules, and how to use it. For a high-level overview of the system, see [`../README.md`](../README.md). For the complete technical report including design decisions and ML methodology, see [`TECHNICAL_REPORT.md`](TECHNICAL_REPORT.md).

---

## Table of Contents

1. [Pipeline Execution Flow](#1-pipeline-execution-flow)
2. [pipeline_loop.py — Orchestrator](#2-pipeline_looppy--orchestrator)
3. [config.py — Credential Management](#3-configpy--credential-management)
4. [ingest.py — Data Extraction and Persistence](#4-ingestpy--data-extraction-and-persistence)
5. [hana_client.py — HANA Write Operations](#5-hana_clientpy--hana-write-operations)
6. [hana_reader.py — HANA Read Operations](#6-hana_readerpy--hana-read-operations)
7. [quick_filter.py — Deterministic Threat Detection](#7-quick_filterpy--deterministic-threat-detection)
8. [alerting.py — Alert Dispatch](#8-alertingpy--alert-dispatch)
9. [feature_eng.py — Feature Engineering](#9-feature_engpy--feature-engineering)
10. [model.py — Machine Learning Detection](#10-modelpy--machine-learning-detection)
11. [Module Dependency Map](#11-module-dependency-map)

---

## 1. Pipeline Execution Flow

The pipeline runs two cycles concurrently from a single process:

```
SHORT CYCLE (every 2 minutes):

    config.py
        ↓
    ingest.py  →  fetch_current_window()  →  API GET /info + /logs/current
        ↓
    ingest.py  →  save_data()             →  CSV backup in data/raw/
        ↓
    ingest.py  →  ingest_and_persist()    →  hana_client.upsert_logs()  →  HANA
        ↓
    quick_filter.py  →  filtrar_amenazas(df_nuevos)   →  list of threats
        ↓
    alerting.py  →  enviar_alerta() per threat        →  POST /alert + HANA ALERTS


LONG CYCLE (every 28 minutes):

    hana_reader.py  →  read 24h historical data from HANA
        ↓
    feature_eng.py  →  transform raw data into numeric feature matrices
        ↓
    model.py  →  analizar_ventana()  →  IF_sistema + IF_llm + LOF_ip
        ↓
    alerting.py  →  enviar_alerta() per anomaly  →  POST /alert + HANA ALERTS
```

Every module import in `pipeline_loop.py` is conditional. If `quick_filter`, `alerting`, or `model` fail to import, the pipeline continues ingesting data. Ingestion is the critical path — detection modules are non-fatal.

---

## 2. pipeline_loop.py — Orchestrator

**Location:** Project root

**Purpose:** Run the complete OBSERVE → ANALYZE → DETECT → RESPOND cycle continuously. This is the entry point executed by Cloud Foundry.

**How it works:** On startup, it validates the configuration, runs one immediate ingestion cycle, then enters an infinite loop polling every 2 minutes. Every 28 minutes, it also triggers the ML analysis cycle. All errors are caught and logged — the loop never crashes permanently (except on HTTP 401, which indicates an invalid token).

### Public Functions

#### `ejecutar_ciclo_ingesta(numero_ciclo: int) -> bool`

Executes one complete short cycle: ingestion → deduplication → HANA upsert → quick filter → alerting.

```python
# Returns True on success, False on recoverable error
exito = ejecutar_ciclo_ingesta(numero_ciclo=1)
```

#### `ejecutar_deteccion_y_alertas(df_nuevos, ventana_inicio, conn=None) -> dict`

Runs quick_filter on new records and dispatches alerts for each detected threat.

```python
resumen = ejecutar_deteccion_y_alertas(
    df_nuevos=df_nuevos,         # DataFrame from ingest_and_persist()
    ventana_inicio="2026-05-04T00:00:00+00:00",
    conn=hana_connection,        # optional — logs alerts to HANA if provided
)
# resumen = {
#     "amenazas_detectadas": 3,
#     "alertas_enviadas": 2,
#     "alertas_fallidas": 0,
#     "duplicados": 1,
# }
```

#### `main()`

Entry point. Validates config, runs the first cycle immediately, then loops indefinitely.

```bash
python pipeline_loop.py
```

### Error Handling

| Error Type | Behavior |
|---|---|
| Timeout / ConnectionError | Log warning, wait 60s, retry |
| HTTP 5xx | Log warning, wait 60s, retry |
| HTTP 401 | Fatal — log error, `sys.exit(1)` |
| Module import failure | Degrade gracefully — continue without that module |
| `KeyboardInterrupt` | Clean shutdown with cycle count report |

---

## 3. config.py — Credential Management

**Location:** `app/config.py`

**Purpose:** Centralize all credential access. This is the only module that reads from `.env` or `VCAP_SERVICES`. Every other module imports credentials from here.

### How Credentials Are Loaded

1. Compute the absolute path to `.env` relative to `config.py`'s location (works regardless of the working directory).
2. Call `load_dotenv(dotenv_path=..., override=False)` — system environment variables take precedence.
3. For HANA: check direct environment variables first, then `VCAP_SERVICES` (Cloud Foundry service binding), then fall back to empty strings.

### Exported Variables

```python
from config import (
    API_BASE_URL,      # str — base URL of the SAP API
    BEARER_TOKEN,      # str — team authentication token
    HANA_HOST,         # str — HANA Cloud host
    HANA_PORT,         # str — typically "443"
    HANA_USER,         # str — database user
    HANA_PASSWORD,     # str — database password
)
```

### Public Functions

#### `validate_config() -> None`

Verifies that `API_BASE_URL` and `BEARER_TOKEN` are present. Raises `EnvironmentError` with a clear message listing missing variables if validation fails.

```python
from config import validate_config

try:
    validate_config()
except EnvironmentError as e:
    print(f"Missing configuration: {e}")
    sys.exit(1)
```

#### `get_headers() -> dict`

Returns the HTTP Authorization header. Built fresh on each call (supports token rotation).

```python
from config import get_headers

response = requests.get(url, headers=get_headers(), timeout=15)
# headers = {"Authorization": "Bearer <token>"}
```

---

## 4. ingest.py — Data Extraction and Persistence

**Location:** `app/ingest.py`

**Purpose:** Extract all logs from the current API window, deduplicate in memory, save to CSV, and upsert to HANA.

### Public Functions

#### `fetch_current_window() -> tuple[pd.DataFrame, dict]`

Calls `GET /info` to discover the total number of pages, then iterates `GET /logs/current?page=1..N` to extract all records.

```python
from ingest import fetch_current_window

df, meta = fetch_current_window()
# df    → DataFrame with all records from the current 30-min window
# meta  → {"window_start": "...", "window_end": "...", "total_records": 5551, ...}
```

#### `save_data(df: pd.DataFrame, meta: dict) -> str`

Saves the DataFrame to `data/raw/logs_<timestamp>.csv` and updates `data/raw/sample_10.csv`. Returns the absolute path to the saved CSV.

```python
from ingest import save_data

csv_path = save_data(df, meta)
# csv_path = "/path/to/data/raw/logs_20260504T000000.csv"
```

#### `ingest_and_persist() -> dict`

Main orchestrator function. Calls `fetch_current_window()` → deduplication → `save_data()` → `upsert_logs()` (if HANA is available). Returns a result dictionary including the new records DataFrame.

```python
from ingest import ingest_and_persist

resultado = ingest_and_persist()
# resultado = {
#     "registros": 5551,          # total from API
#     "nuevos": 327,              # new since last poll
#     "ventana_inicio": "...",
#     "ventana_fin": "...",
#     "csv": "/path/to/file.csv",
#     "hana": {"sistema": 250, "llm": 77},  # or None
#     "df_nuevos": pd.DataFrame,  # for quick_filter
# }
```

### Deduplication

The module maintains two deduplication layers:

1. **In-memory set** (`ids_procesados`): tracks `_id` values seen in the current window. Resets when the window changes.
2. **HANA-side check**: `upsert_logs()` queries existing `log_id` values before inserting.

This two-layer approach handles both normal polling overlap and Cloud Foundry restarts.

---

## 5. hana_client.py — HANA Write Operations

**Location:** `app/hana_client.py`

**Purpose:** Manage all write interactions with SAP HANA Cloud. This is the only module that executes INSERT statements.

### Public Functions

#### `get_connection()`

Creates and returns an active HANA connection using credentials from `config.py`.

```python
from hana_client import get_connection

conn = get_connection()
# conn is an hdbcli.dbapi Connection object
# encrypt=True, sslValidateCertificate=False (required for HANA Cloud trial)
```

#### `hana_esta_viva() -> bool`

Lightweight health check. Executes `SELECT 1 FROM DUMMY` and returns `True`/`False`. Never raises exceptions.

```python
from hana_client import hana_esta_viva

if hana_esta_viva():
    # proceed with HANA operations
```

#### `create_tables()`

Creates `RAW_LOGS_SISTEMA`, `RAW_LOGS_LLM`, and `ALERTS` if they do not exist. Idempotent — safe to call multiple times.

```python
from hana_client import create_tables

create_tables()  # tables now exist in HANA
```

#### `upsert_logs(df_nuevos: pd.DataFrame) -> dict`

Inserts new records into HANA. Performs a SELECT-based deduplication check before inserting to avoid duplicates after CF restarts. Uses `executemany()` for batch efficiency.

```python
from hana_client import upsert_logs

conteos = upsert_logs(df_nuevos)
# conteos = {"sistema": 250, "llm": 77}
```

### Safe Type Conversion

HANA expects `None` for NULL columns, but pandas uses `float('nan')`. Four helper functions handle the conversion:

| Function | Converts to | Handles |
|---|---|---|
| `_safe_str(row, col)` | `str` or `None` | NaN → None, truncates to max_len |
| `_safe_float(row, col)` | `float` or `None` | NaN → None, non-numeric → None |
| `_safe_int(row, col)` | `int` or `None` | NaN → None, handles "1735.0" via float |
| `_safe_ts(row, col)` | `datetime` (naive) or `None` | ISO 8601 → datetime, strips tzinfo |

---

## 6. hana_reader.py — HANA Read Operations

**Location:** `app/hana_reader.py`

**Purpose:** Read data from HANA for the ML models. Separate from `hana_client.py` because it reads instead of writes and handles large historical volumes.

### Important: Column Name Mapping

The API raw column names differ from the HANA column names. This module returns DataFrames using the HANA names:

| API Raw Name | HANA Column Name |
|---|---|
| `sap_function_log_type` | `LOG_TYPE` |
| `http_status_code` | `HTTP_STATUS` |
| `client_ip` | `CLIENT_IP` |
| `heathers_request_path` | `REQUEST_PATH` |
| `sap_function_application` | `APPLICATION` |
| `llm_cost_usd` | `LLM_COST_USD` |
| `llm_response_time_ms` | `LLM_RESPONSE_TIME` |

### Public Functions

#### `abrir_conexion_hana()`

Opens a HANA connection. Returns `None` on failure (never raises exceptions).

```python
from hana_reader import abrir_conexion_hana

conn = abrir_conexion_hana()
try:
    # use conn
finally:
    if conn:
        conn.close()
```

#### `leer_sistema_ventana_actual(conn) -> pd.DataFrame`

Returns system logs from the last 30 minutes. Columns: `LOG_ID`, `EVENT_TIMESTAMP`, `LOG_TYPE`, `HTTP_STATUS`, `CLIENT_IP`, `REQUEST_PATH`, `APPLICATION`, `REGION_NAME`, `MESSAGE`.

#### `leer_llm_ventana_actual(conn) -> pd.DataFrame`

Returns LLM logs from the last 30 minutes. Columns: `LOG_ID`, `EVENT_TIMESTAMP`, `LOG_TYPE`, `LLM_STATUS`, `LLM_MODEL_ID`, `LLM_PROVIDER`, `LLM_COST_USD`, `LLM_RESPONSE_TIME`, `LLM_TOTAL_TOKENS`, `LLM_TEMPERATURE`, `LLM_ERROR_MESSAGE`, `LLM_PROMPT_CATEGORY`.

#### `leer_sistema_historico(conn, horas=24) -> pd.DataFrame`

Returns system logs from the last N hours, excluding the current 30-minute window (to avoid training on scoring data).

#### `leer_llm_historico(conn, horas=24) -> pd.DataFrame`

Same as above for LLM logs.

#### `contar_ventanas_acumuladas(conn) -> int`

Counts distinct 30-minute windows in HANA. Used to determine if there is enough data for historical mode (threshold: 20 windows).

#### `en_modo_historico(conn) -> bool`

Returns `True` if accumulated windows ≥ 20 (historical mode), `False` for cold-start mode.

```python
from hana_reader import (
    abrir_conexion_hana,
    leer_sistema_ventana_actual,
    leer_sistema_historico,
    en_modo_historico,
)

conn = abrir_conexion_hana()
modo = en_modo_historico(conn)         # True if >= 20 windows
df_actual = leer_sistema_ventana_actual(conn)  # last 30 min
df_hist = leer_sistema_historico(conn, horas=24)  # last 24h (excl. current window)
conn.close()
```

---

## 7. quick_filter.py — Deterministic Threat Detection

**Location:** `app/quick_filter.py`

**Purpose:** Apply 6 rule-based detection checks to each batch of new records and return a list of detected threats. This module does not send alerts — it only detects.

### Position in Pipeline

```
upsert_logs(df_nuevos)          ← data already in HANA
        ↓
filtrar_amenazas(df_nuevos)     ← THIS MODULE — operates on raw data, not ETL
        ↓
enviar_alerta() per threat
```

### Public Function

#### `filtrar_amenazas(df: pd.DataFrame) -> list[dict]`

Analyzes a batch of new records and returns all detected threats.

```python
from quick_filter import filtrar_amenazas

amenazas = filtrar_amenazas(df_nuevos)
# amenazas = [
#     {
#         "alert_type": "brute_force",
#         "severity": "high",
#         "details": "6 auth failures from IP 203.0.113.99 in this polling cycle (6 HTTP 401)",
#         "log_id": "bf-001",
#         "event_time": "2026-04-30T12:00:01Z",
#     },
#     ...
# ]
```

### Detection Rules

#### Rule 1 — Security Events (`security_event`)

Triggers on any log with `sap_function_log_type == 'SECURITY'`. Produces one grouped alert per batch with IP count and status distribution. Severity: HIGH (≥3 events), MEDIUM (1–2).

#### Rule 2 — Brute Force (`brute_force`)

Groups HTTP 401/403 errors by `client_ip`. If an IP has ≥ 5 failures in one batch, it generates one alert per offending IP. Severity: always HIGH.

#### Rule 3 — Path Scanning (`path_scan`)

Filters HTTP 404 responses against a set of known suspicious paths (`/phpmyadmin`, `/.env`, `/cgi-bin`, `/wp-admin`, `/.git`, `/etc/passwd`, etc.). Groups by IP. Threshold: ≥ 3 probes. Critical paths (`/.env`, `/etc/passwd`, `/.git`) trigger alerts even below the threshold. Severity: MEDIUM (normal), HIGH (critical paths).

Note: the request path column is `heathers_request_path` (official API typo — not corrected in the code).

#### Rule 4 — High-Cost LLM Errors (`high_cost_llm_error`)

Triggers on `LLM_ERROR` events with `llm_cost_usd > $1.00`. One grouped alert per batch with total cost and max individual cost. Severity: HIGH (≥3 events), MEDIUM (1–2).

#### Rule 5 — LLM Timeouts (`llm_timeout`)

Triggers on any `LLM_TIMEOUT` event. One grouped alert per batch. Severity: HIGH (≥3 events), MEDIUM (1–2).

#### Rule 6 — Slow LLM Responses (`slow_llm_response`)

Triggers on `llm_response_time_ms > 10,000` (10 seconds). One grouped alert with max, average, and affected models. Severity: MEDIUM (≥10 events), LOW (fewer).

### Configurable Thresholds

All thresholds are defined as module-level constants:

| Constant | Default | Purpose |
|---|---|---|
| `UMBRAL_BRUTE_FORCE_INTENTOS` | 5 | Min auth failures per IP for brute force |
| `UMBRAL_PATH_SCAN_INTENTOS` | 3 | Min suspicious 404s per IP for path scan |
| `UMBRAL_LLM_COSTO_USD` | 1.0 | Min cost (USD) for high-cost LLM error |
| `UMBRAL_LLM_RESPONSE_MS` | 10,000 | Min response time (ms) for slow LLM alert |

---

## 8. alerting.py — Alert Dispatch

**Location:** `app/alerting.py`

**Purpose:** Build the WHAT/WHEN/WHY message, send it to `POST /alert`, and record the result in HANA.

### API Contract

The SAP API endpoint `POST {API_BASE_URL}/alert` expects:

```json
{"message": "WHAT: <type>. WHEN: <ISO timestamp>. WHY: <evidence>."}
```

Maximum 300 characters. Same Bearer token as ingestion. Successful response: HTTP 201.

### Public Function

#### `enviar_alerta(...) -> AlertResult`

Sends a single alert. Never raises exceptions — all errors are captured in the return value.

```python
from alerting import enviar_alerta, AlertResult

result = enviar_alerta(
    alert_type   = "brute_force",
    severity     = "high",
    details      = "6 auth failures from IP 203.0.113.99 in 2 minutes",
    log_id       = "abc123",
    event_time   = "2026-05-04T06:31:33+00:00",
    conn         = hana_connection,       # optional — records in HANA if provided
    window_start = "2026-05-04T06:30:00+00:00",  # optional
    source       = "quick_filter",        # "quick_filter" or "model_ml"
)

if result.ok:
    print(f"Sent: {result.alert_id}, HTTP {result.status_code}, {result.elapsed_ms:.0f}ms")
elif result.is_duplicate:
    print(f"Duplicate: log_id already has an alert in HANA")
else:
    print(f"Failed: {result.error}")
```

### AlertResult Dataclass

```python
@dataclass
class AlertResult:
    ok: bool                    # True if SAP responded HTTP 201
    alert_id: str               # UUID for this alert (always present)
    status_code: int | None     # HTTP status from /alert
    error: str | None           # Error description (None if ok)
    is_duplicate: bool          # True if log_id already alerted
    elapsed_ms: float           # Total latency in milliseconds
```

### Internal Workflow

1. Validate inputs (alert_type, severity, details, log_id).
2. Check for duplicates in HANA by `log_id` (if conn provided).
3. Build the WHAT/WHEN/WHY message, truncating WHY if needed to fit 300 chars.
4. INSERT into `DBADMIN.ALERTS` with `alerted=0` (pending).
5. POST to `/alert` with up to 3 retries and exponential backoff.
6. On HTTP 201: UPDATE `alerted=1` (confirmed).

### Retry Policy

| Response | Action |
|---|---|
| HTTP 201 (or any 2xx) | Success — no retry |
| HTTP 401 | Fatal — no retry (invalid token) |
| HTTP 422 | No retry (invalid message format) |
| HTTP 4xx (other) | No retry (client error) |
| HTTP 5xx | Retry up to 3 times (backoff 1s → 2s) |
| Timeout / ConnectionError | Retry up to 3 times (backoff 1s → 2s) |

---

## 9. feature_eng.py — Feature Engineering

**Location:** `app/feature_eng.py`

**Purpose:** Transform raw DataFrames from `hana_reader.py` into numeric feature matrices for the sklearn models. Pure Python/pandas — no HANA dependency, no sklearn dependency.

### Public Functions

#### `build_sistema_features(df) -> pd.DataFrame`

Transforms raw system logs into features for Isolation Forest.

```python
from feature_eng import build_sistema_features

df_feat = build_sistema_features(df_sistema)
# Columns: LOG_ID + 6 numeric + 3 categorical
```

**Output columns:**

| Column | Type | Derivation |
|---|---|---|
| `status_family` | int | `HTTP_STATUS // 100` → 2, 3, 4, 5 |
| `is_4xx` | int | 1 if status family is 4 |
| `is_5xx` | int | 1 if status family is 5 |
| `is_401_or_403` | int | 1 if HTTP_STATUS is 401 or 403 |
| `is_429` | int | 1 if HTTP_STATUS is 429 |
| `hour_utc` | int | Hour of the event (0–23) |
| `LOG_TYPE` | str | Categorical (7 values) |
| `APPLICATION` | str | Categorical (~10 values) |
| `REGION_NAME` | str | Categorical (~108 values) |

#### `build_llm_features(df) -> pd.DataFrame`

Transforms raw LLM logs into features for Isolation Forest.

```python
from feature_eng import build_llm_features

df_feat = build_llm_features(df_llm)
# Columns: LOG_ID + 4 numeric + 4 categorical
```

**Output columns:**

| Column | Type | Derivation |
|---|---|---|
| `log1p_cost` | float | `log1p(LLM_COST_USD)` — compresses heavy-tail |
| `log1p_response_time` | float | `log1p(LLM_RESPONSE_TIME)` |
| `log1p_total_tokens` | float | `log1p(LLM_TOTAL_TOKENS)` |
| `hour_utc` | int | Hour of the event (0–23) |
| `LLM_STATUS` | str | Categorical: success, error, timeout |
| `LLM_MODEL_ID` | str | Categorical: model identifier |
| `LLM_PROVIDER` | str | Categorical: provider name |
| `LOG_TYPE` | str | Categorical: LLM_REQUEST, LLM_ERROR, LLM_TIMEOUT |

#### `build_ip_behavior_table(df) -> pd.DataFrame`

Aggregates system logs by `CLIENT_IP` into one row per IP.

```python
from feature_eng import build_ip_behavior_table

df_ip = build_ip_behavior_table(df_sistema)
# ~105 rows (one per unique IP)
# Columns: CLIENT_IP + 9 numeric features
```

**Output columns:**

| Column | Type | Description |
|---|---|---|
| `event_count` | int | Total events from this IP |
| `distinct_paths` | int | Unique request paths visited |
| `distinct_apps` | int | Unique applications accessed |
| `ratio_4xx` | float | Client errors / total (0.0–1.0) |
| `ratio_5xx` | float | Server errors / total (0.0–1.0) |
| `ratio_security` | float | SECURITY events / total (0.0–1.0) |
| `has_401_or_403` | int | 1 if any 401 or 403 |
| `has_429` | int | 1 if any 429 |
| `n_distinct_status` | int | Count of distinct HTTP status codes |

### NaN Handling

All three functions guarantee no NaN values in the output feature columns. The strategies are:

- Numeric NaN in HTTP_STATUS → default to 200 (defensively normal).
- Numeric NaN in LLM metrics → impute with column median.
- Categorical NaN → replace with `"UNKNOWN"` string.

---

## 10. model.py — Machine Learning Detection

**Location:** `app/model.py`

**Purpose:** Run 3 unsupervised anomaly detection models and return alerts with the same contract as `quick_filter.py`.

### Public Function

#### `analizar_ventana(conn, window_start=None) -> list[dict]`

Entry point for the ML analysis cycle. Reads data from HANA, trains models, scores the current window, and returns up to 5 alerts sorted by severity.

```python
from model import analizar_ventana

alertas = analizar_ventana(
    conn=hana_connection,
    window_start="2026-05-04T06:30:00+00:00",
)
# alertas = [
#     {
#         "alert_type": "ml_ip_anomaly",
#         "severity": "high",
#         "details": "IP 10.0.0.42: 87 eventos, ratio_4xx=68%, ...",
#         "log_id": "abc123",
#         "event_time": "2026-05-04T06:31:00+00:00",
#     },
#     ...
# ]  # max 5 elements, never raises exceptions
```

### Models

#### IF_sistema — Isolation Forest for System Logs

Analyzes individual system log events. Uses 9 features (6 numeric + 3 categorical) from `build_sistema_features()`. Detects events with unusual combinations of HTTP status, log type, region, and hour.

#### IF_llm — Isolation Forest for LLM Logs

Analyzes individual LLM log events. Uses 8 features (4 numeric + 4 categorical) from `build_llm_features()`. Detects events with unusual cost, response time, token usage, or model/provider combinations.

#### LOF_ip — Local Outlier Factor for IP Behavior

Analyzes aggregated behavior per IP address. Uses 9 numeric features from `build_ip_behavior_table()`. Compares each IP against its nearest neighbors — an IP whose overall behavior profile differs significantly from similar IPs is flagged as anomalous.

### Operating Modes

| Mode | Condition | Training Data | Threshold |
|---|---|---|---|
| Cold-start | < 20 windows accumulated | Current window only (fit + predict on same data) | IQR: Q1 − 1.5 × IQR |
| Historical | ≥ 20 windows accumulated | Rolling 24h (excluding current window) | MAD: median − 3.5 × (MAD / 0.6745) |

### Hyperparameters

| Parameter | Value | Applies To |
|---|---|---|
| `n_estimators` | 200 | Isolation Forest |
| `max_samples` | 256 | Isolation Forest |
| `contamination` | `'auto'` | Isolation Forest |
| `random_state` | 42 | Isolation Forest |
| `n_neighbors` | 20 | LOF (adjusted dynamically if fewer IPs) |
| `MAX_ALERTAS_POR_CICLO` | 5 | Global alert cap per ML cycle |

### Severity Assignment

Anomalies are ranked by score (most anomalous first). Severity is assigned by position:

- Top 10% → `high`
- Top 10–30% → `medium`
- Remaining → `low`

### sklearn Pipeline Structure

Both Isolation Forest models use the same preprocessing pipeline:

```
ColumnTransformer:
    ├── Numeric columns  → RobustScaler
    └── Categorical columns → OrdinalEncoder (unknown → -1)
        ↓
IsolationForest(n_estimators=200, max_samples=256, contamination='auto')
```

LOF uses `RobustScaler` directly (all features are numeric after IP aggregation).

---

## 11. Module Dependency Map

```
pipeline_loop.py
    ├── config.py          (validate_config)
    ├── ingest.py          (ingest_and_persist)
    │     ├── config.py    (API_BASE_URL, get_headers, HANA_HOST)
    │     └── hana_client.py (upsert_logs, hana_esta_viva, create_tables)
    │           └── config.py (HANA_*)
    ├── quick_filter.py    (filtrar_amenazas)
    ├── alerting.py        (enviar_alerta)
    │     └── config.py    (API_BASE_URL, get_headers)
    └── model.py           (analizar_ventana)
          ├── hana_reader.py (leer_*_ventana_actual, leer_*_historico, en_modo_historico)
          │     └── config.py (HANA_*)
          └── feature_eng.py (build_sistema_features, build_llm_features, build_ip_behavior_table)
```

Every dependency arrow is a conditional import. If any leaf module fails, the modules above it degrade gracefully rather than crashing. The only hard dependency is `config.py` — without valid credentials, nothing can run.

---

## Testing Individual Modules

Every module includes a standalone test that runs without HANA or API access (using synthetic data):

```bash
python -m app.quick_filter     # Tests all 6 rules with synthetic threats
python -m app.feature_eng      # Tests all 3 feature builders with synthetic data
python -m app.model            # Tests IF and LOF with synthetic data
python -m app.hana_reader      # Tests HANA reads (requires HANA connection)
python app/hana_client.py      # Tests HANA connection and table setup
python app/alerting.py         # Tests alert construction and POST (sends real alert)
```
