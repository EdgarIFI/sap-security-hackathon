"""
dashboard/app_dashboard.py
===========================
Entry point de la aplicación Streamlit — SOC Dashboard completo.

Estructura:
  Tab 1 — Overview:  KPIs de seguridad, timeline de actividad, tabla de alertas críticas.
  Tab 2 — SOC Agent: chat con GPT-4o + panel de gráfica dinámica.

Principios:
  - Nunca escribe SQL directamente. Todo SQL está en hana_dashboard.py.
  - Slicers siempre filtran DataFrames en Python — sin queries adicionales a HANA.
  - Fallo gracioso: si HANA o la API de GPT fallan, la app muestra el error sin crashear.
  - Una sola llamada a get_connection() por sesión (singleton via @st.cache_resource).

Owner: Dev 1
"""

import sys
import os

from dotenv import load_dotenv
load_dotenv()  # carga .env para desarrollo local (no-op en CF donde las vars vienen del entorno)

# ── sys.path: permite importar módulos hermanos desde cualquier CWD.
#    Añade el directorio de este archivo (dashboard/) antes de cualquier import.
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from datetime import datetime, timezone

import streamlit as st
import streamlit.components.v1 as components

from hana_dashboard import (
    get_connection,
    get_kpis,
    get_activity_timeline,
    get_critical_alerts,
    get_chart_data,
)
from charts import build_chart, build_activity_chart, CHART_CATALOG
import agent


# ══════════════════════════════════════════════════════════════════════════════
# CONFIGURACIÓN DE PÁGINA — debe ser el primer comando Streamlit
# ══════════════════════════════════════════════════════════════════════════════

st.set_page_config(
    page_title="SAP AI SOC Dashboard",
    page_icon="🛡️",
    layout="wide",
    initial_sidebar_state="collapsed",
)


# ══════════════════════════════════════════════════════════════════════════════
# SESSION STATE — inicialización de claves
# ══════════════════════════════════════════════════════════════════════════════


# ── CSS global — mejoras de presentación ─────────────────────────────────────
st.markdown("""
<style>
/* Quitar padding excesivo del top */
.block-container { padding-top: 1.5rem !important; }

/* Cards de métricas más definidos */
[data-testid="metric-container"] {
    background: #1A1A2E;
    border: 1px solid #0066CC33;
    border-radius: 8px;
    padding: 1rem 1.2rem;
}

/* Botón refresh más compacto */
[data-testid="stButton"] button {
    border-radius: 6px;
}

/* Chat input styling */
[data-testid="stChatInput"] {
    border-radius: 12px;
}

/* Tabla de alertas — filas alternadas */
[data-testid="stHorizontalBlock"]:nth-child(even) {
    background: rgba(255,255,255,0.02);
    border-radius: 4px;
}

/* Panel derecho — borde sutil */
.panel-chart-container {
    border-left: 1px solid #0066CC22;
    padding-left: 1rem;
}

/* Suggestion buttons más compactos */
.stButton > button[kind="secondary"] {
    font-size: 0.8rem;
    padding: 0.3rem 0.6rem;
}
</style>
""", unsafe_allow_html=True)

def _init_session_state() -> None:
    """
    Inicializa todas las claves de session_state con sus valores por defecto.
    Solo setea las claves que aún no existen (idempotente en cada rerun).
    """
    defaults: dict = {
        # Historial de mensajes del chat. Formato: [{role, content, chart_key?, chart_figure?}]
        "messages": [],

        # Gráfica activa en el panel derecho del Tab 2.
        # Formato: {"key": str, "figure": go.Figure} o None.
        "current_chart": None,

        # Timestamp de la última carga de datos.
        "last_refresh": datetime.now(timezone.utc),

        # Controla el tab activo: "overview" | "agent".
        # Seteado a "agent" cuando el analista hace click en "¿Qué hago? →".
        "active_tab": "overview",

        # Contexto de alerta inyectado desde la tabla de alertas.
        # Formato: {alert_type, severity, detail, client_ip} o None.
        "alert_context": None,

        # Pregunta pendiente de procesar (viene de botones, no del chat_input).
        # Se consume una sola vez en render_chat_tab().
        "pending_question": None,
    }
    for key, default_value in defaults.items():
        if key not in st.session_state:
            st.session_state[key] = default_value


# ══════════════════════════════════════════════════════════════════════════════
# COMPONENTE: HEADER
# ══════════════════════════════════════════════════════════════════════════════

