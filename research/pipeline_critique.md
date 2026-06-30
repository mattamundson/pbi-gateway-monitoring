# Pipeline Plan Critique: On-Premises Data Gateway Monitoring Tool

**Reviewer role:** Principal-level Microsoft Fabric / Power BI platform engineer and research-methodology critic  
**Date:** 2026-06-30  
**Subject:** 4-phase (Phase 0–4) research-to-build pipeline for a best-of-breed On-prem Data Gateway monitoring tool

---

## 1. Executive Verdict

**The plan is mis-aimed and over-engineered for its actual problem.**

The plan is designed as if it were conducting original discovery research to build a greenfield monitoring system. In reality, [Fabric Platform Monitoring (FPM)](https://jumpstart.fabric.microsoft.com/catalog/fpm-gateway-monitoring/) — the Microsoft-maintained successor to two deprecated community repos — already ships a deployable gateway observability stack: two Eventstreams, an Eventhouse/KQL DB, a DirectQuery semantic model, and a six-page Power BI report, deployable in under 2 minutes. The pipeline never forces a formal build-vs-adopt decision, meaning an operator could execute all five phases and generate a "build spec" for something that is 80% already built. The result is a research funnel that is likely to produce a worse artifact than just forking FPM, at significantly higher time cost.

The plan has two additional structural integrity problems: (a) it scopes VNet gateways without understanding that their telemetry surface is a portal-based download — not a PowerShell-collectible file path — which breaks the entire collection architecture; and (b) its Phase 3 firewall, while methodologically interesting, will systematically miss pain points that require product knowledge to even formulate. Taken together, the plan needs a gate inserted at Phase 0.5 (build-vs-fork decision), a hard scope decision on VNet, and a reconciliation loop between Phases 2 and 3.

**Overall rating: Not sound as written.** Salvageable with targeted structural changes.

---

## 2. Top 5 Weak Points (Ranked by Severity)

### Weak Point #1 — CRITICAL: No FPM Build-vs-Adopt Decision Gate

**Severity: Existential**

The plan's most damaging omission is the absence of a forced, explicit decision gate on whether to extend FPM, fork FPM, or build greenfield. [FPM Gateway Monitoring](https://jumpstart.fabric.microsoft.com/catalog/fpm-gateway-monitoring/) (from `ecotte/fabric-platform-monitoring` via `microsoft/fabric-toolbox`) already delivers: PowerShell heartbeat + log uploaders, Eventstream ingestion, an Eventhouse/KQL database with curated views (`QueryExecutionUnified`, `GatewayNodeStatusWithInfo`, `SystemCounters`, `QueryConnections`), a DirectQuery semantic model with 18 measures, and a 6-page Power BI report (Jobs, Job Details, Queries, Running Jobs, Gateways, System Counters) with auto-refresh. The [Fabric community confirms](https://community.fabric.microsoft.com/t5/Real-Time-Intelligence/Fabric-Platform-Monitoring-accelerator-Gateways-module/td-p/4884544) it is actively maintained.

The plan reads as if FPM does not exist. Phase 0 defines "success criteria" and Phase 4 generates "starter code" — but if the research in Phases 1–3 discovers FPM, the correct output of Phase 4 is a fork specification or a gap-fill extension, not a net-new architecture. The pipeline provides no mechanism to make this determination and no stopping condition that says "FPM already solves this."

**Consequence:** A principal engineer who executes this pipeline faithfully can spend weeks doing research and produce a build spec for a system that is functionally inferior to what they could have deployed in 2 minutes via `jumpstart.install("fpm-gateway-monitoring")`.

---

### Weak Point #2 — HIGH: VNet Gateway Architecture Contradiction

**Severity: Architecture-fracturing**

The plan scopes "On-prem + VNet gateways" in Phase 0, but the PowerShell collection architecture — which reads from `C:\Windows\ServiceProfiles\PBIEgwService\AppData\Local\Microsoft\On-premises data gateway` — cannot work on VNet gateways. VNet gateways are serverless/managed; there is no host machine to run a script on.

Critically, the plan treats VNet telemetry as if it requires the same collection mechanism. It does not. [Microsoft's VNet gateway diagnostics documentation](https://learn.microsoft.com/en-us/data-integration/vnet/data-gateway-download-diagnostics) confirms that VNet gateway logs (System Counters, Mashup Logs, Query Execution Report, Resource Consumption) are available as portal downloads — in Parquet and JSONL format, retained for 29 days (Mashup Logs: 48 hours) — via the "Manage connections and gateways" page. The collection mechanism is a REST API call, not a PowerShell file-system reader.

However, there is a further problem: if Fabric Private Link is enabled on the tenant, the download feature is [explicitly unavailable](https://learn.microsoft.com/en-us/data-integration/vnet/data-gateway-download-diagnostics). There is also no automated push mechanism for VNet logs — they must be actively pulled. A pipeline designed around Eventstream push ingestion from on-prem hosts simply does not apply to VNet.

Including VNet in scope without a separate collection sub-architecture fractures Phase 4's build spec. The Phase 4 PySpark/PowerShell collector design will be internally inconsistent.

---

### Weak Point #3 — HIGH: Phase 3 Firewall Creates Structural Blind Spots

**Severity: Methodological — produces systematically incomplete pain data**

The idea of firewalling Phase 3 (pain mining) from Phase 2 (feature knowledge) is defensible in theory — it prevents the researcher from pattern-matching pain to known solutions and inventing false confirmations. However, the methodology has a well-documented flaw in requirements engineering: **operators cannot articulate pain for capabilities they don't know are possible.**

A gateway admin on Reddit will not post "I'm frustrated that my monitoring tool doesn't correlate QueryTrackingId to dataset identity via EvaluationContext in the QueryStart log." They will post "refreshes are slow and I don't know why." The firewall prevents the researcher from recognizing this as the identity-correlation gap described in Phase 2 — and prevents them from probing whether solutions like FPM's `QueryExecutionUnified` view already address it.

This is the classic distinction between **stated pain** (what users articulate) and **root-cause pain** (what the feature knowledge reveals is the actual constraint). Firewalling the two phases means Phase 3 surfaces only stated pain, and the synthesis in Phase 4 cannot bridge the gap because the connection was never made.

There is also a practical problem: Reddit is rate-limited, X/Twitter now requires paid API access ([$0.005/post for enterprise archive search](https://twitterapi.io/blog/twitter-scraping-guide-2026); no free tier), and LinkedIn is largely bot-blocked. The "pain mining" phase may be significantly data-constrained regardless of methodology.

---

### Weak Point #4 — HIGH: Source Quota System Will Force Low-Quality Padding

**Severity: Research quality degradation**

The Phase 1 quota system (MS first-party ≥4, GitHub ≥6, blogs ≥5, vendor ≥3, forums ≥4) imposes minimum counts per source class without any quality weighting. This creates a perverse incentive: the researcher must find 4 forum posts even if 2 high-quality ones have already saturated the information. The result is padding — low-signal sources included purely to meet the quota.

The problem is especially acute for:
- **X/Twitter:** No free read API since 2023. The official API now costs [$0.005 per post for basic access](https://twitterapi.io/blog/twitter-scraping-guide-2026), making systematic data collection expensive. Any "evidence quote + source URL" from X is likely to be either paywalled, manually screenshot, or from a cached/third-party aggregator — none of which meet the plan's own citation standards.
- **LinkedIn:** Essentially unreadable by automated tools. Manual browsing is the only option, which is time-limited and non-reproducible.
- **Reddit:** [Rate-limited and increasingly aggressive about bot detection](https://www.reddit.com/r/datasets/comments/1qju57e/best_way_to_pull_twitterx_data_at_scale_without/). The official API has been tiered since 2023.

The "no source URL = it doesn't exist" hard rule, combined with mandatory quotas for sources that resist URL-level citation, creates a contradiction: either the researcher fakes citations (violating the evidence rule) or they fail to meet quotas (violating the pipeline rule). The plan does not resolve this.

---

### Weak Point #5 — MEDIUM-HIGH: Phase 4 Starter Code Is Unverifiable by Design

**Severity: Delivery risk — produces untested, potentially misleading artifacts**

Phase 4 specifies "working starter code" — PySpark ingest, PowerShell collectors, DAX measures — but the pipeline has no Windows/Fabric environment to test against. This is acknowledged implicitly in the global rule to label `[Unverified]`, but "working starter code" and "[Unverified]" are contradictory claims.

The specific risks:
1. **PowerShell collector scripts** — FPM's own `Get-DataGatewayInfo` script has a [known 401 Unauthorized bug with service principals](https://community.fabric.microsoft.com/t5/Real-Time-Intelligence/Fabric-Platform-Monitoring-accelerator-Gateways-module/m-p/4884544) accessing gateway admin APIs. A generated script will not know to handle this and will fail silently.
2. **KQL/Eventhouse** vs. **Delta/OneLake** — FPM is KQL-centric, but the plan's stack is Delta/PySpark-centric. These are architecturally different. The FPM `QueryExecutionUnified` view is a KQL function; it does not translate directly to a PySpark Delta read. Generating "starter code" that conflates these will produce broken artifacts.
3. **DAX for DirectQuery over KQL** — requires specific handling of `TREATAS`, `USERELATIONSHIP`, and time-intelligence patterns that behave differently over KQL than over import/DirectLake. Generated DAX will likely contain subtle correctness errors that only manifest at query time.

The plan needs an explicit disclaimer section in Phase 4 — not a blanket `[Unverified]` label — specifying exactly which components require a live environment test before being treated as functional.

---

## 3. Specific Phase-by-Phase Changes

### Phase 0: Scope & Calibration

**Problems:**
- No build-vs-adopt hypothesis. FPM is not listed as a known prior art constraint.
- "On-prem + VNet" in scope without acknowledging the radically different collection architecture.
- Success criteria do not define a stopping condition for "FPM already solves this."

**Recommended changes:**

1. **Add FPM as explicit Phase 0 input.** The scope document must start with: "FPM (fabric-toolbox `ecotte/fabric-platform-monitoring`) exists and covers [X, Y, Z]. The hypothesis is that it has gaps in [A, B, C]. Phase 1–2 will test this hypothesis, not assume greenfield." This reframes the entire pipeline as gap analysis, not discovery.

2. **Define a Build-vs-Fork decision gate** as a mandatory Phase 0 deliverable, with explicit criteria:
   - If FPM gap score < threshold → "Fork FPM and extend" path
   - If FPM gap score ≥ threshold → "Greenfield on Delta" path
   - Gate must be passed before Phase 3 begins

3. **Split VNet into a separate scoped question.** Phase 0 should explicitly document: "VNet gateway collection requires portal-based REST API pull (not PowerShell file-system), 48-hour/29-day log retention limits, and is unavailable when Fabric Private Link is enabled. VNet monitoring is a separate sub-scope that may require a separate architecture." If VNet is in scope, define the collection mechanism separately.

4. **Add a 7th job-to-be-done: "Detect cluster-level failover/load-balancing imbalance."** FPM's `Gateway Node Info` table discriminates nodes, but there is no built-in measure for load distribution skew across cluster members. This is a real gap.

---

### Phase 1: Discover Sources

**Problems:**
- Quota counts are arbitrary minimums with no quality floor.
- No explicit treatment of X/Twitter or LinkedIn inaccessibility.
- "Kill rules" are described but their interaction with quota minimums is undefined — what happens when a source class is exhausted at 2 qualifying sources?
- FPM itself should be the first source qualified, not discovered mid-phase.

**Recommended changes:**

1. **Replace hard quotas with quality thresholds.** Minimum counts exist to prevent cherry-picking, not to force padding. Replace "≥N sources per class" with "≥N sources per class OR coverage saturation reached (three consecutive sources add no new signal)." This allows early termination for well-covered domains.

2. **Add source accessibility pre-screen.** Before assigning quotas, document the actual access constraints:
   - X/Twitter: paid API required, budget $0.005/post; if no budget, substitute LinkedIn/Slack communities
   - Reddit: rate-limited; prefer pushshift archives or third-party aggregators
   - LinkedIn: manual-only; mark as non-reproducible; annotate accordingly

3. **Promote FPM and deprecated predecessors (pbigtwmonitor, Fabric-Monitoring-RTI) to Tier 0** — not subject to kill rules, always included, analyzed in full before Phase 1 proper begins.

4. **Add coverage matrix column: "FPM already covers?"** This turns Phase 1 into a gap-mapping exercise, not just a collection exercise.

---

### Phase 2: Extract Features + Code

**Problems:**
- "Crown jewel features" framing assumes greenfield; should be "FPM gaps" framing.
- "Orphan signals" (signals without implementations) will be heavily dominated by the known FPM gaps (network bandwidth, identity correlation, cluster load distribution, Windows Event Log) — these should be pre-seeded based on Phase 0 research, not discovered inductively.
- The evidence-binding rule is sound but generates fragile artifacts: a source URL that goes dead invalidates the entire row.

**Recommended changes:**

1. **Add a "FPM coverage" column to the feature matrix.** Every feature row should have: `Feature | Sources | Evidence Quote | FPM Already Has | FPM Gap Notes`. This makes Phase 2's output directly actionable for the Phase 0 build-vs-fork gate.

2. **Pre-seed known gaps from the verified facts.** The following are already confirmed as FPM gaps and should appear as hypothesis rows in Phase 2 from the start:
   - Network bandwidth/latency monitoring (no gateway diagnostics for this — confirmed by [Microsoft docs](https://learn.microsoft.com/en-us/data-integration/gateway/service-gateway-performance))
   - Dataset/requester identity correlation (fuzzy/time-bounded via `EvaluationContext` in QueryStart, not guaranteed)
   - Cluster load-balance skew detection
   - Windows Event Log (gateway service crash, restart events)
   - Scheduled refresh history via REST API (`GET /datasets/{id}/refreshHistory`)
   - VNet-specific: Resource Consumption log (`CapacityUnitSeconds` per gateway) — absent from on-prem FPM

3. **Document URL-fragility risk.** Archive source URLs via web archive services at collection time. A citation chain is only as durable as its weakest link.

---

### Phase 3: Pain-Point Mining

**Problems:**
- Firewall is too absolute; prevents root-cause pain identification.
- No budget for X/Twitter API.
- Pain Score formula (frequency × severity × how poorly addressed) has a denominator problem: "how poorly addressed" is 0 when no solution is known — but operators may not know FPM exists.

**Recommended changes:**

1. **Replace the hard firewall with a "blind first, informed second" two-pass approach:**
   - Pass 1 (blind): Mine forums/Reddit/community with no feature context. Cluster raw pain statements by theme.
   - Pass 2 (informed): For each cluster, bring in Phase 2 feature knowledge to ask: "Does FPM or any known tool address this? If yes, do operators know about it? If no, is this a genuine gap?" This produces both **stated pain** and **discovery pain** (pain that exists because operators don't know about available solutions).
   - Document which pain clusters are "education gaps" vs. "product gaps." Both are actionable but differently.

2. **Reformulate Pain Score denominator.** "How poorly addressed" should be split into:
   - `A = how poorly addressed by ANY known tool (including FPM)`
   - `D = discovery deficit (operators don't know the solution exists)`
   - Final score = `frequency × severity × max(A, D × 0.5)`
   This prevents a tool awareness problem from scoring as a product gap.

3. **Acknowledge data access constraints explicitly.** Reddit pain mining should use [Pushshift/Arctic Shift archives](https://arcticshift.xnor.app/) or community.fabric.microsoft.com search (which is publicly accessible without rate limiting) as primary sources, not live Reddit API calls.

---

### Phase 4: Synthesize / Build Spec

**Problems:**
- "Working starter code" claim is dishonest given the no-test-environment constraint.
- Delta/PySpark stack conflicts with FPM's KQL/Eventhouse architecture — if the decision gate recommends "fork FPM," the bronze/silver/gold Delta architecture is wrong.
- Feature-to-component traceability table is sound but will be dominated by FPM-matching rows if the prior phases work correctly.

**Recommended changes:**

1. **Gate Phase 4's architecture selection on the Phase 0 build-vs-fork decision.** There should be two Phase 4 variants:
   - **Fork path:** Output is a FPM fork specification — which KQL functions to extend, which new Eventstream sources to add (e.g., Windows Event Log via PowerShell, REST API refresher for dataset identity), which new report pages to build.
   - **Greenfield path:** Output is the Delta/PySpark/PowerShell architecture as currently described.
   The current plan generates only the greenfield path regardless of what the research finds.

2. **Replace "working starter code" with "reference implementation stubs."** All code should be labeled:
   - `[STUB — requires live gateway environment to validate]`
   - `[ADAPTED FROM FPM — credit source, verify license]`
   - `[NET NEW — no prior art, higher risk]`
   Include an explicit "Known Unknowns" section listing: the SP 401 bug in `Get-DataGatewayInfo`, the missing paginated report logging, the Premium capacity query miss rate, and the StreamBeforeRequestCompletes interaction with spool monitoring.

3. **Add a VNet-specific collector spec** if VNet remains in scope: REST API polling of the VNet diagnostic download endpoint (not a PowerShell file reader), with handling for the 48-hour Mashup Log retention cliff and the Private Link unavailability constraint.

4. **Address the FPM KQL-vs-Delta gap explicitly.** If the target is OneLake/Delta, note that FPM's Eventhouse supports [OneLake availability](https://learn.microsoft.com/en-us/fabric/real-time-intelligence/event-house-onelake-availability) — KQL tables can be exposed as Delta Parquet via this feature. This is the bridge, not a rewrite.

---

## 4. Missing Elements That Should Be Added

### A. Monitoring Signals the Plan Likely Misses

| Signal | Source | FPM Coverage | Plan Coverage |
|---|---|---|---|
| **Network bandwidth/latency** | External (Azure Monitor VM metrics, Perfmon Network counters) | Not covered (explicitly excluded from gateway diagnostics per [MS docs](https://learn.microsoft.com/en-us/data-integration/gateway/service-gateway-performance)) | Not mentioned |
| **Windows Event Log (gateway service)** | `System` and `Application` logs on gateway host (gateway restart, crash, service failure events) | Not covered | Not mentioned |
| **Cluster load distribution skew** | Computed from per-node `QueryExecution` in FPM | Not covered | Not mentioned |
| **Mashup engine container exhaustion** | `MashupDefaultPoolContainerMaxCount` counters, configurable since Jun 2019 ([MS docs](https://learn.microsoft.com/en-us/data-integration/gateway/service-gateway-performance-cpu)) | Partial (via SystemCounters) | Not mentioned |
| **Gateway spool disk saturation** | `SpoolingTotalDataSize` in QueryExecution report; spool folder disk utilization | Partial (FPM has DiskRead/DiskWrite) | Not mentioned |
| **Scheduled refresh history via REST** | `GET /datasets/{id}/refreshHistory` — surfaces refresh duration, failure codes, timestamps not in gateway perf logs | Not covered | Not mentioned |
| **VNet Resource Consumption log** | `CapacityUnitSeconds` per gateway per [VNet diagnostics](https://learn.microsoft.com/en-us/data-integration/vnet/data-gateway-download-diagnostics) | Not covered (on-prem only) | Not mentioned |
| **Capacity CU consumption by gateway workload** | Fabric Capacity Metrics App — CU by workspace/workload, not per-gateway per [community discussion](https://community.fabric.microsoft.com/t5/Service/How-to-Monitor-Usage-Across-Multiple-Virtual-Network-Data/m-p/4861955) | Not covered | Not mentioned |
| **Gateway configuration drift** | Comparing `Get-DataGatewayInfo` output across time (version, datasource count, cluster membership changes) | Partial | Not mentioned |
| **EvaluationContext identity field** | `QueryStart` log `EvaluationContext` field — contains `artifactId` (datasetId, dataflowId) for supported workloads. **Only populated for Fabric workloads, not Power BI Dataflow Gen1 or Paginated Reports** | Partially covered, limitation undocumented in FPM | Not mentioned |

### B. Missing Architectural Considerations

- **Eventhouse OneLake availability bridge:** FPM stores data in KQL/Eventhouse. If the operator wants Delta/OneLake, the FPM Eventhouse can expose tables as Delta Parquet via the [OneLake availability feature](https://learn.microsoft.com/en-us/fabric/real-time-intelligence/event-house-onelake-availability) — eliminating the need for a parallel collection pipeline.

- **Alerting layer (Fabric Activator/Reflex):** FPM's jumpstart documentation explicitly describes adding an Activator on KQL queries for gateway-down alerts. The plan's Phase 4 build spec does not mention an alerting component at all.

- **Multi-gateway-cluster fan-out:** FPM handles multiple gateway clusters natively via `GatewayObjectId` discrimination and `SelectedGateways/SelectedClusters` slicers. Any greenfield build spec must account for this.

- **Service principal 401 bug:** There is a [known issue](https://community.fabric.microsoft.com/t5/Real-Time-Intelligence/Fabric-Platform-Monitoring-accelerator-Gateways-module/m-p/4884544) where `Get-DataGatewayInfo` returns 401 Unauthorized even with correct permissions, because service principals do not currently have full access to the gateway admin APIs. This affects any architecture that uses SP-based collection.

---

## 5. Recommended Revised Pipeline Shape

```
Phase 0: Scope + FPM Baseline Audit (NEW STRUCTURE)
  ├── 0a. FPM teardown: document exactly what FPM covers, its schema, its gaps
  ├── 0b. Three-gateway-type telemetry surface map (on-prem vs VNet vs personal)
  ├── 0c. Define jobs-to-be-done AS GAPS relative to FPM baseline
  ├── 0d. VNet scope decision: separate sub-architecture or descope
  └── 0e. BUILD-VS-FORK GATE (mandatory output): fork/extend FPM, or greenfield?
       └── If fork: Phase 4 = FPM extension spec
       └── If greenfield: Phase 4 = Delta/PySpark build spec

Phase 1: Source Discovery (MODIFIED)
  ├── Tier 0: FPM + predecessors (pbigtwmonitor, Fabric-Monitoring-RTI) — always included
  ├── Tier 1: MS first-party docs (quality-gated, not quota-forced)
  ├── Tier 2: GitHub (fabric-toolbox issues, community repos)
  ├── Tier 3: Technical blogs and forums
  └── Coverage matrix: "FPM already covers?" column mandatory

Phase 2: Feature Extraction (MODIFIED)
  ├── Feature matrix with FPM coverage column
  ├── Pre-seeded gap hypotheses from Phase 0
  ├── Orphan signals = signals with no FPM coverage AND no other tool coverage
  └── URL archival at collection time

Phase 2.5: FPM Gap Delta (NEW)
  ├── Explicit reconciliation: Phase 2 features vs FPM schema
  ├── Classify: [FPM Covers] / [FPM Partially Covers] / [FPM Gap] / [FPM Bug]
  ├── Feeds directly into Phase 0e gate scoring
  └── If FPM Gap count < threshold → route to fork path

Phase 3: Pain Mining (MODIFIED)
  ├── Pass 1 (blind): raw pain clustering, no feature context
  ├── Pass 2 (informed): map clusters to FPM gaps vs education gaps
  ├── Sources: community.fabric.microsoft.com (no rate limit), Pushshift/Reddit archives
  └── Pain Score v2: frequency × severity × max(product_gap_score, 0.5 × discovery_deficit)

Phase 4a OR 4b (gated by 0e decision):
  ├── 4a (FORK): FPM extension spec — new KQL functions, new report pages, new Eventstream sources
  │   ├── Windows Event Log collector (PowerShell → Eventstream)
  │   ├── REST API refresh history collector
  │   ├── VNet REST pull collector (separate architecture)
  │   └── Network metrics bridge (Azure Monitor VM → Eventstream)
  └── 4b (GREENFIELD): Delta medallion + PySpark + PowerShell (as currently described)
       └── Requires explicit bridge via Eventhouse OneLake availability if FPM data needed

Phase 5: Validation + Pilot (NEW — REQUIRED)
  ├── Deploy FPM baseline (2-minute jumpstart) as comparison baseline
  ├── Deploy Phase 4 artifact against same gateway
  ├── Confirm: does the build artifact cover all claimed gaps?
  ├── Performance test: DirectQuery over KQL vs Delta import (cost/latency tradeoff)
  └── Document known unknowns and open bugs before handoff
```

---

## 6. Leaner Alternative Path

If the goal is **decision-grade clarity** for an internal tool, rather than a publishable research corpus, the following path reaches the same answer in ~20% of the effort:

### Lean Path (3 days vs. 3–4 weeks)

**Day 1: FPM Baseline Audit**
1. Deploy FPM via `jumpstart.install("fpm-gateway-monitoring")` (2 minutes) against a real gateway.
2. Enumerate exactly what the six report pages show — and what they don't.
3. Document the 8–10 gaps in a simple table (see Missing Signals above).
4. Decide: are these gaps material to your specific monitoring use case?

**Day 2: Gap-Targeted Research**
- For each confirmed gap, do a targeted 2-3 source deep-dive.
- Scope: "Does any tool cover this?" not "What does the entire ecosystem look like?"
- VNet: deploy the VNet diagnostic download and manually inspect the Parquet schema.
- Refresh history: call `GET /datasets/{id}/refreshHistory` manually and inspect the payload.

**Day 3: Fork Spec or Descope Decision**
- If gaps are large: write a FPM fork spec covering only the confirmed gaps.
- If gaps are small: write a "FPM + 2 REST API pollers" integration spec.
- If the operator's actual pain is FPM's KQL-centric architecture (they need Delta/OneLake): confirm whether [Eventhouse OneLake availability](https://learn.microsoft.com/en-us/fabric/real-time-intelligence/event-house-onelake-availability) bridges the gap without a rewrite.

**What you skip:** The full funnel research (Phases 1–3), the quotas/kill-rules machinery, the pain-mining firewall methodology, and the full feature matrix. These add rigor appropriate for a publishable benchmark or product strategy document, but are disproportionate for an internal tooling decision.

**When the full funnel IS justified:** If the operator needs to justify the build decision to leadership, wants to publish the research, or genuinely suspects there are monitoring approaches not captured by FPM that could change the architecture. In that case, run the revised pipeline (Section 5), not the current one.

---

## 7. Summary Table: Issues vs. Severity vs. Fix

| Issue | Phase | Severity | Fix |
|---|---|---|---|
| No FPM build-vs-fork gate | 0, 4 | Critical | Add Phase 0e decision gate; gate Phase 4 on output |
| VNet architecture contradiction | 0, 4 | High | Separate VNet sub-architecture; use REST pull, not PS file reader |
| Phase 3 firewall blind spots | 3 | High | Two-pass approach: blind pass + informed reconciliation |
| Quota system forces padding | 1 | High | Replace hard quotas with coverage-saturation stop conditions |
| Untestable "working" starter code | 4 | High | Label as reference stubs; add Known Unknowns section |
| Missing signal: Windows Event Log | 0, 2 | Medium | Pre-seed as gap hypothesis in Phase 0 |
| Missing signal: network bandwidth | 0, 2 | Medium | Pre-seed; note this requires OS-level metrics, not gateway diagnostics |
| Missing signal: cluster load skew | 0, 2 | Medium | Add as Job-to-be-Done #7 |
| Missing signal: VNet Resource Consumption | 0, 2 | Medium | Add to VNet sub-scope; source: portal download |
| Missing signal: refresh history REST API | 0, 2 | Medium | Add to collector spec |
| No Phase 5 validation | — | Medium | Add pilot/validation phase gated on Phase 4 delivery |
| X/Twitter inaccessibility | 1, 3 | Medium | Budget or substitute; acknowledge in methodology |
| EvaluationContext identity field limitations | 2 | Medium | Document Paginated Reports + DataflowGen1 exclusions |
| SP 401 bug in Get-DataGatewayInfo | 4 | Medium | Document as known issue; require workaround in starter code |
| KQL vs Delta architecture mismatch | 4 | Medium | Resolve via fork path + Eventhouse OneLake availability bridge |
| No alerting layer in build spec | 4 | Low | Add Fabric Activator/Reflex to Phase 4 components |

---

*Critique prepared using verified facts from prior session research plus current web research (June 2026). All source URLs cited inline above. Gaps marked [Inference] where direct confirmation was not available.*
