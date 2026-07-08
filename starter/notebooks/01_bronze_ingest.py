# =============================================================================
# 01_bronze_ingest.py
# Label: [NET-NEW + ADAPTED-FROM-FPM]
#
# Pain point addressed: #4 — PBIT breaks on gateway upgrade / log schema drift
# This is DIFFERENTIATOR #5 — the schema-adaptive parser.
#
# What this does:
#   Reads raw gateway log CSVs (staged as JSON by Collect-GatewayLogs.ps1)
#   + all other collector JSON outputs and writes them to Bronze Delta tables
#   in OneLake. The CRITICAL property is column-NAME-based parsing (not
#   positional), which means adding a new column to a gateway log file does
#   NOT cause DataFormat.Error.
#
# PARSER STATUS (read this before trusting the header):
#   The scalable, column-NAME-based, native-Spark parser lives in the shared
#   module gateway_bronze_lib.read_gateway_csv() -- PERMISSIVE mode,
#   _corrupt_record capture, RFC-4180 quoting, mergeSchema; identity extraction
#   (add_artifact_identity) via UDF-FREE native Spark SQL. BOTH starter/tests
#   tiers exercise THAT LIB and it PASSES against a real Spark engine
#   (see tests/README.md).
#
#   As of 2026-07-02 (U19 follow-up) this notebook now ROUTES its gateway-log
#   ingest THROUGH gateway_bronze_lib.read_gateway_csv() -- the native-Spark,
#   column-name-based, _sanitize_columns()-protected path that the tests exercise
#   and that is PROVEN on real Spark. This closes the pain #4 live-tenant gap:
#   the notebook path is now immune to BOTH the positional DataFormat.Error (that
#   breaks the Microsoft PBIT template) AND the InvalidColumnName failure on new
#   unit-suffixed headers like `Something(ms)` (confirmed on a real tenant via
#   Load-to-Tables -- see LIVE-TENANT-FINDINGS.md 2026-07-01). The legacy
#   driver-side parser (parse_csv_schema_adaptive, below) is RETAINED only as a
#   documented FALLBACK for the JSON-staged (RawCsvContent) collector format and
#   for non-standard dialects; the primary path is the lib.
#
#   IMPORTANT (pain #4 procedural fix, unchanged): do NOT ingest the raw gateway
#   CSV via Fabric's Load-to-Tables shortcut -- that platform path fails at schema
#   inference on `(ms)`/`(bytes)` headers BEFORE any of our code runs. Always
#   route through this notebook.
#
# Schema-adaptivity design (driver-side parser below):
#   - Known columns are mapped/renamed by name (never by position)
#   - Unknown/new columns flow through via mergeSchema on write
#   - Malformed rows captured in _corrupt_record instead of failing the job
#
# Adapted from:
#   FPM log schema mappings:
#     https://github.com/microsoft/fabric-toolbox/tree/main/monitoring/fabric-platform-monitoring
#   RuiRomano/pbigtwmonitor incremental pattern (watermark concept):
#     https://github.com/RuiRomano/pbigtwmonitor
#   MS Gateway Performance Monitoring — authoritative column names:
#     https://learn.microsoft.com/en-us/data-integration/gateway/service-gateway-performance
#
# Optional FPM bridge:
#   If USE_FPM_BRIDGE = True, reads from FPM's Eventhouse tables exposed
#   as Delta via Eventhouse OneLake availability feature:
#     https://learn.microsoft.com/en-us/fabric/real-time-intelligence/event-house-onelake-availability
#   [Assumption] Eventhouse OneLake availability is enabled on customer's Eventhouse.
#
# [Unverified] This notebook has NOT been executed in a live Fabric PySpark
#              environment. All PySpark API calls require Phase 5 validation.
#              Fabric notebook variable names (LAKEHOUSE_PATH, etc.) are
#              [Assumption]-based on Fabric notebook documentation.
#
# Column names sourced from:
#   https://learn.microsoft.com/en-us/data-integration/gateway/service-gateway-performance
# =============================================================================

# ── Fabric notebook: set these as notebook parameters or pipeline variables ──
# All three roots are overridable via env vars so the ingest can run against a
# LOCAL temp dir under the pbi-spark harness (no Fabric tenant). Defaults are
# unchanged -> Fabric runtime behavior is identical.
import os

LAKEHOUSE_PATH = os.environ.get(
    "GATEWAYMON_LAKEHOUSE_ROOT",
    "abfss://<workspace>@onelake.dfs.fabric.microsoft.com/<lakehouse>.Lakehouse",
)
LANDING_PATH = os.environ.get(
    "GATEWAYMON_LANDING_ROOT", f"{LAKEHOUSE_PATH}/Files/bronze_landing"
)  # Where PS scripts drop JSON
BRONZE_PATH = f"{LAKEHOUSE_PATH}/Tables"  # Delta tables destination
# Table storage format: delta in Fabric; local tests set GATEWAYMON_TABLE_FORMAT=parquet.
_TABLE_FORMAT = os.environ.get("GATEWAYMON_TABLE_FORMAT", "delta")
USE_FPM_BRIDGE = False  # Set True if you have FPM Eventhouse with OneLake availability

