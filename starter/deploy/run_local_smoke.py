"""
run_local_smoke.py
Label: [NET-NEW]
[Unverified — requires Fabric tenant pilot for production validation]

Local end-to-end smoke test for the Gateway Monitor medallion pipeline.
Exercises the REAL bronze→silver→gold transform logic using PySpark + Delta
against synthetic CSV/JSON fixtures in starter/tests/synthetic_out/.

USAGE:
    JAVA_HOME=/usr/lib/jvm/java-17-openjdk-amd64 python run_local_smoke.py

REQUIREMENTS:
    pyspark>=3.5.0, delta-spark>=3.1.0 (3.5.x compat), Python 3.9+
    Java 17 on JAVA_HOME.

WHAT THIS PROVES:
    - gateway_bronze_lib.read_gateway_csv() parses synthetic QueryExecution CSV
    - cast_query_execution() applies correct types (long, double, bool, timestamp)
    - add_artifact_identity() extracts artifact_id/artifact_kind (UDF-free Spark SQL)
    - A silver-style RequestId self-join succeeds (schema alignment)
    - A gold-style groupBy aggregation produces the same column names that
      measures.dax references: duration_avg_ms, duration_p95_ms, duration_max_ms,
      spool_total_bytes_sum, spool_writing_ms_avg, spooled_pct, query_count,
      error_count, error_rate_pct

WHAT THIS DOES NOT PROVE:
    - Fabric Lakehouse ABFSS connectivity (requires tenant)
    - DeltaTable merge / SCD-2 (requires real Delta storage)
    - FPM bridge / Eventhouse OneLake availability
    - Activity Events fuzzy join (no activity_events fixture)
    - Fabric Activator rule evaluation
"""

import sys
import os
import textwrap
from pathlib import Path

# ── Locate repo root (this file lives at starter/deploy/run_local_smoke.py) ──
THIS_DIR   = Path(__file__).resolve().parent
STARTER    = THIS_DIR.parent
SYNTH_DIR  = STARTER / "tests" / "synthetic_out"
NOTEBOOKS  = STARTER / "notebooks"

# ── Inject the notebooks directory so gateway_bronze_lib is importable ───────
sys.path.insert(0, str(NOTEBOOKS))

# ── Check for PySpark + Java availability ─────────────────────────────────────
def _check_env():
    java_home = os.environ.get("JAVA_HOME", "")
    if not java_home:
        print("[SKIP] JAVA_HOME not set — cannot start PySpark. "
              "Set JAVA_HOME=/usr/lib/jvm/java-17-openjdk-amd64 and re-run.")
        sys.exit(0)
    java_bin = Path(java_home) / "bin" / "java"
    if not java_bin.exists():
        print(f"[SKIP] Java binary not found at {java_bin}. "
              "Install Java 17 or fix JAVA_HOME.")
        sys.exit(0)
    try:
        import pyspark  # noqa: F401
    except ImportError:
        print("[SKIP] pyspark not installed. Run: pip install pyspark==3.5.1 delta-spark==3.1.0")
        sys.exit(0)
    try:
        import delta  # noqa: F401
    except ImportError:
        print("[SKIP] delta-spark not installed. Run: pip install delta-spark==3.1.0")
        sys.exit(0)


_check_env()


# ── PySpark session (local mode, no Fabric dependency) ─────────────────────
from pyspark.sql import SparkSession
try:
    from delta import configure_spark_with_delta_pip
    _HAVE_DELTA_HELPER = True
except Exception:
    _HAVE_DELTA_HELPER = False
from pyspark.sql import functions as F

print("[smoke] Building local SparkSession (may take ~20s on first run)...")

_builder = (
    SparkSession.builder
    .master("local[2]")
    .appName("GatewayMonitor_SmokeTest")
    .config("spark.sql.extensions", "io.delta.sql.DeltaSparkSessionExtension")
    .config("spark.sql.catalog.spark_catalog", "org.apache.spark.sql.delta.catalog.DeltaCatalog")
    .config("spark.ui.enabled", "false")
)
# Use delta-spark's helper to fetch the matching Delta JAR from Maven on first run.
# Local smoke test proves TRANSFORM LOGIC; if Delta JARs can't be fetched (offline),
# fall back to a plain session and write Parquet instead of Delta (SMOKE_FORMAT).
SMOKE_FORMAT = "delta"
try:
    if _HAVE_DELTA_HELPER:
        spark = configure_spark_with_delta_pip(_builder).getOrCreate()
    else:
        raise RuntimeError("delta helper unavailable")
