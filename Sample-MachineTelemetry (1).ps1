<#
.SYNOPSIS
    One-shot telemetry sample for resource contention monitoring.
    Designed to be invoked every 60 seconds by Windows Task Scheduler.

.DESCRIPTION
    Writes a single CSV row to a daily rotating log under OneDrive so the
    cloud cron can read it.

    What we sample (and WHY — these are the contention signals, not vanity metrics):

    - cpu_pct:              Total CPU load (% of capacity)
    - cpu_queue_length:     # of threads waiting for a CPU slice. >2 = contention.
    - ram_used_gb:          Absolute RAM in use
    - ram_avail_gb:         RAM available (Windows' "memory pressure" sense)
    - ram_committed_pct:    % of commit charge used. >85% = swapping risk.
    - disk_queue_length:    Avg disk queue length on C:. >2 = I/O contention.
    - disk_pct:             % time disk is busy
    - net_kbps_in / out:    Network throughput in KB/s
    - chrome_ram_gb:        Chromium-family processes (Chrome, Edge, Brave, Comet) RAM sum
    - python_ram_gb:        Python processes RAM sum (proxy for jarvis-trader load)
    - top3_procs:           "name:rss_gb" of top-3 processes by RSS, semicolon-joined

    The CSV uses a stable schema so the cloud analyzer can parse it deterministically.

.PARAMETER OutDir
    Directory to write the CSV. Default: $env:OneDrive\jarvis-trader-handoff\telemetry

.NOTES
    Counter access uses Get-Counter (built-in, no admin required for most counters).
    Some counters (Memory\% Committed Bytes In Use) require the user be in the
    "Performance Monitor Users" group. On a single-user dev box that's usually the
    Administrators group already.

    The script is intentionally fast (~2 seconds per sample) and silent on success.
    Failures log to a sibling error file but never throw — Task Scheduler should
    keep running even if one sample fails.
#>

[CmdletBinding()]
param(
    [string]$OutDir = (Join-Path $env:OneDrive "jarvis-trader-handoff\telemetry")
)

$ErrorActionPreference = 'Continue'  # don't crash Task Scheduler on a bad sample
$sampleStart = Get-Date

try {
    if (-not (Test-Path $OutDir)) { New-Item -ItemType Directory -Path $OutDir -Force | Out-Null }

    # ── Counters (single Get-Counter call is faster than many separate calls) ──
    $counterPaths = @(
        '\Processor(_Total)\% Processor Time',
        '\System\Processor Queue Length',
        '\Memory\Available MBytes',
        '\Memory\% Committed Bytes In Use',
        '\PhysicalDisk(_Total)\% Disk Time',
        '\PhysicalDisk(_Total)\Avg. Disk Queue Length',
        '\Network Interface(*)\Bytes Received/sec',
        '\Network Interface(*)\Bytes Sent/sec'
    )
    $cs = Get-CimInstance Win32_ComputerSystem
    $totalRamGB = [math]::Round($cs.TotalPhysicalMemory / 1GB, 2)

    $counters = Get-Counter -Counter $counterPaths -SampleInterval 1 -MaxSamples 1 -ErrorAction Stop

    function Get-CounterValue {
        param([string]$Suffix)
        ($counters.CounterSamples | Where-Object { $_.Path -like "*$Suffix" }).CookedValue
    }

    $cpu_pct           = [math]::Round((Get-CounterValue '\% processor time'), 1)
    $cpu_queue         = [math]::Round((Get-CounterValue '\processor queue length'), 1)
    $ram_avail_mb      = (Get-CounterValue '\available mbytes')
    $ram_avail_gb      = [math]::Round($ram_avail_mb / 1024, 2)
    $ram_used_gb       = [math]::Round($totalRamGB - $ram_avail_gb, 2)
    $ram_committed_pct = [math]::Round((Get-CounterValue '\% committed bytes in use'), 1)
    $disk_pct          = [math]::Round((Get-CounterValue '\% disk time'), 1)
    $disk_queue        = [math]::Round((Get-CounterValue '\avg. disk queue length'), 2)

    # Network: sum across non-loopback interfaces
    $netInSamples  = $counters.CounterSamples | Where-Object { $_.Path -like '*\bytes received/sec' -and $_.Path -notlike '*loopback*' -and $_.Path -notlike '*isatap*' }
    $netOutSamples = $counters.CounterSamples | Where-Object { $_.Path -like '*\bytes sent/sec'     -and $_.Path -notlike '*loopback*' -and $_.Path -notlike '*isatap*' }
    $net_kbps_in   = [math]::Round((($netInSamples  | Measure-Object CookedValue -Sum).Sum / 1024), 1)
    $net_kbps_out  = [math]::Round((($netOutSamples | Measure-Object CookedValue -Sum).Sum / 1024), 1)

    # ── Process attribution ─────────────────────────────────────────────────
    $procs = Get-Process | Where-Object { $_.WorkingSet64 -gt 50MB }

    $chromeRamGB = [math]::Round(
        (($procs | Where-Object { $_.ProcessName -in 'chrome','msedge','brave','comet' } |
            Measure-Object WorkingSet64 -Sum).Sum / 1GB), 2
    )
    $pythonRamGB = [math]::Round(
        (($procs | Where-Object { $_.ProcessName -in 'python','pythonw','py' } |
            Measure-Object WorkingSet64 -Sum).Sum / 1GB), 2
    )

    $top3 = ($procs | Sort-Object WorkingSet64 -Descending | Select-Object -First 3 |
        ForEach-Object { "$($_.ProcessName):$([math]::Round($_.WorkingSet64/1GB,2))" }) -join ';'

    # ── Compose row ─────────────────────────────────────────────────────────
    $row = [PSCustomObject]@{
        ts_utc            = $sampleStart.ToUniversalTime().ToString('o')
        ts_local          = $sampleStart.ToString('yyyy-MM-ddTHH:mm:ss')
        hostname          = $env:COMPUTERNAME
        cpu_pct           = $cpu_pct
        cpu_queue         = $cpu_queue
        ram_total_gb      = $totalRamGB
        ram_used_gb       = $ram_used_gb
        ram_avail_gb      = $ram_avail_gb
        ram_committed_pct = $ram_committed_pct
        disk_pct          = $disk_pct
        disk_queue        = $disk_queue
        net_kbps_in       = $net_kbps_in
        net_kbps_out      = $net_kbps_out
        chrome_ram_gb     = $chromeRamGB
        python_ram_gb     = $pythonRamGB
        top3_procs        = $top3
    }

    # ── Append to today's CSV (create with header if new) ───────────────────
    $dayStamp  = $sampleStart.ToString('yyyy-MM-dd')
    $csvPath   = Join-Path $OutDir "telemetry_$dayStamp.csv"
    $needsHeader = -not (Test-Path $csvPath)

    if ($needsHeader) {
        $row | Export-Csv -Path $csvPath -NoTypeInformation -Encoding UTF8
    } else {
        # Append without header. Export-Csv -Append works in PS 5.1+.
        $row | Export-Csv -Path $csvPath -NoTypeInformation -Encoding UTF8 -Append
    }
}
catch {
    # Never throw — log and exit clean so Task Scheduler keeps invoking us
    $errPath = Join-Path $OutDir "telemetry_errors.log"
    "$($sampleStart.ToString('o'))  $($_.Exception.Message)" |
        Add-Content -Path $errPath -Encoding UTF8 -ErrorAction SilentlyContinue
}
