"""
dashboard/hana_dashboard.py
===========================
Único módulo que ejecuta SQL contra SAP HANA Cloud desde el dashboard.
Todas las operaciones son SELECT — nunca escribe en HANA.

Owner:  Dev 2
Repo:   sap-security-hackathon / dashboard/
"""

import os
import logging
from datetime import datetime, timezone

import pandas as pd
import streamlit as st
from hdbcli import dbapi

logger = logging.getLogger(__name__)

# Cache modular para el nombre de la columna de path (typo histórico)
_PATH_COLUMN: str | None = None


# ── Conexión ──────────────────────────────────────────────────────────────────

@st.cache_resource
def get_connection():
    """
    Retorna conexión hdbcli singleton reutilizada por toda la sesión de Streamlit.
    Lee credenciales exclusivamente desde variables de entorno.
    Al correr fuera de Streamlit (test_dev2.py), se comporta como función normal.

    Variables requeridas en .env:
        HANA_HOST, HANA_USER, HANA_PASSWORD, HANA_PORT (default 443)

    Returns:
        dbapi.Connection activa.

    Raises:
        KeyError: si alguna variable de entorno requerida falta.
        dbapi.Error: si la conexión falla.
    """
    conn = dbapi.connect(
        address=os.environ["HANA_HOST"],
        port=int(os.environ.get("HANA_PORT", 443)),
        user=os.environ["HANA_USER"],
        password=os.environ["HANA_PASSWORD"],
        encrypt=True,
        sslValidateCertificate=False,
    )
    return conn


# ── Helpers internos ──────────────────────────────────────────────────────────

def _exec(conn, sql: str) -> pd.DataFrame:
    """
    Ejecuta SQL y retorna DataFrame con columnas en lowercase.
    Centraliza el manejo de cursor para evitar leaks de recursos.
    No exportado — solo para uso interno del módulo.

    Args:
        conn: conexión HANA activa.
        sql:  query SQL a ejecutar.

    Returns:
        pd.DataFrame con columnas en lowercase.
        DataFrame vacío si la query no retorna filas.

    Raises:
        dbapi.Error: si la query falla. El error incluye el SQL para debugging.
    """
    cursor = conn.cursor()
    try:
        cursor.execute(sql)
        if cursor.description is None:
            return pd.DataFrame()
        cols = [d[0].lower() for d in cursor.description]
        rows = cursor.fetchall()
        return pd.DataFrame(rows, columns=cols)
    except Exception as exc:
        logger.error("SQL error: %s | SQL (primeros 300 chars): %s", exc, sql[:300])
        raise
    finally:
        cursor.close()


def _get_path_column(conn) -> str:
    """
    Detecta el nombre real de la columna de request path en RAW_LOGS_SISTEMA.
    El nombre varía por un typo histórico de la API original:
    puede ser 'heathers_request_path' o 'headers_request_path'.
    Cachea el resultado para no hacer esta query más de una vez por sesión.

    Args:
        conn: conexión HANA activa.

    Returns:
        Nombre de columna en UPPERCASE tal como está en HANA.
        Default 'HEATHERS_REQUEST_PATH' si no se puede detectar.
    """
    global _PATH_COLUMN
    if _PATH_COLUMN is not None:
        return _PATH_COLUMN

    cursor = conn.cursor()
    try:
        cursor.execute("""
            SELECT COLUMN_NAME
            FROM SYS.TABLE_COLUMNS
            WHERE TABLE_NAME = 'RAW_LOGS_SISTEMA'
              AND UPPER(COLUMN_NAME) LIKE '%PATH%'
            LIMIT 1
        """)
        row = cursor.fetchone()
        _PATH_COLUMN = row[0] if row else "HEATHERS_REQUEST_PATH"
    except Exception:
        _PATH_COLUMN = "HEATHERS_REQUEST_PATH"
    finally:
        cursor.close()

    logger.info("Columna de path detectada: %s", _PATH_COLUMN)
    return _PATH_COLUMN


# ── KPIs de estado de seguridad ───────────────────────────────────────────────

