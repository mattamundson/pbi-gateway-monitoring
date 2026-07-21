<#
  install-local-gateway.ps1  -  [NET-NEW] pilot-host gateway bootstrap

  Installs and registers an on-premises data gateway on THIS machine so the
  collectors finally have a real gateway to read. Until now every collector ran
  against assumptions: the spool path, the service account name, the Windows
  service name, the event-log provider name and the GatewayCore.dll.config
  location are all [Assumption]-tagged and none had ever been checked against a
  running gateway.

  WHY A HUMAN RUNS THIS AND NOT THE AGENT
    Install-DataGateway and Add-DataGatewayCluster both require an interactive
    Power BI sign-in, and the auth context lives in the PowerShell PROCESS -- so
    login, install and register must happen in one session. Service principals
    cannot register a gateway cluster; Microsoft requires a user account. That
    makes this a one-time human step by design, not a gap in automation.

  RECOVERY KEY
    You are prompted for it as a SecureString. It is never printed, never
    written to disk by this script, and never passed on the command line.
    STORE IT IN 1PASSWORD IMMEDIATELY -- without it the gateway cannot be
    restored or migrated to another machine, and Microsoft cannot recover it.

  Idempotent: skips install if the gateway is already present, skips
  registration if this host is already a cluster member.

  ASCII-only (PS 5.1 safe). Run from an elevated shell.
#>
[CmdletBinding()]
param(
    # Cluster display name as it will appear in the Fabric/Power BI portal.
    [string] $GatewayName = 'gwmon-pilot',

    # Leave empty to be shown the list and prompted.
    [string] $RegionKey = '',

    # Skip the Add-DataGatewayCluster step (install only).
    [switch] $InstallOnly
)

$ErrorActionPreference = 'Stop'

function Write-Step { param([string]$m) Write-Output "`n=== $m ===" }

# --- 0. preflight -----------------------------------------------------------
$isAdmin = ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole(
    [Security.Principal.WindowsBuiltInRole]::Administrator)
if (-not $isAdmin) { throw 'Run this from an ELEVATED PowerShell session (the MSI needs admin).' }

if (-not (Get-Module -ListAvailable -Name DataGateway)) {
    throw 'DataGateway module missing. Run: Install-Module DataGateway -Scope AllUsers -Force'
}
Import-Module DataGateway -ErrorAction Stop
Write-Output "DataGateway module $((Get-Module DataGateway).Version) loaded."

# --- 1. sign in -------------------------------------------------------------
# Interactive by design (see header). This opens a browser.
Write-Step 'Sign in to Power BI'
Write-Output 'A browser window will open. Sign in with an account that has'
Write-Output 'permission to create a gateway in your tenant.'
Connect-DataGatewayServiceAccount -ErrorAction Stop | Out-Null
Write-Output 'Signed in.'

# --- 2. install -------------------------------------------------------------
Write-Step 'Install gateway binaries'
$installed = Test-Path 'C:\Program Files\On-premises data gateway'
if ($installed) {
    Write-Output 'Gateway already installed at C:\Program Files\On-premises data gateway -- skipping.'
}
else {
    Write-Output 'Downloading and installing (a few hundred MB; this takes several minutes)...'
    Install-DataGateway -AcceptConditions -ErrorAction Stop
    Write-Output 'Install complete.'
}

if ($InstallOnly) {
    Write-Output "`nInstallOnly set -- stopping before registration."
    return
}

# --- 3. register a cluster --------------------------------------------------
Write-Step 'Register the gateway cluster'

# Already a member? Registering again would orphan the existing cluster.
$existing = @()
try { $existing = @(Get-DataGatewayCluster -ErrorAction Stop) } catch { }
$alreadyHere = $existing | Where-Object {
    $_.PSObject.Properties['MemberGateways'] -and
    ($_.MemberGateways | Where-Object { $_.Name -eq $env:COMPUTERNAME })
}
if ($alreadyHere) {
    Write-Output "This host ($env:COMPUTERNAME) is already a member of cluster '$($alreadyHere[0].Name)'."
    Write-Output 'Skipping registration.'
}
else {
    if (-not $RegionKey) {
        Write-Output 'Available regions:'
        Get-DataGatewayRegion | Format-Table -AutoSize | Out-String | Write-Output
        $RegionKey = Read-Host 'RegionKey (copy one from the RegionKey column above)'
    }

    Write-Output ''
    Write-Output 'RECOVERY KEY -- read this before typing:'
    Write-Output '  * You choose it. Minimum 8 characters.'
    Write-Output '  * It is the ONLY way to restore or migrate this gateway.'
    Write-Output '  * Microsoft CANNOT recover it for you.'
    Write-Output '  * Save it to 1Password the moment this finishes.'
    $recoveryKey = Read-Host 'Recovery key' -AsSecureString

    Add-DataGatewayCluster -Name $GatewayName -RecoveryKey $recoveryKey `
        -RegionKey $RegionKey -ErrorAction Stop
    Write-Output "Registered cluster '$GatewayName'."
}

# --- 4. report what the collectors will actually see ------------------------
# These are the [Assumption] values the collectors hardcode. Printing the REAL
# ones here is the point of the whole exercise: every mismatch below is a
# collector that would have silently produced nothing on this host.
Write-Step 'Ground truth for the collectors'

$svc = Get-Service -Name 'PBIEgwService' -ErrorAction SilentlyContinue
if ($svc) {
    Write-Output "Windows service : $($svc.Name) [$($svc.Status)]"
    $svcWmi = Get-CimInstance Win32_Service -Filter "Name='PBIEgwService'" -ErrorAction SilentlyContinue
    if ($svcWmi) { Write-Output "Service account : $($svcWmi.StartName)" }
}
else {
    Write-Output 'Windows service : PBIEgwService NOT FOUND -- collectors assume this name.'
    Get-Service | Where-Object { $_.Name -like '*gateway*' -or $_.DisplayName -like '*gateway*' } |
        Select-Object Name, DisplayName, Status | Format-Table -AutoSize | Out-String | Write-Output
}

foreach ($p in @(
        'C:\Program Files\On-premises data gateway',
        "$env:SystemDrive\Users\PBIEgwService\AppData\Local\Microsoft\On-premises data gateway",
        "$env:LOCALAPPDATA\Microsoft\On-premises data gateway",
        "$env:WINDIR\ServiceProfiles\PBIEgwService\AppData\Local\Microsoft\On-premises data gateway"
    )) {
    Write-Output "$(if (Test-Path $p) { '[EXISTS]  ' } else { '[MISSING] ' })$p"
}

Write-Output ''
Write-Output 'Next: tell Claude this finished, and paste any [MISSING]/NOT FOUND lines above.'
Write-Output 'Those are the hardcoded assumptions that need correcting in the collectors.'
