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
#   - unknown/new columns flow through (mergeSchema on write) -> survives pain #4,
#     but are first run through _sanitize_columns() so a new unit-suffixed header
#     (e.g. NewMetric(ms)) can't break the Delta write with InvalidColumnName --
#     a live-tenant-confirmed failure mode (2026-07-01, LIVE-TENANT-FINDINGS.md)
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
import re

# The Spark functions below need pyspark; the pure-Python helpers (parse_csv_rows,
# normalize_eval_context_py, cast_error_columns) do NOT. Guard the import so this
# module stays importable — and therefore the single source of truth for the
# Spark-free unit tests — on a host without pyspark installed. The Spark functions
# raise clearly if called without pyspark; they are simply never called Spark-free.
try:
    from pyspark.sql import functions as F
    from pyspark.sql.types import StringType
except ImportError:  # Spark-free host (e.g. the portable test runner / CI Tier 1)
    F = None
    StringType = None

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
    "QueryExecutionDuration",
    "DataProcessingDuration",
    "SpoolingDiskWritingDuration",
    "SpoolingDiskReadingDuration",
    "SpoolingTotalDataSize",
    "DataReadingAndSerializationDuration",
]
QE_NUMERIC_DOUBLE = ["DiskRead", "DiskWrite"]
QE_TIMESTAMPS = ["QueryExecutionEndTimeUTC", "DataProcessingEndTimeUTC"]


# ---- column-name sanitizer (pain #4 live-tenant fix, 2026-07-01) -------------
# Delta / Parquet (and Fabric's schema inference) reject these characters in a
# column identifier: space , ; { } ( ) newline tab = and '/'. Gateway exports use
# unit suffixes like "(ms)", "(bytes)", "(byte/sec)" that hit exactly this. Known
# columns are normalized by QUERY_EXECUTION_RENAME; UNKNOWN/new columns (the pain
# #4 gateway-upgrade case) are not, so without this a new "SomeMetric(ms)" header
# would break the bronze write with InvalidColumnName. See LIVE-TENANT-FINDINGS.md.
_ILLEGAL_COL_CHARS = re.compile(r"[ ,;{}()\n\t=/]+")


def _safe_col_name(name):
    """Pure-Python (Spark-free, unit-testable) column-name sanitizer.

    Collapses each run of Delta/Parquet-illegal characters to a single '_' and
    trims leading/trailing '_'. Info-preserving and collision-avoiding:
    'QueryExecutionDuration(ms)' -> 'QueryExecutionDuration_ms',
    'DiskRead(byte/sec)' -> 'DiskRead_byte_sec'. A name with no illegal chars is
    returned unchanged. If sanitizing would empty the name, the original is kept.
    """
    safe = _ILLEGAL_COL_CHARS.sub("_", name).strip("_")
    return safe if safe else name


def _sanitize_columns(df):
    """Rename any DataFrame column whose name contains Delta/Parquet-illegal
    characters to its `_safe_col_name`. No-op for already-safe names. Collisions
    (two raw names sanitizing to the same safe name) are disambiguated with a
    numeric suffix so no column is silently dropped."""
    used = set(df.columns)
    for c in list(df.columns):
        safe = _safe_col_name(c)
        if safe == c:
            continue
        if safe in used and safe != c:
            i = 2
            while f"{safe}_{i}" in used:
                i += 1
            safe = f"{safe}_{i}"
        df = df.withColumnRenamed(c, safe)
        used.discard(c)
        used.add(safe)
    return df


