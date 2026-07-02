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


def bridge_capacity_metrics_xmla(xmla_endpoint: str, dataset: str, token: str) -> list[dict]:
    """Query the Capacity Metrics semantic model via XMLA/executeQueries.
    [Unverified] Requires XMLA read + admin/dataset access; DAX columns per app version."""
    raise NotImplementedError(
        "Wire pyadomd/executeQueries against the Capacity Metrics model here. "
        "Use CAPACITY_METRICS_DAX as the starting query; confirm column names live."
    )


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
            bridge.get("xmlaEndpoint", ""), bridge.get("dataset", ""), bridge.get("token", ""))
    else:
        raise ValueError(f"Unknown capacity bridge mode: {mode}")
    return _write_gold(rows)


if __name__ == "__main__":
    cfg = {}
    cfg_path = os.getenv("CONFIG_PATH", "")
    if cfg_path and os.path.exists(cfg_path):
        cfg = json.load(open(cfg_path))
    run(cfg)
