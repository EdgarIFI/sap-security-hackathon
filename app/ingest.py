# =============================================================================
# app/ingest.py — Versión 2 con integración a SAP HANA Cloud
# =============================================================================
# Cambios respecto a la versión anterior:
#   - Agrega llamada a insert_logs() después de save_data()
#   - HANA es opcional: si no hay credenciales, solo guarda CSV
#   - CSV siempre primero — es el backup local garantizado
#   - Rutas absolutas para data/raw/ y data/processed/ (corrige error de paths)
#   - sys.path corregido para importar correctamente desde pipeline_loop.py
#
# Flujo actualizado:
#   ingest_and_persist()
#     → fetch_current_window()  → extrae de la API
#     → save_data()             → data/raw/logs_[timestamp].csv  (siempre)
#     → insert_logs()           → SAP HANA Cloud                 (si configurado)
# =============================================================================

import requests
import pandas as pd
import time
import os
import sys
import logging

# -----------------------------------------------------------------------------
# Configuración de rutas absolutas — ANTES de cualquier import del proyecto
# -----------------------------------------------------------------------------
# __file__              → .../app/ingest.py
# dirname(__file__)     → .../app/
# dirname(dirname(...)) → .../sap-security-hackathon/ (raíz)
#
# Rutas absolutas garantizan que data/raw/ y data/processed/ siempre se
# encuentren en la ubicación correcta, sin importar desde dónde se llame
# este script (directamente o importado por pipeline_loop.py desde la raíz).

_APP_DIR  = os.path.dirname(os.path.abspath(__file__))
_RAIZ     = os.path.dirname(_APP_DIR)
DATA_RAW  = os.path.join(_RAIZ, "data", "raw")
DATA_PROC = os.path.join(_RAIZ, "data", "processed")

# Agregar app/ al path para importar config.py y hana_client.py
if _APP_DIR not in sys.path:
    sys.path.insert(0, _APP_DIR)

from config import API_BASE_URL, get_headers, validate_config, HANA_HOST

logger = logging.getLogger(__name__)

# -----------------------------------------------------------------------------
# Detección de HANA — módulo opcional
# -----------------------------------------------------------------------------
# Si HANA_HOST está configurado en .env, intentamos cargar hana_client.
# Si no está o falla la carga, el pipeline corre igual solo con CSV.
# Esto permite trabajar localmente sin credenciales HANA configuradas.

HANA_DISPONIBLE = bool(HANA_HOST)

if HANA_DISPONIBLE:
    try:
        from hana_client import insert_logs, create_tables
        logger.info("✓ Módulo HANA cargado — datos se guardarán en HANA y CSV")
    except Exception as e:
        logger.warning(f"⚠️  No se pudo cargar hana_client: {e} — solo CSV")
        HANA_DISPONIBLE = False
else:
    logger.info("ℹ️  HANA_HOST no configurado — solo guardado en CSV local")


# =============================================================================
# FUNCIÓN: fetch_current_window()
# =============================================================================

def fetch_current_window() -> tuple:
    """
    Extrae todos los logs de la ventana UTC actual de 30 minutos.

    Flujo:
        1. GET /info  → total_pages y metadata de la ventana
        2. Loop GET /logs/current?page=1..N → acumula registros
        3. Convierte a DataFrame y retorna con la metadata

    Returns:
        tuple: (df, meta)
            df   → DataFrame con todos los registros
            meta → dict con window_start, window_end, total_records, etc.
    """
    headers = get_headers()

    # ── PASO 1: GET /info ─────────────────────────────────────────────────────
    logger.info("Consultando GET /info...")

    info_response = requests.get(
        f"{API_BASE_URL}/info",
        headers=headers,
        timeout=15
    )
    info_response.raise_for_status()
    meta = info_response.json()

    total_pages   = meta["total_pages"]
    total_records = meta["total_records"]
    window_start  = meta["window_start"]
    window_end    = meta["window_end"]

    logger.info(f"Ventana: {window_start} → {window_end}")
    logger.info(f"Total registros: {total_records:,} | Páginas: {total_pages}")

    # ── PASO 2: Loop paginado ─────────────────────────────────────────────────
    # Acumulamos en lista Python — más eficiente que pd.concat() iterativo
    all_records = []

    for page in range(1, total_pages + 1):
        logger.info(f"  Página {page:>3}/{total_pages}...")

        response = requests.get(
            f"{API_BASE_URL}/logs/current",
            headers=headers,
            params={"page": page},
            timeout=30
        )

        if response.status_code == 422:
            # Página fuera de rango — la ventana cambió durante la extracción
            logger.warning(f"  Página {page} fuera de rango — ventana cambió")
            break

        response.raise_for_status()
        payload = response.json()
        all_records.extend(payload["data"])

        if page < total_pages:
            time.sleep(0.2)   # pausa cortés entre llamadas

    df = pd.DataFrame(all_records)
    logger.info(f"Total extraído: {len(df):,} registros, {len(df.columns)} columnas")
    return df, meta


