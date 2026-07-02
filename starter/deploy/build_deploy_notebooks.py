#!/usr/bin/env python3
"""
build_deploy_notebooks.py  |  Label: [NET-NEW]
Emits two Fabric orchestrator notebooks as valid nbformat-4 JSON:
  Deploy_TenantExtract.ipynb  — Track B: tenant_doctor -> 00 -> 01a -> 04 (config wired)
  Deploy_MatchRate.ipynb      — Track A: land gateway logs -> confirm join -> match rate

Design: each notebook loads config.json (+ Key Vault secret), then chains the
existing medallion notebooks. In Fabric use %run (magic) to import each sibling
notebook; off-cluster/CI we fall back to importlib so the chain is testable in
MOCK mode. Honest [Unverified] labels throughout — not executed in live Fabric.
"""
import json
import pathlib

HERE = pathlib.Path(__file__).resolve().parent  # starter/deploy
NB_DIR = HERE.parent / "notebooks"               # starter/notebooks

FABRIC_META = {
    "kernelspec": {"display_name": "Python 3 (ipykernel)", "language": "python", "name": "python3"},
    "language_info": {"name": "python", "version": "3.11.0"},
    "fabric": {"environment": {"description": "Fabric Spark 3.4 / Runtime 1.2"},
               "_comment": "[Unverified] Fabric notebook metadata — format assumed from community examples"},
}


import itertools
_ids = itertools.count(1)


def _cid():
    return f"cell{next(_ids):02d}"


def md(text):
    return {"cell_type": "markdown", "id": _cid(), "metadata": {}, "source": text.splitlines(keepends=True)}


def code(text):
    return {"cell_type": "code", "id": _cid(), "metadata": {}, "execution_count": None, "outputs": [],
            "source": text.splitlines(keepends=True)}


# Shared helper cell: config load + a run_notebook() that prefers %run in Fabric
# and falls back to importlib off-cluster.
CONFIG_CELL = '''\
# =============================================================================
# (a) CONFIG + CHAIN HELPER  |  Label: [NET-NEW]
# [Unverified — not executed in live Fabric]
# =============================================================================
import os, json, importlib.util

CONFIG_PATH = os.getenv("CONFIG_PATH", "../config/config.json")
NB_DIR = os.getenv("NB_DIR", "../notebooks")

def load_config():
    if os.path.exists(CONFIG_PATH):
        cfg = json.load(open(CONFIG_PATH))
    else:
        print(f"[warn] {CONFIG_PATH} not found — running with empty config (MOCK only).")
        cfg = {}
    # Resolve SP secret from Key Vault at runtime (never store the secret in config.json).
    kv = cfg.get("keyVault") or {}
    if kv.get("vaultUri") and kv.get("spClientSecretSecretName") and not os.getenv("TENANT_EXTRACT_MOCK"):
        try:
            from azure.identity import DefaultAzureCredential
            from azure.keyvault.secrets import SecretClient
            sc = SecretClient(vault_url=kv["vaultUri"], credential=DefaultAzureCredential())
            cfg["clientSecret"] = sc.get_secret(kv["spClientSecretSecretName"]).value
        except Exception as e:  # noqa: BLE001
            print(f"[warn] Key Vault secret fetch failed ({e}); set clientSecret another way for live runs.")
    return cfg

def run_notebook(name):
    """Import + return a sibling medallion notebook module (00_tenant_extract, etc.).
    In Fabric prefer:  %run {name}   (uncomment the magic below). Off-cluster we importlib."""
    # In Fabric, replace the importlib block with:  %run ../notebooks/{name}
    path = os.path.join(NB_DIR, f"{name}.py")
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod

CONFIG = load_config()
print("[config] loaded keys:", sorted(k for k in CONFIG if not k.startswith("_")))
'''