def render_header() -> None:
    """
    Título del dashboard + timestamp de última actualización + botón refresh manual.
    El refresh borra el cache de conexión para forzar datos frescos de HANA.
    """
    col_title, col_refresh = st.columns([5, 1])

    with col_title:
        st.markdown("## 🛡️ SAP AI Security Operations Center")
        last_str = st.session_state["last_refresh"].strftime("%Y-%m-%d  %H:%M:%S UTC")
        st.caption(
            f"Datos cargados: {last_str}  ·  "
            f"Pipeline: `sap-ai-soc-papoi` (read-only)  ·  "
            f"TEC de Monterrey × SAP Hackathon 2026"
        )

    with col_refresh:
        st.write("")  # padding vertical
        if st.button("🔄 Refresh", use_container_width=True, key="btn_refresh"):
            st.session_state["last_refresh"] = datetime.now(timezone.utc)
            st.cache_resource.clear()
            st.rerun()


# ══════════════════════════════════════════════════════════════════════════════
# COMPONENTE: KPIs
# ══════════════════════════════════════════════════════════════════════════════

def render_kpis(kpis: dict) -> None:
    """
    Cuatro métricas de estado de seguridad en una fila de columnas.

    Notas de diseño:
    - delta_color='inverse' en Alerts Last Hour: delta positivo = rojo (amenaza escalando),
      negativo = verde (cediendo). Comportamiento inverso al default de Streamlit.
    - Top Threat usa delta_color='off' para mostrar el conteo en gris neutro.
    """
    col1, col2, col3, col4 = st.columns(4)

    # KPI 1 — Threat Level
    threat_icon = {"NORMAL": "🟢", "ELEVATED": "🟡", "CRITICAL": "🔴"}.get(
        kpis["threat_level"], "⚪"
    )
    threat_colors = {"NORMAL": "#00C851", "ELEVATED": "#FFB300", "CRITICAL": "#FF4444"}
    threat_color  = threat_colors.get(kpis["threat_level"], "#888888")
    threat_html = (
        f"**Threat Level**<br>"
        f"<span style='font-size:1.5rem; color:{threat_color}; font-weight:700;'>"
        f"{threat_icon} {kpis['threat_level']}</span>"
    )
    col1.markdown(threat_html, unsafe_allow_html=True)

    # KPI 2 — Alerts Last Hour (con delta porcentual)
    delta_pct = kpis.get("alerts_delta_pct")
    delta_str = f"{delta_pct:+.1f}%" if delta_pct is not None else None
    col2.metric(
        label="Alerts Last Hour",
        value=kpis["alerts_last_hour"],
        delta=delta_str,
        delta_color="inverse",  # positivo = rojo (más alertas = peor)
    )

    # KPI 3 — Attack Diversity
    div = kpis["attack_diversity"]
    col3.metric(
        label="Attack Diversity",
        value=f"{div['active']} of {div['total']} types",
    )

    # KPI 4 — Top Threat
    top = kpis.get("top_threat")
    if top:
        col4.metric(
            label="Top Threat Now",
            value=top["alert_type"].replace("_", " "),
            delta=f"{top['count']} alerts",
            delta_color="off",
        )
    else:
        col4.metric(label="Top Threat Now", value="— quiet")


# ══════════════════════════════════════════════════════════════════════════════
# COMPONENTE: ACTIVITY TIMELINE (Tab 1)
# ══════════════════════════════════════════════════════════════════════════════

def render_activity_chart(conn) -> None:
    """
    Gráfica de actividad de sistema con marcas de alertas superpuestas.

    Slicers:
    - hora_range: filtro de ventana horaria (0-23 UTC). Afecta window_start.dt.hour.
    - severity_filter: qué marcas de alerta mostrar. Pone a 0 las no seleccionadas.

    Ambos filtros operan sobre el DataFrame en memoria. Sin queries adicionales a HANA.
    """
    st.markdown("#### 📈 System Activity Timeline — últimas 48h")

    try:
        df = get_activity_timeline(conn)
    except Exception as exc:
        st.error(f"No se pudieron cargar datos de actividad: {exc}")
        return

    if df.empty:
        st.info("Sin datos de actividad en las últimas 48 horas.")
        return

    # Slicers
    s_col1, s_col2 = st.columns([1, 2])
    with s_col1:
        hora_range = st.slider(
            "Rango de horas (UTC)",
            min_value=0, max_value=23, value=(0, 23),
            key="timeline_hora",
        )
    with s_col2:
        severity_filter = st.multiselect(
            "Marcas de alerta visibles",
            options=["high", "medium", "low"],
            default=["high", "medium", "low"],
            key="timeline_severity",
        )

    # Filtrado en Python
    df_f = df[df["window_start"].dt.hour.between(hora_range[0], hora_range[1])].copy()
    for sev in ["high", "medium", "low"]:
        if sev not in severity_filter:
            df_f[f"alerts_{sev}"] = 0

    try:
        fig = build_activity_chart(df_f)
        st.plotly_chart(fig, use_container_width=True, key="activity_timeline_chart")
    except Exception as exc:
        st.error(f"Error al renderizar la gráfica de actividad: {exc}")


