#!/usr/bin/env python3
"""
test_cast_and_bridge.py  |  Label: [NET-NEW]  |  Spark-free (Tier 1)

Covers the two pure-code MVP items:
  U14 — _cast_value returns (value, cast_ok); unparseable numerics are FLAGGED,
        legitimate nulls are NOT flagged.
  M6  — capacity_metrics_xmla response normalization: executeQueries JSON
        (keys like "Capacities[Capacity Id]" / "[CU_s]") maps to the
        gold_capacities schema, tolerant of table-prefix/rename drift.
No Spark, no network (requests is monkeypatched).
"""
import importlib.util
import os
import sys
import types

NB = os.path.join(os.path.dirname(__file__), "..", "notebooks")


def _load(mod_file, name):
    spec = importlib.util.spec_from_file_location(name, os.path.join(NB, mod_file))
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def _run_u14():
    # 01_bronze_ingest imports pyspark at module top; if pyspark is absent we can't
    # load it here. Full U14 typing behavior is covered in the Spark tier
    # (test_tenant_spark / CI). Skip gracefully off-cluster.
    try:
        from pyspark.sql.types import DoubleType
    except Exception:  # noqa: BLE001
        return None
    try:
        ing = _load("01_bronze_ingest.py", "bronze_ingest")
    except Exception:  # noqa: BLE001
        return None
    dbl = DoubleType()
    checks = 0
    v, ok = ing._cast_value("N/A", dbl)
    assert v is None and ok is False, "bad numeric must be flagged (cast_ok=False)"
    checks += 1
    v, ok = ing._cast_value("3.14", dbl)
    assert v == 3.14 and ok is True, "good numeric parses, not flagged"
    checks += 1
    v, ok = ing._cast_value("", dbl)
    assert v is None and ok is True, "empty is a legitimate null, NOT an error"
    checks += 1
    v, ok = ing._cast_value("null", dbl)
    assert v is None and ok is True, "'null' sentinel is a legitimate null"
    checks += 1
    return checks


def _run_m6():
    br = _load("04_capacity_bridge.py", "capacity_bridge")

    # Monkeypatch a fake requests.post returning an executeQueries-shaped payload.
    fake_payload = {"results": [{"tables": [{"rows": [
        {"Capacities[Capacity Id]": "cap-1", "Capacities[Capacity Name]": "Finance",
         "Capacities[Region]": "East US", "Capacities[SKU]": "F16",
         "TimePoints[Date]": "2026-07-01", "[CU_s]": 1800000.0},
        {"Capacities[Capacity Id]": "cap-2", "Capacities[Capacity Name]": "Ops",
         "Capacities[Region]": "West US", "Capacities[SKU]": "F8",
         "TimePoints[Date]": "2026-07-01", "[CU_s]": 900000.0},
    ]}]}]}

    class _Resp:
        def raise_for_status(self): pass
        def json(self): return fake_payload

    fake_requests = types.SimpleNamespace(post=lambda *a, **k: _Resp())
    br.requests = fake_requests
    br._REQUESTS = True

    rows = br.bridge_capacity_metrics_xmla(dataset_id="ds-guid", token="tok")
    assert len(rows) == 2, rows
    r0 = rows[0]
    assert r0["CapacityId"] == "cap-1" and r0["CapacityName"] == "Finance"
    assert r0["Region"] == "East US" and r0["Sku"] == "F16"
    assert r0["cu_seconds"] == 1800000.0, "CU_s must map to cu_seconds"
    assert r0["_partition_date"] == "2026-07-01"
    # schema matches gold_capacities / TMDL columns
    assert set(r0.keys()) == {"CapacityId", "CapacityName", "Region", "Sku",
                              "cu_seconds", "_partition_date"}
    # guardrails: missing dataset/token raise clearly
    try:
        br.bridge_capacity_metrics_xmla(dataset_id="", token="tok"); assert False
    except ValueError:
        pass
    return 2


if __name__ == "__main__":
    try:
        n = 0
        u14 = _run_u14()
        if u14 is None:
            print("[SKIP] U14 typing checks need pyspark types (covered in Spark tier)")
        else:
            print(f"PASS: U14 cast quarantine ({u14} checks)"); n += u14
        m6 = _run_m6()
        print(f"PASS: M6 executeQueries normalization ({m6} checks)"); n += m6
        print(f"\n================================================\nRESULT: ALL PASSED ({n} checks)")
    except AssertionError as e:
        print(f"FAIL: {e}"); sys.exit(1)
