# Pain-Point Coverage — Honest Scorecard

Does this tool fix the 10 documented gateway pain points? Here is the truthful,
per-item status. Two axes: **is it built?** and **is it proven?**

**Status legend**
- 🟢 **PROVEN** — code exists AND is verified working (on real Spark, or by test).
- 🟡 **BUILT / LOCAL-TESTED** — code exists and runs on synthetic data; needs a live-tenant pilot to confirm against real gateway output.
- 🟠 **BUILT / UNPROVEN** — code exists but has not been executed against real or synthetic data in this environment; tenant pilot required.
- 🔴 **NOT ADDRESSED** — little or no implementation yet.
- ⚠️ **REGRESSION FOUND** — previously proven locally; live-tenant run exposed a real failure mode. Fix path documented.

> Verified locally with **PySpark 3.5.x + JDK 17** via `starter/deploy/run_local_smoke.py`
> and `starter/tests/` — including `test_medallion_spark.py` (Tier 2.5), which runs the
> notebooks' OWN `build_silver_*` / `build_gold_*` transforms on real Spark. "Local-tested"
> below means the transform logic is proven on synthetic/inline data, NOT against a live
> gateway. Live-tenant findings logged in `LIVE-TENANT-FINDINGS.md`.

---

## Scorecard

| # | Pain point | What addresses it | Status | Proof / gap |
|---|---|---|---|---|
| 4 | **PBIT breaks on gateway upgrade** | Schema-adaptive parser `gateway_bronze_lib.read_gateway_csv` (name-based, PERMISSIVE, `_sanitize_columns`, mergeSchema); **notebook `01_bronze_ingest.py` now routes through it** (`ingest_gateway_logs_via_lib`) | 🟢 **BUILT / PROVEN (local Spark) + notebook path fixed** | Parser proven on real Spark (unknown `(ms)`/`(bytes)` headers read clean, Parquet write succeeds). **Session 4:** closed the last gap — the notebook previously used an unprotected driver-side parser; it now calls the sanitizing lib first (driver parser = fallback). Fabric **Load-to-Tables** still fails at platform schema-inference before our code runs → procedural fix: use the notebook path. See `LIVE-TENANT-FINDINGS.md` → 2026-07-01 (regression) + 2026-07-02 Session 4 (notebook routing). |
| 5 | **Mashup memory/CPU bloat, no per-process visibility** | `Collect-MashupProcesses.ps1` + `read_mashup_processes` + `gold_mashup_health` (runaway detection) | 🟢 **PROVEN (local)** | Smoke test STEP 6: 40 samples → 9 gateway rollups → runaway containers correctly flagged. Real process-name patterns need host confirmation. |
| 6 | **No fleet / multi-gateway view** | `03_gold_aggregate.py` fleet rollup + load-skew CV | 🟢 **PROVEN (local)** | The REAL `build_gold_cluster_load` computes a non-null coefficient-of-variation on a 3-node cluster and `build_gold_gateway_health` assembles heartbeat+cpu+spool+network — both proven in `test_medallion_spark.py`. Real gateway output still needs a tenant. |
| 9 | **Disk spooler surprises** | `Collect-DiskSpool.ps1` + `02_anomaly_forecast.kql` forecast + Activator disk rule | 🟡 **BUILT / LOCAL-TESTED** | Spool metrics flow through the medallion; proactive forecast is KQL, unproven in tenant. |
| 1 | **No real-time alerting** | `alerting/activator-rules.json` (6 rules incl. gateway-offline) | 🟠 **BUILT / UNPROVEN** | Activator DSL is `[Unverified]`; rules must be built + fired in a tenant. |
| 2 | **Opaque refresh-failure triage** | `03_diffpatterns_triage.kql` + silver 3-way join (gateway+eventlog+refresh) | 🟡 **BUILT / LOCAL-TESTED (silver join)** | The REAL `build_silver_triage` is proven in `test_medallion_spark.py`: Level-1 `EXACT_REQUESTID`, Level-2 `TIME_WINDOW`, and `GATEWAY_ONLY` confidence tiers all fire, the **U15 fan-out is closed** (<=1 row per failed query), and `failure_layer` is derived. The KQL `diffpatterns` layer + real refresh/event data still need a tenant. |
| 3 | **No query→identity attribution (flagship)** | `01_identity_join.kql` (RequestId↔XmlaRequestId) + `add_artifact_identity`; **`04_identity_match_rate.kql`** (throttle-safe match-rate measurement, Blocks A/B/C) | Join **key**: `[Desk-Verified]` (MS docs). Silver **transform**: 🟡 **LOCAL-TESTED**. Live **join proof**: 🟠 **blocker confirmed** | The REAL `build_silver_identity_attribution` `EVALUATION_CONTEXT` vs `UNATTRIBUTED` split + disclaimer is proven in `test_medallion_spark.py`. The flagship join to a live Workspace Monitoring Eventhouse remains blocked: **live-tenant confirmed** — the Eventhouse fails to provision on trial capacity (and trial Fabric Spark throttling prevents execution); needs paid **F2+** or a narrow time-window workaround. **Session 4:** a dedicated match-rate query (`04_identity_match_rate.kql`) makes the first green run yield the U11 % directly, not just "rows returned." See `LIVE-TENANT-FINDINGS.md` → 2026-07-01 + 2026-07-02 Sessions 3–4. |
| 7 | **No network bandwidth/latency metrics** | `Collect-NetworkMetrics.ps1` (NIC + latency); ETW deep path = v2 roadmap | 🟡 **BUILT / LOCAL-TESTED (silver join)** | The REAL `build_silver_network_correlated` join is proven in `test_medallion_spark.py`, including the **U15 nearest-pick dedup** (exactly one NIC row per query even with >1 sample in the +/-5-min window). The PowerShell collector is still unrun on a real host; per-query ETW correlation is roadmap. |
| 8 | **Manual, brittle setup** | `Deploy_GatewayMonitor.ipynb` + `Teardown_*` + config model | 🟠 **BUILT / UNPROVEN** | Deploy notebook `[Unverified]`; still non-trivial. Honest: partial improvement, not a clean fix yet. |
| 10 | **Credential state drift** | `Get-GatewayInventory.ps1` datasource-status + Activator credential rule | 🟠 **BUILT / UNPROVEN** | Uses the REST path no tool uses; cmdlet name `[Unverified]`. |

