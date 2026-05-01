"""
app/alerting.py
===============
Módulo de alerting para el SAP AI Security SOC.

Responsabilidad única: construir el mensaje de alerta en formato WHAT/WHEN/WHY
y enviarlo via POST al endpoint /alert de la misma API SAP. Registrar el
resultado en DBADMIN.ALERTS de SAP HANA.

--- CONTRATOS DE LA API (POST /alert) ---

URL:     {API_BASE_URL}/alert
Auth:    Authorization: Bearer <BEARER_TOKEN>  ← mismo token que /logs/current
Body:    {"message": "WHAT: ... WHEN: ... WHY: ..."}   ← UN solo campo, máx 300 chars
Éxito:   HTTP 201 Created
Error:   HTTP 422 si message > 300 chars o campo ausente
Error:   HTTP 401 si Bearer token inválido

IMPORTANTE: No existe una WEBHOOK_URL separada. El endpoint de alerting ES
la misma API SAP que ya usamos para ingerir logs. El Bearer token es el mismo.

--- CONTRATO PÚBLICO DE ESTE MÓDULO ---

    from app.alerting import enviar_alerta, AlertResult

    result = enviar_alerta(
        alert_type   = "brute_force",
        severity     = "high",
        details      = "15 intentos 401 desde IP 203.0.113.45 en 90 seg",
        log_id       = "abc123",
        event_time   = "2026-04-30T12:15:33+00:00",  # cuándo ocurrió el evento
        conn         = hana_conn,        # opcional — registra en HANA si se pasa
        window_start = "2026-04-30T12:00:00+00:00",  # opcional
        source       = "quick_filter",   # opcional, default "quick_filter"
    )

    if result.ok:
        print("Alerta enviada:", result.alert_id)
    else:
        print("Falló:", result.error)

--- INTEGRACIÓN EN pipeline_loop.py ---

    from app.alerting import enviar_alerta
    from app.quick_filter import filtrar_amenazas

    # Después de upsert_logs(df_nuevos):
    amenazas = filtrar_amenazas(df_nuevos)
    for a in amenazas:
        enviar_alerta(
            alert_type   = a["alert_type"],
            severity     = a["severity"],
            details      = a["details"],
            log_id       = a["log_id"],
            event_time   = a["event_time"],
            conn         = conn,
            window_start = meta.get("window_start"),
        )
"""

import os
import uuid
import logging
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional

import requests

# ---------------------------------------------------------------------------
# Logging — mismo patrón que el resto del proyecto
# El nombre "alerting" permite filtrar en `cf logs sap-ai-soc`
# ---------------------------------------------------------------------------
logger = logging.getLogger("alerting")

# ---------------------------------------------------------------------------
# Constantes de comportamiento HTTP
# ---------------------------------------------------------------------------

# Reintentos ante error de servidor (5xx) o timeout de red.
# 3 intentos con backoff 1s → 2s evitan bloquear el ciclo de polling.
# No reintentamos 4xx — el mismo payload fallaría de nuevo.
MAX_RETRIES = 3

# Backoff exponencial base en segundos entre reintentos.
BACKOFF_BASE_SECONDS = 1.0

# Timeout por intento de POST (segundos).
REQUEST_TIMEOUT_SECONDS = 10

# Límite de caracteres impuesto por la API para el campo message.
# Usamos 295 como límite interno (margen de 5 chars) para nunca exceder 300.
MESSAGE_MAX_CHARS = 295

# ---------------------------------------------------------------------------
# Severidades y tipos conocidos — documentación interna
# (La API no valida esto — es para documentar qué genera quick_filter y model.py)
# ---------------------------------------------------------------------------
SEVERIDADES_VALIDAS = {"low", "medium", "high", "critical"}

TIPOS_ALERTA = {
    # quick_filter — logs de Sistema
    "brute_force",          # múltiples 401/403 desde la misma IP
    "path_scan",            # peticiones a rutas sospechosas con 404
    "security_event",       # log con sap_function_log_type = 'SECURITY'
    # quick_filter — logs LLM
    "high_cost_llm_error",  # LLM_ERROR con llm_cost_usd > umbral
    "slow_llm_response",    # llm_response_time_ms > umbral
    "llm_timeout",          # log de tipo LLM_TIMEOUT
    # model.py — anomalías estadísticas ML (unsupervised)
    "count_anomaly",        # volumen inusual de logs en ventana temporal
    "category_anomaly",     # template de mensaje nunca visto en entrenamiento
    # genérico
    "unknown_anomaly",
}


