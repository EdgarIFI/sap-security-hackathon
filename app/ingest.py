# =============================================================================
# app/ingest.py
# =============================================================================
# Propósito: extraer TODOS los logs de la ventana UTC actual de 30 minutos
#            y guardarlos en disco como CSV crudo, sin modificaciones.
#
# Posición en el pipeline:
#   [ingest.py] → etl.py → model.py → alerting.py
#
# Su única responsabilidad es traer los datos fielmente desde la API.
# No limpia, no transforma, no analiza. Solo extrae y guarda.
#
# Cómo funciona internamente:
#   1. GET /info  → descubre cuántas páginas hay en la ventana actual
#   2. Loop GET /logs/current?page=1..N → acumula todos los registros
#   3. Convierte la lista acumulada a DataFrame de pandas
#   4. Guarda CSV en data/raw/ con timestamp de ventana en el nombre
#
# Por qué guardamos con timestamp en el nombre del archivo:
#   Cada ventana de 30 minutos es única e irrecuperable. Guardar con timestamp
#   acumula ventanas sin sobrescribir:
#     data/raw/logs_20260420T060000.csv  ← ventana 06:00-06:30 UTC
#     data/raw/logs_20260420T063000.csv  ← ventana 06:30-07:00 UTC
#
# Sobre los nulos en los datos:
#   Los logs tienen dos tipos que dejan columnas vacías por diseño de la API:
#     - Logs de sistema: columnas llm_* vienen vacías (esperado)
#     - Logs LLM:        service_id, http_status_code, client_ip vacíos (esperado)
#   Este script NO intenta rellenarlos. Los guarda tal como vienen.
#   Manejar esos nulos es responsabilidad de etl.py.
#
# PROBLEMA QUE RESUELVE ESTA VERSIÓN:
#   Cuando ingest.py es importado por pipeline_loop.py (que vive en la raíz),
#   el directorio de trabajo es la raíz del proyecto. os.makedirs("data/raw")
#   usa rutas relativas al directorio de trabajo, lo que es correcto en ese caso.
#   Sin embargo, para garantizar consistencia sin importar desde dónde se llame,
#   calculamos DATA_DIR como ruta absoluta basada en la ubicación de este archivo.
#
# Cómo ejecutarlo directamente:
#   python app/ingest.py          ← desde la raíz del proyecto
#
# Cómo importarlo desde otro script:
#   from ingest import fetch_current_window, save_data
# =============================================================================

import requests
import pandas as pd
import time
import os
import sys


# -----------------------------------------------------------------------------
# Configuración de rutas — absolutas para robustez
# -----------------------------------------------------------------------------
# __file__              → ruta absoluta de ingest.py   → .../app/ingest.py
# dirname(__file__)     → carpeta app/                 → .../app/
# dirname(dirname(...)) → raíz del proyecto            → .../sap-security-hackathon/
#
# Calculamos la raíz del proyecto desde la ubicación de este archivo.
# Así, DATA_DIR siempre apunta a la carpeta data/ correcta, sin importar
# desde qué directorio se ejecute o importe este script.

_APP_DIR   = os.path.dirname(os.path.abspath(__file__))
_RAIZ      = os.path.dirname(_APP_DIR)
DATA_RAW   = os.path.join(_RAIZ, "data", "raw")        # .../data/raw/
DATA_PROC  = os.path.join(_RAIZ, "data", "processed")  # .../data/processed/

# Agregamos app/ al path para importar config.py
# insert(0, ...) da prioridad a nuestros módulos sobre los del sistema
if _APP_DIR not in sys.path:
    sys.path.insert(0, _APP_DIR)

from config import API_BASE_URL, get_headers, validate_config


# =============================================================================
# FUNCIÓN PRINCIPAL: fetch_current_window()
# =============================================================================

