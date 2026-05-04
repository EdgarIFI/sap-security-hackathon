# SAC ↔ HANA Live Connection — Project Documentation
## SAP AI Security Anomaly Detection Hackathon — TEC de Monterrey × SAP
**Date:** May 4, 2026  
**Author:** Cloud Integration Engineer  
**Status:** ✅ Live Connection operational — SAC reading from HANA in real time

---

## 1. Executive Summary

This document records the complete process of connecting SAP Analytics Cloud (SAC) to SAP HANA Cloud via **Live Connection** to build a real-time security dashboard. The dashboard reads from the `RAW_LOGS_LLM` table (populated by the ingestion pipeline every ~28 minutes) and displays token usage, cost, response times, provider distribution, and regional activity without manual data refresh.

**What was achieved:**
- HDI Container `SOC_DASHBOARD_HDI_DB_1` deployed with a Synonym and Calculation View
- Calculation View `CV_LOGS_LLM` exposing 3 measures and 6 attributes from `RAW_LOGS_LLM`
- `SAC_USER` with proper access roles reading data in real time via SAC

**Architecture delivered:**

```
DBADMIN.RAW_LOGS_LLM (table, ~70k+ records)
        │
        │  Synonym (RAW_LOGS_LLM.hdbsynonym)
        ▼
SOC_DASHBOARD_HDI_DB_1 (HDI Container)
        │
        │  Calculation View (CV_LOGS_LLM, type CUBE)
        │  Measures: LLM_TOTAL_TOKENS (sum), LLM_COST_USD (sum), LLM_RESPONSE_TIME (avg)
        │  Attributes: LLM_PROVIDER, LLM_MODEL_ID, LLM_STATUS, EVENT_TIMESTAMP, LOG_TYPE, REGION_NAME
        ▼
SAP Analytics Cloud
        │
        │  Live Connection via SAC_USER
        ▼
Real-time Security Dashboard
```

---

## 2. Prerequisites & Environment

| Component | Details |
|---|---|
| SAP BTP Subaccount | Trial account with Cloud Foundry enabled |
| Cloud Foundry Space | `dev` |
| SAP HANA Cloud | Instance running in BTP Trial (plan `hana-free`) |
| SAP Business Application Studio (BAS) | Dev Space type "SAP HANA Native Application" |
| SAP Analytics Cloud | Trial instance |
| CF CLI | Installed in BAS terminal |
| MTA Build Tool (`mbt`) | Installed globally via npm |
| Pipeline app | `sap-ai-soc-papoi` running in same CF space, writing to DBADMIN tables |

**Tools required in BAS terminal:**
- `mbt` — Multi-Target Application build tool
- `cf` — Cloud Foundry CLI
- `node` — Node.js runtime (v18+)

---

## 3. Users & Permissions Map

| User | Type | Purpose | Permissions |
|---|---|---|---|
| `DBADMIN` | Human (admin) | HANA administration, running GRANTs | Full admin on HANA trial |
| `SAC_USER` | Human (analytics) | SAC Live Connection login | SELECT on DBADMIN schema + HDI Container access roles |
| `PIPELINE_USER` | Technical (pipeline) | Pipeline writes to HANA | INSERT/UPDATE on DBADMIN tables — **never use for SAC** |
| `SOC_DASHBOARD_HDI_DB_1#OO` | Technical (HDI) | Object Owner — owns deployed objects | SELECT WITH GRANT OPTION on `DBADMIN.RAW_LOGS_LLM` |
| `SOC_DASHBOARD_HDI_DB_1#DI` | Technical (HDI) | Deployment Infrastructure | Auto-managed by HDI |

**Permission chronology (order matters):**

