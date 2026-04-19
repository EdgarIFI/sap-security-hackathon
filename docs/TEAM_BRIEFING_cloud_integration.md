# 📡 Cloud Integration Engineer — Team Briefing
### SAP AI Security Anomaly Detection Hackathon · TEC de Monterrey

> **Autor del documento:** Cloud Integration Engineer  
> **Propósito:** Comunicar al equipo completo la arquitectura de integración, los contratos de interfaz entre roles, y las dependencias críticas que afectan a todos.  
> **Lectura obligatoria para:** AI/Data Science Specialist · Data Architect & Backend Developer · Security Analyst & Visualization Lead · Technical Project Manager & Scrum Master

---

## 1. Contexto: por qué este documento existe

Este hackathon no es solo un reto de Machine Learning. Es un reto de **sistema integrado de extremo a extremo**. El flujo completo es:

```
SAP Data Centers
      │
      │  API Auth Key + Data Batch
      ▼
  ETL Pipeline  ◄──────────────────────────── [MI RESPONSABILIDAD PRINCIPAL]
      │
      ▼
 Data Storage (SAP HANA)  ◄─────────────────  [COMPARTIDA CON DATA ARCHITECT]
      │
      ├──► ML Module (Anomaly Detection)  ◄──  [AI/DATA SCIENCE SPECIALIST]
      │         │
      │         │ Anomalías detectadas
      ▼         ▼
  Dashboards ◄──────────────────────────────  [SECURITY ANALYST / VIZ LEAD]
      │
      ▼
 Alerting System ──► Alerting (Webhook) ─────  [YO LO CONECTO; TODOS LO USAN]
                          │
                          ▼
                  SAP AI Security Team
```

**Ningún componente funciona aislado.** Mi trabajo de integración es el tejido conectivo de toda la solución. Si el ETL falla, el modelo no tiene datos. Si las APIs no están configuradas, nadie puede conectar nada. Si el webhook no está operativo, el criterio de evaluación #1 (MTTD + Alert Latency, 40% de la nota) se va a cero.

---

## 2. Mi rol: qué hago yo exactamente

Según la definición oficial del hackathon, el **Cloud Integration Engineer** es responsable de:

- **Orquestación en SAP BTP** — configurar y gestionar el entorno cloud central
- **Despliegue en Cloud Foundry** — hacer que los microservicios corran en producción
- **Gestión de APIs** — construir y consumir las RESTful APIs para ingesta de datos y despacho de alertas
- **Conectividad seamless** — asegurar que los modelos de AI y los servicios SAP estén enlazados sin interrupciones
- **Automatización del alerting flow** — que la detección dispare la alerta sin intervención manual

En términos prácticos: **yo soy el cuello de botella positivo del sistema** (glup). Sin esta pieza, las demás no se pueden conectar. Asi que intentaré enfocarme para no afectar o bloquear el desarrollo de las tareas de los demas integrantes del equipo.

---

## 3. Arquitectura objetivo del sistema

La presentación de SAP define la arquitectura esperada así:

```
┌─────────────────────────────────────────────────┐
│              HIGH MICROSERVICE LEVEL             │
│                                                 │
│  ┌─────────────────┐    ┌──────────────────┐   │
│  │   ML Module      │◄───│   ETL Pipeline   │   │
│  │                 │    │                  │   │
│  │ Model Versioning│    └────────┬─────────┘   │
│  │ Retrain Pipeline│             │              │
│  │ Obs Capabilities│    ┌────────▼─────────┐   │
│  │ Integrations    │    │   Data Storage   │   │
│  └────────┬────────┘    └──────────────────┘   │
│           │                                     │
│  ┌────────▼────────┐   ┌────────────────────┐  │
│  │   Dashboards    │   │  Alerting System   │  │
│  └─────────────────┘   └────────┬───────────┘  │
│                                 │              │
└─────────────────────────────────┼──────────────┘
                                  │ Team Webhook
                    ┌─────────────▼──────────────┐
                    │         SAP                │
                    │  Data Centers │ AI Sec Team │
                    │         Clusters           │
                    └────────────────────────────┘
                                  ▲
                            Attackers (simulados)
```

**Stack tecnológico oficial del hackathon:**

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

## 4. Contratos de interfaz: lo que cada rol necesita de mí

Esta es la parte más importante de este documento para el equipo. Cada uno de ustedes depende de entregas concretas de mi parte. Las especifico aquí para que todos sepamos qué esperar y cuándo.

### 4.1 Para el AI/Data Science Specialist

**Qué necesitas de mí:**
- Un DataFrame o CSV limpio con los logs ya extraídos y parseados
- Esquema de columnas documentado (nombres, tipos, rangos de valores observados)
- Script `ingest.py` funcional que puedan llamar o importar
- Acceso al endpoint de la API de SAP configurado y probado

**Formato de entrega esperado:**
```python
# Lo que les entregaré como mínimo:
df.columns  # → ['timestamp', 'source_ip', 'port_service', 
            #     'event_description', 'status', 'log_type']
df.dtypes   # → timestamp: datetime64, source_ip: object, ...
df.shape    # → (N_rows, 6)
```

**Lo que yo necesito de ustedes:**
- Decirme qué features adicionales necesita el modelo (para incluirlas en el ETL)
- Avisarme si el volumen de datos en producción requiere batching especial

### 4.2 Para el Data Architect & Backend Developer

