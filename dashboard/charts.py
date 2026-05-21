"""
dashboard/charts.py
===================
Catálogo de figuras Plotly para el SOC Dashboard.
No ejecuta queries. No conoce HANA.
Recibe DataFrames preparados por hana_dashboard.py y retorna go.Figure.

Owner:  Dev 2
Repo:   sap-security-hackathon / dashboard/
"""

import pandas as pd
import numpy as np
import plotly.graph_objects as go
from plotly.subplots import make_subplots


# ── Paleta de colores ─────────────────────────────────────────────────────────

_COLOR_NORMAL   = "#0066CC"             # azul SAP — actividad normal / informativo
_COLOR_HIGH     = "#FF4444"             # rojo — anomalía / severidad high
_COLOR_MEDIUM   = "#FFB300"             # amarillo — severidad medium / warning
_COLOR_LOW      = "#888888"             # gris — severidad low / inactivo
_COLOR_AREA     = "rgba(0,102,204,0.15)"  # relleno área línea principal
_COLOR_ORANGE   = "#FF8C00"             # naranja — segunda posición en rankings
_TEMPLATE       = "plotly_dark"
_BG             = "rgba(0,0,0,0)"       # fondo transparente para todos los charts


# ── Catálogo — importado por agent.py para el system prompt ───────────────────

CHART_CATALOG: dict[str, str] = {
    "brute_force_por_ip":    "IPs con mayor número de intentos de autenticación fallidos (24h)",
    "brute_force_por_hora":  "Intentos de autenticación fallidos distribuidos por hora del día",
    "alertas_por_tipo":      "Distribución de alertas por tipo y fuente de detección",
    "alertas_por_hora":      "Mapa de calor de alertas por hora y día de la semana",
    "path_scan_activity":    "Rutas del sistema más escaneadas por IPs sospechosas",
    "llm_anomalias":         "Actividad LLM en el tiempo con marcas de anomalías detectadas",
    "sistema_anomalias":     "Volumen de logs del sistema con error rate y marcas de anomalías",
}


# ── Función pública principal ─────────────────────────────────────────────────

def build_chart(chart_key: str, data) -> go.Figure:
    """
    Dispatcher principal del catálogo.

    Args:
        chart_key: identificador de la gráfica (debe existir en CHART_CATALOG).
        data:
            pd.DataFrame              para chart_keys normales.
            tuple(DataFrame, DataFrame) para 'llm_anomalias' y 'sistema_anomalias'.

    Returns:
        go.Figure lista para st.plotly_chart().
        Si el DataFrame está vacío retorna figura con anotación "Sin datos".

    Raises:
        ValueError: si chart_key no existe en CHART_CATALOG.
    """
    _DISPATCH = {
        "brute_force_por_ip":   _chart_brute_force_por_ip,
        "brute_force_por_hora": _chart_brute_force_por_hora,
        "alertas_por_tipo":     _chart_alertas_por_tipo,
        "alertas_por_hora":     _chart_alertas_por_hora,
        "path_scan_activity":   _chart_path_scan_activity,
        # Overlays reciben tuple — el lambda desempaca antes de llamar la función
        "llm_anomalias":        lambda d: _chart_llm_anomalias(*d),
        "sistema_anomalias":    lambda d: _chart_sistema_anomalias(*d),
    }

    if chart_key not in _DISPATCH:
        raise ValueError(
            f"chart_key '{chart_key}' no existe. "
            f"Disponibles: {list(CHART_CATALOG.keys())}"
        )

    return _DISPATCH[chart_key](data)


# ── Gráfica compuesta Tab 1 ───────────────────────────────────────────────────

