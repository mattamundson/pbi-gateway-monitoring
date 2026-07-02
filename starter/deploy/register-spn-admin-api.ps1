<#
  register-spn-admin-api.ps1  -  [NET-NEW] scaffold for Phase 1 (tenant enablement)

  Registers the Azure AD app + service principal the gateway-monitor tool uses to read
  Power BI / Fabric READ-ONLY ADMIN APIs (the 401 / 0-admin-rows blocker in tenant_doctor.py).

  WHAT IT AUTOMATES (you run it authenticated):
    1. az AD app registration + service principal
    2. Power BI Service application permission (Tenant.Read.All) + admin-consent attempt
    3. a client secret, stored DIRECTLY to 1Password (never printed / logged / committed)
    4. (optional) adds the SP to a security group you pass with -SecurityGroupId

  WHAT STAYS MANUAL (portal, cannot be safely scripted blind):
    A. Fabric Admin portal -> Tenant settings -> "Service principals can access read-only
       admin APIs" -> enable for the security group holding this SP.
    B. Assign the F2 capacity to the workspace + enable Workspace Monitoring.

  PREREQS: az CLI (`az login` as a tenant admin), 1Password CLI (`op`, signed in).
  ASCII-only (PS 5.1 safe). Secret-hygiene: the secret is written to 1Password, never echoed.
#>
[CmdletBinding()]
param(
    [string] $DisplayName = 'gwmon-admin-reader',
    [string] $SecurityGroupId = '',
    [string] $OpVault = 'Amo Personal',
    [string] $OpItem = 'Gwmon-SPN-AdminAPI'
)
$ErrorActionPreference = 'Stop'
$PowerBIServiceAppId = '00000009-0000-0000-c000-000000000000'   # Power BI Service
$TenantReadAllRoleId = 'b2f1b2fa-f35c-407c-979c-a858a808ba85'    # Tenant.Read.All (app role)

function Require-Cmd([string]$name) {
    if (-not (Get-Command $name -ErrorAction SilentlyContinue)) { throw "$name not found on PATH -- install/sign in first." }
}
Require-Cmd az
Require-Cmd op

# 0. confirm az session + tenant admin context
$acct = az account show 2>$null | ConvertFrom-Json
if (-not $acct) { throw "Not signed in. Run: az login  (as a Fabric/Power BI tenant admin)" }
Write-Output "az tenant: $($acct.tenantId)  user: $($acct.user.name)"

# 1. app registration (idempotent by displayName)
$existing = az ad app list --display-name $DisplayName 2>$null | ConvertFrom-Json
if ($existing -and $existing.Count -gt 0) {
    $app = $existing[0]
    Write-Output "Reusing existing app: $($app.appId)"
}
else {
    $app = az ad app create --display-name $DisplayName --sign-in-audience AzureADMyOrg 2>$null | ConvertFrom-Json
    Write-Output "Created app: $($app.appId)"
}
$appId = $app.appId

# 2. service principal (idempotent)
$sp = az ad sp show --id $appId 2>$null | ConvertFrom-Json
if (-not $sp) {
    $sp = az ad sp create --id $appId 2>$null | ConvertFrom-Json
    Write-Output "Created service principal: $($sp.id)"
}
else {
    Write-Output "Service principal exists: $($sp.id)"
}

# 3. Power BI Service Tenant.Read.All application permission + consent
az ad app permission add --id $appId --api $PowerBIServiceAppId `
    --api-permissions "$TenantReadAllRoleId=Role" 2>$null | Out-Null
Write-Output "Added Power BI Service Tenant.Read.All (application). Attempting admin-consent..."
try { az ad app permission admin-consent --id $appId 2>$null | Out-Null; Write-Output "  admin-consent granted." }
catch { Write-Output "  admin-consent could not be granted non-interactively -- grant it in Azure Portal > App registrations > API permissions." }

# 4. client secret -> 1Password (NEVER echoed)
$cred = az ad app credential reset --id $appId --append --display-name 'gwmon-admin-api' --years 1 2>$null | ConvertFrom-Json
if (-not $cred.password) { throw "credential reset returned no secret" }
# store to 1Password via env (keeps the value off argv/logs)
$env:GWMON_SPN_SECRET = $cred.password
op item create --category 'API Credential' --vault $OpVault --title $OpItem `
    "credential[password]=$env:GWMON_SPN_SECRET" `
    "client_id[text]=$appId" `
    "tenant_id[text]=$($acct.tenantId)" 2>$null | Out-Null
Remove-Item Env:GWMON_SPN_SECRET
Write-Output "Client secret stored in 1Password: op://$OpVault/$OpItem/credential  (not printed here)"

# 5. optional: add SP to the admin-API security group
if ($SecurityGroupId) {
    az ad group member add --group $SecurityGroupId --member-id $sp.id 2>$null | Out-Null
    Write-Output "Added SP to security group $SecurityGroupId"
}
else {
    Write-Output "No -SecurityGroupId given: create/choose a security group, add SP object id $($sp.id) to it."
}

Write-Output ''
Write-Output '=== REMAINING MANUAL STEPS (portal) ==='
Write-Output 'A. Fabric Admin portal > Tenant settings > "Service principals can access read-only'
Write-Output '   admin APIs" > Enable for the security group holding this SP.'
Write-Output 'B. Assign the F2 capacity to the workspace; enable Workspace Monitoring (Eventhouse).'
Write-Output 'Then verify: python starter/notebooks/tenant_doctor.py  (expect admin rows > 0, no 401).'
Write-Output ''
Write-Output "env for the notebooks:  AZURE_CLIENT_ID=$appId  AZURE_TENANT_ID=$($acct.tenantId)  AZURE_CLIENT_SECRET=op://$OpVault/$OpItem/credential"
