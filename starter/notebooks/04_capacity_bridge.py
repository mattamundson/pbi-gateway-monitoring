# =============================================================================
# 04_capacity_bridge.py  |  Label: [NET-NEW]
#
# CAPACITY METRICS BRIDGE -> gold_capacities (feeds Tenant Overview CU cards and
# the [CU Consumption (period)] / [CU MoM %] measures in measures.dax).
#
# WHY A BRIDGE, NOT A DIRECT COLLECTOR (headwind — read this):
#   Microsoft does NOT expose per-capacity CU consumption via a clean public REST
#   endpoint. CU lives in the **Fabric Capacity Metrics app** (a semantic model,
#   `Microsoft Fabric Capacity Metrics`) or, if you run Microsoft's Fabric Platform
#   Monitoring accelerator, in its Eventhouse. So we BRIDGE from one of:
#     mode = "capacity_metrics_xmla" : query the Capacity Metrics semantic model via
#            the XMLA endpoint with a DAX query (Premium/Fabric; XMLA read required).
#     mode = "fpm_eventhouse"        : read FPM's CapacityUtilizationEvents exposed as
#            Delta via Eventhouse OneLake availability (config.fpmBridge.eventhouseDeltaPath).
#     mode = "mock"                  : deterministic synthetic CU (CI/local; no live data).
#   The bridge is config-driven (config.capacityBridge.mode). Until one live mode is
#   wired, gold_capacities is empty and the CU cards render blank (by design, not error).
#
# [Unverified] Both live modes require a live tenant + the Capacity Metrics app or
#   FPM deployed. The XMLA DAX text below is a starting query; column names in the
#   Capacity Metrics model change between app versions and MUST be confirmed live.
#
# Refs:
#   Capacity Metrics app: https://learn.microsoft.com/en-us/fabric/enterprise/metrics-app
#   XMLA endpoint:        https://learn.microsoft.com/en-us/power-bi/enterprise/service-premium-connect-tools
#   FPM CapacityUtilizationEvents:
#     https://github.com/microsoft/fabric-toolbox/tree/main/monitoring/fabric-platform-monitoring
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

GOLD_PATH = os.getenv("GOLD_LANDING_PATH", "/tmp/nexus_lake/gold")

# Starting DAX for the XMLA bridge — CONFIRM column names against your app version.
CAPACITY_METRICS_DAX = """
EVALUATE
SUMMARIZECOLUMNS(
    'Capacities'[Capacity Id],
    'Capacities'[Capacity Name],
    'Capacities'[Region],
    'Capacities'[SKU],
    'TimePoints'[Date],
    "CU_s", SUM('MetricsByItemAndOperation'[CU (s)])
)
"""


# ---------------------------------------------------------------------------
# Bridge modes
# ---------------------------------------------------------------------------
def bridge_mock() -> list[dict]:
    today = dt.date.today()
    regions = {"cap-eastus": ("Finance Capacity", "East US", "F16"),
               "cap-westus": ("Ops Capacity", "West US", "F8")}
    rows = []
    for cid, (name, region, sku) in regions.items():
        for d in range(30):
            day = today - dt.timedelta(days=d)
            base = 1_800_000 if sku == "F16" else 900_000
            rows.append({
                "CapacityId": cid, "CapacityName": name, "Region": region, "Sku": sku,
                "cu_seconds": float(base + (d % 7) * 50_000),
                "_partition_date": day.isoformat(),
            })
    return rows


def bridge_fpm_eventhouse(delta_path: str) -> list[dict]:
    """Read FPM CapacityUtilizationEvents (Delta via Eventhouse OneLake availability)."""
    if not _SPARK:
        raise RuntimeError("fpm_eventhouse mode requires Spark")
    df = spark.read.format("delta").load(delta_path)
    # [Unverified] column names per FPM schema; adjust to match your deployment.
    agg = (df.groupBy("CapacityId", "CapacityName", "Region", "Sku", "Date")
             .sum("CuSeconds").withColumnRenamed("sum(CuSeconds)", "cu_seconds"))
    return [r.asDict() for r in agg.collect()]


