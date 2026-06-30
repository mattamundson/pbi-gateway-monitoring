#requires -Version 7
<#
.SYNOPSIS
    One-command per-node deployer for Microsoft Fabric Platform Monitoring (FPM)
    On-Premises Data Gateway monitoring scripts.

.DESCRIPTION
    Automates the manual gateway-node checklist from the FPM solution accelerator:
      1. Verifies prerequisites (PowerShell 7, gateway log path, config.json).
      2. Creates the install folder layout FPM expects.
      3. Downloads the FPM gateway PowerShell scripts + /Modules from GitHub
         (or copies from a local path you already cloned).
      4. Places your generated config.json into .\configs\Config.json.
      5. Installs required PowerShell modules (Az.Accounts, Az.Storage,
         DataGateway, MicrosoftPowerBIMgmt) NON-interactively, so the
         interactive prompts in Setup-UpdateConfiguration.ps1 can be answered "N".
      6. Runs Setup-UpdateConfiguration.ps1 to detect GatewayId + encrypt the
         SP secret (machine-bound — must run on THIS node).
      7. Imports the three Task Scheduler jobs (Heartbeat, Upload Logs, NodeInfo),
         rewriting the hardcoded <UserId> SID and script path to THIS machine.

    Idempotent: re-running re-syncs scripts, re-imports tasks (deletes existing
    same-named tasks first), and leaves an existing populated config alone unless
    -ForceConfig is passed.

    SOURCES (verified June 2026):
      FPM repo: https://github.com/microsoft/fabric-toolbox/tree/main/monitoring/fabric-platform-monitoring
      Task XMLs hardcode SID S-1-5-21-...-500 and path
        "C:\GatewayMonitoring\rt-gateway-log\PowerShell Script\" — this script rewrites both.
      Setup-UpdateConfiguration.ps1 params: -configFilePath ".\configs\Config.json", -logFolder ".\logs\"
      Heartbeat/Upload use BootTrigger + internal loops; NodeInfo is weekly (Sunday).

.PARAMETER InstallRoot
    Root folder on the gateway node where FPM scripts live.
    Default: C:\GatewayMonitoring\rt-gateway-log\PowerShell Script
    (matches the path baked into the FPM Task Scheduler XML templates).

.PARAMETER ConfigSourcePath
    Path to the config.json you downloaded from the FPM "Gateway Config" notebook.
    Required unless a populated config already exists at the destination.

.PARAMETER LocalRepoPath
    Optional. Path to an already-cloned fabric-toolbox repo. If supplied, scripts
    are copied from here instead of downloaded from GitHub (use in air-gapped envs).

.PARAMETER RunAsUser
    The account the scheduled tasks run under (DOMAIN\User or .\User or UPN).
    Defaults to the current user. Its SID is written into the task XML.

.PARAMETER GatewayLogPath
    Override the gateway log directory. Default is the standard PBIEgwService path.

.PARAMETER SkipModuleInstall
    Skip installing the four PowerShell modules (use if already present).

.PARAMETER SkipTasks
    Deploy scripts + config + run setup, but do NOT import the scheduled tasks.

.PARAMETER ForceConfig
    Overwrite an existing destination config.json with -ConfigSourcePath.

.PARAMETER WhatIfTasks
    Show what task import would do without registering tasks.

.EXAMPLE
    .\Deploy-FpmGatewayNode.ps1 -ConfigSourcePath C:\temp\config.json

