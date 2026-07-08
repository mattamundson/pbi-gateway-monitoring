# Databricks notebook source
# =============================================================================
# 03_ingest_admin_api.py  |  Label: [Reconstructed-from-transcript — Unverified, BLOCKED]
#
# ADMIN-SCOPE ingestion (myorg/admin/*).  ***THE TERMINAL BLOCKER LIVES HERE.***
# The service principal sp-gatewaymon-dbx-dev lacks Power BI tenant-admin API rights,
# so every admin call returns 401 / 0 rows and the four admin bronze tables stay
# empty. The notebook PLUMBING is healthy (audit rows land) — the gap is tenant
# permissions, not code. Keep as a diagnostic harness until the grant is made.
#
# UNBLOCK (Amo/tenant admin):
#   Power BI admin portal -> Tenant settings -> "Allow service principals to use
#   read-only admin APIs" -> enable + add the SPN's security group;  OR grant
#   Tenant.Read.All (application) with admin consent.
#
# Canonical pipeline is ../notebooks/ (Fabric-native). See this folder's README.md and
# ../../docs/session-logs/2026-07-02-databricks-first-pivot.md.
# =============================================================================

# COMMAND ----------

# MAGIC %run ./01_config_auth

# COMMAND ----------

# (entity_type, admin REST path, bronze table)
_ADMIN_ENTITIES = [
    ("groups_admin", "admin/groups?$top=5000", "raw_groups_admin"),
    ("capacities", "admin/capacities", "raw_capacities"),
    ("gateways", "admin/gateways", "raw_gateways"),
    ("datasources", "admin/datasources", "raw_datasources"),
]


def ingest_admin_assets():
    """Ingest tenant-admin assets into bronze.raw_*_admin.

    DIAGNOSTIC: with an unprivileged SPN every call 401s and lands 0 rows — which is
    exactly what pbi_get_resilient + log_api_audit make visible. When the admin grant
    is in place these tables fill and silver can join workspace<->capacity<->gateway."""
    log_job_run("ingest_admin_assets", "START")
    token = get_access_token()
    ensure_catalog()

    any_ok = False
    for entity_type, path, table in _ADMIN_ENTITIES:
        status, body = pbi_get_resilient(token, path)
        if status == 200 and body:
            insert_raw_api_payload(table, entity_type, body, f"myorg/{path}")
            any_ok = True
        else:
            # 401 here == the known blocker; the audit row records it.
            print(
                f"[ADMIN-BLOCKED] {path} -> status={status} (SPN lacks admin API rights?)"
            )

    log_job_run(
        "ingest_admin_assets", "END", "ok" if any_ok else "all-admin-calls-blocked"
    )


# COMMAND ----------

# Entry point — explicit.
ingest_admin_assets()
