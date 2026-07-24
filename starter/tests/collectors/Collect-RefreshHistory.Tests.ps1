# =============================================================================
# Collect-RefreshHistory.Tests.ps1  |  roadmap T11
#
# MicrosoftPowerBIMgmt cmdlets are mocked -- there is no live tenant in CI.
# Invoke-PowerBIRestMethod returns a raw JSON string (the collector itself does
# `$resp | ConvertFrom-Json`), matching the real cmdlet's return shape.
#
# NOTE: this collector calls `exit 1` on three failure paths, so it is invoked
# only via an isolated `pwsh -File` subprocess, same as Get-GatewayInventory.
# =============================================================================

BeforeAll {
    $script:Collector = (Resolve-Path (Join-Path $PSScriptRoot '..\..\collectors\Collect-RefreshHistory.ps1')).Path
    $script:OutRoot = Join-Path ([System.IO.Path]::GetTempPath()) "gwmon_pester_$([guid]::NewGuid().ToString('N').Substring(0,8))"
    [System.IO.Directory]::CreateDirectory($script:OutRoot) | Out-Null

    function New-FakeDataset {
        param([string]$Id = [guid]::NewGuid().ToString(), [string]$Name = 'ds-1', [bool]$IsRefreshable = $true, [string]$WorkspaceId = 'ws-1')
        [pscustomobject]@{ Id = $Id; Name = $Name; IsRefreshable = $IsRefreshable; WorkspaceId = $WorkspaceId }
    }

    function New-RefreshesJson {
        param([array]$Entries)
        return (@{ value = $Entries } | ConvertTo-Json -Depth 6 -Compress)
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
        $exit = $null
        $stdout = $null
        try {
            # Same rationale as Get-GatewayInventory.Tests.ps1: the collector's own
            # $ErrorActionPreference='Stop' turns Write-Error into a terminating
            # exception that 2>&1 does not capture; a caught exception here means
            # the real pwsh -File invocation would have exited non-zero too.
            $stdout = & $script:Collector @splat -WarningAction SilentlyContinue 2>&1
            $exit = $LASTEXITCODE
        }
        catch {
            $exit = 1
            $stdout = $_.Exception.Message
        }
        $file = [System.IO.Directory]::GetFiles($out, 'refresh_history_*.json') | Select-Object -First 1
        return @{
            Result    = if ($file) { [System.IO.File]::ReadAllText($file) | ConvertFrom-Json } else { $null }
            ExitCode  = $exit
            OutputDir = $out
            StdOut    = $stdout
        }
    }

    function Get-Health {
        param([string]$OutputDir)
        $f = [System.IO.Directory]::GetFiles($OutputDir, 'collector_health_Collect-RefreshHistory_*.json') | Select-Object -First 1
        if (-not $f) { return $null }
        return ([System.IO.File]::ReadAllText($f) | ConvertFrom-Json)
    }

    function Set-CommonMocks {
        Mock -CommandName Get-Module -MockWith { [pscustomobject]@{ Name = 'MicrosoftPowerBIMgmt' } } -ParameterFilter { $Name -eq 'MicrosoftPowerBIMgmt' }
        Mock -CommandName Import-Module -MockWith { } -ParameterFilter { $Name -eq 'MicrosoftPowerBIMgmt' }
        Mock -CommandName Connect-PowerBIServiceAccount -MockWith { }
        Mock -CommandName Disconnect-PowerBIServiceAccount -MockWith { }
    }
}

AfterAll {
    if (Test-Path $script:OutRoot) { Remove-Item $script:OutRoot -Recurse -Force -ErrorAction SilentlyContinue }
}