def build_activity_chart(df: pd.DataFrame) -> go.Figure:
    """
    Gráfica principal del Tab 1: volumen de logs del sistema en el tiempo
    con marcas de alertas superpuestas por severidad.

    Los markers de alerta se posicionan en la parte superior del área
    (max(log_volume) * 1.1) para garantizar visibilidad independientemente
    del volumen de logs en esa ventana.

    Args:
        df: DataFrame de hana_dashboard.get_activity_timeline().
            Columnas requeridas: window_start, log_volume,
                                 alerts_high, alerts_medium, alerts_low.

    Returns:
        go.Figure con 4 traces:
            Trace 1: área de log_volume
            Trace 2: triángulos ▲ rojos   para alerts_high   > 0
            Trace 3: diamantes ◆ amarillos para alerts_medium > 0
            Trace 4: círculos  · grises    para alerts_low    > 0
    """
    if df.empty:
        return _empty_figure("Actividad del Sistema + Marcas de Alertas")

    # Y para markers: ligeramente por encima del máximo del área
    y_marker = df["log_volume"].max() * 1.1 if df["log_volume"].max() > 0 else 1

    fig = go.Figure()

    # Trace 1: área de actividad
    fig.add_trace(go.Scatter(
        x=df["window_start"],
        y=df["log_volume"],
        mode="lines",
        fill="tozeroy",
        line=dict(color=_COLOR_NORMAL, width=1.5),
        fillcolor=_COLOR_AREA,
        name="Log volume",
        hovertemplate="<b>%{x|%H:%M}</b><br>Logs: %{y:,}<extra></extra>",
    ))

    # Trace 2: alertas HIGH
    df_high = df[df["alerts_high"] > 0]
    if not df_high.empty:
        fig.add_trace(go.Scatter(
            x=df_high["window_start"],
            y=[y_marker] * len(df_high),
            mode="markers",
            marker=dict(symbol="triangle-up", color=_COLOR_HIGH, size=14),
            name="HIGH alert",
            hovertemplate=(
                "<b>%{x|%H:%M}</b><br>"
                "HIGH alerts: " + df_high["alerts_high"].astype(str) +
                "<extra></extra>"
            ),
        ))

    # Trace 3: alertas MEDIUM
    df_med = df[df["alerts_medium"] > 0]
    if not df_med.empty:
        fig.add_trace(go.Scatter(
            x=df_med["window_start"],
            y=[y_marker * 0.92] * len(df_med),
            mode="markers",
            marker=dict(symbol="diamond", color=_COLOR_MEDIUM, size=12),
            name="MEDIUM alert",
            hovertemplate=(
                "<b>%{x|%H:%M}</b><br>"
                "MEDIUM alerts: " + df_med["alerts_medium"].astype(str) +
                "<extra></extra>"
            ),
        ))

    # Trace 4: alertas LOW
    df_low = df[df["alerts_low"] > 0]
    if not df_low.empty:
        fig.add_trace(go.Scatter(
            x=df_low["window_start"],
            y=[y_marker * 0.84] * len(df_low),
            mode="markers",
            marker=dict(symbol="circle", color=_COLOR_LOW, size=8),
            name="LOW alert",
            hovertemplate=(
                "<b>%{x|%H:%M}</b><br>"
                "LOW alerts: " + df_low["alerts_low"].astype(str) +
                "<extra></extra>"
            ),
        ))

    fig.update_layout(**_base_layout(
        title="Actividad del Sistema + Marcas de Alertas (últimas 48h)",
        xaxis_title="Ventana UTC",
        yaxis_title="Número de logs",
    ))

    return fig


# ── Funciones privadas del catálogo ──────────────────────────────────────────

def _chart_brute_force_por_ip(df: pd.DataFrame) -> go.Figure:
    """
    Bar chart horizontal: IPs vs auth failures, ordenadas de mayor a menor.
    Color: barra #1 en rojo, #2 en naranja, resto en amarillo.
    Muestra primera y última actividad en el tooltip.
    """
    if df.empty:
        return _empty_figure("IPs con Intentos de Autenticación Fallidos (24h)")

    df = df.sort_values("auth_failures", ascending=True)  # ascending para bar horizontal

    n = len(df)
    colors = []
    for i in range(n):
        rank = n - 1 - i  # rank 0 = mayor cantidad (último en la lista invertida)
        if rank == 0:
            colors.append(_COLOR_HIGH)
        elif rank == 1:
            colors.append(_COLOR_ORANGE)
        else:
            colors.append(_COLOR_MEDIUM)

    # Hover text con timestamps si están disponibles
    hover_parts = []
    for _, row in df.iterrows():
        txt = f"<b>{row['client_ip']}</b><br>Auth failures: {int(row['auth_failures'])}"
        if "primera_actividad" in df.columns and pd.notna(row.get("primera_actividad")):
            txt += f"<br>Primera: {row['primera_actividad'].strftime('%H:%M UTC')}"
        if "ultima_actividad" in df.columns and pd.notna(row.get("ultima_actividad")):
            txt += f"<br>Última:  {row['ultima_actividad'].strftime('%H:%M UTC')}"
        hover_parts.append(txt + "<extra></extra>")

    fig = go.Figure(go.Bar(
        x=df["auth_failures"],
        y=df["client_ip"].astype(str),
        orientation="h",
        marker_color=colors,
        hovertemplate=hover_parts,
        text=df["auth_failures"],
        textposition="outside",
    ))

    fig.update_layout(**_base_layout(
        title="IPs con Mayor Número de Auth Failures (últimas 24h)",
        xaxis_title="Intentos fallidos",
        yaxis_title="IP",
    ))
    fig.update_yaxes(tickfont=dict(family="monospace", size=11))

    return fig


