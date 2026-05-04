"""
app/feature_eng.py
==================
Módulo de feature engineering para el modelo de detección ML.

Responsabilidad única: recibir DataFrames crudos desde hana_reader.py
y retornar DataFrames con features numéricas/categóricas listas para
los modelos sklearn (IsolationForest + LocalOutlierFactor).

Este módulo es PURO Python/pandas — no depende de HANA ni de sklearn.
Puede testearse completamente en local con datos sintéticos.

--- CONTRATOS DE ENTRADA Y SALIDA ---

build_sistema_features(df_sistema) → pd.DataFrame
    Entrada:  DataFrame con columnas COLS_SISTEMA de hana_reader.py
    Salida:   DataFrame con features numéricas y categóricas para IF_sistema
              Una fila por registro. Misma cantidad de filas que la entrada.
              Columna LOG_ID preservada para trazabilidad.

build_llm_features(df_llm) → pd.DataFrame
    Entrada:  DataFrame con columnas COLS_LLM de hana_reader.py
    Salida:   DataFrame con features para IF_llm
              Una fila por registro. Columna LOG_ID preservada.

build_ip_behavior_table(df_sistema) → pd.DataFrame
    Entrada:  DataFrame con columnas COLS_SISTEMA de hana_reader.py
    Salida:   DataFrame agregado — UNA FILA POR IP única
              ~105 filas en producción. Columna CLIENT_IP preservada.

--- FEATURES DE SISTEMA (para IF_sistema) ---

    Numéricas derivadas:
        status_family    int   http_status // 100  → 2, 3, 4, 5
        is_4xx           int   1 si familia == 4
        is_5xx           int   1 si familia == 5
        is_401_or_403    int   1 si HTTP_STATUS in ('401','403')
        is_429           int   1 si HTTP_STATUS == '429'
        hour_utc         int   hora UTC del evento (0-23)

    Categóricas (OrdinalEncoder en model.py):
        LOG_TYPE         str   7 valores: INFO/WARNING/ERROR/AUDIT/DEBUG/PERF/SECURITY
        APPLICATION      str   10 valores
        REGION_NAME      str   108 valores (max_categories=32 en el encoder)

--- FEATURES DE LLM (para IF_llm) ---

    Numéricas transformadas (log1p por distribución heavy-tail):
        log1p_cost           float   log1p(LLM_COST_USD)
        log1p_response_time  float   log1p(LLM_RESPONSE_TIME)
        log1p_total_tokens   float   log1p(LLM_TOTAL_TOKENS)
        hour_utc             int     hora UTC del evento (0-23)

    Categóricas (OrdinalEncoder en model.py):
        LLM_STATUS     str   3 valores: success / error / timeout
        LLM_MODEL_ID   str   cardinalidad variable
        LLM_PROVIDER   str   cardinalidad variable
        LOG_TYPE       str   3 valores: LLM_REQUEST / LLM_ERROR / LLM_TIMEOUT

--- FEATURES DE COMPORTAMIENTO POR IP (para LOF_ip) ---

    Una fila por IP única en la ventana:
        event_count        int     total eventos de esta IP
        distinct_paths     int     rutas únicas visitadas
        distinct_apps      int     aplicaciones únicas accedidas
        ratio_4xx          float   errores cliente / total
        ratio_5xx          float   errores servidor / total
        ratio_security     float   eventos SECURITY / total
        has_401_or_403     int     1 si algún evento fue 401 o 403
        has_429            int     1 si algún evento fue 429
        n_distinct_status  int     cantidad de HTTP_STATUS distintos

--- USO ---

    from app.feature_eng import (
        build_sistema_features,
        build_llm_features,
        build_ip_behavior_table,
    )

    df_sis_feat  = build_sistema_features(df_sistema)
    df_llm_feat  = build_llm_features(df_llm)
    df_ip_table  = build_ip_behavior_table(df_sistema)

--- PRUEBA STANDALONE ---

    python -m app.feature_eng
"""