# ---------------------------------------------------------------------------
# Dataclass de resultado — contrato de retorno de enviar_alerta()
# ---------------------------------------------------------------------------

@dataclass
class AlertResult:
    """
    Resultado completo de un intento de envío de alerta.

    ok : bool
        True si la API respondió HTTP 201.
        False en cualquier error: red, 4xx, 5xx, HANA, validación.
    alert_id : str
        UUID generado para esta alerta. Siempre presente aunque falle.
        Permite correlacionar logs de CF con registros en HANA.
    status_code : int | None
        Código HTTP de la respuesta de /alert. None si no se completó el POST.
    error : str | None
        Descripción del error. None si ok=True.
    is_duplicate : bool
        True si ya existía una alerta para este log_id en HANA.
        En ese caso no se hace POST (el campo ok=False también).
    elapsed_ms : float
        Latencia total de enviar_alerta() en ms. Contribuye al MTTD medido.
    """
    ok: bool
    alert_id: str
    status_code: Optional[int] = None
    error: Optional[str] = None
    is_duplicate: bool = False
    elapsed_ms: float = 0.0


# ---------------------------------------------------------------------------
# Función principal pública
# ---------------------------------------------------------------------------

def enviar_alerta(
    alert_type: str,
    severity: str,
    details: str,
    log_id: str,
    event_time: Optional[str] = None,
    conn=None,
    window_start: Optional[str] = None,
    source: str = "quick_filter",
) -> AlertResult:
    """
    Envía una alerta de seguridad al endpoint POST /alert de la API SAP.
    Registra la alerta en DBADMIN.ALERTS antes del POST (para trazabilidad).

    La API SAP espera exactamente este body:
        {"message": "WHAT: <qué>. WHEN: <cuándo ISO>. WHY: <por qué con evidencia>."}

    El mensaje se construye automáticamente desde los parámetros de esta función.

    Parámetros
    ----------
    alert_type : str
        Tipo de amenaza. Ej: "brute_force", "path_scan", "count_anomaly".
        Solo para registro interno y construcción del mensaje WHAT.
    severity : str
        Nivel: "low", "medium", "high" o "critical".
        Solo para registro interno (la API no tiene campo de severidad).
    details : str
        Evidencia concreta de la amenaza. Se incluye en el campo WHY del mensaje.
        Ej: "18 intentos HTTP 401 desde IP 203.0.113.45 en 2 minutos".
    log_id : str
        El _id del registro que disparó la alerta.
        Se usa para verificar duplicados en HANA antes de enviar.
    event_time : str | None
        Timestamp ISO UTC de cuándo ocurrió el evento (campo @timestamp del log).
        Se incluye en el campo WHEN del mensaje. Si es None se usa el momento actual.
    conn : hdbcli.dbapi.Connection | None
        Conexión activa a SAP HANA. Si se pasa:
          - Verifica duplicados antes de enviar
          - Registra la alerta en DBADMIN.ALERTS con alerted=0
          - Actualiza alerted=1 si el POST fue exitoso
        Si es None: omite todo lo de HANA (útil en tests).
    window_start : str | None
        Inicio de la ventana UTC activa. Se guarda en HANA para trazabilidad.
    source : str
        Quién generó la alerta: "quick_filter" o "model_ml". Default: "quick_filter".

    Retorna
    -------
    AlertResult — nunca lanza excepciones. Cualquier error queda en .error.
    """
    t_inicio = time.monotonic()
    alert_id = str(uuid.uuid4())

    # Timestamp de detección — cuándo el pipeline detectó la amenaza
    detected_at = datetime.now(timezone.utc).isoformat()

    # Si no viene event_time del log, usamos el momento de detección
    when_str = event_time if event_time else detected_at

    logger.info(
        f"[ALERT] Iniciando | alert_id={alert_id} | "
        f"type={alert_type} | severity={severity} | log_id={log_id}"
    )

    # ------------------------------------------------------------------
    # Paso 1 — Validar inputs básicos
    # ------------------------------------------------------------------
    error_val = _validar_inputs(alert_type, severity, details, log_id)
    if error_val:
        logger.warning(f"[ALERT] Validación fallida: {error_val}")
        return AlertResult(
            ok=False, alert_id=alert_id,
            error=f"Validación: {error_val}",
            elapsed_ms=_elapsed_ms(t_inicio),
        )

    # ------------------------------------------------------------------
    # Paso 2 — Verificar duplicados en HANA (si hay conexión)
    # El mismo log_id puede llegar en múltiples ciclos de polling mientras
    # la ventana no cambie. Solo alertamos la primera vez.
    # ------------------------------------------------------------------
    if conn is not None:
        try:
            if _alerta_ya_existe(conn, log_id):
                logger.info(
                    f"[ALERT] Duplicado ignorado | log_id={log_id} ya registrado en HANA"
                )
                return AlertResult(
                    ok=False, alert_id=alert_id, is_duplicate=True,
                    error="Duplicado: log_id ya tiene alerta en DBADMIN.ALERTS",
                    elapsed_ms=_elapsed_ms(t_inicio),
                )
        except Exception as e:
            # Si la verificación falla, continuamos — preferimos una alerta
            # potencialmente duplicada a no alertar.
            logger.warning(f"[ALERT] No pudo verificar duplicados en HANA: {e}")

    # ------------------------------------------------------------------
    # Paso 3 — Construir el mensaje WHAT/WHEN/WHY para la API
    # Formato exacto que SAP espera. Máximo 300 chars (usamos 295 como límite).
    # ------------------------------------------------------------------
    message = _construir_mensaje(
        alert_type=alert_type,
        when_str=when_str,
        details=details,
    )

    logger.debug(f"[ALERT] Mensaje construido ({len(message)} chars): {message}")

    # ------------------------------------------------------------------
    # Paso 4 — Insertar en HANA con alerted=0 ANTES del POST
    # Si CF reinicia entre el POST y el UPDATE, el registro queda con
    # alerted=0 — que indica "enviado pero no confirmado", auditable.
    # ------------------------------------------------------------------
    hana_insertado = False
    if conn is not None:
        try:
            _insertar_alerta_hana(
                conn=conn,
                alert_id=alert_id,
                log_id=log_id,
                detected_at=detected_at,
                source=source,
                alert_type=alert_type,
                severity=severity,
                details=details,         # guardamos el details completo en HANA
                window_start=window_start,
                alerted=0,
            )
            hana_insertado = True
            logger.info(f"[ALERT] Registrada en HANA (alerted=0) | alert_id={alert_id}")
        except Exception as e:
            # No es fatal — intentamos el POST aunque HANA falle
            logger.warning(f"[ALERT] INSERT en HANA falló (continuando con POST): {e}")

    # ------------------------------------------------------------------
    # Paso 5 — POST al endpoint /alert de la API SAP
    # ------------------------------------------------------------------
    status_code, post_error = _post_alert(message)

    if post_error:
        logger.error(
            f"[ALERT] POST fallido | alert_id={alert_id} | "
            f"status={status_code} | error={post_error}"
        )
        return AlertResult(
            ok=False, alert_id=alert_id,
            status_code=status_code, error=post_error,
            elapsed_ms=_elapsed_ms(t_inicio),
        )

    # ------------------------------------------------------------------
    # Paso 6 — POST exitoso (HTTP 201): actualizar alerted=1 en HANA
    # ------------------------------------------------------------------
    logger.info(
        f"[ALERT] ✅ ENVIADA | alert_id={alert_id} | "
        f"HTTP {status_code} | elapsed={_elapsed_ms(t_inicio):.0f}ms"
    )

    if conn is not None and hana_insertado:
        try:
            _marcar_alerta_enviada(conn, alert_id)
            logger.info(f"[ALERT] HANA actualizada: alerted=1 | alert_id={alert_id}")
        except Exception as e:
            # No fatal — la alerta ya fue enviada a SAP. El alerted=0 en HANA
            # simplemente indica "no confirmado localmente", no "no enviado".
            logger.warning(f"[ALERT] No pudo actualizar alerted=1 en HANA: {e}")

    return AlertResult(
        ok=True, alert_id=alert_id,
        status_code=status_code,
        elapsed_ms=_elapsed_ms(t_inicio),
    )


