# =============================================================================
# app/hana_client.py
# =============================================================================
# Propósito: gestionar toda la interacción con SAP HANA Cloud.
#            Conexión, creación de tablas, e inserción de datos.
#
# Este módulo es la única parte del proyecto que "sabe" cómo hablar con HANA.
# Todos los demás módulos que necesiten persistir datos lo hacen a través
# de las funciones aquí definidas — nunca directamente.
#
# Por qué separar esto en su propio módulo:
#   - Si las credenciales o el schema de HANA cambian, solo tocamos este archivo
#   - El resto del pipeline no necesita saber nada sobre SQL ni sobre hdbcli
#   - Facilita el testing — podemos mockear este módulo fácilmente
#
# Tablas que gestiona:
#   RAW_LOGS_SISTEMA  → logs de tipo INFO, WARNING, ERROR, DEBUG, AUDIT, PERF, SECURITY
#   RAW_LOGS_LLM      → logs de tipo LLM_REQUEST, LLM_ERROR, LLM_TIMEOUT
#
# Por qué dos tablas separadas:
#   Las columnas activas son completamente distintas entre tipos.
#   Una sola tabla tendría ~50% de columnas nulas en cada fila,
#   lo que desperdicia espacio y complica las queries del modelo de ML.
#
# Cómo usar este módulo desde otros scripts:
#   from hana_client import get_connection, create_tables, insert_logs
# =============================================================================

import os
import sys
import logging
import pandas as pd
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

# Importación del driver de SAP HANA
# hdbcli es el driver oficial de SAP para conectarse a HANA desde Python
try:
    from hdbcli import dbapi
except ImportError:
    logger.error("hdbcli no está instalado. Ejecuta: pip install hdbcli")
    sys.exit(1)

sys.path.append(os.path.dirname(os.path.abspath(__file__)))
from config import HANA_HOST, HANA_PORT, HANA_USER, HANA_PASSWORD


# =============================================================================
# CONSTANTES — definición de tablas
# =============================================================================

# Nombre de las tablas en HANA
TABLA_SISTEMA = "RAW_LOGS_SISTEMA"
TABLA_LLM     = "RAW_LOGS_LLM"

# Prefijo que identifica logs LLM
PREFIJO_LLM = "LLM"

# =============================================================================
# FUNCIÓN: get_connection()
# =============================================================================

def get_connection():
    """
    Crea y devuelve una conexión activa a SAP HANA Cloud.

    Parámetros de conexión:
        host:       dirección del servidor HANA (de .env)
        port:       puerto de conexión (443 para HANA Cloud)
        user:       usuario DBADMIN
        password:   contraseña definida al crear la instancia
        encrypt:    True — HANA Cloud requiere conexión cifrada siempre
        sslValidateCertificate: True — valida el certificado SSL del servidor

    Returns:
        Objeto de conexión hdbcli activo.

    Raises:
        SystemExit si faltan credenciales o si la conexión falla.
    """
    # Verificar que tenemos todas las credenciales antes de intentar conectar
    credenciales = {
        "HANA_HOST":     HANA_HOST,
        "HANA_PORT":     HANA_PORT,
        "HANA_USER":     HANA_USER,
        "HANA_PASSWORD": HANA_PASSWORD,
    }
    faltantes = [k for k, v in credenciales.items() if not v]
    if faltantes:
        raise EnvironmentError(
            f"Faltan credenciales de HANA en .env: {faltantes}"
        )

    try:
        conn = dbapi.connect(
            address=HANA_HOST,
            port=int(HANA_PORT),     # asegurar que es entero
            user=HANA_USER,
            password=HANA_PASSWORD,
            encrypt=True,            # requerido para HANA Cloud
            sslValidateCertificate=True
        )
        logger.info(f"✓ Conexión HANA establecida ({HANA_HOST}:{HANA_PORT})")
        return conn

    except dbapi.Error as e:
        logger.error(f"❌ Error conectando a HANA: {e}")
        raise