```
1. GRANT SELECT ON DBADMIN.RAW_LOGS_LLM TO "SOC_DASHBOARD_HDI_DB_1#OO" WITH GRANT OPTION
   ↳ When: AFTER first deploy (synonym only), BEFORE second deploy (CV)
   ↳ Why: #OO needs to read the external table through the synonym

2. CREATE USER SAC_USER PASSWORD <pwd> NO FORCE_FIRST_PASSWORD_CHANGE
   ↳ When: Before connecting SAC

3. GRANT SELECT ON SCHEMA DBADMIN TO SAC_USER
   ↳ When: After creating SAC_USER

4. GRANT "SOC_DASHBOARD_HDI_DB_1::access_role" TO SAC_USER
   GRANT "SOC_DASHBOARD_HDI_DB_1::external_privileges_role" TO SAC_USER
   ↳ When: AFTER successful CV deploy, BEFORE SAC connection
   ↳ Why: These roles allow SAC_USER to read objects inside the HDI Container
```

---

## 4. Table Schema — RAW_LOGS_LLM

The source table in HANA has the following structure:

| Column | Data Type | Role in CV |
|---|---|---|
| ID | INTEGER | Not exposed |
| LOG_ID | NVARCHAR | Not exposed |
| EVENT_TIMESTAMP | TIMESTAMP | Attribute |
| INGESTED_AT | TIMESTAMP | Not exposed |
| LOG_TYPE | NVARCHAR | Attribute |
| APPLICATION | NVARCHAR | Not exposed |
| MESSAGE | NVARCHAR | Not exposed |
| ENVIRONMENT | NVARCHAR | Not exposed |
| REGION_NAME | NVARCHAR | Attribute |
| REGION_CODE | NVARCHAR | Not exposed |
| MACRO_REGION | NVARCHAR | Not exposed |
| LLM_MODEL_ID | NVARCHAR | Attribute |
| LLM_PROVIDER | NVARCHAR | Attribute |
| LLM_STATUS | NVARCHAR | Attribute |
| LLM_ERROR_MESSAGE | NVARCHAR | Not exposed |
| LLM_PROMPT_CATEGORY | NVARCHAR | Not exposed |
| LLM_TOTAL_TOKENS | INTEGER | **Measure (SUM)** |
| LLM_COST_USD | DOUBLE | **Measure (SUM)** |
| LLM_RESPONSE_TIME | DOUBLE | **Measure (AVG)** |
| LLM_TEMPERATURE | DOUBLE | Not exposed |
| LLM_FINISH_REASON | NVARCHAR | Not exposed |

**Critical note:** Column names in HANA are UPPERCASE. The Calculation View XML must use exact names as they appear in HANA, not the original API field names (e.g., `LLM_RESPONSE_TIME` not `llm_response_time_ms`).

---

## 5. Project File Structure

```
soc_dashboard/
├── db/
│   ├── src/
│   │   ├── .hdiconfig                          ← HDI plugin registry (auto-generated)
│   │   ├── RAW_LOGS_LLM.hdbsynonym             ← Synonym pointing to DBADMIN.RAW_LOGS_LLM
│   │   └── CV_LOGS_LLM.hdbcalculationview      ← Calculation View (CUBE with 3 measures)
│   ├── .env                                     ← HDI Container credentials (NEVER commit)
│   ├── .env.backup                              ← Backup of original .env
│   ├── .gitignore                               ← Excludes .env*, default-env.json, node_modules
│   ├── default-env.json                         ← Generated from CF service key (NEVER commit)
│   ├── package.json
│   └── node_modules/
├── mta.yaml                                     ← MTA descriptor (version 0.0.3)
├── mta_archives/
│   └── soc_dashboard_0.0.3.mtar                ← Built archive for CF deploy
└── .gitignore
```

**Security-sensitive files that must NEVER be committed:**
- `db/.env` — HDI Container credentials (VCAP_SERVICES)
- `db/.env.backup` — Backup of original credentials
- `db/default-env.json` — Service key credentials
- Any file containing Bearer tokens, passwords, or connection strings

---

## 6. Step-by-Step Implementation Summary

