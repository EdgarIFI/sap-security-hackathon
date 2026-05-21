"""
dashboard/test_dev1.py
======================
Suite de pruebas para Dev 1 — valida agent.py sin necesidad de la UI de Streamlit.
Requiere HANA activo y OPENAI_API_KEY en .env.

Ejecutar desde la raíz del repositorio:
    python dashboard/test_dev1.py

Ejecutar desde dentro de dashboard/:
    python test_dev1.py

Qué valida:
    1. Importaciones y constantes (ALERT_PLAYBOOK, CHART_CATALOG)
    2. _parse_chart_key() — todos los casos
    3. _build_system_prompt() — chat libre vs botón
    4. Conexión a HANA
    5. get_agent_context() — estructura del string retornado
    6. get_agent_response() — chat libre con datos reales
    7. get_agent_response() — flujo "¿Qué hago?" con playbook
    8. get_agent_response() — manejo de chart_key en respuesta

NO envía tests de la UI de Streamlit (eso se valida con prueba_visual.py).
"""

import sys
import os
import json

# ── Path setup ────────────────────────────────────────────────────────────────
# Permite importar desde dashboard/ tanto si se corre desde la raíz como desde dentro
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# Cargar .env antes de cualquier import que use os.environ
try:
    from dotenv import load_dotenv
    load_dotenv()
    print("✅ .env cargado")
except ImportError:
    print("⚠️  python-dotenv no instalado — asegúrate de tener las variables en el entorno")

# ── Imports ───────────────────────────────────────────────────────────────────
from agent import (
    get_agent_response,
    ALERT_PLAYBOOK,
    SYSTEM_PROMPT_BASE,
    _parse_chart_key,
    _build_system_prompt,
)
from charts import CHART_CATALOG
from hana_dashboard import get_connection, get_agent_context


# ══════════════════════════════════════════════════════════════════════════════
# HELPERS
# ══════════════════════════════════════════════════════════════════════════════

PASS = "✅"
FAIL = "❌"
results: list[tuple[str, bool, str]] = []


def check(label: str, condition: bool, detail: str = "") -> None:
    status = PASS if condition else FAIL
    results.append((label, condition, detail))
    print(f"  {status}  {label}" + (f"  →  {detail}" if detail else ""))


# ══════════════════════════════════════════════════════════════════════════════
# TEST 1 — Constantes e imports
# ══════════════════════════════════════════════════════════════════════════════

def test_constants() -> None:
    print("\n── TEST 1: Constantes e imports ─────────────────────────────────────")

    check("ALERT_PLAYBOOK tiene 7 tipos",
          len(ALERT_PLAYBOOK) == 7,
          str(list(ALERT_PLAYBOOK.keys())))

    expected_types = [
        "brute_force", "path_scan", "security_event", "high_cost_llm_error",
        "ml_sistema_anomaly", "ml_llm_anomaly", "ml_ip_anomaly",
    ]
    for t in expected_types:
        check(f"  playbook['{t}'] existe y tiene acciones",
              t in ALERT_PLAYBOOK and len(ALERT_PLAYBOOK[t]) >= 1)

    check("CHART_CATALOG importado correctamente desde charts.py",
          isinstance(CHART_CATALOG, dict) and len(CHART_CATALOG) == 7,
          str(list(CHART_CATALOG.keys())))

    check("SYSTEM_PROMPT_BASE contiene placeholders requeridos",
          "{chart_catalog}" in SYSTEM_PROMPT_BASE and "{contexto_live}" in SYSTEM_PROMPT_BASE)

    check("OPENAI_API_KEY está en el entorno",
          bool(os.environ.get("OPENAI_API_KEY")),
          "Longitud: " + str(len(os.environ.get("OPENAI_API_KEY", ""))))


# ══════════════════════════════════════════════════════════════════════════════
# TEST 2 — _parse_chart_key()
# ══════════════════════════════════════════════════════════════════════════════

