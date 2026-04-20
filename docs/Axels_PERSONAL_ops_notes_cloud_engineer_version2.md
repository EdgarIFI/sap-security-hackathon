# 🔧 Notas de Operación — Cloud Integration Engineer
### SAP AI Security Anomaly Detection · Uso interno personal

> **Propósito:** Guía de acción propia para los primeros momentos de la competencia.  
> **Audiencia:** Solo yo.  
> **Filosofía:** Primero que funcione local. Luego que funcione en la nube.  
> **Versión:** 2 — actualizada con datos reales de la API del hackathon.

---

## 🚨 ACCIÓN INMEDIATA — HACER AHORA MISMO

Tengo la URL de la API y el Bearer token. El repositorio está creado. No hay excusa para no empezar. Estas son las tres cosas que debo completar **antes de cualquier otra cosa**:

```
[ ] 1. Poner API_BASE_URL y BEARER_TOKEN en mi .env local
[ ] 2. Ejecutar prueba de conectividad contra GET /health (sin auth)
[ ] 3. Ejecutar primera llamada real a GET /info con Bearer token
         → confirmar que recibo batch_size, total_records, total_pages
```

Si esos tres pasos producen respuestas 200, el acceso está confirmado y puedo avanzar al pipeline completo.

---

## ESTADO DE HITOS

```
[x] Repositorio creado y estructura subida a GitHub
[x] URL de la API confirmada
[x] Bearer token (API Key) recibido
[ ] Primera extracción real documentada    → URGENTE, hacer hoy
[ ] data/SCHEMA.md creado y compartido    → URGENTE, hacer hoy
[ ] Webhook de alerting configurado        → deadline: Abr 27
[ ] Pipeline en BTP/Cloud Foundry         → deadline: May 4
[ ] Sistema estable para eliminatoria     → deadline: May 12
```

---

## REFERENCIA CRÍTICA — La API real del hackathon

> Esta sección reemplaza cualquier suposición previa. Estos son los datos confirmados.

### Endpoints disponibles

| Endpoint | Método | Auth | Propósito |
|---|---|---|---|
| `/health` | GET | ❌ No requerida | Verificar que el servidor vive (liveness probe) |
| `/info` | GET | ✅ Bearer token | Obtener batch_size, total_pages antes del loop |
| `/logs/current` | GET | ✅ Bearer token | Traer los logs de la ventana actual (paginado) |

### Autenticación — Bearer token (NO x-api-key)

```python
headers = {
    "Authorization": f"Bearer {BEARER_TOKEN}"
}
```

> ⚠️ **Corrección importante respecto a la versión anterior:** El header correcto es
> `Authorization: Bearer ...`, NO `x-api-key`. Cualquier código que use `x-api-key`
> recibirá un `401` y no funcionará.

### Ventana de tiempo — cómo funciona

La API siempre sirve la **ventana UTC de 30 minutos actual**. No puedes pedir datos históricos.

| Minuto UTC del servidor | Ventana que sirve |
|---|---|
| 00 – 29 | HH:00:00 → HH:30:00 |
| 30 – 59 | HH:30:00 → HH+1:00:00 |

El pipeline debe hacer polling **cada 30 minutos** para no perder ventanas.

### Estructura de respuesta de `/info`

```json
{
  "batch_size": 500,
  "window_start": "2026-03-18T12:00:00+00:00",
  "window_end": "2026-03-18T12:30:00+00:00",
  "total_records": 54832,
  "total_pages": 110
}
```

### Estructura de respuesta de `/logs/current`

```json
{
  "request_time_utc": "2026-03-18T12:17:43.521042+00:00",
  "window_start": "2026-03-18T12:00:00+00:00",
  "window_end": "2026-03-18T12:30:00+00:00",
  "total_records": 54832,
  "batch_size": 500,
  "current_page": 1,
  "total_pages": 110,
  "records_in_page": 500,
  "data": [ ... ]
}
```

Los registros reales vienen en `data[]`. El tamaño de página (500) lo controla el servidor — yo no puedo cambiarlo.

