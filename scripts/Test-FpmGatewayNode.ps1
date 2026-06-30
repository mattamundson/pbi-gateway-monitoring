#requires -Version 7
<#
.SYNOPSIS
    Post-deploy health validator for a Fabric Platform Monitoring (FPM)
    On-Premises Data Gateway monitoring node.

.DESCRIPTION
    Runs a series of local checks on a gateway node that was set up with
    Deploy-FpmGatewayNode.ps1, so you can confirm health across a cluster
    WITHOUT opening the Fabric report for each node. Checks:

      1. Install layout present (scripts, Modules, configs).
      2. config.json sanity: valid JSON, GatewayId set, SP block populated,
         SecretText non-empty (machine-bound — see note), EventHub connection
         strings present for Heartbeat + Reports.
      3. Required PowerShell modules installed (Az.Accounts, Az.Storage,
         DataGateway, MicrosoftPowerBIMgmt).
      4. The on-prem gateway process (Microsoft.PowerBI.EnterpriseGateway) running.
      5. Scheduled tasks present and in the expected state:
         FPM-Gateway-Heartbeat / -UploadLogs Running (boot loops);
         FPM-Gateway-NodeInfo Ready (weekly).
      6. Heartbeat log advancing (mtime within -FreshMinutes).
      7. Upload activity recent (GatewayMonitoring.log mtime within -FreshMinutes),
         plus newest *Report_*.log age in the gateway log path.
      8. Error scan of the node logs for recent failures.

    Emits a colorized table, a structured result object, an optional JSON file
    (-JsonOut) for fleet aggregation, and sets exit code 0 (all pass / warn-only)
    or 1 (any FAIL) for use in monitoring/CI.

.PARAMETER InstallRoot
    FPM script install root on this node.
    Default: C:\GatewayMonitoring\rt-gateway-log\PowerShell Script

.PARAMETER GatewayLogPath
    Gateway log directory. Default: standard PBIEgwService path.

.PARAMETER FreshMinutes
    How recent log activity must be to count as healthy. Default 10.

.PARAMETER JsonOut
    Optional path to write the result object as JSON (for fleet sweeps).

.PARAMETER Quiet
    Suppress the table; only set exit code (+ JSON if requested).

.EXAMPLE
    .\Test-FpmGatewayNode.ps1

.EXAMPLE
    # Fleet sweep across nodes via PowerShell remoting, collect JSON centrally:
    Invoke-Command -ComputerName GW01,GW02,GW03 -FilePath .\Test-FpmGatewayNode.ps1 |
        Export-Csv \\share\fpm-health.csv -NoTypeInformation

.NOTES
    Read-only. Safe to run repeatedly. Run on the gateway node (or via remoting).
    A populated SecretText cannot be validated for *correctness* locally (machine-
    bound encryption); this script only checks it is non-empty.
#>
[CmdletBinding()]
param(
    [string] $InstallRoot = "C:\GatewayMonitoring\rt-gateway-log\PowerShell Script",
    [string] $GatewayLogPath = "C:\Windows\ServiceProfiles\PBIEgwService\AppData\Local\Microsoft\On-premises data gateway",
    [int]    $FreshMinutes = 10,
    [string] $JsonOut,
    [switch] $Quiet
)

$ErrorActionPreference = "Stop"
$now = Get-Date

$results = [System.Collections.Generic.List[object]]::new()
function Add-Check {
    param([string]$Name,[ValidateSet("PASS","WARN","FAIL","INFO")]$Status,[string]$Detail)
    $results.Add([pscustomobject]@{ Check=$Name; Status=$Status; Detail=$Detail })
}

$configDir = Join-Path $InstallRoot "configs"
$logDir    = Join-Path $InstallRoot "logs"
$modDir    = Join-Path $InstallRoot "Modules"
$configPath = Join-Path $configDir "Config.json"

# ---------- 1. LAYOUT ----------
if (Test-Path $InstallRoot) { Add-Check "InstallRoot exists" PASS $InstallRoot }
else { Add-Check "InstallRoot exists" FAIL "Not found: $InstallRoot"; }

foreach ($s in @("Run-GatewayHeartbeat.ps1","Run-UploadGatewayLogs.ps1","Get-DataGatewayInfo.ps1","Setup-UpdateConfiguration.ps1")) {
    $p = Join-Path $InstallRoot $s
    if (Test-Path $p) { Add-Check "Script: $s" PASS "" } else { Add-Check "Script: $s" FAIL "missing" }
}
if (Get-ChildItem $modDir -Filter *.psm1 -ErrorAction SilentlyContinue) { Add-Check "Modules\*.psm1 present" PASS "" }
else { Add-Check "Modules\*.psm1 present" FAIL "no .psm1 in $modDir" }

