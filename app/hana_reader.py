"""
app/hana_reader.py
==================
Módulo de extracción desde SAP HANA para el modelo de detección ML.

Responsabilidad única: abrir conexión, ejecutar queries con los nombres
de columna REALES de HANA (distintos a los nombres del raw de la API),
y retornar DataFrames con tipos correctos listos para feature_eng.py.

--- NOMBRES DE COLUMNA: API raw → HANA ---

RAW_LOGS_SISTEMA:
    sap_function_log_type     → LOG_TYPE        (NVARCHAR 50)
    http_status_code          → HTTP_STATUS     (NVARCHAR 10  ← string, hay que castear)
    client_ip                 → CLIENT_IP       (NVARCHAR 50)
    heathers_request_path     → REQUEST_PATH    (NVARCHAR 500)
    sap_function_application  → APPLICATION     (NVARCHAR 100)
    region_name               → REGION_NAME     (NVARCHAR 100)
    sap_function_message      → MESSAGE         (NVARCHAR 2000)

RAW_LOGS_LLM:
    llm_status                → LLM_STATUS      (NVARCHAR 50)
    llm_model_id              → LLM_MODEL_ID    (NVARCHAR 100)
    llm_provider              → LLM_PROVIDER    (NVARCHAR 100)
    llm_cost_usd              → LLM_COST_USD    (DOUBLE)
    llm_response_time_ms      → LLM_RESPONSE_TIME (DOUBLE, unidad: ms)
    llm_total_tokens          → LLM_TOTAL_TOKENS  (INTEGER)
    llm_temperature           → LLM_TEMPERATURE   (DOUBLE)
    llm_error_message         → LLM_ERROR_MESSAGE (NVARCHAR 1000)
    llm_prompt_category       → LLM_PROMPT_CATEGORY (NVARCHAR 100)

--- USO ---

    from app.hana_reader import (
        leer_sistema_ventana_actual,
        leer_llm_ventana_actual,
        leer_sistema_historico,
        leer_llm_historico,
        contar_ventanas_acumuladas,
    )

    conn = abrir_conexion_hana()
    try:
        df_sis = leer_sistema_ventana_actual(conn)
        df_llm = leer_llm_ventana_actual(conn)
        n_ventanas = contar_ventanas_acumuladas(conn)
    finally:
        if conn:
            conn.close()

--- PRUEBA STANDALONE ---

    python -m app.hana_reader
"""

import logging
import os
from datetime import datetime, timezone

import pandas as pd

logger = logging.getLogger("hana_reader")

# ---------------------------------------------------------------------------
# Constantes
# ---------------------------------------------------------------------------

# Ventana activa: últimos 30 minutos
VENTANA_ACTUAL_SEGUNDOS = 1800

# Historia rolling para entrenamiento: últimas 24 horas
HISTORIA_DEFAULT_HORAS = 24

# Mínimo de ventanas acumuladas para pasar de cold-start a modo histórico
MIN_VENTANAS_MODO_HISTORICO = 20

# Columnas que retorna cada función — contrato explícito
COLS_SISTEMA = [
    "LOG_ID", "EVENT_TIMESTAMP", "LOG_TYPE", "HTTP_STATUS",
    "CLIENT_IP", "REQUEST_PATH", "APPLICATION", "REGION_NAME", "MESSAGE",
]

COLS_LLM = [
    "LOG_ID", "EVENT_TIMESTAMP", "LOG_TYPE", "LLM_STATUS",
    "LLM_MODEL_ID", "LLM_PROVIDER", "LLM_COST_USD",
    "LLM_RESPONSE_TIME", "LLM_TOTAL_TOKENS", "LLM_TEMPERATURE",
    "LLM_ERROR_MESSAGE", "LLM_PROMPT_CATEGORY",
]


# ---------------------------------------------------------------------------
# Conexión
# ---------------------------------------------------------------------------

