# =============================================================================
# app/config.py
# =============================================================================
# Propósito: centralizar toda la configuración y credenciales del proyecto.
#
# Este es el ÚNICO archivo que sabe de dónde vienen las credenciales.
# Todos los demás scripts importan desde aquí — nunca leen .env directamente.
#
# Flujo:
#   .env (en la raíz del proyecto, nunca en Git)
#     └─► load_dotenv() carga esos valores en memoria
#           └─► os.getenv() los lee y los asigna a variables Python
#                 └─► el resto del proyecto importa esas variables desde aquí
#
# PROBLEMA QUE RESUELVE ESTA VERSIÓN:
#   load_dotenv() sin argumentos busca .env en el directorio de trabajo actual
#   (el directorio desde donde corres el script). Si corres pipeline_loop.py
#   desde la raíz, el directorio actual ES la raíz, donde vive .env — bien.
#   Pero si por alguna razón el directorio cambia, load_dotenv() no encuentra
#   el .env. La solución es especificar la ruta absoluta del .env explícitamente,
#   calculada en relación a la ubicación de este archivo (config.py en app/).
# =============================================================================

import os
from dotenv import load_dotenv


# -----------------------------------------------------------------------------
# Cargar el archivo .env con ruta absoluta
# -----------------------------------------------------------------------------
# __file__       → ruta absoluta de config.py         → .../app/config.py
# dirname(...)   → carpeta que contiene config.py      → .../app/
# dirname(...)   → carpeta padre (raíz del proyecto)   → .../sap-security-hackathon/
# join(...,'.env')→ ruta completa del .env             → .../sap-security-hackathon/.env
#
# Así, sin importar desde dónde se ejecute el script que importa config.py,
# siempre encontramos el .env en la raíz del proyecto.

_RAIZ_PROYECTO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_RUTA_ENV      = os.path.join(_RAIZ_PROYECTO, ".env")

# dotenv_path especifica la ruta exacta del .env a cargar
# override=False significa: si la variable ya existe en el entorno del sistema,
# no la sobreescribas (respeta variables de entorno del sistema operativo)
load_dotenv(dotenv_path=_RUTA_ENV, override=False)


# -----------------------------------------------------------------------------
# Variables de configuración
# -----------------------------------------------------------------------------
# os.getenv("NOMBRE") lee la variable NOMBRE del entorno.
# Si no existe devuelve None — detectado por validate_config().

# URL base de la API del hackathon (sin slash al final)
# Ejemplo en .env:  API_BASE_URL=https://sap-api-xx.xxxxx.xyz
API_BASE_URL = os.getenv("API_BASE_URL")

# Token Bearer de autenticación del equipo
# Se usa en el header: Authorization: Bearer <este valor>
# Ejemplo en .env:  BEARER_TOKEN=teamy-2026-...
BEARER_TOKEN = os.getenv("BEARER_TOKEN")

# URL del webhook para envío de alertas (disponible desde Abr 27)
# Ejemplo en .env:  WEBHOOK_URL=https://webhook.site/...
WEBHOOK_URL = os.getenv("WEBHOOK_URL")

# Credenciales de SAP HANA (las provee el Data Architect una vez configurada la instancia)
#HANA_HOST     = os.getenv("HANA_HOST")
#HANA_PORT     = os.getenv("HANA_PORT")
#HANA_USER     = os.getenv("HANA_USER")
#HANA_PASSWORD = os.getenv("HANA_PASSWORD")

#Las de arriba deberían de borrarse pero las comenté por miedo -Tagle

import json

# -----------------------------------------------------------------------------
# _load_hana_creds()
# -----------------------------------------------------------------------------
# Detecta automáticamente el entorno de ejecución:
#   - Si VCAP_SERVICES existe → estamos en Cloud Foundry → leer de ahí
#   - Si no existe            → estamos en local         → leer de .env
#
# Esto permite que el mismo código funcione en ambos entornos sin cambios.
# VCAP_SERVICES es inyectado automáticamente por CF cuando existe un
# Service Binding entre la app y la instancia HANA. Nunca se escribe
# manualmente — lo genera BTP al hacer cf push con el manifest correcto.
#
# En local: las cuatro variables HANA_* vienen del .env como siempre.
# En CF:    las credenciales vienen del JSON de VCAP_SERVICES.
#           No hay .env en CF — nunca se sube a Git.

