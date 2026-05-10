# Reporte Técnico de Arquitectura — Outline Detallado
## SAP AI Security Anomaly Detection Hackathon
## TEC de Monterrey × SAP · Mayo 2026

> **Propósito de este documento:** Outline de trabajo para el reporte técnico de arquitectura requerido como entregable de la Primera Etapa (20 pts). Cada sección incluye los puntos clave que debe cubrir y las preguntas que debe responder.

---

## 1. Resumen Ejecutivo

- Una oración del problema: qué amenaza representa la falta de monitoreo en sistemas SAP
- Una oración de la solución: qué construimos y cómo funciona
- Tabla de métricas clave del sistema en producción:
  - MTTD (Mean Time to Detect)
  - Latencia de alerta (detección → confirmación SAP)
  - Registros acumulados en HANA
  - Ventanas procesadas
  - Uptime del pipeline
- Estado actual: qué está operativo y qué está en progreso

---

## 2. Contexto y Problema de Negocio

### 2.1 El ecosistema SAP y su criticidad
- Qué es SAP y por qué lo usan las empresas enterprise
- Qué tipos de datos y operaciones corren sobre sistemas SAP (finanzas, RRHH, cadenas de suministro)
- Impacto potencial de un breach en un sistema SAP

### 2.2 El problema de seguridad
- Ausencia de visibilidad en tiempo real sobre la actividad de los sistemas
- Ataques raros, nuevos y sin etiquetas previas — no hay "esto es un ataque" histórico
- El estándar de la industria: detección en horas o días
- Por qué un SOC automatizado y no intervención manual

### 2.3 El reto del hackathon
- Qué pide SAP: pipeline OBSERVE → ANALYZE → DETECT → RESPOND
- Por qué no es solo un modelo de ML aislado — es un sistema de extremo a extremo
- A quién le importa: CISOs y equipos de seguridad de empresas que usan SAP BTP

---

## 3. Arquitectura General del Sistema

### 3.1 Visión de alto nivel
- Diagrama de arquitectura general *(insertar Slide 1 del PowerPoint)*
- Descripción del flujo completo: de dónde vienen los datos hasta dónde llega la alerta
- Los cuatro pasos: OBSERVE → ANALYZE → DETECT → RESPOND

### 3.2 Componentes del ecosistema SAP
- **SAP BTP (Business Technology Platform):** qué es y por qué es el entorno elegido
- **Cloud Foundry:** por qué CF y no un servidor propio — runtime gestionado, despliegue con `cf push`, reinicio automático ante fallos
- **SAP HANA Cloud:** por qué HANA y no PostgreSQL o MongoDB — integración nativa con SAP, soporte para SAC Live Connection, rendimiento en queries analíticas
- **SAP Analytics Cloud:** por qué SAC y no Streamlit — herramienta ejecutiva de SAP, Live Connection sin exportar datos, criterio de evaluación #2

### 3.3 Decisiones de diseño arquitectónico
- Por qué dos tablas separadas (Sistema y LLM) en lugar de una sola
- Por qué polling cada 2 minutos en lugar de las ventanas de 30 minutos de la API
- Por qué backup en CSV además de HANA
- Por qué detección en dos ciclos (rápido y lento) en lugar de uno solo

---

## 4. Stack Tecnológico y Librerías

### 4.1 Runtime y entorno
| Tecnología | Versión | Propósito |
|---|---|---|
| Python | 3.x | Lenguaje principal del pipeline |
| Cloud Foundry | — | Runtime de despliegue 24/7 |
| SAP BTP Trial | — | Plataforma cloud de infraestructura |

### 4.2 Ingesta y procesamiento de datos
| Librería | Versión | Propósito |
|---|---|---|
| `requests` | — | Llamadas HTTP a la API SAP |
| `pandas` | — | Manipulación de DataFrames, ETL |
| `numpy` | — | Operaciones numéricas, cálculo de MAD |
| `python-dotenv` | — | Gestión de variables de entorno |

### 4.3 Base de datos
| Librería | Versión | Propósito |
|---|---|---|
| `hdbcli` | — | Driver oficial SAP HANA — conexión, queries, inserciones |