def abrir_conexion_hana():
    """
    Abre una conexión a SAP HANA usando las credenciales de config.py.
    Retorna la conexión si tiene éxito, None si falla (sin lanzar excepción).

    El llamador es responsable de cerrar la conexión con conn.close().
    """
    try:
        try:
            from app.config import HANA_HOST, HANA_PORT, HANA_USER, HANA_PASSWORD
        except ImportError:
            from config import HANA_HOST, HANA_PORT, HANA_USER, HANA_PASSWORD
        import hdbcli.dbapi as hdb
        conn = hdb.connect(
            address=HANA_HOST,
            port=int(HANA_PORT),
            user=HANA_USER,
            password=HANA_PASSWORD,
        )
        logger.debug("[HANA] Conexión abierta correctamente")
        return conn
    except ImportError as e:
        logger.error(f"[HANA] hdbcli no disponible: {e}")
        return None
    except Exception as e:
        logger.error(f"[HANA] No se pudo conectar: {e}")
        return None


# ---------------------------------------------------------------------------
# Funciones de extracción — ventana actual (scoring)
# ---------------------------------------------------------------------------

def leer_sistema_ventana_actual(conn) -> pd.DataFrame:
    """
    Lee los logs de Sistema de los últimos 30 minutos desde HANA.

    Retorna DataFrame con columnas: LOG_ID, EVENT_TIMESTAMP, LOG_TYPE,
    HTTP_STATUS, CLIENT_IP, REQUEST_PATH, APPLICATION, REGION_NAME, MESSAGE.

    HTTP_STATUS viene como string desde HANA (NVARCHAR) — el caller
    (feature_eng.py) es responsable de convertirlo a int cuando sea necesario.

    Retorna DataFrame vacío (no lanza excepción) si hay error.
    """
    sql = """
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
        WHERE EVENT_TIMESTAMP >= ADD_SECONDS(NOW(), :segundos_atras)
        ORDER BY EVENT_TIMESTAMP DESC
    """
    return _ejecutar_query(
        conn=conn,
        sql=sql,
        params={"segundos_atras": -VENTANA_ACTUAL_SEGUNDOS},
        columnas=COLS_SISTEMA,
        nombre="sistema_ventana_actual",
    )


def leer_llm_ventana_actual(conn) -> pd.DataFrame:
    """
    Lee los logs de LLM de los últimos 30 minutos desde HANA.

    Retorna DataFrame con columnas: LOG_ID, EVENT_TIMESTAMP, LOG_TYPE,
    LLM_STATUS, LLM_MODEL_ID, LLM_PROVIDER, LLM_COST_USD, LLM_RESPONSE_TIME,
    LLM_TOTAL_TOKENS, LLM_TEMPERATURE, LLM_ERROR_MESSAGE, LLM_PROMPT_CATEGORY.

    LLM_RESPONSE_TIME está en milisegundos (confirmado: range 200–34,999 ms).
    LLM_TOTAL_TOKENS es INTEGER en HANA.

    Retorna DataFrame vacío (no lanza excepción) si hay error.
    """
    sql = """
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
        WHERE EVENT_TIMESTAMP >= ADD_SECONDS(NOW(), :segundos_atras)
        ORDER BY EVENT_TIMESTAMP DESC
    """
    return _ejecutar_query(
        conn=conn,
        sql=sql,
        params={"segundos_atras": -VENTANA_ACTUAL_SEGUNDOS},
        columnas=COLS_LLM,
        nombre="llm_ventana_actual",
    )


# ---------------------------------------------------------------------------
# Funciones de extracción — historia rolling (entrenamiento)
# ---------------------------------------------------------------------------

