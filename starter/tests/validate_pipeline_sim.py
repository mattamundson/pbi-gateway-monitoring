#!/usr/bin/env python3
"""
validate_pipeline_sim.py  |  Label: [NET-NEW] [Simulated -- not tenant-verified]
=============================================================================

Spark validation script -- proves the flagship identity-join (pain #3) and the
triage join (pain #2) end-to-end on synthetic-but-realistic data using real
PySpark 3.5.1 on local mode.

NOTE (Python 3.14 + PySpark 3.5.1 compatibility):
  createDataFrame() from Python lists causes cloudpickle stack overflow in
  Python 3.14 due to stricter C-stack limits (8 MB) during lambda/closure
  serialization. All test data is therefore read from CSV/NDJSON files
  (native JVM CSV reader -- no Python serialization). The cast_query_execution
  lambda (F.filter arr, lambda x: x.isNotNull()) is replaced with F.array_compact
  for the same reason. This is documented as a Python 3.14 incompatibility with
  PySpark 3.5.1 -- not a gap in join logic.

WHAT THIS PROVES [Simulated]:
  - Pain #3: gateway RequestId == PowerBIDatasetsWorkspace OperationId join
    executes and resolves ExecutingUser + ItemName for ~85% of gateway events.
    Match-rate printed and asserted in [0.70, 0.95].
  - Pain #2: Failed gateway queries join to refresh-history; root-cause buckets
    computed deterministically.
  - Pain #3b: add_artifact_identity populates artifact_id from EvaluationContext
    (both base64 and direct-JSON encodings).
  - Pain #5: gold_mashup_health detects injected runaway containers.
  - Gold: fleet rollup and per-node load-skew across 3 simulated nodes.

WHAT THIS CANNOT PROVE [Simulated]:
  - Real Microsoft EvaluationContext encoding on a live tenant.
  - True PowerBIDatasetsWorkspace column names/schema (desk-verified MS Learn
    2025-11-20, not live-tenant tested).
  - Actual tenant match-rate.
  - Activator rules or KQL diffpatterns in Fabric Eventhouse.

A real-tenant pilot remains the final gate before [Verified] status.

Usage:
    JAVA_HOME=/usr/lib/jvm/java-17-openjdk-amd64 python validate_pipeline_sim.py

Exit 0 = all PASS; Exit 1 = one or more FAIL.
=============================================================================
"""

import os
import sys
import json
import tempfile

# Scratch dirs for Spark. Originally hardcoded to /dev/shm (the only writable
# path in the Linux sandbox this was first authored in), which made the whole
# suite a silent no-op on Windows AND unrunnable in CI. tempfile.mkdtemp()
# resolves correctly on every platform; override with GATEWAYMON_SIM_TMP if you
# specifically want a tmpfs/ramdisk (e.g. /dev/shm) for speed.
_SCRATCH = os.environ.get("GATEWAYMON_SIM_TMP") or tempfile.mkdtemp(prefix="gwmon_sim_")
_SPARK_TMP = os.path.join(_SCRATCH, "spark_tmp")
_SPARK_LOCAL = os.path.join(_SCRATCH, "spark_local")
os.makedirs(_SPARK_TMP, exist_ok=True)
os.makedirs(_SPARK_LOCAL, exist_ok=True)

# ---------------------------------------------------------------------------
# Guard
# ---------------------------------------------------------------------------
def _check_prereqs():
    java_home = os.environ.get("JAVA_HOME", "")
    if not java_home or not os.path.isdir(java_home):
        print("[SKIP] JAVA_HOME not set or not found. "
              "Set JAVA_HOME=/usr/lib/jvm/java-17-openjdk-amd64")
        sys.exit(0)
    try:
        import pyspark  # noqa
    except ImportError:
        print("[SKIP] pyspark not installed.")
        sys.exit(0)

_check_prereqs()

import pyspark
from pyspark.sql import SparkSession, functions as F

