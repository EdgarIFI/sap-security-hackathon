# 🔧 Notas de Operación — Cloud Integration Engineer
### SAP AI Security Anomaly Detection · Uso interno personal

> **Propósito:** Guía de acción propia para los primeros momentos de la competencia.  
> **Audiencia:** Solo yo.  
> **Filosofía:** Primero que funcione local. Luego que funcione en la nube.

---

## ESTADO DE HITOS (actualizar conforme avances)

```
[ ] API & Data Access operativo        → deadline: Abr 13
[ ] Primera extracción real documentada
[ ] Webhook de alerting configurado    → deadline: Abr 27
[ ] Pipeline en BTP/Cloud Foundry      → deadline: May 4
[ ] Sistema estable para eliminatoria  → deadline: May 12
```

---

## BLOQUE 0 — Antes de tocar código (primeros 30 minutos)

Antes de escribir una sola línea, resuelve estas preguntas. Si no tienes respuesta a alguna, es tu primer bloqueante y hay que escalarlo al PM inmediatamente.

```
¿Tengo la URL base de la API de SAP?          [ ] SÍ  [ ] NO → escalarlo
¿Tengo la API Key o credencial de acceso?     [ ] SÍ  [ ] NO → escalarlo
¿Tengo un ejemplo del payload de respuesta?   [ ] SÍ  [ ] NO → pedirlo
¿Tengo la URL del webhook de alerting?        [ ] SÍ  [ ] NO → puede esperar
¿Tengo acceso al repositorio del equipo?      [ ] SÍ  [ ] NO → crearlo yo
¿Tenemos HANA connection string?              [ ] SÍ  [ ] NO → puede esperar
```

**Regla crítica:** No guardes ninguna credencial en archivos de código. Todo va en `.env`.

---

## BLOQUE 1 — Setup del entorno local (primeros 45 minutos)

### 1.1 Estructura de carpetas

```bash
mkdir sap-ai-soc
cd sap-ai-soc

mkdir -p app data/raw data/processed notebooks tests
touch app/__init__.py
touch app/config.py app/ingest.py app/etl.py app/alerting.py app/main.py
touch .env .env.example .gitignore requirements.txt README.md
```

### 1.2 `.gitignore` — configurar ANTES de hacer cualquier commit

```
.env
data/raw/
data/processed/
__pycache__/
*.pyc
.venv/
*.egg-info/
dist/
.DS_Store
```

### 1.3 `.env.example` — esto SÍ va en Git

```
API_BASE_URL=
API_KEY=
WEBHOOK_URL=
HANA_HOST=
HANA_PORT=
HANA_USER=
HANA_PASSWORD=
```

### 1.4 `.env` — esto NUNCA va en Git

```
API_BASE_URL=https://[rellenar-cuando-tengas]
API_KEY=[rellenar-cuando-tengas]
WEBHOOK_URL=https://[rellenar-cuando-tengas]
```

### 1.5 Entorno virtual y dependencias

```bash
python -m venv .venv
source .venv/bin/activate        # Linux/Mac
# .venv\Scripts\activate         # Windows

pip install pandas requests streamlit scikit-learn \
            python-dotenv matplotlib numpy
pip freeze > requirements.txt
```

### 1.6 `app/config.py` — plantilla base

```python
import os
from dotenv import load_dotenv

load_dotenv()

API_BASE_URL = os.getenv("API_BASE_URL")
API_KEY      = os.getenv("API_KEY")
WEBHOOK_URL  = os.getenv("WEBHOOK_URL")

# Validación rápida al importar
def validate_config():
    missing = [k for k, v in {
        "API_BASE_URL": API_BASE_URL,
        "API_KEY":      API_KEY,
    }.items() if not v]
    if missing:
        raise EnvironmentError(f"Variables de entorno faltantes: {missing}")
```

---

## BLOQUE 2 — Primera conexión a la API (prioridad máxima)

**Meta:** Hacer una llamada exitosa y ver datos reales en pantalla.

### 2.1 `app/ingest.py` — versión mínima funcional

