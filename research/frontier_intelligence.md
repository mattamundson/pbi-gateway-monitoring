# Frontier Intelligence Layer for the Fabric Gateway Monitor
## AIOps North Star: From Passive Dashboard to Predictive, Self-Explaining, Self-Healing System

> **Audience:** Principal data engineer building an open-source Fabric-native gateway monitoring tool.  
> **Date:** June 30, 2026  
> **Feasibility key:** [Feasible-now] | [Feasible-with-effort] | [Experimental] | [Speculative]

---

## Executive Summary

The gateway monitor already captures the right signals. The intelligence layer is what transforms those signals into **foresight, explanation, and action**. This report maps four frontier capabilities — anomaly detection & forecasting, LLM-assisted root-cause analysis, self-healing automation, and advanced differentiating analytics — onto the Fabric-native technology stack available in mid-2026, with primary-source citations and honest feasibility grades for each.

**Top 5 differentiating capabilities, ranked by impact × feasibility:**

| Rank | Capability | Feasibility | Core Technology |
|------|-----------|-------------|-----------------|
| 1 | KQL `series_decompose_anomalies` + `series_decompose_forecast` on gateway time-series | [Feasible-now] | Eventhouse KQL, drop-in |
| 2 | Fabric Data Agent (NL2KQL) over Eventhouse — "why did Sales refresh fail?" | [Feasible-now] | GA as of 2026 |
| 3 | Activator → User Data Function closed-loop auto-remediation | [Feasible-now] | Preview, production-grade pattern |
| 4 | SynapseML Isolation Forest / GAT-MVAD — multivariate failure prediction | [Feasible-with-effort] | Fabric notebook + Eventhouse |
| 5 | KQL `diffpatterns` / `autocluster` for automated failure-cluster attribution | [Feasible-now] | Native KQL plugin |

---

## A. Anomaly Detection & Forecasting on Gateway Telemetry

### A1. KQL Native Anomaly Detection — Drop-In for Eventhouse

**What it enables:** Replace hard-coded CPU/spool/duration thresholds with statistically-grounded outlier detection that accounts for seasonality (daily business-hours patterns), trend (capacity growth), and residual spikes. A single `series_decompose_anomalies()` call over a gateway telemetry KQL table surfaces anomaly scores per point; a `series_decompose_forecast()` call projects the next N intervals so the tool can say "spool disk will exhaust in ~2 hours at current trajectory."

