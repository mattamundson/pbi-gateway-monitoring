# =============================================================================
# Collect-EventLog.ps1
# Label: [NET-NEW/ADAPTED]
#
# Pain points addressed:
#   #2  -- Opaque refresh failure triage (service-level crash/restart events)
#   #10 -- Service stability (gateway service events)
#
# Signal: S9 -- Windows Event Log (gateway service crash, restart, failure events)
#
# Adapted from:
#   martinskeem/powerbi-powershell WRITE pattern (New-EventLog / Write-EventLog):
#     https://github.com/martinskeem/powerbi-powershell
#   We INVERT the pattern: martinskeem WRITES gateway status to the Event Log;
#   this script READS the Event Log for gateway service events.
#   The design principle (Event Log as the integration surface) is credited
#   to martinskeem's implementation.
#
# What this collects:
#   - Application log entries from "On-premises data gateway" provider
#   - System log entries related to gateway service (SCM events for PBIEgwService)
#   - Warning and Error level events in the last N minutes
#
# [Unverified] Provider names and Event IDs confirmed from community reports and
#              MS troubleshooting guides but not validated on a live gateway host.
#              Exact Event IDs may vary by gateway version and Windows build.
#              Requires Phase 5 pilot to confirm filter criteria.
#
# Key Event IDs to capture (community-confirmed, [Assumption]):
#   Application log, "On-premises data gateway":
#     - Service started/stopped/crashed events
#   System log, Service Control Manager (SCM):
#     - EventId 7034: service terminated unexpectedly
#     - EventId 7036: service entered stopped/running state
#     - EventId 7031: service crashed and was restarted
#   EventId references: https://docs.microsoft.com/en-us/previous-versions/windows/it-pro/windows-server-2008-R2-and-2008/cc756927(v=ws.10)
#
# Recommended scheduling: Windows Scheduled Task every 5 minutes.
# =============================================================================

