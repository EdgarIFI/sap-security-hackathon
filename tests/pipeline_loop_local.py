# =============================================================================
# pipeline_loop.py
# =============================================================================
# Propósito: correr ingest.py automáticamente cada vez que cambia la ventana
#            UTC de 30 minutos, de forma indefinida, sin intervención manual.
#
# Posición en el proyecto:
#   RAÍZ DEL PROYECTO (no dentro de app/) — mismo nivel que .env, .gitignore
#
# Este archivo resuelve el problema central de la automatización:
#   Los datos de cada ventana son irrecuperables. Si no ingestas una ventana,
#   esos logs desaparecen para siempre. Este loop garantiza que ninguna
#   ventana se pierda mientras el proceso esté corriendo.
#
# Cómo funciona:
#   1. Primera ejecución inmediata — captura la ventana actual al arrancar
#   2. Calcula exactamente cuántos segundos faltan para la próxima ventana
#   3. Duerme ese tiempo exacto (evita drift de sincronización)
#   4. Corre fetch_current_window() + save_data() de ingest.py
#   5. Repite desde el paso 2 indefinidamente
#
# Manejo de errores:
#   Recuperables (timeout, 503, red transitoria): loguea y reintenta en 60s
#   Fatales (401 token inválido):                 loguea y detiene el loop
#   Ctrl+C del usuario:                           cierre limpio con reporte
#
# Logging:
#   Todo queda en pipeline.log (append, no sobreescribe entre sesiones).
#   También se imprime en consola en tiempo real.
#   Cada mensaje muestra hora UTC y hora Monterrey (CDT = UTC-6) en paralelo.
#
# Cómo ejecutarlo (desde la raíz del proyecto, con .venv activo):
#   python pipeline_loop.py
#
# Cómo detenerlo:
#   Ctrl + C  →  cierre limpio con reporte de ciclos completados
# =============================================================================

import time
import logging
import sys
import os
from datetime import datetime, timezone, timedelta


# -----------------------------------------------------------------------------
# Configuración de rutas — CRÍTICO: debe ocurrir ANTES de cualquier import
# de módulos del proyecto (config, ingest).
# -----------------------------------------------------------------------------
# pipeline_loop.py vive en la raíz del proyecto.
# Las funciones que necesitamos (fetch_current_window, save_data) viven en app/.
# Python no sabe buscar en app/ a menos que se lo indiquemos explícitamente.
#
# __file__           → ruta absoluta de pipeline_loop.py  → .../pipeline_loop.py
# dirname(__file__)  → raíz del proyecto                  → .../sap-security-hackathon/
# join(..., "app")   → ruta a app/                        → .../sap-security-hackathon/app/
#
# insert(0, APP_DIR) pone app/ al INICIO de sys.path, dándole prioridad máxima.
# Esto evita que Python confunda nuestro config.py con algún otro módulo
# llamado "config" que pudiera existir en el sistema o en el entorno virtual.

_BASE_DIR = os.path.dirname(os.path.abspath(__file__))
_APP_DIR  = os.path.join(_BASE_DIR, "app")

if _APP_DIR not in sys.path:
    sys.path.insert(0, _APP_DIR)

# Ahora sí podemos importar desde app/ sin error
import requests
from config import validate_config
from ingest import fetch_current_window, save_data


# =============================================================================
# ZONA HORARIA DE MONTERREY
# =============================================================================
# Monterrey opera en CDT (Central Daylight Time) durante horario de verano.
# CDT = UTC - 6 horas.
# Usamos offset fijo para no depender de librerías externas como pytz.
# NOTA: si el horario de verano cambia (noviembre → CST = UTC-6 también en MX
# ya que México eliminó el horario de verano en 2023 excepto zona fronteriza),
# este valor sigue siendo correcto para Monterrey: UTC-6 todo el año.

OFFSET_MONTERREY = timedelta(hours=-6)


# =============================================================================
# HELPERS DE HORA
# =============================================================================

def hora_monterrey(dt_utc: datetime) -> str:
    """
    Convierte datetime UTC a string con solo la hora en zona Monterrey.

    Args:
        dt_utc: datetime timezone-aware en UTC

    Returns:
        string "HH:MM:SS" en hora Monterrey
    """
    return (dt_utc + OFFSET_MONTERREY).strftime("%H:%M:%S")


def hora_completa_monterrey(dt_utc: datetime) -> str:
    """
    Convierte datetime UTC a string completo (fecha + hora) en zona Monterrey.

    Args:
        dt_utc: datetime timezone-aware en UTC

    Returns:
        string "YYYY-MM-DD HH:MM:SS" en hora Monterrey
    """
    return (dt_utc + OFFSET_MONTERREY).strftime("%Y-%m-%d %H:%M:%S")