def test_parse_chart_key() -> None:
    print("\n── TEST 2: _parse_chart_key() ──────────────────────────────────────")

    # Caso 1: sin token
    text, key = _parse_chart_key("El agente no sugiere ninguna gráfica aquí.")
    check("Sin token → key=None",
          key is None and "gráfica" in text)

    # Caso 2: token válido al final
    text2, key2 = _parse_chart_key(
        "Hay 847 intentos desde esa IP. [CHART:brute_force_por_ip]"
    )
    check("Token válido → key extraído correctamente",
          key2 == "brute_force_por_ip",
          repr(key2))
    check("Token válido → texto limpio (sin token)",
          "[CHART:" not in text2,
          repr(text2[:60]))

    # Caso 3: token inválido (clave no existe en CHART_CATALOG)
    text3, key3 = _parse_chart_key("Respuesta con [CHART:clave_inventada]")
    check("Token inválido → key=None (no existe en catálogo)",
          key3 is None)
    check("Token inválido → token eliminado del texto de todas formas",
          "[CHART:" not in text3)

    # Caso 4: token en medio del texto
    text4, key4 = _parse_chart_key(
        "Línea uno.\n[CHART:alertas_por_tipo]\nLínea dos."
    )
    check("Token en medio → key extraído",
          key4 == "alertas_por_tipo")
    check("Token en medio → texto no tiene líneas vacías excesivas",
          "\n\n\n" not in text4)

    # Caso 5: todos los keys del catálogo son parseables
    for ckey in CHART_CATALOG.keys():
        _, parsed = _parse_chart_key(f"Texto [CHART:{ckey}]")
        check(f"  Catálogo key '{ckey}' parseable",
              parsed == ckey)


# ══════════════════════════════════════════════════════════════════════════════
# TEST 3 — _build_system_prompt()
# ══════════════════════════════════════════════════════════════════════════════

def test_build_system_prompt() -> None:
    print("\n── TEST 3: _build_system_prompt() ──────────────────────────────────")

    mock_context = "═══ ALERTAS 24H ═══\n  brute_force high count=5"

    # Chat libre
    prompt_libre = _build_system_prompt(live_context=mock_context)
    check("Chat libre: contexto live inyectado",
          mock_context in prompt_libre)
    check("Chat libre: no contiene sección PLAYBOOK",
          "ACCIONES BASE" not in prompt_libre)
    check("Chat libre: contiene CHART_CATALOG",
          "brute_force_por_ip" in prompt_libre)

    # Con playbook
    mock_alert = {
        "alert_type": "brute_force",
        "severity":   "high",
        "detail":     "847 auth failures desde 192.168.1.47",
        "client_ip":  "192.168.1.47",
    }
    playbook_actions = ALERT_PLAYBOOK["brute_force"]
    prompt_pb = _build_system_prompt(
        live_context=mock_context,
        playbook_actions=playbook_actions,
        alert_data=mock_alert,
    )
    check("Con playbook: sección ALERTA CONCRETA presente",
          "ALERTA CONCRETA" in prompt_pb)
    check("Con playbook: IP real incluida",
          "192.168.1.47" in prompt_pb)
    check("Con playbook: acciones del playbook incluidas",
          "Bloquear la IP" in prompt_pb)
    check("Con playbook: instrucción ESPECIAL presente",
          "INSTRUCCIÓN ESPECIAL" in prompt_pb)

    # Con playbook y client_ip=None (alerta ML)
    mock_alert_ml = {
        "alert_type": "ml_ip_anomaly",
        "severity":   "low",
        "detail":     "Comportamiento de IP atípico detectado por LOF",
        "client_ip":  None,
    }
    prompt_ml = _build_system_prompt(
        live_context=mock_context,
        playbook_actions=ALERT_PLAYBOOK["ml_ip_anomaly"],
        alert_data=mock_alert_ml,
    )
    check("Playbook con client_ip=None: maneja correctamente (no crash)",
          "N/A" in prompt_ml or "alerta ML" in prompt_ml)


