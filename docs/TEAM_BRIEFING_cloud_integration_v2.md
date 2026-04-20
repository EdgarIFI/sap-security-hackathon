# 📡 Cloud Integration Engineer — Team Briefing
### SAP AI Security Anomaly Detection Hackathon · TEC de Monterrey
### Versión 2 — actualizada con infraestructura real operativa

> **Autor:** Cloud Integration Engineer  
> **Propósito:** Poner a todo el equipo en el mismo nivel de contexto técnico, comunicar qué ya está funcionando, qué necesita cada rol de mí, y cómo vamos a trabajar juntos.  
> **Lectura obligatoria para:** AI/Data Science Specialist · Data Architect & Backend Developer · Security Analyst & Visualization Lead · Technical Project Manager & Scrum Master

---

## 1. De qué trata este hackathon — contexto base

Si aún no has leído la presentación oficial de SAP, este resumen te pone al día.

El reto se llama **SAP AI Security Anomaly Detection**. El objetivo es construir un **Security Operations Center (SOC) en vivo** para sistemas SAP — un sistema que, en tiempo real, ingesta logs de seguridad, detecta comportamientos anómalos con Inteligencia Artificial, y dispara alertas automáticas cuando detecta una amenaza.

El flujo completo del sistema sigue esta lógica:

```
OBSERVE → ANALYZE → DETECT → RESPOND
```

En términos concretos:

- **OBSERVE:** recibir y almacenar los logs de seguridad que genera SAP
- **ANALYZE:** explorar los datos, entender qué es comportamiento normal
- **DETECT:** aplicar modelos de ML para identificar anomalías
- **RESPOND:** enviar alertas automáticas y generar reportes forenses

Lo que estamos construyendo no es solo un modelo de IA — es un **pipeline de extremo a extremo** que conecta datos, inteligencia y acción. Cada rol del equipo cubre una parte de ese pipeline. Si alguna parte falla, todo falla.

---

## 2. La infraestructura que ya está operativa

Esta es la diferencia principal respecto a la versión anterior de este documento: **ya no estamos en teoría**. La infraestructura base ya fue configurada y probada.

### Lo que ya funciona hoy

**Repositorio de GitHub** — creado, estructurado y accesible para todo el equipo. Clónenlo antes de su primera sesión de trabajo.

**Conexión a la API del hackathon** — confirmada y funcionando:

| Prueba | Resultado |
|---|---|
| `GET /health` — servidor vivo | ✅ `{"status": "ok"}` |
| `GET /info` — autenticación Bearer token | ✅ 200 OK |
| `GET /logs/current` — extracción real de datos | ✅ 5,729 registros extraídos |

**Pipeline de ingesta (`app/ingest.py`)** — operativo. Extrae automáticamente todos los logs de la ventana actual de 30 minutos con paginación completa y los guarda en `data/raw/`.

**Muestra de datos (`data/raw/sample_10.csv`)** — disponible en el repo. Pueden usarla para comenzar a trabajar con la estructura de los datos sin necesitar acceso a la API.

---

## 3. La API real — lo que todos necesitan entender

La fuente de datos de este hackathon es una API REST que expone un stream continuo de logs de sistemas SAP. Aquí están los datos que ya conocemos y que afectan a todos los roles.

### Cómo funciona el tiempo

La API siempre sirve la **ventana UTC de 30 minutos actual**. No hay datos históricos disponibles — si no ingestas una ventana, esos datos se pierden para siempre. Por eso el pipeline debe correr de forma continua cada 30 minutos.

### Qué contienen los logs

Los logs tienen **dos categorías** con estructuras distintas:

**Logs de Sistema** — generados por los servicios SAP:
- Tipos: `INFO`, `WARNING`, `ERROR`, `DEBUG`, `AUDIT`, `PERF`, `SECURITY`
- Campos clave: `http_status_code`, `client_ip`, `service_id`

**Logs de Interacción LLM** — generados por modelos de lenguaje integrados en SAP:
- Tipos: `LLM_REQUEST`, `LLM_ERROR`, `LLM_TIMEOUT`
- Campos clave: `llm_model_id`, `llm_status`, `llm_cost_usd`, `llm_response_time_ms`

**Regla crítica que todos deben conocer:** cuando un log es de tipo Sistema, todas las columnas `llm_*` vienen vacías. Cuando es de tipo LLM, las columnas `service_id`, `http_status_code` y `client_ip` vienen vacías. Esto **no es un error de datos** — es el diseño de la API. Ningún rol debe intentar rellenar estos nulos.

### Dimensiones reales del dataset

```
Registros por ventana de 30 min:  ~5,729 (puede variar)
Páginas por ventana:               12
Registros por página:              500 (fijo por el servidor)
```

---

## 4. Arquitectura del sistema

Esta es la arquitectura que estamos construyendo, basada en el diseño oficial del hackathon:

