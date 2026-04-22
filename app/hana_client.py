# =============================================================================
# app/hana_client.py
# =============================================================================
# Propósito: gestionar toda la interacción con SAP HANA Cloud.
#            Conexión, creación de tablas e inserción de datos.
#
# Este módulo es el ÚNICO que sabe cómo hablar con HANA.
# El resto del pipeline lo importa — nunca habla directamente con la BD.
#
# Por qué separarlo en su propio módulo:
#   - Si cambian las credenciales o el schema, solo se toca este archivo
#   - El pipeline no necesita saber nada de SQL ni de hdbcli
#   - Facilita pruebas independientes de la conexión
#
# Tablas que gestiona:
#   RAW_LOGS_SISTEMA → logs INFO, WARNING, ERROR, DEBUG, AUDIT, PERF, SECURITY
#   RAW_LOGS_LLM     → logs LLM_REQUEST, LLM_ERROR, LLM_TIMEOUT
#
# Por qué dos tablas separadas:
#   Las columnas activas son completamente distintas entre tipos de log.
#   Una sola tabla tendría ~50% de columnas NULL en cada fila,
#   desperdiciando espacio y complicando queries del modelo de ML.
#
# Cómo probarlo directamente:
#   python app/hana_client.py
#   → prueba conexión y crea las tablas si no existen
# =============================================================================

import os
import sys
import logging
import pandas as pd
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

# -----------------------------------------------------------------------------
# Configuración de rutas — ANTES de cualquier import del proyecto
# -----------------------------------------------------------------------------
# hana_client.py vive en app/. Para importar config.py (también en app/)
# necesitamos que app/ esté en sys.path.
# __file__          → .../app/hana_client.py
# dirname(__file__) → .../app/
# insert(0, ...)    → prioridad máxima sobre otros módulos del sistema

_APP_DIR = os.path.dirname(os.path.abspath(__file__))
if _APP_DIR not in sys.path:
    sys.path.insert(0, _APP_DIR)

from config import HANA_HOST, HANA_PORT, HANA_USER, HANA_PASSWORD

# -----------------------------------------------------------------------------
# Importación del driver oficial de SAP HANA
# -----------------------------------------------------------------------------
# hdbcli es la librería de SAP para conectarse a HANA desde Python.
# Debe estar instalada con: pip install hdbcli
# Si no está instalada, el módulo falla con mensaje claro en lugar de
# producir un ImportError críptico más adelante.

try:
    from hdbcli import dbapi
except ImportError:
    logger.error(
        "hdbcli no está instalado.\n"
        "Instálalo con: pip install hdbcli\n"
        "Luego actualiza requirements.txt con: pip freeze > requirements.txt"
    )
    sys.exit(1)


# =============================================================================
# CONSTANTES
# =============================================================================

TABLA_SISTEMA = "RAW_LOGS_SISTEMA"
TABLA_LLM     = "RAW_LOGS_LLM"
PREFIJO_LLM   = "LLM"


# =============================================================================
# FUNCIÓN: get_connection()
# =============================================================================

