# =============================================================================
# tenant_doctor.py  |  Label: [NET-NEW]
#
# PREFLIGHT for the tenant-extract pipeline (00_tenant_extract.py). Run this
# BEFORE a live tenant extract so a piloter learns — in ~30 seconds — exactly
# which permission/tenant-setting is missing, instead of debugging a 401 mid-run.
#
# Mirrors the "doctor" pattern: each check returns PASS / FAIL / SKIP with a
# precise remediation string. Non-zero exit if any REQUIRED check fails.
#
# Checks (in order — later checks presuppose earlier ones):
#   D1  SP token acquisition (MSAL client-credentials, powerbi/.default scope)
#   D2  Read-only admin API + tenant setting  -> GET /admin/workspaces/modified
#         Proves BOTH: SP has admin read scope AND tenant setting
#         "Allow service principals to use read-only admin APIs" is ON for the
#         SP's Entra security group. This is THE setting that most often blocks
#         tenant extract (401/403).
#   D3  Activity Events access  -> GET /admin/activityevents (today, UTC)
#   D4  Refreshables (Premium) access -> GET /admin/capacities/refreshables
#   D5  Capacity bridge readiness (config capacityBridge.mode reachable)
#
# MOCK mode (TENANT_DOCTOR_MOCK=1 or TENANT_EXTRACT_MOCK=1): all network checks
# return PASS deterministically so CI can exercise the reporting logic. A live
# green result requires a real admin-scoped SP.
#
# Refs (remediation links):
#   Read-only admin APIs for SPs:
#     https://learn.microsoft.com/en-us/power-bi/enterprise/read-only-apis-service-principal-authentication
#   Scanner API:  https://learn.microsoft.com/en-us/rest/api/power-bi/admin/workspace-info-get-modified-workspaces
#   Activity events: https://learn.microsoft.com/en-us/rest/api/power-bi/admin/get-activity-events
# =============================================================================
from __future__ import annotations

import json
import os
import sys
import datetime as dt

try:
    import requests  # type: ignore
    _REQUESTS = True
except Exception:  # noqa: BLE001
    _REQUESTS = False

PBI_ADMIN = "https://api.powerbi.com/v1.0/myorg/admin"
PBI_SCOPE = "https://analysis.windows.net/powerbi/api/.default"
AUTHORITY = "https://login.microsoftonline.com"

MOCK = os.getenv("TENANT_DOCTOR_MOCK", os.getenv("TENANT_EXTRACT_MOCK", "0")) == "1"

# Result codes
PASS, FAIL, SKIP = "PASS", "FAIL", "SKIP"

REMEDIATION = {
    "D1": ("Confirm tenantId/applicationId/clientSecret. The SP must exist in Entra ID and the "
           "secret must be current (not expired). Secret is fetched from Key Vault at runtime."),
    "D2": ("Enable tenant setting 'Allow service principals to use read-only admin APIs' "
           "(Fabric Admin portal -> Tenant settings -> Admin API settings) and add the SP's Entra "
           "SECURITY GROUP to it. Also ensure the SP is a member of that group. "
           "See https://learn.microsoft.com/en-us/power-bi/enterprise/read-only-apis-service-principal-authentication"),
    "D3": ("Activity events use the same read-only admin API surface as D2. If D2 passed but D3 "
           "failed, retry (transient) or check the SP hasn't lost group membership."),
    "D4": ("Refreshables require Premium/Fabric capacity + admin read scope. If you have no Premium "
           "capacity this check may legitimately return no data; treated as WARN, not a hard fail."),
    "D5": ("Set config.capacityBridge.mode to one of mock|fpm_eventhouse|capacity_metrics_xmla. "
           "For live CU, provide eventhouseDeltaPath (FPM) or xmlaEndpoint+dataset (Capacity Metrics)."),
}


def _acquire_token(cfg: dict) -> str:
    if MOCK:
        return "MOCK_TOKEN"
    from msal import ConfidentialClientApplication
    app = ConfidentialClientApplication(
        cfg["applicationId"],
        authority=f"{AUTHORITY}/{cfg['tenantId']}",
        client_credential=cfg["clientSecret"],
    )
    res = app.acquire_token_for_client(scopes=[PBI_SCOPE])
    if "access_token" not in res:
        raise RuntimeError(res.get("error_description", str(res)))
    return res["access_token"]


