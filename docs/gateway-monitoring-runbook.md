# Power BI Gateway Performance Monitoring — Build Runbook
### Hybrid architecture: Rui Romano's collector → ADLS Gen2 landing → Fabric Lakehouse (PySpark medallion) → DirectLake

> **Source basis:** [RuiRomano/pbigtwmonitor](https://github.com/RuiRomano/pbigtwmonitor) (PowerShell collector + ADLS layout, reused as-is) and [Microsoft Learn: Monitor and optimize on-premises data gateway performance](https://learn.microsoft.com/en-us/data-integration/gateway/service-gateway-performance) (log schema + config). The analytics layer is rebuilt natively in Fabric.


> **Document structure:** **Part A** below is the hand-built ADLS→Fabric custom build. **Part B** (end of doc) is the REVISED recommended **hybrid** using Microsoft's maintained Fabric Platform Monitoring for collection + your PySpark analytics. Read Part B's decision gate first.

---

## ⚠️ Honest preface

1. **The repo is deprecated.** Rui Romano points to [Microsoft Fabric Platform Monitoring](https://github.com/microsoft/fabric-toolbox/tree/main/monitoring/fabric-platform-monitoring) as the successor. We reuse only his **PowerShell log-shipping collector** (still genuinely useful for on-prem gateway *performance* logs) and **rebuild the analytics layer** in Fabric.
2. **Blind spots to accept:** paginated-report queries aren't logged; Premium-capacity queries are sometimes missed; logs carry **no dataset/requester identity** (correlate via service audit logs on `RequestId`/`QueryTrackingId`); and gateway diagnostics **does not capture network bandwidth/latency** — the most common real bottleneck. Add OS-level NIC/disk/CPU telemetry separately.

---

## Architecture

```
GATEWAY NODE (Windows, standard mode)
  Perf logging ON → ...\On-premises data gateway\Report\
     QueryExecutionReport_*.log (15-col CSV)
     QueryStartReport_*.log (7-col CSV)
     QueryExecutionAggregationReport_*.log
     SystemCounterAggregationReport_*.log
  Run.ps1 (Task Scheduler hourly, incremental via state.json)
        │ Az.Storage
        ▼
ADLS GEN2 (HNS on) — LANDING ZONE   container: pbigatewaymonitor
   raw/{gatewayId}/logs/...
   raw/{gatewayId}/reports/yyyy/MM/dd/*Report_*.log
   raw/{gatewayId}/metadata/GatewayProperties.json
        │ OneLake SHORTCUT (no copy)
        ▼
FABRIC: Lakehouse GatewayMon_LH
   Files/adls_raw  → shortcut to ADLS container
   nb_bronze → nb_silver → nb_gold  (PySpark, Delta medallion)
   DirectLake semantic model → Report "Gateway Performance"
```

---

## Phase 1 — Enable & verify gateway performance logging (every node)

- Logging is **ON by default** (standard mode only). Verify the four `*Report_*.log` files under `ReportFilePath`, default:
  `C:\Windows\ServiceProfiles\PBIEgwService\AppData\Local\Microsoft\On-premises data gateway\Report`
- In `C:\Program Files\On-premises data gateway\Microsoft.PowerBI.DataMovement.Pipeline.GatewayCore.dll.config`:
  - `QueryExecutionReportOn = True`, `SystemCounterReportOn = True`
  - `ReportFileCount` (def 10), `ReportFileSizeInBytes` (def ~100MB) — raise if collecting daily
  - `QueryExecutionAggregationTimeInMinutes` / `SystemCounterAggregationTimeInMinutes` (def 5)
  - **Restart the gateway service** after edits.
- Add **antivirus exclusions** for the logging dir and the `Spooler` dir.

## Phase 2 — ADLS Gen2 landing zone

- Create storage account with **Hierarchical Namespace enabled**.
- Container `pbigatewaymonitor`; collector writes under `raw/`.
- Prefer a **service principal (Storage Blob Data Contributor)** or scoped SAS over the account key for production.

## Phase 3 — Deploy the collector (reused from repo)

- Install PowerShell 7 + `Az.Accounts` (≥2.8.0), `Az.Storage` (≥4.6.0).
- Copy `Run.ps1`, `UploadGatewayLogs.ps1`, `Utils.psm1`, `Config.json` to `C:\PBIGTWMonitor`.

`Config.json`:
```json
{
    "GatewayLogsPath": ["C:\\Windows\\ServiceProfiles\\PBIEgwService\\AppData\\Local\\Microsoft\\On-premises data gateway"],
    "StorageAccountConnStr": "<ADLS Gen2 connection string>",
    "StorageAccountContainerName": "pbigatewaymonitor",
    "StorageAccountContainerRootPath": "raw",
    "OutputPath": ".\\Data"
}
```

Collector behavior (from `UploadGatewayLogs.ps1`): auto-discovers `GatewayObjectId` from first `*Report_*.log` + core count via `Get-ComputerInfo`; writes `GatewayProperties.json` to `metadata/`; ships logs/reports (date-partitioned `yyyy/MM/dd`) **incrementally** via `state.json` (`LastWriteTimeUtc > LastRun`); copies locally before upload (gateway locks), then deletes.

- **Task Scheduler:** run `Run.ps1` hourly under the service account.

## Phase 4 — OneLake shortcut

- Workspace **Gateway Monitoring** (Fabric capacity) → Lakehouse **GatewayMon_LH**.
- Lakehouse **Files → New shortcut → ADLS Gen2** → `https://<acct>.dfs.core.windows.net/pbigatewaymonitor`, name `adls_raw`. No data copy; stays live.

## Phase 5 — Bronze notebook (parse → typed Delta)

```python
from pyspark.sql import functions as F
BASE = "Files/adls_raw/raw"

def read_report(glob, label):
    df = (spark.read.option("header", True)
          .csv(f"{BASE}/*/reports/*/*/*/*{glob}*.log"))
    return df.withColumn("_src", F.input_file_name()).withColumn("_label", F.lit(label))

qexec  = read_report("QueryExecution",  "query_execution")
qstart = read_report("QueryStart",      "query_start")
counters   = read_report("SystemCounterAggregation", "system_counter")
qexec_agg  = read_report("QueryExecutionAggregation", "query_exec_agg")

qexec_t = (qexec
   .withColumn("QueryExecutionDuration", F.col("QueryExecutionDuration").cast("long"))
   .withColumn("DataProcessingDuration", F.col("DataProcessingDuration").cast("long"))
   .withColumn("SpoolingDiskWritingDuration", F.col("SpoolingDiskWritingDuration").cast("long"))
   .withColumn("SpoolingDiskReadingDuration", F.col("SpoolingDiskReadingDuration").cast("long"))
   .withColumn("SpoolingTotalDataSize", F.col("SpoolingTotalDataSize").cast("long"))
   .withColumn("DataReadingAndSerializationDuration", F.col("DataReadingAndSerializationDuration").cast("long"))
   .withColumn("QueryExecutionEndTimeUTC", F.to_timestamp("QueryExecutionEndTimeUTC")))

for name, df in [("bronze_query_execution", qexec_t),
                 ("bronze_query_start", qstart),
                 ("bronze_system_counter", counters),
                 ("bronze_query_exec_agg", qexec_agg)]:
    df.dropDuplicates().write.mode("overwrite").format("delta").saveAsTable(name)
```
> Dedup matters: `QueryTrackingId` repeats on retry. For incremental, switch to `MERGE` on `(GatewayObjectId, QueryTrackingId, QueryExecutionEndTimeUTC)`.

**Key Query Execution columns** (all from Microsoft Learn): `QueryExecutionDuration`, `DataProcessingDuration`, `SpoolingDisk{Writing,Reading}Duration`, `SpoolingTotalDataSize`, `DataReadingAndSerializationDuration`, `Success`/`ErrorMessage`, `QueryType`, `DataSource`.

## Phase 6 — Silver notebook (conform + the key join)

```python
qs = spark.table("bronze_query_start")
qe = spark.table("bronze_query_execution")

silver_query = (qs.alias("s")
  .join(qe.alias("e"),
        on=[F.col("s.GatewayObjectId")==F.col("e.GatewayObjectId"),
            F.col("s.QueryTrackingId")==F.col("e.QueryTrackingId")],
        how="left")
  .withColumn("DiskWrite_BytesPerSec",
        F.when(F.col("e.SpoolingDiskWritingDuration")>0,
               F.col("e.SpoolingTotalDataSize")/F.col("e.SpoolingDiskWritingDuration")))
  .withColumn("DiskRead_BytesPerSec",
        F.when(F.col("e.SpoolingDiskReadingDuration")>0,
               F.col("e.SpoolingTotalDataSize")/F.col("e.SpoolingDiskReadingDuration")))
  .withColumn("SpoolShare",
        F.col("e.SpoolingDiskWritingDuration")/F.col("e.QueryExecutionDuration")))
silver_query.write.mode("overwrite").format("delta").saveAsTable("silver_query")
spark.table("bronze_system_counter").write.mode("overwrite").format("delta").saveAsTable("silver_counter")
```
> Counter names to pivot: `SystemCPUPercent`, `SystemMEMUsedPercent`, `GatewayCPUPercent` (÷ core count for true %), `GatewayMEMKb`. Core count from `GatewayProperties.json`.

## Phase 7 — Gold (star schema)

```python
dim_gateway = spark.read.json(f"{BASE}/*/metadata/GatewayProperties.json")
fact_query  = spark.table("silver_query").select(
    "GatewayObjectId","DataSource","QueryType","QueryTrackingId",
    "QueryExecutionStartTimeUTC","QueryExecutionEndTimeUTC",
    "QueryExecutionDuration","DataProcessingDuration",
    "DataReadingAndSerializationDuration","SpoolingTotalDataSize",
    "DiskRead_BytesPerSec","DiskWrite_BytesPerSec","Success","ErrorMessage")
fact_counter = spark.table("silver_counter")

for n,df in [("gold_dim_gateway",dim_gateway),
             ("gold_fact_query",fact_query),
             ("gold_fact_counter",fact_counter)]:
    df.write.mode("overwrite").format("delta").saveAsTable(n)
# Then: OPTIMIZE + V-Order gold tables for DirectLake
```

## Phase 8 — Orchestrate

- Fabric Data Pipeline: `nb_bronze → nb_silver → nb_gold`, hourly, ~15 min after the gateway collector. No copy activity (shortcut).

## Phase 9 — Semantic model + report

- **DirectLake** model over gold tables. Relationships: `fact_query[GatewayObjectId] → dim_gateway`; both facts → `dim_date`.
- Measures:
  - `Avg Query Duration (s)` = `AVERAGE(gold_fact_query[QueryExecutionDuration])/1000`
  - `P95 Query Duration` via `PERCENTILEX.INC`
  - `Failure Rate %` = `DIVIDE(COUNTROWS(FILTER(gold_fact_query, NOT gold_fact_query[Success])), COUNTROWS(gold_fact_query))`
  - `Avg Gateway CPU %` = `[GatewayCPUPercent] / MAX(gold_dim_gateway[NumberOfCores])`
  - `Spool Throughput MB/s` from `SpoolingTotalDataSize / SpoolingDiskWritingDuration`
- Pages (mirroring repo layout, on gold model): Queries, Requests (start⨝exec timeline), Counters (CPU/mem vs cores), Gateway Profile, Errors.
- Build as a **PBIP project** for git versioning.

## Phase 10 — Close the network blind spot

Gateway logs are blind to bandwidth/latency. Add per-node Windows perfmon (NIC/disk/CPU) or the Azure Monitor agent feeding the same ADLS, and surface on the Counters page. Validate the pipe by uploading a test file to an Azure region matching your tenant.

---

## What we kept vs replaced

| Layer | Rui Romano repo | This build |
|---|---|---|
| Gateway log collection | `Run.ps1` + `UploadGatewayLogs.ps1` → ADLS | **Kept as-is** |
| Storage layout | `raw/{gatewayId}/{logs,reports,metadata}` | **Kept** |
| Parsing/transform | Power Query (CSV re-parse on refresh) | **Replaced** with PySpark medallion |
| Storage format | In-model import | **Delta** (bronze/silver/gold) |
| Serving | Import PBIT | **DirectLake** semantic model |
| Key join | Start⨝Exec on GatewayId+QueryTrackingId | **Kept** (in silver) |

---

# Phase 11 — FUAM correlation layer (optional, high value)

> Goal: enrich gateway-internal telemetry (`gold_fact_query`) with the **identity** it lacks — which semantic model, refresh, user, and capacity sit behind a slow gateway query — by joining to FUAM. Keep FUAM **vanilla in its own workspace** (so it stays upgradeable); reach it by **OneLake shortcut**, never by forking.

## 11.1 — What FUAM provides vs what it does NOT

FUAM ingests **tenant operational metadata** into `FUAM_Lakehouse` (Delta, DirectLake). It does **NOT** ingest on-prem gateway performance logs — gateways appear only as Scanner-API inventory metadata. So FUAM = "what / who / how much", your build = "why is it slow". They meet on **time window + DataSource**.

## 11.2 — FUAM tables to shortcut (module → table)

Pull only these from `FUAM_Lakehouse` (not the whole lakehouse):

| FUAM module | Table(s) | Why for gateway correlation |
|---|---|---|
| Activities | `activities`, `aggregated_activities_last_30days` | who ran/refreshed what, when (identity + user) |
| Capacity Refreshables | `capacity_refreshables`, `capacity_refreshable_details`, `capacity_refreshable_times` | scheduled semantic-model refresh telemetry + timing |
| Capacity Metrics | `capacity_metrics_by_item_by_operation_by_day`, `capacity_metrics_by_timepoint` | CU burn / throttling overlay vs gateway counters |
| Inventory (Scanner) | `semantic_models`, `datasource_instances`, `workspaces` | map DataSource ↔ semantic model ↔ workspace |

## 11.3 — Shortcut setup

In `GatewayMon_LH` → **Tables → New shortcut → Microsoft OneLake** → select `FUAM_Lakehouse` → pick the tables above. No copy; FUAM and your pipeline refresh independently.

## 11.4 — Join-key reality (labeled by confidence)

- **[Reliable]** `dim_date` (time window) + `DataSource` — present on both sides; primary correlation keys.
- **[Inference]** Where the gateway `EvaluationContext` / `artifactId` column is populated (Fabric/Power Platform semantic models, Dataflow Gen2), it links more directly to a FUAM `semantic_models` / inventory id.
- **[Unverified]** A single exact PK joining gateway logs ↔ FUAM 1:1. Treat `RequestId`/`QueryTrackingId` → service-activity correlation as **fuzzy / time-bounded**, not an exact join. FUAM table names below are documented; exact *column* names must be confirmed against your deployed FUAM version.

## 11.5 — Silver enrichment join (time-window + DataSource)

```python
from pyspark.sql import functions as F

fq  = spark.table("gold_fact_query")              # your build
act = spark.table("activities")                   # FUAM shortcut
ref = spark.table("capacity_refreshables")        # FUAM shortcut
sm  = spark.table("semantic_models")              # FUAM shortcut (inventory)

# 1) Attribute slow gateway queries to a refresh in the same window + datasource.
WIN_MIN = 10  # tolerance window in minutes; tune to your aggregation interval

fq_w = (fq.withColumn("win_start", F.col("QueryExecutionEndTimeUTC") - F.expr(f"INTERVAL {WIN_MIN} MINUTES"))
          .withColumn("win_end",   F.col("QueryExecutionEndTimeUTC")))

# Refreshables carry semantic-model identity + scheduled timing.
# [Inference] time-overlap join; refine with DataSource->model map from inventory.
enriched = (fq_w.alias("q")
  .join(ref.alias("r"),
        F.col("r.refreshStartTime").between(F.col("q.win_start"), F.col("q.win_end")),
        how="left")
  .join(sm.alias("m"), F.col("r.semanticModelId") == F.col("m.id"), how="left"))

# 2) Add user/initiator from activities in the same window (best-effort).
enriched = enriched.join(act.alias("a"),
        (F.col("a.activityTime").between(F.col("q.win_start"), F.col("q.win_end"))) &
        (F.lower(F.col("a.datasetName")) == F.lower(F.col("m.name"))),
        how="left")

enriched.write.mode("overwrite").format("delta").saveAsTable("gold_fact_query_enriched")
```
> Column names (`refreshStartTime`, `semanticModelId`, `activityTime`, `datasetName`) are **[Unverified]** placeholders — map to your FUAM version's actual columns before running. The *pattern* (time-band overlap + identity lookup) is the contract.

## 11.6 — Capacity-vs-gateway overlay

Put FUAM `capacity_metrics_by_timepoint` (CU% / throttling) and your `gold_fact_counter` (gateway CPU/mem/spool) on the **same `dim_date` / timepoint axis** in one report page. Answers the classic ambiguity: slow refresh = **gateway** (spool/disk/network) or **capacity** (CU throttling)?

## 11.7 — Combined semantic model relationships

One DirectLake model over both lakehouses' gold tables. Shared dimensions **`dim_date`** and **`dim_datasource`** are the bridge; `semantic_models` links your gateway facts (via enrichment) to FUAM's refresh/capacity world:

- `gold_fact_query_enriched` → `dim_date`, `gold_dim_gateway` (GatewayObjectId), `dim_datasource` (DataSource, shared), `semantic_models` (semanticModelId) [FUAM]
- `gold_fact_counter` → `dim_date`, `gold_dim_gateway`
- `capacity_metrics_by_timepoint` [FUAM] → `dim_date` (timepoint)
- `capacity_refreshables` [FUAM] → `semantic_models`, `dim_datasource`
- `activities` [FUAM] → `dim_date`

## 11.8 — Division of labor (final)

| Question | Layer |
|---|---|
| Is the gateway box CPU/disk/spool-bound? | Your build (`gold_fact_counter`) |
| Which query/datasource is slow & why (spool vs source-read)? | Your build (`gold_fact_query`) |
| Which model/user/schedule caused it? | FUAM (`activities`, `capacity_refreshables`) |
| Capacity throttling vs gateway bottleneck? | Overlay (FUAM `capacity_metrics` + your counters) |
| Refresh-schedule abuse into the gateway? | FUAM refreshables/activities + your query volume |
| Gateway online/offline & datasource health? | Neither — add REST `gateways/get-datasource-status` |

## 11.9 — Cost caveat

FUAM is a full pipeline + notebook + DirectLake stack with its own non-trivial CU footprint. If gateway performance is your **only** goal, FUAM is a heavy add — justified only when you also want tenant-wide refresh/capacity/activity monitoring.

## FUAM source
- FUAM Architecture (module→table map, extension pattern): https://github.com/microsoft/fabric-toolbox/blob/main/monitoring/fabric-unified-admin-monitoring/media/documentation/FUAM_Architecture.md

## Sources
- Microsoft Learn — Monitor/optimize gateway performance: https://learn.microsoft.com/en-us/data-integration/gateway/service-gateway-performance
- RuiRomano/pbigtwmonitor: https://github.com/RuiRomano/pbigtwmonitor
- Fabric Platform Monitoring (successor): https://github.com/microsoft/fabric-toolbox/tree/main/monitoring/fabric-platform-monitoring
- r/PowerBI gateway tuning thread: https://www.reddit.com/r/PowerBI/comments/1m8sh1i/power_bi_gateway_tuning_slow_uploadsend_speed/
- Fabric Community — gateway logs lack dataset identity: https://community.fabric.microsoft.com/t5/Report-Server/On-premise-gateway-logs-Dataset-and-Gateway-connecton/m-p/2294995


---
---

# PART B — REVISED RECOMMENDED ARCHITECTURE: Hybrid (FPM collection + your PySpark analytics)

> **Why this supersedes Part A's collection phases.** After reading the successor internals: `RuiRomano/pbigtwmonitor` is **deprecated** and `ecotte/Fabric-Monitoring-RTI` is **archived-in-place** — both redirect to Microsoft's **Fabric Platform Monitoring (FPM)**, which is the maintained all-in-one. FPM already covers gateway perf collection, heartbeat/online status, system counters, and identity-rich job correlation. So we stop hand-building the **collector** (Part A Phases 3, the REST gap-6 collector) and instead let FPM own collection, while we keep our **PySpark/Delta medallion + DirectLake** for custom analytics and retention.
>
> Source: [Fabric Platform Monitoring, microsoft/fabric-toolbox](https://github.com/microsoft/fabric-toolbox/tree/main/monitoring/fabric-platform-monitoring)

## B.0 — The gate (decide before building)

Hybrid requires BOTH of these. If either fails → fall back to **Part A pure-custom build + the REST collector (Phase 13 below)**.

1. **Fabric capacity F8 minimum, F16 recommended** (sizing scales with log volume). [FPM README]
2. **Service principal + Entra ID security group + Azure Key Vault** (secrets: Tenant ID, Client ID, SP secret), SP added explicitly to the workspace, with:
   - Tenant settings: SPs can use Fabric APIs; SPs can access read-only admin APIs; enhance admin API responses with metadata + DAX/mashup.
   - **Admin role on the On-premises Data Gateways** to monitor.
   - Member role on the workspace.

> Cost note [Inference]: teams already running a Fabric/OneLake footprint with tenant-admin access can usually clear the SP/Key Vault provisioning without friction; the real question is whether the **F8+ capacity cost** is justified for gateway monitoring alone. If you're on a trial or small capacity, prefer Part A.

## B — Target architecture

```
GATEWAY NODES (Windows, standard mode)
  FPM PowerShell scripts (Task Scheduler):
    Run-GatewayHeartbeat     → status, ~1 min loop
    Run-UploadGatewayLogs    → report files → Eventstream; raw logs → Lakehouse
    Get-DataGatewayInfo      → node info, weekly
    Setup-UpdateConfiguration / Install-DataGatewayAuto (one-time)
  config.json generated by the FPM "Gateway Config" notebook
        │
        ▼
FPM WORKSPACE  (Microsoft-maintained — DO NOT customize in place)
  Eventstreams: GatewayMonitoringHeartbeat, GatewayMonitoringReports,
                CapacityUtilizationEvents
  Eventhouse "Fabric Platform Monitoring" (KQL DB per module:
                Gateway, Capacity, Activity, Inventory)
  Notebooks (Semantic Link Labs): Audit Logs (5m), Refreshables (5m),
                Scanner (120m), Inventory (30m)
  Real-Time Dashboard + Power BI Gateway Report
     pages: Gateways · Jobs · Job Details · Queries · Running Jobs · System Counters
        │  OneLake SHORTCUT  (no copy)  ← THE SEAM
        ▼
YOUR WORKSPACE: GatewayMon_LH  (you own + extend)
  Shortcuts → FPM Eventhouse/Lakehouse gateway tables
  nb_silver / nb_gold (PySpark) → Delta medallion (retention, custom logic)
  + FUAM shortcuts (Phase 11) for capacity/refresh identity overlay
  DirectLake semantic model → your bespoke analysis
```

## Phase 0 — FPM pilot (do this FIRST, abort-cheap)

1. Create an FPM workspace on F8+ capacity. Import the **Platform Monitoring Setup** notebook; run first-time setup (creates eventstreams, eventhouse, notebooks, pipelines, report).
2. Provision the SP + Entra group + Key Vault per B.0.
3. On **one** gateway cluster: run the **Gateway Config** notebook → download the generated `config.json` → place in `/configs/` beside the scripts → run `Setup-UpdateConfiguration.ps1` (installs `Az.Accounts`, `Az.Storage`, `DataGateway` modules; auto-retrieves Gateway ID; wires Eventstream + Lakehouse).
4. Schedule `Run-GatewayHeartbeat`, `Run-UploadGatewayLogs` (Task Scheduler templates ship in `/gateway/TaskSchedulers`); `Get-DataGatewayInfo` weekly.
5. **Validation gate:** confirm the Real-Time Dashboard shows the heartbeat (online status within ~1 min) and the Gateway Report's **Queries** + **System Counters** pages populate. If data does not flow within ~30 min, troubleshoot before expanding to all clusters or building Part B analytics.

> If Phase 0 fails to capture your gateways' data → fall back to Part A. This is the cheap off-ramp.

### Phase 0 — verified deployment checklist (companion file)

> Full step-by-step with exact UI labels, script behavior, Task Scheduler XML cadences, and cited gotchas lives in the companion **`fpm_deployment_research.md`**. Key corrections vs. the README below.

**Corrections the README gets wrong / out of date (verified June 2026):**
- The semantic model is **DirectQuery over the KQL DB**, not DirectLake — you must **set a user credential on the semantic model** post-deploy or the report won't refresh ([FPM README](https://github.com/microsoft/fabric-toolbox/tree/main/monitoring/fabric-platform-monitoring)).
- Tenant setting **renamed**: README's "Service principals can use Fabric APIs" is now **"Service principals can call Fabric public APIs"** (Developer settings), split in May-June 2025 ([MS Learn - Developer tenant settings](https://learn.microsoft.com/en-us/fabric/admin/service-admin-portal-developer)).
- `Run-GatewayHeartbeat` and `Run-UploadGatewayLogs` use **boot triggers + internal loops** (heartbeat 1s, reports 5s), NOT time-based repeat triggers; `Get-DataGatewayInfo` is **weekly (Sunday)** ([Task Scheduler templates](https://github.com/microsoft/fabric-toolbox/tree/main/monitoring/fabric-platform-monitoring/gateway/TaskSchedulers)).
- The SP secret in `config.json` is **machine-bound encrypted** (`ConvertTo-SecureWithMachineKey`) - you MUST run `Setup-UpdateConfiguration.ps1` on **each node**; you cannot copy a populated config between machines.
- **Live bug (June 2026):** `Get-DataGatewayInfo.ps1` 401s via SP; fix merged - pull latest `main` ([GitHub issue #321](https://github.com/microsoft/fabric-toolbox/issues/321)).
- **VNet gateways are NOT supported** - on-prem data gateway only (scripts need local log-file access).

**Pre-deployment (before touching Fabric):**
- [ ] Fabric capacity **F8+** (F16 recommended)
- [ ] Entra app registration -> record Client ID + Tenant ID + client secret
- [ ] Entra **security group** -> SP added as member
- [ ] **Azure Key Vault** with 3 secrets (Tenant ID, Client ID, Client Secret)
- [ ] **"Key Vault Secrets User"** RBAC granted to the notebook-running user

**Fabric tenant settings (add SP security group to each):**
- [ ] Developer settings -> **Service principals can call Fabric public APIs**
- [ ] Admin API settings -> **Service principals can access read-only admin APIs**
- [ ] Admin API settings -> **Enhance admin APIs responses with detailed metadata**
- [ ] Admin API settings -> **Enhance admin APIs responses with DAX and mashup expressions**

**Workspace + gateway role:**
- [ ] Workspace on F8+; **SP added explicitly as Member** (not just the group)
- [ ] SP assigned **Admin** role on the gateway cluster (Power Platform admin center)

**Fabric items:**
- [ ] Run **Setup notebook** (set `INSTALL_GATEWAY_MODULE = True`, KV URI + secret names) -> deploys eventstreams, eventhouse/KQL, notebooks, pipelines, report
- [ ] Run **Gateway Config notebook** -> download `config.json` from Built-in Resources

**Per gateway node:**
- [ ] PowerShell 7+; copy scripts + `modules/`; create `/configs/`; drop in `config.json`
- [ ] Run `Setup-UpdateConfiguration.ps1` (installs `Az.Accounts`/`Az.Storage`/`DataGateway`/`MicrosoftPowerBIMgmt`, detects GatewayId, encrypts SP secret machine-bound)
- [ ] Import Task Scheduler XMLs: Heartbeat (boot), Upload Logs (boot), NodeInfo (weekly Sun) - fix `<UserId>` SID + script path
- [ ] `Set-ExecutionPolicy RemoteSigned` (or run with `-ExecutionPolicy Bypass`) [Unverified in repo]

**Validation gate:**
- [ ] Heartbeat shows online on **Gateways** page within ~1 min
- [ ] **Queries** page populates after a gateway job runs
- [ ] **System Counters** page populates (~10 min)
- [ ] Semantic model credential set (DirectQuery to KQL DB)
- [ ] If any of the above fail within ~30 min -> troubleshoot per companion file, or fall back to Part A.

**Unconfirmed - verify in your environment:** outbound firewall to Eventstream (AMQP 5671/5672 or 443); Azure sub permissions to create Key Vault + assign RBAC; per-node config in HA clusters (each node = own config.json + GatewayId).

## Phase B1 — Shortcut FPM gateway data into your lakehouse (the seam)

- FPM stores gateway data in its **Eventhouse (KQL DB)**; report files also land in its **Lakehouse**. In `GatewayMon_LH`:
  - For Lakehouse-resident tables: **Tables → New shortcut → Microsoft OneLake → FPM Lakehouse**.
  - For Eventhouse/KQL data: query via the KQL DB; for Delta analysis, materialize the gateway report + system counter tables into your lakehouse (OneLake availability on the KQL DB, or a small export notebook). [Inference] exact table names depend on your FPM version — confirm in the Eventhouse after Phase 0.
- **Do not customize FPM items in place** — its README warns updates may revert changes. All your logic lives in `GatewayMon_LH`.

## Phase B2 — Your PySpark analytics on top (reuse Part A Phases 6–7)

Point the **silver/gold** notebooks (Part A §6–§7) at the shortcut tables instead of raw ADLS `.log` files. The transforms are unchanged in spirit:
- Decompose `QueryExecutionDuration` vs `DataProcessingDuration` vs spooling.
- Derive `DiskRead/Write_BytesPerSec`, `SpoolShare`.
- Build `gold_fact_query`, `gold_fact_counter`, `gold_dim_gateway`.
- The Start⨝Execution join is **already done by FPM** in its Job/Query model — verify before re-implementing; you may only need light reshaping for DirectLake + retention.

## Phase B3 — FUAM overlay (Part A Phase 11) — but check for overlap

FPM **already bundles** Activity Events + Inventory modules (overlapping much of FUAM). Decision:
- If you run **FPM**, you may **not need FUAM** for activities/inventory — FPM's Job Details already carries workspace id, item id, service name, datasource kind (the identity correlation we hand-built in Phase 11).
- Keep **FUAM only** if you specifically want its **capacity metrics / refreshables** depth and its polished capacity reports. Otherwise drop Phase 11 to avoid double-ingest cost.

## Phase 13 — REST status collector (FALLBACK ONLY — Part A path)

> **Skip this if you deploy FPM** — FPM's heartbeat + `Get-DataGatewayInfo` already provide online/offline + node info + datasource health. Build this ONLY if you stay on the Part A pure-custom build (failed the B.0 gate).

Standalone PowerShell, scheduled hourly, lands JSON in ADLS `raw/_status/`:

```powershell
# Requires: MicrosoftPowerBIMgmt module; SP auth recommended.
Connect-PowerBIServiceAccount -ServicePrincipal -Credential $cred -TenantId $tid

# 1) Gateways (online/offline + metadata)
$gws = (Invoke-PowerBIRestMethod -Method Get -Url "gateways").Content | ConvertFrom-Json

# 2) Per-gateway datasource connection status
$rows = foreach ($g in $gws.value) {
  $ds = (Invoke-PowerBIRestMethod -Method Get -Url "gateways/$($g.id)/datasources").Content | ConvertFrom-Json
  foreach ($d in $ds.value) {
    $st = (Invoke-PowerBIRestMethod -Method Get `
            -Url "gateways/$($g.id)/datasources/$($d.id)/status").Content | ConvertFrom-Json
    [pscustomobject]@{
      collectedUtc = (Get-Date).ToUniversalTime().ToString("o")
      gatewayId    = $g.id; gatewayName = $g.name
      datasourceId = $d.id; datasourceName = $d.datasourceName
      datasourceType = $d.datasourceType
      status       = $st.error.code ?? "Online"   # null error => reachable
      errorMessage = $st.error.message
    }
  }
}
$out = $rows | ConvertTo-Json -Depth 5
# upload $out to ADLS raw/_status/yyyy/MM/dd/status_<timestamp>.json (Az.Storage)
```
Endpoints (Power BI REST): `GET gateways`, `GET gateways/{id}/datasources`, `GET gateways/{id}/datasources/{dsid}/status`. Ingest the `_status/` JSON in your bronze notebook → `gold_fact_datasource_status` for an availability/error timeline page.

## Final decision matrix (all 7 resources reconciled)

| Need | Use |
|---|---|
| Gateway perf collection (maintained) | **FPM** (`Run-UploadGatewayLogs`) |
| Online/offline + node info | **FPM** heartbeat / `Get-DataGatewayInfo` (else Phase 13) |
| Per-query duration/spool/errors | **FPM** report → your `gold_fact_query` |
| Query↔model/user/workspace identity | **FPM** Job Details (else Part A Phase 11 + FUAM) |
| Long-term retention + custom Delta analytics | **Your `GatewayMon_LH`** (PySpark medallion) |
| Capacity CU/throttling overlay | **FPM Capacity module** or **FUAM** (pick one) |
| Refresh/quality/Power Automate alerts | **kerski/pbi-dataops-monitoring** (optional add-on) |
| Source schema/config truth | **Microsoft Learn** gateway perf doc |
| Network bandwidth/latency (blind spot) | **Neither** — OS-level NIC/disk telemetry (Part A Phase 10) |

## Resource lineage (so the "7" is unambiguous)

- `RuiRomano/pbigtwmonitor` → **deprecated**, redirects to FPM.
- `ecotte/Fabric-Monitoring-RTI` → **archived-in-place**, redirects to FPM (it is FPM's origin).
- `microsoft/fabric-toolbox` **FPM** → the maintained successor (covers gateway perf fully).
- **FUAM** → tenant ops metadata; complementary, partial overlap with FPM's Activity/Inventory.
- `kerski/pbi-dataops-monitoring` → refresh/quality/Power Automate; not gateway-perf.
- **Microsoft Learn** → authoritative log schema/config.
- **Fabric Community thread** → confirms logs lack dataset identity; centralize-the-files pattern.

## Part B sources
- Fabric Platform Monitoring (maintained successor; gateway scripts, eventstream/eventhouse, gateway report): https://github.com/microsoft/fabric-toolbox/tree/main/monitoring/fabric-platform-monitoring
- ecotte/Fabric-Monitoring-RTI (predecessor, redirects to FPM): https://github.com/ecotte/Fabric-Monitoring-RTI
- kerski/pbi-dataops-monitoring (refresh/quality templates): https://github.com/kerski/pbi-dataops-monitoring
- Power BI REST API — Gateways: https://learn.microsoft.com/en-us/rest/api/power-bi/gateways
