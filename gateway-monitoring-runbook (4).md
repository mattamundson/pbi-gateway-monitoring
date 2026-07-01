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

## Sources
- Microsoft Learn — Monitor/optimize gateway performance: https://learn.microsoft.com/en-us/data-integration/gateway/service-gateway-performance
- RuiRomano/pbigtwmonitor: https://github.com/RuiRomano/pbigtwmonitor
- Fabric Platform Monitoring (successor): https://github.com/microsoft/fabric-toolbox/tree/main/monitoring/fabric-platform-monitoring
- r/PowerBI gateway tuning thread: https://www.reddit.com/r/PowerBI/comments/1m8sh1i/power_bi_gateway_tuning_slow_uploadsend_speed/
- Fabric Community — gateway logs lack dataset identity: https://community.fabric.microsoft.com/t5/Report-Server/On-premise-gateway-logs-Dataset-and-Gateway-connecton/m-p/2294995
