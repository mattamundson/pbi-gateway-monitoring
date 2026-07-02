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

## Tally After Session 3

| Pain # | Prior status | Post-session status | Change |
|---|---|---|---|
| #3 | 🟠 BUILT / UNPROVEN | 🔴 Trial-capacity blocker **live-confirmed**; VPN/network ruled out | Root cause isolated; F2+ required |
| U19 (exact identity join) | [Unverified] | [Unverified] — live run blocked on F2+ + gateway pieces | No change; desk-verification stands |
