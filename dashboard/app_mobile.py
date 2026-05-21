"""
dashboard/app_mobile.py
=======================
Mobile-optimized SOC Dashboard — QR-accessible.
Single column layout, touch-friendly, no slicers.

Shows:  4 KPIs · Activity chart (compact) · Alert count banner · SOC Agent chat
Omits:  Slicers (bad UX on mobile) · Detailed table · Catalog charts sidebar

Run locally:
    streamlit run dashboard/app_mobile.py --server.port 8502

CF deploy:
    cf push -f dashboard/manifest_mobile.yml
    cf set-env sap-ai-soc-mobile OPENAI_API_KEY [val]
    cf set-env sap-ai-soc-mobile HANA_HOST [val]
    cf set-env sap-ai-soc-mobile HANA_PASSWORD [val]

QR code:
    Generate at https://qr.io or similar pointing to the CF mobile URL.
    Print or display the QR at the demo station.

Owner: Design
"""

import sys
import os

from dotenv import load_dotenv
load_dotenv()

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from datetime import datetime, timezone

import streamlit as st

from hana_dashboard import (
    get_connection,
    get_kpis,
    get_activity_timeline,
    get_critical_alerts,
    get_chart_data,
)
from charts import build_activity_chart, build_chart, CHART_CATALOG
from styles import inject_mobile_css, render_logo_header, severity_badge
import agent


# ── Page config ───────────────────────────────────────────────────────────────

st.set_page_config(
    page_title="SAP SOC · Mobile",
    page_icon="🛡️",
    layout="centered",           # centered = better single-column on mobile
    initial_sidebar_state="collapsed",
)


# ── Session state ─────────────────────────────────────────────────────────────

def _init_state() -> None:
    defaults = {
        "messages":         [],
        "last_refresh":     datetime.now(timezone.utc),
        "pending_question": None,
        "alert_context":    None,
        "current_chart":    None,
    }
    for k, v in defaults.items():
        if k not in st.session_state:
            st.session_state[k] = v


# ── Mobile header ─────────────────────────────────────────────────────────────

def render_mobile_header(conn) -> None:
    last_str = st.session_state["last_refresh"].strftime("%H:%M:%S UTC")
    render_logo_header(subtitle=f"Last updated: {last_str}")

    col_r, col_s = st.columns(2)
    with col_r:
        if st.button("🔄 Refresh", use_container_width=True, key="mob_refresh"):
            st.session_state["last_refresh"] = datetime.now(timezone.utc)
            st.cache_resource.clear()
            st.rerun()
    with col_s:
        if st.button("🗑️ Clear chat", use_container_width=True, key="mob_clear"):
            st.session_state["messages"] = []
            st.rerun()


# ── KPIs (2×2 grid on mobile) ─────────────────────────────────────────────────

def render_mobile_kpis(kpis: dict) -> None:
    """2 columns × 2 rows — better touch target on small screens."""
    col1, col2 = st.columns(2)
    col3, col4 = st.columns(2)

    threat_icon = {"NORMAL": "🟢", "ELEVATED": "🟡", "CRITICAL": "🔴"}.get(
        kpis["threat_level"], "⚪"
    )
    col1.metric("Threat Level", f"{threat_icon} {kpis['threat_level']}")

    delta_pct = kpis.get("alerts_delta_pct")
    delta_str = f"{delta_pct:+.1f}%" if delta_pct is not None else None
    col2.metric("Alerts / Hour", kpis["alerts_last_hour"],
                delta=delta_str, delta_color="inverse")

    div = kpis["attack_diversity"]
    col3.metric("Attack Diversity", f"{div['active']} / {div['total']}")

    top = kpis.get("top_threat")
    if top:
        col4.metric("Top Threat", top["alert_type"].replace("_", " "),
                    delta=f"{top['count']} alerts", delta_color="off")
    else:
        col4.metric("Top Threat", "— quiet")


# ── Compact activity chart ────────────────────────────────────────────────────

def render_mobile_activity(conn) -> None:
    """No slicers — shows last 24h only to keep it readable on small screens."""
    st.markdown("#### 📈 Activity — last 24h")
    try:
        df = get_activity_timeline(conn)
        if df.empty:
            st.info("No activity data.")
            return

        # Filter to last 24h only for mobile
        if not df.empty and hasattr(df["window_start"].dtype, "tz"):
            from datetime import timezone as tz_mod
            cutoff = df["window_start"].max() - __import__("pandas").Timedelta("24h")
            df = df[df["window_start"] >= cutoff]

        fig = build_activity_chart(df)
        # Compact height for mobile
        fig.update_layout(height=240, margin=dict(t=30, l=30, r=10, b=30))
        st.plotly_chart(fig, use_container_width=True, key="mob_activity")

    except Exception as exc:
        st.error(f"Activity chart error: {exc}")


# ── Alert summary banner ──────────────────────────────────────────────────────

