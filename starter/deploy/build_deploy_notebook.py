"""
build_deploy_notebook.py

Generates Deploy_GatewayMonitor.ipynb as valid nbformat 4 JSON.
Run once to regenerate the notebook after editing cell sources here.
"""
import json
from pathlib import Path

OUTPUT = Path(__file__).parent / "Deploy_GatewayMonitor.ipynb"

def md(source):
    """Create a markdown cell."""
    return {
        "cell_type": "markdown",
        "id": None,  # filled later
        "metadata": {},
        "source": source if isinstance(source, str) else "\n".join(source),
    }

def code(source, tags=None):
    """Create a code cell."""
    cell = {
        "cell_type": "code",
        "execution_count": None,
        "id": None,
        "metadata": {},
        "outputs": [],
        "source": source if isinstance(source, str) else "\n".join(source),
    }
    if tags:
        cell["metadata"]["tags"] = tags
    return cell

# ── Cell sources ───────────────────────────────────────────────────────────────

CELL_A_MD = """\
# Gateway Monitor — Deployment Orchestrator

> **[Unverified — requires Fabric tenant pilot]**  
> **Label: [NET-NEW + ADAPTED]**  
> This notebook has NOT been executed in a live Fabric PySpark environment.  
> Every external call that could not be verified from documentation is wrapped in  
> `try/except` and labeled with `# [Unverified]`.  
> Run cells top-to-bottom in a Fabric notebook session (not locally).

---

## What this notebook does

| Step | Cell | Action |
|------|------|--------|
| (b) | Parameters | Set workspace/lakehouse/Key Vault config |
| (c) | Create infra | Lakehouse + Eventhouse via `sempy.fabric` |
| (d) | Upload notebooks | Import 4 Python notebooks via Fabric REST |
| (e) | Create pipeline | Data Pipeline: bronze → silver → gold (15-min schedule) |
| (f) | Import report | Semantic model + PBIP report via Fabric REST/Git |
| (g) | Validate | Check tables exist, print next steps |

---

## Prerequisites

- **Capacity**: F8 minimum (F64 recommended for production)
- **Service Principal (SP)**:
  - Registered in Entra ID
  - Fabric **Member** role on target workspace
  - Key Vault **Contributor** (to read the SP secret)
  - Client secret stored in Key Vault (name set in Parameters cell)
- **Python packages** (install in first cell or notebook environment):
  ```
  semantic-link-labs>=0.9.0
  azure-identity>=1.16.0
  azure-keyvault-secrets>=4.8.0
  requests>=2.31.0
  ```
- **Repo files** accessible in the Lakehouse Files/ or mounted:
  - `starter/notebooks/01_bronze_ingest.py`
  - `starter/notebooks/02_silver_correlate.py`
  - `starter/notebooks/03_gold_aggregate.py`
  - `starter/notebooks/gateway_bronze_lib.py`
  - `starter/report/gateway_monitor.report.json`
  - `starter/report/definition.pbir`
"""

CELL_INSTALL = """\
# [NET-NEW] Install required packages
# Run this cell once per Fabric environment / notebook session
# %pip install semantic-link-labs>=0.9.0 azure-identity>=1.16.0 azure-keyvault-secrets>=4.8.0 requests>=2.31.0
# Uncomment the line above and run, then restart the kernel before proceeding.
print("[deploy] Package install cell — uncomment %pip line and run once, then restart kernel.")
"""

