#!/usr/bin/env python3
"""
build_dataset_tmdl.py  |  Label: [NET-NEW]
Generates the TMDL semantic-model folder Nexus_Gateway_Observatory.Dataset/ that
definition.pbir points to. DirectLake over OneLake gold/silver tables.

TMDL (Tabular Model Definition Language) is Microsoft's text format for semantic
models — human-editable, Git-friendly, and the format Power BI Desktop (Sept 2024+)
reads from a PBIP project. Column lists mirror the gold notebook output; measures
are referenced from starter/semantic-model/measures.dax (Desktop imports them).

[Unverified] Must be opened in Desktop to bind DirectLake to the deployed Lakehouse
and to mark 'Date' as a date table. expressions/partitions here are DirectLake stubs.
"""
import pathlib

ROOT = pathlib.Path(__file__).resolve().parent
DS = ROOT / "Nexus_Gateway_Observatory.Dataset"
DEF = DS / "definition"
TABLES = DEF / "tables"

# table -> list of (column, dataType)
SCHEMA = {
    "gold_gateway_health": [
        ("GatewayObjectId", "string"), ("last_heartbeat_utc", "dateTime"),
        ("heartbeat_age_minutes", "int64"), ("cpu_pct_avg", "double"),
        ("system_cpu_avg", "double"), ("mem_kb_avg", "double"),
        ("spool_free_pct", "double"), ("network_utilization_pct_avg", "double"),
        ("latency_ms_p95", "double"), ("error_rate_pct", "double"),
        ("_partition_date", "dateTime"),
    ],
    "gold_query_performance": [
        ("hour_utc", "dateTime"), ("DataSource", "string"), ("QueryType", "string"),
        ("query_count", "int64"), ("error_count", "int64"), ("error_rate_pct", "double"),
        ("duration_avg_ms", "double"), ("duration_p95_ms", "double"), ("duration_max_ms", "double"),
        ("spool_total_bytes_sum", "int64"), ("spool_writing_ms_avg", "double"),
        ("spooled_pct", "double"), ("artifact_id", "string"),
    ],
    "gold_cluster_load": [
        ("GatewayClusterName", "string"), ("queries_per_node_cv", "double"),
        ("hottest_node_query_pct", "double"), ("node_count", "int64"),
    ],
    "gold_dim_gateway": [
        ("GatewayObjectId", "string"), ("GatewayClusterId", "string"),
        ("GatewayClusterName", "string"), ("GatewayNodeName", "string"),
        ("status_current", "string"), ("Version", "string"),
    ],
    "silver_triage": [
        ("DataSource", "string"), ("ErrorMessage", "string"), ("failure_layer", "string"),
        ("triage_confidence", "string"), ("failure_layer_os_event", "boolean"),
    ],
    "silver_identity_attribution": [
        ("RequestId", "string"), ("artifact_id", "string"),
        ("attribution_confidence", "string"), ("ExecutingUser", "string"), ("ItemName", "string"),
    ],
    "gold_mashup_health": [
        ("GatewayObjectId", "string"), ("HostName", "string"),
        ("peak_working_set_mb", "double"), ("avg_working_set_mb", "double"),
        ("runaway_container", "boolean"),
    ],
    # ---- NEW tenant-breadth tables (render empty until tenant-extract pipeline) ----
    "gold_gateway_datasources": [
        ("DatasourceId", "string"), ("DatasourceName", "string"), ("DatasourceType", "string"),
        ("GatewayClusterName", "string"), ("Status", "string"), ("LastUpdated", "dateTime"),
    ],
    "gold_capacities": [
        ("CapacityId", "string"), ("CapacityName", "string"), ("Region", "string"),
        ("Sku", "string"), ("cu_seconds", "double"), ("_partition_date", "dateTime"),
    ],
    "gold_activities": [
        ("ActivityId", "string"), ("UserId", "string"), ("Operation", "string"),
        ("WorkspaceId", "string"), ("CapacityId", "string"), ("ActivityDate", "dateTime"),
    ],
    "gold_inventory": [
        ("ItemId", "string"), ("ItemName", "string"), ("ItemKind", "string"),
        ("WorkspaceId", "string"), ("WorkspaceName", "string"), ("CapacityId", "string"),
    ],
    "gold_refreshables": [
        ("DatasetId", "string"), ("DatasetName", "string"), ("WorkspaceId", "string"),
        ("Status", "string"), ("duration_seconds", "double"), ("RefreshDate", "dateTime"),
    ],
}

