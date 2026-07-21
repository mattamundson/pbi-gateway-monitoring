# Implementation Roadmap — from here to a complete, autonomous gateway operations platform

**Status: authored 2026-07-21.** Supersedes nothing; sits above `MVP-ROADMAP.md` (which answers
"does it work?") and `ENTERPRISE-READINESS.md` (which answers "would a F500 let it in the door?").
This answers the third question: **"what is every remaining task between today and finished?"**

## What "finished" means here

"Fully autonomous, JARVIS-level" is interpreted throughout as: **the tool observes the gateway
estate, predicts failures before they land, explains root cause in plain language, and closes the
loop by acting — with human approval tiers proportional to blast radius.** It is *not* an
integration with the separate JARVIS TRADER project; the two share a name and nothing else. That
distinction is stated explicitly because a prior scope confusion in this project ("backtesting")
came from exactly this kind of silent assumption.

Concretely, done means all four autonomy layers are live and validated:

| Layer | Question it answers | Today |
|---|---|---|
| **L1 Detect** | "Something is wrong right now" | Batch-only, partially broken (Phase 0) |
| **L2 Predict** | "Something will be wrong in 2 hours" | One reference KQL query, never scheduled |
| **L3 Explain** | "Here is why, in English" | Zero code |
| **L4 Act** | "It has been fixed; here's what I did" | Zero code, and the hardest wall (see Phase 9) |

---

## Read this before sequencing anything else

### 1. The critical path changed on 2026-07-21

`MVP-ROADMAP.md` states *"~80% of remaining MVP work is gated on one live F2 pilot"* and *"MVP is
now a validation problem, not a build problem."* **That is no longer accurate**, and acting on it
would waste paid capacity. A completeness audit run this session found defects that mean the pilot,
run today, would measure a pipeline that cannot work end-to-end. Three were verified directly
against source, not taken on the auditor's word:

- **`[VERIFIED]` No collector ever uploads anything to Fabric.** `Collect-GatewayLogs.ps1:214`
  carries `TODO (Phase 5): Replace local JSON write with OneLake upload`, and lines 203–205 mark
  the mechanism `[Unverified]`/`[Assumption]`. No `Az.Storage`, no REST PUT, no `az` CLI call
  exists anywhere in `starter/collectors/`. All seven collectors write local JSON to the gateway
  host's disk. `01_bronze_ingest.py` reads from `abfss://.../Files/bronze_landing`. **Nothing in
  this repository connects those two points.** This is the single largest gap in the product and
  it is not tracked in any existing roadmap, backlog, or U-matrix item.
- **`[VERIFIED]` Bronze writes are `append` (`01_bronze_ingest.py:611`), not merge.** Re-running
  any batch duplicates every row. Silver/gold/tenant tables use `overwrite`, which is correct-ish
  but full-recompute. `docs/gateway-monitoring-runbook.md` already flags *"Dedup matters:
  QueryTrackingId repeats on retry. For incremental, switch to MERGE"* — never done. Every metric
  the tool produces is wrong after a retry or backfill.