CELL_B_PARAMS = """\
# =============================================================================
# (b) PARAMETERS — edit these before running
# Label: [NET-NEW]
# [Unverified — requires Fabric tenant pilot]
# =============================================================================

# ── Fabric workspace / capacity ──────────────────────────────────────────────
WORKSPACE_ID    = "<your-workspace-id>"        # GUID from workspace URL
CAPACITY_ID     = "<your-capacity-id>"         # GUID; needed only if creating items
LAKEHOUSE_NAME  = "gateway_monitor_lh"         # Will be created if absent
EVENTHOUSE_NAME = "gateway_monitor_eh"         # Will be created if absent (optional)

# ── Key Vault ────────────────────────────────────────────────────────────────
KV_URI                = "https://<your-kv>.vault.azure.net/"
SP_CLIENT_ID_SECRET   = "gateway-monitor-sp-clientid"    # KV secret name for SP client ID
SP_TENANT_ID_SECRET   = "gateway-monitor-sp-tenantid"    # KV secret name for tenant ID
SP_SECRET_SECRET      = "gateway-monitor-sp-secret"      # KV secret name for SP client secret

# ── Feature flags ────────────────────────────────────────────────────────────
ENABLE_FPM_BRIDGE     = False   # Set True if FPM Eventhouse + OneLake availability enabled
CREATE_EVENTHOUSE     = False   # Set True to create Eventhouse for real-time KQL
CREATE_PIPELINE       = True    # Set True to create the bronze→silver→gold Data Pipeline

# ── Repo root inside Lakehouse (set after mounting or cloning) ───────────────
# [Unverified] Adjust to where the repo is accessible from this Fabric notebook
REPO_ROOT = "/lakehouse/default/Files/gateway-monitor"   # [Assumption] adjust to your mount point

# ── Derived paths ────────────────────────────────────────────────────────────
NOTEBOOKS_PATH = f"{REPO_ROOT}/starter/notebooks"
REPORT_PATH    = f"{REPO_ROOT}/starter/report"

print(f"[deploy] WORKSPACE_ID  = {WORKSPACE_ID}")
print(f"[deploy] LAKEHOUSE_NAME= {LAKEHOUSE_NAME}")
print(f"[deploy] KV_URI        = {KV_URI}")
print(f"[deploy] REPO_ROOT     = {REPO_ROOT}")
print("[deploy] Parameters loaded. Verify values above before proceeding.")
"""

