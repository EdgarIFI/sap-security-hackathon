# =============================================================================
# pipeline_loop_v2.py
# =============================================================================
# Versión 2 del loop principal — integra detección y alerting en el ciclo.
#
# Cambios respecto a pipeline_loop.py (v1):
#   1. Integración de quick_filter.filtrar_amenazas() en cada ciclo corto
#   2. Integración de alerting.enviar_alerta() por cada amenaza detectada
#   3. Dos intervalos separados: CORTO (2 min, detección) y LARGO (28 min, futuro ML)
#   4. Logging completo del resultado de cada alerta enviada a SAP
#
# Cómo usarlo durante desarrollo/prueba:
#   python pipeline_loop_v2.py
#
# Cuando esté validado, reemplaza al v1:
#   mv pipeline_loop.py pipeline_loop_v1_backup.py
#   mv pipeline_loop_v2.py pipeline_loop.py
#   cf push   (manifest.yml ya apunta a pipeline_loop.py — sin cambios)
#
# Posición en el repositorio: RAÍZ del repo (igual que v1)
# =============================================================================

import time
import logging
import sys
import os
from datetime import datetime, timezone, timedelta

# -----------------------------------------------------------------------------
# Rutas — ANTES de cualquier import del proyecto
# -----------------------------------------------------------------------------
# pipeline_loop_v2.py está en la raíz del repo.
# app/ está un nivel abajo. Lo agregamos al path para importar los módulos.

_BASE_DIR = os.path.dirname(os.path.abspath(__file__))
_APP_DIR  = os.path.join(_BASE_DIR, "app")

if _APP_DIR not in sys.path:
    sys.path.insert(0, _APP_DIR)

# -----------------------------------------------------------------------------
# Imports del proyecto
# -----------------------------------------------------------------------------

import requests
from config import validate_config
from ingest import ingest_and_persist

# quick_filter — detección determinística de amenazas
# Si el módulo no existe o falla, el pipeline continúa sin filtro
# (no queremos que un import roto detenga la ingesta)
try:
    from quick_filter import filtrar_amenazas
    QUICK_FILTER_DISPONIBLE = True
    logging.getLogger("pipeline").info("✓ quick_filter cargado")
except ImportError as e:
    QUICK_FILTER_DISPONIBLE = False
    logging.getLogger("pipeline").warning(
        f"⚠️  quick_filter no disponible: {e} — pipeline corre sin detección"
    )

# alerting — envío de alertas a POST /alert de la API SAP
try:
    from alerting import enviar_alerta
    ALERTING_DISPONIBLE = True
    logging.getLogger("pipeline").info("✓ alerting cargado")
except ImportError as e:
    ALERTING_DISPONIBLE = False
    logging.getLogger("pipeline").warning(
        f"⚠️  alerting no disponible: {e} — amenazas detectadas no se enviarán a SAP"
    )

# model — detección de anomalías ML (ciclo largo cada 28 min)
# Import condicional: si falla, el pipeline continúa sin ML.
# La ingesta y quick_filter nunca deben verse afectados por model.py.
try:
    from model import analizar_ventana
    MODEL_DISPONIBLE = True
    logging.getLogger("pipeline").info("✓ model cargado")
except ImportError as e:
    MODEL_DISPONIBLE = False
    logging.getLogger("pipeline").warning(
        f"⚠️  model no disponible: {e} — ciclo largo ML desactivado"
    )


# =============================================================================
# ZONA HORARIA DE MONTERREY
# =============================================================================

OFFSET_MONTERREY = timedelta(hours=-6)


def hora_monterrey(dt_utc: datetime) -> str:
    """HH:MM:SS en hora Monterrey."""
    return (dt_utc + OFFSET_MONTERREY).strftime("%H:%M:%S")


def hora_completa_monterrey(dt_utc: datetime) -> str:
    """YYYY-MM-DD HH:MM:SS en hora Monterrey."""
    return (dt_utc + OFFSET_MONTERREY).strftime("%Y-%m-%d %H:%M:%S")


# =============================================================================
# LOGGING — mismo setup que v1
# =============================================================================

LOG_FILE = "pipeline_v2.log"   # archivo separado para no mezclar con v1

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
# CONEXIÓN HANA — para el bloque de alerting
# =============================================================================