### 4.4 Machine Learning
| Librería | Versión | Componente usado |
|---|---|---|
| `scikit-learn` | ≥1.8.0 | `IsolationForest`, `LocalOutlierFactor`, `OrdinalEncoder`, `RobustScaler` |

### 4.5 Despliegue y DevOps
| Herramienta | Propósito |
|---|---|
| Cloud Foundry CLI (`cf`) | Despliegue y monitoreo de la app |
| `manifest.yml` | Configuración de la app en CF |
| Git / GitHub | Control de versiones, rama `main` |

### 4.6 Visualización
| Herramienta | Propósito |
|---|---|
| SAP Analytics Cloud (SAC) | Dashboards ejecutivos en tiempo real |
| HDI Container (Prod_Vizz) | Synonyms y Calculation Views sobre HANA |
| SAP Business Application Studio (BAS) | IDE para deploy de CVs — no corre en tiempo real |

---

## 5. Pipeline de Datos

### 5.1 La fuente de datos: API SAP
- Los tres endpoints disponibles y su propósito
- El comportamiento de ventanas de 30 minutos — datos irrecuperables si se pierden
- Paginación: 500 registros por página, ~12 páginas por ventana
- Los dos tipos de logs: Sistema y LLM — columna discriminante `sap_function_log_type`
- Nulos por diseño — no son errores de calidad

### 5.2 Ingesta y deduplicación
- El loop de automatización: cálculo exacto de segundos hasta próximo :00 o :30 UTC
- Primera ejecución inmediata al arrancar el pipeline
- Deduplicación en dos capas: set Python en memoria + SELECT de IDs en HANA
- Por qué dos capas: la primera es O(1) y evita queries innecesarias a HANA

### 5.3 ETL y limpieza
- Eliminación de columnas internas de Elasticsearch (`_score`, `_ignored`, `_index`)
- Normalización de timestamps a `datetime64 UTC`
- Normalización de columnas numéricas a `float64`
- Separación de logs Sistema vs LLM

### 5.4 Persistencia
- Inserción en HANA con `executemany()` — O(1) en llamadas al servidor
- Backup en CSV local como fallback — el pipeline continúa si HANA no está disponible
- Gestión de nulos: conversión de `float('nan')` a `None` para NULL en HANA

### 5.5 Schema de las tablas HANA
- Tabla `RAW_LOGS_SISTEMA`: columnas clave, tipos, volumen actual
- Tabla `RAW_LOGS_LLM`: columnas clave, tipos, volumen actual
- Tabla `ALERTS`: columnas, campo `alerted` (0=pendiente, 1=confirmada por SAP)

---

## 6. Detección de Amenazas — Quick Filter

### 6.1 Rol del Quick Filter en el sistema
- Por qué reglas determinísticas antes que ML: latencia, interpretabilidad, cobertura de casos conocidos
- Opera sobre `df_nuevos` antes del ETL — no necesita normalización
- Agrupación por batch: una alerta por tipo y no una por registro — por qué es crítico

### 6.2 Las 6 reglas implementadas
Para cada regla: qué detecta, en qué tipo de log, la lógica exacta, y la severidad asignada.

- **Regla 1 — Security Event:** `log_type == 'SECURITY'` · severidad HIGH/MEDIUM por conteo de IPs
- **Regla 2 — Brute Force:** ≥5 errores HTTP 401/403 desde la misma IP en un ciclo · HIGH
- **Regla 3 — Path Scan:** ≥3 peticiones 404 a rutas sospechosas desde la misma IP · MEDIUM/HIGH
- **Regla 4 — High Cost LLM Error:** `LLM_ERROR` con `llm_cost_usd > 1.0` · HIGH/MEDIUM
- **Regla 5 — LLM Timeout:** cualquier `LLM_TIMEOUT` · HIGH/MEDIUM por conteo
- **Regla 6 — Slow LLM Response:** `llm_response_time_ms > 10,000` · MEDIUM/LOW

### 6.3 Clasificación según taxonomía de seguridad
- Mapeo de reglas a familias Tenable/Nessus: Brute Force Attacks, CGI Abuses, Web Servers, Artificial Intelligence

### 6.4 Resultados en producción
- Tipos de amenazas más frecuentes detectadas
- Distribución de severidades

---

