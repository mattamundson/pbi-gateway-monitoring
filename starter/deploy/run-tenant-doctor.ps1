<#
  run-tenant-doctor.ps1  -  [NET-NEW] one-command Phase 1 verification.

  Fetches the gwmon SPN client secret from Azure Key Vault into a transient env var
  (never printed), runs tenant_doctor.py, then clears the secret from the environment.
  Exit code mirrors tenant_doctor: 0 = admin APIs reachable with data (Phase 1 PASS).

  PREREQS: az CLI signed in with get access to the vault (`az login`); Python 3.8+.
  ASCII-only (PS 5.1 safe).

  Usage:
    pwsh -File starter/deploy/run-tenant-doctor.ps1
#>
[CmdletBinding()]
param(
    [string] $VaultName = 'kv-gwmon-01',
    [string] $SecretName = 'gwmon-admin-api-secret',
    [string] $AppId = '531bd06b-3e5a-4df6-9e09-0c00c12e7adb',
    [string] $TenantId = '16f93f41-0c3b-4163-b362-5e18cfac6898'
)
$ErrorActionPreference = 'Stop'

$az = (Get-Command az -ErrorAction SilentlyContinue).Source
if (-not $az) {
    $cand = 'C:\Program Files\Microsoft SDKs\Azure\CLI2\wbin\az.cmd'
    if (Test-Path $cand) { $az = $cand } else { throw 'az CLI not found -- run az login first.' }
}

# Pick a python launcher.
$py = $null
foreach ($c in @('py', 'python', 'python3')) {
    if (Get-Command $c -ErrorAction SilentlyContinue) { $py = $c; break }
}
if (-not $py) { throw 'No python launcher found (py/python/python3).' }
$pyArgs = @()
if ($py -eq 'py') { $pyArgs = @('-3') }

$doctor = Join-Path $PSScriptRoot '..\notebooks\tenant_doctor.py'
if (-not (Test-Path $doctor)) { throw "tenant_doctor.py not found at $doctor" }

# Fetch the secret (value captured, never printed).
$secret = (& $az keyvault secret show --vault-name $VaultName --name $SecretName --query value -o tsv 2>$null)
if (-not $secret) { throw "could not read secret $SecretName from vault $VaultName (check az login + access policy)." }

$env:AZURE_CLIENT_ID = $AppId
$env:AZURE_TENANT_ID = $TenantId
$env:AZURE_CLIENT_SECRET = $secret
try {
    & $py @pyArgs $doctor
    $rc = $LASTEXITCODE
}
finally {
    Remove-Item Env:AZURE_CLIENT_SECRET -ErrorAction SilentlyContinue
    $secret = $null
}
exit $rc
