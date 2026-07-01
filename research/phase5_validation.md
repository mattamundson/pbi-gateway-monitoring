# Phase 5 — Validation & Pilot Runbook

**Purpose:** Prove the build-new gateway monitoring tool works against a **live Fabric workspace + real On-Premises Data Gateway**, and confirm or refute every `[Unverified]` / `[Assumption]` in the starter code before it is trusted in production.

> **Why this phase exists:** All Phase 4 starter code is reference implementation. It compiles and its pure-Python logic is unit-tested (CSV parsing, EvaluationContext normalization), but **no PySpark/Fabric/PowerShell-on-Windows path has run end to end.** This runbook is the gate between "reference" and "production."

---

## 0. Prerequisites for the pilot

- One **non-production Fabric workspace** on F8+ capacity (F16 recommended).
- One **real On-Premises Data Gateway** (standard mode) processing at least a few refreshes/queries — ideally a cluster of 2+ nodes to exercise fleet/skew logic.
- Service principal + Entra security group + Key Vault per `starter/config/credentials.md`.
- Gateway performance logging **on** (default) — confirm the four log files exist under `ReportFilePath`.
- (Optional) An existing **FPM deployment** if you want to validate the FPM bridge path.

---

## 1. Deploy order (dependency-correct)

| Step | Action | File | Success signal |
|---|---|---|---|
| 1 | Provision SP + Key Vault + tenant settings + gateway Admin role | `config/credentials.md` | SP can call `GET /gateways` (200, not 401) |
| 2 | Create Lakehouse; set notebook params | `config/config.sample.json` → `config.json` | ABFSS path resolves in a notebook |
| 3 | Deploy collectors to a gateway node; schedule | `collectors/*.ps1` | JSON files appear in `Files/bronze_landing` |
| 4 | Run bronze ingest | `notebooks/01_bronze_ingest.py` | `bronze_*` Delta tables created, row counts > 0 |
| 5 | Run silver correlate | `notebooks/02_silver_correlate.py` | `silver_*` tables; triage + attribution rows |
| 6 | Run gold aggregate | `notebooks/03_gold_aggregate.py` | `gold_*` tables; fleet + skew computed |
| 7 | Bind semantic model (DirectLake) + measures | `semantic-model/measures.dax` | Measures resolve; report renders |
| 8 | Configure Activator rules | `alerting/activator-rules.md` | Test alert fires on a forced condition |
| 9 | (Optional) Enable FPM bridge | `01_bronze_ingest.py` `USE_FPM_BRIDGE=True` | FPM Eventhouse Delta reads into bronze |

---

## 2. The `[Unverified]` / `[Assumption]` confirmation matrix

Every load-bearing unknown, how to test it, and what to do if it's wrong. **This is the core of Phase 5.**

