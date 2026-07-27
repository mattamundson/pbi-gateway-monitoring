# =============================================================================
# Get-GatewayInventory.Tests.ps1  |  roadmap T11
#
# Every DataGateway module cmdlet is mocked -- there is no live tenant in CI.
# The mocks exist specifically to regression-guard the three real defects this
# collector shipped with and this session found+fixed:
#   1. Get-DataGatewayClusterMember does not exist; must read cluster.MemberGateways.
#   2. GatewayClusterDatasource has no Status property; must probe
#      Get-DataGatewayClusterDatasourceStatus per datasource and record StatusSource.
#   3. Version-expiry (VersionVerdict/DaysUntilExpiry) was entirely absent.
#
# NOTE: Get-GatewayInventory.ps1 calls `exit 1` on three failure paths, so this
# suite is invoked only via an isolated `pwsh -File` subprocess (see run_pester.ps1
# usage), never directly in an interactive session.
# =============================================================================

BeforeAll {
    $script:Collector = (Resolve-Path (Join-Path $PSScriptRoot '..\..\collectors\Get-GatewayInventory.ps1')).Path
    $script:OutRoot = Join-Path ([System.IO.Path]::GetTempPath()) "gwmon_pester_$([guid]::NewGuid().ToString('N').Substring(0,8))"
    [System.IO.Directory]::CreateDirectory($script:OutRoot) | Out-Null

    function New-FakeCluster {
        param(
            [string]$Id = [guid]::NewGuid().ToString(),
            [string]$Name = 'gw-cluster-01',
            [array]$Members = @(),
            [array]$Datasources = @()
        )
        [pscustomobject]@{
            Id             = $Id
            Name           = $Name
            MemberGateways = $Members
            Datasources    = $Datasources
        }
    }

    function New-FakeMember {
        param(
            [string]$Id = [guid]::NewGuid().ToString(),
            [string]$Name = 'gw-node-01',
            [string]$Status = 'Live',
            [string]$Version = '3000.322.5'
        )
        [pscustomobject]@{
            Id      = $Id
            Name    = $Name
            Status  = $Status
            Version = $Version
        }
    }

    function New-FakeDatasource {
        param([string]$Id = [guid]::NewGuid().ToString(), [string]$Name = 'SqlProd')
        [pscustomobject]@{
            Id                = $Id
            DatasourceName    = $Name
            DatasourceType    = 'SQL'
            ConnectionDetails = [pscustomobject]@{ server = 'sql01'; database = 'proddb' }
        }
    }

    function New-TestConfig {
        $cfgDir = Join-Path $script:OutRoot ([guid]::NewGuid().ToString('N').Substring(0, 8))
        [System.IO.Directory]::CreateDirectory($cfgDir) | Out-Null
        $cfgPath = Join-Path $cfgDir 'config.json'
        @{ tenantId = 'test-tenant'; applicationId = 'test-app' } | ConvertTo-Json | Set-Content $cfgPath -Encoding UTF8
        return $cfgPath
    }

    function Invoke-Collector {
        param([hashtable]$Params = @{})
        $out = Join-Path $script:OutRoot ([guid]::NewGuid().ToString('N').Substring(0, 8))
        [System.IO.Directory]::CreateDirectory($out) | Out-Null
        $splat = @{ OutputPath = $out; ConfigPath = (New-TestConfig) } + $Params
        # The collector sets its own $ErrorActionPreference = 'Stop', which turns
        # Write-Error into a terminating exception that bypasses 2>&1 entirely
        # (stream redirection only covers non-terminating errors). A real
        # `pwsh -File` invocation of this script would exit non-zero on that
        # unhandled exception even though the literal `exit 1` line never runs --
        # so a caught terminating error here is treated as exit code 1, matching
        # real-world behavior instead of asserting on a literal `exit` statement.
        $exit = $null
        $stdout = $null
        try {
            $stdout = & $script:Collector @splat -WarningAction SilentlyContinue 2>&1
            $exit = $LASTEXITCODE
        }
        catch {
            $exit = 1
            $stdout = $_.Exception.Message
        }
        $file = [System.IO.Directory]::GetFiles($out, 'gateway_inventory_*.json') | Select-Object -First 1
        return @{
            Result    = if ($file) { [System.IO.File]::ReadAllText($file) | ConvertFrom-Json } else { $null }
            ExitCode  = $exit
            OutputDir = $out
            StdOut    = $stdout
        }
    }

    function Get-Health {
        param([string]$OutputDir)
        $f = [System.IO.Directory]::GetFiles($OutputDir, 'collector_health_Get-GatewayInventory_*.json') | Select-Object -First 1
        if (-not $f) { return $null }
        return ([System.IO.File]::ReadAllText($f) | ConvertFrom-Json)
    }

    function Set-CommonMocks {
        Mock -CommandName Get-Module -MockWith { [pscustomobject]@{ Name = 'DataGateway' } } -ParameterFilter { $Name -eq 'DataGateway' }
        Mock -CommandName Import-Module -MockWith { } -ParameterFilter { $Name -eq 'DataGateway' }
        Mock -CommandName Connect-DataGatewayServiceAccount -MockWith { }
        Mock -CommandName Disconnect-DataGatewayServiceAccount -MockWith { }
        Mock -CommandName Get-DataGatewayAvailableUpdates -MockWith {
            @([pscustomobject]@{ GatewayInstallerVersion = '3000.322.5' }, [pscustomobject]@{ GatewayInstallerVersion = '3000.302.7' })
        }
    }
}