def get_kpis(conn) -> dict:
    """
    Calcula 4 métricas de estado de seguridad para los cards del Tab 1.
    Ejecuta 3 queries a HANA (combinadas para eficiencia).

    Args:
        conn: conexión HANA activa de get_connection().

    Returns:
        dict con keys:
            threat_level (str):        'NORMAL' | 'ELEVATED' | 'CRITICAL'
            alerts_last_hour (int):    total alertas últimos 60 min
            alerts_delta_pct (float|None): variación vs hora anterior, None si prev==0
            attack_diversity (dict):   {'active': int, 'total': 7}
            top_threat (dict|None):    {'alert_type': str, 'count': int} o None
    """
    # ── Query 1: métricas temporales en una sola pasada ───────────────────────
    df_counts = _exec(conn, """
        SELECT
            SUM(CASE
                WHEN SEVERITY = 'high'
                 AND DETECTED_AT >= ADD_SECONDS(NOW(), -3600)
                THEN 1 ELSE 0 END)                                  AS high_last_hour,
            SUM(CASE
                WHEN DETECTED_AT >= ADD_SECONDS(NOW(), -3600)
                THEN 1 ELSE 0 END)                                  AS alerts_last_hour,
            SUM(CASE
                WHEN DETECTED_AT >= ADD_SECONDS(NOW(), -7200)
                 AND DETECTED_AT  < ADD_SECONDS(NOW(), -3600)
                THEN 1 ELSE 0 END)                                  AS alerts_prev_hour
        FROM DBADMIN.ALERTS
        WHERE DETECTED_AT    >= ADD_SECONDS(NOW(), -7200)
          AND ALERTED         = 1
          AND SUPPRESSED_BY  IS NULL
    """)

    row = df_counts.iloc[0] if not df_counts.empty else {}
    high_last_hour  = int(row.get("high_last_hour",  0) or 0)
    alerts_now      = int(row.get("alerts_last_hour", 0) or 0)
    alerts_prev     = int(row.get("alerts_prev_hour", 0) or 0)

    # Threat level
    if high_last_hour >= 3:
        threat_level = "CRITICAL"
    elif high_last_hour >= 1:
        threat_level = "ELEVATED"
    else:
        threat_level = "NORMAL"

    # Delta porcentual — None si hora anterior tiene 0 alertas
    if alerts_prev == 0:
        delta_pct = None
    else:
        delta_pct = round(((alerts_now - alerts_prev) / alerts_prev) * 100, 1)

    # ── Query 2: diversidad de tipos activos hoy ──────────────────────────────
    df_div = _exec(conn, """
        SELECT COUNT(DISTINCT ALERT_TYPE) AS active_types
        FROM DBADMIN.ALERTS
        WHERE DETECTED_AT   >= ADD_DAYS(NOW(), -1)
          AND ALERTED        = 1
          AND SUPPRESSED_BY IS NULL
    """)
    active_types = int(df_div.iloc[0]["active_types"]) if not df_div.empty else 0

    # ── Query 3: tipo de amenaza más frecuente en la última hora ──────────────
    df_top = _exec(conn, """
        SELECT ALERT_TYPE, COUNT(*) AS cnt
        FROM DBADMIN.ALERTS
        WHERE DETECTED_AT   >= ADD_SECONDS(NOW(), -3600)
          AND ALERTED        = 1
          AND SUPPRESSED_BY IS NULL
        GROUP BY ALERT_TYPE
        ORDER BY cnt DESC
        LIMIT 1
    """)

    if df_top.empty:
        top_threat = None
    else:
        top_threat = {
            "alert_type": str(df_top.iloc[0]["alert_type"]),
            "count":      int(df_top.iloc[0]["cnt"]),
        }

    return {
        "threat_level":      threat_level,
        "alerts_last_hour":  alerts_now,
        "alerts_delta_pct":  delta_pct,
        "attack_diversity":  {"active": active_types, "total": 7},
        "top_threat":        top_threat,
    }


# ── Gráfica principal Tab 1 ───────────────────────────────────────────────────

# ════════════════════════════════════════════════════════════════════
# FUNCIÓN 1: get_activity_timeline
# Reemplaza la función completa en hana_dashboard.py
# ════════════════════════════════════════════════════════════════════
 
