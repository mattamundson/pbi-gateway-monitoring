# =============================================================================
# Collect-RefreshHistory.ps1  |  Label: [NET-NEW]
#
# Pain point addressed: #2 — Opaque refresh failures (DIFFERENTIATOR #2 triage)
#
# THE MISSING LEG. The silver triage join (02_silver_correlate.py) correlates
# three sources to answer "gateway vs. source vs. network?":
#     gateway QueryExecution log   (gateway-side error + timing)
#   + Windows Event Log            (service restarts)
#   + Power BI Service refresh history  <-- THIS collector
# Without the Service-side refresh history, the triage only ever sees the
# gateway + OS legs and can't reconcile them against the error the operator
# actually sees in Power BI ("Gateway Unreachable", "timeout", etc.). This
# collector produces `bronze_refresh_history` so the 3-way join is whole.
#
# Join key: the Power BI refresh record's `requestId` is intended to match the
# gateway log `RequestId` (same linkage the identity join relies on).
#   [Desk-Verified 2026-07-01] The ENGINE-level chain is confirmed by primary
#     source: the AS engine `XmlaRequestId` equals the gateway log `RequestId`
#     column (MS Learn "service-gateway-onprem-tshoot"; Chris Webb, crossjoin.co.uk).
#   [Unverified] The REST refresh-history object's `requestId` (this collector's
#     field) is documented only as "the identifier of the refresh request" — it is
#     NOT documented to byte-match `XmlaRequestId`/gateway `RequestId`. Confirm the
#     match rate in Phase 5 exactly as for the identity join (this is the remaining
#     open question; it is the REST-field link, not the engine chain, that's unproven).
#   Refs: learn.microsoft.com/en-us/power-bi/connect-data/service-gateway-onprem-tshoot
#         blog.crossjoin.co.uk (XmlaRequestId ↔ gateway RequestId)
#
# Auth: service principal, SAME model as Get-GatewayInventory.ps1 (tenantId +
# applicationId from config.json, client secret from a Key Vault-fetched file).
# Uses the MicrosoftPowerBIMgmt module (Connect-PowerBIServiceAccount).
#
# [Unverified] Exact REST route for org-wide per-dataset refresh history and the
#              availability of workspaceId under -Scope Organization require
#              Phase 5 validation against a live tenant. The route used here is
#              the documented per-dataset refreshes endpoint invoked with admin
#              scope; some tenants may require the /admin/ variant.
#   Refs: https://learn.microsoft.com/en-us/rest/api/power-bi/datasets/get-refresh-history
#         https://learn.microsoft.com/en-us/rest/api/power-bi/admin/datasets-get-datasets-as-admin
#
# Recommended scheduling: Windows Scheduled Task every 15-30 minutes (align with
# the gateway-log cadence so RequestIds land in the same window).
# =============================================================================

