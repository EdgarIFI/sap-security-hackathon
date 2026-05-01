"""
app/quick_filter.py
===================
Módulo de detección determinística de amenazas de seguridad.

Responsabilidad única: analizar cada batch de registros nuevos (df_nuevos)
y retornar una lista de amenazas detectadas. No envía alertas — solo detecta.
Quien llama a este módulo (pipeline_loop.py) decide qué hacer con los resultados.

--- POSICIÓN EN EL PIPELINE ---

    fetch_current_window()
          ↓
    deduplicación (memoria + HANA)
          ↓
    upsert_logs(df_nuevos)          ← datos ya en HANA
          ↓
    filtrar_amenazas(df_nuevos)     ← ESTE MÓDULO — opera sobre raw, no ETL
          ↓
    enviar_alerta() por cada amenaza detectada

--- DISEÑO: DETERMINÍSTICO vs ML ---

Este módulo usa reglas explícitas basadas en el conocimiento del schema.
No es ML — no aprende, no predice, no usa modelos.

Ventajas frente al modelo ML para este rol:
  - Latencia cero de entrenamiento: funciona desde el primer registro
  - Determinismo: el mismo input siempre produce el mismo output
  - Explicabilidad: cada alerta tiene una razón concreta y auditable
  - MTTD mínimo: corre cada 1-2 min sobre df_nuevos

El modelo ML (model.py, AI Specialist) corre cada 30 min sobre la ventana
completa y puede detectar patrones más sutiles que estas reglas no capturan.
Ambos son complementarios — no se reemplazan.

--- CONTRATO PÚBLICO ---

    from app.quick_filter import filtrar_amenazas

    amenazas = filtrar_amenazas(df_nuevos)
    # amenazas es una lista de dicts, cada uno con:
    # {
    #   "alert_type":  str,   # ej: "brute_force"
    #   "severity":    str,   # "low" | "medium" | "high" | "critical"
    #   "details":     str,   # descripción concreta con números
    #   "log_id":      str,   # _id del registro que disparó la regla
    #   "event_time":  str,   # @timestamp del evento (ISO UTC)
    # }

    for a in amenazas:
        enviar_alerta(**a, conn=conn, window_start=window_start)

--- COLUMNAS USADAS POR ESTE MÓDULO ---

De RAW_LOGS_SISTEMA (logs con sap_function_log_type IN INFO/WARNING/ERROR/etc.):
  - _id                        → log_id de la alerta
  - @timestamp                 → event_time de la alerta
  - sap_function_log_type      → discriminante: detectar SECURITY directo
  - http_status_code           → "401", "403", "404" etc. (string, no int)
  - client_ip                  → IP del cliente (para agrupar brute force)
  - heathers_request_path      → TYPO OFICIAL de la API — viene así, no corregir

De RAW_LOGS_LLM (logs con sap_function_log_type IN LLM_REQUEST/LLM_ERROR/LLM_TIMEOUT):
  - _id                        → log_id de la alerta
  - @timestamp                 → event_time de la alerta
  - sap_function_log_type      → discriminante: LLM_ERROR, LLM_TIMEOUT
  - llm_cost_usd               → float, None si no aplica
  - llm_response_time_ms       → float, None si no aplica
  - llm_model_id               → para incluir en el detalle de la alerta
"""

import logging
from typing import Optional
import pandas as pd

logger = logging.getLogger("quick_filter")

# ---------------------------------------------------------------------------
# Umbrales de detección — ajustables sin tocar la lógica de las reglas
# ---------------------------------------------------------------------------

# Brute force: cuántos errores 401/403 desde la misma IP en un batch
# para considerarlo un ataque de fuerza bruta.
# El batch tiene ~50-500 registros nuevos por ciclo de 1-2 min.
# 5 intentos en 1-2 min desde la misma IP es claramente sospechoso.
UMBRAL_BRUTE_FORCE_INTENTOS = 5

# Path scanning: cuántos 404 desde la misma IP en un batch
# para considerarlo un escaneo de directorios.
UMBRAL_PATH_SCAN_INTENTOS = 3

