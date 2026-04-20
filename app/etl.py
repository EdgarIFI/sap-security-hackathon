# =============================================================================
# app/etl.py
# =============================================================================
# Propósito: transformar los logs crudos extraídos por ingest.py en datasets
#            limpios, tipados y estructurados, listos para el modelo de ML.
#
# Posición en el pipeline:
#   ingest.py  →  [etl.py]  →  model.py  →  alerting.py
#   (extrae)      (limpia)     (detecta)     (alerta)
#
# Responsabilidades de este módulo:
#   1. Eliminar columnas sin valor analítico (_score, _ignored, _index)
#   2. Normalizar tipos: timestamps → datetime64 UTC, numéricos → float/int
#   3. Separar logs por tipo (Sistema vs LLM) en datasets especializados
#   4. Producir un reporte de calidad que documente el estado de los datos
#
# Lo que este módulo NO hace (por diseño deliberado):
#   - No rellena nulos: los nulos LLM en registros Sistema y viceversa
#     son parte del schema de la API, no errores a corregir.
#   - No hace feature engineering: esa es responsabilidad del AI Specialist.
#   - No modifica los datos crudos en data/raw/: siempre se puede reprocesar.
#   - No toma decisiones sobre qué columnas usa el modelo: eso es del modelo.
#
# Principio de diseño — idempotencia:
#   Correr este script N veces sobre los mismos datos produce exactamente
#   el mismo resultado. No hay efectos acumulativos ni estado externo.
#
# Outputs producidos:
#   data/processed/clean_full.csv     → dataset completo limpio (todos los tipos)
#   data/processed/clean_sistema.csv  → solo logs de Sistema
#   data/processed/clean_llm.csv      → solo logs de LLM
#
# Cómo ejecutarlo (desde la raíz del proyecto, con .venv activo):
#   python app/etl.py
# =============================================================================

import pandas as pd
import numpy as np
import glob
import os
import sys
from datetime import datetime


# =============================================================================
# CONSTANTES DE CONFIGURACIÓN
# =============================================================================
# Centralizamos aquí todas las decisiones de qué columnas tratar y cómo.
# Si la API cambia, solo hay que modificar estas listas — no el código.

# Columnas a eliminar: campos internos de Elasticsearch sin valor analítico.
# Justificación por columna:
#   _score   → relevance score interno de ES, no tiene significado de negocio
#   _ignored → campo de error interno de ES, estaba vacío en todos los registros vistos
#   _index   → nombre del índice de ES (ej: "llm-logs-2026.04"), no aporta señal
COLUMNAS_A_ELIMINAR = [
    "_score",
    "_ignored",
    "_index",
]

# Columnas de timestamp que deben convertirse a datetime64 UTC.
# Vienen como strings ISO 8601 con timezone: "2026-04-20T17:00:00.000Z"
COLUMNAS_TIMESTAMP = [
    "@timestamp",
    "@event_time_requested",
]

# Columnas numéricas que pueden venir como string o con valores mixtos.
# Las convertimos a float de forma segura (errors="coerce" → NaN si falla).
# Son todas columnas LLM — en registros de Sistema vendrán como NaN (esperado).
COLUMNAS_NUMERICAS = [
    "llm_prompt_tokens",
    "llm_completion_tokens",
    "llm_total_tokens",
    "llm_cost_usd",
    "llm_response_time_ms",
    "llm_response_size_bytes",
    "llm_temperature",
    "llm_top_p",
    "sap_llm_response_size",
    "sap_llm_response_time",
    "_score",               # incluido aquí por si no fue eliminado antes
]

# Columnas booleanas que pueden venir como "TRUE"/"FALSE" en string
COLUMNAS_BOOLEANAS = [
    "llm_stream",
]

# Prefijo que distingue logs LLM de logs de Sistema
PREFIJO_LLM = "LLM"

# Columna discriminante principal del dataset
COL_TIPO_LOG = "sap_function_log_type"

