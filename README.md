# SAP AI Security SOC — Anomaly Detection Pipeline

**Live Security Operations Center for SAP BTP**
TEC de Monterrey × SAP Hackathon · Phase 1 · May 2026

---

## 1. Project Overview

This project implements a fully automated **Security Operations Center (SOC)** that monitors SAP Business Technology Platform (BTP) systems in real time. The pipeline ingests security logs from the SAP API every 2 minutes, detects threats using a dual-layer detection engine, and dispatches alerts to SAP without human intervention.

The system follows the security operations cycle:

```
OBSERVE → ANALYZE → DETECT → RESPOND
```

Detection combines **6 deterministic rules** (brute force, path scanning, security events, LLM cost abuse, timeouts, slow responses) with **3 unsupervised Machine Learning models** (Isolation Forest for system logs, Isolation Forest for LLM logs, and Local Outlier Factor for IP behavior profiling). The pipeline has been running continuously on SAP Cloud Foundry since April 24, 2026.

### Key Performance Metrics

| Metric | Value |
|---|---|
| Mean Time to Detect (MTTD) | ~1 second |
| Alert latency (detection → SAP confirmation) | 188–310 ms |
| Pipeline uptime | 24/7 since April 24 |
| Human intervention required | 0 |
| System logs ingested | 1,734,606+ records |
| LLM logs ingested | 1,027,563+ records |
| Alerts confirmed by SAP | 1,537+ alerts |

---

## 2. System Architecture

The system is deployed entirely within the SAP ecosystem: Cloud Foundry for compute, HANA Cloud for persistence, and SAP Analytics Cloud (SAC) for visualization.

### High-Level Data Flow

```
SAP API (source of security logs)
    │
    │  HTTPS + Bearer Token, every 2 minutes
    ▼
pipeline_loop.py  (running on Cloud Foundry, 24/7)
    │
    ├──► CSV backup (one file per 30-min window)
    │
    └──► SAP HANA Cloud
            ├── RAW_LOGS_SISTEMA  (HTTP access logs)
            ├── RAW_LOGS_LLM      (AI model interaction logs)
            └── ALERTS            (detected threats)
                    │
                    │  Calculation Views (via HDI Container)
                    ▼
            SAP Analytics Cloud — SOC Dashboard (Live Connection)
```

### Two Detection Cycles

**Short cycle (every 2 minutes):** The Quick Filter applies 6 deterministic rules to each new batch of records. Detects known attack patterns (brute force, path scanning, etc.) in approximately 1 second.

**Long cycle (every 28 minutes):** The ML module trains on 24 hours of historical data and scores the current window. Detects statistically anomalous behavior that rule-based systems cannot capture.

Both cycles feed into the same alerting module, which sends confirmed threats to SAP via `POST /alert` and records them in the ALERTS table.

For the complete architecture diagram, see [`docs/ARCHITECTURE_DIAGRAM.pdf`](docs/ARCHITECTURE_DIAGRAM.pdf).

For detailed technical documentation of each component, see [`docs/TECHNICAL_REPORT.md`](docs/TECHNICAL_REPORT.md).

---

## 3. Repository Structure

```
sap-security-hackathon/
│
├── pipeline_loop.py              ← Main orchestrator: 2-min polling loop
├── manifest.yml                  ← Cloud Foundry deployment configuration
├── requirements.txt              ← Python dependencies
├── runtime.txt                   ← Python version specification (3.11.x)
├── .env.examples                 ← Template for environment variables
├── .gitignore
├── README.md                     ← This file
│
├── app/                          ← Pipeline modules
│   ├── config.py                 ← Credential management and environment loading
│   ├── ingest.py                 ← API extraction, CSV persistence, HANA upsert
│   ├── hana_client.py            ← HANA connection, table creation, write operations
│   ├── hana_reader.py            ← HANA read operations for ML (historical + window)
│   ├── quick_filter.py           ← 6 deterministic threat detection rules
│   ├── alerting.py               ← Alert dispatch (POST /alert) + HANA logging
│   ├── feature_eng.py            ← Feature engineering for ML models
│   └── model.py                  ← 3 unsupervised ML models (IF×2 + LOF)
│
├── data/
│   └── SCHEMA.md                 ← Documentation of the 43 raw API columns
│
└── docs/
    ├── ARCHITECTURE.md           ← Module documentation with usage examples
    ├── ARCHITECTURE_DIAGRAM.pdf  ← Visual system architecture diagram
    ├── TECHNICAL_REPORT.md       ← Full technical report (Phase 1 deliverable)
    ├── FORENSIC_REPORT.pdf       ← Incident analysis and threat findings
    └── API_DICTIONARY.md         ← Complete API endpoint reference
```

