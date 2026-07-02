#!/usr/bin/env python3
"""
test_tenant_extract.py  |  Label: [NET-NEW]  |  Spark-free (Tier 1)

Covers the tenant-breadth pipeline's pure-Python core end-to-end in MOCK mode:
  00_tenant_extract.run()  -> bronze JSON
  01a_tenant_silver_gold.run() -> gold_inventory/activities/refreshables
  04_capacity_bridge.run() -> gold_capacities
and the individual transform_* functions. No Spark, no network.
"""
import importlib.util
import json
import os
import sys
import tempfile

NB = os.path.join(os.path.dirname(__file__), "..", "notebooks")


def _load(mod_file, name):
    spec = importlib.util.spec_from_file_location(name, os.path.join(NB, mod_file))
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def main():
    tmp = tempfile.mkdtemp(prefix="nexus_test_")
    os.environ.update({
        "TENANT_EXTRACT_MOCK": "1", "FORCE_JSON_LANDING": "1", "CAPACITY_BRIDGE_MODE": "mock",
        "BRONZE_LANDING_PATH": os.path.join(tmp, "bronze"),
        "GOLD_LANDING_PATH": os.path.join(tmp, "gold"),
    })

    extract = _load("00_tenant_extract.py", "tenant_extract")
    slv = _load("01a_tenant_silver_gold.py", "tenant_silver_gold")
    bridge = _load("04_capacity_bridge.py", "capacity_bridge")

    passed = 0

    # 1. transform_inventory flattens workspaces -> items with expected kinds
    inv = slv.transform_inventory([extract._mock_scan_result()])
    assert len(inv) == 4, f"expected 4 inventory rows, got {len(inv)}"
    assert {r["ItemKind"] for r in inv} == {"Report", "SemanticModel"}, inv
    assert all({"ItemId", "ItemName", "ItemKind", "WorkspaceId", "CapacityId"} <= r.keys() for r in inv)
    print("PASS: transform_inventory"); passed += 1

    # 2. transform_activities maps fields + trims timestamp
    act = slv.transform_activities(extract._mock_activity_events())
    assert len(act) == 24 and act[0]["UserId"].endswith("@contoso.com")
    assert len(act[0]["ActivityDate"]) == 19, act[0]["ActivityDate"]
    print("PASS: transform_activities"); passed += 1

    # 3. transform_refreshables computes duration + carries status
    ref = slv.transform_refreshables(extract._mock_refreshables())
    assert len(ref) == 6
    assert any(r["Status"] == "Failed" for r in ref), "mock should include a failed refresh"
    assert all(r["duration_seconds"] >= 0 for r in ref)
    print("PASS: transform_refreshables"); passed += 1

    # 4. full chain writes all four gold tables with rows
    extract.run({})
    slv.run()
    bridge.run({})
    gold = os.environ["GOLD_LANDING_PATH"]
    expected = {"gold_inventory": 4, "gold_activities": 24, "gold_refreshables": 6}
    for t, n in expected.items():
        rows = json.load(open(os.path.join(gold, f"{t}.json")))
        assert len(rows) == n, f"{t}: expected {n}, got {len(rows)}"
    caps = json.load(open(os.path.join(gold, "gold_capacities.json")))
    assert len(caps) == 60 and caps[0]["cu_seconds"] > 0, "capacity bridge should populate CU"
    # gold_capacities schema must match the TMDL model columns
    assert {"CapacityId", "CapacityName", "Region", "Sku", "cu_seconds", "_partition_date"} <= caps[0].keys()
    print("PASS: full mock chain -> 4 gold tables"); passed += 1

    print(f"\n================================================\nRESULT: ALL {passed} TESTS PASSED")


if __name__ == "__main__":
    try:
        main()
    except AssertionError as e:
        print(f"FAIL: {e}"); sys.exit(1)