# Directorios de trabajo
DIR_RAW       = "data/raw"
DIR_PROCESSED = "data/processed"


# =============================================================================
# FUNCIÓN: cargar_datos_crudos()
# =============================================================================

def cargar_datos_crudos() -> pd.DataFrame:
    """
    Carga el CSV crudo más reciente de data/raw/.

    Siempre trabaja sobre el archivo más reciente porque los nombres
    incluyen el timestamp de ventana y se ordenan cronológicamente.

    Returns:
        pd.DataFrame con los datos crudos sin ninguna modificación.

    Raises:
        SystemExit si no hay archivos disponibles.
    """
    archivos = sorted(glob.glob(f"{DIR_RAW}/logs_*.csv"))

    if not archivos:
        print("❌ No hay datos en data/raw/")
        print("   Ejecuta primero: python app/ingest.py")
        sys.exit(1)

    archivo = archivos[-1]
    print(f"  📂 Cargando: {archivo}")

    # low_memory=False garantiza inferencia de tipos consistente.
    # Sin esto, pandas puede inferir tipos distintos en chunks del mismo CSV,
    # lo que produce columnas con dtype "object" inconsistente.
    df = pd.read_csv(archivo, low_memory=False)

    print(f"  ✓ Cargado: {len(df):,} filas × {len(df.columns)} columnas")
    return df, archivo


# =============================================================================
# FUNCIÓN: eliminar_columnas_internas()
# =============================================================================

def eliminar_columnas_internas(df: pd.DataFrame) -> pd.DataFrame:
    """
    Elimina columnas sin valor analítico (campos internos de Elasticsearch).

    Solo elimina columnas que existan en el DataFrame — si la API deja
    de enviar alguna de estas columnas, el script no falla.

    Args:
        df: DataFrame con datos crudos.

    Returns:
        DataFrame sin las columnas internas.
    """
    # Filtramos la lista para eliminar solo columnas que realmente existan
    # Así el script es robusto ante cambios en la API
    a_eliminar = [col for col in COLUMNAS_A_ELIMINAR if col in df.columns]
    no_encontradas = [col for col in COLUMNAS_A_ELIMINAR if col not in df.columns]

    if a_eliminar:
        df = df.drop(columns=a_eliminar)
        print(f"  ✓ Columnas eliminadas ({len(a_eliminar)}): {a_eliminar}")

    if no_encontradas:
        print(f"  ℹ️  No encontradas (ya no vienen en la API): {no_encontradas}")

    return df


# =============================================================================
# FUNCIÓN: normalizar_timestamps()
# =============================================================================

def normalizar_timestamps(df: pd.DataFrame) -> pd.DataFrame:
    """
    Convierte columnas de timestamp de string ISO 8601 a datetime64 UTC.

    Por qué datetime64 con UTC explícito:
      - Permite operaciones de ventana temporal (groupby por hora, minuto, etc.)
      - Evita ambigüedades de timezone en comparaciones
      - Compatible con el particionamiento temporal de SAP HANA
      - Pandas puede calcular duraciones, deltas y resampling con este tipo

    Las filas con timestamps no parseables se marcan como NaT (Not a Time),
    que es el equivalente a NaN para fechas en pandas. No se eliminan.

    Args:
        df: DataFrame con columnas de timestamp como strings.

    Returns:
        DataFrame con columnas de timestamp como datetime64[ns, UTC].
    """
    for col in COLUMNAS_TIMESTAMP:
        if col not in df.columns:
            continue

        antes_nulos = df[col].isna().sum()

        # utc=True fuerza interpretación UTC y produce dtype timezone-aware
        # errors="coerce" convierte valores no parseables a NaT en lugar de fallar
        df[col] = pd.to_datetime(df[col], utc=True, errors="coerce")

        despues_nulos = df[col].isna().sum()
        nuevos_nulos  = despues_nulos - antes_nulos

        if nuevos_nulos > 0:
            print(f"  ⚠️  '{col}': {nuevos_nulos} timestamps no parseables → NaT")
        else:
            print(f"  ✓ '{col}': convertido a datetime64 UTC "
                  f"(rango: {df[col].min()} → {df[col].max()})")

    return df