# ── FPM bridge config (only used if USE_FPM_BRIDGE = True) ──
FPM_EVENTHOUSE_DELTA_PATH = (
    ""  # Path to FPM Eventhouse Delta tables (OneLake availability)
)

# ── Configuration ──────────────────────────────────────────────────────────
from pyspark.sql import SparkSession
from pyspark.sql import functions as F
from pyspark.sql.types import (
    StructType,
    StructField,
    StringType,
    LongType,
    DoubleType,
    TimestampType,
    BooleanType,
    DateType,
    MapType,
)
from delta.tables import DeltaTable
import json
import csv
import io
import base64
import binascii
from datetime import datetime, timezone

spark = SparkSession.builder.getOrCreate()
spark.conf.set("spark.sql.extensions", "io.delta.sql.DeltaSparkSessionExtension")
spark.conf.set(
    "spark.sql.catalog.spark_catalog", "org.apache.spark.sql.delta.catalog.DeltaCatalog"
)

# ── Shared bronze lib: single source of truth for the schema-adaptive,
#    column-name-sanitizing gateway CSV reader (the tested, real-Spark-proven
#    pain #4 fix). The notebook's gateway-log path routes through this. ──
# In Fabric, %run the lib notebook first, OR (local/CI) import it from the same
# folder. Guarded so the notebook still loads if the lib isn't importable, in
# which case ingest_gateway_logs() falls back to the driver-side parser.
try:
    from gateway_bronze_lib import (
        read_gateway_csv,
        cast_query_execution,
        add_artifact_identity,
        QUERY_EXECUTION_RENAME,
    )

    _BRONZE_LIB_AVAILABLE = True
except ImportError:
    _BRONZE_LIB_AVAILABLE = False
    print(
        "[WARN] gateway_bronze_lib not importable -- gateway-log ingest will use "
        "the driver-side fallback parser. In Fabric, %run the lib notebook first."
    )

# ── KNOWN COLUMN SCHEMA — sourced from MS docs, not invented ───────────────
# Source: https://learn.microsoft.com/en-us/data-integration/gateway/service-gateway-performance
# These are the EXACT column names from the gateway log CSV files.
# Note: column names in the file include units in parens, e.g. "QueryExecutionDuration(ms)"
# We normalize by stripping units in parens for Delta column names.

QUERY_EXECUTION_KNOWN_COLS = {
    # CSV column name (as written by gateway) -> Delta column name, PySpark type
    "GatewayObjectId": ("GatewayObjectId", StringType()),
    "RequestId": ("RequestId", StringType()),
    "DataSource": ("DataSource", StringType()),
    "QueryTrackingId": ("QueryTrackingId", StringType()),
    "QueryExecutionEndTimeUTC": ("QueryExecutionEndTimeUTC", TimestampType()),
    "QueryExecutionDuration(ms)": ("QueryExecutionDuration", LongType()),
    "QueryType": ("QueryType", StringType()),
    "DataProcessingEndTimeUTC": ("DataProcessingEndTimeUTC", TimestampType()),
    "DataProcessingDuration(ms)": ("DataProcessingDuration", LongType()),
    "Success": ("Success", BooleanType()),
    "ErrorMessage": ("ErrorMessage", StringType()),
    "SpoolingDiskWritingDuration(ms)": ("SpoolingDiskWritingDuration", LongType()),
    "SpoolingDiskReadingDuration(ms)": ("SpoolingDiskReadingDuration", LongType()),
    "SpoolingTotalDataSize(bytes)": ("SpoolingTotalDataSize", LongType()),
    "DataReadingAndSerializationDuration(ms)": (
        "DataReadingAndSerializationDuration",
        LongType(),
    ),
    "DiskRead(byte/sec)": ("DiskRead", DoubleType()),
    "DiskWrite(byte/sec)": ("DiskWrite", DoubleType()),
}

QUERY_START_KNOWN_COLS = {
    "GatewayObjectId": ("GatewayObjectId", StringType()),
    "RequestId": ("RequestId", StringType()),
    "QueryTrackingId": ("QueryTrackingId", StringType()),
    "QueryStartTimeUTC": ("QueryStartTimeUTC", TimestampType()),
    "QueryType": ("QueryType", StringType()),
    "DataSource": ("DataSource", StringType()),
    # EvaluationContext is a JSON blob (artifactId etc.) — keep as string, parse below
    "EvaluationContext": ("EvaluationContext", StringType()),
}

SYSTEM_COUNTER_KNOWN_COLS = {
    "GatewayObjectId": ("GatewayObjectId", StringType()),
    "CounterTimeUTC": ("CounterTimeUTC", TimestampType()),
    "SystemCPUPercent": ("SystemCPUPercent", DoubleType()),
    "SystemMEMUsedPercent": ("SystemMEMUsedPercent", DoubleType()),
    "GatewayCPUPercent": ("GatewayCPUPercent", DoubleType()),
    "GatewayMEMKb": ("GatewayMEMKb", LongType()),
}


