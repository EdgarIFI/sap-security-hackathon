"""
app/model.py
============
Modelo de detección de anomalías ML para el SAP AI Security SOC.

Responsabilidad única: recibir una conexión HANA activa, extraer los datos
de la ventana actual e históricos, entrenar los modelos sklearn, scorear,
y retornar una lista de alertas con el mismo contrato que quick_filter.

--- ARQUITECTURA DE MODELOS ---

    Nivel 1 — Evento individual:
        IF_sistema   IsolationForest sobre logs de Sistema (~3,500 eventos/ventana)
        IF_llm       IsolationForest sobre logs de LLM     (~2,400 eventos/ventana)

    Nivel 2 — Comportamiento agregado:
        LOF_ip       LocalOutlierFactor sobre tabla por IP (~105 filas/ventana)

--- MODO DE OPERACIÓN ---

    Cold-start (< 20 ventanas acumuladas):
        Entrena IF sobre la ventana actual misma (fit + predict en mismo set)
        Threshold: IQR → Q3 + 1.5×IQR sobre los scores del batch

    Modo histórico (≥ 20 ventanas):
        Entrena IF sobre historia rolling de 24h (excluyendo ventana actual)
        Scorea la ventana actual con el modelo entrenado
        Threshold: MAD → modified z-score > 3.5

--- CONTRATO PÚBLICO ---

    from app.model import analizar_ventana

    alertas = analizar_ventana(conn, window_start="2026-05-04T00:30:00+00:00")

    # alertas es list[dict], cada dict:
    # {
    #     "alert_type": str,    "ml_sistema_anomaly" | "ml_llm_anomaly" | "ml_ip_anomaly"
    #     "severity":   str,    "low" | "medium" | "high"
    #     "details":    str,    descripción ≤ 250 chars
    #     "log_id":     str,    LOG_ID del registro más anómalo
    #     "event_time": str,    EVENT_TIMESTAMP ISO del registro
    # }

    # Cap duro: máximo 5 elementos. Nunca lanza excepciones.

--- PRUEBA STANDALONE ---

    python -m app.model
"""

import logging
import time
from datetime import datetime, timezone
from typing import Optional

import numpy as np
import pandas as pd

logger = logging.getLogger("model")

# ---------------------------------------------------------------------------
# Constantes de configuración
# ---------------------------------------------------------------------------

# IsolationForest — parámetros
IF_N_ESTIMATORS  = 200
IF_MAX_SAMPLES   = 256
IF_RANDOM_STATE  = 42

# LocalOutlierFactor — parámetros
LOF_N_NEIGHBORS  = 20    # seguro con ~105 IPs

# Thresholding
IQR_MULTIPLIER   = 1.5   # cold-start: Q3 + 1.5×IQR
MAD_ZSCORE_THRESHOLD = 3.5  # histórico: modified z-score

# Cap de alertas por ciclo del modelo
MAX_ALERTAS_POR_CICLO = 5

# Mínimo de registros para entrenar — por debajo de esto no corremos el modelo
MIN_REGISTROS_SISTEMA = 50
MIN_REGISTROS_LLM     = 30
MIN_IPS_LOF           = 10   # LOF necesita al menos n_neighbors + margen

# Severidad según posición en el ranking de anomalías
# Top 10% → high, Top 30% → medium, resto → low
UMBRAL_HIGH   = 0.10
UMBRAL_MEDIUM = 0.30


# ---------------------------------------------------------------------------
# Función pública principal — contrato del pipeline
# ---------------------------------------------------------------------------