### Schema real de los logs — dos tipos de registro

Los logs tienen dos categorías con campos distintos. **El patrón de nulos es por diseño, no un error.**

**Tipo Sistema** — `sap_function_log_type`: `INFO`, `WARNING`, `ERROR`, `DEBUG`, `AUDIT`, `PERF`, `SECURITY`
- Campos llenos: `http_status_code`, `client_ip`, `service_id`
- Campos vacíos: todos los `llm_*`

**Tipo LLM** — `sap_function_log_type`: `LLM_REQUEST`, `LLM_ERROR`, `LLM_TIMEOUT`
- Campos llenos: `llm_model_id`, `llm_status`, `llm_cost_usd`, `llm_response_time_ms`
- Campos vacíos: `service_id`, `http_status_code`, `client_ip`

> Mi ETL **no debe intentar rellenar estos nulos**. Debe manejarlos por separado según el tipo de log.

### Códigos de error que debo conocer

| Código | Significado | Acción |
|---|---|---|
| `200` | OK | Continuar |
| `401` | Bearer token faltante o inválido | Revisar `.env` y header |
| `422` | Página fuera de rango | Revisar que `page <= total_pages` |
| `503` | Datos no cargados aún | Esperar y reintentar; el servidor está arrancando |

---

## BLOQUE 0 — Antes de tocar código

```
¿Tengo la URL base de la API?             [x] SÍ
¿Tengo el Bearer token?                   [x] SÍ
¿Tengo ejemplo del payload de respuesta?  [x] SÍ  (documentado arriba)
¿Tengo URL del webhook de alerting?       [ ] NO  → puede esperar hasta Abr 27
¿Tengo acceso al repositorio del equipo?  [x] SÍ  → ya creado
¿Tenemos HANA connection string?          [ ] NO  → puede esperar
```

**Regla crítica:** No guardar ninguna credencial en archivos de código. Todo va en `.env`.

---

## BLOQUE 1 — Setup del entorno local

> Si el repo ya está clonado y la estructura ya existe, saltar directo al Bloque 2.

### 1.1 Clonar el repo y entrar

```bash
git clone https://github.com/[usuario]/sap-security-hackathon.git
cd sap-security-hackathon
```

### 1.2 `.env.example` — esto SÍ va en Git

```
API_BASE_URL=
BEARER_TOKEN=
WEBHOOK_URL=
HANA_HOST=
HANA_PORT=
HANA_USER=
HANA_PASSWORD=
```

### 1.3 `.env` — esto NUNCA va en Git

```
API_BASE_URL=https://[url-real-de-la-api]
BEARER_TOKEN=[token-real-del-equipo]
WEBHOOK_URL=https://[rellenar-cuando-tenga]
```

### 1.4 Verificar que `.env` no está en Git

```bash
git status
# .env NO debe aparecer en la lista
# Si aparece, hay un problema con .gitignore — resolver antes de continuar
```

### 1.5 Entorno virtual y dependencias

```bash
python -m venv .venv
source .venv/bin/activate        # Git Bash / Linux / Mac

pip install pandas requests streamlit scikit-learn \
            python-dotenv matplotlib numpy
pip freeze > requirements.txt
```

### 1.6 `app/config.py` — actualizado con Bearer token

```python
import os
from dotenv import load_dotenv

load_dotenv()

API_BASE_URL  = os.getenv("API_BASE_URL")
BEARER_TOKEN  = os.getenv("BEARER_TOKEN")
WEBHOOK_URL   = os.getenv("WEBHOOK_URL")

def validate_config():
    missing = [k for k, v in {
        "API_BASE_URL": API_BASE_URL,
        "BEARER_TOKEN": BEARER_TOKEN,
    }.items() if not v]
    if missing:
        raise EnvironmentError(f"Variables de entorno faltantes: {missing}")

def get_headers():
    """Devuelve el header de autenticación listo para usar en cualquier request."""
    return {"Authorization": f"Bearer {BEARER_TOKEN}"}
```