def get_activity_timeline(conn):
    """
    Retorna datos para la gráfica de actividad + marcas de alertas.
    Combina RAW_LOGS_SISTEMA (volumen por ventana de 30 min calculada desde
    EVENT_TIMESTAMP) con ALERTS (por severidad, usando su WINDOW_START).
 
    Args:
        conn: conexión HANA activa.
 
    Returns:
        pd.DataFrame ordenado por window_start ASC con columnas:
            window_start (datetime), log_volume (int),
            alerts_high (int), alerts_medium (int), alerts_low (int)
        Rango: últimas 48 horas.
        Ventanas sin alertas tendrán 0 en columnas de alertas (no NULL).
    """
    import pandas as pd
 
    # Volumen de logs agrupado por ventana de 30 min calculada desde EVENT_TIMESTAMP
    df_logs = _exec(conn, """
        SELECT
            ADD_SECONDS(
                TO_TIMESTAMP('1970-01-01'),
                FLOOR(
                    SECONDS_BETWEEN(TO_TIMESTAMP('1970-01-01'), EVENT_TIMESTAMP)
                    / 1800
                ) * 1800
            )           AS WINDOW_START,
            COUNT(*)    AS LOG_VOLUME
        FROM DBADMIN.RAW_LOGS_SISTEMA
        WHERE EVENT_TIMESTAMP >= ADD_DAYS(NOW(), -2)
        GROUP BY
            FLOOR(
                SECONDS_BETWEEN(TO_TIMESTAMP('1970-01-01'), EVENT_TIMESTAMP)
                / 1800
            )
        ORDER BY WINDOW_START ASC
    """)
 
    # Conteo de alertas por ventana y severidad (ALERTS sí tiene WINDOW_START)
    df_alerts = _exec(conn, """
        SELECT
            WINDOW_START,
            SUM(CASE WHEN SEVERITY = 'high'   THEN 1 ELSE 0 END) AS ALERTS_HIGH,
            SUM(CASE WHEN SEVERITY = 'medium' THEN 1 ELSE 0 END) AS ALERTS_MEDIUM,
            SUM(CASE WHEN SEVERITY = 'low'    THEN 1 ELSE 0 END) AS ALERTS_LOW
        FROM DBADMIN.ALERTS
        WHERE WINDOW_START  >= ADD_DAYS(NOW(), -2)
          AND ALERTED        = 1
          AND SUPPRESSED_BY IS NULL
        GROUP BY WINDOW_START
        ORDER BY WINDOW_START ASC
    """)
 
    if df_logs.empty:
        return pd.DataFrame(columns=[
            "window_start", "log_volume",
            "alerts_high", "alerts_medium", "alerts_low"
        ])
 
    # Normalizar timestamps antes del merge
    df_logs["window_start"]   = pd.to_datetime(df_logs["window_start"],   utc=True)
    df_alerts["window_start"] = pd.to_datetime(df_alerts["window_start"], utc=True)
 
    # Left join: preserva todas las ventanas de logs aunque no haya alertas
    df = df_logs.merge(df_alerts, on="window_start", how="left")
 
    # Rellenar NaN en columnas de alertas con 0
    for col in ["alerts_high", "alerts_medium", "alerts_low"]:
        df[col] = df[col].fillna(0).astype(int)
 
    df = df.sort_values("window_start").reset_index(drop=True)
    return df
 

# ── Tabla de alertas críticas Tab 1 ──────────────────────────────────────────

def get_critical_alerts(conn, hours: int = 24, limit: int = 20) -> pd.DataFrame:
    """
    Retorna las alertas más recientes para la tabla del Tab 1.
    client_ip viene de LEFT JOIN con RAW_LOGS_SISTEMA — puede ser None
    para alertas ML que no tienen log de sistema individual asociado.

    Args:
        conn:  conexión HANA activa.
        hours: ventana temporal en horas hacia atrás. Default 24.
        limit: máximo de filas. Default 20.

    Returns:
        pd.DataFrame ordenado por detected_at DESC con columnas:
            alert_id, alert_type, severity, detection_source,
            detail, detected_at (datetime), client_ip (str|None)
    """
    seconds_back = hours * 3600

    df = _exec(conn, f"""
        SELECT
            a.ALERT_ID,
            a.ALERT_TYPE,
            a.SEVERITY,
            a.DETECTION_SOURCE,
            a.DETAILS                  AS DETAIL,
            a.DETECTED_AT,
            s.CLIENT_IP
        FROM DBADMIN.ALERTS a
        LEFT JOIN DBADMIN.RAW_LOGS_SISTEMA s
               ON s.LOG_ID = a.LOG_ID
        WHERE a.ALERTED       = 1
          AND a.DETECTED_AT  >= ADD_SECONDS(NOW(), -{seconds_back})
        ORDER BY a.DETECTED_AT DESC
        LIMIT {limit}
    """)

    if df.empty:
        return df

    # Normalizar tipos
    df["detected_at"] = pd.to_datetime(df["detected_at"], utc=True)

    # client_ip: convertir NaN de pandas a None explícito
    df["client_ip"] = df["client_ip"].where(df["client_ip"].notna(), other=None)

    return df


