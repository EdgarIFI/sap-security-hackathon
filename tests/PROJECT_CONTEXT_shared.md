# Contexto del Proyecto — SAP AI Security Anomaly Detection Hackathon
### Archivo de memoria compartida para el equipo
### TEC de Monterrey × SAP · Abril–Mayo 2026

> **Propósito de este archivo:** proveer contexto completo a cualquier integrante del equipo  
> que inicie una sesión de trabajo con Claude, para que no tenga que reconstruir  
> el historial desde cero. Leer completo antes de pedir ayuda técnica.

---

## 1. Qué es este hackathon

**Nombre oficial:** SAP AI Security Anomaly Detection Challenge  
**Organizado por:** SAP × TEC de Monterrey  
**Duración:** Abril 6 – Mayo 21, 2026

El reto consiste en construir un **Security Operations Center (SOC) en vivo** para sistemas SAP. El sistema debe ingestar logs de seguridad en tiempo real, detectar comportamientos anómalos con modelos de Machine Learning, y disparar alertas automáticas cuando se identifica una amenaza.

El flujo conceptual del sistema es:

```
OBSERVE → ANALYZE → DETECT → RESPOND
```

Lo que se construye no es solo un modelo de IA — es un pipeline de extremo a extremo que conecta datos, inteligencia y acción.

---

## 2. Fechas críticas

| Fecha | Hito | Estado |
|---|---|---|
| Abr 6 | Kick Off + Learning Materials | ✅ Completado |
| Abr 13 | API & Data Access | ✅ Completado |
| Abr 20 | Pipeline de ingesta funcionando | ✅ Completado |
| Abr 27 | Alerting Webhook disponible | ⏳ Pendiente |
| May 4 | Go Live en SAP BTP / Cloud Foundry | ⏳ Pendiente |
| May 12–14 | Primera Fase Eliminatoria | ⏳ Pendiente |
| May 15 | Anuncio de ganadores fase 1 | ⏳ Pendiente |
| May 21 | Final Phase (First to Go Down) | ⏳ Pendiente |

**Penalización importante:** si el equipo sufre un Data Breach o sus credenciales son comprometidas, queda bloqueado para el día siguiente. La gestión segura de credenciales es crítica.

---

## 3. Criterios de evaluación

| Criterio | Peso | Qué mide |
|---|---|---|
| Operational Efficiency & Real-Time Response | **40%** | MTTD, Alert Latency, nivel de automatización end-to-end |
| SAP Ecosystem Integration & Tooling | **25%** | Uso real de BTP, Cloud Foundry, HANA, SAC |
| Architecture & MLOps Maturity | **20%** | Escalabilidad, robustez, separación de componentes |
| Business Impact & Strategic Analysis | **15%** | Calidad del reporte forense, storytelling ejecutivo |

El criterio #1 (40%) depende directamente de que el pipeline corra automatizado. Un sistema que requiere intervención manual no puntúa bien aquí.

---

## 4. Los cinco roles del equipo

| Rol | Responsabilidad principal | Herramientas clave |
|---|---|---|
| **Cloud Integration Engineer** | Orquestación en SAP BTP, ETL pipeline, APIs, webhook alerting, automatización | Python, requests, SAP BTP, Cloud Foundry |
| **AI & Data Science Specialist** | Modelos de detección de anomalías, feature engineering, noise reduction | scikit-learn, Keras, pandas, NumPy |
| **Data Architect & Backend Developer** | Persistencia en SAP HANA, schema design, query optimization | SAP HANA, SQL, Python |
| **Security Analyst & Visualization Lead** | Dashboards en Streamlit y SAP Analytics Cloud, reporte forense final | Streamlit, SAP Analytics Cloud |
| **Technical Project Manager & Scrum Master** | Coordinación Agile, backlog, enlace con evaluadores SAP | Metodología Agile |

---

## 5. Stack tecnológico oficial

| Capa | Tecnología |
|---|---|
| Ingesta / ETL | Python · `requests` · `pandas` · `numpy` |
| ML / Detección | `scikit-learn` · `Keras` |
| Almacenamiento | SAP HANA (formal) · CSV/Parquet (desarrollo) |
| Despliegue | SAP BTP · Cloud Foundry |
| Visualización rápida | Streamlit |
| Visualización ejecutiva | SAP Analytics Cloud (SAC) |
| Alerting | Webhook REST (POST) |

---

## 6. La API del hackathon — datos confirmados