.EXAMPLE
    .\Deploy-FpmGatewayNode.ps1 -ConfigSourcePath .\config.json `
        -RunAsUser "CONTOSO\svc-gwmon" -LocalRepoPath C:\src\fabric-toolbox

.NOTES
    Run in an ELEVATED PowerShell 7 session on the gateway node.
    Register-ScheduledTask with LogonType Password will prompt for the RunAsUser password.
#>
[CmdletBinding()]
param(
    [string] $InstallRoot = "C:\GatewayMonitoring\rt-gateway-log\PowerShell Script",
    [string] $ConfigSourcePath,
    [string] $LocalRepoPath,
    [string] $RunAsUser = "$env:USERDOMAIN\$env:USERNAME",
    [string] $GatewayLogPath = "C:\Windows\ServiceProfiles\PBIEgwService\AppData\Local\Microsoft\On-premises data gateway",
    [switch] $SkipModuleInstall,
    [switch] $SkipTasks,
    [switch] $ForceConfig,
    [switch] $WhatIfTasks
)

$ErrorActionPreference = "Stop"

# ----- constants from the FPM repo -----
$RepoRawBase = "https://raw.githubusercontent.com/microsoft/fabric-toolbox/main/monitoring/fabric-platform-monitoring/gateway"
$ScriptFiles = @(
    "Get-DataGatewayInfo.ps1","Get-DataGatewayInfo.cmd",
    "Install-DataGatewayAuto.ps1","Install-DataGatewayAuto.cmd",
    "Run-GatewayHeartbeat.ps1","Run-GatewayHeartbeat.cmd",
    "Run-UploadGatewayLogs.ps1","Run-UploadGatewayLogs.cmd",
    "Setup-UpdateConfiguration.ps1","Setup-UpdateConfiguration.cmd"
)
# Module files inside /PowerShellScript/modules (case as in repo). We discover at runtime.
$TaskXmls = @{
    "FPM-Gateway-Heartbeat"  = "TaskSchedulers/Gateway-Heartbeat.xml"
    "FPM-Gateway-UploadLogs" = "TaskSchedulers/Gateway-Upload%20Logs.xml"
    "FPM-Gateway-NodeInfo"   = "TaskSchedulers/Gateway-NodeInfo.xml"
}
# Map task -> the script it should launch (basename only; full path computed)
$TaskScript = @{
    "FPM-Gateway-Heartbeat"  = "Run-GatewayHeartbeat.ps1"
    "FPM-Gateway-UploadLogs" = "Run-UploadGatewayLogs.ps1"
    "FPM-Gateway-NodeInfo"   = "Get-DataGatewayInfo.ps1"
}

function Write-Step($m){ Write-Host "`n=== $m ===" -ForegroundColor Cyan }
function Write-Ok($m){ Write-Host "  [OK] $m" -ForegroundColor Green }
function Write-Warn2($m){ Write-Host "  [! ] $m" -ForegroundColor Yellow }

# ---------- 0. PRECHECKS ----------
Write-Step "Prechecks"
if ($PSVersionTable.PSVersion.Major -lt 7) { throw "PowerShell 7+ required. Current: $($PSVersionTable.PSVersion)" }
Write-Ok "PowerShell $($PSVersionTable.PSVersion)"

$pwshPath = (Get-Command pwsh -ErrorAction SilentlyContinue).Source
if (-not $pwshPath) { $pwshPath = "C:\Program Files\PowerShell\7\pwsh.exe" }
if (-not (Test-Path $pwshPath)) { throw "pwsh.exe not found at '$pwshPath'." }
Write-Ok "pwsh: $pwshPath"

$isAdmin = ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
if (-not $isAdmin) { Write-Warn2 "Not elevated. Module install + task registration may fail. Re-run as Administrator." }

if (Test-Path $GatewayLogPath) { Write-Ok "Gateway log path exists: $GatewayLogPath" }
else { Write-Warn2 "Gateway log path NOT found: $GatewayLogPath  (GatewayId auto-detect will fall back to manual entry)" }

# Resolve RunAsUser -> SID
try {
    $sid = (New-Object System.Security.Principal.NTAccount($RunAsUser)).Translate([System.Security.Principal.SecurityIdentifier]).Value
    Write-Ok "RunAsUser '$RunAsUser' -> SID $sid"
} catch { throw "Could not resolve SID for RunAsUser '$RunAsUser'. Use DOMAIN\User, .\User, or UPN. $_" }

# ---------- 1. FOLDER LAYOUT ----------
Write-Step "Folder layout"
$configDir = Join-Path $InstallRoot "configs"
$logDir    = Join-Path $InstallRoot "logs"
$modDir    = Join-Path $InstallRoot "Modules"
foreach ($d in @($InstallRoot,$configDir,$logDir,$modDir)) {
    if (-not (Test-Path $d)) { New-Item -ItemType Directory -Path $d -Force | Out-Null }
}
Write-Ok "Install root: $InstallRoot"

# ---------- 2. FETCH SCRIPTS + MODULES ----------
Write-Step "Sync FPM gateway scripts"
function Copy-FromLocal {
    param($repo)
    $src = Join-Path $repo "monitoring\fabric-platform-monitoring\gateway\PowerShellScript"
    if (-not (Test-Path $src)) { throw "LocalRepoPath given but not found: $src" }
    Copy-Item (Join-Path $src "*") $InstallRoot -Recurse -Force
    # ensure Modules folder name matches what setup expects (.\Modules)
    $srcMods = Join-Path $src "modules"
    if (Test-Path $srcMods) { Copy-Item (Join-Path $srcMods "*") $modDir -Recurse -Force }
}
function Get-FromGitHub {
    foreach ($f in $ScriptFiles) {
        $url = "$RepoRawBase/PowerShellScript/$f"
        $dst = Join-Path $InstallRoot $f
        try { Invoke-WebRequest -Uri $url -OutFile $dst -UseBasicParsing; }
        catch { Write-Warn2 "Could not download $f ($($_.Exception.Message))" }
    }
    # discover module files via GitHub API
    $apiMods = "https://api.github.com/repos/microsoft/fabric-toolbox/contents/monitoring/fabric-platform-monitoring/gateway/PowerShellScript/modules"
    try {
        $mods = Invoke-RestMethod -Uri $apiMods -Headers @{ "User-Agent"="fpm-deploy" } -UseBasicParsing
        foreach ($m in $mods) {
            if ($m.type -eq "file") {
                Invoke-WebRequest -Uri $m.download_url -OutFile (Join-Path $modDir $m.name) -UseBasicParsing
            }
        }
    } catch { Write-Warn2 "Could not enumerate /modules from GitHub API: $($_.Exception.Message). If air-gapped, use -LocalRepoPath." }
}
if ($LocalRepoPath) { Copy-FromLocal -repo $LocalRepoPath; Write-Ok "Scripts copied from local repo" }
else { Get-FromGitHub; Write-Ok "Scripts downloaded from GitHub main" }