## 7. Detección de Anomalías — Modelo de Machine Learning

### 7.1 Justificación del enfoque no supervisado
- Por qué no supervisado: ausencia de etiquetas, naturaleza desconocida de los ataques
- Alternativas consideradas y por qué se descartaron
- El principio: aprender lo normal y detectar desviaciones

### 7.2 Los tres modelos

#### Isolation Forest — Logs de Sistema
- Propósito: detectar eventos individuales anómalos en actividad web
- Features utilizadas (9): `status_family`, `is_4xx`, `is_5xx`, `is_401_or_403`, `is_429`, `hour_utc`, `LOG_TYPE`, `APPLICATION`, `REGION_NAME`
- Hiperparámetros: `n_estimators=200`, `max_samples=256`, `contamination='auto'`, `random_state=42`
- Complejidad: O(t × n × log n)

#### Isolation Forest — Logs de LLM
- Propósito: detectar peticiones LLM anómalas en costo, tiempo o tokens
- Features utilizadas: `log1p(LLM_COST_USD)`, `log1p(LLM_RESPONSE_TIME)`, `log1p(LLM_TOTAL_TOKENS)`, `LLM_STATUS`, `LLM_PROVIDER`, `LLM_MODEL_ID`
- Por qué `log1p`: distribución heavy-tail de las métricas LLM
- Hiperparámetros: mismos que IF Sistema

#### Local Outlier Factor — Comportamiento por IP
- Propósito: detectar IPs con comportamiento agregado anómalo (no eventos individuales)
- Por qué LOF y no IF para este caso: LOF compara densidades locales, captura comportamiento relativo entre IPs
- Features: métricas agregadas por IP — conteo de peticiones, diversidad de rutas, tasa de errores, distribución de códigos HTTP
- Hiperparámetros: `n_neighbors=20`, `contamination='auto'`
- `RobustScaler` previo al LOF: por qué — LOF calcula distancias euclidianas, features en rangos muy distintos

### 7.3 Feature Engineering
- `OrdinalEncoder` para variables categóricas
- Flags booleanos para códigos HTTP críticos
- `log1p()` para métricas con distribución heavy-tail
- `RobustScaler` para LOF

### 7.4 Thresholding con MAD
- Por qué MAD y no desviación estándar: robustez ante outliers
- Fórmula: `threshold = median - 3.5 × (MAD / 0.6745)`
- Modo histórico (≥20 ventanas): usa percentil acumulado
- Cap de 5 alertas por ciclo ML ordenadas por severidad

### 7.5 Resultados en producción
- Primera ejecución (4 mayo 2026): volumen de datos, anomalías detectadas, tiempo de ejecución
- Ejemplos de anomalías detectadas

---

## 8. Sistema de Alerting

### 8.1 El endpoint de alertas
- `POST /alert` en la misma API SAP — mismo `BEARER_TOKEN`
- Formato del mensaje: `WHAT: ... WHEN: ... WHY: ...` · máximo 300 caracteres
- Respuesta exitosa: HTTP 201 (no 200)

### 8.2 Garantías del sistema
- Anti-duplicados: verificación por `log_id` antes de cada envío
- Retry con backoff exponencial: 1s → 2s, máximo 3 intentos ante 5xx o timeout
- `enviar_alerta()` nunca lanza excepciones — errores quedan en `AlertResult.error`
- Registro en `DBADMIN.ALERTS`: `alerted=0` al detectar, `alerted=1` al confirmar HTTP 201

### 8.3 Métricas de alerting
- Latencia de confirmación: 188–310 ms (detección → HTTP 201 de SAP)
- Tasa de alertas confirmadas vs fallidas
- Source de cada alerta: `quick_filter` o `model_ml`

---

## 9. Visualización — SAP Analytics Cloud

### 9.1 Arquitectura de la conexión SAC–HANA
- HDI Container `Prod_Vizz`: qué es y por qué se necesita para la Live Connection
- Synonyms: puente entre el schema DBADMIN y el HDI Container
- Calculation Views: tipo CUBE, measures y attributes expuestos a SAC