def get_connection():
    """
    Crea y devuelve una conexión activa a SAP HANA Cloud.

    Parámetros de conexión usados:
        address:                  host del servidor HANA (de .env)
        port:                     443 — puerto estándar HANA Cloud con TLS
        user:                     DBADMIN (usuario administrador)
        password:                 contraseña definida al crear la instancia
        encrypt:                  True — HANA Cloud SIEMPRE requiere cifrado
        sslValidateCertificate:   False — trial usa certificado wildcard de SAP
        sslHostNameInCertificate: patrón del certificado SAP

    Por qué sslValidateCertificate=False:
        En trial, HANA Cloud usa un certificado wildcard (*.hanacloud.ondemand.com)
        que algunos clientes rechazan por configuración estricta. Desactivar la
        validación estricta mantiene la conexión cifrada pero acepta el certificado
        de SAP sin verificación adicional. En producción real se usaría True.

    Returns:
        Objeto de conexión hdbcli activo y listo para ejecutar queries.

    Raises:
        EnvironmentError: si faltan credenciales en .env
        dbapi.Error:      si la conexión falla
    """
    credenciales = {
        "HANA_HOST":     HANA_HOST,
        "HANA_PORT":     HANA_PORT,
        "HANA_USER":     HANA_USER,
        "HANA_PASSWORD": HANA_PASSWORD,
    }
    faltantes = [k for k, v in credenciales.items() if not v]
    if faltantes:
        raise EnvironmentError(
            f"Faltan credenciales de HANA en .env: {faltantes}\n"
            f"Verifica que tu .env tiene HANA_HOST, HANA_PORT, HANA_USER y HANA_PASSWORD"
        )

    try:
        conn = dbapi.connect(
            address=HANA_HOST,
            port=int(HANA_PORT),
            user=HANA_USER,
            password=HANA_PASSWORD,
            encrypt=True,
            sslValidateCertificate=False,
            sslHostNameInCertificate="*.hanacloud.ondemand.com"
        )
        logger.info(f"✓ Conexión HANA establecida → {HANA_HOST}:{HANA_PORT}")
        return conn

    except dbapi.Error as e:
        logger.error(f"❌ Error conectando a HANA: {e}")
        logger.error(
            "Verifica que:\n"
            "  1. HANA_HOST es correcto (formato: xxx.hanacloud.ondemand.com)\n"
            "  2. HANA_PORT es 443\n"
            "  3. HANA_PASSWORD es la contraseña que pusiste al crear la instancia\n"
            "  4. La instancia HANA está en estado 'Running' en el Cockpit"
        )
        raise


# =============================================================================
# FUNCIÓN: create_tables()
# =============================================================================