# =============================================================================
# CONFIGURACIÓN DEL LOGGING
# =============================================================================
# Dos destinos simultáneos:
#   1. pipeline.log → archivo persistente en disco con toda la historia
#   2. consola      → monitoreo en tiempo real desde la terminal
#
# mode="a" (append) en el FileHandler garantiza que cada vez que reinicias
# el loop, los nuevos logs se agregan al final del archivo existente.
# Así tienes un historial completo de todas las sesiones.
#
# Formato de cada línea:
#   2026-04-21 06:00:01 UTC | INFO     | CICLO #1 — Iniciando ingesta

LOG_FILE = "pipeline.log"

log_formatter = logging.Formatter(
    fmt="%(asctime)s UTC | %(levelname)-8s | %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S"
)

# FileHandler: escribe en disco
file_handler = logging.FileHandler(LOG_FILE, mode="a", encoding="utf-8")
file_handler.setFormatter(log_formatter)

# StreamHandler: escribe en consola
console_handler = logging.StreamHandler(sys.stdout)
console_handler.setFormatter(log_formatter)

# Ensamblamos el logger
logger = logging.getLogger("pipeline")
logger.setLevel(logging.DEBUG)   # captura todos los niveles: DEBUG, INFO, WARNING, ERROR
logger.addHandler(file_handler)
logger.addHandler(console_handler)


# =============================================================================
# CONSTANTES
# =============================================================================

# Segundos de espera antes de reintentar tras error recuperable
# 60 segundos es suficiente para que problemas transitorios se resuelvan solos
SEGUNDOS_REINTENTO = 60

# Códigos HTTP que son errores fatales — no tiene sentido reintentar
# 401: Bearer token inválido o expirado → requiere intervención humana
ERRORES_FATALES_HTTP = {401}


# =============================================================================
# FUNCIÓN: calcular_segundos_hasta_proxima_ventana()
# =============================================================================

def calcular_segundos_hasta_proxima_ventana() -> tuple:
    """
    Calcula exactamente cuántos segundos faltan para el próximo cambio
    de ventana UTC (el próximo :00:00 o :30:00 exacto del reloj UTC).

    POR QUÉ NO USAR time.sleep(1800) (30 minutos fijos):
        Si duermes 30 minutos desde que TERMINA la ingesta, el punto de inicio
        de cada ciclo se desplaza hacia adelante en cada iteración (drift).
        Después de 10 ciclos podrías estar ejecutando 5+ minutos tarde
        respecto al cambio real de ventana, lo que puede causar:
          - Ingestar datos de la ventana anterior (duplicados)
          - Perderte el inicio de la nueva ventana

        Calculando el tiempo EXACTO hasta el próximo :00 o :30 UTC,
        el loop siempre arranca justo al inicio de cada ventana,
        independientemente de cuánto tardó el ciclo anterior.

    CÁLCULO:
        La API tiene exactamente dos puntos de cambio por hora:
          HH:00:00 UTC y HH:30:00 UTC
        Si ahora son las HH:MM:SS UTC:
          - Si MM < 30  → próximo cambio es HH:30:00 (misma hora)
          - Si MM >= 30 → próximo cambio es (HH+1):00:00 (hora siguiente)

    Returns:
        tuple: (segundos_con_margen: int, proxima_ventana: datetime)
            segundos_con_margen → cuántos segundos dormir
            proxima_ventana     → el datetime exacto del próximo cambio
    """
    ahora = datetime.now(timezone.utc)
    # datetime.now(timezone.utc) garantiza un datetime timezone-aware en UTC
    # Nunca usar datetime.utcnow() — produce datetime naive (sin timezone)

    if ahora.minute < 30:
        # Estamos en minutos 00-29 → próxima ventana en :30 de esta hora
        proxima_ventana = ahora.replace(minute=30, second=0, microsecond=0)
    else:
        # Estamos en minutos 30-59 → próxima ventana en :00 de la hora siguiente
        proxima_ventana = (ahora + timedelta(hours=1)).replace(
            minute=0, second=0, microsecond=0
        )

    segundos = (proxima_ventana - ahora).total_seconds()
    # total_seconds() devuelve float → int() lo trunca (no redondea)

    # +2 segundos de margen de seguridad para asegurar que el servidor
    # ya procesó y tiene listos los datos de la nueva ventana
    segundos_con_margen = int(segundos) + 2

    return segundos_con_margen, proxima_ventana


# =============================================================================
# FUNCIÓN: formatear_tiempo()
# =============================================================================

