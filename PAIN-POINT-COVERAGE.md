# Pain-Point Coverage — Honest Scorecard

Does this tool fix the 10 documented gateway pain points? Here is the truthful,
per-item status. Two axes: **is it built?** and **is it proven?**

**Status legend**
- 🟢 **PROVEN** — code exists AND is verified working (on real Spark, or by test).
- 🟡 **BUILT / LOCAL-TESTED** — code exists and runs on synthetic data; needs a live-tenant pilot to confirm against real gateway output.
- 🟠 **BUILT / UNPROVEN** — code exists but has not been executed against real or synthetic data in this environment; tenant pilot required.
- 🔴 **NOT ADDRESSED** — little or no implementation yet.
- ⚠️ **REGRESSION FOUND** — previously proven locally; live-tenant run exposed a real failure mode. Fix path documented.

> Verified locally with **PySpark 3.5.1 + JDK 17** via `starter/deploy/run_local_smoke.py`
> and `starter/tests/`. Live-tenant findings logged in `LIVE-TENANT-FINDINGS.md`.

---

## Scorecard

| # | Pain point | What addresses it | Status | Proof / gap |
|---|---|---|---|---|
| 4 | **PBIT breaks on gateway upgrade** | Schema-adaptive parser `gateway_bronze_lib.read_gateway_csv` (name-based, PERMISSIVE, mergeSchema) | ⚠️ **REGRESSION — live tenant** | Local Spark tests pass. **Live-tenant confirmed:** Fabric Load-to-Tables rejects `(ms)` / `(bytes)` column headers with `InvalidColumnName` before the parser layer runs. Fix: route through notebook path, not Load-to-Tables. See `LIVE-TENANT-FINDINGS.md` → 2026-07-01. |
| 5 | **Mashup memory/CPU bloat, no per-process visibility** | `Collect-MashupProcesses.ps1` + `read_mashup_processes` + `gold_mashup_health` (runaway detection) | 🟢 **PROVEN (local)** | Smoke test STEP 6: 40 samples → 9 gateway rollups → runaway containers correctly flagged. Real process-name patterns need host confirmation. |
| 6 | **No fleet / multi-gateway view** | `03_gold_aggregate.py` fleet rollup + load-skew CV | 🟢 **PROVEN (local)** | Gold aggregation runs on synthetic multi-gateway data; real multi-node CV needs tenant. |
| 9 | **Disk spooler surprises** | `Collect-DiskSpool.ps1` + `02_anomaly_forecast.kql` forecast + Activator disk rule | 🟡 **BUILT / LOCAL-TESTED** | Spool metrics flow through the medallion; proactive forecast is KQL, unproven in tenant. |
| 1 | **No real-time alerting** | `alerting/activator-rules.json` (6 rules incl. gateway-offline) | 🟠 **BUILT / UNPROVEN** | Activator DSL is `[Unverified]`; rules must be built + fired in a tenant. |
| 2 | **Opaque refresh-failure triage** | `03_diffpatterns_triage.kql` + silver 3-way join (gateway+eventlog+refresh) | 🟠 **BUILT / UNPROVEN** | Join logic designed; needs real refresh-history + event-log data to validate. |
| 3 | **No query→identity attribution (flagship)** | `01_identity_join.kql` (RequestId↔XmlaRequestId) + `add_artifact_identity` | 🟠 **BUILT / UNPROVEN — blocker confirmed** | **Live-tenant confirmed blocker:** trial Fabric Spark pool throttling prevents join execution. Needs paid F2+ capacity or narrow time-window workaround. See `LIVE-TENANT-FINDINGS.md` → 2026-07-01. |
| 7 | **No network bandwidth/latency metrics** | `Collect-NetworkMetrics.ps1` (NIC + latency); ETW deep path = v2 roadmap | 🟠 **BUILT / UNPROVEN** | Collector written; never run on a real host; per-query ETW correlation is roadmap. |
| 8 | **Manual, brittle setup** | `Deploy_GatewayMonitor.ipynb` + `Teardown_*` + config model | 🟠 **BUILT / UNPROVEN** | Deploy notebook `[Unverified]`; still non-trivial. Honest: partial improvement, not a clean fix yet. |
| 10 | **Credential state drift** | `Get-GatewayInventory.ps1` datasource-status + Activator credential rule | 🟠 **BUILT / UNPROVEN** | Uses the REST path no tool uses; cmdlet name `[Unverified]`. |

---

## Honest tally

| Status | Count | Pain points |
|---|---|---|
| 🟢 Proven (incl. local) | **2** | #5, #6 |
| ⚠️ Regression (live-tenant) | **1** | #4 — fix path documented |
| 🟡 Built + local-tested | **1** | #9 |
| 🟠 Built, needs tenant (blocker confirmed on #3) | **6** | #1, #2, #3, #7, #8, #10 |
| 🔴 Not addressed | **0** | — |

**Bottom line:** all 10 are *addressed by design*. Live-tenant session 1 (2026-07-01)
confirmed a real regression on #4 (Load-to-Tables `InvalidColumnName` on `(ms)`/`(bytes)`
headers — fix path: use notebook ingestion path, not Load-to-Tables) and confirmed that
#3 identity-join is blocked by trial Spark throttling (needs F2+ capacity).

## How the community moves items from 🟠 → 🟢
Run the pilot in your tenant and report results via the `pilot-report` issue template.
Each confirmed `[Unverified]` fact (EvaluationContext encoding, Workspace Monitoring
columns, `DataGateway` cmdlet names, Activator DSL, real gateway/mashup process names)
upgrades a row here. See `CONTRIBUTING.md`.

*Do not market this as "fixes all 10 pain points." The defensible claim is: "addresses
all 10 by design; proves the upgrade-resilient parser (#4) and per-process mashup
visibility (#5) outright on local Spark; live-tenant session exposed an ingestion-path
bug on #4 (documented fix) and a capacity blocker on #3."*

> Live-tenant findings → `LIVE-TENANT-FINDINGS.md`
