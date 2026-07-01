# Phase 4 Architecture & Build Spec

> **UPDATE (Phase 6 finding — supersedes 'fuzzy attribution' language below):** Query→identity attribution is NOT architecturally impossible. The gateway `RequestId` equals `XmlaRequestId`/`OperationId` in Fabric Workspace Monitoring, so a KQL join yields exact `ExecutingUser` + `DatasetId` + DAX for Fabric semantic-model workloads (see [`phase6_northstar.md`](./phase6_northstar.md) and [`starter/kql/01_identity_join.kql`](../starter/kql/01_identity_join.kql)). The 'best-effort/fuzzy' notes below remain accurate ONLY for the no-Workspace-Monitoring fallback and for per-DirectQuery UserId / Dataflow Gen1 / Paginated Reports, which stay [Blocked-by-platform].

## Gateway Monitor: Fabric-Native On-Premises Data Gateway Observability Stack

**Date:** 2026-06-30  
**Architecture decision:** BUILD-NEW Fabric-native core + OPTIONAL FPM bridge. Not a fork. ([phase0_scope.md §8](./phase0_scope.md))  
**All code described here is [STUB] / reference implementation. Never tested in a live environment. Phase 5 pilot required before treating any component as functional.**

---

## 1. Signal-Ingestion Layer

### 1.1 Collector-to-Signal Mapping

Every signal in the [phase0_scope.md signal map](./phase0_scope.md) is mapped to a collector below.

| Signal | Signal Description | Collector | Method | Cadence | v-Target |
|---|---|---|---|---|---|
| **S1** | Gateway performance logs (QueryExecution, QueryStart, QueryAggregation, SystemCounter) | `Collect-GatewayLogs.ps1` [ADAPTED-FROM-FPM] | Local file read from `$env:USERPROFILE\AppData\Local\Microsoft\On-premises data gateway\Report` (or configured path); incremental by timestamp | Every 5 min (matches gateway write cadence) | v1 |
| **S2** | Gateway config / GatewayProperties.json / datasource list | `Get-GatewayInventory.ps1` [ADAPTED-FROM-SQLvariant] | `Get-DataGatewayCluster -Scope Organization` + REST `GET /gateways` | Every 15 min | v1 |
| **S3** | Windows PerfMon counters (CPU, memory, mashup containers) | `Collect-GatewayLogs.ps1` [ADAPTED-FROM-FPM] | `Get-Counter` on `\Process(PBIEgwService)\*` and gateway-specific PerfMon objects | Every 5 min | v1 |
| **S4** | Verbose mashup/query logs | `Collect-GatewayLogs.ps1` [ADAPTED-FROM-FPM] | Local file read (GatewayInfo.log); optional, off by default | On-demand | v3 |
| **S5** | DataGateway PowerShell module — cluster/node inventory | `Get-GatewayInventory.ps1` [ADAPTED-FROM-SQLvariant] | `Get-DataGatewayCluster -Scope Organization`, `Get-DataGatewayClusterMember` | Every 15 min | v1 |
| **S6** | Fabric/Power BI Admin REST APIs — gateway status, datasource health | `Get-GatewayInventory.ps1` [ADAPTED-FROM-SQLvariant] | REST `GET /gateways`, `GET /gateways/{id}/datasources`, `GET /gateways/{id}/datasources/{id}/status` | Every 5 min (heartbeat), 15 min (inventory) | v1 |
| **S7** | Scheduled refresh history | `Collect-GatewayLogs.ps1` (refresh extension) [NET-NEW] | REST `GET /admin/datasets/{id}/refreshes` (Admin API) | Every 15 min | v2 |
| **S8** | Fabric Capacity Metrics (CU consumption) | Not collected — out of scope v1 | Fabric Capacity Metrics semantic model is a separate artifact | — | v3 |
| **S9** | Windows Event Log — gateway service crash/restart events | `Collect-EventLog.ps1` [NET-NEW/ADAPTED] | `Get-WinEvent -FilterHashtable @{LogName='Application','System'; ProviderName='*gateway*','*OnPremises*'}` | Every 5 min | v1 |
| **S10** | VNet diagnostic download (Parquet/JSONL) | Not collected — VNet descoped v1 | Separate v2 sub-architecture ([phase0_scope.md §4](./phase0_scope.md)) | — | v2 |
| **S11** | OS-level NIC / disk / host metrics (network bandwidth, latency) | `Collect-NetworkMetrics.ps1` [NET-NEW] | `Get-Counter '\Network Interface(*)\Bytes Total/sec'`, `'\Network Interface(*)\Current Bandwidth'`; `Test-Connection` latency probe | Every 5 min | v2 |
| **S11b** | Spool disk free space | `Collect-DiskSpool.ps1` [NET-NEW] | `Get-PSDrive` on spool volume; `Get-Item` on spool directory for size | Every 5 min | v1 |
| **S12** | EvaluationContext field (QueryStart) — artifactId for Fabric workloads | Parsed in `01_bronze_ingest.py` [NET-NEW] | Inline JSON parse of `EvaluationContext` column from QueryStart log | At ingest | v1 |

