# =============================================================================
# 03_gold_aggregate.py
# Label: [NET-NEW]
#
# Pain points addressed:
#   #1 — No real-time alerting (heartbeat_age_minutes drives Activator)
#   #6 — Fleet / multi-gateway view (cluster rollup + skew score)
#   #9 — Disk spooler surprises (spool health in gold_gateway_health)
#   #10 — Credential drift (datasource status in gateway health)
#
# What this does:
#   Builds Gold Delta tables from Silver:
#     gold_gateway_health    — per-node 5-min health rollup; drives Activator alerting
#     gold_query_performance — per-datasource hourly aggregates
#     gold_cluster_load      — per-cluster load skew score (Coefficient of Variation)
#     gold_dim_gateway       — slowly-changing dimension for gateway nodes (SCD type 2)
#
# [Unverified] All PySpark aggregation logic requires Phase 5 validation.
#              Threshold values (e.g., load skew CV > 0.5) are
#              [Assumption]-based starting points — tune after live data collection.
# =============================================================================

import os
from pyspark.sql import SparkSession
from pyspark.sql import functions as F
from pyspark.sql.window import Window
from pyspark.sql.types import StringType, LongType, DoubleType, TimestampType, BooleanType
from delta.tables import DeltaTable
from datetime import datetime, timezone

spark = SparkSession.builder.getOrCreate()

# LAKEHOUSE_PATH overridable via GATEWAYMON_LAKEHOUSE_ROOT so the REAL build_gold_*
# transforms run against a LOCAL temp dir under the pbi-spark harness with no Fabric
# tenant. Default unchanged -> Fabric behavior identical. _TABLE_FORMAT lets local
# tests use parquet (hermetic, no Delta JAR fetch); Fabric keeps delta.
LAKEHOUSE_PATH = os.environ.get(
    "GATEWAYMON_LAKEHOUSE_ROOT",
    "abfss://<workspace>@onelake.dfs.fabric.microsoft.com/<lakehouse>.Lakehouse",
)
_TABLE_FORMAT = os.environ.get("GATEWAYMON_TABLE_FORMAT", "delta")
SILVER_PATH    = f"{LAKEHOUSE_PATH}/Tables"
GOLD_PATH      = f"{LAKEHOUSE_PATH}/Tables"


def read_silver(table_name: str):
    path = f"{SILVER_PATH}/{table_name}"
    try:
        return spark.read.format(_TABLE_FORMAT).load(path)
    except Exception as e:
        print(f"[WARN] Could not read {table_name}: {e}")
        return None


def write_gold(df, table_name: str, partition_cols=None, mode="overwrite"):
    path = f"{GOLD_PATH}/{table_name}"
    writer = (
        df.write
          .format(_TABLE_FORMAT)
          .mode(mode)
          .option("overwriteSchema", "true")
    )
    if partition_cols:
        writer = writer.partitionBy(*partition_cols)
    writer.save(path)
    print(f"[INFO] Wrote {df.count()} rows to {table_name}")