---

## 4. Getting Started

### 4.1 Prerequisites

- Python 3.11.x
- `pip` package manager
- Access to the team `.env` file (provided separately, never committed)
- SAP HANA Cloud instance credentials (provided by the Data Architect)
- Cloud Foundry CLI (`cf`) for deployment

### 4.2 Environment Setup

Clone the repository and install dependencies:

```bash
git clone <repository-url>
cd sap-security-hackathon
pip install -r requirements.txt
```

Create a `.env` file in the project root using the template:

```bash
cp .env.examples .env
```

Fill in the required values:

```env
# SAP Hackathon API
API_BASE_URL=<your_api_base_url>
BEARER_TOKEN=<your_team_bearer_token>

# SAP HANA Cloud
HANA_HOST=<your_hana_host>.hanacloud.ondemand.com
HANA_PORT=443
HANA_USER=<your_hana_user>
HANA_PASSWORD=<your_hana_password>
```

> **Security:** The `.env` file contains credentials and must never be committed to Git. It is listed in `.gitignore`.

### 4.3 Running Locally

Verify the API is accessible:

```bash
python app/ingest.py
```

Run the full pipeline (ingestion + detection + alerting, every 2 minutes):

```bash
python pipeline_loop.py
```

The pipeline will log to both the console and `pipeline_v2.log`. Press `Ctrl+C` to stop gracefully.

To test individual modules independently:

```bash
python -m app.hana_client       # Test HANA connection and table setup
python -m app.hana_reader       # Test HANA read operations
python -m app.quick_filter      # Test detection rules with synthetic data
python -m app.feature_eng       # Test feature engineering
python -m app.model             # Test ML models with synthetic data
python app/alerting.py          # Test alert dispatch (sends a real alert)
```

### 4.4 Deploying to Cloud Foundry

Log in to SAP BTP Cloud Foundry:

```bash
cf login -a <your_cf_api_endpoint>
```

Push the application:

```bash
cf push
```

Cloud Foundry reads `manifest.yml`, installs `requirements.txt`, and starts `pipeline_loop.py`. HANA credentials are injected automatically via the service binding declared in the manifest.

Set the remaining environment variables:

```bash
cf set-env sap-ai-soc-papoi BEARER_TOKEN "<your_team_bearer_token>"
cf set-env sap-ai-soc-papoi API_BASE_URL "<your_api_base_url>"
cf restage sap-ai-soc-papoi
```

> **Alternative for Cloud Foundry development:** You can also use a `.venv` virtual environment on your Cloud Foundry workspace and provide your credentials there. This allows you to manage environment variables locally within the CF environment without relying exclusively on `cf set-env`.

Monitor the running pipeline:

```bash
cf logs sap-ai-soc-papoi --recent    # Recent logs
cf logs sap-ai-soc-papoi             # Live stream
cf app sap-ai-soc-papoi              # Status, memory, uptime
cf restart sap-ai-soc-papoi          # Restart if needed
```

---

## 5. Pipeline Modules

Each module in `app/` has a single responsibility. The pipeline orchestrator (`pipeline_loop.py`) calls them in sequence. If any non-critical module fails to import, the pipeline continues operating in degraded mode — ingestion never stops.