AfterAll {
    if (Test-Path $script:OutRoot) { Remove-Item $script:OutRoot -Recurse -Force -ErrorAction SilentlyContinue }
}

Describe 'Get-GatewayInventory' {

    Context 'happy path (T11 regression: cluster.MemberGateways, not Get-DataGatewayClusterMember)' {

        It 'emits one inventory record per member gateway via cluster.MemberGateways' {
            Set-CommonMocks
            $cluster = New-FakeCluster -Members @((New-FakeMember -Name 'node-a'), (New-FakeMember -Name 'node-b'))
            Mock -CommandName Get-DataGatewayCluster -MockWith { @($cluster) }
            Mock -CommandName Get-DataGatewayClusterDatasource -MockWith { @() }

            $run = Invoke-Collector -Params @{ CheckDatasourceStatus = $false }
            $run.Result | Should -Not -BeNullOrEmpty -Because "$($run.StdOut)"
            $run.Result.NodeCount | Should -Be 2
            ($run.Result.Inventory | Select-Object -ExpandProperty GatewayNodeName) | Should -Contain 'node-a'
            ($run.Result.Inventory | Select-Object -ExpandProperty GatewayNodeName) | Should -Contain 'node-b'
        }

        It 'computes a VersionVerdict for each node (version-expiry enrichment, previously absent entirely)' {
            Set-CommonMocks
            $cluster = New-FakeCluster -Members @((New-FakeMember -Version '3000.302.7'))
            Mock -CommandName Get-DataGatewayCluster -MockWith { @($cluster) }
            Mock -CommandName Get-DataGatewayClusterDatasource -MockWith { @() }

            $run = Invoke-Collector -Params @{ CheckDatasourceStatus = $false }
            $run.Result.Inventory[0].PSObject.Properties.Name | Should -Contain 'VersionVerdict'
            $run.Result.Inventory[0].VersionVerdict | Should -Not -BeNullOrEmpty
            $run.Result.LatestAvailableVersion | Should -Be '3000.322.5'
        }
    }

    Context 'datasource status (T11 regression: GatewayClusterDatasource has no Status property)' {

        It 'records StatusSource=probe and the real status when the probe succeeds' {
            Set-CommonMocks
            $cluster = New-FakeCluster -Members @((New-FakeMember))
            Mock -CommandName Get-DataGatewayCluster -MockWith { @($cluster) }
            Mock -CommandName Get-DataGatewayClusterDatasource -MockWith { @(New-FakeDatasource -Name 'SqlProd') }
            Mock -CommandName Get-DataGatewayClusterDatasourceStatus -MockWith { [pscustomobject]@{ Status = 'Live' } }

            $run = Invoke-Collector -Params @{ CheckDatasourceStatus = $true }
            $ds = $run.Result.Datasources[0]
            $ds.Status | Should -Be 'Live'
            $ds.StatusSource | Should -Be 'probe'
        }

        It 'records StatusSource=probe-failed rather than silently defaulting to Unknown when the probe throws' {
            Set-CommonMocks
            $cluster = New-FakeCluster -Members @((New-FakeMember))
            Mock -CommandName Get-DataGatewayCluster -MockWith { @($cluster) }
            Mock -CommandName Get-DataGatewayClusterDatasource -MockWith { @(New-FakeDatasource -Name 'OracleFin') }
            Mock -CommandName Get-DataGatewayClusterDatasourceStatus -MockWith { throw 'credential expired' }

            $run = Invoke-Collector -Params @{ CheckDatasourceStatus = $true }
            $ds = $run.Result.Datasources[0]
            $ds.Status | Should -Be 'ProbeFailed'
            $ds.StatusSource | Should -Be 'probe-failed' -Because 'a broken probe must never be indistinguishable from a broken credential (both used to read Unknown)'
            $run.Result.CollectionErrors.Count | Should -BeGreaterThan 0
        }
    }

    Context 'failure handling' {

        It 'records a CollectionError (not a silent pass) when a cluster returns zero member gateways' {
            Set-CommonMocks
            $cluster = New-FakeCluster -Members @()
            Mock -CommandName Get-DataGatewayCluster -MockWith { @($cluster) }
            Mock -CommandName Get-DataGatewayClusterDatasource -MockWith { @() }

            $run = Invoke-Collector -Params @{ CheckDatasourceStatus = $false }
            $run.Result.NodeCount | Should -Be 0
            $run.Result.CollectionErrors.Count | Should -BeGreaterThan 0 -Because 'silence here is exactly what hid the missing-cmdlet bug for this collector''s entire life'
        }

        It 'exits 1 when the DataGateway module is not installed' {
            Mock -CommandName Get-Module -MockWith { $null } -ParameterFilter { $Name -eq 'DataGateway' }

            $run = Invoke-Collector
            $run.ExitCode | Should -Be 1
        }

        It 'exits 1 when service-principal authentication fails' {
            Set-CommonMocks
            Mock -CommandName Connect-DataGatewayServiceAccount -MockWith { throw 'AADSTS7000215: invalid client secret' }

            $run = Invoke-Collector
            $run.ExitCode | Should -Be 1
        }

        It 'exits 1 when Get-DataGatewayCluster itself fails' {
            Set-CommonMocks
            Mock -CommandName Get-DataGatewayCluster -MockWith { throw 'service unavailable' }

            $run = Invoke-Collector
            $run.ExitCode | Should -Be 1
        }
    }

    Context 'output contract' {

        It 'writes the health sidecar with the node count on a clean run' {
            Set-CommonMocks
            $cluster = New-FakeCluster -Members @((New-FakeMember))
            Mock -CommandName Get-DataGatewayCluster -MockWith { @($cluster) }
            Mock -CommandName Get-DataGatewayClusterDatasource -MockWith { @() }

            $run = Invoke-Collector -Params @{ CheckDatasourceStatus = $false }
            $health = Get-Health -OutputDir $run.OutputDir
            $health | Should -Not -BeNullOrEmpty
            $health.Status | Should -Be 'OK'
            $health.RecordCount | Should -Be 1
        }
    }
}
