# =============================================================================
# app/explore.py
# =============================================================================
# Propósito: analizar el CSV más reciente extraído por ingest.py,
#            imprimir un reporte técnico completo del dataset,
#            y guardar una versión procesada lista para el modelo.
#
# Este script es el puente entre la extracción cruda (ingest.py)
# y el procesamiento (etl.py). Su output principal es:
#   1. Reporte en consola con toda la información del schema real
#   2. data/processed/logs_limpios.csv — datos listos para el siguiente paso
#
# Conocimiento previo del schema (confirmado con datos reales):
#   Los logs tienen DOS tipos con columnas distintas:
#
#   SISTEMA (sap_function_log_type: INFO, WARNING, ERROR, DEBUG, AUDIT, PERF, SECURITY)
#     Columnas activas:  service_id, http_status_code, client_ip
#     Columnas vacías:   todas las llm_*
#
#   LLM (sap_function_log_type: LLM_REQUEST, LLM_ERROR, LLM_TIMEOUT)
#     Columnas activas:  llm_model_id, llm_provider, llm_status, llm_cost_usd,
#                        llm_response_time_ms, llm_prompt, llm_total_tokens, etc.
#     Columnas vacías:   service_id, http_status_code, client_ip
#
#   Esto NO es un error — es el diseño de la API. No rellenar estos nulos.
#
# Cómo ejecutarlo (desde la raíz del proyecto, con .venv activo):
#   python app/explore.py
# =============================================================================

import pandas as pd
import glob
import os
import sys


# =============================================================================
# PASO 1 — Cargar el CSV más reciente de data/raw/
# =============================================================================

print("=" * 70)
print("EXPLORE.PY — Análisis del dataset de logs SAP")
print("=" * 70)

# glob busca todos los archivos que coincidan con el patrón
# sorted() los ordena alfabéticamente — como los nombres incluyen timestamp,
# el orden alfabético coincide con el orden cronológico
archivos = sorted(glob.glob("data/raw/logs_*.csv"))

if not archivos:
    print("❌ No hay datos en data/raw/")
    print("   Ejecuta primero: python app/ingest.py")
    sys.exit(1)

# Tomamos el último (más reciente)
archivo_reciente = archivos[-1]
print(f"\n📂 Archivo cargado: {archivo_reciente}")
print(f"   (Archivos disponibles en data/raw/: {len(archivos)})")

df = pd.read_csv(archivo_reciente, low_memory=False)
# low_memory=False evita que pandas haga inferencia de tipos por chunks,
# lo que puede causar tipos inconsistentes en columnas mixtas


# =============================================================================
# PASO 2 — Dimensiones generales
# =============================================================================

print("\n" + "─" * 70)
print("DIMENSIONES GENERALES")
print("─" * 70)
print(f"  Filas:    {len(df):,}")
print(f"  Columnas: {len(df.columns)}")


# =============================================================================
# PASO 3 — Columnas y tipos de datos
# =============================================================================

print("\n" + "─" * 70)
print("COLUMNAS Y TIPOS DE DATOS")
print("─" * 70)
for col in df.columns:
    tipo = str(df[col].dtype)
    nulos = df[col].isna().sum()
    pct_nulos = (nulos / len(df)) * 100
    # Mostrar con indicador visual si tiene muchos nulos
    indicador = "⚠️ " if pct_nulos > 50 else "  "
    print(f"  {indicador}{col:<45} {tipo:<10} nulos: {nulos:>5,} ({pct_nulos:>5.1f}%)")


# =============================================================================
# PASO 4 — Distribución por tipo de log (columna discriminante clave)
# =============================================================================

print("\n" + "─" * 70)
print("DISTRIBUCIÓN POR TIPO DE LOG (sap_function_log_type)")
print("─" * 70)

if "sap_function_log_type" in df.columns:
    distribucion = df["sap_function_log_type"].value_counts()
    total = len(df)
    for tipo, cantidad in distribucion.items():
        pct = (cantidad / total) * 100
        print(f"  {tipo:<25} {cantidad:>6,} registros  ({pct:>5.1f}%)")

    # Separar en dos subsets para análisis diferenciado
    # Los tipos LLM empiezan con "LLM_" — usamos str.startswith para filtrar
    mask_llm     = df["sap_function_log_type"].str.startswith("LLM", na=False)
    system_logs  = df[~mask_llm]   # ~ es el operador NOT — todo lo que NO es LLM
    llm_logs     = df[mask_llm]

    print(f"\n  → Logs de SISTEMA: {len(system_logs):,}")
    print(f"  → Logs de LLM:     {len(llm_logs):,}")
else:
    print("  ⚠️  Columna sap_function_log_type no encontrada")
    system_logs = df
    llm_logs    = pd.DataFrame()


# =============================================================================
# PASO 5 — Análisis de columnas de SISTEMA
# =============================================================================

print("\n" + "─" * 70)
print("COLUMNAS DE SISTEMA (presentes en logs no-LLM)")
print("─" * 70)

columnas_sistema = [
    "service_id",
    "http_status_code",
    "client_ip",
    "sap_source_type",
    "sap_function_application",
    "sap_function_message",
    "sap_app_env",
    "region_name",
    "region_code",
    "macro_region",
    "headers_http_request_method",
    "heathers_request_path",   # nota: typo en la API, viene así
]

