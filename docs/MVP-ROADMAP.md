# MVP Roadmap

**Purpose:** the exhaustive, tracked task list from *where the repo is today* to a defensible **MVP**. Grounded in `PAIN-POINT-COVERAGE.md`, the `research/phase5_validation.md` U-matrix, and `LIVE-TENANT-FINDINGS.md`. Milestone tags (M1–M10) map to [GitHub milestones #1–#10](https://github.com/mattamundson/pbi-gateway-monitoring/milestones).

## MVP definition (acceptance)
An MVP exists when **all** of the following are true:
1. **Flagship (pain #3) is LIVE-verified** — a real `match_rate_pct` from `04_identity_match_rate.kql` on a real tenant.
2. **Core medallion runs end-to-end in a real Fabric tenant** (bronze → silver → gold) without hand-holding.
3. **Enhanced Nexus report renders on real data** (12 pages; tenant pages fed or gracefully empty).
4. **All load-bearing validation unknowns (U1–U19) are resolved or documented as known coverage gaps.**
5. **CI is green** across all three jobs.

## Status legend
🟢 built + proven (local) · 🟡 built + local-tested · 🟠 built + unverified (needs tenant) · ⚪ not built · ✅ done · ⏳ open

---

## Current baseline (2026-07-02)

**Pain-point scorecard** (`PAIN-POINT-COVERAGE.md`): 3 🟢 (#4 parser, #5 mashup, #6 fleet) · 1 🟡 (#9 spool) · 6 🟠 (#1 alerting, #2 triage, #3 flagship, #7 network, #8 setup, #10 cred drift).

**Already resolved (do NOT re-do):**
- ✅ **U17** — multi-line JSON ingest: all six pretty-printed `spark.read.json` calls in `01_bronze_ingest.py` already use `multiLine=True`; the one JSONL reader (mashup) correctly uses `multiLine=False`.
- ✅ **U18** — illegal `(ms)`/`(bytes)` column headers: `_sanitize_columns()` added; notebook routes through the sanitizing lib.
- ✅ **U15** — time-window join fan-out: nearest-match dedup (row_number by |Δt|, rank==1) on the **refresh**, **network**, and now **event-log** arms of `02_silver_correlate.py`, plus an explicit `output rows <= failed input` assertion. Tier-2 Spark test proves the dedup.
- ✅ Tenant-extract pipeline (`00_tenant_extract` → `01a_tenant_silver_gold` → `04_capacity_bridge`), `tenant_doctor` preflight, both Deploy orchestrators, the one-sitting `RUNBOOK-F2-pilot.md`, and the enhanced Nexus PBIP/TMDL report.

**The critical truth:** ~80% of remaining MVP work is gated on **one live F2 pilot**. The code exists and is locally tested; nothing has touched a real tenant.

---

## M1 — Live-tenant validation (CRITICAL PATH) 🟠→🟢
- [ ] Provision F2 pay-as-you-go capacity; run `docs/RUNBOOK-F2-pilot.md` end-to-end, pause after.
- [ ] **Track A — flagship number (U19 + U11):** run `04_identity_match_rate.kql` Block A (`match_rate_pct`), B (Refresh vs DirectQuery), C (unattributed sample).
- [ ] Confirm identity join key `OperationId == RequestId` holds live (U19); else fall back to U11 fuzzy time-window.
- [ ] **Track B — tenant extract live:** run `Deploy_TenantExtract` with a real admin-scoped SP; confirm `gold_inventory/activities/refreshables` populate.
- [ ] Confirm Workspace Monitoring Eventhouse provisions on **F2** (validate the F2-minimum claim from Session 3).
- [ ] Verify the enhanced Nexus report renders on real data — open PBIP in Desktop, fix DirectLake bindings, check all 12 pages.
- [ ] File the pilot-report issue (both tracks); update `PAIN-POINT-COVERAGE.md` 🟠→🟢 where proven.

## M2 — Data-ingestion correctness (U1, U2, U3, U7, U9, U10, U14) 🟠→🟢
- [ ] **U1/U2** — inspect 10 real `QueryStart` rows; confirm `EvaluationContext` encoding + `$.artifactId`/`$.artifactKind` keys resolve `artifact_id`.
- [ ] **U10** — force a new column; confirm `mergeSchema=true` append succeeds.
- [x] **U14** — `_cast_value` now returns `(value, cast_ok)`; unparseable numerics are quarantined into a per-row `_cast_errors` column (visible, not silently nulled). ✅ code-complete; live data confirms in M1.
- [ ] **U3** — confirm a comma-bearing `ErrorMessage` parses to the right field count (RFC-4180 reader).
- [ ] **U7** — confirm gold Delta tables are DirectLake-eligible (V-Order, supported types); OPTIMIZE/V-Order if DQ fallback is frequent.
- [ ] **U9** — confirm `LAKEHOUSE_PATH` ABFSS pattern resolves in a real workspace.

## M3 — Collector hardening (U4, U5, U8, U13, U16) 🟠→🟢
- [ ] **U16** — collectors read all host paths from `config.json`; discover the service account from the gateway service registration; never hardcode `PBIEgwService`.
- [ ] **U4** — verify the real datasource cmdlet name via `Get-Command -Module DataGateway`.
- [ ] **U5** — run inventory under the SP; if 401, confirm latest fabric-toolbox + gateway Admin role, or fall back to user-delegated / REST `GET /gateways`.
- [ ] **U8** — confirm the actual spool path for the gateway's service account.
- [ ] **U13** — check `StreamBeforeRequestCompletes`; correlate with spool behavior.
- [ ] Run every collector on a real Windows gateway host at least once; validate output schemas match parsers.

## M4 — Flagship & triage analytics (pain #2, #3, U11, U12) 🟠→🟢
- [ ] **Pain #2** — validate `03_diffpatterns_triage.kql` 3-way join with real refresh-history + event-log data; confirm failure-layer classification.
- [ ] **U12** — run a Paginated Report + Premium refresh; confirm absence in logs; add coverage disclaimer to report footer + attribution visuals.
- [ ] **U11** — ensure the match-rate visual is labeled best-effort (never "exact").

## M5 — Alerting (pain #1, U6) 🟠→🟢
- [ ] **U6** — build the gateway-offline rule in the real Activator UI from `alerting/activator-rules.json`; confirm it fires; correct the spec.
- [ ] Validate all 6 rules fire (offline, spool, credential drift, error-rate, network, heartbeat).
- [ ] Wire notification delivery (Teams webhook / email from config); confirm end-to-end.

## M6 — Capacity Metrics bridge / CU (tenant breadth) 🟠→🟢
- [x] `capacity_metrics_xmla` mode **implemented** via the Power BI REST executeQueries endpoint (DAX → normalized `gold_capacities`); tolerant column mapping. ✅ code-complete; needs a live Metrics model + Execute-Queries tenant setting to verify (M1).
- [ ] Confirm CU DAX column names in the live Capacity Metrics app (they vary by version).
- [ ] Verify CU measures + cards populate on the Tenant Overview page.

## M7 — Report finalization (Nexus enhanced report)
- [ ] Open the PBIP in Power BI Desktop; save a working `.pbix`; commit or document generation.
- [ ] Fix DirectLake bindings on all 13 tables; mark `Date` as a date table.
- [ ] Validate `queryRef`/`visualType` re-serialization by Desktop (R1–R5 caveats).
- [ ] Visually QA all 12 pages (text overflow, broken visuals, empty-state handling).
- [ ] Confirm the theme renders and the left status rail works as designed.

## M8 — Deployment & orchestration (pain #8) 🟠→🟢
- [ ] Validate `Deploy_GatewayMonitor.ipynb` in Fabric (Lakehouse + Eventhouse create, notebook import, pipeline schedule).
- [ ] Validate the bronze→silver→gold pipeline runs on schedule in the service.
- [ ] Test `Teardown_GatewayMonitor.ipynb` cleanly removes artifacts.
- [ ] Confirm `%run` chaining works in real Fabric for both Deploy orchestrators (importlib is the off-cluster fallback).

## M9 — Testing & CI expansion
- [x] Tenant Spark-tier test (`test_tenant_spark.py`) added to the JDK-17 `tier2-spark` job.
- [ ] Add regression tests for the U14 quarantine + U1/U2 encoding once implemented.
- [ ] Consolidate the remaining `01_bronze_ingest.py` readers onto `gateway_bronze_lib` (gateway path already done); test.
- [ ] Confirm the full CI matrix (tier1-parser, tier2-spark, tenant-harness) is green on a real PR.

## M10 — Documentation & release readiness
- [ ] Post-pilot: update `PAIN-POINT-COVERAGE.md` + `LIVE-TENANT-FINDINGS.md` with verified statuses.
- [ ] Add an "MVP status" section to the README (verified vs. open).
- [ ] Security pass: no secrets in samples, Key Vault as sole secret source, SP least-privilege documented.
- [ ] Tag `v0.1-mvp` once M1 (flagship live) + core medallion in-tenant are done.

---

## Sequencing recommendation
1. **M9 pre-work is done** (U15/U17 clean, tenant Spark test in CI) — the first live run won't be wasted debugging known issues.
2. **M1 is the unlock** — one F2 sitting resolves most 🟠 items and U11/U19 at once.
3. **M2/M3 fold into the same pilot** (validate ingestion + collectors on the real host during M1).
4. **M4–M8** are post-pilot hardening; **M10** closes the release.

**Honest bottom line:** the engineering is largely done and locally proven. MVP is now a *validation* problem, not a *build* problem — it needs a human to run M1 against a real tenant. The two suspected pre-pilot bugs (U15 fan-out, U17 multi-line) are **already resolved** in code, with the U15 dedup now covering all three join arms plus a fail-loud row-count assertion.