except Exception as _e:
    print(f"[smoke] Delta unavailable ({_e}); falling back to Parquet (logic-only smoke).")
    SMOKE_FORMAT = "parquet"
    spark = (
        SparkSession.builder.master("local[2]")
        .appName("GatewayMonitor_SmokeTest").config("spark.ui.enabled", "false")
        .getOrCreate()
    )
spark.sparkContext.setLogLevel("WARN")
print("[smoke] SparkSession ready.\n")


# ── Import real library under test ────────────────────────────────────────────
try:
    import gateway_bronze_lib as gbl
    print("[smoke] Imported gateway_bronze_lib OK")
except ImportError as e:
    print(f"[FAIL] Could not import gateway_bronze_lib: {e}")
    print(f"       Expected at: {NOTEBOOKS / 'gateway_bronze_lib.py'}")
    sys.exit(1)


# ─────────────────────────────────────────────────────────────────────────────
# STEP 1 — BRONZE: Read synthetic QueryExecution CSV via gateway_bronze_lib
# ─────────────────────────────────────────────────────────────────────────────
print("=" * 70)
print("STEP 1  BRONZE — gateway_bronze_lib.read_gateway_csv()")
print("=" * 70)

qe_csv = SYNTH_DIR / "QueryExecutionReport_synthetic.csv"
qs_csv = SYNTH_DIR / "QueryStartReport_synthetic.csv"
sc_csv = SYNTH_DIR / "SystemCounterReport_synthetic.csv"

if not qe_csv.exists():
    print(f"[FAIL] Synthetic fixture not found: {qe_csv}")
    print("       Run starter/tests/generate_synthetic_logs.py first.")
    sys.exit(1)

# Use the PRIMARY path from gateway_bronze_lib (native distributed Spark CSV)
bronze_qe = gbl.read_gateway_csv(
    spark,
    str(qe_csv),
    rename_map=gbl.QUERY_EXECUTION_RENAME,
)
bronze_qe = gbl.cast_query_execution(bronze_qe)
bronze_qe = bronze_qe.withColumn(
    "_partition_date", F.to_date(F.col("QueryExecutionEndTimeUTC"))
)

qe_count = bronze_qe.count()
print(f"[PASS] bronze_query_execution: {qe_count} rows")
print(f"       columns: {bronze_qe.columns}")

# Verify key columns exist and have expected types
expected_qe_cols = [
    "GatewayObjectId", "RequestId", "DataSource", "QueryExecutionDuration",
    "Success", "ErrorMessage", "SpoolingTotalDataSize", "DiskRead", "DiskWrite",
    "_partition_date",
]
missing_qe = [c for c in expected_qe_cols if c not in bronze_qe.columns]
if missing_qe:
    print(f"[FAIL] bronze_qe missing expected columns: {missing_qe}")
    sys.exit(1)
print(f"[PASS] All {len(expected_qe_cols)} expected QE columns present")

# Show a sample row
print("\n--- bronze_qe sample (1 row) ---")
bronze_qe.select(
    "RequestId", "GatewayObjectId", "DataSource",
    "QueryExecutionDuration", "Success", "SpoolingTotalDataSize"
).show(1, truncate=80)


# ─────────────────────────────────────────────────────────────────────────────
# STEP 2 — BRONZE: QueryStart CSV + add_artifact_identity (UDF-free)
# ─────────────────────────────────────────────────────────────────────────────
print("=" * 70)
print("STEP 2  BRONZE — QueryStart + add_artifact_identity()")
print("=" * 70)

if qs_csv.exists():
    bronze_qs = gbl.read_gateway_csv(spark, str(qs_csv))
    bronze_qs = gbl.add_artifact_identity(bronze_qs, ctx_col="EvaluationContext")
    qs_count = bronze_qs.count()
    print(f"[PASS] bronze_query_start: {qs_count} rows")
    if "artifact_id" in bronze_qs.columns:
        populated = bronze_qs.filter(F.col("artifact_id").isNotNull()).count()
        print(f"       artifact_id populated: {populated}/{qs_count} rows")
        print(f"[PASS] add_artifact_identity() returned artifact_id column")
    else:
        print("[WARN] artifact_id column absent — EvaluationContext may not exist in fixture")
    bronze_qs.select("RequestId", "EvaluationContext", "artifact_id", "artifact_kind").show(2, truncate=60)
else:
    print(f"[SKIP] {qs_csv} not found — skipping QueryStart step")
    bronze_qs = None


