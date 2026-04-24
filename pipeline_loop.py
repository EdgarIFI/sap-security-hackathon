# =============================================================================
# pipeline_loop.py
# =============================================================================
# Propósito: correr el pipeline de ingesta automáticamente cada vez que
#            cambia la ventana UTC de 30 minutos, indefinidamente.
#
# Posición en el proyecto: RAÍZ del repositorio (no dentro de app/)
#
# Cambio en esta versión:
#   Ahora llama a ingest_and_persist() en lugar de fetch_current_window()
#   + save_data() por separado. ingest_and_persist() orquesta todo:
#   extracción → CSV → HANA (si está disponible).
#
# Cómo ejecutarlo:
#   python pipeline_loop.py
#
# Cómo detenerlo:
#   Ctrl + C → cierre limpio con reporte
# =============================================================================

import time
import logging
import sys
import os
from datetime import datetime, timezone, timedelta

# -----------------------------------------------------------------------------
# Rutas — ANTES de cualquier import del proyecto
# -----------------------------------------------------------------------------
_BASE_DIR = os.path.dirname(os.path.abspath(__file__))
_APP_DIR  = os.path.join(_BASE_DIR, "app")

if _APP_DIR not in sys.path:
    sys.path.insert(0, _APP_DIR)

import requests
from config import validate_config
from ingest import ingest_and_persist
# ingest_and_persist() reemplaza la llamada separada a fetch_current_window()
# + save_data(). Ahora también maneja HANA internamente.


# =============================================================================
# ZONA HORARIA DE MONTERREY
# =============================================================================
# Monterrey = UTC-6 todo el año (México eliminó horario de verano en 2023,
# excepto zona fronteriza norte que sigue horario de EEUU)

OFFSET_MONTERREY = timedelta(hours=-6)


def hora_monterrey(dt_utc: datetime) -> str:
    """HH:MM:SS en hora Monterrey."""
    return (dt_utc + OFFSET_MONTERREY).strftime("%H:%M:%S")


def hora_completa_monterrey(dt_utc: datetime) -> str:
    """YYYY-MM-DD HH:MM:SS en hora Monterrey."""
    return (dt_utc + OFFSET_MONTERREY).strftime("%Y-%m-%d %H:%M:%S")


# =============================================================================
# LOGGING
# =============================================================================

LOG_FILE = "pipeline.log"

log_formatter = logging.Formatter(
    fmt="%(asctime)s UTC | %(levelname)-8s | %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S"
)

file_handler    = logging.FileHandler(LOG_FILE, mode="a", encoding="utf-8")
console_handler = logging.StreamHandler(sys.stdout)
file_handler.setFormatter(log_formatter)
console_handler.setFormatter(log_formatter)

logger = logging.getLogger("pipeline")
logger.setLevel(logging.DEBUG)
logger.addHandler(file_handler)
logger.addHandler(console_handler)


# =============================================================================
# CONSTANTES
# =============================================================================

SEGUNDOS_REINTENTO   = 60
ERRORES_FATALES_HTTP = {401}
INTERVALO_POLLING    = 60*5   # polling cada 5 minutos

# =============================================================================
# FUNCIÓN: calcular_segundos_hasta_proxima_ventana()
# =============================================================================

def calcular_segundos_hasta_proxima_ventana() -> tuple:
    """
    Calcula exactamente cuántos segundos faltan para el próximo :00 o :30 UTC.

    Evita el drift que ocurriría con time.sleep(1800) fijo.
    Siempre sincroniza al inicio exacto de cada ventana de la API.

    Returns:
        tuple: (segundos_con_margen: int, proxima_ventana: datetime)
    """
    ahora = datetime.now(timezone.utc)

    if ahora.minute < 30:
        proxima_ventana = ahora.replace(minute=30, second=0, microsecond=0)
    else:
        proxima_ventana = (ahora + timedelta(hours=1)).replace(
            minute=0, second=0, microsecond=0
        )

    segundos = (proxima_ventana - ahora).total_seconds()
    return int(segundos) + 2, proxima_ventana   # +2s de margen de seguridad


def formatear_tiempo(segundos: int) -> str:
    """Convierte segundos a 'X min Y seg'."""
    return f"{segundos // 60} min {segundos % 60} seg"


# =============================================================================
# FUNCIÓN: ejecutar_ciclo_ingesta()
# =============================================================================