def parse_csv_schema_adaptive(
    raw_csv: str,
    known_cols: dict,
    source_file: str,
    log_type: str,
    host: str,
    collected_at: str,
):
    """
    Parse a raw CSV string using column-NAME-based matching (not positional).
    This is DIFFERENTIATOR #5: schema-adaptive parsing.

    Design:
      - First row = headers. Map each header to known_cols dict.
      - Known columns: cast to correct type.
      - Unknown columns: collect into _extra_cols MAP<STRING, STRING>.
      - Missing known columns: fill with None (null).
      - Result: a list of dicts suitable for spark.createDataFrame().

    Why this matters:
      The Microsoft PBIT template uses positional CSV parsing in Power Query.
      When the gateway team adds a new column between existing columns (confirmed
      in community reports post-upgrade), the positional index shifts and
      Power Query raises DataFormat.Error: "more columns in result than expected."
      Column-name-based parsing is immune to this failure mode.
    """
    # FIX (Phase 5 hardening): use a proper RFC-4180 CSV reader instead of a
    # naive line.split(","). ErrorMessage (and QueryText/EvaluationContext) can
    # contain commas, quotes, and embedded newlines; a naive split silently
    # corrupts every row where that happens -- and ErrorMessage is central to
    # Differentiator #2 (triage), so this bug would poison the highest-value join.
    # Python's csv module handles quoted fields, escaped quotes (""), and
    # embedded commas/newlines correctly. We still parse by column NAME (not
    # position), preserving schema-adaptivity (Differentiator #5).
    reader = csv.reader(io.StringIO(raw_csv))
    try:
        all_rows = list(reader)
    except csv.Error:
        # Never raise: fall back to line-based read so one bad file can't halt ingest
        all_rows = [ln.split(",") for ln in raw_csv.strip().splitlines()]

    if len(all_rows) < 2:
        return []  # Empty or header-only file

    headers = [h.strip().strip('"') for h in all_rows[0]]

    # Build a mapping: csv_header_index -> (delta_col_name, type_obj) or None
    col_map = {}  # index -> (delta_name, type) for known cols
    for i, h in enumerate(headers):
        if h in known_cols:
            col_map[i] = known_cols[h]
        # else: will go to _extra_cols

    records = []
    for values in all_rows[1:]:
        if not values or (len(values) == 1 and not values[0].strip()):
            continue
        # csv.reader already stripped the outer quotes and un-escaped "";
        # trim residual whitespace only.
        values = [v.strip() if v is not None else v for v in values]

        row = {}
        extra_cols = {}
        cast_errors = []  # U14: per-row quarantine of unparseable values

        for i, val in enumerate(values):
            if i >= len(headers):
                # More values than headers — capture as overflow
                extra_cols[f"_overflow_{i}"] = val
                continue

            header = headers[i]
            if i in col_map:
                delta_name, col_type = col_map[i]
                # U14: distinguish a genuine null from an UNPARSEABLE value. A bad
                # numeric (e.g. "N/A" in a (ms) column) must not be silently coerced
                # to null with no trace — record it in _cast_errors so data-quality
                # issues are visible in the bronze table, not masked.
                casted, cast_ok = _cast_value(val, col_type)
                row[delta_name] = casted
                if not cast_ok:
                    cast_errors.append(
                        {"column": delta_name, "raw_value": val,
                         "target_type": type(col_type).__name__}
                    )
            else:
                # Unknown column — preserve in _extra_cols
                extra_cols[header] = val

        # Fill missing known columns with None
        for csv_name, (delta_name, _) in known_cols.items():
            if delta_name not in row:
                row[delta_name] = None

        row["_extra_cols"] = extra_cols if extra_cols else {}
        # U14: sidecar of quarantined cast failures (empty list when the row is clean).
        row["_cast_errors"] = cast_errors
        row["_source_file"] = source_file
        row["_log_type"] = log_type
        row["_gateway_host"] = host
        row["_ingested_at"] = collected_at

        records.append(row)

    return records


def _cast_value(val: str, col_type):
    """Best-effort type casting. Never raises.

    U14: returns a (value, cast_ok) tuple so the caller can distinguish a genuine
    null/empty (cast_ok=True, value=None) from an UNPARSEABLE value that was coerced
    to null (cast_ok=False). The latter is quarantined into the row's _cast_errors
    so silent data-quality loss becomes visible.
    """
    if (
        val is None
        or val.strip() == ""
        or val.strip().lower() in ("null", "nan", "none")
    ):
        return None, True  # legitimate null — not an error
    try:
        if isinstance(col_type, LongType):
            return int(float(val)), True  # float() first handles "0.0" strings
        elif isinstance(col_type, DoubleType):
            return float(val), True
        elif isinstance(col_type, BooleanType):
            return val.strip().lower() in ("true", "1", "yes"), True
        elif isinstance(col_type, TimestampType):
            # Gateway timestamps are UTC ISO8601 or "YYYY-MM-DD HH:MM:SS"
            # Return as string; Spark timestamp cast handles both formats
            return val, True
        else:
            return val, True
    except (ValueError, TypeError):
        # Unparseable for the target type — coerce to null but FLAG it (U14).
        return None, False