def leer_sistema_historico(conn, horas: int = HISTORIA_DEFAULT_HORAS) -> pd.DataFrame:
    """
    Lee los logs de Sistema de las últimas `horas` horas, excluyendo
    la ventana actual (últimos 30 min) para no contaminar el training
    con datos que también se van a scorear.

    Usado para entrenar IF_sistema en modo histórico.
    Con horas=24 devuelve ~800k–1M registros — es el training set completo.

    Retorna DataFrame vacío (no lanza excepción) si hay error.
    """
    segundos_historia = horas * 3600
    sql = """
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
        WHERE EVENT_TIMESTAMP >= ADD_SECONDS(NOW(), :segundos_atras)
          AND EVENT_TIMESTAMP <  ADD_SECONDS(NOW(), :excluir_ventana_actual)
        ORDER BY EVENT_TIMESTAMP ASC
    """
    return _ejecutar_query(
        conn=conn,
        sql=sql,
        params={
            "segundos_atras": -segundos_historia,
            "excluir_ventana_actual": -VENTANA_ACTUAL_SEGUNDOS,
        },
        columnas=COLS_SISTEMA,
        nombre=f"sistema_historico_{horas}h",
    )


def leer_llm_historico(conn, horas: int = HISTORIA_DEFAULT_HORAS) -> pd.DataFrame:
    """
    Lee los logs de LLM de las últimas `horas` horas, excluyendo
    la ventana actual.

    Usado para entrenar IF_llm en modo histórico.

    Retorna DataFrame vacío (no lanza excepción) si hay error.
    """
    segundos_historia = horas * 3600
    sql = """
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
        WHERE EVENT_TIMESTAMP >= ADD_SECONDS(NOW(), :segundos_atras)
          AND EVENT_TIMESTAMP <  ADD_SECONDS(NOW(), :excluir_ventana_actual)
        ORDER BY EVENT_TIMESTAMP ASC
    """
    return _ejecutar_query(
        conn=conn,
        sql=sql,
        params={
            "segundos_atras": -segundos_historia,
            "excluir_ventana_actual": -VENTANA_ACTUAL_SEGUNDOS,
        },
        columnas=COLS_LLM,
        nombre=f"llm_historico_{horas}h",
    )


# ---------------------------------------------------------------------------
# Estado del sistema — cold-start vs. modo histórico
# ---------------------------------------------------------------------------

def contar_ventanas_acumuladas(conn) -> int:
    """
    Estima cuántas ventanas de 30 minutos se han acumulado en HANA.

    Cuenta ventanas distintas usando FLOOR(minuto / 30) como proxy.
    Si el resultado >= MIN_VENTANAS_MODO_HISTORICO (20), el modelo
    debe operar en modo histórico en lugar de cold-start.

    Retorna 0 si hay error (fuerza cold-start como comportamiento seguro).
    """
    sql = """
        SELECT COUNT(DISTINCT
            TO_VARCHAR(EVENT_TIMESTAMP, 'YYYY-MM-DD HH24') ||
            CASE WHEN MINUTE(EVENT_TIMESTAMP) < 30 THEN ':00' ELSE ':30' END
        ) AS n_ventanas
        FROM DBADMIN.RAW_LOGS_SISTEMA
    """
    try:
        cursor = conn.cursor()
        cursor.execute(sql)
        row = cursor.fetchone()
        cursor.close()
        n = int(row[0]) if row and row[0] is not None else 0
        logger.info(f"[HANA] Ventanas acumuladas estimadas: {n}")
        return n
    except Exception as e:
        logger.warning(f"[HANA] No se pudo contar ventanas: {e} — asumiendo cold-start")
        return 0


def en_modo_historico(conn) -> bool:
    """
    Retorna True si hay suficientes ventanas acumuladas para modo histórico.
    Retorna False (cold-start) si hay error o datos insuficientes.
    """
    n = contar_ventanas_acumuladas(conn)
    modo = n >= MIN_VENTANAS_MODO_HISTORICO
    logger.info(
        f"[HANA] Modo: {'HISTÓRICO' if modo else 'COLD-START'} "
        f"({n}/{MIN_VENTANAS_MODO_HISTORICO} ventanas mínimas)"
    )
    return modo


# ---------------------------------------------------------------------------
# Función interna — ejecución de queries
# ---------------------------------------------------------------------------