def _chart_brute_force_por_hora(df: pd.DataFrame) -> go.Figure:
    """
    Line chart: hora del día (0-23) vs intentos de auth fallidos.
    Relleno bajo la curva para mostrar la distribución de volumen.
    Marca la hora pico con un punto destacado.
    """
    if df.empty:
        return _empty_figure("Intentos de Auth Fallidos por Hora del Día")

    hora_pico = df.loc[df["auth_failures"].idxmax(), "hora"] if df["auth_failures"].max() > 0 else None

    fig = go.Figure()

    # Línea principal
    fig.add_trace(go.Scatter(
        x=df["hora"],
        y=df["auth_failures"],
        mode="lines+markers",
        fill="tozeroy",
        line=dict(color=_COLOR_MEDIUM, width=2),
        fillcolor="rgba(255,179,0,0.15)",
        marker=dict(size=6, color=_COLOR_MEDIUM),
        name="Auth failures",
        hovertemplate="<b>%{x}:00h</b><br>Intentos: %{y}<extra></extra>",
    ))

    # Punto de hora pico
    if hora_pico is not None:
        pico_val = df.loc[df["hora"] == hora_pico, "auth_failures"].values[0]
        fig.add_trace(go.Scatter(
            x=[hora_pico],
            y=[pico_val],
            mode="markers+text",
            marker=dict(size=14, color=_COLOR_HIGH, symbol="star"),
            text=[f"Pico: {hora_pico}:00h"],
            textposition="top center",
            name="Hora pico",
            hovertemplate=f"<b>Hora pico: {hora_pico}:00h</b><br>Intentos: {pico_val}<extra></extra>",
        ))

    fig.update_layout(**_base_layout(
        title="Intentos de Autenticación Fallidos por Hora del Día (24h)",
        xaxis_title="Hora UTC",
        yaxis_title="Intentos fallidos",
    ))
    fig.update_xaxes(tickmode="linear", tick0=0, dtick=2, range=[-0.5, 23.5])

    return fig


def _chart_alertas_por_tipo(df: pd.DataFrame) -> go.Figure:
    """
    Bar chart agrupado: alert_type en eje X, count en eje Y,
    agrupado por detection_source (quick_filter en azul, model_ml en amarillo).
    Permite comparar visualmente qué detecta cada fuente.
    """
    if df.empty:
        return _empty_figure("Distribución de Alertas por Tipo y Fuente (24h)")

    fuentes = df["detection_source"].unique()
    color_map = {
        "quick_filter": _COLOR_NORMAL,
        "model_ml":     _COLOR_MEDIUM,
    }

    fig = go.Figure()

    for fuente in fuentes:
        df_f = df[df["detection_source"] == fuente]
        fig.add_trace(go.Bar(
            x=df_f["alert_type"],
            y=df_f["count"],
            name=fuente,
            marker_color=color_map.get(fuente, _COLOR_LOW),
            hovertemplate="<b>%{x}</b><br>Fuente: " + fuente + "<br>Count: %{y}<extra></extra>",
        ))

    fig.update_layout(
        **_base_layout(
            title="Alertas por Tipo y Fuente de Detección (últimas 24h)",
            xaxis_title="Tipo de alerta",
            yaxis_title="Número de alertas",
        ),
        barmode="group",
    )
    fig.update_xaxes(tickangle=-30)

    return fig


