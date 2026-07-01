# pbi-gateway-monitoring

An open-source, Fabric-native monitoring tool for the **Power BI / Microsoft Fabric On-Premises Data Gateway** — plus the full evidence-bound research that designed it.

Anyone with a Fabric + Azure tenant-admin role can fork this repo and stand up gateway observability that goes beyond what any existing tool (including Microsoft's Fabric Platform Monitoring) offers today.

> **Status:** Reference implementation. All code is `[Unverified]` against a live gateway until the Phase 5 pilot is run. It compiles and its pure-Python logic is unit-tested; treat it as a well-grounded starting point, not production-certified.

---

> **New here / ready to test it? Start with the hand-holding pilot guide:** [`docs/PILOT-GUIDE-START-HERE.md`](docs/PILOT-GUIDE-START-HERE.md) — a plain-English, step-by-step walkthrough to validate the tool's flagship capability in ~30 minutes.

## Why this exists

Two capabilities every gateway operator wants, that **no existing tool provides**, are the reason to build:

1. **Query → identity attribution** — know *which dataset and which user* caused a slow/failed gateway query. Long believed impossible; **it isn't** (see below).
2. **Per-query network cost** — the #1 real bottleneck, invisible to gateway diagnostics; recoverable via Windows ETW.

Both are breakable **without modifying Microsoft's gateway**, grounded in primary sources.

---

## Repository map

```
├── README.md                      ← this file
├── docs/                          ← operational deployment kit (FPM path)
│   ├── gateway-monitoring-runbook.md
│   └── FPM-Phase0-Deployment-Reference.md
├── scripts/                       ← FPM node deploy + validate + lint
│   ├── Deploy-FpmGatewayNode.ps1
│   ├── Test-FpmGatewayNode.ps1
│   └── lint.ps1
├── research/                      ← the full design pipeline (evidence-bound)
│   ├── pipeline_critique.md       ← methodology critique that reshaped the plan
│   ├── phase0_scope.md            ← scope, signal map, build-vs-fork gate
│   ├── phase1_2_tools.md          ← top-10 tools + best-of-breed shopping list
│   ├── phase3_painpoints.md       ← top-10 operator pain points (two-pass mined)
│   ├── phase4_dossier.md          ← decision dossier + What-NOT-to-build
│   ├── phase4_architecture.md     ← full build spec + traceability + Known Unknowns
│   ├── phase5_validation.md       ← live-env pilot runbook (U1–U13 + acceptance tests)
│   ├── phase6_northstar.md        ← state-of-the-art evolution (v1→v6)
│   ├── frontier_instrumentation.md← OTel/ETW/identity-join deep dive
│   └── frontier_intelligence.md   ← AIOps: anomaly/forecast/Data Agent/self-healing
└── starter/                       ← the build-new tool scaffold
    ├── collectors/                ← 5 PowerShell collectors (logs, network, event log, disk, inventory)
    ├── notebooks/                 ← PySpark medallion (schema-adaptive bronze → silver → gold)
    ├── kql/                       ← v2 ceiling-breakers (identity join, anomaly/forecast, diffpatterns)
    ├── semantic-model/measures.dax
    ├── alerting/activator-rules.md
    └── config/                    ← config + credential model
```

---

## The two ceiling-breakers (start here — highest value)

### 1. Identity attribution — `starter/kql/01_identity_join.kql`
The gateway `RequestId` is byte-identical to `XmlaRequestId`/`OperationId` in Fabric **Workspace Monitoring**. A KQL join returns `ExecutingUser`, `DatasetId`, `ItemName`, and DAX text — the fields the gateway CSV lacks. `[Feasible-now]`, no gateway changes.
Sources: [MS semantic model operations](https://learn.microsoft.com/en-us/fabric/enterprise/powerbi/semantic-model-operations), [Fabric CAT/Chris Webb](https://blog.crossjoin.co.uk/2024/09/01/finding-power-bi-semantic-model-refresh-operations-in-gateway-logs/).
Residual `[Blocked-by-platform]`: per-DirectQuery UserId, Dataflow Gen1, Paginated Reports.

### 2. Predictive intelligence — `starter/kql/02_anomaly_forecast.kql`
`series_decompose_anomalies` + `series_decompose_forecast` turn static thresholds into "this gateway saturates in ~2h." `starter/kql/03_diffpatterns_triage.kql` auto-attributes failure spikes to their top-offender dimensions. All native KQL, `[Feasible-now]`.

---

## Deploy order

1. Read `research/phase6_northstar.md` §7 (the 3 immediate actions) and `research/phase4_architecture.md`.
2. Stand up a Fabric Lakehouse + Eventhouse; deploy `starter/collectors/*.ps1` to each gateway node (see `starter/config/credentials.md`).
3. Run `starter/notebooks/01→02→03` (bronze→silver→gold).
4. **Enable Workspace Monitoring**, then run `starter/kql/01_identity_join.kql` — this is the flagship capability.
5. Add `starter/kql/02`/`03` queries + `starter/alerting/activator-rules.md`.
6. Execute `research/phase5_validation.md` end-to-end to graduate from reference to production.

*(The `docs/` + `scripts/` kit is the alternative/complementary path for teams adopting Microsoft's FPM directly.)*

---

## Honesty charter

This repo labels every claim: `[Feasible-now]` / `[Feasible-with-effort]` / `[Experimental]` / `[Blocked-by-platform]` / `[Unverified]`, and every code file as `[STUB]` / `[ADAPTED-FROM-*]` / `[NET-NEW]`. It does **not** claim: full autonomy, eBPF-on-Windows, exact per-DirectQuery user attribution, or per-OPDG CU billing. What it can't do yet, it says so.

## License
MIT — see [LICENSE](LICENSE).
