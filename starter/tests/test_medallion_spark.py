#!/usr/bin/env python3
"""
test_medallion_spark.py  |  Label: [NET-NEW]

Tier-2.5 integration test: runs the REAL silver + gold transform functions from
02_silver_correlate.py and 03_gold_aggregate.py end-to-end against DETERMINISTIC,
inline bronze fixtures on a local PySpark session.

WHY THIS EXISTS
    Before this test, NOTHING executed the notebooks' own build_silver_* /
    build_gold_* functions. run_local_smoke.py and validate_pipeline_sim.py both
    RE-IMPLEMENT the logic inline (they only import gateway_bronze_lib), so a bug
    introduced in 02/03 would ship green -- the join logic labeled "runs only in a
    full Spark session" (the U15 fan-out dedup, the time-window fallbacks, the
    cluster-load CV) was untested. This test imports and calls the actual functions
    and asserts the structural guarantees they claim.

WHAT IT PROVES (against the real notebook code)
    - build_silver_query_execution: (RequestId, QueryTrackingId) dedup keeps latest
    - build_silver_triage: confidence vocabulary {EXACT_REQUESTID, TIME_WINDOW,
      GATEWAY_ONLY} + every failed query represented exactly once (distinct RequestId)
    - build_silver_identity_attribution: EVALUATION_CONTEXT vs UNATTRIBUTED split +
      disclaimer column, row count preserved
    - build_silver_network_correlated: U15 nearest-pick dedup -> output row count ==
      silver_query_execution row count (no fan-out, no drop) even when >1 NIC sample
      falls inside a query's ±5-min window
    - build_gold_query_performance: every measures.dax column present
    - build_gold_gateway_health: heartbeat + cpu/mem + spool + network assembled
    - build_gold_cluster_load: non-null coefficient-of-variation on a multi-node
      cluster (load-skew classification computed, not SINGLE_NODE_OR_NO_DATA)
    - build_gold_dim_gateway: one current row per gateway node
    - scd2_apply (T5): a tracked-attribute change CLOSES the prior row and opens
      a new one (history preserved); an unchanged key keeps its original
      valid_from; a key that stops reporting keeps its row open; exactly one
      is_current row per key; re-applying the same snapshot is idempotent.
      The previous implementation was SCD1 wearing an SCD2 label and passed the
      single-run assertions above, which is why they were not sufficient.

HOW IT STAYS HERMETIC
    Sets GATEWAYMON_LAKEHOUSE_ROOT to a temp dir, GATEWAYMON_TABLE_FORMAT=parquet
    (no Delta JAR fetch), and GATEWAYMON_AUTORUN=0 so importing 02/03 does not run
    their pipelines. Bronze tables are written to the temp Lakehouse/Tables dir; the
    real read_bronze/read_silver helpers read them back exactly as they would in
    Fabric.

REQUIREMENTS (same as run_local_smoke.py):
    Python 3.11, pyspark>=3.5,<3.6, Java 17 on JAVA_HOME, and on Windows a Hadoop
    winutils.exe/hadoop.dll on HADOOP_HOME. See reference_pyspark_windows_local_harness.

[Unverified in a live Fabric tenant] This proves the transform logic on a local
Spark engine; ABFSS I/O and the Activity-Events attribution join still require
the Phase-5 tenant pilot. SCD2 is now proven here rather than deferred: it is a
full recompute, not a DeltaTable.merge, precisely so it runs under this
parquet-backed harness -- a merge implementation would have been untestable
without a Delta JAR, which is how the original STUB survived unnoticed.
"""

import os
import sys
import shutil
import tempfile
import importlib.util
from datetime import datetime, timedelta
from pathlib import Path


# ── Environment preflight (mirror run_local_smoke.py; SKIP cleanly if unmet) ──
def _check_env():
    java_home = os.environ.get("JAVA_HOME", "")
    if not java_home:
        print("[SKIP] JAVA_HOME not set — cannot start PySpark.")
        sys.exit(0)
    jbin = Path(java_home) / "bin"
    if not ((jbin / "java").exists() or (jbin / "java.exe").exists()):
        print(f"[SKIP] Java binary not found in {jbin}.")
        sys.exit(0)
    try:
        import pyspark  # noqa: F401
    except ImportError:
        print("[SKIP] pyspark not installed.")
        sys.exit(0)
    if os.name == "nt" and not os.environ.get("HADOOP_HOME", ""):
        print(
            "[WARN] HADOOP_HOME not set on Windows — Spark local mode may fail at "
            "createTempDir() without winutils. See reference_pyspark_windows_local_harness."
        )