def build_tenant_extract():
    cells = [
        md("""# Deploy — Tenant Extract (Track B orchestrator)

> **[Unverified — requires Fabric tenant pilot]**  ·  **Label: [NET-NEW]**
> Chains **`tenant_doctor` → `00_tenant_extract` → `01a_tenant_silver_gold` → `04_capacity_bridge`**
> to populate the Nexus report's tenant pages (`gold_inventory`, `gold_activities`,
> `gold_refreshables`, `gold_capacities`). This is **Track B** of `docs/RUNBOOK-F2-pilot.md`.
>
> **Before you run:** the preflight cell must print `VERDICT: READY`. If it says `BLOCKED`,
> apply the remediation it prints (usually the read-only-admin-API tenant setting) and re-run.
>
> **MOCK mode** (`TENANT_EXTRACT_MOCK=1`) runs the whole chain with synthetic data and no
> network — use it to rehearse. Live runs need an SP with admin read scopes."""),
        code(CONFIG_CELL),
        md("## (b) Preflight — `tenant_doctor` (GATE)\nDo not proceed unless this prints `VERDICT: READY`."),
        code('''\
# =============================================================================
# (b) PREFLIGHT — tenant_doctor  |  Label: [NET-NEW]
# =============================================================================
doctor = run_notebook("tenant_doctor")
_verdict = doctor.run(CONFIG)
assert _verdict["verdict"] == "READY", (
    f"Preflight BLOCKED ({_verdict['required_fail']} required check(s) failing). "
    "Fix the items printed above (usually the read-only-admin-API tenant setting), then re-run."
)
print("Preflight READY — proceeding to extract.")
'''),
        md("## (c) Extract → bronze — `00_tenant_extract`\nScanner API + Activity Events + Refreshables."),
        code('''\
# =============================================================================
# (c) EXTRACT — 00_tenant_extract  |  Label: [NET-NEW]
# =============================================================================
# Ensure the three tenant collectors are enabled for THIS run. config.sample.json
# ships them off by default (opt-in); running this deploy notebook IS the opt-in,
# so force them on for the extract. Set EXTRACT_RESPECT_CONFIG_FLAGS=1 to instead
# honor whatever is in config.json.
_feats = dict(CONFIG.get("features") or {})
if not os.getenv("EXTRACT_RESPECT_CONFIG_FLAGS"):
    for _f in ("collectInventory", "collectActivityEvents", "collectRefreshables"):
        _feats[_f] = True
CONFIG["features"] = _feats

extract = run_notebook("00_tenant_extract")
bronze_counts = extract.run(CONFIG)
print("[extract] bronze counts:", bronze_counts)
assert sum(bronze_counts.values()) > 0, (
    "Extract produced 0 bronze rows. Enable features.collectInventory/collectActivityEvents/"
    "collectRefreshables, or check the SP has data to read (live) — see tenant_doctor D2/D3."
)
'''),
        md("## (d) Conform → gold — `01a_tenant_silver_gold`\ngold_inventory / gold_activities / gold_refreshables."),
        code('''\
# =============================================================================
# (d) SILVER/GOLD — 01a_tenant_silver_gold  |  Label: [NET-NEW]
# =============================================================================
tsg = run_notebook("01a_tenant_silver_gold")
gold_counts = tsg.run()
print("[silver/gold] gold counts:", gold_counts)
'''),
        md("""## (e) Capacity CU bridge — `04_capacity_bridge`
Populates `gold_capacities` + the CU measures. Set `config.capacityBridge.mode` to
`fpm_eventhouse` or `capacity_metrics_xmla` for live CU; `mock` (default) is synthetic."""),
        code('''\
# =============================================================================
# (e) CAPACITY BRIDGE — 04_capacity_bridge  |  Label: [NET-NEW]
# =============================================================================
bridge = run_notebook("04_capacity_bridge")
cap_rows = bridge.run(CONFIG)
print("[capacity bridge] gold_capacities rows:", cap_rows)
'''),
        md("""## (f) Done — verify + next steps

Confirm four gold tables now have rows: `gold_inventory`, `gold_activities`,
`gold_refreshables`, `gold_capacities`. Open the **Nexus Gateway & Fabric Observatory**
report — Tenant Overview / Timeline / Refresh Analytics should populate.

Report back per **Track B** in [`docs/RUNBOOK-F2-pilot.md`](../../docs/RUNBOOK-F2-pilot.md):
the `tenant_doctor` verdict, the four row counts, and which `capacityBridge.mode` you used."""),
        code('''\
# =============================================================================
# (f) SUMMARY  |  Label: [NET-NEW]
# =============================================================================
print("Tenant extract chain complete.")
print("  bronze:", bronze_counts)
print("  gold:  ", gold_counts)
print("  gold_capacities rows:", cap_rows)
'''),
    ]
    return {"cells": cells, "metadata": FABRIC_META, "nbformat": 4, "nbformat_minor": 5}


