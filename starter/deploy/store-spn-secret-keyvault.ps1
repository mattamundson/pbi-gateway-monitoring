<#
  store-spn-secret-keyvault.ps1  -  [NET-NEW] Phase 1: mint the gwmon SPN client secret and
  store it in Azure Key Vault (the native store for an Azure-service credential; reachable
  headlessly via az, and referenceable at runtime by Fabric/Databricks compute).

  Idempotent. Runs fully non-interactively given an `az login` session. The secret is written
  to Key Vault via a temp file (no trailing newline -> byte-exact) and is NEVER printed,
  logged, echoed, or committed. Every az call that could surface the value is `--output none`.

  PREREQS: az CLI signed in as a subscription contributor/owner (`az login`).
  ASCII-only (PS 5.1 safe).

  Verify afterwards (does NOT print the value):
    az keyvault secret show --vault-name kv-gwmon-01 --name gwmon-admin-api-secret --query "attributes.enabled"
#>
[CmdletBinding()]
param(
    [string] $AppId = '531bd06b-3e5a-4df6-9e09-0c00c12e7adb',
    [string] $TenantId = '16f93f41-0c3b-4163-b362-5e18cfac6898',
    [string] $VaultName = 'kv-gwmon-01',
    [string] $SecretName = 'gwmon-admin-api-secret',
    [string] $ResourceGroup = 'rg-gatewaymon-dev',
    [string] $Location = 'centralus',
    [string] $GrantObjectId = 'fe1ec7d9-ecf1-49a9-8b76-78a5a3d0f8d7'   # creator; gets secret get/set/list
)
$ErrorActionPreference = 'Stop'

$az = (Get-Command az -ErrorAction SilentlyContinue).Source
if (-not $az) {
    $cand = 'C:\Program Files\Microsoft SDKs\Azure\CLI2\wbin\az.cmd'
    if (Test-Path $cand) { $az = $cand } else { throw 'az CLI not found -- install it and run az login first.' }
}
$acct = & $az account show 2>$null | ConvertFrom-Json
if (-not $acct) { throw 'az not signed in. Run: az login' }
Write-Output "az sub: $($acct.id)  tenant: $($acct.tenantId)  user: $($acct.user.name)"

# 1. Ensure the vault exists (access-policy mode so the creator + a named object id get data-plane rights).
$vault = & $az keyvault show --name $VaultName --resource-group $ResourceGroup 2>$null | ConvertFrom-Json
if (-not $vault) {
    Write-Output "Creating Key Vault '$VaultName' in $ResourceGroup / $Location ..."
    & $az keyvault create --name $VaultName --resource-group $ResourceGroup --location $Location `
        --enable-rbac-authorization false --output none 2>$null
    if ($LASTEXITCODE -ne 0) { throw "keyvault create failed (exit $LASTEXITCODE)." }
}
else { Write-Output "Key Vault '$VaultName' already exists." }

# 2. Ensure the creating identity can set/get secrets (idempotent).
if ($GrantObjectId) {
    & $az keyvault set-policy --name $VaultName --object-id $GrantObjectId `
        --secret-permissions get list set --output none 2>$null
    if ($LASTEXITCODE -ne 0) { throw "set-policy failed (exit $LASTEXITCODE)." }
}

# 3. Mint a fresh client secret (JSON to stdout captured into a var; password NEVER printed).
$credJson = & $az ad app credential reset --id $AppId --display-name 'gwmon-admin-api' --years 1 2>$null
$cred = $credJson | ConvertFrom-Json
if (-not $cred.password) { throw 'credential reset returned no secret.' }

# 4. Store in Key Vault via a temp file written WITHOUT a trailing newline (byte-exact value).
$tmp = Join-Path $env:TEMP ("gwmon_spn_{0}.txt" -f [guid]::NewGuid().ToString('N'))
try {
    [System.IO.File]::WriteAllText($tmp, [string]$cred.password)
    & $az keyvault secret set --vault-name $VaultName --name $SecretName --file $tmp --output none 2>$null
    if ($LASTEXITCODE -ne 0) { throw "keyvault secret set failed (exit $LASTEXITCODE)." }
}
finally {
    if (Test-Path $tmp) { Remove-Item $tmp -Force -ErrorAction SilentlyContinue }
    $cred = $null; $credJson = $null
}

$secretUri = "https://$VaultName.vault.azure.net/secrets/$SecretName"
Write-Output ''
Write-Output "Secret stored (value not shown):  $secretUri"
Write-Output "Notebook env (local run):  AZURE_CLIENT_ID=$AppId  AZURE_TENANT_ID=$TenantId"
Write-Output "  fetch secret ->  az keyvault secret show --vault-name $VaultName --name $SecretName --query value -o tsv"
