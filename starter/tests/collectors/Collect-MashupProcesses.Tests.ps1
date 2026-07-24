# =============================================================================
# Collect-MashupProcesses.Tests.ps1  |  roadmap T11
#
# Get-Process is mocked -- there is no way to stage a specific mashup container
# process on a real host, and CI runners never run the actual gateway. The fake
# process objects carry exactly the properties the collector reads (Id, Name,
# TotalProcessorTime, WorkingSet64, PrivateMemorySize64, Threads, HandleCount,
# StartTime); Get-Process is called twice per run (before and after the CPU
# sample sleep) so the mock must be stable across both calls.
# =============================================================================

BeforeAll {
    $script:Collector = (Resolve-Path (Join-Path $PSScriptRoot '..\..\collectors\Collect-MashupProcesses.ps1')).Path
    $script:OutRoot = Join-Path ([System.IO.Path]::GetTempPath()) "gwmon_pester_$([guid]::NewGuid().ToString('N').Substring(0,8))"
    [System.IO.Directory]::CreateDirectory($script:OutRoot) | Out-Null

    function New-FakeProc {
        param([int]$Id, [string]$Name, [double]$CpuMs = 100, [long]$WorkingSet = 50MB, [long]$PrivateBytes = 40MB, [int]$Threads = 4, [int]$Handles = 200, [switch]$ThrowOnAccess)
        $obj = [pscustomobject]@{
            Id                  = $Id
            Name                = $Name
            TotalProcessorTime  = [timespan]::FromMilliseconds($CpuMs)
            WorkingSet64        = $WorkingSet
            PrivateMemorySize64 = $PrivateBytes
            Threads             = @(1..$Threads)
            StartTime           = (Get-Date).ToUniversalTime().AddMinutes(-30)
        }
        if ($ThrowOnAccess) {
            # A ScriptProperty getter that throws is silently swallowed to $null
            # by PowerShell's property evaluator (confirmed empirically -- it does
            # NOT propagate as a terminating error), so it cannot model a real
            # exited-process read failure. A malformed property VALUE does throw
            # reliably: $p.WorkingSet64 / 1MB throws a genuine RuntimeException
            # when WorkingSet64 isn't numeric, which is what the collector's
            # per-process try/catch actually has to survive.
            $obj | Add-Member -MemberType NoteProperty -Name 'WorkingSet64' -Value 'not-a-number' -Force
            $obj | Add-Member -MemberType NoteProperty -Name 'HandleCount' -Value $Handles
        }
        else {
            $obj | Add-Member -MemberType NoteProperty -Name 'HandleCount' -Value $Handles
        }
        return $obj
    }

    function Invoke-Collector {
        param([hashtable]$Params = @{}, [switch]$AllowNoOutputFile)
        $out = Join-Path $script:OutRoot ([guid]::NewGuid().ToString('N').Substring(0, 8))
        [System.IO.Directory]::CreateDirectory($out) | Out-Null
        $splat = @{ OutDir = $out; CpuSampleMs = 5 } + $Params
        & $script:Collector @splat -WarningAction SilentlyContinue | Out-Null
        $file = [System.IO.Directory]::GetFiles($out, 'MashupProcesses_*.json') | Select-Object -First 1
        if (-not $file) {
            if ($AllowNoOutputFile) { return @{ Records = @(); OutputDir = $out } }
            throw "collector produced no output file in $out"
        }
        # NDJSON: one compact JSON object per line.
        $records = [System.IO.File]::ReadAllLines($file) | Where-Object { $_ } | ForEach-Object { $_ | ConvertFrom-Json }
        return @{ Records = @($records); OutputDir = $out }
    }

    function Get-Health {
        param([string]$OutputDir)
        $f = [System.IO.Directory]::GetFiles($OutputDir, 'collector_health_Collect-MashupProcesses_*.json') | Select-Object -First 1
        if (-not $f) { return $null }
        return ([System.IO.File]::ReadAllText($f) | ConvertFrom-Json)
    }
}

AfterAll {
    if (Test-Path $script:OutRoot) { Remove-Item $script:OutRoot -Recurse -Force -ErrorAction SilentlyContinue }
}