def _load_hana_creds() -> dict:
    """
    Lee credenciales HANA del entorno correcto según dónde corre el código.

    Returns:
        dict con host, port, user, password
        Valores pueden ser None si no están configurados — 
        detectado después por hana_client.py
    """
    vcap_raw = os.getenv("VCAP_SERVICES")

    if vcap_raw:
        # ── Entorno Cloud Foundry ────────────────────────────────────────
        # CF inyecta VCAP_SERVICES con las credenciales de todos los
        # servicios vinculados. La key puede variar según el tipo de binding.
        try:
            vcap     = json.loads(vcap_raw)
            # Intentar las dos keys posibles para HANA Cloud
            servicios_hana = vcap.get("hana", vcap.get("hana-cloud", []))
            if not servicios_hana:
                raise EnvironmentError(
                    "VCAP_SERVICES existe pero no contiene credenciales HANA.\n"
                    "Verifica que el Service Binding está configurado en manifest.yml"
                )
            creds = servicios_hana[0]["credentials"]
            return {
                "host":     creds.get("host"),
                "port":     creds.get("port", "443"),
                "user":     creds.get("user"),
                "password": creds.get("password"),
            }
        except (json.JSONDecodeError, KeyError) as e:
            raise EnvironmentError(f"Error leyendo VCAP_SERVICES: {e}")
    else:
        # ── Entorno local (.env) ─────────────────────────────────────────
        return {
            "host":     os.getenv("HANA_HOST"),
            "port":     os.getenv("HANA_PORT", "443"),
            "user":     os.getenv("HANA_USER"),
            "password": os.getenv("HANA_PASSWORD"),
        }


# Cargar credenciales HANA según el entorno detectado
_hana_creds   = _load_hana_creds()
HANA_HOST     = _hana_creds["host"]
HANA_PORT     = _hana_creds["port"]
HANA_USER     = _hana_creds["user"]
HANA_PASSWORD = _hana_creds["password"]

# Token de seguridad para el endpoint /run de server.py
# En local: puede estar en .env o vacío (no se valida si no hay scheduler)
# En CF:    se configura con cf set-env — nunca en el código ni manifest.yml
JOB_SECRET_TOKEN = os.getenv("JOB_SECRET_TOKEN", "")

# -----------------------------------------------------------------------------
# validate_config()
# -----------------------------------------------------------------------------
# Verifica que las variables críticas estén presentes ANTES de intentar
# cualquier llamada HTTP. Falla rápido con mensaje claro en lugar de
# producir errores crípticos muchas líneas después.
#
# Cuándo llamarla: al inicio de cualquier script que se conecte a la API.

def validate_config():
    """
    Verifica que las variables de entorno críticas estén presentes.

    Raises:
        EnvironmentError: si falta alguna variable obligatoria,
                          con lista clara de cuáles faltan.
    """
    required = {
        "API_BASE_URL": API_BASE_URL,
        "BEARER_TOKEN": BEARER_TOKEN,
    }

    # Filtramos las que son None o string vacío
    missing = [nombre for nombre, valor in required.items() if not valor]

    if missing:
        raise EnvironmentError(
            f"Faltan variables de entorno obligatorias: {missing}\n"
            f"Archivo .env buscado en: {_RUTA_ENV}\n"
            f"Verifica que el archivo existe y contiene estas variables."
        )

    # Si llegamos aquí, todo está presente
    print("✓ Configuración validada correctamente.")


# -----------------------------------------------------------------------------
# get_headers()
# -----------------------------------------------------------------------------
# Construye el header de autenticación Bearer listo para usar en requests.
#
# Por qué es función y no variable global:
#   Si fuera variable global, se evaluaría una sola vez al importar el módulo.
#   Como función, se construye cada vez que se llama, garantizando que siempre
#   usa el valor actual de BEARER_TOKEN (útil si el token se rota en caliente).
#
# Uso en otros scripts:
#   from config import get_headers
#   response = requests.get(url, headers=get_headers(), timeout=15)

def get_headers() -> dict:
    """
    Devuelve el header HTTP de autenticación Bearer.

    Returns:
        dict: {"Authorization": "Bearer <token>"}
    """
    return {
        "Authorization": f"Bearer {BEARER_TOKEN}"
        # Si BEARER_TOKEN = "abc123", resultado: "Authorization: Bearer abc123"
        # La f antes de las comillas indica f-string (string con variables embebidas)
    }