# ---------- 2. CONFIG ----------
$cfg = $null
if (Test-Path $configPath) {
    try {
        $cfg = Get-Content $configPath -Raw | ConvertFrom-Json
        Add-Check "config.json valid JSON" PASS ""
    } catch { Add-Check "config.json valid JSON" FAIL $_.Exception.Message }
} else { Add-Check "config.json present" FAIL "missing: $configPath" }

if ($cfg) {
    if ($cfg.GatewayId) { Add-Check "GatewayId set" PASS $cfg.GatewayId }
    else { Add-Check "GatewayId set" FAIL "empty — run Setup-UpdateConfiguration" }

    $sp = $cfg.ServicePrincipal
    if ($sp -and $sp.AppId) { Add-Check "SP AppId set" PASS $sp.AppId } else { Add-Check "SP AppId set" FAIL "empty" }
    if ($sp -and $sp.SecretText) { Add-Check "SP SecretText present" PASS "(encrypted; correctness not verifiable locally)" }
    else { Add-Check "SP SecretText present" FAIL "empty — re-run Setup-UpdateConfiguration ON THIS NODE" }

    $cs = $cfg.EventHubs.ConnectionStrings
    $hb = $cs | Where-Object { $_.Report -eq "Heartbeat" -and $_.EventHubConnectionString }
    $rp = $cs | Where-Object { $_.Report -eq "Reports"   -and $_.EventHubConnectionString }
    if ($hb) { Add-Check "EventHub: Heartbeat conn str" PASS "" } else { Add-Check "EventHub: Heartbeat conn str" FAIL "missing" }
    if ($rp) { Add-Check "EventHub: Reports conn str"   PASS "" } else { Add-Check "EventHub: Reports conn str"   FAIL "missing" }

    if ($cfg.GatewayLogsPath) {
        $cfgLog = @($cfg.GatewayLogsPath)[0]
        if ($cfgLog) { $GatewayLogPath = $cfgLog }  # trust the config's path if present
        Add-Check "Config gateway log path" INFO $cfgLog
    }
}

# ---------- 3. MODULES ----------
foreach ($mod in @("Az.Accounts","Az.Storage","DataGateway","MicrosoftPowerBIMgmt")) {
    if (Get-Module -ListAvailable -Name $mod) { Add-Check "Module: $mod" PASS "" }
    else {
        $st = if ($mod -eq "MicrosoftPowerBIMgmt") { "WARN" } else { "FAIL" }  # PBIMgmt only needed for NodeInfo
        Add-Check "Module: $mod" $st "not installed"
    }
}

# ---------- 4. GATEWAY PROCESS ----------
$gwProc = Get-Process -Name "Microsoft.PowerBI.EnterpriseGateway" -ErrorAction SilentlyContinue
if ($gwProc) {
    $ver = ($gwProc | Select-Object -First 1).FileVersion
    Add-Check "Gateway process running" PASS ("PID $($gwProc.Id -join ',') v$ver")
} else {
    Add-Check "Gateway process running" FAIL "Microsoft.PowerBI.EnterpriseGateway not found — gateway service down?"
}

# ---------- 5. SCHEDULED TASKS ----------
$taskExpect = @{
    "FPM-Gateway-Heartbeat"  = "Running"   # boot loop
    "FPM-Gateway-UploadLogs" = "Running"   # boot loop
    "FPM-Gateway-NodeInfo"   = "Ready"     # weekly
}
foreach ($tn in $taskExpect.Keys) {
    $t = Get-ScheduledTask -TaskName $tn -ErrorAction SilentlyContinue
    if (-not $t) { Add-Check "Task: $tn" FAIL "not registered"; continue }
    $state = $t.State.ToString()
    $info  = Get-ScheduledTaskInfo -TaskName $tn -ErrorAction SilentlyContinue
    $last  = if ($info) { "lastRun=$($info.LastRunTime) result=0x{0:X}" -f $info.LastTaskResult } else { "" }
    $want  = $taskExpect[$tn]
    if ($tn -like "*Heartbeat" -or $tn -like "*UploadLogs") {
        if ($state -eq "Running") { Add-Check "Task: $tn" PASS "$state; $last" }
        elseif ($state -eq "Ready") { Add-Check "Task: $tn" WARN "Ready but not Running (boot loop expected to be Running). $last" }
        else { Add-Check "Task: $tn" FAIL "$state; $last" }
    } else {
        if ($state -in @("Ready","Running")) { Add-Check "Task: $tn" PASS "$state; $last" }
        else { Add-Check "Task: $tn" WARN "$state; $last" }
    }
}

