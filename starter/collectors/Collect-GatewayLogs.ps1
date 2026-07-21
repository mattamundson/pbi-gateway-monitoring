# =============================================================================
# Collect-GatewayLogs.ps1
# Label: [ADAPTED-FROM-FPM]
#
# Pain points addressed:
#   #1 - No real-time gateway health alerting (heartbeat data collection)
#   #2 - Opaque refresh failure triage (QueryExecution error rows)
#   #4 - PBIT breaks on upgrade (this script is schema-agnostic at read time;
#        schema-adaptive parsing occurs in 01_bronze_ingest.py)
#   #5 - Mashup memory bloat visibility (SystemCounter rows)
#
# Signals collected: S1 (QueryExecution, QueryStart, Aggregation, SystemCounter)
#                    S3 (PerfMon via SystemCounter supplement)
#
# Adapted from:
#   FPM Run-UploadGatewayLogs pattern:
#     https://github.com/microsoft/fabric-toolbox/tree/main/monitoring/fabric-platform-monitoring
#   RuiRomano/pbigtwmonitor UploadGatewayLogs.ps1 incremental upload pattern:
#     https://github.com/RuiRomano/pbigtwmonitor
#
# [Unverified] - This script has NOT been tested on a live gateway host.
#                Requires Phase 5 pilot to validate file paths, authentication,
#                and OneLake upload behavior.
#
# Known issues to resolve in Phase 5:
#   - Get-DataGatewayInfo returns 401 with service principals in some tenants.
#     Reference: https://community.fabric.microsoft.com/t5/Real-Time-Intelligence/
#       Fabric-Platform-Monitoring-accelerator-Gateways-module/m-p/4884544
#   - Log path varies if gateway runs under a non-default service account.
#     Default: $env:USERPROFILE\AppData\Local\Microsoft\On-premises data gateway\Report
#     Override via -LogRootPath parameter.
#
# Recommended scheduling: Windows Scheduled Task every 5 minutes.
# =============================================================================

