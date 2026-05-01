# SAP SOC Log Ingestion API — Diccionario Completo
## SAP AI Security Anomaly Detection Hackathon · TEC de Monterrey × SAP
**Versión de la API:** 1.0.0 (OAS 3.1)  
**URL base:** `https://sap-api-b4.674318.xyz`  
**Última verificación:** 30 de Abril 2026

---

## Propósito de este documento

Referencia técnica completa de todos los endpoints, schemas, comportamientos y
contratos de la API SAP SOC. Usar antes de escribir cualquier código que interactúe
con la API. Este documento es la fuente de verdad para el equipo.

---

## Resumen de endpoints

| Método | Endpoint | Auth | Propósito |
|---|---|---|---|
| `GET` | `/health` | ❌ No | Liveness probe — verifica que el servidor vive |
| `GET` | `/info` | ✅ Bearer | Metadata de la ventana activa (batch_size, total_pages, etc.) |
| `GET` | `/logs/current` | ✅ Bearer | Logs de la ventana activa, paginados |
| `POST` | `/alert` | ✅ Bearer | Enviar una alerta de seguridad detectada |

---

## Autenticación

Todos los endpoints excepto `/health` requieren Bearer token en el header:

```
Authorization: Bearer <team-token>
```

- El token es por equipo, distribuido al inicio del hackathon.
- Va en `.env` como `BEARER_TOKEN`. Nunca en el código.
- En Cloud Foundry: `cf set-env sap-ai-soc BEARER_TOKEN "..."`.
- Si el token es rechazado (HTTP 401), contactar al staff técnico.

---

## Endpoint 1 — `GET /health`

**Propósito:** Liveness probe. Verifica que el servidor está operativo.  
**Autenticación:** No requerida.  
**Uso en el proyecto:** CF lo usa como health check (`health-check-type: process`). También en `prueba_health.py`.

### Request
```
GET https://sap-api-b4.674318.xyz/health
```
Sin parámetros. Sin headers de auth.

### Response — 200 OK
```json
{
  "status": "ok"
}
```

### Notas
- Siempre responde `"ok"` cuando el servidor está vivo.
- No indica nada sobre el estado de los datos — para eso usar `/info`.

---

## Endpoint 2 — `GET /info`

**Propósito:** Metadata de la ventana activa. Llamar **una vez** al inicio de cada ciclo de ingesta para saber cuántas páginas hay que leer.  
**Autenticación:** Bearer token requerido.  
**Uso en el proyecto:** `ingest.py` lo llama antes de iterar páginas.

### Request
```
GET https://sap-api-b4.674318.xyz/info
Authorization: Bearer <team-token>
```
Sin parámetros.

### Response — 200 OK
```json
{
  "batch_size":    500,
  "window_start":  "2026-03-18T12:00:00+00:00",
  "window_end":    "2026-03-18T12:30:00+00:00",
  "total_records": 54832,
  "total_pages":   110
}
```

### Schema — InfoResponse
| Campo | Tipo | Descripción |
|---|---|---|
| `batch_size` | integer | Registros por página. Fijo en 500, configurado por el servidor. El cliente no puede cambiarlo. |
| `window_start` | string (ISO-8601 UTC) | Inicio de la ventana de 30 min activa (inclusive). |
| `window_end` | string (ISO-8601 UTC) | Fin de la ventana de 30 min activa (exclusive). |
| `total_records` | integer | Total de registros disponibles en la ventana actual. Variable (~5,500 en condiciones normales). |
| `total_pages` | integer | Cuántas llamadas a `/logs/current?page=N` hay que hacer para cubrir toda la ventana. |

### Errores
| Código | Descripción |
|---|---|
| 401 | Missing or invalid Bearer token. |
| 503 | Data not loaded yet — el servidor está arrancando o cargando datos. |

### Patrón de uso correcto
```python
# Llamar ANTES de empezar el loop de paginación
info = requests.get(f"{BASE_URL}/info", headers=headers).json()
total_pages = info["total_pages"]
window_start = info["window_start"]

# Luego iterar
for page in range(1, total_pages + 1):
    data = requests.get(f"{BASE_URL}/logs/current", headers=headers,
                        params={"page": page}).json()
```