# ── Contexto para el agente LLM ───────────────────────────────────────────────

def get_agent_context(conn) -> str:
    """
    Ejecuta múltiples queries y construye un string estructurado para inyectar
    como contexto live al agente GPT-4o en cada pregunta.

    Nunca lanza excepción — si alguna query falla, la sección correspondiente
    muestra un mensaje de error dentro del string de contexto.

    Args:
        conn: conexión HANA activa.

    Returns:
        str con secciones delimitadas con ═══. Siempre retorna string válido.
    """
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    secciones = [f"DATOS EN TIEMPO REAL — SAP HANA (consultados: {ts})"]

    # ── Sección 1: distribución de alertas últimas 24h ────────────────────────
    try:
        df = _exec(conn, """
            SELECT
                ALERT_TYPE,
                SEVERITY,
                COUNT(*) AS total
            FROM DBADMIN.ALERTS
            WHERE DETECTED_AT   >= ADD_DAYS(NOW(), -1)
              AND ALERTED        = 1
              AND SUPPRESSED_BY IS NULL
            GROUP BY ALERT_TYPE, SEVERITY
            ORDER BY total DESC
        """)
        if df.empty:
            secciones.append("═══ ALERTAS ÚLTIMAS 24H ═══\nSin alertas en las últimas 24 horas.")
        else:
            lineas = ["═══ ALERTAS ÚLTIMAS 24H ═══"]
            for _, row in df.iterrows():
                lineas.append(
                    f"  {row['alert_type']:<30} severity={row['severity']:<8} "
                    f"count={int(row['total'])}"
                )
            secciones.append("\n".join(lineas))
    except Exception as exc:
        secciones.append(f"═══ ALERTAS ÚLTIMAS 24H ═══\n[Error al consultar: {exc}]")

    # ── Sección 2: top 5 IPs con auth failures ────────────────────────────────
    try:
        df = _exec(conn, """
            SELECT
                CLIENT_IP,
                COUNT(*) AS auth_failures
            FROM DBADMIN.RAW_LOGS_SISTEMA
            WHERE HTTP_STATUS  IN ('401', '403')
              AND CLIENT_IP    IS NOT NULL
              AND EVENT_TIMESTAMP >= ADD_DAYS(NOW(), -1)
            GROUP BY CLIENT_IP
            ORDER BY auth_failures DESC
            LIMIT 5
        """)
        if df.empty:
            secciones.append("═══ TOP IPs SOSPECHOSAS (24h) ═══\nSin auth failures detectados.")
        else:
            lineas = ["═══ TOP IPs SOSPECHOSAS (24h) ═══"]
            for _, row in df.iterrows():
                lineas.append(
                    f"  IP: {str(row['client_ip']):<20} "
                    f"auth_failures: {int(row['auth_failures'])}"
                )
            secciones.append("\n".join(lineas))
    except Exception as exc:
        secciones.append(f"═══ TOP IPs SOSPECHOSAS (24h) ═══\n[Error al consultar: {exc}]")

    # ── Sección 3: resumen de actividad LLM ───────────────────────────────────
    try:
        df = _exec(conn, """
            SELECT
                COUNT(*)                        AS total_llamadas,
                ROUND(AVG(LLM_COST_USD), 4)     AS avg_costo_usd,
                ROUND(SUM(LLM_COST_USD), 4)     AS costo_total_usd,
                SUM(CASE WHEN LLM_STATUS NOT IN ('success', 'completed')
                         THEN 1 ELSE 0 END)     AS total_errores
            FROM DBADMIN.RAW_LOGS_LLM
            WHERE EVENT_TIMESTAMP >= ADD_DAYS(NOW(), -1)
        """)
        if not df.empty:
            row = df.iloc[0]
            secciones.append(
                "═══ ACTIVIDAD LLM (24h) ═══\n"
                f"  Total llamadas:    {int(row['total_llamadas'] or 0)}\n"
                f"  Costo promedio:    ${float(row['avg_costo_usd'] or 0):.4f} USD\n"
                f"  Costo total:       ${float(row['costo_total_usd'] or 0):.4f} USD\n"
                f"  Total errores LLM: {int(row['total_errores'] or 0)}"
            )
    except Exception as exc:
        secciones.append(f"═══ ACTIVIDAD LLM (24h) ═══\n[Error al consultar: {exc}]")

    # ── Sección 4: última alerta registrada ───────────────────────────────────
    try:
        df = _exec(conn, """
            SELECT
                ALERT_TYPE,
                SEVERITY,
                DETECTED_AT,
                DETAILS
            FROM DBADMIN.ALERTS
            WHERE ALERTED       = 1
              AND SUPPRESSED_BY IS NULL
            ORDER BY DETECTED_AT DESC
            LIMIT 1
        """)
        if df.empty:
            secciones.append("═══ ÚLTIMA ALERTA ═══\nSin alertas registradas.")
        else:
            row = df.iloc[0]
            secciones.append(
                "═══ ÚLTIMA ALERTA ═══\n"
                f"  Tipo:      {row['alert_type']}\n"
                f"  Severidad: {row['severity']}\n"
                f"  Hora:      {row['detected_at']}\n"
                f"  Detalle:   {row['details']}"
            )
    except Exception as exc:
        secciones.append(f"═══ ÚLTIMA ALERTA ═══\n[Error al consultar: {exc}]")

    # ── Sección 5: alertas suprimidas ─────────────────────────────────────────
    try:
        df = _exec(conn, """
            SELECT COUNT(*) AS suprimidas
            FROM DBADMIN.ALERTS
            WHERE DETECTED_AT   >= ADD_DAYS(NOW(), -1)
              AND SUPPRESSED_BY IS NOT NULL
        """)
        suprimidas = int(df.iloc[0]["suprimidas"]) if not df.empty else 0
        secciones.append(f"═══ SUPRESIÓN DE DUPLICADOS (24h) ═══\n  Alertas suprimidas: {suprimidas}")
    except Exception as exc:
        secciones.append(f"═══ SUPRESIÓN ═══\n[Error al consultar: {exc}]")

    return "\n\n".join(secciones)