# =============================================================================
# FUNCIÓN: create_tables()
# =============================================================================

def create_tables():
    """
    Crea las tablas en HANA si no existen todavía.

    Usa CREATE TABLE IF NOT EXISTS para ser idempotente —
    puedes llamar esta función N veces sin problema.

    Las columnas reflejan exactamente el schema real de la API
    documentado en data/SCHEMA.md.

    Columnas comunes a ambas tablas:
        _id, @timestamp, @event_time_requested, sap_function_log_type,
        sap_function_application, sap_function_message, sap_app_env,
        region_name, region_code, macro_region, ingested_at

    Columnas exclusivas de Sistema:
        service_id, http_status_code, client_ip,
        headers_http_request_method, request_path

    Columnas exclusivas de LLM:
        llm_model_id, llm_provider, llm_status, llm_error_message,
        llm_prompt_category, llm_total_tokens, llm_cost_usd,
        llm_response_time_ms, llm_temperature, llm_finish_reason
    """
    conn = get_connection()
    cursor = conn.cursor()

    # ------------------------------------------------------------------
    # Tabla de logs de Sistema
    # ------------------------------------------------------------------
    # Nota sobre tipos HANA:
    #   NVARCHAR(n) → strings Unicode (equivalente a VARCHAR en otros DBs)
    #   TIMESTAMP   → fecha y hora con precisión de microsegundos
    #   INTEGER     → entero 32-bit
    #   DOUBLE      → número decimal de doble precisión
    # ------------------------------------------------------------------

    sql_sistema = f"""
    CREATE TABLE IF NOT EXISTS {TABLA_SISTEMA} (
        id              INTEGER GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
        log_id          NVARCHAR(100),
        event_timestamp TIMESTAMP,
        ingested_at     TIMESTAMP,
        log_type        NVARCHAR(50),
        application     NVARCHAR(100),
        message         NVARCHAR(2000),
        environment     NVARCHAR(50),
        region_name     NVARCHAR(100),
        region_code     NVARCHAR(10),
        macro_region    NVARCHAR(50),
        service_id      NVARCHAR(200),
        http_status     NVARCHAR(10),
        client_ip       NVARCHAR(50),
        request_method  NVARCHAR(20),
        request_path    NVARCHAR(500)
    )
    """

    # ------------------------------------------------------------------
    # Tabla de logs de LLM
    # ------------------------------------------------------------------

    sql_llm = f"""
    CREATE TABLE IF NOT EXISTS {TABLA_LLM} (
        id                  INTEGER GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
        log_id              NVARCHAR(100),
        event_timestamp     TIMESTAMP,
        ingested_at         TIMESTAMP,
        log_type            NVARCHAR(50),
        application         NVARCHAR(100),
        message             NVARCHAR(2000),
        environment         NVARCHAR(50),
        region_name         NVARCHAR(100),
        region_code         NVARCHAR(10),
        macro_region        NVARCHAR(50),
        llm_model_id        NVARCHAR(100),
        llm_provider        NVARCHAR(100),
        llm_status          NVARCHAR(50),
        llm_error_message   NVARCHAR(1000),
        llm_prompt_category NVARCHAR(100),
        llm_total_tokens    INTEGER,
        llm_cost_usd        DOUBLE,
        llm_response_time   DOUBLE,
        llm_temperature     DOUBLE,
        llm_finish_reason   NVARCHAR(50)
    )
    """

    try:
        cursor.execute(sql_sistema)
        logger.info(f"✓ Tabla {TABLA_SISTEMA} lista")

        cursor.execute(sql_llm)
        logger.info(f"✓ Tabla {TABLA_LLM} lista")

        conn.commit()
        logger.info("✓ Tablas verificadas/creadas en HANA")

    except dbapi.Error as e:
        logger.error(f"❌ Error creando tablas: {e}")
        conn.rollback()
        raise

    finally:
        cursor.close()
        conn.close()


# =============================================================================
# FUNCIÓN: insert_logs()
# =============================================================================