CELL_C_INFRA = """\
# =============================================================================
# (c) CREATE INFRASTRUCTURE — Lakehouse + Eventhouse
# Label: [NET-NEW]
# [Unverified — sempy.fabric SDK call signatures are assumed from semantic-link-labs
#  source code and docs at https://semantic-link-labs.readthedocs.io/]
# =============================================================================

# ── Key Vault: retrieve SP credentials ──────────────────────────────────────
# [Unverified] notebookutils.credentials.getSecret() is the Fabric-native KV accessor.
# Falls back to azure-keyvault-secrets SDK if notebookutils unavailable.

def get_kv_secret(kv_uri: str, secret_name: str) -> str:
    \\"\\"\\"Retrieve a secret from Azure Key Vault. [Unverified] — two paths tried.\\"\\"\\"
    try:
        # Path 1: Fabric-native notebookutils (preferred in Fabric notebook context)
        # [Unverified] exact notebookutils.credentials API signature
        import notebookutils  # noqa: F401
        return notebookutils.credentials.getSecret(kv_uri, secret_name)
    except Exception as e_nu:
        print(f"[WARN] notebookutils.credentials unavailable: {e_nu} — trying azure-keyvault-secrets")
    try:
        # Path 2: azure-keyvault-secrets SDK with DefaultAzureCredential (SP/MI)
        from azure.identity import DefaultAzureCredential
        from azure.keyvault.secrets import SecretClient
        cred   = DefaultAzureCredential()
        client = SecretClient(vault_url=kv_uri, credential=cred)
        return client.get_secret(secret_name).value
    except Exception as e_sdk:
        raise RuntimeError(
            f"[TODO] Could not fetch KV secret '{secret_name}': {e_sdk}\\n"
            "Ensure the SP/MI running this notebook has Key Vault Secrets User role."
        )

try:
    _sp_client_id = get_kv_secret(KV_URI, SP_CLIENT_ID_SECRET)
    _sp_tenant_id = get_kv_secret(KV_URI, SP_TENANT_ID_SECRET)
    _sp_secret    = get_kv_secret(KV_URI, SP_SECRET_SECRET)
    print("[deploy] Key Vault secrets retrieved OK")
except Exception as e:
    print(f"[ERROR] {e}")
    print("[TODO] Fix Key Vault access before continuing. Subsequent cells will fail.")
    _sp_client_id = _sp_tenant_id = _sp_secret = None


# ── sempy.fabric: Create Lakehouse if missing ────────────────────────────────
# [Unverified] sempy.fabric.create_lakehouse() — confirmed in semantic-link-labs source
# but exact param names (workspace, lakehouse_name) may differ across versions.
# Reference: https://semantic-link-labs.readthedocs.io/en/stable/sempy.fabric.html

try:
    import sempy.fabric as fabric  # [Unverified] package: semantic-link-labs

    existing_lakehouses = fabric.list_lakehouses(workspace=WORKSPACE_ID)
    lh_names = existing_lakehouses["Lakehouse Name"].tolist() if not existing_lakehouses.empty else []

    if LAKEHOUSE_NAME not in lh_names:
        print(f"[deploy] Creating Lakehouse '{LAKEHOUSE_NAME}'...")
        # [Unverified] create_lakehouse signature: may require display_name kwarg
        fabric.create_lakehouse(lakehouse_name=LAKEHOUSE_NAME, workspace=WORKSPACE_ID)
        print(f"[deploy] Lakehouse '{LAKEHOUSE_NAME}' created.")
    else:
        print(f"[deploy] Lakehouse '{LAKEHOUSE_NAME}' already exists — skipping creation.")

except Exception as e:
    print(f"[TODO/Unverified] sempy.fabric Lakehouse create failed: {e}")
    print("  Manual fallback: create Lakehouse via Fabric UI before re-running.")


# ── sempy.fabric: Create Eventhouse if enabled ───────────────────────────────
# [Unverified] fabric.create_eventhouse() — method existence assumed from
# semantic-link-labs development branch; may not be available in stable release.

if CREATE_EVENTHOUSE:
    try:
        existing_eh = fabric.list_eventhouses(workspace=WORKSPACE_ID)
        eh_names = existing_eh["Eventhouse Name"].tolist() if not existing_eh.empty else []

        if EVENTHOUSE_NAME not in eh_names:
            print(f"[deploy] Creating Eventhouse '{EVENTHOUSE_NAME}'...")
            # [Unverified] exact method name; alternative: use Fabric REST API directly
            fabric.create_eventhouse(eventhouse_name=EVENTHOUSE_NAME, workspace=WORKSPACE_ID)
            print(f"[deploy] Eventhouse '{EVENTHOUSE_NAME}' created.")
        else:
            print(f"[deploy] Eventhouse '{EVENTHOUSE_NAME}' already exists — skipping.")
    except AttributeError:
        print("[TODO/Unverified] fabric.create_eventhouse() not available in this sempy version.")
        print("  Manual fallback: create Eventhouse via Fabric UI.")
    except Exception as e:
        print(f"[TODO/Unverified] Eventhouse create failed: {e}")
else:
    print("[deploy] CREATE_EVENTHOUSE=False — skipping Eventhouse creation.")
"""

