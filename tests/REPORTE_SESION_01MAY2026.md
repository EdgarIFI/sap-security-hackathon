# Reporte de Sesión — 30 Abril / 1 Mayo 2026
## SAP AI Security Anomaly Detection Hackathon · TEC de Monterrey × SAP
**Rama de trabajo:** `alerts_implementation`  
**Autor:** Cloud Integration Engineer  
**Estado al cierre:** ✅ Go Live confirmado en Cloud Foundry

---

## Resumen ejecutivo

Esta sesión completó el bloque crítico que faltaba para el criterio de evaluación #1 (40% del score): el ciclo completo **DETECT → RESPOND** automatizado. El sistema ahora ingiere logs, detecta amenazas con reglas determinísticas y envía alertas a la API SAP sin intervención humana, cada 2 minutos, desde Cloud Foundry.

---

## Lo que se implementó en esta sesión

### 1. `app/alerting.py` — módulo de envío de alertas

Módulo nuevo. Responsabilidad única: construir el mensaje WHAT/WHEN/WHY y hacer `POST /alert` a la API SAP.

**Decisiones de diseño críticas:**
- El endpoint de alerting es `POST /alert` en la **misma API SAP** (`{API_BASE_URL}/alert`), no un webhook externo separado. Usa el mismo `BEARER_TOKEN` que `/logs/current`.
- El body acepta exactamente **un campo**: `{"message": "WHAT: ... WHEN: ... WHY: ..."}` con máximo 300 caracteres.
- La respuesta exitosa es **HTTP 201** (no 200).
- La función `enviar_alerta()` **nunca lanza excepciones** — cualquier error queda en `AlertResult.error`. Esto garantiza que un fallo de alerting no mate el ciclo de polling.
- Reintentos con backoff exponencial: 1s → 2s, máximo 3 intentos ante errores 5xx o timeout.
- Registra cada alerta en `DBADMIN.ALERTS` con `alerted=0` antes del POST, y actualiza a `alerted=1` si el POST fue exitoso. Permite auditoría forense.
- Anti-duplicados por `log_id`: no envía dos alertas para el mismo registro.

**Contrato público:**
```python
from app.alerting import enviar_alerta, AlertResult

result = enviar_alerta(
    alert_type   = "brute_force",
    severity     = "high",
    details      = "18 intentos HTTP 401 desde IP 203.0.113.45 en 90 seg",
    log_id       = "_id del registro",
    event_time   = "@timestamp del log",   # opcional
    conn         = hana_conn,              # opcional — registra en HANA si se pasa
    window_start = "2026-05-01T06:30:00+00:00",  # opcional
    source       = "quick_filter",         # o "model_ml"
)
```

**Cómo probarlo:**
```bash
python -m app.alerting
```

---

### 2. `app/quick_filter.py` — detección determinística de amenazas

Módulo nuevo. Analiza cada batch de `df_nuevos` (registros crudos recién insertados en HANA) y retorna una lista de amenazas. No envía alertas — solo detecta.

**6 reglas implementadas:**

| # | Regla | Tipo de log | Lógica | Severidad |
|---|---|---|---|---|
| 1 | `security_event` | Sistema | `sap_function_log_type == 'SECURITY'` — agrupado en 1 alerta por batch con conteo de IPs | HIGH (≥3) / MEDIUM (1-2) |
| 2 | `brute_force` | Sistema | ≥5 errores HTTP 401/403 desde la misma IP en el batch — 1 alerta por IP | HIGH |
| 3 | `path_scan` | Sistema | ≥3 peticiones 404 a rutas sospechosas (`/phpmyadmin`, `/.env`, `/cgi-bin`, etc.) — 1 alerta por IP | MEDIUM / HIGH (rutas críticas) |
| 4 | `high_cost_llm_error` | LLM | `LLM_ERROR` con `llm_cost_usd > 1.0` — agrupado con costo total y máximo | HIGH (≥3) / MEDIUM (1-2) |
| 5 | `llm_timeout` | LLM | Cualquier `LLM_TIMEOUT` — agrupado por batch con conteo y modelo | HIGH (≥3) / MEDIUM |
| 6 | `slow_llm_response` | LLM | `llm_response_time_ms > 10,000` — agrupado con max/avg/conteo | MEDIUM (≥10) / LOW |

**Decisión crítica de diseño — agrupación por batch:**  
Las reglas 1, 4, 5 y 6 generan **una sola alerta por batch** en lugar de una por registro. Esto es fundamental: en datos reales se observaron 833 registros lentos en un solo ciclo. Sin agrupación, el sistema habría enviado 833 POSTs a SAP saturando su dashboard. Con agrupación: 1 alerta con el resumen completo.

**Columna con typo oficial:**  
La ruta del request viene como `heathers_request_path` (typo oficial de la API — viene con 'h' inicial en lugar de 'headers'). No corregir. El código lo maneja explícitamente.

