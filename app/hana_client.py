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

TABLA_SISTEMA = "DBADMIN.RAW_LOGS_SISTEMA"
TABLA_LLM     = "DBADMIN.RAW_LOGS_LLM"
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
            sslValidateCertificate=False
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
# FUNCIÓN: hana_esta_viva()
# =============================================================================

def hana_esta_viva() -> bool:
    """
    Verifica si HANA Cloud está accesible.

    Ejecuta un query trivial (SELECT 1 FROM DUMMY) que HANA
    siempre puede responder si está activa. DUMMY es una tabla
    del sistema que siempre existe y tiene una sola fila.

    No lanza excepciones — retorna True o False.
    Usar antes de cada ciclo para decidir si persistir en HANA
    o continuar solo con logging de advertencia.

    Returns:
        True  → HANA accesible y respondiendo
        False → HANA caída, pausada o inaccesible
    """
    try:
        conn = get_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT 1 FROM DUMMY")
        cursor.close()
        conn.close()
        return True
    except Exception as e:
        logger.warning(f"⚠️  HANA no responde: {e}")
        return False

# =============================================================================
# FUNCIÓN: create_tables()
# =============================================================================

def create_tables():
    conn   = get_connection()
    cursor = conn.cursor()

    # ── Verificar si una tabla ya existe ────────────────────────────
    def tabla_existe(nombre_tabla):
        cursor.execute(
            "SELECT COUNT(*) FROM TABLES WHERE TABLE_NAME = ?",
            (nombre_tabla,)
        )
        return cursor.fetchone()[0] > 0

    sql_sistema = f"""
        CREATE TABLE {TABLA_SISTEMA} (
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
        CREATE TABLE {TABLA_LLM} (
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
        if tabla_existe(TABLA_SISTEMA):
            logger.info(f"✓ Tabla {TABLA_SISTEMA} ya existe")
        else:
            cursor.execute(sql_sistema)
            logger.info(f"✓ Tabla {TABLA_SISTEMA} creada")

        if tabla_existe(TABLA_LLM):
            logger.info(f"✓ Tabla {TABLA_LLM} ya existe")
        else:
            cursor.execute(sql_llm)
            logger.info(f"✓ Tabla {TABLA_LLM} creada")

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
# FUNCIÓN: upsert_logs() antes era insert, pero cambió por la lógica de la APP, ver más en Contexto Actualisado abr 23
# =============================================================================

def upsert_logs(df_nuevos: pd.DataFrame) -> dict:
    """
    Inserta registros nuevos en HANA usando SELECT previo + executemany().

    Flujo:
        1. SELECT log_id de ambas tablas → obtener IDs ya existentes en HANA
        2. Filtrar df_nuevos en Python   → quedarse solo con los no existentes
        3. executemany() INSERT          → insertar todos en batch (O(1) queries)

    Por qué este enfoque en vez de MERGE fila por fila:
        MERGE individual: N queries SQL para N registros → ~9 min para 5,500 filas
        SELECT + executemany: 2 queries totales          → ~1 min para 5,500 filas

    Es seguro porque:
        - instances: 1 en manifest.yml → no hay concurrencia entre procesos
        - ids_procesados en ingest.py  → primera capa de deduplicación en memoria
        - Este SELECT es la segunda capa → seguro contra reinicios de CF

    Args:
        df_nuevos: DataFrame con registros a insertar (ya filtrados en memoria)

    Returns:
        dict: {"sistema": N, "llm": M}
    """
    if df_nuevos.empty:
        logger.info("Sin registros nuevos para HANA")
        return {"sistema": 0, "llm": 0}

    conn    = get_connection()
    cursor  = conn.cursor()
    ahora   = datetime.now(timezone.utc)
    conteos = {"sistema": 0, "llm": 0}

    try:
        # ── PASO 1: Obtener IDs ya existentes en HANA ────────────────────────
        # Un solo SELECT por tabla — mucho más eficiente que verificar fila por fila.
        # Con el índice UNIQUE en log_id este query es casi instantáneo.
        ids_hana = set()
        for tabla in [TABLA_SISTEMA, TABLA_LLM]:
            cursor.execute(f"SELECT log_id FROM {tabla}")
            ids_hana.update(row[0] for row in cursor.fetchall())

        logger.info(f"  IDs existentes en HANA: {len(ids_hana):,}")

        # ── PASO 2: Filtrar en Python ─────────────────────────────────────────
        # Comparación de sets en memoria — instantáneo sin importar el volumen.
        df_insertar = df_nuevos[~df_nuevos["_id"].isin(ids_hana)]

        if df_insertar.empty:
            logger.info("  Sin registros nuevos después de verificar HANA")
            return {"sistema": 0, "llm": 0}

        logger.info(f"  Registros a insertar: {len(df_insertar):,}")

        # ── PASO 3: Separar Sistema y LLM ─────────────────────────────────────
        mask_llm   = df_insertar["sap_function_log_type"].str.startswith(PREFIJO_LLM, na=False)
        df_sistema = df_insertar[~mask_llm]
        df_llm     = df_insertar[mask_llm]

        # ── PASO 4: INSERT masivo Sistema ────────────────────────────────────
        # executemany() envía todas las filas en una sola operación SQL.
        # O(1) en llamadas al servidor independientemente del número de filas.
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
            logger.info(f"  ✓ Sistema: {conteos['sistema']:,} registros insertados")

        # ── PASO 5: INSERT masivo LLM ─────────────────────────────────────────
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
            logger.info(f"  ✓ LLM: {conteos['llm']:,} registros insertados")

        # ── PASO 6: Commit ────────────────────────────────────────────────────
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