### 1.2 Optional FPM Bridge

For operators already running FPM ([github.com/microsoft/fabric-toolbox](https://github.com/microsoft/fabric-toolbox)):

- FPM stores data in Eventhouse (KQL database).
- The [Eventhouse OneLake availability feature](https://learn.microsoft.com/en-us/fabric/real-time-intelligence/event-house-onelake-availability) exposes KQL tables as Delta Parquet in OneLake.
- Bronze ingest notebook has a `USE_FPM_BRIDGE = True` code path that reads these Delta-exposed tables instead of raw log files.
- **[Assumption]:** Eventhouse OneLake availability is enabled on the target capacity. This requires Fabric capacity ≥ F8. Not verified in live environment.
- Tables bridged: `GatewaysHeartbeat`, `GatewayReports-Raw`, `SystemCounters`, `QueryConnections`.

---

## 2. Bronze / Silver / Gold Delta Model

### 2.1 Layer Philosophy

| Layer | Content | Retention | Format |
|---|---|---|---|
| **Bronze** | Raw, minimally parsed; all source columns preserved; schema-adaptive | 90 days | Delta (append-only, partitioned by date) |
| **Silver** | Cleansed, typed, joined across log files; RequestId/time-window correlation | 90 days | Delta (append + overwrite for corrections) |
| **Gold** | Aggregated facts + dims; fleet rollup; alerting-ready metrics | 365 days | Delta (upsert) |

### 2.2 Bronze Table Schemas

#### Table: `bronze_query_execution`
Source: `*_QueryExecutionReport_*.log` (CSV). Adapts to any column count.

| Column | Type | Source Column Name | Notes |
|---|---|---|---|
| `GatewayObjectId` | STRING | `GatewayObjectId` | Cluster/node identity |
| `RequestId` | STRING | `RequestId` | Cross-log join key |
| `DataSource` | STRING | `DataSource` | Datasource identifier |
| `QueryTrackingId` | STRING | `QueryTrackingId` | Per-query tracking |
| `QueryExecutionEndTimeUTC` | TIMESTAMP | `QueryExecutionEndTimeUTC` | End time for time-window joins |
| `QueryExecutionDuration` | LONG | `QueryExecutionDuration(ms)` | Duration in ms |
| `QueryType` | STRING | `QueryType` | e.g., Default, Refresh |
| `DataProcessingEndTimeUTC` | TIMESTAMP | `DataProcessingEndTimeUTC` | |
| `DataProcessingDuration` | LONG | `DataProcessingDuration(ms)` | |
| `Success` | BOOLEAN | `Success` | True/False |
| `ErrorMessage` | STRING | `ErrorMessage` | Null if Success=True |
| `SpoolingDiskWritingDuration` | LONG | `SpoolingDiskWritingDuration(ms)` | 0 if no spooling |
| `SpoolingDiskReadingDuration` | LONG | `SpoolingDiskReadingDuration(ms)` | |
| `SpoolingTotalDataSize` | LONG | `SpoolingTotalDataSize(bytes)` | Compressed bytes written to spool |
| `DataReadingAndSerializationDuration` | LONG | `DataReadingAndSerializationDuration(ms)` | |
| `DiskRead` | DOUBLE | `DiskRead(byte/sec)` | |
| `DiskWrite` | DOUBLE | `DiskWrite(byte/sec)` | |
| `_extra_cols` | MAP<STRING,STRING> | All unrecognized columns | Schema-adaptive overflow |
| `_ingested_at` | TIMESTAMP | — | Pipeline metadata |
| `_source_file` | STRING | — | Source file path |
| `_partition_date` | DATE | Derived from QueryExecutionEndTimeUTC | Partition key |

#### Table: `bronze_query_start`
Source: `*_QueryStartReport_*.log` (CSV).

| Column | Type | Source Column Name | Notes |
|---|---|---|---|
| `GatewayObjectId` | STRING | `GatewayObjectId` | |
| `RequestId` | STRING | `RequestId` | Links to QueryExecution |
| `QueryTrackingId` | STRING | `QueryTrackingId` | |
| `QueryStartTimeUTC` | TIMESTAMP | `QueryStartTimeUTC` | Join anchor for time-window |
| `QueryType` | STRING | `QueryType` | |
| `DataSource` | STRING | `DataSource` | |
| `EvaluationContext` | STRING | `EvaluationContext` | Raw JSON; parsed in silver |
| `artifact_id` | STRING | Parsed from `EvaluationContext` | artifactId for Fabric workloads |
| `artifact_type` | STRING | Parsed from `EvaluationContext` | datasetId / dataflowId |
| `_extra_cols` | MAP<STRING,STRING> | — | Schema-adaptive overflow |
| `_ingested_at` | TIMESTAMP | — | |
| `_partition_date` | DATE | Derived from QueryStartTimeUTC | |

#### Table: `bronze_system_counter`
Source: `*_SystemCounterReport_*.log` (CSV).

| Column | Type | Source Column Name | Notes |
|---|---|---|---|
| `GatewayObjectId` | STRING | `GatewayObjectId` | |
| `CounterTimeUTC` | TIMESTAMP | `CounterTimeUTC` | 5-min aggregation timestamp |
| `SystemCPUPercent` | DOUBLE | `SystemCPUPercent` | Host-level CPU % |
| `SystemMEMUsedPercent` | DOUBLE | `SystemMEMUsedPercent` | Host-level mem % |
| `GatewayCPUPercent` | DOUBLE | `GatewayCPUPercent` | Gateway process CPU % |
| `GatewayMEMKb` | LONG | `GatewayMEMKb` | Gateway process memory KB |
| `_extra_cols` | MAP<STRING,STRING> | — | |
| `_partition_date` | DATE | | |

#### Table: `bronze_event_log`
Source: `Collect-EventLog.ps1` JSON output.

| Column | Type | Notes |
|---|---|---|
| `TimeCreated` | TIMESTAMP | Event time |
| `EventId` | INT | Windows Event ID |
| `LevelDisplayName` | STRING | Error / Warning / Information |
| `ProviderName` | STRING | Source (e.g., "On-premises data gateway") |
| `Message` | STRING | Full event message |
| `GatewayHostName` | STRING | Collector hostname |
| `_ingested_at` | TIMESTAMP | |
| `_partition_date` | DATE | |

#### Table: `bronze_network_metrics`
Source: `Collect-NetworkMetrics.ps1` JSON output.

| Column | Type | Notes |
|---|---|---|
| `CollectedAtUTC` | TIMESTAMP | Sample time |
| `NicName` | STRING | Network adapter name |
| `BytesTotalPerSec` | DOUBLE | `\Network Interface(*)\Bytes Total/sec` |
| `CurrentBandwidthBps` | DOUBLE | `\Network Interface(*)\Current Bandwidth` |
| `LatencyMs_PBIRelay` | DOUBLE | `Test-Connection` RTT to relay endpoint |
| `TargetHost` | STRING | Latency probe target (msftncsi.com or configured) |
| `GatewayHostName` | STRING | |
| `_partition_date` | DATE | |

#### Table: `bronze_disk_spool`
Source: `Collect-DiskSpool.ps1` JSON output.

| Column | Type | Notes |
|---|---|---|
| `CollectedAtUTC` | TIMESTAMP | |
| `SpoolDriveLetter` | STRING | e.g., "C" |
| `FreeSpaceBytes` | LONG | Drive free space |
| `TotalSpaceBytes` | LONG | Drive total space |
| `FreeSpacePct` | DOUBLE | Computed |
| `SpoolDirSizeBytes` | LONG | Current spool directory size |
| `GatewayHostName` | STRING | |
| `_partition_date` | DATE | |

#### Table: `bronze_gateway_inventory`
Source: `Get-GatewayInventory.ps1` JSON output.

| Column | Type | Notes |
|---|---|---|
| `GatewayClusterId` | STRING | |
| `GatewayClusterName` | STRING | |
| `GatewayObjectId` | STRING | Node-level ID |
| `GatewayNodeName` | STRING | |
| `Status` | STRING | e.g., Live, Offline |
| `Version` | STRING | Installed gateway version |
| `DatasourceCount` | INT | Datasources on this node |
| `ClusterScope` | STRING | Organization / User |
| `CollectedAtUTC` | TIMESTAMP | |
| `_partition_date` | DATE | |

#### Table: `bronze_refresh_history`
Source: REST `GET /admin/datasets/{id}/refreshes`.

| Column | Type | Notes |
|---|---|---|
| `DatasetId` | STRING | |
| `WorkspaceId` | STRING | |
| `RefreshType` | STRING | Scheduled / OnDemand / DirectQuery |
| `StartTime` | TIMESTAMP | |
| `EndTime` | TIMESTAMP | |
| `Status` | STRING | Completed / Failed / Unknown |
| `ServiceExceptionJson` | STRING | Error detail from service |
| `RequestId` | STRING | Nullable; present for OnDemand |
| `_partition_date` | DATE | |

---

### 2.3 Silver Tables

#### Table: `silver_query_execution` — Cleansed + Typed
- Derived from `bronze_query_execution` + `bronze_query_start`
- Join: `bronze_query_execution.RequestId = bronze_query_start.RequestId`
- Adds: `artifact_id`, `artifact_type` from QueryStart EvaluationContext
- Adds: `query_start_time_utc` from QueryStart for duration validation
- Casts all duration columns to LONG (ms); coerces nulls to 0 for spool fields
- De-duplicates on `(RequestId, QueryTrackingId)` with latest-wins

#### Table: `silver_triage` — Unified Failure Triage [Differentiator #2]
- Joins: `silver_query_execution` (failed rows) + `bronze_refresh_history` + `bronze_event_log`
- Join logic:
  - Primary: `silver_query_execution.RequestId = bronze_refresh_history.RequestId` (exact)
  - Fallback: time-window join `|t_refresh_start - t_query_exec| ≤ 30 seconds AND same GatewayObjectId` **[labeled: BEST-EFFORT-TIME-WINDOW]**
  - Event log join: `|t_event - t_query_exec| ≤ 120 seconds AND GatewayHostName matches`
- Adds: `triage_confidence` column: `EXACT_REQUESTID` / `TIME_WINDOW` / `HOST_ONLY`
- Adds: `failure_layer` column: `GATEWAY_LOG_ERROR` / `SERVICE_REFRESH_ERROR` / `OS_SERVICE_EVENT` / `NETWORK_LATENCY_SPIKE`
- **[Assumption]:** RequestId is present in refresh history only for OnDemand refreshes, not scheduled. Scheduled refresh triage falls back to time-window. [Inference from MS docs]

#### Table: `silver_identity_attribution` — Best-Effort [Differentiator #3]
- Joins: `silver_query_execution` + Power BI Activity Events (fetched via REST Admin API)
- Join logic:
  - Primary: `EvaluationContext.artifactId` → dataset mapping (Fabric workloads only)
  - Secondary: time-window match `|t_activity - t_query_start| ≤ 60 seconds AND ArtifactId matches` **[labeled: FUZZY-TIME-WINDOW]**
- Adds: `attribution_confidence`: `EVALUATION_CONTEXT` / `FUZZY_TIME_WINDOW` / `UNATTRIBUTED`
- Adds: `user_alias` (from activity event UserState), `workspace_id`, `dataset_name`
- **PROMINENT LABEL in all DAX measures and report visuals:** "Identity attribution is best-effort and unreliable for Paginated Reports, Dataflow Gen1, and DirectQuery user sessions. Do not use for compliance or billing."

#### Table: `silver_network_correlated`
- Joins: `bronze_network_metrics` + `silver_query_execution` on GatewayHostName + time window (±5 min)
- Adds: `bytes_per_sec_at_query_time`, `latency_ms_at_query_time`, `bandwidth_utilization_pct`
- Computed: `network_throughput_efficiency = SpoolingTotalDataSize / (QueryExecutionDuration_s * BytesTotalPerSec)`

---

### 2.4 Gold Tables

#### Table: `gold_gateway_health` — Fleet View + Alerting
- Per-node, per-5-min heartbeat aggregation
- Columns: `GatewayObjectId`, `GatewayClusterName`, `last_heartbeat_utc`, `heartbeat_age_minutes`, `status_current`, `query_count_5min`, `error_count_5min`, `error_rate_pct`, `cpu_pct_avg`, `mem_kb_avg`, `spool_free_pct`, `nic_bytes_per_sec_avg`, `latency_ms_p95`
- Partitioned by date; upserted on `(GatewayObjectId, window_start_utc)`
- **Drives Activator alerting on `heartbeat_age_minutes`**

#### Table: `gold_query_performance` — Query Saturation
- Per-datasource, per-hour aggregations
- Columns: `GatewayObjectId`, `GatewayClusterName`, `DataSource`, `QueryType`, `hour_utc`, `query_count`, `error_count`, `error_rate_pct`, `duration_avg_ms`, `duration_p95_ms`, `duration_max_ms`, `spool_total_bytes_sum`, `spool_writing_ms_avg`, `data_reading_ms_avg`, `disk_read_bps_avg`, `disk_write_bps_avg`

#### Table: `gold_cluster_load` — Load Skew Score
- Per-cluster, per-hour
- Columns: `GatewayClusterId`, `GatewayClusterName`, `hour_utc`, `node_count`, `queries_per_node_cv` (coefficient of variation — load skew score), `hottest_node_id`, `hottest_node_query_pct`
- **Load skew score:** CV of per-node query counts within cluster. CV > 0.5 = imbalanced. [Inference: threshold is configurable]

#### Table: `gold_dim_gateway` — Slowly Changing Dimension
- `GatewayObjectId`, `GatewayClusterName`, `GatewayNodeName`, `Version`, `DatasourceCount`, `Status`, `valid_from`, `valid_to`, `is_current`

---

## 3. Processing Layer — Fabric PySpark Notebooks

| Notebook | Function | Key Logic | Differentiators |
|---|---|---|---|
| `01_bronze_ingest.py` | Raw log → Bronze Delta | Schema-adaptive column-name parser; FPM bridge toggle; incremental watermark | #5 |
| `02_silver_correlate.py` | Bronze → Silver joins | RequestId+time-window triage join; EvaluationContext attribution; network correlation | #2, #3, #4 |
| `03_gold_aggregate.py` | Silver → Gold aggregates | Heartbeat health rollup; load-skew CV; fleet rollup | #1, #6, fold-ins |

**Scheduling:** Fabric Notebook Pipelines or Data Factory pipeline, triggered every 5 minutes for Bronze+Gold health, every 15 minutes for full Silver/Gold refresh. [Assumption: Fabric Pipeline scheduler is available at F8+]

---

## 4. Semantic Model + Key DAX Measures

### 4.1 DirectLake vs. DirectQuery Choice

**Decision: DirectLake over Gold Delta tables.**

Rationale:
- Gold tables are pre-aggregated; DirectLake over Delta is the correct Fabric-native pattern for pre-aggregated data ([Fabric docs on DirectLake](https://learn.microsoft.com/en-us/fabric/get-started/direct-lake-overview)).
- DirectQuery over Delta is slower for operational dashboards; the 5-minute notebook cadence provides freshness without real-time DirectQuery overhead.
- **[Assumption]:** Fabric capacity ≥ F8 supports DirectLake. Requires the semantic model to be in the same workspace as the Lakehouse.
- **[Known limitation]:** DirectLake does not support all DAX time-intelligence functions identically to import mode. Test on live environment (Phase 5).

### 4.2 Key DAX Measures

Full file at `starter/semantic-model/measures.dax`. Summary:

| Measure | Purpose | Pain # |
|---|---|---|
| `Avg Query Duration (ms)` | Per-datasource average | #1, #2 |
| `P95 Query Duration (ms)` | 95th percentile via PERCENTILE.INC | #2 |
| `Gateway Error Rate %` | `DIVIDE(error_count, query_count)` | #1, #2 |
| `Gateway CPU % (Normalized)` | `GatewayCPUPercent / CoreCount` — requires CoreCount dim | #3 |
| `Spool Throughput (MB/s)` | `SpoolingTotalDataSize / SpoolingDiskWritingDuration` in MB/s | #9 |
| `Cluster Load Skew Score` | CV from gold_cluster_load | #6 |
| `Heartbeat Age (minutes)` | `NOW() - last_heartbeat_utc` (in minutes) | #1 |
| `Network Utilization %` | `BytesTotalPerSec / CurrentBandwidthBps * 100` | #7 |
| `Attribution Confidence` | Distribution of EVALUATION_CONTEXT vs FUZZY_TIME_WINDOW | #3 |

---

## 5. Report + Activator Alerting Layer

### 5.1 Report Pages (answering all 10 Jobs-to-be-Done)

| Page | JtBD Answered | Key Visuals |
|---|---|---|
| **Fleet Overview** | #3 (Headroom), #6 (Online/health), #7 (Cluster load skew) | Multi-gateway scorecard; node status grid; cluster load-skew bar |
| **Query Saturation** | #1 (Query saturation), #5 (Spool disk spill) | Top datasources by query count; duration trend; spool size scatter |
| **Failure Triage** | #2 (Refresh fault attribution), #10 (Service stability) | Failure timeline; triage confidence breakdown; EventLog overlay |
| **Identity Attribution** | #8 (Identity correlation) | Best-effort dataset/user attribution; confidence label prominent |
| **Network & Disk** | #9 (Network bottleneck), #5 (Spool) | NIC bytes/sec vs spool size; latency trend; disk free-space gauge |
| **Source Routing** | #4 (Source→gateway routing) | Datasource→gateway Sankey; cluster membership table |

### 5.2 Activator Alerting Rules

See `starter/alerting/activator-rules.md` for full rule definitions.

| Rule | Trigger | Threshold (configurable) | Action | Pain # |
|---|---|---|---|---|
| `gateway-offline` | `heartbeat_age_minutes > threshold` | Default: 10 min | Email / Teams webhook | #1 |
| `disk-spool-low` | `spool_free_pct < threshold` | Default: 15% | Email + Teams | #9 |
| `credential-drift` | datasource status = "Unknown" or "Error" for N consecutive polls | Default: 3 polls | Teams notification | #10 |
| `error-rate-spike` | `error_rate_pct > threshold` for 5-min window | Default: 25% | Teams notification | #2 |
| `network-saturation` | `network_utilization_pct > threshold` | Default: 80% | Email | #7 |

**[Assumption]:** Fabric Activator (formerly Reflex) is available at F8+. Rule DSL uses the Activator rule format documented at [learn.microsoft.com/en-us/fabric/real-time-intelligence/activator-introduction](https://learn.microsoft.com/en-us/fabric/real-time-intelligence/activator-introduction). Rule syntax is [Unverified] — requires live Activator environment to validate exact format.

---

## 6. Feature-to-Component Traceability Table

**This is the master accountability artifact. Every differentiator and pain point must have a home.**

| Pain # | Pain Description | Differentiator # | Collector | Bronze Table | Silver Table | Gold Table | DAX Measure | Activator Rule | Report Page |
|---|---|---|---|---|---|---|---|---|---|
| **#1** | No real-time alerting | **#1** | `Collect-GatewayLogs.ps1` (heartbeat via REST S6) | `bronze_gateway_inventory` | — | `gold_gateway_health` | `Heartbeat Age (minutes)` | `gateway-offline` | Fleet Overview |
| **#2** | Opaque refresh failures | **#2** | `Collect-GatewayLogs.ps1` + REST refresh history + `Collect-EventLog.ps1` | `bronze_query_execution`, `bronze_refresh_history`, `bronze_event_log` | `silver_triage` | `gold_query_performance` | `Gateway Error Rate %`, `P95 Query Duration` | `error-rate-spike` | Failure Triage |
| **#3** | Zero query attribution | **#3** | `Collect-GatewayLogs.ps1` (S12 EvaluationContext) + REST Activity Events | `bronze_query_start` | `silver_identity_attribution` | — | `Attribution Confidence` | — | Identity Attribution |
| **#4** | PBIT breaks on upgrade | **#5** | All collectors (adaptive output) | All bronze tables (schema-adaptive) | — | — | — | — | — |
| **#5** | Mashup memory bloat | Partial | `Collect-GatewayLogs.ps1` (SystemCounter S3) | `bronze_system_counter` | `silver_query_execution` | `gold_query_performance` | `Gateway CPU % (Normalized)` | — | Query Saturation |
| **#6** | No fleet/multi-gateway view | Fold-in | `Get-GatewayInventory.ps1` (S5) | `bronze_gateway_inventory` | — | `gold_dim_gateway`, `gold_cluster_load` | `Cluster Load Skew Score` | — | Fleet Overview |
| **#7** | Network blind spot | **#4** | `Collect-NetworkMetrics.ps1` (S11) | `bronze_network_metrics` | `silver_network_correlated` | `gold_gateway_health` | `Network Utilization %` | `network-saturation` | Network & Disk |
| **#8** | Manual brittle setup | — | `config.sample.json`, `credentials.md` | — | — | — | — | — | — |
| **#9** | Disk spooler surprises | Fold-in | `Collect-DiskSpool.ps1` (S11b) | `bronze_disk_spool` | — | `gold_gateway_health` | `Spool Throughput (MB/s)` | `disk-spool-low` | Network & Disk |
| **#10** | Credential state drift | Fold-in | `Get-GatewayInventory.ps1` (datasource status REST) | `bronze_gateway_inventory` | — | `gold_gateway_health` | — | `credential-drift` | Fleet Overview |
| S9 (EventLog) | Service crash/restart | Fold-in | `Collect-EventLog.ps1` | `bronze_event_log` | `silver_triage` | — | — | — | Failure Triage |
| S12 (EvaluationContext) | Artifact identity | #3 | `Collect-GatewayLogs.ps1` | `bronze_query_start` | `silver_identity_attribution` | — | — | — | Identity Attribution |

---

## 7. Deployment, Scheduling, Security, and Credential Model

### 7.1 Prerequisites

| Item | Requirement | Notes |
|---|---|---|
| Fabric Capacity | F8+ | F2 may work for dev; F8 recommended for DirectLake |
| Service Principal | One SP with Fabric Admin + Gateway Admin roles | See `credentials.md` |
| Azure Key Vault | SP client secret stored in Key Vault | Accessed via Fabric Data Pipeline credential store or notebook secret scope |
| Gateway node access | PowerShell execution on each gateway host | Windows scheduled task or Azure Arc |
| Power BI Activity Events | Admin API access | Requires Fabric/Power BI Admin or delegated admin |

### 7.2 Deployment Order

1. **Key Vault** — provision SP, store secret
2. **Fabric Workspace + Lakehouse** — create OneLake with `bronze/`, `silver/`, `gold/` containers
3. **Configure collectors** — edit `config.sample.json`; deploy PowerShell scripts to each gateway host; register as Windows Scheduled Tasks (Task Scheduler)
4. **Bronze pipeline** — create Fabric Data Pipeline calling `01_bronze_ingest.py`; schedule every 5 min
5. **Silver pipeline** — `02_silver_correlate.py`; schedule every 15 min
6. **Gold pipeline** — `03_gold_aggregate.py`; schedule every 15 min
7. **Semantic model** — publish DirectLake model over gold tables; apply `measures.dax`
8. **Report** — publish Power BI report
9. **Activator** — create Activator item connected to `gold_gateway_health` Delta table; configure rules per `activator-rules.md`

### 7.3 Security Model

- All secrets (SP client secret, Key Vault URI) stored in Azure Key Vault — never in config files or notebooks.
- PowerShell collectors authenticate using SP credentials fetched from Key Vault at runtime via `Get-AzKeyVaultSecret`.
- Fabric notebooks use notebook secret scope or Fabric Pipeline credential store — **no hardcoded credentials**.
- The `Get-DataGatewayInfo` SP 401 bug ([Fabric community](https://community.fabric.microsoft.com/t5/Real-Time-Intelligence/Fabric-Platform-Monitoring-accelerator-Gateways-module/m-p/4884544)) must be resolved before collector deployment: ensure the SP is added as a Gateway Admin (not just Fabric Admin) AND that the `EnablePowerBIManagementApiForGatewayAdmins` tenant setting is enabled. [Assumption: this resolves the 401; unconfirmed in live env]

---

## 8. Risks, Assumptions, Open Questions, and Known Unknowns

### 8.1 Known Unknowns (must validate in Phase 5)

| Item | Description | Impact |
|---|---|---|
| **Get-DataGatewayInfo 401 bug** | SP returns 401 on gateway admin APIs in some tenants ([Fabric community](https://community.fabric.microsoft.com/t5/Real-Time-Intelligence/Fabric-Platform-Monitoring-accelerator-Gateways-module/m-p/4884544)) | Blocks inventory collector; workaround unverified |
| **Paginated Reports not logged** | Gateway query log does not capture Paginated Report queries ([MS docs](https://learn.microsoft.com/en-us/data-integration/gateway/service-gateway-performance)) | Identity attribution and query saturation metrics are incomplete for PaginatedReport workloads |
| **Premium capacity queries sometimes missed** | "Premium capacity queries sometimes missed" per MS docs — reason not documented | Query count metrics may undercount in Premium capacity contexts |
| **RequestId attribution fuzziness** | DirectQuery sessions do not have a stable RequestId in the audit log; scheduled refresh OnDemand-triggered queries may | Triage confidence labeled FUZZY_TIME_WINDOW for most records |
| **StreamBeforeRequestCompletes interaction** | Setting `StreamBeforeRequestCompletes=true` in gateway config changes spool behavior — may cause SpoolingTotalDataSize to be 0 when streaming is active | Spool metrics require this setting to be false (default); collector should warn if non-default detected |
| **EvaluationContext scope** | `EvaluationContext.artifactId` is populated only for Fabric workloads; not for Dataflow Gen1, Paginated Reports, or Power BI datasets on shared capacity | Identity attribution returns UNATTRIBUTED for these workload types |
| **Activator rule syntax** | Fabric Activator rule format is evolving; exact DSL syntax not verified against live Activator | `activator-rules.md` is reference only; validate in Phase 5 |
| **DirectLake time-intelligence** | DirectLake semantic model time-intelligence DAX may behave differently than import mode | All DAX measures require live-env validation |

### 8.2 Assumptions

| Item | Assumption | Risk if Wrong |
|---|---|---|
| F8+ capacity | Assumed available; required for DirectLake | Drop to DirectQuery over Delta (slower) |
| Key Vault access | SP has Key Vault Secrets User role | Collectors cannot authenticate |
| Gateway log path | Default `$env:USERPROFILE\AppData\Local\Microsoft\On-premises data gateway\Report` | Must be overridden in config for non-default service accounts |
| Windows Task Scheduler | Available on all gateway hosts | May need Azure Arc for constrained environments |
| FPM bridge Eventhouse OneLake availability | Enabled on customer's Eventhouse | FPM bridge code path fails silently if not enabled |

### 8.3 Top 3 Risks

1. **SP 401 bug on `Get-DataGatewayInfo`** — If the bug is not resolved, inventory and heartbeat collectors fail. Workaround requires gateway admin UI configuration that may not be automatable. **Mitigation:** Provide fallback to user-delegated token with documented manual step; this was a noted bug in FPM as of June 2026.

2. **Schema-adaptive parser edge cases** — The `_extra_cols` overflow handles additions, but a column REMOVAL or RENAME in a future gateway version could break column-name-based lookups for that column. **Mitigation:** Emit a warning metric `schema_warn_missing_cols` for any expected column not found; alert via Activator.

3. **DirectLake refresh latency** — DirectLake semantic model reads Delta snapshot at query time; if Gold notebook hasn't run recently, the model shows stale data. **Mitigation:** Gold notebook runs every 5 min for `gold_gateway_health`; model refresh lag is bounded.

---

## 9. Phased MVP → v1 → v2 → v3 Roadmap

### MVP (Proof of Concept — Phase 5 target)
**Goal:** Validate collectors work on a real gateway host; schema-adaptive parser ingest to Bronze; basic Fleet Overview report.

| Component | Scope |
|---|---|
| Collectors | `Collect-GatewayLogs.ps1` (S1) + `Get-GatewayInventory.ps1` (S5, S6) + `Collect-DiskSpool.ps1` (S11b) |
| Bronze | `bronze_query_execution`, `bronze_gateway_inventory`, `bronze_disk_spool` |
| Gold | `gold_gateway_health` (subset) |
| Report | Fleet Overview page only |
| Alerting | `gateway-offline` Activator rule |

### v1 (Differentiators #1 + #5 — highest pain, lowest effort)
**Adds:** Full schema-adaptive Bronze ingest for all 4 log files; Silver triage (partial — EventLog only); full Gold; all 6 report pages; `disk-spool-low` + `gateway-offline` Activator rules; `Collect-EventLog.ps1`.

### v2 (Differentiators #4 + #2)
**Adds:** `Collect-NetworkMetrics.ps1` + `bronze_network_metrics` + `silver_network_correlated`; `silver_triage` with full RefreshHistory join; REST refresh history collector; `credential-drift` Activator rule; VNet sub-architecture spec.

### v3 (Differentiator #3 + cheap folds)
**Adds:** `silver_identity_attribution` (best-effort); REST Activity Events poller; per-mashup-process PID correlation attempt; personal gateway detection; gateway version currency check.

---

## 10. Executive Summary

### What We Are Building

A Fabric-native open-source gateway observability stack that any Power BI / Fabric tenant admin can deploy by forking a single GitHub repository. The tool combines the best patterns from 10 existing tools (FPM's RTI pipeline, pbigtwmonitor's incremental upload, SQLvariant's cluster inventory, martinskeem's Event Log integration, MS Gateway Performance Monitoring's log schema) and fills the five capability gaps where all existing tools fail simultaneously:

1. **Proactive gateway-offline alerting** — via Fabric Activator on heartbeat data; no Logic App required
2. **Unified failure triage** — three-way correlation of refresh history + gateway log + OS event log
3. **Best-effort query attribution** — EvaluationContext + audit log join, labeled fuzzy
4. **Network / bandwidth metrics** — the only open-source tool to surface NIC counters alongside gateway performance
5. **Schema-adaptive log parser** — survives gateway version column additions without breaking

### Why It Beats the Top Sources

- **vs. FPM:** Delta-native (not KQL-locked); adds S7, S9, S11 signals FPM misses; includes alerting engine; forkable without KQL/Eventhouse expertise
- **vs. PBIT template:** Schema-adaptive parser never breaks on upgrade; centralized to OneLake; proactive alerting
- **vs. SummitView/Sentinel (commercial):** Open source; adds performance telemetry those tools lack; no vendor dependency

### Top 3 Risks

1. `Get-DataGatewayInfo` SP 401 bug may block inventory collection — requires live-env workaround validation
2. DirectLake time-intelligence DAX requires live-env testing — may need fallback patterns
3. Activator rule syntax is evolving — `activator-rules.md` requires Phase 5 validation

### Immediate Next 3 Actions

1. **Phase 5 pilot setup:** Provision F8 Fabric capacity + gateway host VM; deploy MVP collectors; validate `Collect-GatewayLogs.ps1` reads logs correctly and Bronze Delta table is written
2. **Resolve SP 401:** Test `Get-DataGatewayInfo` with SP in Gateway Admin role + `EnablePowerBIManagementApiForGatewayAdmins` tenant setting; document exact resolution
3. **Validate schema-adaptive parser:** Run `01_bronze_ingest.py` against real gateway log files; confirm `_extra_cols` capture and no DataFormat.Error; baseline the column set against current gateway version

---

*All architecture decisions grounded in phase0_scope.md, phase1_2_tools.md, phase3_painpoints.md, pipeline_critique.md. Column names sourced from [learn.microsoft.com/en-us/data-integration/gateway/service-gateway-performance](https://learn.microsoft.com/en-us/data-integration/gateway/service-gateway-performance). All code described here is reference/[Unverified] — requires Phase 5 live-environment pilot.*