[CmdletBinding()]
# Justification: Connect-PowerBIServiceAccount -Credential requires a PSCredential
# built from a [SecureString]; the SP secret arrives as plaintext from a Key
# Vault-fetched file at runtime. Same accepted pattern as Get-GatewayInventory.
[Diagnostics.CodeAnalysis.SuppressMessageAttribute('PSAvoidUsingConvertToSecureStringWithPlainText', '')]
param(
    [Parameter(Mandatory = $false)]
    [string]$ConfigPath = "$PSScriptRoot\..\config\config.json",

    [Parameter(Mandatory = $false)]
    [string]$OutputPath = "$PSScriptRoot\..\output",

    # Only collect refreshes whose EndTime is within this many hours.
    [Parameter(Mandatory = $false)]
    [int]$LookbackHours = 24,

    # Cap refresh entries pulled per dataset (most recent first).
    [Parameter(Mandatory = $false)]
    [int]$TopPerDataset = 20,

    # SP credentials (populated at runtime; fall back to config.json).
    [Parameter(Mandatory = $false)]
    [string]$TenantId = "",
    [Parameter(Mandatory = $false)]
    [string]$ApplicationId = "",
    [Parameter(Mandatory = $false)]
    [string]$ClientSecretPath = ""   # path to Key Vault fetched secret
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

if (-not (Test-Path $OutputPath)) { New-Item -ItemType Directory -Path $OutputPath | Out-Null }

$collectedAtUtc = (Get-Date).ToUniversalTime()
$cutoffUtc = $collectedAtUtc.AddHours(-1 * $LookbackHours)

# ---------------------------------------------------------------------------
# Resolve SP config (StrictMode-safe reads; tenantId/applicationId may live at
# the config root, matching Get-GatewayInventory.ps1).
# ---------------------------------------------------------------------------
if (Test-Path $ConfigPath) {
    $config = Get-Content $ConfigPath -Raw | ConvertFrom-Json
    if ([string]::IsNullOrWhiteSpace($TenantId) -and $config.PSObject.Properties['tenantId']) {
        $TenantId = $config.tenantId
    }
    if ([string]::IsNullOrWhiteSpace($ApplicationId) -and $config.PSObject.Properties['applicationId']) {
        $ApplicationId = $config.applicationId
    }
}

# ---------------------------------------------------------------------------
# Module check
# ---------------------------------------------------------------------------
if (-not (Get-Module -ListAvailable -Name 'MicrosoftPowerBIMgmt' -ErrorAction SilentlyContinue)) {
    Write-Error "MicrosoftPowerBIMgmt module is not installed. Run: Install-Module MicrosoftPowerBIMgmt -Scope AllUsers"
    exit 1
}
Import-Module MicrosoftPowerBIMgmt -ErrorAction Stop

# ---------------------------------------------------------------------------
# Authenticate (service principal)
# ---------------------------------------------------------------------------
try {
    if (-not [string]::IsNullOrWhiteSpace($ClientSecretPath) -and (Test-Path $ClientSecretPath)) {
        $clientSecretRaw = Get-Content $ClientSecretPath -Raw
        $clientSecretSS  = $clientSecretRaw.Trim() | ConvertTo-SecureString -AsPlainText -Force
        $spCredential    = New-Object System.Management.Automation.PSCredential($ApplicationId, $clientSecretSS)
        Connect-PowerBIServiceAccount -ServicePrincipal -Credential $spCredential -Tenant $TenantId -ErrorAction Stop | Out-Null
        Write-Verbose "Authenticated via service principal"
    }
    else {
        # No secret file: fall back to interactive auth (not for scheduled tasks).
        Write-Warning "No client secret file found. Attempting interactive auth (not suitable for scheduled tasks)."
        Connect-PowerBIServiceAccount -ErrorAction Stop | Out-Null
    }
}
catch {
    Write-Warning "Power BI service authentication failed: $_"
    Write-Warning "Verify the SP has tenant admin read (Tenant setting: 'Service principals can access read-only admin APIs')."
    exit 1
}

# ---------------------------------------------------------------------------
# Enumerate datasets org-wide, then pull each one's recent refresh history.
# [Unverified] -Scope Organization dataset shape (esp. workspaceId presence) and
#              per-dataset refresh route require Phase 5 confirmation.
# ---------------------------------------------------------------------------
$refreshRecords   = @()
$collectionErrors = @()

try {
    # Array-wrap so .Count is valid for 0/1/many (StrictMode).
    $datasets = @(Get-PowerBIDataset -Scope Organization -ErrorAction Stop)
    Write-Verbose "Enumerating refresh history for $($datasets.Count) dataset(s)"

    foreach ($ds in $datasets) {
        $datasetId   = if ($ds.PSObject.Properties['Id'])          { $ds.Id.ToString() } else { $null }
        $datasetName = if ($ds.PSObject.Properties['Name'])        { $ds.Name }          else { $null }
        $workspaceId = if ($ds.PSObject.Properties['WorkspaceId']) { $ds.WorkspaceId }   else { $null }
        if ([string]::IsNullOrWhiteSpace($datasetId)) { continue }

        # Skip models that can't refresh through a gateway anyway.
        if ($ds.PSObject.Properties['IsRefreshable'] -and $ds.IsRefreshable -eq $false) { continue }

        try {
            $url  = "datasets/$datasetId/refreshes?`$top=$TopPerDataset"
            $resp = Invoke-PowerBIRestMethod -Url $url -Method Get -ErrorAction Stop
            $parsed = $resp | ConvertFrom-Json
            # The refreshes endpoint returns { "value": [ ... ] }.
            $entries = @()
            if ($parsed.PSObject.Properties['value']) { $entries = @($parsed.value) }

            foreach ($r in $entries) {
                $endTimeRaw = if ($r.PSObject.Properties['endTime']) { $r.endTime } else { $null }
                # Lookback filter: keep entries with no end time (in-progress) or within window.
                if (-not [string]::IsNullOrWhiteSpace($endTimeRaw)) {
                    $endParsed = $null
                    if ([datetime]::TryParse($endTimeRaw, [ref]$endParsed)) {
                        if ($endParsed.ToUniversalTime() -lt $cutoffUtc) { continue }
                    }
                }

                $refreshRecords += @{
                    CollectedAtUtc       = $collectedAtUtc.ToString("o")
                    # RequestId is the join key to the gateway log's RequestId.
                    RequestId            = if ($r.PSObject.Properties['requestId'])          { $r.requestId }          else { $null }
                    DatasetId            = $datasetId
                    DatasetName          = $datasetName
                    WorkspaceId          = $workspaceId
                    RefreshType          = if ($r.PSObject.Properties['refreshType'])        { $r.refreshType }        else { $null }
                    Status               = if ($r.PSObject.Properties['status'])             { $r.status }             else { $null }
                    StartTime            = if ($r.PSObject.Properties['startTime'])          { $r.startTime }          else { $null }
                    EndTime              = $endTimeRaw
                    ServiceExceptionJson = if ($r.PSObject.Properties['serviceExceptionJson']) { $r.serviceExceptionJson } else { $null }
                }
            }
        }
        catch {
            # One dataset's failure must not abort the whole collection.
            $errMsg = "Refresh-history fetch failed for dataset $datasetName ($datasetId): $_"
            Write-Warning $errMsg
            $collectionErrors += $errMsg
        }
    }
}
catch {
    $errMsg = "Get-PowerBIDataset (Scope Organization) failed: $_"
    Write-Error $errMsg
    exit 1
}

# ---------------------------------------------------------------------------
# Write JSON output for bronze ingest (01_bronze_ingest.py -> ingest_refresh_history)
# ---------------------------------------------------------------------------
$output = @{
    CollectedAtUtc   = $collectedAtUtc.ToString("o")
    LookbackHours    = $LookbackHours
    RefreshCount     = $refreshRecords.Count
    CollectionErrors = $collectionErrors
    RefreshRecords   = $refreshRecords
}

$timestamp  = $collectedAtUtc.ToString("yyyyMMdd_HHmmss")
$outputFile = "$OutputPath\refresh_history_$timestamp.json"
$output | ConvertTo-Json -Depth 10 | Out-File $outputFile -Encoding UTF8
Write-Output "Collect-RefreshHistory: $($refreshRecords.Count) refresh records, $($collectionErrors.Count) errors. Output: $outputFile"

# Clean up the session
try { Disconnect-PowerBIServiceAccount -ErrorAction SilentlyContinue } catch {}
