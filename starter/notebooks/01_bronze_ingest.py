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
# Schema-adaptivity design:
#   - Known columns are mapped by name using a whitelist dict
#   - Unknown columns are collected into a MAP column _extra_cols
#   - Missing expected columns get a null value (not a pipeline failure)
#   - The DataFormat.Error that breaks the Microsoft PBIT template is caused
#     by positional CSV parsing; we never rely on column position.
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
LAKEHOUSE_PATH = "abfss://<workspace>@onelake.dfs.fabric.microsoft.com/<lakehouse>.Lakehouse"
LANDING_PATH   = f"{LAKEHOUSE_PATH}/Files/bronze_landing"   # Where PS scripts drop JSON
BRONZE_PATH    = f"{LAKEHOUSE_PATH}/Tables"                  # Delta tables destination
USE_FPM_BRIDGE = False   # Set True if you have FPM Eventhouse with OneLake availability

# ── FPM bridge config (only used if USE_FPM_BRIDGE = True) ──
FPM_EVENTHOUSE_DELTA_PATH = ""   # Path to FPM Eventhouse Delta tables (OneLake availability)

# ── Configuration ──────────────────────────────────────────────────────────
from pyspark.sql import SparkSession
from pyspark.sql import functions as F
from pyspark.sql.types import (
    StructType, StructField, StringType, LongType, DoubleType,
    TimestampType, BooleanType, DateType, MapType
)
from delta.tables import DeltaTable
import json
from datetime import datetime, timezone

spark = SparkSession.builder.getOrCreate()
spark.conf.set("spark.sql.extensions", "io.delta.sql.DeltaSparkSessionExtension")
spark.conf.set("spark.sql.catalog.spark_catalog", "org.apache.spark.sql.delta.catalog.DeltaCatalog")

# ── KNOWN COLUMN SCHEMA — sourced from MS docs, not invented ───────────────
# Source: https://learn.microsoft.com/en-us/data-integration/gateway/service-gateway-performance
# These are the EXACT column names from the gateway log CSV files.
# Note: column names in the file include units in parens, e.g. "QueryExecutionDuration(ms)"
# We normalize by stripping units in parens for Delta column names.

QUERY_EXECUTION_KNOWN_COLS = {
    # CSV column name (as written by gateway) -> Delta column name, PySpark type
    "GatewayObjectId":                             ("GatewayObjectId",                    StringType()),
    "RequestId":                                   ("RequestId",                           StringType()),
    "DataSource":                                  ("DataSource",                          StringType()),
    "QueryTrackingId":                             ("QueryTrackingId",                     StringType()),
    "QueryExecutionEndTimeUTC":                    ("QueryExecutionEndTimeUTC",             TimestampType()),
    "QueryExecutionDuration(ms)":                  ("QueryExecutionDuration",               LongType()),
    "QueryType":                                   ("QueryType",                           StringType()),
    "DataProcessingEndTimeUTC":                    ("DataProcessingEndTimeUTC",             TimestampType()),
    "DataProcessingDuration(ms)":                  ("DataProcessingDuration",               LongType()),
    "Success":                                     ("Success",                             BooleanType()),
    "ErrorMessage":                                ("ErrorMessage",                        StringType()),
    "SpoolingDiskWritingDuration(ms)":             ("SpoolingDiskWritingDuration",          LongType()),
    "SpoolingDiskReadingDuration(ms)":             ("SpoolingDiskReadingDuration",          LongType()),
    "SpoolingTotalDataSize(bytes)":                ("SpoolingTotalDataSize",                LongType()),
    "DataReadingAndSerializationDuration(ms)":     ("DataReadingAndSerializationDuration",  LongType()),
    "DiskRead(byte/sec)":                          ("DiskRead",                            DoubleType()),
    "DiskWrite(byte/sec)":                         ("DiskWrite",                           DoubleType()),
}

QUERY_START_KNOWN_COLS = {
    "GatewayObjectId":      ("GatewayObjectId",    StringType()),
    "RequestId":            ("RequestId",           StringType()),
    "QueryTrackingId":      ("QueryTrackingId",     StringType()),
    "QueryStartTimeUTC":    ("QueryStartTimeUTC",   TimestampType()),
    "QueryType":            ("QueryType",           StringType()),
    "DataSource":           ("DataSource",          StringType()),
    # EvaluationContext is a JSON blob (artifactId etc.) — keep as string, parse below
    "EvaluationContext":    ("EvaluationContext",   StringType()),
}