### 9.2 Estado actual de los Calculation Views
- `CV_LOGS_LLM` ✅ — operativo: measures (tokens, costo, tiempo de respuesta), attributes (proveedor, modelo, región, status)
- `CV_LOGS_SISTEMA` ⏳ — en progreso
- `CV_ALERTS` ⏳ — en progreso

### 9.3 Usuarios y acceso a SAC
- `SAC_USER`: permisos SELECT + roles HDI `access_role` y `external_privileges_role`
- Separación de usuarios: SAC_USER ≠ PIPELINE_USER ≠ DBADMIN

### 9.4 Dashboard del SOC
- Métricas y visualizaciones planeadas
- Screenshots del dashboard actual *(insertar evidencia)*

---

## 10. MLOps y Despliegue en Cloud Foundry

### 10.1 Configuración del despliegue
- `manifest.yml`: comando de inicio, `health-check-type: process`, memoria, instancias
- `requirements.txt`: dependencias del entorno de producción
- `runtime.txt`: versión de Python

### 10.2 Gestión de credenciales por entorno
- Local: `.env` → `os.getenv()` → DBADMIN
- Producción (CF): `cf set-env` → User-Provided variables → PIPELINE_USER
- La función `_load_hana_creds()`: prioridad 1) variables directas, 2) VCAP_SERVICES, 3) falla controlada
- VCAP_SERVICES: hana y xsuaa bound — no consumidos activamente por el código

### 10.3 Monitoreo y observabilidad
- Logging dual: consola (tiempo real) + `pipeline.log` (historial persistente)
- Cada mensaje incluye hora UTC y hora Monterrey (CDT = UTC-6)
- `cf logs sap-ai-soc-papoi --recent` para diagnóstico

### 10.4 Manejo de errores en el loop
- HTTP 401 → `sys.exit(1)` — fatal, no sirve reintentar
- HTTP 5xx / Timeout → esperar 60s + reintentar
- HANA no disponible → continuar solo con CSV
- `KeyboardInterrupt` → cierre limpio con reporte de ciclos completados

### 10.5 Diagrama del Pipeline Loop
- *(insertar Slide 3 del PowerPoint)*

---

## 11. Seguridad y Gestión de Credenciales

### 11.1 Separación de usuarios por principio de mínimo privilegio
- `DBADMIN`: acceso total — solo para administración y setup
- `PIPELINE_USER`: SELECT + INSERT + UPDATE sobre las 3 tablas — solo lo necesario para el pipeline
- `SAC_USER`: SELECT + roles HDI — solo lectura para dashboards
- `#OO / #DI`: usuarios técnicos del HDI Container — gestionados por SAP

### 11.2 Gestión de credenciales
- `.env` nunca en Git — verificación con `.gitignore`
- `cf set-env` en lugar de variables hardcodeadas en el código
- `db/.env` y `db/default-env.json` del HDI Container — nunca en Git
- `JOB_SECRET_TOKEN` — legacy, sin uso en producción

### 11.3 Diagrama de usuarios y credenciales
- *(insertar Slide 4 del PowerPoint)*

---

## 12. Funciones Clave del Pipeline

> Para cada función: módulo de origen, propósito, inputs principales, output, y decisión de diseño relevante.

### 12.1 Configuración — `app/config.py`

**`validate_config()`**
Verifica que `API_BASE_URL` y `BEARER_TOKEN` están presentes antes de cualquier llamada HTTP. Falla rápido con mensaje claro.

**`_load_hana_creds()`**
Lee credenciales HANA con tres niveles de fallback: 1) variables directas (`os.getenv`), 2) VCAP_SERVICES, 3) retorna vacío para falla controlada.

**`get_headers()`**
Construye el header de autenticación Bearer en cada llamada — no como variable estática.

---

### 12.2 Ingesta — `app/ingest.py`

**`fetch_current_window()`**
- **Input:** ninguno (lee de config)
- **Output:** `(DataFrame, dict_metadata)`
- Implementa el loop de paginación: GET /info → GET /logs/current?page=1..N → acumula registros → retorna DataFrame completo

**`ingest_and_persist()`**
- **Input:** ninguno
- **Output:** dict con métricas del ciclo (total, nuevos, insertados en HANA)
- Orquesta: `fetch_current_window()` → deduplicación → `save_data()` → `insert_logs()`

---