---

## BLOQUE 2 — Primera conexión a la API ⚡ HACER AHORA

### 2.1 Prueba de liveness — sin autenticación

Esto verifica que el servidor está vivo antes de intentar nada con el token.

```python
# prueba_health.py
import requests
import os
from dotenv import load_dotenv
load_dotenv()

r = requests.get(f"{os.getenv('API_BASE_URL')}/health", timeout=10)
print("Status:", r.status_code)
print("Body:", r.json())
# Esperar: {"status": "ok"}
```

```bash
python prueba_health.py
```

### 2.2 Prueba de autenticación — GET /info

Esto verifica que el Bearer token funciona y me da el tamaño real del dataset.

```python
# prueba_info.py
import requests
import os
from dotenv import load_dotenv
load_dotenv()

headers = {"Authorization": f"Bearer {os.getenv('BEARER_TOKEN')}"}
r = requests.get(f"{os.getenv('API_BASE_URL')}/info", headers=headers, timeout=10)
print("Status:", r.status_code)
print("Body:", r.json())
# Esperar: batch_size, window_start, window_end, total_records, total_pages
```

```bash
python prueba_info.py
```

> Si esto devuelve `401`, el token está mal. Verificar el `.env` y el formato del header.  
> Si devuelve `503`, el servidor está arrancando. Esperar 1-2 minutos y reintentar.

### 2.3 `app/ingest.py` — versión real con paginación completa

Este es el script central de mi rol. Implementa el loop de paginación correcto según la documentación oficial de la API.

```python
import requests
import pandas as pd
import time
from config import API_BASE_URL, get_headers

def fetch_current_window() -> pd.DataFrame:
    """
    Extrae todos los logs de la ventana UTC actual de 30 minutos.
    Maneja paginación automáticamente: llama /info primero,
    luego itera /logs/current?page=1..N hasta traer todo.
    """
    headers = get_headers()

    # Paso 1: descubrir cuántas páginas hay en esta ventana
    info = requests.get(
        f"{API_BASE_URL}/info",
        headers=headers,
        timeout=15
    )
    info.raise_for_status()
    meta = info.json()

    total_pages   = meta["total_pages"]
    total_records = meta["total_records"]
    window_start  = meta["window_start"]
    window_end    = meta["window_end"]

    print(f"Ventana: {window_start} → {window_end}")
    print(f"Total registros: {total_records} | Páginas: {total_pages}")

    # Paso 2: iterar todas las páginas y acumular registros
    all_records = []

    for page in range(1, total_pages + 1):
        print(f"  Fetching page {page}/{total_pages}...", end=" ")

        r = requests.get(
            f"{API_BASE_URL}/logs/current",
            headers=headers,
            params={"page": page},
            timeout=30
        )

        if r.status_code == 422:
            print("fuera de rango, terminando loop.")
            break

        r.raise_for_status()
        payload = r.json()
        all_records.extend(payload["data"])
        print(f"✓ ({payload['records_in_page']} registros)")

        # Pequeña pausa para no saturar la API
        time.sleep(0.2)

    df = pd.DataFrame(all_records)
    print(f"\nTotal extraído: {len(df)} registros, {len(df.columns)} columnas")
    return df, meta


if __name__ == "__main__":
    df, meta = fetch_current_window()

    print("\nColumnas:", df.columns.tolist())
    print("Shape:", df.shape)
    print(df.head(3))

    # Guardar raw con timestamp de ventana para no sobrescribir entre ciclos
    window_tag = meta["window_start"].replace(":", "").replace("+", "").replace("-", "")[:15]
    df.to_csv(f"data/raw/logs_{window_tag}.csv", index=False)
    print(f"\n✓ Guardado en data/raw/logs_{window_tag}.csv")
```

---

## BLOQUE 3 — Exploración de datos

**Meta:** Documentar el schema real para que el AI Specialist y el Data Architect puedan trabajar.

### 3.1 `app/explore.py` — adaptado al schema real de la API