# ══════════════════════════════════════════════════════════════════════════════
# COMPONENTE: TABLA DE ALERTAS CRÍTICAS (Tab 1)
# ══════════════════════════════════════════════════════════════════════════════

def render_critical_alerts_table(conn) -> None:
    """
    Tabla de alertas críticas con botón "¿Qué hago? →" por fila.

    Flujo del botón:
    1. Lee los datos de la fila (alert_type, severity, detail, client_ip).
    2. Guarda alert_context y la pregunta pre-formada en session_state.
    3. Setea active_tab = "agent" para que el JS intente cambiar de tab.
    4. Llama st.rerun() — el Tab 2 procesará la pending_question en el próximo render.

    Slicers:
    - type_filter: multiselect de tipos de alerta.
    - only_high:   toggle para mostrar solo severity=high.
    Ambos filtran el DataFrame en Python.
    """
    st.markdown("#### 🚨 Critical Alerts — últimas 24h")

    try:
        df = get_critical_alerts(conn, hours=24, limit=20)
    except Exception as exc:
        st.error(f"No se pudieron cargar las alertas: {exc}")
        return

    if df.empty:
        st.success("✅ Sin alertas activas en las últimas 24 horas.")
        return

    # Slicers
    s_col1, s_col2 = st.columns([3, 1])
    with s_col1:
        all_types = sorted(df["alert_type"].unique().tolist())
        type_filter = st.multiselect(
            "Filtrar por tipo de alerta",
            options=all_types,
            default=all_types,
            key="alerts_type_filter",
        )
    with s_col2:
        only_high = st.toggle("Solo HIGH severity", key="alerts_only_high")

    # Filtrado en Python
    df_f = df[df["alert_type"].isin(type_filter)].copy()
    if only_high:
        df_f = df_f[df_f["severity"] == "high"]

    if df_f.empty:
        st.info("Sin alertas que coincidan con los filtros actuales.")
        return

    # Cabecera de la tabla
    h_cols = st.columns([1, 2, 1, 1, 3, 1])
    for col, label in zip(h_cols, ["Severity", "Tipo", "Fuente", "IP", "Detalle", "Acción"]):
        col.markdown(f"**{label}**")
    st.divider()

    SEV_ICON = {"high": "🔴", "medium": "🟡", "low": "🟢"}

    for _, row in df_f.iterrows():
        c_sev, c_type, c_src, c_ip, c_det, c_btn = st.columns([1, 2, 1, 1, 3, 1])

        # Severidad
        icon = SEV_ICON.get(str(row["severity"]), "⚪")
        c_sev.write(f"{icon} {str(row['severity']).upper()}")

        # Tipo (guiones → espacios para legibilidad)
        c_type.write(str(row["alert_type"]).replace("_", " "))

        # Fuente
        c_src.caption(str(row["detection_source"]))

        # IP — None para alertas ML
        ip_val = row["client_ip"]
        c_ip.code(str(ip_val) if ip_val else "—")

        # Detalle truncado
        detail_raw = str(row["detail"]) if row["detail"] else ""
        c_det.write(detail_raw[:85] + "…" if len(detail_raw) > 85 else detail_raw)

        # Botón "¿Qué hago? →" — key única por alert_id para evitar conflictos
        btn_key = f"qh_{row['alert_id']}"
        if c_btn.button("¿Qué hago? →", key=btn_key, use_container_width=True):
            st.session_state["alert_context"] = {
                "alert_type": row["alert_type"],
                "severity":   row["severity"],
                "detail":     row["detail"],
                "client_ip":  row["client_ip"],  # puede ser None — agent lo maneja
            }
            st.session_state["active_tab"] = "agent"
            st.session_state["pending_question"] = (
                f"El analista SOC solicita orientación sobre esta alerta activa:\n"
                f"  • Tipo:      {row['alert_type']}\n"
                f"  • Severidad: {row['severity']}\n"
                f"  • Detalle:   {row['detail']}\n"
                f"  • IP origen: {row['client_ip'] or 'N/A (alerta ML)'}\n\n"
                f"¿Qué acciones concretas debo tomar?"
            )
            st.rerun()