import logging

import numpy as np
import pandas as pd

logger = logging.getLogger("feature_eng")

# ---------------------------------------------------------------------------
# Columnas de salida — contratos explícitos
# ---------------------------------------------------------------------------

# Columnas numéricas de sistema (pasadas a RobustScaler antes de IF)
NUMERIC_COLS_SISTEMA = [
    "status_family", "is_4xx", "is_5xx", "is_401_or_403", "is_429", "hour_utc",
]

# Columnas categóricas de sistema (pasadas a OrdinalEncoder antes de IF)
CATEGORICAL_COLS_SISTEMA = [
    "LOG_TYPE", "APPLICATION", "REGION_NAME",
]

# Todas las feature columns de sistema (sin LOG_ID)
FEATURE_COLS_SISTEMA = NUMERIC_COLS_SISTEMA + CATEGORICAL_COLS_SISTEMA

# Columnas numéricas de LLM
NUMERIC_COLS_LLM = [
    "log1p_cost", "log1p_response_time", "log1p_total_tokens", "hour_utc",
]

# Columnas categóricas de LLM
CATEGORICAL_COLS_LLM = [
    "LLM_STATUS", "LLM_MODEL_ID", "LLM_PROVIDER", "LOG_TYPE",
]

# Todas las feature columns de LLM (sin LOG_ID)
FEATURE_COLS_LLM = NUMERIC_COLS_LLM + CATEGORICAL_COLS_LLM

# Columnas de la tabla IP (todas numéricas — para LOF con RobustScaler)
FEATURE_COLS_IP = [
    "event_count", "distinct_paths", "distinct_apps",
    "ratio_4xx", "ratio_5xx", "ratio_security",
    "has_401_or_403", "has_429", "n_distinct_status",
]

# Valor de relleno para categóricas con NaN
UNKNOWN_FILL = "UNKNOWN"

# Valor de relleno para HTTP_STATUS no parseable
HTTP_STATUS_DEFAULT = 200


# ---------------------------------------------------------------------------
# Feature engineering — Sistema
# ---------------------------------------------------------------------------