# ── Dispatcher del catálogo de gráficas ──────────────────────────────────────

def get_chart_data(conn, chart_key: str):
    """
    Dispatcher que ejecuta la query correspondiente al chart_key.

    Retorna pd.DataFrame para chart_keys normales.
    Retorna tuple(pd.DataFrame, pd.DataFrame) para overlays:
        'llm_anomalias'     → (df_llm_actividad,     df_llm_alertas)
        'sistema_anomalias' → (df_sistema_actividad, df_sistema_alertas)

    Raises:
        ValueError: si chart_key no existe en el catálogo.
    """
    _CATALOG = {
        "brute_force_por_ip":   _query_brute_force_por_ip,
        "brute_force_por_hora": _query_brute_force_por_hora,
        "alertas_por_tipo":     _query_alertas_por_tipo,
        "alertas_por_hora":     _query_alertas_por_hora,
        "path_scan_activity":   _query_path_scan_activity,
        "llm_anomalias":        _query_llm_anomalias,
        "sistema_anomalias":    _query_sistema_anomalias,
    }

    if chart_key not in _CATALOG:
        raise ValueError(
            f"chart_key '{chart_key}' no existe. "
            f"Disponibles: {list(_CATALOG.keys())}"
        )

    return _CATALOG[chart_key](conn)


# ── Queries privadas del catálogo ─────────────────────────────────────────────