| Module | Responsibility | Called by |
|---|---|---|
| `config.py` | Load credentials from `.env` or `VCAP_SERVICES` | All modules |
| `ingest.py` | Extract logs from API, deduplicate, save to CSV and HANA | `pipeline_loop.py` |
| `hana_client.py` | HANA connection, table creation, upsert operations | `ingest.py` |
| `hana_reader.py` | Read historical and current-window data from HANA | `model.py` |
| `quick_filter.py` | Apply 6 deterministic detection rules to new records | `pipeline_loop.py` (every 2 min) |
| `alerting.py` | Build alert message, POST to SAP API, log in HANA | `pipeline_loop.py` |
| `feature_eng.py` | Transform raw data into numeric features for ML | `model.py` |
| `model.py` | Run 3 ML models and return anomaly alerts | `pipeline_loop.py` (every 28 min) |

For detailed documentation of each module's public contracts, parameters, and usage examples, see [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md).

---

## 6. Detection Engine

### Quick Filter — Deterministic Rules (every 2 minutes)

| Rule | Threat Detected | Trigger Condition | Severity |
|---|---|---|---|
| Brute Force | Credential guessing attacks | ≥ 5 HTTP 401/403 from same IP in one batch | HIGH |
| Path Scanning | Directory/vulnerability scanning | ≥ 3 HTTP 404 to suspicious paths from same IP | MEDIUM–HIGH |
| Security Events | SAP-flagged security activity | Any log with `sap_function_log_type = 'SECURITY'` | MEDIUM–HIGH |
| High-Cost LLM Errors | AI cost manipulation attacks | `LLM_ERROR` with `llm_cost_usd > $1.00` | HIGH |
| LLM Timeouts | AI service degradation | Any `LLM_TIMEOUT` event | MEDIUM |
| Slow LLM Responses | AI resource exhaustion | `llm_response_time_ms > 10,000` | LOW–MEDIUM |

### ML Models — Statistical Anomaly Detection (every 28 minutes)

| Model | Algorithm | Scope | What It Detects |
|---|---|---|---|
| IF Sistema | Isolation Forest | Individual system log events | Events with unusual HTTP status, log type, region, or timing |
| IF LLM | Isolation Forest | Individual LLM log events | Unusual cost, response time, token usage, or model/provider |
| LOF IP | Local Outlier Factor | Aggregated behavior per IP | IPs whose overall activity pattern deviates from their peers |

All three models are **unsupervised** — they learn what is normal from historical data and flag deviations. No labeled attack data is required.

Key hyperparameters: `n_estimators=200`, `max_samples=256`, `contamination='auto'`, `n_neighbors=20`. Thresholding uses IQR in cold-start mode and MAD-based modified z-scores in historical mode. Maximum 5 ML alerts per cycle, prioritized by severity.

For the complete ML methodology, feature engineering details, and production results, see [`docs/TECHNICAL_REPORT.md`](docs/TECHNICAL_REPORT.md).

---

## 7. Database Schema

The system uses three tables in SAP HANA Cloud, under the `DBADMIN` schema:

| Table | Contents | Records (May 2026) |
|---|---|---|
| `RAW_LOGS_SISTEMA` | HTTP access logs (INFO, WARNING, ERROR, AUDIT, DEBUG, PERF, SECURITY) | 1,734,606+ |
| `RAW_LOGS_LLM` | AI model interaction logs (LLM_REQUEST, LLM_ERROR, LLM_TIMEOUT) | 1,027,563+ |
| `ALERTS` | Detected threats with detection source, severity, and confirmation status | 1,537+ |

Two separate log tables are used because system logs and LLM logs have entirely different column sets. A single table would have approximately 50% NULL values per row.

The ALERTS table tracks both detection and confirmation: `ALERTED = 0` means detected but not yet sent; `ALERTED = 1` means SAP confirmed receipt (HTTP 201).

For full column definitions, data types, and the column name mapping from raw API to HANA, see [`docs/TECHNICAL_REPORT.md`](docs/TECHNICAL_REPORT.md). For the raw API column documentation, see [`data/SCHEMA.md`](data/SCHEMA.md).

### SAP Analytics Cloud Connectivity

The SOC Dashboard in SAP Analytics Cloud (SAC) reads data from HANA via a **Live Connection**. SAC does not import or copy data — it queries HANA directly, so dashboards reflect the latest ingested records.

