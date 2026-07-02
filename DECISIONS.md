# Decisions & Changelog

A condensed, intentional record of the key architectural decisions and their rationale —
the durable replacement for the raw session transcript (deliberately purged for hygiene).
Read this to understand *why* the repo is shaped the way it is.

---

## Architectural decisions

| # | Decision | Rationale |
|---|---|---|
| D1 | **Build-new Fabric-native tool + optional FPM bridge** (not a fork of FPM) | A hard FPM fork inherits its KQL/Eventhouse lock-in and "customizations revert on update" problem, undermining "anyone can fork and use it." Standalone core keeps forkability; an optional adapter ingests FPM data for existing users. |
| D2 | **Delta medallion (bronze→silver→gold) in OneLake, native Spark** | Matches the target stack; gives retention + custom analytics FPM's KQL store doesn't. Native `spark.read.csv` (PERMISSIVE, mergeSchema) scales and is RFC-4180-correct. |
| D3 | **Column-NAME-based, schema-adaptive parsing** | The official Microsoft PBIT breaks with `DataFormat.Error` on gateway-version column additions (positional parsing). Name-based + `_corrupt_record` survives drift — a real differentiator. |
| D4 | **UDF-free identity extraction** (native Spark SQL `unbase64`/`get_json_object`) | Faster than a Python UDF (runs in the JVM) and avoids cloudpickle serialization issues on newer Python. |
| D5 | **VNet gateways descoped from v1** | VNet has no host for PowerShell collectors; telemetry is a portal/REST Parquet pull — architecturally disjoint. Deferred to a v2 sub-architecture. |
| D6 | **Identity attribution via `RequestId == XmlaRequestId` join to Workspace Monitoring** | The flagship finding: gateway logs' `RequestId` matches `OperationId`/`XmlaRequestId` in `PowerBIDatasetsWorkspace`, yielding `ExecutingUser`+`ItemId`+DAX. Breaks the "attribution is permanently fuzzy" ceiling. **[Unverified in-tenant — the pilot must confirm.]** |
| D7 | **Network cost via Windows ETW** (`Microsoft-Windows-TCPIP`/`Kernel-Network`) | eBPF is blocked on Windows in 2026; ETW is the native equivalent for per-PID bytes+RTT. Breaks the network-blindspot ceiling. |
| D8 | **Honesty charter: label everything** | Every claim tagged `[Feasible-now]`/`[Unverified]`/`[Blocked-by-platform]`; every code file `[STUB]`/`[ADAPTED]`/`[NET-NEW]`. Prevents over-selling unproven capability. |
| D9 | **Roadmap: v1 alerting+parser → v2 network+triage → v3 identity → v4 OTLP bus → v5 ML → v6 OTel profiler** | Sequenced by (pain ÷ effort). `[Feasible-now]` items first; `[Experimental]` frontier gated on pilot. |
| D10 | **Databricks-first compute evaluated, DEFERRED — Fabric-native stays canonical** | A 2026-07-02 session pivoted compute to Azure Databricks (Unity Catalog `dbw_gatewaymon_dev`, admin-API ingestion) because the Fabric **trial** Spark pool throttled (HTTP 430) and dropped kernels. But that motivation is *validation reliability*, which the local **`pbi-spark` conda harness** already solves with no tenant/trial (see `test_medallion_spark.py`). So Databricks is **not required for validation** — it stays an *optional production-compute backend* (OneLake = storage contract, Fabric = serving). The Databricks admin-API ingestion is **blocked on a tenant-admin SPN grant** (Amo-gated). The coarser `gateway_id`/`dataset_id` join used there does NOT supersede the canonical D6 identity join. Full record: `docs/session-logs/2026-07-02-databricks-first-pivot.md`; reconstructed skeletons: `starter/databricks/`. |

## What we explicitly do NOT claim
Full autonomy; eBPF-on-Windows; exact per-DirectQuery *user* attribution; per-OPDG CU billing; "works first try" (pending pilot). See `PRODUCTIZATION.md`.

## Verification status
- ✅ **Verified locally (real Spark 3.5.x + JDK 17):** schema-adaptive parser (both test tiers); the medallion bronze→silver→gold on synthetic data (`starter/deploy/run_local_smoke.py`); and — new (Tier 2.5) — the notebooks' OWN `build_silver_*`/`build_gold_*` transforms incl. the U15 fan-out closure and triage confidence tiers (`starter/tests/test_medallion_spark.py`).
- ❌ **Unverified (needs tenant pilot):** identity join, Activator rules, EvaluationContext encoding in-the-wild, Workspace Monitoring column names, `DataGateway` cmdlet names, one-click deploy notebook, report bindings.

## Changelog (high level)
- Operational FPM deploy kit (`docs/`, `scripts/`): node deploy + health validate + lint.
- Research pipeline (`research/`): critique → Phase 0 → tools → pain points → dossier → architecture → validation → north-star + frontier deep-dives.
- Build-new starter (`starter/`): collectors, native-Spark medallion, KQL ceiling-breakers, DAX, Activator rules, config.
- Deployable scaffolding (`starter/deploy/`, `starter/report/`): one-click deploy notebook, teardown notebook, PBIP report skeleton, deployment-bundle exporter, verified end-to-end smoke test.
- Hygiene: purged raw AI-session artifacts; CI for both test tiers; this decisions log replaces the transcript.

## The one gate that matters
**Run the identity-join pilot (D6).** Everything downstream is gated on it. See `docs/PILOT-GUIDE-START-HERE.md`.
