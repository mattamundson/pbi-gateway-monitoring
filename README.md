# pbi-gateway-monitoring

**Fork-ready open-source accelerator for Fabric-native On-Premises Data Gateway observability — built on a two-pass evidence pipeline; pending live-tenant validation.**

> **Status:** Reference architecture + locally-validated starter kit. The schema-adaptive parser (pain #4) is proven on real Spark via the notebook ingestion path (Fabric's Load-to-Tables shortcut still fails at platform schema-inference — see [`LIVE-TENANT-FINDINGS.md`](LIVE-TENANT-FINDINGS.md)). All other capabilities are implemented and locally tested on synthetic data. Live-tenant validation is open to the community. This is not a certified one-click product yet.

> **New here / ready to test?** Start with the hand-holding pilot guide: [`docs/PILOT-GUIDE-START-HERE.md`](docs/PILOT-GUIDE-START-HERE.md) — a plain-English, step-by-step walkthrough to validate the flagship capability in ~30 minutes. Or see the condensed path: [`QUICKSTART.md`](QUICKSTART.md).

---

## What this is / is NOT

| This IS | This is NOT |
|---|---|
| An evidence-based, fork-ready accelerator with a full research pipeline | A certified, one-click product ready for production |
| A starter kit with collectors, a medallion pipeline, KQL analytics, and a report skeleton | A tool with end-to-end tenant-proven correctness (that needs you) |
| The only open-source kit that attempts query-identity attribution via `RequestId == XmlaRequestId` | A replacement for Microsoft FPM — it is complementary or standalone (your choice) |
| Honest about every gap: labeled `[Unverified]`, `[STUB]`, `[NET-NEW]`, `[ADAPTED]` | A product that claims to fix everything out of the box |
| Designed by a two-pass pain-point research pipeline with primary sources | Based on guesswork or copied features from existing tools |

---

## Who this is for

**You need all of the following:**

- A Microsoft Fabric + Azure tenant (F8+ capacity, or F2 Pay-As-You-Go)
- A deployed On-Premises Data Gateway (standard mode, Windows host) — a real gateway with real query traffic
- Fabric Workspace Admin + Gateway Admin roles
- A service principal registered in Entra ID with Key Vault backing
- Workspace Monitoring enabled (or willing to enable it) on a test workspace

**Not for:** VNet gateway operators (v2 roadmap item), personal-mode gateway users, or anyone on a Fabric 90-day trial tenant (Microsoft blocks Workspace Monitoring for the first 90 days on new tenants).

---

## The two things no existing tool does

These are the reason this repo exists. See the 10-tool survey in [`research/phase1_2_tools.md`](research/phase1_2_tools.md).

### 1. Query-identity attribution — `starter/kql/01_identity_join.kql`

The gateway `RequestId` is byte-identical to `XmlaRequestId`/`OperationId` in Fabric Workspace Monitoring. A single KQL join returns `ExecutingUser`, `DatasetId`, `ItemName`, and DAX text — the fields the gateway CSV permanently lacks. **This is the flagship differentiator.**

- Documented in [`docs/TECHNIQUE-query-identity-attribution.md`](docs/TECHNIQUE-query-identity-attribution.md)
- Desk-verified 2026-07-01 against [Microsoft's semantic model operations reference](https://learn.microsoft.com/en-us/fabric/enterprise/powerbi/semantic-model-operations) — the join key is stated verbatim in MS docs, not inferred from community posts
- **Status: join key `[Desk-Verified]`; live-tenant proof `[Built-Unverified]`** — the join key is confirmed in primary docs, but a first live-tenant attempt hit a trial-capacity Spark throttle before match rate could be measured (see [`LIVE-TENANT-FINDINGS.md`](LIVE-TENANT-FINDINGS.md)). Match rate remains the open question the community pilot resolves
- Residual scope: Dataflow Gen1 and Paginated Reports are confirmed-blocked by platform. DirectQuery-through-gateway is an open question (Chris Webb's analysis suggests it may work; Phase 5 tests this explicitly)

### 2. Per-query network cost — `starter/collectors/Collect-NetworkMetrics.ps1`

No gateway diagnostics tool surfaces network bandwidth or latency. This collector captures host-level NIC throughput + latency, time-window correlated to query activity.

- **Honest bound:** this is host-level NIC signal, not per-query byte attribution. True per-query network cost requires Windows ETW (`Microsoft-Windows-TCPIP`/`Kernel-Network` providers) — a researched `[Feasible-with-effort]` v2 roadmap item documented in [`research/frontier_instrumentation.md`](research/frontier_instrumentation.md)
- **Status: `[Built-Unverified]` — locally tested; needs tenant validation**

---

## Pain points addressed

All 10 operator pain points from [`research/phase3_painpoints.md`](research/phase3_painpoints.md) are addressed by design. Detailed evidence and status for each item: [`PAIN-POINT-COVERAGE.md`](PAIN-POINT-COVERAGE.md).

| # | Pain point | Artifact | Honest status |
|---|---|---|---|
| 1 | No real-time gateway-offline alerting | `starter/alerting/activator-rules.json` + KQL | `[Built-Unverified]` |
| 2 | Opaque refresh failures — can't triage gateway vs. source vs. network | `Collect-RefreshHistory.ps1` (Service-side leg) + silver correlate triage join + `starter/kql/03_diffpatterns_triage.kql` | `[Built-Unverified]` |
| 3 | Zero query attribution — no dataset/user/report per query | `starter/kql/01_identity_join.kql` | Join **key**: `[Desk-Verified]`. Live **proof**: `[Built-Unverified]` — a first live-tenant attempt hit a trial-capacity Spark throttle before match rate could be measured. **Live-tenant match rate is still the community pilot goal.** See [`LIVE-TENANT-FINDINGS.md`](LIVE-TENANT-FINDINGS.md) |
| 4 | Gateway Performance PBIT breaks on log schema drift | `starter/notebooks/01_bronze_ingest.py` (schema-adaptive, column-name-based) | **PROVEN on real Spark 3.5.1** via the notebook ingestion path — Fabric's Load-to-Tables shortcut still fails at platform schema-inference on the raw CSV; see [`LIVE-TENANT-FINDINGS.md`](LIVE-TENANT-FINDINGS.md) |
| 5 | Mashup engine memory/CPU bloat with no per-process visibility | `Collect-MashupProcesses.ps1` + `gold_mashup_health` | **PROVEN (local Spark, runaway detection)**  |
| 6 | No multi-gateway / fleet view | `starter/kql/` gold aggregation + inventory collector | `[Built-Local-Tested]` |
| 7 | No gateway-native network bandwidth metrics | `starter/collectors/Collect-NetworkMetrics.ps1` | `[Built-Unverified]` |
| 8 | Manual, brittle monitoring setup | `starter/deploy/Deploy_GatewayMonitor.ipynb` + config scaffold | `[Built-Unverified]` |
| 9 | Disk spooler surprises — no proactive disk alerting | `starter/collectors/Collect-DiskSpool.ps1` + Activator rule | `[Built-Local-Tested]` |
| 10 | Credential/datasource state drift, silent breakage | `starter/kql/` credential-health query + Activator rule | `[Built-Unverified]` |

**Key:** PROVEN = tested on real Spark against real log schema; Built-Local-Tested = runs end-to-end on synthetic data; Built-Unverified = implemented, needs live tenant; Desk-Verified = confirmed in primary docs, needs live-tenant run.

---

## Prerequisites

Before forking and deploying, confirm you have:

- **Fabric capacity:** F8+ (recommended) or F2 Pay-As-You-Go. The F64 trial SKU works; the 90-day new-tenant Fabric trial does **not** support Workspace Monitoring for the first 90 days.
- **Gateway:** A deployed On-Premises Data Gateway (standard mode) with active query traffic. No gateway = no logs to analyze.
- **Gateway Admin role** on the gateway cluster.
- **Fabric Workspace Admin** role on at least one workspace.
- **Service principal** registered in Entra ID, with a client secret stored in Azure Key Vault.
- **Workspace Monitoring enabled** on your test workspace (or willingness to enable it — this is the prerequisite for identity attribution).
- **PowerShell 7** on each gateway host.

See [`starter/config/credentials.md`](starter/config/credentials.md) for the full credential model.

---

## Quickstart — fork, then validate in ~30 min

**Step 1 — Choose your deploy path** (read [`docs/DEPLOYMENT-DECISION.md`](docs/DEPLOYMENT-DECISION.md)):

| Path | When to use |
|---|---|
| **Adopt-FPM** | You already run Microsoft's Fabric Platform Monitoring; add this kit's KQL ceiling-breakers on top |
| **Build-new** | You want a fully standalone Fabric-native pipeline (collectors → OneLake → medallion → KQL → report) |
| **Hybrid** | You run FPM for heartbeat/basic metrics; use this kit for identity attribution and anomaly KQL only |

**Step 2 — Deploy collectors** to each gateway node (see [`starter/deploy/Deploy_GatewayMonitor.ipynb`](starter/deploy/Deploy_GatewayMonitor.ipynb)).

**Step 3 — Run the medallion:** `starter/notebooks/01_bronze_ingest.py` → `02_silver_correlate.py` → `03_gold_aggregate.py`.

**Step 4 — Enable Workspace Monitoring** on your test workspace, then run `starter/kql/01_identity_join.kql`. This is the flagship test.

**Step 5 — Report the result** via the [pilot-report issue template](.github/ISSUE_TEMPLATE/pilot-report.yml).

Full hand-holding walkthrough: [`docs/PILOT-GUIDE-START-HERE.md`](docs/PILOT-GUIDE-START-HERE.md). Condensed 30-min version: [`QUICKSTART.md`](QUICKSTART.md).

---

## Repository map

```
pbi-gateway-monitoring/
├── README.md                          ← this file
├── QUICKSTART.md                      ← condensed 30-min pilot
├── CONTRIBUTING.md                    ← how to validate, report, and contribute
├── DECISIONS.md                       ← why the repo is shaped this way
├── PRODUCTIZATION.md                  ← honest gap-to-market plan
├── PAIN-POINT-COVERAGE.md             ← detailed per-pain-point scorecard
│
├── docs/
│   ├── PILOT-GUIDE-START-HERE.md      ← full hand-holding walkthrough
│   ├── DEPLOYMENT-DECISION.md         ← adopt-FPM vs. build-new vs. hybrid
│   ├── TECHNIQUE-query-identity-attribution.md  ← shareable technique write-up
│   ├── FPM-Phase0-Deployment-Reference.md
│   └── gateway-monitoring-runbook.md
│
├── research/                          ← the full evidence pipeline (read to understand design)
│   ├── pipeline_critique.md           ← methodology critique that reshaped the plan
│   ├── phase0_scope.md                ← scope, signal map, build-vs-fork decision
│   ├── phase1_2_tools.md              ← top-10 tool survey + best-of-breed analysis
│   ├── phase3_painpoints.md           ← top-10 operator pain points (two-pass mined)
│   ├── phase4_dossier.md              ← decision dossier + What-NOT-to-build
│   ├── phase4_architecture.md         ← full build spec + traceability + Known Unknowns
│   ├── phase5_validation.md           ← live-environment pilot runbook (U1–U16)
│   ├── phase6_northstar.md            ← state-of-the-art roadmap (v1→v6)
│   ├── frontier_instrumentation.md    ← ETW/identity-join deep dive
│   └── frontier_intelligence.md      ← AIOps: anomaly, forecast, self-healing
│
├── starter/
│   ├── collectors/                    ← 7 PowerShell collectors
│   │   ├── Collect-GatewayLogs.ps1   ← primary log upload
│   │   ├── Collect-NetworkMetrics.ps1 ← NIC throughput + latency [Built-Unverified]
│   │   ├── Collect-EventLog.ps1       ← Windows Event Log
│   │   ├── Collect-DiskSpool.ps1      ← disk free-space + spool trend
│   │   ├── Collect-MashupProcesses.ps1← per-PID Mashup memory/CPU [Built-Unverified]
│   │   ├── Collect-RefreshHistory.ps1 ← Power BI Service refresh history (triage #2) [Built-Unverified]
│   │   └── Get-GatewayInventory.ps1   ← gateway/datasource inventory
│   ├── notebooks/                     ← PySpark medallion (bronze→silver→gold)
│   ├── kql/                           ← ceiling-breaker KQL queries
│   │   ├── 01_identity_join.kql       ← THE flagship — attribution
│   │   ├── 02_anomaly_forecast.kql    ← predictive saturation
│   │   └── 03_diffpatterns_triage.kql ← failure attribution
│   ├── alerting/                      ← Activator rules (JSON + narrative)
│   ├── semantic-model/measures.dax    ← DAX measures
│   ├── report/                        ← PBIP report skeleton
│   ├── config/                        ← config + credential model
│   ├── deploy/                        ← one-click deploy + teardown notebooks
│   └── tests/                         ← two-tier test suite (pure-Python + Spark)
│
└── scripts/                           ← FPM node deploy + validate + lint (adopt-FPM path)
```

---

## How you can help — validate this kit in your tenant

**The single most valuable contribution is running the identity-join test (Task 1 in [`QUICKSTART.md`](QUICKSTART.md)) and filing a pilot-report issue.**

Every `[Unverified]` label in this repo is a question only someone with a live Fabric tenant + gateway can answer. Specifically, the community can unblock:

1. **Identity join match rate** — does `RequestId == OperationId` yield `ExecutingUser` in your tenant? What % of queries are covered? Does it extend to DirectQuery?
2. **EvaluationContext encoding** — is the `EvaluationContext` column in `QueryStartReport` raw JSON or base64 in your gateway version?
3. **Workspace Monitoring column names** — do the column names in `PowerBIDatasetsWorkspace` match what the KQL queries assume?
4. **DataGateway cmdlet names** — does `Get-Command -Module DataGateway` list the cmdlets the collectors expect?
5. **Activator DSL** — do the alerting rules in `starter/alerting/activator-rules.json` match the current Activator syntax?
6. **Collector fixes for your gateway version** — does `Collect-GatewayLogs.ps1` find the right log path on your service account + OS combination?

**To report:** use the [pilot-report issue template](.github/ISSUE_TEMPLATE/pilot-report.yml). You don't need to run everything — Task 1 alone is hugely valuable. See [`CONTRIBUTING.md`](CONTRIBUTING.md) for full contribution guidance.

---

## Honesty charter

This repo uses explicit status labels on every claim and every code file.

| Label | Meaning |
|---|---|
| `[Feasible-now]` | Technically sound; confirmed achievable without gateway modification |
| `[Desk-Verified]` | Confirmed in Microsoft primary docs; not yet run in a live tenant |
| `[Built-Local-Tested]` | Implemented; runs end-to-end on synthetic data in local Spark |
| `[Built-Unverified]` | Implemented; needs live-tenant run to confirm |
| `[Feasible-with-effort]` | Researched and designed; significant additional build work required |
| `[Experimental]` | Research-stage; not yet fully designed |
| `[Blocked-by-platform]` | Microsoft platform constraint — cannot be solved without platform change |
| `[STUB]` | Code file is a scaffold; replace with real implementation after tenant test |
| `[NET-NEW]` | Original logic not adapted from another tool |
| `[ADAPTED]` | Adapted from a named upstream source |

This table is the **canonical** label set for the whole repo. `CONTRIBUTING.md` points here
rather than redefining its own set. `PAIN-POINT-COVERAGE.md` and `LIVE-TENANT-FINDINGS.md` use
a parallel 🟢/🟡/🟠/🔴/⚠️ emoji shorthand for at-a-glance scanning — the equivalence is:

| Emoji | Meaning | Closest charter label |
|---|---|---|
| 🟢 PROVEN | Code exists and is verified working (real Spark, or a passing test) | `[Built-Local-Tested]` (or `[Desk-Verified]` + tenant-confirmed, for live proof) |
| 🟡 BUILT / LOCAL-TESTED | Code exists, runs end-to-end on synthetic data | `[Built-Local-Tested]` |
| 🟠 BUILT / UNPROVEN | Implemented, not yet executed against real or synthetic data here | `[Built-Unverified]` |
| 🔴 NOT ADDRESSED | Little or no implementation yet | *(no charter label — absence of implementation, not a confirmation claim)* |
| ⚠️ REGRESSION FOUND | Previously proven locally; a live-tenant run exposed a real failure | *(no charter label — see the linked `LIVE-TENANT-FINDINGS.md` entry for the fix path)* |

**What this repo does NOT claim:**
- Full autonomy or self-healing operation
- eBPF-on-Windows (blocked in 2026; ETW is the correct path — see [`research/frontier_instrumentation.md`](research/frontier_instrumentation.md))
- Exact per-DirectQuery-user attribution (open question, not confirmed)
- Per-OPDG CU billing breakdown
- "Works first try" — the kit needs at least one tenant run to fix the `[Unverified]` items
- That all 10 pain points are "fixed" — they are addressed by design; #4 is proven; the rest are implemented and locally tested, pending community validation

---

## Known gaps (internal testers read this)

One known follow-up remains. Flagging it for transparency, not asking you to fix it
during the pilot:

1. **`01_bronze_ingest.py` keeps its own parser instead of importing `gateway_bronze_lib`.**
   `starter/notebooks/gateway_bronze_lib.py` holds the schema-adaptive `read_gateway_csv()` +
   `_sanitize_columns()` logic referenced elsewhere in this repo (including the Pain #4 fix in
   [`LIVE-TENANT-FINDINGS.md`](LIVE-TENANT-FINDINGS.md)), but `01_bronze_ingest.py`'s own
   `ingest_gateway_logs()` has a separate, hand-rolled parsing path rather than importing that
   library. Both are locally tested; they just haven't been consolidated. (The notebook header
   now states this honestly instead of claiming the lib is the primary path.)

**Resolved 2026-07-02:** mashup-process + gateway-inventory `Datasources` ingestion is now
wired into `01_bronze_ingest.py` — new `ingest_mashup_processes()` (→ `bronze_mashup_processes`,
Pain #5) and `ingest_gateway_datasources()` (→ `bronze_gateway_datasources`, the credential-drift
signal, Pain #10), both called from the entry point. Their transforms mirror the tested
`gateway_bronze_lib.read_mashup_processes` and the existing explode-and-select pattern; like the
rest of this notebook they are **[Unverified] in live Fabric** until the pilot run.

---

## License

Proprietary and confidential — internal evaluation and use only. All rights
reserved. Not licensed for public distribution. See [LICENSE](LICENSE).

This repo was designed via an evidence-based research pipeline. Read [`research/`](research/) to follow the full methodology, and [`DECISIONS.md`](DECISIONS.md) to understand why each architectural choice was made.