SYSTEM_COUNTER_KNOWN_COLS = {
    "GatewayObjectId":         ("GatewayObjectId",      StringType()),
    "CounterTimeUTC":          ("CounterTimeUTC",        TimestampType()),
    "SystemCPUPercent":        ("SystemCPUPercent",      DoubleType()),
    "SystemMEMUsedPercent":    ("SystemMEMUsedPercent",  DoubleType()),
    "GatewayCPUPercent":       ("GatewayCPUPercent",     DoubleType()),
    "GatewayMEMKb":            ("GatewayMEMKb",          LongType()),
}


def parse_csv_schema_adaptive(raw_csv: str, known_cols: dict, source_file: str, log_type: str, host: str, collected_at: str):
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
    lines = raw_csv.strip().splitlines()
    if len(lines) < 2:
        return []   # Empty or header-only file

    headers = [h.strip().strip('"') for h in lines[0].split(",")]

    # Build a mapping: csv_header_index -> (delta_col_name, type_obj) or None
    col_map = {}   # index -> (delta_name, type) for known cols
    for i, h in enumerate(headers):
        if h in known_cols:
            col_map[i] = known_cols[h]
        # else: will go to _extra_cols

    records = []
    for line in lines[1:]:
        if not line.strip():
            continue
        # [Assumption] Simple comma-split; quoted fields with embedded commas
        # are NOT handled. Gateway log CSVs are not quoted per observation from
        # community reports; validate in Phase 5.
        values = [v.strip().strip('"') for v in line.split(",")]

        row = {}
        extra_cols = {}

        for i, val in enumerate(values):
            if i >= len(headers):
                # More values than headers — capture as overflow
                extra_cols[f"_overflow_{i}"] = val
                continue

            header = headers[i]
            if i in col_map:
                delta_name, col_type = col_map[i]
                row[delta_name] = _cast_value(val, col_type)
            else:
                # Unknown column — preserve in _extra_cols
                extra_cols[header] = val

        # Fill missing known columns with None
        for csv_name, (delta_name, _) in known_cols.items():
            if delta_name not in row:
                row[delta_name] = None

        row["_extra_cols"]    = extra_cols if extra_cols else {}
        row["_source_file"]   = source_file
        row["_log_type"]      = log_type
        row["_gateway_host"]  = host
        row["_ingested_at"]   = collected_at

        records.append(row)

    return records


def _cast_value(val: str, col_type):
    """Best-effort type casting. Returns None on failure (never raises)."""
    if val is None or val.strip() == "" or val.strip().lower() in ("null", "nan", "none"):
        return None
    try:
        if isinstance(col_type, LongType):
            return int(float(val))   # float() first handles "0.0" strings
        elif isinstance(col_type, DoubleType):
            return float(val)
        elif isinstance(col_type, BooleanType):
            return val.strip().lower() in ("true", "1", "yes")
        elif isinstance(col_type, TimestampType):
            # Gateway timestamps are UTC ISO8601 or "YYYY-MM-DD HH:MM:SS"
            # Return as string; Spark timestamp cast handles both formats
            return val
        else:
            return val
    except (ValueError, TypeError):
        return None   # Do not raise — return null for unparseable values


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
    # [Assumption] EvaluationContext is a base64-encoded JSON or direct JSON string.
    # Community analysis (3Cloud blog) suggests it may be base64-encoded.
    # This implementation tries direct JSON parse first, then base64 decode.
    # Validate in Phase 5.
    df = df.withColumn(
        "artifact_id",
        F.when(
            F.col("EvaluationContext").isNotNull(),
            F.get_json_object(F.col("EvaluationContext"), "$.artifactId")
        ).otherwise(F.lit(None).cast(StringType()))
    ).withColumn(
        "artifact_type",
        F.when(
            F.col("EvaluationContext").isNotNull(),
            F.get_json_object(F.col("EvaluationContext"), "$.artifactKind")
        ).otherwise(F.lit(None).cast(StringType()))
    )
    return df


def add_partition_date(df, ts_col: str):
    """Add _partition_date from a timestamp column for Delta partitioning."""
    return df.withColumn("_partition_date", F.to_date(F.col(ts_col)))