# ---------- 6/7. LOG FRESHNESS ----------
function Test-Fresh {
    param([string]$Path,[string]$Label)
    if (-not (Test-Path $Path)) { Add-Check $Label WARN "no file at $Path (script may not have written yet)"; return }
    $age = ($now - (Get-Item $Path).LastWriteTime).TotalMinutes
    $ageStr = "{0:N1} min old" -f $age
    if ($age -le $FreshMinutes) { Add-Check $Label PASS $ageStr }
    else { Add-Check $Label WARN "$ageStr (> $FreshMinutes min — is the task running?)" }
}
Test-Fresh (Join-Path $logDir "Heartbeat.log")          "Heartbeat log advancing"
Test-Fresh (Join-Path $logDir "GatewayMonitoring.log")  "Upload log advancing"

# newest gateway report file age (data the uploader ships)
if (Test-Path $GatewayLogPath) {
    $newest = Get-ChildItem -Path $GatewayLogPath -Recurse -Filter "*Report_*.log" -ErrorAction SilentlyContinue |
              Sort-Object LastWriteTime -Descending | Select-Object -First 1
    if ($newest) {
        $age = ($now - $newest.LastWriteTime).TotalMinutes
        Add-Check "Newest gateway Report_*.log" INFO ("{0} ({1:N1} min old)" -f $newest.Name,$age)
    } else { Add-Check "Newest gateway Report_*.log" INFO "none found (gateway idle / no jobs yet)" }
} else {
    Add-Check "Gateway log path" WARN "not found: $GatewayLogPath"
}

# ---------- 8. RECENT ERRORS IN NODE LOGS ----------
$errHits = 0
foreach ($lf in @("Heartbeat.log","GatewayMonitoring.log")) {
    $p = Join-Path $logDir $lf
    if (Test-Path $p) {
        $recent = Get-Content $p -Tail 200 -ErrorAction SilentlyContinue |
                  Where-Object { $_ -match '(?i)\b(error|exception|fail|401|unauthorized)\b' }
        if ($recent) { $errHits += $recent.Count }
    }
}
if ($errHits -eq 0) { Add-Check "Recent errors in node logs" PASS "none in last 200 lines" }
else { Add-Check "Recent errors in node logs" WARN "$errHits matching line(s) — inspect $logDir" }

# ---------- OUTPUT ----------
$fail = ($results | Where-Object Status -eq "FAIL").Count
$warn = ($results | Where-Object Status -eq "WARN").Count
$pass = ($results | Where-Object Status -eq "PASS").Count
$overall = if ($fail -gt 0) { "FAIL" } elseif ($warn -gt 0) { "WARN" } else { "PASS" }

$summary = [pscustomobject]@{
    Computer  = $env:COMPUTERNAME
    GatewayId = if ($cfg) { $cfg.GatewayId } else { $null }
    Overall   = $overall
    Pass      = $pass
    Warn      = $warn
    Fail      = $fail
    TimestampUtc = $now.ToUniversalTime().ToString("o")
    Checks    = $results
}

if (-not $Quiet) {
    Write-Host "`nFPM Gateway Node Health — $($env:COMPUTERNAME)" -ForegroundColor Cyan
    foreach ($r in $results) {
        $color = switch ($r.Status) { "PASS"{"Green"} "WARN"{"Yellow"} "FAIL"{"Red"} default{"Gray"} }
        Write-Host ("  [{0,-4}] {1,-32} {2}" -f $r.Status,$r.Check,$r.Detail) -ForegroundColor $color
    }
    $oc = switch ($overall) { "PASS"{"Green"} "WARN"{"Yellow"} default{"Red"} }
    Write-Host ("`n  OVERALL: {0}   (pass {1} / warn {2} / fail {3})" -f $overall,$pass,$warn,$fail) -ForegroundColor $oc
}

if ($JsonOut) {
    $summary | ConvertTo-Json -Depth 6 | Set-Content -Path $JsonOut -Encoding UTF8
    if (-not $Quiet) { Write-Host "  JSON written: $JsonOut" -ForegroundColor Gray }
}

# emit object to pipeline (for Invoke-Command fleet sweeps) and set exit code
$summary
if ($fail -gt 0) { exit 1 } else { exit 0 }