---

## Endpoint 3 — `GET /logs/current`

**Propósito:** Logs de la ventana UTC activa, una página a la vez.  
**Autenticación:** Bearer token requerido.  
**Uso en el proyecto:** `ingest.py` — loop paginado en `fetch_current_window()`.

### Request
```
GET https://sap-api-b4.674318.xyz/logs/current?page=N
Authorization: Bearer <team-token>
```

### Parámetros
| Nombre | Tipo | Obligatorio | Default | Descripción |
|---|---|---|---|---|
| `page` | integer (query) | No | 1 | Número de página, base 1. Mínimo: 1. Máximo: `total_pages` del `/info`. |

### Comportamiento temporal — ventana activa
La API siempre devuelve la ventana UTC de 30 minutos **actual**. No hay parámetro de fecha — el servidor calcula la ventana desde su propio reloj:

| Minuto UTC del servidor | Ventana servida |
|---|---|
| 00 – 29 | `HH:00:00` → `HH:30:00` (exclusive) |
| 30 – 59 | `HH:30:00` → `HH+1:00:00` (exclusive) |

**Consecuencia crítica:** si no se ingesta una ventana, esos registros desaparecen para siempre. No hay histórico.

### Response — 200 OK
```json
{
  "request_time_utc": "2026-03-18T12:17:43.521042+00:00",
  "window_start":     "2026-03-18T12:00:00+00:00",
  "window_end":       "2026-03-18T12:30:00+00:00",
  "total_records":    54832,
  "batch_size":       500,
  "current_page":     1,
  "total_pages":      110,
  "records_in_page":  500,
  "data": [ { ... }, { ... } ]
}
```

### Schema — LogsResponse
| Campo | Tipo | Descripción |
|---|---|---|
| `request_time_utc` | string (ISO-8601) | Timestamp exacto del reloj del servidor cuando procesó este request. Todas las ventanas se derivan de este valor. |
| `window_start` | string (ISO-8601) | Inicio de la ventana activa (inclusive). |
| `window_end` | string (ISO-8601) | Fin de la ventana activa (exclusive). |
| `total_records` | integer | Total de registros en la ventana completa. |
| `batch_size` | integer | Registros por página (siempre 500). |
| `current_page` | integer | Número de página de esta respuesta. |
| `total_pages` | integer | Total de páginas disponibles en esta ventana. |
| `records_in_page` | integer | Registros incluidos en esta respuesta (puede ser < batch_size en la última página). |
| `data` | array<object> | Los registros de log. Ver Schema de columnas abajo. |

### Errores
| Código | Descripción | Qué hacer |
|---|---|---|
| 401 | Missing or invalid Bearer token. | Verificar BEARER_TOKEN en .env. No reintentar automáticamente — es fatal. |
| 422 | Requested page is out of range. | La ventana cambió durante la extracción. Reiniciar el ciclo de ingesta desde página 1. |
| 503 | Data not loaded yet. | Reintentar con backoff. El servidor está cargando datos. |

### Patrón de paginación recomendado (de la documentación oficial)
```python
# 1. Obtener total_pages desde la primera página
r = requests.get(f"{BASE}/logs/current", headers=HEADERS, params={"page": 1})
payload = r.json()
all_records = payload["data"]

# 2. Iterar las páginas restantes
for page in range(2, payload["total_pages"] + 1):
    r = requests.get(f"{BASE}/logs/current", headers=HEADERS, params={"page": page})
    all_records.extend(r.json()["data"])

# 3. Construir DataFrame una sola vez (no pd.concat iterativo — es O(n²))
df = pd.DataFrame(all_records)
```

---

## Endpoint 4 — `POST /alert`  ← NUEVO — crítico para el proyecto