# =============================================================================
# FUNCIÓN: save_data()
# =============================================================================

def save_data(df: pd.DataFrame, meta: dict) -> str:
    """
    Guarda el DataFrame en data/raw/ como CSV con timestamp en el nombre.
    También actualiza data/raw/sample_10.csv con los primeros 10 registros.

    Usa rutas absolutas calculadas desde la ubicación de este archivo,
    garantizando funcionamiento correcto al ser importado desde pipeline_loop.py.

    Returns:
        str: ruta absoluta del CSV guardado
    """
    # Crear carpetas si no existen (idempotente)
    os.makedirs(DATA_RAW,  exist_ok=True)
    os.makedirs(DATA_PROC, exist_ok=True)

    # Nombre de archivo con timestamp de ventana — único y cronológicamente ordenable
    # "2026-04-20T06:00:00+00:00" → "20260420T060000"
    window_tag = (
        meta["window_start"]
        .replace(":", "")
        .replace("-", "")
        .replace("+", "")[:15]
    )

    ruta_principal = os.path.join(DATA_RAW, f"logs_{window_tag}.csv")
    ruta_muestra   = os.path.join(DATA_RAW, "sample_10.csv")

    df.to_csv(ruta_principal, index=False)
    logger.info(f"✓ CSV guardado: {ruta_principal} ({len(df):,} registros)")

    # Muestra de 10 filas para el equipo — siempre actualizada
    df.head(10).to_csv(ruta_muestra, index=False)

    return ruta_principal


# =============================================================================
# FUNCIÓN: ingest_and_persist()
# =============================================================================

def ingest_and_persist() -> dict:
    """
    Función principal que orquesta: extracción → CSV → HANA.

    Esta es la función que llama pipeline_loop.py en cada ciclo.
    CSV siempre se guarda primero como garantía. HANA es adicional.
    Si HANA falla, el ciclo NO muere — los datos ya están en CSV.

    Returns:
        dict:
        {
            "registros":      N,
            "ventana_inicio": "...",
            "ventana_fin":    "...",
            "csv":            "ruta/al/archivo.csv",
            "hana":           {"sistema": N, "llm": M} o None
        }
    """
    # Extracción
    df, meta = fetch_current_window()

    # CSV local — siempre, independiente de HANA
    ruta_csv = save_data(df, meta)

    resultado = {
        "registros":      len(df),
        "ventana_inicio": meta.get("window_start"),
        "ventana_fin":    meta.get("window_end"),
        "csv":            ruta_csv,
        "hana":           None,
    }

    # HANA — opcional, no bloquea el pipeline si falla
    if HANA_DISPONIBLE:
        try:
            conteos_hana = insert_logs(df)
            resultado["hana"] = conteos_hana
            logger.info(
                f"✓ HANA: {conteos_hana['sistema']:,} sistema + "
                f"{conteos_hana['llm']:,} LLM"
            )
        except Exception as e:
            logger.error(f"⚠️  Error insertando en HANA: {e}")
            logger.warning("Datos guardados en CSV. HANA se reintentará próximo ciclo.")

    return resultado


# =============================================================================
# PUNTO DE ENTRADA — ejecución directa para pruebas
# =============================================================================

if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(levelname)s | %(message)s"
    )

    print("=" * 60)
    print("INGEST.PY — Extracción manual de logs SAP")
    print("=" * 60)

    try:
        validate_config()
        resultado = ingest_and_persist()

        print("\n" + "=" * 60)
        print("✅ EXTRACCIÓN COMPLETADA")
        print("=" * 60)
        print(f"  Registros: {resultado['registros']:,}")
        print(f"  CSV:       {resultado['csv']}")
        if resultado["hana"]:
            print(f"  HANA:      {resultado['hana']}")
        print("=" * 60)

    except Exception as e:
        print(f"❌ Error: {type(e).__name__}: {e}")
        sys.exit(1)