def bridge_capacity_metrics_xmla(dataset_id: str, token: str, group_id: str = "",
                                 dax: str = "") -> list[dict]:
    """Query the Fabric Capacity Metrics semantic model with a DAX query via the
    Power BI REST **executeQueries** endpoint (no XMLA client library needed — works
    from any Fabric notebook over HTTPS with a bearer token).

    POST /v1.0/myorg[/groups/{groupId}]/datasets/{datasetId}/executeQueries
      body: {"queries":[{"query": <DAX>}], "serializerSettings":{"includeNulls":true}}
    Response: results[0].tables[0].rows -> list of {"Col[Name]": value} dicts.
    Execute Queries caps: 1 query/call, 100k rows, 120 req/min.
    Docs: https://learn.microsoft.com/en-us/rest/api/power-bi/datasets/execute-queries

    [Unverified] Requires: dataset build/read permission on the Capacity Metrics model,
    the 'Dataset Execute Queries REST API' tenant setting ON, and XMLA read-write on the
    capacity hosting the model. The DAX column names below follow the current app schema
    and MUST be confirmed live (they change between Metrics-app versions).
    """
    if not _REQUESTS:
        raise RuntimeError("capacity_metrics_xmla mode requires the 'requests' package")
    if not dataset_id or not token:
        raise ValueError("capacity_metrics_xmla needs bridge.datasetId and bridge.token")

    base = "https://api.powerbi.com/v1.0/myorg"
    scope = f"{base}/groups/{group_id}" if group_id else base
    url = f"{scope}/datasets/{dataset_id}/executeQueries"
    body = {"queries": [{"query": dax or CAPACITY_METRICS_DAX}],
            "serializerSettings": {"includeNulls": True}}

    r = requests.post(url, headers={"Authorization": f"Bearer {token}",
                                    "Content-Type": "application/json"},
                      json=body, timeout=120)
    r.raise_for_status()
    raw_rows = r.json()["results"][0]["tables"][0].get("rows", [])

    # executeQueries returns keys like "Capacities[Capacity Id]" / "[CU_s]".
    # Normalize to the gold_capacities schema. Map by trailing name, tolerant of
    # table-prefix and bracket differences so a column rename doesn't hard-fail.
    def pick(row, *names):
        for k, v in row.items():
            leaf = k.split("[")[-1].rstrip("]").strip().lower()
            if leaf in names:
                return v
        return None

    out: list[dict] = []
    for row in raw_rows:
        out.append({
            "CapacityId": pick(row, "capacity id", "capacityid", "capacity_id"),
            "CapacityName": pick(row, "capacity name", "capacityname"),
            "Region": pick(row, "region"),
            "Sku": pick(row, "sku"),
            "cu_seconds": pick(row, "cu_s", "cu (s)", "cu seconds", "cu_seconds"),
            "_partition_date": pick(row, "date"),
        })
    print(f"[capacity_metrics_xmla] executeQueries returned {len(out)} rows")
    return out


# ---------------------------------------------------------------------------
def _write_gold(rows: list[dict], table: str = "gold_capacities") -> int:
    if _SPARK and not os.getenv("FORCE_JSON_LANDING"):
        if not rows:
            print(f"[gold] {table}: 0 rows (CU cards render empty until a live bridge is wired)")
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


def run(config: dict | None = None) -> int:
    config = config or {}
    bridge = (config.get("capacityBridge") or {})
    mode = os.getenv("CAPACITY_BRIDGE_MODE", bridge.get("mode", "mock"))
    print(f"[04_capacity_bridge] mode={mode}")
    if mode == "mock":
        rows = bridge_mock()
    elif mode == "fpm_eventhouse":
        rows = bridge_fpm_eventhouse(bridge.get("eventhouseDeltaPath", ""))
    elif mode == "capacity_metrics_xmla":
        rows = bridge_capacity_metrics_xmla(
            dataset_id=bridge.get("datasetId", ""),
            token=bridge.get("token", "") or os.getenv("PBI_ACCESS_TOKEN", ""),
            group_id=bridge.get("groupId", ""),
            dax=bridge.get("dax", ""))
    else:
        raise ValueError(f"Unknown capacity bridge mode: {mode}")
    return _write_gold(rows)


if __name__ == "__main__":
    cfg = {}
    cfg_path = os.getenv("CONFIG_PATH", "")
    if cfg_path and os.path.exists(cfg_path):
        cfg = json.load(open(cfg_path))
    run(cfg)
