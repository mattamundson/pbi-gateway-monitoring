<#
=============================================================================
Collect-MashupProcesses.ps1  |  Label: [NET-NEW]  |  [Unverified -- needs gateway host]

CLOSES PAIN POINT #5: "Mashup engine memory/CPU bloat, no per-process visibility."
No existing tool (FPM, RuiRomano, FUAM) captures PER-PROCESS resource use of the
gateway's mashup evaluation containers. The gateway spawns one or more
  Microsoft.Mashup.Container.*.exe   (a.k.a. Mashup.Container.NetFX45.exe)
child processes; a single runaway query can drive one container to consume all
server RAM, and operators today have zero visibility into WHICH process / how much.

This collector samples per-process working set (RAM), private bytes, CPU%, handle
and thread counts for the gateway service process AND every mashup container child,
and emits one JSON record per process per sample. Correlate to query load in silver
by time window + host to answer "which mashup container blew up, and when."

SIGNAL: S3 (host/process counters), extends beyond what SystemCounter logs give.

USAGE (scheduled via Task Scheduler, e.g. every 1-2 min):
    pwsh -File Collect-MashupProcesses.ps1 -OutDir "C:\GatewayMon\landing\mashup"

NOTES / [Unverified]:
  - Process name patterns below are the observed names across gateway versions;
    confirm on your host with:  Get-Process | ? { $_.Name -like '*ashup*' }
  - CPU% is computed from a short delta sample (two reads) to avoid the
    cumulative-since-start distortion of raw Process.CPU.
  - Runs read-only. Safe to run continuously.
=============================================================================
#>
[CmdletBinding()]
param(
    [string] $OutDir = "$PSScriptRoot\..\output",
    # Process name match patterns (regex). Gateway service + mashup containers.
    [string[]] $ProcessPatterns = @(
        'Microsoft\.Mashup\.Container',      # mashup evaluation containers (all variants)
        'Mashup\.Container',                 # older/short name
        'Microsoft\.PowerBI\.EnterpriseGateway'  # the gateway service itself
    ),
    [int] $CpuSampleMs = 500,
    [string] $GatewayObjectId = $env:GATEWAY_OBJECT_ID  # optional, stamped into records
)

$ErrorActionPreference = 'Stop'
if (-not (Test-Path $OutDir)) { New-Item -ItemType Directory -Path $OutDir -Force | Out-Null }

$patternRegex = ($ProcessPatterns -join '|')
$logicalCores = [Environment]::ProcessorCount

# --- take two CPU-time snapshots to derive a real interval CPU% ----------------
function Get-MatchingProcs {
    Get-Process -ErrorAction SilentlyContinue | Where-Object { $_.Name -match $patternRegex }
}

$collectionErrors = @()
$snap1 = @{}
foreach ($p in Get-MatchingProcs) {
    try { $snap1[$p.Id] = $p.TotalProcessorTime.TotalMilliseconds } catch {}
}
Start-Sleep -Milliseconds $CpuSampleMs
$collectedAtUtc = (Get-Date).ToUniversalTime()
$nowUtc = $collectedAtUtc.ToString('o')

$records = New-Object System.Collections.Generic.List[object]
foreach ($p in Get-MatchingProcs) {
    try {
        $cpuMsNow = $p.TotalProcessorTime.TotalMilliseconds
        $cpuMsPrev = if ($snap1.ContainsKey($p.Id)) { $snap1[$p.Id] } else { $cpuMsNow }
        $deltaCpuMs = [math]::Max(0, $cpuMsNow - $cpuMsPrev)
        # CPU% of one core over the sample window, then normalized to whole machine
        $cpuPctMachine = [math]::Round(($deltaCpuMs / $CpuSampleMs) / $logicalCores * 100, 2)

        $isMashup = ($p.Name -match 'ashup')
        $startTimeUtc = $null
        try { $startTimeUtc = $p.StartTime.ToUniversalTime().ToString('o') } catch { $startTimeUtc = $null }
        $records.Add([pscustomobject]@{
                CollectedAtUtc    = $nowUtc
                GatewayObjectId   = $GatewayObjectId
                HostName          = $env:COMPUTERNAME
                ProcessId         = $p.Id
                ProcessName       = $p.Name
                IsMashupContainer = $isMashup
                WorkingSetMB      = [math]::Round($p.WorkingSet64 / 1MB, 1)
                PrivateBytesMB    = [math]::Round($p.PrivateMemorySize64 / 1MB, 1)
                CpuPercent        = $cpuPctMachine
                ThreadCount       = $p.Threads.Count
                HandleCount       = $p.HandleCount
                StartTimeUtc      = $startTimeUtc
                LogicalCores      = $logicalCores
            })
    }
    catch {
        $errMsg = "skip PID $($p.Id): $($_.Exception.Message)"
        Write-Warning $errMsg
        $collectionErrors += $errMsg
    }
}

if ($records.Count -eq 0) {
    Write-Warning "No gateway/mashup processes matched /$patternRegex/. Confirm names on this host."
}

$stamp = (Get-Date).ToUniversalTime().ToString('yyyyMMddTHHmmssZ')
$outFile = Join-Path $OutDir "MashupProcesses_$stamp.json"
# newline-delimited JSON (one record per line) -- friendly to the bronze reader
$records | ForEach-Object { $_ | ConvertTo-Json -Compress } | Set-Content -Path $outFile -Encoding UTF8
Write-Host "Wrote $($records.Count) process record(s) -> $outFile"

# This collector had no health sidecar at all until now -- silently indistinguishable
# from a healthy 0-process run whether Get-Process enumeration was denied, the process
# patterns stopped matching after a gateway upgrade, or it genuinely found nothing.
. (Join-Path $PSScriptRoot 'CollectorHealth.ps1')
Write-CollectorHealth -CollectorName 'Collect-MashupProcesses' -OutputPath $OutDir `
    -CollectionErrors $collectionErrors -CollectedAtUtc $collectedAtUtc `
    -RecordCount $records.Count