def fetch_current_window() -> tuple:
    """
    Extrae todos los logs de la ventana UTC actual de 30 minutos.

    Implementa el loop de paginación completo según la documentación oficial
    de la API del hackathon:
      1. GET /info  → descubre total_pages para esta ventana
      2. GET /logs/current?page=1..total_pages → acumula registros
      3. Convierte a DataFrame y retorna junto con la metadata

    Returns:
        tuple: (df, meta)
            df   → pandas DataFrame con todos los registros de la ventana
            meta → dict con window_start, window_end, total_records, etc.

    Raises:
        requests.exceptions.HTTPError     → error HTTP de la API
        requests.exceptions.ConnectionError → sin conexión al servidor
        requests.exceptions.Timeout       → servidor no responde en tiempo
    """

    headers = get_headers()
    # headers = {"Authorization": "Bearer <token>"}
    # get_headers() lo construye desde config.py en cada llamada

    # -------------------------------------------------------------------------
    # PASO 1: GET /info — descubrir el tamaño de la ventana actual
    # -------------------------------------------------------------------------
    # SIEMPRE llamar /info ANTES del loop de páginas.
    # Nos dice cuántas páginas iterar. Sin esto no sabemos cuándo parar.

    print("─" * 60)
    print("PASO 1: Consultando GET /info...")
    print("─" * 60)

    info_response = requests.get(
        f"{API_BASE_URL}/info",
        headers=headers,
        timeout=15
        # timeout=15: si el servidor no responde en 15 segundos, lanza Timeout
        # Sin timeout, el script podría quedarse colgado indefinidamente
    )

    # raise_for_status() lanza HTTPError automáticamente si status >= 400
    # Es preferible a revisar el código manualmente — falla rápido y claro
    info_response.raise_for_status()

    meta = info_response.json()
    # meta es un dict con:
    #   "batch_size":     500 (fijo por el servidor, no configurable)
    #   "window_start":   "2026-04-20T06:00:00+00:00"
    #   "window_end":     "2026-04-20T06:30:00+00:00"
    #   "total_records":  5729
    #   "total_pages":    12

    total_pages   = meta["total_pages"]
    total_records = meta["total_records"]
    window_start  = meta["window_start"]
    window_end    = meta["window_end"]
    batch_size    = meta["batch_size"]

    print(f"  Ventana:          {window_start}  →  {window_end}")
    print(f"  Total registros:  {total_records:,}")
    print(f"  Batch size:       {batch_size} registros por página")
    print(f"  Total páginas:    {total_pages}")
    print()

    # -------------------------------------------------------------------------
    # PASO 2: Loop paginado — GET /logs/current?page=1..N
    # -------------------------------------------------------------------------
    # Acumulamos en lista Python, no en DataFrame directamente.
    # Razón: pd.concat() en cada iteración crea un DataFrame nuevo en memoria
    # en cada paso (O(n²) en total). Acumular en lista y hacer pd.DataFrame()
    # una sola vez al final es O(n) — mucho más eficiente.

    print("─" * 60)
    print(f"PASO 2: Extrayendo {total_pages} páginas de /logs/current...")
    print("─" * 60)

    all_records = []

    for page in range(1, total_pages + 1):
        # range(1, total_pages + 1) produce: 1, 2, 3, ..., total_pages
        # El +1 es necesario porque range() excluye el límite superior

        print(f"  Página {page:>3}/{total_pages}...", end=" ")
        # :>3  → alinea a la derecha en 3 caracteres (ej: "  1", " 12", "110")
        # end=" " → no hace salto de línea, el ✓ aparece en la misma línea

        response = requests.get(
            f"{API_BASE_URL}/logs/current",
            headers=headers,
            params={"page": page},
            # params genera la query string: ?page=1, ?page=2, etc.
            timeout=30
            # timeout mayor porque páginas completas (500 registros) pesan más
        )

        # Caso especial: 422 = página fuera de rango
        # Puede ocurrir si la ventana cambió mientras hacíamos el loop
        # (ej: empezamos en el minuto :29 y la ventana rotó al :30)
        if response.status_code == 422:
            print(f"\n  ⚠️  Página {page} fuera de rango — ventana cambió durante extracción.")
            print(f"  Conservando {len(all_records):,} registros de páginas anteriores.")
            break
            # break sale del loop pero conserva all_records acumulado hasta aquí

        response.raise_for_status()

        payload = response.json()
        # payload["data"] es la lista de registros de esta página
        # payload["records_in_page"] dice cuántos hay (puede ser < batch_size en última página)

        registros_en_pagina = payload["records_in_page"]
        all_records.extend(payload["data"])
        # extend() agrega cada elemento de payload["data"] individualmente a all_records
        # A diferencia de append(), que agregaría la lista como un solo elemento anidado

        print(f"✓  ({registros_en_pagina} registros)")

        # Pausa cortés entre llamadas para no saturar el servidor
        # 0.2s × 12 páginas ≈ 2.4s de pausa total — imperceptible para el usuario
        if page < total_pages:
            time.sleep(0.2)

    # -------------------------------------------------------------------------
    # PASO 3: Convertir lista acumulada a DataFrame
    # -------------------------------------------------------------------------
    # pd.DataFrame(lista_de_dicts) crea una tabla donde:
    #   - cada dict de la lista → una fila
    #   - cada key del dict     → una columna
    # Los keys ausentes en algún dict producen NaN en esa celda — comportamiento
    # esperado dado el patrón de nulos por diseño de la API.

    print()
    print("─" * 60)
    print("PASO 3: Construyendo DataFrame...")
    print("─" * 60)

    df = pd.DataFrame(all_records)

    print(f"  Filas:    {len(df):,}")
    print(f"  Columnas: {len(df.columns)}")
    print(f"  Columnas encontradas: {df.columns.tolist()}")
    print()

    # Distribución por tipo de log — útil para verificar que los datos llegaron bien
    if "sap_function_log_type" in df.columns:
        print("  Distribución por tipo de log:")
        for tipo, cantidad in df["sap_function_log_type"].value_counts().items():
            print(f"    {tipo:<20} {cantidad:>6,} registros")
        print()

    return df, meta