def formatear_tiempo(segundos: int) -> str:
    """
    Convierte un número de segundos a string legible "X min Y seg".

    Args:
        segundos: número entero de segundos

    Returns:
        string formateado, ej: "28 min 45 seg", "0 min 30 seg"
    """
    minutos = segundos // 60   # división entera: cuántos minutos completos
    segs    = segundos % 60    # módulo: segundos restantes
    return f"{minutos} min {segs} seg"


# =============================================================================
# FUNCIÓN: ejecutar_ciclo_ingesta()
# =============================================================================

def ejecutar_ciclo_ingesta(numero_ciclo: int) -> bool:
    """
    Ejecuta un ciclo completo de ingesta: extracción → guardado.

    Internamente llama a fetch_current_window() y save_data() de ingest.py.
    Captura y clasifica todos los errores posibles:
      - Errores recuperables → retorna False (el loop reintenta)
      - Errores fatales      → llama sys.exit(1) (el loop se detiene)

    Args:
        numero_ciclo: número secuencial del ciclo, usado solo para el log

    Returns:
        True  → ciclo completado exitosamente
        False → error recuperable, el llamador debe reintentar o continuar
    """
    logger.info(f"{'─' * 50}")
    logger.info(f"CICLO #{numero_ciclo} — Iniciando ingesta")
    logger.info(f"{'─' * 50}")

    try:
        # ── Extracción ────────────────────────────────────────────────────────
        # fetch_current_window() llama GET /info + loop GET /logs/current
        # Retorna (DataFrame con todos los registros, dict con metadata)
        logger.info("Llamando a GET /info y GET /logs/current...")
        df, meta = fetch_current_window()

        ventana_inicio  = meta.get("window_start", "?")
        ventana_fin     = meta.get("window_end",   "?")
        total_registros = len(df)

        logger.info(f"Ventana: {ventana_inicio} → {ventana_fin}")
        logger.info(f"Registros extraídos: {total_registros:,}")

        # ── Guardado ──────────────────────────────────────────────────────────
        # save_data() escribe el CSV en data/raw/logs_[timestamp].csv
        # y actualiza data/raw/sample_10.csv
        ruta = save_data(df, meta)
        logger.info(f"✓ Datos guardados en: {ruta}")
        logger.info(f"CICLO #{numero_ciclo} completado exitosamente")

        return True  # señal de éxito al loop principal

    # ── Errores RECUPERABLES ──────────────────────────────────────────────────
    # Problemas temporales que se resuelven solos con el tiempo.
    # Retornamos False para que el loop espere SEGUNDOS_REINTENTO y reintente.

    except requests.exceptions.Timeout:
        # El servidor no respondió en el tiempo de espera configurado
        # Puede ser carga temporal del servidor — suele resolverse solo
        logger.warning(
            f"CICLO #{numero_ciclo} | Timeout — servidor no respondió. "
            f"Reintentando en {SEGUNDOS_REINTENTO}s"
        )
        return False

    except requests.exceptions.ConnectionError:
        # Sin acceso al servidor — puede ser problema de red transitorio
        # o el servidor está momentáneamente caído
        logger.warning(
            f"CICLO #{numero_ciclo} | Error de conexión. "
            f"Reintentando en {SEGUNDOS_REINTENTO}s"
        )
        return False

    except requests.exceptions.HTTPError as e:
        # Error HTTP con código de estado — distinguimos fatal de recuperable
        codigo = e.response.status_code if e.response else 0

        if codigo in ERRORES_FATALES_HTTP:
            # 401: Bearer token inválido — reintentar no sirve de nada
            # Se necesita intervención humana para rotar el token
            logger.error(
                f"CICLO #{numero_ciclo} | ERROR FATAL HTTP {codigo} — "
                f"Bearer token inválido o expirado. "
                f"Verifica BEARER_TOKEN en .env y reinicia el loop."
            )
            logger.error("Deteniendo el loop.", exc_info=True)
            sys.exit(1)
        else:
            # 503, 429, 500, etc. — pueden ser temporales
            logger.warning(
                f"CICLO #{numero_ciclo} | HTTP {codigo}. "
                f"Reintentando en {SEGUNDOS_REINTENTO}s"
            )
            return False

    except Exception as e:
        # Cualquier otro error no anticipado
        # exc_info=True agrega el traceback completo al log para debugging
        # Lo tratamos como recuperable para no matar el loop por errores raros
        logger.error(
            f"CICLO #{numero_ciclo} | Error inesperado: {type(e).__name__}: {e}",
            exc_info=True
        )
        logger.warning(f"Reintentando en {SEGUNDOS_REINTENTO}s")
        return False


# =============================================================================
# LOOP PRINCIPAL: main()
# =============================================================================

