# =============================================================================
# GatewayVersion.ps1  |  Label: [NET-NEW]  |  roadmap gap: version expiry (audit #3)
#
# Pure, Spark-free, cmdlet-free version-staleness math, dot-sourced by
# Get-GatewayInventory.ps1 and unit-tested directly.
#
# WHY THIS EXISTS
#   An on-premises data gateway version is supported for ~6 monthly releases.
#   Past its ExpiryDate Microsoft BLOCKS the gateway outright and EVERY refresh
#   through it fails. It is the single most predictable outage in gateway
#   operations -- the date is known months ahead -- and nothing in this repo
#   watched for it. The coverage audit flagged it as absent (gap #3), and it was
#   confirmed the hard way on 2026-07-21 when registering the pilot gateway
#   failed with "Upgrade the gateway version to continue".
#
#   The DataGateway module already exposes everything needed:
#     - Get-DataGatewayAvailableUpdates -> the supported version ladder
#     - MemberGateway.Version / .ExpiryDate / .VersionStatus -> per-node state
#   Neither was ever read. This turns them into an actionable verdict.
#
# The comparison logic lives here, separate from the cmdlet calls, so it can be
# tested against fixed inputs without a gateway, a tenant, or a network.
# =============================================================================

function ConvertTo-GatewayVersion {
    # Best-effort [version] parse. Gateway versions look like 3000.322.5.
    # Returns $null for anything unparseable rather than throwing -- an
    # unparseable version must degrade to "unknown", never crash the collector.
    param([string]$Raw)
    if ([string]::IsNullOrWhiteSpace($Raw)) { return $null }
    $v = $null
    if ([version]::TryParse($Raw.Trim(), [ref]$v)) { return $v }
    return $null
}

function Get-GatewayVersionStaleness {
    <#
      Given an installed version, the available-version ladder, and an optional
      expiry date, return a verdict an alert rule can act on.

      Verdict precedence (expiry dominates -- a blocked gateway is an outage now,
      regardless of how many versions behind it is):
        Expired       ExpiryDate <= Now
        ExpiringSoon  ExpiryDate within WarnDays
        Current       installed >= latest available
        Behind        1-2 versions behind
        Stale         3+ versions behind (approaching the ~6-release support window)
        Unknown       installed version unparseable / no ladder to compare against
    #>
    [CmdletBinding()]
    param(
        [string]$InstalledVersion,
        [string[]]$AvailableVersions = @(),
        [Nullable[datetime]]$ExpiryDate = $null,
        [int]$WarnDays = 30,
        [datetime]$Now = ((Get-Date).ToUniversalTime())
    )

    $installed = ConvertTo-GatewayVersion $InstalledVersion

    # Parse + sort the ladder descending; ignore unparseable entries.
    $parsed = @()
    foreach ($a in $AvailableVersions) {
        $p = ConvertTo-GatewayVersion $a
        if ($p) { $parsed += $p }
    }
    $parsed = @($parsed | Sort-Object -Descending -Unique)
    $latest = if ($parsed.Count -gt 0) { $parsed[0] } else { $null }

    # Versions strictly newer than what is installed.
    $behind = $null
    if ($installed -and $parsed.Count -gt 0) {
        $behind = @($parsed | Where-Object { $_ -gt $installed }).Count
    }

    $daysUntilExpiry = $null
    if ($null -ne $ExpiryDate) {
        $daysUntilExpiry = [int][Math]::Floor(($ExpiryDate - $Now).TotalDays)
    }

    # --- verdict ---
    $verdict = 'Unknown'
    if ($null -ne $ExpiryDate -and $ExpiryDate -le $Now) {
        $verdict = 'Expired'
    }
    elseif ($null -ne $daysUntilExpiry -and $daysUntilExpiry -le $WarnDays) {
        $verdict = 'ExpiringSoon'
    }
    elseif ($null -ne $behind) {
        if ($behind -eq 0) { $verdict = 'Current' }
        elseif ($behind -ge 3) { $verdict = 'Stale' }
        else { $verdict = 'Behind' }
    }
    # else: no expiry AND no parseable comparison -> stays 'Unknown'

    return [ordered]@{
        InstalledVersion = if ($installed) { $installed.ToString() } else { $InstalledVersion }
        LatestVersion    = if ($latest) { $latest.ToString() } else { $null }
        VersionsBehind   = $behind
        DaysUntilExpiry  = $daysUntilExpiry
        IsExpired        = ($verdict -eq 'Expired')
        Verdict          = $verdict
    }
}
