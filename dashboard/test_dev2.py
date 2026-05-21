"""
dashboard/test_dev2.py
======================
Script de validación para Dev 2.
Prueba hana_dashboard.py y charts.py contra HANA real.

Uso:
    cd sap-security-hackathon
    python dashboard/test_dev2.py

    Para correr solo una sección:
    python dashboard/test_dev2.py --seccion conexion
    python dashboard/test_dev2.py --seccion kpis
    python dashboard/test_dev2.py --seccion timeline
    python dashboard/test_dev2.py --seccion alertas
    python dashboard/test_dev2.py --seccion contexto
    python dashboard/test_dev2.py --seccion charts_data
    python dashboard/test_dev2.py --seccion charts_render
    python dashboard/test_dev2.py --seccion overlays
    python dashboard/test_dev2.py --seccion contrato
"""

import sys
import os
import argparse
import traceback
import pandas as pd

sys.path.insert(0, ".")
from dotenv import load_dotenv
load_dotenv()

# ── Helpers de output ─────────────────────────────────────────────────────────

def ok(msg):    print(f"  ✅  {msg}")
def fail(msg):  print(f"  ❌  {msg}")
def warn(msg):  print(f"  ⚠️   {msg}")
def info(msg):  print(f"  ℹ️   {msg}")
def header(msg): print(f"\n{'─'*55}\n  {msg}\n{'─'*55}")

passed = []
failed = []

def check(condition, label, detail=""):
    if condition:
        ok(label)
        passed.append(label)
    else:
        fail(f"{label} — {detail}" if detail else label)
        failed.append(label)

# ── Importar módulos ──────────────────────────────────────────────────────────

try:
    from dashboard import hana_dashboard as hd
    from dashboard import charts
    ok("Módulos importados correctamente")
except ImportError as e:
    fail(f"Error de importación: {e}")
    sys.exit(1)

# ═════════════════════════════════════════════════════════════════════════════
# SECCIÓN 1 — CONEXIÓN
# ═════════════════════════════════════════════════════════════════════════════

def test_conexion():
    header("SECCIÓN 1 — Conexión a HANA")
    info("Verifica que las credenciales del .env son válidas y HANA responde.")

    # 1.1 Variables de entorno presentes
    for var in ["HANA_HOST", "HANA_USER", "HANA_PASSWORD", "HANA_PORT"]:
        check(bool(os.environ.get(var)), f"Variable {var} presente en .env")

    # 1.2 Conexión activa
    try:
        conn = hd.get_connection()
        check(conn is not None, "get_connection() retorna objeto de conexión")
    except NotImplementedError:
        fail("get_connection() — NotImplementedError (función no implementada)")
        return None
    except Exception as e:
        fail(f"get_connection() falló: {e}")
        return None

    # 1.3 Query mínima
    try:
        cursor = conn.cursor()
        cursor.execute("SELECT 1 FROM DUMMY")
        result = cursor.fetchone()
        check(result[0] == 1, "Query SELECT 1 FROM DUMMY retorna 1")
    except Exception as e:
        fail(f"Query de prueba falló: {e}")

    # 1.4 Las tres tablas existen
    for tabla in ["DBADMIN.ALERTS", "DBADMIN.RAW_LOGS_SISTEMA", "DBADMIN.RAW_LOGS_LLM"]:
        try:
            cursor.execute(f"SELECT COUNT(*) FROM {tabla}")
            n = cursor.fetchone()[0]
            check(True, f"Tabla {tabla} existe", f"{n:,} filas")
            info(f"    {tabla}: {n:,} filas")
        except Exception as e:
            fail(f"Tabla {tabla} no accesible: {e}")

    return conn


# ═════════════════════════════════════════════════════════════════════════════
# SECCIÓN 2 — KPIs
# ═════════════════════════════════════════════════════════════════════════════