**Qué necesitas de mí:**
- Definición de las tablas que voy a poblar en HANA (acordamos el schema juntos)
- El pipeline de ingesta que alimenta HANA de forma continua
- Configuración de las service instances en BTP que tú vas a consumir

**Lo que yo necesito de ustedes:**
- El connection string de HANA una vez que lo tengan configurado
- Confirmarme el schema de tablas para que mi pipeline escriba en el formato correcto
- Decirme si necesitan batch inserts o streaming

### 4.3 Para el Security Analyst & Visualization Lead

**Qué necesitas de mí:**
- El webhook configurado y probado (ese es el canal por donde llegan las alertas a tu dashboard)
- El modelo de datos en HANA que tu SAP Analytics Cloud va a consumir
- Los campos de anomalías detectadas que el ML Module produce, documentados

**Lo que yo necesito de ustedes:**
- La URL del webhook que van a usar (o yo la configuro si me la piden)
- Decirme qué campos quieren en el payload de alerta para que lo diseñe bien

### 4.4 Para el Technical Project Manager & Scrum Master

**Lo que me comprometo a reportarte:**
- Estado de cada integración: `[ ] pendiente → [~] en progreso → [✓] listo`
- Bloqueos inmediatamente cuando los detecte
- Estimados realistas, no optimistas

**Lo que necesito de ti:**
- Que el backlog refleje las dependencias de integración como bloqueantes reales
- Que ningún rol marque su tarea como "done" si depende de una integración que yo no he liberado aún

---

## 5. Protocolo de trabajo en equipo

### Variables de entorno y secretos
**Nunca** pongan API keys, passwords ni tokens en el código. Usamos un archivo `.env` local (no commiteado a Git):

```
API_BASE_URL=https://...
API_KEY=...
WEBHOOK_URL=https://...
HANA_HOST=...
HANA_PORT=...
HANA_USER=...
HANA_PASSWORD=...
```

El archivo `.env.example` sí va en el repo (con valores vacíos) para que todos sepan qué variables configurar.

### Estructura del repositorio acordada
```
/
├── app/
│   ├── config.py       ← variables de entorno
│   ├── ingest.py       ← extracción de datos (MI RESPONSABILIDAD)
│   ├── etl.py          ← limpieza y transformación (COMPARTIDA)
│   ├── model.py        ← detección ML (AI SPECIALIST)
│   ├── alerting.py     ← webhook dispatch (MI RESPONSABILIDAD)
│   └── main.py         ← dashboard Streamlit (VIZ LEAD)
├── data/
│   ├── raw/            ← datos crudos sin modificar (NUNCA borrar)
│   └── processed/      ← datos limpios para el modelo
├── notebooks/          ← exploración y EDA
├── tests/              ← pruebas básicas
├── .env.example        ← plantilla de variables (SÍ va en Git)
├── .env                ← valores reales (NUNCA en Git)
├── .gitignore
├── requirements.txt
└── README.md
```

### Gitignore mínimo obligatorio
```
.env
data/raw/
data/processed/
__pycache__/
*.pyc
.venv/
```

### Política de branches
- `main` → código estable, demo-ready
- `develop` → integración continua
- `feature/[nombre]` → trabajo individual

---

## 6. Cronograma de hitos del hackathon

| Fecha | Hito | Qué necesito tener listo yo |
|---|---|---|
| Abr 6 | Kick Off + Learning Materials | Entender arquitectura, decidir estructura de repo |
| **Abr 13** | **API & Data Access** | **ETL básico funcional, primera extracción real documentada** |
| **Abr 27** | **Alerting Webhook** | **Webhook configurado, prueba de alerta exitosa** |
| **May 4** | **Go Live** | **Pipeline completo corriendo en BTP/Cloud Foundry** |
| May 12–14 | Primera Fase Eliminatoria | Sistema estable, MTTD medible |
| May 15 | Anuncio de ganadores fase 1 | — |
| May 21 | Final Phase (First to Go Down) | Máxima automatización, demo impecable |

**Atención crítica:** Hay una regla de seguridad del hackathon: si un equipo sufre un **Data Breach** o sus **Credentials son Deprecated**, quedan bloqueados para el siguiente día. La gestión segura de secretos es mi responsabilidad directa.

---

## 7. Señales de alerta que debo reportar de inmediato

Si alguno de ustedes ve alguna de estas situaciones, avísenme sin demora:

- `401 / 403` en llamadas a la API → credenciales comprometidas o expiradas
- `429` repetido → estamos superando el rate limit, necesito agregar backoff
- El modelo no recibe datos frescos → el pipeline de ingesta se rompió
- El webhook no dispara → la integración de alerting está caída
- Datos inconsistentes o columnas inesperadas → el schema cambió, necesito adaptar el ETL

---

## 8. Mi compromiso con el equipo

Como Cloud Integration Engineer, me comprometo a:

1. **No bloquear a nadie** — mis entregas de integración son habilitadoras, no obstáculos
2. **Documentar cada interfaz** — si produzco algo que otro va a consumir, lo documento
3. **Priorizar estabilidad** — prefiero una integración robusta y simple a una compleja y frágil
4. **Comunicar bloqueos a tiempo** — si algo no va a estar listo, lo digo antes, no después
5. **Gestionar secretos con disciplina** — cero credenciales en el repo, sin excepciones

---

*Última actualización: inicio del hackathon*  
*Cualquier cambio de arquitectura que afecte interfaces debe ser comunicado a todo el equipo antes de implementarse.*
