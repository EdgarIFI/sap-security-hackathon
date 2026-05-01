# Tracking de Estado y Próximos Pasos
## SAP AI Security Anomaly Detection Hackathon · TEC de Monterrey × SAP
**Última actualización:** 27 de Abril 2026  
**Nota:** El webhook debía implementarse el 27 de abril pero quedó pendiente. Retomar desde aquí.

---

## Estado actual del sistema

| Componente | Estado |
|---|---|
| Pipeline en Cloud Foundry (polling cada 1-2 min) | ✅ Operativo |
| Datos acumulados en SAP HANA (RAW_LOGS_SISTEMA + RAW_LOGS_LLM) | ✅ Acumulando |
| Tabla ALERTS creada en HANA | ✅ Creada, vacía |
| Webhook de alerting | ❌ Pendiente — no implementado |
| quick_filter.py (reglas determinísticas) | ❌ Pendiente |
| model.py (detección ML) | ❌ Pendiente (AI Specialist) |
| Dashboard Streamlit | ❌ Pendiente (Viz Lead) |
| SAP Analytics Cloud conectado a HANA | ❌ Pendiente (Viz Lead) |

---

## 🔴 URGENTE — Hacer primero (atrasado desde Abr 27)

### CIE (Cloud Integration Engineer)
- [ ] Recibir URL del webhook de los organizadores (si aún no llegó, pedirla)
- [ ] Implementar `app/alerting.py` con función `enviar_alerta(alert_type, severity, details, log_id)`
- [ ] Agregar `WEBHOOK_URL` en Cloud Foundry:
```bash
cf set-env sap-ai-soc WEBHOOK_URL "https://url-del-webhook"
cf restage sap-ai-soc
```
- [ ] Probar que el webhook recibe y registra la alerta correctamente
- [ ] Implementar `app/quick_filter.py` — reglas determinísticas sobre cada batch nuevo:
  - Brute force por IP (múltiples 401/403 desde la misma IP)
  - Path scanning sospechoso (requests a `/cgi-bin`, `/phpmyadmin`, etc. con 404)
  - Eventos de tipo `SECURITY` directamente
  - Errores LLM con costo alto (`llm_cost_usd > 1.0`)
  - Tiempos de respuesta LLM anómalos (`llm_response_time_ms > 10000`)
- [ ] Integrar `quick_filter.py` + `alerting.py` en el ciclo de 1-2 min de `pipeline_loop.py`

---

## 🟠 ESTA SEMANA — Antes del 4 de Mayo (Go Live)

### CIE
- [ ] Monitorear estabilidad del pipeline en CF: `cf logs sap-ai-soc --recent`
- [ ] Medir MTTD real una vez que el filtro rápido esté activo (objetivo: < 2 minutos)
- [ ] Integrar `model.py` en el ciclo de 30 minutos de `pipeline_loop.py` cuando el AI Specialist lo entregue

### AI Specialist
- [ ] Conectarse a HANA y explorar datos acumulados en `DBADMIN.RAW_LOGS_SISTEMA` y `DBADMIN.RAW_LOGS_LLM`
- [ ] Feature engineering sobre columnas clave: `http_status`, `client_ip`, `log_type`, `llm_cost_usd`, `llm_response_time_ms`
- [ ] Implementar `app/model.py` — modelo de detección de anomalías (unsupervised)
- [ ] Verificar que `scikit-learn` está en `requirements.txt`

### Data Architect
- [ ] Verificar integridad de tablas HANA (conteos, ausencia de duplicados)
- [ ] Crear índices para queries del modelo ML en SQL Console de HANA:
```sql
CREATE INDEX IDX_SIS_TIMESTAMP ON DBADMIN.RAW_LOGS_SISTEMA (event_timestamp);
CREATE INDEX IDX_SIS_TYPE      ON DBADMIN.RAW_LOGS_SISTEMA (log_type);
CREATE INDEX IDX_SIS_IP        ON DBADMIN.RAW_LOGS_SISTEMA (client_ip);
CREATE INDEX IDX_LLM_STATUS    ON DBADMIN.RAW_LOGS_LLM (llm_status);
```

### Viz Lead
- [ ] Prototipo de dashboard Streamlit con datos reales de HANA o CSV
- [ ] Iniciar conexión de SAP Analytics Cloud a las tablas `DBADMIN.RAW_LOGS_SISTEMA` y `DBADMIN.RAW_LOGS_LLM`

### PM / Scrum Master
- [ ] Verificar que todos los roles tienen acceso a HANA y al repo
- [ ] Coordinar integración técnica de `model.py` en el pipeline
- [ ] Gestionar deadline Go Live del 4 de Mayo

---

## 🟡 DEADLINE: 4 DE MAYO — Go Live

### Todo el equipo
- [ ] Integración completa funcionando: `quick_filter` + `model.py` + `alerting.py` en el ciclo
- [ ] Prueba end-to-end verificada:
```
Log aparece en API → HANA → detección → webhook → SAP AI Security Team → Threat Resolved
```
- [ ] MTTD medido y documentado
- [ ] Pipeline estable en CF sin intervención manual

---

## 🟢 HASTA LA ELIMINATORIA (May 12–14)

### Viz Lead
- [ ] Dashboard ejecutivo en SAP Analytics Cloud terminado y conectado a HANA live
- [ ] Dashboard Streamlit operativo

### AI Specialist
- [ ] Modelo refinado con datos acumulados de múltiples ventanas
- [ ] Validación de que el modelo distingue ruido de amenazas reales

### CIE
- [ ] Al menos un incidente detectado documentado como reporte forense

### Todo el equipo
- [ ] Reporte estratégico final listo para presentar a SAP

---

## Referencia rápida — comandos críticos

```bash
# Monitorear pipeline en CF
cf logs sap-ai-soc --recent
cf logs sap-ai-soc          # tiempo real
cf app sap-ai-soc           # estado general

# Configurar webhook cuando llegue
cf set-env sap-ai-soc WEBHOOK_URL "https://..."
cf restage sap-ai-soc

# Verificar datos en HANA (SQL Console)
SELECT COUNT(*) FROM DBADMIN.RAW_LOGS_SISTEMA;
SELECT COUNT(*) FROM DBADMIN.RAW_LOGS_LLM;
SELECT TOP 10 log_id, event_timestamp, log_type FROM DBADMIN.RAW_LOGS_SISTEMA ORDER BY ingested_at DESC;
```

---

*Tracking generado el 27 de Abril 2026 — retomar aquí en nueva sesión de trabajo*