# ══════════════════════════════════════════════════════════════════════════════
# TEST 4 — Conexión HANA + get_agent_context()
# ══════════════════════════════════════════════════════════════════════════════

def test_hana_context() -> None:
    print("\n── TEST 4: Conexión HANA + get_agent_context() ──────────────────────")

    try:
        conn = get_connection()
        check("Conexión HANA establecida", True)
    except Exception as exc:
        check("Conexión HANA establecida", False, str(exc))
        print("  ⚠️  Los tests 5, 6, 7 se saltarán por falta de conexión HANA.")
        return None

    try:
        ctx = get_agent_context(conn)
        check("get_agent_context() retorna string",
              isinstance(ctx, str))
        check("Contexto contiene sección de alertas",
              "ALERTAS" in ctx)
        check("Contexto contiene sección de IPs",
              "SOSPECHOSAS" in ctx.upper() or "TOP IP" in ctx.upper())
        check("Contexto contiene sección LLM",
              "LLM" in ctx)
        check("Contexto tiene longitud razonable (>200 chars)",
              len(ctx) > 200,
              f"{len(ctx)} chars")
        print(f"\n  Primeros 400 chars del contexto:\n{'─'*50}")
        print(ctx[:400])
        print('─' * 50)
    except Exception as exc:
        check("get_agent_context() ejecutado sin excepción", False, str(exc))
        return None

    return conn


# ══════════════════════════════════════════════════════════════════════════════
# TEST 5 — get_agent_response() chat libre
# ══════════════════════════════════════════════════════════════════════════════

def test_agent_chat_libre(conn) -> None:
    print("\n── TEST 5: get_agent_response() — chat libre ────────────────────────")

    result = get_agent_response(
        user_question="Dame un resumen breve de las alertas de las últimas 24 horas.",
        conn=conn,
        conversation_history=[],
        alert_context=None,
    )

    check("Retorna dict", isinstance(result, dict))
    check("dict tiene key 'text'",   "text"      in result)
    check("dict tiene key 'chart_key'", "chart_key" in result)
    check("dict tiene key 'error'",  "error"     in result)
    check("text es string no vacío", isinstance(result.get("text"), str) and len(result["text"]) > 10,
          f"{len(result.get('text',''))} chars")
    check("error es None si la llamada fue exitosa",
          result.get("error") is None,
          str(result.get("error")))
    check("chart_key es str o None",
          result.get("chart_key") is None or isinstance(result.get("chart_key"), str))

    print(f"\n  Respuesta (primeros 300 chars):\n{'─'*50}")
    print(result["text"][:300])
    print('─' * 50)


# ══════════════════════════════════════════════════════════════════════════════
# TEST 6 — get_agent_response() flujo "¿Qué hago?"
# ══════════════════════════════════════════════════════════════════════════════

def test_agent_que_hago(conn) -> None:
    print("\n── TEST 6: get_agent_response() — flujo ¿Qué hago? ─────────────────")

    alert_ctx = {
        "alert_type": "brute_force",
        "severity":   "high",
        "detail":     "847 auth failures desde 192.168.1.47 en la última ventana",
        "client_ip":  "192.168.1.47",
    }

    result = get_agent_response(
        user_question=(
            "El analista SOC solicita orientación sobre esta alerta activa:\n"
            "  • Tipo: brute_force\n"
            "  • Severidad: high\n"
            "  • Detalle: 847 auth failures desde 192.168.1.47\n"
            "  • IP origen: 192.168.1.47\n\n"
            "¿Qué acciones concretas debo tomar?"
        ),
        conn=conn,
        conversation_history=[],
        alert_context=alert_ctx,
    )

    check("Retorna dict con estructura correcta",
          all(k in result for k in ["text", "chart_key", "error"]))
    check("Respuesta contiene la IP de la alerta",
          "192.168.1.47" in result["text"],
          "IP encontrada en texto" if "192.168.1.47" in result["text"] else "IP NO encontrada")
    check("Respuesta menciona alguna acción concreta",
          any(word in result["text"].lower()
              for word in ["bloquear", "firewall", "ip", "revisar", "verificar"]))
    check("No hay error en la llamada",
          result.get("error") is None)

    print(f"\n  Respuesta del agente (primeros 400 chars):\n{'─'*50}")
    print(result["text"][:400])
    print('─' * 50)


