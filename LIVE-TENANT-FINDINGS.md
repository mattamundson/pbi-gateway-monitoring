# Live-Tenant Findings Log

Field-confirmed observations from real Fabric tenant runs.
Each entry is dated, tagged to the relevant pain point, and classified as
**CONFIRMED BUG**, **CONFIRMED BLOCKER**, **CONFIRMED WORKING**, or **OBSERVATION**.

---

## 2026-07-01 — Session 1

**Environment:** Trial Fabric tenant (Spark throttling active); On-premises data gateway CSV export via Power BI admin portal.

---

### Pain #4 — CONFIRMED BUG: `InvalidColumnName` on `(ms)` / `(bytes)` gateway CSV headers

**Status upgrade:** 🟢 PROVEN (local) → 🔴 **BROKEN ON REAL TENANT DATA** (see fix path below)

**Observation:**
Fabric's **Load-to-Tables** (the "Load data" shortcut that ingests a CSV directly into a
Lakehouse table via the Fabric pipeline / notebook cell path) rejects the gateway performance
CSV with:

```
AnalysisException: InvalidColumnName
```

Root cause: The gateway export includes column headers that contain parenthetical unit
suffixes — specifically `(ms)` (millisecond columns such as `QueryExecutionDuration(ms)`,
`GatewayTotalDuration(ms)`) and `(bytes)` (e.g. `DataTransferSize(bytes)`). Spark's
column name parser rejects `(` and `)` as illegal characters in column identifiers.

**Why this matters:**
Our schema-adaptive parser `gateway_bronze_lib.read_gateway_csv` uses `PERMISSIVE` mode
and name-based column mapping — but that layer only runs *after* the DataFrame schema is
parsed. Load-to-Tables invokes a different Fabric ingestion code path that fails at schema
inference before our parser even loads. This is a Fabric platform behavior, not a Python bug.

**Affected columns (confirmed):**
- All `*Duration(ms)` columns
- All `*Size(bytes)` columns
- Likely any future columns with `(`, `)`, `/`, or space in the name

**Recommended fix path:**
1. Do **not** use Fabric Load-to-Tables for the raw gateway CSV. Route through the existing
   `starter/deploy/Deploy_GatewayMonitor.ipynb` notebook path which calls
   `read_gateway_csv` — that layer sanitizes column names via `_sanitize_columns()`
   (proposed fix here — implemented in Session 2 below) before materializing the schema.
2. If you must use Load-to-Tables, pre-process the CSV with a lightweight pipeline step
   (e.g., a Data Factory dataflow or a one-cell notebook) that strips/replaces `(`, `)`,
   and `/` in the header row before ingestion.
3. Add a pre-ingest column-name validator to `gateway_bronze_lib.read_gateway_csv` that
   explicitly renames the known offenders using a static map, so the bronze layer is
   self-healing even if the gateway export format changes again.

**Action items:**
- [ ] Add `_sanitize_columns()` coverage for `(ms)` / `(bytes)` patterns to unit tests
      (`starter/tests/test_parser.py`)
- [ ] Add a warning/guard in `QUICKSTART.md` explicitly telling users not to use
      Load-to-Tables for the raw gateway CSV
- [ ] Evaluate adding a pre-ingest rename step to the Deploy notebook

---

### Pain #3 — CONFIRMED BLOCKER: Identity-join still blocked by trial Spark throttling

**Status:** 🟠 BUILT / UNPROVEN — remains unproven; throttle is the active gate.

**Observation:**
The `01_identity_join.kql` + `add_artifact_identity` pipeline could not be validated
because the trial Fabric tenant's Spark pool is throttled. Spark jobs either queue
indefinitely or are killed before the cross-workspace join (gateway RequestId ↔
Workspace Monitoring XmlaRequestId) completes.

**This is a trial-tier infrastructure constraint, not a code bug.** The identity-join
logic itself remains unexecuted against real data.

**Recommended next steps:**
- Validate on a paid Fabric capacity (F2 minimum) or an Azure Databricks cluster
- If trial is the only available option, reduce the join window (filter to a single
  workspace and a narrow time range, e.g., 15 minutes) to stay within throttle limits
- See `CONTRIBUTING.md` → "Pilot report" for how to report a successful tenant validation

---

## Tally After Session 1