- **`[VERIFIED]` The credential-drift alert (pain point #10) has never been capable of firing.**
  `activator-rules.json:64` reads `datasource_status != 'Online'`. The gold layer produces
  `status_current` (`03_gold_aggregate.py:99`) — a different column name — and the collector emits
  `Live`/`Unknown`/`Error` (`Get-GatewayInventory.ps1:199-200`), never the string `Online`. Wrong
  column *and* wrong value vocabulary, with no validation anywhere that would have caught either.

**Revised sequencing rule: Phase 0 lands before the F2 pilot.** The pilot is still the gate for
the flagship number, and that number still gates most downstream value — but a pilot run against
a pipeline with no transport, duplicating writes, and a dead alert rule burns paid capacity to
produce a misleading result.

### 2. There is a hard ceiling on L4 autonomy, and it is not a Fabric limitation

Research against Microsoft primary sources (2026 state) establishes that the last autonomy mile
is **a system you build and own**, not a Fabric feature you configure:

- **Fabric User Data Functions are GA (2026-03-24) and can be an Activator action — but Microsoft
  documents explicitly that they "don't currently support on-premises or VNet data gateways."**
  The obvious "let Fabric call home" design is closed off by documentation, not by configuration.
- **Fabric Data Agent is GA and genuinely good at NL2KQL root-cause explanation — but is
  permanently read-only** by stated design: it *"remain[s] read-only and [doesn't] trigger anomaly
  detection jobs, notebooks, or other write or action workflows."* It can explain; it can never be
  the thing that acts.
- **The gateway has no cloud-callable restart.** Microsoft's only documented restart paths are the
  local gateway app, the Windows Services console, or `net stop/start PBIEgwService` on the box.
  Neither the REST Gateways API nor either PowerShell module exposes a restart. The gateway's
  outbound-only design is the product's security premise, not an oversight.
- **No Microsoft reference architecture exists for closed-loop remediation in Fabric.** The Azure
  Well-Architected self-healing guidance (RE:07) is real but assumes an Azure-resident target with
  an existing automation control plane.

**Implication, stated plainly so it is not discovered late:** L4 is the largest single work item
in this roadmap and the only one with no vendor-supported path. Phases 0–8 deliver a genuinely
best-in-class *observability* product using GA primitives. Phase 9 is a distinct systems-
integration project with its own risk profile. **It is legitimate to ship v1.0 at Phase 8 and
treat Phase 9 as v2.0** — that decision is flagged as a gate, not assumed either way.

### 3. Effort vs. calendar time are not the same constraint

Total engineering effort below is roughly **95–130 engineering-days**. But two items are pure
calendar time that cannot be compressed by adding effort: shadow-mode validation needs **30–60
days** of real operation, and precision/recall for rare-event rules (gateway-offline,
credential-drift — 1–2 fires/week in a healthy fleet) needs **3–6 months** to reach a defensible
confidence interval. Plan the pilot-adopter relationship around that, not around the build.

### 4. Sizing key

`S` ≤ 1 day · `M` 2–5 days · `L` 1–3 weeks · `XL` > 3 weeks. Sizes are engineering effort for one
focused person, excluding review latency.

---

# Phase 0 — Correctness blockers (must land before the pilot)

*~15–20 days. Nothing downstream is trustworthy until these close. Every item is locally
verifiable — none needs a tenant.*

### T1. Build the collector → OneLake transport `XL`
**Problem.** The product has no data path. Verified above.
**Do.**
1. Decide the transport. Recommended: **OneLake ADLS Gen2 REST via `Az.Storage`** with an Entra
   service principal, because it is the same auth path the notebooks already assume and needs no
   inbound firewall change. Alternatives to evaluate and reject in writing: `azcopy` (extra binary
   to fleet-deploy), Fabric Data Pipeline "Copy from on-prem via gateway" (circular — the gateway
   is what we're monitoring), Eventstream custom endpoint (right answer for Phase 7, wrong for
   bulk CSV today).
2. Write `starter/collectors/lib/Send-ToOneLake.ps1` as a shared module: takes a local path +
   destination prefix, acquires a token from Key Vault-stored SP creds, uploads with retry and
   exponential backoff, verifies by re-reading content length, and writes an upload manifest.
3. Make it **idempotent and resumable**: name blobs deterministically
   (`{gatewayId}/{reportType}/{yyyy-MM-dd}/{sourceFileName}.json`), skip on matching size+hash,
   record uploaded files in a local `upload-state.json` so a 6-hour outage backfills cleanly.
4. Refactor all seven collectors to call it, replacing the local-JSON-only terminal step. Keep
   local write as a `-NoUpload` debug mode.
5. Handle the partial-file case explicitly: the gateway writes CSVs continuously, so uploading a
   file being actively appended to yields a truncated final row. Only upload files whose
   `LastWriteTimeUtc` is older than a configurable quiesce window (default 2 min), or upload with
   an explicit `is_partial` flag the bronze layer can reconcile.
**Files.** All of `starter/collectors/*.ps1`; new `starter/collectors/lib/`; `config.sample.json`
(add `oneLake.uploadEnabled`, `.endpoint`, `.quiesceSeconds`).
**Acceptance.** A Pester test with a mocked storage endpoint proves: upload happens, retry fires on
429/503, a re-run of an unchanged file is a no-op, a partial file is held back. Then one real
upload into the pilot Lakehouse landing path during Phase 3, verified by `01_bronze_ingest.py`
reading it.
**Depends on.** Nothing. **Start here.**
**Risk.** `[ASSUMPTION]` OneLake's ADLS Gen2 endpoint is reachable from a corporate gateway host
over 443 with no proxy exception. Large enterprises frequently proxy or block novel egress —
document the exact hostnames for the enterprise firewall conversation (this is a named F500
security-review question in `ENTERPRISE-READINESS.md`).

### T2. Convert bronze to MERGE; define and document natural keys `M`
**Problem.** `append` at `01_bronze_ingest.py:611` duplicates on every re-run. Verified.
**Do.**
1. Author `docs/data-contracts.md`: for every bronze/silver/gold table, record the natural key,
   the watermark column, expected freshness, and the owner. This document does not exist and is a
   prerequisite for T2, T17, T35, and any migration work.
2. Canonical bronze key: `(GatewayObjectId, QueryTrackingId, ReportType)` — validate against real
   log data during Phase 3 before treating as settled.
3. Replace `append` with `DeltaTable.merge()` upsert keyed on the above.
4. Replace the "scan since last run" assumption with a **lookback window** (rescan last 24–48h by
   `LastWriteTimeUtc`) — safe precisely because the merge is idempotent, and it absorbs a
   backfill from a collector that was down for hours without special-casing.
5. Document the data-loss ceiling explicitly: the gateway's own `ReportFileCount`/`ReportRetention`
   settings define how long a host can be offline before source logs rotate away. Make it a named
   constant and an alert threshold, not an implicit assumption.
**Acceptance.** A test runs the same batch three times and asserts identical row counts and
identical aggregate values. A second test simulates a 6-hour gap plus backfill and asserts no
duplicates and no gaps.
**Depends on.** T1 (need real transport semantics to know what "a batch" is).

### T3. Fix the credential-drift rule; add semantic validation so this class cannot recur `S`
**Problem.** Wrong column name and wrong value vocabulary. Verified. Pain point #10 has never worked.
**Do.**
1. Fix the condition to reference the column gold actually produces and the values the collector
   actually emits (`status_current NOT IN ('Live')`, pending confirmation of the full vocabulary
   against a live tenant in Phase 3 — `Error` and `Unknown` may warrant different severities).
2. Build the validator that would have caught it — see T14. **Do not fix this rule without
   building T14**, or the next rule drifts the same way silently.
**Acceptance.** T14's validator passes; a synthetic gold row with `status_current='Error'`
provably satisfies the rule condition.

### T4. Reconcile the alert specification into one source of truth `S`
**Problem.** Three files disagree about the same alerts. `activator-rules.json` hardcodes
`heartbeat_age_minutes > 3`; `activator-rules.md` and `config.sample.json` document **10 minutes**;
`phase5_validation.md` §3's acceptance test expects **~2–3 min**. Separately, the `.md` documents 5
rules including `network-saturation`; the `.json` has 7 rules, includes three the `.md` never
mentions, and has no `network-saturation` at all.
**Do.** Make `activator-rules.json` the single source of truth. Generate `activator-rules.md` from
it with a script. Reconcile every threshold to one number with a written rationale. Decide whether
`network-saturation` is in or out.
**Acceptance.** A CI check fails if the generated `.md` differs from the committed one.
**Why it is Phase 0.** You cannot measure precision/recall (T28) against a rule set that disagrees
with itself, and you cannot fault-inject (T25) against an ambiguous expected time-to-detect.

### T5. Implement real SCD2 on `gold_dim_gateway` `M`
**Problem.** `03_gold_aggregate.py:346-348` — `[STUB] Full SCD2 merge requires DeltaTable.merge()...
Simplified here to overwrite current`. The dimension is labelled SCD2 and behaves as SCD1. Every
version change, status change, and node membership change in fleet history is silently lost.
**Do.** Implement the standard Delta SCD2 merge (close the current row by setting `valid_to` and
`is_current=false`, insert the new version) keyed on `GatewayObjectId` + change detection across
`GatewayNodeName, Version, Status, DatasourceCount`.
**Acceptance.** A test runs two batches with a version change between them and asserts two rows
exist, exactly one `is_current`, and the historical row's `valid_to` equals the new row's
`valid_from`. The existing Tier 2.5 test only asserts row count and `is_current=True` — it would
pass today against the broken implementation, so it must be strengthened, not reused as-is.

### T6. Implement the Activity-Events attribution fallback `L`
**Problem.** `02_silver_correlate.py:449-467` — `[STUB] Activity Events ingestion is not implemented
here`, with the fuzzy-join block commented out. This is **Method 2 of the flagship differentiator**.
Today every query without a Fabric `EvaluationContext` falls through to `UNATTRIBUTED` even when a
match exists in Activity Events.
**Do.** Implement the time-window fuzzy join against the tenant-extract Activity Events already
produced by `00_tenant_extract.py`, emitting `attribution_confidence = FUZZY_TIME_WINDOW`. Apply
the same nearest-match fan-out dedup pattern already proven for U15 in `build_silver_triage`.
**Acceptance.** Tier 2.5 test asserting a query with no EvaluationContext but a matching Activity
Event resolves to `FUZZY_TIME_WINDOW`, and that fan-out is bounded (≤1 row per input).
**Why it matters for the pilot.** The measured match rate in Phase 3 will be *understated* by
however much traffic Method 2 would have caught. Shipping this before the pilot means the flagship
number reflects the real design, not a partial one.

### T7. Materialize schema-drift warnings to a queryable table `S`
**Problem.** `01_bronze_ingest.py:648` — `TODO (Phase 5): write schema_warn rows to a
bronze_schema_warnings Delta table`. Drift is `print()`ed to notebook stdout that nobody reads.
Pain point #4 (gateway upgrades silently change columns) is precisely what this detects.
**Do.** Write to `bronze_schema_warnings` (run_id, table, expected_cols, actual_cols, added,
removed, detected_at). Add an Activator rule on non-zero new drift.
**Acceptance.** A test feeds a CSV with an added and a removed column and asserts both land in the
table with correct classification.

### T8. Stop silently swallowing table-read failures `S`
**Problem.** `02_silver_correlate.py:76-80` and `03_gold_aggregate.py:46-52` wrap **every table
read in the entire silver/gold layer** in `except Exception → print WARN → return None`. Delta
corruption, a permissions error, and "table doesn't exist yet" are indistinguishable; the caller
proceeds with `None` and silently drops that data source. Largest blast radius of any silent
failure in the codebase.
**Do.** Distinguish "table does not exist" (legitimate first-run, return `None`) from every other
exception (raise, or record a hard failure in the run log from T18). Never swallow both.
**Acceptance.** A test that a nonexistent table returns `None`, and a permissions/corruption error
raises rather than returning `None`.

### T9. Collapse the duplicated parsers and fix the false test coverage `M`
**Problem.** `01_bronze_ingest.py` contains its own `parse_csv_schema_adaptive` and
`parse_evaluation_context`, which are *non-identical* reimplementations of tested logic in
`gateway_bronze_lib`. Worse, `test_parser.py:26` imports the **library** function *aliased as the
notebook function's name* — the test reads as covering the notebook parser and does not. The copies
have already drifted (RFC-4180 handling and cast-error tracking exist in one, not the other).
**Do.** Delete the notebook-local copies; import from `gateway_bronze_lib`. If a behavioral
difference is genuinely needed, add it to the library behind a flag. Remove the misleading alias.
**Acceptance.** Grep proves one implementation of each. Existing parser tests still pass against
the notebook's actual call path.
**Related.** Same pattern in `ingest_mashup_processes` (reimplements `read_mashup_processes`) — fix
in the same pass.

### T10. Fix collector silent-failure modes and spool-path discovery `M`
**Problem.** `Collect-DiskSpool.ps1` defaults `$SpoolPath` to a hardcoded `PBIEgwService` account
path; under a custom service account `Test-Path` fails, size defaults to 0, **and no error is
recorded** — a full or inaccessible spool reports as healthy, defeating the
`spool-disk-critical-remediate` rule. `Get-ChildItem -ErrorAction SilentlyContinue` (line 157) makes
permission-denied indistinguishable from empty. `Collect-MashupProcesses.ps1:56` has a bare
unlogged `catch {}` that silently drops processes from the CPU baseline.
**Do.** Discover the spool path from gateway configuration rather than guessing it; on failure to
discover, record a **hard collection error**, never a healthy-looking zero. Replace every
`SilentlyContinue` with explicit handling that appends to `CollectionErrors`. Log the mashup catch.
**Acceptance.** Pester tests (T11) asserting each failure mode produces a non-empty
`CollectionErrors` rather than a healthy-looking result.

---

# Phase 1 — Make the system observable to itself

*~8–12 days. "Who watches the watcher." A monitoring tool that fails silently is worse than none,
because it manufactures false confidence.*

### T11. Pester suite for all seven collectors + bring them into CI lint scope `L`
**Problem.** Zero test coverage — confirmed, no `*.Tests.ps1` anywhere. Worse, `lint.yml:33` scans
only `./scripts`, so `starter/collectors/` **has never been statically analyzed by CI either**. The
seven scripts that run on customer production hosts are the least-verified code in the repo.
**Do.** Pester 5 suite per collector with mocked cmdlets; extend `Invoke-ScriptAnalyzer` to
`starter/collectors`; add a `collectors` job to CI.
**Acceptance.** CI job green; PSScriptAnalyzer clean or with documented suppressions.

### T12. Cover the fifteen untested bronze ingest functions `L`
**Problem.** Every function the Fabric notebook actually auto-runs — `ingest_gateway_logs`,
`_write_bronze_delta`, `_emit_schema_warnings`, `ingest_network_metrics`, `ingest_event_log`,
`ingest_disk_spool`, `ingest_gateway_inventory`, `ingest_gateway_datasources`,
`ingest_refresh_history`, `ingest_mashup_processes`, and the rest — has **zero** coverage. Only two
pure helpers are proven.
**Do.** Extend the Tier 2.5 pattern: run the real functions against inline bronze fixtures in a
temp Lakehouse. Also cover `04_capacity_bridge.bridge_fpm_eventhouse` (no test anywhere) and
`00_tenant_extract`'s real HTTP/pagination/retry paths (only MOCK is exercised).
**Acceptance.** A coverage report showing every auto-run function executed by CI.

### T13. Pipeline run log + heartbeat + external dead-man's switch `M`
**Problem.** No liveness signal exists for the pipeline itself. If the notebook stops running, or a
collector's scheduled task dies, the dashboard shows stale data indistinguishable from a quiet
gateway. This exact failure already happened once in this project: `LIVE-TENANT-FINDINGS.md`
records Workspace Monitoring items that *appeared* created while the backend never provisioned —
caught only by manual query.
**Do.**
1. `_pipeline_run_log` Delta table (run_id, layer, status, rows_in, rows_out, started_at,
   ended_at), written by every notebook in a `try/finally` so failures record too.
2. Per-collector heartbeat record on every scheduled run.
3. **External** dead-man's switch (Healthchecks.io or self-hosted): each job pings on success
   only; absence of a ping is the alarm. Must live outside Fabric — a Fabric outage otherwise
   takes out both the pipeline and its own alarm.
4. Freshness check requiring **3 consecutive missed intervals** before firing, routed to a
   **different channel and severity** than gateway-health alerts. "The monitor is broken" and "the
   gateway is broken" must never be confusable in an inbox.
**Acceptance.** Killing the scheduled task produces an external alert within one interval + margin.

### T14. Validate the Activator rule set — schema and semantics `M`
**Problem.** Nothing validates `activator-rules.json` — not even that it is well-formed JSON. T3
proves the consequence: a rule referencing a nonexistent column with an impossible value shipped
and was never caught.
**Do.**
1. JSON Schema for the rule format; validate in CI.
2. **Semantic validation**: assert every `source_object` is a real gold table and every column
   referenced in a `condition` exists in that table's schema — resolved from the same contract
   file T2 produces.
3. **Trigger fixtures**: for each rule, a synthetic gold row that *should* fire it and one that
   should not; assert the condition evaluates correctly.
**Acceptance.** CI fails if a rule references an unknown column, an unknown table, or a value
outside the column's documented vocabulary. Run it against today's file — it must fail on
`credential-drift` before T3 fixes it. That failing run is the proof the validator works.

### T15. Validate the KQL assets `M`
**Problem.** Five `.kql` files, referenced only by scripts that *print instructions telling a human
to paste them*. Nothing parses, lints, executes, or asserts against any of them. A wrong column or
table name in `01_identity_join.kql` — the flagship join — would never be caught. The Tier 3
simulation proves the join *logic* in PySpark, which is a parallel reimplementation in a different
language, not a test of the shipped KQL.
**Do.** Parse/lint each `.kql` in CI; assert referenced columns exist in the documented schemas;
where a Kusto emulator or a live KQL DB is available, execute against fixture data.
**Acceptance.** CI catches a deliberately introduced typo in a column name in `01_identity_join.kql`.

### T16. Contract tests on the collector→parser boundary `M`
**Problem.** The single highest-value missing test given pain points #4/#5 are literally *about*
gateway upgrades changing log columns.
**Do.** A `schemas/bronze_contract.json` per log type. The **collector** asserts its emitted JSON
matches before upload (fail loud at the cheapest place to catch drift); the **bronze notebook**
validates the same contract on ingest (defense in depth).
**Acceptance.** Changing a column name in a synthetic collector output fails the contract test on
both sides.

### T17. Data-quality gates at each medallion layer `L`
**Do.** Adopt **Great Expectations** (recommended over Soda for versioned, self-documenting
expectation suites usable as an audit trail; over Deequ because this fleet size doesn't need
distributed constraint computation). Per layer:
- **Bronze** — schema drift, freshness, volume anomaly vs trailing 7-day median. → **warn +
  quarantine**, never fail the batch (drift is expected; absorbing it silently is the bug).
- **Silver** — null rate on join keys, duplicate/fan-out detection, referential integrity. →
  **fail the batch** (a corrupt join reaching gold becomes false evidence during an incident RCA,
  which is worse than a late report).
- **Gold** — business-rule bounds, fan-out sanity, row-count reconciliation vs silver. →
  **quarantine offending rows, publish the rest**, and raise a distinct data-quality alert.
**Acceptance.** Suites run in CI against synthetic fixtures, so a change that breaks an expectation
fails the PR rather than production.

### T18. Golden-snapshot and property-based regression protection `M`
**Problem.** Tier 2.5 proves the transforms have the right *shape*; Tier 3 asserts match rate within
`[0.70, 0.95]`. Neither catches a subtle regression moving 87.50% → 82.10% while staying in range.
**Do.**
1. `golden_snapshot.json` freezing **exact** values against the committed `seed=42` fixtures:
   `match_rate_pct`, triage confidence-tier distribution, `queries_per_node_cv` (tol 1e-6),
   mashup runaway flags, identity attribution tier counts, `_cast_errors` quarantine counts. Never
   auto-update; an intentional change requires a reviewed diff.
2. Hypothesis property tests for invariants a fixed fixture cannot cover: `len(silver_triage) ≤
   len(input_failed_queries)` (the U15 invariant, fuzzed); `cv ≥ 0` and `== 0` iff uniform;
   `attribution_confidence` is a closed enum; `_cast_errors` never flags a genuine null.
**Acceptance.** A deliberate one-line change to a transform fails CI with a readable diff.

---

# Phase 2 — Live pilot (the gate)

*~5–8 days of effort, one paid-capacity sitting. This is where the flagship claim becomes a number.*

### T19. Pre-pilot capacity and state audit `S`
**Do.** Confirm the state of `gwmoncap01` (F2, centralus) — provisioned 2026-07-06, state
unconfirmed since, potentially billing at ~$0.36/hr for 15 days. `[RISK]` Confirm before
provisioning anything new; `RUNBOOK-F2-pilot.md` Step 0C already carries the check-table.
**Do also.** Rehearse the full runbook against mocks end-to-end so the paid window is execution,
not debugging. Verify the tenant-admin Workspace Monitoring toggle status — it gates Track B only;
the Track A flagship number does not need it.

### T20. Execute Track A — measure the flagship match rate `M`
**Do.** `docs/RUNBOOK-F2-pilot.md` as written, now with Phase 0 landed: collectors actually upload
(T1), bronze merges (T2), Method 2 attribution is live (T6).
**Acceptance.** `04_identity_match_rate.kql` returns a real number against real tenant data, and
the medallion runs bronze→gold end-to-end in real Fabric.
**Caveat, stated up front.** The runbook's Step 4 triggers 1–2 refreshes — a handful of queries.
That is a **mechanical smoke test proving the join works**, not a statistically defensible match
rate. Do not publish a headline percentage from it. T22/T27 produce the defensible number.

### T21. Build the attribution-gap classification protocol `M`
**Problem.** A bare match-rate percentage is uninterpretable. There is no universal "good" number —
87.5% is a *simulated* assumption about one workload mix, not a benchmark.
**Do.**
1. Compute the tenant's **theoretical ceiling** from `gold_inventory.ItemKind`:
   `ceiling = 1 − (DataflowGen1 + Paginated) / total_gateway_routed_items`. That is the maximum any
   correct implementation could attribute for that tenant.
2. Add Block D to `04_identity_match_rate.kql` joining unmatched samples to `gold_inventory`.
3. Classify every miss through three checks: **(a) widen the window** — if it now matches, it was
   timing, not a gap; **(b) item-kind lookup** — Dataflow Gen1/Paginated = `TRUE_GAP`, a documented
   platform limitation; **(c) monitoring-coverage lookup** — workspace without Workspace Monitoring
   enabled = operational gap, not a code defect. Anything surviving all three is `SUSPECT_BUG` and
   escalates to a join investigation.
4. Report the measured rate **always paired with the ceiling and the classification breakdown**.
   Within 5–10 points of ceiling = healthy; more than ~10 below = investigate as a probable bug.
**Acceptance.** The report page shows measured-vs-ceiling and a gap breakdown, never a bare number.

### T22. Execute Track B and close the U-matrix `M`
**Do.** Tenant-admin toggle → Workspace Monitoring → tenant extract live → close U1, U2, U4–U13,
U19 with real evidence. Run all 8 per-differentiator acceptance tests in `phase5_validation.md` §3.
Confirm the T2 natural key against real log data.
**Acceptance.** `docs/VALIDATED.md` converting each `[Unverified]` to `[Verified against gateway
vX.Y, <date>]` — with anything still unverified honestly left as such. This document is what makes
the fork trustworthy for another company, which is the stated end goal.

---

# Phase 3 — Forward testing (earn the right to be trusted)

*~10–14 days of effort, 30–60 days of calendar time. This phase is why the tool becomes something
an operator relies on rather than glances at.*

### T23. Fault-injection harness `L`
**Do.** `Invoke-FaultInjection.ps1 -Fault <offline|spool|mashup|refresh|credential|network>` and a
paired `Test-FaultDetection.ps1` that polls gold tables and reports **measured** time-to-detect.
Each fault gets a safe, repeatable, auto-reverting induction:

| Fault | Induction | Safety |
|---|---|---|
| Gateway offline | Two tiers: (1) stop the collector task only — isolates alert logic; (2) `Stop-Service` on the real gateway — proves the full path | (2) test VM only; it interrupts every refresh on that node |
| Disk spool | Lower thresholds near current free space; or grow dummy files in the real spool path across several collector cycles so the 2h forecast has a real trend | Never a shared disk |
| Mashup runaway | (a) benign allocator matching the container regex; (b) a pathological `List.Generate` refresh driving a real container past 6000MB | (b) throwaway dataset only |
| Refresh failure | Force failures at each of the three triage layers **separately** so each confidence tier is independently exercised | Test datasource only |
| Credential expiry | Rotate a test datasource password without updating the gateway; plus a transient self-resolving blip to prove debounce | Test credential only |
| Network | Saturate NIC with a large harmless transfer during a real refresh; or a latency injector on an isolated VM | Reversible; never shared infra |

**Critical for spool:** verify the **proactive forecast alert fires measurably earlier** than the
reactive <5% alert. That two-tier design is the point; "it also fires" is not a pass.
**Critical for mashup:** hold the condition >30 min and verify the documented circuit breaker (max
3 fires/30min/host) actually caps volume. A violated circuit breaker is a P0 regardless of precision.
**Acceptance.** All six induced, each with a measured TTD recorded against the reconciled T4
thresholds. This converts a one-time manual pilot exercise into a repeatable release regression suite.

### T24. Alert log and operator verdict intake `M`
**Problem.** Precision/recall cannot be computed after the fact without a labeling artifact. None
exists. **Build this before shadow mode starts, not after** — retroactive labeling doesn't work.
**Do.** `gold_alert_log` (alert_id, rule_name, fired_at_utc, entity_key, raw condition values,
operator `verdict` TP/FP/pending, `resolution_notes`), populated by every Activator notify action,
with a lightweight intake form. Operator triages within 24h while context is fresh.

### T25. Day-0 calibration `M`
**Problem.** Every threshold in `activator-rules.json` and `measures.dax` (15%/5% spool, 25% error
rate, 80% network, 0.5/0.8 skew) is a generic constant calibrated to nothing.
**Do.** Run the full medallion with Activator **disabled** for the shadow window; compute empirical
p50/p90/p99 per metric per gateway; set each tenant's live thresholds from its own baseline (alert
at its p99, not a shared default). Make this a **required, documented pre-go-live step** for every
adopter, not a one-off.