### 12.3 Persistencia en HANA — `app/hana_client.py`

**`get_connection()`**
- **Input:** credenciales de `config.py`
- **Output:** objeto de conexión `hdbcli` activo
- Parámetros clave: `encrypt=True`, `sslValidateCertificate=False` (trial), `port=443`

**`insert_logs(df)`**
- **Input:** DataFrame con logs del ciclo actual
- **Output:** dict `{"sistema": N, "llm": M}` con conteo de inserciones
- Separa Sistema y LLM → convierte NaN a None → `executemany()` → `conn.commit()`

---

### 12.4 Detección rápida — `app/quick_filter.py`

**`filtrar_amenazas(df_nuevos)`**
- **Input:** DataFrame con registros nuevos (crudos, antes del ETL)
- **Output:** lista de dicts con `alert_type`, `severity`, `details`, `log_id`, `event_time`
- Aplica las 6 reglas en secuencia, agrupando por batch cuando corresponde

---

### 12.5 Alerting — `app/alerting.py`

**`enviar_alerta(alert_type, severity, details, log_id, ...)`**
- **Input:** tipo, severidad, descripción, log_id del registro, y opcionales (conn HANA, window_start, source)
- **Output:** `AlertResult` (dataclass con `success: bool`, `status_code: int`, `error: str`)
- Nunca lanza excepciones · anti-duplicados por log_id · retry x3 backoff exponencial

---

### 12.6 Feature Engineering — `app/feature_eng.py`

**`build_features_sistema(df)`**
- **Input:** DataFrame de logs Sistema
- **Output:** DataFrame con features numéricas para IF Sistema
- Genera flags booleanos de códigos HTTP, extrae hora UTC, aplica OrdinalEncoder

**`build_features_llm(df)`**
- **Input:** DataFrame de logs LLM
- **Output:** DataFrame con features numéricas para IF LLM
- Aplica `log1p()` a costo, tiempo y tokens; OrdinalEncoder a categóricas

**`build_features_ip(df)`**
- **Input:** DataFrame de logs Sistema
- **Output:** DataFrame agregado por IP con métricas de comportamiento
- Agrupa por IP: conteo de peticiones, rutas únicas, tasa de errores, distribución de status

---

### 12.7 Modelo ML — `app/model.py`

**`run_isolation_forest(df_features, label)`**
- **Input:** DataFrame de features, string label para logging
- **Output:** array de anomaly scores
- Entrena IF con hiperparámetros fijos, retorna scores normalizados

**`run_lof(df_features)`**
- **Input:** DataFrame de features agregadas por IP
- **Output:** array de scores LOF
- Aplica RobustScaler previo, entrena LOF, retorna scores

**`_compute_threshold(scores)`**
- **Input:** array de scores históricos
- **Output:** float threshold
- Calcula `median - 3.5 × (MAD / 0.6745)` — robusto ante outliers

**`detectar_anomalias(conn)`**
- **Input:** conexión HANA activa
- **Output:** lista de dicts de amenazas detectadas (mismo contrato que `filtrar_amenazas`)
- Orquesta: `hana_reader` → `feature_eng` → 3 modelos → thresholding → cap de 5 alertas

---

## 13. Riesgos Técnicos y Limitaciones

### 13.1 Riesgos de infraestructura
- **HANA trial se pausa por inactividad:** verificar estado Running antes de cada sesión · fallback CSV mitiga pérdida de datos
- **Ventanas irrecuperables si el pipeline cae:** monitoreo activo con `cf logs` · los organizadores verifican continuidad
- **Plan hana-free con límites de storage:** ~32GB — suficiente para el hackathon, escala en producción real

### 13.2 Riesgos del modelo de detección
- **Falsos positivos en Quick Filter:** umbrales calibrados empíricamente con datos acumulados — pueden requerir ajuste con más contexto
- **Modelo ML sin etiquetas:** no hay ground truth para medir precisión exacta — se evalúa por coherencia de las anomalías detectadas
- **Cap de 5 alertas por ciclo ML:** puede perder anomalías en ciclos con alta actividad — tradeoff deliberado para no saturar el dashboard de SAP

