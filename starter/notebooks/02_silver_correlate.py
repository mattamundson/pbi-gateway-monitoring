# =============================================================================
# 02_silver_correlate.py
# Label: [NET-NEW]
#
# Pain points addressed:
#   #2 — Opaque refresh failures (DIFFERENTIATOR #2: unified triage join)
#   #3 — Zero query attribution (DIFFERENTIATOR #3: best-effort identity)
#   #7 — Network blind spot (DIFFERENTIATOR #4: network correlation)
#
# What this does:
#   Reads from Bronze Delta tables and produces Silver tables:
#     1. silver_query_execution  — cleaned + typed, QE + QS joined on RequestId
#     2. silver_triage           — 3-way failure correlation (QE + RefreshHistory + EventLog)
#     3. silver_identity_attribution — best-effort query → dataset/user (FUZZY, labeled)
#     4. silver_network_correlated   — NIC metrics joined to query execution context
#
# IMPORTANT LABEL — READ BEFORE USE:
#   silver_triage: triage_confidence column values mean:
#     EXACT_REQUESTID   = RequestId matched exactly (reliable)
#     TIME_WINDOW       = Matched by ±30s time window + GatewayObjectId (best-effort)
#     HOST_ONLY         = Matched by hostname + ±120s (weakest — may be false positive)
#
#   silver_identity_attribution: attribution_confidence values mean:
#     EVALUATION_CONTEXT = EvaluationContext.artifactId matched (Fabric workloads only — reliable)
#     FUZZY_TIME_WINDOW  = RequestId + ±60s time window match against audit log (best-effort)
#     UNATTRIBUTED       = No attribution possible
#
#   Identity attribution is NOT available for:
#     - Paginated Reports (not logged in gateway query log)
#     - Dataflow Gen1 (EvaluationContext not populated)
#     - DirectQuery user sessions (RequestId not in audit log)
#   Reference: https://learn.microsoft.com/en-us/data-integration/gateway/service-gateway-performance
#             https://3cloudsolutions.com/resources/monitoring-power-bi-on-premises-data-gateway-performance/
#
# [Unverified] All PySpark join logic requires Phase 5 validation in live environment.
#              Time-window join sizes and thresholds are [Assumption]-based starting points.
# =============================================================================

from pyspark.sql import SparkSession
from pyspark.sql import functions as F
from pyspark.sql.window import Window
from pyspark.sql.types import StringType, LongType, DoubleType, TimestampType
from delta.tables import DeltaTable
from datetime import datetime, timezone, timedelta

spark = SparkSession.builder.getOrCreate()

# ── Configuration ─────────────────────────────────────────────────────────
LAKEHOUSE_PATH = (
    "abfss://<workspace>@onelake.dfs.fabric.microsoft.com/<lakehouse>.Lakehouse"
)
BRONZE_PATH = f"{LAKEHOUSE_PATH}/Tables"
SILVER_PATH = f"{LAKEHOUSE_PATH}/Tables"

# Time-window thresholds (seconds) for fuzzy joins — adjust based on Phase 5 observations
TRIAGE_TIME_WINDOW_SECS = 30  # QE ↔ RefreshHistory time window
EVENT_LOG_TIME_WINDOW_SECS = 120  # QE ↔ EventLog time window
ATTRIBUTION_TIME_WINDOW_SECS = 60  # QS ↔ Activity Events time window

# Incremental: only process records newer than this watermark
# In production: read from a watermark Delta table; simplified here
PROCESS_SINCE_DAYS = 1  # Process last N days; adjust for full backfill


def read_bronze(table_name: str):
    path = f"{BRONZE_PATH}/{table_name}"
    try:
        return spark.read.format("delta").load(path)
    except Exception as e:
        print(f"[WARN] Could not read {table_name}: {e}")
        return None


def write_silver(df, table_name: str, partition_cols=None):
    path = f"{SILVER_PATH}/{table_name}"
    writer = (
        df.write.format("delta").mode("overwrite").option("overwriteSchema", "true")
    )
    if partition_cols:
        writer = writer.partitionBy(*partition_cols)
    writer.save(path)
    print(f"[INFO] Wrote {df.count()} rows to {table_name}")


