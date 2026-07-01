# machine_audit.ps1
# Self-audit for Comet desktop suitability + general dev-machine health.
# Run from PowerShell. No admin required.
# Paste the bottom JSON block back into the chat.

$ErrorActionPreference = 'SilentlyContinue'

$os   = Get-CimInstance Win32_OperatingSystem
$cs   = Get-CimInstance Win32_ComputerSystem
$cpu  = Get-CimInstance Win32_Processor | Select-Object -First 1
$ram  = [math]::Round($cs.TotalPhysicalMemory / 1GB, 1)
$disk = Get-CimInstance Win32_LogicalDisk -Filter "DeviceID='C:'"
$gpu  = Get-CimInstance Win32_VideoController | Select-Object -First 1

# Free RAM right now (gives a realistic picture of headroom while you're working)
$freeRamGB = [math]::Round($os.FreePhysicalMemory / 1MB, 1)

# Currently running Chromium-family processes — tells us if Chrome is already
# eating your RAM and Comet would be additive.
$chromeProcs = Get-Process chrome,msedge,brave,comet -ErrorAction SilentlyContinue
$chromeRamGB = if ($chromeProcs) {
    [math]::Round(($chromeProcs | Measure-Object WorkingSet64 -Sum).Sum / 1GB, 2)
} else { 0 }

# Python / git presence (relevant for the Jarvis local CLI workloads)
$pythonVer = (python --version 2>&1) -as [string]
$gitVer    = (git --version    2>&1) -as [string]

# Network sanity — latency to Perplexity (rough)
$ping = Test-Connection -ComputerName perplexity.ai -Count 2 -Quiet:$false -ErrorAction SilentlyContinue |
        Measure-Object -Property ResponseTime -Average
$latencyMs = if ($ping) { [math]::Round($ping.Average, 0) } else { $null }

$result = [ordered]@{
    timestamp_utc        = (Get-Date).ToUniversalTime().ToString('o')
    hostname             = $env:COMPUTERNAME
    os                   = "$($os.Caption) $($os.Version) (build $($os.BuildNumber))"
    cpu                  = "$($cpu.Name.Trim()) — $($cpu.NumberOfCores)C/$($cpu.NumberOfLogicalProcessors)T @ $($cpu.MaxClockSpeed)MHz"
    ram_total_gb         = $ram
    ram_free_gb_now      = $freeRamGB
    disk_c_free_gb       = [math]::Round($disk.FreeSpace / 1GB, 1)
    disk_c_total_gb      = [math]::Round($disk.Size / 1GB, 1)
    gpu                  = "$($gpu.Name) — $([math]::Round($gpu.AdapterRAM/1GB,1))GB"
    chromium_procs_count = ($chromeProcs | Measure-Object).Count
    chromium_ram_gb_now  = $chromeRamGB
    python               = $pythonVer
    git                  = $gitVer
    latency_to_pplx_ms   = $latencyMs
}

Write-Host ""
Write-Host "===== PASTE THIS BLOCK BACK INTO PERPLEXITY =====" -ForegroundColor Cyan
$result | ConvertTo-Json -Depth 3
Write-Host "===== END =====" -ForegroundColor Cyan