def _abrir_conexion_hana():
    """
    Abre una conexión HANA usando las credenciales de config.py.
    Retorna la conexión si tiene éxito, None si falla (sin lanzar excepción).
    El pipeline nunca debe morir por no poder conectar a HANA para alerting.
    """
    try:
        from config import HANA_HOST, HANA_PORT, HANA_USER, HANA_PASSWORD
        import hdbcli.dbapi as hdb
        conn = hdb.connect(
            address=HANA_HOST,
            port=int(HANA_PORT),
            user=HANA_USER,
            password=HANA_PASSWORD,
        )
        return conn
    except Exception as e:
        logger.warning(f"[HANA] No se pudo abrir conexión para alerting: {e}")
        return None


def _cerrar_conexion_hana(conn):
    """Cierra la conexión HANA silenciosamente."""
    if conn is not None:
        try:
            conn.close()
        except Exception:
            pass

def _ip_ya_alertada_por_qf(conn, ip: str, ventana_inicio: str) -> bool:
    """
    Verifica si quick_filter ya envió una alerta sobre esta IP
    en la ventana actual o en la hora previa.

    Retorna True si existe una alerta de quick_filter para esta IP
    en DBADMIN.ALERTS dentro de la última hora — en ese caso,
    la alerta ML debe ser suprimida para evitar doble conteo.

    Retorna False si no hay solapamiento, o si conn es None
    (comportamiento seguro: sin conexión, no suprimir).
    """
    if conn is None or not ip:
        return False
    try:
        cursor = conn.cursor()
        cursor.execute(
            """
            SELECT COUNT(*) FROM DBADMIN.ALERTS
            WHERE DETECTION_SOURCE = 'quick_filter'
              AND (DETAILS LIKE ? OR DETAILS LIKE ?)
              AND DETECTED_AT >= ADD_SECONDS(NOW(), -3600)
              AND ALERTED = 1
            """,
            (f"%{ip}%", f"%IP {ip}%"),
        )
        row = cursor.fetchone()
        cursor.close()
        count = int(row[0]) if row else 0
        return count > 0
    except Exception as e:
        logger.warning(f"[SUPRESS] Error verificando IP {ip} en ALERTS: {e}")
        return False  # ante cualquier error, no suprimir
    
# =============================================================================
# CONSTANTES
# =============================================================================

SEGUNDOS_REINTENTO = 60

ERRORES_FATALES_HTTP = {401}

# Ciclo corto — detección en tiempo real con quick_filter
# Cada 10 minutos sobre la ventana activa → MTTD ≤ 10 minutos
# Impacto directo en el criterio #1 (40% del score)
INTERVALO_POLLING_CORTO = 60 * 10    # 10 minutos

# Ciclo largo — reservado para model.py (AI Specialist)
# Se activa cuando se detecta cambio de ventana UTC
# Por ahora solo loguea — se integrará cuando model.py esté listo
INTERVALO_POLLING_LARGO = 60 * 28   # 28 minutos (ventana de 30 min - margen)


# =============================================================================
# FUNCIÓN: ejecutar_deteccion_y_alertas()
# =============================================================================