# Costo LLM: umbral en USD para disparar alerta de costo alto.
# Un error LLM que costó más de $1 es anómalo (posible prompt injection loop).
UMBRAL_LLM_COSTO_USD = 1.0

# Latencia LLM: umbral en ms para disparar alerta de respuesta lenta.
# 10 segundos es 10x el tiempo normal de respuesta.
UMBRAL_LLM_RESPONSE_MS = 10_000.0

# Rutas sospechosas que indican escaneo de vulnerabilidades conocidas.
# Son rutas que ninguna aplicación SAP legítima debería solicitar.
RUTAS_SOSPECHOSAS = {
    "/phpmyadmin",
    "/cgi-bin",
    "/wp-admin",
    "/wp-login",
    "/.env",
    "/config",
    "/admin",
    "/.git",
    "/etc/passwd",
    "/shell",
    "/cmd",
    "/.htaccess",
    "/backup",
    "/xmlrpc.php",
    "/robots.txt",   # exploración de estructura
    "/sitemap.xml",  # exploración de estructura
}

# Tipos de log LLM que indican un problema (no solo información normal)
TIPOS_LLM_PROBLEMA = {"LLM_ERROR", "LLM_TIMEOUT"}


# ---------------------------------------------------------------------------
# Función principal pública
# ---------------------------------------------------------------------------

def filtrar_amenazas(df: pd.DataFrame) -> list[dict]:
    """
    Analiza un batch de registros nuevos y retorna todas las amenazas detectadas.

    Parámetros
    ----------
    df : pd.DataFrame
        DataFrame con registros nuevos del ciclo actual. Son datos crudos
        (raw) tal como vienen de la API — no han pasado por ETL.
        Puede contener tanto logs de Sistema como de LLM mezclados.
        Puede estar vacío (df.empty == True) — en ese caso retorna lista vacía.

    Retorna
    -------
    list[dict]
        Lista de amenazas detectadas. Puede estar vacía si no hay amenazas.
        Cada elemento tiene exactamente las claves que espera enviar_alerta():
        {
            "alert_type": str,
            "severity":   str,
            "details":    str,
            "log_id":     str,
            "event_time": str,
        }

    Notas
    -----
    - Una misma amenaza puede disparar múltiples reglas (ej: un registro
      SECURITY con 401 dispara tanto "security_event" como contribuye al
      conteo de "brute_force"). Cada regla es independiente.
    - Los NaN/None en columnas numéricas se manejan con pd.notna() antes
      de comparar — nunca comparamos NaN directamente.
    - Las columnas string (http_status_code, etc.) vienen como object dtype
      en pandas — comparamos con strings, no con ints.
    """
    if df is None or df.empty:
        logger.debug("[FILTER] df vacío — sin amenazas que evaluar")
        return []

    logger.info(f"[FILTER] Evaluando {len(df)} registros nuevos")

    amenazas: list[dict] = []

    # Separar los dos tipos de log usando la columna discriminante.
    # sap_function_log_type es string — los LLM empiezan con "LLM_"
    mask_llm = df["sap_function_log_type"].str.startswith("LLM_", na=False)
    df_sistema = df[~mask_llm].copy()
    df_llm     = df[mask_llm].copy()

    logger.debug(
        f"[FILTER] Split: {len(df_sistema)} sistema, {len(df_llm)} LLM"
    )

    # --- Reglas sobre logs de Sistema ---
    if not df_sistema.empty:
        amenazas.extend(_regla_security_event(df_sistema))
        amenazas.extend(_regla_brute_force(df_sistema))
        amenazas.extend(_regla_path_scan(df_sistema))

    # --- Reglas sobre logs LLM ---
    if not df_llm.empty:
        amenazas.extend(_regla_llm_error_costo_alto(df_llm))
        amenazas.extend(_regla_llm_timeout(df_llm))
        amenazas.extend(_regla_llm_respuesta_lenta(df_llm))

    if amenazas:
        logger.warning(
            f"[FILTER] ⚠️  {len(amenazas)} amenaza(s) detectada(s) en este batch"
        )
        for a in amenazas:
            logger.warning(
                f"[FILTER]   → {a['alert_type']} ({a['severity']}) | {a['details'][:80]}"
            )
    else:
        logger.info("[FILTER] Sin amenazas en este batch")

    return amenazas