```python
import pandas as pd
import glob
import os

# Cargar el CSV más reciente de data/raw/
archivos = sorted(glob.glob("data/raw/logs_*.csv"))
if not archivos:
    print("No hay datos. Ejecuta primero: python app/ingest.py")
    exit()

df = pd.read_csv(archivos[-1])
print("Archivo:", archivos[-1])
print("=" * 60)
print("SHAPE:", df.shape)

print("\nCOLUMNAS Y TIPOS:")
print(df.dtypes)

print("\nNULOS POR COLUMNA:")
nulos = df.isna().sum()
print(nulos[nulos > 0])

print("\nMUESTRA (3 filas):")
print(df.head(3))

# Distribución por tipo de log — columna clave
if "sap_function_log_type" in df.columns:
    print("\nDISTRIBUCIÓN POR TIPO DE LOG:")
    print(df["sap_function_log_type"].value_counts())

    # Separar tipos para análisis diferenciado
    system_logs = df[~df["sap_function_log_type"].str.startswith("LLM")]
    llm_logs    = df[df["sap_function_log_type"].str.startswith("LLM")]
    print(f"\nLogs de sistema: {len(system_logs)} | Logs LLM: {len(llm_logs)}")

# Columnas de seguridad — solo en logs de sistema
for col in ["http_status_code", "client_ip", "service_id"]:
    if col in df.columns:
        print(f"\nTOP 10 en '{col}':")
        print(df[col].dropna().value_counts().head(10))

# Columnas LLM — solo en logs LLM
for col in ["llm_status", "llm_model_id"]:
    if col in df.columns:
        print(f"\nTOP 10 en '{col}':")
        print(df[col].dropna().value_counts().head(10))

# Timestamp
ts_col = next((c for c in df.columns if "timestamp" in c.lower()), None)
if ts_col:
    df[ts_col] = pd.to_datetime(df[ts_col], errors="coerce")
    print(f"\nRango temporal ({ts_col}):")
    print("  Desde:", df[ts_col].min())
    print("  Hasta:", df[ts_col].max())

df.to_csv("data/processed/logs_limpios.csv", index=False)
print("\n✓ Guardado en data/processed/logs_limpios.csv")
```

### 3.2 `data/SCHEMA.md` — llenar inmediatamente después de la primera extracción

```markdown
# Schema de datos — [rellenar fecha]

## Fuente
- Base URL: [rellenar]
- Endpoint principal: GET /logs/current
- Autenticación: Authorization: Bearer <token>
- Ventana: 30 minutos UTC rolling

## Tipos de log

| Categoría | sap_function_log_type valores |
|-----------|-------------------------------|
| Sistema   | INFO, WARNING, ERROR, DEBUG, AUDIT, PERF, SECURITY |
| LLM       | LLM_REQUEST, LLM_ERROR, LLM_TIMEOUT |

## Columnas — completar con valores reales observados

| Columna | Tipo | Presente en | Ejemplo | Notas |
|---------|------|-------------|---------|-------|
| sap_function_log_type | string | Ambos | INFO | Columna discriminante |
| http_status_code | int/string | Sistema | 200, 404 | Vacío en LLM |
| client_ip | string | Sistema | 192.168.1.x | Vacío en LLM |
| service_id | string | Sistema | [?] | Vacío en LLM |
| llm_model_id | string | LLM | [?] | Vacío en Sistema |
| llm_status | string | LLM | [?] | Vacío en Sistema |
| llm_cost_usd | float | LLM | [?] | Vacío en Sistema |
| llm_response_time_ms | float | LLM | [?] | Vacío en Sistema |

## Dimensiones (rellenar con datos reales)
- Total registros por ventana de 30 min: ~54,832 (según ejemplo de API)
- Páginas por ventana: ~110
- Batch size: 500 (fijo por servidor)
- Columnas totales: [rellenar]
```

---

## BLOQUE 4 — Configurar el webhook de alerting

> El webhook URL aún no está disponible. Este bloque se activa cuando llegue — deadline Abr 27.