### T26. Shadow-mode operation `L` *(effort) + 30–60 days (calendar)*
**Do.** Deploy the full stack against real production gateways with every rule routed to a
dedicated `#gateway-monitor-shadow` channel — never to on-call. The operator keeps their current
process in parallel. Minimum 30 days; 60 if refresh cadence is monthly-batch-heavy (30 days won't
show a month-end pattern twice). Measure continuously: alert-fire log with verdicts, baseline
distributions, daily match-rate trend, notebook success rate, real end-to-end latency, and Fabric
CU cost under real load.
**Exit criteria — all required:**
- ≥99% notebook run success rate over the window
- Every rule meets its precision/recall bar (T27)
- **Zero silent misses** — every incident the operator knew about through an independent channel
  appears in the shadow alert log. 100% recall on *known* incidents is the bar before trusting the
  tool on unknown ones.
- Match rate stable within ±5 points across two non-overlapping 2-week sub-windows
- ≥250 gateway queries in the match-rate sample (the statistical minimum for a ±5-point CI at
  p≈0.8 — which is precisely why the one-sitting pilot cannot produce this number)

### T27. Precision/recall analysis and threshold tuning `M` *(+3–6 months calendar for rare rules)*
**Do.** Compute per-rule precision/recall with Wilson score intervals from `gold_alert_log`. Ground
truth must be **independent of the tool's own ingested copy** — check the Admin Portal directly,
not the same REST response the tool already stored, or an ingestion bug falsely confirms its own
alert. Targets: **Tier 1 (notify) ≥80% precision, ≥90% recall** — missing a real incident is worse
than an extra notification. **Tier 2 (autonomous action) ≥98% precision**, and keep it human-gated.
**Honest note.** High-frequency rules clear in 30 days. Gateway-offline and credential-drift fire
1–2×/week in a healthy fleet, so ≥20–30 fires means **3–6 months**. Say this to adopters rather
than implying the 30-day window validates everything.

