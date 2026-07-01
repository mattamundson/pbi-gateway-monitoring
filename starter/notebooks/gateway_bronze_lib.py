# =============================================================================
# gateway_bronze_lib.py  |  Label: [NET-NEW]
#
# Shared bronze-ingest logic used by BOTH the Fabric notebook (01_bronze_ingest.py)
# and the PySpark test (tests/test_parser_spark.py). Single source of truth --
# fixes the earlier "keep parser copies in sync manually" weakness.
#
# PRIMARY PATH: native Spark distributed CSV reader (spark.read.csv) in PERMISSIVE
# mode. This is RFC-4180-correct (handles commas/quotes/multiline natively),
# distributed (parses on executors, not the driver), and schema-adaptive:
#   - unknown/new columns flow through (mergeSchema on write) -> survives pain #4
#   - malformed rows captured in _corrupt_record instead of failing the job
#
# The driver-side Python parser (parse_csv_rows) is retained as a documented
# FALLBACK for non-standard dialects and as the portable (Spark-free) test target.
#
# Column names sourced from MS Learn (authoritative):
#   https://learn.microsoft.com/en-us/data-integration/gateway/service-gateway-performance
# =============================================================================
import base64
import binascii
import csv
import io

from pyspark.sql import functions as F
from pyspark.sql.types import StringType

# ---- canonical CSV-header -> normalized Delta column name --------------------
QUERY_EXECUTION_RENAME = {
    "QueryExecutionDuration(ms)": "QueryExecutionDuration",
    "DataProcessingDuration(ms)": "DataProcessingDuration",
    "SpoolingDiskWritingDuration(ms)": "SpoolingDiskWritingDuration",
    "SpoolingDiskReadingDuration(ms)": "SpoolingDiskReadingDuration",
    "SpoolingTotalDataSize(bytes)": "SpoolingTotalDataSize",
    "DataReadingAndSerializationDuration(ms)": "DataReadingAndSerializationDuration",
    "DiskRead(byte/sec)": "DiskRead",
    "DiskWrite(byte/sec)": "DiskWrite",
}

QE_NUMERIC_LONG = [
    "QueryExecutionDuration", "DataProcessingDuration", "SpoolingDiskWritingDuration",
    "SpoolingDiskReadingDuration", "SpoolingTotalDataSize", "DataReadingAndSerializationDuration",
]
QE_NUMERIC_DOUBLE = ["DiskRead", "DiskWrite"]
QE_TIMESTAMPS = ["QueryExecutionEndTimeUTC", "DataProcessingEndTimeUTC"]


def read_gateway_csv(spark, path, rename_map=None):
    """
    PRIMARY PATH — native distributed Spark CSV read.

    - header=True: column-NAME based (positional drift immune -> pain #4)
    - mode=PERMISSIVE + columnNameOfCorruptRecord: malformed rows captured,
      never fail the job
    - multiLine + escape='"': RFC-4180 quoted commas / embedded quotes handled
    - inferSchema=False: read as strings, cast explicitly downstream (safe)
    """
    df = (
        spark.read
        .option("header", True)
        .option("inferSchema", False)
        .option("multiLine", True)
        .option("quote", '"')
        .option("escape", '"')
        .option("mode", "PERMISSIVE")
        .option("columnNameOfCorruptRecord", "_corrupt_record")
        .csv(path)
    )
    if rename_map:
        for src, dst in rename_map.items():
            if src in df.columns:
                df = df.withColumnRenamed(src, dst)
    return df


def cast_query_execution(df):
    """Explicit, null-safe casts. Unparseable -> null (never raises)."""
    for c in QE_NUMERIC_LONG:
        if c in df.columns:
            df = df.withColumn(c, F.col(c).cast("long"))
    for c in QE_NUMERIC_DOUBLE:
        if c in df.columns:
            df = df.withColumn(c, F.col(c).cast("double"))
    for c in QE_TIMESTAMPS:
        if c in df.columns:
            df = df.withColumn(c, F.to_timestamp(F.col(c)))
    if "Success" in df.columns:
        df = df.withColumn("Success", F.lower(F.col("Success")).isin("true", "1", "yes"))
    return df