def _chart_alertas_por_hora(df: pd.DataFrame) -> go.Figure:
    """
    Heatmap: eje X = hora del día (0-23), eje Y = día.
    Colorscale de azul oscuro (0 alertas) a rojo (muchas alertas).
    Pivota el DataFrame para construir la matriz del heatmap.
    """
    if df.empty:
        return _empty_figure("Mapa de Calor de Alertas por Hora y Día (7 días)")

    # Pivot: filas = días, columnas = horas
    df["dia_str"] = df["dia"].astype(str)
    pivot = df.pivot_table(
        index="dia_str", columns="hora", values="count",
        aggfunc="sum", fill_value=0,
    )

    # Asegurar todas las horas 0-23 aunque no haya datos en alguna
    for h in range(24):
        if h not in pivot.columns:
            pivot[h] = 0
    pivot = pivot.reindex(columns=range(24), fill_value=0)

    fig = go.Figure(go.Heatmap(
        z=pivot.values,
        x=list(range(24)),
        y=list(pivot.index),
        colorscale=[
            [0.0,  "#1A1A2E"],   # sin alertas: fondo oscuro
            [0.01, "#0066CC"],   # muy pocas: azul
            [0.4,  "#FFB300"],   # moderado: amarillo
            [1.0,  "#FF4444"],   # muchas: rojo
        ],
        hovertemplate="<b>%{y} — %{x}:00h</b><br>Alertas: %{z}<extra></extra>",
        colorbar=dict(title="Alertas"),
        zmin=0,
    ))

    fig.update_layout(**_base_layout(
        title="Mapa de Calor — Alertas por Hora y Día (últimos 7 días)",
        xaxis_title="Hora UTC",
        yaxis_title="Día",
    ))
    fig.update_xaxes(tickmode="linear", tick0=0, dtick=2)

    return fig


def _chart_path_scan_activity(df: pd.DataFrame) -> go.Figure:
    """
    Bar chart horizontal: rutas más escaneadas vs conteo de requests.
    Tooltip muestra cuántas IPs distintas han solicitado cada ruta.
    Trunca rutas largas para legibilidad.
    """
    if df.empty:
        return _empty_figure("Rutas del Sistema Más Escaneadas (24h)")

    df = df.sort_values("count", ascending=True)

    # Truncar rutas largas para el eje Y
    df["path_display"] = df["request_path"].astype(str).apply(
        lambda p: p if len(p) <= 45 else "..." + p[-42:]
    )

    fig = go.Figure(go.Bar(
        x=df["count"],
        y=df["path_display"],
        orientation="h",
        marker_color=_COLOR_MEDIUM,
        text=df["count"],
        textposition="outside",
        customdata=df[["unique_ips", "request_path"]].values,
        hovertemplate=(
            "<b>%{customdata[1]}</b><br>"
            "Requests: %{x}<br>"
            "IPs distintas: %{customdata[0]}<extra></extra>"
        ),
    ))

    fig.update_layout(**_base_layout(
        title="Rutas Más Escaneadas — HTTP 404 (últimas 24h)",
        xaxis_title="Número de requests",
        yaxis_title="",
    ))
    fig.update_yaxes(tickfont=dict(family="monospace", size=10))

    return fig


