# Schema de Datos — SAP AI Security Anomaly Detection
**Última actualización:** 20 de Abril 2026  
**Generado por:** Cloud Integration Engineer  
**Basado en:** extracción real de `GET /logs/current` — ventana 2026-04-20

---

## Fuente de datos

| Campo | Valor |
|---|---|
| Base URL | `https://sap-api-b4.674318.xyz` |
| Endpoint principal | `GET /logs/current` |
| Endpoint de metadata | `GET /info` |
| Endpoint de liveness | `GET /health` |
| Autenticación | `Authorization: Bearer <team-token>` |
| Ventana de datos | 30 minutos UTC rolling — sin datos históricos |
| Paginación | Servidor controla batch size (500 registros/página) |

---

## Dimensiones reales del dataset

| Métrica | Valor observado |
|---|---|
| Registros por ventana | ~5,729 (variable según actividad) |
| Páginas por ventana | ~12 |
| Batch size | 500 (fijo por el servidor) |
| Columnas totales | 43 |
| Ventana de ejemplo | 2026-04-20T06:00:00 → 2026-04-20T06:30:00 UTC |

---

## Tipos de log — columna discriminante

La columna `sap_function_log_type` define el tipo de cada registro.
**Esta es la columna más importante del dataset** — determina qué otros campos tienen valor.

| Categoría | Valores de `sap_function_log_type` | Descripción |
|---|---|---|
| **Sistema** | `INFO`, `WARNING`, `ERROR`, `DEBUG`, `AUDIT`, `PERF`, `SECURITY` | Logs generados por servicios SAP internos |
| **LLM** | `LLM_REQUEST`, `LLM_ERROR`, `LLM_TIMEOUT` | Logs de interacciones con modelos de lenguaje |

### ⚠️ Patrón de nulos — crítico para todo el equipo

Los nulos no son errores de calidad — son parte del diseño de la API:

- Logs de **Sistema** → columnas `llm_*` vienen **siempre vacías**
- Logs de **LLM** → columnas `service_id`, `http_status_code`, `client_ip` vienen **siempre vacías**

**Nadie debe intentar rellenar estos nulos.** El ETL los maneja separando ambos tipos.

---

## Catálogo completo de columnas

### Columnas de identificación y metadata interna

| Columna | Tipo | Presente en | Ejemplo | Notas |
|---|---|---|---|---|
| `_id` | string | Ambos | `82825b64-58e4-4311-a6c1-072f85f926c1` | ID único del registro — usar como primary key en HANA |
| `_index` | string | Ambos | `llm-logs-2026.04` | Índice de Elasticsearch de origen |
| `_score` | float | Ambos | `0.9098` | Score de relevancia interno — no usar para ML |
| `_ignored` | string | Ambos | _(vacío)_ | Campo interno de Elasticsearch |

### Columnas temporales

| Columna | Tipo | Presente en | Ejemplo | Notas |
|---|---|---|---|---|
| `@timestamp` | datetime (UTC) | Ambos | `2026-04-20T17:00:00.000Z` | Timestamp oficial del evento — usar para particionamiento en HANA |
| `@event_time_requested` | datetime (UTC) | Ambos | `2026-04-20T16:59:59.332Z` | Momento en que el evento fue solicitado |
| `@version` | string | Ambos | `1` | Versión del schema de log |

### Columnas de clasificación del log

| Columna | Tipo | Presente en | Ejemplo | Notas |
|---|---|---|---|---|
| `sap_function_log_type` | string | Ambos | `LLM_ERROR`, `INFO`, `SECURITY` | **Columna discriminante principal** |
| `sap_source_type` | string | Ambos | `OData` | Tipo de fuente SAP que generó el log |
| `sap_function_application` | string | Ambos | `sap-analytics-cloud` | Aplicación SAP de origen |
| `sap_app_env` | string | Ambos | `development` | Entorno: development / production |
| `sap_function_message` | string | Ambos | `LLM ERROR: This capability is not available...` | Mensaje descriptivo del evento |
| `event_code_version` | string | Ambos | `1.4.0` | Versión del código de evento |
| `event_hash` | string | Ambos | `3abd18f8...` | Hash del evento para deduplicación |

### Columnas de red y HTTP (solo logs de Sistema)

| Columna | Tipo | Presente en | Ejemplo | Notas |
|---|---|---|---|---|
| `service_id` | string | **Sistema** | _(rellenar con datos reales)_ | ID del servicio SAP — vacío en LLM |
| `http_status_code` | int/string | **Sistema** | `200`, `404`, `500` | Código HTTP — vacío en LLM |
| `client_ip` | string | **Sistema** | `192.168.x.x` | IP del cliente — vacío en LLM |
| `headers_http_host` | string | Ambos | `llm.ie.sap-ai.internal` | Host del request HTTP |
| `headers_http_request_method` | string | Ambos | `DELETE`, `GET`, `POST` | Método HTTP del request |
| `headers_content_type` | string | Ambos | `text/plain` | Content-Type del request |
| `heathers_request_path` | string | Ambos | `/api/llm/sap-analytics-cloud/completion` | Path del endpoint llamado — **nota: typo en la API, viene con 'heathers'** |