CELL_D_NOTEBOOKS = """\
# =============================================================================
# (d) UPLOAD NOTEBOOKS — Import medallion Python notebooks via Fabric REST
# Label: [NET-NEW]
# [Unverified — Fabric REST notebook import schema is assumed from:
#  https://learn.microsoft.com/en-us/rest/api/fabric/notebook/items/create-notebook
#  The exact definition.parts format for .py-sourced notebooks requires
#  Phase 5 validation. The notebook may need to be in .ipynb format.]
# =============================================================================

import base64
import json
import os
import requests
from pathlib import Path

FABRIC_API_BASE = "https://api.fabric.microsoft.com/v1"

def get_fabric_token(tenant_id: str, client_id: str, client_secret: str) -> str:
    \\"\\"\\"Get a Bearer token for the Fabric REST API using SP credentials.
    [Unverified] scope 'https://api.fabric.microsoft.com/.default' assumed.
    \\"\\"\\"
    try:
        from azure.identity import ClientSecretCredential
        cred  = ClientSecretCredential(tenant_id, client_id, client_secret)
        token = cred.get_token("https://api.fabric.microsoft.com/.default")
        return token.token
    except Exception as e:
        raise RuntimeError(f"[TODO] Token acquisition failed: {e}\\n"
                           "Verify SP credentials and Fabric API permissions.")

def import_notebook_rest(
    workspace_id: str,
    token: str,
    display_name: str,
    py_source: str,
) -> dict:
    \\"\\"\\"
    POST a Python notebook to Fabric workspace via REST API.
    [Unverified] The definition.parts schema for a Python-sourced notebook:
      - payloadType: 'InlineBase64'
      - path: 'notebook-content.py'   <-- [Assumption] correct path name
    The API may require .ipynb wrapping instead of raw .py.
    Validate in Phase 5 and adjust path/format accordingly.
    \\"\\"\\"
    encoded = base64.b64encode(py_source.encode("utf-8")).decode("ascii")
    payload = {
        "displayName": display_name,
        "type": "Notebook",
        "definition": {
            "format": "ipynb",   # [Unverified] 'ipynb' or 'py' accepted
            "parts": [
                {
                    "path": "notebook-content.py",  # [Assumption] path name
                    "payload": encoded,
                    "payloadType": "InlineBase64",
                }
            ],
        },
    }
    url = f"{FABRIC_API_BASE}/workspaces/{workspace_id}/notebooks"
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    }
    resp = requests.post(url, headers=headers, json=payload, timeout=60)
    if resp.status_code in (200, 201, 202):
        return resp.json()
    raise RuntimeError(
        f"[TODO] Notebook import failed: HTTP {resp.status_code}\\n"
        f"  Response: {resp.text[:400]}"
    )


# ── Notebooks to import (in dependency order) ────────────────────────────────
NOTEBOOKS_TO_IMPORT = [
    "gateway_bronze_lib.py",   # Shared lib — must be present before bronze notebook runs
    "01_bronze_ingest.py",
    "02_silver_correlate.py",
    "03_gold_aggregate.py",
]

if _sp_client_id and _sp_tenant_id and _sp_secret:
    try:
        fabric_token = get_fabric_token(_sp_tenant_id, _sp_client_id, _sp_secret)
        print("[deploy] Fabric API token acquired")

        for nb_filename in NOTEBOOKS_TO_IMPORT:
            nb_path = Path(NOTEBOOKS_PATH) / nb_filename
            display_name = nb_filename.replace(".py", "").replace("_", " ").title()

            try:
                with open(nb_path, "r", encoding="utf-8") as f:
                    py_source = f.read()
            except FileNotFoundError:
                print(f"[ERROR] Notebook source not found: {nb_path}")
                print(f"[TODO]  Ensure repo is cloned/mounted at REPO_ROOT={REPO_ROOT}")
                continue

            try:
                result = import_notebook_rest(WORKSPACE_ID, fabric_token, display_name, py_source)
                item_id = result.get("id", "unknown")
                print(f"[deploy] Imported '{display_name}' → item_id={item_id}")
            except RuntimeError as e:
                print(f"[TODO/Unverified] {e}")

    except RuntimeError as e:
        print(f"[ERROR] {e}")
else:
    print("[SKIP] SP credentials not available — skipping notebook import.")
    print("[TODO] Fix Key Vault access in cell (c) and re-run.")
"""

