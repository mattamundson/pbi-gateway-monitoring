<#
.SYNOPSIS
    Machine hardware audit — writes JSON to OneDrive for automated cloud pickup.
    Replaces the interactive v1 that only printed to stdout.

.DESCRIPTION
    Same signal capture as v1, but:
      - Writes to OneDrive at jarvis-trader-handoff/telemetry/machine_audit_latest.json
      - Also appends to machine_audit_history.jsonl (one row per invocation)
      - Silent on success; errors go to machine_audit_errors.log
      - Designed to run via Task Scheduler on boot + weekly, no interaction

    You STILL get to paste it manually if you want — the file lives in OneDrive
    where you can open it with any editor.

.PARAMETER OutDir
    Default: $env:OneDrive\jarvis-trader-handoff\telemetry

.PARAMETER Print
    If set, also prints the JSON to stdout (for manual invocation and copy-paste).
#>

[CmdletBinding()]
param(
    [string]$OutDir = (Join-Path $env:OneDrive "jarvis-trader-handoff\telemetry"),
    [switch]$Print
)

$ErrorActionPreference = 'Continue'
$startedAt = Get-Date

try {
    if (-not (Test-Path $OutDir)) { New-Item -ItemType Directory -Path $OutDir -Force | Out-Null }

    $os   = Get-CimInstance Win32_OperatingSystem
    $cpu  = Get-CimInstance Win32_Processor | Select-Object -First 1
    $cs   = Get-CimInstance Win32_ComputerSystem
    $ram  = [math]::Round($cs.TotalPhysicalMemory / 1GB, 1)
    $disk = Get-CimInstance Win32_LogicalDisk -Filter "DeviceID='C:'"
    $gpu  = Get-CimInstance Win32_VideoController | Select-Object -First 1
    $freeRamGB = [math]::Round($os.FreePhysicalMemory / 1MB, 1)

    $chromeProcs = Get-Process chrome,msedge,brave,comet -ErrorAction SilentlyContinue
    $chromeRamGB = if ($chromeProcs) {
        [math]::Round(($chromeProcs | Measure-Object WorkingSet64 -Sum).Sum / 1GB, 2)
    } else { 0 }

    $pythonVer = (python --version 2>&1) -as [string]
    $gitVer    = (git --version    2>&1) -as [string]

    $latencyMs = $null
    try {
        $ping = Test-Connection -ComputerName perplexity.ai -Count 2 -ErrorAction Stop
        $latencyMs = [math]::Round(($ping | Measure-Object ResponseTime -Average).Average, 0)
    } catch { }

    # Also detect SSD vs HDD (this was missing from v1)
    $mediaType = try {
        $physDisk = Get-PhysicalDisk | Where-Object DeviceId -eq 0 | Select-Object -First 1
        if ($physDisk) { $physDisk.MediaType } else { 'Unknown' }
    } catch { 'Unknown' }

    $result = [ordered]@{
        timestamp_utc        = $startedAt.ToUniversalTime().ToString('o')
        hostname             = $env:COMPUTERNAME
        os                   = "$($os.Caption) $($os.Version) (build $($os.BuildNumber))"
        cpu                  = "$($cpu.Name.Trim()) — $($cpu.NumberOfCores)C/$($cpu.NumberOfLogicalProcessors)T @ $($cpu.MaxClockSpeed)MHz"
        cpu_cores            = $cpu.NumberOfCores
        cpu_threads          = $cpu.NumberOfLogicalProcessors
        cpu_clock_mhz        = $cpu.MaxClockSpeed
        ram_total_gb         = $ram
        ram_free_gb_now      = $freeRamGB
        disk_c_free_gb       = [math]::Round($disk.FreeSpace / 1GB, 1)
        disk_c_total_gb      = [math]::Round($disk.Size / 1GB, 1)
        disk_c_media_type    = $mediaType  # 'SSD', 'HDD', or 'Unknown'
        gpu                  = "$($gpu.Name) — $([math]::Round($gpu.AdapterRAM/1GB,1))GB"
        chromium_procs_count = ($chromeProcs | Measure-Object).Count
        chromium_ram_gb_now  = $chromeRamGB
        python               = $pythonVer
        git                  = $gitVer
        latency_to_pplx_ms   = $latencyMs
    }

    $json = $result | ConvertTo-Json -Depth 3

    # Write latest (overwrites)
    $latestPath = Join-Path $OutDir "machine_audit_latest.json"
    $json | Set-Content -Path $latestPath -Encoding UTF8

    # Append to history (one line per run, for trend analysis)
    $historyPath = Join-Path $OutDir "machine_audit_history.jsonl"
    ($result | ConvertTo-Json -Depth 3 -Compress) | Add-Content -Path $historyPath -Encoding UTF8

    if ($Print) {
        Write-Host ""
        Write-Host "===== MACHINE AUDIT JSON =====" -ForegroundColor Cyan
        Write-Output $json
        Write-Host "===== END =====" -ForegroundColor Cyan
        Write-Host ""
        Write-Host "Also written to: $latestPath" -ForegroundColor Gray
    }
}
catch {
    $errPath = Join-Path $OutDir "machine_audit_errors.log"
    "$($startedAt.ToString('o'))  $($_.Exception.Message)" |
        Add-Content -Path $errPath -Encoding UTF8 -ErrorAction SilentlyContinue
    exit 1
}
exit 0