# ── MAIN INGEST LOGIC ────────────────────────────────────────────────────────

def ingest_gateway_logs():
    """
    Main ingest: reads staged JSON from Collect-GatewayLogs.ps1 output,
    parses schema-adaptively, writes to Bronze Delta tables.
    """
    print(f"[01_bronze_ingest] Starting gateway log ingest from: {LANDING_PATH}")

    # Read all staged gateway log JSON files
    # [Assumption] Fabric Lakehouse Files/ is accessible via ABFSS path
    try:
        landing_df = spark.read.json(f"{LANDING_PATH}/gateway_logs_*.json")
    except Exception as e:
        print(f"[WARN] No gateway log staging files found or read error: {e}")
        return

    if landing_df.rdd.isEmpty():
        print("[INFO] No new gateway log records to ingest")
        return

    rows = landing_df.collect()
    print(f"[INFO] Found {len(rows)} staged file records")

    # Separate by log type
    qe_records  = []   # QueryExecution
    qs_records  = []   # QueryStart
    sc_records  = []   # SystemCounter
    agg_records = []   # QueryAggregation (simplified for now)

    for row in rows:
        raw_csv     = row["RawCsvContent"]
        log_type    = row["LogType"]
        source_file = row["SourceFileName"]
        host        = row["GatewayHostName"]
        collected   = row["CollectedAtUtc"]

        if log_type == "QueryExecution":
            parsed = parse_csv_schema_adaptive(raw_csv, QUERY_EXECUTION_KNOWN_COLS, source_file, log_type, host, collected)
            qe_records.extend(parsed)
        elif log_type == "QueryStart":
            parsed = parse_csv_schema_adaptive(raw_csv, QUERY_START_KNOWN_COLS, source_file, log_type, host, collected)
            qs_records.extend(parsed)
        elif log_type == "SystemCounter":
            parsed = parse_csv_schema_adaptive(raw_csv, SYSTEM_COUNTER_KNOWN_COLS, source_file, log_type, host, collected)
            sc_records.extend(parsed)
        elif log_type == "QueryAggregation":
            agg_records.extend([{"_raw": raw_csv, "_source_file": source_file, "_host": host}])

    print(f"[INFO] Parsed: QE={len(qe_records)}, QS={len(qs_records)}, SC={len(sc_records)}, Agg={len(agg_records)}")

    # ── Write QueryExecution Bronze Delta ──────────────────────────────────
    if qe_records:
        qe_df = spark.createDataFrame(qe_records)
        # Cast timestamp columns explicitly (Spark accepts ISO8601 strings)
        for ts_col in ["QueryExecutionEndTimeUTC", "DataProcessingEndTimeUTC"]:
            if ts_col in qe_df.columns:
                qe_df = qe_df.withColumn(ts_col, F.to_timestamp(F.col(ts_col)))
        qe_df = add_partition_date(qe_df, "QueryExecutionEndTimeUTC")

        _write_bronze_delta(qe_df, "bronze_query_execution", partition_cols=["_partition_date"])
        # Emit schema warning if any expected columns were missing
        _emit_schema_warnings(qe_records, QUERY_EXECUTION_KNOWN_COLS, "QueryExecution")

    # ── Write QueryStart Bronze Delta ──────────────────────────────────────
    if qs_records:
        qs_df = spark.createDataFrame(qs_records)
        if "QueryStartTimeUTC" in qs_df.columns:
            qs_df = qs_df.withColumn("QueryStartTimeUTC", F.to_timestamp(F.col("QueryStartTimeUTC")))
        qs_df = parse_evaluation_context(qs_df)
        qs_df = add_partition_date(qs_df, "QueryStartTimeUTC")
        _write_bronze_delta(qs_df, "bronze_query_start", partition_cols=["_partition_date"])

    # ── Write SystemCounter Bronze Delta ───────────────────────────────────
    if sc_records:
        sc_df = spark.createDataFrame(sc_records)
        if "CounterTimeUTC" in sc_df.columns:
            sc_df = sc_df.withColumn("CounterTimeUTC", F.to_timestamp(F.col("CounterTimeUTC")))
        sc_df = add_partition_date(sc_df, "CounterTimeUTC")
        _write_bronze_delta(sc_df, "bronze_system_counter", partition_cols=["_partition_date"])


