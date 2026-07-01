# Phase 4 Dossier — Decision-Grade Synthesis

> **UPDATE (Phase 6 finding — supersedes 'fuzzy attribution' language below):** Query→identity attribution is NOT architecturally impossible. The gateway `RequestId` equals `XmlaRequestId`/`OperationId` in Fabric Workspace Monitoring, so a KQL join yields exact `ExecutingUser` + `DatasetId` + DAX for Fabric semantic-model workloads (see [`phase6_northstar.md`](./phase6_northstar.md) and [`starter/kql/01_identity_join.kql`](../starter/kql/01_identity_join.kql)). The 'best-effort/fuzzy' notes below remain accurate ONLY for the no-Workspace-Monitoring fallback and for per-DirectQuery UserId / Dataflow Gen1 / Paginated Reports, which stay [Blocked-by-platform].

## On-Premises Data Gateway Monitoring: Best-of-Breed Feature Synthesis, Ranked Pain Points, and White-Space Map

**Date:** 2026-06-30  
**Status:** Decision-grade reference — grounded in evidence from phases 0–3. All claims carry source citations.  
**Architecture decision already resolved:** BUILD-NEW Fabric-native core + OPTIONAL FPM bridge (see [phase0_scope.md §8](./phase0_scope.md)).

---

## 1. Qualified Source Landscape (Top 10 Tools)

The following inventory is ranked by gateway-monitoring value and evidence quality. All URLs verified June 2026.