**Propósito:** Enviar una alerta de seguridad detectada por el pipeline al sistema SAP. Cada alerta es etiquetada con la identidad del equipo (derivada del Bearer token) y almacenada centralmente para revisión.  
**Autenticación:** Bearer token requerido — **el mismo token que se usa para `/logs/current`**.  
**Uso en el proyecto:** `app/alerting.py` — función `enviar_alerta()`.

> **Importante:** Este NO es un webhook externo. Es un endpoint de la misma API SAP.  
> La URL es `https://sap-api-b4.674318.xyz/alert`.  
> No se necesita ninguna `WEBHOOK_URL` adicional en `.env`.

### Request
```
POST https://sap-api-b4.674318.xyz/alert
Authorization: Bearer <team-token>
Content-Type: application/json

{
  "message": "WHAT: Brute-force login on SAP-ERP-01. WHEN: 2026-04-26T17:32:00Z. WHY: 63 HTTP 401 from IP 192.168.4.22 in 4 min."
}
```

### Body — AlertRequest
| Campo | Tipo | Obligatorio | Restricción | Descripción |
|---|---|---|---|---|
| `message` | string | ✅ Sí | **Máx. 300 caracteres** | Descripción de la alerta. Debe responder las 3 preguntas obligatorias. |

### Formato obligatorio del mensaje — las 3 preguntas

El mensaje **debe** responder estas tres preguntas. No es opcional:

| # | Pregunta | Ejemplo |
|---|---|---|
| 1 | **What** happened? | `Brute-force login attempt on SAP-ERP-01` |
| 2 | **When** did it occur? | `2026-04-26T17:32:00Z` |
| 3 | **Why** was it triggered? | `63 HTTP 401 from IP 192.168.4.22 in 4 min` |

### Formato de mensaje recomendado
```
WHAT: <qué amenaza>. WHEN: <timestamp ISO UTC>. WHY: <evidencia concreta con números>.
```

Ejemplo completo (64 caracteres — bien dentro del límite de 300):
```
WHAT: Brute-force login on SAP-ERP-01. WHEN: 2026-04-26T17:32:00Z. WHY: 63 HTTP 401 from IP 192.168.4.22 within 4 min.
```

### Response — 201 Created (éxito)
```json
{
  "status":        "alert received",
  "team_name":     "team_alpha",
  "message":       "Suspicious login spike detected on service SAP-ERP-01",
  "timestamp_utc": "2026-04-26T17:41:00.000000+00:00"
}
```

### Schema — AlertResponse
| Campo | Tipo | Descripción |
|---|---|---|
| `status` | string | Siempre `"alert received"` si el POST fue exitoso. |
| `team_name` | string | Nombre del equipo derivado del Bearer token. Confirma que la alerta fue atribuida correctamente. |
| `message` | string | El mensaje tal como fue recibido y almacenado. |
| `timestamp_utc` | string (ISO-8601) | Timestamp del servidor de cuándo se registró la alerta. |

### Errores
| Código | Descripción | Causa común |
|---|---|---|
| 201 | Alert received and queued for indexing. | ✅ Éxito |
| 401 | Missing or invalid Bearer token. | Token incorrecto o ausente |
| 422 | Validation error (e.g. message too long). | `message` supera 300 caracteres, o falta el campo |

### Notas de implementación
- El código de éxito es **201**, no 200. Verificar `response.status_code == 201` o `200 <= status < 300`.
- El equipo se identifica **automáticamente** desde el Bearer token — no hay campo `team_id` en el body.
- Si el mensaje supera 300 caracteres, la API devuelve 422. Truncar a 295 chars antes de enviar.
- La autenticación usa `get_headers()` de `config.py` — exactamente igual que `/logs/current`.

---

## Schema de los logs — columnas de `data[]`

### Tipo discriminante: `sap_function_log_type`

Los logs tienen **dos categorías** con columnas distintas. Los nulos son por diseño — no corregir.

