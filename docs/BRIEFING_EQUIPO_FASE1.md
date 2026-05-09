# Briefing del Equipo — Fase 1 de Entrega
## SAP AI Security Anomaly Detection Hackathon · TEC de Monterrey × SAP
**Fecha:** 8 Mayo 2026  
**Deadline de entrega:** 12 Mayo 2026, 11:59 PM  
**Estado del sistema:** ✅ Pipeline completo corriendo 24/7 en la nube de SAP  
**Propósito de este documento:** Que cualquier integrante del equipo entienda qué es el proyecto, qué está construido, y qué le toca hacer — sin necesidad de haber participado antes.

---

## Índice

1. [¿Qué es este proyecto?](#1-qué-es-este-proyecto)
2. [¿De dónde vienen los datos y qué significan?](#2-de-dónde-vienen-los-datos-y-qué-significan)
3. [¿Qué amenazas detecta el sistema?](#3-qué-amenazas-detecta-el-sistema)
4. [¿Cómo funciona el sistema completo?](#4-cómo-funciona-el-sistema-completo)
5. [El ecosistema SAP — qué es cada pieza](#5-el-ecosistema-sap--qué-es-cada-pieza)
6. [Arquitectura interna — los módulos del código](#6-arquitectura-interna--los-módulos-del-código)
7. [Esquemas de la Base de Datos](#7-esquemas-de-la-base-de-datos)
8. [El modelo de Machine Learning — resumen ejecutivo](#8-el-modelo-de-machine-learning--resumen-ejecutivo)
9. [Línea del tiempo — cómo se construyó todo](#9-línea-del-tiempo--cómo-se-construyó-todo)
10. [Qué pide la rúbrica y cómo nos evalúan](#10-qué-pide-la-rúbrica-y-cómo-nos-evalúan)
11. [Plan de acción — tareas, responsabilidades y prioridades](#11-plan-de-acción--tareas-responsabilidades-y-prioridades)
12. [Referencia rápida — queries, comandos y accesos](#12-referencia-rápida--queries-comandos-y-accesos)

---

## 1. ¿Qué es este proyecto?

### El reto en una oración

Construir un **centro de operaciones de seguridad (SOC) automatizado** que vigile sistemas SAP en tiempo real, detecte comportamiento sospechoso usando inteligencia artificial, y envíe alertas automáticas cuando encuentre una amenaza.

### ¿Qué es un SOC?

Un SOC es como un sistema de cámaras de seguridad inteligentes para software. En lugar de vigilar puertas y pasillos, vigila la actividad digital: quién entra al sistema, qué hace, si algo se ve fuera de lo normal. La diferencia con un sistema de cámaras tradicional es que nuestro SOC no necesita un humano mirando pantallas — detecta las amenazas solo y avisa automáticamente.

### Los 4 pasos del sistema

```
OBSERVE  →  ANALYZE  →  DETECT  →  RESPOND
Recoger     Procesar    Encontrar   Enviar
los datos   y limpiar   anomalías   alertas
```

Cada uno de estos pasos está automatizado. El sistema corre sin intervención humana, las 24 horas del día, los 7 días de la semana.

### ¿Por qué importa?

SAP es el software que usan miles de las empresas más grandes del mundo para manejar sus operaciones: finanzas, recursos humanos, cadenas de suministro. Un ataque exitoso a un sistema SAP puede paralizar una empresa entera. Nuestro sistema detecta esos ataques en menos de 2 segundos — cuando el estándar de la industria es de horas o días.

---

## 2. ¿De dónde vienen los datos y qué significan?

> **¿Quién necesita leer esto?** Todos. Entender qué representan los datos es fundamental para cualquier tarea — dashboard, reporte forense, presentación o video.

### El origen: aplicaciones SAP reales

Los datos que vigilamos provienen de dos fuentes dentro de la plataforma SAP:

**Fuente 1 — Aplicaciones web SAP (Logs de Sistema)**

Imaginen una aplicación empresarial a la que acceden empleados, proveedores y sistemas automatizados. Cada vez que alguien accede — o intenta acceder — se genera un registro. Es como el libro de visitas de un edificio: se anota quién llegó, a qué piso quería ir, si le dejaron pasar o no, y a qué hora fue.

Cada registro de sistema dice:
- **Quién:** la dirección IP (la "dirección" del dispositivo que se conectó)
- **Qué quiso hacer:** la ruta a la que intentó acceder (por ejemplo, `/api/users` o `/admin/settings`)
- **Qué método usó:** GET (consultar), POST (enviar datos), DELETE (borrar)
- **Qué le respondió el servidor:** un código numérico que indica el resultado

Los códigos de respuesta más importantes para seguridad:
| Código | Significado | ¿Es sospechoso? |
|---|---|---|
| 200, 201, 204 | Todo bien, la petición fue exitosa | No |
| 301, 302 | El recurso se movió a otra dirección | No |
| 400 | La petición estaba mal formada | Depende del contexto |
| **401** | **No autorizado — credenciales inválidas** | **Sí, si se repite muchas veces** |
| **403** | **Prohibido — no tienes permiso** | **Sí, si se repite** |
| **404** | **No encontrado — esa ruta no existe** | **Sí, si buscan rutas sensibles** |
| 429 | Demasiadas peticiones — bloqueado temporalmente | Sí |
| 500, 502, 503 | Error interno del servidor | Depende |

**Fuente 2 — Generative AI Hub de SAP (Logs de LLM)**

SAP tiene un servicio llamado **Generative AI Hub** dentro de su plataforma BTP. Este servicio permite que las aplicaciones empresariales usen modelos de lenguaje (inteligencia artificial que procesa texto — como GPT, Claude, Mistral, etc.) para tareas como analizar documentos, generar reportes, o responder preguntas de empleados.

Cada vez que una aplicación le pide algo a uno de estos modelos, se genera un registro que dice:
- **Qué modelo se usó:** por ejemplo, GPT-4, Claude, Mistral
- **Qué proveedor lo sirve:** OpenAI, Anthropic, etc.
- **Cuánto costó:** en dólares (cada petición tiene un costo basado en los tokens procesados)
- **Cuánto tardó:** el tiempo de respuesta en milisegundos
- **Si funcionó o falló:** éxito, error, o timeout (se tardó demasiado y el sistema cortó la conexión)
- **Cuántos tokens usó:** los tokens son las "piezas" de texto que el modelo procesa — más tokens = más costo

¿Por qué vigilar esto? Porque un atacante podría:
- Hacer que el sistema llame modelos costosos masivamente, generando facturas enormes
- Intentar extraer información sensible a través de prompts maliciosos
- Provocar timeouts intencionales para degradar el servicio

### Los números

Cada 30 minutos la API genera una ventana de datos con aproximadamente **5,500 registros**: ~3,500 de sistema y ~2,000 de LLM. Si no los recolectamos a tiempo, esos datos se pierden para siempre — la API no guarda historial.

Nuestro pipeline los recolecta cada 2 minutos para no perder ninguno. Al día de hoy tenemos más de **1.6 millones de registros** acumulados en la base de datos.

> **Detalle técnico**
>
> La columna `sap_function_log_type` discrimina el tipo de log:
> - Sistema: `INFO`, `WARNING`, `ERROR`, `DEBUG`, `AUDIT`, `PERF`, `SECURITY`
> - LLM: `LLM_REQUEST`, `LLM_ERROR`, `LLM_TIMEOUT`
>
> Los logs de Sistema tienen las columnas `llm_*` vacías (NULL por diseño). Los de LLM tienen `service_id`, `http_status_code` y `client_ip` vacías. No son errores — la API los genera así.
>
> Nota: la columna de ruta viene como `heathers_request_path` (typo oficial de la API, no corregir en el código).

---

## 3. ¿Qué amenazas detecta el sistema?

> **¿Quién necesita leer esto?** Todos — es fundamental para el reporte forense, el dashboard, y para poder explicar el sistema en el video. Si estás haciendo el reporte de impacto de negocio, esta sección es especialmente importante.

### Las amenazas del mundo real que buscamos

Nuestro sistema detecta patrones que en la industria de ciberseguridad tienen nombres y clasificaciones establecidas. Para referencia, la taxonomía de Tenable/Nessus (una de las herramientas de escaneo de vulnerabilidades más usadas en la industria) clasifica las amenazas en familias. Las que aplican a nuestro contexto son:

### 1. Fuerza bruta (Brute Force Attacks)

**¿Qué es?** Alguien intenta adivinar la contraseña de un usuario probando miles de combinaciones automáticamente.

**¿Cómo se ve en nuestros datos?** Muchas peticiones con código 401 (no autorizado) o 403 (prohibido) desde la misma dirección IP en poco tiempo. Si una IP tiene 18 intentos fallidos en 90 segundos, es casi seguro un ataque de fuerza bruta.

**¿Qué tan grave es?** Alta. Si el atacante logra adivinar la contraseña, tiene acceso al sistema.

**Regla del sistema:** Si detectamos 5 o más errores 401/403 desde la misma IP en un ciclo de 2 minutos → alerta de severidad HIGH.

### 2. Escaneo de rutas (Path Scanning / CGI Abuses)

**¿Qué es?** Alguien recorre automáticamente rutas del servidor buscando archivos sensibles o paneles de administración que no deberían estar expuestos.

**¿Cómo se ve en nuestros datos?** Peticiones a rutas como `/phpmyadmin`, `/.env` (archivo de credenciales), `/cgi-bin`, `/wp-admin` (panel de WordPress), con códigos 404 (no encontrado). El atacante está probando a ciegas buscando algo que explotar.

**¿Qué tan grave es?** Media a alta. El escaneo en sí no causa daño, pero es la fase de reconocimiento antes de un ataque real.

**Regla del sistema:** Si detectamos 3 o más peticiones 404 a rutas sospechosas desde la misma IP → alerta de severidad MEDIUM (o HIGH si las rutas son críticas como `/.env`).

### 3. Eventos de seguridad (Security Events)

**¿Qué es?** El propio servidor SAP marca ciertos eventos como relevantes para seguridad. No necesariamente son ataques, pero son eventos que un equipo de seguridad debería revisar.

**¿Cómo se ve en nuestros datos?** Registros con `log_type = 'SECURITY'`. El servidor los clasifica así porque involucran cambios de permisos, accesos a recursos restringidos, o actividad fuera de horario.

**Regla del sistema:** Cualquier evento SECURITY genera una alerta agrupada con conteo de IPs involucradas → severidad HIGH si son 3 o más, MEDIUM si son 1-2.

### 4. Abuso de costos LLM (High Cost LLM Errors)

**¿Qué es?** Peticiones a modelos de IA que generan costos altos pero fallan. Podría ser un ataque diseñado para inflar la factura del servicio de IA (un tipo de ataque de denegación de servicio económico).

**¿Cómo se ve en nuestros datos?** Registros LLM con `llm_status = 'error'` y `llm_cost_usd > 1.0`. El modelo falló pero el costo ya se generó.

**Regla del sistema:** Errores LLM con costo superior a $1 USD → alerta con costo total acumulado.

### 5. Timeouts de LLM

**¿Qué es?** Peticiones a modelos de IA que nunca terminan de responder. En volumen, puede indicar un ataque de degradación de servicio o problemas sistémicos.

**¿Cómo se ve en nuestros datos?** Registros con `log_type = 'LLM_TIMEOUT'`.

**Regla del sistema:** Cualquier timeout genera alerta agrupada por ciclo.

### 6. Respuestas lentas (Slow Response)

**¿Qué es?** Modelos de IA que responden pero tardan demasiado. Puede indicar sobrecarga del servicio o ataques que buscan consumir recursos.

**¿Cómo se ve en nuestros datos?** Registros con `llm_response_time > 10,000 ms` (más de 10 segundos).

**Regla del sistema:** Respuestas > 10 segundos → alerta agrupada con máximo, promedio y conteo.

### 7. Anomalías estadísticas (detectadas por ML)

Además de las 6 reglas anteriores, el modelo de Machine Learning detecta patrones anómalos que no se pueden capturar con reglas simples. Por ejemplo, una IP que individualmente no rompe ninguna regla pero cuyo comportamiento agregado (combinación de rutas visitadas, códigos de respuesta, y frecuencia) es estadísticamente diferente al de todas las demás IPs.

Más detalle en la [Sección 8](#8-el-modelo-de-machine-learning--resumen-ejecutivo).

---

## 4. ¿Cómo funciona el sistema completo?

> **¿Quién necesita leer esto?** Todos. Este es el flujo que hay que poder explicar en el video.

### La vista de alto nivel

```
                    CADA 2 MINUTOS (automático, 24/7):

                         API de SAP
                             │
                   Recolectar logs nuevos
                             │
                   ┌─────────┴──────────┐
                   │                    │
              Guardar en            Guardar como
            base de datos            respaldo CSV
              (HANA)                   (local)
                   │
          ┌────────┴────────┐
          │                 │
    Filtro Rápido      Modelo ML
   (reglas simples)   (cada 28 min)
   ¿Fuerza bruta?    ¿Patrón raro
   ¿Escaneo?          estadístico?
   ¿Error costoso?
          │                 │
          └────────┬────────┘
                   │
            ¿Amenaza encontrada?
                   │
              Sí ──┤── No → esperar
                   │       al siguiente
           Enviar alerta    ciclo
           a SAP (POST)
                   │
           Guardar registro
           en tabla ALERTS
                   │
                  ✅
```

### Los dos ciclos de detección

El sistema tiene dos "velocidades" de detección que trabajan en paralelo:

**Ciclo rápido (cada 2 minutos):** Aplica las 6 reglas determinísticas sobre los datos nuevos. Es rápido porque solo revisa condiciones concretas (¿hay más de 5 errores 401 desde la misma IP? ¿hay rutas sospechosas?). Resultado: detecta amenazas en ~1 segundo.

**Ciclo largo (cada 28 minutos):** Ejecuta el modelo de Machine Learning sobre las últimas 24 horas de datos acumulados. Es más lento pero más inteligente — detecta patrones complejos que las reglas simples no ven. Resultado: detecta anomalías estadísticas que requerirían un analista humano experimentado.

### ¿Qué pasa cuando se detecta una amenaza?

1. El sistema construye un mensaje corto con formato QUÉ/CUÁNDO/POR QUÉ (máximo 300 caracteres)
2. Guarda el registro en la tabla ALERTS con estado "detectada, pendiente de envío"
3. Envía el mensaje a la API de SAP
4. Si SAP confirma la recepción (código 201), actualiza el estado a "detectada y confirmada"
5. Si falla, reintenta hasta 3 veces
6. Si la misma amenaza ya fue reportada, no la envía de nuevo

> **Detalle técnico**
>
> El endpoint de alerting es `POST /alert` en `{API_BASE_URL}/alert`, mismo `BEARER_TOKEN`. Body: `{"message": "WHAT: ... WHEN: ... WHY: ..."}`, máx 300 chars. Respuesta exitosa: HTTP 201. Reintentos: backoff exponencial 1s → 2s, máx 3 intentos ante 5xx/timeout. `enviar_alerta()` nunca lanza excepciones. Anti-duplicados por `log_id` en `DBADMIN.ALERTS` con campo `alerted` (0 = pendiente, 1 = confirmada).

---

## 5. El ecosistema SAP — qué es cada pieza

> **¿Quién necesita leer esto?** Todos para la explicación general. Los bloques técnicos marcados son solo para quien esté configurando conexiones SAC o desplegando a Cloud Foundry.

### La analogía del edificio

Piensen en el ecosistema SAP como un edificio corporativo:

**SAP BTP (Business Technology Platform)** es el edificio completo. Es la plataforma en la nube de SAP donde viven todos los servicios. Cuando decimos "está desplegado en SAP", queremos decir que está aquí.

**Cloud Foundry** es el piso de oficinas dentro del edificio donde corren las aplicaciones. Cuando subimos nuestro código Python a Cloud Foundry, es como instalar un programa en una computadora que nunca se apaga. Cloud Foundry se encarga de mantenerlo corriendo, reiniciarlo si falla, e instalar las librerías que necesita. Así nuestro pipeline funciona 24/7 sin depender de que alguien tenga su laptop encendida.

**SAP HANA Cloud** es la bodega del edificio donde se guardan todos los datos de manera organizada. No es un archivo — es un sistema de base de datos completo donde cualquier miembro del equipo puede consultar los datos con las credenciales correctas. Al día de hoy tiene más de 1.6 millones de registros acumulados.

**SAP Analytics Cloud (SAC)** es la sala de monitoreo del edificio. Es la herramienta de visualización de SAP que se conecta directamente a HANA y muestra los datos en dashboards en tiempo real. Es donde construiremos el dashboard de seguridad del SOC.

**HDI Container** es una "habitación privada" dentro de HANA que SAC necesita para poder leer los datos. No es algo que los usuarios vean directamente — es infraestructura que conecta SAC con las tablas de datos.

### Cómo se conectan todas las piezas

```
API SAP (fuente de datos)
    │
    │  Conexión segura con token de autenticación
    ▼
Pipeline Python (corriendo en Cloud Foundry, 24/7)
    │
    ├──► Archivos CSV de respaldo (uno por cada ventana de 30 min)
    │
    └──► SAP HANA Cloud (base de datos)
              │
              ├── Tabla RAW_LOGS_SISTEMA  (~1M registros de aplicaciones)
              ├── Tabla RAW_LOGS_LLM      (~580K registros de IA)
              └── Tabla ALERTS            (amenazas detectadas)
                          │
                          ▼
                SAP Analytics Cloud (dashboards en tiempo real)
                          │
                          ▼
                  Dashboard del SOC para la presentación
```

> **¿Quién necesita leer lo siguiente?** Solo si estás trabajando en la conexión de SAC a HANA o si necesitas entender la arquitectura de permisos para el reporte técnico.

### Usuarios y permisos en la base de datos

La base de datos tiene diferentes usuarios con diferentes niveles de acceso. Piénsenlo como las llaves de un edificio — cada persona tiene acceso solo a lo que necesita:

**DBADMIN** — la llave maestra. Tiene acceso total. Solo la usan los responsables de infraestructura para crear tablas y dar permisos.

**PIPELINE_USER** — la tarjeta del personal de mantenimiento. Es el usuario que usa el pipeline automático para escribir datos cada 2 minutos. Puede leer y escribir datos pero no puede cambiar la estructura.

**SAC_USER** — el pase de visitante. Es el usuario que usa SAP Analytics Cloud para leer datos y mostrarlos en dashboards. Solo lectura, nunca escribe ni modifica.

**#OO (Object Owner)** — un usuario técnico que el sistema SAP crea automáticamente. No lo usa ninguna persona — existe para que la conexión entre SAC y HANA funcione internamente.

> **Detalle técnico (solo para quien configure permisos o conexiones SAC)**
>
> El orden de los GRANTs es crítico:
> 1. `GRANT SELECT ON DBADMIN.<TABLA> TO "SOC_DASHBOARD_HDI_DB_1#OO" WITH GRANT OPTION` — después del primer deploy del HDI Container
> 2. `CREATE USER SAC_USER PASSWORD <pwd> NO FORCE_FIRST_PASSWORD_CHANGE`
> 3. `GRANT SELECT ON SCHEMA DBADMIN TO SAC_USER`
> 4. `GRANT "SOC_DASHBOARD_HDI_DB_1::access_role" TO SAC_USER` y `GRANT "SOC_DASHBOARD_HDI_DB_1::external_privileges_role" TO SAC_USER` — después del deploy del Calculation View
>
> Regla de seguridad: nunca usar `PIPELINE_USER` para SAC ni `SAC_USER` para el pipeline.

---

## 6. Arquitectura interna — los módulos del código

> **¿Quién necesita leer esto?** Todos para la vista general. Si estás contribuyendo al reporte técnico, necesitas entender qué hace cada módulo y por qué existe.

### Estructura del repositorio

```
sap-security-hackathon/
│
├── pipeline_loop.py         ← El "cerebro" — orquesta todo el ciclo de 2 min
├── server.py                ← Endpoints de monitoreo (verificar que el sistema vive)
├── manifest.yml             ← Instrucciones para desplegar en Cloud Foundry
├── runtime.txt              ← Versión de Python
├── requirements.txt         ← Librerías necesarias
├── .env                     ← Credenciales (NUNCA en Git)
│
├── app/                     ← Los módulos del pipeline
│   ├── config.py            ← Lee credenciales del entorno
│   ├── ingest.py            ← Recolecta datos de la API
│   ├── hana_client.py       ← Escribe datos en HANA
│   ├── etl.py               ← Limpia y transforma datos
│   ├── quick_filter.py      ← 6 reglas de detección rápida
│   ├── alerting.py          ← Envía alertas a SAP
│   ├── hana_reader.py       ← Lee datos de HANA (para el modelo ML)
│   ├── feature_eng.py       ← Prepara datos para los algoritmos ML
│   └── model.py             ← Los 3 modelos de Machine Learning
│
├── data/
│   ├── raw/                 ← CSVs de respaldo (uno por ventana)
│   ├── processed/           ← Datos limpios para análisis
│   └── SCHEMA.md            ← Documentación de las 43 columnas
│
└── docs/                    ← Documentación del proyecto
```

### Qué hace cada módulo (sin entrar al código)

**`pipeline_loop.py`** — Es el módulo principal. Como un reloj que suena cada 2 minutos y dice "es hora de revisar si hay datos nuevos". Coordina todo el flujo: recolectar → guardar → detectar → alertar. Corre indefinidamente en Cloud Foundry.

**`config.py`** — Es el módulo que sabe dónde están las credenciales. Todos los demás módulos le preguntan a este las contraseñas y URLs en lugar de tenerlas escritas directamente. Esto es crítico para seguridad — si las credenciales estuvieran en el código y se subieran a GitHub, el equipo quedaría penalizado.

**`ingest.py`** — Se conecta a la API de SAP y descarga todos los registros de la ventana actual. Como los registros vienen en páginas de 500, tiene que hacer varias llamadas para traerlos todos. También se encarga de no descargar el mismo registro dos veces (deduplicación).

**`hana_client.py`** — Es el único módulo que habla directamente con la base de datos HANA. Crea las tablas si no existen, e inserta los datos nuevos. Si HANA no está disponible (a veces se pausa por inactividad), el pipeline sigue funcionando solo con archivos CSV.

**`etl.py`** — Toma los datos crudos y los limpia: convierte fechas a un formato estándar, normaliza números, y separa los registros de sistema y de LLM en archivos separados.

**`quick_filter.py`** — Aplica las 6 reglas de detección rápida (fuerza bruta, escaneo de rutas, etc.) sobre cada lote de datos nuevos. No envía alertas, solo detecta — devuelve una lista de amenazas encontradas.

**`alerting.py`** — Recibe las amenazas detectadas y las envía a SAP. Se encarga de construir el mensaje, reintentar si falla, y registrar cada alerta en la base de datos.

**`hana_reader.py`** — Lee datos históricos desde HANA para alimentar al modelo de ML. Es diferente de `hana_client.py` porque lee en lugar de escribir, y lee grandes volúmenes (24 horas de historia).

**`feature_eng.py`** — Transforma los datos crudos en números que los algoritmos de ML pueden procesar. Los algoritmos no entienden texto como "ERROR" o "200" — necesitan vectores numéricos.

**`model.py`** — Contiene los 3 modelos de Machine Learning que detectan anomalías estadísticas. Es el "cerebro analítico" que complementa las reglas simples del filtro rápido.

> **Detalle técnico — cómo se conectan los módulos**
>
> ```
> pipeline_loop.py
>     │
>     ├── Cada 2 min:
>     │   config.py → ingest.py → hana_client.py → quick_filter.py → alerting.py
>     │
>     └── Cada 28 min:
>         config.py → hana_reader.py → feature_eng.py → model.py → alerting.py
> ```
>
> Imports condicionales: si `quick_filter` o `alerting` fallan al importar, el pipeline no muere — sigue ingiriendo datos. La ingesta nunca puede romperse por un módulo de detección.

---

## 7. Esquemas de la Base de Datos

> **¿Quién necesita leer esto?** Si estás construyendo el dashboard en SAC, necesitas saber qué columnas hay disponibles y qué significan. Si estás haciendo el reporte forense, necesitas entender la tabla ALERTS. Si no estás haciendo ninguna de las dos, puedes saltar al resumen al final de la sección.

### Tabla: RAW_LOGS_SISTEMA (logs de aplicaciones web)

Cada fila es un evento de acceso a una aplicación SAP.

| Columna | Qué significa | Ejemplo |
|---|---|---|
| `ID` | Identificador interno auto-generado | 1, 2, 3... |
| `LOG_ID` | Identificador único del evento (viene de la API) | `abc123def456` |
| `EVENT_TIMESTAMP` | Cuándo ocurrió el evento | `2026-05-01 06:31:00` |
| `INGESTED_AT` | Cuándo lo recolectó nuestro pipeline | `2026-05-01 06:31:35` |
| `LOG_TYPE` | Tipo de evento | `INFO`, `WARNING`, `ERROR`, `SECURITY` |
| `APPLICATION` | Qué aplicación SAP generó el evento | nombre de la app |
| `MESSAGE` | Mensaje descriptivo del evento | texto libre |
| `ENVIRONMENT` | Entorno de ejecución | `production`, `staging` |
| `REGION_NAME` | Región geográfica del servidor | `US East`, `EU Central` |
| `REGION_CODE` | Código corto de la región | `us-east-1` |
| `MACRO_REGION` | Región general | `Americas`, `EMEA` |
| `SERVICE_ID` | Identificador del servicio SAP | ID del servicio |
| `HTTP_STATUS` | Código de respuesta del servidor | `200`, `401`, `404`, `500` |
| `CLIENT_IP` | Dirección IP de quien hizo la petición | `203.0.113.45` |
| `REQUEST_METHOD` | Método HTTP usado | `GET`, `POST`, `DELETE` |
| `REQUEST_PATH` | Ruta a la que intentó acceder | `/api/users`, `/.env` |

**Total: ~1,026,000+ registros** acumulados al 4 de mayo.

### Tabla: RAW_LOGS_LLM (logs de modelos de IA)

Cada fila es una interacción con un modelo de lenguaje.

| Columna | Qué significa | Ejemplo |
|---|---|---|
| `ID` | Identificador interno auto-generado | 1, 2, 3... |
| `LOG_ID` | Identificador único del evento | `xyz789ghi012` |
| `EVENT_TIMESTAMP` | Cuándo ocurrió | `2026-05-01 06:31:00` |
| `INGESTED_AT` | Cuándo lo recolectamos | `2026-05-01 06:31:35` |
| `LOG_TYPE` | Tipo de evento LLM | `LLM_REQUEST`, `LLM_ERROR`, `LLM_TIMEOUT` |
| `APPLICATION` | Qué aplicación hizo la petición | nombre de la app |
| `MESSAGE` | Mensaje descriptivo | texto libre |
| `ENVIRONMENT` | Entorno | `production` |
| `REGION_NAME` | Región geográfica | `US East` |
| `REGION_CODE` | Código de región | `us-east-1` |
| `MACRO_REGION` | Región general | `Americas` |
| `LLM_MODEL_ID` | Qué modelo de IA se usó | `gpt-4`, `claude-3` |
| `LLM_PROVIDER` | Quién provee el modelo | `OpenAI`, `Anthropic` |
| `LLM_STATUS` | Resultado de la petición | `success`, `error`, `timeout` |
| `LLM_ERROR_MESSAGE` | Mensaje de error (si falló) | texto del error |
| `LLM_PROMPT_CATEGORY` | Categoría del prompt | tipo de tarea |
| `LLM_TOTAL_TOKENS` | Total de tokens procesados | 1500, 3000 |
| `LLM_COST_USD` | Costo en dólares | 0.003, 0.12 |
| `LLM_RESPONSE_TIME` | Tiempo de respuesta en milisegundos | 2500, 15000 |
| `LLM_TEMPERATURE` | Parámetro de creatividad del modelo | 0.7, 1.0 |
| `LLM_FINISH_REASON` | Por qué terminó la respuesta | `stop`, `length`, `error` |

**Total: ~584,000+ registros** acumulados al 4 de mayo.

### Tabla: ALERTS (amenazas detectadas)

Cada fila es una amenaza que el sistema detectó y (opcionalmente) envió a SAP.

| Columna | Qué significa | Ejemplo |
|---|---|---|
| `ALERT_ID` | Identificador único de la alerta | `uuid-generado` |
| `LOG_ID` | Identificador del registro que la disparó | `abc123def456` |
| `DETECTED_AT` | Cuándo se detectó | `2026-05-01 06:31:35` |
| `DETECTION_SOURCE` | Qué componente la detectó | `quick_filter` o `model_ml` |
| `ALERT_TYPE` | Tipo de amenaza | `brute_force`, `path_scan`, `ml_ip_anomaly` |
| `SEVERITY` | Gravedad | `low`, `medium`, `high` |
| `DETAILS` | Descripción corta de la amenaza | texto libre, máx 300 chars |
| `WINDOW_START` | Ventana temporal del evento | `2026-05-01 06:30:00` |
| `ALERTED` | ¿Se envió exitosamente a SAP? | `0` = no, `1` = sí |

**Dato clave:** `ALERTED = 1` significa que SAP confirmó la recepción de la alerta. `ALERTED = 0` significa que se detectó pero el envío falló o está pendiente.

### Resumen rápido de las tablas

| Tabla | Qué guarda | Registros | Para qué la usamos |
|---|---|---|---|
| `RAW_LOGS_SISTEMA` | Actividad de aplicaciones web | ~1M+ | Detectar ataques de red |
| `RAW_LOGS_LLM` | Actividad de modelos de IA | ~580K+ | Detectar abuso de IA |
| `ALERTS` | Amenazas detectadas | variable | Reporte forense + auditoría |

---

## 8. El modelo de Machine Learning — resumen ejecutivo

> **¿Quién necesita leer esto?** Todos — necesitan poder explicar en el video por qué usamos estos algoritmos. La explicación funcional es suficiente para la presentación. El detalle técnico es para el reporte de arquitectura.

### ¿Por qué Machine Learning y por qué no supervisado?

**El problema:** No tenemos ejemplos previos de ataques etiquetados. Nadie ha revisado millones de logs y marcado "este es un ataque" y "este no lo es". Sin esas etiquetas, no podemos usar algoritmos que aprendan de ejemplos (como los que se usan para clasificar emails como spam).

**La solución:** Usamos algoritmos que aprenden qué es "normal" observando la mayoría de los datos, y luego detectan lo que se desvía de ese patrón. Es como aprender el ritmo de una ciudad — si todos los días hay tráfico moderado a las 3pm y un día hay un embotellamiento masivo, algo anormal está pasando, aunque nunca hayas visto un embotellamiento antes.

### Los 3 modelos y qué detecta cada uno

**Modelo 1 — Isolation Forest para logs de Sistema**

Analiza cada evento de acceso web individualmente. Busca eventos que son "fáciles de separar" del resto — si un evento se ve muy diferente a los demás, probablemente es anómalo. Usa 9 características de cada evento: el tipo de código HTTP, el tipo de log, la aplicación, la región, y la hora.

**Modelo 2 — Isolation Forest para logs de LLM**

Igual que el anterior pero para eventos de IA. Analiza costo, tiempo de respuesta, tokens consumidos, modelo usado, y proveedor. Detecta, por ejemplo, una petición que costó 10 veces más que el promedio o que usó un modelo inusual.

**Modelo 3 — Local Outlier Factor (LOF) para comportamiento por IP**

En lugar de analizar eventos individuales, agrupa toda la actividad de cada dirección IP y compara IPs entre sí. Una IP que visitó muchas rutas diferentes, tuvo muchos errores, y accedió a aplicaciones variadas se ve diferente a una IP que solo hizo 2 peticiones normales. LOF detecta esas IPs con comportamiento inusual comparándolas con sus "vecinas" más similares.

**¿Por qué 3 modelos y no 1?** Los logs de sistema y de LLM tienen columnas completamente diferentes — mezclarlos en un solo modelo confundiría al algoritmo. Y el análisis por IP es un ángulo diferente: los modelos 1 y 2 ven eventos individuales, el modelo 3 ve comportamiento agregado.

### Resultados reales

En el primer ciclo de ML en producción (4 mayo 2026):
- Entrenó con ~127,000 registros de sistema + ~43,000 de LLM (últimas 24 horas)
- Detectó 5 IPs con comportamiento anómalo
- Las 5 alertas fueron enviadas y confirmadas por SAP (HTTP 201)
- Tiempo de ejecución: ~6 segundos

> **Detalle técnico (para el reporte de arquitectura)**
>
> **Isolation Forest:** `n_estimators=200`, `max_samples=256`, `contamination='auto'`, `random_state=42`. Complejidad: O(t × n × log n) donde t=200 y n=256.
>
> **LOF:** `n_neighbors=20`, `contamination='auto'`. Opera sobre tabla agregada por IP (~105 filas). Usa `RobustScaler` porque LOF calcula distancias euclidianas y las features tienen rangos muy diferentes.
>
> **Feature engineering Sistema:** `HTTP_STATUS` → `status_family` (int), `is_4xx`, `is_5xx`, `is_401_or_403`, `is_429` (bools). `EVENT_TIMESTAMP` → `hour_utc`. `LOG_TYPE`, `APPLICATION`, `REGION_NAME` → `OrdinalEncoder`.
>
> **Feature engineering LLM:** `LLM_COST_USD`, `LLM_RESPONSE_TIME`, `LLM_TOTAL_TOKENS` → `log1p()` por distribución heavy-tail. Categóricas → `OrdinalEncoder`.
>
> **Thresholding:** Modo histórico (≥20 ventanas): `threshold = median - 3.5 * (MAD / 0.6745)`. MAD es robusto a outliers (usa mediana, no media). Cap de 5 alertas por ciclo, ordenadas por severidad.
>
> **Librerías:** `scikit-learn>=1.8.0` (IsolationForest, LocalOutlierFactor, OrdinalEncoder, RobustScaler), `pandas`, `numpy`.

---

## 9. Línea del tiempo — cómo se construyó todo

| Semana | Qué se hizo | Resultado |
|---|---|---|
| **Abr 6-13** | Kick-off. Se creó el repositorio, la estructura de carpetas, y los primeros scripts de prueba para conectarse a la API. | Primera conexión exitosa: 5,729 registros descargados. |
| **Abr 13-20** | Se construyó el módulo de ingesta (`ingest.py`) con paginación completa y el ETL de limpieza. El loop de automatización corrió toda la noche acumulando ventanas. | Pipeline local funcionando cada 30 minutos. |
| **Abr 20-22** | Se integró SAP HANA Cloud. Se resolvieron problemas de SSL, de sintaxis SQL incompatible con la versión trial, y de rendimiento de inserción (de 9 min a 16 seg). Se creó la tabla ALERTS. | Base de datos operativa con datos acumulándose. |
| **Abr 23-24** | Rediseño arquitectónico: de ciclos de 30 min a polling cada 1-2 min. Se implementó deduplicación en dos capas (memoria + HANA). Deploy a Cloud Foundry. | Pipeline corriendo en la nube 24/7, MTTD reducido a ~1-2 min. |
| **Abr 27** | Se descubrió que el alerting usa `POST /alert` en la misma API (no un webhook externo). Se documentó el diccionario completo de la API. | Ruta de alerting clara. |
| **May 1** | Se implementaron `quick_filter.py` (6 reglas) y `alerting.py` (envío a SAP). Se integró todo en el pipeline. Deploy a CF. | Primer ciclo completo DETECT→RESPOND: 3 amenazas detectadas y confirmadas por SAP. |
| **May 3-4** | Se construyó el sistema ML completo: `hana_reader.py`, `feature_eng.py`, `model.py`. Se resolvió bug crítico de persistencia en ALERTS. Se configuró SAC Live Connection con Calculation View. | ML en producción. 5 anomalías detectadas y confirmadas. MTTD medido: <2 segundos. |

### Métricas clave al día de hoy

| Métrica | Valor |
|---|---|
| MTTD (tiempo de detección) | ~1 segundo |
| Latencia de alerta (detección → confirmación SAP) | 188-310 ms |
| Registros acumulados (sistema) | 1,026,000+ |
| Registros acumulados (LLM) | 584,000+ |
| Ventanas procesadas | 309+ |
| Uptime del pipeline | 24/7 desde el 24 de abril |
| Crashes desde el primer deploy | 0 |

---

## 10. Qué pide la rúbrica y cómo nos evalúan

> **¿Quién necesita leer esto?** Todos. Cada tarea del plan de acción está vinculada directamente a estos criterios.

### Los 4 entregables obligatorios

Si falta cualquiera de estos, ese criterio se evalúa en **cero**.

| Entregable | Formato | Puntos |
|---|---|---|
| Repositorio GitHub | URL pública y accesible | 20 |
| Video técnico | YouTube o Drive, máx 8 min (recomendado 5) | 20 |
| Diagrama de arquitectura | PDF o imagen dentro del repo | 20 |
| Reporte técnico de arquitectura | PDF o Markdown dentro del repo | 20 |

### Criterios adicionales

| Criterio | Puntos |
|---|---|
| Impacto de negocio | 8 |
| Presentación y pensamiento estratégico | 15 |
| **Total máximo** | **103** |
| **Mínimo para pasar** | **62** |

### Penalizaciones

| Situación | Penalización |
|---|---|
| Video supera 8 minutos | -3 pts |
| README sin instrucciones de setup | -3 pts |
| Pipeline sin actividad continua durante el reto | -5 pts |
| Credenciales expuestas en el código | **-10 pts** |
| Video no entregado | -15 pts |

### Descalificación

- Repositorio inaccesible (privado sin respuesta)
- Plagio
- Solución fuera del alcance del reto

### Lo que los organizadores están monitoreando

**Los organizadores tienen visibilidad directa sobre la actividad de cada equipo.** Monitorean los logs de consumo de la API y las alertas generadas. Un pipeline que solo se active justo antes de la entrega tendrá penalización. El nuestro corre desde el 24 de abril — esto es una ventaja.

### Puntos extra: Agente de IA

Los organizadores mencionaron que SAP está apostando por AI Agents y sugirieron crear un agente que pueda hacer llamadas a HANA desde el dashboard. Esto no es obligatorio pero da puntos extra y demuestra visión estratégica.

---

## 11. Plan de acción — tareas, responsabilidades y prioridades

### Contexto: somos 5 personas, tenemos 4 días

Dos integrantes tienen contexto profundo del proyecto (construyeron todo el pipeline y la infraestructura). Tres integrantes se integran ahora y tienen habilidades de programación y análisis que pueden aplicar inmediatamente sobre tareas concretas.

---

### BLOQUE A — Infraestructura y documentación técnica (CIE + Data Architect)

> Los dos integrantes con contexto del sistema se encargan de lo que solo ellos pueden hacer: limpiar el repositorio, documentar el código que escribieron, y terminar las conexiones SAP.

**CIE — Prioridad CRÍTICA (hoy/mañana):**

- Limpiar el repositorio: eliminar archivos de backup y artefactos de desarrollo, verificar que no hay credenciales en el historial de commits, merge de la rama de trabajo a `main`.
- Escribir el README maestro: qué es el proyecto, estructura de carpetas, cómo instalar, cómo correr, cómo desplegar. Incluir screenshots como evidencia.

**CIE — Prioridad ALTA (días 2-3):**

- Escribir la documentación completa del modelo ML para el reporte técnico: por qué estos algoritmos, qué alternativas se consideraron, feature engineering, thresholding, resultados reales.
- Co-crear el diagrama de arquitectura con el Data Architect.
- Exportar un CSV de `DBADMIN.ALERTS` para que el equipo construya el reporte forense sin necesitar acceso directo a HANA.
- Estar disponible como soporte técnico para el equipo.

**Data Architect — Prioridad CRÍTICA (hoy/mañana):**

- Completar los Calculation Views en SAC para las tablas `RAW_LOGS_SISTEMA` y `ALERTS`. El proceso ya está validado con `RAW_LOGS_LLM` — es replicar el mismo patrón.
- Co-crear el diagrama de arquitectura con el CIE.

**Data Architect — Prioridad ALTA (días 2-3):**

- Escribir la sección de integración SAP del reporte técnico: por qué HANA, por qué dos tablas, cómo funciona la conexión a SAC.
- Proveer credenciales y queries al equipo para el dashboard y el agente.
- Verificar que el pipeline sigue corriendo en CF cada mañana.

**En conjunto — Prioridad ALTA (día 2):**

- Diagrama de arquitectura visual (usando draw.io, Lucidchart o similar). Debe mostrar todos los componentes, las flechas con etiquetas de qué dato fluye entre cada uno, y los componentes SAP correctamente representados.

---

### BLOQUE B — Dashboard, reportes y presentación (equipo de apoyo)

> Las tres personas que se integran pueden elegir entre estas tareas según sus fortalezas. Todas son ejecutables con la información de este documento + los datos que provean el CIE y el Data Architect.

**TAREA 1 — Dashboard en SAP Analytics Cloud (Prioridad ALTA)**

Construir el dashboard de seguridad del SOC en SAC. Una vez que el Data Architect complete los Calculation Views, las tres tablas estarán disponibles como fuentes de datos.

El dashboard debería mostrar como mínimo:
- Volumen de logs por ventana temporal (línea de tiempo)
- Distribución de tipos de log (sistema vs LLM)
- Mapa o gráfica de actividad por región
- Alertas detectadas por tipo y severidad
- Métricas LLM: costo acumulado, tokens, tiempos de respuesta por proveedor
- Estado del pipeline: registros acumulados, última ingesta

Mientras los CVs se completan, se puede diseñar el layout del dashboard (qué gráficas, dónde van, qué colores).

**Componente de puntos extra — Agente de IA:**

Un agente que permita consultar HANA en lenguaje natural. El usuario escribe una pregunta como "¿Cuántas alertas de brute force hubo hoy?" y el agente genera la query SQL, la ejecuta contra HANA, y muestra el resultado.

Puede vivir como un componente separado en Streamlit (`app/agent.py`) que complementa el dashboard de SAC. Se implementa con una llamada directa a la API de un proveedor de LLM, pasándole el schema de las tablas como contexto para que genere las queries correctas.

**TAREA 2 — Reporte forense + Impacto de negocio (Prioridad ALTA)**

Construir la narrativa de los incidentes reales detectados. El CIE exportará un CSV de la tabla ALERTS. Con esos datos, documentar al menos 2-3 incidentes reales con esta estructura:

Para cada incidente:
- **Qué se detectó:** tipo de amenaza (brute force, path scan, anomalía ML)
- **Cuándo ocurrió:** timestamp exacto
- **Qué regla o modelo lo identificó:** quick_filter o model_ml
- **Qué significaría en un entorno real:** impacto potencial en una empresa SAP
- **Qué acción automática tomó el sistema:** alerta enviada, confirmada, tiempo de respuesta

Para la sección de impacto de negocio:
- ¿Qué problema concreto resuelve esto y para quién? (CISOs de empresas que usan SAP)
- Métricas de éxito reales: MTTD < 2 segundos (objetivo era ≤ 2 minutos), latencia de alerta 188-310ms, cobertura 24/7
- ¿Qué significaría para una empresa tener un SOC que detecta en 1 segundo vs horas?
- Referencia a taxonomías reales de seguridad (familias Tenable/Nessus) para dar credibilidad

**TAREA 3 — Presentación estratégica + Guión del video (Prioridad ALTA)**

Estructurar la narrativa de presentación y coordinar el video.

Guión sugerido para el video (máx 8 min, apuntar a 5-6):

| Bloque | Tiempo | Contenido |
|---|---|---|
| El problema | 30 seg | Qué problema de ciberseguridad resolvemos y para quién |
| Arquitectura | 1.5 min | Diagrama explicado: de dónde vienen los datos hasta dónde llega la alerta |
| Demo pipeline | 1 min | Logs reales de CF mostrando ingesta + detección + alerta |
| Demo dashboard | 1 min | Dashboard SAC con datos reales de HANA |
| Modelo ML | 1 min | Por qué estos algoritmos, qué detectan, resultados reales |
| Agente IA (si se implementa) | 30 seg | Demo de consulta en lenguaje natural contra HANA |
| Métricas | 30 seg | MTTD, latencia, registros acumulados, uptime |
| Impacto y visión | 30 seg | Valor para SAP, escalabilidad, visión de autonomous enterprise |

Para la presentación estratégica, la rúbrica evalúa:
- ¿Comprenden el problema de ciberseguridad y su impacto real?
- ¿La solución genera valor claro y práctico?
- ¿Es implementable y considera escalabilidad?
- ¿Comunican de forma clara y profesional?
- ¿Demuestran pensamiento estratégico?

---

### Calendario de ejecución

**Día 1 (hoy — 8 mayo):**
- CIE limpia repositorio + empieza README
- Data Architect trabaja en CVs de SAC
- Ambos hacen el diagrama de arquitectura
- CIE exporta CSV de ALERTS
- Equipo de apoyo lee este documento completo

**Días 2-3 (9-10 mayo, en paralelo):**
- CIE escribe documentación ML + termina README
- Data Architect termina CVs + escribe sección SAP del reporte
- Equipo construye dashboard SAC + agente IA
- Equipo escribe reporte forense + impacto de negocio
- Equipo estructura presentación + guión del video

**Día 4 (11 mayo — integración):**
- Integrar todas las secciones del reporte técnico
- Grabar el video siguiendo el guión
- Verificación final: repo público, README funcional, diagrama coincide con código, pipeline corriendo, sin credenciales expuestas

**Día 5 (12 mayo — entrega):**
- Revisión final de todos los entregables
- Subir video a YouTube o Drive
- Entregar antes de las 11:59 PM

---

## 12. Referencia rápida — queries, comandos y accesos

> **¿Quién necesita leer esto?** Si necesitas consultar datos en HANA o monitorear el pipeline, aquí están los comandos. Si no, puedes saltarlo.

### Queries SQL para consultar datos en HANA

```sql
-- ¿Cuántos registros hay en cada tabla?
SELECT COUNT(*) AS total FROM DBADMIN.RAW_LOGS_SISTEMA;
SELECT COUNT(*) AS total FROM DBADMIN.RAW_LOGS_LLM;

-- ¿Cuántas alertas se han detectado y enviado?
SELECT COUNT(*) AS total FROM DBADMIN.ALERTS;
SELECT COUNT(*) AS enviadas FROM DBADMIN.ALERTS WHERE ALERTED = 1;
SELECT COUNT(*) AS pendientes FROM DBADMIN.ALERTS WHERE ALERTED = 0;

-- Ver las últimas alertas detectadas
SELECT ALERT_ID, ALERT_TYPE, SEVERITY, DETECTED_AT, ALERTED, DETECTION_SOURCE
FROM DBADMIN.ALERTS
ORDER BY DETECTED_AT DESC;

-- Ver los últimos registros de sistema
SELECT TOP 10 LOG_ID, EVENT_TIMESTAMP, LOG_TYPE, CLIENT_IP, HTTP_STATUS
FROM DBADMIN.RAW_LOGS_SISTEMA
ORDER BY INGESTED_AT DESC;

-- Solo logs de seguridad (los más relevantes para detección)
SELECT TOP 20 LOG_ID, EVENT_TIMESTAMP, LOG_TYPE, CLIENT_IP, HTTP_STATUS, REQUEST_PATH
FROM DBADMIN.RAW_LOGS_SISTEMA
WHERE LOG_TYPE = 'SECURITY'
ORDER BY EVENT_TIMESTAMP DESC;

-- Distribución de tipos de log LLM
SELECT LOG_TYPE, LLM_STATUS, COUNT(*) AS total
FROM DBADMIN.RAW_LOGS_LLM
GROUP BY LOG_TYPE, LLM_STATUS
ORDER BY total DESC;

-- Errores LLM con costo alto
SELECT EVENT_TIMESTAMP, LLM_MODEL_ID, LLM_PROVIDER, LLM_COST_USD, LLM_ERROR_MESSAGE
FROM DBADMIN.RAW_LOGS_LLM
WHERE LOG_TYPE = 'LLM_ERROR' AND LLM_COST_USD > 0.5
ORDER BY LLM_COST_USD DESC;
```

### Comandos para monitorear el pipeline en Cloud Foundry

```bash
# Ver los últimos logs del pipeline
cf logs sap-ai-soc-papoi --recent

# Ver logs en tiempo real
cf logs sap-ai-soc-papoi

# Estado de la aplicación (memoria, CPU, uptime)
cf app sap-ai-soc-papoi

# Reiniciar si hay problemas
cf restart sap-ai-soc-papoi

# Redesplegar con cambios de código
cf push
```

### ¿Qué credenciales necesito y quién las tiene?

| Necesitas | Para qué | Quién te la da |
|---|---|---|
| Credenciales HANA (host, puerto, usuario, contraseña) | Consultar datos desde Python o SQL Console | CIE o Data Architect |
| Acceso al repositorio GitHub | Ver y modificar el código | CIE |
| Acceso a SAP Analytics Cloud | Construir dashboards | Data Architect |
| CF CLI configurado | Monitorear o redesplegar el pipeline | CIE |

**Regla de seguridad:** Ninguna credencial va en el código ni en el repositorio. Siempre en variables de entorno (`.env` local o `cf set-env` en Cloud Foundry). Si se sube una credencial a GitHub, el equipo queda penalizado con bloqueo al día siguiente.

---

## Alertas y riesgos antes de la entrega

**HANA trial se pausa por inactividad.** Si nadie la usa por unas horas, se apaga automáticamente. Verificar que está "Running" en el Cockpit de BTP antes de cada sesión de trabajo.

**El pipeline puede caerse.** Los organizadores monitorean la actividad en tiempo real. Verificar con `cf logs` al inicio de cada día. Si el pipeline se cayó, reiniciar con `cf restart`.

**Credenciales en el historial de Git.** Antes de hacer el repositorio público, verificar que ningún commit histórico contiene `.env` o el token. Comando: `git log --all -- .env`

**Video > 8 minutos = -3 puntos.** Cronometrar el guión antes de grabar. Apuntar a 5-6 minutos.

**El agente de IA es puntos extra, no obligatorio.** Si el tiempo no alcanza, priorizar dashboard funcional sin agente antes que agente a medias sin dashboard.

---

*Documento generado el 8 de Mayo 2026*  
*SAP AI Security Anomaly Detection Hackathon · TEC de Monterrey × SAP*  
*Actualizar conforme avance el trabajo del equipo*
