# Databricks notebook source
# =============================================================================
# 01_config_auth.py  |  Label: [Reconstructed-from-transcript — Unverified, Amo-gated]
#
# HELPERS ONLY. This notebook defines functions + config and RUNS NOTHING at import
# (%run) time. The earlier working copy had stray bottom-of-cell execution lines
# (`access_token = get_access_token(); ingest_workspace_assets()`) that made every
# `%run` throw NameError — deliberately omitted here.
#
# Canonical pipeline is the Fabric-native medallion in ../notebooks/. See this folder's
# README.md, ../../docs/session-logs/2026-07-02-databricks-first-pivot.md, DECISIONS D10.
# NOT verified. Reference plumbing only.
# =============================================================================

# COMMAND ----------

import json
import time
import requests  # available on Databricks runtime
from pyspark.sql import functions as F

# ── Config (Unity Catalog) ───────────────────────────────────────────────────
CATALOG = "dbw_gatewaymon_dev"
BRONZE = f"{CATALOG}.bronze"
SILVER = f"{CATALOG}.silver"
GOLD = f"{CATALOG}.gold"

PBI_SCOPE = "https://analysis.windows.net/powerbi/api/.default"
PBI_BASE = "https://api.powerbi.com/v1.0/myorg"

# Secrets come from a Databricks secret scope — NEVER hard-code credentials.
# Configure: databricks secrets create-scope gatewaymon ; then put tenant_id/client_id/client_secret.
_SECRET_SCOPE = "gatewaymon"


def _secret(key):
    """Read a secret; dbutils is injected by the Databricks runtime."""
    return dbutils.secrets.get(scope=_SECRET_SCOPE, key=key)  # noqa: F821


# COMMAND ----------

# ── Auth: OAuth2 client-credentials ──────────────────────────────────────────
def get_access_token():
    """Acquire a bearer token for the Power BI REST API via client-credentials.

    Returns the access_token string. Raises on non-200 (a common failure mode is
    UNREPLACED placeholder tenant/client ids, which return a sign-in HTML page
    instead of JSON — check the response is JSON before trusting it)."""
    tenant_id = _secret("tenant_id")
    resp = requests.post(
        f"https://login.microsoftonline.com/{tenant_id}/oauth2/v2.0/token",
        data={
            "grant_type": "client_credentials",
            "client_id": _secret("client_id"),
            "client_secret": _secret("client_secret"),
            "scope": PBI_SCOPE,
        },
        timeout=30,
    )
    resp.raise_for_status()
    return resp.json()["access_token"]


# COMMAND ----------

# ── Resilient REST GET + audit ───────────────────────────────────────────────
def pbi_get(access_token, path):
    """Single GET against the Power BI REST API. `path` is relative to /myorg."""
    url = path if path.startswith("http") else f"{PBI_BASE}/{path.lstrip('/')}"
    return requests.get(
        url, headers={"Authorization": f"Bearer {access_token}"}, timeout=60
    )


def pbi_get_resilient(access_token, path, retries=3, backoff=2.0):
    """GET with retry/backoff on 429/5xx. Returns (status_code, json_or_none).

    Logs every call to bronze.api_call_audit via log_api_audit() so a 401/empty
    result is observable rather than silent (this is how the admin-API 401 blocker
    was diagnosed: plumbing healthy, audit rows landing, admin tables empty)."""
    last_status = None
    for attempt in range(retries):
        r = pbi_get(access_token, path)
        last_status = r.status_code
        if r.status_code == 200:
            body = r.json()
            log_api_audit(path, 200, _row_count(body))
            return 200, body
        if r.status_code in (429, 500, 502, 503, 504):
            time.sleep(backoff * (attempt + 1))
            continue
        # 401/403/404 etc. — do not retry; record and return
        log_api_audit(path, r.status_code, 0)
        return r.status_code, None
    log_api_audit(path, last_status or -1, 0)
    return last_status or -1, None


def _row_count(body):
    if isinstance(body, dict) and isinstance(body.get("value"), list):
        return len(body["value"])
    return 1 if body else 0


# COMMAND ----------

# ── Bronze envelope + raw insert ─────────────────────────────────────────────
def bronze_envelope(entity_type, payload_obj, source_name):
    """Wrap a raw API payload as a one-row bronze record: (ingestion_ts, source_file,
    entity_type, payload) where payload is the JSON string as received."""
    return {
        "ingestion_ts": None,  # set by current_timestamp() on write
        "entity_type": entity_type,
        "source_file": source_name,
        "payload": json.dumps(payload_obj),
    }


def insert_raw_api_payload(table, entity_type, payload_obj, source_name):
    """Append one raw-payload row to bronze.<table> (schema-on-read; parsed in silver)."""
    rec = bronze_envelope(entity_type, payload_obj, source_name)
    df = spark.createDataFrame([rec]).withColumn(  # noqa: F821
        "ingestion_ts", F.current_timestamp()
    )
    df.write.mode("append").saveAsTable(f"{BRONZE}.{table}")


# COMMAND ----------

# ── Audit logging ────────────────────────────────────────────────────────────
def log_api_audit(path, status_code, row_count):
    """Record one REST call outcome to bronze.api_call_audit."""
    rec = {"api_path": path, "status_code": int(status_code), "row_count": int(row_count)}
    df = spark.createDataFrame([rec]).withColumn(  # noqa: F821
        "audit_ts", F.current_timestamp()
    )
    df.write.mode("append").saveAsTable(f"{BRONZE}.api_call_audit")


def log_job_run(job_name, phase, detail=""):
    """Record a job START/END marker to bronze.job_run_audit."""
    rec = {"job_name": job_name, "phase": phase, "detail": detail}
    df = spark.createDataFrame([rec]).withColumn(  # noqa: F821
        "audit_ts", F.current_timestamp()
    )
    df.write.mode("append").saveAsTable(f"{BRONZE}.job_run_audit")


def safe_value(d, key, default=None):
    """Null-safe dict access for building bronze rows from ragged API payloads."""
    return d.get(key, default) if isinstance(d, dict) else default


# COMMAND ----------

# ── Unity Catalog bootstrap (idempotent) ─────────────────────────────────────
def ensure_catalog():
    spark.sql(f"CREATE CATALOG IF NOT EXISTS {CATALOG}")  # noqa: F821
    for schema in (BRONZE, SILVER, GOLD):
        spark.sql(f"CREATE SCHEMA IF NOT EXISTS {schema}")  # noqa: F821