def ejecutar_deteccion_y_alertas(
    df_nuevos,
    ventana_inicio: str,
    conn=None,
) -> dict:
    """
    Corre quick_filter sobre df_nuevos y envía alertas por cada amenaza.

    Esta función encapsula el bloque DETECT → RESPOND del pipeline.
    Se llama dentro de ejecutar_ciclo_ingesta() después del upsert en HANA.

    Parámetros
    ----------
    df_nuevos : pd.DataFrame
        Registros nuevos del ciclo actual. Raw, sin ETL.
        Son exactamente los que acaban de insertarse en HANA.
    ventana_inicio : str
        ISO timestamp del inicio de la ventana activa.
        Se pasa a enviar_alerta() para trazabilidad en HANA.ALERTS.
    conn : hdbcli.dbapi.Connection | None
        Conexión HANA activa. Si se pasa, las alertas se registran en
        DBADMIN.ALERTS antes del POST. Si es None, solo se hace el POST.

    Retorna
    -------
    dict:
        {
            "amenazas_detectadas": int,   # total de amenazas encontradas
            "alertas_enviadas":    int,   # POSTs exitosos a SAP (HTTP 201)
            "alertas_fallidas":    int,   # POSTs que fallaron
            "duplicados":          int,   # amenazas ya registradas en HANA
        }
    """
    resumen = {
        "amenazas_detectadas": 0,
        "alertas_enviadas":    0,
        "alertas_fallidas":    0,
        "duplicados":          0,
    }

    # Verificar que los módulos están disponibles
    if not QUICK_FILTER_DISPONIBLE:
        logger.debug("[DETECT] quick_filter no disponible — saltando detección")
        return resumen

    if df_nuevos is None or df_nuevos.empty:
        logger.debug("[DETECT] df_nuevos vacío — sin registros que analizar")
        return resumen

    # ── Paso 1: Detección determinística ─────────────────────────────────────
    logger.info(f"[DETECT] Analizando {len(df_nuevos):,} registros nuevos...")

    try:
        amenazas = filtrar_amenazas(df_nuevos)
    except Exception as e:
        logger.error(f"[DETECT] Error en quick_filter: {e}", exc_info=True)
        return resumen

    resumen["amenazas_detectadas"] = len(amenazas)

    if not amenazas:
        logger.info("[DETECT] Sin amenazas en este batch ✓")
        return resumen

    logger.warning(
        f"[DETECT] ⚠️  {len(amenazas)} amenaza(s) detectada(s) — iniciando alerting"
    )

    # ── Paso 2: Enviar alerta por cada amenaza detectada ──────────────────────
    if not ALERTING_DISPONIBLE:
        logger.warning(
            "[DETECT] alerting no disponible — amenazas detectadas pero NO enviadas a SAP"
        )
        for a in amenazas:
            logger.warning(
                f"[DETECT]   PERDIDA: {a['alert_type']} ({a['severity']}) | {a['details'][:80]}"
            )
        return resumen

    for amenaza in amenazas:
        try:
            result = enviar_alerta(
                alert_type   = amenaza["alert_type"],
                severity     = amenaza["severity"],
                details      = amenaza["details"],
                log_id       = amenaza["log_id"],
                event_time   = amenaza.get("event_time"),
                conn         = conn,           # None si HANA no está disponible
                window_start = ventana_inicio,
                source       = "quick_filter",
            )

            if result.is_duplicate:
                resumen["duplicados"] += 1
                logger.info(
                    f"[DETECT] Duplicado ignorado | "
                    f"type={amenaza['alert_type']} | log_id={amenaza['log_id']}"
                )
            elif result.ok:
                resumen["alertas_enviadas"] += 1
                logger.info(
                    f"[DETECT] ✅ Alerta enviada a SAP | "
                    f"type={amenaza['alert_type']} ({amenaza['severity']}) | "
                    f"alert_id={result.alert_id} | "
                    f"HTTP {result.status_code} | {result.elapsed_ms:.0f}ms"
                )
            else:
                resumen["alertas_fallidas"] += 1
                logger.error(
                    f"[DETECT] ❌ Alerta fallida | "
                    f"type={amenaza['alert_type']} | "
                    f"error={result.error}"
                )

        except Exception as e:
            resumen["alertas_fallidas"] += 1
            logger.error(
                f"[DETECT] Excepción enviando alerta {amenaza.get('alert_type')}: {e}",
                exc_info=True,
            )

    # ── Resumen del ciclo de detección ────────────────────────────────────────
    logger.info(
        f"[DETECT] Resumen: "
        f"{resumen['amenazas_detectadas']} detectadas | "
        f"{resumen['alertas_enviadas']} enviadas ✅ | "
        f"{resumen['alertas_fallidas']} fallidas ❌ | "
        f"{resumen['duplicados']} duplicadas ↩"
    )

    return resumen


# =============================================================================
# FUNCIÓN: calcular_segundos_hasta_proxima_ventana()
# =============================================================================

def calcular_segundos_hasta_proxima_ventana() -> tuple:
    """
    Calcula exactamente cuántos segundos faltan para el próximo :00 o :30 UTC.
    Reservado para uso futuro del ciclo largo (model.py).
    """
    ahora = datetime.now(timezone.utc)

    if ahora.minute < 30:
        proxima_ventana = ahora.replace(minute=30, second=0, microsecond=0)
    else:
        proxima_ventana = (ahora + timedelta(hours=1)).replace(
            minute=0, second=0, microsecond=0
        )

    segundos = (proxima_ventana - ahora).total_seconds()
    return int(segundos) + 2, proxima_ventana