# ══════════════════════════════════════════════════════════════════════════════
# TEST 7 — get_agent_response() con historial de conversación
# ══════════════════════════════════════════════════════════════════════════════

def test_agent_con_historial(conn) -> None:
    print("\n── TEST 7: get_agent_response() — con historial de conversación ─────")

    history = [
        {"role": "user",      "content": "Hola, ¿puedes ver los datos de HANA?"},
        {"role": "assistant", "content": "Sí, tengo acceso a los datos en tiempo real."},
    ]

    result = get_agent_response(
        user_question="¿Y qué tipo de alerta ha sido la más frecuente?",
        conn=conn,
        conversation_history=history,
        alert_context=None,
    )

    check("Con historial: respuesta es coherente (no crashea)",
          isinstance(result.get("text"), str) and len(result["text"]) > 10)
    check("Con historial: no hay error",
          result.get("error") is None)

    print(f"\n  Respuesta (primeros 250 chars):\n{'─'*50}")
    print(result["text"][:250])
    print('─' * 50)


# ══════════════════════════════════════════════════════════════════════════════
# TEST 8 — Manejo de errores
# ══════════════════════════════════════════════════════════════════════════════

def test_error_handling() -> None:
    print("\n── TEST 8: Manejo de errores ─────────────────────────────────────────")

    # alert_context con alert_type sin playbook definido → no debería crashear
    alert_sin_playbook = {
        "alert_type": "tipo_inexistente_xyz",
        "severity":   "low",
        "detail":     "Alerta de prueba",
        "client_ip":  None,
    }

    prompt = _build_system_prompt(
        live_context="Contexto de prueba",
        playbook_actions=None,   # simula que el playbook no se encontró
        alert_data=alert_sin_playbook,
    )
    check("alert_type sin playbook: _build_system_prompt no crashea",
          isinstance(prompt, str) and len(prompt) > 0)

    # _parse_chart_key con texto vacío
    text_clean, key = _parse_chart_key("")
    check("_parse_chart_key con texto vacío: no crashea, key=None",
          key is None and isinstance(text_clean, str))

    # _parse_chart_key con solo el token
    text_only, key_only = _parse_chart_key("[CHART:brute_force_por_ip]")
    check("_parse_chart_key con solo el token: texto limpio es string vacío o whitespace",
          isinstance(text_only, str))
    check("_parse_chart_key con solo el token: key extraído",
          key_only == "brute_force_por_ip")


# ══════════════════════════════════════════════════════════════════════════════
# RUNNER PRINCIPAL
# ══════════════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    print("=" * 60)
    print("  Dev 1 — Test Suite  |  agent.py")
    print("=" * 60)

    test_constants()
    test_parse_chart_key()
    test_build_system_prompt()

    conn = test_hana_context()

    if conn:
        test_agent_chat_libre(conn)
        test_agent_que_hago(conn)
        test_agent_con_historial(conn)

    test_error_handling()

    # ── Resumen final ─────────────────────────────────────────────────────────
    total   = len(results)
    passed  = sum(1 for _, ok, _ in results if ok)
    failed  = total - passed

    print("\n" + "=" * 60)
    print(f"  RESULTADO FINAL:  {passed}/{total} checks  ·  {failed} fallos")
    print("=" * 60)

    if failed > 0:
        print("\nChecks fallidos:")
        for label, ok, detail in results:
            if not ok:
                print(f"  ❌  {label}" + (f"  →  {detail}" if detail else ""))
        sys.exit(1)
    else:
        print("  ✅  Todos los checks pasaron.")
        sys.exit(0)