# ---------------------------------------------------------------------------
# Construcción del mensaje WHAT/WHEN/WHY
# ---------------------------------------------------------------------------

def _construir_mensaje(
    alert_type: str,
    when_str: str,
    details: str,
) -> str:
    """
    Construye el mensaje de alerta en el formato WHAT/WHEN/WHY que exige la API SAP.

    Formato exacto documentado en la API:
        "WHAT: <qué>. WHEN: <timestamp ISO>. WHY: <evidencia>."

    Límite: 300 caracteres (usamos 295 como techo para tener margen).

    La función garantiza que el resultado nunca supera MESSAGE_MAX_CHARS.
    Si la concatenación supera el límite, trunca el campo WHY (el más variable).

    Ejemplos de mensajes generados:
        "WHAT: Brute-force login detected. WHEN: 2026-04-30T12:15:00Z. WHY: 18 HTTP 401 from IP 203.0.113.45 in 90s."
        "WHAT: Path scanning detected. WHEN: 2026-04-30T12:16:00Z. WHY: GET /phpmyadmin 404 from IP 10.0.0.5."
        "WHAT: High-cost LLM error. WHEN: 2026-04-30T12:17:00Z. WHY: LLM_ERROR cost_usd=3.47 (threshold: 1.0)."
    """
    # Mapeo de alert_type a descripción WHAT legible por humanos
    # (los evaluadores SAP ven este campo en su dashboard)
    what_map = {
        "brute_force":         "Brute-force login detected",
        "path_scan":           "Path scanning detected",
        "security_event":      "SECURITY log event triggered",
        "high_cost_llm_error": "High-cost LLM error detected",
        "slow_llm_response":   "Slow LLM response detected",
        "llm_timeout":         "LLM timeout detected",
        "count_anomaly":       "Count anomaly in log volume",
        "category_anomaly":    "Unseen log category anomaly",
        "unknown_anomaly":     "Unknown anomaly detected",
    }
    what = what_map.get(alert_type, f"Security anomaly: {alert_type}")

    # Normalizar el timestamp WHEN — si trae microsegundos o timezone largo,
    # acortarlo para no desperdiciar caracteres.
    # "2026-04-30T12:15:33.123456+00:00" → "2026-04-30T12:15:33Z"
    when_normalizado = _normalizar_timestamp(when_str)

    # Construir los tres fragmentos
    what_part = f"WHAT: {what}."
    when_part = f"WHEN: {when_normalizado}."
    why_part  = f"WHY: {details}."

    mensaje_completo = f"{what_part} {when_part} {why_part}"

    # Si supera el límite, truncar el WHY (el más variable y más largo)
    if len(mensaje_completo) > MESSAGE_MAX_CHARS:
        # Calcular cuántos chars quedan para el WHY
        prefijo = f"{what_part} {when_part} WHY: "
        chars_disponibles = MESSAGE_MAX_CHARS - len(prefijo) - 1  # -1 por el punto
        if chars_disponibles > 10:
            # Hay espacio para algo útil
            why_truncado = details[:chars_disponibles]
            mensaje_completo = f"{prefijo}{why_truncado}."
        else:
            # Caso extremo: WHAT+WHEN ya son muy largos — enviar solo WHAT+WHEN
            mensaje_completo = f"{what_part} {when_part}"[:MESSAGE_MAX_CHARS]

        logger.warning(
            f"[ALERT] Mensaje truncado a {len(mensaje_completo)} chars "
            f"(original: {len(f'{what_part} {when_part} {why_part}')} chars)"
        )

    return mensaje_completo


