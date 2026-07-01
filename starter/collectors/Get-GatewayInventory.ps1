# =============================================================================
# Get-GatewayInventory.ps1
# Label: [ADAPTED-FROM-SQLvariant]
#
# Pain points addressed:
#   #1  — Gateway online/offline status (heartbeat)
#   #6  — Fleet / multi-gateway view
#   #10 — Credential / datasource state drift (datasource status check)
#
# Signals collected: S2 (config/datasource list), S5 (PS module inventory), S6 (REST APIs)
#
# Adapted from:
#   SQLvariant/GatewayClusters.ps1 gist (Get-DataGatewayCluster -Scope Organization):
#     https://gist.github.com/SQLvariant/fd3b77e597fc6e13118636bf0d682383
#   SQLvariant SQLServerCentral blog on DataGateway PS module:
#     https://www.sqlservercentral.com/blogs/get-the-governance-data-you-need-out-of-your-power-bi-gateways-with-powershell
#
# DataGateway PowerShell module (23 cmdlets):
#   Install-Module DataGateway
#   Documentation: https://learn.microsoft.com/en-us/powershell/module/datagateway/
#
# Known issue:
#   Get-DataGatewayInfo returns 401 Unauthorized with service principals
#   in some tenants, even with Gateway Admin role assigned.
#   Reference: https://community.fabric.microsoft.com/t5/Real-Time-Intelligence/
#     Fabric-Platform-Monitoring-accelerator-Gateways-module/m-p/4884544
#   Workaround attempted here: ensure tenant setting
#   "EnablePowerBIManagementApiForGatewayAdmins" is enabled.
#   [Assumption] This resolves the 401; unconfirmed in live environment.
#   If the 401 persists, fall back to -AsUser parameter (interactive login).
#
# [Unverified] All cmdlet calls require live environment validation (Phase 5).
#              DataGateway module version confirmed as of June 2026; cmdlet
#              parameters may change in future releases.
#
# Recommended scheduling: Windows Scheduled Task every 15 minutes.
# =============================================================================

