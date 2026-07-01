# =============================================================================
# Collect-NetworkMetrics.ps1
# Label: [NET-NEW]
#
# Pain point addressed: #7 — Network / Bandwidth Blind Spot
# Signal: S11 — OS-level NIC counters + latency probe
#
# This is Differentiator #4 — the HIGHEST-VALUE GAP. No existing tool
# (FPM, pbigtwmonitor, Gateway PBIT, SummitView, or any other) collects
# OS-level network bandwidth and correlates it with gateway query data.
#
# Microsoft's own documentation explicitly confirms this gap:
#   "Gateway diagnostics doesn't capture diagnostics directly related to
#    the (virtual) machine and its network, like bandwidth or latency."
#   Source: https://learn.microsoft.com/en-us/data-integration/gateway/service-gateway-performance
#
# What this collects:
#   - \Network Interface(*)\Bytes Total/sec  — total NIC throughput
#   - \Network Interface(*)\Current Bandwidth — maximum NIC bandwidth (bps)
#   - Test-Connection latency to Power BI relay endpoint
#
# [NET-NEW] No prior art in existing gateway monitoring tools.
# [Unverified] PerfMon counter names confirmed from Microsoft Windows docs
#              but not tested on a live gateway host with Phase 5 pilot.
#
# Counter name reference:
#   https://learn.microsoft.com/en-us/windows-server/administration/windows-commands/typeperf
#   https://learn.microsoft.com/en-us/data-integration/gateway/service-gateway-performance#network
#
# Recommended scheduling: Windows Scheduled Task every 5 minutes.
# =============================================================================

