# =============================================================================
# pipeline_loop.py
# =============================================================================
# Propósito: correr ingest.py automáticamente cada vez que cambia la ventana
#            UTC de 30 minutos, de forma indefinida, sin intervención manual.
#
# Este archivo resuelve el problema central de la automatización:
#   Los datos de cada ventana son irrecuperables. Si no ingestas una ventana,
#   esos logs desaparecen para siempre. Este loop garantiza que ninguna
#   ventana se pierda mientras el proceso esté corriendo.
#
# Cómo funciona:
#   1. Calcula exactamente cuánto tiempo falta para la próxima ventana
#   2. Duerme ese tiempo exacto (sin drift)
#   3. Corre ingest.py completo
#   4. Guarda los datos en data/raw/
#   5. Vuelve al paso 1
#
# Manejo de errores:
#   - Errores recuperables (timeout, 503, red): loguea y reintenta en 60s
#   - Errores fatales (401 token inválido): loguea y detiene el loop
#   - Ctrl+C del usuario: cierre limpio con mensaje de confirmación
#
# Logging:
#   Todo queda registrado en pipeline.log con timestamp.
#   También se imprime en consola para monitoreo en tiempo real.
#
# Cómo ejecutarlo (desde la raíz del proyecto, con .venv activo):
#   python pipeline_loop.py
#
# Cómo detenerlo:
#   Ctrl + C  →  cierre limpio
# =============================================================================

import time
import logging
import sys
import os
from datetime import datetime, timezone, timedelta

# Agregamos app/ al path para poder importar desde ahí
sys.path.append(os.path.join(os.path.dirname(os.path.abspath(__file__)), "app"))

import requests
from config import validate_config
from ingest import fetch_current_window, save_data


# =============================================================================
# CONFIGURACIÓN DEL LOGGING
# =============================================================================
# Configuramos dos destinos simultáneos para los logs:
#   1. pipeline.log  → archivo persistente en disco (toda la historia)
#   2. consola       → para monitoreo en tiempo real mientras miras la terminal
#
# El formato incluye timestamp, nivel (INFO/ERROR/etc.) y el mensaje.
# Ejemplo de línea en el log:
#   2026-04-20 06:00:01 UTC | INFO     | Iniciando ciclo de ingesta #1

LOG_FILE = "pipeline.log"

# Formato de cada línea del log
log_formatter = logging.Formatter(
    fmt="%(asctime)s UTC | %(levelname)-8s | %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S"
)
# datefmt controla cómo se muestra la fecha/hora en cada línea

# Handler para archivo — guarda todo en pipeline.log
# mode="a" = append (agrega al final, no sobreescribe si ya existe)
file_handler = logging.FileHandler(LOG_FILE, mode="a", encoding="utf-8")
file_handler.setFormatter(log_formatter)

# Handler para consola — imprime en tiempo real
console_handler = logging.StreamHandler(sys.stdout)
console_handler.setFormatter(log_formatter)

# Logger principal del pipeline
logger = logging.getLogger("pipeline")
logger.setLevel(logging.DEBUG)   # captura desde DEBUG hacia arriba
logger.addHandler(file_handler)
logger.addHandler(console_handler)


# =============================================================================
# CONSTANTES
# =============================================================================

# Segundos a esperar antes de reintentar si hay un error recuperable
SEGUNDOS_REINTENTO = 60

# Códigos HTTP que consideramos errores fatales (no tiene sentido reintentar)
# 401 = token inválido o expirado → requiere intervención humana
ERRORES_FATALES_HTTP = {401}


# =============================================================================
# FUNCIÓN: calcular_segundos_hasta_proxima_ventana()
# =============================================================================