def parse_evaluation_context(df):
    """
    Parse the EvaluationContext JSON column from QueryStart logs.
    EvaluationContext contains artifactId (datasetId/dataflowId) for
    Fabric workloads only.

    [Unverified] EvaluationContext JSON structure confirmed from MS docs
    and 3Cloud blog analysis:
      https://learn.microsoft.com/en-us/data-integration/gateway/service-gateway-performance
      https://3cloudsolutions.com/resources/monitoring-power-bi-on-premises-data-gateway-performance/

    LIMITATION: Only populated for Fabric workloads.
    NOT populated for: Dataflow Gen1, Paginated Reports, or Power BI
    datasets on shared capacity. These rows will have null artifact_id.
    """

    # FIX (Phase 5 hardening): EvaluationContext may be EITHER direct JSON OR
    # base64-encoded JSON (3Cloud community analysis indicates base64; MS docs
    # confirm the field but not its encoding). The previous version called
    # get_json_object directly, which returns NULL for every row if the value is
    # base64 -- silently zeroing out Differentiator #3 (identity attribution).
    # We now normalize first: if the raw value doesn't parse as JSON but does
    # base64-decode into JSON, use the decoded form. A UDF auto-detects per-row
    # so the pipeline works regardless of which encoding this gateway version emits.
    def _normalize_eval_context(raw):
        if raw is None:
            return None
        s = raw.strip()
        if not s:
            return None
        # 1) Already JSON?
        if s.startswith("{"):
            return s
        # 2) Try base64 -> JSON
        try:
            decoded = base64.b64decode(s, validate=True).decode(
                "utf-8", errors="strict"
            )
            if decoded.strip().startswith("{"):
                return decoded
        except (binascii.Error, ValueError, UnicodeDecodeError):
            pass
        # 3) Unknown format -- return raw so get_json_object yields null, but we
        #    keep the original for debugging in _extra/inspection.
        return s

    normalize_udf = F.udf(_normalize_eval_context, StringType())
    df = df.withColumn("_eval_context_json", normalize_udf(F.col("EvaluationContext")))

    df = df.withColumn(
        "artifact_id",
        F.when(
            F.col("_eval_context_json").isNotNull(),
            F.get_json_object(F.col("_eval_context_json"), "$.artifactId"),
        ).otherwise(F.lit(None).cast(StringType())),
    ).withColumn(
        "artifact_type",
        F.when(
            F.col("_eval_context_json").isNotNull(),
            F.get_json_object(F.col("_eval_context_json"), "$.artifactKind"),
        ).otherwise(F.lit(None).cast(StringType())),
    )
    # [Phase 5] Confirm the actual JSON key names ($.artifactId / $.artifactKind).
    # MS docs confirm the field carries artifactId for Fabric workloads; exact
    # key casing/nesting is [Unverified] until inspected against real logs.
    return df


def add_partition_date(df, ts_col: str):
    """Add _partition_date from a timestamp column for Delta partitioning."""
    return df.withColumn("_partition_date", F.to_date(F.col(ts_col)))


# ── MAIN INGEST LOGIC ────────────────────────────────────────────────────────


def ingest_gateway_logs_via_lib():
    """PRIMARY gateway-log path (U19): read the raw gateway CSVs directly with
    gateway_bronze_lib.read_gateway_csv -- native distributed Spark, column-NAME
    based (positional-drift immune, pain #4) AND _sanitize_columns-protected so
    new `(ms)`/`(bytes)` headers can't break the Delta write with
    InvalidColumnName (the live-tenant failure, LIVE-TENANT-FINDINGS.md).

    Reads QueryExecutionReport / QueryStartReport CSVs staged in LANDING_PATH.
    Returns True if it handled ingest, False to signal the caller to fall back
    to the JSON-staged driver-side parser.
    """
    if not _BRONZE_LIB_AVAILABLE:
        return False

    handled_any = False

    # QueryExecution: the table the identity join + triage depend on.
    try:
        qe = read_gateway_csv(
            spark,
            f"{LANDING_PATH}/QueryExecutionReport*.csv",
            rename_map=QUERY_EXECUTION_RENAME,
        )
        if not qe.rdd.isEmpty():
            qe = cast_query_execution(qe)
            if "QueryExecutionEndTimeUTC" in qe.columns:
                qe = add_partition_date(qe, "QueryExecutionEndTimeUTC")
                _write_bronze_delta(
                    qe, "bronze_query_execution", partition_cols=["_partition_date"]
                )
            else:
                _write_bronze_delta(qe, "bronze_query_execution", partition_cols=[])
            handled_any = True
    except Exception as e:  # noqa: BLE001 -- never let one file form halt ingest
        print(f"[INFO] lib path: no readable QueryExecution CSVs ({e})")

    # QueryStart: carries EvaluationContext -> artifact identity fallback.
    try:
        qs = read_gateway_csv(spark, f"{LANDING_PATH}/QueryStartReport*.csv")
        if not qs.rdd.isEmpty():
            if "QueryStartTimeUTC" in qs.columns:
                qs = qs.withColumn(
                    "QueryStartTimeUTC", F.to_timestamp(F.col("QueryStartTimeUTC"))
                )
            qs = add_artifact_identity(qs)  # UDF-free, both encodings
            if "QueryStartTimeUTC" in qs.columns:
                qs = add_partition_date(qs, "QueryStartTimeUTC")
                _write_bronze_delta(
                    qs, "bronze_query_start", partition_cols=["_partition_date"]
                )
            else:
                _write_bronze_delta(qs, "bronze_query_start", partition_cols=[])
            handled_any = True
    except Exception as e:  # noqa: BLE001
        print(f"[INFO] lib path: no readable QueryStart CSVs ({e})")

    return handled_any