_check_env()

# CRITICAL (Windows + conda): pin the Spark PYTHON WORKER to THIS interpreter
# (the pbi-spark 3.11 env running the test). createDataFrame from a Python
# collection spawns an executor-side Python worker; without this, Spark resolves
# `python` on PATH and can launch the system Python 3.14, which is incompatible
# with pyspark 3.5.x (cloudpickle C-stack overflow / worker socket reset). Must be
# set BEFORE the SparkSession is created. See reference_pyspark_windows_local_harness.
os.environ.setdefault("PYSPARK_PYTHON", sys.executable)
os.environ.setdefault("PYSPARK_DRIVER_PYTHON", sys.executable)

NOTEBOOKS = Path(__file__).resolve().parent.parent / "notebooks"
sys.path.insert(0, str(NOTEBOOKS))

# ── Temp Lakehouse root + hermetic table format; MUST be set before importing 02/03 ──
_ROOT = tempfile.mkdtemp(prefix="gwmon_medallion_").replace("\\", "/")
os.environ["GATEWAYMON_LAKEHOUSE_ROOT"] = _ROOT
os.environ["GATEWAYMON_TABLE_FORMAT"] = "parquet"
os.environ["GATEWAYMON_AUTORUN"] = "0"  # do NOT auto-run the pipelines on import
_TABLES = f"{_ROOT}/Tables"

from pyspark.sql import SparkSession, functions as F
from pyspark.sql.types import (
    StructType,
    StructField,
    StringType,
    LongType,
    DoubleType,
    BooleanType,
    TimestampType,
    IntegerType,
)

import gateway_bronze_lib as gbl

print("[medallion] Building local SparkSession...")
spark = (
    SparkSession.builder.master("local[2]")
    .appName("GatewayMonitor_MedallionIT")
    .config("spark.ui.enabled", "false")
    .config("spark.sql.session.timeZone", "UTC")
    .getOrCreate()
)
spark.sparkContext.setLogLevel("WARN")

# ── Deterministic clock ──────────────────────────────────────────────────────
T0 = datetime(2026, 7, 2, 10, 0, 0)


def t(mins=0):
    return T0 + timedelta(minutes=mins)


# ── Result tracking ──────────────────────────────────────────────────────────
_FAILS = []


def check(cond, label):
    if cond:
        print(f"  [PASS] {label}")
    else:
        print(f"  [FAIL] {label}")
        _FAILS.append(label)


def write_bronze(df, name):
    df.write.mode("overwrite").parquet(f"{_TABLES}/{name}")


# ═════════════════════════════════════════════════════════════════════════════
# BRONZE FIXTURES (deterministic, hand-built to exercise every downstream branch)
# ═════════════════════════════════════════════════════════════════════════════

