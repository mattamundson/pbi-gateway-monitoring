# =============================================================================
# Collect-DiskSpool.ps1
# Label: [NET-NEW]
#
# Pain point addressed: #9 — Disk Spooler Surprises (no proactive disk monitoring)
# Signal: S11b — Spool directory disk free space + current spool directory size
#
# Context:
#   The gateway writes compressed query results to a spool directory before
#   sending to Power BI Service. SpoolingTotalDataSize in QueryExecution logs
#   records how much was spooled historically. When the disk fills, queries
#   fail with cryptic errors that do not mention disk.
#
#   No existing tool monitors spool disk health proactively:
#   - FPM SystemCounters page does not include disk free-space metrics
#   - PBIT template shows historical spool durations but not current disk
#   - Microsoft documentation: https://learn.microsoft.com/en-us/data-integration/gateway/service-gateway-performance
#
# [NET-NEW] No prior art in any reviewed gateway monitoring tool.
# [Unverified] Spool directory path is [Assumption]-based; confirm on Phase 5 pilot.
#
# Note on StreamBeforeRequestCompletes:
#   If the gateway config has StreamBeforeRequestCompletes=true, spooling
#   behavior changes and SpoolingTotalDataSize may be 0. This script checks
#   and warns on that setting.
#   Reference: https://www.reddit.com/r/PowerBI/comments/1f8uwte/refresh_large_dataset_throttling_error/
#
# Recommended scheduling: Windows Scheduled Task every 5 minutes.
# =============================================================================