def analizar_ventana(
    conn,
    window_start: Optional[str] = None,
) -> list:
    """
    Detecta anomalías estadísticas en la ventana actual de logs.

    Parámetros
    ----------
    conn : hdbcli.dbapi.Connection
        Conexión HANA activa. Viene de pipeline_loop._abrir_conexion_hana().
        model.py NO abre ni cierra conexiones — usa la que recibe.
    window_start : str | None
        ISO timestamp del inicio de la ventana activa (para logging).

    Retorna
    -------
    list[dict] — máximo MAX_ALERTAS_POR_CICLO elementos, cada uno con:
        alert_type, severity, details, log_id, event_time

    Nunca lanza excepciones — cualquier error retorna lista vacía.
    """
    t_inicio = time.monotonic()
    alertas_totales = []

    if conn is None:
        logger.warning("[MODEL] conn=None — omitiendo análisis ML")
        return []

    label_ventana = window_start or "ventana_actual"
    logger.info(f"[MODEL] Iniciando análisis ML | ventana={label_ventana}")

    try:
        # Importar módulos del proyecto con doble-path
        try:
            from app.hana_reader import (
                leer_sistema_ventana_actual, leer_llm_ventana_actual,
                leer_sistema_historico, leer_llm_historico,
                en_modo_historico,
            )
            from app.feature_eng import (
                build_sistema_features, build_llm_features,
                build_ip_behavior_table,
                FEATURE_COLS_SISTEMA, NUMERIC_COLS_SISTEMA, CATEGORICAL_COLS_SISTEMA,
                FEATURE_COLS_LLM, NUMERIC_COLS_LLM, CATEGORICAL_COLS_LLM,
                FEATURE_COLS_IP,
            )
        except ImportError:
            from hana_reader import (
                leer_sistema_ventana_actual, leer_llm_ventana_actual,
                leer_sistema_historico, leer_llm_historico,
                en_modo_historico,
            )
            from feature_eng import (
                build_sistema_features, build_llm_features,
                build_ip_behavior_table,
                FEATURE_COLS_SISTEMA, NUMERIC_COLS_SISTEMA, CATEGORICAL_COLS_SISTEMA,
                FEATURE_COLS_LLM, NUMERIC_COLS_LLM, CATEGORICAL_COLS_LLM,
                FEATURE_COLS_IP,
            )

        # ── Paso 1: Determinar modo operativo ─────────────────────────────────
        modo_historico = en_modo_historico(conn)

        # ── Paso 2: Leer ventana actual (scoring) ─────────────────────────────
        df_sis_actual = leer_sistema_ventana_actual(conn)
        df_llm_actual = leer_llm_ventana_actual(conn)

        logger.info(
            f"[MODEL] Ventana actual: {len(df_sis_actual):,} sistema, "
            f"{len(df_llm_actual):,} LLM"
        )

        # ── Paso 3: IF_sistema ────────────────────────────────────────────────
        alertas_sis = _correr_isolation_forest_sistema(
            df_actual=df_sis_actual,
            conn=conn if modo_historico else None,
            modo_historico=modo_historico,
            build_features_fn=build_sistema_features,
            leer_historico_fn=leer_sistema_historico,
            feature_cols=FEATURE_COLS_SISTEMA,
            numeric_cols=NUMERIC_COLS_SISTEMA,
            categorical_cols=CATEGORICAL_COLS_SISTEMA,
            min_registros=MIN_REGISTROS_SISTEMA,
            alert_type="ml_sistema_anomaly",
        )
        alertas_totales.extend(alertas_sis)

        # ── Paso 4: IF_llm ────────────────────────────────────────────────────
        alertas_llm = _correr_isolation_forest_llm(
            df_actual=df_llm_actual,
            conn=conn if modo_historico else None,
            modo_historico=modo_historico,
            build_features_fn=build_llm_features,
            leer_historico_fn=leer_llm_historico,
            feature_cols=FEATURE_COLS_LLM,
            numeric_cols=NUMERIC_COLS_LLM,
            categorical_cols=CATEGORICAL_COLS_LLM,
            min_registros=MIN_REGISTROS_LLM,
            alert_type="ml_llm_anomaly",
        )
        alertas_totales.extend(alertas_llm)

        # ── Paso 5: LOF_ip ────────────────────────────────────────────────────
        alertas_ip = _correr_lof_ip(
            df_sistema=df_sis_actual,
            build_ip_fn=build_ip_behavior_table,
            feature_cols=FEATURE_COLS_IP,
        )
        alertas_totales.extend(alertas_ip)

        # ── Paso 6: Cap y ordenar por prioridad ───────────────────────────────
        # Ordenar: high → medium → low, luego tomar los primeros N
        orden_sev = {"high": 0, "medium": 1, "low": 2}
        alertas_totales.sort(key=lambda a: orden_sev.get(a["severity"], 3))
        alertas_finales = alertas_totales[:MAX_ALERTAS_POR_CICLO]

        elapsed = (time.monotonic() - t_inicio) * 1000
        logger.info(
            f"[MODEL] Análisis completado en {elapsed:.0f}ms | "
            f"anomalías: {len(alertas_totales)} detectadas | "
            f"{len(alertas_finales)} enviadas (cap={MAX_ALERTAS_POR_CICLO})"
        )
        return alertas_finales

    except Exception as e:
        logger.error(f"[MODEL] Error inesperado en analizar_ventana: {e}", exc_info=True)
        return []