RELATIONSHIPS = [
    ("gold_dim_gateway", "GatewayObjectId", "gold_gateway_health", "GatewayObjectId"),
    ("gold_dim_gateway", "GatewayObjectId", "gold_mashup_health", "GatewayObjectId"),
    ("Date", "Date", "gold_query_performance", "hour_utc"),
    ("Date", "Date", "gold_activities", "ActivityDate"),
    ("Date", "Date", "gold_refreshables", "RefreshDate"),
    ("Date", "Date", "gold_capacities", "_partition_date"),
]


def col_block(name, dtype, is_key=False):
    src = name
    extra = "\n\t\tisKey: true" if is_key else ""
    return (f"\tcolumn '{name}'\n"
            f"\t\tdataType: {dtype}\n"
            f"\t\tsourceColumn: {src}{extra}\n"
            f"\t\tsummarizeBy: none\n")


def table_tmdl(tname, cols):
    lines = [f"table {tname}\n"]
    for c, dt in cols:
        lines.append(col_block(c, dt))
        lines.append("\n")
    # DirectLake partition stub (entity name = the OneLake gold table)
    lines.append(
        f"\tpartition {tname} = entity\n"
        f"\t\tmode: directLake\n"
        f"\t\tsource\n"
        f"\t\t\tentityName: {tname}\n"
        f"\t\t\texpressionSource: DatabaseQuery\n"
    )
    return "".join(lines)


def date_table_tmdl():
    return (
        "table Date\n"
        "\tdataCategory: Time\n\n"
        "\tcolumn Date\n"
        "\t\tdataType: dateTime\n"
        "\t\tisKey: true\n"
        "\t\tsourceColumn: Date\n"
        "\t\tsummarizeBy: none\n\n"
        "\tcolumn Year\n\t\tdataType: int64\n\t\tsourceColumn: Year\n\t\tsummarizeBy: none\n\n"
        "\tcolumn Month\n\t\tdataType: string\n\t\tsourceColumn: Month\n\t\tsummarizeBy: none\n\n"
        "\tcolumn 'Year-Month'\n\t\tdataType: string\n\t\tsourceColumn: Year-Month\n\t\tsummarizeBy: none\n\n"
        "\t// [Unverified] Mark-as-date-table + calculated Date range must be set in Desktop.\n"
        "\tpartition Date = calculated\n"
        "\t\tmode: import\n"
        "\t\tsource = Calendar(DATE(2024,1,1), DATE(2026,12,31))\n"
    )


def main():
    TABLES.mkdir(parents=True, exist_ok=True)

    # model.tmdl
    (DEF / "model.tmdl").write_text(
        "model Model\n"
        "\tculture: en-US\n"
        "\tdefaultPowerBIDataSourceVersion: powerBI_V3\n"
        "\tdiscourageImplicitMeasures\n\n"
        "\t// Measures live in ../../starter/semantic-model/measures.dax — import via\n"
        "\t// Desktop/Tabular Editor, or paste into a measures table on first open.\n"
    )
    # database.tmdl
    (DEF / "database.tmdl").write_text(
        "database\n\tcompatibilityLevel: 1601\n"
    )
    # relationships.tmdl
    rel_lines = []
    for i, (ft, fc, tt, tc) in enumerate(RELATIONSHIPS, 1):
        rel_lines.append(
            f"relationship rel{i}\n"
            f"\tfromColumn: {tt}.{tc}\n"
            f"\ttoColumn: {ft}.{fc}\n"
            f"\tcrossFilteringBehavior: oneDirection\n\n"
        )
    (DEF / "relationships.tmdl").write_text("".join(rel_lines))

    # tables
    for tname, cols in SCHEMA.items():
        (TABLES / f"{tname}.tmdl").write_text(table_tmdl(tname, cols))
    (TABLES / "Date.tmdl").write_text(date_table_tmdl())

    # dataset-level pbip metadata
    (DS / "definition.pbidataset").write_text(
        '{\n  "$schema": "https://developer.microsoft.com/json-schemas/fabric/gitIntegration/pbidataset/1.0.0/schema.json",\n'
        '  "version": "4.2"\n}\n'
    )
    print(f"TMDL model: {len(SCHEMA)+1} tables (incl. Date), {len(RELATIONSHIPS)} relationships -> {DS}")


if __name__ == "__main__":
    main()
