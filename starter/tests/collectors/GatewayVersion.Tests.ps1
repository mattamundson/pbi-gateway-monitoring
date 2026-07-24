# =============================================================================
# GatewayVersion.Tests.ps1  |  roadmap: version-expiry monitoring (audit gap #3)
#
# Tests the pure staleness verdict. No gateway, tenant, or network -- the whole
# point of separating the math from Get-DataGatewayAvailableUpdates is that the
# outage-predicting logic is verifiable against fixed inputs.
#
# The version ladder used here is the REAL one returned by
# Get-DataGatewayAvailableUpdates on 2026-07-21 (Jan-Jun 2026), so a change to
# Microsoft's version-number shape that broke parsing would fail these tests.
# =============================================================================

BeforeAll {
    . (Resolve-Path (Join-Path $PSScriptRoot '..\..\collectors\GatewayVersion.ps1')).Path

    # Real ladder, newest-first, as observed live.
    $script:Ladder = @(
        '3000.322.5',   # June 2026 (latest)
        '3000.318.11',  # May 2026
        '3000.314.6',   # April 2026
        '3000.310.3',   # March 2026
        '3000.306.5',   # February 2026
        '3000.302.7'    # January 2026
    )
    # A fixed "now" so expiry math is deterministic (no Get-Date in assertions).
    $script:Now = [datetime]::new(2026, 7, 21, 0, 0, 0, [DateTimeKind]::Utc)
}

Describe 'Get-GatewayVersionStaleness' {

    Context 'version-ladder comparison (no expiry date)' {

        It 'reports Current when installed is the latest' {
            $r = Get-GatewayVersionStaleness -InstalledVersion '3000.322.5' -AvailableVersions $script:Ladder -Now $script:Now
            $r.Verdict | Should -Be 'Current'
            $r.VersionsBehind | Should -Be 0
            $r.LatestVersion | Should -Be '3000.322.5'
        }

        It 'reports Current when installed is newer than the whole ladder' {
            $r = Get-GatewayVersionStaleness -InstalledVersion '3000.400.0' -AvailableVersions $script:Ladder -Now $script:Now
            $r.Verdict | Should -Be 'Current'
            $r.VersionsBehind | Should -Be 0
        }

        It 'reports Behind when 1-2 versions back' {
            $r = Get-GatewayVersionStaleness -InstalledVersion '3000.318.11' -AvailableVersions $script:Ladder -Now $script:Now
            $r.Verdict | Should -Be 'Behind'
            $r.VersionsBehind | Should -Be 1
        }

        It 'reports Stale when 3+ versions back' {
            # This is the version the DataGateway MODULE bundles (3000.318.6, May)
            # against the June latest -- one behind by version number but the
            # install of it was still rejected as too old to register, which is
            # why the ladder check matters. Use an explicitly-old one for the
            # 3-behind boundary:
            $r = Get-GatewayVersionStaleness -InstalledVersion '3000.310.3' -AvailableVersions $script:Ladder -Now $script:Now
            $r.Verdict | Should -Be 'Stale'
            $r.VersionsBehind | Should -Be 3
        }

        It 'reports Unknown for an unparseable installed version with no expiry' {
            $r = Get-GatewayVersionStaleness -InstalledVersion 'not-a-version' -AvailableVersions $script:Ladder -Now $script:Now
            $r.Verdict | Should -Be 'Unknown'
            $r.VersionsBehind | Should -BeNullOrEmpty
        }

        It 'reports Unknown when the ladder is empty and no expiry is known' {
            $r = Get-GatewayVersionStaleness -InstalledVersion '3000.318.11' -AvailableVersions @() -Now $script:Now
            $r.Verdict | Should -Be 'Unknown'
            $r.LatestVersion | Should -BeNullOrEmpty
        }
    }

    Context 'expiry date dominates the verdict' {

        It 'reports Expired when the expiry date has passed, even if version is Current' {
            $r = Get-GatewayVersionStaleness -InstalledVersion '3000.322.5' -AvailableVersions $script:Ladder `
                -ExpiryDate ([datetime]::new(2026, 7, 1, 0, 0, 0, [DateTimeKind]::Utc)) -Now $script:Now
            $r.Verdict | Should -Be 'Expired'
            $r.IsExpired | Should -BeTrue
            $r.DaysUntilExpiry | Should -BeLessThan 0
        }

        It 'reports ExpiringSoon inside the warn window' {
            $r = Get-GatewayVersionStaleness -InstalledVersion '3000.322.5' -AvailableVersions $script:Ladder `
                -ExpiryDate ([datetime]::new(2026, 8, 10, 0, 0, 0, [DateTimeKind]::Utc)) -Now $script:Now
            $r.Verdict | Should -Be 'ExpiringSoon'
            $r.IsExpired | Should -BeFalse
            $r.DaysUntilExpiry | Should -BeGreaterThan 0
        }

        It 'does NOT flag ExpiringSoon well outside the warn window' {
            $r = Get-GatewayVersionStaleness -InstalledVersion '3000.322.5' -AvailableVersions $script:Ladder `
                -ExpiryDate ([datetime]::new(2026, 12, 1, 0, 0, 0, [DateTimeKind]::Utc)) -Now $script:Now
            $r.Verdict | Should -Be 'Current'
        }

        It 'reports Expired for an old version whose date has passed (the real outage shape)' {
            # January 2026 build, expired -- this is exactly what blocks refreshes.
            $r = Get-GatewayVersionStaleness -InstalledVersion '3000.302.7' -AvailableVersions $script:Ladder `
                -ExpiryDate ([datetime]::new(2026, 7, 1, 0, 0, 0, [DateTimeKind]::Utc)) -Now $script:Now
            $r.Verdict | Should -Be 'Expired'
        }
    }

    Context 'ConvertTo-GatewayVersion' {
        It 'parses a real gateway version' {
            (ConvertTo-GatewayVersion '3000.322.5').ToString() | Should -Be '3000.322.5'
        }
        It 'returns null for junk rather than throwing' {
            ConvertTo-GatewayVersion 'June 2026' | Should -BeNullOrEmpty
            ConvertTo-GatewayVersion '' | Should -BeNullOrEmpty
        }
    }
}