# ---------------------------------------------------------------------------
# Reglas sobre logs de Sistema
# ---------------------------------------------------------------------------

def _regla_security_event(df: pd.DataFrame) -> list[dict]:
    """
    Regla 1 — SECURITY directo.

    Cualquier log con sap_function_log_type == 'SECURITY' es una amenaza
    de alta severidad por definición. SAP ya los marcó explícitamente.

    DISEÑO DE AGRUPACIÓN: genera UNA sola alerta por batch con el conteo
    total y las IPs más frecuentes. Evita spam de alertas cuando hay decenas
    de eventos SECURITY en un mismo ciclo (ej: ataque sostenido).

    Severidad: HIGH si hay ≥ 3 eventos; MEDIUM si hay 1-2 eventos aislados.
    """
    df_sec = df[df["sap_function_log_type"] == "SECURITY"]

    if df_sec.empty:
        return []

    count = len(df_sec)
    logger.debug(f"[FILTER] Regla SECURITY: {count} evento(s)")

    # Tomar el primer registro como representativo para log_id y event_time
    primer = df_sec.iloc[0]
    log_id     = _safe_str(primer.get("_id"))
    event_time = _safe_str(primer.get("@timestamp"))

    # IPs únicas involucradas (máx 3 en el mensaje)
    ips_unicas = df_sec["client_ip"].dropna().unique().tolist()
    ips_str = ", ".join(str(ip) for ip in ips_unicas[:3])
    if len(ips_unicas) > 3:
        ips_str += f" (+{len(ips_unicas) - 3} more)"

    # Distribución de status codes
    if "http_status_code" in df_sec.columns:
        status_counts = df_sec["http_status_code"].dropna().value_counts().to_dict()
        status_str = ", ".join(f"{cnt}x HTTP {code}" for code, cnt in list(status_counts.items())[:3])
    else:
        status_str = "N/A"

    details = (
        f"{count} SECURITY event(s) in this batch. "
        f"IPs: {ips_str if ips_str else 'unknown'}. "
        f"Status: {status_str}"
    )

    severity = "high" if count >= 3 else "medium"

    return [_amenaza(
        alert_type = "security_event",
        severity   = severity,
        details    = details,
        log_id     = log_id,
        event_time = event_time,
    )]