```
┌─────────────────────────────────────────────────────┐
│              NUESTRO SISTEMA (Team X)               │
│                                                     │
│  ┌──────────────┐      ┌──────────────────────┐    │
│  │  ML Module   │◄─────│    ETL Pipeline      │    │
│  │              │      │   (ingest + clean)   │    │
│  │ Detección de │      └──────────┬───────────┘    │
│  │  anomalías   │                 │                 │
│  └──────┬───────┘      ┌──────────▼───────────┐    │
│         │              │    Data Storage       │    │
│         │              │    (SAP HANA)         │    │
│  ┌──────▼───────┐      └──────────────────────┘    │
│  │  Dashboards  │                                   │
│  │  (Streamlit  │      ┌──────────────────────┐    │
│  │   + SAC)     │      │   Alerting System    │    │
│  └──────────────┘      │   (Webhook → SAP)    │    │
│                        └──────────────────────┘    │
└─────────────────────────────────────────────────────┘
              ▲                        │
              │ API + Bearer Token     │ Team Webhook
              │                        ▼
        ┌─────┴──────────────────────────────┐
        │              SAP                   │
        │  Data Centers · AI Security Team  │
        │         Clusters                   │
        └────────────────────────────────────┘
                          ▲
                     Attackers (simulados)
```

### Stack tecnológico oficial

| Capa | Tecnología |
|---|---|
| Ingesta / ETL | Python · `requests` · `pandas` |
| ML / Detección | `scikit-learn` · `Keras` · NumPy |
| Almacenamiento | SAP HANA (formal) · CSV/Parquet (desarrollo) |
| Despliegue | SAP BTP · Cloud Foundry |
| Visualización rápida | Streamlit |
| Visualización ejecutiva | SAP Analytics Cloud (SAC) |
| Alerting | Webhook REST (POST) |

---

## 5. Los cinco roles — qué hace cada uno

### Rol 1 — AI & Data Science Specialist
Diseña y entrena los modelos de detección de anomalías. Trabaja sobre los datos limpios que produce el ETL pipeline. Su entregable central es un modelo capaz de distinguir comportamiento normal de amenazas reales, tanto en volumen (Count Anomaly) como en patrones de mensaje (Categorization Anomaly).

### Rol 2 — Cloud Integration Engineer (yo)
Orquesta toda la infraestructura de integración. Soy el responsable de que los datos fluyan desde la API de SAP hasta el modelo, y de que las alertas lleguen al webhook de SAP cuando el modelo detecta algo. Sin mi pieza, ningún otro rol tiene datos con qué trabajar.

### Rol 3 — Data Architect & Backend Developer
Diseña y gestiona el almacenamiento en SAP HANA. Define el schema de las tablas, optimiza las queries para alto volumen, y asegura que los datos persistan correctamente para que el modelo y los dashboards puedan consultarlos.

### Rol 4 — Security Analyst & Visualization Lead
Construye los dashboards en Streamlit y SAP Analytics Cloud. Traduce los resultados técnicos del modelo en insights accionables para una audiencia ejecutiva. También genera el reporte forense final del hackathon.

### Rol 5 — Technical Project Manager & Scrum Master
Coordina al equipo, gestiona el backlog, asegura que los hitos se cumplan en tiempo, y es el enlace entre el equipo técnico y los evaluadores de SAP.

---

## 6. Contratos de interfaz — qué necesita cada rol de mí y qué necesito yo

Esta sección es la más importante para el trabajo diario. Define las dependencias concretas entre roles.

### Lo que entrego al AI & Data Science Specialist

- DataFrame o CSV con logs ya extraídos y parseados, listos para feature engineering
- Schema de columnas documentado (nombres, tipos, distribución de valores)
- Script `ingest.py` funcional que pueden importar o llamar directamente
- Muestra de datos (`sample_10.csv`) disponible en el repo desde hoy

**Lo que necesito de ti:**
- Avisarme si el modelo necesita features adicionales que no estén en el ETL actual
- Informarme el volumen de datos que necesitas por ciclo para ajustar el pipeline

### Lo que entrego al Data Architect & Backend Developer

- Pipeline de ingesta que alimentará HANA de forma continua cada 30 minutos
- Definición de las estructuras de datos que voy a escribir (coordinamos el schema)
- Configuración de las service instances en BTP que tú vas a consumir

**Lo que necesito de ti:**
- El connection string de HANA una vez configurado
- Confirmación del schema de tablas para que mi pipeline escriba en el formato correcto
- Decirme si necesitas batch inserts o puedo escribir registro a registro

### Lo que entrego al Security Analyst & Visualization Lead

- Webhook configurado y probado (el canal por donde llegan las alertas a tu dashboard)
- Modelo de datos en HANA que tu SAP Analytics Cloud va a consultar
- Campos de anomalías detectadas documentados para que diseñes los dashboards

**Lo que necesito de ti:**
- La URL del webhook cuando esté disponible (deadline: Abr 27)
- El payload de alerta que necesitas recibir para que lo diseñe correctamente