def insert_logs(df: pd.DataFrame) -> dict:
    """
    Inserta los registros del DataFrame en las tablas correspondientes de HANA.

    Separa automáticamente los logs de Sistema y LLM según
    sap_function_log_type, y los inserta en su tabla respectiva.

    Usa INSERT batch para eficiencia — en lugar de hacer un INSERT
    por cada fila (hasta 5,729 llamadas SQL), agrupa todas las filas
    de cada tipo en una sola operación. Esto es mucho más rápido.

    Args:
        df: DataFrame completo con logs de la ventana actual.
            Debe tener la columna sap_function_log_type.

    Returns:
        dict con conteo de registros insertados por tabla:
        {"sistema": N, "llm": M}
    """
    if df.empty:
        logger.warning("DataFrame vacío — nada que insertar en HANA")
        return {"sistema": 0, "llm": 0}

    conn = get_connection()
    cursor = conn.cursor()

    # Timestamp de ingesta — el momento en que insertamos en HANA
    # Distinto del timestamp del evento — útil para auditoría y debugging
    ahora = datetime.now(timezone.utc)

    conteos = {"sistema": 0, "llm": 0}

    try:
        # Separar por tipo de log
        mask_llm    = df["sap_function_log_type"].str.startswith(PREFIJO_LLM, na=False)
        df_sistema  = df[~mask_llm]
        df_llm      = df[mask_llm]

        # --------------------------------------------------------------
        # Insertar logs de Sistema
        # --------------------------------------------------------------
        if not df_sistema.empty:
            filas_sistema = []

            for _, row in df_sistema.iterrows():
                fila = (
                    _safe_str(row, "_id"),
                    _safe_ts(row,  "@timestamp"),
                    ahora,
                    _safe_str(row, "sap_function_log_type"),
                    _safe_str(row, "sap_function_application"),
                    _safe_str(row, "sap_function_message", max_len=2000),
                    _safe_str(row, "sap_app_env"),
                    _safe_str(row, "region_name"),
                    _safe_str(row, "region_code"),
                    _safe_str(row, "macro_region"),
                    _safe_str(row, "service_id"),
                    _safe_str(row, "http_status_code"),
                    _safe_str(row, "client_ip"),
                    _safe_str(row, "headers_http_request_method"),
                    _safe_str(row, "heathers_request_path", max_len=500),
                )
                filas_sistema.append(fila)

            sql_insert_sistema = f"""
                INSERT INTO {TABLA_SISTEMA} (
                    log_id, event_timestamp, ingested_at,
                    log_type, application, message, environment,
                    region_name, region_code, macro_region,
                    service_id, http_status, client_ip,
                    request_method, request_path
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """
            # executemany inserta todas las filas en una sola operación
            cursor.executemany(sql_insert_sistema, filas_sistema)
            conteos["sistema"] = len(filas_sistema)
            logger.info(f"  ✓ Sistema: {conteos['sistema']:,} registros insertados")

        # --------------------------------------------------------------
        # Insertar logs de LLM
        # --------------------------------------------------------------
        if not df_llm.empty:
            filas_llm = []

            for _, row in df_llm.iterrows():
                fila = (
                    _safe_str(row, "_id"),
                    _safe_ts(row,  "@timestamp"),
                    ahora,
                    _safe_str(row, "sap_function_log_type"),
                    _safe_str(row, "sap_function_application"),
                    _safe_str(row, "sap_function_message", max_len=2000),
                    _safe_str(row, "sap_app_env"),
                    _safe_str(row, "region_name"),
                    _safe_str(row, "region_code"),
                    _safe_str(row, "macro_region"),
                    _safe_str(row, "llm_model_id"),
                    _safe_str(row, "llm_provider"),
                    _safe_str(row, "llm_status"),
                    _safe_str(row, "llm_error_message", max_len=1000),
                    _safe_str(row, "llm_prompt_category"),
                    _safe_int(row, "llm_total_tokens"),
                    _safe_float(row, "llm_cost_usd"),
                    _safe_float(row, "llm_response_time_ms"),
                    _safe_float(row, "llm_temperature"),
                    _safe_str(row, "llm_finish_reason"),
                )
                filas_llm.append(fila)

            sql_insert_llm = f"""
                INSERT INTO {TABLA_LLM} (
                    log_id, event_timestamp, ingested_at,
                    log_type, application, message, environment,
                    region_name, region_code, macro_region,
                    llm_model_id, llm_provider, llm_status,
                    llm_error_message, llm_prompt_category,
                    llm_total_tokens, llm_cost_usd,
                    llm_response_time, llm_temperature,
                    llm_finish_reason
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """
            cursor.executemany(sql_insert_llm, filas_llm)
            conteos["llm"] = len(filas_llm)
            logger.info(f"  ✓ LLM: {conteos['llm']:,} registros insertados")

        conn.commit()
        total = conteos["sistema"] + conteos["llm"]
        logger.info(f"  ✓ Total insertado en HANA: {total:,} registros")

    except dbapi.Error as e:
        logger.error(f"❌ Error insertando en HANA: {e}")
        conn.rollback()
        raise

    finally:
        cursor.close()
        conn.close()

    return conteos