def _regla_brute_force(df: pd.DataFrame) -> list[dict]:
    """
    Regla 2 — Brute force por IP.

    Detecta múltiples intentos de autenticación fallida (HTTP 401 o 403)
    desde la misma IP en el mismo batch (ventana de 1-2 minutos).

    Si una IP acumula >= UMBRAL_BRUTE_FORCE_INTENTOS errores 401/403,
    se genera UNA alerta por IP (no una por registro).

    Severidad: HIGH — acceso no autorizado repetido es un ataque activo.

    Por qué agrupamos por IP y no por registro:
    Un brute force es un patrón, no un evento único. Una sola alerta por
    IP con el conteo total es más útil operacionalmente que 15 alertas
    individuales que sobrecargarían el dashboard de SAP.
    """
    resultados = []

    # http_status_code viene como string en la API ("401", "403", "200", etc.)
    # Filtramos los errores de autenticación/autorización
    mask_auth_error = df["http_status_code"].isin(["401", "403"])
    df_auth = df[mask_auth_error].copy()

    if df_auth.empty:
        return []

    # Agrupar por IP y contar intentos
    # client_ip puede tener NaN (en logs LLM, pero ya separamos antes de llamar)
    df_auth_con_ip = df_auth[df_auth["client_ip"].notna()].copy()

    if df_auth_con_ip.empty:
        return []

    conteo_por_ip = (
        df_auth_con_ip
        .groupby("client_ip")
        .agg(
            intentos    = ("_id", "count"),
            primer_log  = ("_id", "first"),        # log_id representativo
            primer_ts   = ("@timestamp", "first"),  # timestamp representativo
            status_list = ("http_status_code", lambda x: x.value_counts().to_dict()),
        )
        .reset_index()
    )

    # Filtrar solo las IPs que superan el umbral
    ips_sospechosas = conteo_por_ip[
        conteo_por_ip["intentos"] >= UMBRAL_BRUTE_FORCE_INTENTOS
    ]

    if ips_sospechosas.empty:
        return []

    logger.debug(
        f"[FILTER] Brute force: {len(ips_sospechosas)} IP(s) sobre umbral "
        f"de {UMBRAL_BRUTE_FORCE_INTENTOS} intentos"
    )

    for _, fila in ips_sospechosas.iterrows():
        ip       = str(fila["client_ip"])
        intentos = int(fila["intentos"])
        status_d = fila["status_list"]  # ej: {"401": 12, "403": 3}

        # Construir descripción de status codes para el detalle
        status_str = ", ".join(
            f"{cnt} HTTP {code}" for code, cnt in sorted(status_d.items())
        )

        details = (
            f"{intentos} auth failures from IP {ip} "
            f"in this polling cycle ({status_str})"
        )

        resultados.append(_amenaza(
            alert_type = "brute_force",
            severity   = "high",
            details    = details,
            log_id     = str(fila["primer_log"]),
            event_time = str(fila["primer_ts"]),
        ))

    return resultados


