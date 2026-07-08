# Databricks notebook source
# =============================================================================
# 02_ingest_workspace_api.py  |  Label: [Reconstructed-from-transcript — Unverified]
#
# USER-SCOPE ingestion (myorg/*). This path WORKED in the 2026-07-02 session:
# token acquired, rows landed, audit rows written. Still Unverified as committed code
# (reconstructed from the transcript). Depends on helpers in 01_config_auth.py.
#
# Run order in Databricks:  %run ./01_config_auth   then this notebook.
# Canonical pipeline is ../notebooks/ (Fabric-native). See this folder's README.md.
# =============================================================================

# COMMAND ----------

# MAGIC %run ./01_config_auth

# COMMAND ----------

def ingest_workspace_assets():
    """Ingest user-scope Power BI assets into bronze.raw_* using the caller's SPN.

    Walks: groups (workspaces) -> per-workspace datasets -> per-dataset refreshes +
    datasources. Every payload lands as a raw envelope; every REST call is audited.
    Tolerant: a non-200 on any leg logs the status and continues (no hard fail)."""
    log_job_run("ingest_workspace_assets", "START")
    token = get_access_token()
    ensure_catalog()

    status, groups = pbi_get_resilient(token, "groups")
    if status != 200 or not groups:
        log_job_run("ingest_workspace_assets", "END", f"groups status={status}")
        return
    insert_raw_api_payload("raw_groups", "groups", groups, "myorg/groups")

    for ws in groups.get("value", []):
        ws_id = safe_value(ws, "id")
        if not ws_id:
            continue

        # datasets in the workspace
        s, datasets = pbi_get_resilient(token, f"groups/{ws_id}/datasets")
        if s == 200 and datasets:
            insert_raw_api_payload(
                "raw_datasets", "datasets", datasets, f"myorg/groups/{ws_id}/datasets"
            )
            for ds in datasets.get("value", []):
                ds_id = safe_value(ds, "id")
                if not ds_id:
                    continue
                # refresh history
                s_r, refreshes = pbi_get_resilient(
                    token, f"groups/{ws_id}/datasets/{ds_id}/refreshes"
                )
                if s_r == 200 and refreshes:
                    insert_raw_api_payload(
                        "raw_refreshes", "refreshes", refreshes,
                        f"myorg/groups/{ws_id}/datasets/{ds_id}/refreshes",
                    )
                # datasources (gateway binding)
                s_d, datasources = pbi_get_resilient(
                    token, f"groups/{ws_id}/datasets/{ds_id}/datasources"
                )
                if s_d == 200 and datasources:
                    insert_raw_api_payload(
                        "raw_datasources", "datasources", datasources,
                        f"myorg/groups/{ws_id}/datasets/{ds_id}/datasources",
                    )

    log_job_run("ingest_workspace_assets", "END", "ok")


# COMMAND ----------

# Entry point — explicit; NOT auto-run on %run of 01_config_auth.
ingest_workspace_assets()