# =============================================================================
# FUNCIONES AUXILIARES DE CONVERSIÓN SEGURA
# =============================================================================
# Estas funciones convierten valores del DataFrame a tipos seguros para HANA.
# Manejan NaN, None, strings vacíos y tipos incorrectos sin lanzar excepciones.
# Por qué son necesarias:
#   pandas usa NaN para valores faltantes, pero HANA espera None/NULL.
#   Sin conversión, hdbcli lanza errores en columnas con NaN.

def _safe_str(row, col: str, max_len: int = 200):
    """Extrae string seguro — NaN se convierte a None (NULL en HANA)."""
    val = row.get(col)
    if val is None or (isinstance(val, float) and pd.isna(val)):
        return None
    result = str(val).strip()
    if max_len and len(result) > max_len:
        result = result[:max_len]   # truncar si excede el límite de la columna
    return result if result else None


def _safe_float(row, col: str):
    """Extrae float seguro — NaN se convierte a None."""
    val = row.get(col)
    if val is None or (isinstance(val, float) and pd.isna(val)):
        return None
    try:
        return float(val)
    except (ValueError, TypeError):
        return None


def _safe_int(row, col: str):
    """Extrae int seguro — NaN se convierte a None."""
    val = row.get(col)
    if val is None or (isinstance(val, float) and pd.isna(val)):
        return None
    try:
        return int(float(val))   # float() primero por si viene como "1735.0"
    except (ValueError, TypeError):
        return None


def _safe_ts(row, col: str):
    """
    Extrae timestamp seguro — convierte a datetime Python o None.
    HANA espera objetos datetime, no strings ISO 8601.
    """
    val = row.get(col)
    if val is None or (isinstance(val, float) and pd.isna(val)):
        return None
    try:
        ts = pd.to_datetime(val, utc=True)
        # Convertir a datetime naive (sin timezone) porque hdbcli
        # no siempre maneja bien los timezone-aware datetimes
        return ts.to_pydatetime().replace(tzinfo=None)
    except Exception:
        return None


# =============================================================================
# PUNTO DE ENTRADA — prueba de conexión y creación de tablas
# =============================================================================

if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(levelname)s | %(message)s"
    )

    print("=" * 60)
    print("HANA CLIENT — Prueba de conexión y setup de tablas")
    print("=" * 60)

    try:
        # Probar conexión
        conn = get_connection()
        conn.close()
        print("✓ Conexión exitosa")

        # Crear tablas
        create_tables()
        print("✓ Tablas listas")
        print()
        print("HANA está lista para recibir datos.")
        print("Próximo paso: python pipeline_loop.py")

    except Exception as e:
        print(f"❌ Error: {e}")
        sys.exit(1)