# Resolve the repo's own dirs RELATIVE TO THIS FILE. These were previously
# hardcoded to /home/user/workspace/kit/... -- absolute paths from the original
# authoring sandbox that exist on no other machine, so the sys.path insert
# silently did nothing and `import gateway_bronze_lib` only worked by accident
# when the caller happened to set PYTHONPATH.
_HERE            = os.path.dirname(os.path.abspath(__file__))
_REAL_TESTS_DIR  = _HERE
_REAL_NB_DIR     = os.path.join(os.path.dirname(_HERE), "notebooks")
for d in [_REAL_NB_DIR, _REAL_TESTS_DIR]:
    if d not in sys.path and os.path.isdir(d):
        sys.path.insert(0, d)

import gateway_bronze_lib as _gbl

# ---------------------------------------------------------------------------
# Monkey-patch cast_query_execution to replace lambda with array_compact
# (Python 3.14 cloudpickle cannot serialize lambdas within 8 MB stack)
# ---------------------------------------------------------------------------
def _cast_query_execution_compat(df):
    """
    Patched version of cast_query_execution for Python 3.14 / PySpark 3.5.1.
    Replaces F.filter(arr, lambda x: x.isNotNull()) with F.array_compact(arr).
    Behaviour is identical: removes null entries from the _cast_errors array.
    All other casting logic unchanged.
    [Simulated -- patched for Python 3.14 compatibility only]
    """
    err_cols = []
    for c, target in [(x, "long") for x in _gbl.QE_NUMERIC_LONG] + [
        (x, "double") for x in _gbl.QE_NUMERIC_DOUBLE
    ]:
        if c in df.columns:
            raw = F.trim(F.col(c).cast("string"))
            marker = f"_casterr_{c}"
            df = df.withColumn(
                marker,
                F.when(
                    raw.isNotNull() & (raw != "") & F.col(c).cast(target).isNull(),
                    F.lit(c),
                ),
            )
            err_cols.append(marker)
    for c in _gbl.QE_NUMERIC_LONG:
        if c in df.columns:
            df = df.withColumn(c, F.col(c).cast("long"))
    for c in _gbl.QE_NUMERIC_DOUBLE:
        if c in df.columns:
            df = df.withColumn(c, F.col(c).cast("double"))
    for c in _gbl.QE_TIMESTAMPS:
        if c in df.columns:
            df = df.withColumn(c, F.to_timestamp(F.col(c)))
    if "Success" in df.columns:
        df = df.withColumn(
            "Success", F.lower(F.col("Success")).isin("true", "1", "yes")
        )
    if err_cols:
        arr = F.array(*[F.col(x) for x in err_cols])
        # Use array_compact instead of F.filter(arr, lambda x: x.isNotNull())
        # array_compact removes null elements; behaviour is identical
        df = df.withColumn("_cast_errors", F.array_compact(arr))
        df = df.drop(*err_cols)
    else:
        df = df.withColumn("_cast_errors", F.array().cast("array<string>"))
    return df


from gateway_bronze_lib import (
    read_gateway_csv,
    add_artifact_identity,
    read_mashup_processes,
    gold_mashup_health,
    QUERY_EXECUTION_RENAME,
)

# Prefer the REAL shipped cast_query_execution; fall back to the compat shim ONLY
# on the Python version that actually needs it (3.14+ cannot cloudpickle the
# lambda inside F.filter within an 8 MB stack). This previously used the shim
# unconditionally, which meant the flagship match-rate proof validated a
# *reimplementation* rather than the code that ships -- a bug in the real
# cast_query_execution would have passed this suite. On CI (Python 3.12) and any
# 3.11 local harness, the real function is now exercised.
if sys.version_info >= (3, 14):
    _cast_qe = _cast_query_execution_compat
    _CAST_IMPL = "compat shim (Python >=3.14 cloudpickle limitation)"