def calcular_segundos_hasta_proxima_ventana() -> tuple:
    """
    Calcula exactamente cuántos segundos faltan para el próximo cambio
    de ventana UTC (el próximo :00:00 o :30:00 exacto).

    Por qué no simplemente time.sleep(1800):
        Si duermes 30 minutos fijos desde que termina la ingesta,
        el drift se acumula — cada ciclo empieza un poco más tarde
        respecto al cambio real de ventana. Con el tiempo puedes
        perderte transiciones de ventana o ingestar datos duplicados.

        Calculando el tiempo exacto hasta el próximo :00 o :30,
        el loop siempre se ejecuta justo al inicio de cada ventana,
        sin importar cuánto tardó la ingesta anterior.

    Returns:
        tuple: (segundos_a_esperar, proxima_ventana_datetime)
    """
    # Tiempo actual en UTC — siempre UTC, nunca hora local
    ahora = datetime.now(timezone.utc)

    minuto_actual = ahora.minute

    if minuto_actual < 30:
        # Estamos en la primera mitad de la hora (00-29)
        # La próxima ventana empieza en el minuto :30 de esta misma hora
        proxima_ventana = ahora.replace(
            minute=30,
            second=0,
            microsecond=0
        )
    else:
        # Estamos en la segunda mitad de la hora (30-59)
        # La próxima ventana empieza en el minuto :00 de la hora siguiente
        proxima_ventana = (ahora + timedelta(hours=1)).replace(
            minute=0,
            second=0,
            microsecond=0
        )

    # Calculamos la diferencia en segundos
    # total_seconds() devuelve un float — lo convertimos a int para sleep()
    segundos = (proxima_ventana - ahora).total_seconds()

    # Margen de seguridad: esperamos 2 segundos extra para asegurarnos
    # de que el servidor ya cargó los datos de la nueva ventana
    segundos_con_margen = int(segundos) + 2

    return segundos_con_margen, proxima_ventana


# =============================================================================
# FUNCIÓN: formatear_tiempo()
# =============================================================================

def formatear_tiempo(segundos: int) -> str:
    """
    Convierte segundos a formato legible "X min Y seg".

    Args:
        segundos: número de segundos a formatear

    Returns:
        string formateado, ej: "28 min 45 seg"
    """
    minutos = segundos // 60   # división entera — cuántos minutos completos
    segs    = segundos % 60    # el resto — cuántos segundos sobran
    return f"{minutos} min {segs} seg"


# =============================================================================
# FUNCIÓN: ejecutar_ciclo_ingesta()
# =============================================================================

def ejecutar_ciclo_ingesta(numero_ciclo: int) -> bool:
    """
    Ejecuta un ciclo completo de ingesta: fetch → save.

    Args:
        numero_ciclo: número secuencial del ciclo (para el log)

    Returns:
        True  si el ciclo fue exitoso
        False si hubo un error recuperable (el loop debe reintentar)

    Raises:
        SystemExit si el error es fatal (el loop debe detenerse)
    """
    logger.info(f"{'─' * 50}")
    logger.info(f"CICLO #{numero_ciclo} — Iniciando ingesta")
    logger.info(f"{'─' * 50}")

    try:
        # ─────────────────────────────────────────────
        # Extracción de datos
        # ─────────────────────────────────────────────
        logger.info("Llamando a GET /info y GET /logs/current...")
        df, meta = fetch_current_window()

        ventana_inicio = meta.get("window_start", "?")
        ventana_fin    = meta.get("window_end",   "?")
        total_registros = len(df)

        logger.info(f"Ventana: {ventana_inicio} → {ventana_fin}")
        logger.info(f"Registros extraídos: {total_registros:,}")

        # ─────────────────────────────────────────────
        # Guardado de datos
        # ─────────────────────────────────────────────
        ruta = save_data(df, meta)
        logger.info(f"✓ Datos guardados en: {ruta}")
        logger.info(f"CICLO #{numero_ciclo} completado exitosamente")

        return True

    # ─────────────────────────────────────────────────
    # Errores RECUPERABLES — loguear y señalar reintento
    # ─────────────────────────────────────────────────

    except requests.exceptions.Timeout:
        # El servidor tardó demasiado — puede ser temporal
        logger.warning(
            f"CICLO #{numero_ciclo} | Timeout — el servidor no respondió a tiempo. "
            f"Reintentando en {SEGUNDOS_REINTENTO}s"
        )
        return False

    except requests.exceptions.ConnectionError:
        # Problema de red — puede ser transitorio
        logger.warning(
            f"CICLO #{numero_ciclo} | Error de conexión — sin acceso al servidor. "
            f"Reintentando en {SEGUNDOS_REINTENTO}s"
        )
        return False

    except requests.exceptions.HTTPError as e:
        # Error HTTP — distinguimos fatales de recuperables
        codigo = e.response.status_code if e.response else 0

        if codigo in ERRORES_FATALES_HTTP:
            # 401: el token es inválido — no tiene sentido reintentar
            logger.error(
                f"CICLO #{numero_ciclo} | ERROR FATAL HTTP {codigo} — "
                f"Bearer token inválido o expirado. "
                f"Detener el loop y verificar BEARER_TOKEN en .env"
            )
            # exc_info=True agrega el traceback completo al log
            logger.error("Deteniendo el loop.", exc_info=True)
            sys.exit(1)   # salida con código de error
        else:
            # Otros errores HTTP (503, 429, etc.) — pueden ser temporales
            logger.warning(
                f"CICLO #{numero_ciclo} | HTTP {codigo} — "
                f"Reintentando en {SEGUNDOS_REINTENTO}s"
            )
            return False

    except Exception as e:
        # Cualquier otro error inesperado — loguear con traceback completo
        # y tratar como recuperable para no matar el loop por errores raros
        logger.error(
            f"CICLO #{numero_ciclo} | Error inesperado: {type(e).__name__}: {e}",
            exc_info=True
        )
        logger.warning(f"Reintentando en {SEGUNDOS_REINTENTO}s")
        return False