def create_tables():
    """
    Crea las tablas RAW_LOGS_SISTEMA y RAW_LOGS_LLM en HANA si no existen.

    Es idempotente — puedes llamarla N veces sin error ni duplicados.
    Si las tablas ya existen, no hace nada.

    Tipos de datos HANA usados:
        NVARCHAR(n)  → strings Unicode
        TIMESTAMP    → fecha y hora con precisión de microsegundos
        INTEGER      → entero 32-bit
        DOUBLE       → decimal de doble precisión
    """
    conn   = get_connection()
    cursor = conn.cursor()

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
        logger.info("✓ Setup de tablas completado")

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
    Inserta todos los registros del DataFrame en HANA.

    Separa automáticamente Sistema y LLM e inserta cada grupo
    en su tabla usando executemany() para máxima eficiencia.

    Por qué executemany() y no un INSERT por fila:
        Con ~5,729 registros, un INSERT individual por fila significaría
        5,729 llamadas SQL. executemany() las agrupa en una sola operación,
        reduciendo el tiempo de minutos a segundos.

    Args:
        df: DataFrame completo con logs de la ventana actual

    Returns:
        dict: {"sistema": N, "llm": M}
    """
    if df.empty:
        logger.warning("DataFrame vacío — nada que insertar en HANA")
        return {"sistema": 0, "llm": 0}

    conn   = get_connection()
    cursor = conn.cursor()

    ahora   = datetime.now(timezone.utc)
    conteos = {"sistema": 0, "llm": 0}

    try:
        mask_llm   = df["sap_function_log_type"].str.startswith(PREFIJO_LLM, na=False)
        df_sistema = df[~mask_llm]
        df_llm     = df[mask_llm]

        # ── Logs de Sistema ───────────────────────────────────────────────────
        if not df_sistema.empty:
            filas_sistema = [
                (
                    _safe_str(row, "_id"),
                    _safe_ts(row,  "@timestamp"),
                    ahora.replace(tzinfo=None),
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
                for _, row in df_sistema.iterrows()
            ]

            cursor.executemany(f"""
                INSERT INTO {TABLA_SISTEMA} (
                    log_id, event_timestamp, ingested_at,
                    log_type, application, message, environment,
                    region_name, region_code, macro_region,
                    service_id, http_status, client_ip,
                    request_method, request_path
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """, filas_sistema)

            conteos["sistema"] = len(filas_sistema)
            logger.info(f"  ✓ Sistema: {conteos['sistema']:,} registros")

        # ── Logs de LLM ───────────────────────────────────────────────────────
        if not df_llm.empty:
            filas_llm = [
                (
                    _safe_str(row,   "_id"),
                    _safe_ts(row,    "@timestamp"),
                    ahora.replace(tzinfo=None),
                    _safe_str(row,   "sap_function_log_type"),
                    _safe_str(row,   "sap_function_application"),
                    _safe_str(row,   "sap_function_message", max_len=2000),
                    _safe_str(row,   "sap_app_env"),
                    _safe_str(row,   "region_name"),
                    _safe_str(row,   "region_code"),
                    _safe_str(row,   "macro_region"),
                    _safe_str(row,   "llm_model_id"),
                    _safe_str(row,   "llm_provider"),
                    _safe_str(row,   "llm_status"),
                    _safe_str(row,   "llm_error_message", max_len=1000),
                    _safe_str(row,   "llm_prompt_category"),
                    _safe_int(row,   "llm_total_tokens"),
                    _safe_float(row, "llm_cost_usd"),
                    _safe_float(row, "llm_response_time_ms"),
                    _safe_float(row, "llm_temperature"),
                    _safe_str(row,   "llm_finish_reason"),
                )
                for _, row in df_llm.iterrows()
            ]

            cursor.executemany(f"""
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
            """, filas_llm)

            conteos["llm"] = len(filas_llm)
            logger.info(f"  ✓ LLM: {conteos['llm']:,} registros")

        conn.commit()
        logger.info(f"  ✓ Total HANA: {conteos['sistema'] + conteos['llm']:,}")

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
# pandas usa float('nan') para valores faltantes.
# hdbcli espera None para insertar NULL en HANA.
# Sin estas funciones cada NaN causaría un error de tipo en el INSERT.

def _safe_str(row, col: str, max_len: int = 200):
    """String seguro: NaN → None, trunca si excede max_len."""
    val = row.get(col)
    if val is None or (isinstance(val, float) and pd.isna(val)):
        return None
    result = str(val).strip()
    if max_len and len(result) > max_len:
        result = result[:max_len]
    return result if result else None


def _safe_float(row, col: str):
    """Float seguro: NaN / no numérico → None."""
    val = row.get(col)
    if val is None or (isinstance(val, float) and pd.isna(val)):
        return None
    try:
        return float(val)
    except (ValueError, TypeError):
        return None


def _safe_int(row, col: str):
    """Int seguro: NaN / no numérico → None. Pasa por float para "1735.0"."""
    val = row.get(col)
    if val is None or (isinstance(val, float) and pd.isna(val)):
        return None
    try:
        return int(float(val))
    except (ValueError, TypeError):
        return None


def _safe_ts(row, col: str):
    """
    Timestamp seguro: ISO 8601 → datetime naive (sin timezone).
    hdbcli tiene comportamiento inconsistente con timezone-aware datetimes,
    por lo que eliminamos tzinfo pero el valor UTC sigue siendo correcto.
    """
    val = row.get(col)
    if val is None or (isinstance(val, float) and pd.isna(val)):
        return None
    try:
        ts = pd.to_datetime(val, utc=True)
        return ts.to_pydatetime().replace(tzinfo=None)
    except Exception:
        return None


# =============================================================================
# PUNTO DE ENTRADA — prueba directa
# =============================================================================

if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(levelname)s | %(message)s"
    )

    print("=" * 60)
    print("HANA CLIENT — Prueba de conexión y setup")
    print("=" * 60)
    print()

    try:
        print("Probando conexión...")
        conn = get_connection()
        conn.close()
        print("✓ Conexión exitosa\n")

        print("Verificando/creando tablas...")
        create_tables()

        print()
        print("=" * 60)
        print("✅ HANA lista para recibir datos")
        print("=" * 60)
        print()
        print("Próximo paso: python pipeline_loop.py")

    except Exception as e:
        print(f"\n❌ Error: {type(e).__name__}: {e}")
        sys.exit(1)