**Cómo probarlo:**
```bash
python -m app.quick_filter
# Debe mostrar: 6/6 reglas detectadas, 0 falsas alarmas en registro normal
```

---

### 3. `pipeline_loop.py` (antes `pipeline_loop_v2.py`) — integración completa

El pipeline principal fue reemplazado con la versión v2 que integra detección y alerting. El v1 original fue preservado como `pipeline_loop_v1_backup.py`.

**Cambios respecto al v1:**

| Aspecto | v1 | v2 (actual) |
|---|---|---|
| Detección | Bloque comentado (placeholder) | `quick_filter.filtrar_amenazas()` activo |
| Alerting | No implementado | `alerting.enviar_alerta()` por cada amenaza |
| Intervalo | 29 minutos fijo | 2 min (ciclo corto detección) + placeholder 28 min para ML |
| Imports | Sin quick_filter ni alerting | Imports condicionales — si fallan, el pipeline continúa |
| Logging | Solo ingesta | Ciclo completo: nuevos + amenazas + alertas_ok + alertas_err |
| Log file | `pipeline.log` | `pipeline_v2.log` |

**Dos ciclos en el diseño:**
```
Cada 2 min  →  ingest → quick_filter → alerting  (detección en tiempo real, MTTD ≤ 2 min)
Cada 28 min →  placeholder para model.py          (ML profundo, pendiente AI Specialist)
```

**Imports condicionales — por qué:**  
Si `quick_filter` o `alerting` tienen un error de importación, el pipeline **no muere** — loguea una advertencia y continúa ingiriendo datos. La ingesta de datos nunca puede romperse por un módulo de detección.

---

### 4. `docs/API_DICTIONARY.md` — diccionario completo de la API

Documento nuevo. Referencia técnica de todos los endpoints, schemas y contratos de la API SAP SOC v1.0.0. Usar antes de escribir cualquier código que interactúe con la API.

**Hallazgo importante documentado:**  
El endpoint de alerting no es un webhook externo — es `POST /alert` en la misma API SAP, con el mismo Bearer token. No se necesita `WEBHOOK_URL` adicional.

---

## Aclaraciones importantes para todo el equipo

### Sobre `config.py`
- `WEBHOOK_URL` ya no tiene propósito — puede eliminarse o dejarse comentada. El alerting usa `{API_BASE_URL}/alert` con el mismo `BEARER_TOKEN`.
- Todo lo demás en `config.py` está correcto y no requiere cambios.

### Sobre el flujo de datos y el ETL
```
API → fetch_current_window() → df (raw)
    → deduplicación en memoria (ids_procesados)
    → df_nuevos (solo registros nuevos, crudos)
    → upsert_logs(df_nuevos) → HANA           ← datos raw en HANA
    → quick_filter(df_nuevos) → amenazas       ← opera sobre raw, ANTES del ETL
    → enviar_alerta() por cada amenaza         ← POST /alert a SAP
```

`quick_filter` opera sobre `df_nuevos` **antes** del ETL porque sus reglas son simples (comparaciones directas de campos) y no necesitan normalización. El ETL es para el modelo ML que necesita datos limpios y normalizados.

### Sobre los dos ciclos de detección
- **Ciclo corto (2 min):** `quick_filter` — reglas determinísticas, MTTD ≤ 2 minutos, impacto directo en criterio #1 (40%).
- **Ciclo largo (28 min):** placeholder para `model.py` del AI Specialist — ML profundo sobre la ventana completa. Ambos llaman a `enviar_alerta()` con `source="quick_filter"` o `source="model_ml"`.

### Sobre `DBADMIN.ALERTS`
- `alerted=0` significa "detectada, POST pendiente o fallido".
- `alerted=1` significa "detectada Y confirmada por SAP (HTTP 201)".
- Esta distinción es clave para el reporte forense.

### Cómo ejecutar cada módulo independientemente
```bash
# Desde la raíz del repo — siempre con -m para que app/ se reconozca como paquete
python -m app.alerting       # prueba de conectividad con POST /alert real
python -m app.quick_filter   # prueba de reglas con datos sintéticos
python -m app.ingest         # extracción manual de una ventana
python pipeline_loop.py      # pipeline completo (no usar -m, está en raíz)
```

---

## Estado del sistema al cierre de sesión

### Verificado en Cloud Foundry — logs reales

```
PIPELINE LOOP v2 — Ingesta + Detección + Alerting
quick_filter: ✓ activo
alerting:     ✓ activo
✓ Configuración validada

CICLO #1
  Ventana: 2026-05-01T06:30:00 → 2026-05-01T07:00:00
  Registros: 5,423 | Nuevos: 5,423
  HANA: 3,293 sistema + 2,130 LLM insertados
  [DETECT] 3 amenazas detectadas:
    → security_event (HIGH)  | 112 eventos SECURITY | HTTP 201 ✅
    → llm_timeout (HIGH)     | 200 LLM_TIMEOUT      | HTTP 201 ✅
    → slow_llm_response (MED)| 712 respuestas lentas | HTTP 201 ✅
  Resumen: 3 detectadas | 3 enviadas ✅ | 0 fallidas | 0 duplicadas

CICLO #2, #3, #4
  nuevos=0 | Sin registros nuevos — omitiendo detección ✓
  (deduplicación funcionando correctamente — misma ventana)
```