def ingest_gateway_logs():
    """
    Gateway log ingest. PRIMARY path is the shared lib (ingest_gateway_logs_via_lib,
    reading raw CSVs). This function is the FALLBACK for the JSON-staged form
    (Collect-GatewayLogs.ps1 wraps CSV text in RawCsvContent) and for dialects the
    Spark reader mishandles; it uses the driver-side column-NAME parser.
    """
    # Try the tested, sanitizing lib path first (raw CSVs).
    if ingest_gateway_logs_via_lib():
        print(
            "[01_bronze_ingest] gateway logs ingested via gateway_bronze_lib "
            "(schema-adaptive + column-name-sanitized). Skipping driver-side fallback."
        )
        return

    print(f"[01_bronze_ingest] lib path found no raw CSVs; using JSON-staged fallback from: {LANDING_PATH}")

    # Read all staged gateway log JSON files
    # [Assumption] Fabric Lakehouse Files/ is accessible via ABFSS path
    try:
        # multiLine=True: collectors write pretty-printed ConvertTo-Json (a single
        # multi-line root object), NOT JSONL. Spark's default (multiLine=false) reads
        # line-by-line and would corrupt a pretty object into _corrupt_record.
        landing_df = spark.read.option("multiLine", True).json(
            f"{LANDING_PATH}/gateway_logs_*.json"
        )
    except Exception as e:
        print(f"[WARN] No gateway log staging files found or read error: {e}")
        return

    if landing_df.rdd.isEmpty():
        print("[INFO] No new gateway log records to ingest")
        return

    rows = landing_df.collect()
    print(f"[INFO] Found {len(rows)} staged file records")

    # Separate by log type
    qe_records = []  # QueryExecution
    qs_records = []  # QueryStart
    sc_records = []  # SystemCounter
    agg_records = []  # QueryAggregation (simplified for now)

    for row in rows:
        raw_csv = row["RawCsvContent"]
        log_type = row["LogType"]
        source_file = row["SourceFileName"]
        host = row["GatewayHostName"]
        collected = row["CollectedAtUtc"]

        if log_type == "QueryExecution":
            parsed = parse_csv_schema_adaptive(
                raw_csv,
                QUERY_EXECUTION_KNOWN_COLS,
                source_file,
                log_type,
                host,
                collected,
            )
            qe_records.extend(parsed)
        elif log_type == "QueryStart":
            parsed = parse_csv_schema_adaptive(
                raw_csv, QUERY_START_KNOWN_COLS, source_file, log_type, host, collected
            )
            qs_records.extend(parsed)
        elif log_type == "SystemCounter":
            parsed = parse_csv_schema_adaptive(
                raw_csv,
                SYSTEM_COUNTER_KNOWN_COLS,
                source_file,
                log_type,
                host,
                collected,
            )
            sc_records.extend(parsed)
        elif log_type == "QueryAggregation":
            agg_records.extend(
                [{"_raw": raw_csv, "_source_file": source_file, "_host": host}]
            )

    print(
        f"[INFO] Parsed: QE={len(qe_records)}, QS={len(qs_records)}, SC={len(sc_records)}, Agg={len(agg_records)}"
    )

    # ── Write QueryExecution Bronze Delta ──────────────────────────────────
    if qe_records:
        qe_df = spark.createDataFrame(qe_records)
        # Cast timestamp columns explicitly (Spark accepts ISO8601 strings)
        for ts_col in ["QueryExecutionEndTimeUTC", "DataProcessingEndTimeUTC"]:
            if ts_col in qe_df.columns:
                qe_df = qe_df.withColumn(ts_col, F.to_timestamp(F.col(ts_col)))
        qe_df = add_partition_date(qe_df, "QueryExecutionEndTimeUTC")

        _write_bronze_delta(
            qe_df, "bronze_query_execution", partition_cols=["_partition_date"]
        )
        # Emit schema warning if any expected columns were missing
        _emit_schema_warnings(qe_records, QUERY_EXECUTION_KNOWN_COLS, "QueryExecution")

    # ── Write QueryStart Bronze Delta ──────────────────────────────────────
    if qs_records:
        qs_df = spark.createDataFrame(qs_records)
        if "QueryStartTimeUTC" in qs_df.columns:
            qs_df = qs_df.withColumn(
                "QueryStartTimeUTC", F.to_timestamp(F.col("QueryStartTimeUTC"))
            )
        qs_df = parse_evaluation_context(qs_df)
        qs_df = add_partition_date(qs_df, "QueryStartTimeUTC")
        _write_bronze_delta(
            qs_df, "bronze_query_start", partition_cols=["_partition_date"]
        )

    # ── Write SystemCounter Bronze Delta ───────────────────────────────────
    if sc_records:
        sc_df = spark.createDataFrame(sc_records)
        if "CounterTimeUTC" in sc_df.columns:
            sc_df = sc_df.withColumn(
                "CounterTimeUTC", F.to_timestamp(F.col("CounterTimeUTC"))
            )
        sc_df = add_partition_date(sc_df, "CounterTimeUTC")
        _write_bronze_delta(
            sc_df, "bronze_system_counter", partition_cols=["_partition_date"]
        )