# ─────────────────────────────────────────────────────────────────────────────
# GOLD 1: gold_gateway_health
#
# Per-node, per-5-minute health snapshot.
# PRIMARY DRIVER for Fabric Activator alerting (heartbeat_age_minutes).
#
# heartbeat_age_minutes: time since last successful gateway inventory check
# that confirmed the node is "Live". When this exceeds the Activator threshold,
# the gateway-offline alert fires.
#
# Includes: query counts, error rates, CPU/mem, spool health, NIC utilization
# ─────────────────────────────────────────────────────────────────────────────
def build_gold_gateway_health():
    print("[gold_gateway_health] Building...")

    silver_qe  = read_silver("silver_query_execution")
    sc_df      = read_silver("bronze_system_counter")   # Read bronze directly (no silver transform needed)
    inv_df     = read_silver("bronze_gateway_inventory")
    disk_df    = read_silver("bronze_disk_spool")
    net_df     = read_silver("silver_network_correlated")

    now_utc = datetime.now(timezone.utc)

    # ── Heartbeat: last seen timestamp per node ─────────────────────────────
    heartbeat = None
    if inv_df is not None:
        heartbeat = inv_df.filter(F.col("Status") == "Live").groupBy("GatewayObjectId").agg(
            F.max("CollectedAtUTC").alias("last_heartbeat_utc"),
            F.last("GatewayClusterName").alias("GatewayClusterName"),
            F.last("GatewayNodeName").alias("GatewayNodeName"),
            F.last("Status").alias("status_current"),
            F.last("Version").alias("Version"),
        ).withColumn(
            "heartbeat_age_minutes",
            (F.lit(now_utc.timestamp()).cast(DoubleType()) -
             F.unix_timestamp(F.col("last_heartbeat_utc"))) / 60.0
        )

    # ── Query counts per node (last 5 min window) ─────────────────────────
    query_stats = None
    if silver_qe is not None:
        # 5-minute time bucket for rolling stats
        query_stats = silver_qe.withColumn(
            "window_5min",
            F.window("QueryExecutionEndTimeUTC", "5 minutes").getField("start")
        ).groupBy("GatewayObjectId", "window_5min").agg(
            F.count("*").alias("query_count_5min"),
            F.sum(F.when(F.col("Success") == False, 1).otherwise(0)).alias("error_count_5min"),
            F.avg("QueryExecutionDuration").alias("duration_avg_ms"),
            F.expr("percentile_approx(QueryExecutionDuration, 0.95)").alias("duration_p95_ms"),
        ).withColumn(
            "error_rate_pct",
            F.round(F.col("error_count_5min") / F.greatest(F.col("query_count_5min"), F.lit(1)) * 100, 2)
        )

    # ── System counter stats (CPU/mem) ─────────────────────────────────────
    cpu_mem = None
    if sc_df is not None:
        cpu_mem = sc_df.groupBy("GatewayObjectId").agg(
            F.avg("GatewayCPUPercent").alias("cpu_pct_avg"),
            F.avg("GatewayMEMKb").alias("mem_kb_avg"),
            F.avg("SystemCPUPercent").alias("system_cpu_avg"),
            F.max("CounterTimeUTC").alias("last_counter_utc"),
        )

    # ── Disk/spool health ─────────────────────────────────────────────────
    spool_health = None
    if disk_df is not None:
        spool_health = disk_df.groupBy("GatewayHostName").agg(
            F.last("FreeSpacePct").alias("spool_free_pct"),
            F.last("SpoolDirSizeBytes").alias("spool_dir_size_bytes"),
            F.last("AlertLevel").alias("spool_alert_level"),
            F.last("StreamBeforeRequestCompletes_Warning").alias("spool_stream_mode_warn"),
        )

    # ── Network stats ─────────────────────────────────────────────────────
    net_stats = None
    if net_df is not None:
        net_stats = net_df.groupBy("GatewayObjectId").agg(
            F.avg("BytesTotalPerSec_max").alias("nic_bytes_per_sec_avg"),
            F.avg("LatencyMs_PBIRelay").alias("latency_ms_avg"),
            F.expr("percentile_approx(LatencyMs_PBIRelay, 0.95)").alias("latency_ms_p95"),
            F.avg("UtilizationPct_max").alias("network_utilization_pct_avg"),
        )

    # ── Assemble gold_gateway_health ────────────────────────────────────────
    if heartbeat is None:
        print("[WARN] No inventory/heartbeat data; gold_gateway_health will be empty or partial")
        return

    health = heartbeat

    if query_stats is not None:
        # Get most recent 5-min window stats per node
        latest_window = Window.partitionBy("GatewayObjectId").orderBy(F.col("window_5min").desc())
        qs_latest = query_stats.withColumn("_rn", F.row_number().over(latest_window)) \
                               .filter(F.col("_rn") == 1).drop("_rn", "window_5min")
        health = health.join(qs_latest, on="GatewayObjectId", how="left")

    if cpu_mem is not None:
        health = health.join(cpu_mem, on="GatewayObjectId", how="left")

    # Note: spool and network join on hostname (not GatewayObjectId)
    # [Assumption] GatewayNodeName matches hostname from spool/network collectors
    if spool_health is not None:
        health = health.join(
            spool_health,
            health["GatewayNodeName"] == spool_health["GatewayHostName"],
            how="left"
        ).drop("GatewayHostName")

    if net_stats is not None:
        health = health.join(net_stats, on="GatewayObjectId", how="left")

    health = health.withColumn("_computed_at_utc", F.current_timestamp()) \
                   .withColumn("_partition_date", F.to_date(F.col("last_heartbeat_utc")))

    write_gold(health, "gold_gateway_health", partition_cols=["_partition_date"])