# =============================================================================
# FUNCIÓN: normalizar_numericos()
# =============================================================================

def normalizar_numericos(df: pd.DataFrame) -> pd.DataFrame:
    """
    Convierte columnas numéricas a float64 de forma segura.

    Por qué float64 y no int:
      - Las columnas LLM como llm_cost_usd son decimales por naturaleza
      - Algunas columnas de tokens podrían ser int, pero float es compatible
        con NaN (int de pandas no admite NaN nativamente sin usar Int64 nullable)
      - Usar float64 uniformemente simplifica el código del modelo de ML

    Valores no convertibles (strings inesperados, símbolos) se convierten a NaN.
    Esto es preferible a fallar — un NaN es manejable, una excepción no controlada no.

    Args:
        df: DataFrame con columnas numéricas posiblemente en formato string.

    Returns:
        DataFrame con columnas numéricas como float64.
    """
    for col in COLUMNAS_NUMERICAS:
        if col not in df.columns:
            continue

        # Solo procesamos si la columna no es ya numérica
        if pd.api.types.is_numeric_dtype(df[col]):
            continue

        antes_nulos = df[col].isna().sum()
        df[col] = pd.to_numeric(df[col], errors="coerce")
        despues_nulos = df[col].isna().sum()
        nuevos_nulos  = despues_nulos - antes_nulos

        if nuevos_nulos > 0:
            print(f"  ⚠️  '{col}': {nuevos_nulos} valores no numéricos → NaN")
        else:
            print(f"  ✓ '{col}': normalizado a float64")

    return df


# =============================================================================
# FUNCIÓN: normalizar_booleanos()
# =============================================================================

def normalizar_booleanos(df: pd.DataFrame) -> pd.DataFrame:
    """
    Convierte columnas booleanas de string ("TRUE"/"FALSE") a bool de Python.

    La columna llm_stream viene como "TRUE" o "FALSE" en string.
    Convertirla a bool permite operaciones lógicas directas en el modelo.

    Valores no reconocidos se convierten a NaN para no perder filas.

    Args:
        df: DataFrame con columnas booleanas como strings.

    Returns:
        DataFrame con columnas booleanas como bool (o NaN donde aplique).
    """
    mapa_bool = {
        "true":  True,
        "false": False,
        "1":     True,
        "0":     False,
    }

    for col in COLUMNAS_BOOLEANAS:
        if col not in df.columns:
            continue

        # Normalizamos a minúsculas para comparación case-insensitive
        df[col] = (
            df[col]
            .astype(str)
            .str.lower()
            .str.strip()
            .map(mapa_bool)   # map() reemplaza valores según el diccionario
                              # los valores no encontrados quedan como NaN
        )

        print(f"  ✓ '{col}': normalizado a bool")

    return df


# =============================================================================
# FUNCIÓN: separar_por_tipo()
# =============================================================================

def separar_por_tipo(df: pd.DataFrame) -> tuple:
    """
    Separa el dataset en dos subsets según el tipo de log.

    Criterio de separación:
      - LLM:     sap_function_log_type empieza con "LLM"  → LLM_REQUEST, LLM_ERROR, LLM_TIMEOUT
      - Sistema: todo lo demás                            → INFO, WARNING, ERROR, DEBUG, AUDIT, PERF, SECURITY

    Por qué separar en lugar de usar una sola tabla:
      - Las features de ML son completamente distintas entre tipos
      - Los modelos de detección pueden necesitar umbrales distintos
      - El Data Architect puede querer tablas separadas en HANA
      - Evita confusión al analizar nulos (que son esperados por diseño)

    Args:
        df: DataFrame completo ya limpio.

    Returns:
        tuple: (df_sistema, df_llm)
    """
    if COL_TIPO_LOG not in df.columns:
        print(f"  ⚠️  Columna '{COL_TIPO_LOG}' no encontrada — no se puede separar")
        return df, pd.DataFrame()

    mask_llm    = df[COL_TIPO_LOG].str.startswith(PREFIJO_LLM, na=False)
    df_sistema  = df[~mask_llm].copy()   # .copy() evita SettingWithCopyWarning
    df_llm      = df[mask_llm].copy()    # al modificar subsets más adelante

    total = len(df)
    print(f"  ✓ Logs de Sistema: {len(df_sistema):,} ({len(df_sistema)/total*100:.1f}%)")
    print(f"  ✓ Logs de LLM:     {len(df_llm):,} ({len(df_llm)/total*100:.1f}%)")

    return df_sistema, df_llm