**Technology:**  
- [`series_decompose_anomalies()`](https://learn.microsoft.com/en-us/kusto/query/anomaly-detection?view=microsoft-fabric) — scores anomalies via Tukey's fence on the residual after seasonal + trend decomposition. Scores > 1.5 = mild, > 3.0 = strong. Vectorized: processes thousands of time series in seconds. Applies to: ✅ Microsoft Fabric Eventhouse.  
- [`series_decompose_forecast()`](https://learn.microsoft.com/en-us/kusto/query/anomaly-detection?view=microsoft-fabric) — extrapolates baseline (seasonal + trend) N steps forward. Confidence intervals are implicit in the decomposition.  
- [`make-series` operator](https://learn.microsoft.com/en-us/kusto/query/time-series-analysis?view=microsoft-fabric) — the foundation: converts raw gateway telemetry rows into regular time-series arrays at any grain (1-min, 5-min) partitioned by gateway ID, cluster, or data source.

**Pattern for gateway telemetry (sketch):**
```kql
GatewayTelemetry
| make-series CpuPct=avg(CpuPercent) default=0
      on EventTime from ago(7d) to now() step 5m
      by GatewayId
| extend (Anomalies, AnomalyScore, Baseline) =
      series_decompose_anomalies(CpuPct, 1.5, -1, 'linefit')
| extend ForecastNext2h =
      series_decompose_forecast(CpuPct, 24)   // 24 × 5-min steps
| mv-expand ...
| where AnomalyScore > 2.5
```

**Feasibility:** [Feasible-now]. Eventhouse is the FPM telemetry store; `make-series` + `series_decompose_*` are native KQL functions with no external dependencies. Seasonality auto-detected via `series_periods_detect()`. The only engineering work is defining the right grain and making sure the telemetry table has a regular timestamp.

**Proactive vs reactive shift:** Forecasting spool-disk exhaustion requires extending the pattern to a trend-only (non-seasonal) series:
```kql
| extend SpoolForecast = series_decompose_forecast(SpoolDiskFreeGB, 48)
| project GatewayId, SpoolForecast
```
If the lowest forecasted value crosses a threshold, Activator fires *before* the disk fills — moving the tool from "gateway went offline" to "this gateway will saturate in ~2 hours."

**Risk/Caveat:** `series_decompose_*` assumes additive seasonality and linear trend. Gateways with irregular query patterns (bursty enterprise batches) may produce noisy anomaly scores. Tune the `threshold` parameter and consider `series_outliers()` with the `"tukey"` method directly for simpler spike detection on non-seasonal metrics like spool disk.

---

### A2. Multivariate Anomaly Detection — Fabric Spark + Eventhouse Hybrid

**What it enables:** Catches failure modes that are invisible in any single metric but detectable in the *joint distribution* of (CPU + spool I/O + query duration + network latency + memory pressure). A gateway thrashing under a large M query may show only mild CPU but severe combined stress. [MVAD](https://learn.microsoft.com/en-us/fabric/real-time-intelligence/multivariate-anomaly-overview) captures this.

**Technology:**  
- **SynapseML Isolation Forest** — [Feasible-now]. [Microsoft's own tutorial](https://learn.microsoft.com/en-us/fabric/data-science/isolation-forest-multivariate-anomaly-detection) trains `synapse.ml.isolationforest.IsolationForest` on Apache Spark inside a Fabric notebook, then runs inference. Detects correlation-aware anomalies across any N sensor/metric columns. Outputs anomaly scores per row. **Directly applicable to gateway fleet metrics.**  
- **Graph Attention Network (GAT) MVAD** — [Feasible-with-effort]. The [Fabric MVAD tutorial](https://learn.microsoft.com/en-us/fabric/real-time-intelligence/multivariate-anomaly-detection) uses the `time-series-anomaly-detector` PyPI package (implementing GAT) trained in a Fabric Spark notebook with data from Eventhouse via OneLake. Inference runs in real-time via the **KQL Python plugin** (Python 3.11 DL image) directly inside Eventhouse, enabling sub-second anomaly scoring on streaming gateway data without round-tripping to Spark.

**Architecture for gateway monitoring:**
1. Gateway telemetry lands in Eventhouse (KQL table).  
2. OneLake availability enabled → data exposed as Delta Parquet.  
3. Fabric notebook trains Isolation Forest or GAT model on historical telemetry → model saved to MLflow in Fabric.  
4. KQL Python plugin loads the model, runs `predict()` on live telemetry within a KQL query → returns anomaly boolean + score.  
5. Activator rule fires when score exceeds threshold.

**Feasibility:** [Feasible-with-effort] for GAT (requires KQL Python plugin setup, model retraining pipeline). [Feasible-now] for Isolation Forest in batch/scheduled mode.

**Risk/Caveat:** The KQL Python plugin requires enabling the `Python 3.11.7 DL (preview)` image and has per-query compute cost. GAT training requires labeled anomaly data or an unsupervised warmup period; cold-start on a new gateway fleet takes 2–4 weeks of history. Isolation Forest is entirely unsupervised and works from day one.

---

### A3. Predictive Failure Probability — Spark MLlib / LightGBM

**What it enables:** Binary classifier — "will this gateway cluster experience a failed refresh in the next 60 minutes?" — trained on historical (telemetry features, refresh outcome) pairs. Moves from "anomaly score" to a calibrated probability that can be surfaced directly in the Fleet View heatmap.

**Technology:**  
- Fabric Spark notebook with [Apache Spark MLlib](https://learn.microsoft.com/en-us/fabric/data-science/fabric-sparkml-tutorial) or LightGBM via SynapseML.  
- [Predictive Maintenance tutorial](https://learn.microsoft.com/en-us/fabric/data-science/predictive-maintenance) demonstrates the exact end-to-end workflow: feature engineering → LightGBM training → MLflow autologging → `PREDICT` function for batch scoring → Power BI visualization.  
- Features: rolling 15-min stats (mean/std of CPU, spool I/O, duration, error rate), time-since-last-restart, pending query count, OS patch age, network RTT variance.

**Drift detection:** Query duration distributions shift over time (new reports, schema changes). SynapseML's `synapse.ml.automl` or a scheduled KQL `series_decompose_anomalies` on the *model's prediction error* can flag when the classifier needs retraining — a meta-level anomaly detection loop.

**Feasibility:** [Feasible-with-effort]. Requires 4–8 weeks of labeled historical data linking telemetry to refresh outcomes (joinable via the gateway log + Power BI service refresh history API). The Fabric ML infrastructure (notebooks, MLflow, PREDICT) is fully available today.

**Risk/Caveat:** Class imbalance (failures are rare) requires SMOTE or weighted loss. False positives generate alert fatigue; calibrate the decision threshold against ops tolerance.

---

## B. LLM-Assisted Root-Cause Analysis & Natural-Language Ops

### B1. Fabric Data Agent — NL2KQL over Gateway Telemetry

**What it enables:** Any operator — including non-KQL engineers — can ask: *"Why did the Sales refresh fail at 3 AM last Thursday?"* and receive a plain-English answer synthesizing gateway error logs, network telemetry, and Windows Event Log data stored in Eventhouse, without writing a single query.

**Technology:**  
The [Fabric Data Agent](https://learn.microsoft.com/en-us/fabric/data-science/concept-data-agent) is **generally available** as of 2026. It:
- Connects to up to 5 data sources simultaneously, including **Eventhouse KQL Databases** (NL2KQL), SQL lakehouse (NL2SQL), Power BI semantic models (NL2DAX), and Azure AI Search indexes (for unstructured runbooks/docs).  
- Translates natural language to KQL using the selected schema + user-provided example queries (few-shot).  
- Executes the query and returns a human-readable synthesized answer with citations.

[Data source configuration](https://learn.microsoft.com/en-us/fabric/data-science/data-agent-add-datasources) for Eventhouse:
- Select specific KQL tables/materialized views/functions.
- Provide datasource instructions (e.g., "GatewayHeartbeat stores offline events per gateway ID indexed by timestamp").
- Provide example NL→KQL query pairs to teach complex aggregation patterns.

**Practical RCA synthesis pattern:** Configure a single Data Agent with 4 sources:
1. Eventhouse: gateway error events + network telemetry + heartbeat.
2. Eventhouse: Windows Event Log (via Azure Monitor Agent → Eventhouse [preview](https://learn.microsoft.com/en-us/azure/azure-monitor/vm/send-fabric-destination)).
3. Lakehouse (SQL endpoint): Power BI service refresh history Delta table.
4. Azure AI Search: runbook PDFs indexed from SharePoint/OneLake.

The agent can correlate across all four in a single conversational turn: "The Sales refresh failed at 03:14. Gateway GW-PROD-01 showed 847 error events of type `DM_GWPipeline_Gateway_MashupCrash` between 03:12–03:15. Network RTT to the SQL Server source spiked to 420ms (vs 12ms baseline). Likely cause: intermittent network loss to source system. Recommended action: check network path to SQL01, see runbook [Gateway Network Troubleshooting]."

**Feasibility:** [Feasible-now]. Data Agent GA, Eventhouse as a data source GA, Azure Monitor → Eventhouse preview (Windows Event Logs supported). The engineering work is: schema documentation, few-shot example query authoring (~20–40 examples for quality NL2KQL), and runbook indexing in Azure AI Search.

**Risk/Caveat:** NL2KQL quality degrades on complex multi-join temporal queries without good few-shot examples. The agent cannot *write* to the gateway or trigger actions — it is read-only. Cross-geo AI processing must be enabled by Fabric admin. Requires F2+ or P1+ capacity.

---

### B2. Eventhouse Remote MCP Server — Agent Framework Integration

**What it enables:** Exposes the gateway's Eventhouse database as an [MCP endpoint](https://learn.microsoft.com/en-us/fabric/real-time-intelligence/mcp-remote-eventhouse) consumable by any MCP-compatible AI agent (Copilot Studio, Azure AI Foundry agents, GitHub Copilot in VS Code). This is the composability layer: the gateway monitor's Eventhouse becomes a data tool that any AI orchestrator can call, query, and reason over.

**Technology:**  
The [Eventhouse Remote MCP Server (preview)](https://learn.microsoft.com/en-us/fabric/real-time-intelligence/mcp-remote-eventhouse) is a hosted HTTP MCP endpoint at:
```
https://api.fabric.microsoft.com/v1/mcp/workspaces/<WorkspaceID>/kqlDatabases/<DatabaseID>
```
AI agents using this endpoint can: discover schema dynamically, generate KQL, execute queries, and return structured results — with no additional infrastructure deployed.

**For the gateway tool:** An Azure AI Foundry agent or a Copilot Studio bot can be granted access to the gateway's Eventhouse MCP server and become a conversational ops assistant that reasons over live telemetry. This is architecturally cleaner than embedding a Fabric Data Agent inside every reporting surface — the MCP server is the universal adapter.

**Feasibility:** [Feasible-now] for read-only query scenarios. [Experimental] for full agentic loops (plan → query → interpret → act) because MCP tool-calling reliability at scale in production is still maturing.

---

### B3. LLM Alert Enrichment — Activator Alert → Contextualized Incident

**What it enables:** When Activator fires a raw alert ("Gateway GW-PROD-01 CPU > 95% for 3 minutes"), an LLM transforms it into a *contextualized incident*: "**GW-PROD-01 is saturating.** Current workload: 47 concurrent queries, 12 from the Sales team's morning refresh wave. Historical pattern: this gateway saturates every Monday 08:00–09:00 due to the weekly Sales aggregate. **Recommended action:** Temporarily redirect Sales data sources to GW-PROD-02 (currently at 31% CPU). Runbook: [Gateway Load Balancing Playbook](link)."

**Technology:**  
- **Fabric AI Functions (preview)** — [available in Fabric Data Warehouse and notebooks](https://learn.microsoft.com/en-us/fabric/fundamentals/whats-new). `ai.summarize()`, `ai.extract()`, `ai.classify()` as DataFrame methods, backed by Azure OpenAI with 200× default concurrency. Apply directly to alert payload DataFrames in a Fabric notebook triggered by Activator.  
- **SynapseML `OpenAIChatCompletion`** — [Feasible-now]. As demonstrated in the [Fabric text classification tutorial](https://learn.microsoft.com/en-us/fabric/data-science/tutorial-text-classification), call Azure OpenAI GPT-4o from a Spark DataFrame with a structured prompt that injects: alert timestamp, gateway ID, recent error summary (KQL query result), historical incident pattern, and runbook catalog. Returns structured JSON: `{likely_cause, confidence, recommended_actions[], runbook_url}`.  
- **Azure OpenAI Python SDK from Fabric notebook** — for low-latency (non-batch) enrichment, call `openai.ChatCompletion.create()` from a notebook triggered by Activator with the alert context as the system prompt.

**Feasibility:** [Feasible-now] for batch enrichment of alert history. [Feasible-with-effort] for real-time per-alert enrichment within the Activator → Notebook → Teams/email pipeline (latency is 2–8 seconds per enrichment call, acceptable for P2/P3 severity alerts).

**Risk/Caveat:** LLM hallucination risk is real for root-cause hypotheses; the output must be labeled "AI-suggested, not confirmed" and paired with evidence citations (the KQL results that fed the prompt). Avoid autonomous action based solely on LLM output — use it for human-in-loop recommendation, not as an autonomous decision-maker.

---

### B4. Natural-Language Querying via Copilot in Real-Time Intelligence

**What it enables:** [Copilot in Fabric Real-Time Intelligence](https://learn.microsoft.com/en-us/fabric/fundamentals/copilot-fabric-overview) allows users to type natural-language questions directly in a KQL Queryset or Real-Time Dashboard and get auto-generated KQL queries. For gateway monitoring, this means: "Show me all gateways that had more than 5 consecutive offline events last week" → auto-generated KQL → chart.

**Feasibility:** [Feasible-now]. Copilot in RTI is GA for Fabric F64+ (preview for smaller SKUs). Copilot-translated KQL questions are displayed before execution so users can validate. No code required for consumption — just a configured Eventhouse and Fabric Copilot enabled.

---

## C. Self-Healing / Autonomous Remediation

### C1. Activator → Fabric Item Closed-Loop Actions

**What it enables:** The alerting pipeline becomes an actuation pipeline. Instead of just notifying a human, a rule condition triggers a pre-approved remediation action automatically.

**Technology — Fabric Activator's native action types** ([documentation](https://learn.microsoft.com/en-us/fabric/real-time-intelligence/data-activator/activator-introduction)):
- **Run a Fabric Pipeline** — for orchestrated multi-step remediation (e.g., "clear spool dir → restart gateway → validate heartbeat → post Teams message with outcome").
- **Run a Fabric Notebook** — for Python-based custom logic: call the Power BI REST API, execute `net stop PBIEgwService / net start PBIEgwService` via an Azure Function HTTP action, or re-validate credentials via the Power BI gateway REST API.
- **Run a User Data Function (UDF, preview)** — [Feasible-now]. As demonstrated in the [Eventstream + Activator + UDF tutorial](https://learn.microsoft.com/en-us/fabric/real-time-hub/business-events/tutorial-business-events-event-stream-user-data-function-activator), Activator rules can trigger UDFs with parameter values from the triggering event. A gateway monitor UDF can receive `{gatewayId, alertType, severity}` and execute: call Azure REST API to restart the gateway service, write an audit event to Eventhouse, and post a Teams card with the action taken.
- **Run a Spark Job Definition** — for heavier remediation workloads.
- **Power Automate Flow** — [Feasible-now]. For actions requiring cross-system orchestration: restart Azure VM, recycle Windows service via Azure Arc script, or escalate to ITSM.

**Activator features that enable safe automation** ([What's New: Activator as business events publisher](https://learn.microsoft.com/en-us/fabric/fundamentals/whats-new)):
- **Impact preview**: Activator shows how often a rule *would have* fired on historical data before you activate it — prevents alert-loop automation.
- **Stateful transitions**: Rules fire on state *change* (threshold crossed), not every polling interval — prevents remediation storms.
- **Business events publisher (preview)**: When Activator detects a condition, it can emit a structured business event to Real-Time Hub, consumable by other agents/systems downstream — composable remediation chains.
- **Publish a business event action (preview)**: Downstream consumers subscribe to the event rather than being directly invoked, decoupling trigger from action.

**Gateway restart via automation:**  
The [on-premises gateway can be restarted](https://learn.microsoft.com/en-us/data-integration/gateway/service-gateway-restart) via `net stop/start PBIEgwService` at the OS level, or via the Power BI REST API (`POST /gateways/{id}/restart`). An Azure Function or Fabric UDF can call the REST API — automatable from Activator. The [Fabric What's New page](https://learn.microsoft.com/en-us/fabric/fundamentals/whats-new) confirms manual gateway update via API is now in preview (December 2025 release, version 3000.298+).

**Feasibility summary by action type:**

| Remediation Action | Method | Feasibility | Risk Level |
|---|---|---|---|
| Send Teams/email alert | Activator native | [Feasible-now] | None |
| Trigger diagnostic notebook | Activator → Notebook | [Feasible-now] | Low |
| Gateway service restart | Activator → UDF → REST API | [Feasible-now] | Medium — needs health check post-restart |
| Clear spool directory | Activator → Pipeline → Azure Function | [Feasible-with-effort] | Medium — needs size threshold guard |
| Re-validate credentials | Activator → Pipeline → Power BI API | [Feasible-now] | Low — read-only check |
| Cluster rebalance (redirect data sources) | Activator → Pipeline → Power BI API | [Feasible-with-effort] | Medium — service impact |
| Auto-scale: add gateway node | Activator → ARM API via Azure Function | [Experimental] | High — requires IaC discipline |

---

### C2. Guardrails for Autonomous Ops

The "Jarvis bar" requires: autonomous action only for well-understood, reversible, low-blast-radius operations. The following framework should govern the tool's remediation design:

**Tier 1 — Fully autonomous (no human approval required):**
- Notify (Teams/email/PagerDuty).
- Write diagnostic artifact to OneLake (for audit).
- Trigger read-only health check pipeline.
- Re-test data source connectivity.

**Tier 2 — Automated with human-in-loop confirmation:**
- Gateway service restart (UDF sends a Teams Adaptive Card with Approve/Deny buttons before executing).
- Spool directory cleanup (show projected space recovery, require approval).
- Credential re-validation with auto-refresh attempt.

**Tier 3 — Recommended only (no auto-execution):**
- Gateway cluster scale-out (cost impact).
- Data source migration across clusters.
- OS-level interventions.

**Implementation pattern:** Activator → UDF fires. UDF posts an Adaptive Card to Teams via Power Automate with a 15-minute timeout. If approved, UDF executes action and writes audit record. If timeout or deny, UDF writes "deferred" record and creates a Linear/ServiceNow ticket. This mirrors the ITSM approval gate without requiring a ticketing system integration to be on the critical path.

**Risk/Caveat:** Autonomous restart loops are a real risk — a gateway in a crash loop could be continuously restarted by automation, masking a deeper issue (corrupted config, storage fault). Implement a **circuit breaker**: if the same remediation fires > 3 times in 30 minutes for the same gateway, escalate to Tier 3 and suppress further automation.

---

## D. Advanced Analytics — Differentiation

### D1. KQL Failure-Pattern Attribution — `autocluster` + `diffpatterns`

**What it enables:** During a failure window, automatically identify *what changed* — which combination of error code × gateway host × data source type × time-of-day accounts for the anomalous spike. This is the KQL-native equivalent of a multi-dimensional drill-down, automated.

**Technology:**  
- [`autocluster` plugin](https://learn.microsoft.com/en-us/kusto/query/autocluster-plugin?view=microsoft-fabric) — finds common patterns in discrete dimensions (e.g., `ErrorCode × GatewayHost × DataSourceType`) within the failure window. Returns a ranked list of patterns that cover the most rows.  
- [`diffpatterns` plugin](https://learn.microsoft.com/en-us/kusto/query/anomaly-diagnosis?view=microsoft-fabric) — compares the anomalous window against a baseline window. Finds the *differential* patterns — "during the spike, 78% of errors came from GatewayHost=GW02, ErrorCode=DM_GWPipeline_Gateway_MashupCrash, DataSource=OracleDB — compared to 12% during baseline."

**Sketch for the gateway triage view:**
```kql
let spike_window = GatewayErrors | where EventTime between (alert_start..alert_end);
let baseline_window = GatewayErrors | where EventTime between (alert_start-1h..alert_start);
spike_window
| extend AB = "spike"
| union (baseline_window | extend AB = "baseline")
| evaluate diffpatterns(AB, "spike", "baseline",
    WeightColumn="ErrorWeight",
    Columns=dynamic(["GatewayHost","ErrorCode","DataSourceType","DatasourceName"]))
```

This returns an auto-ranked table of differentiating patterns — the "top offender" attribution the tool's unified-triage view needs, fully automated, no ML training required.

**Feasibility:** [Feasible-now]. Both plugins are native KQL, apply to Microsoft Fabric Eventhouse, and require no external dependencies. Engineering effort: ~1 day to wire into the triage query pipeline.

---

### D2. Query Fingerprinting & Duration-Distribution Clustering

**What it enables:** Identify *structural query patterns* from the gateway query log — not by data source name, but by query shape. A query that JOINs 3 tables will always be slower than a simple SELECT regardless of dataset; clustering queries by their normalized hash fingerprint reveals which *patterns* are the systemic offenders.

**Technology:**  
- **Power BI gateway logs** contain raw query text (or truncated M/DAX expressions). A Fabric notebook applies:
  1. **Query normalization**: strip literals, uppercase keywords, replace variable table/column names with placeholders using regex → produces a canonical fingerprint.
  2. **Hashing**: `hashlib.sha256(normalized_query)` → query fingerprint hash.
  3. **Clustering**: Group by fingerprint hash → compute (count, mean_duration_ms, p95_duration_ms, fail_rate) per cluster.
  4. **Ranking**: Sort by `p95_duration_ms × count` — the highest-impact patterns.
- For richer clustering: apply KMeans (Spark MLlib) on [query feature vectors](https://arxiv.org/html/2511.13059v2) (token count, join depth, estimated row count, data source type) to form semantic clusters even when exact fingerprints differ.
- The `query_hash` / `query_plan_hash` pattern from [SQL Server telemetry](https://www.sqlskills.com/blogs/jonathan/how-useful-are-query_hash-and-query_plan_hash-for-troubleshooting/) can be adapted for M query fingerprinting if the gateway logs expose query plan IDs (currently partial — [Unverified] for full plan hashes in OPDG logs as of mid-2026).

**Feasibility:** [Feasible-with-effort] for regex-based fingerprinting of M/DAX queries (gateway logs expose partial query text; full query text requires enhanced gateway logging enabled). [Feasible-now] for fingerprinting by data source + query type + duration bucket as a proxy.

---

### D3. Causal Correlation — Multi-Signal Attribution (Network vs Source vs Gateway)

**What it enables:** When a refresh fails, automatically distinguish: (a) gateway-internal failure (CPU/memory/spool), (b) source system failure (DB timeout, network hop), (c) Fabric service failure (semantic model, Power BI backend). The tool already collects all three signal streams — the intelligence layer joins them causally.

**Technology:**

**KQL-native approach (immediate):**  
Multi-signal `join` in KQL with temporal alignment:
```kql
GatewayErrors
| join kind=leftouter (NetworkMetrics | summarize AvgRTT=avg(RTT) by GatewayId, bin(EventTime, 1m)) on GatewayId, EventTime
| join kind=leftouter (FabricRefreshHistory | where Status=="Failed") on $left.CorrelationId == $right.CorrelationId
| extend Root = case(AvgRTT > 200, "network", CpuPct > 90, "gateway_cpu", SpoolFull, "gateway_spool", "source_or_fabric")
```
This is deterministic rule-based causal attribution — fast, transparent, good for 80% of cases.

**Statistical causal inference (advanced):**  
Granger causality testing between the three signal streams — does network RTT *precede* query failure (true causal direction)? [ICLR 2025 AERCA](https://iclr.cc/virtual/2025/poster/28602) demonstrates integrated Granger causal discovery + root cause ranking for multivariate time series. Implementation requires:
- `statsmodels.tsa.stattools.grangercausalitytests` (available in Fabric Spark notebooks).
- Per-gateway VAR (Vector Autoregression) model trained on 30-day rolling window.
- Output: causal graph updated nightly → displayed as "most likely root cause signal" in the Fleet View.

**Feasibility:** [Feasible-now] for KQL-native rule-based attribution. [Experimental] for Granger causality testing in a production monitoring pipeline (computationally expensive at fleet scale, requires careful temporal alignment). The [AIOps + causal inference literature](https://www.ijrti.org/papers/IJRTI2505203.pdf) confirms this is active research, not a solved engineering problem.

---

### D4. Capacity/Cost Intelligence — Gateway Load tied to Fabric CU Consumption

**What it enables:** Tells the operator not just that a gateway is busy, but *what it costs* in Fabric CU-seconds, and when to scale out to avoid CU throttling. Ties the gateway Fleet View to the financial/capacity layer.

**Technology:**  
- [Fabric Capacity Metrics app](https://learn.microsoft.com/en-us/fabric/enterprise/metrics-app) exposes CU consumption per item per 30-second timepoint via a Power BI semantic model. The app's **Compute page** shows operations, utilization trends, and throttling events at 14-day history.  
- **VNet Data Gateway CU cost**: [Each active VNet gateway member costs 4 CUs/hour](https://community.fabric.microsoft.com/t5/Fabric-platform/Fabric-Capacity-VNET-Gateway-Uptime/m-p/4733933) regardless of usage — this is a fixed overhead that the tool should surface prominently.  
- **On-premises gateway CU attribution**: As of mid-2026, Microsoft Fabric does **not** expose per-OPDG CU consumption at gateway-member granularity in the Metrics app — the community confirms this gap. [Unverified] whether this becomes available in H2 2026.  
- **Workaround**: Proxy CU cost by correlating gateway query volume (from gateway logs) with the CU spike timestamps from the Capacity Metrics semantic model. A KQL join of gateway query count (5-min bins) against the Metrics app export produces a per-gateway CU attribution estimate.  
- **Scale-out recommendation engine**: If (forecast_peak_CU > 0.85 × capacity_CU) AND (gateway_CPU_forecast > 80%), recommend adding a gateway cluster member. Expose as a "Capacity Advisory" panel in the Fleet View.

**Feasibility:** [Feasible-with-effort] for the proxy CU attribution model. [Unverified] for true per-OPDG CU granularity from the Capacity Metrics app. Direct VNet gateway CU billing is confirmed and immediately surfaceable.

---

### D5. Anomaly Detection No-Code (Fabric Preview Feature)

**What it enables:** The [Fabric What's New page](https://learn.microsoft.com/en-us/fabric/fundamentals/whats-new) lists **"Anomaly detection (Preview)"** as a no-code feature with automatic model selection. If this applies to Eventhouse data streams, it could provide a zero-configuration anomaly baseline for gateway telemetry without any KQL authoring.

**Feasibility:** [Unverified] — the feature is in preview and its exact applicability to Eventhouse time-series (vs. only warehouse/lakehouse tables) is not yet confirmed in the documentation available as of June 2026. Monitor the [Fabric Updates Blog](https://blog.fabric.microsoft.com/) for GA announcement.

---

## Architecture Blueprint — Intelligence Layers Stacked

```
┌────────────────────────────────────────────────────────────────────────┐
│  LAYER 4: SELF-HEALING                                                 │
│  Activator rule → UDF/Notebook/Pipeline → REST restart/rebalance       │
│  Circuit breaker + Adaptive Card approval gate                         │
├────────────────────────────────────────────────────────────────────────┤
│  LAYER 3: EXPLANATION                                                  │
│  Fabric Data Agent (NL2KQL) + LLM alert enrichment (SynapseML GPT-4o) │
│  diffpatterns/autocluster auto-attribution                              │
│  Eventhouse MCP server → Copilot Studio / AI Foundry                  │
├────────────────────────────────────────────────────────────────────────┤
│  LAYER 2: PREDICTION                                                   │
│  series_decompose_forecast → spool/CPU saturation ETA                 │
│  Isolation Forest / GAT MVAD → fleet-level failure probability         │
│  LightGBM refresh failure classifier → P(failure|features) heatmap    │
├────────────────────────────────────────────────────────────────────────┤
│  LAYER 1: DETECTION                                                    │
│  series_decompose_anomalies (per-gateway, per-metric, KQL native)      │
│  diffpatterns (failure attribution vs baseline)                        │
│  Query fingerprint clusters (top-offender ranking)                     │
├────────────────────────────────────────────────────────────────────────┤
│  FOUNDATION: Eventhouse (KQL) + OneLake Delta + Activator              │
│  Gateway logs + Windows Event Log (AMA→Eventhouse) + Network metrics  │
└────────────────────────────────────────────────────────────────────────┘
```

---

## Implementation Roadmap — From Passive to Predictive

| Phase | Deliverable | Effort | Feasibility |
|---|---|---|---|
| **Sprint 1** | `series_decompose_anomalies` on CPU/spool/duration in Eventhouse KQL | 1–2 days | [Feasible-now] |
| **Sprint 1** | `diffpatterns` wired into unified triage view | 1 day | [Feasible-now] |
| **Sprint 2** | `series_decompose_forecast` → saturation ETA card in Fleet View | 2 days | [Feasible-now] |
| **Sprint 2** | Fabric Data Agent over Eventhouse for NL ops querying | 3–5 days | [Feasible-now] |
| **Sprint 3** | Activator → UDF → gateway restart + Teams approval card | 3–5 days | [Feasible-now] |
| **Sprint 4** | SynapseML Isolation Forest MVAD — batch scoring in notebook | 1 week | [Feasible-with-effort] |
| **Sprint 5** | LLM alert enrichment (SynapseML OpenAIChatCompletion) | 1 week | [Feasible-with-effort] |
| **Sprint 6** | Query fingerprinting + cluster ranking | 1 week | [Feasible-with-effort] |
| **Sprint 7** | GAT MVAD via KQL Python plugin (real-time inference) | 2 weeks | [Feasible-with-effort] |
| **Sprint 8** | Granger causal attribution (network vs source vs gateway) | 3 weeks | [Experimental] |
| **Future** | Per-OPDG CU attribution from Capacity Metrics | TBD | [Unverified] |

---

## Primary Source URL Index

| Topic | Document | URL |
|---|---|---|
| KQL anomaly detection & forecasting | Microsoft Learn / Kusto | https://learn.microsoft.com/en-us/kusto/query/anomaly-detection?view=microsoft-fabric |
| KQL time series analysis | Microsoft Learn / Kusto | https://learn.microsoft.com/en-us/kusto/query/time-series-analysis?view=microsoft-fabric |
| KQL anomaly diagnosis (autocluster, diffpatterns, basket) | Microsoft Learn / Kusto | https://learn.microsoft.com/en-us/kusto/query/anomaly-diagnosis?view=microsoft-fabric |
| KQL autocluster plugin | Microsoft Learn / Kusto | https://learn.microsoft.com/en-us/kusto/query/autocluster-plugin?view=microsoft-fabric |
| Multivariate anomaly detection (MVAD) — overview | Microsoft Fabric Learn | https://learn.microsoft.com/en-us/fabric/real-time-intelligence/multivariate-anomaly-overview |
| Multivariate anomaly detection — tutorial (GAT) | Microsoft Fabric Learn | https://learn.microsoft.com/en-us/fabric/real-time-intelligence/multivariate-anomaly-detection |
| SynapseML Isolation Forest MVAD | Microsoft Fabric Learn | https://learn.microsoft.com/en-us/fabric/data-science/isolation-forest-multivariate-anomaly-detection |
| SynapseML overview | Microsoft Fabric Learn | https://learn.microsoft.com/en-us/fabric/data-science/synapse-overview |
| Predictive maintenance tutorial (LightGBM + MLflow) | Microsoft Fabric Learn | https://learn.microsoft.com/en-us/fabric/data-science/predictive-maintenance |
| Fabric Data Agent concepts | Microsoft Fabric Learn | https://learn.microsoft.com/en-us/fabric/data-science/concept-data-agent |
| Fabric Data Agent — add datasources | Microsoft Fabric Learn | https://learn.microsoft.com/en-us/fabric/data-science/data-agent-add-datasources |
| Eventhouse Remote MCP Server (preview) | Microsoft Fabric Learn | https://learn.microsoft.com/en-us/fabric/real-time-intelligence/mcp-remote-eventhouse |
| Copilot in Fabric — overview | Microsoft Fabric Learn | https://learn.microsoft.com/en-us/fabric/fundamentals/copilot-fabric-overview |
| AI Functions in Fabric (preview) | Microsoft Fabric Learn | https://learn.microsoft.com/en-us/fabric/data-science/ai-services/how-to-use-openai-ai-functions |
| LLM text classification via SynapseML | Microsoft Fabric Learn | https://learn.microsoft.com/en-us/fabric/data-science/tutorial-text-classification |
| Fabric Activator — introduction | Microsoft Fabric Learn | https://learn.microsoft.com/en-us/fabric/real-time-intelligence/data-activator/activator-introduction |
| Activator — trigger Fabric items | Microsoft Fabric Learn | https://learn.microsoft.com/en-us/fabric/real-time-intelligence/data-activator/activator-trigger-fabric-items |
| Activator + Eventstream + User Data Functions tutorial | Microsoft Fabric Learn | https://learn.microsoft.com/en-us/fabric/real-time-hub/business-events/tutorial-business-events-event-stream-user-data-function-activator |
| Fabric User Data Functions — overview | Microsoft Fabric Learn | https://learn.microsoft.com/en-us/fabric/data-engineering/user-data-functions/user-data-functions-overview |
| Set alerts from Real-Time Hub | Microsoft Fabric Learn | https://learn.microsoft.com/en-us/fabric/real-time-intelligence/user-flow-5 |
| On-premises gateway — restart methods | Microsoft Learn | https://learn.microsoft.com/en-us/data-integration/gateway/service-gateway-restart |
| Azure Monitor Agent → Fabric Eventhouse (preview) | Microsoft Azure Learn | https://learn.microsoft.com/en-us/azure/azure-monitor/vm/send-fabric-destination |
| Fabric Capacity Metrics app | Microsoft Fabric Learn | https://learn.microsoft.com/en-us/fabric/enterprise/metrics-app |
| Fabric What's New (June 2026) | Microsoft Fabric Learn | https://learn.microsoft.com/en-us/fabric/fundamentals/whats-new |
| SparkML / MLlib in Fabric | Microsoft Fabric Learn | https://learn.microsoft.com/en-us/fabric/data-science/fabric-sparkml-tutorial |
| AERCA: Granger causal RCA (ICLR 2025) | ICLR | https://iclr.cc/virtual/2025/poster/28602 |
| LLM4Log: LLM-based log analysis survey | arXiv 2025 | https://arxiv.org/abs/2604.16359v2 |
| Causal inference for AIOps (Fabric community) | Fabric community | https://community.fabric.microsoft.com/t5/Fabric-platform/How-to-Monitor-Usage-Across-Multiple-Virtual-Network-Data/m-p/4861955 |