# ══════════════════════════════════════════════════════════════════════════════
# COMPONENTE: SUGGESTION BUTTONS (Tab 2)
# ══════════════════════════════════════════════════════════════════════════════

def render_suggestion_buttons() -> None:
    """
    Chips de preguntas frecuentes. Al hacer click, actúan como input de chat
    seteando pending_question y relanzando el render.

    Uso de key única por sugerencia para evitar conflictos entre reruns.
    """
    SUGGESTIONS = [
        ("📋 Resumen 24h",   "Dame un resumen completo de las alertas de las últimas 24 horas."),
        ("🌐 IPs sospechosas", "¿Cuáles son las IPs más sospechosas en este momento y qué sugieren?"),
        ("📈 Actividad LLM", "¿Hay algo preocupante en la actividad LLM reciente?"),
        ("🔍 Brute force",   "Muéstrame los detalles del patrón de brute force activo."),
        ("❓ ML vs reglas",  "Explícame la diferencia entre alertas de quick_filter y de model_ml."),
    ]

    st.caption("Preguntas rápidas:")
    cols = st.columns(len(SUGGESTIONS))
    for col, (label, question) in zip(cols, SUGGESTIONS):
        if col.button(label, key=f"sug_{label[:8]}", use_container_width=True):
            st.session_state["pending_question"] = question
            st.rerun()


# ══════════════════════════════════════════════════════════════════════════════
# HELPER: PROCESAR UNA PREGUNTA (Tab 2)
# ══════════════════════════════════════════════════════════════════════════════

def _process_question(question: str, conn, alert_context: dict | None) -> None:
    """
    Orquesta el ciclo completo de una pregunta al agente:
      1. Mostrar el mensaje del usuario en el chat.
      2. Añadirlo al historial de session_state.
      3. Llamar a agent.get_agent_response() con contexto HANA live.
      4. Mostrar respuesta + gráfica opcional en el chat.
      5. Guardar la respuesta (con referencias a gráfica) en el historial.
      6. Llamar st.rerun() para re-renderizar el historial limpio.

    La pending_question DEBE estar limpiada (a None) ANTES de llamar esta función
    para evitar loops de procesamiento infinito.

    Args:
        question:      texto de la pregunta a procesar.
        conn:          conexión HANA activa.
        alert_context: dict de la alerta o None si viene del chat libre.
    """
    # Mostrar mensaje del usuario en el chat
    with st.chat_message("user"):
        st.write(question)

    # Añadir al historial — usamos solo role+content para el contexto del LLM
    st.session_state["messages"].append({
        "role":    "user",
        "content": question,
    })

    # Construir historial para el LLM (sin el mensaje actual que acabamos de añadir)
    llm_history = [
        {"role": m["role"], "content": m["content"]}
        for m in st.session_state["messages"][:-1]
    ]

    # Llamar al agente y mostrar respuesta
    chart_figure = None
    with st.chat_message("assistant"):
        with st.spinner("Consultando HANA y analizando con GPT-4o…"):
            result = agent.get_agent_response(
                user_question=question,
                conn=conn,
                conversation_history=llm_history,
                alert_context=alert_context,
            )

        # Mostrar error si lo hay (sin crashear)
        if result["error"]:
            st.warning(f"⚠️ Advertencia: {result['error']}")

        # Mostrar texto de respuesta
        st.write(result["text"])

        # Si el agente sugirió una gráfica, cargarla y renderizarla inline
        if result["chart_key"]:
            try:
                chart_data   = get_chart_data(conn, result["chart_key"])
                chart_figure = build_chart(result["chart_key"], chart_data)
                n_msgs = len(st.session_state["messages"])
                chart_k = result["chart_key"] or "chart"
                st.plotly_chart(
                    chart_figure,
                    use_container_width=True,
                    key=f"chat_inline_{chart_k}_{n_msgs}",
                )

                # Actualizar el panel de gráfica dinámica del lado derecho
                st.session_state["current_chart"] = {
                    "key":    result["chart_key"],
                    "figure": chart_figure,
                }
            except Exception as exc:
                st.caption(f"_(No se pudo cargar la gráfica sugerida: {exc})_")

    # Guardar respuesta en historial (incluyendo referencias a gráfica para re-render)
    st.session_state["messages"].append({
        "role":          "assistant",
        "content":       result["text"],
        "chart_key":     result["chart_key"],
        "chart_figure":  chart_figure,
    })

    # Limpiar alert_context después de usarlo (evita que persista en próximas preguntas)
    if alert_context:
        st.session_state["alert_context"] = None

    # Rerun final: limpia el estado intermedio y re-renderiza el historial completo
    st.rerun()