# =============================================================================
# FUNCIÓN: reporte_calidad()
# =============================================================================

def reporte_calidad(df: pd.DataFrame, nombre: str):
    """
    Imprime un reporte de calidad del DataFrame procesado.

    Por qué un reporte de calidad en el ETL:
      - Permite detectar rápidamente si la API cambió su schema
      - Documenta el estado de los datos antes de pasarlos al modelo
      - Facilita el debugging cuando el modelo produce resultados inesperados

    Args:
        df:     DataFrame a auditar.
        nombre: nombre descriptivo para el reporte (ej: "Sistema", "LLM").
    """
    if df.empty:
        print(f"  ⚠️  Dataset '{nombre}' está vacío")
        return

    print(f"\n  [{nombre}] — {len(df):,} filas × {len(df.columns)} columnas")

    # Columnas con nulos — solo mostramos las que tienen nulos para no saturar
    nulos = df.isna().sum()
    nulos_presentes = nulos[nulos > 0]

    if len(nulos_presentes) > 0:
        print(f"  [{nombre}] Columnas con nulos ({len(nulos_presentes)}):")
        for col, n in nulos_presentes.items():
            pct = n / len(df) * 100
            # Distinguimos nulos esperados (>90% = columna del otro tipo)
            # de nulos inesperados (<90% = posible problema de datos)
            etiqueta = "esperado" if pct > 90 else "⚠️ revisar"
            print(f"    {col:<45} {n:>5,} ({pct:>5.1f}%) [{etiqueta}]")
    else:
        print(f"  [{nombre}] Sin nulos inesperados ✓")

    # Tipos de datos resultantes
    print(f"  [{nombre}] Tipos de datos:")
    for col in df.columns:
        print(f"    {col:<45} {str(df[col].dtype)}")


# =============================================================================
# FUNCIÓN: guardar_outputs()
# =============================================================================

def guardar_outputs(df_full: pd.DataFrame,
                    df_sistema: pd.DataFrame,
                    df_llm: pd.DataFrame) -> dict:
    """
    Guarda los tres datasets procesados en data/processed/.

    Por qué tres archivos:
      - clean_full.csv    → para quien necesite todo el contexto (PM, análisis cruzado)
      - clean_sistema.csv → para el AI Specialist (features de seguridad de red)
      - clean_llm.csv     → para el AI Specialist (features de uso de LLM)
      El Data Architect usará estos mismos archivos para poblar HANA.

    Args:
        df_full:    Dataset completo limpio.
        df_sistema: Subset de logs de Sistema.
        df_llm:     Subset de logs de LLM.

    Returns:
        dict con las rutas de los archivos generados.
    """
    os.makedirs(DIR_PROCESSED, exist_ok=True)

    rutas = {}

    outputs = [
        (df_full,    "clean_full.csv",    "Dataset completo limpio"),
        (df_sistema, "clean_sistema.csv", "Logs de Sistema"),
        (df_llm,     "clean_llm.csv",     "Logs de LLM"),
    ]

    for df, nombre_archivo, descripcion in outputs:
        if df.empty:
            print(f"  ⚠️  '{descripcion}' está vacío — no se guarda")
            continue

        ruta = f"{DIR_PROCESSED}/{nombre_archivo}"

        # Para timestamps: guardar en ISO 8601 para preservar el timezone
        # date_format se aplica a columnas datetime automáticamente
        df.to_csv(ruta, index=False, date_format="%Y-%m-%dT%H:%M:%S%z")

        rutas[nombre_archivo] = ruta
        print(f"  ✓ {descripcion:<25} → {ruta}  ({len(df):,} filas)")

    return rutas