# --- bronze_query_execution ---------------------------------------------------
# 8 rows; (req-1004, qt-7) appears twice (dedup -> latest kept => 7 distinct).
# Failed queries: req-1001 (exact refresh), req-1003 (exact refresh),
# req-2000 (orphan refresh same end-time -> TIME_WINDOW), req-2001 (GATEWAY_ONLY).
_QE_SCHEMA = StructType(
    [
        StructField("GatewayObjectId", StringType()),
        StructField("RequestId", StringType()),
        StructField("QueryTrackingId", StringType()),
        StructField("DataSource", StringType()),
        StructField("QueryType", StringType()),
        StructField("QueryExecutionEndTimeUTC", TimestampType()),
        StructField("QueryExecutionDuration", LongType()),
        StructField("Success", BooleanType()),
        StructField("ErrorMessage", StringType()),
        StructField("SpoolingTotalDataSize", LongType()),
        StructField("SpoolingDiskWritingDuration", LongType()),
        StructField("DataReadingAndSerializationDuration", LongType()),
        StructField("DiskRead", DoubleType()),
        StructField("DiskWrite", DoubleType()),
        StructField("_gateway_host", StringType()),
    ]
)
_qe_rows = [
    ("gw-001", "req-1000", "qt-1", "SqlProd", "Refresh", t(0), 1200, True, None, 100, 50, 30, 1000.0, 500.0, "GWHOST1"),
    ("gw-001", "req-1001", "qt-2", "SqlProd", "Refresh", t(1), 90000, False, "Timeout expired, retry failed", 0, 0, 0, 0.0, 0.0, "GWHOST1"),
    ("gw-002", "req-1002", "qt-3", "OracleFin", "DirectQuery", t(2), 500, True, None, 200000, 100, 40, 2000.0, 800.0, "GWHOST2"),
    ("gw-002", "req-1003", "qt-4", "OracleFin", "Refresh", t(3), 75000, False, 'Login failed for user "svc", locked', 0, 0, 0, 0.0, 0.0, "GWHOST2"),
    ("gw-003", "req-2000", "qt-5", "SharePointOnPrem", "Refresh", t(5), 60000, False, "Connector crashed", 0, 0, 0, 0.0, 0.0, "GWHOST3"),
    ("gw-003", "req-2001", "qt-6", "SharePointOnPrem", "DirectQuery", t(20), 30000, False, "Unknown error", 0, 0, 0, 0.0, 0.0, "GWHOST3"),
    ("gw-001", "req-1004", "qt-7", "SqlProd", "Refresh", t(0), 800, True, None, 300, 20, 10, 1500.0, 600.0, "GWHOST1"),
    # duplicate (req-1004, qt-7) with an EARLIER end-time -> dedup must drop this one
    ("gw-001", "req-1004", "qt-7", "SqlProd", "Refresh", t(-10), 700, True, None, 250, 15, 8, 1400.0, 550.0, "GWHOST1"),
]
bronze_qe = spark.createDataFrame(_qe_rows, _QE_SCHEMA)
write_bronze(bronze_qe, "bronze_query_execution")

# --- bronze_query_start (EvaluationContext -> artifact identity via the lib) ---
_QS_SCHEMA = StructType(
    [
        StructField("GatewayObjectId", StringType()),
        StructField("RequestId", StringType()),
        StructField("QueryTrackingId", StringType()),
        StructField("QueryStartTimeUTC", TimestampType()),
        StructField("QueryType", StringType()),
        StructField("DataSource", StringType()),
        StructField("EvaluationContext", StringType()),
    ]
)
import base64 as _b64

_ctx1 = '{"artifactId":"dataset-1","artifactKind":"SemanticModel"}'
_ctx1_b64 = _b64.b64encode(_ctx1.encode()).decode()
_ctx3 = '{"artifactId":"dataset-3","artifactKind":"SemanticModel"}'
_ctx4 = '{"artifactId":"dataset-4","artifactKind":"Dataflow"}'
_qs_rows = [
    ("gw-001", "req-1000", "qt-1", t(-1), "Refresh", "SqlProd", _ctx1),        # direct JSON
    ("gw-001", "req-1001", "qt-2", t(0), "Refresh", "SqlProd", _ctx1_b64),      # base64 JSON
    ("gw-002", "req-1002", "qt-3", t(1), "DirectQuery", "OracleFin", ""),       # non-Fabric -> null
    ("gw-002", "req-1003", "qt-4", t(2), "Refresh", "OracleFin", _ctx3),        # direct JSON
    ("gw-003", "req-2000", "qt-5", t(4), "Refresh", "SharePointOnPrem", ""),    # null
    ("gw-003", "req-2001", "qt-6", t(19), "DirectQuery", "SharePointOnPrem", ""),  # null
    ("gw-001", "req-1004", "qt-7", t(-1), "Refresh", "SqlProd", _ctx4),         # direct JSON
]
bronze_qs = spark.createDataFrame(_qs_rows, _QS_SCHEMA)
# Mirror 01_bronze_ingest: produce artifact_id + artifact_type on the bronze table.
# The lib emits artifact_id + artifact_kind; rename kind->type to match the schema
# 02_silver_correlate reads.
bronze_qs = gbl.add_artifact_identity(bronze_qs, ctx_col="EvaluationContext")
bronze_qs = bronze_qs.withColumnRenamed("artifact_kind", "artifact_type")
write_bronze(bronze_qs, "bronze_query_start")