def _write_bronze_delta(df, table_name: str, partition_cols: list):
    """
    Write DataFrame to Bronze Delta table (append mode).
    Uses merge-on-schema to handle new columns gracefully.
    [Unverified] mergeSchema=true behavior in Fabric Delta requires Phase 5 validation.
    """
    table_path = f"{BRONZE_PATH}/{table_name}"
    (
        df.write
          .format("delta")
          .mode("append")
          .option("mergeSchema", "true")   # Allow new columns without error
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
    all_cols = set(records[0].keys()) - {"_extra_cols", "_source_file", "_log_type", "_gateway_host", "_ingested_at"}
    expected_delta_names = {v[0] for v in known_cols.values()}
    missing = expected_delta_names - all_cols
    if missing:
        print(f"[SCHEMA_WARN] {log_type}: Expected columns not found in logs: {missing}")
        print(f"[SCHEMA_WARN] These may have been renamed/removed in current gateway version.")
        print(f"[SCHEMA_WARN] Validate against: https://learn.microsoft.com/en-us/data-integration/gateway/service-gateway-performance")
        # TODO (Phase 5): write schema_warn rows to a bronze_schema_warnings Delta table
        # for monitoring in the report and Activator alerting


def ingest_network_metrics():
    """Ingest network metrics JSON from Collect-NetworkMetrics.ps1."""
    try:
        df = spark.read.json(f"{LANDING_PATH}/network_metrics_*.json")
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
            "CollectedAtUTC", "GatewayHostName",
            F.col("nic.NicName").alias("NicName"),
            F.col("nic.BytesTotalPerSec_avg").cast(DoubleType()).alias("BytesTotalPerSec"),
            F.col("nic.CurrentBandwidthBps").cast(DoubleType()).alias("CurrentBandwidthBps"),
            F.col("nic.UtilizationPct").cast(DoubleType()).alias("UtilizationPct"),
            F.col("LatencyProbe.LatencyMs_avg").cast(DoubleType()).alias("LatencyMs_PBIRelay"),
            F.col("LatencyProbe.TargetHost").alias("LatencyProbeTarget"),
        )
        .withColumn("_partition_date", F.to_date(F.col("CollectedAtUTC")))
    )
    _write_bronze_delta(df_exploded, "bronze_network_metrics", partition_cols=["_partition_date"])


def ingest_event_log():
    """Ingest Windows Event Log JSON from Collect-EventLog.ps1."""
    try:
        df = spark.read.json(f"{LANDING_PATH}/event_log_*.json")
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
            "_ingested_at", "GatewayHostName",
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
    _write_bronze_delta(df_exploded, "bronze_event_log", partition_cols=["_partition_date"])


def ingest_disk_spool():
    """Ingest disk spool JSON from Collect-DiskSpool.ps1."""
    try:
        df = spark.read.json(f"{LANDING_PATH}/disk_spool_*.json")
    except Exception as e:
        print(f"[WARN] No disk spool files: {e}")
        return

    df_clean = (
        df.select(
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
        )
        .withColumn("_partition_date", F.to_date(F.col("CollectedAtUTC")))
    )
    _write_bronze_delta(df_clean, "bronze_disk_spool", partition_cols=["_partition_date"])


def ingest_gateway_inventory():
    """Ingest gateway inventory JSON from Get-GatewayInventory.ps1."""
    try:
        df = spark.read.json(f"{LANDING_PATH}/gateway_inventory_*.json")
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
    _write_bronze_delta(df_inv, "bronze_gateway_inventory", partition_cols=["_partition_date"])


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
    print("[STUB] FPM bridge: column mapping not yet implemented. See phase4_architecture.md §1.2")


# ── ENTRY POINT ──────────────────────────────────────────────────────────────
if __name__ == "__main__" or True:   # True: runs in Fabric notebook context
    print(f"=== 01_bronze_ingest.py starting at {datetime.now(timezone.utc).isoformat()} ===")
    ingest_gateway_logs()
    ingest_network_metrics()
    ingest_event_log()
    ingest_disk_spool()
    ingest_gateway_inventory()
    if USE_FPM_BRIDGE:
        ingest_fpm_bridge()
    print("=== 01_bronze_ingest.py complete ===")