### T28. Canary promotion `S`
**Do.** Promote one cluster from shadow to live while the rest stay shadow; expand only after the
canary clears the bar. Reuses the existing `gold_cluster_load` grain — no new schema.

---

# Phase 4 — Enterprise readiness

*~25–35 days. Fully detailed in `ENTERPRISE-READINESS.md`; summarized here for sequencing. Tier 0.1
(licensing → Apache-2.0) closed 2026-07-21.*

### T29. Privacy posture on the flagship feature `L`
**Problem.** The headline capability joins queries to real user UPNs and persists them permanently
(bronze 90d / gold 365d, no privacy-driven expiry). The honest framing: the marginal risk is not
novel collection — Microsoft already exposes `ExecutingUser` — it is **durability and joinability**.
Microsoft's own May/June 2026 gateway release consent-gates comparable data with dual admin consent
and a **48-hour purge**; this tool applies neither.
**Do.** `PSEUDONYMIZE_IDENTITY` switch, hashing UPNs **by default** with real values strictly
opt-in (most operator value — which dataset, which pattern, which gateway — survives
pseudonymization); privacy-driven retention on identity-bearing tables specifically, separate from
operational retention; `docs/SECURITY-PRIVACY.md` covering data processed, location, lifetime, DSAR
purge, and controller/processor roles.
**Framing.** Done right this is a *selling point*: native retention is 30 days and enterprises need
longer. "Durable gateway history with a defensible privacy model" beats both "we keep everything
forever" and "use the 30-day native tool."

