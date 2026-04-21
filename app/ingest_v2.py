# =============================================================================
# app/ingest.py
# =============================================================================
# Versión 2 — con integración a SAP HANA Cloud
#
# Cambios respecto a la versión anterior:
#   - Agrega llamada a insert_logs() después de save_data()
#   - HANA es opcional: si no hay credenciales configuradas, solo guarda CSV
#   - El guardado en CSV sigue siendo siempre el primero — es el backup local
#
# Flujo actualizado:
#   fetch_current_window()
#     → save_data()         → data/raw/logs_[timestamp].csv  (siempre)
#     → insert_logs()       → SAP HANA Cloud                 (si está configurado)
# =============================================================================

import requests
import pandas as pd
import time
import os
import sys
import logging

sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from config import API_BASE_URL, get_headers, validate_config, HANA_HOST

logger = logging.getLogger(__name__)

# Determinar si HANA está disponible según si hay credenciales configuradas
# Esto permite correr ingest.py en modo local (solo CSV) sin credenciales HANA
HANA_DISPONIBLE = bool(HANA_HOST)

if HANA_DISPONIBLE:
    try:
        from hana_client import insert_logs, create_tables
        logger.info("✓ Módulo HANA cargado — los datos se guardarán en HANA y en CSV")
    except Exception as e:
        logger.warning(f"⚠️  No se pudo cargar hana_client: {e} — solo CSV")
        HANA_DISPONIBLE = False
else:
    logger.info("ℹ️  HANA_HOST no configurado — solo guardado en CSV local")


# =============================================================================
# FUNCIÓN PRINCIPAL: fetch_current_window()
# =============================================================================

def fetch_current_window() -> tuple:
    """
    Extrae todos los logs de la ventana UTC actual de 30 minutos.

    Flujo:
        1. GET /info  → obtiene total_pages y metadata de la ventana
        2. Loop GET /logs/current?page=1..N → acumula todos los registros
        3. Convierte a DataFrame y devuelve junto con la metadata

    Returns:
        tuple: (df, meta)
    """
    headers = get_headers()

    # ─────────────────────────────────────────────
    # PASO 1: GET /info — cuántas páginas hay
    # ─────────────────────────────────────────────
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

    # ─────────────────────────────────────────────
    # PASO 2: iterar todas las páginas
    # ─────────────────────────────────────────────
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
            logger.warning(f"  Página {page} fuera de rango — ventana cambió")
            break

        response.raise_for_status()
        payload = response.json()
        all_records.extend(payload["data"])

        if page < total_pages:
            time.sleep(0.2)

    df = pd.DataFrame(all_records)
    logger.info(f"Total extraído: {len(df):,} registros, {len(df.columns)} columnas")
    return df, meta


# =============================================================================
# FUNCIÓN: save_data()
# =============================================================================

def save_data(df: pd.DataFrame, meta: dict) -> str:
    """
    Guarda el DataFrame en data/raw/ como CSV con timestamp en el nombre.
    También guarda una muestra de 10 filas para el equipo.

    Returns:
        str: ruta del archivo principal guardado
    """
    os.makedirs("data/raw", exist_ok=True)

    window_tag = (
        meta["window_start"]
        .replace(":", "")
        .replace("-", "")
        .replace("+", "")[:15]
    )

    ruta_principal = f"data/raw/logs_{window_tag}.csv"
    df.to_csv(ruta_principal, index=False)
    logger.info(f"✓ CSV guardado: {ruta_principal} ({len(df):,} registros)")

    # Muestra para el equipo — siempre actualizada con datos recientes
    df.head(10).to_csv("data/raw/sample_10.csv", index=False)

    return ruta_principal


# =============================================================================
# FUNCIÓN: ingest_and_persist()
# =============================================================================

def ingest_and_persist() -> dict:
    """
    Función principal que orquesta extracción + guardado CSV + inserción HANA.

    Esta es la función que llama pipeline_loop.py en cada ciclo.
    Devuelve un resumen del resultado para el logging del loop.

    Returns:
        dict con resultado del ciclo:
        {
            "registros": N,
            "ventana_inicio": "...",
            "ventana_fin": "...",
            "csv": "ruta/al/archivo.csv",
            "hana": {"sistema": N, "llm": M} o None
        }
    """
    # Extraer datos
    df, meta = fetch_current_window()

    # Guardar CSV local (siempre, independiente de HANA)
    ruta_csv = save_data(df, meta)

    resultado = {
        "registros":      len(df),
        "ventana_inicio": meta.get("window_start"),
        "ventana_fin":    meta.get("window_end"),
        "csv":            ruta_csv,
        "hana":           None,
    }

    # Insertar en HANA si está disponible
    if HANA_DISPONIBLE:
        try:
            conteos_hana = insert_logs(df)
            resultado["hana"] = conteos_hana
            logger.info(
                f"✓ HANA: {conteos_hana['sistema']:,} sistema + "
                f"{conteos_hana['llm']:,} LLM"
            )
        except Exception as e:
            # Si HANA falla, el ciclo NO muere — los datos ya están en CSV
            # HANA es una capa adicional, no el único punto de guardado
            logger.error(f"⚠️  Error insertando en HANA: {e}")
            logger.warning("Los datos están guardados en CSV. HANA se reintentará en el próximo ciclo.")

    return resultado


# =============================================================================
# PUNTO DE ENTRADA — ejecución directa (para pruebas)
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