# =============================================================================
# PIPELINE PRINCIPAL: run_etl()
# =============================================================================

def run_etl() -> dict:
    """
    Ejecuta el pipeline ETL completo en secuencia.

    Cada paso recibe el DataFrame del paso anterior — si un paso falla,
    el pipeline se detiene antes de escribir outputs incompletos.

    Returns:
        dict con rutas de los archivos generados.
    """
    print("\n" + "─" * 70)
    print("PASO 1 — Cargando datos crudos")
    print("─" * 70)
    df, archivo_fuente = cargar_datos_crudos()
    filas_originales = len(df)

    print("\n" + "─" * 70)
    print("PASO 2 — Eliminando columnas sin valor analítico")
    print("─" * 70)
    df = eliminar_columnas_internas(df)

    print("\n" + "─" * 70)
    print("PASO 3 — Normalizando timestamps")
    print("─" * 70)
    df = normalizar_timestamps(df)

    print("\n" + "─" * 70)
    print("PASO 4 — Normalizando columnas numéricas")
    print("─" * 70)
    df = normalizar_numericos(df)

    print("\n" + "─" * 70)
    print("PASO 5 — Normalizando columnas booleanas")
    print("─" * 70)
    df = normalizar_booleanos(df)

    print("\n" + "─" * 70)
    print("PASO 6 — Separando por tipo de log")
    print("─" * 70)
    df_sistema, df_llm = separar_por_tipo(df)

    print("\n" + "─" * 70)
    print("PASO 7 — Reporte de calidad")
    print("─" * 70)
    reporte_calidad(df,         "COMPLETO")
    reporte_calidad(df_sistema, "SISTEMA")
    reporte_calidad(df_llm,     "LLM")

    print("\n" + "─" * 70)
    print("PASO 8 — Guardando outputs")
    print("─" * 70)
    rutas = guardar_outputs(df, df_sistema, df_llm)

    # Verificación de integridad: el total de filas no debe cambiar
    # Si cambia, algo en el pipeline está filtrando filas accidentalmente
    filas_finales = len(df)
    if filas_finales != filas_originales:
        print(f"\n  ⚠️  ADVERTENCIA: filas originales ({filas_originales:,}) "
              f"≠ filas finales ({filas_finales:,})")
        print(f"     El pipeline no debe eliminar filas — revisar el código.")
    else:
        print(f"\n  ✓ Integridad verificada: {filas_finales:,} filas conservadas")

    return rutas


# =============================================================================
# PUNTO DE ENTRADA
# =============================================================================

if __name__ == "__main__":

    print("=" * 70)
    print("ETL.PY — Limpieza y estructuración de logs SAP")
    print(f"Ejecutado: {datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S')} UTC")
    print("=" * 70)

    try:
        rutas = run_etl()

        print("\n" + "=" * 70)
        print("✅ ETL COMPLETADO")
        print("=" * 70)
        print("  Archivos listos para el modelo de ML:")
        for nombre, ruta in rutas.items():
            print(f"    {ruta}")
        print()
        print("  Próximo paso: python app/model.py")
        print("=" * 70)

    except KeyboardInterrupt:
        print("\n⚠️  ETL interrumpido por el usuario.")
        sys.exit(0)

    except Exception as e:
        print(f"\n❌ Error inesperado en ETL: {type(e).__name__}: {e}")
        print("   Revisa el stack trace arriba para más detalles.")
        raise   # re-lanzamos para ver el traceback completo