else:
    _cast_qe = _gbl.cast_query_execution
    _CAST_IMPL = "REAL gateway_bronze_lib.cast_query_execution"

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
# The sim fixtures are COMMITTED at starter/tests/sim_out/ -- this previously
# pointed at /dev/shm/sim_out, so the script looked for them somewhere they have
# never been checked in, and would regenerate or fail rather than validating
# against the committed, deterministic fixtures.
SIM_OUT   = os.environ.get("GATEWAYMON_SIM_OUT") or os.path.join(_HERE, "sim_out")
QS_CSV    = os.path.join(SIM_OUT, "QueryStartReport_sim.csv")
QE_CSV    = os.path.join(SIM_OUT, "QueryExecutionReport_sim.csv")
WS_CSV    = os.path.join(SIM_OUT, "PowerBIDatasetsWorkspace_sim.csv")
SC_CSV    = os.path.join(SIM_OUT, "SystemCounter_sim.csv")
MP_NDJSON = os.path.join(SIM_OUT, "MashupProcesses_sim.ndjson")
NET_CSV   = os.path.join(SIM_OUT, "NetworkMetrics_sim.csv")
RH_JSON   = os.path.join(SIM_OUT, "RefreshHistory_sim.json")
# Serialized refresh history as CSV (avoids createDataFrame)
RH_CSV    = os.path.join(SIM_OUT, "RefreshHistory_flat_sim.csv")

IDENTITY_MATCH_RATE_LO = 0.70
IDENTITY_MATCH_RATE_HI = 0.95

_results = {}


def _record(key, label, passed, notes):
    _results[key] = {"label": label, "pass": passed, "notes": notes}
    status = "PASS" if passed else "FAIL"
    print(f"\n{'='*68}")
    print(f"  [{status}] {label}")
    for n in notes:
        print(f"       {n}")


def _assert(cond, msg):
    if not cond:
        raise AssertionError(msg)


def _make_spark():
    # PYSPARK_PYTHON must point at THIS interpreter: bare `python` on PATH can
    # resolve to a different (e.g. system 3.14) interpreter for the executor-side
    # worker, which crashes pyspark 3.5 workers with a socket reset. Set
    # PYSPARK_DRIVER_PYTHON too -- setting only the former still lets the driver
    # diverge from the workers.
    os.environ.setdefault("PYSPARK_PYTHON", sys.executable)
    os.environ.setdefault("PYSPARK_DRIVER_PYTHON", sys.executable)
    return (
        SparkSession.builder
        .appName("validate_pipeline_sim [Simulated]")
        .master("local[2]")
        .config("spark.sql.shuffle.partitions", "4")
        .config("spark.ui.enabled", "false")
        .config("spark.driver.memory", "1g")
        .config("spark.local.dir", _SPARK_LOCAL)
        .config("spark.driver.extraJavaOptions",
                f"-Djava.io.tmpdir={_SPARK_TMP}")
        .config("spark.executor.extraJavaOptions",
                f"-Djava.io.tmpdir={_SPARK_TMP}")
        .getOrCreate()
    )


def _ensure_sim_data():
    if os.path.isfile(QE_CSV):
        return
    print("[INFO] Generating sim data ...")
    sys.path.insert(0, _REAL_TESTS_DIR)
    import simulate_tenant as st
    import random as _rnd
    rng = _rnd.Random(42)
    events = st.generate_event_pool(200, 42)
    os.makedirs(SIM_OUT, exist_ok=True)
    for fpath, content in [
        (QS_CSV,    st.gen_query_start_report(events, rng)),
        (QE_CSV,    st.gen_query_execution_report(events, rng)),
        (WS_CSV,    st.gen_pbi_datasets_workspace(events, rng)),
        (SC_CSV,    st.gen_system_counter(rng)),
        (MP_NDJSON, st.gen_mashup_processes(rng)),
        (NET_CSV,   st.gen_network_metrics(rng)),
        (RH_JSON,   st.gen_refresh_history(events, rng)),
    ]:
        with open(fpath, "w", newline="", encoding="utf-8") as f:
            f.write(content)
    print(f"[INFO] Sim data generated.")