| Categoría | Valores de `sap_function_log_type` | Columnas vacías |
|---|---|---|
| **Sistema** | `INFO`, `WARNING`, `ERROR`, `DEBUG`, `AUDIT`, `PERF`, `SECURITY` | Todas las `llm_*` |
| **LLM** | `LLM_REQUEST`, `LLM_ERROR`, `LLM_TIMEOUT` | `service_id`, `http_status_code`, `client_ip` |

### Columnas completas (43 en total)

#### Grupo: Identificación
| Columna | Tipo | Notas |
|---|---|---|
| `_id` | string | Primary key del registro. Usar como `log_id` en HANA y en alertas. |
| `_index` | string | Índice interno de Elasticsearch. Eliminar en ETL. |
| `_score` | float/null | Score de relevancia ES. Eliminar en ETL. |
| `_ignored` | list/null | Campos ignorados por ES. Eliminar en ETL. |

#### Grupo: Temporales
| Columna | Tipo | Notas |
|---|---|---|
| `@timestamp` | string ISO-8601 | Timestamp del evento. Campo principal para series de tiempo. |
| `@event_time_requested` | string ISO-8601 | Timestamp de cuándo se solicitó el evento. |
| `@version` | string | Versión del schema. |

#### Grupo: Clasificación
| Columna | Tipo | Notas |
|---|---|---|
| `sap_function_log_type` | string | **Campo discriminante.** Ver tabla de categorías arriba. |
| `sap_source_type` | string | Tipo de fuente SAP. |
| `sap_function_application` | string | Aplicación SAP que generó el log. |
| `sap_app_env` | string | Entorno de la aplicación (prod, dev, etc.). |
| `sap_function_message` | string | Mensaje del log. |
| `event_code_version` | string | Versión del código de evento. |
| `event_hash` | string | Hash del evento para deduplicación. |

#### Grupo: Red/HTTP — Solo logs de Sistema
| Columna | Tipo | Notas |
|---|---|---|
| `service_id` | string/null | ID del servicio. NULL en logs LLM. |
| `http_status_code` | string/null | Código de respuesta HTTP (ej: "401", "404"). NULL en logs LLM. Clave para quick_filter. |
| `client_ip` | string/null | IP del cliente. NULL en logs LLM. Clave para detección de brute force. |
| `headers_http_host` | string/null | Header Host de la petición HTTP. |
| `headers_http_request_method` | string/null | Método HTTP (GET, POST, etc.). |
| `headers_content_type` | string/null | Content-Type de la petición. |
| `heathers_request_path` | string/null | **Typo oficial de la API** — viene como `heathers` no `headers`. No corregir. Ruta del request HTTP. Clave para detección de path scanning. |

#### Grupo: Región
| Columna | Tipo | Notas |
|---|---|---|
| `region_id` | string | ID de región SAP. |
| `region_name` | string | Nombre de la región. |
| `region_code` | string | Código de la región. |
| `macro_region` | string | Macro-región geográfica. |

#### Grupo: LLM — Modelo — Solo logs LLM
| Columna | Tipo | Notas |
|---|---|---|
| `llm_model_id` | string/null | ID del modelo LLM usado. NULL en logs Sistema. |
| `llm_provider` | string/null | Proveedor del LLM. NULL en logs Sistema. |
| `llm_status` | string/null | Estado de la llamada LLM. NULL en logs Sistema. |
| `llm_error_message` | string/null | Mensaje de error si falló. NULL en logs Sistema. |
| `llm_finish_reason` | string/null | Razón de fin de la generación. NULL en logs Sistema. |

#### Grupo: LLM — Prompt — Solo logs LLM
| Columna | Tipo | Notas |
|---|---|---|
| `llm_prompt_id` | string/null | ID del prompt enviado. NULL en logs Sistema. |
| `llm_prompt_category` | string/null | Categoría del prompt. NULL en logs Sistema. |
| `llm_prompt` | string/null | Texto del prompt. NULL en logs Sistema. |
| `llm_stream` | bool/null | Si la respuesta fue streaming. NULL en logs Sistema. |

