# =============================================================================
# 00_tenant_extract.py  |  Label: [NET-NEW]
#
# TENANT-BREADTH EXTRACT — feeds the enhanced "Nexus" report pages that the
# gateway-only collectors cannot fill: Tenant Overview, Timeline, Refresh
# Analytics, and (via 04_capacity_bridge) the CU measures.
#
# Produces bronze landing tables:
#   bronze_scanner_result   <- Power BI Admin Scanner API (inventory + lineage)
#   bronze_activity_events  <- Power BI Admin Activity Events API (usage/adoption)
#   bronze_refreshables     <- Power BI Admin "Refreshables" (Premium) API
# Downstream: 01a_tenant_silver_gold.py conforms these into gold_inventory /
#   gold_activities / gold_refreshables that the report + measures.dax bind to.
#
# WHY A SEPARATE PIPELINE (headwind, documented honestly):
#   The gateway collectors run on the gateway HOST and see only gateway logs.
#   Tenant inventory/usage lives behind the Fabric/Power BI ADMIN REST APIs and
#   must be pulled with a service principal that has admin read scopes + the
#   tenant setting "Allow service principals to use read-only admin APIs". This
#   is a different trust boundary, hence its own notebook (feature-flagged in
#   config.features.collectActivityEvents / collectInventory).
#
# AUTH: MSAL client-credentials for scope
#   https://analysis.windows.net/powerbi/api/.default  (SP secret from Key Vault).
#
# RATE LIMITS (respected via backoff below — do not raise blindly):
#   Scanner getInfo: 500 req/hr, 16 concurrent, <=100 workspaces/batch, results 24h.
#   Activity events: 200 req/hr, same-UTC-day window, 28-day lookback, continuationToken.
#   Docs:
#     https://learn.microsoft.com/en-us/rest/api/power-bi/admin/workspace-info-post-workspace-info
#     https://learn.microsoft.com/en-us/rest/api/power-bi/admin/get-activity-events
#     https://learn.microsoft.com/en-us/rest/api/power-bi/admin/datasets-get-refreshables-as-admin
#
# [Unverified] All REST calls require a live tenant + admin-scoped SP to validate.
#   In CI / local smoke this notebook runs in MOCK mode (TENANT_EXTRACT_MOCK=1),
#   emitting deterministic synthetic bronze so the medallion + report bind end-to-end
#   without network. Real match/coverage numbers only come from a live run.
# =============================================================================
from __future__ import annotations

import json
import os
import time
import datetime as dt
from typing import Any

# -- Spark is optional at import time so the module is unit-testable off-cluster --
try:
    from pyspark.sql import SparkSession
    import pyspark.sql.functions as F  # noqa: F401

    spark = SparkSession.builder.getOrCreate()
    _SPARK = True
except Exception:  # noqa: BLE001
    spark = None
    _SPARK = False

# HTTP is optional too (MOCK mode needs neither requests nor network).
try:
    import requests  # type: ignore

    _REQUESTS = True
except Exception:  # noqa: BLE001
    _REQUESTS = False

PBI_ADMIN = "https://api.powerbi.com/v1.0/myorg/admin"
PBI_SCOPE = "https://analysis.windows.net/powerbi/api/.default"
AUTHORITY = "https://login.microsoftonline.com"

MOCK = os.getenv("TENANT_EXTRACT_MOCK", "0") == "1"
LANDING_PATH = os.getenv("BRONZE_LANDING_PATH", "/tmp/nexus_lake/bronze")


# -----------------------------------------------------------------------------
# Auth
# -----------------------------------------------------------------------------
def acquire_token(tenant_id: str, client_id: str, client_secret: str) -> str:
    """Client-credentials token for the Power BI admin APIs (MSAL)."""
    if MOCK:
        return "MOCK_TOKEN"
    from msal import ConfidentialClientApplication  # imported lazily

    app = ConfidentialClientApplication(
        client_id,
        authority=f"{AUTHORITY}/{tenant_id}",
        client_credential=client_secret,
    )
    result = app.acquire_token_for_client(scopes=[PBI_SCOPE])
    if "access_token" not in result:
        raise RuntimeError(f"Token acquisition failed: {result.get('error_description', result)}")
    return result["access_token"]


def _get(url: str, token: str, params: dict | None = None) -> dict:
    """GET with 429/5xx-aware backoff. Honors Retry-After."""
    for attempt in range(6):
        r = requests.get(url, headers={"Authorization": f"Bearer {token}"}, params=params, timeout=120)
        if r.status_code == 429 or r.status_code >= 500:
            wait = int(r.headers.get("Retry-After", 2 ** attempt))
            print(f"[backoff] {r.status_code} on {url} -> sleep {wait}s")
            time.sleep(wait)
            continue
        r.raise_for_status()
        return r.json()
    raise RuntimeError(f"GET giving up after retries: {url}")