def _regla_path_scan(df: pd.DataFrame) -> list[dict]:
    """
    Regla 3 — Path scanning / directory traversal.

    Detecta peticiones a rutas sospechosas (herramientas de admin, archivos
    de configuración, paneles de WordPress, etc.) con respuesta 404.

    Patrón: GET /phpmyadmin → 404 indica que alguien está sondeando qué
    software corre en el servidor buscando vulnerabilidades conocidas.

    IMPORTANTE — columna con typo oficial de la API:
    La ruta del request viene en 'heathers_request_path' (con typo 'heathers'
    en lugar de 'headers'). Este es el nombre real en la API — no corregir.

    Severidad: MEDIUM — es reconocimiento activo, no explotación directa.
    Pero si hay muchos en poco tiempo (>= umbral), merece alerta.
    """
    resultados = []

    # Verificar que la columna con typo existe en este DataFrame
    # (puede no estar presente si la ventana no tiene logs de sistema con path)
    col_path = "heathers_request_path"  # TYPO OFICIAL — no cambiar
    if col_path not in df.columns:
        logger.debug(f"[FILTER] Columna '{col_path}' no presente en df — omitiendo regla path_scan")
        return []

    # Solo nos interesan 404 (recurso no encontrado — indica sondeo)
    df_404 = df[df["http_status_code"] == "404"].copy()

    if df_404.empty:
        return []

    # Filtrar filas donde la ruta contiene una de las rutas sospechosas
    # Usamos str.contains con regex=False para comparación literal
    def es_ruta_sospechosa(path: str) -> bool:
        if not isinstance(path, str):
            return False
        path_lower = path.lower()
        return any(ruta in path_lower for ruta in RUTAS_SOSPECHOSAS)

    mask_sospechosa = df_404[col_path].apply(es_ruta_sospechosa)
    df_scan = df_404[mask_sospechosa].copy()

    if df_scan.empty:
        return []

    # Agrupar por IP para detectar escaneo sistemático
    df_scan_con_ip = df_scan[df_scan["client_ip"].notna()].copy()

    if df_scan_con_ip.empty:
        # Sin IP — tomar el primer registro sospechoso como alerta individual
        fila = df_scan.iloc[0]
        path = _safe_str(fila.get(col_path)) or "ruta desconocida"
        details = f"Path scan: {path} → 404 (IP desconocida)"
        resultados.append(_amenaza(
            alert_type = "path_scan",
            severity   = "medium",
            details    = details,
            log_id     = _safe_str(fila.get("_id")),
            event_time = _safe_str(fila.get("@timestamp")),
        ))
        return resultados

    # Agrupar por IP
    conteo_por_ip = (
        df_scan_con_ip
        .groupby("client_ip")
        .agg(
            intentos   = ("_id", "count"),
            primer_log = ("_id", "first"),
            primer_ts  = ("@timestamp", "first"),
            rutas      = (col_path, lambda x: list(x.dropna().unique())[:5]),  # máx 5 rutas
        )
        .reset_index()
    )

    ips_escaneando = conteo_por_ip[
        conteo_por_ip["intentos"] >= UMBRAL_PATH_SCAN_INTENTOS
    ]

    if ips_escaneando.empty:
        # Hay sondeos pero bajo el umbral — igual alertamos si hay rutas muy críticas
        # (ej: /.env o /etc/passwd son siempre críticas independientemente del conteo)
        rutas_criticas = {"/.env", "/etc/passwd", "/.git"}
        df_critica = df_scan[
            df_scan[col_path].apply(
                lambda p: isinstance(p, str) and
                any(r in p.lower() for r in rutas_criticas)
            )
        ]
        if not df_critica.empty:
            fila = df_critica.iloc[0]
            path  = _safe_str(fila.get(col_path))
            ip    = _safe_str(fila.get("client_ip")) or "IP desconocida"
            details = f"Critical path probe: {path} from IP {ip} → 404"
            resultados.append(_amenaza(
                alert_type = "path_scan",
                severity   = "high",   # rutas críticas → high
                details    = details,
                log_id     = _safe_str(fila.get("_id")),
                event_time = _safe_str(fila.get("@timestamp")),
            ))
        return resultados

    logger.debug(
        f"[FILTER] Path scan: {len(ips_escaneando)} IP(s) sobre umbral "
        f"de {UMBRAL_PATH_SCAN_INTENTOS} rutas sospechosas"
    )

    for _, fila in ips_escaneando.iterrows():
        ip       = str(fila["client_ip"])
        intentos = int(fila["intentos"])
        rutas    = fila["rutas"]
        rutas_str = ", ".join(rutas[:3])  # máx 3 rutas en el mensaje

        details = (
            f"{intentos} suspicious path probes from IP {ip} → 404 "
            f"(paths: {rutas_str})"
        )

        resultados.append(_amenaza(
            alert_type = "path_scan",
            severity   = "medium",
            details    = details,
            log_id     = str(fila["primer_log"]),
            event_time = str(fila["primer_ts"]),
        ))

    return resultados


# ---------------------------------------------------------------------------
# Reglas sobre logs LLM
# ---------------------------------------------------------------------------

def _regla_llm_error_costo_alto(df: pd.DataFrame) -> list[dict]:
    """
    Regla 4 — LLM_ERROR con costo alto.

    Un error LLM con costo alto indica prompts que consumieron muchos tokens
    antes de fallar. Patrón típico de prompt injection o bucles anómalos.

    DISEÑO DE AGRUPACIÓN: genera UNA sola alerta por batch con conteo,
    costo total acumulado y costo máximo individual.

    Severidad: HIGH si hay >= 3 errores costosos; MEDIUM si hay 1-2.
    """
    df_error = df[df["sap_function_log_type"] == "LLM_ERROR"].copy()

    if df_error.empty:
        return []

    mask_costo = (
        df_error["llm_cost_usd"].notna() &
        (df_error["llm_cost_usd"].astype(float) > UMBRAL_LLM_COSTO_USD)
    )
    df_caro = df_error[mask_costo].copy()

    if df_caro.empty:
        return []

    count     = len(df_caro)
    costo_max = df_caro["llm_cost_usd"].astype(float).max()
    costo_sum = df_caro["llm_cost_usd"].astype(float).sum()
    primer    = df_caro.iloc[0]
    modelo_top = _safe_str(primer.get("llm_model_id")) or "unknown"

    logger.debug(f"[FILTER] LLM error costo alto: {count} evento(s), total=${costo_sum:.4f}")

    details = (
        f"{count} high-cost LLM_ERROR(s) in batch. "
        f"Max: ${costo_max:.4f}, total: ${costo_sum:.4f} "
        f"(threshold: ${UMBRAL_LLM_COSTO_USD}). "
        f"Top model: {modelo_top}"
    )

    return [_amenaza(
        alert_type = "high_cost_llm_error",
        severity   = "high" if count >= 3 else "medium",
        details    = details,
        log_id     = _safe_str(primer.get("_id")),
        event_time = _safe_str(primer.get("@timestamp")),
    )]