def formatear_tiempo(segundos: int) -> str:
    """Convierte segundos a 'X min Y seg'."""
    return f"{segundos // 60} min {segundos % 60} seg"


# =============================================================================
# FUNCIÓN: ejecutar_ciclo_ingesta()
# =============================================================================

def ejecutar_ciclo_ingesta(numero_ciclo: int) -> bool:
    """
    Ejecuta un ciclo completo:
        extracción → deduplicación → UPSERT HANA → quick_filter → alerting

    El flujo OBSERVE → ANALYZE → DETECT → RESPOND ocurre aquí.

    Returns:
        True  → éxito (ingesta completada, alerting ejecutado si había amenazas)
        False → error recuperable, el loop debe reintentar
    """
    logger.info(f"{'─' * 55}")
    logger.info(f"CICLO #{numero_ciclo} — Iniciando")
    logger.info(f"{'─' * 55}")

    try:
        # ── OBSERVE + ANALYZE: ingesta y persistencia ─────────────────────────
        resultado = ingest_and_persist()

        ventana_inicio = resultado["ventana_inicio"]
        ventana_fin    = resultado["ventana_fin"]
        df_nuevos      = resultado.get("df_nuevos")

        logger.info(f"Ventana: {ventana_inicio} → {ventana_fin}")
        logger.info(
            f"Registros: {resultado['registros']:,} | "
            f"Nuevos este ciclo: {resultado['nuevos']:,}"
        )
        logger.info(f"CSV: {resultado['csv']}")

        if resultado.get("hana"):
            logger.info(
                f"HANA: {resultado['hana']['sistema']:,} sistema + "
                f"{resultado['hana']['llm']:,} LLM insertados"
            )
        else:
            logger.info("HANA: no disponible en este ciclo — datos en CSV")

# ── DETECT + RESPOND: quick_filter → alerting ─────────────────────────
        if df_nuevos is not None and not df_nuevos.empty:
            # Abrir conexión HANA dedicada para registrar alertas en DBADMIN.ALERTS
            conn_alerting = _abrir_conexion_hana()
            try:
                resumen_alertas = ejecutar_deteccion_y_alertas(
                    df_nuevos      = df_nuevos,
                    ventana_inicio = ventana_inicio,
                    conn           = conn_alerting,   # ← ahora sí pasa conn real
                )
            finally:
                _cerrar_conexion_hana(conn_alerting)
        else:
            logger.info("[DETECT] Sin registros nuevos — omitiendo detección")
            resumen_alertas = {
                "amenazas_detectadas": 0,
                "alertas_enviadas": 0,
                "alertas_fallidas": 0,
                "duplicados": 0,
            }

        # ── Log final del ciclo ───────────────────────────────────────────────
        logger.info(
            f"CICLO #{numero_ciclo} completado | "
            f"nuevos={resultado['nuevos']:,} | "
            f"amenazas={resumen_alertas['amenazas_detectadas']} | "
            f"alertas_ok={resumen_alertas['alertas_enviadas']} | "
            f"alertas_err={resumen_alertas['alertas_fallidas']}"
        )
        return True

    except requests.exceptions.Timeout:
        logger.warning(
            f"CICLO #{numero_ciclo} | Timeout de red. "
            f"Reintentando en {SEGUNDOS_REINTENTO}s"
        )
        return False

    except requests.exceptions.ConnectionError:
        logger.warning(
            f"CICLO #{numero_ciclo} | Sin conexión. "
            f"Reintentando en {SEGUNDOS_REINTENTO}s"
        )
        return False

    except requests.exceptions.HTTPError as e:
        codigo = e.response.status_code if e.response else 0
        if codigo in ERRORES_FATALES_HTTP:
            logger.error(
                f"CICLO #{numero_ciclo} | HTTP {codigo} FATAL — "
                f"Bearer token inválido. Deteniendo pipeline."
            )
            sys.exit(1)
        else:
            logger.warning(
                f"CICLO #{numero_ciclo} | HTTP {codigo}. "
                f"Reintentando en {SEGUNDOS_REINTENTO}s"
            )
            return False

    except Exception as e:
        logger.error(
            f"CICLO #{numero_ciclo} | Error inesperado: {type(e).__name__}: {e}",
            exc_info=True,
        )
        logger.warning(f"Reintentando en {SEGUNDOS_REINTENTO}s")
        return False