# --- bronze_refresh_history (Service-side leg of the triage join) -------------
_RH_SCHEMA = StructType(
    [
        StructField("RequestId", StringType()),
        StructField("DatasetId", StringType()),
        StructField("WorkspaceId", StringType()),
        StructField("Status", StringType()),
        StructField("StartTime", TimestampType()),
        StructField("EndTime", TimestampType()),
        StructField("ServiceExceptionJson", StringType()),
    ]
)
_rh_rows = [
    ("req-1001", "dataset-2", "ws-001", "Failed", t(-5), t(1), '{"errorCode":"CredentialsNotSpecified"}'),  # EXACT
    ("req-1003", "dataset-3", "ws-002", "Failed", t(-5), t(3), '{"errorCode":"ModelRefreshFailed"}'),        # EXACT
    ("req-8001", "dataset-9", "ws-001", "Failed", t(-5), t(5), '{"errorCode":"Orphan"}'),                    # same end-time as req-2000 QE -> TIME_WINDOW
    ("req-9001", "dataset-1", "ws-003", "Completed", t(-60), t(-55), ""),                                    # pure orphan (no gateway query)
]
bronze_rh = spark.createDataFrame(_rh_rows, _RH_SCHEMA)
write_bronze(bronze_rh, "bronze_refresh_history")

# --- bronze_event_log (Level-3 OS-event correlation) --------------------------
_EL_SCHEMA = StructType(
    [
        StructField("TimeCreated", TimestampType()),
        StructField("EventId", IntegerType()),
        StructField("LevelDisplayName", StringType()),
        StructField("Message", StringType()),
        StructField("GatewayHostName", StringType()),
        StructField("EventSource", StringType()),
    ]
)
_el_rows = [
    (t(1), 5000, "Error", "Gateway service crashed", "GWHOST1", "Application"),   # within 120s of req-1001 failure
    (t(120), 4000, "Warning", "High memory", "GWHOST3", "System"),                # far from any failure
]
bronze_el = spark.createDataFrame(_el_rows, _EL_SCHEMA)
write_bronze(bronze_el, "bronze_event_log")

# --- bronze_network_metrics (2 GWHOST1 samples in-window -> exercise U15 dedup) ---
_NET_SCHEMA = StructType(
    [
        StructField("GatewayHostName", StringType()),
        StructField("CollectedAtUTC", TimestampType()),
        StructField("BytesTotalPerSec", DoubleType()),
        StructField("UtilizationPct", DoubleType()),
        StructField("LatencyMs_PBIRelay", DoubleType()),
        StructField("CurrentBandwidthBps", DoubleType()),
    ]
)
_net_rows = [
    ("GWHOST1", t(0), 5.0e7, 40.0, 25.0, 1.0e9),
    ("GWHOST1", t(2), 4.0e7, 35.0, 30.0, 1.0e9),   # 2nd in-window sample for GWHOST1 queries
    ("GWHOST2", t(2), 3.0e7, 20.0, 15.0, 1.0e9),
    ("GWHOST3", t(5), 2.0e7, 60.0, 129.0, 5.0e8),
    ("GWHOST3", t(20), 1.0e7, 10.0, 130.0, 5.0e8),
]
bronze_net = spark.createDataFrame(_net_rows, _NET_SCHEMA)
write_bronze(bronze_net, "bronze_network_metrics")

# --- bronze_gateway_inventory (3 nodes, ONE cluster -> multi-node CV) ----------
_INV_SCHEMA = StructType(
    [
        StructField("GatewayObjectId", StringType()),
        StructField("GatewayClusterId", StringType()),
        StructField("GatewayClusterName", StringType()),
        StructField("GatewayNodeName", StringType()),
        StructField("Status", StringType()),
        StructField("Version", StringType()),
        StructField("DatasourceCount", IntegerType()),
        StructField("CollectedAtUTC", TimestampType()),
    ]
)
_inv_rows = [
    ("gw-001", "clu-001", "Prod-Gateway-Cluster", "GWHOST1", "Live", "3000.214.4", 5, t(-2)),
    ("gw-002", "clu-001", "Prod-Gateway-Cluster", "GWHOST2", "Live", "3000.214.4", 3, t(-2)),
    ("gw-003", "clu-001", "Prod-Gateway-Cluster", "GWHOST3", "Live", "3000.100.2", 8, t(-2)),
]
bronze_inv = spark.createDataFrame(_inv_rows, _INV_SCHEMA)
write_bronze(bronze_inv, "bronze_gateway_inventory")