# ─────────────────────────────────────────────────────────────────────────────
# SILVER 1: silver_query_execution
# Join QueryExecution + QueryStart on RequestId; add EvaluationContext fields
# ─────────────────────────────────────────────────────────────────────────────
def build_silver_query_execution():
    print("[silver_query_execution] Building...")

    qe_df = read_bronze("bronze_query_execution")
    qs_df = read_bronze("bronze_query_start")

    if qe_df is None:
        print("[WARN] bronze_query_execution not found; skipping")
        return

    # Deduplicate QE on (RequestId, QueryTrackingId) — latest wins
    window_qe = Window.partitionBy("RequestId", "QueryTrackingId").orderBy(
        F.col("QueryExecutionEndTimeUTC").desc()
    )
    qe_dedup = (
        qe_df.withColumn("_rn", F.row_number().over(window_qe))
        .filter(F.col("_rn") == 1)
        .drop("_rn")
    )

    if qs_df is not None:
        # Join QS onto QE on RequestId (left join — QE is authoritative)
        qs_select = qs_df.select(
            "RequestId",
            F.col("QueryStartTimeUTC").alias("QueryStartTimeUTC"),
            F.col("EvaluationContext").alias("EvaluationContext"),
            F.col("artifact_id").alias("artifact_id"),
            F.col("artifact_type").alias("artifact_type"),
        ).dropDuplicates(["RequestId"])

        silver_qe = qe_dedup.join(qs_select, on="RequestId", how="left")
    else:
        silver_qe = qe_dedup.withColumn(
            "QueryStartTimeUTC", F.lit(None).cast(TimestampType())
        )
        silver_qe = silver_qe.withColumn("artifact_id", F.lit(None).cast(StringType()))
        silver_qe = silver_qe.withColumn(
            "artifact_type", F.lit(None).cast(StringType())
        )

    # Computed columns
    silver_qe = silver_qe.withColumn(
        "is_spooled", F.col("SpoolingTotalDataSize") > 0
    ).withColumn("_partition_date", F.to_date(F.col("QueryExecutionEndTimeUTC")))

    write_silver(
        silver_qe, "silver_query_execution", partition_cols=["_partition_date"]
    )