# =============================================================================
# LOOP PRINCIPAL
# =============================================================================

def main():
    """
    Loop principal de automatización.

    Flujo de cada iteración:
        1. Ejecutar ciclo de ingesta
        2. Si falla → esperar SEGUNDOS_REINTENTO y reintentar
        3. Si funciona → calcular tiempo hasta próxima ventana → esperar → repetir
    """

    logger.info("=" * 60)
    logger.info("PIPELINE LOOP — Ingesta automática de logs SAP")
    logger.info(f"Iniciado: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S')} UTC")
    logger.info(f"Log guardado en: {LOG_FILE}")
    logger.info("Presiona Ctrl+C para detener limpiamente")
    logger.info("=" * 60)

    # Validar configuración antes de entrar al loop
    # Si falta BEARER_TOKEN o API_BASE_URL, falla aquí con mensaje claro
    try:
        validate_config()
        logger.info("✓ Configuración validada")
    except EnvironmentError as e:
        logger.error(f"Error de configuración: {e}")
        logger.error("Verifica tu archivo .env antes de continuar")
        sys.exit(1)

    numero_ciclo = 0

    # ─────────────────────────────────────────────────────────────────
    # PRIMERA EJECUCIÓN INMEDIATA
    # ─────────────────────────────────────────────────────────────────
    # En lugar de esperar hasta la próxima ventana para el primer ciclo,
    # corremos inmediatamente al arrancar. Esto captura los datos de la
    # ventana actual que de otro modo podríamos perder.
    # ─────────────────────────────────────────────────────────────────

    logger.info("Ejecutando primera ingesta inmediata (ventana actual)...")
    numero_ciclo += 1
    exito = ejecutar_ciclo_ingesta(numero_ciclo)

    if not exito:
        logger.warning(f"Primera ingesta falló. Reintentando en {SEGUNDOS_REINTENTO}s...")
        time.sleep(SEGUNDOS_REINTENTO)
        numero_ciclo += 1
        ejecutar_ciclo_ingesta(numero_ciclo)

    # ─────────────────────────────────────────────────────────────────
    # LOOP INFINITO
    # ─────────────────────────────────────────────────────────────────

    while True:

        # Calcular tiempo hasta próxima ventana
        segundos_espera, proxima_ventana = calcular_segundos_hasta_proxima_ventana()

        logger.info(
            f"Próxima ventana: {proxima_ventana.strftime('%H:%M:%S')} UTC  "
            f"(en {formatear_tiempo(segundos_espera)})"
        )

        # Dormir hasta la próxima ventana
        # El try/except captura Ctrl+C para un cierre limpio
        try:
            time.sleep(segundos_espera)
        except KeyboardInterrupt:
            logger.info("")
            logger.info("=" * 60)
            logger.info("Loop detenido por el usuario (Ctrl+C)")
            logger.info(f"Total de ciclos completados: {numero_ciclo}")
            logger.info(f"Detenido: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S')} UTC")
            logger.info("=" * 60)
            sys.exit(0)   # salida limpia sin error

        # Ejecutar el ciclo de ingesta
        numero_ciclo += 1
        exito = ejecutar_ciclo_ingesta(numero_ciclo)

        # Si falló, reintentar una vez antes de esperar la siguiente ventana
        if not exito:
            logger.warning(f"Reintentando ciclo #{numero_ciclo} en {SEGUNDOS_REINTENTO}s...")
            time.sleep(SEGUNDOS_REINTENTO)
            ejecutar_ciclo_ingesta(numero_ciclo)
            # Si falla de nuevo, simplemente seguimos al próximo ciclo
            # — preferimos perder una ventana a matar el loop completo


# =============================================================================
# PUNTO DE ENTRADA
# =============================================================================

if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        # Captura Ctrl+C si ocurre fuera del sleep (por ejemplo durante la ingesta)
        logger.info("Loop interrumpido durante ingesta. Saliendo limpiamente.")
        sys.exit(0)