# ---------------------------------------------------------------------------
# IF_sistema — Isolation Forest sobre logs de Sistema
# ---------------------------------------------------------------------------

def _correr_isolation_forest_sistema(
    df_actual, conn, modo_historico, build_features_fn,
    leer_historico_fn, feature_cols, numeric_cols,
    categorical_cols, min_registros, alert_type,
) -> list:
    """
    Entrena IF sobre sistema y scorea la ventana actual.
    Retorna lista de alertas (puede ser vacía).
    """
    if df_actual is None or len(df_actual) < min_registros:
        logger.info(f"[MODEL] {alert_type}: insuficientes registros ({len(df_actual) if df_actual is not None else 0} < {min_registros}) — omitiendo")
        return []

    try:
        from sklearn.ensemble import IsolationForest
        from sklearn.preprocessing import OrdinalEncoder, RobustScaler
        from sklearn.pipeline import Pipeline
        from sklearn.compose import ColumnTransformer

        # Features de la ventana actual
        df_feat_actual = build_features_fn(df_actual)
        if df_feat_actual.empty:
            return []

        X_actual = df_feat_actual[feature_cols]

        # Datos de entrenamiento
        if modo_historico and conn is not None:
            df_hist = leer_historico_fn(conn, horas=24)
            if len(df_hist) >= min_registros:
                df_feat_hist = build_features_fn(df_hist)
                X_train = df_feat_hist[feature_cols]
                logger.info(f"[MODEL] {alert_type}: entrenando con {len(X_train):,} registros históricos")
            else:
                # Histórico insuficiente → cold-start
                X_train = X_actual
                logger.info(f"[MODEL] {alert_type}: histórico insuficiente → cold-start")
        else:
            X_train = X_actual
            logger.info(f"[MODEL] {alert_type}: cold-start con {len(X_train):,} registros")

        # Pipeline sklearn
        pipeline = _construir_pipeline_if(numeric_cols, categorical_cols)

        # Fit + predict
        pipeline.fit(X_train)
        scores = pipeline.decision_function(X_actual)  # más alto = más normal

        # Threshold y alertas
        threshold = _calcular_threshold(scores, modo_historico and len(X_train) != len(X_actual))
        mask_anomalo = scores < threshold

        n_anomalos = mask_anomalo.sum()
        logger.info(
            f"[MODEL] {alert_type}: {n_anomalos}/{len(scores)} anómalos "
            f"(threshold={threshold:.4f})"
        )

        if n_anomalos == 0:
            return []

        return _scores_a_alertas(
            df_feat=df_feat_actual,
            df_original=df_actual,
            scores=scores,
            mask_anomalo=mask_anomalo,
            alert_type=alert_type,
            context_cols=["LOG_TYPE", "HTTP_STATUS", "APPLICATION"],
        )

    except Exception as e:
        logger.error(f"[MODEL] Error en {alert_type}: {e}", exc_info=True)
        return []


# ---------------------------------------------------------------------------
# IF_llm — Isolation Forest sobre logs de LLM
# ---------------------------------------------------------------------------

def _correr_isolation_forest_llm(
    df_actual, conn, modo_historico, build_features_fn,
    leer_historico_fn, feature_cols, numeric_cols,
    categorical_cols, min_registros, alert_type,
) -> list:
    """
    Entrena IF sobre LLM y scorea la ventana actual.
    Lógica idéntica a IF_sistema pero con features LLM.
    """
    if df_actual is None or len(df_actual) < min_registros:
        logger.info(f"[MODEL] {alert_type}: insuficientes registros ({len(df_actual) if df_actual is not None else 0} < {min_registros}) — omitiendo")
        return []

    try:
        from sklearn.ensemble import IsolationForest
        from sklearn.preprocessing import OrdinalEncoder, RobustScaler

        df_feat_actual = build_features_fn(df_actual)
        if df_feat_actual.empty:
            return []

        X_actual = df_feat_actual[feature_cols]

        if modo_historico and conn is not None:
            df_hist = leer_historico_fn(conn, horas=24)
            if len(df_hist) >= min_registros:
                df_feat_hist = build_features_fn(df_hist)
                X_train = df_feat_hist[feature_cols]
                logger.info(f"[MODEL] {alert_type}: entrenando con {len(X_train):,} registros históricos")
            else:
                X_train = X_actual
                logger.info(f"[MODEL] {alert_type}: histórico insuficiente → cold-start")
        else:
            X_train = X_actual
            logger.info(f"[MODEL] {alert_type}: cold-start con {len(X_train):,} registros")

        pipeline = _construir_pipeline_if(numeric_cols, categorical_cols)
        pipeline.fit(X_train)
        scores = pipeline.decision_function(X_actual)

        threshold = _calcular_threshold(scores, modo_historico and len(X_train) != len(X_actual))
        mask_anomalo = scores < threshold

        n_anomalos = mask_anomalo.sum()
        logger.info(
            f"[MODEL] {alert_type}: {n_anomalos}/{len(scores)} anómalos "
            f"(threshold={threshold:.4f})"
        )

        if n_anomalos == 0:
            return []

        return _scores_a_alertas(
            df_feat=df_feat_actual,
            df_original=df_actual,
            scores=scores,
            mask_anomalo=mask_anomalo,
            alert_type=alert_type,
            context_cols=["LLM_STATUS", "LLM_MODEL_ID", "LOG_TYPE"],
        )

    except Exception as e:
        logger.error(f"[MODEL] Error en {alert_type}: {e}", exc_info=True)
        return []


