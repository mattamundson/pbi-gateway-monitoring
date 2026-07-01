# Credential Handling Model
## Label: [NET-NEW]

This document describes how to configure the service principal, Azure Key Vault, and all credential references for Gateway Monitor.

---

## Overview: SP + Key Vault Architecture

```
Azure AD (Entra ID)
└── Service Principal (SP)
    └── Client Secret → stored in Azure Key Vault
                         ↑
                         PowerShell collectors fetch secret at runtime
                         via Get-AzKeyVaultSecret (Az module)
                         ↑
                         Fabric notebooks access via Fabric secret scope
                         or Data Pipeline credential store
```

**Never store the SP client secret in:**
- `config.json` (used as plaintext on gateway host — visible in file system)
- Notebook code (visible in git history)
- Windows Task Scheduler task arguments (logged in Event Log)

---

## Step 1: Create Service Principal

```powershell
# Run in Azure Cloud Shell or from a machine with Az PowerShell module installed
# [Unverified] Exact parameter names verified from Microsoft docs; validate in Phase 5

Connect-AzAccount -TenantId "<your-tenant-id>"

$sp = New-AzADServicePrincipal -DisplayName "GatewayMonitorSP"

# Note the ApplicationId and TenantId — put these in config.json
Write-Output "ApplicationId: $($sp.AppId)"
Write-Output "TenantId: $(Get-AzContext).Tenant.Id"
```

---

## Step 2: Assign Required Roles

The SP needs the following permissions. All are admin-level — consistent with the deployer being a Fabric + Azure tenant owner.

### 2a. Gateway Admin Role (required for DataGateway PS cmdlets + REST API)

1. In Power BI Service / Fabric portal → Settings → Manage connections and gateways
2. Select each gateway cluster → Settings → Add administrators
3. Add the SP's Application ID as a Gateway Admin

**[Known Issue]** Even with Gateway Admin role, `Get-DataGatewayInfo` may return 401 with SPs in some tenants.  
Workaround: In Power BI Admin portal → Tenant settings → enable  
"Allow service principals to use Power BI APIs" AND "Allow service principals to manage gateways"  
Reference: [Fabric community thread](https://community.fabric.microsoft.com/t5/Real-Time-Intelligence/Fabric-Platform-Monitoring-accelerator-Gateways-module/m-p/4884544)

### 2b. Fabric Workspace Admin (required for OneLake write)

```
Fabric workspace → Settings → Permissions → Add the SP as Admin
```

### 2c. Power BI Admin (required for refresh history + Activity Events Admin APIs)

```
Power BI Admin portal → Admin API settings →
  "Allow service principals to use read-only Power BI admin APIs"
  AND
  "Allow service principals to use admin APIs without login"
```

### 2d. Azure Key Vault — Secrets Reader

```powershell
# [Unverified] Set-AzKeyVaultAccessPolicy syntax; validate for your Key Vault RBAC model
Set-AzKeyVaultAccessPolicy `
    -VaultName "<key-vault-name>" `
    -ServicePrincipalName $sp.AppId `
    -PermissionsToSecrets "get"
```

Or if using Key Vault RBAC (recommended): assign role "Key Vault Secrets User" to the SP.

---

## Step 3: Store SP Client Secret in Key Vault

```powershell
# Generate a new client secret (valid for 1 year — rotate before expiry)
$cred = New-AzADServicePrincipalCredential -ApplicationId $sp.AppId

# Store in Key Vault
Set-AzKeyVaultSecret `
    -VaultName "<key-vault-name>" `
    -Name "gateway-monitor-sp-secret" `
    -SecretValue ($cred.SecretText | ConvertTo-SecureString -AsPlainText -Force)

Write-Output "Secret stored. Note: rotate before $((Get-Date).AddYears(1))"
```

Set a reminder to rotate the secret before expiry. Expired secrets cause silent collector failures.

---

## Step 4: Configure Collectors on Gateway Host

The PowerShell collectors fetch the secret from Key Vault at runtime. They require the Az module:

```powershell
# Run once on each gateway host (as Administrator)
Install-Module -Name Az.Accounts, Az.KeyVault, DataGateway -Scope AllUsers -Force
```

The collectors authenticate to Key Vault using the SP credentials. The SP's ApplicationId and TenantId go in `config.json` (not the secret). The collectors then call:

```powershell
# This is what the collectors do internally at runtime
Connect-AzAccount -ServicePrincipal -TenantId $config.tenantId `
    -ApplicationId $config.applicationId -CertificateThumbprint "..."
# OR for client secret (fetched from a pre-staged file on the host):
$secret = Get-AzKeyVaultSecret -VaultName $config.keyVault.vaultUri -Name $config.keyVault.spClientSecretSecretName -AsPlainText
```

**[Assumption]** The gateway host has outbound HTTPS access to `vault.azure.net` (Key Vault endpoint). Verify with your network team if the host is in a locked-down network.

---

## Step 5: Fabric Notebook Credentials

Fabric notebooks should NOT contain credentials. Use one of:

**Option A: Fabric secret scope (recommended)**  
In the Fabric workspace, create a Secret Scope backed by the same Key Vault. Reference secrets in notebooks:
```python
sp_secret = dbutils.secrets.get(scope="gateway-monitor-kv", key="gateway-monitor-sp-secret")
```
[Assumption] Fabric supports Azure Key Vault-backed secret scopes similar to Databricks.  
[Unverified] Exact Fabric API for secret scope creation — validate in Phase 5.

**Option B: Fabric Data Pipeline credential store**  
Define a Service Principal connection in the Fabric workspace → Data Pipelines → Connections. Reference by name in notebooks/pipelines without embedding credentials.

**Option C: Managed Identity (preferred for Fabric-native workloads)**  
If the Fabric workspace has a workspace identity (Managed Identity), grant it the same permissions as the SP above and use the workspace identity in notebooks instead of SP credentials. Eliminates all client secret management.  
Reference: [Fabric workspace identity documentation](https://learn.microsoft.com/en-us/fabric/security/workspace-identity)

---

## Credential Rotation Checklist

| Credential | Location | Rotation Cadence | Action on Rotation |
|---|---|---|---|
| SP client secret | Azure Key Vault | Annually (or per policy) | Update Key Vault secret; collectors pick up automatically on next run |
| Gateway datasource credentials | Power BI Service | Per password policy | Re-enter in gateway datasource settings; Activator `credential-drift` rule detects staleness |
| Key Vault access policy | Azure RBAC | On SP changes | Update Key Vault Secrets User role assignment |

---

## Security Notes

- **Least privilege for secret access:** The SP only needs `get` permission on the specific secret, not full Key Vault access.
- **No service account passwords in scripts:** All credential references are via Key Vault lookup, not hardcoded.
- **Git hygiene:** `config.json` (with real values) should be in `.gitignore`. Only `config.sample.json` (with placeholder values) is committed.
- **Recovery key:** The Power BI gateway has a recovery key that, if lost, requires re-entering all datasource credentials. Document and store the recovery key securely (Key Vault recommended). Reference: [powerbiconsulting.com](https://powerbiconsulting.com/blog/power-bi-gateway-architecture-large-enterprises).

---

*All commands are reference / [Unverified]. Validate against your specific Azure / Fabric environment in Phase 5.*
