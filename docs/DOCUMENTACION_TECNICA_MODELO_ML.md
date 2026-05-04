# Documentacion Tecnica del Modelo ML
## SAP AI Security Anomaly Detection — app/model.py
**Proyecto:** SAP AI Security Anomaly Detection Hackathon · TEC de Monterrey x SAP  
**Autor:** Cloud Integration Engineer  
**Fecha:** 4 Mayo 2026  
**Estado:** Desplegado en produccion (Cloud Foundry) y verificado con datos reales

---

## 1. Que es esto — resumen en una oracion

El modelo ML es un sistema de deteccion de anomalias no supervisado que aprende el comportamiento normal de los logs de seguridad SAP a partir de historia acumulada, y detecta automaticamente patrones que se desvian de ese baseline, complementando las reglas deterministicas de `quick_filter` con inteligencia estadistica.

---

## 2. Por que ML — y por que no supervisado

### El problema fundamental

No tenemos etiquetas. No existe una columna `es_ataque = True/False` en los datos. El sistema SAP genera logs continuamente, pero nadie ha clasificado manualmente cuales son anomalos y cuales son normales. Esto descarta todos los algoritmos de clasificacion supervisada (Random Forest con labels, XGBoost, redes neuronales de clasificacion).

### La solucion: aprendizaje no supervisado

Los algoritmos no supervisados aprenden la estructura de los datos sin labels. Su logica es: aprende que es "normal" a partir de la mayoria de los datos, y marca como anomalo lo que no encaja con ese patron aprendido.

Este es exactamente el paradigma correcto para un SOC en tiempo real donde no existe ground truth historico.

---

## 3. Los tres modelos — que hace cada uno y por que

### Modelo 1 y 2: Isolation Forest (IF_sistema e IF_llm)

**Que es Isolation Forest:**
Isolation Forest aísla anomalias en lugar de modelar normalidad. Su logica es geometrica: un punto anomalo es mas facil de aislar que uno normal. El algoritmo construye arboles de decision aleatorios y mide cuantos cortes necesita para aislar cada punto. Los puntos que se aislan en pocos cortes son anomalos.

**Por que es correcto para este problema:**
- Funciona bien con datos de alta dimension (nuestros vectores tienen 9–12 features)
- No asume ninguna distribucion subyacente — importante porque HTTP_STATUS, LOG_TYPE y tiempos de respuesta no son Gaussianos
- Es eficiente: O(t × n × log n) donde t=200 arboles y n=max_samples=256, lo que significa que procesa 127,000 registros historicos en segundos
- Tolera datos contaminados — si hay anomalias en el training set, el modelo no se rompe

**IF_sistema** opera sobre logs de sistema (INFO, WARNING, ERROR, AUDIT, DEBUG, PERF, SECURITY) con features de HTTP_STATUS, LOG_TYPE, APPLICATION, REGION_NAME y hora UTC.

**IF_llm** opera sobre logs de LLM (LLM_REQUEST, LLM_ERROR, LLM_TIMEOUT) con features de costo, tiempo de respuesta, tokens, status del modelo y modelo usado.

**Por que dos modelos separados y no uno:**
Los logs de sistema tienen columnas LLM completamente vacias (NaN por diseno) y viceversa. Mezclarlos en un solo vector significaria que el modelo aprenderia el patron de nulidad en lugar de anomalias reales. Dos modelos separados sobre poblaciones homogeneas dan resultados mucho mas precisos.

### Modelo 3: Local Outlier Factor (LOF_ip)

**Que es LOF:**
LOF calcula la densidad local de cada punto comparandola con la densidad de sus vecinos mas cercanos. Un punto con densidad mucho menor que sus vecinos es un outlier local. A diferencia de Isolation Forest que opera a nivel global, LOF detecta anomalias relativas al contexto local.

**Por que es correcto para comportamiento por IP:**
Una IP que hace 50 requests con 80% de errores 401 es claramente anomala. Pero una IP con 2 requests y 1 error 401 podria ser completamente normal. LOF captura esta relatividad: compara cada IP contra sus IPs vecinas en el espacio de comportamiento, no contra un umbral global fijo.

**Que analiza LOF_ip:**
En lugar de analizar cada log individualmente, LOF trabaja sobre una tabla agregada: una fila por IP unica, con metricas de comportamiento acumulado en la ventana de 30 minutos. Con ~105 IPs unicas, esta tabla tiene ~105 filas — LOF con n_neighbors=20 es computacionalmente trivial en este tamano.

---

## 4. Feature engineering — como se transforman los datos

### Por que feature engineering importa

Los algoritmos sklearn no pueden recibir strings ni timestamps directamente. Tampoco pueden recibir columnas con NaN. El feature engineering es la transformacion que convierte los datos crudos de HANA en vectores numericos validos para los modelos.