# ---------------------------------------------------------------------------
# LOF_ip — Local Outlier Factor sobre tabla de comportamiento por IP
# ---------------------------------------------------------------------------

def _correr_lof_ip(df_sistema, build_ip_fn, feature_cols) -> list:
    """
    Construye la tabla de comportamiento por IP y aplica LOF.
    Retorna lista de alertas para IPs con comportamiento anómalo.
    """
    if df_sistema is None or df_sistema.empty:
        return []

    try:
        from sklearn.neighbors import LocalOutlierFactor
        from sklearn.preprocessing import RobustScaler

        df_ip = build_ip_fn(df_sistema)

        if len(df_ip) < MIN_IPS_LOF:
            logger.info(
                f"[MODEL] ml_ip_anomaly: {len(df_ip)} IPs < mínimo {MIN_IPS_LOF} — omitiendo LOF"
            )
            return []

        X = df_ip[feature_cols].values.astype(float)

        # RobustScaler antes de LOF (LOF necesita distancias métricas)
        scaler = RobustScaler()
        X_scaled = scaler.fit_transform(X)

        # n_neighbors ajustado dinámicamente si hay pocas IPs
        n_neighbors = min(LOF_N_NEIGHBORS, len(df_ip) - 1)

        lof = LocalOutlierFactor(
            n_neighbors=n_neighbors,
            contamination="auto",
            novelty=False,
        )
        labels = lof.fit_predict(X_scaled)        # -1 = outlier, 1 = inlier
        lof_scores = lof.negative_outlier_factor_  # más negativo = más anómalo

        mask_anomalo = labels == -1
        n_anomalos = mask_anomalo.sum()

        logger.info(
            f"[MODEL] ml_ip_anomaly: {n_anomalos}/{len(df_ip)} IPs anómalas "
            f"(n_neighbors={n_neighbors})"
        )

        if n_anomalos == 0:
            return []

        # Construir alertas para IPs anómalas
        alertas = []
        df_anomalas = df_ip[mask_anomalo].copy()
        df_anomalas["_lof_score"] = lof_scores[mask_anomalo]

        # Ordenar por score más negativo (más anómalo primero)
        df_anomalas = df_anomalas.sort_values("_lof_score", ascending=True)

        n_total_anomalos = len(df_anomalas)

        for rank, (_, row) in enumerate(df_anomalas.iterrows()):
            severity = _rank_to_severity(rank, n_total_anomalos)

            # Construir descripción legible
            details = (
                f"IP {row['CLIENT_IP']}: {int(row['event_count'])} eventos, "
                f"ratio_4xx={row['ratio_4xx']:.0%}, "
                f"ratio_5xx={row['ratio_5xx']:.0%}, "
                f"paths_distintos={int(row['distinct_paths'])}, "
                f"LOF_score={row['_lof_score']:.2f}"
            )
            details = details[:250]

            # Buscar el LOG_ID más reciente de esta IP en df_sistema
            registros_ip = df_sistema[df_sistema["CLIENT_IP"] == row["CLIENT_IP"]]
            if not registros_ip.empty:
                # El más reciente
                idx_mas_reciente = registros_ip["EVENT_TIMESTAMP"].idxmax()
                log_id    = str(registros_ip.loc[idx_mas_reciente, "LOG_ID"])
                event_time = str(registros_ip.loc[idx_mas_reciente, "EVENT_TIMESTAMP"])
            else:
                log_id    = f"ip_{row['CLIENT_IP']}"
                event_time = datetime.now(timezone.utc).isoformat()

            alertas.append({
                "alert_type": "ml_ip_anomaly",
                "severity":   severity,
                "details":    details,
                "log_id":     log_id,
                "event_time": event_time,
            })

        return alertas

    except Exception as e:
        logger.error(f"[MODEL] Error en LOF_ip: {e}", exc_info=True)
        return []