def _query_brute_force_por_ip(conn) -> pd.DataFrame:
    """
    Top IPs con mayor número de intentos de autenticación fallidos en 24h.
    WHERE CLIENT_IP IS NOT NULL es obligatorio para evitar fila agregada con IP=None.
    HTTP_STATUS es NVARCHAR — usar strings en el IN.
    """
    df = _exec(conn, """
        SELECT
            CLIENT_IP,
            COUNT(*)        AS AUTH_FAILURES,
            MIN(EVENT_TIMESTAMP) AS PRIMERA_ACTIVIDAD,
            MAX(EVENT_TIMESTAMP) AS ULTIMA_ACTIVIDAD
        FROM DBADMIN.RAW_LOGS_SISTEMA
        WHERE HTTP_STATUS       IN ('401', '403')
          AND CLIENT_IP         IS NOT NULL
          AND EVENT_TIMESTAMP   >= ADD_DAYS(NOW(), -1)
        GROUP BY CLIENT_IP
        ORDER BY AUTH_FAILURES DESC
        LIMIT 20
    """)
    if not df.empty:
        df["primera_actividad"] = pd.to_datetime(df["primera_actividad"], utc=True)
        df["ultima_actividad"]  = pd.to_datetime(df["ultima_actividad"],  utc=True)
        df["auth_failures"]     = df["auth_failures"].astype(int)
    return df


def _query_brute_force_por_hora(conn) -> pd.DataFrame:
    """
    Distribución de intentos de autenticación fallidos por hora del día (0-23).
    Útil para identificar franjas horarias de mayor actividad de ataque.
    """
    df = _exec(conn, """
        SELECT
            HOUR(EVENT_TIMESTAMP) AS HORA,
            COUNT(*)              AS AUTH_FAILURES
        FROM DBADMIN.RAW_LOGS_SISTEMA
        WHERE HTTP_STATUS      IN ('401', '403')
          AND CLIENT_IP        IS NOT NULL
          AND EVENT_TIMESTAMP  >= ADD_DAYS(NOW(), -1)
        GROUP BY HOUR(EVENT_TIMESTAMP)
        ORDER BY HORA ASC
    """)

    if df.empty:
        return df

    df["hora"]          = df["hora"].astype(int)
    df["auth_failures"] = df["auth_failures"].astype(int)

    # Asegurar que todas las horas 0-23 estén representadas
    df_full = pd.DataFrame({"hora": range(24)})
    df = df_full.merge(df, on="hora", how="left").fillna(0)
    df["auth_failures"] = df["auth_failures"].astype(int)

    return df


def _query_alertas_por_tipo(conn) -> pd.DataFrame:
    """
    Distribución de alertas por tipo y fuente de detección, últimas 24h.
    Útil para comparar qué detecta quick_filter vs model_ml.
    """
    df = _exec(conn, """
        SELECT
            ALERT_TYPE,
            DETECTION_SOURCE,
            COUNT(*) AS CNT
        FROM DBADMIN.ALERTS
        WHERE DETECTED_AT   >= ADD_DAYS(NOW(), -1)
          AND ALERTED        = 1
          AND SUPPRESSED_BY IS NULL
        GROUP BY ALERT_TYPE, DETECTION_SOURCE
        ORDER BY CNT DESC
    """)

    if not df.empty:
        df["count"] = df["cnt"].astype(int)
        df = df.drop(columns=["cnt"])

    return df


def _query_alertas_por_hora(conn) -> pd.DataFrame:
    """
    Mapa de calor: alertas agrupadas por hora del día y por día.
    Rango: últimos 7 días.
    Permite identificar patrones temporales de ataques.
    """
    df = _exec(conn, """
        SELECT
            HOUR(DETECTED_AT)     AS HORA,
            TO_DATE(DETECTED_AT)  AS DIA,
            COUNT(*)              AS CNT
        FROM DBADMIN.ALERTS
        WHERE DETECTED_AT   >= ADD_DAYS(NOW(), -7)
          AND ALERTED        = 1
          AND SUPPRESSED_BY IS NULL
        GROUP BY HOUR(DETECTED_AT), TO_DATE(DETECTED_AT)
        ORDER BY DIA ASC, HORA ASC
    """)

    if not df.empty:
        df["hora"]  = df["hora"].astype(int)
        df["count"] = df["cnt"].astype(int)
        df["dia"]   = pd.to_datetime(df["dia"]).dt.date
        df = df.drop(columns=["cnt"])

    return df