### Columnas de región geográfica

| Columna | Tipo | Presente en | Ejemplo | Notas |
|---|---|---|---|---|
| `region_id` | string | Ambos | `EU-016` | ID interno de región SAP |
| `region_name` | string | Ambos | `Ireland` | Nombre del país/región |
| `region_code` | string | Ambos | `IE` | Código ISO del país |
| `macro_region` | string | Ambos | `Europe` | Región geográfica macro |

### Columnas LLM — modelo y proveedor (solo logs LLM)

| Columna | Tipo | Presente en | Ejemplo | Notas |
|---|---|---|---|---|
| `llm_model_id` | string | **LLM** | `o3-mini` | Modelo de lenguaje usado — vacío en Sistema |
| `llm_provider` | string | **LLM** | `OpenAI` | Proveedor del modelo — vacío en Sistema |
| `llm_status` | string | **LLM** | `error`, `success` | Estado de la llamada al LLM |
| `llm_error_message` | string | **LLM** | `This capability is not available on trial plans.` | Mensaje de error si aplica |
| `llm_finish_reason` | string | **LLM** | `content_filter`, `stop` | Razón de finalización de la generación |

### Columnas LLM — prompt (solo logs LLM)

| Columna | Tipo | Presente en | Ejemplo | Notas |
|---|---|---|---|---|
| `llm_prompt_id` | string | **LLM** | `budget-variance` | ID del template de prompt usado |
| `llm_prompt_category` | string | **LLM** | `Finance` | Categoría de negocio del prompt |
| `llm_prompt` | string | **LLM** | `Analyse the budget variance for...` | Texto completo del prompt — puede ser largo |
| `llm_stream` | boolean | **LLM** | `TRUE`, `FALSE` | Si la respuesta fue en modo streaming |

### Columnas LLM — métricas de uso y costo (solo logs LLM)

| Columna | Tipo | Presente en | Ejemplo | Notas |
|---|---|---|---|---|
| `llm_prompt_tokens` | int | **LLM** | `768` | Tokens del prompt |
| `llm_completion_tokens` | int | **LLM** | `967` | Tokens de la respuesta generada |
| `llm_total_tokens` | int | **LLM** | `1735` | Total de tokens consumidos |
| `llm_cost_usd` | float | **LLM** | `0.001909` | Costo en USD de la llamada |
| `llm_response_time_ms` | float | **LLM** | `2369.9` | Tiempo de respuesta en milisegundos |
| `llm_response_size_bytes` | int | **LLM** | _(rellenar)_ | Tamaño de la respuesta en bytes |
| `llm_temperature` | float | **LLM** | `0.39` | Temperatura del modelo (0.0 - 1.0) |
| `llm_top_p` | float | **LLM** | `0.53` | Top-p sampling del modelo |

### Columnas SAP heredadas (comportamiento a confirmar)

| Columna | Tipo | Presente en | Ejemplo | Notas |
|---|---|---|---|---|
| `sap_llm_response_size` | float | Ambos | `2369.9` | Similar a `llm_response_size_bytes` — posible duplicado |
| `sap_llm_response_time` | float | Ambos | _(rellenar)_ | Similar a `llm_response_time_ms` — posible duplicado |

---

## Columnas relevantes por rol

### Para el AI & Data Science Specialist
Las columnas más útiles para feature engineering de anomalías:

**En logs de Sistema:**
`http_status_code`, `client_ip`, `service_id`, `sap_function_log_type`, `headers_http_request_method`, `heathers_request_path`, `@timestamp`

**En logs de LLM:**
`llm_status`, `llm_cost_usd`, `llm_total_tokens`, `llm_response_time_ms`, `llm_model_id`, `llm_error_message`, `llm_prompt_category`, `@timestamp`

### Para el Data Architect & Backend Developer
Columnas clave para el schema de HANA:
- Primary key: `_id`
- Particionamiento temporal: `@timestamp`
- Columna de routing entre tablas: `sap_function_log_type`

### Para el Security Analyst & Visualization Lead
Columnas para dashboards de seguridad:
`sap_function_log_type`, `http_status_code`, `client_ip`, `sap_function_application`, `region_name`, `llm_status`, `llm_cost_usd`, `@timestamp`

---

## Anomalías conocidas del schema

| Anomalía | Descripción |
|---|---|
| Typo en `heathers_request_path` | La API devuelve `heathers_` en lugar de `headers_` — es un bug de la fuente, no corregir en ETL para no romper consistencia |
| Posibles duplicados | `sap_llm_response_size` / `llm_response_size_bytes` y `sap_llm_response_time` / `llm_response_time_ms` parecen contener la misma información — confirmar con datos reales |
| `_score` y `_ignored` | Campos internos de Elasticsearch que no aportan valor analítico — candidatos a eliminar en ETL |

---

*Documento vivo — actualizar cada vez que cambie el schema de la API.*  
*Cualquier cambio en este documento debe ser comunicado a todo el equipo antes de modificar el ETL.*