### 4.1 `app/alerting.py`

```python
import requests
from datetime import datetime
from config import WEBHOOK_URL

def send_alert(alert_type: str, severity: str, message: str,
               source_ip: str = None, log_type: str = None):
    """
    Envía alerta al webhook configurado.
    severity: "low" | "medium" | "high" | "critical"
    """
    payload = {
        "alert_type":  alert_type,
        "severity":    severity,
        "message":     message,
        "source_ip":   source_ip or "unknown",
        "log_type":    log_type or "unknown",
        "timestamp":   datetime.utcnow().isoformat() + "Z",
        "source":      "sap-ai-soc-pipeline"
    }

    response = requests.post(WEBHOOK_URL, json=payload, timeout=30)
    print(f"Webhook status: {response.status_code}")
    if response.status_code not in (200, 201, 202, 204):
        print(f"⚠ Respuesta inesperada: {response.text}")
    return response


def send_test_alert():
    return send_alert(
        alert_type="connectivity_test",
        severity="low",
        message="Prueba de conectividad del pipeline de alerting",
        source_ip="0.0.0.0",
        log_type="SECURITY"
    )


if __name__ == "__main__":
    send_test_alert()
```

---

## BLOQUE 5 — Dashboard mínimo con Streamlit

**Meta:** Mostrar datos reales en pantalla. No tiene que ser bonito — tiene que funcionar.

### 5.1 `app/main.py` — adaptado al schema real

```python
import streamlit as st
import pandas as pd

st.set_page_config(page_title="SAP AI SOC Dashboard", layout="wide")
st.title("🔐 SAP AI Security Operations Center")

try:
    df = pd.read_csv("data/processed/logs_limpios.csv")
except FileNotFoundError:
    st.error("No hay datos. Ejecuta primero: python app/ingest.py")
    st.stop()

# Separar tipos de log
if "sap_function_log_type" in df.columns:
    system_df = df[~df["sap_function_log_type"].str.startswith("LLM", na=False)]
    llm_df    = df[df["sap_function_log_type"].str.startswith("LLM", na=False)]
else:
    system_df = df
    llm_df    = pd.DataFrame()

# Métricas rápidas
col1, col2, col3, col4 = st.columns(4)
col1.metric("Total logs", len(df))
col2.metric("Logs de sistema", len(system_df))
col3.metric("Logs LLM", len(llm_df))
if "client_ip" in df.columns:
    col4.metric("IPs únicas", df["client_ip"].nunique())

st.divider()

# Distribución por tipo
if "sap_function_log_type" in df.columns:
    st.subheader("Distribución por tipo de log")
    st.bar_chart(df["sap_function_log_type"].value_counts())

# Logs de sistema — columnas de seguridad
if not system_df.empty:
    st.subheader("Logs de sistema")
    col_a, col_b = st.columns(2)
    if "http_status_code" in system_df.columns:
        with col_a:
            st.write("Top HTTP status codes")
            st.bar_chart(system_df["http_status_code"].dropna().value_counts().head(10))
    if "client_ip" in system_df.columns:
        with col_b:
            st.write("Top client IPs")
            st.bar_chart(system_df["client_ip"].dropna().value_counts().head(15))

# Tabla de logs más recientes
st.subheader("Logs recientes")
st.dataframe(df.tail(50), use_container_width=True)
```

### 5.2 Ejecutar el dashboard

```bash
streamlit run app/main.py
# Abrir navegador en http://localhost:8501
```

---

## BLOQUE 6 — Checklist de fin del primer bloque

### Accesos
- [x] URL de la API confirmada
- [x] Bearer token recibido y en `.env`
- [ ] GET /health devuelve `{"status": "ok"}`
- [ ] GET /info devuelve total_records y total_pages
- [ ] GET /logs/current page=1 devuelve data[] con registros reales
- [ ] Ejemplo de payload guardado en `data/raw/ejemplo_respuesta.json`