# =============================================================================
# FUNCIÓN DE GUARDADO: save_data()
# =============================================================================

def save_data(df: pd.DataFrame, meta: dict) -> str:
    """
    Guarda el DataFrame en data/raw/ con el timestamp de ventana en el nombre.
    También actualiza data/raw/sample_10.csv con los primeros 10 registros.

    Usa rutas absolutas calculadas desde la ubicación de este archivo,
    por lo que funciona correctamente sin importar desde dónde se llame
    (directamente o importado por pipeline_loop.py).

    Args:
        df:   DataFrame con todos los registros extraídos
        meta: dict con metadata de la ventana (resultado de GET /info)

    Returns:
        str: ruta absoluta del archivo CSV principal guardado
    """

    # Crear carpetas si no existen
    # exist_ok=True evita error si la carpeta ya existe — idempotente
    os.makedirs(DATA_RAW,  exist_ok=True)
    os.makedirs(DATA_PROC, exist_ok=True)

    # -------------------------------------------------------------------------
    # Construir nombre de archivo con timestamp de la ventana
    # -------------------------------------------------------------------------
    # meta["window_start"] = "2026-04-20T06:00:00+00:00"
    # Transformamos a nombre de archivo válido eliminando caracteres especiales:
    #   "2026-04-20T06:00:00+00:00"
    #   → "20260420T0600000000"      (eliminados -, : y +)
    #   → "20260420T060000"          (primeros 15 caracteres)
    #
    # Resultado: logs_20260420T060000.csv
    # Esto garantiza un nombre único por ventana y ordenable cronológicamente.

    window_tag = (
        meta["window_start"]
        .replace(":", "")   # elimina los : de la hora
        .replace("-", "")   # elimina los - de la fecha
        .replace("+", "")   # elimina el + del offset UTC
        [:15]               # toma solo los primeros 15 caracteres
    )

    ruta_principal = os.path.join(DATA_RAW, f"logs_{window_tag}.csv")
    ruta_muestra   = os.path.join(DATA_RAW, "sample_10.csv")

    # Guardar CSV completo
    # index=False evita que pandas agregue columna extra con índice numérico (0,1,2...)
    df.to_csv(ruta_principal, index=False)
    print(f"  ✓ Archivo principal: {ruta_principal}  ({len(df):,} registros)")

    # Guardar muestra de 10 filas para el equipo
    # Permite que AI Specialist y Data Architect trabajen con la estructura
    # sin necesitar ejecutar ingest.py completo ni tener acceso a la API
    df.head(10).to_csv(ruta_muestra, index=False)
    print(f"  ✓ Muestra para el equipo: {ruta_muestra}  (10 registros)")

    return ruta_principal


# =============================================================================
# PUNTO DE ENTRADA DIRECTO
# =============================================================================
# if __name__ == "__main__" ejecuta este bloque SOLO cuando corres:
#   python app/ingest.py
#
# Cuando pipeline_loop.py importa este módulo con:
#   from ingest import fetch_current_window, save_data
# este bloque NO se ejecuta — solo se importan las funciones.
# Esto es el patrón estándar de Python para archivos que sirven como
# módulo importable Y como script ejecutable independiente.

if __name__ == "__main__":

    print("=" * 60)
    print("INGEST.PY — Extracción de logs SAP")
    print("=" * 60)
    print()

    try:
        validate_config()
    except EnvironmentError as e:
        print(f"❌ Error de configuración: {e}")
        sys.exit(1)

    print()

    try:
        df, meta = fetch_current_window()

        print("─" * 60)
        print("PASO 4: Guardando datos...")
        print("─" * 60)
        ruta = save_data(df, meta)

        print()
        print("=" * 60)
        print("✅ EXTRACCIÓN COMPLETADA")
        print("=" * 60)
        print(f"  Registros extraídos: {len(df):,}")
        print(f"  Archivo guardado:    {ruta}")
        print()
        print("  Próximo paso: python app/explore.py")
        print("=" * 60)

    except requests.exceptions.ConnectionError:
        print("❌ Error de conexión — ¿El servidor está caído?")
        print("   Ejecuta primero: python prueba_health.py")
        sys.exit(1)

    except requests.exceptions.Timeout:
        print("❌ Timeout — El servidor tardó demasiado.")
        print("   Intenta de nuevo en un momento.")
        sys.exit(1)

    except requests.exceptions.HTTPError as e:
        print(f"❌ Error HTTP: {e}")
        print("   Ejecuta python prueba_info.py para diagnosticar.")
        sys.exit(1)

    except Exception as e:
        print(f"❌ Error inesperado: {type(e).__name__}: {e}")
        sys.exit(1)
