# Power BI Gateway Performance Monitoring — Build Runbook
### Hybrid architecture: Rui Romano's collector → ADLS Gen2 landing → Fabric Lakehouse (PySpark medallion) → DirectLake

> **Source basis:** [RuiRomano/pbigtwmonitor](https://github.com/RuiRomano/pbigtwmonitor) (PowerShell collector + ADLS layout, reused as-is) and [Microsoft Learn: Monitor and optimize on-premises data gateway performance](https://learn.microsoft.com/en-us/data-integration/gateway/service-gateway-performance) (log schema + config). The analytics layer is rebuilt natively in Fabric.

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