def build_match_rate():
    cells = [
        md("""# Deploy — Match Rate (Track A orchestrator)

> **[Unverified — requires Fabric tenant pilot + F2 capacity]**  ·  **Label: [NET-NEW]**
> Chains the **flagship** query→identity attribution flow end to end:
> land gateway logs → confirm the identity join → **measure the match rate**.
> This is **Track A** of `docs/RUNBOOK-F2-pilot.md`.
>
> **Prereq:** Workspace Monitoring enabled on an **F2+** capacity (trial can't provision the
> Eventhouse — see `LIVE-TENANT-FINDINGS.md`), and a gateway-routed refresh has occurred.
>
> The KQL (`01_identity_join`, `PILOT-identity-join-test`, `04_identity_match_rate`) runs
> against the Monitoring KQL database — run those blocks in a **KQL Queryset**, not here.
> This notebook orchestrates the **log-landing** step and points you to each KQL block."""),
        code(CONFIG_CELL),
        md("""## (b) Land gateway logs → bronze — `01_bronze_ingest`
Routes through `gateway_bronze_lib.read_gateway_csv` (the `(ms)`/`(bytes)`-safe path).
**Do NOT** use Fabric Load-to-Tables on the raw CSV (it fails at schema inference)."""),
        code('''\
# =============================================================================
# (b) LAND GATEWAY LOGS — 01_bronze_ingest  |  Label: [NET-NEW]
# [Unverified — needs the gateway QueryExecution/QueryStart CSVs staged in LANDING_PATH]
# =============================================================================
# In Fabric:  %run ../notebooks/01_bronze_ingest
# Off-cluster the module runs its ingest on import via the __main__-style guard;
# here we import and call the gateway-log entrypoint explicitly.
ingest = run_notebook("01_bronze_ingest")
try:
    ingest.ingest_gateway_logs()   # primary path -> bronze_query_execution / bronze_query_start
    print("[ingest] gateway logs landed to bronze.")
except Exception as e:  # noqa: BLE001
    print(f"[ingest] entrypoint not runnable off-cluster / no CSVs staged: {e}")
    print("        On the pilot host, ensure QueryExecutionReport*.csv is in LANDING_PATH.")
'''),
        md("""## (c) Confirm the join returns rows — KQL
Open a **KQL Queryset** on the Monitoring KQL database and run, in order:
1. `starter/kql/PILOT-identity-join-test.kql` Block 1 — recent monitoring rows exist.
2. Block 2 — paste a `RequestId` from the gateway host; confirm `ExecutingUser` + `ItemName`.
3. Block 3 — `getschema` to confirm live column names (report any drift)."""),
        code('''\
# =============================================================================
# (c) POINTERS — the KQL to run in a Queryset (not executable here)
# =============================================================================
print("Run in a KQL Queryset on the Monitoring KQL DB:")
print("  1) starter/kql/PILOT-identity-join-test.kql   (Blocks 1-3: confirm the join)")
print("  2) starter/kql/01_identity_join.kql           (the join itself)")
print("  3) starter/kql/04_identity_match_rate.kql     (Block A = match_rate_pct)")
'''),
        md("""## (d) Measure the match rate — `04_identity_match_rate.kql`
In the KQL Queryset run **Block A** for the headline `match_rate_pct`, **Block B** for the
Refresh-vs-DirectQuery split, **Block C** to sample unattributed queries if the rate is low.
Keep `lookback = 15m` + one workspace GUID on a throttled capacity."""),
        md("""## (e) Report back — Track A

File a pilot-report issue with the Block A counts + `match_rate_pct`, the Block B split,
the capacity SKU (F2), and any schema drift. See **Track A** in
[`docs/RUNBOOK-F2-pilot.md`](../../docs/RUNBOOK-F2-pilot.md). Then **pause the F2 capacity**."""),
    ]
    return {"cells": cells, "metadata": FABRIC_META, "nbformat": 4, "nbformat_minor": 5}


def main():
    for name, nb in [("Deploy_TenantExtract", build_tenant_extract()),
                     ("Deploy_MatchRate", build_match_rate())]:
        out = HERE / f"{name}.ipynb"
        out.write_text(json.dumps(nb, indent=1))
        print(f"wrote {out}  ({len(nb['cells'])} cells)")


if __name__ == "__main__":
    main()
