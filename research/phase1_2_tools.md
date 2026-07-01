# Top 10 Tools for Monitoring Power BI / Microsoft Fabric On-Premises Data Gateway

> **Scope:** On-premises Data Gateway (standard mode) performance and health monitoring only.  
> VNet-only gateways and generic API/app-gateway tools are excluded.  
> Entries marked **[Unverified]** denote features claimed by a source but not independently confirmed in code or docs.

---

## Per-Tool Profiles

---

### 1. Microsoft Fabric Platform Monitoring (FPM) — Gateway Monitoring Module

| Field | Detail |
|---|---|
| **Name** | Fabric Platform Monitoring (FPM) — Gateway Monitoring |
| **URL** | [github.com/microsoft/fabric-toolbox/monitoring/fabric-platform-monitoring](https://github.com/microsoft/fabric-toolbox/tree/main/monitoring/fabric-platform-monitoring) · [Fabric Jumpstart catalog entry](https://jumpstart.fabric.microsoft.com/catalog/fpm-gateway-monitoring/) |
| **Type** | MS first-party community OSS (Microsoft Fabric CAT team) |
| **What it monitors (gateway-specific)** | Per-gateway heartbeat telemetry (availability / latency), query-execution events (JobId, DurationMs, DataSourceType, Success/Error, SpoolingDurationMs, DataReadingDuration), running jobs in real time, system counters (CPU %, Memory KB, Disk R/W bytes/sec) streamed from each gateway node |
| **Best attribute / crown-jewel** | **End-to-end RTI pipeline**: PowerShell uploader → two Eventstreams → Eventhouse KQL database → DirectQuery Power BI report (6 pages: Jobs · Job Details · Queries · Running Jobs · Gateways · System Counters). Near-real-time stream means no batch lag. Deploy in ~2 min via Fabric Jumpstart. |
| **Notable limitation** | Requires a Microsoft Fabric capacity (F-SKU or P-SKU); does not work on Power BI Premium per-user alone. Each gateway machine needs the PowerShell uploader installed separately. |
| **Artifact present** | Yes — PowerShell uploader, Fabric Notebook setup script, KQL schemas, DirectQuery semantic model, Power BI report (6 pages). Fabric Jumpstart auto-deploys the full stack. |
| **Freshness / maintained?** | Actively maintained by Microsoft Fabric CAT; Jumpstart entry confirmed live June 2026. Canonical successor to ecotte/Fabric-Monitoring-RTI and RuiRomano/pbigtwmonitor. |
| **Evidence note** | Jumpstart catalog page describes all deployed items verbatim: two Eventstreams, Eventhouse tables (`GatewaysHeartbeat`, `GatewayReports-Raw`), 6-page DirectQuery report, six config variables (`HeartbeatEnable`, `HeartbeatInterval`, `ReportSendInterval`, etc.) — [Fabric Jumpstart](https://jumpstart.fabric.microsoft.com/catalog/fpm-gateway-monitoring/). |

---

### 2. RuiRomano/pbigtwmonitor (Deprecated — Archive)

| Field | Detail |
|---|---|
| **Name** | pbigtwmonitor |
| **URL** | [github.com/RuiRomano/pbigtwmonitor](https://github.com/RuiRomano/pbigtwmonitor) |
| **Type** | Community OSS (DEPRECATED — archived Dec 10, 2025) |
| **What it monitors (gateway-specific)** | Gateway query logs (query text, duration, data-source, error messages), system performance counters (CPU %, Memory), gateway error/info logs, mashup engine logs, gateway request log; multi-cluster centralization to ADLS Gen2 |
| **Best attribute / crown-jewel** | **Two PBIT templates for zero-deployment quick analysis**: `Gateway Monitor - FromDisk.pbit` (point directly at local log folder, no infrastructure needed) and `Gateway Monitor - FromLake.pbit` (ADLS-backed centralized store). Disk template remains the fastest way to ad-hoc analyze gateway logs today. |
| **Notable limitation** | Archived; no new development. Requires ADLS Gen2 + PowerShell 7 with Az modules for full deployment. Query start/execution logs only — no heartbeat stream. |
| **Artifact present** | Yes — `Run.ps1`, `UploadGatewayLogs.ps1`, `Utils.psm1`, `Gateway Monitor - FromDisk.pbit`, `Gateway Monitor - FromLake.pbit`, `Tools - Get Dataset Refresh History.ps1`. Full pages: Logs, Queries, Gateway Profile, Counters, Requests, Mashup Profiles, Mashup Logs. |
| **Freshness / maintained?** | Archived read-only as of Dec 10, 2025. Deprecation notice points to FPM (entry #1). 101 stars, 35 forks — widely adopted reference. |
| **Evidence note** | GitHub archive notice and repo file listing confirmed by direct fetch — [RuiRomano/pbigtwmonitor](https://github.com/RuiRomano/pbigtwmonitor). |

---

### 3. ecotte/Fabric-Monitoring-RTI (Predecessor — Folded into FPM)

| Field | Detail |
|---|---|
| **Name** | Fabric-Monitoring-RTI |
| **URL** | [github.com/ecotte/Fabric-Monitoring-RTI](https://github.com/ecotte/Fabric-Monitoring-RTI) |
| **Type** | Community OSS (Microsoft employee — Edgar Cotte) |
| **What it monitors (gateway-specific)** | Gateway locks/events, audit log streams, platform inventory; RTI-based Eventstream ingestion pipeline (direct predecessor architecture to FPM) |
| **Best attribute / crown-jewel** | First public proof-of-concept that gateway log telemetry could be streamed into Fabric RTI (Eventstream → Eventhouse → KQL); established the design pattern now used in FPM. Chris Webb's blog describes it as "even more powerful" than pbigtwmonitor at the time — [blog.crossjoin.co.uk](https://blog.crossjoin.co.uk/2024/10/27/new-solution-for-monitoring-power-bi-fabric-gateways/). |
| **Notable limitation** | No longer actively developed standalone; effectively folded into FPM. |
| **Artifact present** | Yes — GitHub repo with Eventstream/Eventhouse configs and README (Oct 2024). |
| **Freshness / maintained?** | Superseded by FPM; historical reference only. |
| **Evidence note** | GitHub repo confirmed live; Chris Webb crossjoin.co.uk blog (Oct 27 2024) describes the evolution from pbigtwmonitor → ecotte RTI → FPM. |

---

### 4. Microsoft Gateway Performance Monitoring — Built-in Feature + PBIT Template

| Field | Detail |
|---|---|
| **Name** | Microsoft On-Premises Data Gateway — Performance Monitoring (public preview) + Gateway Performance PBI Template |
| **URL** | [learn.microsoft.com/en-us/data-integration/gateway/service-gateway-performance](https://learn.microsoft.com/en-us/data-integration/gateway/service-gateway-performance) |
| **Type** | MS first-party (feature embedded in gateway; PBIT downloadable from docs) |
| **What it monitors (gateway-specific)** | Four CSV log files written locally: (1) Query Execution Report — GatewayObjectId, RequestId, DataSource, QueryTrackingId, QueryExecutionEndTimeUTC, QueryExecutionDuration(ms), QueryType, DataProcessingEndTimeUTC, DataProcessingDuration(ms), Success, ErrorMessage, SpoolingDiskWritingDuration(ms), SpoolingDiskReadingDuration(ms), SpoolingTotalDataSize(bytes), DataReadingAndSerializationDuration(ms), DiskRead(byte/sec), DiskWrite(byte/sec); (2) Query Start Report — includes base64-encoded QueryText + EvaluationContext (artifactId/dataflowId); (3) Query Execution Aggregation Report — 5-min aggregations of Avg/Max/Min duration + count per datasource+querytype+success; (4) System Counter Aggregation Report — SystemCPUPercent, SystemMEMUsedPercent, GatewayCPUPercent, GatewayMEMKb per node at 5-min intervals |
| **Best attribute / crown-jewel** | **Richest native log schema of any tool**: SpoolingDiskWritingDuration, DiskRead/Write byte/sec, EvaluationContext (artifact traceability), DataReadingAndSerializationDuration — all captured at the query level in flat CSV. The only tool providing spool disk write/read metrics. Zero infrastructure dependency (logs are local files). |
| **Notable limitation** | No centralization — logs are local per-node. PBIT reads directly from a local folder; no cloud pipeline. Paginated reports not logged. Premium-capacity-to-gateway queries sometimes missed. Network bandwidth/latency NOT captured. |
| **Artifact present** | Yes — downloadable Gateway Performance `.pbit` template (linked from docs); four log files auto-generated when feature is on (default: on). |
| **Freshness / maintained?** | Actively maintained MS feature; last doc update Dec 2025. Still labeled "public preview" in docs wording. |
| **Evidence note** | Full log schema with all column names and descriptions confirmed from Microsoft Learn page — [learn.microsoft.com](https://learn.microsoft.com/en-us/data-integration/gateway/service-gateway-performance). |

---

### 5. FUAM — Fabric Unified Admin Monitoring

| Field | Detail |
|---|---|
| **Name** | Fabric Unified Admin Monitoring (FUAM) |
| **URL** | [github.com/microsoft/fabric-toolbox/tree/main/monitoring/fabric-unified-admin-monitoring](https://github.com/microsoft/fabric-toolbox/tree/main/monitoring/fabric-unified-admin-monitoring) |
| **Type** | MS first-party community OSS (Fabric CAT) |
| **What it monitors (gateway-specific)** | Gateway inventory (cluster names, node count, version, admin list) via Admin APIs; gateway-to-datasource associations; refreshable items per gateway; gateway health status. **Not** query-level performance logs. |
| **Best attribute / crown-jewel** | **Broadest tenant-ops metadata coverage** of any tool: capacity, workspaces, refreshables, activities, inventory — with gateway inventory as one module. Modular architecture allows extension. Carlo Consulting provides commercial support for FUAM on Azure Marketplace — [marketplace.microsoft.com](https://marketplace.microsoft.com/en-us/product/saas/carloconsultingllc1611096483318.fabric_monitor_02?tab=overview). |
| **Notable limitation** | Gateway monitoring is inventory/admin metadata only — no query execution telemetry, no per-query duration, no CPU/memory counters from the gateway host. |
| **Artifact present** | Yes — PowerShell collectors, KQL, Power BI report. |
| **Freshness / maintained?** | Actively maintained; updated in fabric-toolbox (842 stars, 357 forks as of June 2026) — [github.com/microsoft/fabric-toolbox](https://github.com/microsoft/fabric-toolbox). |
| **Evidence note** | Fabric-toolbox README lists FUAM under Monitoring; Carlo Consulting Azure Marketplace listing explicitly names "Gateway Monitoring" as a key feature and references FUAM source. |

---

### 6. kerski/pbi-dataops-monitoring

| Field | Detail |
|---|---|
| **Name** | pbi-dataops-monitoring |
| **URL** | [github.com/kerski/pbi-dataops-monitoring](https://github.com/kerski/pbi-dataops-monitoring) |
| **Type** | Community OSS (John Kerski) |
| **What it monitors (gateway-specific)** | Refresh failures and schedules via Power BI REST APIs (gateway is the delivery channel for refreshes); custom connectors must be installed on a gateway for cloud refresh — gateway is a prerequisite but not a direct monitoring target. Monitors: failed dataset refreshes, unscheduled datasets (refresh disabled), long-running refreshes (>2h), failed dataflow refreshes, data quality error tables, Power Automate flow failures. |
| **Best attribute / crown-jewel** | **DataOps-oriented refresh quality monitoring** with Power Automate flow integration; 5 PBIT variants (Commercial, GCC, DOD, with/without Power Automate); custom Power Query connectors for Power BI REST API and Power Automate REST API. Latest release v1.4 (Feb 15, 2025). |
| **Notable limitation** | Does NOT monitor gateway performance logs, CPU/memory, query latency, or cluster health — it monitors refresh outcomes, not gateway internals. Gateway monitoring is only tangential (connector installation required on gateway). |
| **Artifact present** | Yes — 5 PBIT files + PowerShell scripts + custom Power Query connector docs. |
| **Freshness / maintained?** | Actively maintained; v1.4 released Feb 2025 — [github.com/kerski/pbi-dataops-monitoring](https://github.com/kerski/pbi-dataops-monitoring). |
| **Evidence note** | README explicitly lists monitored items; gateway mentioned only as prerequisite for running custom connectors in cloud refresh. |

---

### 7. SummitView — Power BI Observability Platform (Commercial SaaS)

| Field | Detail |
|---|---|
| **Name** | SummitView |
| **URL** | [summitview.app/features/gateway-monitoring](https://www.summitview.app/features/gateway-monitoring) |
| **Type** | Commercial SaaS |
| **What it monitors (gateway-specific)** | Tenant-wide gateway inventory (enterprise + personal); online/offline status monitoring with alerting; personal gateway flagging (governance risk indicators); version tracking with outdated-version warnings; data source count and type per gateway; dependency mapping (which datasets depend on which gateways); affected-dataset context in every alert ("blast radius") |
| **Best attribute / crown-jewel** | **Personal gateway discovery + dependency mapping**: uniquely identifies shadow personal gateways (e.g., user's laptop) across the tenant and maps which datasets break when that gateway goes offline — the only verified tool to do this. 5-minute setup, no agent required for core features. |
| **Notable limitation** | No query-execution performance data (latency, duration, errors per query); no CPU/memory metrics from gateway host; monitoring is REST-API-derived status/inventory, not log-file telemetry. |
| **Artifact present** | SaaS — no downloadable code. 14-day free trial. |
| **Freshness / maintained?** | Active commercial product; pricing page and feature pages confirmed live June 2026 — [summitview.app](https://www.summitview.app). |
| **Evidence note** | Feature page lists all capabilities verbatim with "See it in action" screenshots and "Works with every license tier" note — [summitview.app/features/gateway-monitoring](https://www.summitview.app/features/gateway-monitoring). |

---

### 8. Power BI Sentinel (Commercial SaaS)

| Field | Detail |
|---|---|
| **Name** | Power BI Sentinel |
| **URL** | [powerbisentinel.com](https://www.powerbisentinel.com) |
| **Type** | Commercial SaaS |
| **What it monitors (gateway-specific)** | Gateway inventory per datasource (all gateways connected to tenant, processed separately); dataset-to-gateway lineage (data source → dataset → report chain); dataset refresh history (rolling 90 days); gateway detail visible inside lineage diagrams (double-click data source to see gateway detail). "Enhanced processing of Power BI gateways" released Sep 2024: now processes each gateway separately, provides full picture of all data sources connected including unused ones. |
| **Best attribute / crown-jewel** | **Deepest data lineage of any tool**: gateway → data source → table → dataset → report chain, with table-level lineage for SQL Server, Azure SQL, MySQL, Oracle, Snowflake, etc. Automated daily PBIX backups + change tracking — uniquely combines governance with gateway inventory. |
| **Notable limitation** | Gateway monitoring is inventory/lineage-focused — no query latency, CPU/memory, or performance log analysis. Not a performance monitoring tool. SaaS-only (no on-prem deployment option). |
| **Artifact present** | SaaS; exports data to customer's own Azure SQL DB for custom Power BI reporting. |
| **Freshness / maintained?** | Actively maintained commercial product; changelog updated Nov 2025 with Discovery AI, new license tiers — [powerbisentinel.com/whats-new/](https://www.powerbisentinel.com/whats-new/). |
| **Evidence note** | What's New page (Sep 2024 entry) explicitly states "Enhanced processing of Power BI gateways... processing each gateway separately... full and up-to-date picture of all data sources" — [powerbisentinel.com/whats-new/](https://www.powerbisentinel.com/whats-new/). |

---

### 9. martinskeem/powerbi-powershell — Gateway Status to Windows Event Log

| Field | Detail |
|---|---|
| **Name** | powerbi-powershell (Get-GatewayStatus module + Windows Event Log reporter) |
| **URL** | [github.com/martinskeem/powerbi-powershell](https://github.com/martinskeem/powerbi-powershell) · [PowerShell Gallery sample](https://www.powershellgallery.com/packages/powerbi-powershell/1.20/Content/Samples%5CGet-GatewayStatus.ps1) |
| **Type** | Community OSS / PowerShell Gallery module |
| **What it monitors (gateway-specific)** | Gateway online/offline status via Power BI REST API; iterates all accessible gateway clusters; writes status events to Windows Application Event Log (`New-EventLog -Source "On-premises data gateway Check" -LogName "Application"`); scriptable for scheduled-task polling |
| **Best attribute / crown-jewel** | **Windows Event Log integration**: gateway status events become first-class Windows Application Log entries, enabling pickup by any Windows-native monitoring agent (SCOM, Nagios NRPE, Zabbix agent, Splunk UF, etc.) — the only confirmed approach that bridges Power BI gateway status into the standard Windows operational monitoring pipeline. |
| **Notable limitation** | Status-only (online/offline) — no query performance, no CPU/memory, no log-file parsing. Requires Azure AD app registration with gateway enumeration permissions. Script is a sample, not a production-hardened tool. |
| **Artifact present** | Yes — PowerShell module on PowerShell Gallery (`Install-Module -Name powerbi-powershell`); `Samples/Get-GatewayStatus.ps1` confirmed on PSGallery v1.20 — [powershellgallery.com](https://www.powershellgallery.com/packages/powerbi-powershell/1.20/Content/Samples%5CGet-GatewayStatus.ps1). |
| **Freshness / maintained?** | 36 commits; last significant activity 2021–2022. Functional but not actively developed. Module installable from PSGallery. |
| **Evidence note** | GitHub README section "Report gateway status to event log" and PSGallery sample script confirmed by direct fetch — [github.com/martinskeem/powerbi-powershell](https://github.com/martinskeem/powerbi-powershell). |

---

### 10. SQLvariant — DataGateway PowerShell Governance Gists

| Field | Detail |
|---|---|
| **Name** | SQLvariant / GatewayClusters.ps1 + Export_PowerPlatform_Gateway_Assets.ipynb |
| **URL** | [gist.github.com/SQLvariant/fd3b77e597fc6e13118636bf0d682383](https://gist.github.com/SQLvariant/fd3b77e597fc6e13118636bf0d682383) (GatewayClusters.ps1, last active Dec 9, 2024) · [gist.github.com/SQLvariant/32432b6dc6e72ecc88ac58360733733f](https://gist.github.com/SQLvariant/32432b6dc6e72ecc88ac58360733733f) (Jupyter notebook) · [sqlservercentral.com](https://www.sqlservercentral.com/blogs/get-the-governance-data-you-need-out-of-your-power-bi-gateways-with-powershell) |
| **Type** | Community OSS (Aaron Nelson / SQLvariant, Microsoft MVP) |
| **What it monitors (gateway-specific)** | Tenant-wide gateway cluster enumeration (`Get-DataGatewayCluster -Scope Organization`); all gateway nodes across all clusters; member gateway properties; optional export to Excel via `ImportExcel` module; PowerPlatform gateway asset inventory notebook. Uses the official `DataGateway` PowerShell module (23 cmdlets). |
| **Best attribute / crown-jewel** | **Minimal-friction governance baseline**: two PowerShell modules (`DataGateway` + `ImportExcel`) yield a complete tenant-wide gateway cluster/node inventory in a few lines. Gist is a clean, copy-paste starting point for any custom gateway health-check pipeline. Last updated Dec 2024 — still current. |
| **Notable limitation** | Inventory and status only — no performance telemetry, no log parsing. Not a monitoring solution by itself; a building block. |
| **Artifact present** | Yes — `GatewayClusters.ps1` gist (downloadable ZIP) and Jupyter notebook gist. |
| **Freshness / maintained?** | Gist last active Dec 9, 2024 — confirmed current against DataGateway PS module — [gist.github.com/SQLvariant](https://gist.github.com/SQLvariant/fd3b77e597fc6e13118636bf0d682383). |
| **Evidence note** | Gist content confirms `Get-DataGatewayCluster -Scope Organization`, `Install-Module DataGateway`, `Install-Module ImportExcel`; SQLServerCentral blog describes the 23-cmdlet DataGateway module. |

---

## Ranked Top 10 — By Gateway-Monitoring Value + Evidence Quality

| Rank | Tool | Type | Gateway-Specific Signals | Crown-Jewel Feature | Evidence Quality |
|---|---|---|---|---|---|
| **1** | [FPM — Fabric Platform Monitoring Gateway Module](https://jumpstart.fabric.microsoft.com/catalog/fpm-gateway-monitoring/) | MS first-party OSS | Heartbeat, query execution (full schema), running jobs, system counters — streamed | RTI pipeline: Eventstream → Eventhouse → 6-page DirectQuery report; ~2min Jumpstart deploy | ★★★★★ Jumpstart docs + GitHub |
| **2** | [Microsoft Gateway Performance Monitoring + PBIT](https://learn.microsoft.com/en-us/data-integration/gateway/service-gateway-performance) | MS first-party feature | Richest query-level schema: spool disk R/W, DataReading duration, EvaluationContext, 5-min aggregations, system counters | Only tool capturing SpoolingDiskWritingDuration, DiskRead/Write byte/sec per query; zero infrastructure | ★★★★★ Full schema in MS Learn docs |
| **3** | [RuiRomano/pbigtwmonitor](https://github.com/RuiRomano/pbigtwmonitor) (archived) | Community OSS | Query logs, system counters, error/info logs, mashup logs; multi-cluster ADLS centralization | FromDisk PBIT: fastest ad-hoc log analysis with zero infrastructure; still the reference design | ★★★★★ GitHub archive, 101 stars |
| **4** | [SummitView](https://www.summitview.app/features/gateway-monitoring) | Commercial SaaS | Online/offline status, personal gateway discovery, version tracking, dataset dependency map | Personal gateway shadow IT discovery + blast-radius alerting; only verified commercial tool with this feature | ★★★★ Feature page with details |
| **5** | [ecotte/Fabric-Monitoring-RTI](https://github.com/ecotte/Fabric-Monitoring-RTI) | Community OSS | Gateway event streams, audit logs, platform inventory via RTI | Designed the Eventstream → Eventhouse pattern now used in FPM | ★★★★ GitHub + Chris Webb blog |
| **6** | [FUAM — Fabric Unified Admin Monitoring](https://github.com/microsoft/fabric-toolbox/tree/main/monitoring/fabric-unified-admin-monitoring) | MS first-party OSS | Gateway inventory, datasource associations, refreshables, admin metadata | Broadest tenant-ops coverage; modular; commercial support via Carlo Consulting | ★★★★ fabric-toolbox GitHub |
| **7** | [Power BI Sentinel](https://www.powerbisentinel.com) | Commercial SaaS | Gateway inventory, datasource lineage, refresh history, gateway-in-lineage diagram | Deepest data lineage (gateway → table → dataset → report); governance + DR combined | ★★★★ What's New changelog |
| **8** | [kerski/pbi-dataops-monitoring](https://github.com/kerski/pbi-dataops-monitoring) | Community OSS | Refresh outcomes (via gateway as transport); 5 PBIT variants | DataOps refresh quality + Power Automate flow health; covers GCC/DOD tenants | ★★★★ GitHub + v1.4 release |
| **9** | [martinskeem/powerbi-powershell](https://github.com/martinskeem/powerbi-powershell) | Community OSS | Gateway online/offline status → Windows Event Log | Windows Event Log integration bridges gateway status into any Windows-native monitoring tool (SCOM, Zabbix, Nagios, Splunk) | ★★★ GitHub + PSGallery |
| **10** | [SQLvariant / GatewayClusters.ps1 gists](https://gist.github.com/SQLvariant/fd3b77e597fc6e13118636bf0d682383) | Community OSS | Tenant-wide cluster/node inventory + node properties | Minimal-friction governance baseline using official DataGateway PS module; copy-paste starting point | ★★★ Gist confirmed Dec 2024 |

---

## Best-of-Breed Feature Shopping List

For a new open-source tool that combines the best implementations of each important capability:

| Capability | Best Implementation | Source Tool | Why Best |
|---|---|---|---|
| **Real-time telemetry pipeline** | PowerShell uploader → Eventstream → Eventhouse (KQL) | [FPM](https://jumpstart.fabric.microsoft.com/catalog/fpm-gateway-monitoring/) | Only tool with a live streaming architecture; Jumpstart deploys in ~2 min; heartbeat + report events on separate streams allow independent retention/scaling |
| **Query-level log schema** | 4-file CSV log with SpoolingDiskWritingDuration, DiskRead/Write byte/sec, DataReadingAndSerializationDuration, EvaluationContext (artifactId), base64 QueryText | [MS Gateway Performance Monitoring](https://learn.microsoft.com/en-us/data-integration/gateway/service-gateway-performance) | Official schema is the richest available at query level; includes spool disk metrics no other tool exposes |
| **Ad-hoc / zero-infra log analysis** | `Gateway Monitor - FromDisk.pbit` (point at local log folder) | [pbigtwmonitor](https://github.com/RuiRomano/pbigtwmonitor) | No Azure dependencies; open the PBIT, set path, analyze immediately — invaluable for incident triage without a deployed pipeline |
| **Multi-cluster log centralization** | PowerShell incremental upload → ADLS Gen2 partitioned by date | [pbigtwmonitor](https://github.com/RuiRomano/pbigtwmonitor) | Well-tested pattern (101 stars, 35 forks); incremental delta avoids re-uploading; compatible with Spark/Synapse for ad-hoc KQL-free exploration |
| **Personal gateway shadow IT detection** | REST-API-driven tenant scan flagging personal (laptop-installed) gateways with governance risk badges | [SummitView](https://www.summitview.app/features/gateway-monitoring) | Only verified implementation with dependency mapping showing affected datasets; feature explicitly described and demonstrated on product page |
| **Dataset dependency / blast-radius mapping** | Dependency map: gateway → datasource → dataset → report; affected-dataset list in every alert | [SummitView](https://www.summitview.app/features/gateway-monitoring) | Blast-radius context transforms an alert from "gateway offline" to "these 12 datasets/reports will break" |
| **Deep data lineage (gateway → column level)** | Gateway detail in lineage diagram; table-level lineage from SQL/Snowflake sources; 90-day refresh history | [Power BI Sentinel](https://www.powerbisentinel.com) | Deepest lineage of any tool; exports to customer's own Azure SQL DB for custom Power BI reports |
| **Windows Event Log / native monitoring integration** | `Get-GatewayStatus.ps1` → `New-EventLog` / `Write-EventLog` to Application log | [martinskeem/powerbi-powershell](https://github.com/martinskeem/powerbi-powershell) | Enables pickup by SCOM, Zabbix, Nagios NRPE, Splunk UF without any custom HTTP/API connector — reuses existing enterprise monitoring infrastructure |
| **Tenant-wide cluster/node inventory (governance)** | `Get-DataGatewayCluster -Scope Organization` → Excel/CSV; DataGateway PS module (23 cmdlets) | [SQLvariant gists](https://gist.github.com/SQLvariant/fd3b77e597fc6e13118636bf0d682383) | Minimal code, official Microsoft module, updated Dec 2024; best bootstrapping script for a new monitoring tool's inventory collector |
| **Refresh quality + DataOps health** | PBIT with REST-API custom connectors monitoring failed/unscheduled/long-running refreshes + Power Automate flows | [kerski/pbi-dataops-monitoring](https://github.com/kerski/pbi-dataops-monitoring) | Only tool combining dataset refresh quality with Power Automate flow health; GCC/DOD variants included |
| **KQL-based operational analytics** | Eventhouse-backed KQL database with `GatewaysHeartbeat` + `GatewayReports-Raw` tables; DirectQuery semantic model | [FPM](https://jumpstart.fabric.microsoft.com/catalog/fpm-gateway-monitoring/) | KQL time-series queries (summarize, bin, project) are far more efficient than Power Query for operational log analysis at scale |

---

## Coverage Note: Build-It-Yourself Opportunities

The following important monitoring signals are **not well-covered by any tool in this inventory** and represent clear gaps for a new open-source tool to fill:

| Signal / Capability | Current Coverage | Gap Description |
|---|---|---|
| **Network bandwidth / latency** | ❌ No tool covers this | Microsoft's own docs explicitly state: "Gateway diagnostics doesn't capture diagnostics directly related to the (virtual) machine and its network, like bandwidth or latency" — [learn.microsoft.com](https://learn.microsoft.com/en-us/data-integration/gateway/service-gateway-performance). No tool uses `Test-NetConnection` or network performance counters (`\Network Interface(*)\Bytes Total/sec`) to measure gateway↔cloud relay bandwidth. **Build opportunity**: PerfMon collector for `\Network Interface(*)\Bytes Total/sec` and `\Network Interface(*)\Current Bandwidth` correlated with query duration spikes. |
| **Windows Event Log correlation** | ⚠️ Partial (martinskeem writes status; no reader) | The gateway writes errors and warnings to the Windows Application/System Event Log, but no tool reads/parses these Event Log entries and correlates them with query-level performance data. **Build opportunity**: `Get-WinEvent` collector that extracts gateway-related Event IDs and joins them with the CSV query execution report on timestamp. |
| **Cluster load skew / node affinity** | ⚠️ Minimal | FPM and pbigtwmonitor capture per-node system counters, but no tool computes a "load skew score" across cluster nodes (e.g., node A handling 80% of queries vs. node B at 20%). No cluster-level load-balancing effectiveness metric is exposed. **Build opportunity**: per-node query count + average duration → coefficient of variation → skew alert. |
| **Query-to-identity / EffectiveUser correlation** | ❌ No tool covers this | The `QueryStart` log captures `QueryTrackingId` and `EvaluationContext` (artifactId), but does not capture the `EffectiveUserName` used for row-level security impersonation. No tool correlates slow queries to specific user identities or RLS personas. **Build opportunity**: join gateway `QueryStart` logs with Power BI audit logs (Activity Events) on `RequestId` / dataset ID to add user identity context. |
| **Spool disk utilization / capacity** | ⚠️ Schema captured, not monitored | The MS PBIT template shows `SpoolingDiskWritingDuration` and `SpoolingTotalDataSize`, but no tool monitors the spool directory (`C:\...\Spooler`) for disk space consumption or alerts when spool storage approaches capacity (a common cause of gateway crashes). **Build opportunity**: scheduled `Get-Item` / `Get-PSDrive` check on the spool path with threshold alerting. |
| **Gateway version currency across cluster** | ⚠️ SummitView (commercial only) | SummitView tracks outdated gateway versions commercially. No open-source tool compares installed gateway version to the latest available version from the Microsoft update feed and alerts on nodes running old versions. **Build opportunity**: compare `Get-DataGatewayCluster` node version to the JSON-published latest version from `https://go.microsoft.com/fwlink/?linkid=868594`. |
| **Mashup engine / M query performance** | ⚠️ pbigtwmonitor only (deprecated) | Only pbigtwmonitor exposed the mashup engine log (query folding failures, M evaluation steps). FPM does not currently surface mashup-level diagnostics. **Build opportunity**: parse the `GatewayInfo` log for mashup evaluation lines and extract folding/no-folding ratios per datasource. |
| **Cross-gateway refresh history correlation** | ⚠️ Partial (kerski monitors refresh outcomes) | No tool correlates refresh failures or duration spikes across multiple gateway clusters simultaneously, with gateway CPU/memory as a confounding variable. kerski monitors outcomes but not gateway-side cause. **Build opportunity**: join REST API refresh history (`GET /datasets/{id}/refreshes`) with gateway `QueryExecutionAggregationReport` on time window + datasource. |

---

## Exclusions and Out-of-Scope Notes

- **minceddata.info / Tom Martens (Oct 2023)** — Blog solution using a Fabric notebook + REST API `/gateways` endpoint to write gateway availability to a lakehouse CSV table. Interesting as an early Fabric-native pattern but the artifact is a OneDrive `.ipynb` link (not a GitHub repo) and covers availability/status only — not included in the main ranked list but confirmed as a real, working approach using [Power BI Gateways REST API](https://learn.microsoft.com/en-us/rest/api/power-bi/gateways/get-gateways) — [minceddata.info](https://www.minceddata.info/2023/10/16/power-bi-gateway-monitoring-and-more-using-microsoft-fabric/).

- **TALASSX/How-to-Capture-Power-BI-On-Premises-Gateway-Status** — GitHub repo (PowerShell + Power Automate Desktop → Excel on OneDrive). Minimal (3 commits, 0 stars); status-only; not ranked due to low evidence quality and limited scope — [github.com/TALASSX](https://github.com/TALASSX/How-to-Capture-Power-BI-On-Premises-Gateway-Status-Using-PowerShell-and-Power-Automate.).

- **Carlo Consulting LLC BI Monitoring Accelerator** — Azure Marketplace SaaS listing explicitly mentions "Gateway Monitoring" as a feature (health + performance of data gateways) alongside FUAM support. Treated as a commercial support wrapper around FUAM rather than a standalone tool; not independently ranked but noted as a verified commercial offering — [marketplace.microsoft.com](https://marketplace.microsoft.com/en-us/product/saas/carloconsultingllc1611096483318.fabric_monitor_02?tab=overview).

- **devladlog.com Gateway Health Check Script (2025)** — Practitioner blog with inline PowerShell function `Get-GatewayPerformance` using `Get-Counter` for `\Process(PBIEgwService)\Private Bytes`, `% Processor Time`, `Thread Count`. Content appears to be AI-generated reference material rather than a maintained open-source artifact; no GitHub repo. Not ranked but the PerfMon counter list is useful — [devladlog.com](https://www.devladlog.com/articles/power-bi/2025-11-17-on-premises-data-gateway-configuration-management/).

- **Power BI Sentinel (gateway scope)** — Included in rankings at #7. Its gateway monitoring is inventory/lineage-focused, NOT performance monitoring. Ranking reflects its value for the governance + lineage use case, not query performance.

---

*Report generated June 30, 2026. All URLs verified by direct page fetch. Features marked [Unverified] where a source claims a feature but no code/schema evidence could be independently confirmed.*