def _regla_llm_timeout(df: pd.DataFrame) -> list[dict]:
    """
    Regla 5 — LLM_TIMEOUT.

    Cualquier log de tipo LLM_TIMEOUT indica que el modelo no respondió
    en el tiempo esperado. Puede ser síntoma de prompts extremadamente
    largos, sobrecarga del sistema, o intentos de DoS via prompts.

    Severidad: MEDIUM — timeouts aislados son normales; múltiples en un
    batch corto son sospechosos.
    """
    resultados = []

    df_timeout = df[df["sap_function_log_type"] == "LLM_TIMEOUT"].copy()

    if df_timeout.empty:
        return []

    logger.debug(f"[FILTER] LLM timeout: {len(df_timeout)} evento(s)")

    # Si hay múltiples timeouts en un batch corto, es más grave
    count = len(df_timeout)
    severity = "high" if count >= 3 else "medium"

    # Tomar el primer registro como representativo para la alerta
    fila    = df_timeout.iloc[0]
    modelo  = _safe_str(fila.get("llm_model_id")) or "modelo desconocido"

    details = (
        f"{count} LLM_TIMEOUT event(s) in this polling cycle "
        f"on model={modelo}"
    )

    resultados.append(_amenaza(
        alert_type = "llm_timeout",
        severity   = severity,
        details    = details,
        log_id     = _safe_str(fila.get("_id")),
        event_time = _safe_str(fila.get("@timestamp")),
    ))

    return resultados


def _regla_llm_respuesta_lenta(df: pd.DataFrame) -> list[dict]:
    """
    Regla 6 — LLM respuesta lenta.

    Detecta llamadas LLM con tiempo de respuesta muy alto.

    DISEÑO DE AGRUPACIÓN: genera UNA sola alerta por batch con el conteo
    total, la latencia máxima y la latencia promedio. Evita el spam masivo
    que ocurre cuando decenas o cientos de llamadas LLM son lentas en un
    mismo ciclo (comportamiento normal en ciertos picos de carga).

    Severidad: MEDIUM si hay >= 10 lentas (patrón sistémico); LOW si son
    pocas (puede ser ruido de red puntual).
    """
    mask_lento = (
        df["llm_response_time_ms"].notna() &
        (df["llm_response_time_ms"].astype(float) > UMBRAL_LLM_RESPONSE_MS)
    )
    df_lento = df[mask_lento].copy()

    if df_lento.empty:
        return []

    count   = len(df_lento)
    ms_max  = df_lento["llm_response_time_ms"].astype(float).max()
    ms_mean = df_lento["llm_response_time_ms"].astype(float).mean()
    primer  = df_lento.iloc[0]

    # Modelos más afectados (máx 2)
    if "llm_model_id" in df_lento.columns:
        modelos = df_lento["llm_model_id"].dropna().value_counts().index.tolist()
        modelos_str = ", ".join(str(m) for m in modelos[:2])
    else:
        modelos_str = "unknown"

    logger.debug(
        f"[FILTER] LLM respuesta lenta: {count} evento(s), "
        f"max={ms_max:.0f}ms, avg={ms_mean:.0f}ms"
    )

    details = (
        f"{count} slow LLM response(s) in batch "
        f"(threshold: {UMBRAL_LLM_RESPONSE_MS:.0f}ms). "
        f"Max: {ms_max:.0f}ms, avg: {ms_mean:.0f}ms. "
        f"Models: {modelos_str}"
    )

    return [_amenaza(
        alert_type = "slow_llm_response",
        severity   = "medium" if count >= 10 else "low",
        details    = details,
        log_id     = _safe_str(primer.get("_id")),
        event_time = _safe_str(primer.get("@timestamp")),
    )]


