# =============================================================================
# app/ingest.py
# =============================================================================
# Propósito: extraer TODOS los logs de la ventana UTC actual de 30 minutos
#            y guardarlos en disco como CSV crudo, sin modificaciones.
#
# Este es el primer eslabón del pipeline completo:
#
#   ingest.py → etl.py → model.py → alerting.py
#
# Su única responsabilidad es traer los datos fielmente desde la API.
# No limpia, no transforma, no analiza. Solo extrae y guarda.
#
# Cómo funciona internamente:
#   1. Llama GET /info → descubre cuántas páginas hay en esta ventana
#   2. Itera GET /logs/current?page=1 ... ?page=N → acumula todos los registros
#   3. Convierte la lista acumulada a un DataFrame de pandas
#   4. Guarda el CSV en data/raw/ con el timestamp de la ventana en el nombre
#
# Por qué guardamos con timestamp en el nombre:
#   Cada ventana de 30 minutos es única. Si siempre sobrescribiéramos el mismo
#   archivo, perderíamos el historial. Con el timestamp acumulamos ventanas:
#     data/raw/logs_20260420T060000.csv  ← ventana 06:00-06:30
#     data/raw/logs_20260420T063000.csv  ← ventana 06:30-07:00
#
# Sobre los nulos en los datos:
#   Los logs tienen dos tipos y cada tipo deja columnas vacías por diseño:
#     - Logs de sistema: columnas llm_* vienen vacías
#     - Logs de LLM:     columnas service_id, http_status_code, client_ip vacías
#   Este script NO intenta rellenarlos. Los guarda tal como vienen.
#   Manejar esos nulos es responsabilidad de etl.py.
#
# Cómo ejecutarlo (desde la raíz del proyecto, con .venv activo):
#   python app/ingest.py
#
# Qué produce:
#   data/raw/logs_[timestamp].csv   ← archivo con todos los registros de la ventana
#   data/raw/sample_10.csv          ← muestra de 10 filas para uso del equipo
# =============================================================================

import requests     # para hacer las llamadas HTTP a la API
import pandas as pd # para convertir los registros a DataFrame y guardar CSV
import time         # para hacer pausas entre llamadas y no saturar la API
import os           # para crear carpetas si no existen
import sys          # para poder salir del programa con código de error

# Agregamos el directorio raíz al path de Python para que pueda encontrar config.py
# Esto es necesario porque ingest.py está dentro de app/ pero config.py también
# está en app/ — Python necesita saber dónde buscar módulos al importar
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from config import API_BASE_URL, get_headers, validate_config


# =============================================================================
# FUNCIÓN PRINCIPAL: fetch_current_window()
# =============================================================================