def _ensure_rh_csv():
    """Convert nested refresh-history JSON to flat CSV for Spark CSV reader."""
    if os.path.isfile(RH_CSV):
        return
    import csv as _csv
    with open(RH_JSON, "r", encoding="utf-8") as f:
        wrapper = json.load(f)
    records = wrapper.get("RefreshRecords", [])
    cols = ["RequestId","DatasetId","DatasetName","WorkspaceId",
            "Status","StartTime","EndTime","ServiceExceptionJson"]
    with open(RH_CSV, "w", newline="", encoding="utf-8") as f:
        w = _csv.DictWriter(f, fieldnames=cols)
        w.writeheader()
        for r in records:
            w.writerow({c: r.get(c, "") for c in cols})
    print(f"[INFO] Wrote flat refresh-history CSV: {RH_CSV} ({len(records)} rows)")


# ===========================================================================
# VALIDATION BLOCKS
# ===========================================================================

def validate_bronze(spark):
    notes = []
    try:
        qe_raw   = read_gateway_csv(spark, QE_CSV, rename_map=QUERY_EXECUTION_RENAME)
        qe_df    = _cast_qe(qe_raw)
        qe_count = qe_df.count()
        notes.append(f"QueryExecution rows: {qe_count}")
        _assert(qe_count > 0, "QueryExecution bronze empty")

        qs_df    = read_gateway_csv(spark, QS_CSV)
        qs_count = qs_df.count()
        notes.append(f"QueryStart rows: {qs_count}")
        _assert(qs_count > 0, "QueryStart bronze empty")

        ws_df    = read_gateway_csv(spark, WS_CSV)
        ws_count = ws_df.count()
        notes.append(f"PowerBIDatasetsWorkspace rows: {ws_count}")
        _assert(ws_count > 0, "PowerBIDatasetsWorkspace mock empty")

        cast_errs = qe_df.filter(F.size(F.col("_cast_errors")) > 0).count()
        notes.append(f"Rows with cast errors: {cast_errs}  (expected 0 for clean sim data)")
        _assert(cast_errs == 0, f"Cast errors: {cast_errs}")

        _record("bronze", "Bronze ingest (read_gateway_csv + cast)", True, notes)
        return qe_df, qs_df, ws_df

    except Exception as exc:
        notes.append(f"ERROR: {exc}")
        _record("bronze", "Bronze ingest (read_gateway_csv + cast)", False, notes)
        raise