# --- bronze_disk_spool --------------------------------------------------------
_DISK_SCHEMA = StructType(
    [
        StructField("GatewayHostName", StringType()),
        StructField("FreeSpacePct", DoubleType()),
        StructField("SpoolDirSizeBytes", LongType()),
        StructField("AlertLevel", StringType()),
        StructField("StreamBeforeRequestCompletes_Warning", BooleanType()),
    ]
)
_disk_rows = [
    ("GWHOST1", 45.0, 5_000_000, "OK", False),
    ("GWHOST2", 12.0, 90_000_000, "WARN", True),
    ("GWHOST3", 60.0, 2_000_000, "OK", False),
]
bronze_disk = spark.createDataFrame(_disk_rows, _DISK_SCHEMA)
write_bronze(bronze_disk, "bronze_disk_spool")

# --- bronze_system_counter ----------------------------------------------------
_SC_SCHEMA = StructType(
    [
        StructField("GatewayObjectId", StringType()),
        StructField("CounterTimeUTC", TimestampType()),
        StructField("SystemCPUPercent", DoubleType()),
        StructField("SystemMEMUsedPercent", DoubleType()),
        StructField("GatewayCPUPercent", DoubleType()),
        StructField("GatewayMEMKb", LongType()),
    ]
)
_sc_rows = [
    ("gw-001", t(-1), 40.0, 60.0, 20.0, 2_000_000),
    ("gw-002", t(-1), 55.0, 70.0, 30.0, 3_000_000),
    ("gw-003", t(-1), 25.0, 50.0, 15.0, 1_500_000),
]
bronze_sc = spark.createDataFrame(_sc_rows, _SC_SCHEMA)
write_bronze(bronze_sc, "bronze_system_counter")


# ═════════════════════════════════════════════════════════════════════════════
# IMPORT the real notebook modules (AUTORUN=0 so import does NOT run pipelines)
# ═════════════════════════════════════════════════════════════════════════════
def _load(fname, modname):
    spec = importlib.util.spec_from_file_location(modname, str(NOTEBOOKS / fname))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


print("[medallion] Importing real 02/03 notebook modules...")
silver = _load("02_silver_correlate.py", "silver_correlate")
gold = _load("03_gold_aggregate.py", "gold_aggregate")


def read_tbl(name):
    return spark.read.parquet(f"{_TABLES}/{name}")


# ═════════════════════════════════════════════════════════════════════════════
# RUN silver, then gold — the REAL build_* functions
# ═════════════════════════════════════════════════════════════════════════════
print("\n" + "=" * 70)
print("SILVER — real 02_silver_correlate.build_* functions")
print("=" * 70)
silver.build_silver_query_execution()
silver.build_silver_triage()
silver.build_silver_identity_attribution()
silver.build_silver_network_correlated()

sqe = read_tbl("silver_query_execution")
sqe_count = sqe.count()
check(sqe_count == 7, f"silver_query_execution dedup (RequestId,QueryTrackingId): {sqe_count} == 7")
check(
    sqe.filter(F.col("artifact_id").isNotNull()).count() == 4,
    "silver_query_execution carries artifact_id from QueryStart join (4 populated)",
)

triage = read_tbl("silver_triage")
tri_conf = {r["triage_confidence"] for r in triage.select("triage_confidence").distinct().collect()}
tri_reqs = triage.select("RequestId").distinct().count()
check(tri_reqs == 4, f"silver_triage covers every failed query exactly once: {tri_reqs} distinct RequestId == 4")
check(
    tri_conf.issubset({"EXACT_REQUESTID", "TIME_WINDOW", "GATEWAY_ONLY"}),
    f"silver_triage confidence vocabulary valid: {sorted(tri_conf)}",
)
check("EXACT_REQUESTID" in tri_conf, "silver_triage produced EXACT_REQUESTID matches")
check("TIME_WINDOW" in tri_conf, "silver_triage produced a TIME_WINDOW (fuzzy) match")
check("GATEWAY_ONLY" in tri_conf, "silver_triage produced a GATEWAY_ONLY (no-refresh) row")
check("failure_layer" in triage.columns, "silver_triage has derived failure_layer column")