CELL_E_PIPELINE = """\
# =============================================================================
# (e) CREATE DATA PIPELINE — bronze → silver → gold (15-min schedule)
# Label: [NET-NEW]
# [Unverified — Fabric Data Pipeline REST API schema is not fully documented.
#  The pipeline definition format below is based on:
#  https://learn.microsoft.com/en-us/rest/api/fabric/datapipeline/items/create-data-pipeline
#  and community analysis of exported pipeline JSON.
#  The activitiesJson format MUST be validated in Phase 5.]
# =============================================================================

import json
import requests

PIPELINE_DISPLAY_NAME = "GatewayMonitor_Orchestration"

# [Unverified] Pipeline activity JSON — notebook IDs must be real GUIDs from step (d)
# Replace NB_ID_BRONZE / SILVER / GOLD with actual item IDs returned in step (d)
# or look them up via GET /v1/workspaces/{workspaceId}/notebooks

PIPELINE_DEFINITION = {
    "name": PIPELINE_DISPLAY_NAME,
    "description": "Gateway Monitor bronze→silver→gold medallion pipeline. "
                   "Runs 01_bronze_ingest → 02_silver_correlate → 03_gold_aggregate sequentially.",
    # [Unverified] 'properties' key name and structure assumed from ADF/Synapse pipeline format
    "properties": {
        "activities": [
            {
                "name": "Run_Bronze_Ingest",
                "type": "TridentNotebook",      # [Unverified] activity type name in Fabric
                "dependsOn": [],
                "typeProperties": {
                    "notebookId": "<NB_ID_BRONZE>",    # [TODO] replace with real notebook item GUID
                    "workspaceId": WORKSPACE_ID,
                }
            },
            {
                "name": "Run_Silver_Correlate",
                "type": "TridentNotebook",
                "dependsOn": [
                    {"activity": "Run_Bronze_Ingest", "dependencyConditions": ["Succeeded"]}
                ],
                "typeProperties": {
                    "notebookId": "<NB_ID_SILVER>",    # [TODO] replace with real notebook item GUID
                    "workspaceId": WORKSPACE_ID,
                }
            },
            {
                "name": "Run_Gold_Aggregate",
                "type": "TridentNotebook",
                "dependsOn": [
                    {"activity": "Run_Silver_Correlate", "dependencyConditions": ["Succeeded"]}
                ],
                "typeProperties": {
                    "notebookId": "<NB_ID_GOLD>",      # [TODO] replace with real notebook item GUID
                    "workspaceId": WORKSPACE_ID,
                }
            },
        ],
        # [Unverified] Schedule trigger format — may differ from ADF recurrence
        "triggers": [
            {
                "name": "Every15Min",
                "type": "ScheduleTrigger",
                "typeProperties": {
                    "recurrence": {
                        "frequency": "Minute",
                        "interval": 15,
                    }
                }
            }
        ]
    }
}

def create_pipeline(workspace_id: str, token: str, pipeline_def: dict) -> dict:
    \\"\\"\\"
    Create a Fabric Data Pipeline via REST.
    [Unverified] POST /v1/workspaces/{workspaceId}/dataPipelines
    Request body format assumed — validate schema in Phase 5.
    \\"\\"\\"
    encoded_def = base64.b64encode(
        json.dumps(pipeline_def).encode("utf-8")
    ).decode("ascii")

    payload = {
        "displayName": pipeline_def["name"],
        "type": "DataPipeline",
        "definition": {
            "parts": [
                {
                    "path": "pipeline-content.json",  # [Assumption]
                    "payload": encoded_def,
                    "payloadType": "InlineBase64",
                }
            ]
        }
    }
    url = f"{FABRIC_API_BASE}/workspaces/{workspace_id}/dataPipelines"
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    }
    resp = requests.post(url, headers=headers, json=payload, timeout=60)
    if resp.status_code in (200, 201, 202):
        return resp.json()
    raise RuntimeError(
        f"[TODO/Unverified] Pipeline create failed: HTTP {resp.status_code}\\n"
        f"  Response: {resp.text[:400]}\\n"
        "  Manual fallback: create the pipeline in the Fabric UI and connect the 3 notebook activities."
    )

if CREATE_PIPELINE and _sp_client_id:
    try:
        # Re-use token acquired in cell (d)
        result = create_pipeline(WORKSPACE_ID, fabric_token, PIPELINE_DEFINITION)
        print(f"[deploy] Pipeline created: {result.get('id', 'unknown')}")
        print("[TODO] Replace <NB_ID_BRONZE/SILVER/GOLD> placeholders above with real notebook item GUIDs.")
        print("[TODO] Enable and test the schedule trigger in the Fabric UI.")
    except RuntimeError as e:
        print(f"[TODO/Unverified] {e}")
else:
    print("[SKIP] CREATE_PIPELINE=False or no SP credentials — skipping pipeline creation.")
"""