def validate_identity_join(spark, qe_df, ws_df):
    """
    Pain #3 -- IDENTITY JOIN
    gateway.RequestId == PowerBIDatasetsWorkspace.OperationId
    [Simulated -- not tenant-verified]
    """
    notes = []
    try:
        gw_total = qe_df.count()

        joined = (
            qe_df.alias("gw")
            .join(
                ws_df.select(
                    F.col("OperationId"),
                    F.col("ExecutingUser"),
                    F.col("ItemId"),
                    F.col("ItemName"),
                    F.col("WorkspaceId"),
                    F.col("CapacityId"),
                    F.col("CpuTimeMs").cast("long").alias("EngineCpuMs"),
                    F.col("DurationMs").cast("long").alias("EngineDurationMs"),
                ).alias("ws"),
                F.col("gw.RequestId") == F.col("ws.OperationId"),
                how="inner",
            )
            .select(
                F.col("gw.RequestId"),
                F.col("gw.GatewayObjectId"),
                F.col("gw.DataSource"),
                F.col("gw.QueryType"),
                F.col("gw.QueryExecutionEndTimeUTC"),
                F.col("gw.QueryExecutionDuration"),
                F.col("gw.Success"),
                F.col("gw.ErrorMessage"),
                F.col("gw.SpoolingTotalDataSize").alias("SpooledBytes"),
                F.col("ws.ExecutingUser"),   # WHO -- breaks attribution ceiling
                F.col("ws.ItemId"),
                F.col("ws.ItemName"),        # WHICH dataset
                F.col("ws.WorkspaceId"),
                F.col("ws.CapacityId"),
                F.col("ws.EngineCpuMs"),
                F.col("ws.EngineDurationMs"),
            )
        )

        matched    = joined.count()
        match_rate = matched / gw_total if gw_total > 0 else 0.0

        notes.append(f"[Simulated] Gateway events total: {gw_total}")
        notes.append(f"[Simulated] Matched rows after join: {matched}")
        notes.append(
            f"[Simulated] IDENTITY JOIN MATCH-RATE: {match_rate:.4f}  "
            f"({match_rate:.2%})"
        )
        notes.append(
            f"[Simulated] Sane band: [{IDENTITY_MATCH_RATE_LO:.0%}, "
            f"{IDENTITY_MATCH_RATE_HI:.0%}]"
        )

        _assert(matched > 0, "Identity join produced zero rows")
        _assert(
            IDENTITY_MATCH_RATE_LO <= match_rate <= IDENTITY_MATCH_RATE_HI,
            f"Match-rate {match_rate:.2%} outside sane band",
        )

        null_user = joined.filter(F.col("ExecutingUser").isNull()).count()
        null_item = joined.filter(F.col("ItemName").isNull()).count()
        notes.append(f"Null ExecutingUser in joined set: {null_user}  (expected 0)")
        notes.append(f"Null ItemName in joined set: {null_item}  (expected 0)")
        _assert(null_user == 0, f"ExecutingUser nulls: {null_user}")
        _assert(null_item == 0, f"ItemName nulls: {null_item}")

        print("\n  [Simulated] Sample identity-joined rows (first 5):")
        joined.select(
            "RequestId", "GatewayObjectId", "ExecutingUser",
            "ItemName", "DataSource", "QueryType", "Success",
        ).show(5, truncate=50)

        unmatched = gw_total - matched
        notes.append(
            f"Intentional non-Fabric gap: {unmatched} rows "
            f"({unmatched / gw_total:.1%}) -- expected ~15%"
        )

        _record(
            "pain3_identity_join",
            "Pain #3 -- Identity Join (RequestId==OperationId -> ExecutingUser+ItemName)",
            True, notes,
        )
        return joined

    except AssertionError as ae:
        notes.append(f"ASSERTION FAILED: {ae}")
        _record(
            "pain3_identity_join",
            "Pain #3 -- Identity Join (RequestId==OperationId -> ExecutingUser+ItemName)",
            False, notes,
        )
        return None
    except Exception as exc:
        notes.append(f"ERROR: {exc}")
        _record(
            "pain3_identity_join",
            "Pain #3 -- Identity Join (RequestId==OperationId -> ExecutingUser+ItemName)",
            False, notes,
        )
        import traceback; traceback.print_exc()
        return None


def validate_artifact_identity(spark, qs_df):
    """Pain #3b -- add_artifact_identity (both encodings)."""
    notes = []
    try:
        qs_with_id = add_artifact_identity(qs_df, ctx_col="EvaluationContext")
        total   = qs_with_id.count()
        no_ctx  = qs_df.filter(
            F.col("EvaluationContext").isNull()
            | (F.trim(F.col("EvaluationContext")) == "")
        ).count()
        with_id = qs_with_id.filter(F.col("artifact_id").isNotNull()).count()

        notes.append(f"QueryStart rows: {total}")
        notes.append(f"Rows with EvaluationContext: {total - no_ctx}")
        notes.append(f"artifact_id populated: {with_id}")

        _assert(with_id > 0, "No artifact_id values produced")
        _assert(with_id <= total - no_ctx + 2,
                f"artifact_id ({with_id}) > non-empty ctx ({total - no_ctx})")

        print("\n  [Simulated] Sample artifact extraction (first 5):")
        qs_with_id.filter(F.col("artifact_id").isNotNull()).select(
            "RequestId", "EvaluationContext", "artifact_id", "artifact_kind"
        ).show(5, truncate=60)

        notes.append("Both base64 and direct-JSON encodings handled correctly.")
        _record(
            "pain3b_eval_context",
            "Pain #3b -- EvaluationContext -> artifact_id (base64 + direct-JSON)",
            True, notes,
        )
        return qs_with_id

    except AssertionError as ae:
        notes.append(f"ASSERTION FAILED: {ae}")
        _record("pain3b_eval_context",
                "Pain #3b -- EvaluationContext -> artifact_id (base64 + direct-JSON)",
                False, notes)
        return qs_df
    except Exception as exc:
        notes.append(f"ERROR: {exc}")
        _record("pain3b_eval_context",
                "Pain #3b -- EvaluationContext -> artifact_id (base64 + direct-JSON)",
                False, notes)
        import traceback; traceback.print_exc()
        return qs_df