### Estado de tablas HANA
```sql
-- Verificar al inicio de la próxima sesión
SELECT COUNT(*) FROM DBADMIN.RAW_LOGS_SISTEMA;
SELECT COUNT(*) FROM DBADMIN.RAW_LOGS_LLM;
SELECT COUNT(*) FROM DBADMIN.ALERTS WHERE alerted = 1;
SELECT COUNT(*) FROM DBADMIN.ALERTS WHERE alerted = 0;
```

---

## Archivos modificados en esta sesión

```
sap-security-hackathon/
├── pipeline_loop.py              ← REEMPLAZADO (era v1, ahora es v2 con detección+alerting)
├── pipeline_loop_v1_backup.py    ← NUEVO (v1 original preservado)
├── docs/
│   └── API_DICTIONARY.md        ← NUEVO (referencia completa de la API)
├── REPORTE_SESION_01MAY2026.md  ← NUEVO (este archivo)
└── app/
    ├── alerting.py               ← NUEVO
    └── quick_filter.py           ← NUEVO
```

---

## Pendientes para la próxima sesión

### Prioridad inmediata — antes del 4 de Mayo (Go Live formal)

**CIE (tú):**
- [ ] Medir MTTD real: cronometrar desde que ocurre un evento en la API hasta que aparece `HTTP 201` en los logs de CF. Objetivo: ≤ 2 minutos.
- [ ] Monitorear estabilidad durante la noche: `cf logs sap-ai-soc-papoi --recent` al despertar.
- [ ] Revisar si HANA dice "no disponible" en ciclos donde sí hay registros nuevos — puede indicar que la conexión se cierra entre ciclos.
- [ ] Integrar `model.py` en el bloque placeholder del ciclo largo cuando el AI Specialist lo entregue.

**AI Specialist:**
- [ ] Conectarse a `DBADMIN.RAW_LOGS_SISTEMA` y `DBADMIN.RAW_LOGS_LLM` y explorar datos acumulados.
- [ ] Implementar `app/model.py` — detección de anomalías unsupervised.
- [ ] Entregar `model.py` con el mismo contrato que `quick_filter`: recibe un DataFrame, retorna lista de dicts con `alert_type`, `severity`, `details`, `log_id`, `event_time`.

**Data Architect:**
- [ ] Verificar integridad de tablas HANA — conteos, ausencia de duplicados.
- [ ] Confirmar que los índices están creados:
```sql
CREATE INDEX IDX_SIS_TIMESTAMP ON DBADMIN.RAW_LOGS_SISTEMA (event_timestamp);
CREATE INDEX IDX_SIS_TYPE      ON DBADMIN.RAW_LOGS_SISTEMA (log_type);
CREATE INDEX IDX_SIS_IP        ON DBADMIN.RAW_LOGS_SISTEMA (client_ip);
CREATE INDEX IDX_LLM_STATUS    ON DBADMIN.RAW_LOGS_LLM (llm_status);
```

**Viz Lead:**
- [ ] Dashboard Streamlit con datos reales de HANA.
- [ ] Conectar SAP Analytics Cloud a `DBADMIN.RAW_LOGS_SISTEMA`, `DBADMIN.RAW_LOGS_LLM` y `DBADMIN.ALERTS`.

**Todo el equipo — deadline 4 Mayo:**
- [ ] Prueba end-to-end completa documentada: `API → HANA → detección → POST /alert → SAP Team`.
- [ ] MTTD medido y documentado formalmente.

### Hasta la eliminatoria (12-14 Mayo)
- [ ] Documentar al menos un incidente real detectado como reporte forense.
- [ ] Refinar umbrales de `quick_filter` con datos acumulados de múltiples ventanas.
- [ ] Reporte estratégico final para presentar a SAP.

---

## Comandos de referencia rápida

```bash
# Monitorear pipeline en CF
cf logs sap-ai-soc-papoi --recent
cf logs sap-ai-soc-papoi          # tiempo real

# Estado general de la app
cf app sap-ai-soc-papoi

# Reiniciar si hay problemas
cf restart sap-ai-soc-papoi

# Redesplegar con cambios de código
git add .
git commit -m "feat/fix/chore: descripción"
git push origin alerts_implementation
cf push

# Probar módulos individualmente (desde raíz del repo)
python -m app.alerting
python -m app.quick_filter
python -m app.ingest
python pipeline_loop.py
```

---

*Reporte generado al cierre de sesión — 1 Mayo 2026, 01:00 CST*  
*Cloud Integration Engineer — SAP AI Security Anomaly Detection Hackathon*