# ══════════════════════════════════════════════════════════════════════════════
# COMPONENTE: PANEL DE GRÁFICA DINÁMICA (Tab 2, columna derecha)
# ══════════════════════════════════════════════════════════════════════════════

def render_dynamic_chart_panel(conn) -> None:
    """
    Panel derecho del Tab 2. Muestra la gráfica más reciente sugerida por el agente.
    Si no hay gráfica activa, muestra un placeholder con instrucciones.
    """
    current = st.session_state.get("current_chart")

    if current:
        chart_desc = CHART_CATALOG.get(current["key"], current["key"])
        st.markdown(f"#### 📊 {chart_desc}")

        # Recargar la gráfica desde HANA (datos frescos en cada render del panel)
        try:
            fresh_data   = get_chart_data(conn, current["key"])
            fresh_figure = build_chart(current["key"], fresh_data)
            st.plotly_chart(
                fresh_figure,
                use_container_width=True,
                key=f"panel_fresh_{current['key']}",
            )
        except Exception as exc:
            # Fallback: usar la figura guardada en session_state si la recarga falla
            if current.get("figure"):
                st.caption(f"_(Mostrando datos previos — error al recargar: {exc})_")
                st.plotly_chart(
                current["figure"],
                use_container_width=True,
                key=f"panel_fallback_{current['key']}",
            )
            else:
                st.error(f"Error al cargar la gráfica: {exc}")

        # Botón para limpiar el panel
        if st.button("✕ Cerrar gráfica", key="btn_clear_chart"):
            st.session_state["current_chart"] = None
            st.rerun()
    else:
        st.markdown("#### 📊 Gráfica dinámica")
        st.info(
            "Las gráficas aparecen aquí cuando el agente sugiere una análisis visual,\n"
            "o cuando haces click en **¿Qué hago? →** en la tabla de alertas."
        )
        st.caption("También puedes pedirle directamente al agente:")
        st.caption('_"Muéstrame la actividad LLM con anomalías"_')
        st.caption('_"Quiero ver las IPs con más intentos de brute force"_')


# ══════════════════════════════════════════════════════════════════════════════
# COMPONENTE: TAB 2 — SOC AGENT
# ══════════════════════════════════════════════════════════════════════════════

def render_chat_tab(conn) -> None:
    """
    Tab 2 completo: chat con el agente (izquierda) + panel de gráfica dinámica (derecha).

    Layout: columna izquierda 60% (chat) | columna derecha 40% (gráfica).

    Flujo de procesamiento de mensajes:
    1. Renderizar historial completo desde session_state["messages"].
    2. Verificar si hay pending_question (viene de botones/¿Qué hago?).
       Si hay → limpiar pending_question → procesar → st.rerun().
    3. Si no → verificar st.chat_input().
       Si hay input → procesar → st.rerun().
    """
    col_chat, col_chart = st.columns([6, 4])

    # ── Columna izquierda: chat ───────────────────────────────────────────────
    with col_chat:
        st.markdown("#### 🤖 SOC Agent — GPT-4o con contexto live de HANA")

        # Botón "Nueva conversación"
        btn_c, _ = st.columns([2, 5])
        with btn_c:
            if st.button("🗑️ Nueva conversación", key="btn_new_conv", use_container_width=True):
                st.session_state["messages"]       = []
                st.session_state["current_chart"]  = None
                st.session_state["alert_context"]  = None
                st.session_state["pending_question"] = None
                st.rerun()

        # Chips de preguntas frecuentes
        render_suggestion_buttons()
        st.divider()

        # ── Historial de mensajes ─────────────────────────────────────────────
        for msg_idx, msg in enumerate(st.session_state["messages"]):
            with st.chat_message(msg["role"]):
                st.write(msg["content"])
                # Si el mensaje del agente tiene una gráfica asociada, re-renderizarla
                # key único por posición en historial — evita DuplicateElementId
                if msg.get("chart_key") and msg.get("chart_figure"):
                    st.plotly_chart(
                        msg["chart_figure"],
                        use_container_width=True,
                        key=f"history_chart_{msg_idx}_{msg['chart_key']}",
                    )

        # ── Procesar pending_question (botones / ¿Qué hago?) ─────────────────
        # IMPORTANTE: limpiar ANTES de llamar _process_question para evitar loop.
        pending = st.session_state.get("pending_question")
        if pending:
            alert_ctx = st.session_state.get("alert_context")
            st.session_state["pending_question"] = None  # limpiar primero
            _process_question(pending, conn, alert_ctx)
            # _process_question llama st.rerun() — el código después no se ejecuta

        # ── Input libre del chat ──────────────────────────────────────────────
        if prompt := st.chat_input("Pregunta al agente SOC…", key="chat_input_free"):
            _process_question(prompt, conn, alert_context=None)
            # _process_question llama st.rerun() — el código después no se ejecuta

    # ── Columna derecha: gráfica dinámica ─────────────────────────────────────
    with col_chart:
        render_dynamic_chart_panel(conn)