### 13.3 Riesgos de seguridad
- **Credenciales en historial de Git:** verificado con `git log --all -- .env` antes de hacer el repo público
- **Token del equipo comprometido:** penalización de bloqueo al día siguiente — credenciales solo en `.env` local y `cf set-env`

---

## 14. Reporte Forense — Incidentes Reales Detectados

> Esta sección documenta anomalías reales detectadas por el sistema en producción, con datos extraídos de `DBADMIN.ALERTS`.

### 14.1 Metodología del análisis forense
- Fuente de datos: tabla `DBADMIN.ALERTS` con campo `alerted=1` (confirmados por SAP)
- Período analizado: desde el primer deploy (24 abril) hasta la fecha del reporte
- Clasificación usando taxonomía Tenable/Nessus

### 14.2 Incidente 1 — [Tipo de amenaza]
- Qué se detectó
- Cuándo ocurrió (timestamp exacto)
- Qué componente lo identificó (quick_filter / model_ml)
- Qué significa en un entorno de producción real SAP
- Qué acción tomó el sistema automáticamente
- Tiempo de respuesta (detección → confirmación SAP)

### 14.3 Incidente 2 — [Tipo de amenaza]
*(misma estructura)*

### 14.4 Incidente 3 — [Tipo de amenaza]
*(misma estructura)*

### 14.5 Resumen estadístico de alertas detectadas
- Total de alertas generadas (alerted=0 + alerted=1)
- Total de alertas confirmadas por SAP (alerted=1)
- Distribución por tipo de amenaza
- Distribución por severidad
- Distribución por fuente de detección (quick_filter vs model_ml)

---

## 15. Impacto de Negocio

### 15.1 El problema que resolvemos
- Para quién: equipos de seguridad (CISOs) de empresas enterprise que usan SAP BTP
- Qué problema concreto: falta de visibilidad en tiempo real sobre amenazas en sistemas críticos

### 15.2 Métricas de éxito del sistema
| Métrica | Objetivo | Resultado obtenido |
|---|---|---|
| MTTD | ≤ 2 minutos | ~1 segundo |
| Latencia de alerta | Lo más bajo posible | 188–310 ms |
| Cobertura temporal | 24/7 | 24/7 desde Abr 24 |
| Intervención humana requerida | Ninguna | 0 intervenciones |

### 15.3 Comparación con el estándar de la industria
- MTTD estándar: horas a días
- Nuestro MTTD: ~1 segundo — mejora de varios órdenes de magnitud
- Qué significa en términos de impacto: un ataque de brute force que antes se detectaría en horas, ahora genera una alerta en 1 segundo

### 15.4 Escalabilidad y viabilidad en producción
- Cómo escala el sistema más allá del hackathon
- Qué cambiaría para producción real: plan HANA de producción, múltiples instancias CF, etiquetas de ataques para supervisar el modelo
- Visión de autonomous enterprise: cómo el agente IA (tentativo) encaja en la dirección estratégica de SAP

---

## Apéndices

### Apéndice A — Schema completo de las tablas HANA
- Schema de `RAW_LOGS_SISTEMA`: todas las columnas con tipo de dato y descripción
- Schema de `RAW_LOGS_LLM`: todas las columnas con tipo de dato y descripción
- Schema de `ALERTS`: todas las columnas con tipo de dato y descripción

### Apéndice B — Fragmentos de código clave
- `pipeline_loop.py`: lógica de sincronización temporal
- `quick_filter.py`: implementación de la regla de brute force
- `model.py`: función `_compute_threshold()` con MAD
- `hana_client.py`: función `insert_logs()` con `executemany()`

### Apéndice C — Evidencia de implementación
- Screenshots de logs de Cloud Foundry mostrando ciclos completos
- Output de queries HANA con conteos de registros
- Screenshot del dashboard SAC con datos reales
- Logs de alertas confirmadas (HTTP 201)

### Apéndice D — Diagrama de usuarios y credenciales
- *(insertar Slide 4 del PowerPoint)*

### Apéndice E — Queries SQL de referencia
- Queries usadas para el reporte forense
- Queries de verificación de integridad de tablas

---

*Outline generado el 10 de Mayo 2026*
*SAP AI Security Anomaly Detection Hackathon · TEC de Monterrey × SAP*
*Actualizar conforme se complete cada sección*