def _probe(url: str, token: str, params: dict | None = None) -> tuple[bool, str]:
    """Returns (ok, detail). ok=True on 2xx; detail carries status/short body."""
    if MOCK:
        return True, "mock 200"
    try:
        r = requests.get(url, headers={"Authorization": f"Bearer {token}"}, params=params, timeout=60)
        if r.status_code in (200, 204):
            return True, f"{r.status_code}"
        return False, f"{r.status_code}: {r.text[:160]}"
    except Exception as e:  # noqa: BLE001
        return False, f"exception: {e}"


def run(cfg: dict | None = None) -> dict:
    cfg = cfg or {}
    results: list[dict] = []

    def record(cid, name, status, detail, required=True):
        results.append({"id": cid, "name": name, "status": status,
                        "detail": detail, "required": required})

    # D1 — token
    if not MOCK and not _REQUESTS:
        record("D1", "SP token acquisition", FAIL, "requests/msal not installed and not MOCK", True)
        return _finish(results)
    try:
        token = _acquire_token(cfg)
        record("D1", "SP token acquisition", PASS, "token acquired")
    except Exception as e:  # noqa: BLE001
        record("D1", "SP token acquisition", FAIL, f"{e}")
        return _finish(results)  # nothing else can run

    # D2 — read-only admin API + tenant setting (the make-or-break check)
    since = (dt.datetime.utcnow() - dt.timedelta(hours=1)).strftime("%Y-%m-%dT%H:%M:%SZ")
    ok, detail = _probe(f"{PBI_ADMIN}/workspaces/modified", token, {"modifiedSince": since})
    record("D2", "Read-only admin API + tenant setting", PASS if ok else FAIL, detail)

    # D3 — activity events
    day = dt.datetime.utcnow().date()
    ok3, d3 = _probe(f"{PBI_ADMIN}/activityevents", token, {
        "startDateTime": f"'{day}T00:00:00.000Z'", "endDateTime": f"'{day}T00:10:00.000Z'"})
    record("D3", "Activity Events access", PASS if ok3 else FAIL, d3)

    # D4 — refreshables (WARN, not hard fail — may be empty without Premium)
    ok4, d4 = _probe(f"{PBI_ADMIN}/capacities/refreshables", token, {"$top": 1})
    record("D4", "Refreshables (Premium) access", PASS if ok4 else SKIP, d4, required=False)

    # D5 — capacity bridge readiness (config only, no network)
    mode = (cfg.get("capacityBridge") or {}).get("mode", os.getenv("CAPACITY_BRIDGE_MODE", "mock"))
    valid = mode in ("mock", "fpm_eventhouse", "capacity_metrics_xmla")
    record("D5", f"Capacity bridge mode = {mode!r}", PASS if valid else FAIL,
           "valid mode" if valid else "unknown mode", required=False)

    return _finish(results)


def _finish(results: list[dict]) -> dict:
    print("\n=== Tenant-extract preflight (tenant_doctor) ===")
    for r in results:
        mark = {"PASS": "[PASS]", "FAIL": "[FAIL]", "SKIP": "[WARN]"}[r["status"]]
        print(f"  {mark} {r['id']}  {r['name']}  -- {r['detail']}")
        if r["status"] == FAIL:
            print(f"         -> FIX: {REMEDIATION.get(r['id'], 'see docs')}")
    required_fail = [r for r in results if r["required"] and r["status"] == FAIL]
    verdict = "READY" if not required_fail else "BLOCKED"
    print(f"\nVERDICT: {verdict}"
          + ("" if verdict == "READY" else f" ({len(required_fail)} required check(s) failing)"))
    return {"verdict": verdict, "results": results, "required_fail": len(required_fail)}


if __name__ == "__main__":
    cfg = {}
    cfg_path = os.getenv("CONFIG_PATH", "")
    if cfg_path and os.path.exists(cfg_path):
        cfg = json.load(open(cfg_path))
    # Also accept the AZURE_CLIENT_ID/AZURE_TENANT_ID/AZURE_CLIENT_SECRET env-var
    # convention (used by starter/deploy/run-tenant-doctor.ps1, which fetches the
    # secret from Key Vault into a transient env var) as a fallback when no
    # CONFIG_PATH is set -- lets the one-command script and this checker agree on
    # how credentials are passed without requiring a config file on disk.
    if not cfg.get("applicationId") and os.getenv("AZURE_CLIENT_ID"):
        cfg = {
            **cfg,
            "applicationId": os.environ["AZURE_CLIENT_ID"],
            "tenantId": os.environ.get("AZURE_TENANT_ID", cfg.get("tenantId", "")),
            "clientSecret": os.environ.get("AZURE_CLIENT_SECRET", cfg.get("clientSecret", "")),
        }
    out = run(cfg)
    sys.exit(0 if out["verdict"] == "READY" else 2)