def ejecutar_ciclo_ingesta(numero_ciclo: int) -> bool:
    """
    Ejecuta un ciclo completo: extracción → deduplicación → UPSERT → filtro rápido.

    Returns:
        True  → éxito
        False → error recuperable, el loop debe reintentar
    """
    logger.info(f"{'─' * 50}")
    logger.info(f"CICLO #{numero_ciclo} — Iniciando")
    logger.info(f"{'─' * 50}")

    try:
        resultado = ingest_and_persist()

        logger.info(f"Ventana: {resultado['ventana_inicio']} → {resultado['ventana_fin']}")
        logger.info(f"Registros: {resultado['registros']:,} | Nuevos: {resultado['nuevos']:,}")
        logger.info(f"CSV: {resultado['csv']}")

        if resultado.get("hana"):
            logger.info(
                f"HANA: {resultado['hana']['sistema']:,} sistema + "
                f"{resultado['hana']['llm']:,} LLM"
            )
        else:
            logger.info("HANA: no disponible — solo CSV")

        # ── Filtro rápido sobre registros nuevos ─────────────────────
        # Cuando quick_filter.py esté listo, descomentar:
        # df_nuevos = resultado.get("df_nuevos")
        # if df_nuevos is not None and not df_nuevos.empty:
        #     from quick_filter import analizar
        #     alertas = analizar(df_nuevos)
        #     if alertas:
        #         from alerting import enviar_alertas
        #         enviar_alertas(alertas)

        logger.info(f"CICLO #{numero_ciclo} completado exitosamente")
        return True

    except requests.exceptions.Timeout:
        logger.warning(f"CICLO #{numero_ciclo} | Timeout. Reintentando en {SEGUNDOS_REINTENTO}s")
        return False

    except requests.exceptions.ConnectionError:
        logger.warning(f"CICLO #{numero_ciclo} | Sin conexión. Reintentando en {SEGUNDOS_REINTENTO}s")
        return False

    except requests.exceptions.HTTPError as e:
        codigo = e.response.status_code if e.response else 0
        if codigo in ERRORES_FATALES_HTTP:
            logger.error(f"CICLO #{numero_ciclo} | HTTP {codigo} FATAL — token inválido. Deteniendo.")
            sys.exit(1)
        else:
            logger.warning(f"CICLO #{numero_ciclo} | HTTP {codigo}. Reintentando en {SEGUNDOS_REINTENTO}s")
            return False

    except Exception as e:
        logger.error(f"CICLO #{numero_ciclo} | Error: {type(e).__name__}: {e}", exc_info=True)
        logger.warning(f"Reintentando en {SEGUNDOS_REINTENTO}s")
        return False


# =============================================================================
# LOOP PRINCIPAL
# =============================================================================

def main():
    ahora_utc = datetime.now(timezone.utc)

    logger.info("=" * 60)
    logger.info("PIPELINE LOOP — Ingesta continua SAP + HANA (polling 2 min)")
    logger.info(
        f"Iniciado: {ahora_utc.strftime('%Y-%m-%d %H:%M:%S')} UTC  "
        f"({hora_completa_monterrey(ahora_utc)} Monterrey)"
    )
    logger.info(f"Intervalo de polling: {INTERVALO_POLLING // 60} minutos")
    logger.info(f"Log en: {LOG_FILE}")
    logger.info("Ctrl+C para detener")
    logger.info("=" * 60)

    try:
        validate_config()
        logger.info("✓ Configuración validada")
    except EnvironmentError as e:
        logger.error(f"Error de configuración: {e}")
        sys.exit(1)

    numero_ciclo = 0

    # Primera ejecución inmediata — captura lo que hay ahora
    numero_ciclo += 1
    exito = ejecutar_ciclo_ingesta(numero_ciclo)

    if not exito:
        logger.warning(f"Primera ingesta falló. Reintento en {SEGUNDOS_REINTENTO}s...")
        time.sleep(SEGUNDOS_REINTENTO)
        numero_ciclo += 1
        ejecutar_ciclo_ingesta(numero_ciclo)

    # Loop continuo — polling cada 2 minutos
    while True:
        ahora_utc = datetime.now(timezone.utc)
        logger.info(
            f"Próximo polling en {INTERVALO_POLLING // 60} min  "
            f"({hora_monterrey(ahora_utc)} Monterrey)"
        )

        try:
            time.sleep(INTERVALO_POLLING)
        except KeyboardInterrupt:
            ahora_utc = datetime.now(timezone.utc)
            logger.info("")
            logger.info("=" * 60)
            logger.info("Loop detenido por el usuario (Ctrl+C)")
            logger.info(f"Ciclos completados: {numero_ciclo}")
            logger.info(
                f"Detenido: {ahora_utc.strftime('%Y-%m-%d %H:%M:%S')} UTC  "
                f"({hora_completa_monterrey(ahora_utc)} Monterrey)"
            )
            logger.info("=" * 60)
            sys.exit(0)

        numero_ciclo += 1
        exito = ejecutar_ciclo_ingesta(numero_ciclo)

        if not exito:
            logger.warning(f"Ciclo #{numero_ciclo} falló. Reintento en {SEGUNDOS_REINTENTO}s...")
            time.sleep(SEGUNDOS_REINTENTO)
            ejecutar_ciclo_ingesta(numero_ciclo)

# =============================================================================
# PUNTO DE ENTRADA
# =============================================================================

if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        logger.info("Interrumpido durante ingesta. Saliendo limpiamente.")
        sys.exit(0)