def render_mobile_alert_banner(conn) -> None:
    """
    Compact alert summary — counts by severity.
    Clicking a severity chip pre-fills the chat with a targeted question.
    """
    st.markdown("#### 🚨 Active Alerts")
    try:
        df = get_critical_alerts(conn, hours=24, limit=50)
        if df.empty:
            st.success("✅ No active alerts.")
            return

        counts = df["severity"].value_counts()
        c_h, c_m, c_l, c_total = st.columns(4)

        n_high   = counts.get("high",   0)
        n_medium = counts.get("medium", 0)
        n_low    = counts.get("low",    0)

        c_h.metric("HIGH",   n_high,   delta=None)
        c_m.metric("MEDIUM", n_medium, delta=None)
        c_l.metric("LOW",    n_low,    delta=None)
        c_total.metric("TOTAL", len(df), delta=None)

        # Show top 3 most recent alerts as compact cards
        st.markdown("**Most recent:**")
        for _, row in df.head(3).iterrows():
            badge = severity_badge(str(row["severity"]))
            ip    = f" · `{row['client_ip']}`" if row["client_ip"] else ""
            time  = str(row["detected_at"])[:16] if row.get("detected_at") else ""
            st.markdown(
                f"{badge} &nbsp; **{str(row['alert_type']).replace('_', ' ')}**"
                f"{ip} &nbsp; <span style='color:#4A5266;font-size:.75rem'>{time}</span>",
                unsafe_allow_html=True,
            )
            # Quick action button per alert
            if st.button(
                f"What should I do? →",
                key=f"mob_qh_{row['alert_id']}",
                use_container_width=True,
            ):
                st.session_state["pending_question"] = (
                    f"Active alert — Type: {row['alert_type']}, "
                    f"Severity: {row['severity']}, "
                    f"Detail: {row['detail']}, "
                    f"IP: {row['client_ip'] or 'N/A'}. "
                    f"What concrete actions should I take right now?"
                )
                st.session_state["alert_context"] = {
                    "alert_type": row["alert_type"],
                    "severity":   row["severity"],
                    "detail":     row["detail"],
                    "client_ip":  row["client_ip"],
                }
                st.rerun()

    except Exception as exc:
        st.error(f"Could not load alerts: {exc}")


# ── Mobile Chat ───────────────────────────────────────────────────────────────

def render_mobile_chat(conn) -> None:
    """Full-width chat for mobile — no side panel."""
    st.markdown("#### 🤖 SOC Agent")

    # Compact suggestion chips — 2 per row on mobile
    SUGGESTIONS = [
        ("📋 Summary",         "Give me a summary of the last 24h alerts."),
        ("🌐 Suspicious IPs",  "Which IPs are most suspicious right now?"),
        ("🔍 Brute force",     "Show details of the active brute force pattern."),
        ("📈 LLM Activity",    "Is there anything concerning in the recent LLM activity?"),
    ]

    st.caption("Quick questions:")
    row1 = st.columns(2)
    row2 = st.columns(2)
    for col, (label, question) in zip(row1 + row2, SUGGESTIONS):
        if col.button(label, key=f"mob_sug_{label[:6]}", use_container_width=True):
            st.session_state["pending_question"] = question
            st.rerun()

    st.divider()

    # Chat history
    for msg_idx, msg in enumerate(st.session_state["messages"]):
        with st.chat_message(msg["role"]):
            st.write(msg["content"])
            if msg.get("chart_key") and msg.get("chart_figure"):
                fig = msg["chart_figure"]
                fig.update_layout(height=220, margin=dict(t=25, l=20, r=10, b=20))
                st.plotly_chart(
                    fig, use_container_width=True,
                    key=f"mob_hist_{msg_idx}_{msg['chart_key']}",
                )

    # Process pending question
    pending = st.session_state.get("pending_question")
    if pending:
        alert_ctx = st.session_state.get("alert_context")
        st.session_state["pending_question"] = None
        _mobile_process_question(pending, conn, alert_ctx)

    # Free chat input
    if prompt := st.chat_input("Ask the SOC agent…", key="mob_chat_input"):
        _mobile_process_question(prompt, conn, alert_context=None)


def _mobile_process_question(question: str, conn, alert_context: dict | None) -> None:
    """Same agent logic as desktop but with compact chart heights."""
    with st.chat_message("user"):
        st.write(question)

    st.session_state["messages"].append({"role": "user", "content": question})

    llm_history = [
        {"role": m["role"], "content": m["content"]}
        for m in st.session_state["messages"][:-1]
    ]

    chart_figure = None
    with st.chat_message("assistant"):
        with st.spinner("Analyzing with GPT-4o…"):
            result = agent.get_agent_response(
                user_question=question,
                conn=conn,
                conversation_history=llm_history,
                alert_context=alert_context,
            )

        if result["error"]:
            st.warning(f"⚠️ {result['error']}")

        st.write(result["text"])

        if result["chart_key"]:
            try:
                chart_data   = get_chart_data(conn, result["chart_key"])
                chart_figure = build_chart(result["chart_key"], chart_data)
                # Compact height for mobile
                chart_figure.update_layout(height=220, margin=dict(t=25, l=20, r=10, b=20))
                n = len(st.session_state["messages"])
                st.plotly_chart(
                    chart_figure, use_container_width=True,
                    key=f"mob_inline_{result['chart_key']}_{n}",
                )
            except Exception as exc:
                st.caption(f"_(Chart load error: {exc})_")

    st.session_state["messages"].append({
        "role":         "assistant",
        "content":      result["text"],
        "chart_key":    result["chart_key"],
        "chart_figure": chart_figure,
    })

    if alert_context:
        st.session_state["alert_context"] = None

    st.rerun()


# ── Main ──────────────────────────────────────────────────────────────────────

def main() -> None:
    _init_state()

    try:
        conn = get_connection()
    except Exception as exc:
        st.error(f"❌ HANA connection failed: {exc}")
        st.stop()

    try:
        kpis         = get_kpis(conn)
        threat_level = kpis.get("threat_level", "NORMAL")
    except Exception:
        kpis         = None
        threat_level = "NORMAL"

    inject_mobile_css()                  # desktop CSS + mobile overrides

    render_mobile_header(conn)
    st.divider()

    if kpis:
        render_mobile_kpis(kpis)
    else:
        st.error("Could not load KPIs.")

    st.divider()
    render_mobile_activity(conn)
    st.divider()
    render_mobile_alert_banner(conn)
    st.divider()
    render_mobile_chat(conn)


if __name__ == "__main__":
    main()