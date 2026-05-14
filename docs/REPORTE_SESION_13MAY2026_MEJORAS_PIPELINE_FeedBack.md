# Session Report — Pipeline Improvements
## SAP AI Security Anomaly Detection Hackathon · TEC de Monterrey × SAP
**Date:** May 13–14, 2026 (~22:00–00:10 CST)
**Author:** Cloud Integration Engineer
**Branch:** alerts_implementation
**Deploy status:** ✅ Live in Cloud Foundry since 2026-05-14 06:01:59 UTC

---

## Context — Why These Changes Were Made

Feedback from Santiago Reyes (SAP evaluator) identified three issues with the
current alert output:

1. **High false positive rate:** ~3,000 alerts in the month, almost all
   flagged as false positives. Root cause: `llm_timeout` and `slow_llm_response`
   rules firing continuously on normal LLM infrastructure behavior.

2. **Alert message quality:** Messages were technically correct but not
   actionable. The evaluator specifically asked to improve the message format.

3. **Duplicate alerts between sources:** Data analysis confirmed that 642 out
   of 1,000 sampled ML alerts (`ml_ip_anomaly`) were paired with a `brute_force`
   or `security_event` alert from quick_filter for the same IP within the same
   time window — the same incident being reported twice from two different
   detection perspectives.

Additionally, the API call limit was set to 100 calls per 30-minute window
starting May 4. With the previous 2-minute polling cycle, the theoretical
maximum API usage was ~195–225 calls per window under high load, exceeding
the limit.

---

## Change 1 — Short Cycle Interval: 2 min → 10 min

**File:** `pipeline_loop.py`  
**Line changed:** `INTERVALO_POLLING_CORTO`

```python
# Before
INTERVALO_POLLING_CORTO = 60 * 2    # 2 minutes

# After
INTERVALO_POLLING_CORTO = 60 * 10   # 10 minutes
```

**Effect:** The pipeline now polls the SAP API every 10 minutes instead of
every 2 minutes. Each polling cycle performs approximately 10–13 API calls
(ingestion + alerts). With 3 cycles per 30-minute window, total API usage
is approximately 30–39 calls — well within the 100-call limit.

**MTTD impact:** Maximum MTTD increases from ~1 second to ~10 minutes.
This remains significantly better than the industry standard of hours to days,
and is a deliberate trade-off to reduce false positive volume and API load
following evaluator feedback.

---

## Change 2 — Deactivate quick_filter Rules 5 and 6

**File:** `app/quick_filter.py`  
**Section:** `filtrar_amenazas()` function, LLM rules block

```python
# Before
if not df_llm.empty:
    amenazas.extend(_regla_llm_error_costo_alto(df_llm))
    amenazas.extend(_regla_llm_timeout(df_llm))
    amenazas.extend(_regla_llm_respuesta_lenta(df_llm))

# After
if not df_llm.empty:
    amenazas.extend(_regla_llm_error_costo_alto(df_llm))
    # DEACTIVATED: llm_timeout generates false positives — feedback Santiago Reyes 13-May-2026
    # amenazas.extend(_regla_llm_timeout(df_llm))
    # DEACTIVATED: slow_llm_response generates false positives — feedback Santiago Reyes 13-May-2026
    # amenazas.extend(_regla_llm_respuesta_lenta(df_llm))
```

**Effect:** The system no longer generates `llm_timeout` or `slow_llm_response`
alerts from quick_filter. These event types are structural characteristics of
the SAP LLM infrastructure (persistent ~30% error+timeout rate) and not
actionable security threats. The underlying functions `_regla_llm_timeout()`
and `_regla_llm_respuesta_lenta()` remain in the codebase and can be
reactivated by uncommenting the two lines above.

**Note:** The ML model (IF_llm) still analyzes LLM timeout and response time
features internally as part of its statistical anomaly detection. Deactivating
the rule only removes the deterministic alert — the ML layer continues to
detect statistically unusual LLM behavior.

**Active quick_filter rules after this change:**

| Rule | Alert Type | Status |
|---|---|---|
| 1 | `security_event` | ✅ Active |
| 2 | `brute_force` | ✅ Active |
| 3 | `path_scan` | ✅ Active |
| 4 | `high_cost_llm_error` | ✅ Active |
| 5 | `llm_timeout` | ❌ Deactivated |
| 6 | `slow_llm_response` | ❌ Deactivated |

---

## Change 3 — Improve Alert Message Format

**File:** `app/quick_filter.py`

### Rule 1 — security_event

```python
# Before
details = (
    f"{count} SECURITY event(s) in this batch. "
    f"IPs: {ips_str if ips_str else 'unknown'}. "
    f"Status: {status_str}"
)

# After
details = (
    f"SECURITY ALERT: {count} flagged event(s) detected. "
    f"Involved IPs: {ips_str if ips_str else 'unknown'}. "
    f"HTTP status breakdown: {status_str}. "
    f"Immediate review recommended."
)
```

### Rule 2 — brute_force

```python
# Before
details = (
    f"{intentos} auth failures from IP {ip} "
    f"in this polling cycle ({status_str})"
)

# After
details = (
    f"BRUTE FORCE: {intentos} consecutive auth failures from IP {ip}. "
    f"Breakdown: {status_str}. "
    f"Credential attack in progress — block IP immediately."
)
```

**Effect:** Alert messages now lead with the threat category in uppercase,
provide structured context, and include a clear recommended action. This
directly addresses the evaluator's request to improve message quality.

