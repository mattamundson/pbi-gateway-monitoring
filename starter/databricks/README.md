# `starter/databricks/` — Databricks-first ingestion (RECONSTRUCTED, UNVERIFIED)

> **Status: `[Reconstructed-from-transcript — Unverified, Amo-gated on admin-API grant]`**
>
> These notebooks are **not** the canonical pipeline. The canonical, locally-verified
> line is the Fabric-native medallion in `../notebooks/` (`01_bronze_ingest.py` →
> `02_silver_correlate.py` → `03_gold_aggregate.py`), tested by `../tests/`. **Do not
> treat anything here as proven.** It is reference plumbing reconstructed from the
> 2026-07-02 working session so the design isn't lost — see the full record in
> `../../docs/session-logs/2026-07-02-databricks-first-pivot.md` and `DECISIONS.md` **D10**.

## What this is

An alternative compute path that runs the ingestion on **Azure Databricks** (Unity
Catalog) instead of Fabric Spark, landing raw **Power BI REST API** payloads into a
bronze/silver/gold medallion. It was explored because the Fabric *trial* Spark pool
throttled; it is kept as an **optional production-compute backend**, not a requirement
(the local `pbi-spark` harness already validates the Fabric-native transforms with no
tenant — see `../tests/test_medallion_spark.py`).

## Files

| File | Role |
|---|---|
| `01_config_auth.py` | **Helpers only** — OAuth client-credentials token, resilient REST GET, `bronze_envelope`, raw-payload insert, API/job audit logging, Unity Catalog bootstrap. Defines functions; runs nothing on `%run`. |
| `02_ingest_workspace_api.py` | **User-scope** ingestion (`myorg/groups`, datasets, refreshes, datasources) → `bronze.raw_*`. This path WORKED in-session. |
| `03_ingest_admin_api.py` | **Admin-scope** ingestion (`myorg/admin/*`). **BLOCKED:** the SPN lacks tenant-admin API rights → `401` / 0 rows. Kept as a diagnostic harness. |

## The blocker (Amo-gated)

`03_ingest_admin_api.py` returns 0 rows because the service principal
`sp-gatewaymon-dbx-dev` cannot call the Power BI **admin** APIs. A **tenant admin**
must either:
1. Power BI admin portal → Tenant settings → **"Allow service principals to use
   read-only admin APIs"** → enable + add the SPN's security group; **or**
2. Grant the SPN `Tenant.Read.All` (application) with admin consent.

Until then, build silver **workspace-ingestion-first** so it tolerates empty admin
bronze tables.

## Assumptions if you run this

- Databricks workspace with Unity Catalog; catalog `dbw_gatewaymon_dev`, schemas
  `bronze`/`silver`/`gold`.
- SPN client id / secret / tenant id provided via Databricks **secret scope**
  (never hard-code — the placeholders here read from `dbutils.secrets`).
- Scope `https://analysis.windows.net/powerbi/api/.default`.