def build_sistema_features(df: pd.DataFrame) -> pd.DataFrame:
    """
    Construye features para IF_sistema a partir del DataFrame crudo de sistema.

    Retorna DataFrame con columnas:
        LOG_ID (preservado para trazabilidad)
        + FEATURE_COLS_SISTEMA (6 numéricas + 3 categóricas)

    Nunca lanza excepciones — retorna DataFrame vacío si hay error.
    Las filas con HTTP_STATUS no parseable reciben status_family=2
    (comportamiento defensivo: no marcar como anómalo por dato faltante).
    """
    if df is None or df.empty:
        logger.warning("[FEAT] build_sistema_features: DataFrame vacío — retornando vacío")
        return pd.DataFrame(columns=["LOG_ID"] + FEATURE_COLS_SISTEMA)

    try:
        out = pd.DataFrame()
        out["LOG_ID"] = df["LOG_ID"].copy()

        # ── HTTP_STATUS → features numéricas ──────────────────────────────────
        # HTTP_STATUS viene como string desde HANA (NVARCHAR)
        # Convertir a int con fallback defensivo
        http_int = _parse_http_status(df["HTTP_STATUS"])

        out["status_family"]  = (http_int // 100).clip(1, 5).astype(int)
        out["is_4xx"]         = (out["status_family"] == 4).astype(int)
        out["is_5xx"]         = (out["status_family"] == 5).astype(int)
        out["is_401_or_403"]  = http_int.isin([401, 403]).astype(int)
        out["is_429"]         = (http_int == 429).astype(int)

        # ── Hora UTC ───────────────────────────────────────────────────────────
        out["hour_utc"] = _extract_hour_utc(df["EVENT_TIMESTAMP"])

        # ── Categóricas — rellenar NaN con UNKNOWN ────────────────────────────
        for col in CATEGORICAL_COLS_SISTEMA:
            if col in df.columns:
                out[col] = df[col].fillna(UNKNOWN_FILL).astype(str)
            else:
                logger.warning(f"[FEAT] Columna '{col}' no encontrada — usando '{UNKNOWN_FILL}'")
                out[col] = UNKNOWN_FILL

        logger.info(
            f"[FEAT] sistema: {len(out):,} filas | "
            f"4xx={out['is_4xx'].sum()} | 5xx={out['is_5xx'].sum()} | "
            f"SECURITY={(out['LOG_TYPE'] == 'SECURITY').sum()}"
        )
        return out

    except Exception as e:
        logger.error(f"[FEAT] build_sistema_features falló: {e}", exc_info=True)
        return pd.DataFrame(columns=["LOG_ID"] + FEATURE_COLS_SISTEMA)


# ---------------------------------------------------------------------------
# Feature engineering — LLM
# ---------------------------------------------------------------------------

def build_llm_features(df: pd.DataFrame) -> pd.DataFrame:
    """
    Construye features para IF_llm a partir del DataFrame crudo de LLM.

    Retorna DataFrame con columnas:
        LOG_ID (preservado para trazabilidad)
        + FEATURE_COLS_LLM (4 numéricas + 4 categóricas)

    Transformaciones numéricas:
        log1p_cost           = log1p(LLM_COST_USD)          — heavy-tail
        log1p_response_time  = log1p(LLM_RESPONSE_TIME)     — heavy-tail
        log1p_total_tokens   = log1p(LLM_TOTAL_TOKENS)      — heavy-tail

    Los NaN en numéricas se imputan con la mediana de la columna
    (no con cero — cero sería un outlier en estas distribuciones).

    Nunca lanza excepciones — retorna DataFrame vacío si hay error.
    """
    if df is None or df.empty:
        logger.warning("[FEAT] build_llm_features: DataFrame vacío — retornando vacío")
        return pd.DataFrame(columns=["LOG_ID"] + FEATURE_COLS_LLM)

    try:
        out = pd.DataFrame()
        out["LOG_ID"] = df["LOG_ID"].copy()

        # ── Numéricas con log1p ────────────────────────────────────────────────
        out["log1p_cost"]          = _log1p_safe(df, "LLM_COST_USD")
        out["log1p_response_time"] = _log1p_safe(df, "LLM_RESPONSE_TIME")
        out["log1p_total_tokens"]  = _log1p_safe(df, "LLM_TOTAL_TOKENS")

        # ── Hora UTC ───────────────────────────────────────────────────────────
        out["hour_utc"] = _extract_hour_utc(df["EVENT_TIMESTAMP"])

        # ── Categóricas ────────────────────────────────────────────────────────
        for col in CATEGORICAL_COLS_LLM:
            if col in df.columns:
                out[col] = df[col].fillna(UNKNOWN_FILL).astype(str)
            else:
                logger.warning(f"[FEAT] Columna '{col}' no encontrada — usando '{UNKNOWN_FILL}'")
                out[col] = UNKNOWN_FILL

        logger.info(
            f"[FEAT] llm: {len(out):,} filas | "
            f"timeouts={(out['LLM_STATUS'] == 'timeout').sum()} | "
            f"errors={(out['LLM_STATUS'] == 'error').sum()} | "
            f"log1p_cost rango: [{out['log1p_cost'].min():.3f}, {out['log1p_cost'].max():.3f}]"
        )
        return out

    except Exception as e:
        logger.error(f"[FEAT] build_llm_features falló: {e}", exc_info=True)
        return pd.DataFrame(columns=["LOG_ID"] + FEATURE_COLS_LLM)


# ---------------------------------------------------------------------------
# Feature engineering — Tabla IP (comportamiento agregado)
# ---------------------------------------------------------------------------

def build_ip_behavior_table(df: pd.DataFrame) -> pd.DataFrame:
    """
    Construye la tabla de comportamiento por IP para LOF_ip.

    Agrega los logs de sistema por CLIENT_IP y calcula métricas de
    comportamiento. Retorna UNA FILA POR IP única (~105 IPs en producción).

    Retorna DataFrame con columnas:
        CLIENT_IP (preservado como índice lógico)
        + FEATURE_COLS_IP (9 features numéricas)

    Las features están en escala natural — model.py aplica RobustScaler
    antes de pasarlas a LOF.

    Nunca lanza excepciones — retorna DataFrame vacío si hay error.
    """
    if df is None or df.empty:
        logger.warning("[FEAT] build_ip_behavior_table: DataFrame vacío — retornando vacío")
        return pd.DataFrame(columns=["CLIENT_IP"] + FEATURE_COLS_IP)

    # Necesitamos CLIENT_IP y HTTP_STATUS para agregar
    required = {"CLIENT_IP", "HTTP_STATUS", "LOG_TYPE"}
    missing = required - set(df.columns)
    if missing:
        logger.error(f"[FEAT] build_ip_behavior_table: columnas faltantes {missing}")
        return pd.DataFrame(columns=["CLIENT_IP"] + FEATURE_COLS_IP)

    try:
        # Parsear HTTP_STATUS una sola vez para todo el DataFrame
        df_work = df.copy()
        df_work["_http_int"]    = _parse_http_status(df_work["HTTP_STATUS"])
        df_work["_family"]      = (df_work["_http_int"] // 100).clip(1, 5)
        df_work["_is_4xx"]      = (df_work["_family"] == 4).astype(int)
        df_work["_is_5xx"]      = (df_work["_family"] == 5).astype(int)
        df_work["_is_401_403"]  = df_work["_http_int"].isin([401, 403]).astype(int)
        df_work["_is_429"]      = (df_work["_http_int"] == 429).astype(int)
        df_work["_is_security"] = (df_work["LOG_TYPE"] == "SECURITY").astype(int)

        # Agregar por IP
        grp = df_work.groupby("CLIENT_IP", sort=False)

        agg = pd.DataFrame()
        agg["event_count"]       = grp["LOG_ID"].count()
        agg["distinct_paths"]    = grp["REQUEST_PATH"].nunique() if "REQUEST_PATH" in df_work.columns else 0
        agg["distinct_apps"]     = grp["APPLICATION"].nunique() if "APPLICATION" in df_work.columns else 0
        agg["ratio_4xx"]         = grp["_is_4xx"].mean()
        agg["ratio_5xx"]         = grp["_is_5xx"].mean()
        agg["ratio_security"]    = grp["_is_security"].mean()
        agg["has_401_or_403"]    = grp["_is_401_403"].max()
        agg["has_429"]           = grp["_is_429"].max()
        agg["n_distinct_status"] = grp["_http_int"].nunique()

        agg = agg.reset_index()  # CLIENT_IP pasa de índice a columna

        # Asegurar tipos correctos
        int_cols = ["event_count", "distinct_paths", "distinct_apps",
                    "has_401_or_403", "has_429", "n_distinct_status"]
        for col in int_cols:
            agg[col] = agg[col].fillna(0).astype(int)

        float_cols = ["ratio_4xx", "ratio_5xx", "ratio_security"]
        for col in float_cols:
            agg[col] = agg[col].fillna(0.0).astype(float)

        logger.info(
            f"[FEAT] ip_behavior: {len(agg):,} IPs únicas | "
            f"event_count rango: [{agg['event_count'].min()}, {agg['event_count'].max()}] | "
            f"IPs con 401/403: {agg['has_401_or_403'].sum()} | "
            f"IPs con SECURITY: {(agg['ratio_security'] > 0).sum()}"
        )
        return agg

    except Exception as e:
        logger.error(f"[FEAT] build_ip_behavior_table falló: {e}", exc_info=True)
        return pd.DataFrame(columns=["CLIENT_IP"] + FEATURE_COLS_IP)


# ---------------------------------------------------------------------------
# Utilidades internas
# ---------------------------------------------------------------------------

def _parse_http_status(series: pd.Series) -> pd.Series:
    """
    Convierte una Serie de HTTP_STATUS (NVARCHAR desde HANA) a enteros.

    HTTP_STATUS viene como string: '200', '404', '503', etc.
    Los valores no parseables reciben HTTP_STATUS_DEFAULT (200).

    Retorna Serie de int64.
    """
    def safe_int(val):
        try:
            return int(str(val).strip())
        except (ValueError, TypeError):
            return HTTP_STATUS_DEFAULT

    return series.apply(safe_int).astype(int)


def _extract_hour_utc(series: pd.Series) -> pd.Series:
    """
    Extrae la hora UTC (0-23) de una Serie de timestamps.

    Maneja tanto datetime64[ns, UTC] como strings ISO.
    Retorna Serie de int (0-23). Valores no parseables → 0.
    """
    try:
        if pd.api.types.is_datetime64_any_dtype(series):
            return series.dt.hour.fillna(0).astype(int)
        else:
            parsed = pd.to_datetime(series, utc=True, errors="coerce")
            return parsed.dt.hour.fillna(0).astype(int)
    except Exception:
        return pd.Series(0, index=series.index, dtype=int)


def _log1p_safe(df: pd.DataFrame, col: str) -> pd.Series:
    """
    Aplica log1p a una columna numérica con imputación de NaN por mediana.

    Si la columna no existe, retorna serie de ceros.
    Los valores negativos (no deberían existir en costos/tiempos) se clipean a 0.
    """
    if col not in df.columns:
        logger.warning(f"[FEAT] Columna '{col}' no encontrada — log1p devuelve ceros")
        return pd.Series(0.0, index=df.index)

    s = pd.to_numeric(df[col], errors="coerce")

    # Imputar NaN con mediana (no con cero — cero sería outlier en estas distribuciones)
    if s.isna().any():
        mediana = s.median()
        n_nan = s.isna().sum()
        logger.debug(f"[FEAT] '{col}': {n_nan} NaN imputados con mediana={mediana:.4f}")
        s = s.fillna(mediana)

    # Clipear negativos a 0 (no deberían existir, pero por seguridad)
    s = s.clip(lower=0)

    return np.log1p(s).astype(float)


# ---------------------------------------------------------------------------
# Script de prueba standalone — sin HANA, con datos sintéticos
# ---------------------------------------------------------------------------

def _probar_feature_eng():
    """
    Prueba completa del módulo con datos sintéticos que replican
    las distribuciones reales observadas en HANA.

    Verifica:
        1. Columnas de salida correctas en los 3 DataFrames
        2. Tipos de datos correctos
        3. No hay NaN en la salida
        4. Rangos de valores válidos
        5. Comportamiento con entrada vacía
        6. Comportamiento con HTTP_STATUS no parseables

    Uso:
        python -m app.feature_eng
    """
    import logging
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
        datefmt="%H:%M:%S",
    )

    print("\n" + "=" * 60)
    print("PRUEBA — app/feature_eng.py (datos sintéticos)")
    print("=" * 60)

    # ── Datos sintéticos de sistema ───────────────────────────────────────────
    n = 200
    rng = np.random.default_rng(42)

    http_statuses = ["200", "201", "400", "401", "403", "404",
                     "429", "500", "503", "408", "301", "204"]
    log_types_sis = ["INFO", "WARNING", "ERROR", "AUDIT", "DEBUG", "PERF", "SECURITY"]
    applications  = [f"app_{i}" for i in range(10)]
    regions       = [f"region_{i}" for i in range(30)]
    ips           = [f"192.168.1.{i}" for i in range(20)]

    timestamps = pd.date_range("2026-05-04 00:00:00", periods=n, freq="10s", tz="UTC")

    df_sistema_raw = pd.DataFrame({
        "LOG_ID":          [f"sys-{i}" for i in range(n)],
        "EVENT_TIMESTAMP": timestamps,
        "LOG_TYPE":        rng.choice(log_types_sis, n),
        "HTTP_STATUS":     rng.choice(http_statuses, n),
        "CLIENT_IP":       rng.choice(ips, n),
        "REQUEST_PATH":    rng.choice(["/api/v1", "/btp/event", "/health", "/admin", "/.env"], n),
        "APPLICATION":     rng.choice(applications, n),
        "REGION_NAME":     rng.choice(regions, n),
        "MESSAGE":         ["Test message"] * n,
    })

    # Introducir algunos NaN para probar robustez
    df_sistema_raw.loc[5, "HTTP_STATUS"]  = None
    df_sistema_raw.loc[10, "LOG_TYPE"]    = None
    df_sistema_raw.loc[15, "APPLICATION"] = None
    df_sistema_raw.loc[20, "HTTP_STATUS"] = "invalid_value"

    # ── Datos sintéticos de LLM ───────────────────────────────────────────────
    m = 150
    llm_statuses = ["success", "error", "timeout"]
    llm_models   = ["claude-3-sonnet", "gpt-4-turbo", "gemini-1.5-pro",
                    "mistral-7b-instruct", "claude-3-5-sonnet", "grok-4.20-b2"]
    providers    = ["anthropic", "openai", "google", "mistral", "xai"]

    timestamps_llm = pd.date_range("2026-05-04 00:00:00", periods=m, freq="12s", tz="UTC")

    df_llm_raw = pd.DataFrame({
        "LOG_ID":              [f"llm-{i}" for i in range(m)],
        "EVENT_TIMESTAMP":     timestamps_llm,
        "LOG_TYPE":            rng.choice(["LLM_REQUEST", "LLM_ERROR", "LLM_TIMEOUT"], m),
        "LLM_STATUS":          rng.choice(llm_statuses, m),
        "LLM_MODEL_ID":        rng.choice(llm_models, m),
        "LLM_PROVIDER":        rng.choice(providers, m),
        "LLM_COST_USD":        rng.uniform(0.000007, 0.14, m),
        "LLM_RESPONSE_TIME":   rng.uniform(200, 35000, m),
        "LLM_TOTAL_TOKENS":    rng.integers(84, 3500, m),
        "LLM_TEMPERATURE":     rng.uniform(0.0, 1.0, m),
        "LLM_ERROR_MESSAGE":   [None] * m,
        "LLM_PROMPT_CATEGORY": rng.choice(["chat", "code", "analysis", None], m),
    })

    # Introducir algunos NaN en numéricas
    df_llm_raw.loc[3, "LLM_COST_USD"]        = None
    df_llm_raw.loc[7, "LLM_RESPONSE_TIME"]   = None
    df_llm_raw.loc[12, "LLM_STATUS"]         = None

    # ── Test 1: build_sistema_features ────────────────────────────────────────
    print("\n[TEST 1] build_sistema_features...")
    df_sis_feat = build_sistema_features(df_sistema_raw)

    _verificar_df(
        df=df_sis_feat,
        nombre="sistema_features",
        cols_requeridas=["LOG_ID"] + FEATURE_COLS_SISTEMA,
        n_esperado=n,
    )

    # Verificar rangos
    assert df_sis_feat["status_family"].between(1, 5).all(), "status_family fuera de [1,5]"
    assert df_sis_feat["is_4xx"].isin([0, 1]).all(), "is_4xx no es binario"
    assert df_sis_feat["is_5xx"].isin([0, 1]).all(), "is_5xx no es binario"
    assert df_sis_feat["is_401_or_403"].isin([0, 1]).all(), "is_401_or_403 no es binario"
    assert df_sis_feat["is_429"].isin([0, 1]).all(), "is_429 no es binario"
    assert df_sis_feat["hour_utc"].between(0, 23).all(), "hour_utc fuera de [0,23]"
    assert (df_sis_feat["LOG_TYPE"] != "").all(), "LOG_TYPE tiene vacíos"
    print("   ✅ Rangos y tipos correctos")

    # Verificar que el NaN de HTTP_STATUS fue manejado
    row_nan = df_sis_feat.loc[5]
    assert row_nan["status_family"] == (HTTP_STATUS_DEFAULT // 100), \
        f"HTTP_STATUS None no recibió default correcto: {row_nan['status_family']}"
    row_invalid = df_sis_feat.loc[20]
    assert row_invalid["status_family"] == (HTTP_STATUS_DEFAULT // 100), \
        f"HTTP_STATUS inválido no recibió default: {row_invalid['status_family']}"
    print("   ✅ HTTP_STATUS None e inválido manejados correctamente")

    # Verificar que LOG_TYPE None fue rellenado con UNKNOWN
    assert df_sis_feat.loc[10, "LOG_TYPE"] == UNKNOWN_FILL, \
        f"LOG_TYPE None no fue rellenado con UNKNOWN: {df_sis_feat.loc[10, 'LOG_TYPE']}"
    print("   ✅ NaN en categóricas rellenados con UNKNOWN")

    # ── Test 2: build_llm_features ────────────────────────────────────────────
    print("\n[TEST 2] build_llm_features...")
    df_llm_feat = build_llm_features(df_llm_raw)

    _verificar_df(
        df=df_llm_feat,
        nombre="llm_features",
        cols_requeridas=["LOG_ID"] + FEATURE_COLS_LLM,
        n_esperado=m,
    )

    # Verificar que log1p produce valores no negativos
    assert (df_llm_feat["log1p_cost"] >= 0).all(), "log1p_cost tiene negativos"
    assert (df_llm_feat["log1p_response_time"] >= 0).all(), "log1p_response_time tiene negativos"
    assert (df_llm_feat["log1p_total_tokens"] >= 0).all(), "log1p_total_tokens tiene negativos"

    # Verificar que log1p comprimió la distribución (max debe ser mucho menor que el raw)
    max_rt_raw  = df_llm_raw["LLM_RESPONSE_TIME"].max()
    max_rt_log1p = df_llm_feat["log1p_response_time"].max()
    assert max_rt_log1p < max_rt_raw, "log1p no comprimió LLM_RESPONSE_TIME"
    print(f"   ✅ log1p comprimió LLM_RESPONSE_TIME: {max_rt_raw:.0f}ms → {max_rt_log1p:.3f}")

    # Verificar que NaN en numéricas fue imputado (no hay NaN en salida)
    assert not df_llm_feat[NUMERIC_COLS_LLM].isna().any().any(), \
        "Hay NaN en features numéricas de LLM después de imputación"
    print("   ✅ NaN en numéricas imputados con mediana")

    # Verificar que LLM_STATUS None fue rellenado
    assert df_llm_feat.loc[12, "LLM_STATUS"] == UNKNOWN_FILL, \
        f"LLM_STATUS None no rellenado: {df_llm_feat.loc[12, 'LLM_STATUS']}"
    print("   ✅ NaN en LLM_STATUS rellenado con UNKNOWN")

    # ── Test 3: build_ip_behavior_table ───────────────────────────────────────
    print("\n[TEST 3] build_ip_behavior_table...")
    df_ip = build_ip_behavior_table(df_sistema_raw)

    n_ips_esperadas = df_sistema_raw["CLIENT_IP"].nunique()
    _verificar_df(
        df=df_ip,
        nombre="ip_behavior_table",
        cols_requeridas=["CLIENT_IP"] + FEATURE_COLS_IP,
        n_esperado=n_ips_esperadas,
    )

    # Una fila por IP
    assert df_ip["CLIENT_IP"].nunique() == len(df_ip), "IP duplicadas en la tabla"
    print(f"   ✅ Una fila por IP: {len(df_ip)} IPs únicas")

    # Ratios entre 0 y 1
    for col in ["ratio_4xx", "ratio_5xx", "ratio_security"]:
        assert df_ip[col].between(0, 1).all(), f"{col} fuera de [0,1]"
    print("   ✅ Ratios en [0,1]")

    # event_count > 0 para todas las IPs
    assert (df_ip["event_count"] > 0).all(), "Alguna IP tiene event_count=0"
    print("   ✅ event_count > 0 para todas las IPs")

    # ── Test 4: entradas vacías ───────────────────────────────────────────────
    print("\n[TEST 4] Comportamiento con entradas vacías...")
    df_empty = pd.DataFrame()

    df_sis_vacio = build_sistema_features(df_empty)
    assert df_sis_vacio.empty and list(df_sis_vacio.columns) == ["LOG_ID"] + FEATURE_COLS_SISTEMA
    print("   ✅ build_sistema_features(vacío) → DataFrame vacío con columnas correctas")

    df_llm_vacio = build_llm_features(df_empty)
    assert df_llm_vacio.empty and list(df_llm_vacio.columns) == ["LOG_ID"] + FEATURE_COLS_LLM
    print("   ✅ build_llm_features(vacío) → DataFrame vacío con columnas correctas")

    df_ip_vacio = build_ip_behavior_table(df_empty)
    assert df_ip_vacio.empty and list(df_ip_vacio.columns) == ["CLIENT_IP"] + FEATURE_COLS_IP
    print("   ✅ build_ip_behavior_table(vacío) → DataFrame vacío con columnas correctas")

    # ── Resumen ───────────────────────────────────────────────────────────────
    print("\n--- Muestra de features de sistema (primeras 3 filas) ---")
    print(df_sis_feat[["LOG_ID"] + FEATURE_COLS_SISTEMA].head(3).to_string())

    print("\n--- Muestra de features de LLM (primeras 3 filas) ---")
    print(df_llm_feat[["LOG_ID"] + FEATURE_COLS_LLM].head(3).to_string())

    print("\n--- Muestra de tabla IP (primeras 3 filas) ---")
    print(df_ip[["CLIENT_IP"] + FEATURE_COLS_IP].head(3).to_string())

    print("\n" + "=" * 60)
    print("✅ Todos los tests pasaron — feature_eng.py listo para model.py")
    print("=" * 60 + "\n")


def _verificar_df(
    df: pd.DataFrame,
    nombre: str,
    cols_requeridas: list,
    n_esperado: int,
):
    """Verifica columnas, tipos y ausencia de NaN en un DataFrame de features."""
    # Filas
    assert len(df) == n_esperado, \
        f"{nombre}: esperaba {n_esperado} filas, got {len(df)}"
    print(f"   ✅ Filas: {len(df)}")

    # Columnas
    cols_faltantes = [c for c in cols_requeridas if c not in df.columns]
    assert not cols_faltantes, f"{nombre}: columnas faltantes {cols_faltantes}"
    print(f"   ✅ Columnas: {len(cols_requeridas)}/{len(cols_requeridas)} presentes")

    # Sin NaN en features (la columna ID puede tener NaN en teoría, no la verificamos)
    feature_cols = [c for c in cols_requeridas if c not in ("LOG_ID", "CLIENT_IP")]
    nan_counts = df[feature_cols].isna().sum()
    cols_con_nan = nan_counts[nan_counts > 0]
    assert cols_con_nan.empty, \
        f"{nombre}: NaN encontrados en features:\n{cols_con_nan}"
    print(f"   ✅ Sin NaN en features")


if __name__ == "__main__":
    _probar_feature_eng()