# ══════════════════════════════════════════════════════════════════════════════
# HELPER: INTENTAR CAMBIO PROGRAMÁTICO DE TAB (best-effort)
# ══════════════════════════════════════════════════════════════════════════════

def _attempt_tab_switch_to_agent() -> None:
    """
    Intenta cambiar al Tab 2 (SOC Agent) via JavaScript.
    Streamlit no soporta programmatic tab switching de forma nativa.
    Este método usa una inyección JS que hace click en el segundo botón de tab.

    Best-effort: si el selector DOM cambia en versiones futuras de Streamlit,
    falla silenciosamente. El toast de notificación sirve como fallback.
    """
    js_code = """
    <script>
        (function() {
            // Dar tiempo al DOM de Streamlit para renderizar los tabs
            setTimeout(function() {
                const tabButtons = window.parent.document.querySelectorAll(
                    'button[data-baseweb="tab"]'
                );
                if (tabButtons && tabButtons.length >= 2) {
                    tabButtons[1].click();
                }
            }, 200);
        })();
    </script>
    """
    components.html(js_code, height=0, scrolling=False)


# ══════════════════════════════════════════════════════════════════════════════
# ENTRY POINT — main()
# ══════════════════════════════════════════════════════════════════════════════

def main() -> None:
    """
    Punto de entrada principal. Ejecuta el ciclo completo de render de Streamlit.

    Orden de ejecución en cada rerun:
    1. Inicializar session_state.
    2. Renderizar header.
    3. Obtener conexión HANA singleton.
    4. Construir tabs y renderizar cada uno.
    5. Si active_tab == "agent" (viene del botón ¿Qué hago?), intentar JS tab switch.
    """
    _init_session_state()

    # Conexión HANA singleton — reutilizada por todos los componentes
    try:
        conn = get_connection()
    except Exception as exc:
        st.error(
            f"❌ No se pudo conectar a SAP HANA Cloud: {exc}\n\n"
            "Verifica que HANA_HOST, HANA_USER y HANA_PASSWORD estén seteadas en el entorno."
        )
        st.stop()

    render_header()
    st.divider()

    # ── Tabs ──────────────────────────────────────────────────────────────────
    tab1, tab2 = st.tabs(["📊 Overview", "🤖 SOC Agent"])

    with tab1:
        try:
            kpis = get_kpis(conn)
            render_kpis(kpis)
        except Exception as exc:
            st.error(f"Error al cargar KPIs: {exc}")

        st.divider()
        render_activity_chart(conn)
        st.divider()
        render_critical_alerts_table(conn)

    with tab2:
        render_chat_tab(conn)

    # ── Cambio de tab programático (después de ¿Qué hago? →) ─────────────────
    # Se intenta solo una vez por rerun cuando active_tab está en "agent".
    # Se resetea a "overview" para no repetir en reruns subsecuentes.
    if st.session_state.get("active_tab") == "agent":
        st.session_state["active_tab"] = "overview"
        st.toast(
            "✅ Análisis enviado → haz click en la pestaña **🤖 SOC Agent**",
            icon="🛡️",
        )
        _attempt_tab_switch_to_agent()


# ── Ejecutar ──────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    main()