def _post(url: str, token: str, params: dict | None = None, body: Any = None) -> dict:
    for attempt in range(6):
        r = requests.post(
            url, headers={"Authorization": f"Bearer {token}"}, params=params, json=body, timeout=120
        )
        if r.status_code == 429 or r.status_code >= 500:
            wait = int(r.headers.get("Retry-After", 2 ** attempt))
            time.sleep(wait)
            continue
        r.raise_for_status()
        return r.json()
    raise RuntimeError(f"POST giving up after retries: {url}")


# -----------------------------------------------------------------------------
# Scanner API  (inventory + lineage)  — the async 4-step dance
# -----------------------------------------------------------------------------
def extract_scanner(token: str, modified_since: dt.datetime | None = None) -> list[dict]:
    """getModifiedWorkspaces -> postWorkspaceInfo(batch<=100) -> poll getScanStatus
    -> getScanResult. Returns list of raw scan-result JSON blobs."""
    if MOCK:
        return [_mock_scan_result()]

    since = (modified_since or (dt.datetime.utcnow() - dt.timedelta(days=1))).strftime("%Y-%m-%dT%H:%M:%SZ")
    mod = _get(f"{PBI_ADMIN}/workspaces/modified", token, params={"modifiedSince": since})
    ws_ids = [w["id"] for w in mod]
    results: list[dict] = []
    for i in range(0, len(ws_ids), 100):  # <=100 workspaces per batch
        batch = ws_ids[i : i + 100]
        scan = _post(
            f"{PBI_ADMIN}/workspaces/getInfo",
            token,
            params={
                "lineage": "true",
                "datasourceDetails": "true",
                "datasetSchema": "true",
                "datasetExpressions": "true",
                "getArtifactUsers": "true",
            },
            body={"workspaces": batch},
        )
        scan_id = scan["id"]
        # poll status (500 req/hr budget; small sleeps)
        for _ in range(60):
            status = _get(f"{PBI_ADMIN}/workspaces/scanStatus/{scan_id}", token)
            if status.get("status") == "Succeeded":
                break
            time.sleep(2)
        results.append(_get(f"{PBI_ADMIN}/workspaces/scanResult/{scan_id}", token))
    return results


# -----------------------------------------------------------------------------
# Activity Events  (usage / adoption)
# -----------------------------------------------------------------------------
def extract_activity_events(token: str, since_days: int = 7) -> list[dict]:
    """Iterate each UTC day in [now-since_days, now]; follow continuationToken.
    Constraint: startDateTime/endDateTime must be within the SAME UTC day."""
    if MOCK:
        return _mock_activity_events()

    events: list[dict] = []
    today = dt.datetime.utcnow().date()
    for d in range(since_days, -1, -1):
        day = today - dt.timedelta(days=d)
        start = f"'{day}T00:00:00.000Z'"
        end = f"'{day}T23:59:59.999Z'"
        url = f"{PBI_ADMIN}/activityevents"
        resp = _get(url, token, params={"startDateTime": start, "endDateTime": end})
        events.extend(resp.get("activityEventEntities", []))
        cont = resp.get("continuationUri")
        while cont:
            resp = _get(cont, token)
            events.extend(resp.get("activityEventEntities", []))
            cont = resp.get("continuationUri")
    return events


# -----------------------------------------------------------------------------
# Refreshables (Premium) — refresh health across the tenant
# -----------------------------------------------------------------------------
def extract_refreshables(token: str, top: int = 1000) -> list[dict]:
    if MOCK:
        return _mock_refreshables()
    resp = _get(f"{PBI_ADMIN}/capacities/refreshables", token, params={"$top": top, "$expand": "capacity,group"})
    return resp.get("value", [])