### Endpoints

| Endpoint | Auth | Propósito |
|---|---|---|
| `GET /health` | No requerida | Liveness probe — verifica que el servidor vive |
| `GET /info` | Bearer token | Metadata de la ventana actual (total_pages, total_records, batch_size) |
| `GET /logs/current?page=N` | Bearer token | Logs de la ventana actual, paginados (500 registros/página) |

### Autenticación

```
Authorization: Bearer <team-token>
```

El token es por equipo. Va en el archivo `.env` como `BEARER_TOKEN`. Nunca en el código.

### Comportamiento temporal

La API sirve siempre la **ventana UTC de 30 minutos actual**. No hay datos históricos. El pipeline debe hacer polling cada 30 minutos para no perder ventanas.

| Minuto UTC | Ventana servida |
|---|---|
| 00 – 29 | HH:00:00 → HH:30:00 |
| 30 – 59 | HH:30:00 → HH+1:00:00 |

### Dimensiones reales observadas

```
Registros por ventana:  ~5,729 (variable)
Páginas por ventana:    ~12
Batch size:             500 (fijo por el servidor)
Columnas totales:       43
```

### Estructura de respuesta de `/logs/current`

```json
{
  "request_time_utc": "...",
  "window_start": "...",
  "window_end": "...",
  "total_records": 5729,
  "batch_size": 500,
  "current_page": 1,
  "total_pages": 12,
  "records_in_page": 500,
  "data": [ ... ]
}
```

---

## 7. Schema de los logs — columnas reales

Los logs tienen **dos categorías** con columnas distintas. Los nulos son por diseño — no corregirlos.

### Columna discriminante: `sap_function_log_type`

| Categoría | Valores | Columnas vacías |
|---|---|---|
| **Sistema** | `INFO`, `WARNING`, `ERROR`, `DEBUG`, `AUDIT`, `PERF`, `SECURITY` | Todas las `llm_*` |
| **LLM** | `LLM_REQUEST`, `LLM_ERROR`, `LLM_TIMEOUT` | `service_id`, `http_status_code`, `client_ip` |

### Columnas completas (43 en total)

**Identificación:** `_id`, `_index`, `_score`, `_ignored`

**Temporales:** `@timestamp`, `@event_time_requested`, `@version`

**Clasificación:** `sap_function_log_type`, `sap_source_type`, `sap_function_application`, `sap_app_env`, `sap_function_message`, `event_code_version`, `event_hash`

**Red/HTTP (Sistema):** `service_id`, `http_status_code`, `client_ip`, `headers_http_host`, `headers_http_request_method`, `headers_content_type`, `heathers_request_path` *(typo en la API — viene con 'heathers', no corregir)*

**Región:** `region_id`, `region_name`, `region_code`, `macro_region`

**LLM — modelo:** `llm_model_id`, `llm_provider`, `llm_status`, `llm_error_message`, `llm_finish_reason`

**LLM — prompt:** `llm_prompt_id`, `llm_prompt_category`, `llm_prompt`, `llm_stream`

**LLM — métricas:** `llm_prompt_tokens`, `llm_completion_tokens`, `llm_total_tokens`, `llm_cost_usd`, `llm_response_time_ms`, `llm_response_size_bytes`, `llm_temperature`, `llm_top_p`

**SAP heredadas:** `sap_llm_response_size`, `sap_llm_response_time` *(posibles duplicados de métricas LLM — pendiente confirmar)*

---

## 8. Repositorio y estructura del proyecto

**Repositorio:** GitHub (privado) — pedir acceso al Cloud Integration Engineer  
**Rama principal:** `main`  
**Rama de trabajo:** `develop`

### Estructura de carpetas

```
/
├── app/
│   ├── config.py       ← variables de entorno y autenticación ✅
│   ├── ingest.py       ← extracción paginada de la API ✅
│   ├── explore.py      ← análisis exploratorio del dataset ✅
│   ├── etl.py          ← limpieza y estructuración ✅
│   ├── model.py        ← detección de anomalías ⏳
│   ├── alerting.py     ← webhook dispatch ⏳
│   └── main.py         ← dashboard Streamlit ⏳
├── data/
│   ├── raw/
│   │   ├── logs_[timestamp].csv    ← datos crudos por ventana
│   │   └── sample_10.csv           ← muestra para el equipo ✅
│   └── processed/
│       ├── clean_full.csv          ← dataset completo limpio ✅
│       ├── clean_sistema.csv       ← solo logs de Sistema ✅
│       └── clean_llm.csv           ← solo logs de LLM ✅
├── data/SCHEMA.md      ← documentación completa de columnas ✅
├── docs/
│   └── TEAM_BRIEFING_cloud_integration_v2.md ✅
├── notebooks/          ← exploración e ideas (AI Specialist)
├── tests/
├── .env.example        ← plantilla de variables ✅
├── requirements.txt    ✅
└── README.md
```