| Pain # | Pre-session status | Post-session status | Change |
|---|---|---|---|
| #4 | 🟢 PROVEN (local) | 🟡 PROVEN (local) / ⚠️ BROKEN via Load-to-Tables | Regression found; fix path documented |
| #3 | 🟠 BUILT / UNPROVEN | 🟠 BUILT / UNPROVEN | Blocker confirmed: trial Spark throttle |

---

*Add new sessions below this line, newest last.*

---

## 2026-07-01 — Session 2 (code follow-up to Session 1 findings)

Off-tenant follow-up: turned the Session 1 findings into code/doc fixes and executed
what could be executed locally (isolated conda PySpark 3.5.8 harness — see
`research/phase5_validation.md` → "Local Spark smoke harness").

### Pain #4 — FIXED (notebook path) + guarded

- **Code fix (U18):** `read_gateway_csv` only renamed *known* columns; unknown/new
  unit-suffixed headers (the gateway-upgrade case) flowed through with `( ) /` intact
  and would break the Delta write. Added `_safe_col_name()` + `_sanitize_columns()`
  (runs after `rename_map`) — the `_sanitize_columns()` the Session 1 fix path referenced
  now actually exists. **Proven on real Spark:** unknown `UpgradeMetric(ms)`/`NewSize(bytes)`
  columns read clean and a Parquet write succeeded (raises `InvalidColumnName` without it).
- **Session 1 action items:**
  - [x] `_sanitize_columns()` coverage for `(ms)`/`(bytes)` — 6 assertions in `test_parser.py` Test group 4.
  - [x] QUICKSTART.md warning not to use Load-to-Tables for the raw CSV — added.
  - [x] Pre-ingest rename in the deploy path — already covered: `Deploy_GatewayMonitor.ipynb`
        orchestrates `01_bronze_ingest.py` → `read_gateway_csv` (now sanitizing); its own
        `spark.read.format("delta").load(...)` only reads *back* written tables, not the raw CSV.
- **Unchanged (platform, not code):** Fabric **Load-to-Tables** still fails at platform
  schema-inference before our code runs. Fix remains procedural: use the notebook path.

### Pain #3 — throttle-survival workaround added (still infra-gated for [Verified])

The blocker (trial Spark/KQL throttle) is infrastructure, not a code bug, so the join
stays UNPROVEN on a paid capacity. But Session 1's recommended workaround ("narrow the
window / single workspace") is now built into `starter/kql/01_identity_join.kql`:
`lookback` is documented as narrowable (e.g. `15m`) and a new `workspace_filter`
(`dynamic([...])`) is pushed down **before** the join so the throttled engine scans far
fewer rows. Empty filter = all workspaces (unchanged default). Validate on F2+ or a
narrow-window trial run per Session 1.

---

## 2026-07-02 — Session 3 (live tenant, browser-driven attempt)

**Environment:** Real Fabric tenant, **free trial capacity** (59 days left), `Gateway-Pilot`
workspace, driven live via browser automation. Goal: run the flagship identity-join pilot.

### Pain #3 — CONFIRMED BLOCKER (live): Workspace Monitoring Eventhouse fails to provision on trial capacity

**Status upgrade:** 🟠 BUILT / UNPROVEN → 🔴 **LIVE-CONFIRMED as a trial-capacity blocker**
(root cause isolated; not a code bug, not a network/VPN issue).

**What happened, in order:**
1. `Gateway-Pilot` had **Workspace Monitoring OFF** — no Eventhouse, so `PowerBIDatasetsWorkspace`
   did not exist. That alone is why the pilot query had nothing to hit. (Prerequisite gap, now
   surfaced explicitly.)
2. Enabled it via **Workspace settings → Monitoring → + Eventhouse**. Fabric created three items
   (**Monitoring Eventhouse**, **Monitoring KQL database**, **Monitoring_Eventstream**) but then
   threw: **`Failed to update ingestion for fabric monitoring`**.
3. After ~10 min + multiple reloads, the Monitoring KQL database still shows
   **`Access error: Can't access the Eventhouse. Check your network for a VPN preventing access`**
   and **`Something went wrong`** (KustoWebV2 errors) on the Tables / Data Activity panels. The
   Eventhouse backend never came online.