[CmdletBinding()]
param(
    [Parameter(Mandatory = $false)]
    [string]$ConfigPath = "$PSScriptRoot\..\config\config.json",

    [Parameter(Mandatory = $false)]
    [string]$OutputPath = "$PSScriptRoot\..\output",

    # If true, check datasource connection statuses (may be slow for large tenants)
    [Parameter(Mandatory = $false)]
    [bool]$CheckDatasourceStatus = $true,

    # SP credentials from Key Vault (populated at runtime)
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

# ---------------------------------------------------------------------------
# Load configuration and resolve SP credentials from Key Vault
# [Assumption] SP has DataGateway.Read.All and PowerBI tenant admin roles
# ---------------------------------------------------------------------------
if (Test-Path $ConfigPath) {
    $config = Get-Content $ConfigPath -Raw | ConvertFrom-Json
    if ([string]::IsNullOrWhiteSpace($TenantId))       { $TenantId       = $config.tenantId }
    if ([string]::IsNullOrWhiteSpace($ApplicationId))  { $ApplicationId  = $config.applicationId }
}

# ---------------------------------------------------------------------------
# Module check
# [ADAPTED-FROM-SQLvariant] SQLvariant uses Install-Module DataGateway
# Reference: https://gist.github.com/SQLvariant/fd3b77e597fc6e13118636bf0d682383
# ---------------------------------------------------------------------------
if (-not (Get-Module -ListAvailable -Name 'DataGateway' -ErrorAction SilentlyContinue)) {
    Write-Error "DataGateway PowerShell module is not installed. Run: Install-Module DataGateway -Scope AllUsers"
    exit 1
}
Import-Module DataGateway -ErrorAction Stop

# ---------------------------------------------------------------------------
# Authenticate
# [ADAPTED-FROM-FPM] SP authentication pattern from FPM gateway config
# Reference: https://github.com/microsoft/fabric-toolbox/tree/main/monitoring/fabric-platform-monitoring
#
# Known 401 bug: Get-DataGatewayInfo may return 401 with SP even when
# the SP has Gateway Admin role. See header comment.
# [Unverified] The workaround below (Connect-DataGatewayServiceAccount) may
# not resolve the 401 on all tenants.
# ---------------------------------------------------------------------------
try {
    if (-not [string]::IsNullOrWhiteSpace($ClientSecretPath) -and (Test-Path $ClientSecretPath)) {
        $clientSecretRaw = Get-Content $ClientSecretPath -Raw
        $clientSecretSS  = $clientSecretRaw.Trim() | ConvertTo-SecureString -AsPlainText -Force
    }
    else {
        Write-Warning "No client secret file found. Attempting interactive auth (not suitable for scheduled tasks)."
        # [Assumption] Falls back to device code flow — not suitable for automation
        Connect-DataGatewayServiceAccount -ErrorAction Stop
        goto skipSpConnect
    }

    $credential = New-Object System.Management.Automation.PSCredential($ApplicationId, $clientSecretSS)
    Connect-DataGatewayServiceAccount -TenantId $TenantId -ApplicationId $ApplicationId `
        -ClientSecret $clientSecretSS -ErrorAction Stop
    Write-Verbose "Authenticated via service principal"
}
catch {
    Write-Warning "DataGateway SP authentication failed: $_"
    Write-Warning "If this is a 401 error, verify that the SP has Gateway Admin role AND"
    Write-Warning "  the tenant setting 'EnablePowerBIManagementApiForGatewayAdmins' is enabled."
    Write-Warning "Reference: https://community.fabric.microsoft.com/t5/Real-Time-Intelligence/Fabric-Platform-Monitoring-accelerator-Gateways-module/m-p/4884544"
    exit 1
}

# ---------------------------------------------------------------------------
# Enumerate all gateway clusters (tenant-wide)
# [ADAPTED-FROM-SQLvariant] Core pattern from GatewayClusters.ps1 gist
# Reference: https://gist.github.com/SQLvariant/fd3b77e597fc6e13118636bf0d682383
#
# Get-DataGatewayCluster -Scope Organization returns all clusters the SP
# has visibility into via Gateway Admin permissions.
# ---------------------------------------------------------------------------
$inventoryRecords = @()
$datasourceRecords = @()
$collectionErrors = @()

try {
    Write-Verbose "Enumerating all gateway clusters (Scope: Organization)..."
    $clusters = Get-DataGatewayCluster -Scope Organization -ErrorAction Stop

    Write-Verbose "Found $($clusters.Count) gateway cluster(s)"

    foreach ($cluster in $clusters) {
        # ---------------------------------------------------------------------------
        # Enumerate cluster members (nodes)
        # [ADAPTED-FROM-SQLvariant] Extending the gist pattern to collect node details
        # ---------------------------------------------------------------------------
        try {
            $members = Get-DataGatewayClusterMember -GatewayClusterId $cluster.Id -ErrorAction Stop

            foreach ($member in $members) {
                $inventoryRecords += @{
                    CollectedAtUtc    = $collectedAtUtc.ToString("o")
                    GatewayClusterId  = $cluster.Id.ToString()
                    GatewayClusterName = $cluster.Name
                    ClusterScope      = "Organization"
                    GatewayObjectId   = $member.Id.ToString()
                    GatewayNodeName   = $member.Name
                    Status            = $member.Status         # e.g., Live, Offline
                    Version           = $member.Version
                    # [Assumption] DatasourceCount may not be a direct property;
                    # may require separate call. Set null if not available.
                    DatasourceCount   = if ($member.PSObject.Properties['DatasourceCount']) { $member.DatasourceCount } else { $null }
                    GatewayMachineOs  = if ($member.PSObject.Properties['MachineOs'])      { $member.MachineOs }      else { $null }
                    GatewayAnnotation = if ($member.PSObject.Properties['Annotation'])     { $member.Annotation }     else { $null }
                }
            }
        }
        catch {
            $errMsg = "Failed to enumerate members of cluster $($cluster.Name): $_"
            Write-Warning $errMsg
            $collectionErrors += $errMsg
        }

        # ---------------------------------------------------------------------------
        # Datasource status check (for credential drift detection — Pain #10)
        # REST: GET /gateways/{clusterId}/datasources/{datasourceId}/status
        # [Unverified] Get-DataGatewayClusterDatasource cmdlet existence and
        #              exact parameter names require Phase 5 validation.
        # ---------------------------------------------------------------------------
        if ($CheckDatasourceStatus) {
            try {
                $datasources = Get-DataGatewayClusterDatasource -GatewayClusterId $cluster.Id -ErrorAction Stop

                foreach ($ds in $datasources) {
                    $datasourceRecords += @{
                        CollectedAtUtc    = $collectedAtUtc.ToString("o")
                        GatewayClusterId  = $cluster.Id.ToString()
                        GatewayClusterName = $cluster.Name
                        DatasourceId      = $ds.Id.ToString()
                        DatasourceName    = $ds.DatasourceName
                        DatasourceType    = $ds.DatasourceType
                        ConnectionDetails = $ds.ConnectionDetails | ConvertTo-Json -Compress
                        # Status field: Live, Unknown, Error — drives credential-drift alerting
                        Status            = if ($ds.PSObject.Properties['Status']) { $ds.Status } else { "Unknown" }
                        LastUpdated       = if ($ds.PSObject.Properties['LastUpdated']) { $ds.LastUpdated } else { $null }
                    }
                }
            }
            catch {
                # Datasource status check is optional — do not fail entire collection
                $errMsg = "Datasource status check failed for cluster $($cluster.Name): $_"
                Write-Warning $errMsg
                $collectionErrors += $errMsg
            }
        }
    }
}
catch {
    $errMsg = "Get-DataGatewayCluster failed: $_"
    Write-Error $errMsg
    exit 1
}

# ---------------------------------------------------------------------------
# Write output
# ---------------------------------------------------------------------------
$output = @{
    CollectedAtUtc     = $collectedAtUtc.ToString("o")
    GatewayHostName    = $env:COMPUTERNAME
    ClusterCount       = ($inventoryRecords | Select-Object -ExpandProperty GatewayClusterId -Unique | Measure-Object).Count
    NodeCount          = $inventoryRecords.Count
    DatasourceCount    = $datasourceRecords.Count
    CollectionErrors   = $collectionErrors
    Inventory          = $inventoryRecords
    Datasources        = $datasourceRecords
}

$timestamp = $collectedAtUtc.ToString("yyyyMMdd_HHmmss")
$outputFile = "$OutputPath\gateway_inventory_$timestamp.json"

$output | ConvertTo-Json -Depth 10 | Out-File $outputFile -Encoding UTF8
Write-Output "Get-GatewayInventory: $($inventoryRecords.Count) nodes, $($datasourceRecords.Count) datasources. Output: $outputFile"

# Disconnect to clean up session
try { Disconnect-DataGatewayServiceAccount -ErrorAction SilentlyContinue } catch {}
