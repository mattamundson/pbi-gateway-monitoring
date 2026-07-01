# SIMULATION.md
## [Simulated — not tenant-verified]

This file documents the scope and limitations of the correlated simulator and
Spark validation suite in `starter/tests/`.

---

## What the simulator proves

`simulate_tenant.py` generates a **coherent 3-day dataset** of 200 query events
where every ID lines up across sources:

| Source | File | Key columns |
|--------|------|-------------|
| Gateway QueryStartReport | `QueryStartReport_sim.csv` | RequestId, QueryTrackingId, EvaluationContext |
| Gateway QueryExecutionReport | `QueryExecutionReport_sim.csv` | RequestId, QueryTrackingId, Success, ErrorMessage |
| Workspace Monitoring (mock) | `PowerBIDatasetsWorkspace_sim.csv` | OperationId (== RequestId for ~85%), ExecutingUser, ItemId, ItemName |
| SystemCounter | `SystemCounter_sim.csv` | GatewayObjectId, CounterTimeUTC, SystemCPUPercent |
| Mashup Processes | `MashupProcesses_sim.ndjson` | GatewayObjectId, WorkingSetMB, IsMashupContainer |
| Network Metrics | `NetworkMetrics_sim.csv` | GatewayHostName, LatencyMs_PBIRelay |
| Refresh History | `RefreshHistory_sim.json` | RequestId (correlated with failed gateway queries) |

`validate_pipeline_sim.py` runs **real PySpark 3.5.1** against this data and
asserts the following, **all of which PASS**:

| Pain point | Check | Result |
|------------|-------|--------|
| **Pain #3** — Identity join | `gateway.RequestId == PowerBIDatasetsWorkspace.OperationId` resolves `ExecutingUser` + `ItemName` | **PASS — 87.50% match-rate** |
| **Pain #3b** — EvaluationContext | `add_artifact_identity` populates `artifact_id` from both base64 and direct-JSON encodings | **PASS** |
| **Pain #2** — Triage join | Failed gateway queries join to refresh-history (exact RequestId + +-30s time window); root-cause buckets produced | **PASS** |
| **Pain #5** — Mashup health | `gold_mashup_health` detects injected runaway containers (WorkingSetMB > 6000) | **PASS** |
| **Gold** — Fleet rollup | Per-node load skew + network latency signal (GWHOST3 highest) | **PASS** |

### Measured identity-join match-rate

```
[Simulated] IDENTITY JOIN MATCH-RATE: 0.8750  (87.50%)
```

175 of 200 gateway events matched a `PowerBIDatasetsWorkspace` row.
The 25 unmatched events (~12.5%) simulate non-Fabric workloads (paginated
reports, Dataflow Gen1) that fire gateway queries but emit no Workspace
Monitoring event -- a realistic and expected gap.

---

## What the simulator CANNOT prove
### [Simulated -- not tenant-verified]

The following require a live Fabric tenant and are **NOT** proven by this simulator:

1. **Real EvaluationContext encoding** -- The base64 / direct-JSON patterns are
   simulated. Real Microsoft gateways may use different JSON structures, field
   names, or encoding variants on specific workload types.

2. **True `PowerBIDatasetsWorkspace` column names** -- Column names
   (`OperationId`, `ExecutingUser`, `ItemId`, `ItemName`, `CapacityId`,
   `CpuTimeMs`, `DurationMs`) are desk-verified against
   MS Learn (semantic-model-operations, updated 2026-02-17) but have **not been
   confirmed against a live Eventhouse table**. Schema may drift between tenants,
   preview rollouts, or Fabric updates.

3. **True tenant match-rate** -- The 87.50% rate is a simulated value. Real
   match-rate depends on: proportion of non-Fabric workloads, whether Workspace
   Monitoring is enabled on all relevant workspaces, whether Log Analytics is
   co-enabled (mutually exclusive per workspace), and gateway routing rules.

4. **Activator alert triggers** -- No Activator rules were validated. KQL-based
   alert conditions in `starter/kql/` cannot be tested without a live Eventhouse.

5. **KQL `diffpatterns` and `autocluster`** -- These are Fabric Eventhouse native
   plugins and cannot run in PySpark. The deterministic root-cause bucketing in
   the validation script is a structural analogue only.

6. **DirectQuery-through-gateway attribution** -- The open question in
   `01_identity_join.kql` (whether `XmlaRequestId` covers gateway-routed
   DirectQuery) is unresolved. Phase 5 tenant testing is required.

7. **Dataflow Gen1 and Paginated Reports** -- These workload types are
   intentionally excluded by design; the 12.5% gap models them.

---

## Why a real-tenant pilot is still the final gate

The simulator confirms **structural correctness** -- the join keys are wired up,
the data flows, and the logic produces the right shape of output. It does NOT
confirm:

- That Microsoft's production schema matches what is simulated here.
- That the join key (`RequestId` == `OperationId`) is populated identically for
  all workload types in a real tenant.
- That real EvaluationContext data is parseable by `add_artifact_identity` as
  implemented.

**Phase 5 validation** (`research/phase5_validation.md`, item U11) remains
required before this kit can be labeled `[Verified]`.

---

## Running the simulator locally

```bash
# Prerequisites: Python 3.x, no Spark needed for simulation
cd starter/tests
python simulate_tenant.py sim_out/ --n-events 200 --seed 42
```

## Running the validation

```bash
# Prerequisites: JAVA_HOME=JDK 17 (NOT 21+), pyspark==3.5.1
export JAVA_HOME=/usr/lib/jvm/java-17-openjdk-amd64
export TMPDIR=/dev/shm/spark_tmp   # or any writable tmpdir
mkdir -p $TMPDIR
cd starter/tests
python validate_pipeline_sim.py
# Exit 0 = all PASS
```

### Python 3.14 compatibility note

PySpark 3.5.1 with Python 3.14 has a known cloudpickle stack overflow when
serializing lambdas (8 MB C-stack limit). `validate_pipeline_sim.py` works
around this by:

1. Reading all test data from CSV/NDJSON files (JVM-only, no Python
   serialization).
2. Replacing `F.filter(arr, lambda x: x.isNotNull())` in the local copy of
   `cast_query_execution` with `F.array_compact(arr)` (equivalent semantics,
   no lambda). The original `gateway_bronze_lib.py` is **not modified**.

This does not affect the join logic being tested.

---

*[Simulated -- not tenant-verified]  Generated 2026-07-01.*