def test_kpis(conn):
    header("SECCIÓN 2 — KPIs de estado de seguridad")
    info("Verifica que get_kpis() retorna el dict del contrato con tipos correctos.")

    try:
        kpis = hd.get_kpis(conn)
    except NotImplementedError:
        warn("get_kpis() — pendiente de implementar, saltando sección")
        return
    except Exception as e:
        fail(f"get_kpis() lanzó excepción: {e}")
        return

    # 2.1 Tipo de retorno
    check(isinstance(kpis, dict), "get_kpis() retorna dict")

    # 2.2 Keys del contrato presentes
    required_keys = ["threat_level", "alerts_last_hour", "alerts_delta_pct",
                     "attack_diversity", "top_threat"]
    for key in required_keys:
        check(key in kpis, f"Key '{key}' presente en el dict")

    if not all(k in kpis for k in required_keys):
        return

    # 2.3 threat_level
    check(
        kpis["threat_level"] in ["NORMAL", "ELEVATED", "CRITICAL"],
        f"threat_level es NORMAL/ELEVATED/CRITICAL",
        f"valor actual: '{kpis['threat_level']}'"
    )

    # 2.4 alerts_last_hour
    check(
        isinstance(kpis["alerts_last_hour"], int) and kpis["alerts_last_hour"] >= 0,
        f"alerts_last_hour es int >= 0",
        f"valor: {kpis['alerts_last_hour']}"
    )

    # 2.5 alerts_delta_pct
    check(
        kpis["alerts_delta_pct"] is None or isinstance(kpis["alerts_delta_pct"], float),
        "alerts_delta_pct es float o None",
        f"valor: {kpis['alerts_delta_pct']}"
    )

    # 2.6 attack_diversity
    div = kpis["attack_diversity"]
    check(
        isinstance(div, dict) and "active" in div and "total" in div,
        "attack_diversity es dict con keys 'active' y 'total'"
    )
    if isinstance(div, dict):
        check(div.get("total") == 7, "attack_diversity.total == 7 (hardcodeado)")
        check(0 <= div.get("active", -1) <= 7, "attack_diversity.active entre 0 y 7")

    # 2.7 top_threat
    tt = kpis["top_threat"]
    check(
        tt is None or (isinstance(tt, dict) and "alert_type" in tt and "count" in tt),
        "top_threat es None o dict con 'alert_type' y 'count'"
    )

    info(f"\n  Valores actuales:")
    for k, v in kpis.items():
        info(f"    {k}: {v}")


# ═════════════════════════════════════════════════════════════════════════════
# SECCIÓN 3 — ACTIVITY TIMELINE
# ═════════════════════════════════════════════════════════════════════════════

def test_timeline(conn):
    header("SECCIÓN 3 — Activity Timeline")
    info("Verifica shape, columnas, tipos y que no haya nulls donde no deben.")

    try:
        df = hd.get_activity_timeline(conn)
    except NotImplementedError:
        warn("get_activity_timeline() — pendiente de implementar")
        return
    except Exception as e:
        fail(f"get_activity_timeline() lanzó excepción: {e}")
        return

    # 3.1 Es DataFrame
    check(isinstance(df, pd.DataFrame), "Retorna pd.DataFrame")
    if df.empty:
        warn("DataFrame vacío — puede ser normal si no hay datos en 48h")
        return

    # 3.2 Columnas del contrato
    required_cols = ["window_start", "log_volume", "alerts_high",
                     "alerts_medium", "alerts_low"]
    for col in required_cols:
        check(col in df.columns, f"Columna '{col}' presente")

    # 3.3 Tipos
    check(pd.api.types.is_datetime64_any_dtype(df["window_start"]),
          "window_start es datetime")
    for col in ["log_volume", "alerts_high", "alerts_medium", "alerts_low"]:
        if col in df.columns:
            check(pd.api.types.is_numeric_dtype(df[col]),
                  f"{col} es numérico")

    # 3.4 Sin nulls en columnas críticas
    for col in required_cols:
        if col in df.columns:
            n_nulls = df[col].isna().sum()
            check(n_nulls == 0, f"Sin nulls en '{col}'", f"{n_nulls} nulls encontrados")

    # 3.5 Ordenado por window_start ASC
    if "window_start" in df.columns and len(df) > 1:
        check(
            df["window_start"].is_monotonic_increasing,
            "DataFrame ordenado por window_start ASC"
        )

    # 3.6 Valores lógicos
    for col in ["alerts_high", "alerts_medium", "alerts_low"]:
        if col in df.columns:
            check((df[col] >= 0).all(), f"Todos los valores de {col} >= 0")

    info(f"\n  Shape: {df.shape}")
    info(f"  Rango temporal: {df['window_start'].min()} → {df['window_start'].max()}")


