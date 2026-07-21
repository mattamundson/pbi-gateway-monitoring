# Phase 1 — Tenant Enablement (live resource inventory + verification)

> **Status note (2026-07-21 reconciliation):** this inventory was last verified 2026-07-06 on a
> branch that never merged to `main`, so `main`'s own docs (`RUNBOOK-F2-pilot.md`,
> `MVP-ROADMAP.md`) had no record of it and described Phase 1 as entirely unstarted. Whether
> `gwmoncap01` is still provisioned (and billing) or was paused/deleted since is **unconfirmed
> as of this reconciliation** — recheck directly in the Fabric/Azure portal before assuming
> either state, and pause it if it's still running and idle (~$0.36/hr).

> The master gate. Everything downstream (collectors -> medallion -> report -> alerting)
> depends on a real Fabric capacity + a service principal that can read the Power BI
> read-only admin APIs. This doc records the **live** tenant objects provisioned for the
> gateway-monitoring pilot and the exact command to verify the grant.
>
> **No secrets live in this file** — only non-sensitive identifiers (app/object/tenant IDs,
> Key Vault URIs). The SPN client secret lives only in Azure Key Vault.

## Live resource inventory

| Thing | Value |
|---|---|
| Subscription | `b8f892ab-7e2e-4f5a-970f-c4527de0dd07` |
| Tenant (AAD) | `16f93f41-0c3b-4163-b362-5e18cfac6898` |
| Resource group | `rg-gatewaymon-dev` (centralus) |
| Fabric capacity | `gwmoncap01` (**F2**, centralus) — assigned to workspace **Gateway-Pilot** |
| Workspace Monitoring | Enabled on `Gateway-Pilot` (Eventhouse; required for D6 identity join) |
| SPN app registration | `gwmon-admin-reader` — appId `531bd06b-3e5a-4df6-9e09-0c00c12e7adb` |
| SPN service principal | objectId `b7d111f8-6287-4e79-93dd-b3d1d0acdee6` |
| API permission | Power BI Service **Tenant.Read.All** (application) — admin-consent granted |
| Security group | `gwmon-admin-api-sps` — objectId `15a1e68b-a14c-4fae-9bff-f0f4548aac8a` (SP is a member) |
| Key Vault | `kv-gwmon-01` — `https://kv-gwmon-01.vault.azure.net/` |
| Secret | `gwmon-admin-api-secret` (the SPN client secret; value only in Key Vault) |

### Secret store decision

The SPN client secret is stored in **Azure Key Vault** (`kv-gwmon-01`), not 1Password. Rationale:
the consumer is Azure/Fabric compute, which can reference a Key Vault secret at runtime
(workspace identity -> `getSecret`) but cannot reach a local 1Password desktop store; and Key
Vault needs no interactive auth for headless mint/rotate. A 1Password helper
(`starter/deploy/store-spn-secret.ps1`) is retained as an alternative for anyone who prefers it.

## Provisioning scripts

| Script | What it does |
|---|---|
| `starter/deploy/create-f2-capacity.ps1` | ARM `az rest` PUT to create the F2 capacity (dry-run by default; `-Execute` to spend) |
| `starter/deploy/register-spn-admin-api.ps1` | App reg + SP + Tenant.Read.All + admin-consent + group add |
| `starter/deploy/store-spn-secret-keyvault.ps1` | Mint client secret -> Key Vault (headless; secret never printed) |
| `starter/deploy/store-spn-secret.ps1` | Alternative: mint client secret -> 1Password (interactive `op signin`) |
| `starter/deploy/run-tenant-doctor.ps1` | Fetch secret from Key Vault -> run `tenant_doctor.py` -> clear env |
| `starter/notebooks/tenant_doctor.py` | Stdlib-only smoke: client-credentials token -> read-only admin APIs |

## Verify (one command)

```powershell
pwsh -File starter/deploy/run-tenant-doctor.ps1
```

- `RESULT: PASS` -> admin APIs reachable with data. **Phase 1 complete.**
- `RESULT: BLOCKED ... 401/403` -> the tenant setting below isn't effective yet.

## The ONE remaining action (tenant admin, portal-only)

Verified 2026-07-06: the SPN authenticates and acquires an AAD token successfully, but the
read-only admin APIs return **401** because the tenant setting is not yet enabled. To clear it:

1. **app.fabric.microsoft.com** -> gear -> **Admin portal** -> **Tenant settings**
2. Search **"admin APIs"** -> **"Service principals can access read-only admin APIs"**
3. **Enable** -> *Apply to -> Specific security groups* -> add **`gwmon-admin-api-sps`** -> **Apply**
4. Wait a few minutes for propagation, then re-run the verify command above.

## Cost note

F2 capacity `gwmoncap01` bills ~$0.36/hr pay-as-you-go. **Pause it when idle**
(Azure Portal -> the capacity -> Pause) to stop billing. Key Vault secret storage is negligible.