| # | Item | Where | How to verify | If wrong → fix |
|---|---|---|---|---|
| U1 | **EvaluationContext encoding** (direct JSON vs base64) | `01_bronze_ingest.py` `parse_evaluation_context` | Inspect 10 raw `QueryStart` rows; check if `EvaluationContext` starts with `{` or is base64. The normalizer auto-detects, so just confirm `artifact_id` is non-null for Fabric-workload rows | If neither: capture the real format, extend `_normalize_eval_context`. Attribution (#3) degrades gracefully to null, not a crash |
| U2 | **JSON key names** `$.artifactId` / `$.artifactKind` | same | Parse a decoded EvaluationContext; confirm exact key casing/nesting | Update the `get_json_object` paths to the real keys |
| U3 | **CSV quoting** in gateway logs | `01_bronze_ingest.py` parser | Find an `ErrorMessage` containing a comma; confirm the row parses into the right field count (now uses RFC-4180 csv reader) | Already hardened; if logs use a non-standard delimiter/escape, set `csv.reader` dialect |
| U4 | **`Get-DataGatewayClusterDatasource` cmdlet name** | `Get-GatewayInventory.ps1` | `Get-Command -Module DataGateway \| Select Name` — confirm the actual datasource cmdlet | Swap to the real cmdlet name from the module |
| U5 | **`Get-DataGatewayInfo` SP 401 bug** | inventory collector | Run inventory under the SP; if 401, confirm latest fabric-toolbox `main` + SP gateway Admin role | If still 401: fall back to user-delegated auth for inventory only, or Power BI REST `GET /gateways` |
| U6 | **Activator rule DSL/syntax** | `alerting/activator-rules.md` | Build one rule in the Activator UI from the spec; confirm it fires | Adjust rule spec to match the real Activator config surface |
| U7 | **DirectLake vs DirectQuery** for the model | `measures.dax`, architecture §4 | Confirm gold Delta tables are DirectLake-eligible (V-Order, supported types); check fallback-to-DirectQuery events | If DL falls back often: OPTIMIZE/V-Order gold, or accept DQ for hot tables |
| U8 | **Spool directory path** (service-account variance) | `Collect-DiskSpool.ps1` | Confirm actual spool path for this gateway's service account | Set explicit path override in config |
| U9 | **Fabric notebook ABFSS pathing** | all notebooks | Confirm `LAKEHOUSE_PATH` pattern resolves in this workspace | Use the workspace's actual OneLake path/mount |
| U10 | **`mergeSchema=true` on append** | `_write_bronze_delta` | Force a new column in a test log; confirm append succeeds without error | If it errors: pre-evolve schema or use Delta `ALTER TABLE` |
| U11 | **RequestId attribution fuzziness** | `02_silver_correlate.py` | Measure match-rate of gateway `RequestId` → Activity Events over a known window; record % matched | Document real match rate; never present as exact — label the report visual accordingly |
| U12 | **Paginated reports not logged / Premium queries sometimes missed** | coverage | Run a paginated report + a Premium-capacity refresh; confirm absence in logs | Document as known coverage gaps in the report footer |
| U13 | **StreamBeforeRequestCompletes interaction** | `Collect-DiskSpool.ps1` | Check the gateway config value; correlate with spool behavior | Note the setting's state alongside spool metrics |
| U14 | **Silent type-cast drops** | `01_bronze_ingest.py` `_cast_value` (~L218-236) | Feed a row with an unparseable numeric (e.g. `"N/A"` in a `(ms)` column); confirm whether the bad value is silently coerced to null with no trace | Log the offending `(column, raw_value, row_id)` to a `_cast_errors` sidecar (or a `quarantine` column) so data-quality issues are visible, not masked |
| U15 | **Time-window join fan-out** | `02_silver_correlate.py` triage join (~L207-224) + network correlation (~L387-439) | On a busy window, count output rows vs input QE rows; if N refresh/activity records fall in one ±window, each source query multiplies | Rank-and-pick the nearest-timestamp match (or `row_number()` dedup) **before** the join so one gateway query maps to at most one correlated record; assert output row-count ≤ input |
| U16 | **Service-account path assumptions** | all `collectors/*.ps1` (e.g. `Collect-GatewayLogs.ps1`, `Collect-DiskSpool.ps1`, `Get-GatewayInventory.ps1`) | Run collectors on a gateway whose service account is **not** `PBIEgwService`; confirm they resolve the real log/spool paths | Read all host paths from `config.json` (already modeled in `config.sample.json` `gateway.logPath`/`spoolPath`); never hardcode `PBIEgwService` — fall back to discovering the service account from the gateway service registration |
| U17 | **JSON ingest format mismatch (multi-line vs JSONL)** | `01_bronze_ingest.py` all six `spark.read.json` calls (gateway_logs, network_metrics, event_log, disk_spool, gateway_inventory, refresh_history) | Point each ingest at a real collector output file; confirm the DataFrame is NOT a single `_corrupt_record` column | The six collectors write **pretty-printed** `ConvertTo-Json` (a single multi-line root object); Spark's `read.json` defaults to `multiLine=false` (JSONL, one object per line) and corrupts a pretty object. Add `.option("multiLine", True)` to each read (only `Collect-MashupProcesses` emits JSONL, read via the `read_mashup_processes` lib helper — it is unaffected) |

| U18 | **Illegal column names break the Delta write (pain #4 regression)** | `gateway_bronze_lib.read_gateway_csv` + `01_bronze_ingest` write | Feed a CSV with an **unknown** unit-suffixed header (e.g. `NewMetric(ms)`); confirm the bronze Delta/Parquet write does not raise `InvalidColumnName` | **Live-tenant CONFIRMED (peer, 2026-07-01):** gateway exports carry `(ms)`/`(bytes)`/`(byte/sec)` headers; Delta/Parquet + Fabric reject `( ) ,;{}=/` in column identifiers. `rename_map` only fixes *known* columns; unknown/new ones (the pain #4 upgrade case) flowed through illegal → write fails. Add `_sanitize_columns()` (runs after `rename_map`) that scrubs any remaining illegal-char header to a safe name (`NewMetric(ms)`→`NewMetric_ms`). NOTE: this fixes the **notebook** path only; Fabric **Load-to-Tables** fails earlier at platform schema-inference — route raw gateway CSV through the notebook, not Load-to-Tables |

> **Provenance of U14–U18:** surfaced by 2026-07-01 static code review + end-to-end contract tracing + a peer live-tenant pilot, not all reproduced on a live host. They are code-quality/correctness items (not new unknowns about the platform) — fold their fixes in during the pilot, and add a regression case to `starter/tests/` where the logic is Spark-free.
>
> **Status (2026-07-01 — fixes applied):**
> - **U16 — FIXED & VERIFIED.** The collectors read `config.json` paths, but read the *flat* keys (`$config.gatewayLogPath`/`$config.spoolPath`) while `config.sample.json` nests them (`gateway.logPath`/`gateway.spoolPath`). Under `Set-StrictMode -Version Latest` that flat read **threw** (not silently fell back), so the documented override *crashed* the collector on a custom service account. Now reads the nested key StrictMode-safely (legacy flat key still honored). Verified under StrictMode: nested resolves, no-`gateway`-key returns empty without throwing, legacy flat honored.
> - **U14 — FIXED & VERIFIED (Spark-free logic).** `cast_query_execution` now adds an additive `_cast_errors` array column listing columns whose present-but-unparseable raw value cast to null (existing values unchanged). Detection rule mirrored Spark-free in `gateway_bronze_lib.cast_error_columns()` and covered by 6 assertions in `starter/tests/test_parser.py` Test group 3 (all pass). The Spark wiring itself is compile-checked; confirm in-Fabric during the pilot.
> - **U15 — FIXED, COMPILE-CHECKED ONLY.** Both time-window joins in `02_silver_correlate.py` now `row_number()`-dedup to the nearest-timestamp match per `RequestId` (structurally guarantees output ≤ input). This is **not execution-verified** — Spark column-resolution/window semantics run only in a session; marked `[Fabric-verify-pending]` inline. Validate row-counts during the pilot.
> - **U18 — FIXED & EXECUTION-VERIFIED (real Spark run 2026-07-01).** Peer live-tenant pilot hit `InvalidColumnName` on `(ms)`/`(bytes)` gateway headers; the fix path they documented pointed at a `_sanitize_columns()` helper that **did not exist**. Root cause: `read_gateway_csv` renamed only *known* columns via `rename_map`; unknown/new unit-suffixed columns (the pain #4 gateway-upgrade case) flowed through with `( ) /` intact and would break the Delta/Parquet write. Implemented `_safe_col_name()` (pure-Python, Spark-free) + `_sanitize_columns()` (runs after `rename_map`), scrubbing any remaining illegal-char header (`NewMetric(ms)`→`NewMetric_ms`, `DiskRead(byte/sec)`→`DiskRead_byte_sec`), collision-safe. **Proven on real Spark:** a CSV with unknown `UpgradeMetric(ms)`/`NewSize(bytes)` columns read clean (zero illegal chars) AND the Parquet write succeeded (would raise `InvalidColumnName` without the fix); known columns still resolve to their canonical names; full smoke test still 7/7. 6 Spark-free assertions added to `test_parser.py` Test group 4. Scope: fixes the **notebook** ingest path; Fabric **Load-to-Tables** fails earlier at platform level (route raw CSV through the notebook, per U18).
> - **U17 — FIXED & EXECUTION-VERIFIED (real Spark run 2026-07-01).** Found by tracing the collector→ingest contract end to end: all six JSON bronze ingests called `spark.read.json` with the default `multiLine=false`, but six of the seven collectors emit pretty-printed multi-line `ConvertTo-Json` output. Spark's default JSONL mode would parse each physical line separately → `_corrupt_record` → **every one of those six ingests would fail on real collector data.** The tell: `Collect-MashupProcesses` is the only collector emitting JSONL (`ConvertTo-Json -Compress` per line), which is exactly why the mashup path is the only JSON ingest the local smoke test exercises successfully. Fixed by adding `.option("multiLine", True)` to all six reads. **Now confirmed on a real engine:** `run_local_smoke.py` STEP 7 ran green under PySpark 3.5.8 and reported `bronze_refresh_history: 18 refresh records (multiLine JSON + explode(RefreshRecords) OK)` — the pretty wrapper parsed correctly, no `_corrupt_record`. Added a `refresh_history_synthetic.json` fixture (generator: `gen_refresh_history()`) and the STEP 7 Level-1 exact-`RequestId` triage join, which produced **3 triage rows from 5 failed gateway queries**, each labeled `EXACT_REQUESTID` with the Service `ServiceExceptionJson` reconciled — so the pain #2 refresh leg, previously in the smoke test's *NOT exercised* list, is now exercised and passing.

### Local Spark smoke harness (reproducible — 2026-07-01)

`run_local_smoke.py` exercises the **real** bronze→silver→gold transform logic (not the Fabric I/O) against the synthetic fixtures, so the notebook code can be run off-tenant. On 2026-07-01 the full run passed **all 7 steps, zero FAIL/SKIP**, on an isolated env:

```bash
# Isolated, reproducible — does not touch the base Python or the system PATH.
conda create -n pbi-spark -c conda-forge python=3.11 openjdk=17 -y
conda activate pbi-spark
pip install "pyspark>=3.5,<3.6" "delta-spark>=3.2,<3.3"   # resolves to pyspark 3.5.8 + delta-spark 3.2.1
python starter/tests/generate_synthetic_logs.py           # (re)build fixtures incl. refresh_history_synthetic.json
# Windows only: Spark needs Hadoop winutils on HADOOP_HOME (Linux/WSL does not).
#   winutils.exe + hadoop.dll for hadoop-3.3.x -> <HADOOP_HOME>/bin ; export HADOOP_HOME
JAVA_HOME=<env>/Library HADOOP_HOME=<winutils-dir> python starter/deploy/run_local_smoke.py
```

Result (PySpark 3.5.8 / OpenJDK 17): STEP 1 bronze QE 20 rows · STEP 2 artifact identity · STEP 3 silver join 20 rows · STEP 4 gold perf 11 buckets + all 9 `measures.dax` deps · STEP 5 gold health 30 rows · STEP 6 mashup pain #5 (4 runaways) · STEP 7 pain #2 refresh leg (18 refresh records via multiLine+explode; 3 exact-`RequestId` triage rows). This graduates the transform logic (casts, artifact identity, the multiLine JSON reads, and the Level-1 triage join) from *compile-checked* to *executed on a real Spark engine*. Still Fabric-only (unchanged): ABFSS/OneLake I/O, DeltaTable merge/SCD-2, the U15 Level-2 time-window dedup on a busy window, Activity-Events attribution, Activator, FPM bridge. A benign post-run `Failed to delete …antlr4-runtime.jar` line is the known Windows Spark temp-cleanup warning (fires after all steps pass).

> **Portability fix applied:** `run_local_smoke.py`'s env check looked only for a bare `bin/java`, which never exists on Windows (`java.exe`), so it would have `[SKIP]`ped on every Windows host. It now accepts `java` **or** `java.exe`.

#### Two complementary local proof paths — which to run

The repo has **two** real-PySpark validation scripts. They overlap (both run Spark on synthetic data) but answer different primary questions — keep both:

| Script | Primary question it answers | Run it when |
|---|---|---|
| `starter/deploy/run_local_smoke.py` | **Does the notebook transform code compose on a real Spark engine?** bronze→silver→gold, incl. the U17 `multiLine` reads against the collectors' exact pretty-JSON wrapper and the pain-#2 STEP 7 exact-`RequestId` triage leg. | After changing any **notebook transform code** (`0*_*.py`, `gateway_bronze_lib.py`) — fast smoke that the pipeline still runs. |
| `starter/tests/validate_pipeline_sim.py` (+ `simulate_tenant.py`) | **Do the joins correlate correctly on realistic linked data?** Identity join #3 (RequestId == OperationId → ExecutingUser/ItemName) with the match-rate asserted in [0.70, 0.95], triage #2 root-cause buckets, fleet/skew across 3 nodes. | To prove the **correlation logic** and match-rate behaviour, or after changing join/attribution logic in `02_silver_correlate.py`. |

**Both verified green under the one harness (2026-07-01).** Under the `pbi-spark` env (Python 3.11 / PySpark 3.5.8 / OpenJDK 17): `run_local_smoke.py` = 7/7 steps PASS; `validate_pipeline_sim.py` = all 6 pain-point checks PASS with identity-join match-rate **87.50%** (inside its asserted [0.70, 0.95]). One invocation detail: `validate_pipeline_sim.py` imports `gateway_bronze_lib` without adding the notebooks dir to `sys.path`, so run it with `PYTHONPATH=starter/notebooks` (or from that dir); `run_local_smoke.py` inserts the path itself and needs no `PYTHONPATH`.

**Environment note:** `validate_pipeline_sim.py`'s header documents in-code workarounds for a **Python 3.14 + PySpark 3.5.1** incompatibility (`array_compact` instead of a `filter` lambda; file-based data to avoid a cloudpickle C-stack overflow). The conda harness above pins **Python 3.11**, which sidesteps that incompatibility at the environment level — confirmed by the green run above. Prefer the pinned env over per-script version workarounds.

### #2 refresh-triage join key — desk-verification split (2026-07-01)

The new `Collect-RefreshHistory.ps1` (the missing Service-side leg of the #2 triage join) depends on the Power BI **REST refresh-history** record's `requestId` matching the gateway log `RequestId`. Desk verification against primary sources found this is **two links, not one**, and only one is confirmed:

| Link | State | Source |
|---|---|---|
| AS engine `XmlaRequestId` == gateway log `RequestId` column | **[Desk-Verified 2026-07-01]** | MS Learn `service-gateway-onprem-tshoot`; Chris Webb (crossjoin.co.uk). This is the same engine→gateway chain the identity join (#3 / U11) already rests on. |
| REST refresh-history `requestId` (the field this collector emits) == that `XmlaRequestId` | **[Unverified]** | REST reference (`datasets/get-refresh-history`) documents `requestId` only as *"The identifier of the refresh request. Provide this identifier in all service requests."* — no documented equivalence to `XmlaRequestId`/gateway `RequestId`. |

**Phase-5 test (add alongside U11):** over a known window, measure the match rate of REST refresh-history `requestId` → gateway-log `RequestId` for gateway-served refreshes. If the REST field does **not** match, the fix is to source the linkage from Log Analytics/Workspace Monitoring `XmlaRequestId` (the verified chain) instead of the REST field. Report the match rate honestly; never present the #2 join as exact until this specific link is measured.

---

## 3. Per-differentiator acceptance tests

| Differentiator | Test | Pass criteria |
|---|---|---|
| **#1 Real-time alerting** (pain #1) | Stop the gateway service; wait one heartbeat interval | Activator fires an offline alert within (interval + processing) ≤ ~2–3 min |
| **#5 Schema-adaptive parser** (pain #4) | Inject a log file with a **new column added mid-schema**; re-run bronze | Ingest succeeds, new col lands in `_extra_cols` (or as a real col via mergeSchema), **no DataFormat.Error** |
| **#4 Network metrics** (pain #7) | Run a large refresh; capture NIC bytes/sec during it | `bronze_network_metrics` shows throughput; correlates in time with a high `SpoolingTotalDataSize` query |
| **#2 Unified triage** (pain #2) | Force a refresh failure (e.g., stop the source DB mid-refresh) | `silver_triage` row joins the service refresh error + gateway `ErrorMessage` + any Event Log entry in the same window, with a confidence label |
| **#3 Best-effort attribution** (pain #3) | Trigger an on-demand refresh of a known dataset | `silver_identity_attribution` links that `RequestId` → dataset, flagged **best-effort**; record the match rate (U11) |
| **Fleet + skew** (pain #6) | Run refreshes across a 2+ node cluster | `gold` skew score reflects real per-node load; fleet page lists all nodes |
| **Disk-spool alert** (pain #9) | Lower the spool-free threshold below current free space | Activator fires a disk alert |
| **Credential drift** (pain #10) | Expire/lock a datasource credential | Datasource status flips; credential-drift alert fires |

---

## 4. Pilot exit criteria (go/no-go for production)

- [ ] All 3 notebooks run end-to-end in Fabric with no runtime errors.
- [ ] Every U-item (U1–U13) is confirmed or has a documented fix applied.
- [ ] All 8 per-differentiator acceptance tests pass (or a gap is documented with a workaround).
- [ ] Attribution match-rate (U11) measured and displayed honestly (not as "exact").
- [ ] Alerting proven to fire for offline, disk-spool, and credential-drift conditions.
- [ ] Known coverage gaps (paginated reports, Premium miss) noted in the report UI.
- [ ] `config.json` uses Key Vault refs only — no secrets committed (`.gitignore` enforced).

---

## 5. What to record for the repo

After the pilot, update the repo with a `docs/VALIDATED.md` capturing: gateway version tested, EvaluationContext encoding found, RequestId attribution match-rate, DirectLake eligibility result, and any cmdlet/API corrections. This turns `[Unverified]` labels into `[Verified against gateway vX.Y, June 2026]` and is what makes the fork trustworthy for the next adopter.

---

## 6. Honest limitations that a pilot will NOT fix (design constraints, not bugs)

- **Query→identity is inherently fuzzy** — the gateway log has no `DatasetId`/`UserId`; time-window + RequestId is the ceiling until Microsoft adds identity to the schema.
- **Network is host-level, not per-query** — NIC counters show host throughput; attributing a specific byte to a specific query is correlation, not measurement.
- **VNet gateways are out of scope** (v1) — no host to run collectors on; see architecture §VNet for the v2 REST-pull path.
- **Paginated reports + some Premium queries aren't in the gateway logs at all** — no tool can surface what the gateway doesn't log.

---

*Phase 5 runbook complete. Execute against a live environment to graduate the starter code from reference to production. Every unknown has a test and a fallback — the tool is honest about what remains to be proven.*