[CmdletBinding()]
param(
    [Parameter(Mandatory = $false)]
    [string]$OutputPath = "$PSScriptRoot\..\output",

    # Default spool directory path.
    # [Assumption] Gateway spool is under the service account AppData Temp path.
    # The actual path depends on gateway version and service account configuration.
    # Override via config.json spoolPath setting.
    # Validate on Phase 5 pilot with: Get-DataGatewayInfo | Select-Object SpoolingPath
    # (if Get-DataGatewayInfo SP 401 bug is resolved)
    [Parameter(Mandatory = $false)]
    [string]$SpoolPath = "",

    [Parameter(Mandatory = $false)]
    [string]$ConfigPath = "$PSScriptRoot\..\config\config.json",

    # Alert threshold: warn if free space is below this percentage
    [Parameter(Mandatory = $false)]
    [double]$WarnThresholdPct = 15.0,

    # Alert threshold: error if free space is below this percentage
    [Parameter(Mandatory = $false)]
    [double]$ErrorThresholdPct = 5.0
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

if (-not (Test-Path $OutputPath)) { New-Item -ItemType Directory -Path $OutputPath | Out-Null }

$collectedAtUtc = (Get-Date).ToUniversalTime()

# ---------------------------------------------------------------------------
# Resolve spool path from config or default
# [Assumption] Spool path is under gateway AppData or a configured override
# ---------------------------------------------------------------------------
if ([string]::IsNullOrWhiteSpace($SpoolPath) -and (Test-Path $ConfigPath)) {
    $config = Get-Content $ConfigPath -Raw | ConvertFrom-Json
    # config.sample.json models this as the NESTED key gateway.spoolPath. Reading
    # the flat $config.spoolPath (the old code) THROWS under StrictMode -Latest when
    # that flat key is absent. Read the nested key StrictMode-safely; tolerate legacy flat.
    $cfgSpoolPath = $null
    $gwProp = $config.PSObject.Properties['gateway']
    if ($gwProp -and $gwProp.Value -and $gwProp.Value.PSObject.Properties['spoolPath']) {
        $cfgSpoolPath = $gwProp.Value.spoolPath
    }
    elseif ($config.PSObject.Properties['spoolPath']) {
        $cfgSpoolPath = $config.spoolPath   # legacy flat key
    }
    if (-not [string]::IsNullOrWhiteSpace($cfgSpoolPath)) {
        $SpoolPath = $cfgSpoolPath
    }
}

if ([string]::IsNullOrWhiteSpace($SpoolPath)) {
    # [Assumption] Default spool location under PBIEgwService service account
    # Actual path: confirm by inspecting gateway config or running Get-DataGatewayInfo
    $SpoolPath = "$env:SystemDrive\Users\PBIEgwService\AppData\Local\Microsoft\On-premises data gateway\Spooler"
    Write-Verbose "Using default spool path (may be incorrect): $SpoolPath"
    Write-Verbose "Override by setting spoolPath in config.json"
    Write-Verbose "See: https://learn.microsoft.com/en-us/data-integration/gateway/service-gateway-performance"
}

$result = @{
    CollectedAtUtc     = $collectedAtUtc.ToString("o")
    GatewayHostName    = $env:COMPUTERNAME
    SpoolPath          = $SpoolPath
    SpoolPathExists    = $false
    DiskInfo           = $null
    SpoolDirSizeBytes  = $null
    AlertLevel         = "OK"
    AlertMessage       = $null
    StreamBeforeRequestCompletes_Warning = $false
    CollectionErrors   = @()
}

# ---------------------------------------------------------------------------
# Check disk free space for the drive containing the spool path
# ---------------------------------------------------------------------------
try {
    $spoolDrive = Split-Path -Qualifier $SpoolPath -ErrorAction Stop

    # Get-PSDrive returns drive info including Used and Free space
    # [Unverified] Get-PSDrive works for local drives; UNC/network spool paths
    # would require WMI (Get-WmiObject Win32_LogicalDisk) instead.
    $drive = Get-PSDrive -Name $spoolDrive.TrimEnd(':') -ErrorAction Stop

    $freeBytes = $drive.Free
    $totalBytes = $drive.Free + $drive.Used
    $freePct = if ($totalBytes -gt 0) { ($freeBytes / $totalBytes) * 100 } else { 0 }

    $result.DiskInfo = @{
        DriveLetter    = $spoolDrive
        FreeSpaceBytes = $freeBytes
        TotalSpaceBytes = $totalBytes
        FreeSpacePct   = [Math]::Round($freePct, 2)
    }

    # Determine alert level
    if ($freePct -le $ErrorThresholdPct) {
        $result.AlertLevel   = "ERROR"
        $result.AlertMessage = "Spool disk free space critically low: $([Math]::Round($freePct,1))% free ($([Math]::Round($freeBytes/1GB,1)) GB). Gateway may crash on next large refresh."
        Write-Warning $result.AlertMessage
    }
    elseif ($freePct -le $WarnThresholdPct) {
        $result.AlertLevel   = "WARNING"
        $result.AlertMessage = "Spool disk free space low: $([Math]::Round($freePct,1))% free. Monitor closely."
        Write-Warning $result.AlertMessage
    }

    Write-Verbose "Disk $($spoolDrive): $([Math]::Round($freeBytes/1GB,2)) GB free ($([Math]::Round($freePct,1))%)"
}
catch {
    $errMsg = "Disk free space check failed for spool drive: $_"
    Write-Warning $errMsg
    $result.CollectionErrors += $errMsg
}

# ---------------------------------------------------------------------------
# Measure spool directory size (trend data to correlate with SpoolingTotalDataSize)
# ---------------------------------------------------------------------------
try {
    if (Test-Path $SpoolPath) {
        $result.SpoolPathExists = $true
        # Array-wrap: empty dir -> $null -> $null.Count throws under StrictMode Latest.
        $spoolItems = @(Get-ChildItem -Path $SpoolPath -Recurse -File -ErrorAction SilentlyContinue)
        $spoolSizeBytes = ($spoolItems | Measure-Object -Property Length -Sum).Sum
        $result.SpoolDirSizeBytes = if ($null -ne $spoolSizeBytes) { $spoolSizeBytes } else { 0 }
        Write-Verbose "Spool directory size: $([Math]::Round($result.SpoolDirSizeBytes/1MB,2)) MB ($($spoolItems.Count) files)"
    }
    else {
        Write-Verbose "Spool path not found: $SpoolPath"
        Write-Verbose "This may mean the path is incorrect — validate in Phase 5."
        $result.SpoolDirSizeBytes = 0
    }
}
catch {
    $errMsg = "Spool directory size measurement failed: $_"
    Write-Warning $errMsg
    $result.CollectionErrors += $errMsg
}

# ---------------------------------------------------------------------------
# Check StreamBeforeRequestCompletes gateway config setting
# If set to true, spooling is bypassed and SpoolingTotalDataSize = 0 in logs.
# This affects spool trend correlation in the silver notebook.
# Reference: https://www.reddit.com/r/PowerBI/comments/1f8uwte/refresh_large_dataset_throttling_error/
# [Assumption] Config file location; validate in Phase 5.
# ---------------------------------------------------------------------------
try {
    $gatewayConfigPath = "$env:SystemDrive\Users\PBIEgwService\AppData\Local\Microsoft\On-premises data gateway\Microsoft.PowerBI.DataMovement.Pipeline.GatewayCore.dll.config"
    if (Test-Path $gatewayConfigPath) {
        $configContent = Get-Content $gatewayConfigPath -Raw
        if ($configContent -match 'StreamBeforeRequestCompletes.*?value\s*=\s*"true"') {
            $result.StreamBeforeRequestCompletes_Warning = $true
            Write-Warning "StreamBeforeRequestCompletes=true detected in gateway config. SpoolingTotalDataSize will be 0 in logs. Spool size metrics unreliable."
        }
    }
}
catch {
    Write-Verbose "Could not check StreamBeforeRequestCompletes setting: $_"
}

# ---------------------------------------------------------------------------
# Write output
# ---------------------------------------------------------------------------
$timestamp = $collectedAtUtc.ToString("yyyyMMdd_HHmmss")
$outputFile = "$OutputPath\disk_spool_$timestamp.json"

$result | ConvertTo-Json -Depth 5 | Out-File $outputFile -Encoding UTF8
Write-Output "Collect-DiskSpool: AlertLevel=$($result.AlertLevel), FreeSpacePct=$($result.DiskInfo.FreeSpacePct)%. Output: $outputFile"