# -----------------------------------------------------------------------------
# Bronze landing
# -----------------------------------------------------------------------------
def _write_bronze_json(records: list[dict], table: str) -> int:
    """Write raw records to bronze. Spark Delta on cluster; JSON files off-cluster."""
    provenance = {
        "_ingested_at_utc": dt.datetime.utcnow().isoformat(),
        "_collector": "00_tenant_extract",
        "_collector_version": "1.0",
        "_mock": MOCK,
    }
    stamped = [{**r, **provenance} for r in records]
    if _SPARK and not os.getenv("FORCE_JSON_LANDING"):
        if not stamped:
            print(f"[bronze] {table}: 0 records, skipping Delta write")
            return 0
        df = spark.createDataFrame([json.loads(json.dumps(r, default=str)) for r in stamped])
        (df.write.format("delta").mode("overwrite").option("mergeSchema", "true").saveAsTable(table))
        print(f"[bronze] {table}: wrote {len(stamped)} rows (delta)")
        return len(stamped)
    # off-cluster JSON landing (MOCK/CI)
    os.makedirs(LANDING_PATH, exist_ok=True)
    path = os.path.join(LANDING_PATH, f"{table}.json")
    with open(path, "w") as fh:
        json.dump(stamped, fh, default=str, indent=2)
    print(f"[bronze] {table}: wrote {len(stamped)} rows -> {path}")
    return len(stamped)


# -----------------------------------------------------------------------------
# MOCK fixtures (deterministic — CI + local smoke, no network)
# -----------------------------------------------------------------------------
def _mock_scan_result() -> dict:
    return {
        "workspaces": [
            {
                "id": "ws-001", "name": "Finance", "capacityId": "cap-eastus",
                "reports": [{"id": "rp-1", "name": "Exec Dashboard"}],
                "datasets": [{"id": "ds-1", "name": "Sales Model"}],
                "dataflows": [], "dashboards": [],
            },
            {
                "id": "ws-002", "name": "Ops", "capacityId": "cap-westus",
                "reports": [{"id": "rp-2", "name": "Gateway Ops"}],
                "datasets": [{"id": "ds-2", "name": "Gateway Model"}],
            },
        ]
    }


def _mock_activity_events() -> list[dict]:
    base = dt.datetime.utcnow().replace(hour=9, minute=0, second=0, microsecond=0)
    out = []
    for i in range(24):
        out.append({
            "Id": f"act-{i}", "UserId": f"user{i % 4}@contoso.com",
            "Operation": ["ViewReport", "ViewDashboard", "AnalyzedByExternalApplication", "Refresh"][i % 4],
            "WorkspaceId": f"ws-00{(i % 2) + 1}", "CapacityId": ["cap-eastus", "cap-westus"][i % 2],
            "CreationTime": (base - dt.timedelta(hours=i)).isoformat() + "Z",
        })
    return out


def _mock_refreshables() -> list[dict]:
    out = []
    for i in range(6):
        out.append({
            "id": f"ds-{i}", "name": f"Model {i}", "group": {"id": f"ws-00{(i % 2) + 1}"},
            "capacity": {"id": ["cap-eastus", "cap-westus"][i % 2]},
            "refreshSchedule": {}, "lastRefresh": {
                "status": ["Completed", "Completed", "Failed"][i % 3],
                "startTime": (dt.datetime.utcnow() - dt.timedelta(hours=i)).isoformat() + "Z",
                "endTime": (dt.datetime.utcnow() - dt.timedelta(hours=i) + dt.timedelta(minutes=3 + i)).isoformat() + "Z",
            },
        })
    return out


# -----------------------------------------------------------------------------
# Entry point
# -----------------------------------------------------------------------------
def run(config: dict | None = None) -> dict:
    config = config or {}
    counts: dict[str, int] = {}
    if MOCK:
        token = "MOCK_TOKEN"
    else:
        if not _REQUESTS:
            raise RuntimeError("requests not available and not in MOCK mode")
        token = acquire_token(config["tenantId"], config["applicationId"], config["clientSecret"])

    feats = (config.get("features") or {})
    # Inventory (Scanner) — default on unless explicitly disabled
    if feats.get("collectInventory", True):
        scan = extract_scanner(token)
        counts["bronze_scanner_result"] = _write_bronze_json(scan, "bronze_scanner_result")
    # Activity events
    if feats.get("collectActivityEvents", True):
        acts = extract_activity_events(token, since_days=int(config.get("activityLookbackDays", 7)))
        counts["bronze_activity_events"] = _write_bronze_json(acts, "bronze_activity_events")
    # Refreshables
    if feats.get("collectRefreshables", True):
        refs = extract_refreshables(token)
        counts["bronze_refreshables"] = _write_bronze_json(refs, "bronze_refreshables")

    print(f"[00_tenant_extract] done (mock={MOCK}): {counts}")
    return counts


if __name__ == "__main__":
    # Local/CI: MOCK mode with no config; live: load config.json + Key Vault secret.
    cfg = {}
    cfg_path = os.getenv("CONFIG_PATH", "")
    if cfg_path and os.path.exists(cfg_path):
        cfg = json.load(open(cfg_path))
    run(cfg)