#### Grupo: LLM — Métricas — Solo logs LLM
| Columna | Tipo | Notas |
|---|---|---|
| `llm_prompt_tokens` | float/null | Tokens del prompt. NULL en logs Sistema. |
| `llm_completion_tokens` | float/null | Tokens de la respuesta. NULL en logs Sistema. |
| `llm_total_tokens` | float/null | Total de tokens. NULL en logs Sistema. |
| `llm_cost_usd` | float/null | Costo en USD de la llamada. NULL en logs Sistema. Clave para alertas de costo alto. |
| `llm_response_time_ms` | float/null | Latencia de respuesta en ms. NULL en logs Sistema. Clave para alertas de lentitud. |
| `llm_response_size_bytes` | float/null | Tamaño de la respuesta en bytes. NULL en logs Sistema. |
| `llm_temperature` | float/null | Temperatura usada. NULL en logs Sistema. |
| `llm_top_p` | float/null | Top-p usado. NULL en logs Sistema. |

#### Grupo: SAP heredadas
| Columna | Tipo | Notas |
|---|---|---|
| `sap_llm_response_size` | float/null | Posible duplicado de `llm_response_size_bytes`. Pendiente confirmar. |
| `sap_llm_response_time` | float/null | Posible duplicado de `llm_response_time_ms`. Pendiente confirmar. |

---

## Variables de entorno — referencia actualizada

```bash
# API del hackathon
API_BASE_URL=https://sap-api-b4.674318.xyz
BEARER_TOKEN=[token del equipo]

# SAP HANA Cloud
HANA_HOST=[xxx].hanacloud.ondemand.com
HANA_PORT=443
HANA_USER=DBADMIN
HANA_PASSWORD=[contraseña de la instancia]

# Seguridad server.py
JOB_SECRET_TOKEN=[token local para proteger /run]

# NOTA: WEBHOOK_URL ya NO es necesaria.
# El endpoint de alerting es POST /alert en la misma API_BASE_URL.
# La autenticación usa el mismo BEARER_TOKEN.
```

---

## Tabla de códigos de respuesta — resumen

| Endpoint | Código | Significado |
|---|---|---|
| Todos | 401 | Bearer token inválido o ausente → fatal, no reintentar |
| `/health` | 200 | Servidor vivo |
| `/info` | 200 | Metadata de ventana OK |
| `/info` | 503 | Datos no cargados aún → reintentar con backoff |
| `/logs/current` | 200 | Página de logs OK |
| `/logs/current` | 422 | Página fuera de rango → ventana cambió, reiniciar ciclo |
| `/logs/current` | 503 | Datos no cargados aún → reintentar con backoff |
| `/alert` | **201** | Alerta recibida y registrada ✅ |
| `/alert` | 422 | Mensaje muy largo (>300 chars) o campo ausente |

---

## Preguntas frecuentes del equipo

**¿Puedo pedir datos históricos?**  
No. La API siempre devuelve la ventana UTC activa. Sin parámetros de fecha. Si no ingestas una ventana, esos datos desaparecen.

**¿Cómo sé cuántas páginas leer?**  
Llamar `/info` al inicio del ciclo. El campo `total_pages` dice cuántas iteraciones hacer.

**¿Qué hago si recibo 422 en `/logs/current`?**  
La ventana cambió durante la extracción (pasaste del minuto :29 al :30). Reiniciar el ciclo de ingesta desde página 1.

**¿El endpoint `/alert` necesita URL adicional?**  
No. Es `POST {API_BASE_URL}/alert`. Usa el mismo `BEARER_TOKEN`. No hay `WEBHOOK_URL` separada.

**¿Qué pasa si el mensaje de alerta supera 300 caracteres?**  
La API devuelve 422. Siempre truncar a 295 chars antes de enviar (dejando margen).

**¿Puedo enviar múltiples alertas?**  
Sí, cada detección genera un POST independiente. La tabla `DBADMIN.ALERTS` en HANA previene duplicados por `log_id`.

---

*Documento generado el 30 de Abril 2026 — Cloud Integration Engineer*  
*Basado en la documentación oficial de la API SAP SOC Log Ingestion API v1.0.0*
