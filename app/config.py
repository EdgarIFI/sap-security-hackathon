# =============================================================================
# app/config.py
# =============================================================================
# Propósito: centralizar toda la configuración y credenciales del proyecto.
#
# Este es el ÚNICO archivo que sabe de dónde vienen las credenciales.
# Todos los demás scripts importan desde aquí — nunca leen .env directamente.
#
# Flujo:
#   .env (en tu disco, nunca en Git)
#     └─► load_dotenv() carga esos valores en memoria
#           └─► os.getenv() los lee y los asigna a variables Python
#                 └─► el resto del proyecto importa esas variables desde aquí
# =============================================================================

import os               # módulo estándar de Python para leer variables de entorno
from dotenv import load_dotenv  # librería externa: lee el archivo .env y carga su contenido


# -----------------------------------------------------------------------------
# Cargar el archivo .env
# -----------------------------------------------------------------------------
# load_dotenv() busca un archivo llamado ".env" en la carpeta actual
# y pone sus contenidos en el entorno del proceso (en memoria, no en disco).
# Si el archivo no existe, no falla — simplemente no carga nada.
# Debe llamarse ANTES de cualquier os.getenv(), porque si no, no hay nada que leer.

load_dotenv()


# -----------------------------------------------------------------------------
# Variables de configuración
# -----------------------------------------------------------------------------
# os.getenv("NOMBRE") lee la variable NOMBRE del entorno.
# Si no existe (porque no está en .env o .env no existe), devuelve None.
# None es el valor que usaremos para detectar configuración faltante.

# URL base de la API del hackathon.
# Ejemplo de cómo se ve en .env:
#   API_BASE_URL=https://soc-api.hackathon.example.com
API_BASE_URL = os.getenv("API_BASE_URL")

# Token de autenticación del equipo (Bearer token).
# Se usa en el header: Authorization: Bearer <este valor>
# Ejemplo en .env:
#   BEARER_TOKEN=abc123xyz...
BEARER_TOKEN = os.getenv("BEARER_TOKEN")

# URL del webhook al que mandamos alertas cuando detectamos anomalías.
# Aún no disponible — llegará antes del 27 de abril.
# Ejemplo en .env:
#   WEBHOOK_URL=https://webhook.site/...
WEBHOOK_URL = os.getenv("WEBHOOK_URL")

# Datos de conexión a SAP HANA (pendientes — los provee el Data Architect).
HANA_HOST     = os.getenv("HANA_HOST")
HANA_PORT     = os.getenv("HANA_PORT")
HANA_USER     = os.getenv("HANA_USER")
HANA_PASSWORD = os.getenv("HANA_PASSWORD")


# -----------------------------------------------------------------------------
# validate_config()
# -----------------------------------------------------------------------------
# Función de validación temprana.
#
# Por qué existe: si falta una variable crítica (por ejemplo, olvidaste poner
# el BEARER_TOKEN en .env), es mejor saberlo INMEDIATAMENTE al arrancar,
# con un mensaje claro, que descubrirlo 10 líneas después con un error críptico
# como "NoneType has no attribute 'encode'".
#
# Cuándo llamarla: al inicio de cualquier script que necesite conectarse a la API.
# Si la validación falla, el programa para antes de intentar nada.

def validate_config():
    """
    Verifica que las variables de entorno críticas estén presentes.
    Lanza un error explícito si falta alguna, en lugar de fallar silenciosamente.
    """
    # Diccionario de variables que consideramos obligatorias para operar
    required = {
        "API_BASE_URL": API_BASE_URL,
        "BEARER_TOKEN": BEARER_TOKEN,
    }

    # Filtramos las que son None o string vacío
    missing = [nombre for nombre, valor in required.items() if not valor]

    if missing:
        # Si falta algo, levantamos un error con lista clara de qué falta
        raise EnvironmentError(
            f"Faltan variables de entorno obligatorias: {missing}\n"
            f"Verifica que tu archivo .env existe y contiene estas variables."
        )

    # Si llegamos aquí, todo está presente
    print("✓ Configuración validada correctamente.")


# -----------------------------------------------------------------------------
# get_headers()
# -----------------------------------------------------------------------------
# Función de conveniencia que construye el header de autenticación.
#
# Por qué existe como función y no como variable:
# Si lo declaráramos como variable al importar este módulo, el valor quedaría
# fijo en ese momento. Como función, se construye cada vez que se llama,
# tomando el valor actual de BEARER_TOKEN. Más flexible y fácil de probar.
#
# Cómo se usa en otros scripts:
#   from config import get_headers
#   response = requests.get(url, headers=get_headers())

def get_headers() -> dict:
    """
    Devuelve el diccionario de headers HTTP necesario para autenticarse
    con la API del hackathon.

    La API usa Bearer token en el header Authorization.
    Formato oficial: Authorization: Bearer <token>

    Returns:
        dict con el header de autenticación listo para pasar a requests.get()
    """
    return {
        "Authorization": f"Bearer {BEARER_TOKEN}"
        # f"Bearer {BEARER_TOKEN}" construye el string:
        # si BEARER_TOKEN = "abc123", el resultado es "Bearer abc123"
        # La f antes de las comillas indica que es un f-string (string con variables)
    }