def _normalizar_timestamp(ts: str) -> str:
    """
    Convierte un timestamp ISO 8601 a formato compacto para el mensaje de alerta.

    Ejemplos:
        "2026-04-30T12:15:33.123456+00:00"  →  "2026-04-30T12:15:33Z"
        "2026-04-30T12:15:33+00:00"          →  "2026-04-30T12:15:33Z"
        "2026-04-30T12:15:33Z"               →  "2026-04-30T12:15:33Z"

    Si el timestamp no se puede parsear, retorna el original (no falla).
    """
    try:
        # Parsear con datetime y reformatear compacto
        if ts.endswith("Z"):
            ts_parse = ts.replace("Z", "+00:00")
        else:
            ts_parse = ts
        dt = datetime.fromisoformat(ts_parse)
        # Formato compacto: sin microsegundos, con Z para UTC
        return dt.strftime("%Y-%m-%dT%H:%M:%SZ")
    except (ValueError, AttributeError):
        # Si no se puede parsear, retornar el original
        return ts if ts else datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# ---------------------------------------------------------------------------
# POST al endpoint /alert de la API SAP
# ---------------------------------------------------------------------------

def _post_alert(message: str) -> tuple[Optional[int], Optional[str]]:
    """
    Ejecuta POST /alert con reintentos y backoff exponencial.

    Usa las credenciales de config.py (API_BASE_URL + BEARER_TOKEN) — las
    mismas que usa ingest.py para /logs/current.

    Retorna (status_code, error_message):
        Éxito:  (201, None)
        Error:  (último_status_code, descripción_del_error)

    Política de reintentos:
        - 5xx y timeouts: reintentamos (el servidor puede recuperarse)
        - 4xx: NO reintentamos (el mismo payload fallaría de nuevo)
        - Backoff: 1s → 2s entre intentos
    """
    # Leer credenciales de config.py (igual que el resto del proyecto)
    try:
        from app.config import API_BASE_URL, get_headers
        url = f"{API_BASE_URL}/alert"
        headers = get_headers()  # incluye Authorization: Bearer <token>
    except (ImportError, AttributeError) as e:
        # Fallback a variables de entorno directas (para tests sin config.py)
        base_url = os.getenv("API_BASE_URL", "").rstrip("/")
        token = os.getenv("BEARER_TOKEN", "")
        if not base_url or not token:
            return None, f"No se pudo leer API_BASE_URL o BEARER_TOKEN: {e}"
        url = f"{base_url}/alert"
        headers = {"Authorization": f"Bearer {token}"}

    # Añadir Content-Type si no viene en get_headers()
    headers["Content-Type"] = "application/json"

    # Body con el único campo que acepta la API
    body = {"message": message}

    ultimo_status = None
    ultimo_error = None

    for intento in range(1, MAX_RETRIES + 1):
        try:
            logger.debug(
                f"[ALERT] POST intento {intento}/{MAX_RETRIES} → {url} "
                f"({len(message)} chars)"
            )

            response = requests.post(
                url=url,
                json=body,
                headers=headers,
                timeout=REQUEST_TIMEOUT_SECONDS,
            )

            ultimo_status = response.status_code

            # HTTP 201 = éxito (la API de SAP responde 201, no 200)
            if response.status_code == 201:
                logger.debug(f"[ALERT] POST exitoso: HTTP 201 en intento {intento}")
                return 201, None

            # También aceptamos cualquier 2xx por si acaso (tolerancia)
            if 200 <= response.status_code < 300:
                logger.debug(
                    f"[ALERT] POST exitoso: HTTP {response.status_code} en intento {intento}"
                )
                return response.status_code, None

            # 401 — token inválido: NO reintentar, es fatal
            if response.status_code == 401:
                msg = (
                    "HTTP 401 — Bearer token inválido o ausente. "
                    "Verificar BEARER_TOKEN en .env / cf set-env. No se reintenta."
                )
                logger.error(f"[ALERT] {msg}")
                return 401, msg

            # 422 — mensaje inválido (muy largo, o campo ausente): NO reintentar
            if response.status_code == 422:
                msg = (
                    f"HTTP 422 — Validación fallida. "
                    f"Mensaje de {len(message)} chars puede ser muy largo (máx 300). "
                    f"Body de respuesta: {response.text[:200]}"
                )
                logger.error(f"[ALERT] {msg}")
                return 422, msg

            # Cualquier otro 4xx: NO reintentar
            if 400 <= response.status_code < 500:
                msg = (
                    f"HTTP {response.status_code} — Error del cliente. "
                    f"No se reintenta. Body: {response.text[:200]}"
                )
                logger.error(f"[ALERT] {msg}")
                return response.status_code, msg

            # 5xx — error del servidor: SÍ reintentar
            ultimo_error = (
                f"HTTP {response.status_code} (servidor) en intento {intento}. "
                f"Body: {response.text[:200]}"
            )
            logger.warning(f"[ALERT] {ultimo_error}")

        except requests.exceptions.Timeout:
            ultimo_error = (
                f"Timeout ({REQUEST_TIMEOUT_SECONDS}s) en intento {intento}"
            )
            logger.warning(f"[ALERT] {ultimo_error}")

        except requests.exceptions.ConnectionError as e:
            ultimo_error = f"ConnectionError en intento {intento}: {e}"
            logger.warning(f"[ALERT] {ultimo_error}")

        except requests.exceptions.RequestException as e:
            ultimo_error = f"RequestException en intento {intento}: {e}"
            logger.warning(f"[ALERT] {ultimo_error}")

        # Esperar antes del siguiente intento (backoff exponencial)
        if intento < MAX_RETRIES:
            espera = BACKOFF_BASE_SECONDS * (2 ** (intento - 1))  # 1s, 2s
            logger.debug(f"[ALERT] Esperando {espera:.1f}s antes del intento {intento + 1}")
            time.sleep(espera)

    return ultimo_status, f"Falló tras {MAX_RETRIES} intentos. Último: {ultimo_error}"