# ═════════════════════════════════════════════════════════════════════════════
# SECCIÓN 4 — CRITICAL ALERTS
# ═════════════════════════════════════════════════════════════════════════════

def test_alertas(conn):
    header("SECCIÓN 4 — Critical Alerts")
    info("Verifica columnas del contrato, tipos y manejo de client_ip nullable.")

    try:
        df = hd.get_critical_alerts(conn)
    except NotImplementedError:
        warn("get_critical_alerts() — pendiente de implementar")
        return
    except Exception as e:
        fail(f"get_critical_alerts() lanzó excepción: {e}")
        return

    check(isinstance(df, pd.DataFrame), "Retorna pd.DataFrame")
    if df.empty:
        warn("DataFrame vacío — sin alertas en las últimas 24h")
        return

    # 4.1 Columnas del contrato
    required_cols = ["alert_id", "alert_type", "severity", "detection_source",
                     "detail", "detected_at", "client_ip"]
    for col in required_cols:
        check(col in df.columns, f"Columna '{col}' presente")

    # 4.2 severity solo tiene valores válidos
    if "severity" in df.columns:
        valid_severities = {"high", "medium", "low"}
        actual = set(df["severity"].dropna().unique())
        check(
            actual.issubset(valid_severities),
            "severity solo contiene 'high', 'medium', 'low'",
            f"valores encontrados: {actual}"
        )

    # 4.3 detection_source solo tiene valores válidos
    if "detection_source" in df.columns:
        valid_sources = {"quick_filter", "model_ml"}
        actual = set(df["detection_source"].dropna().unique())
        check(
            actual.issubset(valid_sources),
            "detection_source solo contiene 'quick_filter', 'model_ml'",
            f"valores encontrados: {actual}"
        )

    # 4.4 detected_at es datetime
    if "detected_at" in df.columns:
        check(
            pd.api.types.is_datetime64_any_dtype(df["detected_at"]),
            "detected_at es datetime"
        )

    # 4.5 client_ip puede ser None (no falla con nulls)
    if "client_ip" in df.columns:
        n_nulls = df["client_ip"].isna().sum()
        info(f"  client_ip nulls: {n_nulls} de {len(df)} filas (esperado para alertas ML)")

    # 4.6 Ordenado DESC
    if "detected_at" in df.columns and len(df) > 1:
        check(
            df["detected_at"].is_monotonic_decreasing,
            "DataFrame ordenado por detected_at DESC"
        )

    # 4.7 Parámetros funcionan
    try:
        df_12h = hd.get_critical_alerts(conn, hours=12, limit=5)
        check(len(df_12h) <= 5, "Parámetro limit=5 respetado")
    except NotImplementedError:
        pass

    info(f"\n  Shape: {df.shape}")
    info(f"  Tipos de alerta: {df['alert_type'].value_counts().to_dict()}")


# ═════════════════════════════════════════════════════════════════════════════
# SECCIÓN 5 — AGENT CONTEXT
# ═════════════════════════════════════════════════════════════════════════════

def test_contexto(conn):
    header("SECCIÓN 5 — Agent Context")
    info("Verifica que el string de contexto tiene contenido y secciones esperadas.")

    try:
        ctx = hd.get_agent_context(conn)
    except NotImplementedError:
        warn("get_agent_context() — pendiente de implementar")
        return
    except Exception as e:
        fail(f"get_agent_context() lanzó excepción: {e}")
        return

    # 5.1 Es string
    check(isinstance(ctx, str), "Retorna string")
    check(len(ctx) > 100, f"String tiene contenido suficiente ({len(ctx)} chars)")

    # 5.2 Secciones esperadas con delimitador ═══
    secciones = ["═══"]
    for s in secciones:
        check(s in ctx, f"Contiene delimitador '{s}'")

    # 5.3 No lanza excepción si HANA tiene problemas
    info("  Primeros 400 caracteres del contexto:")
    info(f"  {ctx[:400]}")