def _query_path_scan_activity(conn) -> pd.DataFrame:
    """
    Rutas más solicitadas con HTTP 404, agrupadas por path.
    Indica qué rutas están siendo escaneadas por IPs sospechosas.

    IMPORTANTE: detecta el nombre real de la columna de path en HANA
    mediante _get_path_column() para manejar el typo histórico
    ('heathers_request_path' vs 'headers_request_path').
    """
    path_col = _get_path_column(conn)

    df = _exec(conn, f"""
        SELECT
            {path_col}                      AS REQUEST_PATH,
            COUNT(*)                         AS CNT,
            COUNT(DISTINCT CLIENT_IP)        AS UNIQUE_IPS
        FROM DBADMIN.RAW_LOGS_SISTEMA
        WHERE HTTP_STATUS      = '404'
          AND {path_col}       IS NOT NULL
          AND EVENT_TIMESTAMP  >= ADD_DAYS(NOW(), -1)
        GROUP BY {path_col}
        HAVING COUNT(*) >= 2
        ORDER BY CNT DESC
        LIMIT 20
    """)

    if not df.empty:
        df["count"]       = df["cnt"].astype(int)
        df["unique_ips"]  = df["unique_ips"].astype(int)
        df = df.drop(columns=["cnt"])

    return df


def _query_llm_anomalias(conn):
    """
    Retorna dos DataFrames independientes para la gráfica overlay LLM.
    SIN JOIN entre ellos — se alinean por eje temporal en la figura.
 
    df_actividad: RAW_LOGS_LLM agrupado por ventana de 30 min (EVENT_TIMESTAMP)
    df_alertas:   ALERTS filtrado por ALERT_TYPE = 'ml_llm_anomaly' (DETECTED_AT)
 
    Returns:
        tuple(df_llm_actividad, df_llm_alertas)
    """
    import pandas as pd
 
    # Actividad LLM agrupada por ventana de 30 min
    df_actividad = _exec(conn, """
        SELECT
            ADD_SECONDS(
                TO_TIMESTAMP('1970-01-01'),
                FLOOR(
                    SECONDS_BETWEEN(TO_TIMESTAMP('1970-01-01'), EVENT_TIMESTAMP)
                    / 1800
                ) * 1800
            )                                        AS WINDOW_START,
            ROUND(AVG(LLM_COST_USD), 6)              AS AVG_COST_USD,
            ROUND(AVG(LLM_RESPONSE_TIME), 2)      AS AVG_RESPONSE_TIME_MS,
            COUNT(*)                                 AS LOG_COUNT
        FROM DBADMIN.RAW_LOGS_LLM
        WHERE EVENT_TIMESTAMP >= ADD_DAYS(NOW(), -2)
        GROUP BY
            FLOOR(
                SECONDS_BETWEEN(TO_TIMESTAMP('1970-01-01'), EVENT_TIMESTAMP)
                / 1800
            )
        ORDER BY WINDOW_START ASC
    """)
 
    # Alertas ML LLM — usa DETECTED_AT, no EVENT_TIMESTAMP
    df_alertas = _exec(conn, """
        SELECT
            DETECTED_AT,
            SEVERITY,
            DETAILS
        FROM DBADMIN.ALERTS
        WHERE ALERT_TYPE    = 'ml_llm_anomaly'
          AND ALERTED        = 1
          AND DETECTED_AT   >= ADD_DAYS(NOW(), -2)
        ORDER BY DETECTED_AT ASC
    """)
 
    if not df_actividad.empty:
        df_actividad["window_start"]         = pd.to_datetime(df_actividad["window_start"], utc=True)
        df_actividad["avg_cost_usd"]         = df_actividad["avg_cost_usd"].astype(float).fillna(0.0)
        df_actividad["avg_response_time_ms"] = df_actividad["avg_response_time_ms"].astype(float).fillna(0.0)
        df_actividad["log_count"]            = df_actividad["log_count"].astype(int)
 
    if not df_alertas.empty:
        df_alertas["detected_at"] = pd.to_datetime(df_alertas["detected_at"], utc=True)
 
    return df_actividad, df_alertas