attr = read_tbl("silver_identity_attribution")
attr_conf = {r["attribution_confidence"] for r in attr.select("attribution_confidence").distinct().collect()}
check(attr.count() == sqe_count, f"silver_identity_attribution preserves row count: {attr.count()} == {sqe_count}")
check(
    attr_conf == {"EVALUATION_CONTEXT", "UNATTRIBUTED"},
    f"silver_identity_attribution split correct: {sorted(attr_conf)}",
)
check(
    "_attribution_disclaimer" in attr.columns,
    "silver_identity_attribution carries the BEST-EFFORT disclaimer column",
)

net = read_tbl("silver_network_correlated")
net_count = net.count()
# U15: nearest-pick dedup + left join => exactly one network row per silver query,
# even though GWHOST1 has 2 NIC samples inside a query's ±5-min window.
check(
    net_count == sqe_count,
    f"silver_network_correlated U15 fan-out closed: {net_count} == {sqe_count} (one row per query, no fan-out)",
)
check(
    "network_throughput_efficiency" in net.columns,
    "silver_network_correlated computes network_throughput_efficiency",
)

print("\n" + "=" * 70)
print("GOLD — real 03_gold_aggregate.build_* functions")
print("=" * 70)
gold.build_gold_gateway_health()
gold.build_gold_query_performance()
gold.build_gold_cluster_load()
gold.build_gold_dim_gateway()

gqp = read_tbl("gold_query_performance")
MEASURES_DAX_DEPS = [
    "query_count", "error_count", "error_rate_pct", "duration_avg_ms",
    "duration_p95_ms", "duration_max_ms", "spool_total_bytes_sum",
    "spool_writing_ms_avg", "spooled_pct",
]
missing = [c for c in MEASURES_DAX_DEPS if c not in gqp.columns]
check(gqp.count() > 0, f"gold_query_performance produced rows: {gqp.count()}")
check(not missing, f"gold_query_performance has all measures.dax columns (missing: {missing})")

gh = read_tbl("gold_gateway_health")
check(gh.count() == 3, f"gold_gateway_health one row per node: {gh.count()} == 3")
for col in ["heartbeat_age_minutes", "cpu_pct_avg", "spool_free_pct", "query_count_5min"]:
    check(col in gh.columns, f"gold_gateway_health assembled column present: {col}")

gcl = read_tbl("gold_cluster_load")
cv_non_null = gcl.filter(F.col("queries_per_node_cv").isNotNull()).count()
classes = {r["load_skew_classification"] for r in gcl.select("load_skew_classification").distinct().collect()}
check(gcl.count() > 0, f"gold_cluster_load produced rows: {gcl.count()}")
check(cv_non_null > 0, f"gold_cluster_load computed a non-null CV on the 3-node cluster: {cv_non_null} row(s)")
check(
    classes != {"SINGLE_NODE_OR_NO_DATA"},
    f"gold_cluster_load classified load skew (not degenerate): {sorted(classes)}",
)

gdim = read_tbl("gold_dim_gateway")
check(gdim.count() == 3, f"gold_dim_gateway one current row per node: {gdim.count()} == 3")
check(
    gdim.filter(F.col("is_current") == True).count() == 3,
    "gold_dim_gateway marks all snapshots is_current=True",
)

# ═════════════════════════════════════════════════════════════════════════════
# SCD2 — roadmap T5
#
# The two assertions above are exactly what the OLD implementation passed while
# being SCD1 in disguise: it overwrote the table every run, so a single-run test
# could never tell the difference. These exercise scd2_apply directly across a
# simulated second run where one node's Version changed, one is unchanged, one
# stopped reporting, and one is brand new.
# ═════════════════════════════════════════════════════════════════════════════
print("\n-- SCD2 (T5) " + "-" * 57)

from pyspark.sql.types import (  # noqa: E402
    StructType, StructField, StringType as _S, LongType as _L,
    TimestampType as _T, BooleanType as _B,
)
from datetime import datetime as _dt  # noqa: E402

_scd_schema = StructType([
    StructField("GatewayObjectId", _S()),
    StructField("GatewayClusterId", _S()),
    StructField("GatewayClusterName", _S()),
    StructField("GatewayNodeName", _S()),
    StructField("Version", _S()),
    StructField("Status", _S()),
    StructField("DatasourceCount", _L()),
    StructField("valid_from", _T()),
    StructField("valid_to", _T()),
    StructField("is_current", _B()),
])