def _ejecutar_query(
    conn,
    sql: str,
    params: dict,
    columnas: list,
    nombre: str,
) -> pd.DataFrame:
    """
    Ejecuta un query parametrizado en HANA y retorna un DataFrame.

    Convierte los parámetros del dict a lista posicional reemplazando
    los placeholders con nombre (:param) por '?' para hdbcli.

    Retorna DataFrame vacío con las columnas correctas si hay cualquier error.
    """
    df_vacio = pd.DataFrame(columns=columnas)

    if conn is None:
        logger.warning(f"[HANA] conn=None para query '{nombre}' — retornando vacío")
        return df_vacio

    try:
        # hdbcli usa ? como placeholder posicional, no :nombre
        # Convertir el dict de params a lista en el orden en que aparecen en el SQL
        sql_posicional, valores = _convertir_params(sql, params)

        cursor = conn.cursor()
        cursor.execute(sql_posicional, valores)
        rows = cursor.fetchall()
        cursor.close()

        if not rows:
            logger.info(f"[HANA] Query '{nombre}': 0 registros")
            return df_vacio

        df = pd.DataFrame(rows, columns=columnas)

        # Convertir EVENT_TIMESTAMP a datetime si viene como string
        if "EVENT_TIMESTAMP" in df.columns:
            df["EVENT_TIMESTAMP"] = pd.to_datetime(df["EVENT_TIMESTAMP"], utc=True, errors="coerce")

        logger.info(f"[HANA] Query '{nombre}': {len(df):,} registros")
        return df

    except Exception as e:
        logger.error(f"[HANA] Error en query '{nombre}': {e}")
        return df_vacio


def _convertir_params(sql: str, params: dict) -> tuple:
    """
    Convierte placeholders con nombre (:param) a '?' posicionales para hdbcli.

    Ejemplo:
        sql    = "WHERE x >= :a AND x < :b"
        params = {"a": -1800, "b": -30}
        →  sql_out = "WHERE x >= ? AND x < ?"
        →  valores = [-1800, -30]

    El orden de los valores respeta el orden de aparición en el SQL.
    """
    import re
    orden = re.findall(r":(\w+)", sql)
    sql_posicional = re.sub(r":\w+", "?", sql)
    valores = [params[k] for k in orden]
    return sql_posicional, valores


# ---------------------------------------------------------------------------
# Script de prueba standalone
# ---------------------------------------------------------------------------

