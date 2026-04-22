# Actualización de Avance — Cloud Foundry Deploy
## SAP AI Security Anomaly Detection Hackathon
**Fecha:** 22 de Abril 2026  
**Autor:** Cloud Integration Engineer  
**Branch:** cf-deploy *(o el nombre de tu branch)*

---

## Qué se hizo en esta sesión

### Fase 1 — HANA local ✅ COMPLETADA

Se verificó y corrigió la conexión a SAP HANA Cloud desde la máquina local.

**Correcciones aplicadas a `app/hana_client.py`:**
- Se eliminó el parámetro `sslHostNameInCertificate` que causaba error de SSL con instancias `hna1.prod` (el error era: *"Se especificaron marcas no válidas"*)
- Se reemplazó `CREATE TABLE IF NOT EXISTS` por verificación manual contra el catálogo del sistema (`SELECT COUNT(*) FROM TABLES WHERE TABLE_NAME = ?`) porque la sintaxis `IF NOT EXISTS` no es compatible con la versión de HANA Cloud del trial

**Resultado:** pipeline corriendo localmente con ingesta completa a HANA:
- Conexión establecida a instancia `hna1.prod`
- Tablas `RAW_LOGS_SISTEMA` y `RAW_LOGS_LLM` creadas exitosamente
- Datos insertados correctamente en ambas tablas vía `pipeline_loop.py`

---

### Fase 2 — Preparación para Cloud Foundry ✅ COMPLETADA

Se adaptó el proyecto para poder desplegarse en SAP BTP Cloud Foundry.

**Archivos nuevos:**

`server.py` — entry point Flask que en CF reemplaza el trigger del loop de `pipeline_loop.py`. Expone dos endpoints:
- `GET /health` → liveness probe para CF y el Job Scheduler
- `POST /run` → ejecuta un ciclo completo del pipeline (protegido con token)

`runtime.txt` — especifica la versión de Python para el buildpack de CF:
```
python-3.11.x
```

**Archivos modificados:**

`app/config.py` — se agregó lectura dual de credenciales HANA:
- En local: lee de `.env` como siempre
- En CF: lee de `VCAP_SERVICES` (inyectado automáticamente por el Service Binding)
- Se agregó `JOB_SECRET_TOKEN` para proteger el endpoint `/run`

`requirements.txt` — se agregó `Flask`

**Sin cambios:**
- `pipeline_loop.py` — sigue igual, útil para desarrollo local
- `app/hana_client.py` — las correcciones de esta sesión no se hicieron en esta branch

**Pruebas realizadas localmente:**

| Prueba | Resultado |
|---|---|
| `GET /health` | ✅ 200 `{"status":"running"}` |
| `POST /run` sin token | ✅ 401 `{"error":"unauthorized"}` |
| `POST /run` con token falso | ✅ 401 `{"error":"unauthorized"}` |
| `POST /run` con token correcto | ✅ 200 Pipeline completo — datos en HANA |

---

## Arquitectura actual del sistema

```
EN LOCAL (funcionando):

  python pipeline_loop.py   ←  útil para desarrollo y pruebas locales
  python server.py          ←  nuevo entry point, probado localmente

EN CF (siguiente paso):

  Job Scheduler (BTP)
    │  HTTP POST → /run  cada 30 min
    ▼
  server.py (Cloud Foundry)
    │  llama ingest_and_persist()
    ├──► data/raw/logs_[timestamp].csv
    └──► SAP HANA Cloud
           ├── RAW_LOGS_SISTEMA
           └── RAW_LOGS_LLM
```

---

## Cómo se manejan las credenciales en CF

| Credencial | Origen en CF | Cómo se configura |
|---|---|---|
| HANA host/port/user/password | `VCAP_SERVICES` | Automático via Service Binding en `manifest.yml` |
| `BEARER_TOKEN` | Variable de entorno CF | `cf set-env` manual |
| `API_BASE_URL` | Variable de entorno CF | `cf set-env` manual |
| `JOB_SECRET_TOKEN` | Variable de entorno CF | `cf set-env` manual |

