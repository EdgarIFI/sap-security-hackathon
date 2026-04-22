# =============================================================================
# server.py
# =============================================================================
# Propósito: entry point de la aplicación en Cloud Foundry.
#            Expone dos endpoints HTTP:
#              /health → liveness probe para CF y el Job Scheduler
#              /run    → ejecuta un ciclo completo del pipeline
#
# Quién llama a /run:
#            El Job Scheduling Service de SAP BTP, cada 30 minutos.
#            Reemplaza el loop infinito de pipeline_loop.py en producción.
#
# Cómo correrlo localmente (para probar antes de cf push):
#            python server.py
#            curl -X POST http://localhost:8080/run \
#                 -H "X-Job-Token: el-valor-de-JOB_SECRET_TOKEN-en-tu-.env"
#
# Seguridad:
#            El endpoint /run valida un token secreto en el header.
#            El token se configura con cf set-env — nunca en el código.
#            Sin token válido, /run devuelve 401 y no ejecuta nada.
#
# Este archivo es seguro para subir a Git:
#            No contiene credenciales ni valores sensibles.
#            Todos los secretos vienen de variables de entorno.
# =============================================================================

import os
import sys
import logging

# -----------------------------------------------------------------------------
# Rutas — ANTES de cualquier import del proyecto
# -----------------------------------------------------------------------------
# server.py vive en la raíz. app/ está un nivel adentro.
# Agregamos app/ a sys.path para que Python encuentre config.py, ingest.py, etc.

_BASE_DIR = os.path.dirname(os.path.abspath(__file__))
_APP_DIR  = os.path.join(_BASE_DIR, "app")

if _APP_DIR not in sys.path:
    sys.path.insert(0, _APP_DIR)

from flask import Flask, request, jsonify
from config import validate_config, JOB_SECRET_TOKEN
from ingest import ingest_and_persist

# -----------------------------------------------------------------------------
# Logging
# -----------------------------------------------------------------------------
# En CF, los logs van a stdout y CF los captura con: cf logs sap-ai-soc
# No usamos FileHandler aquí porque CF no garantiza persistencia de archivos.

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s UTC | %(levelname)-8s | %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S"
)
logger = logging.getLogger("server")

# -----------------------------------------------------------------------------
# Validación de configuración al arrancar
# -----------------------------------------------------------------------------
# Si faltan API_BASE_URL o BEARER_TOKEN, el servidor arranca pero /run va a
# fallar. Lo detectamos temprano para que aparezca en cf logs inmediatamente.

try:
    validate_config()
    logger.info("✓ Configuración validada")
except EnvironmentError as e:
    logger.error(f"⚠️  Configuración incompleta: {e}")
    logger.warning("El servidor arranca pero /run puede fallar.")

# -----------------------------------------------------------------------------
# App Flask
# -----------------------------------------------------------------------------

app = Flask(__name__)


# =============================================================================
# ENDPOINT: /health  y  /
# =============================================================================

@app.route("/", methods=["GET"])
@app.route("/health", methods=["GET"])
def health():
    """
    Liveness probe.
    CF verifica este endpoint para saber si la app está viva.
    El Job Scheduler también lo puede usar para verificar disponibilidad.
    Siempre devuelve 200 si el proceso está corriendo.
    """
    return jsonify({"status": "running"}), 200


# =============================================================================
# ENDPOINT: /run
# =============================================================================

@app.route("/run", methods=["POST"])
def run_pipeline():
    """
    Ejecuta un ciclo completo del pipeline:
        ingest_and_persist() → extracción API → CSV → HANA

    Seguridad:
        Valida el header X-Job-Token antes de ejecutar cualquier cosa.
        Si el token no coincide o no viene, devuelve 401 inmediatamente.

    Cuando el ETL esté listo se agrega aquí como segundo paso:
        df_limpio = run_etl(resultado["csv"])
        sin cambiar nada más de la estructura.

    Returns:
        200 → ciclo completado exitosamente, con conteos en el body
        401 → token inválido o ausente
        500 → error durante el pipeline, con detalle en el body
    """
    # ── Validación de token ──────────────────────────────────────────────────
    # JOB_SECRET_TOKEN viene de config.py que lo lee de:
    #   - .env en local
    #   - cf set-env en Cloud Foundry
    # Si está vacío (no configurado), se omite la validación.
    # En producción siempre debe estar configurado.

    if JOB_SECRET_TOKEN:
        token_recibido = request.headers.get("X-Job-Token", "")
        if token_recibido != JOB_SECRET_TOKEN:
            logger.warning(
                f"Intento de /run con token inválido "
                f"desde {request.remote_addr}"
            )
            return jsonify({"error": "unauthorized"}), 401

    # ── Ejecución del pipeline ───────────────────────────────────────────────
    try:
        logger.info("=" * 50)
        logger.info("Ciclo iniciado por Job Scheduler")
        logger.info("=" * 50)

        resultado = ingest_and_persist()

        logger.info(f"Ventana:    {resultado['ventana_inicio']} → {resultado['ventana_fin']}")
        logger.info(f"Registros:  {resultado['registros']:,}")
        logger.info(f"CSV:        {resultado['csv']}")

        if resultado.get("hana"):
            logger.info(
                f"HANA:       {resultado['hana']['sistema']:,} sistema + "
                f"{resultado['hana']['llm']:,} LLM"
            )
        else:
            logger.info("HANA:       no disponible — datos en CSV")

        logger.info("Ciclo completado exitosamente")
        logger.info("=" * 50)

        return jsonify({
            "status":         "ok",
            "ventana_inicio": resultado["ventana_inicio"],
            "ventana_fin":    resultado["ventana_fin"],
            "registros":      resultado["registros"],
            "hana":           resultado.get("hana"),
        }), 200

    except Exception as e:
        logger.error(f"Pipeline FAILED: {type(e).__name__}: {e}", exc_info=True)
        return jsonify({
            "status": "error",
            "tipo":   type(e).__name__,
            "detail": str(e),
        }), 500


# =============================================================================
# PUNTO DE ENTRADA
# =============================================================================

if __name__ == "__main__":
    # PORT lo inyecta CF automáticamente.
    # En local usa 8080 si no está definido.
    port = int(os.environ.get("PORT", 8080))
    logger.info(f"Servidor arrancando en puerto {port}")
    app.run(host="0.0.0.0", port=port)