CELL_F_REPORT = """\
# =============================================================================
# (f) IMPORT SEMANTIC MODEL + REPORT — PBIP via Fabric REST
# Label: [NET-NEW + ADAPTED]
# [Unverified — Two approaches documented; Path A (Git Integration) preferred
#  but requires workspace Git connection. Path B (REST import) is lower-level.
#  Both require Phase 5 validation.]
# =============================================================================

# ── PATH A: Fabric Git Integration (recommended) ──────────────────────────────
# [Unverified] Assumes workspace is connected to a Git repo containing the PBIP files.
# Steps (manual setup required before this cell):
#   1. In Fabric UI: Workspace Settings → Git Integration → Connect to your repo
#   2. Commit starter/report/ to the connected branch
#   3. In Fabric UI: Git Integration → Update → Fabric updates the semantic model + report
#
# This is the most reliable path for PBIP files and matches the fuam-basic pattern.
# After Git sync, Power BI Desktop must open the report once to finalize measure bindings.

print("[deploy] PATH A: Git Integration")
print("  [TODO] Ensure workspace Git Integration is configured and repo is connected.")
print("  [TODO] Commit starter/report/gateway_monitor.report.json and definition.pbir to the branch.")
print("  [TODO] Trigger Fabric Git sync (UI or REST: POST /v1/workspaces/{workspaceId}/git/updateFromGit).")
print()


# ── PATH B: Direct REST import of semantic model definition ──────────────────
# [Unverified] Uses Fabric REST to create a SemanticModel item from the PBIP definition.
# https://learn.microsoft.com/en-us/rest/api/fabric/semanticmodel/items/create-semantic-model
# The definition.pbir and report.json must be base64-encoded and POSTed.

def import_pbip_rest(workspace_id: str, token: str, display_name: str, report_json_path: str, pbir_path: str):
    \\"\\"\\"
    Import a Power BI report from PBIP files via Fabric REST.
    [Unverified] Exact parts schema and endpoint are assumed from:
    https://learn.microsoft.com/en-us/rest/api/fabric/report/items/create-report
    \\"\\"\\"
    import base64
    from pathlib import Path

    parts = []
    for file_path, part_path in [
        (report_json_path, "report.json"),
        (pbir_path, "definition.pbir"),
    ]:
        try:
            with open(file_path, "rb") as f:
                encoded = base64.b64encode(f.read()).decode("ascii")
            parts.append({
                "path": part_path,
                "payload": encoded,
                "payloadType": "InlineBase64",
            })
        except FileNotFoundError:
            print(f"[WARN] File not found for REST import: {file_path}")

    if not parts:
        raise RuntimeError("[TODO] No report files found — cannot import via REST.")

    payload = {
        "displayName": display_name,
        "type": "Report",
        "definition": {"parts": parts},
    }
    url = f"https://api.fabric.microsoft.com/v1/workspaces/{workspace_id}/reports"
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    }
    import requests
    resp = requests.post(url, headers=headers, json=payload, timeout=60)
    if resp.status_code in (200, 201, 202):
        return resp.json()
    raise RuntimeError(
        f"[TODO/Unverified] Report import failed: HTTP {resp.status_code}\\n"
        f"  Response: {resp.text[:400]}\\n"
        "  Manual fallback: open starter/report/gateway_monitor.report.json in Power BI Desktop."
    )

print("[deploy] PATH B: REST import (fallback if Git Integration not configured)")

REPORT_JSON = f"{REPORT_PATH}/gateway_monitor.report.json"
PBIR_FILE   = f"{REPORT_PATH}/definition.pbir"

# Commented out by default — uncomment to use REST import path
# try:
#     result = import_pbip_rest(WORKSPACE_ID, fabric_token, "Gateway Monitor", REPORT_JSON, PBIR_FILE)
#     print(f"[deploy] Report imported: {result.get('id', 'unknown')}")
# except RuntimeError as e:
#     print(f"[TODO/Unverified] {e}")

print("  [TODO] Uncomment the block above OR use Path A (Git Integration).")
print("  [TODO] After import: open the report in Power BI Desktop once to finalize measure bindings.")
print("  [TODO] Connect the semantic model's DirectLake source to the Lakehouse gold Delta tables.")
"""

