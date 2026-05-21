"""
dashboard/agent.py
==================
SOC Agent — GPT-4o + ALERT_PLAYBOOK + lógica de sugerencia de gráficas.

Responsabilidad:
  - Consultar hana_dashboard.get_agent_context() para obtener contexto live.
  - Construir el system prompt con playbook si viene del botón "¿Qué hago?".
  - Llamar a GPT-4o y retornar respuesta estructurada.
  - Parsear [CHART:key] del texto del agente y validar contra CHART_CATALOG.

Nunca escribe SQL directamente. Nunca importa streamlit.
Owner: Dev 1
"""

import os
import re
import logging

import sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from openai import OpenAI

from hana_dashboard import get_agent_context
from charts import CHART_CATALOG

logger = logging.getLogger(__name__)


# ══════════════════════════════════════════════════════════════════════════════
# CONSTANTES
# ══════════════════════════════════════════════════════════════════════════════

ALERT_PLAYBOOK: dict[str, list[str]] = {
    "brute_force": [
        "Bloquear la IP en el firewall a nivel de red de forma inmediata.",
        "Verificar en RAW_LOGS_SISTEMA si hubo login exitoso después de los intentos fallidos.",
        "Revisar si la misma IP tiene actividad en otras aplicaciones del entorno.",
        "Considerar implementar rate limiting en el endpoint afectado.",
        "Documentar la IP y el rango horario para el reporte forense.",
    ],
    "path_scan": [
        "Identificar las rutas solicitadas — priorizar si incluyen /admin, /login, /api/keys.",
        "Bloquear la IP si el patrón de escaneo continúa en la siguiente ventana.",
        "Revisar si alguna de las rutas escaneadas retornó 200 (acceso exitoso).",
        "Actualizar reglas WAF para las rutas más frecuentemente escaneadas.",
    ],
    "security_event": [
        "Revisar el campo DETAILS del log SECURITY para identificar el evento específico.",
        "Correlacionar con actividad de la misma aplicación en la misma ventana temporal.",
        "Escalar si el evento involucra credenciales de administrador o acceso a configuración.",
        "Verificar si el evento es recurrente o aislado comparando con ventanas anteriores.",
    ],
    "high_cost_llm_error": [
        "Identificar el LLM_PROMPT_ID asociado para localizar la fuente del error.",
        "Verificar si el error es recurrente del mismo modelo o proveedor.",
        "Revisar si hay un patrón de prompts que sistemáticamente dispara errores costosos.",
        "Considerar circuit breaker temporal si el costo acumulado supera el threshold diario.",
    ],
    "ml_sistema_anomaly": [
        "Revisar el log específico en RAW_LOGS_SISTEMA: HTTP_STATUS, APPLICATION, hora.",
        "Comparar con el patrón histórico normal de esa aplicación (¿falla habitualmente?).",
        "Si es HTTP 500 en aplicación crítica fuera de horario laboral — escalar de inmediato.",
        "Verificar si la anomalía coincide con un spike de volumen en la gráfica de sistema.",
    ],
    "ml_llm_anomaly": [
        "Revisar LLM_COST_USD y LLM_RESPONSE_TIME del log asociado.",
        "Comparar con el promedio de la ventana — ¿qué tan fuera de rango está?",
        "Identificar el modelo (LLM_MODEL_ID) y proveedor involucrado.",
        "Verificar si el LLM_STATUS indica un error o si la llamada fue exitosa con costo anómalo.",
    ],
    "ml_ip_anomaly": [
        "Revisar el comportamiento completo de la IP en RAW_LOGS_SISTEMA.",
        "Analizar el ratio de errores 4xx — ¿está por encima del 70%?",
        "Correlacionar con alertas activas de brute_force o path_scan de la misma IP.",
        "Si la IP tiene ml_ip_anomaly + brute_force activos simultáneamente — bloqueo inmediato.",
    ],
}