[CmdletBinding()]
param(
    [Parameter(Mandatory = $false)]
    [string]$OutputPath = "$PSScriptRoot\..\output",

    # Power BI / Fabric relay probe target.
    # [Assumption] msftncsi.com is accessible from gateway host for latency probing.
    # A more accurate target would be the Azure Service Bus relay endpoint,
    # but that is not publicly documented for customer use.
    # [Unverified] - confirm with your network team.
    [Parameter(Mandatory = $false)]
    [string]$LatencyProbeTarget = "msftncsi.com",

    [Parameter(Mandatory = $false)]
    [int]$LatencyProbeCount = 4,

    # Number of PerfMon sample intervals (1 per second by default)
    [Parameter(Mandatory = $false)]
    [int]$SampleIntervalSeconds = 10,

    [Parameter(Mandatory = $false)]
    [int]$SampleCount = 3
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

if (-not (Test-Path $OutputPath)) { New-Item -ItemType Directory -Path $OutputPath | Out-Null }

$collectedAtUtc = (Get-Date).ToUniversalTime()
$result = @{
    CollectedAtUtc  = $collectedAtUtc.ToString("o")
    GatewayHostName = $env:COMPUTERNAME
    NicMetrics      = @()
    LatencyProbe    = $null
    CollectionErrors = @()
}

# ---------------------------------------------------------------------------
# NIC counters via Get-Counter
# Counter names use (*) wildcard to capture all network interfaces.
# The gateway typically uses the primary NIC connected to the corporate LAN.
# Operators may need to filter by NIC name via config if multiple NICs exist.
# [NET-NEW] No existing gateway monitoring tool collects these.
# ---------------------------------------------------------------------------
try {
    Write-Verbose "Collecting NIC counters..."

    # [Unverified] Counter path syntax confirmed from Windows documentation
    # but exact counter names may vary by Windows version/NIC driver.
    $counterPaths = @(
        '\Network Interface(*)\Bytes Total/sec',
        '\Network Interface(*)\Current Bandwidth'
    )

    $counterData = Get-Counter -Counter $counterPaths `
                               -SampleInterval $SampleIntervalSeconds `
                               -MaxSamples $SampleCount `
                               -ErrorAction Stop

    # Average across samples per NIC instance
    $nicSamples = @{}
    foreach ($sampleSet in $counterData.CounterSamples) {
        foreach ($sample in $sampleSet) {
            $nicName = $sample.InstanceName
            if (-not $nicSamples.ContainsKey($nicName)) {
                $nicSamples[$nicName] = @{
                    BytesTotalPerSec   = @()
                    CurrentBandwidthBps = @()
                }
            }
            if ($sample.Path -like '*Bytes Total/sec*') {
                $nicSamples[$nicName].BytesTotalPerSec += $sample.CookedValue
            }
            elseif ($sample.Path -like '*Current Bandwidth*') {
                $nicSamples[$nicName].CurrentBandwidthBps += $sample.CookedValue
            }
        }
    }

    foreach ($nicName in $nicSamples.Keys) {
        # Skip loopback and virtual adapters commonly named "isatap" or "Teredo"
        if ($nicName -match "isatap|teredo|loopback|6to4" ) { continue }

        $bytesPerSec = $nicSamples[$nicName].BytesTotalPerSec
        $bandwidth   = $nicSamples[$nicName].CurrentBandwidthBps

        $avgBytesPerSec = if ($bytesPerSec.Count -gt 0) { ($bytesPerSec | Measure-Object -Average).Average } else { 0 }
        $avgBandwidth   = if ($bandwidth.Count -gt 0)   { ($bandwidth   | Measure-Object -Average).Average } else { 0 }

        # Utilization: (BytesTotalPerSec * 8) / CurrentBandwidthBps * 100
        # BytesTotalPerSec is bytes; CurrentBandwidthBps is bits per second
        $utilizationPct = if ($avgBandwidth -gt 0) {
            [Math]::Min(100, ($avgBytesPerSec * 8) / $avgBandwidth * 100)
        } else { $null }

        $result.NicMetrics += @{
            NicName              = $nicName
            BytesTotalPerSec_avg = [Math]::Round($avgBytesPerSec, 2)
            CurrentBandwidthBps  = $avgBandwidth
            UtilizationPct       = if ($utilizationPct) { [Math]::Round($utilizationPct, 2) } else { $null }
            SampleCount          = $bytesPerSec.Count
        }
    }

    Write-Verbose "Collected metrics for $($result.NicMetrics.Count) NIC(s)"
}
catch {
    $errMsg = "NIC counter collection failed: $_"
    Write-Warning $errMsg
    $result.CollectionErrors += $errMsg
}

# ---------------------------------------------------------------------------
# Latency probe via Test-Connection
# Measures round-trip time to a well-known endpoint as a proxy for
# gateway → Power BI Service network latency.
#
# [Assumption] msftncsi.com (Microsoft's network connectivity test host)
#              is accessible from gateway host and ICMP is not blocked.
# [Limitation] This measures ICMP latency, not TCP/Service Bus latency.
#              True Azure Service Bus latency would require a TCP probe
#              against the gateway's relay endpoint, which is not publicly
#              documented. ICMP is a best-effort proxy.
# ---------------------------------------------------------------------------
try {
    Write-Verbose "Running latency probe to $LatencyProbeTarget..."

    # Test-Connection -ComputerName returns PingStatus objects
    # [Unverified] ResponseTime property name confirmed for PS 5.1+; PS 7 uses Latency
    $pings = Test-Connection -ComputerName $LatencyProbeTarget `
                              -Count $LatencyProbeCount `
                              -ErrorAction Stop

    # Handle both PS 5.1 (ResponseTime) and PS 7 (Latency) property names
    $latencies = foreach ($ping in $pings) {
        if ($null -ne $ping.ResponseTime) { $ping.ResponseTime }
        elseif ($null -ne $ping.Latency)  { $ping.Latency }
        else { $null }
    }
    $latencies = $latencies | Where-Object { $null -ne $_ }

    $result.LatencyProbe = @{
        TargetHost      = $LatencyProbeTarget
        LatencyMs_avg   = if ($latencies.Count -gt 0) { [Math]::Round(($latencies | Measure-Object -Average).Average, 1) } else { $null }
        LatencyMs_min   = if ($latencies.Count -gt 0) { ($latencies | Measure-Object -Minimum).Minimum } else { $null }
        LatencyMs_max   = if ($latencies.Count -gt 0) { ($latencies | Measure-Object -Maximum).Maximum } else { $null }
        ProbeCount      = $latencies.Count
        ProbeMethod     = "ICMP (proxy for Service Bus latency)"
    }
    Write-Verbose "Latency to $LatencyProbeTarget`: avg=$($result.LatencyProbe.LatencyMs_avg)ms"
}
catch {
    $errMsg = "Latency probe failed (target: $LatencyProbeTarget): $_"
    Write-Warning $errMsg
    $result.CollectionErrors += $errMsg
    $result.LatencyProbe = @{
        TargetHost   = $LatencyProbeTarget
        Error        = $errMsg
    }
}

# ---------------------------------------------------------------------------
# Write JSON output for bronze ingest (01_bronze_ingest.py)
# ---------------------------------------------------------------------------
$timestamp = $collectedAtUtc.ToString("yyyyMMdd_HHmmss")
$outputFile = "$OutputPath\network_metrics_$timestamp.json"

$result | ConvertTo-Json -Depth 5 | Out-File $outputFile -Encoding UTF8
Write-Output "Collect-NetworkMetrics: Wrote $outputFile (NICs: $($result.NicMetrics.Count), errors: $($result.CollectionErrors.Count))"
