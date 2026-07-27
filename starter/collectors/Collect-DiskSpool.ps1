# =============================================================================
# Collect-DiskSpool.ps1
# Label: [NET-NEW]
#
# Pain point addressed: #9 -- Disk Spooler Surprises (no proactive disk monitoring)
# Signal: S11b -- Spool directory disk free space + current spool directory size
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

# Tracks whether the path below is the hardcoded built-in guess rather than a
# configured value, so a "spool path not found" error can say which one failed.
$spoolPathIsDefault = $false
$spoolPathAccountSource = "configured"

# T10: "PBIEgwService" is the WINDOWS SERVICE name, not necessarily the
# account's home-folder name -- the account it actually LOGS ON AS
# (Win32_Service.StartName) is whatever was chosen at install time (a domain
# account, a dedicated local service account, etc.) and is what the AppData
# path is really under. The prior code assumed these were the same string.
# Discover the real account once, up front, so both the spool-path guess
# below and the gateway-config-file probe further down can use it.
$discoveredServiceAccount = $null
try {
    $svc = Get-CimInstance -ClassName Win32_Service -Filter "Name='PBIEgwService'" -ErrorAction Stop
    if ($svc -and $svc.StartName -and $svc.StartName -notmatch '^(LocalSystem|NT AUTHORITY\\|NT SERVICE\\)') {
        # StartName is DOMAIN\user, .\user, or a bare user; the AppData
        # folder is named after the bare account, so strip any prefix.
        $discoveredServiceAccount = ($svc.StartName -split '\\')[-1]
    }
}
catch {
    Write-Verbose "PBIEgwService account discovery failed, falling back to the default guess: $_"
}

if ([string]::IsNullOrWhiteSpace($SpoolPath)) {
    if ($discoveredServiceAccount) {
        $SpoolPath = "$env:SystemDrive\Users\$discoveredServiceAccount\AppData\Local\Microsoft\On-premises data gateway\Spooler"
        $spoolPathAccountSource = "discovered"
        Write-Verbose "Spool path derived from the gateway service's actual account ($discoveredServiceAccount): $SpoolPath"
    }
    else {
        # [Assumption] Default spool location under a service account literally
        # named PBIEgwService. Actual path: confirm by inspecting gateway
        # config or running Get-DataGatewayInfo.
        $SpoolPath = "$env:SystemDrive\Users\PBIEgwService\AppData\Local\Microsoft\On-premises data gateway\Spooler"
        $spoolPathAccountSource = "default-guess"
        Write-Verbose "Using default spool path (may be incorrect): $SpoolPath"
        Write-Verbose "Override by setting spoolPath in config.json"
        Write-Verbose "See: https://learn.microsoft.com/en-us/data-integration/gateway/service-gateway-performance"
    }
    $spoolPathIsDefault = $true
}