# ---------------------------------------------------------------------------
# Operaciones en HANA — registro y actualización de alertas
# ---------------------------------------------------------------------------

def _alerta_ya_existe(conn, log_id: str) -> bool:
    """
    Verifica si ya existe una alerta para este log_id en DBADMIN.ALERTS.

    La query usa el índice IDX_ALERT_LOGID para ser eficiente.
    """
    cursor = conn.cursor()
    try:
        cursor.execute(
            "SELECT COUNT(*) FROM DBADMIN.ALERTS WHERE log_id = ?",
            (log_id,)
        )
        return cursor.fetchone()[0] > 0
    finally:
        cursor.close()


def _insertar_alerta_hana(
    conn,
    alert_id: str,
    log_id: str,
    detected_at: str,
    source: str,
    alert_type: str,
    severity: str,
    details: str,
    window_start: Optional[str],
    alerted: int,
) -> None:
    """
    Inserta un registro en DBADMIN.ALERTS.

    Schema de la tabla (creada por hana_client.py):
        alert_id         NVARCHAR(100) PRIMARY KEY
        log_id           NVARCHAR(100)
        detected_at      TIMESTAMP
        detection_source NVARCHAR(20)     ← 'quick_filter' o 'model_ml'
        alert_type       NVARCHAR(100)
        severity         NVARCHAR(10)     ← 'low', 'medium', 'high', 'critical'
        details          NVARCHAR(2000)   ← evidencia completa (no truncada)
        window_start     TIMESTAMP
        alerted          TINYINT DEFAULT 0  ← 0=pendiente, 1=confirmado

    Nota: guardamos el details completo en HANA (hasta 2000 chars) aunque el
    mensaje que va a SAP esté truncado a 300 chars. HANA es el registro forense
    completo; el mensaje de la API es el resumen para el equipo SAP.
    """
    details_hana = details[:2000] if details else ""

    # Normalizar window_start — puede ser string o datetime
    window_start_str = None
    if window_start:
        window_start_str = (
            window_start.isoformat()
            if hasattr(window_start, "isoformat")
            else str(window_start)
        )

    sql = """
        INSERT INTO DBADMIN.ALERTS
            (alert_id, log_id, detected_at, detection_source,
             alert_type, severity, details, window_start, alerted)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
    """

    cursor = conn.cursor()
    try:
        cursor.execute(sql, (
            alert_id,
            log_id,
            detected_at,
            source,
            alert_type,
            severity,
            details_hana,
            window_start_str,  # None → NULL en HANA
            alerted,
        ))
        conn.commit()
    finally:
        cursor.close()