### Phase 1 — Create MTA Project in BAS
1. Open BAS Dev Space (SAP HANA Native Application)
2. Login to CF: `cf login` → select space `dev`
3. File → New Project from Template → SAP HANA Database Project
4. Project name: `soc_dashboard`, Module: `db`, Database: HANA Cloud
5. Bind to CF: Yes → Service instance name: `soc_dashboard_hdi_db`
6. Fix `mta.yaml`: rename resource from default `hdi_db` to `soc_dashboard_hdi_db`

### Phase 2 — Create Synonym
1. Create `db/src/RAW_LOGS_LLM.hdbsynonym` pointing to `DBADMIN.RAW_LOGS_LLM`
2. Verify only `.hdiconfig` and the synonym exist in `db/src/`

### Phase 3 — First Deploy (Synonym only)
1. `mbt build` → `cf deploy mta_archives/soc_dashboard_0.0.1.mtar`
2. Deploy will succeed for the synonym registration
3. Verify synonym exists in HANA with SQL query

### Phase 4 — Grant Permissions to Object Owner
1. Identify #OO user: `SELECT USER_NAME FROM SYS.USERS WHERE USER_NAME LIKE '%SOC%' AND USER_NAME LIKE '%#OO%'`
2. Grant: `GRANT SELECT ON DBADMIN.RAW_LOGS_LLM TO "<OO_NAME>" WITH GRANT OPTION`
3. Verify with `GRANTED_PRIVILEGES` query

### Phase 5 — Create Calculation View (XML method)
1. Create `db/src/CV_LOGS_LLM.hdbcalculationview` with XML content
2. Must include at least one `<measure>` in `<baseMeasures>` (CUBE requirement)
3. Column names must match HANA exactly (UPPERCASE)

### Phase 6 — Second Deploy (Synonym + CV)
1. Bump version in `mta.yaml`
2. Clean build: `rm -rf mta_archives/ .soc_dashboard_mta_build_tmp/`
3. `mbt build` → `cf deploy`
4. Verify CV exists and returns data

### Phase 7 — Grant Access to SAC_USER
1. Create user if needed: `CREATE USER SAC_USER PASSWORD <pwd> NO FORCE_FIRST_PASSWORD_CHANGE`
2. Grant schema access: `GRANT SELECT ON SCHEMA DBADMIN TO SAC_USER`
3. Grant HDI roles: `access_role` and `external_privileges_role`
4. Test: connect as SAC_USER and `SELECT TOP 10` from the CV

### Phase 8 — Connect from SAC
1. Create Live Data connection to HANA Cloud using SAC_USER
2. Create model from CV_LOGS_LLM
3. Build dashboard with charts using measures and attributes

---

## 7. Error Log & Lessons Learned

### Error 1 — `"Unable to find service undefined"` in BAS editor
**Symptom:** Calculation View editor cannot search for Data Sources.  
**Root cause:** BAS visual editor has a known bug with `.env` binding format. The wizard generates credentials in a single-line compressed format that the editor can't parse.  
**Resolution:** Write the Calculation View XML directly instead of using the visual editor. The HDI deploy engine only reads the XML file, not the editor state.  
**Prevention:** Always have a fallback plan to write CV XML manually.

### Error 2 — `"The container's object owner is not authorized"`
**Symptom:** Deploy fails because #OO can't access the synonym target.  
**Root cause:** The HDI Container's Object Owner needs explicit SELECT on external tables.  
**Resolution:** `GRANT SELECT ON DBADMIN.RAW_LOGS_LLM TO "#OO_NAME" WITH GRANT OPTION`  
**Prevention:** Always grant permissions to #OO AFTER the first deploy (which creates the #OO user) and BEFORE deploying the Calculation View.

### Error 3 — `"Input name not set"` [34011]
**Symptom:** CV deploy fails with "Inconsistent calculation model."  
**Root cause:** The `<input>` element in the XML was missing the correct `node` attribute reference to the DataSource.  
**Resolution:** Use `<input node="RAW_LOGS_LLM">` (the DataSource ID without `#` prefix, without `<inputNodes>` wrapper).  
**Prevention:** Use the exact XML template provided in the AI reference document. The `node` attribute must match the `id` of the `<DataSource>` element.