# ---------------------------------------------------------------------------
# Pipeline sklearn — IF con preprocessing
# ---------------------------------------------------------------------------

def _construir_pipeline_if(
    numeric_cols: list,
    categorical_cols: list,
):
    """
    Construye un Pipeline sklearn para IsolationForest.

    Preprocessing:
        Numéricas   → RobustScaler  (robusto a outliers, que es exactamente lo que buscamos)
        Categóricas → OrdinalEncoder (correcto para tree-based IF)

    La separación numérica/categórica viene definida por las listas
    exportadas de feature_eng.py — no se hardcodea aquí.
    """
    from sklearn.ensemble import IsolationForest
    from sklearn.preprocessing import OrdinalEncoder, RobustScaler
    from sklearn.pipeline import Pipeline
    from sklearn.compose import ColumnTransformer

    preprocessor = ColumnTransformer(
        transformers=[
            (
                "num",
                RobustScaler(),
                numeric_cols,
            ),
            (
                "cat",
                OrdinalEncoder(
                    handle_unknown="use_encoded_value",
                    unknown_value=-1,
                ),
                categorical_cols,
            ),
        ],
        remainder="drop",  # descarta columnas que no son features (LOG_ID, etc.)
    )

    pipeline = Pipeline([
        ("preprocessing", preprocessor),
        ("isolation_forest", IsolationForest(
            n_estimators=IF_N_ESTIMATORS,
            max_samples=IF_MAX_SAMPLES,
            contamination="auto",
            random_state=IF_RANDOM_STATE,
            n_jobs=-1,
        )),
    ])

    return pipeline


# ---------------------------------------------------------------------------
# Thresholding
# ---------------------------------------------------------------------------

def _calcular_threshold(scores: np.ndarray, modo_historico: bool) -> float:
    """
    Calcula el umbral de anomalía según el modo operativo.

    Cold-start (modo_historico=False):
        threshold = Q3 + IQR_MULTIPLIER × IQR
        Nota: decision_function de IF retorna valores donde más negativo = más anómalo.
        Usamos el extremo INFERIOR (Q1 - 1.5×IQR) para marcar los valores más negativos.

    Modo histórico (modo_historico=True):
        Modified Z-score basado en MAD.
        threshold = median - MAD_ZSCORE_THRESHOLD × (MAD / 0.6745)
        Marcamos como anómalos los scores significativamente por debajo de la mediana.
    """
    if modo_historico:
        mediana = np.median(scores)
        mad = np.median(np.abs(scores - mediana))
        if mad == 0:
            # MAD=0 significa todos los scores son iguales — sin anomalías
            return -np.inf
        threshold = mediana - MAD_ZSCORE_THRESHOLD * (mad / 0.6745)
        logger.debug(
            f"[MODEL] Threshold MAD: median={mediana:.4f}, "
            f"mad={mad:.4f}, threshold={threshold:.4f}"
        )
    else:
        q1 = np.percentile(scores, 25)
        q3 = np.percentile(scores, 75)
        iqr = q3 - q1
        if iqr == 0:
            return -np.inf
        threshold = q1 - IQR_MULTIPLIER * iqr
        logger.debug(
            f"[MODEL] Threshold IQR: Q1={q1:.4f}, Q3={q3:.4f}, "
            f"IQR={iqr:.4f}, threshold={threshold:.4f}"
        )

    return threshold


# ---------------------------------------------------------------------------
# Conversión de scores a alertas
# ---------------------------------------------------------------------------