def _marcar_alerta_enviada(conn, alert_id: str) -> None:
    """
    Actualiza alerted=1 en DBADMIN.ALERTS.

    Confirma que el POST a /alert fue exitoso. El campo alerted distingue
    "detectada" (0) de "confirmada por SAP" (1) en el reporte forense.
    """
    cursor = conn.cursor()
    try:
        cursor.execute(
            "UPDATE DBADMIN.ALERTS SET alerted = 1 WHERE alert_id = ?",
            (alert_id,)
        )
        conn.commit()
    finally:
        cursor.close()


# ---------------------------------------------------------------------------
# Utilidades internas
# ---------------------------------------------------------------------------

def _validar_inputs(
    alert_type: str, severity: str, details: str, log_id: str
) -> Optional[str]:
    """
    Valida inputs antes de procesar.
    Retorna None si todo está OK, o string con mensaje de error.
    """
    if not alert_type or not isinstance(alert_type, str):
        return "alert_type no puede ser vacío"
    if severity not in SEVERIDADES_VALIDAS:
        return (
            f"severity='{severity}' inválida. "
            f"Válidas: {sorted(SEVERIDADES_VALIDAS)}"
        )
    if not details or not isinstance(details, str):
        return "details no puede ser vacío"
    if not log_id or not isinstance(log_id, str):
        return "log_id no puede ser vacío"
    return None