# ─────────────────────────────────────────────────────────────────────────────
# GOLD 2: gold_query_performance
# Per-datasource hourly aggregates for the Query Saturation report page
# ─────────────────────────────────────────────────────────────────────────────
def build_gold_query_performance():
    print("[gold_query_performance] Building...")

    silver_qe = read_silver("silver_query_execution")
    if silver_qe is None:
        return

    perf = silver_qe.withColumn(
        "hour_utc", F.date_trunc("hour", F.col("QueryExecutionEndTimeUTC"))
    ).groupBy(
        "GatewayObjectId", "DataSource", "QueryType", "hour_utc"
    ).agg(
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
        F.round(F.avg("DataReadingAndSerializationDuration"), 0).alias("data_reading_ms_avg"),
        F.round(F.avg("DiskRead"), 2).alias("disk_read_bps_avg"),
        F.round(F.avg("DiskWrite"), 2).alias("disk_write_bps_avg"),
        # is_spooled percentage
        F.round(
            F.sum(F.when(F.col("is_spooled") == True, 1).otherwise(0)) /
            F.greatest(F.count("*"), F.lit(1)) * 100, 2
        ).alias("spooled_pct"),
    ).withColumn(
        "_partition_date", F.to_date(F.col("hour_utc"))
    )

    write_gold(perf, "gold_query_performance", partition_cols=["_partition_date"])


# ─────────────────────────────────────────────────────────────────────────────
# GOLD 3: gold_cluster_load — Cluster Load Skew Score
#
# Computes per-cluster load balance metric using Coefficient of Variation (CV)
# of per-node query counts. A high CV means one node is handling most queries
# (load skew) — a problem for reliability and performance.
#
# CV = std_dev(per_node_query_count) / mean(per_node_query_count)
# CV > 0.5 = imbalanced [Assumption: threshold is a starting point]
# CV = 0   = perfectly balanced
#
# This implements the load skew detection gap identified in pipeline_critique.md
# and phase0_scope.md JtBD #7.
# ─────────────────────────────────────────────────────────────────────────────
def build_gold_cluster_load():
    print("[gold_cluster_load] Building...")

    silver_qe = read_silver("silver_query_execution")
    inv_df    = read_silver("bronze_gateway_inventory")

    if silver_qe is None or inv_df is None:
        print("[WARN] Missing data for cluster load skew; skipping")
        return

    # Get cluster membership from inventory
    cluster_map = inv_df.select(
        "GatewayObjectId", "GatewayClusterId", "GatewayClusterName"
    ).dropDuplicates(["GatewayObjectId"])

    # Query counts per node per hour
    node_hourly = silver_qe.withColumn(
        "hour_utc", F.date_trunc("hour", F.col("QueryExecutionEndTimeUTC"))
    ).join(cluster_map, on="GatewayObjectId", how="inner") \
     .groupBy("GatewayClusterId", "GatewayClusterName", "GatewayObjectId", "hour_utc") \
     .agg(F.count("*").alias("node_query_count"))

    # Compute cluster-level stats for CV
    cluster_stats = node_hourly.groupBy("GatewayClusterId", "GatewayClusterName", "hour_utc").agg(
        F.count("GatewayObjectId").alias("node_count"),
        F.avg("node_query_count").alias("node_query_mean"),
        F.stddev("node_query_count").alias("node_query_stddev"),
        F.max("node_query_count").alias("max_node_queries"),
        F.sum("node_query_count").alias("total_cluster_queries"),
    ).withColumn(
        # Coefficient of Variation (CV) = stddev / mean
        # Returns null if mean = 0 (no queries) or single node
        "queries_per_node_cv",
        F.when(
            (F.col("node_query_mean") > 0) & (F.col("node_count") > 1),
            F.round(F.col("node_query_stddev") / F.col("node_query_mean"), 3)
        ).otherwise(F.lit(None).cast(DoubleType()))
    ).withColumn(
        # Load skew classification [Assumption: thresholds]
        "load_skew_classification",
        F.when(F.col("queries_per_node_cv").isNull(), F.lit("SINGLE_NODE_OR_NO_DATA"))
         .when(F.col("queries_per_node_cv") > 0.8, F.lit("SEVERELY_SKEWED"))
         .when(F.col("queries_per_node_cv") > 0.5, F.lit("MODERATELY_SKEWED"))
         .when(F.col("queries_per_node_cv") > 0.2, F.lit("SLIGHTLY_SKEWED"))
         .otherwise(F.lit("BALANCED"))
    )

    # Identify the hottest node
    hottest_node = node_hourly.join(
        cluster_stats.select("GatewayClusterId", "hour_utc", "max_node_queries"),
        on=["GatewayClusterId", "hour_utc"]
    ).filter(
        F.col("node_query_count") == F.col("max_node_queries")
    ).select(
        "GatewayClusterId", "hour_utc",
        F.col("GatewayObjectId").alias("hottest_node_id"),
        F.col("node_query_count").alias("hottest_node_queries"),
    )

    cluster_load = cluster_stats.join(
        hottest_node, on=["GatewayClusterId", "hour_utc"], how="left"
    ).withColumn(
        "hottest_node_query_pct",
        F.when(
            F.col("total_cluster_queries") > 0,
            F.round(F.col("hottest_node_queries") / F.col("total_cluster_queries") * 100, 1)
        ).otherwise(F.lit(None).cast(DoubleType()))
    ).withColumn(
        "_partition_date", F.to_date(F.col("hour_utc"))
    )

    write_gold(cluster_load, "gold_cluster_load", partition_cols=["_partition_date"])