```python
import requests
import pandas as pd
from config import API_BASE_URL, API_KEY

def fetch_logs(limit=100):
    headers = {
        "x-api-key": API_KEY,
        # Si usan Bearer token en su lugar:
        # "Authorization": f"Bearer {API_KEY}"
    }
    params = {"limit": limit}  # ajustar según lo que soporte la API

    response = requests.get(
        API_BASE_URL,
        headers=headers,
        params=params,
        timeout=30
    )
    response.raise_for_status()  # lanza excepción si status >= 400

    data = response.json()

    # La respuesta puede ser lista directa o tener un key wrapper
    # Ajustar según estructura real:
    if isinstance(data, list):
        df = pd.DataFrame(data)
    elif isinstance(data, dict):
        # Buscar el key que contiene los registros
        # Ejemplo: data["logs"], data["events"], data["data"]
        key = next(iter(data))  # tomar primer key como exploración
        df = pd.DataFrame(data[key])
    else:
        raise ValueError(f"Estructura inesperada: {type(data)}")

    return df


if __name__ == "__main__":
    df = fetch_logs()
    print("Shape:", df.shape)
    print("Columnas:", df.columns.tolist())
    print(df.head())
    df.to_csv("data/raw/primer_extracto.csv", index=False)
    print("✓ Guardado en data/raw/primer_extracto.csv")
```

### 2.2 Prueba de conectividad mínima (antes de parsear)

```python
# prueba_rapida.py — solo para verificar que la API responde
import requests
import os
from dotenv import load_dotenv
load_dotenv()

r = requests.get(
    os.getenv("API_BASE_URL"),
    headers={"x-api-key": os.getenv("API_KEY")},
    timeout=10
)
print("Status:", r.status_code)
print("Headers respuesta:", dict(r.headers))
print("Body (primeros 500 chars):", r.text[:500])
```

### 2.3 Diagnóstico de errores HTTP comunes

| Código | Causa probable | Acción |
|---|---|---|
| `200` | Todo bien | Continuar |
| `401` | API Key incorrecta o faltante | Verificar header, revisar `.env` |
| `403` | Sin permisos para ese endpoint | Verificar URL y permisos del key |
| `404` | URL incorrecta | Revisar base URL y path |
| `429` | Rate limit superado | Agregar `time.sleep(1)` entre llamadas |
| `500` | Error del servidor SAP | Esperar y reintentar; notificar al PM |
| `Timeout` | Red lenta o API caída | Aumentar timeout; verificar conectividad |

---

## BLOQUE 3 — Exploración de datos (inmediatamente después de primera extracción)

**Meta:** Documentar el schema para que el AI Specialist y el Data Architect puedan trabajar.

### 3.1 `app/explore.py`

```python
import pandas as pd

df = pd.read_csv("data/raw/primer_extracto.csv")

print("=" * 60)
print("SHAPE:", df.shape)
print("=" * 60)

print("\nCOLUMNAS Y TIPOS:")
print(df.dtypes)

print("\nNULOS POR COLUMNA:")
print(df.isna().sum())

print("\nMUESTRA (5 filas):")
print(df.head())

print("\nESTADÍSTICAS BÁSICAS:")
print(df.describe(include="all"))

# Columnas de interés para seguridad — ajustar según data real
for col in ["event_type", "status", "log_type", "source_ip"]:
    if col in df.columns:
        print(f"\nTOP 10 valores en '{col}':")
        print(df[col].value_counts().head(10))

# Parsear timestamp si existe
if "timestamp" in df.columns:
    df["timestamp"] = pd.to_datetime(df["timestamp"], errors="coerce")
    print("\nRango temporal:")
    print("  Desde:", df["timestamp"].min())
    print("  Hasta:", df["timestamp"].max())
    null_ts = df["timestamp"].isna().sum()
    if null_ts > 0:
        print(f"  ⚠ {null_ts} timestamps no parseables")

df.to_csv("data/processed/logs_limpios.csv", index=False)
print("\n✓ Guardado en data/processed/logs_limpios.csv")
```

### 3.2 Lo que debo documentar para el equipo inmediatamente después

Crear `data/SCHEMA.md` con esta info:

```markdown
# Schema de datos — [fecha]

## Fuente
- Endpoint: [URL]
- Método: GET
- Autenticación: x-api-key header

## Columnas

| Columna | Tipo | Ejemplo | Notas |
|---------|------|---------|-------|
| timestamp | datetime | 2026-04-04T14:45:01Z | UTC |
| source_ip | string | 192.168.1.105 | IPv4 |
| port_service | string | TCP/22 (SSH) | formato "PROTO/PORT (NOMBRE)" |
| event_description | string | Failed login attempt | texto libre |
| status | string | DENIED | DENIED/BLOCKED/ACCOUNT LOCKED/DROPPED |
| log_type | string | security | siempre "security" en muestra vista |

## Dimensiones
- Filas en muestra: N
- Rango temporal: desde X hasta Y
- Anomalías visibles a ojo: Z
```

---

## BLOQUE 4 — Configurar el webhook de alerting

**Meta:** Poder enviar un POST con una alerta y verificar que llega.

### 4.1 `app/alerting.py`

```python
import requests
from datetime import datetime
from config import WEBHOOK_URL

def send_alert(alert_type: str, severity: str, message: str,
               source_ip: str = None, timestamp: str = None):
    """
    Envía una alerta al webhook configurado.

    severity: "low" | "medium" | "high" | "critical"
    """
    payload = {
        "alert_type":  alert_type,
        "severity":    severity,
        "message":     message,
        "source_ip":   source_ip or "unknown",
        "timestamp":   timestamp or datetime.utcnow().isoformat() + "Z",
        "source":      "sap-ai-soc-pipeline"
    }

    response = requests.post(WEBHOOK_URL, json=payload, timeout=30)
    print(f"Webhook status: {response.status_code}")
    if response.status_code not in (200, 201, 202, 204):
        print(f"⚠ Respuesta inesperada: {response.text}")
    return response


def send_test_alert():
    """Prueba mínima para verificar que el webhook responde."""
    return send_alert(
        alert_type="connectivity_test",
        severity="low",
        message="Prueba de conectividad del pipeline de alerting",
        source_ip="0.0.0.0"
    )


if __name__ == "__main__":
    send_test_alert()
```

### 4.2 Prueba del webhook

```bash
python app/alerting.py
# Esperar: "Webhook status: 200" (o 201/202/204 según config del receptor)
```

---

## BLOQUE 5 — Dashboard mínimo con Streamlit

**Meta:** Mostrar datos reales en pantalla. No tiene que ser bonito — tiene que funcionar.

### 5.1 `app/main.py`

```python
import streamlit as st
import pandas as pd

st.set_page_config(page_title="SAP AI SOC Dashboard", layout="wide")
st.title("🔐 SAP AI Security Operations Center")

# Cargar datos
try:
    df = pd.read_csv("data/processed/logs_limpios.csv")
    if "timestamp" in df.columns:
        df["timestamp"] = pd.to_datetime(df["timestamp"], errors="coerce")
except FileNotFoundError:
    st.error("No se encontró el archivo de datos. Ejecuta primero: python app/ingest.py")
    st.stop()

# Métricas rápidas
col1, col2, col3 = st.columns(3)
col1.metric("Total de logs", len(df))
if "status" in df.columns:
    anomalias = df[df["status"].isin(["DENIED","BLOCKED","ACCOUNT LOCKED","DROPPED"])]
    col2.metric("Eventos bloqueados/denegados", len(anomalias))
if "source_ip" in df.columns:
    col3.metric("IPs únicas", df["source_ip"].nunique())

st.divider()

# Tabla de datos
st.subheader("Logs más recientes")
st.dataframe(df.tail(50), use_container_width=True)

# Distribuciones
if "event_type" in df.columns or "status" in df.columns:
    col_a, col_b = st.columns(2)
    if "status" in df.columns:
        with col_a:
            st.subheader("Distribución por status")
            st.bar_chart(df["status"].value_counts())
    if "source_ip" in df.columns:
        with col_b:
            st.subheader("Top IPs")
            st.bar_chart(df["source_ip"].value_counts().head(15))

# Serie temporal
if "timestamp" in df.columns:
    st.subheader("Volumen de eventos por hora")
    df["hour"] = df["timestamp"].dt.floor("H")
    hourly = df.groupby("hour").size()
    st.line_chart(hourly)
```

### 5.2 Ejecutar el dashboard

```bash
streamlit run app/main.py
# Abrir navegador en http://localhost:8501
```