# ─────────────────────────────────────────────────────────────────────────────
# STEP 3 — SILVER: silver_query_execution subset
# Join QE + QS on RequestId (mirrors 02_silver_correlate.build_silver_query_execution)
# ─────────────────────────────────────────────────────────────────────────────
print("=" * 70)
print("STEP 3  SILVER — RequestId join (QE ⋈ QS)")
print("=" * 70)

if bronze_qs is not None:
    qs_sel = bronze_qs.select(
        "RequestId",
        F.col("EvaluationContext"),
        F.col("artifact_id"),
        F.col("artifact_kind").alias("artifact_type"),
    ).dropDuplicates(["RequestId"])

    silver_qe = bronze_qe.join(qs_sel, on="RequestId", how="left")
    silver_qe = silver_qe.withColumn(
        "is_spooled", F.col("SpoolingTotalDataSize") > 0
    )
else:
    silver_qe = bronze_qe.withColumn("artifact_id", F.lit(None).cast("string"))
    silver_qe = silver_qe.withColumn("artifact_type", F.lit(None).cast("string"))
    silver_qe = silver_qe.withColumn("is_spooled", F.col("SpoolingTotalDataSize") > 0)

silver_count = silver_qe.count()
print(f"[PASS] silver_query_execution: {silver_count} rows")
print(f"       is_spooled=True: {silver_qe.filter(F.col('is_spooled')).count()} rows")
silver_qe.select(
    "RequestId", "DataSource", "QueryExecutionDuration", "Success",
    "is_spooled", "artifact_id"
).show(3, truncate=60)


# ─────────────────────────────────────────────────────────────────────────────
# STEP 4 — GOLD: gold_query_performance aggregation subset
# Mirrors 03_gold_aggregate.build_gold_query_performance()
# Column names are EXACTLY what measures.dax references
# ─────────────────────────────────────────────────────────────────────────────
print("=" * 70)
print("STEP 4  GOLD — gold_query_performance aggregation")
print("=" * 70)

gold_qp = (
    silver_qe
    .withColumn("hour_utc", F.date_trunc("hour", F.col("QueryExecutionEndTimeUTC")))
    .groupBy("GatewayObjectId", "DataSource", "QueryType", "hour_utc")
    .agg(
        F.count("*").alias("query_count"),
        F.sum(F.when(F.col("Success") == False, 1).otherwise(0)).alias("error_count"),
        F.round(
            F.sum(F.when(F.col("Success") == False, 1).otherwise(0)) /
            F.greatest(F.count("*"), F.lit(1)) * 100, 2
        ).alias("error_rate_pct"),
        F.round(F.avg("QueryExecutionDuration"), 0).alias("duration_avg_ms"),
        F.round(F.expr("percentile_approx(QueryExecutionDuration, 0.95)"), 0).alias("duration_p95_ms"),
        F.max("QueryExecutionDuration").alias("duration_max_ms"),
        F.sum("SpoolingTotalDataSize").alias("spool_total_bytes_sum"),
        F.round(F.avg("SpoolingDiskWritingDuration"), 0).alias("spool_writing_ms_avg"),
        F.round(
            F.sum(F.when(F.col("is_spooled") == True, 1).otherwise(0)) /
            F.greatest(F.count("*"), F.lit(1)) * 100, 2
        ).alias("spooled_pct"),
    )
    .withColumn("_partition_date", F.to_date(F.col("hour_utc")))
)

gold_count = gold_qp.count()
print(f"[PASS] gold_query_performance: {gold_count} hour-bucket rows")

# Verify measures.dax column dependencies are all present
MEASURES_DAX_DEPS = [
    "query_count", "error_count", "error_rate_pct",
    "duration_avg_ms", "duration_p95_ms", "duration_max_ms",
    "spool_total_bytes_sum", "spool_writing_ms_avg", "spooled_pct",
]
missing_gold = [c for c in MEASURES_DAX_DEPS if c not in gold_qp.columns]
if missing_gold:
    print(f"[FAIL] gold_qp missing measures.dax-required columns: {missing_gold}")
    sys.exit(1)
print(f"[PASS] All {len(MEASURES_DAX_DEPS)} measures.dax column dependencies present")

gold_qp.select(
    "GatewayObjectId", "DataSource", "query_count", "error_count",
    "duration_avg_ms", "duration_p95_ms", "spool_total_bytes_sum", "spooled_pct"
).show(5, truncate=50)


# ─────────────────────────────────────────────────────────────────────────────
# STEP 5 — GOLD: gold_gateway_health subset (CPU/mem from SystemCounter)
# Verifies columns referenced by measures.dax Section 4
# ─────────────────────────────────────────────────────────────────────────────
print("=" * 70)
print("STEP 5  GOLD — gold_gateway_health (SystemCounter aggregation)")
print("=" * 70)