# ─────────────────────────────────────────────────────────────────────────────
# GOLD 4: gold_dim_gateway — Slowly Changing Dimension (SCD Type 2)
#
# Tracks gateway node properties over time to detect version changes,
# status changes, and datasource count changes.
# This is the source for the "fleet" view in reports.
# ─────────────────────────────────────────────────────────────────────────────
def build_gold_dim_gateway():
    print("[gold_dim_gateway] Building...")

    inv_df = read_silver("bronze_gateway_inventory")
    if inv_df is None:
        return

    # Latest snapshot per node
    latest_window = Window.partitionBy("GatewayObjectId").orderBy(F.col("CollectedAtUTC").desc())
    current_state = inv_df.withColumn("_rn", F.row_number().over(latest_window)) \
                          .filter(F.col("_rn") == 1).drop("_rn")

    dim = current_state.select(
        "GatewayObjectId", "GatewayClusterId", "GatewayClusterName",
        "GatewayNodeName", "Version", "Status", "DatasourceCount",
        F.col("CollectedAtUTC").alias("valid_from"),
        F.lit(None).cast(TimestampType()).alias("valid_to"),
        F.lit(True).cast(BooleanType()).alias("is_current"),
    ).withColumn("_partition_date", F.to_date(F.col("valid_from")))

    # [STUB] Full SCD2 merge requires DeltaTable.merge() to handle
    # version changes correctly. Simplified here to overwrite current.
    # In Phase 5: implement proper SCD2 merge on GatewayObjectId + Version.
    write_gold(dim, "gold_dim_gateway", partition_cols=["_partition_date"])


# ── ENTRY POINT ──────────────────────────────────────────────────────────────
# Auto-runs in a Fabric notebook (__name__ != "__main__"). Tests import this module
# to call build_gold_* directly and set GATEWAYMON_AUTORUN=0 to suppress auto-run.
if __name__ == "__main__" or os.environ.get("GATEWAYMON_AUTORUN", "1") != "0":
    print(f"=== 03_gold_aggregate.py starting at {datetime.now(timezone.utc).isoformat()} ===")
    build_gold_gateway_health()
    build_gold_query_performance()
    build_gold_cluster_load()
    build_gold_dim_gateway()
    print("=== 03_gold_aggregate.py complete ===")