def _chart_llm_anomalias(
    df_actividad: pd.DataFrame,
    df_alertas: pd.DataFrame,
) -> go.Figure:
    """
    Gráfica overlay: actividad LLM en el tiempo + markers de anomalías.
    Dos DataFrames independientes — sin JOIN — alineados por eje X temporal.

    Trace 1: línea de avg_cost_usd por ventana (eje izquierdo)
    Trace 2: barras de log_count por ventana (eje derecho, semitransparente)
    Trace 3: markers ▲ rojos en posición fija superior para cada alerta ML

    Args:
        df_actividad: columnas window_start, avg_cost_usd,
                      avg_response_time_ms, log_count
        df_alertas:   columnas detected_at, severity, details
    """
    if df_actividad.empty and df_alertas.empty:
        return _empty_figure("Actividad LLM + Anomalías Detectadas (48h)")

    fig = make_subplots(specs=[[{"secondary_y": True}]])

    # ── Trace 1: costo promedio por ventana ───────────────────────────────────
    if not df_actividad.empty:
        fig.add_trace(
            go.Scatter(
                x=df_actividad["window_start"],
                y=df_actividad["avg_cost_usd"],
                mode="lines+markers",
                line=dict(color=_COLOR_NORMAL, width=2),
                marker=dict(size=5),
                name="Avg cost (USD)",
                hovertemplate=(
                    "<b>%{x|%H:%M}</b><br>"
                    "Avg cost: $%{y:.5f}<extra></extra>"
                ),
            ),
            secondary_y=False,
        )

    # ── Trace 2: volumen de llamadas (eje secundario) ─────────────────────────
    if not df_actividad.empty:
        fig.add_trace(
            go.Bar(
                x=df_actividad["window_start"],
                y=df_actividad["log_count"],
                name="LLM calls",
                marker_color="rgba(0,102,204,0.2)",
                hovertemplate=(
                    "<b>%{x|%H:%M}</b><br>"
                    "Llamadas LLM: %{y}<extra></extra>"
                ),
            ),
            secondary_y=True,
        )

    # ── Trace 3: markers de alerta ML ────────────────────────────────────────
    if not df_alertas.empty and not df_actividad.empty:
        y_max = df_actividad["avg_cost_usd"].max()
        y_marker = y_max * 1.15 if y_max > 0 else 0.001

        fig.add_trace(
            go.Scatter(
                x=df_alertas["detected_at"],
                y=[y_marker] * len(df_alertas),
                mode="markers",
                marker=dict(
                    symbol="triangle-up",
                    color=_COLOR_HIGH,
                    size=14,
                    line=dict(width=1, color="#CC0000"),
                ),
                name="ml_llm_anomaly",
                customdata=df_alertas[["severity", "details"]].values,
                hovertemplate=(
                    "<b>🚨 Anomalía LLM detectada</b><br>"
                    "Hora: %{x|%H:%M UTC}<br>"
                    "Severidad: %{customdata[0]}<br>"
                    "Detalle: %{customdata[1]}<extra></extra>"
                ),
            ),
            secondary_y=False,
        )
    elif not df_alertas.empty:
        # Si no hay actividad pero sí alertas, usar y=1 como fallback
        fig.add_trace(go.Scatter(
            x=df_alertas["detected_at"],
            y=[1] * len(df_alertas),
            mode="markers",
            marker=dict(symbol="triangle-up", color=_COLOR_HIGH, size=14),
            name="ml_llm_anomaly",
        ), secondary_y=False)

    layout = _base_layout(
        title="Actividad LLM + Anomalías Detectadas (últimas 48h)",
        xaxis_title="Ventana UTC",
        yaxis_title="Avg cost (USD)",
    )
    fig.update_layout(**layout)
    fig.update_yaxes(title_text="LLM calls", secondary_y=True)

    return fig


def _chart_sistema_anomalias(
    df_actividad: pd.DataFrame,
    df_alertas: pd.DataFrame,
) -> go.Figure:
    """
    Gráfica overlay: volumen de logs del sistema + error rate + markers de anomalías.
    Tres traces — dos DataFrames independientes — alineados por eje X temporal.

    Trace 1: área de log_count por ventana (eje izquierdo)
    Trace 2: barras de error_rate por ventana (eje derecho, naranja semitransparente)
    Trace 3: markers ▲ rojos para cada alerta ml_sistema_anomaly

    La combinación de volumen + error_rate permite ver si el spike de anomalía
    coincide con degradación del sistema o solo con aumento de tráfico.

    Args:
        df_actividad: columnas window_start, log_count, error_rate, ratio_4xx
        df_alertas:   columnas detected_at, severity, details
    """
    if df_actividad.empty and df_alertas.empty:
        return _empty_figure("Actividad del Sistema + Anomalías ML Detectadas (48h)")

    fig = make_subplots(specs=[[{"secondary_y": True}]])

    # ── Trace 1: volumen de logs (área) ───────────────────────────────────────
    if not df_actividad.empty:
        fig.add_trace(
            go.Scatter(
                x=df_actividad["window_start"],
                y=df_actividad["log_count"],
                mode="lines",
                fill="tozeroy",
                line=dict(color=_COLOR_NORMAL, width=1.5),
                fillcolor=_COLOR_AREA,
                name="Log volume",
                hovertemplate=(
                    "<b>%{x|%H:%M}</b><br>"
                    "Logs: %{y:,}<extra></extra>"
                ),
            ),
            secondary_y=False,
        )

    # ── Trace 2: error rate (barras, eje secundario) ──────────────────────────
    if not df_actividad.empty:
        fig.add_trace(
            go.Bar(
                x=df_actividad["window_start"],
                y=df_actividad["error_rate"],
                name="Error rate",
                marker_color="rgba(255,179,0,0.35)",
                hovertemplate=(
                    "<b>%{x|%H:%M}</b><br>"
                    "Error rate: %{y:.2%}<extra></extra>"
                ),
            ),
            secondary_y=True,
        )

    # ── Trace 3: markers de alerta ML ────────────────────────────────────────
    if not df_alertas.empty and not df_actividad.empty:
        y_max = df_actividad["log_count"].max()
        y_marker = y_max * 1.15 if y_max > 0 else 1

        fig.add_trace(
            go.Scatter(
                x=df_alertas["detected_at"],
                y=[y_marker] * len(df_alertas),
                mode="markers",
                marker=dict(
                    symbol="triangle-up",
                    color=_COLOR_HIGH,
                    size=14,
                    line=dict(width=1, color="#CC0000"),
                ),
                name="ml_sistema_anomaly",
                customdata=df_alertas[["severity", "details"]].values,
                hovertemplate=(
                    "<b>🚨 Anomalía Sistema detectada</b><br>"
                    "Hora: %{x|%H:%M UTC}<br>"
                    "Severidad: %{customdata[0]}<br>"
                    "Detalle: %{customdata[1]}<extra></extra>"
                ),
            ),
            secondary_y=False,
        )
    elif not df_alertas.empty:
        fig.add_trace(go.Scatter(
            x=df_alertas["detected_at"],
            y=[1] * len(df_alertas),
            mode="markers",
            marker=dict(symbol="triangle-up", color=_COLOR_HIGH, size=14),
            name="ml_sistema_anomaly",
        ), secondary_y=False)

    layout = _base_layout(
        title="Actividad del Sistema + Anomalías ML Detectadas (últimas 48h)",
        xaxis_title="Ventana UTC",
        yaxis_title="Número de logs",
    )
    fig.update_layout(**layout)
    fig.update_yaxes(
        title_text="Error rate",
        tickformat=".0%",
        secondary_y=True,
    )

    return fig