Each HANA table exposed to SAC requires a **Calculation View** deployed through an HDI Container. The current deployment includes Calculation Views for all three tables (`RAW_LOGS_SISTEMA`, `RAW_LOGS_LLM`, and `ALERTS`). A dedicated read-only database user provides SAC with the minimum permissions required to query the views.

For the complete SAC connection setup, Calculation View configuration, HDI Container deployment, and the GRANT chain required between database users, see [`docs/TECHNICAL_REPORT.md`](docs/TECHNICAL_REPORT.md).

---

## 8. Alerting System

Alerts are sent to the SAP API via `POST /alert` using the same base URL and Bearer token as log ingestion. No separate webhook URL is required.

**Message format:**

```
WHAT: <threat_type>. WHEN: <ISO_timestamp>. WHY: <evidence>.
```

Maximum 300 characters. The full evidence text (up to 2,000 characters) is stored in the HANA ALERTS table for forensic purposes.

**Reliability:** Failed alerts are retried up to 3 times with exponential backoff (1s → 2s). HTTP 4xx errors are not retried (same payload would fail again). Duplicate detection prevents re-sending alerts for the same `log_id`.

For documented incidents and threat analysis, see [`docs/FORENSIC_REPORT.pdf`](docs/FORENSIC_REPORT.pdf).

---

## 9. Security and Credentials

### Credential Management

All credentials are stored in environment variables — never in source code or version control.

| Environment | How credentials are provided |
|---|---|
| Local development | `.env` file in project root (listed in `.gitignore`) |
| Cloud Foundry | `cf set-env` for API credentials; `VCAP_SERVICES` for HANA (auto-injected via service binding). Alternatively, a `.venv` environment can be used to manage credentials within the CF workspace |

`config.py` is the only module that reads credentials. All other modules import from `config.py` — they never access `.env` or environment variables directly.

### Database User Separation

The HANA instance uses separate users with distinct permission levels:

| User | Purpose | Access Level |
|---|---|---|
| Database administrator | Infrastructure setup, table creation, permission grants | Full access |
| Pipeline user | Automated read/write by the running pipeline | Read + write on data tables |
| Dashboard user | SAP Analytics Cloud data source | Read-only |

No user has more permissions than its role requires. The pipeline user cannot modify table structures. The dashboard user cannot write data.

### Repository Safety Checklist

Before making the repository public, verify:

```bash
# Ensure no .env file is tracked
git log --all --diff-filter=A -- .env

# Ensure no credentials in commit history
git log --all -p | grep -i "bearer\|password\|token" | head -20

# Verify .gitignore includes sensitive patterns
cat .gitignore
```

---

## 10. Deliverables — Phase 1

| Deliverable | Format | Location |
|---|---|---|
| Repository | GitHub (public) | This repository |
| Technical Report | Markdown | [`docs/TECHNICAL_REPORT.md`](docs/TECHNICAL_REPORT.md) |
| Architecture Diagram | PDF | [`docs/ARCHITECTURE_DIAGRAM.pdf`](docs/ARCHITECTURE_DIAGRAM.pdf) |
| Forensic Report | PDF | [`docs/FORENSIC_REPORT.pdf`](docs/FORENSIC_REPORT.pdf) |
| Video | YouTube / Google Drive | [Link placeholder — update before submission] |

---

## 11. References and Documentation Index

| Document | Description |
|---|---|
| [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) | Detailed module documentation with public contracts and usage examples |
| [`docs/TECHNICAL_REPORT.md`](docs/TECHNICAL_REPORT.md) | Complete technical report: architecture decisions, ML methodology, database schema, deployment, and business impact |
| [`docs/ARCHITECTURE_DIAGRAM.pdf`](docs/ARCHITECTURE_DIAGRAM.pdf) | Visual architecture diagram showing all components and data flows |
| [`docs/FORENSIC_REPORT.pdf`](docs/FORENSIC_REPORT.pdf) | Analysis of real incidents detected by the system |
| [`docs/API_DICTIONARY.md`](docs/API_DICTIONARY.md) | Complete API reference: endpoints, schemas, authentication, and response formats |
| [`data/SCHEMA.md`](data/SCHEMA.md) | Documentation of the 43 columns in the raw API response |