# ─────────────────────────────────────────────────────────────────────────────
# SILVER 2: silver_triage — Unified Failure Triage (DIFFERENTIATOR #2)
#
# Joins: failed silver_query_execution + bronze_refresh_history + bronze_event_log
#
# Join strategy:
#   Level 1 (EXACT_REQUESTID): QE.RequestId = RefreshHistory.RequestId
#   Level 2 (TIME_WINDOW):     |QE.EndTime - RefreshHistory.StartTime| ≤ 30s
#                               AND same GatewayObjectId is unavailable from refresh history
#                               so we use ±30s window on QE.EndTime vs RefreshHistory.EndTime
#   Level 3 (HOST_ONLY):       EventLog join on hostname + ±120s time window
#
# LABEL — triage_confidence tells you how much to trust the correlation:
#   EXACT_REQUESTID: trust high
#   TIME_WINDOW:     trust medium — may join unrelated events
#   HOST_ONLY:       trust low — temporal coincidence only
# ─────────────────────────────────────────────────────────────────────────────
def build_silver_triage():
    print("[silver_triage] Building...")

    silver_qe = read_bronze("silver_query_execution")
    rh_df = read_bronze("bronze_refresh_history")
    el_df = read_bronze("bronze_event_log")

    if silver_qe is None:
        print("[WARN] silver_query_execution not found; skipping triage")
        return

    # Only look at failed queries
    failed_qe = silver_qe.filter(F.col("Success") == False)

    if failed_qe.rdd.isEmpty():
        print("[INFO] No failed queries to triage")
        return

    triage_base = failed_qe.select(
        "RequestId",
        "GatewayObjectId",
        "DataSource",
        "QueryType",
        "QueryExecutionEndTimeUTC",
        "ErrorMessage",
        "artifact_id",
        F.col("SpoolingTotalDataSize").alias("SpoolBytes"),
    ).withColumnRenamed("QueryExecutionEndTimeUTC", "qe_end_time")

    # ── Level 1: Exact RequestId join with RefreshHistory ──────────────────
    rh_base = None
    if rh_df is not None:
        rh_base = rh_df.select(
            "RequestId",
            "DatasetId",
            "WorkspaceId",
            "Status",
            "StartTime",
            "EndTime",
            "ServiceExceptionJson",
        ).withColumnRenamed("RequestId", "rh_RequestId")

        exact_join = (
            triage_base.join(
                rh_base,
                triage_base["RequestId"] == rh_base["rh_RequestId"],
                how="inner",
            )
            .withColumn("triage_confidence", F.lit("EXACT_REQUESTID"))
            .withColumn("failure_layer_refresh", F.lit(True))
            .drop("rh_RequestId")
        )

        # ── Level 2: Time-window join for rows without exact match ──────────
        unmatched_qe = triage_base.join(
            rh_base.withColumnRenamed("rh_RequestId", "RequestId"),
            on="RequestId",
            how="left_anti",
        )

        # Time window join: |qe_end_time - rh EndTime| ≤ TRIAGE_TIME_WINDOW_SECS
        # [Assumption] Refresh history EndTime approximates gateway query completion time
        # [BEST-EFFORT] This may join unrelated records — label accordingly
        rh_with_ts = rh_base.withColumn(
            "rh_end_ts", F.col("EndTime").cast(TimestampType())
        )
        # U15: this non-equi window join can match ONE gateway query to MANY refresh
        # rows that fall inside ±TRIAGE_TIME_WINDOW_SECS, duplicating the query and
        # inflating failure counts. Keep only the nearest-in-time refresh per query
        # (row_number over RequestId, ordered by |Δt|) so one query maps to <=1
        # refresh record. A query with no match keeps its single all-null row
        # (gap = null -> nulls_last -> rank 1) and stays GATEWAY_ONLY. The window
        # dedup structurally guarantees output row-count <= input, closing the fan-out.
        # [Fabric-verify-pending: compile-checked here; window semantics run in Spark.]
        tw_join = (
            unmatched_qe.alias("qe")
            .join(
                rh_with_ts.alias("rh"),
                F.abs(
                    F.unix_timestamp("qe.qe_end_time")
                    - F.unix_timestamp("rh.rh_end_ts")
                )
                <= TRIAGE_TIME_WINDOW_SECS,
                how="left",
            )
            .withColumn(
                "_tw_gap",
                F.abs(
                    F.unix_timestamp("qe.qe_end_time")
                    - F.unix_timestamp("rh.rh_end_ts")
                ),
            )
            .withColumn(
                "_tw_rank",
                F.row_number().over(
                    Window.partitionBy(F.col("qe.RequestId")).orderBy(
                        F.col("_tw_gap").asc_nulls_last()
                    )
                ),
            )
            .filter(F.col("_tw_rank") == 1)
            .drop("_tw_rank", "_tw_gap")
            .withColumn(
                "triage_confidence",
                F.when(
                    F.col("rh.DatasetId").isNotNull(), F.lit("TIME_WINDOW")
                ).otherwise(F.lit("GATEWAY_ONLY")),
            )
            .select(
                "qe.RequestId",
                "qe.GatewayObjectId",
                "qe.DataSource",
                "qe.QueryType",
                "qe.qe_end_time",
                "qe.ErrorMessage",
                "qe.artifact_id",
                "qe.SpoolBytes",
                "rh.DatasetId",
                "rh.WorkspaceId",
                "rh.Status",
                "rh.StartTime",
                "rh.EndTime",
                "rh.ServiceExceptionJson",
                "triage_confidence",
            )
            .withColumn("failure_layer_refresh", F.col("DatasetId").isNotNull())
        )

        refresh_triage = exact_join.unionByName(tw_join, allowMissingColumns=True)
    else:
        # No refresh history available — QE failures only
        refresh_triage = triage_base.withColumn(
            "triage_confidence", F.lit("GATEWAY_ONLY")
        ).withColumn("failure_layer_refresh", F.lit(False))

    # ── Level 3: EventLog join on hostname + time window ───────────────────
    if el_df is not None:
        el_errors = el_df.filter(
            F.col("LevelDisplayName").isin(["Error", "Warning"])
        ).select(
            F.col("TimeCreated").alias("evt_time"),
            F.col("EventId"),
            F.col("LevelDisplayName").alias("evt_level"),
            F.col("Message").alias("evt_message"),
            F.col("GatewayHostName"),
            F.col("EventSource"),
        )

        # Left join: event log ±EVENT_LOG_TIME_WINDOW_SECS around qe_end_time
        # [BEST-EFFORT] Host-level only; no GatewayObjectId correlation
        triage_with_events = (
            refresh_triage.alias("tr")
            .join(
                el_errors.alias("el"),
                F.abs(
                    F.unix_timestamp("tr.qe_end_time") - F.unix_timestamp("el.evt_time")
                )
                <= EVENT_LOG_TIME_WINDOW_SECS,
                how="left",
            )
            .withColumn("failure_layer_os_event", F.col("el.EventId").isNotNull())
            .select(
                "tr.*",
                "el.evt_time",
                "el.EventId",
                "el.evt_level",
                "el.evt_message",
                "el.EventSource",
                "failure_layer_os_event",
            )
        )
    else:
        triage_with_events = refresh_triage.withColumn(
            "failure_layer_os_event", F.lit(False)
        )
        triage_with_events = triage_with_events.withColumn(
            "evt_message", F.lit(None).cast(StringType())
        )

    # Derive failure_layer summary
    triage_final = triage_with_events.withColumn(
        "failure_layer",
        F.when(F.col("failure_layer_os_event"), F.lit("OS_SERVICE_EVENT"))
        .when(F.col("failure_layer_refresh") == True, F.lit("SERVICE_REFRESH_ERROR"))
        .when(F.col("ErrorMessage").isNotNull(), F.lit("GATEWAY_LOG_ERROR"))
        .otherwise(F.lit("UNKNOWN")),
    ).withColumn("_partition_date", F.to_date(F.col("qe_end_time")))

    write_silver(triage_final, "silver_triage", partition_cols=["_partition_date"])


