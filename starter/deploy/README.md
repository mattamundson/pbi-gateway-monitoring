# Gateway Monitor — Deploy Guide

> **[Unverified — requires Fabric tenant pilot]**
> Every step in this guide has been authored against public Fabric REST API documentation
> and semantic-link-labs source code. None of it has been validated in a live Fabric tenant.
> All REST endpoints, SDK call signatures, and permission requirements are marked with
> `[Unverified]` where exact behavior could not be confirmed from documentation alone.
> Treat this as a validated *intent* document for your Phase 5 pilot, not a runbook.

---

## What lives here

| File | Purpose |
|------|---------|
| `Deploy_GatewayMonitor.ipynb` | Fabric-notebook orchestrator — creates workspace items, uploads notebooks, creates pipeline, imports semantic model + report |
| `run_local_smoke.py` | Local PySpark smoke test against synthetic fixtures — proves medallion logic end-to-end before any tenant work |
| `README.md` | This file |

---

## Deploy order

```
1. Prerequisites (manual, ~30 min)
   └── Service Principal, Key Vault, Fabric workspace, capacity (F8+)

2. Local smoke test (optional, ~5 min)
   └── bash/Linux/macOS: JAVA_HOME=/usr/lib/jvm/java-17-openjdk-amd64 python deploy/run_local_smoke.py
   └── PowerShell/Windows: $env:JAVA_HOME='C:\Program Files\Eclipse Adoptium\jdk-17'; python deploy/run_local_smoke.py
       (the bash-style "VAR=value python ..." prefix form does NOT work in PowerShell)
       Also requires Python 3.11 (pyspark 3.5.x does not support 3.13/3.14) and,
       on Windows, winutils.exe/hadoop.dll on HADOOP_HOME (else SparkContext dies
       at createTempDir).

3. Open Deploy_GatewayMonitor.ipynb in a Fabric notebook
   └── Fill in the parameters cell (workspace_id, capacity_id, lakehouse_name, kv_uri…)
   └── Run cells top to bottom

4. After first successful run:
   └── Export serialized item bundle (see §Generating the one-click bundle below)

5. Configure Windows Task Scheduler on gateway hosts
   └── Every 5 min: collectors/Collect-GatewayLogs.ps1, Collect-DiskSpool.ps1, etc.
   └── Upload output JSON to Lakehouse Files/bronze_landing/

6. Verify end-to-end: run identity join KQL (kql/01_identity_join.kql) in Eventhouse
```

---

## What the deployment notebook does

**Cell (a) — Intro + prerequisites**
Documents F8+ capacity, Service Principal with Fabric Member role, Key Vault, and
the `semantic-link-labs` package requirement.

**Cell (b) — Parameters**
`workspace_id`, `capacity_id`, `lakehouse_name`, `kv_uri`, secret names,
`enable_fpm_bridge` flag. Edit these before running.

**Cell (c) — Create Lakehouse + Eventhouse**
Uses `sempy.fabric` / `semantic-link-labs` to create a Lakehouse and an Eventhouse
if they don't already exist. `[Unverified]` — exact SDK call signatures are labeled
in the notebook.

**Cell (d) — Upload medallion notebooks**
Imports `01_bronze_ingest.py`, `02_silver_correlate.py`, `03_gold_aggregate.py`,
and `gateway_bronze_lib.py` into the workspace as Fabric Notebook items via the
Fabric REST API (`POST /v1/workspaces/{workspaceId}/notebooks`).

**Cell (e) — Create Data Pipeline**
POSTs a pipeline definition that runs bronze→silver→gold on a 15-minute schedule
via Fabric REST (`POST /v1/workspaces/{workspaceId}/dataPipelines`).
`[Unverified]` — the pipeline activity JSON schema is labeled as assumed.

**Cell (f) — Import semantic model + report**
Imports `starter/report/gateway_monitor.report.json` and `definition.pbir`
via Fabric Git Integration or REST import. Documented with both approaches;
`[Unverified]` pending pilot.

**Cell (g) — Validate**
Checks that bronze/silver/gold Delta tables exist and prints next steps.

---

## Generating the fuam-basic-style one-click bundle
### (the `deployment_file.json` this repo does NOT include)

