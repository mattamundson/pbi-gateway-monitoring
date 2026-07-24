# =============================================================================
# Collect-NetworkMetrics.Tests.ps1  |  roadmap T11
#
# Get-Counter and Test-Connection are both mocked -- there is no way to stage a
# specific NIC throughput or a specific ping latency on a real host, and CI
# runners have no meaningful network counters anyway. The shapes mocked here
# mirror the REAL return types: Get-Counter -MaxSamples N returns an array of
# PerformanceCounterSampleSet, each carrying a .CounterSamples array; the
# collector's own nested foreach expects exactly that shape.
# =============================================================================

BeforeAll {
    $script:Collector = (Resolve-Path (Join-Path $PSScriptRoot '..\..\collectors\Collect-NetworkMetrics.ps1')).Path
    $script:OutRoot = Join-Path ([System.IO.Path]::GetTempPath()) "gwmon_pester_$([guid]::NewGuid().ToString('N').Substring(0,8))"
    [System.IO.Directory]::CreateDirectory($script:OutRoot) | Out-Null

    function New-CounterSampleSet {
        # Builds ONE sample-set (one point in time) across N named NICs, each
        # carrying both a Bytes Total/sec and a Current Bandwidth sample --
        # the two counter paths the collector requests together.
        param([hashtable]$NicBytesAndBandwidth)
        $samples = foreach ($nic in $NicBytesAndBandwidth.Keys) {
            $bw = $NicBytesAndBandwidth[$nic]
            [pscustomobject]@{ Path = "\\host\Network Interface($nic)\Bytes Total/sec"; InstanceName = $nic; CookedValue = $bw[0] }
            [pscustomobject]@{ Path = "\\host\Network Interface($nic)\Current Bandwidth"; InstanceName = $nic; CookedValue = $bw[1] }
        }
        [pscustomobject]@{ CounterSamples = @($samples) }
    }

    function Invoke-Collector {
        param([hashtable]$Params = @{})
        $out = Join-Path $script:OutRoot ([guid]::NewGuid().ToString('N').Substring(0, 8))
        [System.IO.Directory]::CreateDirectory($out) | Out-Null
        $splat = @{ OutputPath = $out } + $Params
        & $script:Collector @splat -WarningAction SilentlyContinue | Out-Null
        $file = [System.IO.Directory]::GetFiles($out, 'network_metrics_*.json') | Select-Object -First 1
        if (-not $file) { throw "collector produced no output file in $out" }
        return ([System.IO.File]::ReadAllText($file) | ConvertFrom-Json)
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

Describe 'Collect-NetworkMetrics' {

    Context 'NIC metrics' {

        It 'computes average throughput and utilization across samples' {
            # 100 MB/s sustained on a 1 Gbps NIC -> (100e6*8)/1e9*100 = 80%
            $set1 = New-CounterSampleSet -NicBytesAndBandwidth @{ Ethernet = @(100000000, 1000000000) }
            $set2 = New-CounterSampleSet -NicBytesAndBandwidth @{ Ethernet = @(100000000, 1000000000) }
            Mock -CommandName Get-Counter -MockWith { @($set1, $set2) }
            Mock -CommandName Test-Connection -MockWith { @() }

            $r = Invoke-Collector
            $r.NicMetrics.Count | Should -Be 1
            $r.NicMetrics[0].NicName | Should -Be 'Ethernet'
            $r.NicMetrics[0].UtilizationPct | Should -Be 80
            $r.NicMetrics[0].SampleCount | Should -Be 2
        }

        It 'excludes virtual/loopback adapters by name' {
            $set = New-CounterSampleSet -NicBytesAndBandwidth @{
                Ethernet        = @(1000, 1000000)
                'isatap.local'  = @(1000, 1000000)
                'Teredo Tunnel' = @(1000, 1000000)
            }
            Mock -CommandName Get-Counter -MockWith { @($set) }
            Mock -CommandName Test-Connection -MockWith { @() }

            $r = Invoke-Collector
            $r.NicMetrics.Count | Should -Be 1
            $r.NicMetrics[0].NicName | Should -Be 'Ethernet'
        }

        It 'caps utilization at 100 even if throughput exceeds reported bandwidth' {
            # A counter glitch or a bandwidth misreport must not produce >100%.
            $set = New-CounterSampleSet -NicBytesAndBandwidth @{ Ethernet = @(999999999, 1000000) }
            Mock -CommandName Get-Counter -MockWith { @($set) }
            Mock -CommandName Test-Connection -MockWith { @() }

            $r = Invoke-Collector
            $r.NicMetrics[0].UtilizationPct | Should -Be 100
        }

        It 'reports null utilization when bandwidth is unknown (zero), not a divide error' {
            $set = New-CounterSampleSet -NicBytesAndBandwidth @{ Ethernet = @(1000, 0) }
            Mock -CommandName Get-Counter -MockWith { @($set) }
            Mock -CommandName Test-Connection -MockWith { @() }

            { Invoke-Collector } | Should -Not -Throw
            $r = Invoke-Collector
            $r.NicMetrics[0].UtilizationPct | Should -BeNullOrEmpty
        }

        It 'records a CollectionError when Get-Counter fails, without crashing' {
            Mock -CommandName Get-Counter -MockWith { throw 'Access to performance counters denied' }
            Mock -CommandName Test-Connection -MockWith { @() }

            { Invoke-Collector } | Should -Not -Throw
            $r = Invoke-Collector
            $r.NicMetrics.Count | Should -Be 0
            (Get-ErrorCount $r) | Should -BeGreaterThan 0
        }
    }

    Context 'latency probe' {

        It 'averages latency using the ResponseTime property (Windows PowerShell 5.1 shape)' {
            Mock -CommandName Get-Counter -MockWith { @() }
            Mock -CommandName Test-Connection -MockWith {
                @(
                    [pscustomobject]@{ ResponseTime = 10 },
                    [pscustomobject]@{ ResponseTime = 20 }
                )
            }

            $r = Invoke-Collector
            $r.LatencyProbe.LatencyMs_avg | Should -Be 15
            $r.LatencyProbe.ProbeCount | Should -Be 2
        }

        It 'averages latency using the Latency property (PowerShell 7 shape)' {
            Mock -CommandName Get-Counter -MockWith { @() }
            Mock -CommandName Test-Connection -MockWith {
                @(
                    [pscustomobject]@{ Latency = 30 },
                    [pscustomobject]@{ Latency = 50 }
                )
            }

            $r = Invoke-Collector
            $r.LatencyProbe.LatencyMs_avg | Should -Be 40
        }

        It 'records a CollectionError and an Error field when the probe target is unreachable' {
            Mock -CommandName Get-Counter -MockWith { @() }
            Mock -CommandName Test-Connection -MockWith { throw 'destination host unreachable' }

            $r = Invoke-Collector -Params @{ LatencyProbeTarget = 'unreachable.invalid' }
            (Get-ErrorCount $r) | Should -BeGreaterThan 0
            $r.LatencyProbe.Error | Should -Not -BeNullOrEmpty
            $r.LatencyProbe.TargetHost | Should -Be 'unreachable.invalid'
        }
    }

    Context 'output contract' {

        It 'emits every field the bronze ingest contract expects, and writes the health sidecar' {
            Mock -CommandName Get-Counter -MockWith { @() }
            Mock -CommandName Test-Connection -MockWith {
                @([pscustomobject]@{ ResponseTime = 5 })
            }

            $out = Join-Path $script:OutRoot ([guid]::NewGuid().ToString('N').Substring(0, 8))
            [System.IO.Directory]::CreateDirectory($out) | Out-Null
            & $script:Collector -OutputPath $out -WarningAction SilentlyContinue | Out-Null

            $payload = [System.IO.Directory]::GetFiles($out, 'network_metrics_*.json') | Select-Object -First 1
            $payload | Should -Not -BeNullOrEmpty
            $r = [System.IO.File]::ReadAllText($payload) | ConvertFrom-Json
            foreach ($f in 'CollectedAtUtc', 'GatewayHostName', 'NicMetrics', 'LatencyProbe', 'CollectionErrors') {
                $r.PSObject.Properties.Name | Should -Contain $f
            }

            $health = [System.IO.Directory]::GetFiles($out, 'collector_health_Collect-NetworkMetrics_*.json') | Select-Object -First 1
            $health | Should -Not -BeNullOrEmpty -Because 'the health sidecar is the only channel a downstream ingest reads CollectionErrors from'
        }
    }
}