# ---------------------------------------------------------------------------
# Utilidades internas
# ---------------------------------------------------------------------------

def _amenaza(
    alert_type: str,
    severity: str,
    details: str,
    log_id: Optional[str],
    event_time: Optional[str],
) -> dict:
    """
    Constructor de dict de amenaza con valores por defecto seguros.
    Centraliza la estructura para que todas las reglas produzcan
    exactamente el mismo formato que espera enviar_alerta().
    """
    return {
        "alert_type": alert_type,
        "severity":   severity,
        "details":    details or "sin detalles",
        "log_id":     log_id or "unknown",
        "event_time": event_time or "",
    }


def _safe_str(valor) -> Optional[str]:
    """
    Convierte un valor pandas a string de forma segura.

    pandas usa float('nan') para valores nulos en columnas object.
    isinstance(nan, float) es True — no podemos comparar directamente.
    Esta función retorna None si el valor es NaN o None, string si no lo es.
    """
    if valor is None:
        return None
    try:
        if pd.isna(valor):
            return None
    except (TypeError, ValueError):
        # pd.isna() puede fallar con ciertos tipos (listas, dicts)
        pass
    return str(valor)


# ---------------------------------------------------------------------------
# Script de prueba — ejecutar directamente para validar las reglas
# ---------------------------------------------------------------------------

def _crear_df_prueba() -> pd.DataFrame:
    """
    Crea un DataFrame sintético que dispara todas las reglas.
    Usado para validar que las reglas funcionan antes de conectar al pipeline.
    """
    import numpy as np

    registros = [
        # --- SECURITY directo (Regla 1) ---
        {
            "_id": "sec-001", "@timestamp": "2026-04-30T12:00:01Z",
            "sap_function_log_type": "SECURITY",
            "http_status_code": "403", "client_ip": "10.0.0.1",
            "sap_function_message": "Access denied to restricted resource",
            "heathers_request_path": "/admin/users",
            "llm_cost_usd": np.nan, "llm_response_time_ms": np.nan,
            "llm_model_id": np.nan, "llm_error_message": np.nan,
        },
        # --- Brute force: 6 intentos 401 desde misma IP (Regla 2) ---
        *[{
            "_id": f"bf-00{i}", "@timestamp": f"2026-04-30T12:00:0{i}Z",
            "sap_function_log_type": "ERROR",
            "http_status_code": "401", "client_ip": "203.0.113.99",
            "sap_function_message": "Authentication failed",
            "heathers_request_path": "/api/login",
            "llm_cost_usd": np.nan, "llm_response_time_ms": np.nan,
            "llm_model_id": np.nan, "llm_error_message": np.nan,
        } for i in range(1, 7)],
        # --- Path scan: 4 rutas sospechosas desde misma IP (Regla 3) ---
        *[{
            "_id": f"ps-00{i}", "@timestamp": f"2026-04-30T12:01:0{i}Z",
            "sap_function_log_type": "WARNING",
            "http_status_code": "404", "client_ip": "198.51.100.5",
            "sap_function_message": "Not found",
            "heathers_request_path": ruta,
            "llm_cost_usd": np.nan, "llm_response_time_ms": np.nan,
            "llm_model_id": np.nan, "llm_error_message": np.nan,
        } for i, ruta in enumerate(["/phpmyadmin", "/cgi-bin", "/wp-admin", "/.env"], 1)],
        # --- LLM_ERROR con costo alto (Regla 4) ---
        {
            "_id": "llm-err-001", "@timestamp": "2026-04-30T12:02:00Z",
            "sap_function_log_type": "LLM_ERROR",
            "http_status_code": None, "client_ip": None,
            "sap_function_message": None,
            "heathers_request_path": None,
            "llm_cost_usd": 2.75, "llm_response_time_ms": 8500.0,
            "llm_total_tokens": 15000,
            "llm_model_id": "gpt-4-turbo", "llm_error_message": "Context length exceeded",
        },
        # --- LLM_TIMEOUT (Regla 5) ---
        {
            "_id": "llm-to-001", "@timestamp": "2026-04-30T12:03:00Z",
            "sap_function_log_type": "LLM_TIMEOUT",
            "http_status_code": None, "client_ip": None,
            "sap_function_message": None,
            "heathers_request_path": None,
            "llm_cost_usd": np.nan, "llm_response_time_ms": np.nan,
            "llm_model_id": "gpt-4", "llm_error_message": None,
        },
        # --- LLM respuesta lenta (Regla 6) ---
        {
            "_id": "llm-slow-001", "@timestamp": "2026-04-30T12:04:00Z",
            "sap_function_log_type": "LLM_REQUEST",
            "http_status_code": None, "client_ip": None,
            "sap_function_message": None,
            "heathers_request_path": None,
            "llm_cost_usd": 0.05, "llm_response_time_ms": 15200.0,
            "llm_model_id": "claude-3-opus", "llm_error_message": None,
        },
        # --- Registro normal — NO debe disparar ninguna regla ---
        {
            "_id": "ok-001", "@timestamp": "2026-04-30T12:05:00Z",
            "sap_function_log_type": "INFO",
            "http_status_code": "200", "client_ip": "192.168.1.10",
            "sap_function_message": "Request processed",
            "heathers_request_path": "/api/data",
            "llm_cost_usd": None, "llm_response_time_ms": None,
            "llm_model_id": None, "llm_error_message": None,
        },
    ]

    return pd.DataFrame(registros)