def fetch_current_window() -> tuple:
    """
    Extrae todos los logs de la ventana UTC actual de 30 minutos.

    Flujo interno:
        1. GET /info  → obtiene total_pages y metadata de la ventana
        2. Loop GET /logs/current?page=1..N → acumula todos los registros
        3. Convierte a DataFrame y devuelve junto con la metadata

    Returns:
        tuple: (df, meta)
            df   → pandas DataFrame con todos los registros de la ventana
            meta → diccionario con info de la ventana (window_start, total_records, etc.)

    Raises:
        requests.exceptions.HTTPError: si alguna llamada devuelve error HTTP
        requests.exceptions.ConnectionError: si no hay conexión con el servidor
    """

    headers = get_headers()  # {"Authorization": "Bearer <token>"}

    # -------------------------------------------------------------------------
    # PASO 1: Llamar GET /info para conocer el tamaño de la ventana actual
    # -------------------------------------------------------------------------
    # Siempre llamamos /info PRIMERO, antes de empezar el loop.
    # Necesitamos saber total_pages para saber cuántas iteraciones hacer.
    # Sin esto, no sabemos cuándo parar.

    print("─" * 60)
    print("PASO 1: Consultando GET /info...")
    print("─" * 60)

    info_response = requests.get(
        f"{API_BASE_URL}/info",
        headers=headers,
        timeout=15   # si no responde en 15 segundos, lanzar Timeout
    )

    # raise_for_status() revisa el status code y lanza una excepción automática
    # si es 4xx o 5xx. Esto detiene el programa con un mensaje claro
    # en lugar de continuar con datos incorrectos.
    info_response.raise_for_status()

    # Convertimos la respuesta JSON a diccionario Python
    meta = info_response.json()

    # Extraemos los valores que necesitamos del diccionario
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
    # PASO 2: Iterar todas las páginas y acumular registros
    # -------------------------------------------------------------------------
    # Hacemos una llamada por página. Cada respuesta contiene hasta 500 registros
    # en el campo "data" (lista de diccionarios).
    # Los acumulamos todos en una lista Python antes de convertir a DataFrame.
    #
    # Por qué acumulamos en lista y no en DataFrame directamente:
    #   Hacer pd.concat() en cada iteración es muy lento porque crea un DataFrame
    #   nuevo en memoria en cada paso. Es más eficiente acumular en lista y
    #   hacer pd.DataFrame() una sola vez al final.

    print("─" * 60)
    print(f"PASO 2: Extrayendo {total_pages} páginas de /logs/current...")
    print("─" * 60)

    all_records = []  # lista donde acumulamos todos los registros de todas las páginas

    for page in range(1, total_pages + 1):
        # range(1, total_pages + 1) genera: 1, 2, 3, ..., total_pages
        # El +1 es porque range() excluye el último número

        print(f"  Página {page:>3}/{total_pages}...", end=" ")
        # :>3 alinea el número a la derecha en 3 caracteres (para que quede ordenado)
        # end=" " evita el salto de línea para que el ✓ quede en la misma línea

        response = requests.get(
            f"{API_BASE_URL}/logs/current",
            headers=headers,
            params={"page": page},   # ?page=N — le decimos qué página queremos
            timeout=30               # páginas grandes pueden tardar más
        )

        # Manejo específico del error 422 (página fuera de rango)
        # Esto puede pasar si la ventana cambió mientras hacíamos el loop
        # (por ejemplo, si pasamos del minuto 29 al 30 durante la extracción)
        if response.status_code == 422:
            print(f"\n  ⚠️  Página {page} fuera de rango — la ventana cambió durante la extracción.")
            print(f"  Deteniendo en página {page - 1}. Los datos anteriores están guardados.")
            break  # salimos del loop pero conservamos lo que ya acumulamos

        response.raise_for_status()  # cualquier otro error HTTP lanza excepción

        payload = response.json()
        # payload es un diccionario con:
        #   "data": [lista de registros]
        #   "records_in_page": cuántos hay en esta página
        #   "current_page": número de página actual
        #   etc.

        registros_en_pagina = payload["records_in_page"]
        all_records.extend(payload["data"])
        # extend() agrega todos los elementos de la lista payload["data"]
        # a all_records, uno por uno (no como lista anidada)

        print(f"✓  ({registros_en_pagina} registros)")

        # Pausa pequeña entre llamadas para no saturar el servidor
        # 0.2 segundos × 12 páginas = ~2.4 segundos de pausa total
        # Es un balance entre velocidad y cortesía con el servidor
        if page < total_pages:  # no pausar después de la última página
            time.sleep(0.2)

    # -------------------------------------------------------------------------
    # PASO 3: Convertir la lista acumulada a DataFrame
    # -------------------------------------------------------------------------
    # pd.DataFrame(all_records) toma una lista de diccionarios y la convierte
    # en una tabla donde cada diccionario es una fila y cada key es una columna.
    #
    # Ejemplo:
    #   all_records = [
    #     {"sap_function_log_type": "INFO", "client_ip": "192.168.1.1", ...},
    #     {"sap_function_log_type": "LLM_REQUEST", "llm_model_id": "gpt-4", ...},
    #   ]
    #   → DataFrame con 2 filas y tantas columnas como keys distintos haya

    print()
    print("─" * 60)
    print("PASO 3: Construyendo DataFrame...")
    print("─" * 60)

    df = pd.DataFrame(all_records)

    print(f"  Filas:    {len(df):,}")
    print(f"  Columnas: {len(df.columns)}")
    print(f"  Columnas encontradas: {df.columns.tolist()}")
    print()

    # Mostrar distribución por tipo de log — información valiosa para el equipo
    if "sap_function_log_type" in df.columns:
        print("  Distribución por tipo de log:")
        distribucion = df["sap_function_log_type"].value_counts()
        for tipo, cantidad in distribucion.items():
            print(f"    {tipo:<20} {cantidad:>6,} registros")
        print()

    return df, meta