def _write_bronze_delta(df, table_name: str, partition_cols: list):
    """
    Write DataFrame to Bronze Delta table (append mode).
    Uses merge-on-schema to handle new columns gracefully.
    [Unverified] mergeSchema=true behavior in Fabric Delta requires Phase 5 validation.
    """
    table_path = f"{BRONZE_PATH}/{table_name}"
    (
        df.write.format(_TABLE_FORMAT)
        .mode("append")
        .option("mergeSchema", "true")  # Allow new columns without error
        .partitionBy(*partition_cols)
        .save(table_path)
    )
    count = df.count()
    print(f"[INFO] Wrote {count} rows to {table_name} (path: {table_path})")


def _emit_schema_warnings(records: list, known_cols: dict, log_type: str):
    """
    Check if any expected column was absent from ALL parsed records.
    If a known column is consistently missing, emit a warning.
    This may indicate the gateway version renamed or removed a column.
    The warning is captured in a Delta table for Activator alerting.
    """
    if not records:
        return
    all_cols = set(records[0].keys()) - {
        "_extra_cols",
        "_source_file",
        "_log_type",
        "_gateway_host",
        "_ingested_at",
    }
    expected_delta_names = {v[0] for v in known_cols.values()}
    missing = expected_delta_names - all_cols
    if missing:
        print(
            f"[SCHEMA_WARN] {log_type}: Expected columns not found in logs: {missing}"
        )
        print(
            f"[SCHEMA_WARN] These may have been renamed/removed in current gateway version."
        )
        print(
            f"[SCHEMA_WARN] Validate against: https://learn.microsoft.com/en-us/data-integration/gateway/service-gateway-performance"
        )
        # TODO (Phase 5): write schema_warn rows to a bronze_schema_warnings Delta table
        # for monitoring in the report and Activator alerting


def ingest_network_metrics():
    """Ingest network metrics JSON from Collect-NetworkMetrics.ps1."""
    try:
        df = spark.read.option("multiLine", True).json(
            f"{LANDING_PATH}/network_metrics_*.json"
        )
    except Exception as e:
        print(f"[WARN] No network metrics files: {e}")
        return

    # Explode NicMetrics array into rows
    df_exploded = (
        df.select(
            F.col("CollectedAtUtc").cast(TimestampType()).alias("CollectedAtUTC"),
            F.col("GatewayHostName"),
            F.explode_outer(F.col("NicMetrics")).alias("nic"),
            F.col("LatencyProbe"),
        )
        .select(
            "CollectedAtUTC",
            "GatewayHostName",
            F.col("nic.NicName").alias("NicName"),
            F.col("nic.BytesTotalPerSec_avg")
            .cast(DoubleType())
            .alias("BytesTotalPerSec"),
            F.col("nic.CurrentBandwidthBps")
            .cast(DoubleType())
            .alias("CurrentBandwidthBps"),
            F.col("nic.UtilizationPct").cast(DoubleType()).alias("UtilizationPct"),
            F.col("LatencyProbe.LatencyMs_avg")
            .cast(DoubleType())
            .alias("LatencyMs_PBIRelay"),
            F.col("LatencyProbe.TargetHost").alias("LatencyProbeTarget"),
        )
        .withColumn("_partition_date", F.to_date(F.col("CollectedAtUTC")))
    )
    _write_bronze_delta(
        df_exploded, "bronze_network_metrics", partition_cols=["_partition_date"]
    )


def ingest_event_log():
    """Ingest Windows Event Log JSON from Collect-EventLog.ps1."""
    try:
        df = spark.read.option("multiLine", True).json(
            f"{LANDING_PATH}/event_log_*.json"
        )
    except Exception as e:
        print(f"[WARN] No event log files: {e}")
        return

    df_exploded = (
        df.select(
            F.col("CollectedAtUtc").cast(TimestampType()).alias("_ingested_at"),
            F.col("GatewayHostName"),
            F.explode_outer(F.col("Events")).alias("evt"),
        )
        .select(
            "_ingested_at",
            "GatewayHostName",
            F.col("evt.TimeCreated").cast(TimestampType()).alias("TimeCreated"),
            F.col("evt.EventId").cast("int").alias("EventId"),
            F.col("evt.LevelDisplayName").alias("LevelDisplayName"),
            F.col("evt.ProviderName").alias("ProviderName"),
            F.col("evt.LogName").alias("LogName"),
            F.col("evt.Message").alias("Message"),
            F.col("evt.EventSource").alias("EventSource"),
        )
        .withColumn("_partition_date", F.to_date(F.col("TimeCreated")))
    )
    _write_bronze_delta(
        df_exploded, "bronze_event_log", partition_cols=["_partition_date"]
    )