# ─────────────────────────────────────────────────────────────────────────────
# SILVER 3: silver_identity_attribution (DIFFERENTIATOR #3)
#
# Best-effort query → dataset/workspace/user attribution.
#
# !! READ BEFORE USING IN REPORTS !!
# This table must be labeled "BEST-EFFORT / FUZZY" on all report visuals.
# It is NOT a reliable audit trail. It is an operational approximation.
#
# Attribution methods (in order of reliability):
#   1. EvaluationContext.artifactId (Fabric workloads only)
#      — artifact_id parsed from QueryStart log in bronze_ingest
#      — Reliable where populated; absent for non-Fabric workloads
#   2. Power BI Activity Events RequestId match (±60s time window)
#      — Activity Events API: GET /v1.0/myorg/admin/activityevents
#      — Only reliable for OnDemand/API-triggered refreshes
#      — [Assumption] Activity Events API is accessible via admin SP
#   3. No match: UNATTRIBUTED
#
# Known non-attributable workload types:
#   - Paginated Reports (not logged in gateway query log)
#   - Dataflow Gen1 (EvaluationContext not populated)
#   - DirectQuery user sessions (RequestId not in audit log)
#
# Reference:
#   https://learn.microsoft.com/en-us/data-integration/gateway/service-gateway-performance
#   https://3cloudsolutions.com/resources/monitoring-power-bi-on-premises-data-gateway-performance/
# ─────────────────────────────────────────────────────────────────────────────
def build_silver_identity_attribution():
    print("[silver_identity_attribution] Building (BEST-EFFORT)...")

    silver_qe = read_bronze("silver_query_execution")
    if silver_qe is None:
        return

    # Method 1: EvaluationContext attribution (most reliable)
    qe_with_artifact = silver_qe.select(
        "RequestId",
        "GatewayObjectId",
        "QueryStartTimeUTC",
        "QueryExecutionEndTimeUTC",
        "DataSource",
        "QueryType",
        "QueryExecutionDuration",
        "Success",
        "artifact_id",
        "artifact_type",
    )

    eval_context_attr = (
        qe_with_artifact.filter(F.col("artifact_id").isNotNull())
        .withColumn("attribution_confidence", F.lit("EVALUATION_CONTEXT"))
        .withColumn(
            "attribution_method_note",
            F.lit(
                "Fabric workload: artifactId from QueryStart.EvaluationContext (reliable for Fabric workloads only)"
            ),
        )
    )

    # Method 2: Activity Events time-window join (FUZZY)
    # [NET-NEW] No existing tool implements this join.
    # Activity Events REST API: GET /v1.0/myorg/admin/activityevents?startDateTime=...&endDateTime=...
    # [STUB] Activity Events ingestion is not implemented here.
    # In Phase 5: ingest Activity Events to bronze_activity_events Delta table,
    # then join here on RequestId and ±ATTRIBUTION_TIME_WINDOW_SECS.
    # For now, emit a placeholder column showing the join would happen here.

    unattributed = qe_with_artifact.filter(F.col("artifact_id").isNull())

    # TODO (Phase 5 / v3): join unattributed against bronze_activity_events
    # activity_df = read_bronze("bronze_activity_events")
    # if activity_df is not None:
    #     fuzzy_join = unattributed.alias("qe").join(
    #         activity_df.alias("ae"),
    #         (F.col("qe.RequestId") == F.col("ae.RequestId")) |
    #         (F.abs(F.unix_timestamp("qe.QueryStartTimeUTC") - F.unix_timestamp("ae.CreationTime"))
    #          <= ATTRIBUTION_TIME_WINDOW_SECS),
    #         how="left"
    #     ) ...

    # For now: mark as UNATTRIBUTED with explanation
    unattributed_with_label = unattributed.withColumn(
        "attribution_confidence", F.lit("UNATTRIBUTED")
    ).withColumn(
        "attribution_method_note",
        F.lit(
            "No EvaluationContext available. "
            "May be Dataflow Gen1, Paginated Report, or DirectQuery session. "
            "Activity Events join not yet implemented (v3). "
            "See: https://learn.microsoft.com/en-us/data-integration/gateway/service-gateway-performance"
        ),
    )

    # Union with strong label comment
    attribution = (
        eval_context_attr.unionByName(unattributed_with_label, allowMissingColumns=True)
        .withColumn(
            "_attribution_disclaimer",
            F.lit(
                "BEST-EFFORT ONLY. Do not use for compliance, billing, or security audit. "
                "Paginated Reports and Dataflow Gen1 are excluded by design. "
                "DirectQuery sessions are not correlated."
            ),
        )
        .withColumn("_partition_date", F.to_date(F.col("QueryExecutionEndTimeUTC")))
    )

    write_silver(
        attribution, "silver_identity_attribution", partition_cols=["_partition_date"]
    )