### Features de Sistema (IF_sistema)

Los datos crudos de `RAW_LOGS_SISTEMA` se transforman en estas 9 features:

```
HTTP_STATUS (string) → status_family (int: 2/3/4/5)
                     → is_4xx (bool → int: 0/1)
                     → is_5xx (bool → int: 0/1)
                     → is_401_or_403 (bool → int: 0/1)
                     → is_429 (bool → int: 0/1)

EVENT_TIMESTAMP      → hour_utc (int: 0-23)

LOG_TYPE (string)    → OrdinalEncoder → int
APPLICATION (string) → OrdinalEncoder → int
REGION_NAME (string) → OrdinalEncoder (max 32 categorias) → int
```

**Por que OrdinalEncoder y no OneHotEncoder para las categoricas:**
Isolation Forest usa arboles de decision internamente. Los arboles hacen splits numericos — pueden operar directamente sobre enteros ordinales sin necesitar one-hot encoding. OneHotEncoder expandiria el espacio de 1 columna a N columnas binarias, aumentando la dimension innecesariamente para un modelo de arboles.

**Por que `status_family` en lugar del codigo HTTP raw:**
Hay 16 valores distintos de HTTP_STATUS. En lugar de encodear los 16 valores, extraemos la informacion semantica relevante: la familia (2xx=normal, 4xx=error_cliente, 5xx=error_servidor) y flags booleanos para los codigos de mayor interes de seguridad (401, 403, 429).

### Features de LLM (IF_llm)

```
LLM_COST_USD (float)        → log1p(LLM_COST_USD)
LLM_RESPONSE_TIME (float)   → log1p(LLM_RESPONSE_TIME)
LLM_TOTAL_TOKENS (int)      → log1p(LLM_TOTAL_TOKENS)
EVENT_TIMESTAMP              → hour_utc (int: 0-23)

LLM_STATUS (string)         → OrdinalEncoder → int
LLM_MODEL_ID (string)       → OrdinalEncoder → int
LLM_PROVIDER (string)       → OrdinalEncoder → int
LOG_TYPE (string)            → OrdinalEncoder → int
```

**Por que log1p en las columnas numericas LLM:**
Las tres metricas numericas de LLM tienen distribuciones con cola pesada (heavy-tail):
- `LLM_COST_USD`: rango [0.000007, 0.139] — diferencia de 20,000x entre min y max
- `LLM_RESPONSE_TIME`: rango [200ms, 34,999ms] — diferencia de 174x
- `LLM_TOTAL_TOKENS`: rango [84, 3,498] — diferencia de 41x

Sin transformar, los valores extremos dominarian el modelo y enmascararian anomalias de valores intermedios. `log1p(x) = log(1+x)` comprime el rango de forma monotona: log1p(34,999) = 10.46, mucho mas manejable que 34,999 directo.

### Tabla de comportamiento por IP (LOF_ip)

Agregacion por IP a partir de `RAW_LOGS_SISTEMA`:

```
CLIENT_IP → una fila por IP unica (~105 filas)
         → event_count: total de eventos
         → distinct_paths: rutas unicas visitadas
         → distinct_apps: aplicaciones accedidas
         → ratio_4xx: fraccion de errores de cliente
         → ratio_5xx: fraccion de errores de servidor
         → ratio_security: fraccion de eventos SECURITY
         → has_401_or_403: 1 si hubo intentos de autenticacion fallidos
         → has_429: 1 si fue bloqueada por rate limiting
         → n_distinct_status: variedad de codigos HTTP (IPs que escanean tienen muchos)
```

**Por que RobustScaler antes de LOF y no antes de IF:**
LOF calcula distancias euclidianas entre puntos. Si `event_count` va de 1 a 1000 y `ratio_4xx` va de 0 a 1, la distancia estara dominada por `event_count`. RobustScaler normaliza cada feature restando la mediana y dividiendo por el rango intercuartilico — robusto a outliers porque no usa la media ni la desviacion estandar, que se distorsionan con valores extremos.

IF en cambio no calcula distancias — hace splits aleatorios — por eso OrdinalEncoder es suficiente para las categoricas y RobustScaler no es necesario para las numericas simples.

---

## 5. Thresholding — como se decide que es anomalo

### El problema del threshold

Isolation Forest retorna un `decision_function` donde valores mas negativos = mas anomalos. LOF retorna un `negative_outlier_factor` donde valores mas negativos = mas anomalos. Pero, donde exactamente esta la linea entre "anomalo" y "normal"? No hay una respuesta universalmente correcta — depende de los datos.

### Modo Cold-Start (menos de 20 ventanas acumuladas)

Cuando el sistema arranca sin historia suficiente, entrena y scorea sobre la misma ventana actual. El threshold se calcula con IQR:

```
threshold = Q1 - 1.5 * IQR
```

donde `Q1` es el percentil 25 y `IQR = Q3 - Q1`. Todo lo que cae por debajo de este umbral se considera anomalo. Es el mismo criterio que se usa para detectar outliers en boxplots — conservador y ampliamente establecido.

### Modo Historico (20+ ventanas acumuladas — nuestro caso actual: 309 ventanas)

Con historia suficiente, el modelo entrena sobre las ultimas 24 horas de datos (excluyendo la ventana actual) y scorea solo la ventana nueva. El threshold usa MAD (Median Absolute Deviation):

```
threshold = mediana - 3.5 * (MAD / 0.6745)
```

donde `MAD = mediana(|score - mediana(scores)|)`.

**Por que MAD en lugar de media y desviacion estandar:**
La media y la desviacion estandar son sensibles a outliers — si hay 5 scores extremadamente negativos, distorsionan el calculo. MAD usa la mediana, que es robusta a outliers. El factor 0.6745 es una constante de calibracion que hace MAD comparable a la desviacion estandar bajo distribucion normal. El factor 3.5 es el umbral del modified z-score — equivalente a "mas de 3.5 desviaciones estandar de la mediana", que es muy conservador y minimiza falsos positivos.

---

## 6. Asignacion de severidad

Una vez que se identifican los eventos anomalos, se ordenan de mas a menos anomalo (score mas negativo primero) y se asigna severidad segun su posicion en el ranking:

```
Top 10% de anomalias → HIGH
Top 10-30%           → MEDIUM
Resto                → LOW
```

Este ranking relativo es mas robusto que umbrales absolutos: si hay 3 anomalias, la primera es HIGH, la segunda puede ser MEDIUM o LOW dependiendo de que tan separadas esten en el ranking.

---

## 7. Como se integra en el pipeline

### Dos ciclos de deteccion

```
Ciclo corto (cada 2 minutos):
    quick_filter.filtrar_amenazas(df_nuevos)
    → reglas deterministicas sobre registros nuevos del ciclo
    → MTTD ~1 segundo
    → detecta: brute_force, path_scan, security_event, llm_timeout, etc.

Ciclo largo (cada 28 minutos):
    model.analizar_ventana(conn)
    → lee historia de 24h desde HANA (~127k sistema + ~43k LLM)
    → entrena IF_sistema, IF_llm
    → scorea ventana actual
    → construye tabla IP, corre LOF
    → retorna anomalias estadisticas
    → detecta: ml_sistema_anomaly, ml_llm_anomaly, ml_ip_anomaly
```

### Flujo de conexiones (sin duplicados)

```
pipeline_loop.py abre conn_ml
    |
    +-- hana_reader.py usa conn_ml para leer
    |       +-- leer_sistema_historico(conn_ml, horas=24)
    |       +-- leer_llm_historico(conn_ml, horas=24)
    |       +-- leer_sistema_ventana_actual(conn_ml)
    |       +-- leer_llm_ventana_actual(conn_ml)
    |
    +-- feature_eng.py transforma DataFrames en vectores
    |
    +-- model.py entrena IF/LOF y retorna list[dict]
    |
pipeline_loop.py cierra conn_ml

pipeline_loop.py abre conn_alerting_ml (solo si hay anomalias)
    |
    +-- alerting.py INSERT en DBADMIN.ALERTS (alerted=0)
    +-- alerting.py POST /alert a API SAP -> HTTP 201
    +-- alerting.py UPDATE DBADMIN.ALERTS (alerted=1)
    |
pipeline_loop.py cierra conn_alerting_ml
```

**Por que dos conexiones separadas:** una conexion para lectura (modelo) y una para escritura (alertas). Las conexiones HANA no deben mezclarse entre operaciones de lectura intensiva y escritura transaccional. Ademas, si el modelo falla, la conexion de alertas nunca se abre — cero recursos desperdiciados.

---

## 8. Contrato publico de model.py

```python
def analizar_ventana(conn, window_start=None) -> list[dict]:
    """
    Entrada: conexion HANA activa (viene de pipeline_loop)
    Salida:  lista de hasta 5 dicts, cada uno con:
        {
            "alert_type": "ml_sistema_anomaly" | "ml_llm_anomaly" | "ml_ip_anomaly",
            "severity":   "low" | "medium" | "high",
            "details":    str <= 250 chars con score, rank y contexto del evento,
            "log_id":     LOG_ID del registro anomalo en HANA,
            "event_time": EVENT_TIMESTAMP ISO del registro
        }

    Garantias:
    - Nunca lanza excepciones (cualquier error retorna [])
    - conn=None retorna [] inmediatamente
    - Cap duro de 5 alertas (ordenadas por severidad)
    - Tiempo de ejecucion: < 10 segundos sobre 127k registros historicos
    """
```