# ---- EvaluationContext normalizer (direct-JSON OR base64) --------------------
# Implemented with NATIVE Spark SQL functions only (no Python UDF): faster (runs
# in the JVM, not a Python worker) and avoids UDF serialization pitfalls. We trim,
# detect direct JSON by a leading '{', else base64-decode via unbase64/decode,
# then extract artifact fields with get_json_object.
def add_artifact_identity(df, ctx_col="EvaluationContext"):
    """Extract datasetId/kind from EvaluationContext (handles both encodings), UDF-free."""
    if ctx_col not in df.columns:
        return df
    trimmed = F.trim(F.col(ctx_col))
    # base64 -> utf8 string (invalid base64 yields garbage, guarded by the '{' check below)
    decoded_b64 = F.decode(F.unbase64(trimmed), "UTF-8")
    normalized = (
        F.when(trimmed.isNull() | (trimmed == ""), F.lit(None).cast(StringType()))
         .when(trimmed.startswith("{"), trimmed)                        # already JSON
         .when(F.trim(decoded_b64).startswith("{"), decoded_b64)        # base64 JSON
         .otherwise(F.lit(None).cast(StringType()))                    # unknown -> null
    )
    df = df.withColumn("_eval_context_json", normalized)
    df = (
        df.withColumn("artifact_id", F.get_json_object("_eval_context_json", "$.artifactId"))
          .withColumn("artifact_kind", F.get_json_object("_eval_context_json", "$.artifactKind"))
    )
    return df


# ---- Portable (Spark-free) EvaluationContext normalizer ----------------------
# Retained for the Spark-free portable test and non-Spark callers. Mirrors the
# native-SQL logic above.
def normalize_eval_context_py(raw):
    if raw is None:
        return None
    s = raw.strip()
    if not s:
        return None
    if s.startswith("{"):
        return s
    try:
        decoded = base64.b64decode(s, validate=True).decode("utf-8", "strict")
        if decoded.strip().startswith("{"):
            return decoded
    except (binascii.Error, ValueError, UnicodeDecodeError):
        pass
    return s


# ---- FALLBACK: driver-side Python parser (portable / non-standard dialects) --
def parse_csv_rows(raw_csv, known_cols):
    """
    Spark-free RFC-4180 parser. Column-name based; unknown -> _extra_cols.
    Retained as (a) the portable test target and (b) a fallback for dialects the
    Spark reader mishandles. NOT the primary production path.
    """
    try:
        rows = list(csv.reader(io.StringIO(raw_csv)))
    except csv.Error:
        rows = [ln.split(",") for ln in raw_csv.strip().splitlines()]
    if len(rows) < 2:
        return []
    headers = [h.strip().strip('"') for h in rows[0]]
    out = []
    for values in rows[1:]:
        if not values or (len(values) == 1 and not values[0].strip()):
            continue
        rec, extra = {}, {}
        for i, val in enumerate(values):
            if i >= len(headers):
                extra[f"_overflow_{i}"] = val
                continue
            h = headers[i]
            (rec if h in known_cols else extra)[h] = val
        for h in known_cols:
            rec.setdefault(h, None)
        rec["_extra_cols"] = extra
        out.append(rec)
    return out


# ---- Mashup process telemetry (PAIN #5) --------------------------------------
# Reads newline-delimited JSON emitted by collectors/Collect-MashupProcesses.ps1
# and aggregates per-process memory/CPU so operators can see WHICH mashup
# container is bloating — the visibility no existing tool provides. [NET-NEW]
def read_mashup_processes(spark, path):
    """Read NDJSON mashup-process samples into a typed DataFrame."""
    df = spark.read.option("multiLine", False).json(path)
    for c, t in [("WorkingSetMB", "double"), ("PrivateBytesMB", "double"),
                 ("CpuPercent", "double"), ("ThreadCount", "int"),
                 ("HandleCount", "int"), ("ProcessId", "long"), ("LogicalCores", "int")]:
        if c in df.columns:
            df = df.withColumn(c, F.col(c).cast(t))
    if "CollectedAtUtc" in df.columns:
        df = df.withColumn("CollectedAtUtc", F.to_timestamp("CollectedAtUtc"))
    return df


def gold_mashup_health(df, runaway_working_set_mb=6000.0):
    """Per-gateway mashup rollup: peak/avg working set, container count, and a
    runaway flag when any container's working set exceeds the threshold."""
    agg = (df.groupBy("GatewayObjectId", "HostName")
             .agg(F.max("WorkingSetMB").alias("peak_working_set_mb"),
                  F.avg("WorkingSetMB").alias("avg_working_set_mb"),
                  F.max("CpuPercent").alias("peak_cpu_pct"),
                  F.countDistinct("ProcessId").alias("distinct_processes"),
                  F.sum(F.when(F.col("IsMashupContainer") == True, 1).otherwise(0)).alias("mashup_samples"))
             .withColumn("runaway_container",
                         F.col("peak_working_set_mb") > F.lit(runaway_working_set_mb)))
    return agg