# unblock downloaded files (Mark-of-the-Web)
Get-ChildItem $InstallRoot -Recurse -Include *.ps1,*.psm1,*.cmd -ErrorAction SilentlyContinue | Unblock-File -ErrorAction SilentlyContinue

# sanity: required scripts present
foreach ($req in @("Setup-UpdateConfiguration.ps1","Run-GatewayHeartbeat.ps1","Run-UploadGatewayLogs.ps1","Get-DataGatewayInfo.ps1")) {
    if (-not (Test-Path (Join-Path $InstallRoot $req))) { throw "Required script missing after sync: $req" }
}
if (-not (Get-ChildItem $modDir -Filter *.psm1 -ErrorAction SilentlyContinue)) {
    Write-Warn2 "No *.psm1 found in .\Modules — Setup-UpdateConfiguration.ps1 will fail to import functions. Provide -LocalRepoPath."
}

# ---------- 3. CONFIG ----------
Write-Step "Config.json placement"
$destConfig = Join-Path $configDir "Config.json"
$haveDest = Test-Path $destConfig
if ($ConfigSourcePath) {
    if (-not (Test-Path $ConfigSourcePath)) { throw "ConfigSourcePath not found: $ConfigSourcePath" }
    if ($haveDest -and -not $ForceConfig) {
        Write-Warn2 "Destination config exists; not overwriting (use -ForceConfig). Keeping existing."
    } else {
        Copy-Item $ConfigSourcePath $destConfig -Force
        Write-Ok "Config copied to $destConfig"
    }
} elseif (-not $haveDest) {
    throw "No -ConfigSourcePath and no existing $destConfig. Generate config.json from the FPM 'Gateway Config' notebook first."
} else { Write-Ok "Using existing $destConfig" }

# ---------- 4. MODULES ----------
if (-not $SkipModuleInstall) {
    Write-Step "Install PowerShell modules (non-interactive)"
    $needed = "Az.Accounts","Az.Storage","DataGateway","MicrosoftPowerBIMgmt"
    foreach ($mod in $needed) {
        if (Get-Module -ListAvailable -Name $mod) { Write-Ok "$mod already installed" }
        else {
            try { Install-Module -Name $mod -Scope AllUsers -Force -AllowClobber -ErrorAction Stop; Write-Ok "Installed $mod" }
            catch { try { Install-Module -Name $mod -Scope CurrentUser -Force -AllowClobber; Write-Ok "Installed $mod (CurrentUser)" }
                    catch { Write-Warn2 "Failed to install $mod : $($_.Exception.Message)" } }
        }
    }
} else { Write-Warn2 "SkipModuleInstall set — assuming modules present" }