def _query_sistema_anomalias(conn):
    """
    Retorna dos DataFrames independientes para la gráfica overlay de Sistema.
    SIN JOIN entre ellos — se alinean por eje temporal en la figura.
 
    df_actividad: RAW_LOGS_SISTEMA agrupado por ventana de 30 min (EVENT_TIMESTAMP)
    df_alertas:   ALERTS filtrado por ALERT_TYPE = 'ml_sistema_anomaly' (DETECTED_AT)
 
    Nota: HTTP_STATUS es NVARCHAR — se usa LIKE '4%' / '5%' en lugar de cast numérico.
 
    Returns:
        tuple(df_sistema_actividad, df_sistema_alertas)
    """
    import pandas as pd
 
    # Actividad sistema agrupada por ventana de 30 min
    df_actividad = _exec(conn, """
        SELECT
            ADD_SECONDS(
                TO_TIMESTAMP('1970-01-01'),
                FLOOR(
                    SECONDS_BETWEEN(TO_TIMESTAMP('1970-01-01'), EVENT_TIMESTAMP)
                    / 1800
                ) * 1800
            )                                                     AS WINDOW_START,
            COUNT(*)                                              AS LOG_COUNT,
            ROUND(
                CAST(
                    SUM(CASE WHEN HTTP_STATUS LIKE '4%' THEN 1
                             WHEN HTTP_STATUS LIKE '5%' THEN 1
                             ELSE 0 END)
                AS DOUBLE) / NULLIF(COUNT(*), 0), 4)             AS ERROR_RATE,
            ROUND(
                CAST(
                    SUM(CASE WHEN HTTP_STATUS LIKE '4%' THEN 1 ELSE 0 END)
                AS DOUBLE) / NULLIF(COUNT(*), 0), 4)             AS RATIO_4XX
        FROM DBADMIN.RAW_LOGS_SISTEMA
        WHERE EVENT_TIMESTAMP >= ADD_DAYS(NOW(), -2)
        GROUP BY
            FLOOR(
                SECONDS_BETWEEN(TO_TIMESTAMP('1970-01-01'), EVENT_TIMESTAMP)
                / 1800
            )
        ORDER BY WINDOW_START ASC
    """)
 
    # Alertas ML sistema — usa DETECTED_AT, no EVENT_TIMESTAMP
    df_alertas = _exec(conn, """
        SELECT
            DETECTED_AT,
            SEVERITY,
            DETAILS
        FROM DBADMIN.ALERTS
        WHERE ALERT_TYPE    = 'ml_sistema_anomaly'
          AND ALERTED        = 1
          AND DETECTED_AT   >= ADD_DAYS(NOW(), -2)
        ORDER BY DETECTED_AT ASC
    """)
 
    if not df_actividad.empty:
        df_actividad["window_start"] = pd.to_datetime(df_actividad["window_start"], utc=True)
        df_actividad["log_count"]    = df_actividad["log_count"].astype(int)
        df_actividad["error_rate"]   = df_actividad["error_rate"].astype(float).fillna(0.0)
        df_actividad["ratio_4xx"]    = df_actividad["ratio_4xx"].astype(float).fillna(0.0)
 
    if not df_alertas.empty:
        df_alertas["detected_at"] = pd.to_datetime(df_alertas["detected_at"], utc=True)
 
    return df_actividad, df_alertas
 

# ── Ejecución directa para pruebas ───────────────────────────────────────────

if __name__ == "__main__":
    import sys
    sys.path.insert(0, ".")
    from dotenv import load_dotenv
    load_dotenv()

    print("Conectando a HANA...")
    conn = get_connection()
    print("✅ Conexión establecida\n")

    print("── KPIs ──")
    kpis = get_kpis(conn)
    for k, v in kpis.items():
        print(f"  {k}: {v}")

    print("\n── Activity timeline ──")
    df = get_activity_timeline(conn)
    print(f"  Shape: {df.shape}")
    print(df.head(3).to_string())

    print("\n── Critical alerts ──")
    df2 = get_critical_alerts(conn)
    print(f"  Shape: {df2.shape}")
    print(df2.head(3).to_string())

    print("\n── Agent context (primeros 600 chars) ──")
    ctx = get_agent_context(conn)
    print(ctx[:600])

    print("\n── Chart data — brute_force_por_ip ──")
    df3 = get_chart_data(conn, "brute_force_por_ip")
    print(f"  Shape: {df3.shape}")
    print(df3.head(5).to_string())

    print("\n── Chart data — llm_anomalias (tuple) ──")
    df_act, df_ale = get_chart_data(conn, "llm_anomalias")
    print(f"  df_actividad shape: {df_act.shape}")
    print(f"  df_alertas shape:   {df_ale.shape}")