Describe 'Collect-RefreshHistory' {

    Context 'happy path' {

        It 'maps requestId as the RequestId join key and carries dataset identity' {
            Set-CommonMocks
            Mock -CommandName Get-PowerBIDataset -MockWith { @(New-FakeDataset -Id 'ds-1' -Name 'SalesModel') }
            Mock -CommandName Invoke-PowerBIRestMethod -MockWith {
                New-RefreshesJson -Entries @(
                    @{ requestId = 'req-abc'; refreshType = 'Scheduled'; status = 'Completed'; startTime = (Get-Date).ToUniversalTime().ToString('o'); endTime = (Get-Date).ToUniversalTime().ToString('o') }
                )
            }

            $run = Invoke-Collector
            $run.Result | Should -Not -BeNullOrEmpty -Because "$($run.StdOut)"
            $run.Result.RefreshCount | Should -Be 1
            $run.Result.RefreshRecords[0].RequestId | Should -Be 'req-abc'
            $run.Result.RefreshRecords[0].DatasetName | Should -Be 'SalesModel'
        }

        It 'skips datasets flagged IsRefreshable=false' {
            Set-CommonMocks
            Mock -CommandName Get-PowerBIDataset -MockWith { @(New-FakeDataset -Id 'ds-2' -IsRefreshable $false) }
            Mock -CommandName Invoke-PowerBIRestMethod -MockWith { throw 'should never be called for a non-refreshable dataset' }

            $run = Invoke-Collector
            $run.Result.RefreshCount | Should -Be 0
            $run.Result.CollectionErrors.Count | Should -Be 0
        }
    }

    Context 'lookback filter' {

        It 'excludes a refresh whose endTime is older than the lookback window' {
            Set-CommonMocks
            Mock -CommandName Get-PowerBIDataset -MockWith { @(New-FakeDataset -Id 'ds-1') }
            $oldEnd = (Get-Date).ToUniversalTime().AddHours(-48).ToString('o')
            Mock -CommandName Invoke-PowerBIRestMethod -MockWith {
                New-RefreshesJson -Entries @(@{ requestId = 'req-old'; status = 'Completed'; endTime = $oldEnd })
            }

            $run = Invoke-Collector -Params @{ LookbackHours = 24 }
            $run.Result.RefreshCount | Should -Be 0
        }

        It 'keeps an in-progress refresh with no endTime regardless of lookback' {
            Set-CommonMocks
            Mock -CommandName Get-PowerBIDataset -MockWith { @(New-FakeDataset -Id 'ds-1') }
            Mock -CommandName Invoke-PowerBIRestMethod -MockWith {
                New-RefreshesJson -Entries @(@{ requestId = 'req-inprogress'; status = 'Unknown'; endTime = $null })
            }

            $run = Invoke-Collector -Params @{ LookbackHours = 1 }
            $run.Result.RefreshCount | Should -Be 1
            $run.Result.RefreshRecords[0].RequestId | Should -Be 'req-inprogress'
        }
    }

    Context 'failure handling' {

        It 'records a CollectionError for one dataset and still processes the others' {
            Set-CommonMocks
            Mock -CommandName Get-PowerBIDataset -MockWith { @((New-FakeDataset -Id 'ds-bad' -Name 'Bad'), (New-FakeDataset -Id 'ds-good' -Name 'Good')) }
            Mock -CommandName Invoke-PowerBIRestMethod -MockWith {
                param($Url)
                if ($Url -like 'datasets/ds-bad/*') { throw 'throttled: 429' }
                New-RefreshesJson -Entries @(@{ requestId = 'req-good'; status = 'Completed'; endTime = (Get-Date).ToUniversalTime().ToString('o') })
            }

            $run = Invoke-Collector
            $run.Result.CollectionErrors.Count | Should -Be 1
            $run.Result.RefreshCount | Should -Be 1
            $run.Result.RefreshRecords[0].RequestId | Should -Be 'req-good'
        }

        It 'exits 1 when the MicrosoftPowerBIMgmt module is not installed' {
            Mock -CommandName Get-Module -MockWith { $null } -ParameterFilter { $Name -eq 'MicrosoftPowerBIMgmt' }

            $run = Invoke-Collector
            $run.ExitCode | Should -Be 1
        }

        It 'exits 1 when service-principal authentication fails' {
            Set-CommonMocks
            Mock -CommandName Connect-PowerBIServiceAccount -MockWith { throw 'AADSTS7000215: invalid client secret' }

            $run = Invoke-Collector
            $run.ExitCode | Should -Be 1
        }

        It 'exits 1 when Get-PowerBIDataset itself fails' {
            Set-CommonMocks
            Mock -CommandName Get-PowerBIDataset -MockWith { throw 'service unavailable' }

            $run = Invoke-Collector
            $run.ExitCode | Should -Be 1
        }
    }

    Context 'output contract' {

        It 'writes the health sidecar with the refresh count on a clean run' {
            Set-CommonMocks
            Mock -CommandName Get-PowerBIDataset -MockWith { @(New-FakeDataset -Id 'ds-1') }
            Mock -CommandName Invoke-PowerBIRestMethod -MockWith {
                New-RefreshesJson -Entries @(@{ requestId = 'req-1'; status = 'Completed'; endTime = (Get-Date).ToUniversalTime().ToString('o') })
            }

            $run = Invoke-Collector
            $health = Get-Health -OutputDir $run.OutputDir
            $health | Should -Not -BeNullOrEmpty
            $health.Status | Should -Be 'OK'
            $health.RecordCount | Should -Be 1
        }
    }
}