def main():
    """
    Orquesta el loop infinito de ingesta automática.

    Flujo:
        1. Validar configuración
        2. Primera ingesta inmediata (ventana actual)
        3. Loop:
             a. Calcular tiempo hasta próxima ventana → dormir
             b. Ejecutar ciclo de ingesta
             c. Si falla → reintentar una vez → continuar de todas formas
             d. Volver a (a)
    """

    # Capturar hora de inicio para el mensaje de arranque
    ahora_utc = datetime.now(timezone.utc)

    logger.info("=" * 60)
    logger.info("PIPELINE LOOP — Ingesta automática de logs SAP")
    logger.info(
        f"Iniciado: {ahora_utc.strftime('%Y-%m-%d %H:%M:%S')} UTC  "
        f"({hora_completa_monterrey(ahora_utc)} Monterrey)"
    )
    logger.info(f"Log persistente en: {LOG_FILE}")
    logger.info("Presiona Ctrl+C para detener limpiamente")
    logger.info("=" * 60)

    # Validar que .env tiene las variables necesarias
    # Si falta algo, mejor saberlo ahora que en medio del loop
    try:
        validate_config()
        logger.info("✓ Configuración validada — API_BASE_URL y BEARER_TOKEN presentes")
    except EnvironmentError as e:
        logger.error(f"Error de configuración: {e}")
        logger.error("Verifica tu archivo .env y reinicia el loop.")
        sys.exit(1)

    numero_ciclo = 0

    # ── Primera ejecución inmediata ───────────────────────────────────────────
    # En lugar de esperar hasta la próxima ventana, ingestamos inmediatamente
    # al arrancar. Esto garantiza que capturamos la ventana actual aunque
    # ya lleve varios minutos en curso.

    logger.info("Primera ingesta inmediata — capturando ventana actual...")
    numero_ciclo += 1
    exito = ejecutar_ciclo_ingesta(numero_ciclo)

    if not exito:
        # Si la primera ingesta falla, esperamos y reintentamos UNA VEZ
        logger.warning(f"Primera ingesta falló. Reintento en {SEGUNDOS_REINTENTO}s...")
        time.sleep(SEGUNDOS_REINTENTO)
        numero_ciclo += 1
        ejecutar_ciclo_ingesta(numero_ciclo)
        # Si falla de nuevo, continuamos al loop normal —
        # preferimos perder una ventana a bloquear el arranque indefinidamente

    # ── Loop infinito ─────────────────────────────────────────────────────────

    while True:

        # Calcular y loguear cuánto falta para la próxima ventana
        segundos_espera, proxima_ventana = calcular_segundos_hasta_proxima_ventana()

        logger.info(
            f"Próxima ventana: {proxima_ventana.strftime('%H:%M:%S')} UTC  "
            f"({hora_monterrey(proxima_ventana)} Monterrey)  "
            f"(en {formatear_tiempo(segundos_espera)})"
        )

        # Dormir hasta la próxima ventana
        # El try/except aquí captura Ctrl+C durante el sleep para cierre limpio
        try:
            time.sleep(segundos_espera)
        except KeyboardInterrupt:
            ahora_utc = datetime.now(timezone.utc)
            logger.info("")
            logger.info("=" * 60)
            logger.info("Loop detenido por el usuario (Ctrl+C)")
            logger.info(f"Total de ciclos completados: {numero_ciclo}")
            logger.info(
                f"Detenido: {ahora_utc.strftime('%Y-%m-%d %H:%M:%S')} UTC  "
                f"({hora_completa_monterrey(ahora_utc)} Monterrey)"
            )
            logger.info("=" * 60)
            sys.exit(0)   # código 0 = salida limpia sin error

        # Ejecutar el ciclo de ingesta para esta ventana
        numero_ciclo += 1
        exito = ejecutar_ciclo_ingesta(numero_ciclo)

        if not exito:
            # Reintento único antes de pasar a la siguiente ventana
            # Si falla de nuevo, seguimos — preferimos perder una ventana
            # a bloquear el loop esperando indefinidamente
            logger.warning(
                f"Ciclo #{numero_ciclo} falló. "
                f"Reintentando en {SEGUNDOS_REINTENTO}s..."
            )
            time.sleep(SEGUNDOS_REINTENTO)
            ejecutar_ciclo_ingesta(numero_ciclo)


# =============================================================================
# PUNTO DE ENTRADA
# =============================================================================

if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        # Captura Ctrl+C si ocurre fuera del sleep (durante ingesta activa)
        logger.info("Loop interrumpido durante ingesta. Saliendo limpiamente.")
        sys.exit(0)