### Entorno
- [x] Repositorio creado con estructura correcta
- [x] `.gitignore` configurado y verificado
- [x] `.env.example` commiteado
- [x] `requirements.txt` generado y commiteado
- [ ] Entorno virtual activo y dependencias instaladas

### Datos
- [ ] Primera extracción completa guardada en `data/raw/logs_[timestamp].csv`
- [ ] `data/SCHEMA.md` creado con columnas reales observadas
- [ ] Schema compartido con AI Specialist y Data Architect
- [ ] Columna `sap_function_log_type` y su distribución documentada

### Visualización
- [ ] `app/explore.py` ejecutado sin errores
- [ ] Streamlit corriendo con datos reales

### Alerting
- [ ] En espera de WEBHOOK_URL (deadline Abr 27)

### Coordinación
- [ ] PM informado del estado actual
- [ ] AI Specialist tiene acceso al schema y a una muestra de datos

---

## BLOQUE 7 — Próximos pasos después del arranque inicial

### Etapa 2 — Integración con HANA (con Data Architect)
```
- Recibir connection string de HANA
- Adaptar ingest.py para escribir en HANA además de CSV
- Usar _id como primary key y @timestamp para particionamiento temporal
- Verificar que las tablas se poblan correctamente
```

### Etapa 3 — Automatización del pipeline (cada 30 minutos)
```
- Hacer que fetch_current_window() corra automáticamente cada 30 min
- Implementar: detección de anomalía → send_alert() sin intervención manual
- Medir MTTD: tiempo desde log generado hasta alerta disparada
- Objetivo: MTTD < 1 minuto
```

### Etapa 4 — Despliegue en SAP BTP / Cloud Foundry

**`manifest.yml`:**
```yaml
applications:
  - name: sap-ai-soc
    memory: 512M
    instances: 1
    buildpacks:
      - python_buildpack
    command: streamlit run app/main.py --server.port=$PORT --server.address=0.0.0.0
    env:
      API_BASE_URL: ((API_BASE_URL))
      BEARER_TOKEN: ((BEARER_TOKEN))
      WEBHOOK_URL:  ((WEBHOOK_URL))
```

```bash
cf login
cf push
cf logs sap-ai-soc --recent
```

> Usar `/health` como liveness probe en Cloud Foundry — no requiere auth y confirma que la API fuente está viva.

---

## BLOQUE 8 — Reglas personales que no debo romper

1. **Nunca** commitear `.env` — verificar con `git status` antes de cada `git push`
2. **Siempre** guardar datos raw con timestamp en el nombre — nunca sobrescribir
3. **Siempre** probar en local antes de deployar a BTP
4. **Siempre** documentar el schema cuando cambie — el equipo depende de eso
5. **Siempre** avisar al PM si voy a tardar más de 30 minutos en desbloquear a alguien
6. **Nunca** hacer el pipeline más complejo de lo necesario — la simplicidad es robustez
7. **Siempre** guardar `data/raw/sample_10.csv` para que otros trabajen sin necesitar la API
8. **Nunca** intentar rellenar los nulos de columnas LLM en logs de sistema ni viceversa — es por diseño

---

## REFERENCIA RÁPIDA — Comandos más usados

```bash
# Activar entorno virtual (Git Bash / Linux / Mac)
source .venv/bin/activate

# Instalar dependencias
pip install -r requirements.txt

# Prueba de liveness (sin auth)
python prueba_health.py

# Prueba de autenticación
python prueba_info.py

# Extracción completa de ventana actual
python app/ingest.py

# Exploración de datos
python app/explore.py

# Dashboard
streamlit run app/main.py

# Probar webhook (cuando esté disponible)
python app/alerting.py

# Git — flujo diario
git pull                                        # siempre primero
git add .
git commit -m "feat: descripción del cambio"
git push origin develop

# Verificar que .env no está en el commit
git status

# Cloud Foundry
cf login
cf push
cf logs sap-ai-soc --recent
```

---

*Versión 2 — actualizada con datos reales de la API del hackathon.*  
*Actualizar conforme avanza la competencia.*
