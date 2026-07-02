# =============================================================================
# 01a_tenant_silver_gold.py  |  Label: [NET-NEW]
#
# Conforms the tenant-breadth bronze (from 00_tenant_extract.py) into the GOLD
# tables the enhanced "Nexus" report + measures.dax bind to:
#   gold_inventory     <- bronze_scanner_result   (flatten workspaces -> items)
#   gold_activities    <- bronze_activity_events
#   gold_refreshables  <- bronze_refreshables
#   gold_capacities    <- Capacity Metrics BRIDGE (see 04_capacity_bridge below)
#
# Pure-Python core (transform_* fns) so it is unit-testable off-cluster; a thin
# Spark wrapper writes Delta on a Fabric cluster. Reads the JSON landing written
# by 00_tenant_extract in MOCK/CI, or Delta bronze tables on cluster.
#
# [Unverified] Delta write path + DirectLake exposure need a live Lakehouse.
# =============================================================================
from __future__ import annotations

import json
import os
import datetime as dt

try:
    from pyspark.sql import SparkSession
    spark = SparkSession.builder.getOrCreate()
    _SPARK = True
except Exception:  # noqa: BLE001
    spark = None
    _SPARK = False

LANDING_PATH = os.getenv("BRONZE_LANDING_PATH", "/tmp/nexus_lake/bronze")
GOLD_PATH = os.getenv("GOLD_LANDING_PATH", "/tmp/nexus_lake/gold")

# Item kinds we surface as KPI cards on Tenant Overview (align with measures.dax)
ITEM_KEYS = [
    ("reports", "Report"), ("datasets", "SemanticModel"), ("dataflows", "Dataflow"),
    ("dashboards", "Dashboard"), ("notebooks", "Notebook"), ("pipelines", "DataPipeline"),
    ("lakehouses", "Lakehouse"), ("eventhouses", "Eventhouse"), ("warehouses", "Warehouse"),
]


# ---------------------------------------------------------------------------
# Pure-Python transforms (testable; no Spark required)
# ---------------------------------------------------------------------------
def transform_inventory(scan_results: list[dict]) -> list[dict]:
    """Flatten Scanner workspaces -> one row per item (gold_inventory)."""
    rows: list[dict] = []
    for blob in scan_results:
        for ws in blob.get("workspaces", []):
            wsid, wsname = ws.get("id"), ws.get("name")
            capid = ws.get("capacityId")
            for key, kind in ITEM_KEYS:
                for item in ws.get(key, []) or []:
                    rows.append({
                        "ItemId": item.get("id"),
                        "ItemName": item.get("name"),
                        "ItemKind": kind,
                        "WorkspaceId": wsid,
                        "WorkspaceName": wsname,
                        "CapacityId": capid,
                    })
    return rows


def transform_activities(events: list[dict]) -> list[dict]:
    rows: list[dict] = []
    for e in events:
        rows.append({
            "ActivityId": e.get("Id"),
            "UserId": e.get("UserId"),
            "Operation": e.get("Operation"),
            "WorkspaceId": e.get("WorkspaceId"),
            "CapacityId": e.get("CapacityId"),
            "ActivityDate": (e.get("CreationTime") or "")[:19],
        })
    return rows


def transform_refreshables(refs: list[dict]) -> list[dict]:
    rows: list[dict] = []
    for r in refs:
        last = r.get("lastRefresh") or {}
        dur = _duration_seconds(last.get("startTime"), last.get("endTime"))
        rows.append({
            "DatasetId": r.get("id"),
            "DatasetName": r.get("name"),
            "WorkspaceId": (r.get("group") or {}).get("id"),
            "CapacityId": (r.get("capacity") or {}).get("id"),
            "Status": last.get("status", "Unknown"),
            "duration_seconds": dur,
            "RefreshDate": (last.get("startTime") or "")[:19],
        })
    return rows


def _duration_seconds(start: str | None, end: str | None) -> float:
    if not start or not end:
        return 0.0
    try:
        s = dt.datetime.fromisoformat(start.replace("Z", "+00:00"))
        e = dt.datetime.fromisoformat(end.replace("Z", "+00:00"))
        return max(0.0, (e - s).total_seconds())
    except Exception:  # noqa: BLE001
        return 0.0


# ---------------------------------------------------------------------------
# I/O — JSON landing (mock/CI) or Delta (cluster)
# ---------------------------------------------------------------------------
def _read_bronze_json(table: str) -> list:
    path = os.path.join(LANDING_PATH, f"{table}.json")
    if not os.path.exists(path):
        print(f"[01a] bronze missing: {path} -> treating as empty")
        return []
    return json.load(open(path))


def _write_gold(rows: list[dict], table: str) -> int:
    if _SPARK and not os.getenv("FORCE_JSON_LANDING"):
        if not rows:
            print(f"[gold] {table}: 0 rows (table still defined; page renders empty)")
            return 0
        df = spark.createDataFrame(rows)
        df.write.format("delta").mode("overwrite").option("mergeSchema", "true").saveAsTable(table)
        print(f"[gold] {table}: wrote {len(rows)} rows (delta)")
        return len(rows)
    os.makedirs(GOLD_PATH, exist_ok=True)
    with open(os.path.join(GOLD_PATH, f"{table}.json"), "w") as fh:
        json.dump(rows, fh, indent=2)
    print(f"[gold] {table}: wrote {len(rows)} rows -> {GOLD_PATH}/{table}.json")
    return len(rows)


def run() -> dict:
    inv = transform_inventory(_read_bronze_json("bronze_scanner_result"))
    act = transform_activities(_read_bronze_json("bronze_activity_events"))
    ref = transform_refreshables(_read_bronze_json("bronze_refreshables"))
    counts = {
        "gold_inventory": _write_gold(inv, "gold_inventory"),
        "gold_activities": _write_gold(act, "gold_activities"),
        "gold_refreshables": _write_gold(ref, "gold_refreshables"),
    }
    print(f"[01a_tenant_silver_gold] done: {counts}")
    return counts


if __name__ == "__main__":
    run()