### Error 4 — Deploy uses cached version
**Symptom:** After fixing the XML, deploy still shows the same error from a previous attempt.  
**Root cause:** CF caches the MTA archive. If the `.mtar` filename doesn't change, CF may reuse the old version.  
**Resolution:** Bump version in `mta.yaml`, delete `mta_archives/` and `.soc_dashboard_mta_build_tmp/`, rebuild and redeploy.  
**Prevention:** Always increment the version number when retrying a failed deploy.

### Error 5 — Column names mismatch
**Symptom:** CV references columns that don't exist in the table.  
**Root cause:** The HANA table uses normalized UPPERCASE names (e.g., `LLM_RESPONSE_TIME`) while the API documentation uses lowercase with different suffixes (e.g., `llm_response_time_ms`).  
**Resolution:** Query `TABLE_COLUMNS` to get exact column names before writing the CV XML.  
**Prevention:** Always verify column names with `SELECT COLUMN_NAME, DATA_TYPE_NAME FROM TABLE_COLUMNS WHERE TABLE_NAME = '<table>'` before creating any Calculation View.

### Error 6 — Orphaned HDI Containers from failed attempts
**Symptom:** Multiple schemas and technical users left behind from previous failed deployments (`SAC_PRUEBAS_2_*`).  
**Root cause:** Failed deploys create schemas, users, and roles that don't get cleaned up automatically.  
**Resolution:** If `DROP_CONTAINER` API is unavailable, leave orphans and use different project names. Revoke any GRANTs on production tables from orphaned users.  
**Prevention:** Use `cf undeploy <app> -f --delete-services` for clean teardowns. Always verify with `cf services` and HANA schema queries before creating new containers.

---

## 8. Golden Rules

1. **Synonym ALWAYS before Calculation View** — deploy synonym first, verify it exists, then add the CV
2. **GRANT to #OO before CV deploy** — without `WITH GRANT OPTION`, the CV can't propagate access
3. **Every CUBE needs at least one Measure** — no exceptions, deploy will fail
4. **Verify column names against HANA, not API docs** — names may differ
5. **Bump version on every retry** — prevents CF from using cached archives
6. **Clean build artifacts before retry** — `rm -rf mta_archives/ .soc_dashboard_mta_build_tmp/`
7. **Never share `.env`, `default-env.json`, Bearer tokens, or passwords**
8. **Never use `PIPELINE_USER` for SAC** — use dedicated `SAC_USER`
9. **Verify each step with SQL queries** — don't assume something worked
10. **HANA trial pauses on inactivity** — check it's Running before any deploy

---

## 9. Replication Checklist for New Tables

To replicate this process for another table (e.g., `RAW_LOGS_SISTEMA` or `ALERTS`):

- [ ] Verify table exists and has data: `SELECT COUNT(*) FROM DBADMIN.<TABLE>`
- [ ] Get exact column names: `SELECT COLUMN_NAME, DATA_TYPE_NAME FROM TABLE_COLUMNS WHERE TABLE_NAME = '<TABLE>'`
- [ ] Identify at least one numeric column for Measure (INTEGER or DOUBLE)
- [ ] Create `.hdbsynonym` file pointing to `DBADMIN.<TABLE>`
- [ ] Create `.hdbcalculationview` XML with correct column names and at least one Measure
- [ ] Grant SELECT on new table to #OO: `GRANT SELECT ON DBADMIN.<TABLE> TO "#OO" WITH GRANT OPTION`
- [ ] Build and deploy
- [ ] Verify CV returns data
- [ ] Grant HDI roles to SAC_USER (if not already granted)
- [ ] Add as new data source in SAC

---

*Document generated May 4, 2026 · Cloud Integration Engineer*  
*Update as the project evolves*