def _probar_hana_reader():
    """
    Prueba completa del módulo contra HANA real.

    Ejecuta:
        1. Conexión a HANA
        2. leer_sistema_ventana_actual()  — debe tener columnas y tipos correctos
        3. leer_llm_ventana_actual()
        4. leer_sistema_historico()       — debe tener más registros que la ventana
        5. leer_llm_historico()
        6. contar_ventanas_acumuladas()   — debe ser >= 20 con datos acumulados
        7. en_modo_historico()

    Uso:
        python -m app.hana_reader
    """
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
        datefmt="%H:%M:%S",
    )

    print("\n" + "=" * 60)
    print("PRUEBA — app/hana_reader.py")
    print("=" * 60)

    # --- Paso 1: Conexión ---
    print("\n[1/7] Abriendo conexión HANA...")
    conn = abrir_conexion_hana()
    if conn is None:
        print("❌ No se pudo conectar a HANA. Verificar variables de entorno.")
        print("   Requeridas: HANA_HOST, HANA_PORT, HANA_USER, HANA_PASSWORD")
        return

    print("✅ Conexión exitosa")

    try:
        # --- Paso 2: Sistema ventana actual ---
        print("\n[2/7] Leyendo sistema — ventana actual (últimos 30 min)...")
        df_sis = leer_sistema_ventana_actual(conn)
        _reportar_df(df_sis, "sistema_ventana_actual", COLS_SISTEMA)

        # --- Paso 3: LLM ventana actual ---
        print("\n[3/7] Leyendo LLM — ventana actual (últimos 30 min)...")
        df_llm = leer_llm_ventana_actual(conn)
        _reportar_df(df_llm, "llm_ventana_actual", COLS_LLM)

        # --- Paso 4: Sistema histórico ---
        print("\n[4/7] Leyendo sistema — histórico 24h (excluyendo ventana actual)...")
        df_sis_hist = leer_sistema_historico(conn, horas=24)
        _reportar_df(df_sis_hist, "sistema_historico_24h", COLS_SISTEMA)

        # Sanidad: histórico debe tener más registros que la ventana actual
        if len(df_sis_hist) > len(df_sis):
            print(f"   ✅ Sanidad OK: histórico ({len(df_sis_hist):,}) > ventana ({len(df_sis):,})")
        else:
            print(f"   ⚠️  Sanidad: histórico ({len(df_sis_hist):,}) ≤ ventana ({len(df_sis):,}) — puede ser normal si el pipeline acaba de arrancar")

        # --- Paso 5: LLM histórico ---
        print("\n[5/7] Leyendo LLM — histórico 24h...")
        df_llm_hist = leer_llm_historico(conn, horas=24)
        _reportar_df(df_llm_hist, "llm_historico_24h", COLS_LLM)

        # --- Paso 6: Contar ventanas ---
        print("\n[6/7] Contando ventanas acumuladas...")
        n_ventanas = contar_ventanas_acumuladas(conn)
        print(f"   Ventanas: {n_ventanas} (mínimo para modo histórico: {MIN_VENTANAS_MODO_HISTORICO})")

        # --- Paso 7: Modo operativo ---
        print("\n[7/7] Determinando modo operativo...")
        modo = en_modo_historico(conn)
        print(f"   Modo: {'✅ HISTÓRICO' if modo else '⚠️  COLD-START'}")
        if not modo:
            print(f"   (Faltan {MIN_VENTANAS_MODO_HISTORICO - n_ventanas} ventanas para modo histórico)")

        # --- Resumen de tipos ---
        print("\n--- Tipos de columnas (sistema) ---")
        if not df_sis.empty:
            print(df_sis.dtypes.to_string())
            print(f"\n   HTTP_STATUS sample: {df_sis['HTTP_STATUS'].dropna().head(3).tolist()}")
            print(f"   LOG_TYPE distribución:\n{df_sis['LOG_TYPE'].value_counts().head(7).to_string()}")

        print("\n--- Tipos de columnas (LLM) ---")
        if not df_llm.empty:
            print(df_llm.dtypes.to_string())
            print(f"\n   LLM_STATUS distribución:\n{df_llm['LLM_STATUS'].value_counts().to_string()}")
            print(f"   LLM_RESPONSE_TIME range: {df_llm['LLM_RESPONSE_TIME'].min():.1f} – {df_llm['LLM_RESPONSE_TIME'].max():.1f} ms")
            print(f"   LLM_COST_USD range: {df_llm['LLM_COST_USD'].min():.6f} – {df_llm['LLM_COST_USD'].max():.6f}")

    finally:
        conn.close()
        print("\n[HANA] Conexión cerrada")

    print("\n" + "=" * 60)
    print("✅ Prueba completada — hana_reader.py está listo para feature_eng.py")
    print("=" * 60 + "\n")


def _reportar_df(df: pd.DataFrame, nombre: str, cols_esperadas: list):
    """Imprime un resumen de diagnóstico de un DataFrame."""
    print(f"   Registros: {len(df):,}")

    if df.empty:
        print(f"   ⚠️  DataFrame vacío — puede ser normal si no hay datos en ventana")
        return

    # Verificar columnas esperadas
    cols_faltantes = [c for c in cols_esperadas if c not in df.columns]
    if cols_faltantes:
        print(f"   ❌ Columnas faltantes: {cols_faltantes}")
    else:
        print(f"   ✅ Todas las columnas presentes ({len(cols_esperadas)})")

    # Rango temporal
    if "EVENT_TIMESTAMP" in df.columns and not df["EVENT_TIMESTAMP"].isna().all():
        ts_min = df["EVENT_TIMESTAMP"].min()
        ts_max = df["EVENT_TIMESTAMP"].max()
        print(f"   Rango temporal: {ts_min} → {ts_max}")


if __name__ == "__main__":
    _probar_hana_reader()