**Ninguna credencial toca el repositorio.** El `.env` sigue en `.gitignore`.

---

## Lo que falta — Fase 3: Deploy a Cloud Foundry

### Prerequisitos
- [ ] CF CLI instalado (`brew install cloudfoundry/tap/cf-cli` en Mac, o descarga para Windows)
- [ ] Verificar que `cf target` muestra tu org y space correctos

### Pasos en orden

```bash
# 1. Crear el Job Scheduling Service
cf create-service jobscheduler lite soc-scheduler

# 2. Verificar que tus servicios existen
cf services
# Deben aparecer: tu instancia HANA + soc-scheduler

# 3. Actualizar manifest.yml con los service bindings
#    (ver sección de manifest más abajo)

# 4. Deploy
cf push

# 5. Configurar secretos — NUNCA van en el código ni en manifest.yml
cf set-env sap-ai-soc BEARER_TOKEN "tu-token-real"
cf set-env sap-ai-soc API_BASE_URL "https://sap-api-xx.xxxxxx.xyz"
cf set-env sap-ai-soc JOB_SECRET_TOKEN "tu-token-generado"
cf restage sap-ai-soc

# 6. Verificar que arrancó
cf logs sap-ai-soc --recent

# 7. Probar manualmente
curl -X POST https://[url-de-tu-app]/run \
     -H "X-Job-Token: tu-token"

# 8. Crear el job en el Scheduler Dashboard (BTP Cockpit)
#    Name:     soc-ingest-job
#    URL:      https://[url-de-tu-app]/run
#    Method:   POST
#    Header:   X-Job-Token = tu-token
#    Schedule: */30 * * * *
```

### `manifest.yml` — actualizar antes del cf push

El `manifest.yml` actual tiene `command: python pipeline_loop.py`. Debe quedar así:

```yaml
---
applications:
  - name: sap-ai-soc
    random-route: true
    path: ./
    memory: 512M
    disk_quota: 1G
    instances: 1
    buildpacks:
      - python_buildpack
    command: python server.py
    health-check-type: http
    health-check-http-endpoint: /health
    services:
      - nombre-exacto-de-tu-instancia-hana   # verificar con: cf services
      - soc-scheduler
    env:
      # Solo configuración no sensible aquí
      # Los secretos van con cf set-env — NUNCA en este archivo
```

---

## Pendiente de otros roles

**AI Specialist:**
- `app/model.py` — modelo de detección de anomalías
- Feature engineering sobre `clean_sistema.csv` y `clean_llm.csv`
- Cuando esté listo se integra en `server.py` como segundo paso después de `ingest_and_persist()`

**Viz Lead:**
- `app/main.py` — dashboard Streamlit
- Dashboard en SAP Analytics Cloud conectado a `RAW_LOGS_SISTEMA` y `RAW_LOGS_LLM`

**Data Architect:**
- Verificar schema de tablas HANA
- Crear índices para optimizar queries del modelo ML:
```sql
CREATE INDEX IDX_LOGS_TIMESTAMP ON RAW_LOGS_SISTEMA (event_timestamp);
CREATE INDEX IDX_LOGS_TYPE      ON RAW_LOGS_SISTEMA (log_type);
```

**Todos:**
- Alerting webhook URL llega el **27 de Abril** — `alerting.py` se implementa ese día
- Go Live en CF deadline: **4 de Mayo**

---

## Notas importantes para el equipo

**`pipeline_loop.py` no se modificó** — sigue funcionando igual para pruebas locales. En CF el trigger es el Job Scheduler, no este script.

**HANA trial se pausa por inactividad** — antes de cualquier prueba verificar en BTP Cockpit que la instancia esté en estado *Running*.

**El `.env` nunca va a Git** — verificar siempre con `git status` antes de `git push`. Si aparece en verde, detener el push inmediatamente.

---

*Actualización generada el 22 de Abril 2026*