# Template base del system prompt.
# Placeholders: {chart_catalog_with_tokens}, {contexto_live}
SYSTEM_PROMPT_BASE = """\
Eres el agente analítico del SOC (Security Operations Center) del sistema SAP AI Security \
Anomaly Detection — hackathon TEC de Monterrey × SAP.

Tu función es ayudar a los analistas de seguridad a entender las alertas detectadas, \
interpretar anomalías del modelo ML, y recomendar acciones concretas.

═══ ARQUITECTURA DEL SISTEMA ═══
• quick_filter (ciclo ~10 min) — detección determinística:
    - security_event      : LOG_TYPE == 'SECURITY'
    - brute_force         : ≥5 errores 401/403 misma IP en el ciclo
    - path_scan           : ≥3 requests 404 a rutas sospechosas misma IP
    - high_cost_llm_error : LLM_ERROR con costo > $1.00 USD

• model_ml (ciclo ~28 min) — tres modelos scikit-learn:
    - IF_sistema  : IsolationForest sobre logs sistema (status, tipo, app, hora_UTC)
    - IF_llm      : IsolationForest sobre logs LLM (costo, response_time, tokens)
    - LOF_ip      : LocalOutlierFactor sobre comportamiento de IPs (event_count, ratio_4xx/5xx)

• Supresión de duplicados: si una IP ya fue alertada por quick_filter, la alerta ML se
  registra como SUPPRESSED_BY='quick_filter' y NO se re-envía a SAP.

• Threshold MAD activo (≥400 ventanas): median(scores) - 3.5 × (MAD/0.6745).

═══ DEFINICIÓN DE ANOMALÍA ═══
IsolationForest: puntos fáciles de aislar → score cercano a -1 → anomalía.
  Ejemplo: HTTP 500 en app que nunca falla a las 3am = combinación atípica.
LOF: puntos en zonas de baja densidad respecto a sus vecinos.
  Ejemplo: IP con 800 requests y 90%% de errores 4xx = anomalía vs cluster normal.

═══ SEVERIDADES ═══
  high   → requiere acción inmediata
  medium → investigar en las próximas horas
  low    → monitorear, no urgente

═══ REGLA CRÍTICA PARA GRÁFICAS — LEE CON ATENCIÓN ═══
El sistema puede renderizar gráficas automáticamente SI y SOLO SI incluyes el token
correcto en tu respuesta. El formato es ESTRICTAMENTE:

    [CHART:nombre_clave]

REGLAS OBLIGATORIAS:
1. El token DEBE incluir el prefijo CHART: dentro de los corchetes.
2. NUNCA escribas solo [nombre_clave] sin el prefijo — el sistema no lo detectará.
3. NUNCA escribas "ver gráfica X" ni "consulta la gráfica" sin el token — no funciona.
4. Incluye MÁXIMO UN token por respuesta.
5. Coloca el token al FINAL de tu respuesta, en su propia línea.

Gráficas disponibles con sus tokens exactos listos para copiar:
{chart_catalog_with_tokens}

Cuando el analista pida "muéstrame", "grafica", "plotea", "dame la gráfica" o similar,
DEBES incluir el token correspondiente. No es opcional — es la única forma de mostrarla.

═══ CONTEXTO LIVE — SAP HANA (datos consultados ahora mismo) ═══
{contexto_live}

═══ INSTRUCCIONES DE RESPUESTA ═══
• Profundidad técnica primero: datos numéricos reales del contexto HANA, nombres de
  tablas y columnas relevantes, lógica del modelo cuando sea pertinente.
• Acciones específicas: "bloquear IP 192.168.x.x", "revisar ruta /admin/login", no generalidades.
• Si no tienes suficiente información, dilo directamente.
• No repitas el contexto HANA textualmente — analízalo e interpreta.
• Termina SIEMPRE con una sección de cierre clara y ejecutable con este formato exacto:

  ─── BOTTOM LINE ───
  • Situación: [una línea — qué está pasando]
  • Acción inmediata: [una línea — qué hacer ahora mismo]
  • Próximo paso: [una línea — qué monitorear o validar después]\
"""


# ══════════════════════════════════════════════════════════════════════════════
# FUNCIONES PRIVADAS
# ══════════════════════════════════════════════════════════════════════════════