[CmdletBinding()]
param(
    [Parameter(Mandatory = $false)]
    [string]$OutputPath = "$PSScriptRoot\..\output",

    # How far back to look on first run; subsequent runs use watermark
    [Parameter(Mandatory = $false)]
    [int]$LookbackMinutes = 30,

    # Only collect Warning and Error level events (skip Information noise)
    [Parameter(Mandatory = $false)]
    [string[]]$LevelFilter = @("Error", "Warning"),

    # Gateway service name to filter SCM events (EventId 7034/7031/7036)
    # [Assumption] Gateway Windows Service is named "PBIEgwService"
    [Parameter(Mandatory = $false)]
    [string]$GatewayServiceName = "PBIEgwService"
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

if (-not (Test-Path $OutputPath)) { New-Item -ItemType Directory -Path $OutputPath | Out-Null }

$watermarkPath = "$OutputPath\watermark_event_log.json"
$collectedAtUtc = (Get-Date).ToUniversalTime()

# ---------------------------------------------------------------------------
# Watermark for incremental collection
# ---------------------------------------------------------------------------
$watermark = @{ LastProcessedUtc = $collectedAtUtc.AddMinutes(-$LookbackMinutes).ToString("o") }
if (Test-Path $watermarkPath) {
    $watermark = Get-Content $watermarkPath -Raw | ConvertFrom-Json
}
$sinceUtc = [datetime]::Parse($watermark.LastProcessedUtc)
Write-Verbose "Collecting Event Log entries since: $sinceUtc"

# ---------------------------------------------------------------------------
# Level mapping: Get-WinEvent uses Level values (1=Critical, 2=Error, 3=Warning)
# ---------------------------------------------------------------------------
$levelMap = @{
    "Critical"    = 1
    "Error"       = 2
    "Warning"     = 3
    "Information" = 4
    "Verbose"     = 5
}
$levelValues = $LevelFilter | ForEach-Object { $levelMap[$_] } | Where-Object { $_ -ne $null }

$allEvents = @()
$collectionErrors = @()

# ---------------------------------------------------------------------------
# Application log -- "On-premises data gateway" provider
# [Adapted from martinskeem pattern] martinskeem uses Application log as
# the integration surface for gateway status. We read from the same log.
# Reference: https://github.com/martinskeem/powerbi-powershell
# [Unverified] ProviderName "On-premises data gateway" is the confirmed
# provider name used by the gateway service for Application log entries.
# Validate in Phase 5 with: Get-WinEvent -ListProvider "*gateway*"
# ---------------------------------------------------------------------------
try {
    Write-Verbose "Querying Application log for gateway provider events..."

    $appFilter = @{
        LogName      = 'Application'
        StartTime    = $sinceUtc
        Level        = $levelValues
    }

    # Note: Get-WinEvent -FilterHashtable does not support ProviderName in all
    # PS versions. We filter by provider post-query for compatibility.
    # Array-wrap the pipeline: under Set-StrictMode -Version Latest, a pipeline
    # that emits nothing assigns $null, and $null.Count THROWS. @(...) forces an
    # array so .Count is always valid (0 when empty).
    $appEvents = @(Get-WinEvent -FilterHashtable $appFilter -ErrorAction Stop |
        Where-Object { $_.ProviderName -match "gateway|PBIEgw|OnPremises" })

    foreach ($evt in $appEvents) {
        $allEvents += @{
            TimeCreated      = $evt.TimeCreated.ToUniversalTime().ToString("o")
            EventId          = $evt.Id
            LevelDisplayName = $evt.LevelDisplayName
            ProviderName     = $evt.ProviderName
            LogName          = "Application"
            Message          = $evt.Message
            GatewayHostName  = $env:COMPUTERNAME
            EventSource      = "Application_GatewayProvider"
        }
    }
    Write-Verbose "Found $($appEvents.Count) Application log gateway events"
}
catch {
    if ($_.Exception.Message -like "*No events were found*") {
        Write-Verbose "No Application log gateway events found in window"
    }
    else {
        $errMsg = "Application log query failed: $_"
        Write-Warning $errMsg
        $collectionErrors += $errMsg
    }
}

# ---------------------------------------------------------------------------
# System log -- Service Control Manager events for gateway service
# EventId 7034: Service terminated unexpectedly
# EventId 7031: Service terminated and will be restarted
# EventId 7036: Service entered running/stopped state
# [Unverified] These SCM Event IDs are standard Windows but filtering
#              by ServiceName in Message requires string match (fragile).
# ---------------------------------------------------------------------------
try {
    Write-Verbose "Querying System log for SCM service events..."

    $scmFilter = @{
        LogName   = 'System'
        Id        = @(7034, 7031, 7036)
        StartTime = $sinceUtc
    }

    # Note: Get-WinEvent -FilterHashtable with Id array syntax requires PS 5.1+
    # Array-wrap (see note above): empty pipeline -> $null -> $null.Count throws.
    $sysEvents = @(Get-WinEvent -FilterHashtable $scmFilter -ErrorAction Stop |
        Where-Object { $_.Message -match $GatewayServiceName -or $_.Message -match "gateway" })

    foreach ($evt in $sysEvents) {
        $allEvents += @{
            TimeCreated      = $evt.TimeCreated.ToUniversalTime().ToString("o")
            EventId          = $evt.Id
            LevelDisplayName = $evt.LevelDisplayName
            ProviderName     = $evt.ProviderName
            LogName          = "System"
            Message          = $evt.Message
            GatewayHostName  = $env:COMPUTERNAME
            EventSource      = "System_SCM_GatewayService"
        }
    }
    Write-Verbose "Found $($sysEvents.Count) System SCM gateway service events"
}
catch {
    if ($_.Exception.Message -like "*No events were found*") {
        Write-Verbose "No System SCM gateway events found in window"
    }
    else {
        $errMsg = "System log SCM query failed: $_"
        Write-Warning $errMsg
        $collectionErrors += $errMsg
    }
}

# ---------------------------------------------------------------------------
# Write output
# ---------------------------------------------------------------------------
$output = @{
    CollectedAtUtc   = $collectedAtUtc.ToString("o")
    GatewayHostName  = $env:COMPUTERNAME
    EventCount       = $allEvents.Count
    CollectionErrors = $collectionErrors
    Events           = $allEvents
}

$timestamp = $collectedAtUtc.ToString("yyyyMMdd_HHmmss")
$outputFile = "$OutputPath\event_log_$timestamp.json"

$output | ConvertTo-Json -Depth 10 | Out-File $outputFile -Encoding UTF8

# Update watermark
@{ LastProcessedUtc = $collectedAtUtc.ToString("o") } | ConvertTo-Json | Out-File $watermarkPath -Encoding UTF8

Write-Output "Collect-EventLog: $($allEvents.Count) events written to $outputFile"
