# MVP Roadmap — pbi-gateway-monitoring

> Tracked checklist from current state to a working MVP. Owners: **[Amo]** (tenant / host /
> spend / GUI), **[me]** (headless engineering), **[me→Amo]** (I scaffold, you run authenticated).
> Legend: `[x]` done · `[ ]` todo · **GATED** = blocked by Phase 1 (F2 + admin-API grant).

**MVP definition.** The tool deployed to a real Fabric tenant on an F2 capacity, ingesting real
telemetry from >=1 live gateway through the validated medallion into a published DirectLake report,
with the gateway-offline alert firing and the flagship query->identity attribution (D6) confirmed on
real data — i.e. the top pain points proven end-to-end, not just locally.

---

## Phase 0 — Land current work (near-done; Amo keystroke)
- [x] PR #2 (report: 14 pages, `.pbit`, PBIP, binding validator) — CLEAN / mergeable
- [x] PR #1 (medallion Tier-2.5 verify + Databricks capture) — conflict resolved / mergeable
- [ ] **[Amo]** `gh pr merge 2 --merge`
- [ ] **[Amo]** `gh pr merge 1 --merge` (once its re-run CI is green)
- [ ] **[me]** Post-merge: delete stale worktrees/branches; confirm `main` clean; confirm the two loops run on merged `main`
- [ ] **[me]** Confirm the reports-loop binding brake runs live (skips until PR #2 lands on `main`)

## Phase 1 — Tenant enablement (THE master gate — everything live is downstream)
- [ ] **[Amo]** Provision an **F2+ Fabric capacity** and assign it to the workspace
- [ ] **[Amo]** Enable **Workspace Monitoring** (Eventhouse) — trial refused it; F2 unblocks (required for D6)
- [ ] **[Amo]** Register the **service principal** + enable *Tenant settings -> "Service principals can access read-only admin APIs"* + admin consent  *(script: `starter/deploy/register-spn-admin-api.ps1`)*
- [ ] **[Amo]** Re-run `starter/notebooks/tenant_doctor.py` — confirm 401 / 0-admin-rows clears
- [ ] **[Amo]** Decide capacity sizing + cost ceiling

## Phase 2 — Semantic model + report finalization (Desktop)
- [x] Branded 14-page PBIR report, DirectLake `.pbit`, one-click PBIP, binding validator (PASS)
- [x] **[me]** `dim_date` authored as a calculated CALENDAR date table (time-intelligence works on open)
- [ ] **[Amo]** Open `starter/report/pbip/gwmon.pbip` in Power BI Desktop
- [ ] **[Amo]** Repoint `DirectLakeSource` M expression -> real Lakehouse SQL endpoint
- [ ] **[Amo]** Confirm `dim_date` mark-as-date-table + relationships resolve
- [ ] **[Amo]** Verify all 120 visuals **render** (no headless check can prove this)
- [ ] **[Amo]** Confirm every measure evaluates (validator proved names resolve; Desktop proves DAX)
- [ ] **[Amo]** *Save As `.pbix`*; publish report + model to workspace; confirm DirectLake refresh

## Phase 3 — Collector deployment on a real gateway host (all 7 `[Unverified]` — never run) — **GATED**
- [ ] **[Amo]** Identify a pilot on-prem gateway host with PowerShell access
- [ ] **[Amo]** Run + validate + schedule each collector:
  - [ ] `Collect-GatewayLogs.ps1` (query exec/errors — pain #4/#2/#3)
  - [ ] `Get-GatewayInventory.ps1` (cluster/node/version/datasource — pain #6/#10; cmdlet names `[Unverified]`)
  - [ ] `Collect-DiskSpool.ps1` (pain #9)
  - [ ] `Collect-MashupProcesses.ps1` (pain #5)
  - [ ] `Collect-NetworkMetrics.ps1` (pain #7)
  - [ ] `Collect-EventLog.ps1` (triage Level-3 OS correlation)
  - [ ] `Collect-RefreshHistory.ps1` (refresh-status for triage join)
- [ ] **[Amo]** Confirm collectors' **real output headers** vs parser expectations (the `(ms)`/`(bytes)` issue — pain #4)
- [ ] **[me]** Reconcile any real-vs-assumed column drift into `gateway_bronze_lib` + medallion schema + `.pbit` model (validator catches mismatches)
- [ ] **[Amo]** Establish landing path (PS -> OneLake `Files/bronze_landing`) + cadence

## Phase 4 — Medallion live run + validation — **GATED**
- [x] `build_silver_*` / `build_gold_*` proven locally (Tier 2.5)
- [ ] **[Amo]** Run `01_bronze_ingest.py` on real data via the **notebook path** (NOT Fabric Load-to-Tables — pain #4)
- [ ] **[Amo]** Run `02_silver_correlate.py` + `03_gold_aggregate.py`; confirm 4 silver + 4 gold Delta tables
- [ ] **[Amo]** Validate **U15 fan-out closure** on real data (<=1 row per failed query / NIC window)
- [ ] **[Amo]** Confirm `gold_dim_gateway` **DeltaTable SCD-2 merge** in-tenant (local tests use parquet — coverage gap)
- [ ] **[Amo]** Schedule the pipeline (Fabric Data Pipeline / notebook scheduler) for incremental loads

## Phase 5 — Flagship: query->identity attribution live proof (D6 — "the one gate that matters") — **GATED**
- [ ] **[Amo]** Run `starter/kql/PILOT-identity-join-test.kql` — confirm `RequestId == XmlaRequestId/OperationId` returns rows
- [ ] **[Amo]** Run `04_identity_match_rate.kql` (Blocks A/B/C) — get the U11 attribution-match %
- [ ] **[Amo]** Confirm Workspace Monitoring column names match join assumptions (`[Unverified]`)
- [ ] **[Amo]** Validate `EVALUATION_CONTEXT` vs `UNATTRIBUTED` split + disclaimer on real sessions
- [ ] **[me]** Fold match-rate into Attribution Rate % measure; flip pain #3 `[Unverified] -> [Proven]`

## Phase 6 — Alerting live (pain #1; Activator rules never fired) — **GATED**
- [ ] **[Amo]** Build the 6 `activator-rules.json` rules as real Data Activator items
- [ ] **[Amo]** Fire + confirm the **gateway-offline** rule (Heartbeat Age > threshold) — core MVP alert
- [ ] **[Amo]** Confirm disk-spool + credential-drift rules trigger on real thresholds
- [ ] **[me→Amo]** Wire alert destinations (Teams/email); validate Activator DSL (`[Unverified]`)

## Phase 7 — Deployment / setup productization (pain #8) — partly GATED
- [ ] **[Amo]** Run `Deploy_GatewayMonitor.ipynb` end-to-end in a clean workspace (`[Unverified]`)
- [ ] **[Amo]** Verify `Teardown_GatewayMonitor.ipynb` removes everything cleanly
- [ ] **[me]** Finalize `starter/config/config.sample.json` + `credentials.md` with the real pilot params
- [ ] **[me]** Validate `export_deployment_bundle.py` produces a portable install package
- [ ] **[me→Amo]** Decide notebook-deploy vs Fabric Git Integration as canonical install (`docs/DEPLOYMENT-DECISION.md`)

## Phase 8 — Documentation / operator onboarding
- [ ] **[me]** Flip `PAIN-POINT-COVERAGE.md` labels as each pain point proves in-tenant
- [ ] **[me]** Fill `docs/gateway-monitoring-runbook.md` with the real deploy/operate/troubleshoot flow
- [ ] **[me]** Write operator quickstart (collector install -> schedule -> open report) into `QUICKSTART.md`
- [ ] **[me]** Record pilot results into `LIVE-TENANT-FINDINGS.md` + a session log
- [ ] **[me]** Add the `.pbit`/PBIP/validator workflow to top-level `README.md`

## Phase 9 — Observability / CI / loop hardening
- [x] CI Tier 1/2/2.5 green; two Loopify loops live; binding-validation brake wired into reports loop
- [x] **[me]** CI job running `validate_report_bindings.py` on every PR (`.github/workflows/report-bindings.yml`)
- [ ] **[me]** Add a `.pbit` rebuild + parse check to CI (regression-guard the model artifact)
- [ ] **[me]** Vendor/pin `pbi-tools.core` for reproducible off-machine builds
- [ ] **[Amo]** Confirm GitHub Actions billing headroom for added jobs (configured safe)
- [ ] **[me]** Add a north-star scorecard run to the reports loop once tenant data exists

## Phase 10 — Open decisions to close
- [ ] **[Amo/me]** **D10 Databricks-vs-Fabric compute** (in PR #1; lands on `main`'s DECISIONS.md at merge) — confirm Fabric-native MVP path, Databricks deferred
- [ ] **[Amo]** VNet-gateway support (D5) — confirm out-of-scope for MVP (v2)
- [ ] **[me]** Reconcile reconstructed `starter/databricks/` coarse join vs canonical D6 `RequestId` join (D6 authoritative)

---

## MVP Definition of Done (acceptance gate)
- [ ] Real gateway telemetry flowing: >=1 collector scheduled on a live host -> OneLake bronze
- [ ] Medallion runs in-tenant on real data; all 8 silver+gold tables materialize; U15 fan-out holds
- [ ] Published DirectLake report renders all core pages with live data
- [ ] Gateway-offline alert fires on a real heartbeat gap
- [ ] D6 identity attribution confirmed with a measured match-rate % on real sessions
- [ ] One-click deploy + teardown proven in a clean workspace
- [ ] >=6 of 10 pain points at `[Proven]` (now: 2 proven-local, 3 local-tested, 4 tenant-gated)
- [ ] Operator can install + operate from docs without the author

**Critical path:** Phases 3–6 are hard-blocked by Phase 1 (F2 + admin-API grant). The single
highest-leverage action is provisioning the F2 capacity + SPN grant — it converts ~25 gated tasks
into doable ones. Headless slices of Phases 2/8/9 need no tenant.