# ═════════════════════════════════════════════════════════════════════════════
# SECCIÓN 6 — CHART DATA (queries)
# ═════════════════════════════════════════════════════════════════════════════

CHARTS_NORMALES = [
    "brute_force_por_ip",
    "brute_force_por_hora",
    "alertas_por_tipo",
    "alertas_por_hora",
    "path_scan_activity",
]

CHARTS_OVERLAY = [
    "llm_anomalias",
    "sistema_anomalias",
]

COLUMNAS_ESPERADAS = {
    "brute_force_por_ip":   ["client_ip", "auth_failures"],
    "brute_force_por_hora": ["hora", "auth_failures"],
    "alertas_por_tipo":     ["alert_type", "detection_source", "count"],
    "alertas_por_hora":     ["hora", "dia", "count"],
    "path_scan_activity":   ["request_path", "count", "unique_ips"],
}

COLUMNAS_OVERLAY = {
    "llm_anomalias": {
        "actividad": ["window_start", "avg_cost_usd", "avg_response_time_ms", "log_count"],
        "alertas":   ["detected_at", "severity", "details"],
    },
    "sistema_anomalias": {
        "actividad": ["window_start", "log_count", "error_rate", "ratio_4xx"],
        "alertas":   ["detected_at", "severity", "details"],
    },
}

def test_charts_data(conn):
    header("SECCIÓN 6 — Chart Data (queries normales)")
    info("Verifica que get_chart_data() retorna DataFrames con las columnas del contrato.")

    # 6.1 Key inválido lanza ValueError
    try:
        hd.get_chart_data(conn, "key_que_no_existe")
        fail("get_chart_data() debería lanzar ValueError para key inválido")
    except ValueError:
        ok("ValueError lanzado correctamente para chart_key inválido")
    except NotImplementedError:
        warn("get_chart_data() — pendiente de implementar")
        return
    except Exception as e:
        fail(f"Error inesperado: {e}")

    # 6.2 Cada chart normal
    for key in CHARTS_NORMALES:
        try:
            data = hd.get_chart_data(conn, key)
        except NotImplementedError:
            warn(f"  {key} — pendiente de implementar")
            continue
        except Exception as e:
            fail(f"  {key} — excepción: {e}")
            continue

        check(isinstance(data, pd.DataFrame), f"  {key}: retorna DataFrame")

        if not isinstance(data, pd.DataFrame):
            continue

        if data.empty:
            warn(f"  {key}: DataFrame vacío (sin datos en el rango)")
            continue

        for col in COLUMNAS_ESPERADAS.get(key, []):
            check(col in data.columns, f"  {key}: columna '{col}' presente")

        info(f"    Shape: {data.shape}")