def ingest_disk_spool():
    """Ingest disk spool JSON from Collect-DiskSpool.ps1."""
    try:
        df = spark.read.option("multiLine", True).json(
            f"{LANDING_PATH}/disk_spool_*.json"
        )
    except Exception as e:
        print(f"[WARN] No disk spool files: {e}")
        return

    df_clean = df.select(
        F.col("CollectedAtUtc").cast(TimestampType()).alias("CollectedAtUTC"),
        F.col("GatewayHostName"),
        F.col("DiskInfo.DriveLetter").alias("SpoolDriveLetter"),
        F.col("DiskInfo.FreeSpaceBytes").cast(LongType()).alias("FreeSpaceBytes"),
        F.col("DiskInfo.TotalSpaceBytes").cast(LongType()).alias("TotalSpaceBytes"),
        F.col("DiskInfo.FreeSpacePct").cast(DoubleType()).alias("FreeSpacePct"),
        F.col("SpoolDirSizeBytes").cast(LongType()).alias("SpoolDirSizeBytes"),
        F.col("AlertLevel"),
        F.col("AlertMessage"),
        F.col("StreamBeforeRequestCompletes_Warning").cast(BooleanType()),
    ).withColumn("_partition_date", F.to_date(F.col("CollectedAtUTC")))
    _write_bronze_delta(
        df_clean, "bronze_disk_spool", partition_cols=["_partition_date"]
    )


def ingest_gateway_inventory():
    """Ingest gateway inventory JSON from Get-GatewayInventory.ps1."""
    try:
        df = spark.read.option("multiLine", True).json(
            f"{LANDING_PATH}/gateway_inventory_*.json"
        )
    except Exception as e:
        print(f"[WARN] No gateway inventory files: {e}")
        return

    df_inv = (
        df.select(
            F.col("CollectedAtUtc").cast(TimestampType()).alias("CollectedAtUTC"),
            F.explode_outer(F.col("Inventory")).alias("node"),
        )
        .select(
            "CollectedAtUTC",
            F.col("node.GatewayClusterId"),
            F.col("node.GatewayClusterName"),
            F.col("node.GatewayObjectId"),
            F.col("node.GatewayNodeName"),
            F.col("node.Status"),
            F.col("node.Version"),
            F.col("node.DatasourceCount").cast("int"),
            F.col("node.ClusterScope"),
        )
        .withColumn("_partition_date", F.to_date(F.col("CollectedAtUTC")))
    )
    _write_bronze_delta(
        df_inv, "bronze_gateway_inventory", partition_cols=["_partition_date"]
    )


def ingest_gateway_datasources():
    """Ingest the per-datasource records from Get-GatewayInventory.ps1 (the
    ``Datasources`` array) into bronze_gateway_datasources -- the credential-drift
    signal (Pain #10).

    The inventory JSON carries two arrays: ``Inventory`` (nodes, handled by
    ingest_gateway_inventory) and ``Datasources`` (per-datasource Status/type).
    The node path keeps only DatasourceCount; this surfaces the individual
    datasource Status values that credential-drift alerting keys on. The array is
    only present when the collector ran with -CheckDatasourceStatus (default on).
    """
    try:
        df = spark.read.option("multiLine", True).json(
            f"{LANDING_PATH}/gateway_inventory_*.json"
        )
    except Exception as e:
        print(f"[WARN] No gateway inventory files: {e}")
        return

    if "Datasources" not in df.columns:
        print(
            "[WARN] gateway inventory has no Datasources array "
            "(CheckDatasourceStatus off?) -- skipping bronze_gateway_datasources"
        )
        return

    df_ds = (
        df.select(
            F.col("CollectedAtUtc").cast(TimestampType()).alias("CollectedAtUTC"),
            F.explode_outer(F.col("Datasources")).alias("ds"),
        )
        .select(
            "CollectedAtUTC",
            F.col("ds.GatewayClusterId"),
            F.col("ds.GatewayClusterName"),
            F.col("ds.DatasourceId"),
            F.col("ds.DatasourceName"),
            F.col("ds.DatasourceType"),
            F.col("ds.ConnectionDetails"),  # JSON string (ConvertTo-Json -Compress)
            F.col("ds.Status"),  # Live / Unknown / Error -- drives cred-drift alert
            F.col("ds.LastUpdated"),
        )
        # explode_outer emits a null ds for inventories with an empty array; drop them
        .filter(F.col("DatasourceId").isNotNull())
        .withColumn("_partition_date", F.to_date(F.col("CollectedAtUTC")))
    )
    _write_bronze_delta(
        df_ds, "bronze_gateway_datasources", partition_cols=["_partition_date"]
    )