# ── Helpers internos ──────────────────────────────────────────────────────────

def _empty_figure(title: str) -> go.Figure:
    """
    Retorna figura vacía con anotación visible cuando no hay datos.
    Evita que st.plotly_chart() muestre un área en blanco sin explicación.
    """
    fig = go.Figure()
    fig.update_layout(
        **_base_layout(title),
        annotations=[dict(
            text="Sin datos disponibles en el rango seleccionado",
            xref="paper", yref="paper",
            x=0.5, y=0.5,
            showarrow=False,
            font=dict(size=14, color=_COLOR_LOW),
        )],
    )
    return fig


def _base_layout(
    title: str,
    xaxis_title: str = "",
    yaxis_title: str = "",
) -> dict:
    """
    Retorna dict de layout base para todas las figuras del catálogo.
    Garantiza consistencia visual: template oscuro, fondo transparente,
    márgenes y tipografía uniformes.
    """
    return dict(
        title=dict(text=title, font=dict(size=14)),
        template=_TEMPLATE,
        paper_bgcolor=_BG,
        plot_bgcolor=_BG,
        xaxis_title=xaxis_title,
        yaxis_title=yaxis_title,
        margin=dict(t=55, l=50, r=30, b=50),
        legend=dict(
            orientation="h",
            yanchor="bottom",
            y=1.02,
            xanchor="right",
            x=1,
        ),
        hoverlabel=dict(
            bgcolor="#1A1A2E",
            font_size=12,
        ),
    )


# ── Ejecución directa para pruebas ───────────────────────────────────────────

if __name__ == "__main__":
    import sys
    sys.path.insert(0, ".")
    from dotenv import load_dotenv
    load_dotenv()

    from dashboard.hana_dashboard import (
        get_connection, get_chart_data, get_activity_timeline
    )

    conn = get_connection()
    print("✅ Conexión establecida\n")

    print("── CHART_CATALOG ──")
    for k, v in CHART_CATALOG.items():
        print(f"  {k}: {v}")

    print("\n── Probando cada gráfica del catálogo ──")
    for key in CHART_CATALOG:
        try:
            data = get_chart_data(conn, key)
            fig  = build_chart(key, data)
            print(f"  ✅ {key}: go.Figure con {len(fig.data)} trace(s)")
        except Exception as exc:
            print(f"  ❌ {key}: {exc}")

    print("\n── build_activity_chart ──")
    try:
        df  = get_activity_timeline(conn)
        fig = build_activity_chart(df)
        print(f"  ✅ go.Figure con {len(fig.data)} traces")
    except Exception as exc:
        print(f"  ❌ {exc}")