### Variables de entorno requeridas (`.env` local — nunca en Git)

```
API_BASE_URL=      ← URL base de la API
BEARER_TOKEN=      ← token de autenticación del equipo
WEBHOOK_URL=       ← pendiente hasta Abr 27
HANA_HOST=         ← pendiente — Data Architect lo provee
HANA_PORT=
HANA_USER=
HANA_PASSWORD=
```

### Flujo diario de trabajo con Git

```bash
git pull                              # siempre primero
# ... trabajar ...
git add .
git commit -m "descripción"
git push origin develop
```

---

## 9. Estado del pipeline al día de hoy (20 Abr 2026)

### Lo que funciona

`ingest.py` — extrae automáticamente todos los logs de la ventana actual con paginación completa. Produce `data/raw/logs_[timestamp].csv` y `data/raw/sample_10.csv`.

`explore.py` — analiza el CSV más reciente y produce reporte completo de columnas, tipos, distribuciones y rango temporal. Produce subsets separados en `data/processed/`.

`etl.py` — limpia los datos crudos: elimina columnas internas de Elasticsearch (`_score`, `_ignored`, `_index`), normaliza timestamps a `datetime64 UTC`, normaliza columnas numéricas a `float64`, normaliza booleanos, separa por tipo de log, y produce reporte de calidad con clasificación de nulos esperados vs inesperados. Produce `clean_full.csv`, `clean_sistema.csv`, `clean_llm.csv`.

### Lo que viene inmediatamente

**Tarea siguiente (Tarea 3):** automatizar el loop de 30 minutos — hacer que `ingest.py` + `etl.py` corran cada vez que cambia la ventana, sin intervención manual. Esto es lo que convierte el sistema en detección continua real y es el corazón del criterio de evaluación #1 (40%).

**Pendiente de otros roles:**
- AI Specialist: explorar `clean_sistema.csv` y `clean_llm.csv`, definir features, construir prototipo de detección
- Data Architect: diseñar schema de HANA y configurar la instancia
- Viz Lead: prototipo de dashboard en Streamlit con `sample_10.csv`

---

## 10. Conceptos clave que todo el equipo debe conocer

**Ventana de 30 minutos:** la API siempre devuelve datos del intervalo UTC actual. No hay histórico. Cada ventana es única e irrecuperable si se pierde.

**Paginación:** los registros vienen en páginas de 500. Para traer todo hay que llamar `/info` primero (descubrir total_pages) y luego iterar `/logs/current?page=1..N`.

**Patrón de nulos por diseño:** los nulos en columnas LLM de logs de Sistema y viceversa son intencionales. No rellenar.

**Bearer token:** credencial de autenticación del equipo. Va solo en `.env`. Si se sube a GitHub, el equipo queda penalizado.

**MTTD (Mean Time to Detect):** el tiempo que tarda el sistema desde que ocurre una anomalía hasta que dispara una alerta. Es la métrica principal del criterio #1. Objetivo: menos de 1 minuto.

**Idempotencia:** el ETL puede correrse N veces sobre los mismos datos con el mismo resultado. Principio de diseño que hace el sistema predecible.

**Drift temporal:** el desplazamiento acumulativo entre cuándo debería ejecutarse el pipeline y cuándo se ejecuta. El loop de automatización lo resuelve calculando el tiempo exacto hasta la próxima ventana en lugar de dormir un tiempo fijo.

---

## 11. Convenciones de código del proyecto

- Lenguaje: Python 3.x
- Todas las credenciales van en `.env`, nunca en el código
- Cada script tiene un bloque `if __name__ == "__main__"` para ejecutarse independientemente
- Los errores se manejan con `try/except` explícito — no silenciar excepciones
- Los datos raw nunca se modifican — solo se leen
- Commits en inglés con prefijo: `feat:`, `fix:`, `chore:`, `docs:`

---

*Documento creado el 20 de Abril 2026 por el Cloud Integration Engineer.*  
*Actualizar conforme avance el proyecto.*