### T30. Row-level security `M`
**Problem.** The model defines no roles. Every viewer sees every gateway — fails immediately at a
F500 with business-unit or legal-entity separation obligations, and becomes acute once
`ExecutingUser` is in the model.
**Do.** RLS roles keyed on gateway/cluster/workspace, mapped to Entra groups.
**Note.** RLS click-through verification in Desktop is a **non-negotiable human gate** (T39) — an
RLS bug on a model containing UPNs is the worst failure mode to leave unautomated.

### T31. Fleet-shaped configuration `M`
**Problem.** `config.sample.json` assumes one gateway, one spool path, one workspace, one lakehouse.
Enterprise estates are tens-to-hundreds of hosts across clusters and often multiple workspaces.
**Do.** List-shaped inventory config with per-host overrides and per-cluster grouping — and
**confirm the medallion's grain actually holds at that cardinality** rather than assuming it does.

### T32. Infrastructure as code `L`
**Problem.** No Terraform, Bicep, or ARM. Deployment is hand-run notebooks and scripts. Platform
teams do not hand-run notebooks in production.
**Do.** Bicep or Terraform for the Azure/Fabric side (capacity, Key Vault, SPN, workspace,
lakehouse). Adopt **`fabric-cicd`** (now Microsoft-backed with long-term support) plus Fabric
**Variable Libraries** to eliminate hardcoded workspace/lakehouse IDs across dev/test/prod, rather
than hand-rolling item export/import.
**Landmine to document.** Activator items using Power BI or Blob sources, or **UDF actions**,
currently **break Fabric's deployment-pipeline and Git integration entirely**. This repo's rules
read from Delta tables, so it is on the safe side today — but T44 (UDF actions) would walk into it.