def probar_quick_filter() -> None:
    """
    Prueba todas las reglas con datos sintéticos.

    Uso:
        python -m app.quick_filter
    """
    import logging as _logging
    _logging.basicConfig(
        level=_logging.DEBUG,
        format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
        datefmt="%H:%M:%S",
    )

    print("\n" + "=" * 60)
    print("PRUEBA DE quick_filter — app/quick_filter.py")
    print("=" * 60)

    df_prueba = _crear_df_prueba()
    print(f"\nDataFrame de prueba: {len(df_prueba)} registros")
    print(f"Tipos: {df_prueba['sap_function_log_type'].value_counts().to_dict()}")

    print("\n--- Ejecutando filtrar_amenazas() ---\n")
    amenazas = filtrar_amenazas(df_prueba)

    print(f"\nTotal amenazas detectadas: {len(amenazas)}")
    print()

    reglas_esperadas = {
        "security_event", "brute_force", "path_scan",
        "high_cost_llm_error", "llm_timeout", "slow_llm_response",
    }
    reglas_detectadas = {a["alert_type"] for a in amenazas}

    for i, a in enumerate(amenazas, 1):
        print(f"  [{i}] {a['alert_type']} ({a['severity']})")
        print(f"       log_id: {a['log_id']}")
        print(f"       details: {a['details']}")
        print()

    # Verificar que todas las reglas dispararon
    faltantes = reglas_esperadas - reglas_detectadas
    if faltantes:
        print(f"⚠️  REGLAS NO DISPARADAS (revisar): {faltantes}")
    else:
        print("✅ Todas las reglas detectaron su amenaza correspondiente")

    # Verificar que el registro normal NO disparó ninguna regla
    ids_alertados = {a["log_id"] for a in amenazas}
    if "ok-001" in ids_alertados:
        print("❌ ERROR: el registro normal (ok-001) disparó una alerta — revisar lógica")
    else:
        print("✅ El registro normal no disparó falsas alarmas")

    print("\n" + "=" * 60)
    print("Prueba completada.")
    print("=" * 60 + "\n")


if __name__ == "__main__":
    probar_quick_filter()