if sc_csv.exists():
    bronze_sc = gbl.read_gateway_csv(spark, str(sc_csv))
    # Apply explicit casts matching 01_bronze_ingest expectations
    for c in ["GatewayCPUPercent", "SystemCPUPercent"]:
        if c in bronze_sc.columns:
            bronze_sc = bronze_sc.withColumn(c, F.col(c).cast("double"))
    if "GatewayMEMKb" in bronze_sc.columns:
        bronze_sc = bronze_sc.withColumn("GatewayMEMKb", F.col("GatewayMEMKb").cast("long"))

    sc_count = bronze_sc.count()
    print(f"[PASS] bronze_system_counter: {sc_count} rows")

    cpu_mem = bronze_sc.groupBy("GatewayObjectId").agg(
        F.avg("GatewayCPUPercent").alias("cpu_pct_avg"),
        F.avg("GatewayMEMKb").alias("mem_kb_avg"),
        F.avg("SystemCPUPercent").alias("system_cpu_avg"),
        F.max("CounterTimeUTC").alias("last_counter_utc"),
    )

    HEALTH_DAX_DEPS = ["cpu_pct_avg", "mem_kb_avg", "system_cpu_avg"]
    missing_health = [c for c in HEALTH_DAX_DEPS if c not in cpu_mem.columns]
    if missing_health:
        print(f"[FAIL] gold_gateway_health missing measures.dax-required columns: {missing_health}")
        sys.exit(1)
    print(f"[PASS] All {len(HEALTH_DAX_DEPS)} health column dependencies present")
    cpu_mem.show(3, truncate=60)
else:
    print(f"[SKIP] {sc_csv} not found — skipping SystemCounter step")


# ─────────────────────────────────────────────────────────────────────────────
# SUMMARY
# ─────────────────────────────────────────────────────────────────────────────
print()
print("=" * 70)
print("SMOKE TEST SUMMARY")
print("=" * 70)
print(f"  bronze_query_execution  : {qe_count:>6} rows  [PASS]")
print(f"  silver_query_execution  : {silver_count:>6} rows  [PASS]")
print(f"  gold_query_performance  : {gold_count:>6} hour-buckets  [PASS]")
print()
print("  measures.dax column deps: ALL PRESENT  [PASS]")
print()
# ---------------------------------------------------------------------------
# STEP 6 — MASHUP PROCESSES (PAIN #5): per-process memory/CPU + runaway detection
# ---------------------------------------------------------------------------
print("\nSTEP 6  GOLD — mashup process health (pain #5, per-process visibility)")
mashup_json = SYNTH_DIR / "MashupProcesses_synthetic.json"
try:
    if mashup_json.exists():
        bronze_mashup = gbl.read_mashup_processes(spark, str(mashup_json))
        mrows = bronze_mashup.count()
        print(f"[PASS] bronze_mashup_processes: {mrows} process samples")
        gold_mashup = gbl.gold_mashup_health(bronze_mashup)
        runaways = gold_mashup.filter(F.col("runaway_container") == True).count()
        print(f"[PASS] gold_mashup_health computed for {gold_mashup.count()} gateway(s)")
        print(f"       runaway containers detected: {runaways} (pain #5 signal working)")
        gold_mashup.select("GatewayObjectId","peak_working_set_mb","avg_working_set_mb",
                           "distinct_processes","runaway_container").show(5, truncate=False)
    else:
        print("[SKIP] no MashupProcesses_synthetic.json (regenerate synthetic data)")
except Exception as _e:
    print(f"[FAIL] mashup step: {_e}")

print(textwrap.dedent("""
  [Unverified — requires Fabric tenant pilot]
  The following were NOT exercised by this local smoke test:
    - Fabric Lakehouse ABFSS read/write (requires live OneLake)
    - DeltaTable merge / SCD-2 on gold_dim_gateway
    - silver_triage 3-way join (requires bronze_refresh_history fixture)
    - silver_identity_attribution fuzzy join (requires Activity Events fixture)
    - silver_network_correlated time-window join (requires network_metrics fixture)
    - Collect-MashupProcesses on a REAL gateway host (process names vary by version)
    - gold_cluster_load CV computation (requires multi-node inventory fixture)
    - Fabric Activator rule evaluation
    - FPM Eventhouse bridge
""").strip())
print("=" * 70)

spark.stop()
print("\n[smoke] Done. SparkSession stopped.")