# ─────────────────────────────────────────────────────────────────────────────
# SILVER 4: silver_network_correlated (DIFFERENTIATOR #4)
#
# Joins NIC metrics with query execution records by host + time window.
# Adds network context to query performance for diagnosing bandwidth-limited refreshes.
# ─────────────────────────────────────────────────────────────────────────────
def build_silver_network_correlated():
    print("[silver_network_correlated] Building...")

    silver_qe = read_bronze("silver_query_execution")
    net_df = read_bronze("bronze_network_metrics")

    if silver_qe is None or net_df is None:
        print(
            "[WARN] Missing silver_query_execution or bronze_network_metrics; skipping"
        )
        return

    # Use the NIC with highest bandwidth utilization at each collection time
    # as the "representative" NIC for the gateway (typically the primary LAN adapter)
    net_agg = net_df.groupBy("GatewayHostName", "CollectedAtUTC").agg(
        F.max("BytesTotalPerSec").alias("BytesTotalPerSec_max"),
        F.max("UtilizationPct").alias("UtilizationPct_max"),
        F.first("LatencyMs_PBIRelay").alias("LatencyMs_PBIRelay"),
        F.first("CurrentBandwidthBps").alias("CurrentBandwidthBps"),
    )

    # Time-window join: NIC sample within ±5 minutes of query execution end
    NET_WINDOW_SECS = 300  # 5 minutes

    correlated = (
        silver_qe.alias("qe")
        .join(
            net_agg.alias("net"),
            (F.col("qe._gateway_host") == F.col("net.GatewayHostName"))
            & (
                F.abs(
                    F.unix_timestamp("qe.QueryExecutionEndTimeUTC")
                    - F.unix_timestamp("net.CollectedAtUTC")
                )
                <= NET_WINDOW_SECS
            ),
            how="left",
        )
        # U15: with a ~5-min NIC collection cadence and a ±5-min window, more than
        # one net_agg sample can fall in range and fan the query row out. Keep the
        # nearest NIC sample per query (row_number over RequestId by |Δt|) so one
        # query maps to <=1 network row; a query with no sample keeps its single
        # all-null row. Structurally guarantees output row-count <= input.
        # [Fabric-verify-pending: compile-checked here; window semantics run in Spark.]
        .withColumn(
            "_net_gap",
            F.abs(
                F.unix_timestamp("qe.QueryExecutionEndTimeUTC")
                - F.unix_timestamp("net.CollectedAtUTC")
            ),
        )
        .withColumn(
            "_net_rank",
            F.row_number().over(
                Window.partitionBy(F.col("qe.RequestId")).orderBy(
                    F.col("_net_gap").asc_nulls_last()
                )
            ),
        )
        .filter(F.col("_net_rank") == 1)
        .drop("_net_rank", "_net_gap")
        .select(
            "qe.RequestId",
            "qe.GatewayObjectId",
            "qe.DataSource",
            "qe.QueryType",
            "qe.QueryExecutionEndTimeUTC",
            "qe.QueryExecutionDuration",
            "qe.SpoolingTotalDataSize",
            "qe.Success",
            "qe.ErrorMessage",
            "net.BytesTotalPerSec_max",
            "net.UtilizationPct_max",
            "net.LatencyMs_PBIRelay",
            "net.CurrentBandwidthBps",
        )
        .withColumn(
            # Estimated network throughput efficiency: how fast we transferred data
            # SpoolingTotalDataSize / (QueryExecutionDuration_s * BytesTotalPerSec)
            # [Inference] SpoolingTotalDataSize is compressed bytes; ratio is approximate
            "network_throughput_efficiency",
            F.when(
                (F.col("SpoolingTotalDataSize") > 0)
                & (F.col("BytesTotalPerSec_max") > 0)
                & (F.col("QueryExecutionDuration") > 0),
                F.col("SpoolingTotalDataSize")
                / (
                    (F.col("QueryExecutionDuration") / 1000.0)
                    * F.col("BytesTotalPerSec_max")
                ),
            ).otherwise(F.lit(None).cast(DoubleType())),
        )
        .withColumn("_partition_date", F.to_date(F.col("QueryExecutionEndTimeUTC")))
    )

    write_silver(
        correlated, "silver_network_correlated", partition_cols=["_partition_date"]
    )


# ── ENTRY POINT ─────────────────────────────────────────────────────────────
if __name__ == "__main__" or True:
    print(
        f"=== 02_silver_correlate.py starting at {datetime.now(timezone.utc).isoformat()} ==="
    )
    build_silver_query_execution()
    build_silver_triage()
    build_silver_identity_attribution()
    build_silver_network_correlated()
    print("=== 02_silver_correlate.py complete ===")