### T33. Fleet collector deployment `L`
**Do.** DSC, Intune, GPO, or Ansible path to deploy and upgrade collectors across 100 hosts, with a
documented rollback. Nobody hand-copies a script to 100 Windows hosts.

### T34. Versioning, migrations, and support contract `M`
**Do.** SemVer (MAJOR = Delta schema or semantic-model breaking change; MINOR = additive signals or
pages; PATCH = fixes), tags, `CHANGELOG.md`, `VERSION`, and a stated compatibility matrix ("this
release validated against gateway vX, Fabric SKU Y"). Numbered idempotent migration notebooks with
a `schema_registry` control table.
**DirectLake nuance.** Additive column changes auto-reflect into the model — no migration needed.
The danger is the reverse: **removing or renaming a column a visual depends on crashes the report
with no warning.** Policy: deprecate-then-remove across two MINOR releases, with a pre-flight
measure/visual dependency check before any column drop.
**Why this matters here specifically.** It is what makes `[Verified against gateway vX.Y]` in
`VALIDATED.md` mean anything at all.

### T35. Supply chain and security review pack `M`
**Do.** Pin PowerShell module versions in a `RequiredModules` manifest; hash-pin Python deps;
generate a **CycloneDX SBOM** as a release artifact; wire Dependabot/Renovate with the CI suite as
the safety net; add `SECURITY.md` with private vulnerability disclosure. Prepare answers to the
questions a F500 security review will actually ask: data classification, secret custody, SP
least-privilege proof, **the exact egress hostname list** (T1's firewall conversation), retention
and purge-on-decommission.

### T36. SLOs, error budget, and runbook restructure `M`
**Do.** Define and publish: heartbeat freshness ≤10 min for 99% of 5-min windows/month;
bronze→gold latency ≤20 min p95; alert delivery ≤5 min from breach. Tie each to its T13 canary.
Restructure the runbook to a uniform per-alert shape: Symptom → ranked causes → exact diagnostic
commands → remediation → escalation → related rules. Add a fill-in-the-blank on-call section, since
this ships as a template for other organizations.

---

# Phase 5 — Report completeness and validation

*~10–14 days. Deliberately after Phase 4: `ENTERPRISE-READINESS.md` is right that no F500 rejects a
tool for having 12 pages instead of 18. Pages are table stakes; the join is the moat.*

### T37. Automate everything about the model that can be automated `M`
**Do.** `validate_report_bindings.py` (asserts every visual `queryRef` resolves to a real
measure/column in the TMDL); **Tabular Editor CLI + Best Practice Analyzer** in CI for DAX linting
and antipatterns; TOM structural checks for measures referencing nonexistent columns, DirectLake-
unsupported column types, and an unmarked date table; measure dependency graph for orphans and
cycles; **PBI Inspector** for rules-based report-layout testing against the committed `.pbip`.
**Why.** The Session-1 `InvalidColumnName` regression was exactly this class of bug and would have
been caught at the model layer.

### T38. Report parity and trend pages `L`
**Do.** The six RuiRomano-parity pages Nexus lacks (Logs Explorer, Mashups Logs, Requests, Counters
Deep-Dive, Gateway Profile, Alerting State) plus historical/trend pages (`NumberDays`/`SinceDate`),
which are the largest single gap. Source material is on branch `worktree-report-buildout-v1`.
Add the T21 measured-vs-ceiling attribution panel.

### T39. Human validation gates `M`
**Do.** What cannot be automated, done deliberately rather than skipped: visual/layout QA across
all pages in Desktop; DirectLake fallback-to-DirectQuery behavior via DAX Studio; manual
verification of the DirectLake DAX caveats `measures.dax` itself flags (`NOW()`,
`PERCENTILEX.INC`, `TREATAS`/`USERELATIONSHIP`) by hand-computing an expected value and comparing;
**RLS click-through per role**; executive design sign-off.
**Acceptance.** A recorded pass/fail per item, not a general "looks good."

---

# Phase 6 — Autonomy L1: real-time detection

*~8–12 days. Everything from here is net-new capability, all on GA Fabric primitives.*

### T40. Stream telemetry via Eventstream custom endpoint `L`
**Do.** Add a streaming path alongside the batch land: collectors publish to an **Eventstream
Custom Endpoint** (Event Hub-compatible, SAS or Entra managed identity, outbound 443 only, **no VPN
required**) into an Eventhouse KQL database. Keep batch for history and bulk CSV; stream the
signals where latency matters (heartbeat, spool, mashup, error rate).
**Set expectations honestly.** Realistic end-to-end is **2–15 seconds**, not sub-second. Activator's
"subsecond" figure applies to its in-memory rule evaluation *after* an event has landed, not to the
on-prem→cloud trip. `[UNVERIFIED]` The 2–15s figure is corroborated across multiple independent
analyses but no Microsoft SLA page states a hard number.
**Capacity note.** Microsoft guidance: F8 to avoid overage on an always-on Eventhouse, F4 minimum
for Eventstream — above the F2 the pilot uses. Budget for it before committing to streaming.

### T41. Activator rules as code `M`
**Do.** Move rule management from GUI to source control: author once in the UI, extract via **Get
Item Definition**, template the resulting JSON, and manage through the Fabric REST API
(`POST /v1/workspaces/{id}/reflexes`) or the `fabric_activator` Terraform resource.
**Honest constraint.** The DSL is not hand-authorable — its `templateId`/`steps`/`rows` structure
has no published schema or SDK object model. Plan on round-tripping through the GUI at least once
per rule shape. `[UNVERIFIED]` The Terraform resource's production hardening.
**Design against the documented limits:** 10,000 events/sec per rule (exceeding it **stops the
rule**); email/Teams 30 per recipient/hour vs **500 per channel/hour** — so route P1 alerts to a
Teams **channel**, not to individuals, or a fleet-wide outage silently drops messages exactly when
they matter most. Performance degrades past ~10,000 unique object IDs.
**Also fix:** Activator's Power BI-sourced ingestion is owned by a **single user identity** — if
that user loses access or rotates credentials, **ingestion and rule evaluation stop with no
notification**. Own it under a dedicated service principal covered by identity-lifecycle policy.

---

# Phase 7 — Autonomy L2: prediction

*~8–15 days. Start cheap; escalate only on evidence.*

### T42. Native KQL anomaly detection and forecasting, scheduled `M`
**Do.** Promote `02_anomaly_forecast.kql` from reference query to scheduled job:
`series_decompose_anomalies()` for seasonal CPU/error anomalies and `series_decompose_forecast()`
for spool exhaustion, run on a pipeline schedule, writing to a `gold_predictions` table the report
and Activator both read. This is the **cheapest real win in the entire autonomy stack** — no
training, no MLflow, no model registry.
**Acceptance.** A forecast alert fires measurably before the reactive threshold alert on the T23
spool injection.

### T43. Escalate to multivariate only if the univariate approach under-detects `L`
**Gate, not a task to start.** Only build this if T42 demonstrably misses correlated failure modes
(CPU + memory + queue depth degrading together before a crash). If so: Eventhouse **native
multivariate anomaly detection** first (no custom training), then SynapseML/AutoML in a scheduled
notebook with MLflow registry and drift monitoring — real MLOps, real ongoing cost.
**Do not build this speculatively.** The failure mode here is a model nobody validates quietly
producing worse predictions than a threshold.

---

# Phase 8 — Autonomy L3: explanation

*~5–8 days. The highest value-per-effort item in the whole roadmap.*

### T44. Fabric Data Agent for root-cause explanation `M`
**Do.** Stand up a Data Agent (GA) scoped to the Eventhouse and Lakehouse telemetry, tuned with
custom instructions and up to 100 example query pairs per source teaching it the domain vocabulary
("gateway", "cluster", "spool", "mashup container", "queue depth"). Supports NL2KQL, NL2DAX, and
NL2SQL across up to five sources. Surface it as an incident-explanation panel in the report and as
enrichment on alert payloads.
**Prerequisites.** F2+ (already met), cross-geo AI processing enabled.
**Cost, with real numbers.** 200 CU-seconds per 1,000 input tokens, 600 per 1,000 output — roughly
**400 CU-seconds (~6.67 CU-minutes) per request**, billed as a background job against a designated
Fabric Copilot capacity. Prompt caching reduces repeated schema/instruction context. Budget it;
don't let it surprise a capacity.
**Architectural constraint to design around now.** The Data Agent is **permanently read-only by
design** — it *"[doesn't] trigger anomaly detection jobs, notebooks, or other write or action
workflows."* It can explain an incident; it can never be the thing that acts. If L3 is to feed L4,
it must hand off to an Activator rule or function. Answers are also capped at 25 rows × 25 columns
— it is built for conversational insight, not bulk export.

---

# Phase 9 — Autonomy L4: closed-loop self-healing

*`XL`, ~20–30 days. **The gate decision.** This is a distinct systems-integration project with no
vendor-supported path — see "hard ceiling" above. Shipping v1.0 at Phase 8 and treating this as
v2.0 is a legitimate and probably correct call.*

### T45. Decide and document the on-prem action bridge `L`
**Problem.** Fabric cannot reach into a gateway host. UDFs explicitly do not support on-prem/VNet
gateways; the gateway opens only outbound 443 by design; no restart API exists in any REST surface
or PowerShell module (`Disable` is a routing flag, not service control).
**Do.** Choose one, and write the rejected options down with reasons:
1. **Azure Arc-enabled server + Run Command** — likely best. Uses an existing Microsoft control
   plane, RBAC-governed, audited, no inbound ports, and many enterprises already run Arc.
2. **Azure Relay Hybrid Connection** — a local listener dials out; a UDF calls the relay. More
   moving parts, fully under your control.
3. **Poll-based agent** — the gateway host polls a command queue (Service Bus / a Delta table) and
   executes approved actions. Simplest security story (still outbound-only), highest latency.
**Non-negotiable design constraints.** Least-privilege identity scoped to exactly the permitted
actions; every decision and action written to an append-only audit log; an allowlist of permitted
actions (never arbitrary command execution); and a global kill switch.
**Acceptance.** A design doc reviewed against the enterprise security questions in T35 *before* any
code — this is the component most likely to fail a customer security review.

### T46. Closed-loop remediation with tiered approval `L`
**Do.** Implement detect → explain → **decide** → act → **verify** as an explicit state machine:
- **Tier 0 (auto, no approval):** provably safe, reversible, zero-blast-radius — clear a spool
  directory of files older than N days; re-run a failed pipeline stage.
- **Tier 1 (auto with notification):** restart a mashup container process.
- **Tier 2 (human approval in Teams, with timeout to no-op):** restart the gateway service.
- **Tier 3 (never automated):** anything touching credentials, cluster membership, or config.
**Verify is mandatory, not optional.** After every action, confirm recovery from an independent
signal and **automatically roll back or escalate on failure**. An action that silently fails is
worse than no action, because the alert cleared.
**Guardrails** (from Azure Well-Architected RE:07, the only applicable official doctrine — written
for Azure PaaS targets, so adapted): dead-letter anything that cannot be auto-resolved, rate-limit
actions per host per window, and refuse to act during a declared maintenance window.

### T47. Autonomy safety validation `M`
**Do.** Before any Tier 1+ action runs against a real gateway: dry-run mode logging intended
actions without executing, for a full shadow window; **≥98% precision on the triggering rule**
(T27) as a hard gate; a documented blast-radius analysis per action; game-day exercises proving
the kill switch works under load.
**Rule.** No remediation tier goes live until its triggering detection has cleared shadow mode with
its precision bar met. Autonomy inherits every false positive underneath it.

---

# Phase 10 — Release and adoption

### T48. Application and security review pack `M`
**Do.** Assemble what an enterprise review actually consumes: SBOM, `SECURITY.md`, the privacy/DPIA
pack from T29, SP least-privilege matrix, egress hostname list, the compatibility matrix, `VALIDATED.md`,
SLOs, and the architecture decision record for T45. Dry-run it against a real reviewer if a pilot
adopter will provide one.

### T49. v1.0.0 release `S`
**Do.** Tag, changelog, compatibility statement, upgrade guide. Publish honestly: what is verified,
what is simulated, what is unverified — the labeling discipline this project already follows is a
genuine differentiator and should survive into the release notes rather than being smoothed over
for marketing.

### T50. Fork onboarding and the pilot-adopter program `M`
**Do.** `UPSTREAM.md` documenting the base SHA and the `git remote add upstream` sync workflow
(graduate to **Copier** only if per-fork variable substitution becomes necessary — Copier has
update-in-place semantics that Cruft lacks). A quickstart that gets a new adopter to first data in
under a day. Structure the pilot-report issue template around the U-matrix so community reports
convert `[Unverified]` → `[Verified]` on gateway versions, tenant shapes, and SKUs **no single
operator can access alone**. That mechanism is the entire reason the Apache-2.0 decision was correct.

---

## Sequencing summary

```
Phase 0  Correctness blockers        15-20d   ← START HERE, gates everything
Phase 1  Self-observability          8-12d    ← can partly parallel Phase 0
Phase 2  Live pilot                  5-8d     ← needs Phase 0; one paid sitting
Phase 3  Forward testing             10-14d   + 30-60d calendar (3-6mo rare rules)
Phase 4  Enterprise readiness        25-35d   ← parallel with Phase 3's calendar time
Phase 5  Report completeness         10-14d
Phase 6  L1 real-time detection      8-12d
Phase 7  L2 prediction               8-15d
Phase 8  L3 explanation              5-8d     ← best value-per-effort in the roadmap
─────────────────────────────────────────────  v1.0 ships here
Phase 9  L4 self-healing             20-30d   ← gate decision; no vendor-supported path
Phase 10 Release and adoption        5-8d
```

**The three decisions that need a human, not an engineer:**
1. **Now:** confirm `gwmoncap01`'s state — it may have been billing since 2026-07-06 (T19).
2. **After Phase 2:** if the measured match rate lands far below its ceiling with no `SUSPECT_BUG`
   explanation, the value proposition changes and Phases 4–9 should be re-scoped before investment.
3. **Before Phase 9:** ship v1.0 at Phase 8, or take on L4 as a distinct project with its own
   security review and risk budget.

---

*Roadmap authored 2026-07-21 from a four-agent audit: code completeness (file:line verified),
Fabric 2026 capability research (Microsoft Learn primary sources), SRE/production hardening
practice, and validation strategy design. Three critical-path findings were re-verified directly
against source rather than taken from the audit. Items marked `[UNVERIFIED]`, `[ASSUMPTION]`, or
`[RISK]` are flagged rather than estimated.*