The GT-Analytics/fuam-basic model uses a `deployment_file.json` containing
**base64-encoded serialized Fabric item definitions** that are exported directly
from a running tenant. We deliberately **do not fabricate** this file with fake
payloads. It can only be minted after your first successful manual deploy.

### How to export after a successful deploy [Unverified]

For each Fabric item you want to include in the bundle:

```bash
# 1. Get the item definition (Fabric REST API)
#    [Unverified] Requires workspace Member + item Read permission on the SP
GET https://api.fabric.microsoft.com/v1/workspaces/{workspaceId}/items/{itemId}/getDefinition

# Response body contains:
# {
#   "definition": {
#     "parts": [
#       { "path": "...", "payload": "<base64>", "payloadType": "InlineBase64" }
#     ]
#   }
# }

# 2. Collect the parts for each item type:
#    - Lakehouse:        itemType = "Lakehouse"
#    - Notebook (×4):   itemType = "Notebook"
#    - DataPipeline:     itemType = "DataPipeline"
#    - SemanticModel:    itemType = "SemanticModel"
#    - Report:           itemType = "Report"
#    - Eventhouse:       itemType = "Eventhouse"  (if Activator/KQL used)

# 3. Assemble into deployment_file.json in the fuam-basic schema:
#    {
#      "version": "1.0",
#      "items": [
#        { "type": "Lakehouse", "displayName": "gateway_monitor_lh", "definition": { ... } },
#        ...
#      ]
#    }

# 4. Commit deployment_file.json to the repo.
#    Subsequent deploys: POST each item definition via
#    POST /v1/workspaces/{workspaceId}/items + payload from the file.
```

**Reference implementations to study:**
- [GT-Analytics/fuam-basic](https://github.com/GT-Analytics/fuam-basic) — the model this repo emulates
- [microsoft/fabric-toolbox deployment patterns](https://github.com/microsoft/fabric-toolbox)
- [Fabric REST API — Get Item Definition](https://learn.microsoft.com/en-us/rest/api/fabric/core/items/get-item-definition)

---

## Prerequisites checklist

- [ ] Fabric capacity F8 or higher (F64+ recommended for production)
- [ ] Service Principal (SP) registered in Entra ID
  - [ ] SP has **Fabric Member** role on target workspace
  - [ ] SP has **Contributor** on Key Vault
  - [ ] SP client secret stored in Key Vault as `gateway-monitor-sp-secret`
- [ ] Key Vault created, URI recorded
- [ ] Fabric workspace created (not My Workspace)
- [ ] `semantic-link-labs` installed in the Fabric environment:
  `%pip install semantic-link-labs` (run in Fabric notebook cell)
- [ ] Python 3.11 (PySpark 3.5.x does not support Python 3.13/3.14 — cloudpickle
      stack overflow), delta-spark 3.1.x (for local smoke test only). On Windows,
      also winutils.exe/hadoop.dll on HADOOP_HOME.

---

## Known [Unverified] items — must be validated in Phase 5 pilot

| ID | Item | Risk |
|----|------|------|
| U1 | `sempy.fabric.FabricRestClient` call signature for create_lakehouse / create_eventhouse | Medium — SDK evolves fast |
| U2 | Fabric REST `POST /notebooks` — exact `definition.parts` schema for .py notebooks | High — format differs from .ipynb |
| U3 | Fabric REST `POST /dataPipelines` — activity JSON schema for sequential notebook execution | High — not publicly documented |
| U4 | Fabric Git Integration PBIP import path and API endpoint | Medium |
| U5 | `gold_dim_gateway.GatewayNodeName` == `GatewayHostName` from spool/network collectors | Medium — may require config mapping |
| U6 | Activator rule import format (activator-rules.json) — UI creation may be required | High — import API not public |
| U7 | DirectLake semantic model auto-detect of Delta tables by name | Medium |
| U8 | EvaluationContext JSON key casing (`$.artifactId` vs `$.ArtifactId`) | Low — validate with real logs |
| U9 | Power BI PBIR report `definition.pbir` format — may require Desktop open to finalize bindings | High — hand-authored |

---

*Generated by: deployable scaffolding build — 2026-06-30*
*All items labeled [Unverified] pending Phase 5 Fabric tenant pilot.*