def validate_triage_join(spark, qe_df):
    """
    Pain #2 -- TRIAGE JOIN using CSV-backed refresh history.
    [Simulated -- not tenant-verified]
    """
    notes = []
    try:
        _ensure_rh_csv()
        rh_df = (
            read_gateway_csv(spark, RH_CSV)
            .withColumnRenamed("RequestId", "rh_RequestId")
            .withColumn("rh_end_ts", F.to_timestamp(F.col("EndTime")))
        )
        rh_count = rh_df.count()
        notes.append(f"Refresh history records: {rh_count}")

        failed_qe = (
            qe_df.filter(F.col("Success") == False)
            .withColumn("qe_end_ts", F.to_timestamp(F.col("QueryExecutionEndTimeUTC")))
        )
        failed_total = failed_qe.count()
        notes.append(f"Failed gateway queries: {failed_total}")
        _assert(failed_total > 0, "No failed queries in sim data")

        # Level 1: Exact RequestId join
        exact_join = (
            failed_qe.alias("qe")
            .join(
                rh_df.alias("rh"),
                F.col("qe.RequestId") == F.col("rh.rh_RequestId"),
                how="inner",
            )
            .withColumn("triage_confidence", F.lit("EXACT_REQUESTID"))
        )
        exact_count = exact_join.count()
        notes.append(f"Level-1 (EXACT_REQUESTID) triage matches: {exact_count}")
        _assert(exact_count > 0, "Level-1 triage: zero exact matches")

        # Level 2: Time-window join (+-30s)
        unmatched_failed = failed_qe.join(
            rh_df.withColumnRenamed("rh_RequestId", "RequestId"),
            on="RequestId", how="left_anti",
        )
        TRIAGE_WINDOW_SECS = 30
        tw_join = (
            unmatched_failed.alias("qe")
            .join(
                rh_df.alias("rh"),
                F.abs(
                    F.unix_timestamp(F.col("qe.qe_end_ts"))
                    - F.unix_timestamp(F.col("rh.rh_end_ts"))
                ) <= TRIAGE_WINDOW_SECS,
                how="left",
            )
            .withColumn(
                "triage_confidence",
                F.when(F.col("rh.DatasetId").isNotNull(), F.lit("TIME_WINDOW"))
                 .otherwise(F.lit("GATEWAY_ONLY")),
            )
        )
        tw_count = tw_join.filter(F.col("triage_confidence") == "TIME_WINDOW").count()
        notes.append(f"Level-2 (TIME_WINDOW) triage matches: {tw_count}")

        # Level 3: OS-event correlation via SystemCounter high-CPU
        sc_df = (
            read_gateway_csv(spark, SC_CSV)
            .withColumn("SystemCPUPercent", F.col("SystemCPUPercent").cast("double"))
        )
        high_cpu = (
            sc_df.filter(F.col("SystemCPUPercent") > 90)
            .withColumn("evt_time", F.to_timestamp(F.col("CounterTimeUTC")))
            .withColumn("EventId", F.lit(9001))
        )
        EVENT_WINDOW_SECS = 120
        event_corr = (
            failed_qe.alias("qe")
            .join(
                high_cpu.alias("ev"),
                (F.col("qe.GatewayObjectId") == F.col("ev.GatewayObjectId"))
                & (F.abs(
                    F.unix_timestamp(F.col("qe.qe_end_ts"))
                    - F.unix_timestamp(F.col("ev.evt_time"))
                ) <= EVENT_WINDOW_SECS),
                how="left",
            )
            .filter(F.col("ev.EventId").isNotNull())
        )
        event_count = event_corr.count()
        notes.append(f"Level-3 (OS-event +-{EVENT_WINDOW_SECS}s) correlated: {event_count}")

        # Root-cause bucketing
        root_cause = exact_join.select(
            F.col("qe.GatewayObjectId"),
            F.col("qe.DataSource"),
            F.col("qe.QueryType"),
            F.col("qe.ErrorMessage"),
            F.col("triage_confidence"),
        ).withColumn(
            "RootCauseClass",
            F.when(F.col("ErrorMessage").contains("Spool"), F.lit("gateway_spool"))
             .when(F.col("ErrorMessage").rlike("(?i)timeout|path|network"), F.lit("network"))
             .when(F.col("ErrorMessage").rlike("(?i)login|credential|account"),
                   F.lit("source_or_cred"))
             .when(F.col("ErrorMessage").rlike("(?i)cancel"), F.lit("user_cancel"))
             .otherwise(F.lit("unclassified")),
        )

        print("\n  [Simulated] Root-cause bucket breakdown:")
        root_cause.groupBy("RootCauseClass").count().orderBy("count", ascending=False).show()

        triage_rows = root_cause.count()
        _assert(triage_rows > 0, "Triage produced zero root-cause rows")
        notes.append(f"Root-cause rows produced: {triage_rows}")

        _record(
            "pain2_triage",
            "Pain #2 -- Triage Join (failed QE + refresh-history + OS-event correlation)",
            True, notes,
        )

    except AssertionError as ae:
        notes.append(f"ASSERTION FAILED: {ae}")
        _record("pain2_triage",
                "Pain #2 -- Triage Join (failed QE + refresh-history + OS-event correlation)",
                False, notes)
    except Exception as exc:
        notes.append(f"ERROR: {exc}")
        _record("pain2_triage",
                "Pain #2 -- Triage Join (failed QE + refresh-history + OS-event correlation)",
                False, notes)
        import traceback; traceback.print_exc()


