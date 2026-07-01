# Gateway Monitor — Fabric-Native On-Premises Data Gateway Observability

> **DISCLAIMER — READ FIRST**  
> All code in this repository is **reference implementation / [STUB]** status.  
> It has **never been executed in a live Windows / Microsoft Fabric environment**.  
> Every component requires a Phase 5 live-environment pilot before being treated as functional.  
> Do not deploy to production without validating each file against your actual gateway host and Fabric capacity.  
> See `KNOWN_UNKNOWNS` section below.

---

## What This Is

An open-source Power BI / Microsoft Fabric On-Premises Data Gateway (OPDG) monitoring tool that any Fabric tenant admin can deploy by forking this repository.

**It combines the best patterns from 10 existing tools and fills the 5 capability gaps where all of them fail simultaneously.**

| | |
|---|---|
| **Replaces** | Manual PBIT template (breaks on upgrade), FPM (KQL-locked, no alerting), ad-hoc PerfMon |
| **Requires** | Fabric F8+ capacity, Service Principal, Azure Key Vault, gateway admin role |
| **Architecture** | PowerShell collectors → Bronze/Silver/Gold Delta in OneLake → DirectLake semantic model → Power BI report + Fabric Activator alerting |
| **Not** | A fork of FPM; VNet gateway support (v2); per-process mashup attribution (v3) |

---

## Feature → Pain → Module Map

| Feature | Pain Point Solved | Module | Differentiator | v-Target |
|---|---|---|---|---|
| Proactive gateway-offline alerting | #1 No real-time alerting | `collectors/Collect-GatewayLogs.ps1` + `alerting/activator-rules.md` | **#1** | v1 |
| Unified failure triage (refresh + gateway log + EventLog) | #2 Opaque refresh failures | `collectors/Collect-EventLog.ps1` + `notebooks/02_silver_correlate.py` | **#2** | v2 |
| Best-effort query → dataset attribution | #3 Zero query attribution | `notebooks/02_silver_correlate.py` (labeled FUZZY) | **#3** | v3 |
| Schema-adaptive log parser | #4 PBIT breaks on upgrade | `notebooks/01_bronze_ingest.py` | **#5** | v1 |
| NIC counter + latency metrics | #7 Network blind spot | `collectors/Collect-NetworkMetrics.ps1` + `notebooks/02_silver_correlate.py` | **#4** | v2 |
| Fleet / multi-gateway view | #6 No fleet view | `collectors/Get-GatewayInventory.ps1` + `notebooks/03_gold_aggregate.py` | fold-in | v1 |
| Disk spool free-space alerting | #9 Disk spooler surprises | `collectors/Collect-DiskSpool.ps1` + `alerting/activator-rules.md` | fold-in | v1 |
| Credential drift detection | #10 Silent credential breakage | `collectors/Get-GatewayInventory.ps1` (datasource status) + `alerting/activator-rules.md` | fold-in | v2 |
| Windows Event Log reader | #2, service stability | `collectors/Collect-EventLog.ps1` | fold-in | v1 |
| Cluster inventory | #6 | `collectors/Get-GatewayInventory.ps1` | fold-in | v1 |

---

## Repository Structure

```
kit/starter/
├── README.md                          ← this file
├── collectors/
│   ├── Collect-GatewayLogs.ps1        [ADAPTED-FROM-FPM]   S1, S3
│   ├── Collect-NetworkMetrics.ps1     [NET-NEW]             S11
│   ├── Collect-EventLog.ps1           [NET-NEW/ADAPTED]     S9
│   ├── Collect-DiskSpool.ps1          [NET-NEW]             S11b
│   └── Get-GatewayInventory.ps1       [ADAPTED-FROM-SQLvariant] S2, S5, S6
├── notebooks/
│   ├── 01_bronze_ingest.py            [NET-NEW + ADAPTED-FROM-FPM]  Differentiator #5
│   ├── 02_silver_correlate.py         [NET-NEW]             Differentiators #2, #3, #4
│   └── 03_gold_aggregate.py           [NET-NEW]             Fleet rollup, load skew
├── semantic-model/
│   └── measures.dax                   [NET-NEW]             Key DAX measures
├── alerting/
│   └── activator-rules.md             [NET-NEW]             Fabric Activator rules
└── config/
    ├── config.sample.json             [NET-NEW]             Configuration template
    └── credentials.md                 [NET-NEW]             SP + Key Vault guide
```

---

## Deploy Order