def ingest_refresh_history():
    """Ingest Power BI Service refresh history JSON from Collect-RefreshHistory.ps1.

    Produces bronze_refresh_history — the Service-side leg of the Differentiator #2
    triage join (02_silver_correlate reads exactly these columns). Without this the
    silver triage falls back to gateway-log + event-log only and cannot reconcile
    against the refresh error the operator actually sees.
    """
    try:
        df = spark.read.option("multiLine", True).json(
            f"{LANDING_PATH}/refresh_history_*.json"
        )
    except Exception as e:
        print(f"[WARN] No refresh history files: {e}")
        return

    df_exploded = (
        df.select(
            F.col("CollectedAtUtc").cast(TimestampType()).alias("_ingested_at"),
            F.explode_outer(F.col("RefreshRecords")).alias("r"),
        )
        .select(
            "_ingested_at",
            # RequestId: the join key back to the gateway log RequestId.
            F.col("r.RequestId").alias("RequestId"),
            F.col("r.DatasetId").alias("DatasetId"),
            F.col("r.DatasetName").alias("DatasetName"),
            F.col("r.WorkspaceId").alias("WorkspaceId"),
            F.col("r.RefreshType").alias("RefreshType"),
            F.col("r.Status").alias("Status"),
            F.col("r.StartTime").cast(TimestampType()).alias("StartTime"),
            F.col("r.EndTime").cast(TimestampType()).alias("EndTime"),
            F.col("r.ServiceExceptionJson").alias("ServiceExceptionJson"),
        )
        # Drop rows with no RequestId — they cannot join to a gateway query.
        .filter(F.col("RequestId").isNotNull())
        .withColumn("_partition_date", F.to_date(F.col("EndTime")))
    )
    _write_bronze_delta(
        df_exploded, "bronze_refresh_history", partition_cols=["_partition_date"]
    )


def ingest_mashup_processes():
    """Ingest per-process mashup telemetry (NDJSON) from Collect-MashupProcesses.ps1
    into bronze_mashup_processes -- per-container memory/CPU visibility (Pain #5).

    Unlike the other collectors, the mashup collector emits newline-delimited JSON
    (one flat record per line, no wrapper), so this reads with multiLine=False and
    does NOT explode. Column casts mirror gateway_bronze_lib.read_mashup_processes
    (the tested reference exercised by run_local_smoke STEP 6).
    """
    try:
        df = spark.read.option("multiLine", False).json(
            f"{LANDING_PATH}/MashupProcesses_*.json"
        )
    except Exception as e:
        print(f"[WARN] No mashup process files: {e}")
        return

    for c, t in [
        ("ProcessId", "long"),
        ("WorkingSetMB", "double"),
        ("PrivateBytesMB", "double"),
        ("CpuPercent", "double"),
        ("ThreadCount", "int"),
        ("HandleCount", "int"),
        ("LogicalCores", "int"),
    ]:
        if c in df.columns:
            df = df.withColumn(c, F.col(c).cast(t))

    df_mashup = df.withColumn(
        "CollectedAtUTC", F.col("CollectedAtUtc").cast(TimestampType())
    ).withColumn("_partition_date", F.to_date(F.col("CollectedAtUTC")))
    _write_bronze_delta(
        df_mashup, "bronze_mashup_processes", partition_cols=["_partition_date"]
    )


# ── OPTIONAL FPM BRIDGE ───────────────────────────────────────────────────────
def ingest_fpm_bridge():
    """
    If USE_FPM_BRIDGE = True, read FPM Eventhouse tables exposed as Delta
    via Eventhouse OneLake availability and write to our bronze tables.
    Reference: https://learn.microsoft.com/en-us/fabric/real-time-intelligence/event-house-onelake-availability
    [Assumption] Eventhouse OneLake availability is enabled; Delta path is configured.
    [Unverified] FPM table schemas (GatewaysHeartbeat, GatewayReports-Raw) may differ
                 from the column names we expect. Validate in Phase 5.
    """
    if not USE_FPM_BRIDGE or not FPM_EVENTHOUSE_DELTA_PATH:
        return

    print("[INFO] FPM bridge mode: reading from Eventhouse Delta tables")
    # TODO (Phase 5): implement FPM bridge column mapping
    # Tables to bridge: GatewaysHeartbeat, GatewayReports-Raw, SystemCounters, QueryConnections
    # Map FPM column names -> our bronze schema column names
    # Key difference: FPM uses Eventhouse ingestion time column "_timestamp"; our schema uses QueryExecutionEndTimeUTC
    print(
        "[STUB] FPM bridge: column mapping not yet implemented. See phase4_architecture.md §1.2"
    )


# ── ENTRY POINT ──────────────────────────────────────────────────────────────
# Auto-runs in a Fabric notebook (where __name__ is not "__main__"). Set
# GATEWAYMON_AUTORUN=0 to suppress the auto-run when importing this module (tests).
if __name__ == "__main__" or os.environ.get("GATEWAYMON_AUTORUN", "1") != "0":
    print(
        f"=== 01_bronze_ingest.py starting at {datetime.now(timezone.utc).isoformat()} ==="
    )
    ingest_gateway_logs()
    ingest_network_metrics()
    ingest_event_log()
    ingest_disk_spool()
    ingest_gateway_inventory()
    ingest_gateway_datasources()
    ingest_refresh_history()
    ingest_mashup_processes()
    if USE_FPM_BRIDGE:
        ingest_fpm_bridge()
    print("=== 01_bronze_ingest.py complete ===")