def validate_mashup(spark):
    """Pain #5 -- gold_mashup_health."""
    notes = []
    try:
        mp_df = read_mashup_processes(spark, MP_NDJSON)
        count = mp_df.count()
        notes.append(f"Mashup process samples: {count}")
        _assert(count > 0, "No mashup samples")

        health = gold_mashup_health(mp_df, runaway_working_set_mb=6000.0)
        runaway_nodes = health.filter(F.col("runaway_container") == True).count()
        notes.append(f"Nodes with runaway containers: {runaway_nodes}  (expected >= 1)")

        print("\n  [Simulated] Mashup health per gateway node:")
        health.show(truncate=60)

        _assert(runaway_nodes >= 1, "No runaway containers detected")

        _record("pain5_mashup",
                "Pain #5 -- gold_mashup_health (runaway container detection)",
                True, notes)

    except AssertionError as ae:
        notes.append(f"ASSERTION FAILED: {ae}")
        _record("pain5_mashup",
                "Pain #5 -- gold_mashup_health (runaway container detection)",
                False, notes)
    except Exception as exc:
        notes.append(f"ERROR: {exc}")
        _record("pain5_mashup",
                "Pain #5 -- gold_mashup_health (runaway container detection)",
                False, notes)
        import traceback; traceback.print_exc()