# =============================================================================
# FUNCIÓN DE GUARDADO: save_data()
# =============================================================================

def save_data(df: pd.DataFrame, meta: dict) -> str:
    """
    Guarda el DataFrame en data/raw/ con el timestamp de la ventana en el nombre.
    También guarda una muestra de 10 filas para uso del equipo.

    Args:
        df:   DataFrame con todos los registros extraídos
        meta: diccionario con metadata de la ventana (de GET /info)

    Returns:
        str: ruta del archivo principal guardado
    """

    # Crear la carpeta data/raw/ si no existe
    # exist_ok=True evita error si la carpeta ya existe
    os.makedirs("data/raw", exist_ok=True)

    # -------------------------------------------------------------------------
    # Construir el nombre del archivo con el timestamp de la ventana
    # -------------------------------------------------------------------------
    # meta["window_start"] tiene formato: "2026-04-20T06:00:00+00:00"
    # Necesitamos convertirlo a algo usable como nombre de archivo.
    #
    # Transformación:
    #   "2026-04-20T06:00:00+00:00"
    #   → reemplazar ":" con ""   → "2026-04-20T060000+0000"
    #   → reemplazar "-" con ""   → "20260420T060000+0000"
    #   → reemplazar "+" con ""   → "20260420T0600000000"
    #   → tomar primeros 15 chars → "20260420T060000"

    window_tag = (
        meta["window_start"]
        .replace(":", "")
        .replace("-", "")
        .replace("+", "")[:15]
    )

    # Ruta principal del archivo
    ruta_principal = f"data/raw/logs_{window_tag}.csv"

    # Guardar el CSV completo
    # index=False evita que pandas agregue una columna extra con el índice numérico
    df.to_csv(ruta_principal, index=False)
    print(f"  ✓ Archivo principal: {ruta_principal}  ({len(df):,} registros)")

    # -------------------------------------------------------------------------
    # Guardar muestra de 10 filas
    # -------------------------------------------------------------------------
    # Esta muestra permite que el AI Specialist y el Data Architect empiecen
    # a trabajar con la estructura de los datos sin necesitar correr ingest.py
    # completo ni tener acceso a la API.

    ruta_muestra = "data/raw/sample_10.csv"
    df.head(10).to_csv(ruta_muestra, index=False)
    print(f"  ✓ Muestra para el equipo: {ruta_muestra}  (10 registros)")

    return ruta_principal


# =============================================================================
# PUNTO DE ENTRADA — se ejecuta cuando corres: python app/ingest.py
# =============================================================================
# El bloque if __name__ == "__main__" es un patrón estándar de Python.
# Significa: "ejecuta este código SOLO si este archivo se corre directamente".
# Si otro script importa ingest.py, este bloque NO se ejecuta automáticamente.

if __name__ == "__main__":

    print("=" * 60)
    print("INGEST.PY — Extracción de logs SAP")
    print("=" * 60)
    print()

    # Validar configuración antes de intentar cualquier llamada HTTP
    # Si falta API_BASE_URL o BEARER_TOKEN, esto lanza error y para aquí
    try:
        validate_config()
    except EnvironmentError as e:
        print(f"❌ Error de configuración: {e}")
        sys.exit(1)

    print()

    # Ejecutar la extracción completa
    try:
        df, meta = fetch_current_window()

        # Guardar los datos en disco
        print("─" * 60)
        print("PASO 4: Guardando datos...")
        print("─" * 60)
        ruta = save_data(df, meta)

        # Resumen final
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