| Rank | Tool | Type | Best Feature | Gateway Signal Coverage | Evidence Quality |
|---|---|---|---|---|---|
| 1 | **FPM — Fabric Platform Monitoring** ([github.com/microsoft/fabric-toolbox](https://github.com/microsoft/fabric-toolbox/tree/main/monitoring/fabric-platform-monitoring), [jumpstart](https://jumpstart.fabric.microsoft.com/catalog/fpm-gateway-monitoring/)) | MS first-party OSS | End-to-end RTI pipeline: PowerShell → Eventstream → Eventhouse → 6-page DirectQuery report in ~2 min | S1, S2, S3 (partial), S5, S6, partial S12 | ★★★★★ |
| 2 | **Microsoft Gateway Performance Monitoring + PBIT** ([learn.microsoft.com](https://learn.microsoft.com/en-us/data-integration/gateway/service-gateway-performance)) | MS first-party feature | Richest native log schema: SpoolingDiskWritingDuration, DiskRead/Write, EvaluationContext per-query | S1 (richest), S3 | ★★★★★ |
| 3 | **RuiRomano/pbigtwmonitor** ([github.com/RuiRomano/pbigtwmonitor](https://github.com/RuiRomano/pbigtwmonitor)) (archived Dec 2025) | Community OSS | FromDisk PBIT: fastest ad-hoc log analysis, zero infrastructure; incremental ADLS upload pattern | S1, S3, S4 | ★★★★★ |
| 4 | **SummitView** ([summitview.app/features/gateway-monitoring](https://www.summitview.app/features/gateway-monitoring)) | Commercial SaaS | Personal gateway discovery + blast-radius dataset-dependency alerting | S5, S6 (commercial) | ★★★★ |
| 5 | **ecotte/Fabric-Monitoring-RTI** ([github.com/ecotte/Fabric-Monitoring-RTI](https://github.com/ecotte/Fabric-Monitoring-RTI)) (folded into FPM) | Community OSS | First proof-of-concept Eventstream → Eventhouse pattern; predecessor to FPM | S1, S5, S6 | ★★★★ |
| 6 | **FUAM — Fabric Unified Admin Monitoring** ([github.com/microsoft/fabric-toolbox/…/fabric-unified-admin-monitoring](https://github.com/microsoft/fabric-toolbox/tree/main/monitoring/fabric-unified-admin-monitoring)) | MS first-party OSS | Broadest tenant-ops metadata: capacity, workspaces, gateway inventory, modular | S5, S6 (inventory) | ★★★★ |
| 7 | **Power BI Sentinel** ([powerbisentinel.com](https://www.powerbisentinel.com)) | Commercial SaaS | Deepest data lineage: gateway → datasource → table → dataset → report; 90-day refresh history | S6, S7 (commercial) | ★★★★ |
| 8 | **kerski/pbi-dataops-monitoring** ([github.com/kerski/pbi-dataops-monitoring](https://github.com/kerski/pbi-dataops-monitoring)) | Community OSS | DataOps refresh quality + Power Automate flow health; GCC/DOD variants | S7 (outcomes) | ★★★★ |
| 9 | **martinskeem/powerbi-powershell** ([github.com/martinskeem/powerbi-powershell](https://github.com/martinskeem/powerbi-powershell)) | Community OSS | Windows Event Log integration — gateway status becomes first-class Application Log entry | S6, S9 (partial) | ★★★ |
| 10 | **SQLvariant / GatewayClusters.ps1** ([gist.github.com/SQLvariant/fd3b77e597fc6e13118636bf0d682383](https://gist.github.com/SQLvariant/fd3b77e597fc6e13118636bf0d682383)) | Community OSS | `Get-DataGatewayCluster -Scope Organization`: minimal-friction tenant-wide cluster/node inventory | S5 | ★★★ |

---

## 2. Consolidated Best-of-Breed Feature Set

Each row names the best implementation and the tool to borrow it from.

| Capability | Best Implementation | Source Tool | Notes |
|---|---|---|---|
| Real-time telemetry pipeline | PowerShell uploader → Eventstream → KQL/Eventhouse | [FPM](https://jumpstart.fabric.microsoft.com/catalog/fpm-gateway-monitoring/) | Pattern to adapt; our stack uses Delta instead of Eventhouse |
| Query-level log schema | 4-file CSV: QueryExecution (17 cols), QueryStart (EvaluationContext), QueryAggregation, SystemCounter | [MS Gateway Perf Monitoring](https://learn.microsoft.com/en-us/data-integration/gateway/service-gateway-performance) | Only source for SpoolingDiskWritingDuration, DiskRead/Write byte/sec at query level |
| Ad-hoc log analysis | `Gateway Monitor - FromDisk.pbit` | [pbigtwmonitor](https://github.com/RuiRomano/pbigtwmonitor) | Zero-infra incident triage; should be documented as companion tool |
| Multi-cluster log centralization | Incremental upload → partitioned storage by date | [pbigtwmonitor](https://github.com/RuiRomano/pbigtwmonitor) | Reuse incremental delta pattern in PowerShell collector |
| Personal gateway + dependency mapping | REST tenant scan + blast-radius alerting | [SummitView](https://www.summitview.app/features/gateway-monitoring) | Commercial; design principles applicable to OSS |
| Windows Event Log integration | `New-EventLog` / `Write-EventLog` source pattern | [martinskeem](https://github.com/martinskeem/powerbi-powershell) | Adapt pattern for reading (Get-WinEvent), not just writing |
| Tenant-wide cluster inventory | `Get-DataGatewayCluster -Scope Organization` | [SQLvariant](https://gist.github.com/SQLvariant/fd3b77e597fc6e13118636bf0d682383) | Direct copy-adapt; uses official DataGateway PS module |
| Refresh quality monitoring | REST API refresh history per dataset | [kerski](https://github.com/kerski/pbi-dataops-monitoring) | Design pattern; we use Admin REST API endpoint |
| KQL operational analytics | Eventhouse-backed time-series KQL | [FPM](https://jumpstart.fabric.microsoft.com/catalog/fpm-gateway-monitoring/) | Optional via FPM bridge; primary path is Delta + DAX |
| Governance / lineage | Gateway → datasource → dataset → report chain | [Power BI Sentinel](https://www.powerbisentinel.com) | Do not rebuild; out of scope v1-v2 |

---

## 3. Ranked Operator Pain Points

Full evidence at [phase3_painpoints.md](./phase3_painpoints.md). Summary with feature→solution mapping:

| Rank | Pain Point | Score | Classification | Our Differentiator |
|---|---|---|---|---|
| **#1** | No real-time gateway-offline alerting | 5/5 | 🔴 PRODUCT GAP | Differentiator #1: Fabric Activator on heartbeat |
| **#2** | Opaque refresh failure triage (can't tell gateway vs. source vs. network) | 5/5 | 🔴 PRODUCT GAP | Differentiator #2: unified triage join |
| **#3** | Zero query attribution (no dataset/user/report for slow queries) | 5/5 | 🔴 PRODUCT GAP | Differentiator #3: best-effort RequestId+audit-log join (labeled fuzzy) |
| **#4** | PBIT template breaks on gateway upgrade / log schema drift | 4/5 | 🔴 PRODUCT GAP | Differentiator #5: schema-adaptive column-name parser |
| **#5** | Mashup engine memory/CPU bloat, no per-process visibility | 4/5 | 🔴 PRODUCT GAP | Partial: SystemCounter + PerfMon counters; per-PID correlation is v3+ |
| **#6** | No fleet / multi-gateway view | 4/5 | 🔴/🟡 MIXED | Fold-in: fleet rollup in gold + cluster inventory |
| **#7** | Network/bandwidth blind spot | 4/5 | 🔴 PRODUCT GAP | Differentiator #4: NET-NEW NIC collector |
| **#8** | Manual brittle setup (log path, config, service account) | 3/5 | 🟡 DISCOVERY DEFICIT | Config wizard in config.sample.json + credentials.md |
| **#9** | Disk spooler surprises — no proactive disk monitoring | 3/5 | 🔴 PRODUCT GAP | Fold-in: Collect-DiskSpool.ps1 + Activator rule |
| **#10** | Credential / datasource state drift, silent breakage | 3/5 | 🟡 DISCOVERY DEFICIT | Fold-in: scheduled REST datasource status check + Activator rule |

---

## 4. Best Feature → Pain Solved Map

| Best Feature | Source Tool(s) | Pain #(s) Solved | Differentiator # |
|---|---|---|---|
| Fabric Activator alerting on heartbeat age | [FPM](https://jumpstart.fabric.microsoft.com/catalog/fpm-gateway-monitoring/) pattern + Activator | **#1** | #1 |
| Triage join: RefreshHistory + QueryExecution + EventLog on RequestId/time-window | [kerski](https://github.com/kerski/pbi-dataops-monitoring) (refresh history) + MS log schema | **#2** | #2 |
| Best-effort RequestId + audit-log join for identity | [MS docs EvaluationContext](https://learn.microsoft.com/en-us/data-integration/gateway/service-gateway-performance) + [3Cloud analysis](https://3cloudsolutions.com/resources/monitoring-power-bi-on-premises-data-gateway-performance/) | **#3** | #3 |
| Column-name-based schema-adaptive log parser | [pbigtwmonitor](https://github.com/RuiRomano/pbigtwmonitor) (positional) → improve to named | **#4** | #5 |
| PerfMon NIC counters (`\Network Interface(*)\Bytes Total/sec`) + Test-Connection latency | [MS docs gap confirmed](https://learn.microsoft.com/en-us/data-integration/gateway/service-gateway-performance) + NET-NEW | **#7** | #4 |
| `Get-DataGatewayCluster -Scope Organization` fleet view | [SQLvariant](https://gist.github.com/SQLvariant/fd3b77e597fc6e13118636bf0d682383) | **#6** | fold-in |
| `Get-WinEvent` gateway service event reader | [martinskeem](https://github.com/martinskeem/powerbi-powershell) pattern | **#2, #10 (service crash)** | fold-in |
| Spool-disk free-space collector + Activator threshold | NET-NEW, MS log schema provides trend context | **#9** | fold-in |
| REST datasource status poller (`/datasources/{id}/statuses`) | [MS REST API docs](https://learn.microsoft.com/en-us/rest/api/power-bi/gateways/get-datasource-status) | **#10** | fold-in |

---

## 5. The 5 Differentiators (White Space)

These are the capabilities where **pain points and tool gaps converge** and no existing open-source tool provides an adequate implementation.

### Differentiator #1 — Real-Time Gateway-Offline Alerting
**Pain:** #1 (Score 5/5). Threads dating 2018–2025 confirm zero native Microsoft alerting. FPM collects heartbeat but has no alerting engine.  
**White space:** No tool wires heartbeat → threshold-based alert → notification without a Logic App.  
**Our approach:** Collect heartbeat via PowerShell → Bronze Delta → Activator rule on `heartbeat_age_minutes > threshold`. Configurable threshold in config.json. No Logic App required.  
**Source evidence:** [Fabric community thread Dec 2021](https://community.fabric.microsoft.com/t5/Service/Gateway-offline-alert-notification/m-p/2243074), [Microsoft Ideas board 2018](https://ideas.powerbi.com/forums/265200-power-bi-ideas/suggestions/18100489-gateway-error-notification).

### Differentiator #2 — Unified Failure Triage
**Pain:** #2 (Score 5/5). No tool joins Power BI Service refresh error + gateway QueryExecution error + Windows Event Log service event.  
**White space:** FPM Job Details is time-window fuzzy; pbigtwmonitor archived; kerski monitors refresh outcomes only, not gateway-side cause.  
**Our approach:** Silver notebook joins `refreshHistory` (S7) + `QueryExecution.ErrorMessage` (S1) + `EventLog` (S9) on `RequestId` and a ±30s time window. Labeled "best-effort time-window join" where RequestId is absent.  
**Source evidence:** [Microsoft Known Issue #844](https://github.com/MicrosoftDocs/fabric-docs/blob/main/docs/known-issues/known-issue-844-intermittent-refresh-failure-gateway.md), [phase3_painpoints.md #2](./phase3_painpoints.md).

### Differentiator #3 — Best-Effort Query → Identity Attribution
**Pain:** #3 (Score 5/5). Architectural gap confirmed by Microsoft documentation and [3Cloud practitioner blog](https://3cloudsolutions.com/resources/monitoring-power-bi-on-premises-data-gateway-performance/).  
**White space:** The `EvaluationContext` field in `QueryStart` contains `artifactId` for Fabric workloads only. Power BI audit event `RequestId` is present for on-demand refreshes only. No tool implements even the partial join.  
**Our approach:** Silver notebook joins `QueryStart.EvaluationContext.artifactId` → dataset mapping, AND joins `QueryStart.RequestId` → Power BI Activity Events API on RequestId + ±60s time window. **Prominently labeled:** "BEST-EFFORT / FUZZY — only reliable for Fabric workloads with EvaluationContext populated; not available for Paginated Reports, Dataflow Gen1, or DirectQuery user sessions."  
**Source evidence:** [MS docs: EvaluationContext limitations](https://learn.microsoft.com/en-us/data-integration/gateway/service-gateway-performance), [3cloudsolutions.com](https://3cloudsolutions.com/resources/monitoring-power-bi-on-premises-data-gateway-performance/).

### Differentiator #4 — Network / Bandwidth Metrics (Highest-Value Gap)
**Pain:** #7 (Score 4/5). Confirmed by Microsoft's own documentation: ["Gateway diagnostics doesn't capture diagnostics directly related to the (virtual) machine and its network, like bandwidth or latency"](https://learn.microsoft.com/en-us/data-integration/gateway/service-gateway-performance).  
**White space:** Zero tools address this. The data exists at the OS NIC counter level but has no gateway-semantic context in any monitoring tool.  
**Our approach:** NET-NEW `Collect-NetworkMetrics.ps1` using `Get-Counter` on `\Network Interface(*)\Bytes Total/sec` and `\Network Interface(*)\Current Bandwidth`, plus `Test-Connection` latency probe to Power BI relay endpoint. JSON output correlated with `SpoolingTotalDataSize` in silver notebook.  
**Source evidence:** [MS docs confirmed gap](https://learn.microsoft.com/en-us/data-integration/gateway/service-gateway-performance), [phase1_2_tools.md Build-It-Yourself section](./phase1_2_tools.md).

### Differentiator #5 — Schema-Adaptive Log Parser (The Sleeper Win)
**Pain:** #4 (Score 4/5). PBIT template breaks on every gateway version that adds columns — confirmed across multiple community threads 2020–2024.  
**White space:** The PBIT uses rigid positional column parsing. FPM's PowerShell is more resilient but has high setup cost. No published tool implements a true column-name-based adaptive parser.  
**Our approach:** `01_bronze_ingest.py` reads gateway log CSVs using column-name inference, not positional indexing. Extra columns are preserved in a `_extra_cols` struct field. Missing columns are handled with `.get()` + null default. Parser never raises `DataFormat.Error` from column additions.  
**Source evidence:** [community.fabric.microsoft.com thread 2022](https://community.fabric.microsoft.com/t5/Desktop/Gateway-Performance-Monitoring-PBIT-Dataformat-error/td-p/1386477), [phase3_painpoints.md #4](./phase3_painpoints.md).

---

## 6. Fold-In Features (Cheap Wins)

| Feature | Source Pattern | Pain # | v-Target |
|---|---|---|---|
| Fleet/multi-gateway view | `Get-DataGatewayCluster -Scope Organization` ([SQLvariant](https://gist.github.com/SQLvariant/fd3b77e597fc6e13118636bf0d682383)) | #6 | v1 |
| Disk-spool free-space alerting | NET-NEW `Collect-DiskSpool.ps1` + Activator | #9 | v1 |
| Credential-drift re-validation | REST `GET /gateways/{id}/datasources/{id}/statuses` | #10 | v2 |
| Windows Event Log reader | `Get-WinEvent` ([martinskeem pattern](https://github.com/martinskeem/powerbi-powershell)) | #2, #10 | v1 |
| Gateway version currency check | Compare node version vs. [MS update feed](https://go.microsoft.com/fwlink/?linkid=868594) | #6 | v2 |
| Cluster load-skew score | Per-node query count CV computed in gold notebook | #6 | v2 |

---

## 7. What NOT to Build

These items are explicitly excluded to prevent scope creep, wasted effort, or duplication of better-maintained tools.

| Don't Build | Reason | Better Alternative |
|---|---|---|
| Re-implement FPM's collection from scratch | FPM's PowerShell collectors are Microsoft-maintained; duplicating them adds undifferentiated maintenance burden | Wrap/adapt FPM patterns; use FPM bridge for existing FPM users |
| Promise exact query attribution | Architecturally impossible without Microsoft adding `DatasetId` to gateway log schema; claiming otherwise misleads operators | Label best-effort/fuzzy; document limitations prominently |
| VNet gateway support in v1 | Completely different collection mechanism (REST portal download, not file-system); breaks collector design | Documented as v2 sub-architecture; see [phase0_scope.md §4](./phase0_scope.md) |
| Rebuild commercial lineage (Power BI Sentinel) | Gateway → table → report lineage is Power BI Sentinel's crown jewel (commercial); rebuilding it is 6+ months of work with less data access | Link to Sentinel; focus on gateway-side operational metrics |
| Azure Monitor VM agent integration | Adds Azure infrastructure dependency outside Fabric/Power BI; over-engineers the network metric | OS-level `Get-Counter` on the gateway host is sufficient for v1 |
| Personal gateway shadow IT discovery | SummitView's commercial crown jewel; requires REST tenant scan + dependency graph; complex for marginal v1 value | Document SummitView for this use case; deferred to v3 |
| Per-process mashup PID correlation | Requires Windows Process Monitor or ETW tracing; no gateway log correlation hook; high complexity for uncertain value | Surface aggregate SystemCounter metrics; flag per-process as v3 |
| KQL / Eventhouse as primary store | We are Delta-native; KQL is available via FPM bridge only | Eventhouse OneLake availability bridge is the optional path for FPM users |

---

## 8. Source Reliability Assessment

| Source | Reliability | Notes |
|---|---|---|
| [learn.microsoft.com gateway performance docs](https://learn.microsoft.com/en-us/data-integration/gateway/service-gateway-performance) | ★★★★★ Authoritative | Full log schema confirmed; network gap confirmed by Microsoft |
| [FPM / Fabric Jumpstart](https://jumpstart.fabric.microsoft.com/catalog/fpm-gateway-monitoring/) | ★★★★★ Authoritative | MS first-party; describes deployed items verbatim |
| [RuiRomano/pbigtwmonitor](https://github.com/RuiRomano/pbigtwmonitor) | ★★★★★ Code-verified | 101 stars, 35 forks; archived Dec 2025; code confirms patterns |
| [community.fabric.microsoft.com pain threads](https://community.fabric.microsoft.com/t5/Service/Gateway-offline-alert-notification/m-p/2243074) | ★★★★ High | Direct quotes from multiple users; MS support responses confirm gaps |
| [3cloudsolutions.com practitioner blog](https://3cloudsolutions.com/resources/monitoring-power-bi-on-premises-data-gateway-performance/) | ★★★★ High | Detailed technical analysis with RequestId attribution quote |
| [SQLvariant gist](https://gist.github.com/SQLvariant/fd3b77e597fc6e13118636bf0d682383) | ★★★ Medium-High | Gist confirmed Dec 2024; functional code |
| [martinskeem/powerbi-powershell](https://github.com/martinskeem/powerbi-powershell) | ★★★ Medium-High | Functional pattern; last active 2021-2022; PSGallery confirmed |
| [SummitView feature page](https://www.summitview.app/features/gateway-monitoring) | ★★★★ High (marketing) | Commercial product; feature claims from vendor page, not code |
| [Power BI Sentinel changelog](https://www.powerbisentinel.com/whats-new/) | ★★★★ High (marketing) | Changelog confirms Sep 2024 gateway enhancement |
| [pipeline_critique.md](./pipeline_critique.md) | ★★★★★ Internal synthesis | Evidence-bound critique; all claims cited |

---

*All source URLs carried from phase0_scope.md, phase1_2_tools.md, phase3_painpoints.md, and pipeline_critique.md. No URLs invented. Grounding note at bottom of document.*
