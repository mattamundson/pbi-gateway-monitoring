<#
  store-spn-secret.ps1  -  [NET-NEW] Phase 1 helper: reset the gwmon SPN client secret
  and store it in 1Password. RUN THIS FROM AN INTERACTIVE SHELL (Amo's `!` prompt) --
  a background job cannot reach the 1Password desktop app IPC, so `op` fails there.

  The app registration + SP + Tenant.Read.All + admin-consent + group membership are ALREADY
  done. This script only (1) resets a fresh client secret and (2) stores it to 1Password.
  The secret is never printed, logged, or committed -- only the op:// reference is shown.

  PREREQS (interactive session): az CLI signed in (`az login`); 1Password desktop app running
  + unlocked; `op` CLI integration enabled. ASCII-only (PS 5.1 safe).

  Usage (from Amo's interactive Claude prompt):
    ! pwsh -File "C:\Users\mattm\Code\pbi-gateway-monitoring\.claude\worktrees\report-buildout-v1\starter\deploy\store-spn-secret.ps1"
#>
[CmdletBinding()]
param(
    [string] $AppId = '531bd06b-3e5a-4df6-9e09-0c00c12e7adb',
    [string] $TenantId = '16f93f41-0c3b-4163-b362-5e18cfac6898',
    [string] $OpVault = 'Amo Personal',
    [string] $OpItem = 'Gwmon-SPN-AdminAPI'
)
$ErrorActionPreference = 'Stop'

# Resolve az (may not be on this shell's PATH yet after the winget install).
$az = (Get-Command az -ErrorAction SilentlyContinue).Source
if (-not $az) {
    $cand = 'C:\Program Files\Microsoft SDKs\Azure\CLI2\wbin\az.cmd'
    if (Test-Path $cand) { $az = $cand } else { throw 'az CLI not found -- open a fresh terminal or add az to PATH.' }
}
if (-not (Get-Command op -ErrorAction SilentlyContinue)) { throw 'op (1Password CLI) not found on PATH.' }

# Confirm op can reach the desktop app before we mint a secret (avoids a lost credential).
& op whoami 1>$null 2>$null
if ($LASTEXITCODE -ne 0) {
    throw 'op is not signed in / cannot reach the 1Password desktop app. Open + unlock the 1Password app, then re-run.'
}
& op account get 1>$null 2>$null   # warm the session

# Confirm az session.
$acct = & $az account show 2>$null | ConvertFrom-Json
if (-not $acct) { throw 'az not signed in. Run: az login' }
Write-Output "az tenant: $($acct.tenantId)  user: $($acct.user.name)"

# 1. Reset a fresh client secret (JSON to stdout; password captured, never echoed).
$credJson = & $az ad app credential reset --id $AppId --display-name 'gwmon-admin-api' --years 1 2>$null
$cred = $credJson | ConvertFrom-Json
if (-not $cred.password) { throw 'credential reset returned no secret.' }

# 2. Store to 1Password (value passed via env to keep it off argv/logs).
$env:GWMON_SPN_SECRET = $cred.password
try {
    $exists = $false
    & op item get $OpItem --vault $OpVault 1>$null 2>$null
    if ($LASTEXITCODE -eq 0) { $exists = $true }

    if ($exists) {
        & op item edit $OpItem --vault $OpVault `
            "credential[password]=$env:GWMON_SPN_SECRET" `
            "client_id[text]=$AppId" `
            "tenant_id[text]=$TenantId" 1>$null 2>$null
        if ($LASTEXITCODE -ne 0) { throw "op item edit failed (exit $LASTEXITCODE)." }
        Write-Output "Updated existing 1Password item."
    }
    else {
        & op item create --category 'API Credential' --vault $OpVault --title $OpItem `
            "credential[password]=$env:GWMON_SPN_SECRET" `
            "client_id[text]=$AppId" `
            "tenant_id[text]=$TenantId" 1>$null 2>$null
        if ($LASTEXITCODE -ne 0) { throw "op item create failed (exit $LASTEXITCODE)." }
        Write-Output "Created new 1Password item."
    }
}
finally {
    Remove-Item Env:GWMON_SPN_SECRET -ErrorAction SilentlyContinue
    $cred = $null; $credJson = $null
}

Write-Output ''
Write-Output "Client secret stored:  op://$OpVault/$OpItem/credential  (not printed)"
Write-Output "Notebook env:  AZURE_CLIENT_ID=$AppId  AZURE_TENANT_ID=$TenantId  AZURE_CLIENT_SECRET=op://$OpVault/$OpItem/credential"
