<#
  create-f2-capacity.ps1  -  [NET-NEW] scaffold for Phase 1 Step 2 (F2 capacity)

  Creates a Microsoft Fabric F2 capacity via the ARM REST API (az rest). Defaults to
  DRY-RUN: it prints the exact PUT it would make and does NOT spend. Pass -Execute to
  actually create the capacity (that is the spend action -- your deliberate call).

  Capacity assignment to a workspace + Workspace Monitoring stay manual (Fabric portal):
  they are UI/Fabric-API toggles, not ARM.

  PREREQS: az CLI signed in as a subscription contributor (`az login`).
  Cost: F2 ~= $0.36/hr pay-as-you-go; PAUSE it when idle to stop billing.
  ASCII-only (PS 5.1 safe).

  Example (dry-run):
    pwsh starter/deploy/create-f2-capacity.ps1 -ResourceGroup rg-gwmon -Name gwmoncap01 -Region eastus
  Then, to actually create:
    pwsh starter/deploy/create-f2-capacity.ps1 -ResourceGroup rg-gwmon -Name gwmoncap01 -Region eastus -Execute
#>
[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)] [string] $ResourceGroup,
    [Parameter(Mandatory = $true)] [string] $Name,          # 3-63 chars, lowercase alphanumeric, globally unique
    [string] $Region = 'eastus',
    [string] $Sku = 'F2',
    [string] $AdminUpn = '',                                 # capacity admin; defaults to the signed-in user
    [switch] $Execute
)
$ErrorActionPreference = 'Stop'
if (-not (Get-Command az -ErrorAction SilentlyContinue)) { throw 'az CLI not found -- install it and run az login first.' }

$acct = az account show 2>$null | ConvertFrom-Json
if (-not $acct) { throw 'Not signed in. Run: az login' }
$sub = $acct.id
if (-not $AdminUpn) { $AdminUpn = $acct.user.name }
Write-Output "subscription: $sub   region: $Region   sku: $Sku   admin: $AdminUpn"

# Ensure the resource group exists (idempotent; RG creation is free).
$rg = az group show --name $ResourceGroup 2>$null | ConvertFrom-Json
if (-not $rg) {
    if ($Execute) { az group create --name $ResourceGroup --location $Region 2>$null | Out-Null; Write-Output "created RG $ResourceGroup" }
    else { Write-Output "[dry-run] would create RG $ResourceGroup in $Region" }
}

$url = "https://management.azure.com/subscriptions/$sub/resourceGroups/$ResourceGroup/providers/Microsoft.Fabric/capacities/$Name`?api-version=2023-11-01"
$bodyObj = @{
    location   = $Region
    sku        = @{ name = $Sku; tier = 'Fabric' }
    properties = @{ administration = @{ members = @($AdminUpn) } }
}
$body = ($bodyObj | ConvertTo-Json -Depth 6 -Compress)

Write-Output ''
Write-Output '=== ARM request ==='
Write-Output "PUT $url"
Write-Output "BODY $body"
Write-Output ''

if (-not $Execute) {
    Write-Output '[DRY-RUN] Nothing created. Re-run with -Execute to provision (this starts billing).'
    return
}

# Spend action.
$tmp = Join-Path $env:TEMP "f2body_$Name.json"
$body | Set-Content -Path $tmp -Encoding ASCII
try {
    az rest --method put --url $url --body "@$tmp" --headers 'Content-Type=application/json' 2>$null | Out-Null
    Write-Output "F2 capacity '$Name' create submitted. Poll: az resource show --ids /subscriptions/$sub/resourceGroups/$ResourceGroup/providers/Microsoft.Fabric/capacities/$Name --query properties.state"
}
finally { Remove-Item $tmp -ErrorAction SilentlyContinue }

Write-Output ''
Write-Output '=== NEXT (Fabric portal, manual) ==='
Write-Output "1. app.fabric.microsoft.com > your Workspace > Settings > License info > assign capacity '$Name'."
Write-Output '2. Workspace > Settings > Monitoring > enable (provisions the Eventhouse for D6 identity join).'
Write-Output '3. Pause the capacity when idle to stop billing (Azure Portal > the capacity > Pause).'
