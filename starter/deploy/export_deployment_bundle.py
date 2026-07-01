#!/usr/bin/env python3
# =============================================================================
# export_deployment_bundle.py  |  Label: [NET-NEW] [Unverified — requires Fabric tenant]
#
# Generates the fuam-basic-style ONE-CLICK bundle (deployment_file.json) by
# exporting the Fabric items you created during your FIRST successful manual
# deploy. This is the artifact that makes redeploy into a fresh workspace truly
# "one click" -- and it can ONLY be produced from a real, deployed instance
# (the item definitions are base64 payloads minted by Fabric, not hand-writable).
#
# Mirrors how GT-Analytics/fuam-basic made its deployment_file.json:
#   for each item in the source workspace -> GetItemDefinition -> base64 payload
#   -> record {displayName, type, org_id, definition{parts[...]}} in a JSON bundle
#   that the Deploy notebook re-instantiates via CreateItem/UpdateItemDefinition.
#
# RUN THIS ONCE, IN YOUR TENANT, AFTER a working manual deploy (Phase P1).
# It is intentionally a thin, well-documented wrapper -- every Fabric API call is
# marked [Unverified] and must be confirmed against the current Fabric REST /
# semantic-link-labs API surface when you run it.
#
# Fabric REST references (confirm current shapes at run time):
#   List items:          GET /v1/workspaces/{workspaceId}/items
#   Get item definition: POST /v1/workspaces/{workspaceId}/items/{itemId}/getDefinition
#   (semantic-link-labs / sempy.fabric expose equivalents; prefer those in a notebook.)
#   https://learn.microsoft.com/en-us/rest/api/fabric/core/items
# =============================================================================
import argparse
import base64
import json
import sys
from datetime import datetime, timezone

# Item types worth bundling for a gateway-monitor deploy. Adjust to what your
# P1 deploy actually created.
DEFAULT_ITEM_TYPES = [
    "Lakehouse", "Notebook", "DataPipeline",
    "Eventhouse", "KQLDatabase", "KQLQueryset",
    "SemanticModel", "Report", "Reflex",  # Reflex == Activator
]


def get_fabric_client():
    """
    [Unverified] Prefer running INSIDE a Fabric notebook where sempy.fabric is
    available and auth is ambient. Outside a notebook, use a service principal
    token against the Fabric REST API.
    """
    try:
        import sempy.fabric as fabric  # available in Fabric notebooks
        return ("sempy", fabric)
    except Exception:
        return ("rest", None)  # caller must supply token + requests


def export_via_sempy(fabric, workspace_id, item_types):
    """[Unverified] semantic-link path. Confirm method names against your version."""
    items = fabric.list_items(workspace=workspace_id)  # [Unverified] signature
    bundle_items = []
    for _, row in items.iterrows():
        itype = row.get("Type") or row.get("type")
        if itype not in item_types:
            continue
        item_id = row.get("Id") or row.get("id")
        name = row.get("Display Name") or row.get("displayName")
        # [Unverified] get_item_definition may return dict with base64 parts already
        try:
            definition = fabric.get_item_definition(  # [Unverified] method name
                workspace=workspace_id, item=item_id
            )
        except Exception as e:
            print(f"  [skip] {name} ({itype}): {e}")
            continue
        bundle_items.append({
            "displayName": name,
            "type": itype,
            "org_id": item_id,
            "definition": definition,
        })
        print(f"  [ok]  exported {name} ({itype})")
    return bundle_items


def export_via_rest(workspace_id, token, item_types):
    """[Unverified] REST path. Requires: pip install requests; a bearer token."""
    import requests
    base = "https://api.fabric.microsoft.com/v1"
    h = {"Authorization": f"Bearer {token}"}
    items = requests.get(f"{base}/workspaces/{workspace_id}/items", headers=h).json().get("value", [])
    bundle_items = []
    for it in items:
        if it["type"] not in item_types:
            continue
        # POST getDefinition returns { definition: { parts: [ {path, payload(base64), payloadType} ] } }
        r = requests.post(
            f"{base}/workspaces/{workspace_id}/items/{it['id']}/getDefinition",
            headers=h,
        )
        if r.status_code not in (200, 202):
            print(f"  [skip] {it['displayName']} ({it['type']}): HTTP {r.status_code}")
            continue
        definition = r.json().get("definition", {})
        bundle_items.append({
            "displayName": it["displayName"],
            "type": it["type"],
            "org_id": it["id"],
            "definition": definition,
        })
        print(f"  [ok]  exported {it['displayName']} ({it['type']})")
    return bundle_items


def main():
    ap = argparse.ArgumentParser(description="Export Fabric items to a one-click deployment bundle.")
    ap.add_argument("--workspace-id", required=True, help="Source workspace GUID (your P1 deploy).")
    ap.add_argument("--token", help="Bearer token (REST path only; omit inside a Fabric notebook).")
    ap.add_argument("--out", default="deployment_file.json")
    ap.add_argument("--types", nargs="*", default=DEFAULT_ITEM_TYPES)
    args = ap.parse_args()

    mode, fabric = get_fabric_client()
    print(f"[bundle] mode={mode}  workspace={args.workspace_id}  types={args.types}")

    if mode == "sempy":
        items = export_via_sempy(fabric, args.workspace_id, args.types)
    else:
        if not args.token:
            print("ERROR: outside a Fabric notebook you must pass --token (SP bearer token).")
            sys.exit(2)
        items = export_via_rest(args.workspace_id, args.token, args.types)

    if not items:
        print("No items exported. Confirm workspace id, permissions, and item types.")
        sys.exit(1)

    bundle = {
        "_schema": "gateway-monitor.deployment_file/v1",
        "_generated_utc": datetime.now(timezone.utc).isoformat(),
        "_note": "Re-instantiated by Deploy_GatewayMonitor.ipynb into a fresh workspace.",
        "source_workspace": args.workspace_id,
        "items": items,
    }
    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(bundle, f, indent=1)
    print(f"\n[bundle] wrote {args.out} with {len(items)} items.")
    print("[bundle] Commit this file so others can one-click deploy. "
          "Re-run after any item change to refresh the bundle.")


if __name__ == "__main__":
    main()