def _parse_chart_key(response_text: str) -> tuple[str, str | None]:
    """
    Extrae el token de gráfica de la respuesta del LLM.

    Acepta dos formatos para máxima robustez ante variaciones de GPT:
      - Formato correcto:  [CHART:brute_force_por_ip]
      - Formato incorrecto pero recuperable: [brute_force_por_ip]

    En ambos casos elimina el token del texto visible y retorna la clave limpia.
    Si la clave no existe en CHART_CATALOG, elimina el token pero retorna None.

    Args:
        response_text: texto completo de la respuesta GPT-4o.

    Returns:
        tuple(texto_limpio: str, chart_key: str | None)
    """
    # Patrón 1 — formato correcto: [CHART:nombre_clave]
    pattern_correct   = r'\[CHART:([a-z_]+)\]'
    # Patrón 2 — formato sin prefijo: [nombre_clave] donde nombre_clave está en catálogo
    # Se construye dinámicamente con las claves conocidas para evitar falsos positivos
    known_keys_pattern = '|'.join(re.escape(k) for k in CHART_CATALOG.keys())
    pattern_fallback  = rf'\[({known_keys_pattern})\]'

    chart_key = None

    # Intentar formato correcto primero
    match = re.search(pattern_correct, response_text)
    if match:
        chart_key = match.group(1)
        clean_text = re.sub(pattern_correct, '', response_text)
    else:
        # Intentar formato sin prefijo (fallback para cuando GPT omite "CHART:")
        match = re.search(pattern_fallback, response_text)
        if match:
            chart_key = match.group(1)
            logger.warning(
                "_parse_chart_key: GPT usó formato sin prefijo '[%s]' en lugar de "
                "'[CHART:%s]'. Recuperado automáticamente.",
                chart_key, chart_key,
            )
            clean_text = re.sub(pattern_fallback, '', response_text)
        else:
            return response_text, None

    # Limpiar texto: colapsar múltiples líneas vacías
    clean_text = re.sub(r'\n{3,}', '\n\n', clean_text).strip()

    # Validar que la clave exista en el catálogo
    if chart_key not in CHART_CATALOG:
        logger.warning(
            "_parse_chart_key: clave '%s' no existe en CHART_CATALOG. "
            "Claves válidas: %s. Ignorando.",
            chart_key, list(CHART_CATALOG.keys()),
        )
        return clean_text, None

    return clean_text, chart_key


def _build_system_prompt(
    live_context: str,
    playbook_actions: list[str] | None = None,
    alert_data: dict | None = None,
) -> str:
    """
    Construye el system prompt completo para la llamada a GPT-4o.

    Para chat libre: inyecta contexto HANA + catálogo de gráficas con tokens exactos.
    Para botón "¿Qué hago?": añade sección con alerta específica + playbook personalizado.

    Args:
        live_context:     string de get_agent_context() con datos frescos de HANA.
        playbook_actions: lista de acciones de ALERT_PLAYBOOK. None si chat libre.
        alert_data:       dict{alert_type, severity, detail, client_ip}. None si libre.

    Returns:
        str con el system prompt completo listo para enviar a GPT-4o.
    """
    # Catálogo con el token exacto listo para usar — GPT puede copiarlo literalmente
    chart_catalog_str = "\n".join(
        f"  [CHART:{key}]  →  {desc}"
        for key, desc in CHART_CATALOG.items()
    )

    prompt = SYSTEM_PROMPT_BASE.format(
        chart_catalog_with_tokens=chart_catalog_str,
        contexto_live=live_context,
    )

    # Sección adicional solo cuando viene del botón "¿Qué hago? →"
    if playbook_actions and alert_data:
        ip_display = (
            alert_data.get("client_ip")
            or "N/A — alerta ML sin IP de origen específica"
        )
        acciones_str = "\n".join(
            f"  {i+1}. {accion}"
            for i, accion in enumerate(playbook_actions)
        )
        prompt += f"""

═══════════════════════════════════════════════════════════════════════════════
ALERTA CONCRETA BAJO ANÁLISIS (el analista hizo click en "¿Qué hago? →"):

  Tipo de alerta : {alert_data.get('alert_type', 'desconocido')}
  Severidad      : {alert_data.get('severity', 'desconocida')}
  Detalle        : {alert_data.get('detail', 'sin detalle disponible')}
  IP de origen   : {ip_display}

ACCIONES BASE DEL PLAYBOOK PARA ESTE TIPO DE ALERTA:
{acciones_str}

INSTRUCCIÓN ESPECIAL:
Personaliza cada acción del playbook con los datos reales de esta alerta concreta
(usa la IP específica, el detalle del incidente, los datos del contexto HANA).
Sé operacional y directo — el analista necesita saber exactamente qué hacer ahora.
Termina con la sección ─── BOTTOM LINE ─── como siempre.
═══════════════════════════════════════════════════════════════════════════════"""

    return prompt


