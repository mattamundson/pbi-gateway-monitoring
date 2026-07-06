#!/usr/bin/env python3
"""
tenant_doctor.py  --  [NET-NEW] Phase 1 verification instrument.

Confirms the gwmon service principal can actually read the Power BI READ-ONLY ADMIN APIs
-- i.e. that the 401 / 0-admin-rows blocker (missing Tenant.Read.All grant + the tenant
setting "Service principals can access read-only admin APIs") is cleared.

Design goals:
  * stdlib ONLY (urllib + json) -- runs in any Python 3.8+, no pip installs, so it works
    in a plain terminal or a Fabric/Databricks notebook alike.
  * the client SECRET is read from the environment and NEVER printed / logged.
  * clear exit code: 0 = admin APIs reachable (grant effective), non-zero = still blocked.

Credentials come from the environment (the secret stays in your shell, e.g. pulled from
1Password in the same window so it never touches disk or argv):

  PowerShell:
    $env:AZURE_CLIENT_ID     = "531bd06b-3e5a-4df6-9e09-0c00c12e7adb"
    $env:AZURE_TENANT_ID     = "16f93f41-0c3b-4163-b362-5e18cfac6898"
    $env:AZURE_CLIENT_SECRET = (op read "op://Amo Personal/Gwmon-SPN-AdminAPI/credential")
    python starter/notebooks/tenant_doctor.py

  bash:
    export AZURE_CLIENT_ID=531bd06b-3e5a-4df6-9e09-0c00c12e7adb
    export AZURE_TENANT_ID=16f93f41-0c3b-4163-b362-5e18cfac6898
    export AZURE_CLIENT_SECRET="$(op read 'op://Amo Personal/Gwmon-SPN-AdminAPI/credential')"
    python starter/notebooks/tenant_doctor.py
"""
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request

AUTHORITY = "https://login.microsoftonline.com/{tenant}/oauth2/v2.0/token"
SCOPE = "https://analysis.windows.net/powerbi/api/.default"
ADMIN_BASE = "https://api.powerbi.com/v1.0/myorg/admin"
TIMEOUT = 30

# Read-only admin surfaces to probe. Each returns a JSON object with a "value" array when the
# grant + tenant setting are effective; a 401 means the SPN cannot read the admin APIs yet.
PROBES = [
    ("workspaces", "/groups?$top=100"),
    ("capacities", "/capacities"),
]


def _fail(msg, code=1):
    print("RESULT: BLOCKED -- " + msg)
    sys.exit(code)


def get_token(tenant, client_id, client_secret):
    url = AUTHORITY.format(tenant=tenant)
    data = urllib.parse.urlencode({
        "grant_type": "client_credentials",
        "client_id": client_id,
        "client_secret": client_secret,
        "scope": SCOPE,
    }).encode("utf-8")
    req = urllib.request.Request(url, data=data, method="POST")
    req.add_header("Content-Type", "application/x-www-form-urlencoded")
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
            body = json.loads(resp.read().decode("utf-8"))
            return body["access_token"]
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", "replace")[:400]
        # Do NOT surface the secret; AAD error bodies never echo it, but keep this terse.
        _fail("token request failed (HTTP %s). Check AZURE_CLIENT_ID / TENANT / SECRET.\n  %s"
              % (e.code, detail))
    except Exception as e:  # noqa: BLE001
        _fail("token request errored: %s" % e)


def probe(token, label, path):
    # Encode defensively so a filter containing spaces/quotes can't raise on urlopen.
    url = ADMIN_BASE + urllib.parse.quote(path, safe="/?$=&,'")
    req = urllib.request.Request(url, method="GET")
    req.add_header("Authorization", "Bearer " + token)
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
            body = json.loads(resp.read().decode("utf-8"))
            rows = body.get("value", [])
            return (200, len(rows), None)
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", "replace")[:200].replace("\n", " ")
        return (e.code, 0, detail)
    except Exception as e:  # noqa: BLE001
        return (-1, 0, str(e))


def main():
    client_id = os.environ.get("AZURE_CLIENT_ID", "").strip()
    tenant = os.environ.get("AZURE_TENANT_ID", "").strip()
    secret = os.environ.get("AZURE_CLIENT_SECRET", "").strip()

    missing = [n for n, v in (("AZURE_CLIENT_ID", client_id),
                              ("AZURE_TENANT_ID", tenant),
                              ("AZURE_CLIENT_SECRET", secret)) if not v]
    if missing:
        _fail("missing env var(s): %s" % ", ".join(missing), code=2)

    print("tenant_doctor -- Power BI read-only admin API check")
    print("  client_id : %s" % client_id)
    print("  tenant_id : %s" % tenant)
    print("  secret    : (present, %d chars, not shown)" % len(secret))
    print("")

    token = get_token(tenant, client_id, secret)
    print("[ok] acquired AAD token for the Power BI API.\n")

    any_401 = False
    any_rows = False
    for label, path in PROBES:
        status, count, detail = probe(token, label, path)
        if status == 200:
            print("  [200] %-11s -> %d row(s)" % (label, count))
            if count > 0:
                any_rows = True
        elif status in (401, 403):
            any_401 = True
            print("  [%s] %-11s -> FORBIDDEN: %s" % (status, label, detail))
        else:
            print("  [%s] %-11s -> %s" % (status, label, detail))

    print("")
    if any_401:
        _fail("admin API returned 401/403. The SPN grant OR the tenant setting "
              "'Service principals can access read-only admin APIs' is not effective yet "
              "(enable it for group gwmon-admin-api-sps; allow a few minutes to propagate).")
    if not any_rows:
        print("RESULT: REACHABLE but every probe returned 0 rows.")
        print("  Admin access works (no 401) -- but the tenant appears empty on these surfaces.")
        print("  If you expect workspaces/capacities, re-check after propagation.")
        sys.exit(3)

    print("RESULT: PASS -- read-only admin APIs are reachable with data. Phase 1 grant confirmed.")
    sys.exit(0)


if __name__ == "__main__":
    main()