def _scores_a_alertas(
    df_feat: pd.DataFrame,
    df_original: pd.DataFrame,
    scores: np.ndarray,
    mask_anomalo: np.ndarray,
    alert_type: str,
    context_cols: list,
) -> list:
    """
    Convierte los índices anómalos en lista de dicts con el contrato del pipeline.

    Selecciona el registro MÁS anómalo (score más negativo) como representante.
    Para cada registro anómalo calcula severidad según su posición en el ranking.

    context_cols: columnas del df_original que se incluyen en el campo details.
    """
    alertas = []

    # Índices originales de los anómalos
    idx_anomalos = np.where(mask_anomalo)[0]

    if len(idx_anomalos) == 0:
        return []

    # Ordenar de más anómalo (score más negativo) a menos
    scores_anomalos = scores[idx_anomalos]
    orden = np.argsort(scores_anomalos)  # ascendente: más negativo primero
    idx_ordenados = idx_anomalos[orden]

    n_total = len(idx_ordenados)

    for rank, idx in enumerate(idx_ordenados):
        severity = _rank_to_severity(rank, n_total)

        # LOG_ID y EVENT_TIMESTAMP del registro anómalo
        try:
            log_id     = str(df_feat.iloc[idx]["LOG_ID"])
            event_time = str(df_original.iloc[idx]["EVENT_TIMESTAMP"])
        except (IndexError, KeyError):
            log_id     = f"unknown_{idx}"
            event_time = datetime.now(timezone.utc).isoformat()

        # Construir details con contexto del registro
        context_parts = []
        for col in context_cols:
            if col in df_original.columns:
                val = df_original.iloc[idx].get(col, "?")
                if val and str(val) not in ("nan", "None", "UNKNOWN"):
                    context_parts.append(f"{col}={val}")

        score_val = scores[idx]
        context_str = ", ".join(context_parts)
        details = (
            f"Anomaly score={score_val:.4f} | rank={rank+1}/{n_total} | {context_str}"
        )
        details = details[:250]

        alertas.append({
            "alert_type": alert_type,
            "severity":   severity,
            "details":    details,
            "log_id":     log_id,
            "event_time": event_time,
        })

    return alertas


def _rank_to_severity(rank: int, n_total: int) -> str:
    """
    Asigna severidad según la posición en el ranking de anomalías.

    Top 10% → high
    Top 10–30% → medium
    Resto → low
    """
    if n_total == 0:
        return "low"
    ratio = rank / n_total
    if ratio < UMBRAL_HIGH:
        return "high"
    elif ratio < UMBRAL_MEDIUM:
        return "medium"
    else:
        return "low"


# ---------------------------------------------------------------------------
# Script de prueba standalone
# ---------------------------------------------------------------------------