# ══════════════════════════════════════════════════════════════════════════════
# FUNCIÓN PÚBLICA PRINCIPAL
# ══════════════════════════════════════════════════════════════════════════════

def get_agent_response(
    user_question: str,
    conn,
    conversation_history: list[dict],
    alert_context: dict | None = None,
) -> dict:
    """
    Función principal del agente SOC. Orquesta: HANA → prompt → GPT-4o → parse.

    Args:
        user_question:
            Texto del analista, o pregunta pre-formada generada por el botón.

        conn:
            Conexión HANA activa de hana_dashboard.get_connection().

        conversation_history:
            Lista de mensajes anteriores [{"role": "user"|"assistant", "content": str}].
            NO incluye el mensaje actual. Pasa [] para primera pregunta.

        alert_context:
            Dict con datos de la alerta si viene del botón "¿Qué hago? →".
            Keys esperadas: alert_type, severity, detail, client_ip (puede ser None).
            Pasa None para chat libre.

    Returns:
        dict con keys garantizadas (nunca KeyError):
            text (str)          : respuesta narrativa limpia, sin token [CHART:...].
            chart_key (str|None): clave del catálogo si GPT sugirió gráfica, else None.
            error (str|None)    : mensaje legible si algo falló, None si ok.
    """
    # ── Paso 1: Contexto live de HANA ─────────────────────────────────────────
    try:
        live_context = get_agent_context(conn)
    except Exception as exc:
        logger.error("get_agent_context() lanzó excepción inesperada: %s", exc)
        live_context = f"[Error al consultar HANA: {exc}]"

    # ── Paso 2: Resolver playbook si viene del botón ──────────────────────────
    playbook_actions: list[str] | None = None
    if alert_context:
        alert_type = alert_context.get("alert_type", "")
        playbook_actions = ALERT_PLAYBOOK.get(alert_type)
        if playbook_actions is None:
            logger.warning(
                "alert_type '%s' no tiene playbook definido. "
                "El agente responderá sin acciones base.",
                alert_type,
            )

    # ── Paso 3: Construir system prompt ───────────────────────────────────────
    system_prompt = _build_system_prompt(
        live_context=live_context,
        playbook_actions=playbook_actions,
        alert_data=alert_context,
    )

    # ── Paso 4: Llamar a GPT-4o ───────────────────────────────────────────────
    try:
        client = OpenAI(api_key=os.environ["OPENAI_API_KEY"])

        api_messages = [
            {"role": "system", "content": system_prompt},
            *conversation_history,
            {"role": "user",   "content": user_question},
        ]

        response = client.chat.completions.create(
            model="gpt-4o",
            max_tokens=1000,
            temperature=0.2,
            messages=api_messages,
        )

        raw_text = response.choices[0].message.content or ""

    except KeyError:
        err_msg = "Variable de entorno OPENAI_API_KEY no encontrada. Agrégala al .env."
        logger.error(err_msg)
        return {"text": err_msg, "chart_key": None, "error": err_msg}

    except Exception as exc:
        err_msg = f"Error al contactar GPT-4o: {exc}"
        logger.error(err_msg)
        return {
            "text": "No se pudo contactar al agente en este momento. Intenta de nuevo.",
            "chart_key": None,
            "error": err_msg,
        }

    # ── Paso 5: Parsear chart_key del texto ───────────────────────────────────
    clean_text, chart_key = _parse_chart_key(raw_text)

    return {
        "text":      clean_text,
        "chart_key": chart_key,
        "error":     None,
    }