CELL_G_VALIDATE = """\
# =============================================================================
# (g) VALIDATE — check tables exist, print next steps
# Label: [NET-NEW]
# [Unverified — spark.read.format("delta").load() path assumes Lakehouse is attached
#  to this notebook via notebookutils.lakehouse or the Fabric notebook default lakehouse.]
# =============================================================================

from pyspark.sql import SparkSession

spark = SparkSession.builder.getOrCreate()

# Expected gold tables from 03_gold_aggregate.py
EXPECTED_TABLES = [
    "bronze_query_execution",
    "bronze_query_start",
    "bronze_system_counter",
    "bronze_network_metrics",
    "bronze_event_log",
    "bronze_disk_spool",
    "bronze_gateway_inventory",
    "silver_query_execution",
    "silver_triage",
    "silver_identity_attribution",
    "silver_network_correlated",
    "gold_gateway_health",
    "gold_query_performance",
    "gold_cluster_load",
    "gold_dim_gateway",
]

# [Unverified] Fabric notebook default lakehouse table path
TABLES_BASE = "Tables"   # relative to the attached Lakehouse

print("=" * 60)
print("VALIDATION — checking Delta tables")
print("=" * 60)

results = {}
for table in EXPECTED_TABLES:
    try:
        df = spark.read.format("delta").load(f"{TABLES_BASE}/{table}")
        row_count = df.count()
        results[table] = ("OK", row_count)
        print(f"  [OK]   {table:<40} {row_count:>8} rows")
    except Exception as e:
        msg = str(e)[:80]
        results[table] = ("MISSING", msg)
        print(f"  [MISSING] {table:<40} {msg}")

ok_count      = sum(1 for v in results.values() if v[0] == "OK")
missing_count = len(results) - ok_count
print()
print(f"  Summary: {ok_count}/{len(EXPECTED_TABLES)} tables present")
print()

if missing_count == 0:
    print("[PASS] All expected Delta tables found.")
    print()
    print("NEXT STEPS:")
    print("  1. Verify the Data Pipeline schedule is active (Fabric UI → Pipeline → Schedule).")
    print("  2. Run starter/kql/01_identity_join.kql in the Eventhouse to verify KQL queries.")
    print("  3. Open the Power BI report in Desktop to finalize DirectLake measure bindings.")
    print("  4. Configure Activator rules (see starter/alerting/activator-rules.json).")
    print("  5. Export the Fabric item bundle (see deploy/README.md §Generating the one-click bundle).")
else:
    print(f"[WARN] {missing_count} tables missing.")
    print("  Check that the pipeline ran successfully: Fabric UI → Pipeline → Run History.")
    print("  If bronze tables are missing: verify collector JSON files landed in Files/bronze_landing/")
    print("  Re-run pipeline manually after confirming landing files are present.")
"""

# ── Assemble cells with auto-generated IDs ────────────────────────────────────
import uuid

cells_raw = [
    md(CELL_A_MD),
    code(CELL_INSTALL),
    md("## (b) Parameters"),
    code(CELL_B_PARAMS, tags=["parameters"]),
    md("## (c) Create Lakehouse + Eventhouse"),
    code(CELL_C_INFRA),
    md("## (d) Upload Medallion Notebooks"),
    code(CELL_D_NOTEBOOKS),
    md("## (e) Create Data Pipeline"),
    code(CELL_E_PIPELINE),
    md("## (f) Import Semantic Model + Report"),
    code(CELL_F_REPORT),
    md("## (g) Validate"),
    code(CELL_G_VALIDATE),
]

# Assign stable short IDs (8-char hex)
for i, cell in enumerate(cells_raw):
    cell["id"] = f"{i:02d}{uuid.uuid4().hex[:6]}"

notebook = {
    "nbformat": 4,
    "nbformat_minor": 5,
    "metadata": {
        "kernelspec": {
            "display_name": "Python 3 (ipykernel)",
            "language": "python",
            "name": "python3",
        },
        "language_info": {
            "name": "python",
            "version": "3.11.0",
        },
        "fabric": {
            "environment": {
                "description": "Fabric Spark 3.4 / Runtime 1.2",
            },
            "_comment": "[Unverified] Fabric notebook metadata — format assumed from community examples",
        },
    },
    "cells": cells_raw,
}

OUTPUT.write_text(json.dumps(notebook, indent=2, ensure_ascii=False), encoding="utf-8")
print(f"Written: {OUTPUT}")