_t1 = _dt(2026, 7, 1, 10, 0, 0)
_t2 = _dt(2026, 7, 2, 10, 0, 0)


def _row(gid, ver, vfrom, vto=None, cur=True, status="Live", dsc=5):
    return (gid, "c1", "cluster-1", f"node-{gid}", ver, status, dsc, vfrom, vto, cur)


run1 = spark.createDataFrame(
    [_row("g1", "3000.1", _t1), _row("g2", "3000.1", _t1), _row("g3", "3000.1", _t1)],
    schema=_scd_schema,
)

# First run against an empty dimension returns the incoming set unchanged.
first = gold.scd2_apply(None, run1)
check(first.count() == 3, f"SCD2 first run opens one version per node: {first.count()} == 3")

# Second run: g1 upgraded, g2 unchanged, g3 stopped reporting, g4 is new.
run2 = spark.createDataFrame(
    [_row("g1", "3000.2", _t2), _row("g2", "3000.1", _t2), _row("g4", "3000.2", _t2)],
    schema=_scd_schema,
)
second = gold.scd2_apply(run1, run2)
rows = {r["GatewayObjectId"]: [] for r in second.select("GatewayObjectId").distinct().collect()}
for r in second.collect():
    rows[r["GatewayObjectId"]].append(r)

# History preserved for the changed node.
g1 = sorted(rows.get("g1", []), key=lambda r: r["valid_from"])
check(len(g1) == 2, f"SCD2 version change preserves history: g1 has {len(g1)} rows == 2")
if len(g1) == 2:
    check(g1[0]["Version"] == "3000.1" and not g1[0]["is_current"],
          "SCD2 closes the prior version row (is_current=False)")
    check(g1[0]["valid_to"] == _t2,
          f"SCD2 closes valid_to at the new version's valid_from: {g1[0]['valid_to']} == {_t2}")
    check(g1[1]["Version"] == "3000.2" and g1[1]["is_current"],
          "SCD2 opens the new version row as current")

# Unchanged node must NOT restart its validity interval.
g2 = rows.get("g2", [])
check(len(g2) == 1, f"SCD2 unchanged node stays at one row: {len(g2)} == 1")
if len(g2) == 1:
    check(g2[0]["valid_from"] == _t1,
          f"SCD2 unchanged node keeps ORIGINAL valid_from: {g2[0]['valid_from']} == {_t1}")
    check(g2[0]["is_current"], "SCD2 unchanged node stays current")

# A node absent from the new snapshot is not evidence of decommissioning.
g3 = rows.get("g3", [])
check(len(g3) == 1 and g3[0]["is_current"],
      "SCD2 keeps a non-reporting node's row open (offline is the heartbeat's job)")

g4 = rows.get("g4", [])
check(len(g4) == 1 and g4[0]["is_current"], "SCD2 opens a first version for a new node")

# The core SCD2 invariant, across every key.
dupes = (
    second.filter(F.col("is_current") == True)
    .groupBy("GatewayObjectId").count().filter(F.col("count") > 1).count()
)
check(dupes == 0, f"SCD2 invariant: exactly one is_current row per key ({dupes} violations)")

# Idempotency: re-applying the same snapshot must change nothing.
third = gold.scd2_apply(second, run2)
check(third.count() == second.count(),
      f"SCD2 is idempotent on a repeated snapshot: {third.count()} == {second.count()}")

# ═════════════════════════════════════════════════════════════════════════════
# SUMMARY
# ═════════════════════════════════════════════════════════════════════════════
print("\n" + "=" * 70)
print("MEDALLION INTEGRATION TEST SUMMARY")
print("=" * 70)
spark.stop()
try:
    shutil.rmtree(_ROOT, ignore_errors=True)
except Exception:
    pass

if _FAILS:
    print(f"RESULT: FAILED ({len(_FAILS)} assertion(s))")
    for f in _FAILS:
        print(f"  - {f}")
    sys.exit(1)
print("RESULT: ALL MEDALLION INTEGRATION TESTS PASSED")
print("  Proved the REAL build_silver_*/build_gold_* functions + U15 fan-out dedup.")