for col in columnas_sistema:
    if col in df.columns and not system_logs.empty:
        valores = system_logs[col].dropna()
        if len(valores) > 0:
            print(f"\n  TOP 10 en '{col}':")
            top = valores.value_counts().head(10)
            for val, cnt in top.items():
                print(f"    {str(val):<50} {cnt:>6,}")
        else:
            print(f"\n  '{col}': sin valores en logs de sistema")


# =============================================================================
# PASO 6 — Análisis de columnas LLM
# =============================================================================

print("\n" + "─" * 70)
print("COLUMNAS LLM (presentes en logs LLM_*)")
print("─" * 70)

columnas_llm = [
    "llm_model_id",
    "llm_provider",
    "llm_status",
    "llm_prompt_category",
    "llm_finish_reason",
    "llm_error_message",
]

for col in columnas_llm:
    if col in df.columns and not llm_logs.empty:
        valores = llm_logs[col].dropna()
        if len(valores) > 0:
            print(f"\n  TOP 10 en '{col}':")
            top = valores.value_counts().head(10)
            for val, cnt in top.items():
                print(f"    {str(val):<60} {cnt:>6,}")
        else:
            print(f"\n  '{col}': sin valores en logs LLM")

# Métricas numéricas LLM
print("\n  MÉTRICAS NUMÉRICAS LLM:")
metricas_llm = [
    "llm_cost_usd",
    "llm_total_tokens",
    "llm_prompt_tokens",
    "llm_completion_tokens",
    "llm_response_time_ms",
    "llm_response_size_bytes",
    "llm_temperature",
    "llm_top_p",
]

for col in metricas_llm:
    if col in df.columns and not llm_logs.empty:
        serie = pd.to_numeric(llm_logs[col], errors="coerce").dropna()
        if len(serie) > 0:
            print(f"\n  {col}:")
            print(f"    min={serie.min():.4f}  max={serie.max():.4f}  "
                  f"media={serie.mean():.4f}  mediana={serie.median():.4f}")


# =============================================================================
# PASO 7 — Análisis temporal
# =============================================================================

print("\n" + "─" * 70)
print("ANÁLISIS TEMPORAL")
print("─" * 70)

# La columna principal de tiempo es @timestamp
ts_cols = [c for c in df.columns if "timestamp" in c.lower() or c == "@timestamp"]

for ts_col in ts_cols:
    ts = pd.to_datetime(df[ts_col], errors="coerce", utc=True)
    nulos_ts = ts.isna().sum()
    print(f"\n  Columna: '{ts_col}'")
    print(f"    Desde:         {ts.min()}")
    print(f"    Hasta:         {ts.max()}")
    print(f"    No parseables: {nulos_ts}")
    if nulos_ts == 0:
        duracion = ts.max() - ts.min()
        print(f"    Duración:      {duracion}")


# =============================================================================
# PASO 8 — Muestra de filas reales
# =============================================================================

print("\n" + "─" * 70)
print("MUESTRA — 2 filas de SISTEMA + 2 filas de LLM")
print("─" * 70)

pd.set_option("display.max_columns", None)
pd.set_option("display.max_colwidth", 40)
pd.set_option("display.width", 200)

if not system_logs.empty:
    print("\n  Logs de SISTEMA:")
    print(system_logs.head(2).T.to_string())   # .T transpone para leer mejor

if not llm_logs.empty:
    print("\n  Logs de LLM:")
    print(llm_logs.head(2).T.to_string())


# =============================================================================
# PASO 9 — Guardar datos procesados
# =============================================================================

print("\n" + "─" * 70)
print("GUARDANDO DATOS PROCESADOS")
print("─" * 70)

os.makedirs("data/processed", exist_ok=True)

# Guardar el dataset completo sin modificar (los nulos quedan como están)
df.to_csv("data/processed/logs_limpios.csv", index=False)
print(f"  ✓ Dataset completo: data/processed/logs_limpios.csv  ({len(df):,} filas)")

# Guardar subsets separados — útil para el AI Specialist
if not system_logs.empty:
    system_logs.to_csv("data/processed/logs_sistema.csv", index=False)
    print(f"  ✓ Logs de sistema:  data/processed/logs_sistema.csv  ({len(system_logs):,} filas)")

if not llm_logs.empty:
    llm_logs.to_csv("data/processed/logs_llm.csv", index=False)
    print(f"  ✓ Logs LLM:         data/processed/logs_llm.csv  ({len(llm_logs):,} filas)")

# Actualizar muestra de 10 filas (5 sistema + 5 LLM si es posible)
muestra = pd.concat([
    system_logs.head(5) if not system_logs.empty else pd.DataFrame(),
    llm_logs.head(5)    if not llm_logs.empty    else pd.DataFrame(),
]).reset_index(drop=True)
muestra.to_csv("data/raw/sample_10.csv", index=False)
print(f"  ✓ Muestra equipo:   data/raw/sample_10.csv  ({len(muestra)} filas)")

print("\n" + "=" * 70)
print("✅ EXPLORACIÓN COMPLETADA")
print("=" * 70)
print("   Próximo paso: revisar output arriba y actualizar data/SCHEMA.md")
print("=" * 70)
