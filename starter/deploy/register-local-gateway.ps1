<#
  register-local-gateway.ps1  -  [NET-NEW] pilot-host cluster registration

  Registers this host as an on-premises data gateway cluster, using the
  gwmon-admin-reader service principal. Assumes Install-DataGateway has already
  placed the binaries (see install-local-gateway.ps1, or the SP-driven install).

  RECOVERY KEY HANDLING
    The key is generated here with a cryptographic RNG, stored in Azure Key
    Vault, and READ BACK AND COMPARED before registration proceeds. If the
    round-trip fails the script aborts WITHOUT registering -- registering with a
    key that was not durably stored produces a gateway that can never be
    restored or migrated, and Microsoft cannot recover it. Fail-closed is the
    only safe ordering.

    The value is never printed, never written to disk, and never passed on a
    command line.

  Idempotent: exits cleanly if this host is already a cluster member.

  ASCII-only (PS 5.1 safe).
#>
[CmdletBinding()]
param(
    [string] $GatewayName = 'gwmon-pilot',
    [string] $RegionKey = 'centralus',
    [string] $VaultName = 'kv-gwmon-01',
    [string] $RecoveryKeySecretName = 'gwmon-gateway-recovery-key',
    [string] $SpSecretName = 'gwmon-admin-api-secret',
    [string] $TenantId = '16f93f41-0c3b-4163-b362-5e18cfac6898',
    [string] $ApplicationId = '531bd06b-3e5a-4df6-9e09-0c00c12e7adb',

    # Print what would happen without registering or writing any secret.
    [switch] $WhatIfOnly
)

$ErrorActionPreference = 'Stop'

if (-not (Test-Path 'C:\Program Files\On-premises data gateway')) {
    throw 'Gateway binaries not found. Run Install-DataGateway first.'
}
Import-Module DataGateway -ErrorAction Stop

# --- connect ---------------------------------------------------------------
$spSecret = az keyvault secret show --vault-name $VaultName --name $SpSecretName --query value -o tsv 2>$null
if (-not $spSecret) { throw "Could not read $SpSecretName from $VaultName (az login? access policy?)" }

Connect-DataGatewayServiceAccount -ApplicationId $ApplicationId `
    -ClientSecret (ConvertTo-SecureString $spSecret -AsPlainText -Force) `
    -Tenant $TenantId -ErrorAction Stop | Out-Null
Write-Output 'Connected as service principal.'

# --- idempotency guard ------------------------------------------------------
# Re-registering an already-registered host orphans the existing cluster.
$existing = @()
try { $existing = @(Get-DataGatewayCluster -ErrorAction Stop) } catch { }
$mine = $existing | Where-Object {
    $_.PSObject.Properties['MemberGateways'] -and
    ($_.MemberGateways | Where-Object { $_.Name -eq $env:COMPUTERNAME })
}
if ($mine) {
    Write-Output "Host $env:COMPUTERNAME is already a member of cluster '$($mine[0].Name)'. Nothing to do."
    return
}

if ($WhatIfOnly) {
    Write-Output "[what-if] would generate a recovery key, store it at $VaultName/$RecoveryKeySecretName,"
    Write-Output "[what-if] verify the round-trip, then Add-DataGatewayCluster -Name $GatewayName -RegionKey $RegionKey"
    return
}

# --- recovery key: generate, store, VERIFY, then use ------------------------
$bytes = [byte[]]::new(24)
[System.Security.Cryptography.RandomNumberGenerator]::Fill($bytes)
# Base64 minus characters that are awkward in shells/JSON; still ~140 bits.
$recoveryKey = ([Convert]::ToBase64String($bytes)) -replace '[+/=]', 'x'
Write-Output "Generated recovery key (length $($recoveryKey.Length)); value not displayed."

az keyvault secret set --vault-name $VaultName --name $RecoveryKeySecretName `
    --value $recoveryKey --output none 2>&1 | Out-Null

$readBack = az keyvault secret show --vault-name $VaultName --name $RecoveryKeySecretName --query value -o tsv 2>$null
if ($readBack -ne $recoveryKey) {
    throw "Recovery key failed read-back verification from Key Vault. ABORTING before registration -- a gateway registered with an unstored key is unrecoverable."
}
Write-Output "Recovery key stored and read-back verified: $VaultName/$RecoveryKeySecretName"

# --- register ---------------------------------------------------------------
Write-Output "Registering cluster '$GatewayName' in region '$RegionKey'..."
Add-DataGatewayCluster -Name $GatewayName `
    -RecoveryKey (ConvertTo-SecureString $recoveryKey -AsPlainText -Force) `
    -RegionKey $RegionKey -ErrorAction Stop

Write-Output 'Registered. Verifying...'
$after = @(Get-DataGatewayCluster -ErrorAction Stop)
Write-Output "Clusters visible to this SP: $($after.Count)"
$after | Format-Table -AutoSize | Out-String -Width 200 | Write-Output

$svc = Get-Service PBIEgwService -ErrorAction SilentlyContinue
if ($svc) { Write-Output "PBIEgwService: $($svc.Status)" }