def _probar_model():
    """
    Prueba el módulo con datos sintéticos — sin HANA, sin conexión real.

    Simula el flujo completo:
        1. Datos sintéticos que replican distribuciones reales de HANA
        2. build_features (feature_eng)
        3. IF_sistema con cold-start
        4. IF_llm con cold-start
        5. LOF_ip
        6. analizar_ventana() con conn=None (modo degradado)
        7. Verificación del contrato de retorno

    Uso:
        python -m app.model
    """
    import logging
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
        datefmt="%H:%M:%S",
    )

    try:
        from sklearn.ensemble import IsolationForest
        from sklearn.neighbors import LocalOutlierFactor
    except ImportError:
        print("❌ scikit-learn no está instalado. Ejecutar: pip install scikit-learn")
        return

    try:
        from app.feature_eng import (
            build_sistema_features, build_llm_features, build_ip_behavior_table,
            FEATURE_COLS_SISTEMA, NUMERIC_COLS_SISTEMA, CATEGORICAL_COLS_SISTEMA,
            FEATURE_COLS_LLM, NUMERIC_COLS_LLM, CATEGORICAL_COLS_LLM,
            FEATURE_COLS_IP,
        )
    except ImportError:
        from feature_eng import (
            build_sistema_features, build_llm_features, build_ip_behavior_table,
            FEATURE_COLS_SISTEMA, NUMERIC_COLS_SISTEMA, CATEGORICAL_COLS_SISTEMA,
            FEATURE_COLS_LLM, NUMERIC_COLS_LLM, CATEGORICAL_COLS_LLM,
            FEATURE_COLS_IP,
        )

    print("\n" + "=" * 60)
    print("PRUEBA — app/model.py (datos sintéticos)")
    print("=" * 60)

    rng = np.random.default_rng(42)
    n_sis = 500
    n_llm = 300
    n_ips = 25

    http_statuses = ["200"] * 60 + ["201"] * 15 + ["400"] * 8 + ["401"] * 5 + \
                    ["403"] * 4 + ["429"] * 3 + ["500"] * 3 + ["503"] * 2
    log_types_sis = ["INFO"] * 40 + ["WARNING"] * 20 + ["ERROR"] * 15 + \
                    ["AUDIT"] * 10 + ["DEBUG"] * 10 + ["PERF"] * 3 + ["SECURITY"] * 2
    ips = [f"10.0.0.{i}" for i in range(n_ips)]
    timestamps = pd.date_range("2026-05-04 00:00:00", periods=n_sis, freq="4s", tz="UTC")

    df_sis = pd.DataFrame({
        "LOG_ID":          [f"s{i}" for i in range(n_sis)],
        "EVENT_TIMESTAMP": timestamps,
        "LOG_TYPE":        rng.choice(log_types_sis, n_sis),
        "HTTP_STATUS":     rng.choice(http_statuses, n_sis),
        "CLIENT_IP":       rng.choice(ips, n_sis),
        "REQUEST_PATH":    rng.choice(["/api", "/btp", "/health", "/.env", "/admin"], n_sis),
        "APPLICATION":     rng.choice([f"app_{i}" for i in range(10)], n_sis),
        "REGION_NAME":     rng.choice([f"region_{i}" for i in range(30)], n_sis),
        "MESSAGE":         ["msg"] * n_sis,
    })
    # Inyectar anomalías claras para que IF las detecte
    df_sis.loc[0, "HTTP_STATUS"]  = "401"
    df_sis.loc[0, "LOG_TYPE"]     = "SECURITY"
    df_sis.loc[1, "HTTP_STATUS"]  = "403"
    df_sis.loc[2, "HTTP_STATUS"]  = "500"

    timestamps_llm = pd.date_range("2026-05-04 00:00:00", periods=n_llm, freq="6s", tz="UTC")
    df_llm = pd.DataFrame({
        "LOG_ID":              [f"l{i}" for i in range(n_llm)],
        "EVENT_TIMESTAMP":     timestamps_llm,
        "LOG_TYPE":            rng.choice(["LLM_REQUEST"] * 7 + ["LLM_ERROR"] * 2 + ["LLM_TIMEOUT"], n_llm),
        "LLM_STATUS":          rng.choice(["success"] * 7 + ["error"] * 2 + ["timeout"], n_llm),
        "LLM_MODEL_ID":        rng.choice(["claude-3-sonnet", "gpt-4", "gemini-1.5-pro"], n_llm),
        "LLM_PROVIDER":        rng.choice(["anthropic", "openai", "google"], n_llm),
        "LLM_COST_USD":        rng.uniform(0.00001, 0.05, n_llm),
        "LLM_RESPONSE_TIME":   rng.uniform(200, 15000, n_llm),
        "LLM_TOTAL_TOKENS":    rng.integers(84, 2000, n_llm),
        "LLM_TEMPERATURE":     rng.uniform(0, 1, n_llm),
        "LLM_ERROR_MESSAGE":   [None] * n_llm,
        "LLM_PROMPT_CATEGORY": rng.choice(["chat", "code", None], n_llm),
    })
    # Inyectar anomalías LLM claras
    df_llm.loc[0, "LLM_COST_USD"]       = 0.13   # costo extremo
    df_llm.loc[0, "LLM_RESPONSE_TIME"]  = 34999   # timeout máximo
    df_llm.loc[1, "LLM_STATUS"]         = "timeout"

    # ── Test 1: IF_sistema cold-start ─────────────────────────────────────────
    print("\n[TEST 1] IF_sistema cold-start...")
    alertas_sis = _correr_isolation_forest_sistema(
        df_actual=df_sis,
        conn=None,
        modo_historico=False,
        build_features_fn=build_sistema_features,
        leer_historico_fn=lambda conn, horas: pd.DataFrame(),
        feature_cols=FEATURE_COLS_SISTEMA,
        numeric_cols=NUMERIC_COLS_SISTEMA,
        categorical_cols=CATEGORICAL_COLS_SISTEMA,
        min_registros=MIN_REGISTROS_SISTEMA,
        alert_type="ml_sistema_anomaly",
    )
    _verificar_alertas(alertas_sis, "ml_sistema_anomaly")

    # ── Test 2: IF_llm cold-start ─────────────────────────────────────────────
    print("\n[TEST 2] IF_llm cold-start...")
    alertas_llm = _correr_isolation_forest_llm(
        df_actual=df_llm,
        conn=None,
        modo_historico=False,
        build_features_fn=build_llm_features,
        leer_historico_fn=lambda conn, horas: pd.DataFrame(),
        feature_cols=FEATURE_COLS_LLM,
        numeric_cols=NUMERIC_COLS_LLM,
        categorical_cols=CATEGORICAL_COLS_LLM,
        min_registros=MIN_REGISTROS_LLM,
        alert_type="ml_llm_anomaly",
    )
    _verificar_alertas(alertas_llm, "ml_llm_anomaly")

    # ── Test 3: LOF_ip ────────────────────────────────────────────────────────
    print("\n[TEST 3] LOF_ip...")
    alertas_ip = _correr_lof_ip(
        df_sistema=df_sis,
        build_ip_fn=build_ip_behavior_table,
        feature_cols=FEATURE_COLS_IP,
    )
    _verificar_alertas(alertas_ip, "ml_ip_anomaly")

    # ── Test 4: Cap de alertas ────────────────────────────────────────────────
    print("\n[TEST 4] Cap de alertas...")
    todas = alertas_sis + alertas_llm + alertas_ip
    orden_sev = {"high": 0, "medium": 1, "low": 2}
    todas.sort(key=lambda a: orden_sev.get(a["severity"], 3))
    finales = todas[:MAX_ALERTAS_POR_CICLO]
    assert len(finales) <= MAX_ALERTAS_POR_CICLO, \
        f"Cap violado: {len(finales)} > {MAX_ALERTAS_POR_CICLO}"
    print(f"   ✅ Cap: {len(todas)} totales → {len(finales)} enviadas (max={MAX_ALERTAS_POR_CICLO})")

    # ── Test 5: analizar_ventana con conn=None ────────────────────────────────
    print("\n[TEST 5] analizar_ventana(conn=None) → debe retornar [] sin crash...")
    resultado = analizar_ventana(conn=None, window_start="test")
    assert resultado == [], f"Esperaba [], got {resultado}"
    print("   ✅ conn=None retorna [] sin excepción")

    # ── Test 6: Thresholds ────────────────────────────────────────────────────
    print("\n[TEST 6] Thresholds IQR y MAD...")
    scores_test = np.array([-0.5, -0.4, -0.3, -0.2, -0.1, 0.0, 0.1, 0.2, -2.0, -3.0])
    t_iqr = _calcular_threshold(scores_test, modo_historico=False)
    t_mad = _calcular_threshold(scores_test, modo_historico=True)
    assert t_iqr < 0, f"IQR threshold debería ser negativo: {t_iqr}"
    assert t_mad < 0, f"MAD threshold debería ser negativo: {t_mad}"
    assert t_mad < t_iqr, f"MAD debería ser más restrictivo que IQR en este caso"
    print(f"   ✅ IQR threshold: {t_iqr:.4f}")
    print(f"   ✅ MAD threshold: {t_mad:.4f}")
    print(f"   ✅ MAD más restrictivo que IQR (esperado en modo histórico)")

    # ── Resumen final ─────────────────────────────────────────────────────────
    print(f"\n--- Alertas generadas ---")
    print(f"   IF_sistema:  {len(alertas_sis)} alertas")
    print(f"   IF_llm:      {len(alertas_llm)} alertas")
    print(f"   LOF_ip:      {len(alertas_ip)} alertas")
    print(f"   Total final: {len(finales)} (cap={MAX_ALERTAS_POR_CICLO})")

    if finales:
        print("\n   Muestra de la primera alerta:")
        a = finales[0]
        for k, v in a.items():
            print(f"     {k}: {v}")

    print("\n" + "=" * 60)
    print("✅ Todos los tests pasaron — model.py listo para integración")
    print("=" * 60 + "\n")