### Lo que reporto al Technical Project Manager & Scrum Master

Estado de cada integración en formato claro: pendiente / en progreso / listo. Bloqueos comunicados inmediatamente cuando aparecen, no cuando ya son un problema.

**Lo que necesito de ti:**
- Que las dependencias de integración estén reflejadas en el backlog como bloqueantes reales
- Que ningún rol marque su tarea como "done" si depende de una integración que yo no he liberado

---

## 7. Protocolo de trabajo en equipo

### Regla absoluta — credenciales

**Nunca** pongan el Bearer token, passwords, ni ninguna credencial en el código. El hackathon tiene una penalización explícita: si hay un Data Breach o las credenciales son comprometidas, el equipo queda **bloqueado para el día siguiente**. Usamos un archivo `.env` local que nunca se sube a GitHub.

### Cómo clonar el repositorio y empezar

```bash
# 1. Clonar el repo
git clone https://github.com/[usuario]/sap-security-hackathon.git
cd sap-security-hackathon

# 2. Crear entorno virtual
python -m venv .venv
source .venv/bin/activate      # Git Bash / Mac / Linux

# 3. Instalar dependencias
pip install -r requirements.txt

# 4. Crear tu .env local (nunca lo compartas ni lo subas)
# Pide el Bearer token al Cloud Integration Engineer
```

### Estructura del repositorio

```
/
├── app/
│   ├── config.py       ← variables de entorno y autenticación
│   ├── ingest.py       ← extracción de datos ✅ LISTO
│   ├── etl.py          ← limpieza y transformación
│   ├── model.py        ← detección ML
│   ├── alerting.py     ← webhook dispatch
│   └── main.py         ← dashboard Streamlit
├── data/
│   ├── raw/            ← datos crudos (no modificar)
│   │   └── sample_10.csv  ← muestra disponible hoy ✅
│   └── processed/      ← datos limpios para el modelo
├── docs/               ← documentación del equipo
├── notebooks/          ← exploración y EDA
├── tests/
├── .env.example        ← plantilla de variables (pedir valores reales al CIE)
├── requirements.txt
└── README.md
```

### Flujo diario de trabajo con Git

```bash
git pull                              # siempre primero, antes de trabajar
# ... hacer cambios ...
git add .
git commit -m "descripción del cambio"
git push origin develop
```

---

## 8. Cronograma de hitos

| Fecha | Hito | Estado |
|---|---|---|
| Abr 6 | Kick Off + Learning Materials | ✅ Completado |
| **Abr 13** | **API & Data Access** | **✅ Operativo** |
| **Abr 20** | Pipeline de ingesta funcionando | **✅ Completado hoy** |
| Abr 27 | Alerting Webhook | ⏳ Pendiente |
| May 4 | Go Live en BTP/Cloud Foundry | ⏳ Pendiente |
| May 12–14 | Primera Fase Eliminatoria | ⏳ Pendiente |
| May 15 | Anuncio de ganadores fase 1 | ⏳ Pendiente |
| May 21 | Final Phase (First to Go Down) | ⏳ Pendiente |

---

## 9. Próximos pasos inmediatos para cada rol

**AI & Data Science Specialist**
- Clonar el repo y revisar `data/raw/sample_10.csv`
- Explorar la estructura de los datos y proponer features para el modelo
- Coordinar conmigo qué columnas adicionales necesitas en el ETL

**Data Architect & Backend Developer**
- Clonar el repo y revisar la estructura de datos en `sample_10.csv`
- Proponer schema de tablas en HANA para logs de sistema y logs LLM por separado
- Configurar SAP HANA y compartirme el connection string

**Security Analyst & Visualization Lead**
- Clonar el repo y revisar `sample_10.csv` para empezar a diseñar los dashboards
- Definir qué campos quieres en el payload de alerta
- Empezar el diseño visual en Streamlit con datos de muestra

**Technical Project Manager & Scrum Master**
- Clonar el repo y revisar este documento
- Organizar el backlog reflejando las dependencias entre roles
- Confirmar que todos los integrantes tienen acceso al repo y al Bearer token

---

## 10. Mi compromiso con el equipo

Como Cloud Integration Engineer me comprometo a:

1. **No bloquear a nadie** — mis entregas son habilitadoras, no obstáculos
2. **Documentar cada interfaz** — si produzco algo que otro consume, lo documento
3. **Priorizar estabilidad** — prefiero una integración simple y robusta sobre una compleja y frágil
4. **Comunicar bloqueos a tiempo** — si algo no va a estar listo, lo digo antes, no después
5. **Gestionar credenciales con disciplina** — cero secretos en el repo, sin excepciones

---

*Versión 2 — actualizada con infraestructura real operativa al 20 de Abril 2026.*  
*Cualquier cambio de arquitectura que afecte interfaces debe comunicarse al equipo antes de implementarse.*
