# =============================================================================
# Collect-EventLog.Tests.ps1  |  roadmap T11
#
# Get-WinEvent is mocked with -ParameterFilter on LogName so the Application and
# System queries can be independently controlled. Real Get-WinEvent semantics
# are preserved in the mocks: a THROW for "no events were found" is benign (the
# collector catches it by message text, matching live Get-WinEvent behavior),
# any other throw is a real collection failure.
# =============================================================================

BeforeAll {
    $script:Collector = (Resolve-Path (Join-Path $PSScriptRoot '..\..\collectors\Collect-EventLog.ps1')).Path
    $script:OutRoot = Join-Path ([System.IO.Path]::GetTempPath()) "gwmon_pester_$([guid]::NewGuid().ToString('N').Substring(0,8))"
    [System.IO.Directory]::CreateDirectory($script:OutRoot) | Out-Null

    function New-FakeEvent {
        param([int]$Id = 7034, [string]$Provider = 'On-premises data gateway', [string]$Message = 'gateway service crashed', [string]$Level = 'Error')
        [pscustomobject]@{
            TimeCreated      = (Get-Date).ToUniversalTime()
            Id               = $Id
            LevelDisplayName = $Level
            ProviderName     = $Provider
            Message          = $Message
        }
    }

    function Invoke-Collector {
        param([hashtable]$Params = @{})
        $out = Join-Path $script:OutRoot ([guid]::NewGuid().ToString('N').Substring(0, 8))
        [System.IO.Directory]::CreateDirectory($out) | Out-Null
        $splat = @{ OutputPath = $out } + $Params
        & $script:Collector @splat -WarningAction SilentlyContinue | Out-Null
        $file = [System.IO.Directory]::GetFiles($out, 'event_log_*.json') | Select-Object -First 1
        if (-not $file) { throw "collector produced no output file in $out" }
        return @{
            Result    = ([System.IO.File]::ReadAllText($file) | ConvertFrom-Json)
            OutputDir = $out
        }
    }

    function Get-Watermark {
        param([string]$OutputDir)
        $p = Join-Path $OutputDir 'watermark_event_log.json'
        if (-not (Test-Path $p)) { return $null }
        return (Get-Content $p -Raw | ConvertFrom-Json)
    }

    function Get-ErrorCount {
        param($Result)
        if ($null -eq $Result.CollectionErrors) { return 0 }
        return @($Result.CollectionErrors).Count
    }
}

AfterAll {
    if (Test-Path $script:OutRoot) { Remove-Item $script:OutRoot -Recurse -Force -ErrorAction SilentlyContinue }
}