---

## 9. Verificacion en produccion — primer ciclo real

**Fecha:** 4 Mayo 2026, 09:16:42 UTC  
**Ventana analizada:** 09:00:00 → 09:30:00 UTC  
**Registros de entrenamiento:** ~127,382 sistema + ~42,908 LLM (ultimas 24h)  
**Modo:** HISTORICO (309 ventanas acumuladas)  
**Tiempo de ejecucion:** ~6 segundos  
**Resultado:** 5 anomalias `ml_ip_anomaly` detectadas

```
ALERTA 1: ml_ip_anomaly HIGH   | alerted=1 | detection_source=model_ml
ALERTA 2: ml_ip_anomaly MEDIUM | alerted=1 | detection_source=model_ml
ALERTA 3: ml_ip_anomaly MEDIUM | alerted=1 | detection_source=model_ml
ALERTA 4: ml_ip_anomaly LOW    | alerted=1 | detection_source=model_ml
ALERTA 5: ml_ip_anomaly LOW    | alerted=1 | detection_source=model_ml
```

Las 5 alertas llegaron a la API SAP con HTTP 201 y quedaron persistidas en `DBADMIN.ALERTS` con `alerted=1`. Trazabilidad completa confirmada.

El hecho de que todas sean `ml_ip_anomaly` (LOF) y ninguna sea `ml_sistema_anomaly` o `ml_llm_anomaly` (IF) indica que en esta ventana no hay eventos individuales extremadamente anomalos, pero si hay IPs con patrones de comportamiento inusuales en conjunto. Esto es exactamente lo que distingue LOF de IF: LOF captura anomalias de comportamiento agregado que IF no veria.

---

## 10. Como cubrimos las rubricas del hackathon

### Criterio 1 — Operational Efficiency & Real-Time Response (40%)

| Metrica | Objetivo | Resultado |
|---|---|---|
| MTTD | <= 2 minutos | ~1 segundo (quick_filter) |
| Automatizacion | End-to-end sin intervencion | Pipeline 24/7 en CF |
| Alert latency | Minima | 188-310ms por POST /alert |
| Cobertura | Continua | Cada 2 min (reglas) + cada 28 min (ML) |

### Criterio 2 — SAP Ecosystem Integration & Tooling (25%)

| Componente | Uso real |
|---|---|
| SAP BTP | Plataforma de despliegue |
| Cloud Foundry | Runtime del pipeline |
| SAP HANA | Persistencia de 1M+ logs y alertas |
| SAP Alert API | POST /alert con HTTP 201 confirmado |

### Criterio 3 — Architecture & MLOps Maturity (20%)

| Aspecto | Implementacion |
|---|---|
| Separacion de componentes | 4 modulos independientes con responsabilidad unica |
| Escalabilidad | max_samples=256 permite escalar a millones de registros sin cambios |
| Robustez | Cada modulo captura excepciones — fallo de ML no rompe ingesta |
| Reproducibilidad | random_state=42 en todos los modelos |
| Observabilidad | Logging detallado en cada paso del ciclo |

### Criterio 4 — Business Impact & Strategic Analysis (15%)

| Entregable | Estado |
|---|---|
| Alertas reales detectadas | 8+ quick_filter + 5 model_ml en produccion |
| Reporte forense | Pendiente — datos disponibles en DBADMIN.ALERTS |
| Narrativa ejecutiva | Pendiente reporte estrategico |

---

## 11. Dependencias requeridas

```
scikit-learn>=1.8.0   # IsolationForest, LocalOutlierFactor, OrdinalEncoder, RobustScaler
pandas>=2.0           # DataFrames
numpy>=1.24           # operaciones vectoriales, log1p, percentiles
hdbcli>=2.0           # conexion SAP HANA
```

Todas incluidas en `requirements.txt` y verificadas en el buildpack de Cloud Foundry.

---

## 12. Archivos del sistema ML

```
app/
├── hana_reader.py    extraccion desde HANA, queries con nombres reales de columna
├── feature_eng.py    transformaciones puras pandas/numpy, sin sklearn ni HANA
└── model.py          IF x2 + LOF, thresholding, contrato analizar_ventana()

pipeline_loop.py      orquestacion: ciclo corto (quick_filter) + ciclo largo (model)
```

**Pruebas standalone:**
```bash
python -m app.hana_reader   # verifica conexion y extraccion real desde HANA
python -m app.feature_eng   # verifica transformaciones con datos sinteticos
python -m app.model         # verifica modelos con datos sinteticos
```

---

*Documento tecnico del sistema ML — 4 Mayo 2026*  
*SAP AI Security Anomaly Detection Hackathon · TEC de Monterrey x SAP*