Describe 'Collect-MashupProcesses' {

    Context 'happy path' {

        It 'emits one record per matching process and excludes non-matching ones' {
            Mock -CommandName Get-Process -MockWith {
                @(
                    New-FakeProc -Id 100 -Name 'Microsoft.Mashup.Container.NetFX45'
                    New-FakeProc -Id 200 -Name 'Microsoft.PowerBI.EnterpriseGateway'
                    New-FakeProc -Id 300 -Name 'notepad'
                )
            }

            $run = Invoke-Collector
            $run.Records.Count | Should -Be 2
            ($run.Records | Where-Object ProcessId -eq 300) | Should -BeNullOrEmpty
        }

        It 'flags IsMashupContainer true only for mashup-named processes' {
            Mock -CommandName Get-Process -MockWith {
                @(
                    New-FakeProc -Id 100 -Name 'Microsoft.Mashup.Container.NetFX45'
                    New-FakeProc -Id 200 -Name 'Microsoft.PowerBI.EnterpriseGateway'
                )
            }

            $run = Invoke-Collector
            ($run.Records | Where-Object ProcessId -eq 100).IsMashupContainer | Should -Be $true
            ($run.Records | Where-Object ProcessId -eq 200).IsMashupContainer | Should -Be $false
        }

        It 'reports the health sidecar as OK when processes are found and nothing errors' {
            Mock -CommandName Get-Process -MockWith { @(New-FakeProc -Id 100 -Name 'Microsoft.Mashup.Container.NetFX45') }

            $run = Invoke-Collector
            $health = Get-Health -OutputDir $run.OutputDir
            $health | Should -Not -BeNullOrEmpty
            $health.Status | Should -Be 'OK'
            $health.RecordCount | Should -Be 1
        }
    }

    Context 'failure handling (T11 -- this collector had zero health-sidecar wiring before)' {

        It 'does not crash and reports DEGRADED via the health sidecar when zero processes match' {
            # Set-Content with zero piped objects creates no file at all (confirmed
            # empirically) -- a zero-match run legitimately has no data file, but
            # the health sidecar (a separate file, written unconditionally) still
            # must exist and say DEGRADED. That is the whole point of T11 here:
            # this collector had NO sidecar at all before this fix.
            Mock -CommandName Get-Process -MockWith { @(New-FakeProc -Id 900 -Name 'notepad') }

            $run = Invoke-Collector -AllowNoOutputFile
            $run.Records.Count | Should -Be 0
            $health = Get-Health -OutputDir $run.OutputDir
            $health | Should -Not -BeNullOrEmpty -Because 'the health sidecar must exist even on a run that writes no data file'
            $health.Status | Should -Be 'DEGRADED' -Because 'a matched-zero run is usually a misconfigured pattern list, not a healthy idle state'
        }

        It 'records a CollectionError and keeps other processes when one process has a malformed property' {
            # A genuinely bad property value throws mid-expression while building
            # that process's record; the good process must still be recorded and
            # the run must not crash.
            Mock -CommandName Get-Process -MockWith {
                @(
                    (New-FakeProc -Id 100 -Name 'Microsoft.Mashup.Container.NetFX45'),
                    (New-FakeProc -Id 999 -Name 'Microsoft.Mashup.Container.Exiting' -ThrowOnAccess)
                )
            }

            $run = Invoke-Collector
            ($run.Records | Where-Object ProcessId -eq 100) | Should -Not -BeNullOrEmpty
            ($run.Records | Where-Object ProcessId -eq 999) | Should -BeNullOrEmpty
            $health = Get-Health -OutputDir $run.OutputDir
            $health.Status | Should -Be 'DEGRADED'
            $health.ErrorCount | Should -BeGreaterThan 0 -Because 'a per-process read failure must surface, not just a Write-Warning nobody reads'
        }
    }

    Context 'output contract' {

        It 'emits every field the bronze ingest contract expects' {
            Mock -CommandName Get-Process -MockWith { @(New-FakeProc -Id 100 -Name 'Microsoft.Mashup.Container.NetFX45') }

            $run = Invoke-Collector
            $r = $run.Records[0]
            foreach ($f in 'CollectedAtUtc', 'HostName', 'ProcessId', 'ProcessName', 'IsMashupContainer', 'WorkingSetMB', 'PrivateBytesMB', 'CpuPercent', 'ThreadCount', 'HandleCount') {
                $r.PSObject.Properties.Name | Should -Contain $f
            }
        }
    }
}