---

## BLOQUE 6 — Checklist de fin del primer bloque de trabajo

Completa esto antes de reportar avance al PM.

### Accesos
- [ ] URL de la API confirmada y documentada
- [ ] API Key recibida y guardada en `.env`
- [ ] Primera llamada HTTP exitosa (status 200)
- [ ] Ejemplo de payload guardado en `data/raw/ejemplo_respuesta.json`

### Entorno
- [ ] Repositorio creado con estructura correcta
- [ ] `.gitignore` configurado (verificar que `.env` no aparece en `git status`)
- [ ] `.env.example` commiteado
- [ ] `requirements.txt` generado y commiteado
- [ ] Entorno virtual funcional

### Datos
- [ ] Primera extracción real guardada en `data/raw/primer_extracto.csv`
- [ ] `data/SCHEMA.md` creado y compartido con el equipo
- [ ] Columnas clave identificadas y documentadas
- [ ] Rango temporal del dataset conocido

### Visualización
- [ ] `app/explore.py` ejecutado sin errores
- [ ] Streamlit corriendo con datos reales

### Alerting
- [ ] `WEBHOOK_URL` en `.env`
- [ ] `python app/alerting.py` devuelve status 200

### Coordinación
- [ ] Schema documentado y compartido con AI Specialist
- [ ] Schema compartido con Data Architect
- [ ] PM informado del estado (qué está listo, qué está bloqueado)

---

## BLOQUE 7 — Próximos pasos después del arranque inicial

Una vez que el bloque anterior esté completo, el trabajo evoluciona así:

### Etapa 2 — Integración con HANA (coordinada con Data Architect)
```
- Recibir connection string de HANA del Data Architect
- Adaptar ingest.py para escribir en HANA en lugar de solo CSV
- Verificar que las tablas en HANA se poblan correctamente
- Liberar interfaz para que el ML Module lea desde HANA
```

### Etapa 3 — Automatización del pipeline
```
- Hacer que ingest.py corra en loop o en intervalos
- Implementar detección → alerta de forma automática (no manual)
- Medir MTTD: tiempo desde log generado hasta alerta disparada
- Objetivo de MTTD: lo más bajo posible (< 1 minuto idealmente)
```

### Etapa 4 — Despliegue en SAP BTP / Cloud Foundry
```
- Crear manifest.yml para Cloud Foundry
- Configurar environment variables en BTP (no en el código)
- Hacer cf push y verificar que el servicio responde
- Conectar SAP HANA como servicio binding en BTP
```

**Manifest mínimo para Cloud Foundry (`manifest.yml`):**
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
      API_KEY: ((API_KEY))
      WEBHOOK_URL: ((WEBHOOK_URL))
```

---

## BLOQUE 8 — Reglas personales que no debo romper

1. **Nunca** commitear `.env` — verificar con `git status` antes de cada `git push`
2. **Siempre** guardar los datos raw sin modificar — los procesados pueden regenerarse, los raw no
3. **Siempre** probar en local antes de deployar a BTP
4. **Siempre** documentar el schema cuando cambie — el equipo depende de eso
5. **Siempre** avisar al PM si voy a tardar más de 30 minutos en desbloquear a alguien
6. **Nunca** hacer el pipeline más complejo de lo necesario — la simplicidad es robustez
7. **Siempre** guardar una muestra pequeña (`data/raw/sample_10.csv`) para que otros puedan trabajar sin necesitar la API

---

## REFERENCIA RÁPIDA — Comandos más usados

```bash
# Activar entorno virtual
source .venv/bin/activate

# Instalar/actualizar dependencias
pip install -r requirements.txt

# Extraer datos
python app/ingest.py

# Explorar datos
python app/explore.py

# Correr dashboard
streamlit run app/main.py

# Probar webhook
python app/alerting.py

# Git — flujo básico
git add .
git commit -m "feat: descripción del cambio"
git push origin develop

# Ver qué va a ir en el commit (VERIFICAR .env no esté)
git status

# Cloud Foundry — deploy
cf login
cf push

# Ver logs de la app en Cloud Foundry
cf logs sap-ai-soc --recent
```

---

*Documento de uso personal — actualizar conforme avanza la competencia.*