Describe 'Collect-EventLog' {

    Context 'happy path' {

        It 'collects Application log gateway events and System SCM events' {
            Mock -CommandName Get-WinEvent -ParameterFilter { $FilterHashtable.LogName -eq 'Application' } -MockWith {
                @(New-FakeEvent -Id 1000 -Provider 'On-premises data gateway' -Message 'started')
            }
            Mock -CommandName Get-WinEvent -ParameterFilter { $FilterHashtable.LogName -eq 'System' } -MockWith {
                @(New-FakeEvent -Id 7034 -Provider 'Service Control Manager' -Message 'The PBIEgwService service terminated unexpectedly')
            }

            $r = (Invoke-Collector).Result
            $r.EventCount | Should -Be 2
            ($r.Events | Where-Object { $_.LogName -eq 'Application' }).Count | Should -Be 1
            ($r.Events | Where-Object { $_.LogName -eq 'System' }).Count | Should -Be 1
        }

        It 'filters out System log events that do not mention the gateway service' {
            Mock -CommandName Get-WinEvent -ParameterFilter { $FilterHashtable.LogName -eq 'Application' } -MockWith { @() }
            Mock -CommandName Get-WinEvent -ParameterFilter { $FilterHashtable.LogName -eq 'System' } -MockWith {
                @(New-FakeEvent -Id 7034 -Provider 'Service Control Manager' -Message 'The Print Spooler service terminated unexpectedly')
            }

            $r = (Invoke-Collector).Result
            $r.EventCount | Should -Be 0
        }

        It 'treats "no events were found" as a benign empty result, not an error' {
            Mock -CommandName Get-WinEvent -MockWith { throw 'No events were found that match the specified selection criteria.' }

            $r = (Invoke-Collector).Result
            $r.EventCount | Should -Be 0
            (Get-ErrorCount $r) | Should -Be 0
        }
    }

    Context 'watermark integrity (T10-class regression)' {

        It 'advances the watermark after a clean run' {
            Mock -CommandName Get-WinEvent -MockWith { @() }
            $before = (Get-Date).ToUniversalTime()

            $run = Invoke-Collector
            $wm = Get-Watermark -OutputDir $run.OutputDir
            $wm | Should -Not -BeNullOrEmpty
            ([datetime]$wm.LastProcessedUtc) | Should -BeGreaterOrEqual $before
        }

        It 'does NOT advance the watermark when both log queries fail with a real error' {
            # A persistent failure (permissions revoked, log corrupted, etc) must
            # not silently swallow the time window it failed to cover. Advancing
            # the watermark here means those events are gone forever with no
            # retry -- the collector would report success while data quietly
            # disappeared. Same failure shape as the Collect-GatewayLogs fix:
            # never update the watermark for a window that was not actually read.
            Mock -CommandName Get-WinEvent -MockWith { throw [System.UnauthorizedAccessException]::new('Access to the event log is denied.') }

            $run = Invoke-Collector -Params @{ LookbackMinutes = 5 }
            (Get-ErrorCount $run.Result) | Should -BeGreaterThan 0

            $wmPath = Join-Path $run.OutputDir 'watermark_event_log.json'
            if (Test-Path $wmPath) {
                $wm = Get-Content $wmPath -Raw | ConvertFrom-Json
                $expectedUnadvanced = (Get-Date).ToUniversalTime().AddMinutes(-5)
                # The watermark must still point at (collection time - lookback),
                # i.e. it was never moved forward past the failed window.
                ([datetime]$wm.LastProcessedUtc) | Should -BeLessThan ((Get-Date).ToUniversalTime().AddMinutes(-4)) -Because `
                    'a watermark advanced past a failed collection window permanently loses those events'
            }
        }

        It 'does NOT advance the watermark on a PARTIAL failure (System log fails, Application log succeeds)' {
            Mock -CommandName Get-WinEvent -ParameterFilter { $FilterHashtable.LogName -eq 'Application' } -MockWith { @() }
            Mock -CommandName Get-WinEvent -ParameterFilter { $FilterHashtable.LogName -eq 'System' } -MockWith {
                throw [System.UnauthorizedAccessException]::new('Access to the System event log is denied.')
            }

            $run = Invoke-Collector -Params @{ LookbackMinutes = 5 }
            (Get-ErrorCount $run.Result) | Should -BeGreaterThan 0

            $wmPath = Join-Path $run.OutputDir 'watermark_event_log.json'
            if (Test-Path $wmPath) {
                $wm = Get-Content $wmPath -Raw | ConvertFrom-Json
                ([datetime]$wm.LastProcessedUtc) | Should -BeLessThan ((Get-Date).ToUniversalTime().AddMinutes(-4)) -Because `
                    'both log sources share one watermark; a partial failure must not advance it, or the failed source''s window is silently skipped next run too'
            }
        }
    }

    Context 'output contract' {

        It 'emits every field the bronze ingest contract expects, and writes the health sidecar' {
            Mock -CommandName Get-WinEvent -MockWith { @() }
            $run = Invoke-Collector
            foreach ($f in 'CollectedAtUtc', 'GatewayHostName', 'EventCount', 'CollectionErrors', 'Events') {
                $run.Result.PSObject.Properties.Name | Should -Contain $f
            }
            $health = [System.IO.Directory]::GetFiles($run.OutputDir, 'collector_health_Collect-EventLog_*.json') | Select-Object -First 1
            $health | Should -Not -BeNullOrEmpty
        }
    }
}