$result = @{
    CollectedAtUtc                       = $collectedAtUtc.ToString("o")
    GatewayHostName                      = $env:COMPUTERNAME
    CollectorName                        = "Collect-DiskSpool"
    SpoolPath                            = $SpoolPath
    SpoolPathExists                      = $false
    SpoolPathIsDefault                   = $spoolPathIsDefault
    # "configured" (explicit SpoolPath/config.json) | "discovered" (real
    # PBIEgwService StartName resolved via CIM) | "default-guess" (fallback
    # literal PBIEgwService folder name -- discovery found nothing usable).
    SpoolPathAccountSource               = $spoolPathAccountSource
    DiskInfo                             = $null
    # $null means NOT MEASURED. Zero means measured and genuinely empty. These are
    # different facts and the collector must never conflate them -- a wrong spool
    # path previously reported 0 bytes with a clean bill of health, making a
    # misconfigured collector indistinguishable from a healthy gateway.
    SpoolDirSizeBytes                    = $null
    SpoolFileCount                       = $null
    AlertLevel                           = "OK"
    AlertMessage                         = $null
    StreamBeforeRequestCompletes_Warning = $false
    ConfigProbeAttempted                 = $false
    CollectionErrors                     = @()
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
        DriveLetter     = $spoolDrive
        FreeSpaceBytes  = $freeBytes
        TotalSpaceBytes = $totalBytes
        FreeSpacePct    = [Math]::Round($freePct, 2)
    }

    # Determine alert level
    if ($freePct -le $ErrorThresholdPct) {
        $result.AlertLevel = "ERROR"
        $result.AlertMessage = "Spool disk free space critically low: $([Math]::Round($freePct,1))% free ($([Math]::Round($freeBytes/1GB,1)) GB). Gateway may crash on next large refresh."
        Write-Warning $result.AlertMessage
    }
    elseif ($freePct -le $WarnThresholdPct) {
        $result.AlertLevel = "WARNING"
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

        # -ErrorAction SilentlyContinue alone made an unreadable spool directory
        # look identical to an empty one: a FULL spool under a service account
        # without read rights reported 0 bytes and AlertLevel OK. Keep the
        # non-terminating behaviour (a single denied subdirectory must not lose
        # the whole measurement) but CAPTURE the errors so an incomplete walk is
        # reported as incomplete.
        $gciErrors = @()
        $spoolItems = @(Get-ChildItem -Path $SpoolPath -Recurse -File `
                -ErrorAction SilentlyContinue -ErrorVariable +gciErrors)

        # Sum explicitly rather than via Measure-Object: on an EMPTY collection
        # Measure-Object -Property emits no object at all, so `.Sum` throws
        # PropertyNotFoundException under Set-StrictMode -Version Latest. The
        # healthy steady state of a spool directory is empty, so the collector
        # crashed on exactly the case it is supposed to report as fine.
        [long]$spoolSizeBytes = 0
        foreach ($f in $spoolItems) { $spoolSizeBytes += $f.Length }

        $result.SpoolDirSizeBytes = $spoolSizeBytes
        $result.SpoolFileCount = $spoolItems.Count

        if ($gciErrors.Count -gt 0) {
            # The number is a FLOOR, not a measurement. Say so, and null it out --
            # a partial byte count silently understates spool pressure, which is
            # the precise failure the disk alert exists to catch.
            $errMsg = ("Spool directory walk incomplete: $($gciErrors.Count) path(s) could not be read " +
                "(first: $($gciErrors[0].Exception.Message)). Measured $spoolSizeBytes bytes across " +
                "$($spoolItems.Count) readable files; TRUE size is higher.")
            Write-Warning $errMsg
            $result.CollectionErrors += $errMsg
            $result.SpoolDirSizeBytes = $null
        }

        Write-Verbose "Spool directory size: $([Math]::Round($spoolSizeBytes / 1MB, 2)) MB ($($spoolItems.Count) files)"
    }
    else {
        # A missing spool path is a COLLECTION FAILURE, not an empty directory.
        # The default path is a hardcoded guess at the PBIEgwService account's
        # AppData, so this is the EXPECTED state on any gateway running under a
        # custom service account -- i.e. the common case, not the rare one.
        $errMsg = "Spool path not found: $SpoolPath"
        if ($spoolPathIsDefault) {
            $errMsg += (" This is the built-in DEFAULT path, not a configured one. Set gateway.spoolPath " +
                "in config.json to this gateway's real spool directory. Spool size is UNKNOWN, not zero.")
        }
        else {
            $errMsg += " Configured via gateway.spoolPath in config.json. Spool size is UNKNOWN, not zero."
        }
        Write-Warning $errMsg
        $result.CollectionErrors += $errMsg
        $result.SpoolDirSizeBytes = $null
    }
}
catch {
    $errMsg = "Spool directory size measurement failed: $_"
    Write-Warning $errMsg
    $result.CollectionErrors += $errMsg
    $result.SpoolDirSizeBytes = $null
}

# ---------------------------------------------------------------------------
# Check StreamBeforeRequestCompletes gateway config setting
# If set to true, spooling is bypassed and SpoolingTotalDataSize = 0 in logs.
# This affects spool trend correlation in the silver notebook.
# Reference: https://www.reddit.com/r/PowerBI/comments/1f8uwte/refresh_large_dataset_throttling_error/
# [Assumption] Config file location; validate in Phase 5.
# ---------------------------------------------------------------------------
try {
    # T10: reuse the account discovered above rather than re-assuming PBIEgwService.
    $gatewayConfigAccount = if ($discoveredServiceAccount) { $discoveredServiceAccount } else { "PBIEgwService" }
    $gatewayConfigPath = "$env:SystemDrive\Users\$gatewayConfigAccount\AppData\Local\Microsoft\On-premises data gateway\Microsoft.PowerBI.DataMovement.Pipeline.GatewayCore.dll.config"
    if (Test-Path $gatewayConfigPath) {
        $result.ConfigProbeAttempted = $true
        $configContent = Get-Content $gatewayConfigPath -Raw
        if ($configContent -match 'StreamBeforeRequestCompletes.*?value\s*=\s*"true"') {
            $result.StreamBeforeRequestCompletes_Warning = $true
            Write-Warning "StreamBeforeRequestCompletes=true detected in gateway config. SpoolingTotalDataSize will be 0 in logs. Spool size metrics unreliable."
        }
    }
}
catch {
    # Previously Write-Verbose only -- invisible under a scheduled task, and the
    # ONLY catch block in this script that did not record a CollectionError. A
    # failed probe means StreamBeforeRequestCompletes is UNKNOWN, and if it is
    # true then every spool metric downstream is meaningless. Silence made an
    # unreliable dataset look authoritative.
    $errMsg = "StreamBeforeRequestCompletes probe failed; spool metrics may be unreliable: $_"
    Write-Warning $errMsg
    $result.CollectionErrors += $errMsg
}

# ---------------------------------------------------------------------------
# Write output
# ---------------------------------------------------------------------------
$timestamp = $collectedAtUtc.ToString("yyyyMMdd_HHmmss")
$outputFile = "$OutputPath\disk_spool_$timestamp.json"

$result | ConvertTo-Json -Depth 5 | Out-File $outputFile -Encoding UTF8

# Health sidecar -- the ONLY channel by which a failing collector becomes visible
# in the lakehouse. Without it, every CollectionError above dies on this host.
. (Join-Path $PSScriptRoot 'CollectorHealth.ps1')
Write-CollectorHealth -CollectorName 'Collect-DiskSpool' -OutputPath $OutputPath `
    -CollectionErrors $result.CollectionErrors -CollectedAtUtc $collectedAtUtc `
    -RecordCount 1 -Context @{
    SpoolPath              = $SpoolPath
    SpoolPathExists        = "$($result.SpoolPathExists)"
    SpoolPathIsDefault     = "$($result.SpoolPathIsDefault)"
    SpoolPathAccountSource = $result.SpoolPathAccountSource
    AlertLevel             = $result.AlertLevel
}

# $result.DiskInfo is $null whenever the drive read failed. Dereferencing
# .FreeSpacePct on it threw PropertyNotFoundException under StrictMode -Latest,
# AFTER the file was written but BEFORE the script exited -- so the collector
# terminated non-zero and the scheduled task reported failure even though the
# output (including the CollectionError explaining the drive failure) was on
# disk. The summary line must never be able to fail the collection it summarises.
$freePctText = if ($null -ne $result.DiskInfo) { "$($result.DiskInfo.FreeSpacePct)%" } else { "unknown" }
$spoolText = if ($null -ne $result.SpoolDirSizeBytes) { "$([Math]::Round($result.SpoolDirSizeBytes / 1MB, 2)) MB" } else { "NOT MEASURED" }
$errText = if ($result.CollectionErrors.Count -gt 0) { ", Errors=$($result.CollectionErrors.Count)" } else { "" }
Write-Output "Collect-DiskSpool: AlertLevel=$($result.AlertLevel), FreeSpacePct=$freePctText, Spool=$spoolText$errText. Output: $outputFile"