[CmdletBinding()]
param(
    [Parameter(Mandatory = $false)]
    [string]$ConfigPath = "$PSScriptRoot\..\config\config.json",

    # Override log root if gateway service account is not PBIEgwService
    [Parameter(Mandatory = $false)]
    [string]$LogRootPath = "",

    # If set, uploads to OneLake staging path; otherwise writes local JSON
    [Parameter(Mandatory = $false)]
    [string]$OutputPath = "$PSScriptRoot\..\output",

    # Key Vault secret name for SP client secret
    [Parameter(Mandatory = $false)]
    [string]$KeyVaultSecretName = "gateway-monitor-sp-secret"
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

# ---------------------------------------------------------------------------
# Load configuration
# [Unverified] Config file structure matches config.sample.json
# ---------------------------------------------------------------------------
if (-not (Test-Path $ConfigPath)) {
    Write-Error "Config file not found: $ConfigPath. Copy config.sample.json to config.json and fill in your values."
    exit 1
}
$config = Get-Content $ConfigPath -Raw | ConvertFrom-Json

# ---------------------------------------------------------------------------
# Resolve gateway log path
# [ADAPTED-FROM-FPM] FPM uses a similar path resolution in Run-UploadGatewayLogs
# Reference: https://github.com/microsoft/fabric-toolbox/tree/main/monitoring/fabric-platform-monitoring
# ---------------------------------------------------------------------------
if ([string]::IsNullOrWhiteSpace($LogRootPath)) {
    # Read the configured path. config.sample.json models it as the NESTED key
    # gateway.logPath -- reading the flat $config.gatewayLogPath (the old code)
    # THROWS under Set-StrictMode -Version Latest when that flat key is absent,
    # so an operator on a custom service account who set gateway.logPath got a
    # crashed collector instead of their path. Read the nested key StrictMode-safely
    # (via PSObject.Properties) and tolerate a legacy flat key for back-compat.
    $cfgLogPath = $null
    $gwProp = $config.PSObject.Properties['gateway']
    if ($gwProp -and $gwProp.Value -and $gwProp.Value.PSObject.Properties['logPath']) {
        $cfgLogPath = $gwProp.Value.logPath
    }
    elseif ($config.PSObject.Properties['gatewayLogPath']) {
        $cfgLogPath = $config.gatewayLogPath   # legacy flat key
    }

    if (-not [string]::IsNullOrWhiteSpace($cfgLogPath)) {
        $LogRootPath = $cfgLogPath
    }
    else {
        # [Assumption] Default service account is PBIEgwService -- will not match a
        # custom service account; set gateway.logPath in config.json to override.
        $LogRootPath = "$env:SystemDrive\Users\PBIEgwService\AppData\Local\Microsoft\On-premises data gateway\Report"
    }
}

Write-Verbose "Gateway log path: $LogRootPath"

if (-not (Test-Path $LogRootPath)) {
    Write-Warning "Gateway log path does not exist: $LogRootPath"
    Write-Warning "Check that performance logging is enabled and the path is correct."
    Write-Warning "See: https://learn.microsoft.com/en-us/data-integration/gateway/service-gateway-performance"
    exit 1
}

# ---------------------------------------------------------------------------
# Incremental watermark
# [ADAPTED-FROM-FPM / pbigtwmonitor] Incremental delta pattern avoids
# re-uploading files already processed. Watermark stored locally.
# Reference: https://github.com/RuiRomano/pbigtwmonitor
# ---------------------------------------------------------------------------
$watermarkPath = "$OutputPath\watermark_gateway_logs.json"
if (-not (Test-Path $OutputPath)) { New-Item -ItemType Directory -Path $OutputPath | Out-Null }

$watermark = @{ LastProcessedUtc = (Get-Date).AddDays(-1).ToUniversalTime().ToString("o") }
if (Test-Path $watermarkPath) {
    $watermark = Get-Content $watermarkPath -Raw | ConvertFrom-Json
}
$lastProcessedUtc = [datetime]::Parse($watermark.LastProcessedUtc)

Write-Verbose "Incremental watermark: $lastProcessedUtc"

# ---------------------------------------------------------------------------
# Discover log files newer than watermark
# Gateway writes: *_QueryExecutionReport_*.log, *_QueryStartReport_*.log,
#                 *_QueryExecutionAggregationReport_*.log, *_SystemCounterReport_*.log
# File naming example: GatewayPerformanceData_QueryExecutionReport_20260101_000000.log
# [Unverified] Exact file name patterns confirmed from MS docs but not live-tested.
# ---------------------------------------------------------------------------
$logPatterns = @(
    "*_QueryExecutionReport_*.log",
    "*_QueryStartReport_*.log",
    "*_QueryExecutionAggregationReport_*.log",
    "*_SystemCounterReport_*.log"
)

$newFiles = @()
foreach ($pattern in $logPatterns) {
    # Array-wrap: an empty pipeline assigns $null, and `$newFiles += $null`
    # would append a phantom null element (inflating .Count) under StrictMode.
    $files = @(Get-ChildItem -Path $LogRootPath -Filter $pattern -Recurse -ErrorAction SilentlyContinue |
            Where-Object { $_.LastWriteTimeUtc -gt $lastProcessedUtc } |
            Sort-Object LastWriteTimeUtc)
    $newFiles += $files
}

Write-Verbose "Found $($newFiles.Count) new/modified log files since $lastProcessedUtc"

if ($newFiles.Count -eq 0) {
    Write-Verbose "No new files to process. Exiting."
    exit 0
}

# ---------------------------------------------------------------------------
# Read and stage each file as a JSON record for bronze ingest
# [NET-NEW] We do NOT parse column values here -- that is the job of
# 01_bronze_ingest.py which does schema-adaptive column-name parsing.
# We only emit the raw CSV content + metadata.
# This avoids the DataFormat.Error that breaks the PBIT template on upgrade.
# Pain #4: schema-adaptive approach; breaking change resilience.
# ---------------------------------------------------------------------------
$stagingRecords = @()
$maxProcessedUtc = $lastProcessedUtc

foreach ($file in $newFiles) {
    try {
        $rawContent = Get-Content $file.FullName -Raw -Encoding UTF8

        # Determine log type from filename
        $logType = switch -Wildcard ($file.Name) {
            "*QueryExecutionReport*" { "QueryExecution" }
            "*QueryStartReport*" { "QueryStart" }
            "*QueryExecutionAggregationReport*" { "QueryAggregation" }
            "*SystemCounterReport*" { "SystemCounter" }
            default { "Unknown" }
        }

        $record = @{
            SourceFile       = $file.FullName
            SourceFileName   = $file.Name
            LogType          = $logType
            GatewayHostName  = $env:COMPUTERNAME
            FileLastWriteUtc = $file.LastWriteTimeUtc.ToString("o")
            CollectedAtUtc   = (Get-Date).ToUniversalTime().ToString("o")
            RawCsvContent    = $rawContent
        }
        $stagingRecords += $record

        if ($file.LastWriteTimeUtc -gt $maxProcessedUtc) {
            $maxProcessedUtc = $file.LastWriteTimeUtc
        }
    }
    catch {
        # Previously a Write-Warning and nothing else. Under a scheduled task
        # nobody reads warnings, so a log directory the service account cannot
        # read produced an EMPTY staging file and a successful exit -- a gateway
        # with zero observability reported as a gateway with zero problems.
        $errMsg = "Failed to read log file $($file.FullName): $_"
        Write-Warning $errMsg
        $collectionErrors += $errMsg
        # Do not update watermark for failed files -- they will retry next run
    }
}

# ---------------------------------------------------------------------------
# Write staging output
# In MVP/dev mode: write to local JSON files for notebook pickup
# In production: upload directly to OneLake landing zone
# [Assumption] OneLake ADLS Gen2 endpoint is accessible from gateway host
# [Unverified] Actual upload mechanism to OneLake requires az CLI or REST
#              and is not implemented here -- placeholder for Phase 5.
# ---------------------------------------------------------------------------
$timestamp = (Get-Date).ToUniversalTime().ToString("yyyyMMdd_HHmmss")
$outputFile = "$OutputPath\gateway_logs_$timestamp.json"

$stagingRecords | ConvertTo-Json -Depth 5 | Out-File $outputFile -Encoding UTF8
Write-Verbose "Staged $($stagingRecords.Count) file records to: $outputFile"

# TODO (Phase 5): Replace local JSON write with OneLake upload
# Pattern from FPM: use Az.Storage or OneLake REST API to PUT to
# https://{workspace}.dfs.fabric.microsoft.com/{lakehouse}/Files/bronze_landing/
# Reference: https://learn.microsoft.com/en-us/fabric/onelake/onelake-access-api

# ---------------------------------------------------------------------------
# Update watermark only after successful staging
# ---------------------------------------------------------------------------
@{ LastProcessedUtc = $maxProcessedUtc.ToString("o") } | ConvertTo-Json | Out-File $watermarkPath -Encoding UTF8
Write-Verbose "Watermark updated to: $maxProcessedUtc"

. (Join-Path $PSScriptRoot 'CollectorHealth.ps1')
Write-CollectorHealth -CollectorName 'Collect-GatewayLogs' -OutputPath $OutputPath `
    -CollectionErrors $collectionErrors -RecordCount $stagingRecords.Count

Write-Output "Collect-GatewayLogs: Staged $($stagingRecords.Count) files. Output: $outputFile"