# ---------- 5. RUN SETUP (interactive: detects GatewayId, encrypts SP secret machine-bound) ----------
Write-Step "Run Setup-UpdateConfiguration.ps1 (interactive)"
Write-Host "  This step is INTERACTIVE. Answer 'N' to the four module-install prompts (already handled above)."
Write-Host "  When prompted, confirm the gateway log path and ENTER THE SP CLIENT SECRET."
Write-Host "  The secret is encrypted with a MACHINE key — it is bound to THIS node.`n"
Push-Location $InstallRoot
try {
    & $pwshPath -NoProfile -ExecutionPolicy Bypass -File (Join-Path $InstallRoot "Setup-UpdateConfiguration.ps1") `
        -configFilePath ".\configs\Config.json" -logFolder ".\logs\"
    Write-Ok "Setup-UpdateConfiguration completed"
} catch {
    Write-Warn2 "Setup-UpdateConfiguration reported an error: $($_.Exception.Message)"
    Write-Warn2 "You can re-run it manually from $InstallRoot once resolved."
} finally { Pop-Location }

# ---------- 6. TASK SCHEDULER IMPORT (SID + path rewrite) ----------
if ($SkipTasks) { Write-Warn2 "SkipTasks set — not registering scheduled tasks"; Write-Step "Done"; return }

Write-Step "Register scheduled tasks (rewriting SID + script path)"
$tmp = Join-Path $env:TEMP "fpm-tasks"
New-Item -ItemType Directory -Path $tmp -Force | Out-Null

foreach ($taskName in $TaskXmls.Keys) {
    $rel = $TaskXmls[$taskName]
    $xmlPath = Join-Path $tmp ("{0}.xml" -f $taskName)

    # get the template
    if ($LocalRepoPath) {
        $localXml = Join-Path $LocalRepoPath ("monitoring\fabric-platform-monitoring\gateway\" + ($rel -replace '%20',' ' -replace '/','\'))
        if (-not (Test-Path $localXml)) { Write-Warn2 "Local task XML missing: $localXml"; continue }
        Copy-Item $localXml $xmlPath -Force
    } else {
        try { Invoke-WebRequest -Uri "$RepoRawBase/$rel" -OutFile $xmlPath -UseBasicParsing }
        catch { Write-Warn2 "Could not download task XML $rel : $($_.Exception.Message)"; continue }
    }

    # Task XMLs are UTF-16; read with auto-detect
    $content = Get-Content -Path $xmlPath -Raw -Encoding Unicode
    if ($content -notmatch "<Task") { $content = Get-Content -Path $xmlPath -Raw }  # fallback UTF-8

    # rewrite the hardcoded script path (the FPM default) to THIS install root
    $targetScript = Join-Path $InstallRoot $TaskScript[$taskName]
    $content = $content -replace [regex]::Escape('C:\GatewayMonitoring\rt-gateway-log\PowerShell Script\Run-GatewayHeartbeat.ps1'), $targetScript
    $content = $content -replace [regex]::Escape('C:\GatewayMonitoring\rt-gateway-log\PowerShell Script\Run-UploadGatewayLogs.ps1'), $targetScript
    $content = $content -replace [regex]::Escape('C:\GatewayMonitoring\rt-gateway-log\PowerShell Script\Get-DataGatewayInfo.ps1'), $targetScript
    # generic: any remaining reference to the default dir -> our install root
    $content = $content -replace [regex]::Escape('C:\GatewayMonitoring\rt-gateway-log\PowerShell Script'), $InstallRoot
    # rewrite pwsh path if different
    $content = $content -replace [regex]::Escape('C:\Program Files\PowerShell\7\pwsh.exe'), $pwshPath
    # rewrite the hardcoded SID -> resolved RunAsUser SID
    $content = $content -replace 'S-1-5-21-[0-9\-]+', $sid

    $finalXml = Join-Path $tmp ("{0}.final.xml" -f $taskName)
    Set-Content -Path $finalXml -Value $content -Encoding Unicode

    if ($WhatIfTasks) { Write-Warn2 "WhatIf: would register '$taskName' -> $targetScript (SID $sid)"; continue }

    # idempotent: remove existing same-named task
    if (Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue) {
        Unregister-ScheduledTask -TaskName $taskName -Confirm:$false
    }

    try {
        # LogonType Password -> prompts for RunAsUser password (matches template <LogonType>Password</LogonType>)
        $cred = Get-Credential -UserName $RunAsUser -Message "Password for scheduled task '$taskName' (RunAs $RunAsUser)"
        Register-ScheduledTask -TaskName $taskName -Xml (Get-Content $finalXml -Raw) `
            -User $cred.UserName -Password $cred.GetNetworkCredential().Password | Out-Null
        Write-Ok "Registered task '$taskName'"
    } catch {
        Write-Warn2 "Failed to register '$taskName': $($_.Exception.Message)"
        Write-Warn2 "Rewritten XML left at: $finalXml — import manually via Task Scheduler if needed."
    }
}

# ---------- 7. SUMMARY / VALIDATION HINTS ----------
Write-Step "Done — validation"
Write-Host @"
  Next, confirm data flow (per FPM):
   1. Start the tasks now (or reboot):  Start-ScheduledTask -TaskName FPM-Gateway-Heartbeat ; Start-ScheduledTask -TaskName FPM-Gateway-UploadLogs
   2. Within ~1 min: Gateways page shows this node online (Responding = true).
   3. After a gateway job runs: Queries page populates.
   4. ~10 min: System Counters page populates.
   5. Logs on this node: $logDir\Heartbeat.log and $logDir\GatewayMonitoring.log

  Reminders:
   - The SP secret is MACHINE-BOUND — run this script on every node; do not copy configs.
   - VNet gateways are NOT supported (on-prem data gateway only).
   - If Get-DataGatewayInfo 401s, ensure latest FPM 'main' + SP gateway Admin role (GitHub issue #321).
"@ -ForegroundColor Gray