# =============================================================================
# LOOP PRINCIPAL
# =============================================================================

def main():
    ahora_utc = datetime.now(timezone.utc)

    logger.info("=" * 60)
    logger.info("PIPELINE LOOP v2 — Ingesta + Detección + Alerting")
    logger.info(
        f"Iniciado: {ahora_utc.strftime('%Y-%m-%d %H:%M:%S')} UTC  "
        f"({hora_completa_monterrey(ahora_utc)} Monterrey)"
    )
    logger.info(f"Ciclo corto (ingesta + detección): cada {INTERVALO_POLLING_CORTO // 60} minutos")
    logger.info(f"Ciclo largo (futuro ML): cada {INTERVALO_POLLING_LARGO // 60} minutos")
    logger.info(f"quick_filter: {'✓ activo' if QUICK_FILTER_DISPONIBLE else '✗ no disponible'}")
    logger.info(f"alerting:     {'✓ activo' if ALERTING_DISPONIBLE else '✗ no disponible'}")
    logger.info(f"model ML:     {'✓ activo' if MODEL_DISPONIBLE else '✗ no disponible'}")
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
    ultimo_ciclo_largo = datetime.now(timezone.utc)
    ventana_inicio_ultimo_ciclo = datetime.now(timezone.utc).isoformat()

    # ── Primera ejecución inmediata ───────────────────────────────────────────
    # Captura lo que hay ahora sin esperar el próximo intervalo
    numero_ciclo += 1
    exito = ejecutar_ciclo_ingesta(numero_ciclo)

    if not exito:
        logger.warning(f"Primera ingesta falló. Reintento en {SEGUNDOS_REINTENTO}s...")
        time.sleep(SEGUNDOS_REINTENTO)
        numero_ciclo += 1
        ejecutar_ciclo_ingesta(numero_ciclo)

    # ── Loop continuo — ciclo corto cada 2 minutos ────────────────────────────
    while True:
        ahora_utc = datetime.now(timezone.utc)

        logger.info(
            f"Próximo ciclo en {INTERVALO_POLLING_CORTO // 60} min  "
            f"({hora_monterrey(ahora_utc)} Monterrey)"
        )

        try:
            time.sleep(INTERVALO_POLLING_CORTO)
        except KeyboardInterrupt:
            _cerrar_limpio(numero_ciclo)

        numero_ciclo += 1
        exito = ejecutar_ciclo_ingesta(numero_ciclo)

        if not exito:
            logger.warning(
                f"Ciclo #{numero_ciclo} falló. Reintento en {SEGUNDOS_REINTENTO}s..."
            )
            time.sleep(SEGUNDOS_REINTENTO)
            ejecutar_ciclo_ingesta(numero_ciclo)

        # Actualizar ventana para el ciclo largo
        ventana_inicio_ultimo_ciclo = datetime.now(timezone.utc).isoformat()

        # ── Ciclo largo: detección ML con model.py ────────────────────────────
        # Se activa cada ~28 minutos. Usa la misma conexión HANA del ciclo
        # corto para no duplicar conexiones. Si model.py falla, el pipeline
        # continúa sin interrupciones — la ingesta y quick_filter son prioritarios.
        ahora_utc = datetime.now(timezone.utc)
        minutos_desde_largo = (ahora_utc - ultimo_ciclo_largo).total_seconds() / 60

        if minutos_desde_largo >= (INTERVALO_POLLING_LARGO / 60):
            if MODEL_DISPONIBLE:
                logger.info(
                    f"[CICLO-LARGO] Han pasado {minutos_desde_largo:.1f} min — "
                    f"iniciando análisis ML"
                )
                conn_ml = _abrir_conexion_hana()
                try:
                    anomalias_ml = analizar_ventana(
                        conn=conn_ml,
                        window_start=ventana_inicio_ultimo_ciclo,
                    )

                    if anomalias_ml:
                        logger.warning(
                            f"[CICLO-LARGO] ⚠️  {len(anomalias_ml)} anomalía(s) ML detectada(s)"
                        )
                        conn_alerting_ml = _abrir_conexion_hana()
                        try:
                            for anomalia in anomalias_ml:
                                if not ALERTING_DISPONIBLE:
                                    continue

                                # ── Supresión de duplicados entre fuentes ─────
                                # Si quick_filter ya alertó sobre esta IP en la
                                # última hora, registramos la alerta ML como
                                # suprimida en HANA pero NO la enviamos a SAP.
                                # Esto evita que SAP reciba dos alertas sobre el
                                # mismo incidente visto desde dos perspectivas.
                                ip_en_details = ""
                                if anomalia["alert_type"] == "ml_ip_anomaly":
                                    # Extraer IP del campo details: "IP X.X.X.X: N eventos..."
                                    try:
                                        ip_en_details = anomalia["details"].split("IP ")[1].split(":")[0].strip()
                                    except (IndexError, AttributeError):
                                        ip_en_details = ""

                                if ip_en_details and _ip_ya_alertada_por_qf(
                                    conn_alerting_ml, ip_en_details, ventana_inicio_ultimo_ciclo
                                ):
                                    # Registrar en HANA como suprimida (para auditoría forense)
                                    try:
                                        cursor = conn_alerting_ml.cursor()
                                        cursor.execute(
                                            """
                                            UPDATE DBADMIN.ALERTS
                                            SET SUPPRESSED_BY = 'quick_filter'
                                            WHERE LOG_ID = ? AND DETECTION_SOURCE = 'model_ml'
                                            """,
                                            (anomalia["log_id"],)
                                        )
                                        conn_alerting_ml.commit()
                                        cursor.close()
                                    except Exception as e:
                                        logger.warning(f"[SUPRESS] No se pudo marcar suprimida: {e}")

                                    logger.info(
                                        f"[CICLO-LARGO] ⏭ Alerta ML suprimida (ya cubierta por quick_filter) | "
                                        f"IP={ip_en_details} | type={anomalia['alert_type']}"
                                    )
                                    continue  # no enviar a SAP

                                # ── Enviar alerta ML normalmente ──────────────
                                result = enviar_alerta(
                                    alert_type   = anomalia["alert_type"],
                                    severity     = anomalia["severity"],
                                    details      = anomalia["details"],
                                    log_id       = anomalia["log_id"],
                                    event_time   = anomalia.get("event_time"),
                                    conn         = conn_alerting_ml,
                                    window_start = ventana_inicio_ultimo_ciclo,
                                    source       = "model_ml",
                                )
                                if result.ok:
                                    logger.info(
                                        f"[CICLO-LARGO] ✅ Alerta ML enviada | "
                                        f"type={anomalia['alert_type']} "
                                        f"({anomalia['severity']}) | "
                                        f"HTTP {result.status_code}"
                                    )
                                else:
                                    logger.error(
                                        f"[CICLO-LARGO] ❌ Alerta ML fallida | "
                                        f"error={result.error}"
                                    )
                        finally:
                            _cerrar_conexion_hana(conn_alerting_ml)
                    else:
                        logger.info("[CICLO-LARGO] ✓ Sin anomalías ML en esta ventana")

                except Exception as e:
                    logger.error(
                        f"[CICLO-LARGO] Error en model.py: {e}", exc_info=True
                    )
                finally:
                    _cerrar_conexion_hana(conn_ml)
            else:
                logger.info(
                    f"[CICLO-LARGO] Han pasado {minutos_desde_largo:.1f} min — "
                    f"model.py no disponible, omitiendo análisis ML"
                )

            ultimo_ciclo_largo = ahora_utc


def _cerrar_limpio(numero_ciclo: int):
    """Cierre limpio con reporte al recibir Ctrl+C."""
    ahora_utc = datetime.now(timezone.utc)
    logger.info("")
    logger.info("=" * 60)
    logger.info("Pipeline detenido por el usuario (Ctrl+C)")
    logger.info(f"Ciclos completados: {numero_ciclo}")
    logger.info(
        f"Detenido: {ahora_utc.strftime('%Y-%m-%d %H:%M:%S')} UTC  "
        f"({hora_completa_monterrey(ahora_utc)} Monterrey)"
    )
    logger.info("=" * 60)
    sys.exit(0)


# =============================================================================
# PUNTO DE ENTRADA
# =============================================================================

if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        logger.info("Interrumpido durante ingesta. Saliendo limpiamente.")
        sys.exit(0)