def read_gateway_csv(spark, path, rename_map=None):
    """
    PRIMARY PATH — native distributed Spark CSV read.

    - header=True: column-NAME based (positional drift immune -> pain #4)
    - mode=PERMISSIVE + columnNameOfCorruptRecord: malformed rows captured,
      never fail the job
    - multiLine + escape='"': RFC-4180 quoted commas / embedded quotes handled
    - inferSchema=False: read as strings, cast explicitly downstream (safe)
    - _sanitize_columns: after known-column renames, scrub any remaining
      illegal-char headers (unknown unit-suffixed columns) so the Delta write
      never fails with InvalidColumnName (pain #4 live-tenant fix).
    """
    df = (
        spark.read.option("header", True)
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
    # Scrub any remaining illegal-character headers (unknown/new columns).
    df = _sanitize_columns(df)
    return df


def cast_query_execution(df):
    """Explicit, null-safe casts. Unparseable -> null (never raises).

    U14: a raw value that is present but unparseable (e.g. "N/A" in a (ms) column)
    would otherwise be *silently* coerced to null, masking a data-quality problem.
    We keep the null-on-failure cast (so the job never fails) but ALSO surface every
    such drop in an additive `_cast_errors` array column listing the offending
    column names, so silent losses are visible. Existing cast values are unchanged;
    this only ADDS the error column (low blast radius). The detection rule is
    mirrored Spark-free in `cast_error_columns()` below and unit-tested there.
    """
    err_cols = []
    # Pass 1: detect cast failures from the ORIGINAL raw strings (before overwriting).
    for c, target in [(x, "long") for x in QE_NUMERIC_LONG] + [
        (x, "double") for x in QE_NUMERIC_DOUBLE
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
    # Pass 2: apply the casts.
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
        df = df.withColumn(
            "Success", F.lower(F.col("Success")).isin("true", "1", "yes")
        )
    # Fold the per-column markers into one array<string>, dropping the null (no-error) slots.
    if err_cols:
        arr = F.array(*[F.col(x) for x in err_cols])
        df = df.withColumn("_cast_errors", F.filter(arr, lambda x: x.isNotNull()))
        df = df.drop(*err_cols)
    else:
        df = df.withColumn("_cast_errors", F.array().cast("array<string>"))
    return df


# ---- Portable (Spark-free) mirror of the cast-error detection (U14) ----------
def _cast_present(v):
    return v is not None and str(v).strip() != ""


def _parses_long(v):
    try:
        int(str(v).strip())
        return True
    except (ValueError, TypeError):
        return False


def _parses_double(v):
    try:
        float(str(v).strip())
        return True
    except (ValueError, TypeError):
        return False


def cast_error_columns(row, long_cols=None, double_cols=None):
    """Spark-free mirror of the numeric cast in `cast_query_execution`.

    Returns the list of column names whose raw value is present (non-empty) but
    would NOT parse to its target numeric type — i.e. a silent cast-to-null that
    should be surfaced (U14) rather than masked. Used by the Spark-free regression
    test so the detection rule is verifiable without a Spark session.
    """
    long_cols = QE_NUMERIC_LONG if long_cols is None else long_cols
    double_cols = QE_NUMERIC_DOUBLE if double_cols is None else double_cols
    errs = []
    for c in long_cols:
        if _cast_present(row.get(c)) and not _parses_long(row.get(c)):
            errs.append(c)
    for c in double_cols:
        if _cast_present(row.get(c)) and not _parses_double(row.get(c)):
            errs.append(c)
    return errs


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
        .when(trimmed.startswith("{"), trimmed)  # already JSON
        .when(F.trim(decoded_b64).startswith("{"), decoded_b64)  # base64 JSON
        .otherwise(F.lit(None).cast(StringType()))  # unknown -> null
    )
    df = df.withColumn("_eval_context_json", normalized)
    df = df.withColumn(
        "artifact_id", F.get_json_object("_eval_context_json", "$.artifactId")
    ).withColumn(
        "artifact_kind", F.get_json_object("_eval_context_json", "$.artifactKind")
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
    for c, t in [
        ("WorkingSetMB", "double"),
        ("PrivateBytesMB", "double"),
        ("CpuPercent", "double"),
        ("ThreadCount", "int"),
        ("HandleCount", "int"),
        ("ProcessId", "long"),
        ("LogicalCores", "int"),
    ]:
        if c in df.columns:
            df = df.withColumn(c, F.col(c).cast(t))
    if "CollectedAtUtc" in df.columns:
        df = df.withColumn("CollectedAtUtc", F.to_timestamp("CollectedAtUtc"))
    return df


def gold_mashup_health(df, runaway_working_set_mb=6000.0):
    """Per-gateway mashup rollup: peak/avg working set, container count, and a
    runaway flag when any container's working set exceeds the threshold."""
    agg = (
        df.groupBy("GatewayObjectId", "HostName")
        .agg(
            F.max("WorkingSetMB").alias("peak_working_set_mb"),
            F.avg("WorkingSetMB").alias("avg_working_set_mb"),
            F.max("CpuPercent").alias("peak_cpu_pct"),
            F.countDistinct("ProcessId").alias("distinct_processes"),
            F.sum(F.when(F.col("IsMashupContainer") == True, 1).otherwise(0)).alias(
                "mashup_samples"
            ),
        )
        .withColumn(
            "runaway_container",
            F.col("peak_working_set_mb") > F.lit(runaway_working_set_mb),
        )
    )
    return agg