def _elapsed_ms(t_inicio: float) -> float:
    """Milisegundos transcurridos desde t_inicio (time.monotonic())."""
    return (time.monotonic() - t_inicio) * 1000


# ---------------------------------------------------------------------------
# Script de prueba — ejecutar directamente para validar la integración
# ---------------------------------------------------------------------------

def probar_alerting() -> None:
    """
    Prueba el módulo contra la API SAP real.

    Uso:
        python app/alerting.py

    Qué hace:
    1. Construye mensajes de prueba con _construir_mensaje() y muestra resultado.
    2. Envía una alerta real al endpoint POST /alert de la API SAP.
    3. Muestra la respuesta del servidor.

    Prerequisito: BEARER_TOKEN y API_BASE_URL deben estar en .env.
    Esta prueba SÍ consume una alerta real en el sistema SAP.
    """
    logging.basicConfig(
        level=logging.DEBUG,
        format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
        datefmt="%H:%M:%S",
    )

    print("\n" + "=" * 60)
    print("PRUEBA DE ALERTING — app/alerting.py")
    print("=" * 60)

    # --- Caso A: verificar construcción de mensajes ---
    print("\n--- Construcción de mensajes WHAT/WHEN/WHY ---")

    casos = [
        {
            "alert_type": "brute_force",
            "when_str":   "2026-04-30T12:15:33.123456+00:00",
            "details":    "18 intentos HTTP 401 desde IP 203.0.113.45 en 90 segundos",
        },
        {
            "alert_type": "path_scan",
            "when_str":   "2026-04-30T12:16:00Z",
            "details":    "GET /phpmyadmin 404 y GET /cgi-bin 404 desde IP 10.0.0.5",
        },
        {
            "alert_type": "high_cost_llm_error",
            "when_str":   "2026-04-30T12:17:00+00:00",
            "details":    "LLM_ERROR con llm_cost_usd=3.47 (umbral: 1.0) en modelo gpt-4",
        },
    ]

    for c in casos:
        msg = _construir_mensaje(
            alert_type=c["alert_type"],
            when_str=c["when_str"],
            details=c["details"],
        )
        print(f"\n  Tipo:    {c['alert_type']}")
        print(f"  Mensaje: {msg}")
        print(f"  Chars:   {len(msg)} / 300")
        assert len(msg) <= 300, f"ERROR: mensaje supera 300 chars ({len(msg)})"

    print("\n✅ Todos los mensajes dentro del límite de 300 chars")

    # --- Caso B: enviar una alerta real (sin HANA) ---
    print("\n--- Envío real a POST /alert (sin HANA) ---")
    print("AVISO: Esta prueba envía una alerta real a la API SAP.")
    confirmar = input("¿Continuar? (s/N): ").strip().lower()

    if confirmar != "s":
        print("Prueba cancelada.")
    else:
        result = enviar_alerta(
            alert_type   = "brute_force",
            severity     = "high",
            details      = "18 intentos HTTP 401 desde IP 203.0.113.45 en 90 seg — PRUEBA",
            log_id       = f"test-{uuid.uuid4()}",   # ID único para no bloquear por duplicados
            event_time   = datetime.now(timezone.utc).isoformat(),
            conn         = None,   # sin HANA en la prueba
        )

        print(f"\n  ok={result.ok}")
        print(f"  alert_id={result.alert_id}")
        print(f"  status_code={result.status_code}")
        print(f"  elapsed={result.elapsed_ms:.0f}ms")
        if result.error:
            print(f"  error={result.error}")
        if result.ok:
            print("\n✅ Alerta enviada exitosamente a SAP")
        else:
            print("\n❌ Falló el envío — revisar logs arriba")

    # --- Caso C: inputs inválidos ---
    print("\n--- Validación de inputs incorrectos ---")
    r_invalid = enviar_alerta(
        alert_type="brute_force",
        severity="URGENTE",     # inválida
        details="test",
        log_id="test-invalid",
        conn=None,
    )
    print(f"  Severidad inválida → ok={r_invalid.ok} | error={r_invalid.error}")
    assert not r_invalid.ok
    print("✅ Validación funciona correctamente")

    print("\n" + "=" * 60)
    print("Prueba completada.")
    print("=" * 60 + "\n")


if __name__ == "__main__":
    probar_alerting()