1. **Read `config/credentials.md`** — provision SP, Key Vault secret
2. **Copy `config/config.sample.json` → `config/config.json`** — fill in your workspace, lakehouse, and log paths
3. **Deploy collectors** to each gateway host:
   - Copy `collectors/` folder to a directory on the host (e.g., `C:\GatewayMonitor\`)
   - Create Windows Scheduled Tasks calling each `.ps1` (see each script header for recommended cadence)
4. **Create Fabric Lakehouse** with `bronze/`, `silver/`, `gold/` containers in OneLake
5. **Upload notebooks** to the Fabric workspace; configure `LAKEHOUSE_PATH` variable in each
6. **Create Fabric Data Pipelines** to schedule notebooks (5-min Bronze/Gold health; 15-min Silver/Gold full)
7. **Publish semantic model** with `semantic-model/measures.dax` DAX measures added
8. **Create Fabric Activator** item connected to `gold_gateway_health` Delta table; configure rules from `alerting/activator-rules.md`

---

## Signal Map Summary

| Signal | Source | Collector | Table |
|---|---|---|---|
| S1: Gateway perf logs (QueryExecution, QueryStart, Aggregation, SystemCounter) | Local CSV files | `Collect-GatewayLogs.ps1` | `bronze_query_execution`, `bronze_query_start`, `bronze_system_counter` |
| S2: Gateway config + datasource list | REST API | `Get-GatewayInventory.ps1` | `bronze_gateway_inventory` |
| S3: PerfMon counters (CPU/mem/mashup) | `Get-Counter` | `Collect-GatewayLogs.ps1` | `bronze_system_counter` |
| S5: DataGateway PS module (cluster/node inventory) | PowerShell | `Get-GatewayInventory.ps1` | `bronze_gateway_inventory` |
| S6: Power BI REST APIs (status, datasource health, heartbeat) | REST | `Get-GatewayInventory.ps1` | `bronze_gateway_inventory` |
| S7: Refresh history | REST Admin API | `Collect-GatewayLogs.ps1` (extension) | `bronze_refresh_history` |
| S9: Windows Event Log (service crash/restart) | `Get-WinEvent` | `Collect-EventLog.ps1` | `bronze_event_log` |
| S11: NIC counters + latency | `Get-Counter` + `Test-Connection` | `Collect-NetworkMetrics.ps1` | `bronze_network_metrics` |
| S11b: Spool disk free space | `Get-PSDrive` | `Collect-DiskSpool.ps1` | `bronze_disk_spool` |
| S12: EvaluationContext (artifactId) | QueryStart log parse | `01_bronze_ingest.py` | `bronze_query_start` |

---

## Known Unknowns (Phase 5 Validation Required)

| Item | Description | Risk |
|---|---|---|
| `Get-DataGatewayInfo` SP 401 | Known bug: SP returns 401 on gateway admin APIs in some tenants ([Fabric community](https://community.fabric.microsoft.com/t5/Real-Time-Intelligence/Fabric-Platform-Monitoring-accelerator-Gateways-module/m-p/4884544)) | Inventory collector fails; workaround unverified |
| Paginated Reports not logged | Gateway query log does not capture Paginated Report queries ([MS docs](https://learn.microsoft.com/en-us/data-integration/gateway/service-gateway-performance)) | Query saturation metrics incomplete for paginated workloads |
| Premium capacity queries sometimes missed | Undocumented miss rate per MS docs | Query counts may undercount in Premium capacity contexts |
| RequestId attribution fuzziness | DirectQuery sessions lack stable RequestId in audit log | Triage defaults to FUZZY_TIME_WINDOW for most records |
| StreamBeforeRequestCompletes setting | `true` causes SpoolingTotalDataSize = 0 | Spool metrics require default `false` setting |
| EvaluationContext scope | Only populated for Fabric workloads (not Dataflow Gen1, Paginated Reports) | Identity attribution returns UNATTRIBUTED for those workload types |
| Activator rule syntax | Fabric Activator DSL is evolving | `activator-rules.md` rules require live-env validation |
| DirectLake time-intelligence DAX | May differ from import mode | All DAX measures require Phase 5 testing |

---

## Source Credits

This repository adapts patterns from the following tools — full citations in each file header:

- **FPM (Fabric Platform Monitoring)** — [github.com/microsoft/fabric-toolbox](https://github.com/microsoft/fabric-toolbox/tree/main/monitoring/fabric-platform-monitoring) — PowerShell uploader and incremental log upload patterns [ADAPTED-FROM-FPM]
- **RuiRomano/pbigtwmonitor** — [github.com/RuiRomano/pbigtwmonitor](https://github.com/RuiRomano/pbigtwmonitor) — incremental delta upload pattern [ADAPTED-FROM-FPM]
- **SQLvariant/GatewayClusters.ps1** — [gist.github.com/SQLvariant/fd3b77e597fc6e13118636bf0d682383](https://gist.github.com/SQLvariant/fd3b77e597fc6e13118636bf0d682383) — `Get-DataGatewayCluster -Scope Organization` pattern [ADAPTED-FROM-SQLvariant]
- **martinskeem/powerbi-powershell** — [github.com/martinskeem/powerbi-powershell](https://github.com/martinskeem/powerbi-powershell) — Windows Event Log integration pattern [ADAPTED]
- **Microsoft Gateway Performance Monitoring** — [learn.microsoft.com](https://learn.microsoft.com/en-us/data-integration/gateway/service-gateway-performance) — authoritative log schema column names used throughout

---

*Architecture documented in `kit/research/phase4_architecture.md`. Pain point evidence in `kit/research/phase3_painpoints.md`. All code is [STUB] / reference implementation pending Phase 5 pilot.*

## Post-build hardening (applied)

Two correctness fixes were applied to `notebooks/01_bronze_ingest.py` after the initial build and unit-tested (pure-Python logic):

1. **Quote-safe CSV parsing** — replaced naive `line.split(",")` with an RFC-4180 `csv.reader`. `ErrorMessage` (central to triage, Differentiator #2) frequently contains commas and quotes; the naive split silently corrupted those rows. Now handles embedded commas, escaped quotes (`""`), and newlines. Falls back gracefully; never raises.
2. **Robust EvaluationContext handling** — auto-detects direct-JSON vs base64-encoded JSON per row before extracting `artifactId` (Differentiator #3). The prior direct `get_json_object` call would have returned null for every row if the gateway emits base64, silently disabling attribution.

Both remain `[Unverified]` against a live gateway until Phase 5 (`research/phase5_validation.md`, items U1–U3) — but the logic is unit-tested against representative inputs.