**Confirmed working in production (Cycle #1, 2026-05-14 06:02:12 UTC):**
```
SECURITY ALERT: 94 flagged event(s) detected. Involved IPs: 55.234.242.146, 41.1...
```

---

## Change 4 — Cross-Source Duplicate Suppression

**Files:** `pipeline_loop.py`  
**New function:** `_ip_ya_alertada_por_qf(conn, ip, ventana_inicio)`  
**Modified section:** ML long cycle alert dispatch loop

### Background

Analysis of `DBADMIN.ALERTS` confirmed 642 out of 1,000 sampled `ml_ip_anomaly`
alerts were paired with a `brute_force` or `security_event` alert from
quick_filter for the same IP within a 1-hour window. This represented a 64%
overlap rate — the same incident reported twice from different detection layers.

### New function `_ip_ya_alertada_por_qf()`

```python
def _ip_ya_alertada_por_qf(conn, ip: str, ventana_inicio: str) -> bool:
    """
    Returns True if quick_filter already sent a confirmed alert (alerted=1)
    for this IP within the last hour. In that case, the ML alert should be
    suppressed to avoid double-reporting the same incident.
    Falls back to False (no suppression) if conn is None or on any error.
    """
```

The function queries `DBADMIN.ALERTS` for any quick_filter alert containing
the IP address in the `DETAILS` field, detected within the last 3,600 seconds
and confirmed by SAP (`ALERTED = 1`).

### Suppression logic in the ML cycle

When an `ml_ip_anomaly` alert is about to be dispatched:

1. Extract the IP from the alert's `details` field
2. Call `_ip_ya_alertada_por_qf()` to check for existing quick_filter coverage
3. If covered → UPDATE `DBADMIN.ALERTS` setting `SUPPRESSED_BY = 'quick_filter'`,
   log the suppression, and skip the POST to SAP
4. If not covered → send the alert normally (POST /alert → HTTP 201)

Suppressed alerts are still recorded in HANA with full details for forensic
auditing — they are not lost, only not forwarded to SAP.

---

## Change 5 — New Column SUPPRESSED_BY in DBADMIN.ALERTS

**Applied in:** SAP HANA Cloud SQL Console (DBADMIN user)

```sql
ALTER TABLE DBADMIN.ALERTS ADD (SUPPRESSED_BY NVARCHAR(50) DEFAULT NULL);
```

**Schema after change:**

| Column | Type | Description |
|---|---|---|
| ALERT_ID | NVARCHAR | Unique alert UUID |
| LOG_ID | NVARCHAR | Triggering log record ID |
| DETECTED_AT | TIMESTAMP | Detection timestamp |
| DETECTION_SOURCE | NVARCHAR | `quick_filter` or `model_ml` |
| ALERT_TYPE | NVARCHAR | Threat type |
| SEVERITY | NVARCHAR | `low`, `medium`, `high` |
| DETAILS | NVARCHAR | Human-readable description |
| WINDOW_START | TIMESTAMP | Start of data window |
| ALERTED | TINYINT | 0=pending/failed, 1=confirmed by SAP |
| **SUPPRESSED_BY** | **NVARCHAR** | **NULL=sent normally, 'quick_filter'=suppressed** |

**Pending:** The Viz Lead needs to add `SUPPRESSED_BY` as an attribute in
`CV_ALERTS` (HDI Container `Prod_Vizz`) and redeploy for SAC dashboard
visibility. A step-by-step tutorial has been generated:
`TUTORIAL_HDI_CV_ALERTS_SUPPRESSED_BY.md`

---

## Production Verification

**Deploy confirmed:** 2026-05-14 06:01:59 UTC  
**First cycle output:**

```
PIPELINE LOOP v2 — Ingesta + Detección + Alerting
Ciclo corto (ingesta + detección): cada 10 minutos   ✅
quick_filter: ✓ activo
alerting:     ✓ activo
model ML:     ✓ activo

CICLO #1 — Iniciando
Ventana: 2026-05-14T06:00:00+00:00 → 2026-05-14T06:30:00+00:00
Registros: 5,483 | Nuevos este ciclo: 5,483
[FILTER] → security_event (high) | SECURITY ALERT: 94 flagged event(s) detected...
[DETECT] Resumen: 1 detectadas | 0 enviadas | 0 fallidas | 1 duplicadas
Próximo ciclo en 10 min
```

**Confirmed working:**
- 10-minute interval ✅
- Only `security_event` fired — no `llm_timeout`, no `slow_llm_response` ✅
- Improved message format visible in production ✅
- No `llm_timeout` or `slow_llm_response` in output ✅

---

## Files Modified in This Session

| File | Type of Change |
|---|---|
| `pipeline_loop.py` | Interval 2→10 min, `_ip_ya_alertada_por_qf()`, suppression logic |
| `app/quick_filter.py` | Rules 5+6 deactivated, messages improved |
| `DBADMIN.ALERTS` (HANA) | Column `SUPPRESSED_BY` added via ALTER TABLE |

---

## Pending Actions

| Action | Owner | When |
|---|---|---|
| Update `CV_ALERTS` in HDI Container `Prod_Vizz` to expose `SUPPRESSED_BY` | Viz Lead | Next session |
| Verify Cycle #2+ shows no `llm_timeout`/`slow_llm_response` alerts | CIE | Tomorrow |
| Verify `SUPPRESSED_BY` populated after first ML cycle (28 min) | CIE | Tomorrow |

---

*Report generated: 2026-05-14, 00:10 CST*
*Cloud Integration Engineer — SAP AI Security Anomaly Detection Hackathon*
