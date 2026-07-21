# Runbook — One F2 sitting: flagship match rate **+** tenant extract

**Goal:** in a single ~60–90 min rental of a Fabric **F2** capacity, produce BOTH live results the repo cannot generate off-tenant, then pause the capacity so you stop paying:
- **Track A — flagship match rate** (pain #3 / U11): the query→identity attribution %.
- **Track B — tenant extract** (Nexus report breadth): populate `gold_inventory / gold_activities / gold_refreshables / gold_capacities` so the Tenant Overview, Timeline, Refresh Analytics, and CU pages light up with real data.

Both tracks share the **same capacity, same service principal, and same sitting** — do them back to back. Run the **preflights first** (Step 0) so you don't discover a missing permission after the meter is running.

**Why F2 at all:** Session 3 field-confirmed that Workspace Monitoring's Eventhouse **fails to provision on trial capacity** (`LIVE-TENANT-FINDINGS.md`, 2026-07-02). The identity join reads `PowerBIDatasetsWorkspace`, which only exists once that Eventhouse provisions. **F2 is the smallest paid SKU that clears the blocker.** `[Unverified]` whether an even cheaper path exists; F2 is the documented-minimum in this repo's own findings.

> **Cost honesty:** F2 pay-as-you-go bills per hour and can be **paused** (billing stops while paused). Do the whole run in one sitting and pause immediately after. Confirm the current F2 hourly rate on the [Fabric pricing page](https://azure.microsoft.com/en-us/pricing/details/microsoft-fabric/) before you start — this runbook does **not** quote a price because it changes. Also note Workspace Monitoring's Eventhouse consumes CU while ingesting; keep the window short.

---

## Prerequisites (gather before you provision — the clock is money)

| # | Prereq | How to check |
|---|---|---|
| 1 | **Azure subscription** with rights to create a Fabric capacity (Contributor on a resource group). | Azure portal → Resource groups. |
| 2 | **Fabric admin** (or Capacity admin) to assign a workspace to the capacity. | Fabric Admin portal → Capacity settings. |
| 3 | A **workspace** whose semantic model **refreshes through your on-prem gateway** (real gateway traffic). | Workspace → dataset → Settings → Gateway connection. |
| 4 | **Access to the gateway host** file system (to copy a `RequestId`). Usually a server, not your laptop. | RDP/console to the gateway machine. |
| 5 | Repo cloned; you can open the Fabric portal and a KQL Queryset. | — |
| 6 | *(Track B)* A **service principal** with admin read scopes, its client secret in Key Vault, and the SP added to the security group used by the tenant setting **"Allow service principals to use read-only admin APIs."** | Preflight in Step 0B tells you exactly. |

If #3 or #4 is missing, provisioning F2 will burn money without producing a match rate. If #6 is missing, Track B returns 401/403. Line them all up first.

---

## Step 0 — Preflights (run BEFORE provisioning — free, ~2 min)

The meter isn't running yet, so validate permissions now.

**Step 0A — Track A sanity:** confirm you can reach the gateway host (Prereq 4) and that the target model refreshes through the gateway (Prereq 3). No script needed.

**Step 0B — Track B preflight (`tenant_doctor`):** point it at your `config.json` (with the SP + Key Vault secret resolved) and run:
```bash
CONFIG_PATH=starter/config/config.json python starter/notebooks/tenant_doctor.py
```
It runs 5 checks (SP token, **read-only admin API + tenant setting**, activity events, refreshables, capacity-bridge mode) and prints `VERDICT: READY` or `BLOCKED` with the **exact fix** for each failure. Do not start Track B until this says **READY**. (Exit code is non-zero when blocked, so it also gates CI/automation.)
> Rehearse the report format with no tenant: `TENANT_DOCTOR_MOCK=1 python starter/notebooks/tenant_doctor.py`.
>
> If the SPN secret is in Key Vault (it is — `kv-gwmon-01`), the one-command form does the
> fetch → run → scrub for you and needs no `config.json` on disk:
> ```powershell
> pwsh -File starter/deploy/run-tenant-doctor.ps1
> ```

**Step 0C — DO NOT re-provision what already exists (checked BEFORE Step 1).**
Phase 1 was largely completed on **2026-07-06** and recorded in `docs/PHASE1-TENANT-ENABLEMENT.md`
— capacity **`gwmoncap01`** (F2, centralus) assigned to workspace **`Gateway-Pilot`**, Workspace
Monitoring/Eventhouse enabled on it, SPN `gwmon-admin-reader` registered with admin consent, secret
in Key Vault. That record lived on an unmerged branch until 2026-07-21, so **Steps 1–3 below were
written assuming a from-scratch start and are probably already satisfied.** Creating a second
capacity would waste both money and paid-window minutes.

Check first, then skip what's done:

| Check | Where | If present |
|---|---|---|
| Capacity `gwmoncap01` exists (and is it **Paused** or **Active**?) | Azure portal → the capacity, or `az resource list --resource-type Microsoft.Fabric/capacities -o table` | **Skip Step 1.** If Paused, just **Resume** it — that is the whole of Step 1. |
| Workspace `Gateway-Pilot` assigned to it | Fabric → Workspace settings → License info | **Skip Step 2.** |
| Monitoring KQL DB answers `PowerBIDatasetsWorkspace \| take 5` | Fabric → the monitored workspace → Monitoring | **Skip Step 3** (the trial blocker is already cleared). |

> **The one action Phase 1 logged as still open** (portal-only, tenant admin): Admin portal →
> Tenant settings → **"Service principals can access read-only admin APIs"** → enable for group
> `gwmon-admin-api-sps`. Verify with `run-tenant-doctor.ps1` → `RESULT: PASS`. This gates **Track B
> only** — Track A (the flagship match rate) does not need it, so a 401 here should not stop you
> from getting the flagship number.

---

## Step 1 — Create the F2 capacity (~5 min) — **likely SKIP, see Step 0C**

> Only run this if Step 0C found no existing `gwmoncap01`. If it exists but is Paused, **Resume**
> it instead (billing restarts on resume) and go straight to Step 4. Scripted alternative to the
> portal clicks below: `pwsh starter/deploy/create-f2-capacity.ps1 -ResourceGroup rg-gatewaymon-dev
> -Name gwmoncap01 -Region centralus` (dry-run by default; add `-Execute` to actually spend).

1. Azure portal → **Create a resource** → search **"Microsoft Fabric"** → **Microsoft Fabric (capacity)** → **Create**.
2. Fill in:
   - **Subscription / Resource group:** your choice.
   - **Capacity name:** e.g. `gw-pilot-f2`.
   - **Region:** the **same region** as your workspace/tenant home region if possible (avoids cross-region friction; Workspace Monitoring is region-sensitive).
   - **Size:** **F2**.
3. **Review + create** → **Create**. Wait for deployment (~1–2 min).
4. **Note the capacity is RUNNING** = billing has started. Move quickly.

## Step 2 — Assign your workspace to the F2 capacity (~2 min) — **likely SKIP, see Step 0C**

1. Fabric portal ([app.fabric.microsoft.com](https://app.fabric.microsoft.com)) → your workspace → **Workspace settings** → **License info** (or **Premium**).
2. Set **License mode = Fabric capacity** and select **`gw-pilot-f2`**. Apply.
   - *Alt (admin):* Fabric **Admin portal → Capacity settings → gw-pilot-f2 → Workspaces → assign.*

## Step 3 — Enable Workspace Monitoring (the step trial blocks) (~5–10 min) — **likely SKIP, see Step 0C**

1. Workspace → **Workspace settings** → **Monitoring** → **+ Eventhouse** (enable).
2. Fabric creates **Monitoring Eventhouse**, **Monitoring KQL database**, **Monitoring_Eventstream**.
3. **Wait until the Monitoring KQL database shows tables with no error.** On F2 this should provision cleanly (unlike trial). If you still see `Access error` / `KustoWebV2` errors after ~10 min, capture the error IDs and see Troubleshooting below.
4. Confirm the table exists: open the Monitoring KQL DB → **New KQL Queryset** → run:
   ```kql
   PowerBIDatasetsWorkspace | take 5
   ```
   Rows (or even an empty-but-valid result) = provisioned. A "table not found" = not ready yet.

## Step 4 — Generate gateway-routed traffic + land the logs (~10 min)

1. **Trigger a refresh** of the gateway-connected semantic model (Dataset → **Refresh now**). Do it 1–2 times so there are queries to attribute.
2. Wait **~10–15 min** for the Analysis Services engine events to land in `PowerBIDatasetsWorkspace` (Microsoft states ~5 min; give margin).
3. **Land the gateway logs** into the same Eventhouse (or reach them via a OneLake shortcut) as `bronze_query_execution`:
   - Run `starter/notebooks/01_bronze_ingest.py` (via the Deploy notebook). It now routes through `gateway_bronze_lib.read_gateway_csv`, so `(ms)`/`(bytes)` headers no longer break ingest (pain #4 fix).
   - **Do NOT** use Fabric **Load-to-Tables** on the raw gateway CSV — it fails at platform schema-inference on those headers before any repo code runs (`LIVE-TENANT-FINDINGS.md`).
4. On the **gateway host**, open the newest `QueryStartReport*.csv` at
   `C:\Windows\ServiceProfiles\PBIEgwService\AppData\Local\Microsoft\On-premises data gateway\Report`
   and copy 2–3 values from the **`RequestId`** column.

## Step 5 — Confirm the join returns rows (~2 min)

Run blocks (1)–(3) of `starter/kql/PILOT-identity-join-test.kql`:
- **Block 1** — recent monitoring rows exist (else wait longer / check step 3).
- **Block 2** — paste a `RequestId` into `OperationId ==` and confirm `ExecutingUser` + `ItemName` populate. That is the flagship promise, proven live.
- **Block 3** — `getschema` to confirm the real column names in your tenant (report any drift in the pilot issue).

## Step 6 — Measure the match rate (the number) (~3 min)

Open **`starter/kql/04_identity_match_rate.kql`**:
1. Keep the throttle-safe defaults: `lookback = 15m`; set `workspace_filter = dynamic(["<your-workspace-guid>"])` (the `WorkspaceId` from Step 5 Block 2). On F2 you may widen `lookback` to `24h` and empty the filter.
2. Run **Block A** → record `total_gateway_queries`, `attributed_queries`, **`match_rate_pct`**. *This is the flagship number.*
3. Run **Block B** → the **Refresh vs DirectQuery** split (DirectQuery coverage is the genuinely-open question).
4. If the rate is low, run **Block C** → sample unattributed queries; likely causes: wrong workspace, window too narrow (engine event lands after the gateway row → widen `lookback`), or Dataflow Gen1 / Paginated (confirmed not covered).

## Step 6B — (Track B) Run the tenant extract while the capacity is up (~10 min)

Do this in the **same sitting**, right after Step 6. Track B uses the admin REST APIs (not Workspace Monitoring), but running it now reuses the SP you already validated and keeps everything in one window.

1. Ensure Step 0B printed **READY**. In `config.json` set `features.collectInventory`, `features.collectActivityEvents`, `features.collectRefreshables` → `true`.
2. Run the pipeline live (Deploy notebook or shell):
   ```bash
   CONFIG_PATH=starter/config/config.json python starter/notebooks/00_tenant_extract.py
   CONFIG_PATH=starter/config/config.json python starter/notebooks/01a_tenant_silver_gold.py
   ```
3. **Capacity CU (optional):** set `config.capacityBridge.mode` to `fpm_eventhouse` (+ `eventhouseDeltaPath`) if you run Microsoft's FPM accelerator, or `capacity_metrics_xmla` (+ `xmlaEndpoint`/`dataset`) to query the Fabric Capacity Metrics model, then run `04_capacity_bridge.py`. Leave `mock` to skip live CU.
4. Confirm four gold tables now have rows: `gold_inventory`, `gold_activities`, `gold_refreshables`, `gold_capacities`. Open the Nexus report — Tenant Overview / Timeline / Refresh Analytics should populate.

## Step 7 — PAUSE the capacity (stop billing) (~1 min)

**Do this the moment you have the number.** Azure portal → `gw-pilot-f2` → **Pause**. Billing stops while paused. (Delete it entirely if you're done: **Delete**.)
> Reassigning the workspace off the capacity before pausing avoids the workspace going into a "no capacity" state mid-report. Either pause (workspace stays assigned, just frozen) or move the workspace back to trial/Pro first.

## Step 8 — Report it back

File a **Pilot report** issue (`.github/ISSUE_TEMPLATE/pilot-report.yml`).
- **Track A:** paste the Block A counts + `match_rate_pct`, the Block B split, the capacity SKU (F2), and any schema drift from Step 5 Block 3. That number upgrades pain #3 in `PAIN-POINT-COVERAGE.md` from 🟠 Built/Unverified toward 🟢 Proven and resolves phase5 **U19**.
- **Track B:** note the `tenant_doctor` verdict, the four gold-table row counts, and which `capacityBridge.mode` you used (and whether CU populated). That upgrades the tenant-report feed from mock-tested toward live-verified.

---

## Troubleshooting

| Symptom | Likely cause | Action |
|---|---|---|
| Monitoring Eventhouse still errors on F2 (`KustoWebV2` / `Access error`) | Region mismatch, or provisioning lag | Confirm capacity + workspace share a region; wait 10–15 min; capture Activity + Kusto error IDs (as in `LIVE-TENANT-FINDINGS.md` 2026-07-02) for MS support. |
| `PowerBIDatasetsWorkspace` empty after refresh | Engine events not landed yet, or model not in the monitored workspace | Wait longer; verify the refreshed model lives in the monitored workspace (Log Analytics/monitoring is per-workspace). |
| Block 2 returns no match for a real `RequestId` | Window too narrow, or query type not covered | Widen `lookback`; check it wasn't Dataflow Gen1 / Paginated (not covered). |
| Track B: 401/403 on admin APIs | SP missing read-only-admin-API tenant setting / group membership | Re-run `tenant_doctor` (Step 0B); apply its D2 fix. |
| Match rate lower than expected | DirectQuery share, or fuzzy timing | Use Block B to separate Refresh vs DirectQuery; report both — the split *is* the finding. |

## Alternative that avoids Fabric Eventhouse entirely
If you already use **Log Analytics** on the workspace instead of Workspace Monitoring, the same identity chain works via the `XmlaRequestId` column in the Log Analytics `PowerBIDatasetsWorkspace` export — no Fabric Eventhouse (and no F2-for-monitoring) required. See the note in `starter/kql/01_identity_join.kql`. This is the cheaper path if Log Analytics is already wired up.

---
*Status: `[Unverified]` end-to-end — this runbook is the procedure to PRODUCE the first verified match rate. F2-minimum is from this repo's own live findings, not an independent MS statement. Confirm current pricing before provisioning.*

---

## Pre-sitting readiness (verified off-tenant 2026-07-21 — nothing below needs the meter running)

So the paid window is spent on the tenant, not on debugging the kit:

- ✅ **All 3 CI jobs green** on `main` (tier1-parser, tier2-spark, tenant-harness) — MVP acceptance #5.
- ✅ **All 5 deploy scripts parse clean** under the PowerShell AST parser (no syntax landmines mid-sitting).
- ✅ **`run-tenant-doctor.ps1` → `tenant_doctor.py` handoff verified** — the script passes credentials
  as `AZURE_CLIENT_ID`/`AZURE_TENANT_ID`/`AZURE_CLIENT_SECRET`; the checker previously read only a
  `CONFIG_PATH` JSON file with different key names, so the one-command form would have silently
  failed on the day. Env-var fallback added and tested.
- ✅ **Full tenant mock chain runs end-to-end** (`00_tenant_extract` → `01a_tenant_silver_gold` →
  `04_capacity_bridge`) producing all 4 gold tables, so Track B's shape is proven before it meets a
  real SP.
- ✅ **`create-f2-capacity.ps1` defaults to dry-run** — it cannot start billing by accident; `-Execute`
  is required.
- ⚠️ **Two placeholders are filled live, by design:** `PILOT-identity-join-test.kql` needs a real
  `RequestId` (Block 2) and workspace GUID (Block 4) copied from the gateway host. Have the gateway
  host RDP session open *before* resuming the capacity.

**Not verifiable off-tenant (this is what the sitting is for):** the identity join itself (U19), the
real match rate (U11), whether Workspace Monitoring column names match our assumptions, Activator
DSL, and whether the Nexus report's DirectLake bindings resolve in Desktop.