**Error IDs (for MS support):** Activity `d9069e3d-e3aa-435d-b6fc-1d3bdbbdfc65`;
Kusto `KustoWebV2:5a19b9af-a6b0-4074-a586-5a7cf615b9ba` / `KustoWebV2:a2b9c74d-dc31-44f1-9967-a4c73e940ecc`;
Time `2026-07-02 01:53:38 CDT`; Service version `13.0.28506.393`.

**VPN / network RULED OUT (hard evidence)** — Fabric's "check your VPN" text is a misleading
generic. From the client machine:
- Cluster host `16f93f410c3b4163b3625e18cfac6898-api.analysis.windows.net` **resolves** to a public
  Azure IP `20.98.145.48` (normal `privatelink → wabi-us-central → centralus.cloudapp.azure.com` CNAME chain).
- **TCP 443 to the cluster succeeds** (`TcpTestSucceeded: True`).
- System DNS resolver is **Comcast `75.75.75.75`**, not the VPN's `100.100.100.100` — the VPN's DNS
  is not in the path. (Machine does run a split-tunnel VPN, but it is provably not intercepting this.)
- The front door is reachable; the **Kusto backend itself returns application errors** → the
  Eventhouse failed to provision, which is a **capacity** problem, not connectivity.

**Conclusion:** Workspace Monitoring's Eventhouse cannot provision on the free trial capacity.
This is exactly the Pain #3 trial-capacity limit, now field-confirmed. **Resolution: paid F2+
capacity** (matches Session 1 + the repo's standing recommendation). Support escalation possible
with the IDs above; the Log-Analytics path (`XmlaRequestId`) is the alternative that avoids the
Fabric Eventhouse entirely.

### Flagship identity join — still UNPROVEN (unchanged)

The exact join (phase5 **U19**) requires `PowerBIDatasetsWorkspace`, which requires a working
monitoring Eventhouse → blocked until F2+. The **desk-verification of the join key is unaffected**;
this only blocks the *live* confirmation. Two other prerequisites remain and are independent of
capacity: (a) a semantic model actively refreshing **through the on-prem gateway** in the monitored
workspace, and (b) a `RequestId` copied from the **gateway host** `QueryStartReport*.csv` (that host
is not the client machine). All three (F2+ capacity + gateway-routed refresh + gateway RequestId)
must be present at once for a green run — best assembled in the real pilot environment.

## 2026-07-02 — Session 8 (pre-pilot code completion: M6 CU bridge + U14 quarantine)

Finished the two pure-code items so the F2 pilot exercises the COMPLETE tool.
- **M6 — live CU bridge implemented.** `04_capacity_bridge.bridge_capacity_metrics_xmla` was a `NotImplementedError`; it now runs DAX against the Fabric Capacity Metrics semantic model via the Power BI REST **executeQueries** endpoint (no XMLA client lib; works from any Fabric notebook over HTTPS). Response keys (`Capacities[Capacity Id]`, `[CU_s]`, …) are normalized to the `gold_capacities` schema with a rename-tolerant `pick()`. Config gains `datasetId/groupId/token/dax`. **[Unverified]** live: needs the Metrics app installed, the 'Dataset Execute Queries REST API' tenant setting, and confirmation of the app's DAX column names.
- **U14 — cast-error quarantine.** `_cast_value` now returns `(value, cast_ok)`; a bad numeric (e.g. `"N/A"` in a `(ms)` column) is coerced to null but FLAGGED into a per-row `_cast_errors` column, while genuine empties/`null` sentinels are not flagged. Silent data-quality loss is now visible in bronze.
- **Tests/CI:** new Spark-free `test_cast_and_bridge.py` (M6 executeQueries normalization with monkeypatched requests; U14 skips gracefully off-cluster) wired into `tier1-parser`; U14 typing checks added to `test_tenant_spark.py` (JDK-17 tier). All Spark-free suites pass.

No live tenant run this session. Remaining MVP work is validation (M1) + report finalize (M7) + deploy-in-Fabric (M8).

---

## 2026-07-02 — Session 7 (MVP roadmap + U15 event-log arm + tenant Spark test)

- **`docs/MVP-ROADMAP.md`**: exhaustive path-to-MVP task list (M1–M10) mapped to GitHub milestones #1–#10, with an explicit MVP acceptance definition. Records that ~80% of remaining work is gated on one live F2 pilot (M1).
- **U15 re-audit — found + fixed a real remaining gap.** The refresh and network join arms were already nearest-match-deduped, but the **Level-3 event-log join** in `02_silver_correlate.py` still fanned one triage row into N OS-event matches. Added the same `row_number() over(RequestId order by |Δt|) == 1` dedup to the event-log arm, plus a fail-loud `silver_triage rows <= failed input` assertion. U17 (multiLine) and U18 (sanitize) were re-confirmed already fixed — not re-touched.
- **`starter/tests/test_tenant_spark.py`** (Tier 2, JDK 17): tenant transforms → real Spark DataFrame + Delta write, and a real-Spark proof that the U15 window dedup collapses 1-query×3-NIC-samples to exactly 1 row (nearest kept). Wired into the `tier2-spark` CI job. Skips cleanly where Spark is unavailable.

No live tenant run this session; MVP remains a validation problem (M1).

---

## 2026-07-02 — Session 6 (tenant preflight + one-sitting pilot runbook)

- **New `starter/notebooks/tenant_doctor.py`**: preflight for the tenant extract. 5 checks (SP token; **read-only admin API + tenant setting**; activity events; refreshables; capacity-bridge mode), each with an exact remediation string; non-zero exit when a required check fails. MOCK mode for CI. Prints `VERDICT: READY|BLOCKED`.
- **New `starter/tests/test_tenant_doctor.py`** (Spark-free): READY path, non-required FAIL stays READY, D1 token-failure short-circuit → BLOCKED, remediation coverage. **PASS 4/4.**
- **CI**: preflight test added to `tier1-parser`; `tenant-harness` job now runs `tenant_doctor` (mock) as a gate before the mock chain.
- **Runbook** renamed `docs/RUNBOOK-F2-pilot.md` and restructured into **one F2 sitting, two tracks**: Track A (match rate) + Track B (tenant extract), with a free Step 0 preflight (0A gateway sanity, 0B `tenant_doctor`), a Track B extract step (6B) reusing the same SP/capacity, dual report-back, and a Track B 401/403 troubleshooting row. QUICKSTART reference updated.

Net: a piloter validates BOTH permission paths before the meter starts and produces both live results (match rate + populated tenant gold) in a single rental. Live numbers still gated on the real SP + tenant setting.

---

## 2026-07-02 — Session 5 (tenant-extract pipeline + Capacity Metrics bridge + CI harness)

Off-tenant engineering. Turns the enhanced report's biggest headwind — "tenant pages render empty" — into a wired, CI-tested feed. No live tenant run; live numbers still gated on an admin-scoped SP.

### Tenant-extract pipeline (feeds Nexus Tenant Overview / Timeline / Refresh Analytics)
- **New `starter/notebooks/00_tenant_extract.py`**: Scanner API (getModifiedWorkspaces → getInfo batch≤100 → poll scanStatus → scanResult), Activity Events (per-UTC-day paging + continuationToken), and Refreshables. MSAL client-credentials auth; 429/5xx backoff honoring Retry-After. Spark- and requests-optional so it is unit-testable; **MOCK mode** (`TENANT_EXTRACT_MOCK=1`) emits deterministic bronze with no network.
- **New `starter/notebooks/01a_tenant_silver_gold.py`**: pure-Python transforms → `gold_inventory`, `gold_activities`, `gold_refreshables` (schemas match the TMDL model exactly).
- **New `starter/notebooks/04_capacity_bridge.py`**: `gold_capacities` + CU, config-driven `capacityBridge.mode` = `mock` | `fpm_eventhouse` | `capacity_metrics_xmla`. Honest headwind captured: CU has no clean public REST endpoint, so a bridge is mandatory for live CU.

### Verification
- **New `starter/tests/test_tenant_extract.py`** (Spark-free): transforms + full mock chain → 4 gold tables. **PASS 4/4.** Existing Tier-1 parser suite still **PASS**.
- **CI (`.github/workflows/tests.yml`)**: added the tenant test to `tier1-parser`; added a `tenant-harness` job (mock chain 00→01a→04). The `tier2-spark` job already runs the full PySpark harness on **ubuntu-latest + JDK 17** (`test_parser_spark.py` + `run_local_smoke.py`) — the JDK-25-vs-Spark-3.5 blocker from Session 4 is a local-sandbox limit only; CI pins JDK 17.

### Status
| Item | Status | Note |
|---|---|---|
| Tenant report feed | 🟢 **Built + CI-tested (mock)** | Live run needs admin-scoped SP + read-only admin API tenant setting |
| `gold_capacities` / CU | 🟠 **Bridge built; live [Unverified]** | Needs `fpm_eventhouse` or `capacity_metrics_xmla` mode wired; XMLA DAX columns per app version |

---

## 2026-07-02 — Session 4 (off-tenant code follow-up: G3 match-rate + G4 notebook routing)

Off-tenant engineering follow-up turning the standing G3/G4 findings into runnable artifacts. No new tenant run; items remain infra-gated for `[Verified]`.

### Pain #3 (G3) — match-rate measurement now a first-class, throttle-safe artifact

- **New file `starter/kql/04_identity_match_rate.kql`.** `01_identity_join.kql` proved the join *returns rows*; it never quantified the attribution rate. This computes `attributed_queries / total_gateway_queries = match_rate_pct` (Block A), a Refresh-vs-DirectQuery split (Block B), and an unattributed-sample diagnostic (Block C). Every block pushes `lookback` + `workspace_filter` down **before** the join, so it survives the trial Spark/KQL throttle (Session 1/3) at `lookback = 15m` + a single workspace GUID.
- **Status unchanged for `[Verified]`:** still requires a working Workspace Monitoring Eventhouse (F2+; trial-blocked per Session 3). The *measurement path* is now ready so the very first green run yields the U11 number instead of just "rows came back."

### Pain #4 (G4) — notebook now routes through the sanitizing lib (gap from Session 2 closed)

- **Root of the remaining exposure:** Session 2 fixed `read_gateway_csv` (`_sanitize_columns`) and proved it on real Spark, but `01_bronze_ingest.py` still ran its **own driver-side parser** (`parse_csv_schema_adaptive`) that never called `_sanitize_columns` — so the notebook path was *not* actually protected against new `(ms)`/`(bytes)` headers.
- **Fix:** added `ingest_gateway_logs_via_lib()` and made `ingest_gateway_logs()` call it first — reading raw `QueryExecutionReport*.csv` / `QueryStartReport*.csv` through `read_gateway_csv` (native Spark, column-name-based, `_sanitize_columns`-protected) + `cast_query_execution` + `add_artifact_identity`. The driver-side parser is retained as an explicit **fallback** for the JSON-staged `RawCsvContent` form and odd dialects. Import is guarded so the notebook still loads without the lib.
- **Net:** the primary notebook ingest path is now immune to both the positional `DataFormat.Error` (PBIT template failure) **and** the `InvalidColumnName` failure on new unit-suffixed headers. `py_compile` clean. Fabric Load-to-Tables remains procedurally excluded (unchanged).

### Pilot template — G3 run steps added

- `.github/ISSUE_TEMPLATE/pilot-report.yml` now includes an ordered, ~20-min G3 runbook (prereqs incl. the F2+ Eventhouse gate, enable monitoring → land logs → refresh → confirm rows → run `04_identity_match_rate.kql` Block A/B/C), a match-rate field wired to the new file, and an explicit capacity-SKU field (trial-vs-F2+ provisioning).

## Tally After Session 4

| Pain # | Prior status | Post-session status | Change |
|---|---|---|---|
| #3 (G3) | 🟠 Built / Unverified; trial-blocked | 🟠 Built / Unverified — **match-rate measurement now runnable + throttle-safe** | New `04_identity_match_rate.kql`; still needs F2+ for live proof |
| #4 (G4) | 🟡 Proven (local) / ⚠️ notebook path unprotected | 🟢 **Notebook routed through sanitizing lib** (syntax-verified) | Session 2 gap closed; live Fabric run still pending |

---

## Tally After Session 3

| Pain # | Prior status | Post-session status | Change |
|---|---|---|---|
| #3 | 🟠 BUILT / UNPROVEN | 🔴 Trial-capacity blocker **live-confirmed**; VPN/network ruled out | Root cause isolated; F2+ required |
| U19 (exact identity join) | [Unverified] | [Unverified] — live run blocked on F2+ + gateway pieces | No change; desk-verification stands |