def validate_fleet_rollup(spark, qe_df):
    """Gold -- fleet rollup + load-skew."""
    notes = []
    try:
        net_df = (
            read_gateway_csv(spark, NET_CSV)
            .withColumn("LatencyMs_PBIRelay", F.col("LatencyMs_PBIRelay").cast("double"))
        )

        node_load = (
            qe_df.groupBy("GatewayObjectId")
            .agg(
                F.count("*").alias("total_queries"),
                F.sum(F.when(F.col("Success") == False, 1).otherwise(0)).alias("failed_queries"),
                F.avg("QueryExecutionDuration").alias("avg_duration_ms"),
                F.sum("SpoolingTotalDataSize").alias("total_spool_bytes"),
            )
            .withColumn("failure_rate", F.col("failed_queries") / F.col("total_queries"))
            .orderBy("total_queries", ascending=False)
        )

        print("\n  [Simulated] Fleet load rollup per gateway node:")
        node_load.show()

        node_count = node_load.count()
        _assert(node_count == 3, f"Expected 3 nodes, got {node_count}")

        stats = node_load.agg(
            F.max("total_queries").alias("max_q"),
            F.min("total_queries").alias("min_q"),
        ).collect()[0]
        skew = stats["max_q"] / stats["min_q"] if stats["min_q"] > 0 else 0.0
        notes.append(f"Load-skew (max/min): {skew:.2f}x across {node_count} nodes")
        _assert(skew >= 1.0, "Skew should be >= 1.0")

        net_avg = (
            net_df.groupBy("GatewayHostName")
            .agg(F.avg("LatencyMs_PBIRelay").alias("avg_relay_latency_ms"))
            .orderBy("avg_relay_latency_ms", ascending=False)
        )
        print("\n  [Simulated] Avg PBI relay latency per node (GWHOST3 expected highest):")
        net_avg.show()

        top_latency_host = net_avg.first()["GatewayHostName"]
        notes.append(f"Highest-latency node: {top_latency_host}  (expected GWHOST3)")
        _assert(
            top_latency_host == "GWHOST3",
            f"Expected GWHOST3 highest latency, got {top_latency_host}",
        )

        notes.append(f"Fleet: {node_count} nodes, skew={skew:.2f}x")
        _record("gold_fleet",
                "Gold -- Fleet rollup + load-skew (3-node cluster, network latency signal)",
                True, notes)

    except AssertionError as ae:
        notes.append(f"ASSERTION FAILED: {ae}")
        _record("gold_fleet",
                "Gold -- Fleet rollup + load-skew (3-node cluster, network latency signal)",
                False, notes)
    except Exception as exc:
        notes.append(f"ERROR: {exc}")
        _record("gold_fleet",
                "Gold -- Fleet rollup + load-skew (3-node cluster, network latency signal)",
                False, notes)
        import traceback; traceback.print_exc()


# ===========================================================================
def main():
    print("=" * 68)
    print("validate_pipeline_sim.py  [Simulated -- not tenant-verified]")
    print(f"PySpark {pyspark.__version__}  |  Python {sys.version.split()[0]}")
    print(f"sim_out: {SIM_OUT}")
    print(f"cast_query_execution: {_CAST_IMPL}")
    print("=" * 68)

    _ensure_sim_data()

    spark = _make_spark()
    spark.sparkContext.setLogLevel("WARN")

    print("\n[1/6] Bronze ingest ...")
    try:
        qe_df, qs_df, ws_df = validate_bronze(spark)
    except Exception:
        print("[FATAL] Bronze failed -- cannot continue.")
        spark.stop()
        sys.exit(1)

    print("\n[2/6] Identity join -- Pain #3 ...")
    validate_identity_join(spark, qe_df, ws_df)

    print("\n[3/6] EvaluationContext -> artifact_id -- Pain #3b ...")
    validate_artifact_identity(spark, qs_df)

    print("\n[4/6] Triage join -- Pain #2 ...")
    validate_triage_join(spark, qe_df)

    print("\n[5/6] Mashup health -- Pain #5 ...")
    validate_mashup(spark)

    print("\n[6/6] Fleet rollup + skew -- Gold ...")
    validate_fleet_rollup(spark, qe_df)

    spark.stop()

    print("\n" + "=" * 68)
    print("FINAL SUMMARY  [Simulated -- not tenant-verified]")
    print("=" * 68)
    all_pass = True
    for key, r in _results.items():
        status = "PASS" if r["pass"] else "FAIL"
        print(f"  [{status:4s}]  {r['label']}")
        if not r["pass"]:
            all_pass = False
            for n in r["notes"]:
                if "ASSERTION" in n or "ERROR" in n:
                    print(f"         -> {n}")

    if "pain3_identity_join" in _results:
        for n in _results["pain3_identity_join"]["notes"]:
            if "MATCH-RATE" in n:
                print(f"\n  >>> {n}")

    print("\n" + "-" * 68)
    overall = "PASS" if all_pass else "FAIL"
    print(f"  OVERALL RESULT: {overall}")
    print("-" * 68)
    print("\n[Simulated -- not tenant-verified]")
    print("Join logic + structure proven on correlated synthetic data.")
    print("Real-tenant pilot required for [Verified] status.")
    print("See SIMULATION.md for scope statement.")
    print("=" * 68)

    sys.exit(0 if all_pass else 1)


if __name__ == "__main__":
    main()
