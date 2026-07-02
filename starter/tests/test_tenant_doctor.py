#!/usr/bin/env python3
"""
test_tenant_doctor.py  |  Label: [NET-NEW]  |  Spark-free (Tier 1)

Covers the tenant-extract preflight (tenant_doctor.py) reporting + verdict logic
without network: mock READY path, invalid capacity-bridge mode (non-required
FAIL still READY), D1 token-failure short-circuit -> BLOCKED, and a forced
required-check FAIL -> BLOCKED.
"""
import importlib.util
import os
import sys

NB = os.path.join(os.path.dirname(__file__), "..", "notebooks")


def _load():
    spec = importlib.util.spec_from_file_location("tenant_doctor", os.path.join(NB, "tenant_doctor.py"))
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def main():
    os.environ["TENANT_DOCTOR_MOCK"] = "1"
    doc = _load()
    passed = 0

    # 1. all-mock -> READY, 5 checks, zero required failures
    out = doc.run({"capacityBridge": {"mode": "mock"}})
    assert out["verdict"] == "READY", out
    assert len(out["results"]) == 5 and out["required_fail"] == 0
    print("PASS: mock preflight READY"); passed += 1

    # 2. invalid capacity-bridge mode is a NON-required FAIL -> still READY
    out = doc.run({"capacityBridge": {"mode": "bogus"}})
    d5 = [r for r in out["results"] if r["id"] == "D5"][0]
    assert d5["status"] == "FAIL" and d5["required"] is False
    assert out["verdict"] == "READY", "non-required fail must not block"
    print("PASS: invalid bridge mode -> non-blocking"); passed += 1

    # 3. D1 token failure short-circuits -> BLOCKED, only D1 recorded
    orig = doc._acquire_token
    doc._acquire_token = lambda cfg: (_ for _ in ()).throw(RuntimeError("bad secret"))
    doc.MOCK = False  # force real path so the patched token raises
    try:
        out = doc.run({"applicationId": "x", "tenantId": "y", "clientSecret": "z"})
    finally:
        doc._acquire_token = orig
        doc.MOCK = True
    assert out["verdict"] == "BLOCKED" and len(out["results"]) == 1
    assert out["results"][0]["id"] == "D1" and out["results"][0]["status"] == "FAIL"
    print("PASS: D1 token failure -> BLOCKED short-circuit"); passed += 1

    # 4. remediation strings exist for every required check id
    for cid in ("D1", "D2", "D3"):
        assert cid in doc.REMEDIATION and len(doc.REMEDIATION[cid]) > 20
    print("PASS: remediation coverage"); passed += 1

    print(f"\n================================================\nRESULT: ALL {passed} TESTS PASSED")


if __name__ == "__main__":
    try:
        main()
    except AssertionError as e:
        print(f"FAIL: {e}"); sys.exit(1)