def _verificar_alertas(alertas: list, alert_type_esperado: str):
    """Verifica que las alertas cumplen el contrato del pipeline."""
    print(f"   Alertas generadas: {len(alertas)}")

    campos_requeridos = {"alert_type", "severity", "details", "log_id", "event_time"}
    severidades_validas = {"low", "medium", "high"}

    for i, a in enumerate(alertas):
        # Campos presentes
        faltantes = campos_requeridos - set(a.keys())
        assert not faltantes, f"Alerta {i}: campos faltantes {faltantes}"

        # alert_type correcto
        assert a["alert_type"] == alert_type_esperado, \
            f"Alerta {i}: alert_type='{a['alert_type']}' ≠ '{alert_type_esperado}'"

        # Severidad válida
        assert a["severity"] in severidades_validas, \
            f"Alerta {i}: severidad inválida '{a['severity']}'"

        # details no vacío y dentro del límite
        assert a["details"] and len(a["details"]) <= 250, \
            f"Alerta {i}: details vacío o > 250 chars ({len(a['details'])})"

        # log_id no vacío
        assert a["log_id"], f"Alerta {i}: log_id vacío"

    print(f"   ✅ Contrato cumplido: campos, tipos, severidades y límites correctos")
    if alertas:
        sevs = [a["severity"] for a in alertas]
        from collections import Counter
        print(f"   Severidades: {dict(Counter(sevs))}")


if __name__ == "__main__":
    _probar_model()