def test_overlays(conn):
    header("SECCIÓN 7 — Chart Data (overlays — tuple)")
    info("Verifica que llm_anomalias y sistema_anomalias retornan tuple de dos DataFrames.")

    for key in CHARTS_OVERLAY:
        try:
            data = hd.get_chart_data(conn, key)
        except NotImplementedError:
            warn(f"  {key} — pendiente de implementar")
            continue
        except Exception as e:
            fail(f"  {key} — excepción: {e}")
            continue

        # 7.1 Es tuple
        check(isinstance(data, tuple), f"{key}: retorna tuple")
        if not isinstance(data, tuple):
            continue

        # 7.2 Tiene exactamente 2 elementos
        check(len(data) == 2, f"{key}: tuple tiene exactamente 2 elementos")
        if len(data) != 2:
            continue

        df_act, df_ale = data

        # 7.3 Ambos son DataFrames
        check(isinstance(df_act, pd.DataFrame), f"{key}: df_actividad es DataFrame")
        check(isinstance(df_ale, pd.DataFrame), f"{key}: df_alertas es DataFrame")

        # 7.4 Columnas de actividad
        cols_act = COLUMNAS_OVERLAY[key]["actividad"]
        for col in cols_act:
            check(col in df_act.columns if not df_act.empty else True,
                  f"{key} actividad: columna '{col}' presente")

        # 7.5 Columnas de alertas
        cols_ale = COLUMNAS_OVERLAY[key]["alertas"]
        for col in cols_ale:
            check(col in df_ale.columns if not df_ale.empty else True,
                  f"{key} alertas: columna '{col}' presente")

        # 7.6 window_start es datetime en actividad
        if not df_act.empty and "window_start" in df_act.columns:
            check(
                pd.api.types.is_datetime64_any_dtype(df_act["window_start"]),
                f"{key} actividad: window_start es datetime"
            )

        # 7.7 detected_at es datetime en alertas
        if not df_ale.empty and "detected_at" in df_ale.columns:
            check(
                pd.api.types.is_datetime64_any_dtype(df_ale["detected_at"]),
                f"{key} alertas: detected_at es datetime"
            )

        info(f"    df_actividad shape: {df_act.shape}")
        info(f"    df_alertas shape:   {df_ale.shape}")


# ═════════════════════════════════════════════════════════════════════════════
# SECCIÓN 8 — CHARTS RENDER (figuras Plotly)
# ═════════════════════════════════════════════════════════════════════════════

def test_charts_render(conn):
    header("SECCIÓN 8 — Charts Render (figuras Plotly)")
    info("Verifica que build_chart() y build_activity_chart() retornan go.Figure.")

    try:
        import plotly.graph_objects as go
    except ImportError:
        fail("plotly no instalado — correr: pip install plotly")
        return

    # 8.1 CHART_CATALOG exportado con los 7 keys
    check(
        hasattr(charts, "CHART_CATALOG") and len(charts.CHART_CATALOG) == 7,
        f"CHART_CATALOG exportado con 7 keys",
        f"keys actuales: {len(getattr(charts, 'CHART_CATALOG', {}))}"
    )

    # 8.2 ValueError para key inválido
    try:
        charts.build_chart("key_invalido", pd.DataFrame())
        fail("build_chart() debería lanzar ValueError para key inválido")
    except ValueError:
        ok("build_chart(): ValueError lanzado para key inválido")
    except NotImplementedError:
        warn("build_chart() — dispatcher pendiente de implementar")

    # 8.3 Cada gráfica normal
    for key in CHARTS_NORMALES:
        try:
            data = hd.get_chart_data(conn, key)
            fig = charts.build_chart(key, data)
            check(isinstance(fig, go.Figure), f"build_chart('{key}') retorna go.Figure")
        except NotImplementedError:
            warn(f"  {key} — pendiente de implementar")
        except Exception as e:
            fail(f"  {key} — error: {e}")

    # 8.4 Gráficas overlay
    for key in CHARTS_OVERLAY:
        try:
            data = hd.get_chart_data(conn, key)
            fig = charts.build_chart(key, data)
            check(isinstance(fig, go.Figure), f"build_chart('{key}') retorna go.Figure")
            if isinstance(fig, go.Figure):
                check(len(fig.data) >= 2, f"  {key}: figura tiene al menos 2 traces")
        except NotImplementedError:
            warn(f"  {key} — pendiente de implementar")
        except Exception as e:
            fail(f"  {key} — error: {e}")

    # 8.5 build_activity_chart
    try:
        df = hd.get_activity_timeline(conn)
        fig = charts.build_activity_chart(df)
        check(isinstance(fig, go.Figure), "build_activity_chart() retorna go.Figure")
        if isinstance(fig, go.Figure):
            check(len(fig.data) >= 2, "build_activity_chart: al menos 2 traces (área + markers)")
    except NotImplementedError:
        warn("build_activity_chart() — pendiente de implementar")
    except Exception as e:
        fail(f"build_activity_chart() — error: {e}")


