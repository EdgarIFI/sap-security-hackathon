import sys
sys.path.insert(0, ".")
from dotenv import load_dotenv
load_dotenv()

import streamlit as st
from dashboard.hana_dashboard import (
    get_connection, get_kpis, get_activity_timeline,
    get_critical_alerts, get_chart_data
)
from dashboard.charts import build_chart, build_activity_chart, CHART_CATALOG

st.set_page_config(layout="wide", page_title="Dev2 Visual Test")
st.title("🛡️ Dev 2 — Visual Test")

conn = get_connection()

# ── KPIs
st.header("KPIs")
kpis = get_kpis(conn)
c1, c2, c3, c4 = st.columns(4)
c1.metric("Threat Level",     kpis["threat_level"])
c2.metric("Alerts Last Hour", kpis["alerts_last_hour"],
          delta=f"{kpis['alerts_delta_pct']}%" if kpis["alerts_delta_pct"] else None)
c3.metric("Attack Diversity", f"{kpis['attack_diversity']['active']} of 7")
c4.metric("Top Threat",
          kpis["top_threat"]["alert_type"] if kpis["top_threat"] else "—",
          delta=str(kpis["top_threat"]["count"]) if kpis["top_threat"] else None)

st.divider()

# ── Gráfica principal
st.header("Activity Timeline")
st.plotly_chart(build_activity_chart(get_activity_timeline(conn)),
                use_container_width=True)

st.divider()

# ── Tabla alertas
st.header("Critical Alerts")
st.dataframe(get_critical_alerts(conn), use_container_width=True)

st.divider()

# ── Catálogo completo
st.header("Chart Catalog")
for key, desc in CHART_CATALOG.items():
    st.subheader(f"{key}")
    st.caption(desc)
    try:
        data = get_chart_data(conn, key)
        fig  = build_chart(key, data)
        st.plotly_chart(fig, use_container_width=True)
    except Exception as e:
        st.error(f"{key}: {e}")
    st.divider()