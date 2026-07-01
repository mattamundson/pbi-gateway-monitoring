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

> **Provenance of U14–U16:** surfaced by a 2026-07-01 static code review, not yet reproduced on a live host. They are code-quality/correctness items (not new unknowns about the platform) — fold their fixes in during the pilot, and add a regression case to `starter/tests/` where the logic is Spark-free.

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