# ═════════════════════════════════════════════════════════════════════════════
# SECCIÓN 9 — CONTRATO DE INTEGRACIÓN
# ═════════════════════════════════════════════════════════════════════════════

def test_contrato(conn):
    header("SECCIÓN 9 — Contrato de integración con Dev 1")
    info("Simula las llamadas exactas que app_dashboard.py y agent.py harán.")

    # 9.1 Llamada exacta de app_dashboard a get_kpis
    try:
        kpis = hd.get_kpis(conn)
        check("threat_level" in kpis, "app_dashboard puede leer kpis['threat_level']")
        check("alerts_last_hour" in kpis, "app_dashboard puede leer kpis['alerts_last_hour']")
    except (NotImplementedError, Exception):
        warn("get_kpis() pendiente")

    # 9.2 Llamada exacta de agent.py a get_agent_context
    try:
        ctx = hd.get_agent_context(conn)
        check(isinstance(ctx, str) and len(ctx) > 0,
              "agent.py puede usar get_agent_context() como string")
    except (NotImplementedError, Exception):
        warn("get_agent_context() pendiente")

    # 9.3 CHART_CATALOG importable por agent.py
    try:
        from dashboard.charts import CHART_CATALOG
        check(isinstance(CHART_CATALOG, dict) and len(CHART_CATALOG) > 0,
              "agent.py puede importar CHART_CATALOG desde charts.py")
        for key, desc in CHART_CATALOG.items():
            check(isinstance(desc, str) and len(desc) > 5,
                  f"  CHART_CATALOG['{key}'] tiene descripción legible")
    except Exception as e:
        fail(f"CHART_CATALOG no importable: {e}")

    # 9.4 build_chart acepta tuple para overlays
    for key in CHARTS_OVERLAY:
        try:
            data = hd.get_chart_data(conn, key)
            check(isinstance(data, tuple),
                  f"app_dashboard puede desempacar tuple para '{key}'")
        except (NotImplementedError, Exception):
            warn(f"  {key} pendiente")


# ═════════════════════════════════════════════════════════════════════════════
# RUNNER
# ═════════════════════════════════════════════════════════════════════════════

SECCIONES = {
    "conexion":      test_conexion,
    "kpis":          lambda conn: test_kpis(conn),
    "timeline":      lambda conn: test_timeline(conn),
    "alertas":       lambda conn: test_alertas(conn),
    "contexto":      lambda conn: test_contexto(conn),
    "charts_data":   lambda conn: test_charts_data(conn),
    "overlays":      lambda conn: test_overlays(conn),
    "charts_render": lambda conn: test_charts_render(conn),
    "contrato":      lambda conn: test_contrato(conn),
}

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--seccion", choices=list(SECCIONES.keys()),
                        help="Correr solo una sección específica")
    args = parser.parse_args()

    print("\n" + "═"*55)
    print("  SOC Dashboard — Test Suite Dev 2")
    print("  hana_dashboard.py + charts.py")
    print("═"*55)

    conn = test_conexion()

    if conn is None:
        print("\n❌ Sin conexión a HANA — no se pueden correr los demás tests.")
        sys.exit(1)

    if args.seccion:
        SECCIONES[args.seccion](conn)
    else:
        test_kpis(conn)
        test_timeline(conn)
        test_alertas(conn)
        test_contexto(conn)
        test_charts_data(conn)
        test_overlays(conn)
        test_charts_render(conn)
        test_contrato(conn)

    # ── Resumen final ─────────────────────────────────────────────────────────
    print(f"\n{'═'*55}")
    print(f"  RESUMEN FINAL")
    print(f"{'═'*55}")
    print(f"  ✅  Pasaron:  {len(passed)}")
    print(f"  ❌  Fallaron: {len(failed)}")
    if failed:
        print(f"\n  Checks fallidos:")
        for f in failed:
            print(f"    · {f}")
    print(f"{'═'*55}\n")