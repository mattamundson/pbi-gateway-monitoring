# Phase 0 — Scope, FPM Baseline Teardown & Build-vs-Fork Gate

**Project:** Best-of-breed On-Premises Data Gateway monitoring for Power BI / Microsoft Fabric
**Operator:** Principal data/analytics engineer, internal tool
**Pipeline mode:** Lean merged path (incorporates the [pipeline critique](./pipeline_critique.md) structural fixes at lean scale, reusing prior session research)

> **Framing correction (the critique's #1 point, adopted):** This is **NOT a greenfield discovery project.** Microsoft's **Fabric Platform Monitoring (FPM)** already ships ~80% of a gateway observability stack. This pipeline is therefore a **gap analysis** that must terminate in an explicit **build-vs-fork decision** (§5) before any architecture or code is produced. Phase 4 branches on that decision.

---

## 1. Disambiguation — the three "gateways"

| Type | What it is | Host / runtime | Telemetry surface | In scope? |
|---|---|---|---|---|
| **On-premises Data Gateway** | Customer-managed bridge between cloud (Power BI/Fabric) and on-prem/IaaS sources | Windows host machine (standard mode) you control | Local report/log files (`*Report_*.log`, Mashup/Query/Spatial logs), PerfMon counters, Windows Event Log, `DataGateway` PS cmdlets | **YES — primary** |
| **Virtual Network (VNet) data gateway** | Microsoft-managed serverless gateway into an Azure VNet | Serverless / managed — **no host machine** | Portal/REST **diagnostic download** (Parquet + JSONL): System Counters, Mashup Logs, Query Execution, **Resource Consumption** (`CapacityUnitSeconds`). 29-day retention (Mashup 48h). **Unavailable under Fabric Private Link** ([MS docs](https://learn.microsoft.com/en-us/data-integration/vnet/data-gateway-download-diagnostics)) | **CONDITIONAL — see §4** |
| **API / app gateways** (Azure API Management, App Gateway, etc.) | Network/API traffic routers | n/a | n/a | **NO — excluded, do not conflate** |

The critical, repeatedly-confirmed fact: **on-prem and VNet gateways do not share a collection mechanism.** On-prem = read local files on a host you own. VNet = pull a portal/REST download. A single collector design cannot serve both.

---

## 2. Monitoring surface map (every signal source)

| # | Signal source | What it exposes | Access method | Gateway type |
|---|---|---|---|---|
| S1 | **Gateway Performance logs** — QueryExecution (15-col), QueryStart (7-col), QueryExecutionAggregation, SystemCounter | per-query duration, spool sizes, success/error, QueryType, DataSource; aggregated counters | Local CSV `*Report_*.log` files; on by default (standard mode) | On-prem |
| S2 | **Gateway config / report files** | gateway version, datasource list, cluster membership | Local files + `GatewayProperties.json` | On-prem |
| S3 | **Windows PerfMon counters** (gateway) | CPU, memory, mashup container counts/limits, system vs gateway CPU | PerfMon / typeperf on host; some surfaced in SystemCounter log | On-prem |
| S4 | **Gateway verbose logs** (Mashup / Query / Spatial) | detailed mashup engine traces, query strings (when additional logging on) | Local files; additional logging toggled in app | On-prem |
| S5 | **`DataGateway` PowerShell module** (`Get-DataGatewayCluster*`, etc.) | cluster/node inventory, status, member health | PS cmdlets / Fabric gateway REST | On-prem + VNet (inventory) |
| S6 | **Fabric/Power BI Admin & Gateway REST APIs** | `GET gateways`, `/datasources`, `/datasources/{id}/status` (online/offline, datasource health) | REST (SP or user auth) | On-prem + VNet |
| S7 | **Scheduled refresh history REST** | `GET /datasets/{id}/refreshHistory` — refresh duration, failure codes, timestamps **not in gateway logs** | REST | service-side |
| S8 | **Monitoring workspace / Fabric Capacity Metrics app** | CU consumption by workspace/workload (NOT per-gateway), throttling | Capacity Metrics semantic model | capacity-side |
| S9 | **Windows Event Log (System/Application)** | gateway **service crash, restart, failure** events | Event Log on host (`Get-WinEvent`) | On-prem |
| S10 | **VNet diagnostic download** | System Counters, Mashup, Query Execution, **Resource Consumption** (`CapacityUnitSeconds`/gateway) | Portal / REST Parquet+JSONL pull | VNet only |
| S11 | **OS-level NIC / disk / host metrics** (Azure Monitor VM, PerfMon Network) | **network bandwidth & latency**, disk IO, spool-disk free space | Azure Monitor agent / PerfMon — **NOT in any gateway diagnostic** | On-prem host |
| S12 | **EvaluationContext field** (in QueryStart) | `artifactId` (datasetId/dataflowId) for **Fabric workloads only** — not Dataflow Gen1, not Paginated Reports | parse QueryStart log | On-prem |

**Backbone note:** S11 (network) is the most common real bottleneck and is invisible to S1–S10. S7, S9, S11, S12 are the signals most likely to be *gaps* in any existing tool (confirmed in §5 / Phase 2.5).

---

## 3. Jobs-to-be-done (the requirements later phases score against)

1. **Query saturation** — which queries/datasources are consuming the gateway, and how is time split (source-read vs processing vs spool)?
2. **Refresh fault attribution** — is a failing refresh the gateway's fault, the source's, or the network's?
3. **Headroom** — gateway CPU/memory/mashup-container headroom per node; when to scale out.
4. **Source→gateway routing** — which datasources route through which gateway/cluster.
5. **Spool disk spill** — are spooler files spilling to disk; is spool-disk space at risk?
6. **Online/health** — is each gateway node up (heartbeat), and are its datasources reachable?
7. **Cluster load skew** *(added per critique)* — is load balanced across cluster members, or is one node hot?
8. **Identity correlation** — map a slow gateway query to the dataset/report/user/refresh that caused it.
9. **Network bottleneck** — is gateway→cloud bandwidth/latency the constraint? (requires S11)
10. **Service stability** — is the gateway service crashing/restarting? (requires S9)

---

## 4. VNet scope decision (forced)

**Decision: VNet is DESCOPED from v1; documented as a separate v2 sub-architecture.**

Rationale:
- VNet collection is a portal/REST **pull** of Parquet/JSONL — architecturally disjoint from the on-prem file/Eventstream push model. Bundling it fractures the collector design (critique #2).
- VNet diagnostic download is **unavailable under Fabric Private Link** ([MS docs](https://learn.microsoft.com/en-us/data-integration/vnet/data-gateway-download-diagnostics)) — a hard blocker for many enterprise tenants.
- VNet adds a unique signal (S10 Resource Consumption / `CapacityUnitSeconds`) worth capturing later, but it does not share schema or cadence with on-prem.

**v2 VNet sub-architecture (deferred):** a scheduled REST poller that pulls the diagnostic download, lands Parquet in OneLake, and conforms the Query Execution + Resource Consumption schemas into the same gold model. Tracked as an explicit open item, not built in v1.

---

## 5. BUILD-VS-FORK GATE (mandatory deliverable)

The decision the entire pipeline exists to make. Scored in Phase 2.5; criteria defined here.

### 5.1 What FPM already provides (baseline teardown — verified this session)

FPM (`microsoft/fabric-toolbox` → fabric-platform-monitoring; successor to deprecated RuiRomano/pbigtwmonitor + ecotte/Fabric-Monitoring-RTI):

- **Collection:** gateway-node PowerShell — `Run-GatewayHeartbeat`, `Run-UploadGatewayLogs`, `Get-DataGatewayInfo`; config generated by a Gateway Config notebook.
- **Ingestion/storage:** two Eventstreams (Heartbeat, Reports) → **Eventhouse / KQL DB** with curated views (`QueryExecutionUnified`, `GatewayNodeStatusWithInfo`, `SystemCounters`, `QueryConnections`).
- **Serving:** DirectQuery semantic model (~18 measures) + **6-page Power BI report** (Gateways, Jobs, Job Details, Queries, Running Jobs, System Counters) with page auto-refresh.
- **Identity correlation:** Job Details carries workspace id / item id / service name / datasource kind — partial S12 coverage.
- **Multi-cluster:** native via `GatewayObjectId` discrimination + cluster slicers.
- **Maintained:** active in fabric-toolbox; covers S1, S2, S5, S6, partial S3/S12.

### 5.2 Verified FPM gaps (pre-seeded; confirmed in Phase 2.5)

| Gap | Signal | Evidence |
|---|---|---|
| Network bandwidth/latency | S11 | Explicitly outside gateway diagnostics ([MS docs](https://learn.microsoft.com/en-us/data-integration/gateway/service-gateway-performance)) |
| Windows Event Log (service crash/restart) | S9 | Not collected by FPM scripts |
| Cluster load-skew detection | S1-derived | No FPM measure for per-node load distribution |
| Scheduled refresh history | S7 | Not ingested; lives in dataset refresh REST |
| Identity correlation completeness | S12 | `EvaluationContext` populated for Fabric workloads only — **not** Dataflow Gen1 / Paginated Reports |
| Delta/OneLake-native storage | — | FPM is KQL/Eventhouse-centric; **bridgeable** via [Eventhouse OneLake availability](https://learn.microsoft.com/en-us/fabric/real-time-intelligence/event-house-onelake-availability), not a rewrite |
| VNet Resource Consumption | S10 | On-prem only; VNet descoped (§4) |
| Known bug | — | `Get-DataGatewayInfo` SP **401** ([Fabric community](https://community.fabric.microsoft.com/t5/Real-Time-Intelligence/Fabric-Platform-Monitoring-accelerator-Gateways-module/m-p/4884544)) |

### 5.3 Gate scoring rule

Phase 2.5 classifies every candidate feature as **[FPM Covers] / [Partial] / [Gap] / [Bug]** and scores each Gap by (operator value × build effort⁻¹).

- **Weighted Gap Score < threshold → FORK PATH.** Output = FPM extension spec: new Eventstream sources (Windows Event Log collector, refresh-history REST poller, Azure Monitor network bridge), new KQL functions/report pages, and — if Delta/OneLake is required — the **Eventhouse OneLake availability** bridge rather than a parallel pipeline.
- **Weighted Gap Score ≥ threshold → GREENFIELD PATH.** Output = Delta medallion + PySpark + PowerShell build spec, with the bridge to reuse FPM data where sensible.

**[Inference] Prior:** Based on all session evidence, the gate is expected to resolve to **FORK/EXTEND** — the gaps (S7, S9, S11 + Delta bridge) are additive pollers and a storage bridge, not reasons to re-implement FPM's collection. Greenfield would mostly re-build what FPM maintains. This prior is to be confirmed, not assumed, in Phase 2.5.

---

## 6. Success criteria for the final tool

1. Answers all 10 jobs-to-be-done (§3), or explicitly documents which signal makes one unanswerable.
2. Closes at least the verified gaps S7, S9, S11 that FPM misses.
3. Reuses FPM (fork/bridge) wherever it already wins — no re-implementing maintained collection without cause.
4. Delta/OneLake-accessible analytics layer for retention + custom analysis (via bridge if forking).
5. Honest about limits: network blind spot addressed via S11; identity correlation labeled fuzzy/time-bounded; paginated/Gen1 exclusions documented.
6. Code delivered as labeled reference stubs (`[STUB]/[ADAPTED-FROM-FPM]/[NET-NEW]`) with a Known-Unknowns section — never claimed "working" without a live-env pilot (Phase 5).

---

## 7. Process changes adopted from the critique

- **Phase 2.5 (FPM gap-delta)** inserted as the keystone feeding the gate.
- **Phase 3** runs only if gate=greenfield or leadership-grade evidence is needed; uses **two-pass** (blind cluster → informed reconciliation), separating product gaps from discovery deficits; sourced from **community.fabric.microsoft.com (no rate limit)** + Reddit archives rather than live rate-limited/paid APIs.
- **Phase 1 quotas** replaced by coverage-saturation (we largely have the inventory already); FPM + predecessors are Tier-0, always included.
- **Phase 4** gated and code-labeled; **Phase 5** validation/pilot added (deferred to a real Fabric env).

---

*Phase 0 complete. Next: Phase 2.5 gap-delta table → resolve the gate. Sources cited inline; verified facts carried from this session's prior research + the pipeline critique.*

---

## 8. AMENDMENT — product reframing & gate resolution (post Phase 0 review)

The operator clarified the product goal, which resolves the build-vs-fork gate:

**Product goal:** a tool **anyone can use by forking the GitHub repo**, that (a) fixes the **top 10 most-discussed gateway pain points across the internet** and (b) **combines the best attributes of the top ~10 tools/resources.**

**Friction floor (operator decision):** Fabric-native, FPM-class prerequisites acceptable (F8+ capacity, service principal, Key Vault OK). Not required to run capacity-free.

**Gate resolution: BUILD-NEW (from-scratch) Fabric-native core + OPTIONAL FPM bridge.**
(Note: "build-new / from-scratch" replaces the generic engineering term previously used; it has no relation to any company.)

Rationale (least architectural impact while retaining best features):
- **Not a hard FPM fork** — a fork inherits FPM's KQL/Eventhouse storage, its "customizations may revert on update" problem, and its release-cycle coupling, all of which undermine "anyone forks and uses it."
- **Not fully independent of FPM either** — FPM's gateway-node PowerShell collectors are Microsoft-maintained; re-implementing them adds undifferentiated maintenance/bugs.
- **Therefore: standalone Fabric-native core** (own/wrapped collectors + Delta medallion + semantic model + report — cleanly forkable, FPM not required) **+ an optional adapter** that ingests FPM data via [Eventhouse OneLake availability](https://learn.microsoft.com/en-us/fabric/real-time-intelligence/event-house-onelake-availability) for those already running FPM. FPM becomes a *supported input*, not a *dependency*.

**Consequences for the pipeline:**
- Phase 3 (pain mining) is now **mandatory and primary** — the top-10 pain points are the spec driver.
- Phase 1/2 must yield a genuine **ranked top-10 tools** so the "best attributes" synthesis is honest.
- Phase 4 targets the build-new core + optional FPM bridge; code labeled `[STUB]/[ADAPTED-FROM-FPM]/[NET-NEW]`.

---

## 9. AMENDMENT — deployer privilege assumption

**The repo owner/deployer is a Fabric + Azure tenant owner with full access to all API keys, permissions, and secrets.**

Design consequences:
- No least-privilege/non-admin fallback paths needed. Assume service principal creation, tenant-setting toggles, gateway **Admin** role, Key Vault provisioning, and **admin-level REST APIs** (Scanner, admin gateway APIs, dataset refresh history) are all available.
- "Anyone can fork and use it" is correctly read as "any **tenant/Fabric admin** can fork and use it" — the appropriate audience for a gateway-monitoring tool.
- The `Get-DataGatewayInfo` SP **401** is therefore a **bug to fix**, not a permission boundary to design around.
- This reinforces the gate resolution: FPM-class prerequisites are not a barrier for this deployer, so the build-new Fabric-native core stands.