---

## Honest tally

| Status | Count | Pain points |
|---|---|---|
| 🟢 Proven (incl. local) | **2** | #5, #6 |
| ⚠️ Regression (live-tenant) | **1** | #4 — fix path documented |
| 🟡 Built + local-tested | **3** | #9, #2, #7 (silver transforms now run on real Spark) |
| 🟠 Built, needs tenant (live join blocker on #3) | **4** | #1, #3, #8, #10 |
| 🔴 Not addressed | **0** | — |

**Bottom line:** all 10 are *addressed by design*. The Tier-2.5 medallion integration
test (`test_medallion_spark.py`) added the first coverage of the notebooks' OWN silver/gold
transforms, moving #2 (triage 3-way join, confidence tiers, U15 fan-out closure) and #7
(network correlation + U15 dedup) to local-tested, and hardening #6's cluster-load CV proof.
Live-tenant session 1 (2026-07-01) still stands: a real regression on #4 (Load-to-Tables
`InvalidColumnName` on `(ms)`/`(bytes)` headers — fix: use the notebook ingestion path) and
the #3 flagship identity-join blocked by trial Spark throttling (needs F2+ capacity).

## How the community moves items from 🟠 → 🟢
Run the pilot in your tenant and report results via the `pilot-report` issue template.
Each confirmed `[Unverified]` fact (EvaluationContext encoding, Workspace Monitoring
columns, `DataGateway` cmdlet names, Activator DSL, real gateway/mashup process names)
upgrades a row here. See `CONTRIBUTING.md`.

*Do not market this as "fixes all 10 pain points." The defensible claim is: "addresses
all 10 by design; proves the upgrade-resilient parser (#4), per-process mashup visibility
(#5), and — on real local Spark — the silver/gold transform logic behind triage (#2),
network correlation (#7), and fleet load-skew (#6), including the U15 fan-out closure.
Live-tenant session exposed an ingestion-path bug on #4 (documented fix) and a capacity
blocker on the #3 live join."*

> Live-tenant findings → `LIVE-TENANT-FINDINGS.md`
