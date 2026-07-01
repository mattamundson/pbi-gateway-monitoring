<#
.SYNOPSIS
    Register Sample-MachineTelemetry.ps1 to run every minute via Task Scheduler.

.DESCRIPTION
    Idempotent — re-running this updates the existing task instead of duplicating.
    Run from an elevated PowerShell prompt (right-click → Run as Administrator)
    because creating scheduled tasks at MachineLevel requires admin.

.PARAMETER ScriptPath
    Full path to Sample-MachineTelemetry.ps1. Default assumes you've placed it
    alongside this installer.

.PARAMETER TaskName
    Default 'MachineTelemetrySampler'

.EXAMPLE
    .\Install-TelemetryTask.ps1
    .\Install-TelemetryTask.ps1 -ScriptPath D:\tools\Sample-MachineTelemetry.ps1
#>

[CmdletBinding()]
param(
    [string]$ScriptPath = (Join-Path $PSScriptRoot 'Sample-MachineTelemetry.ps1'),
    [string]$TaskName   = 'MachineTelemetrySampler'
)

$ErrorActionPreference = 'Stop'

if (-not (Test-Path $ScriptPath)) {
    Write-Error "Sampler script not found: $ScriptPath"
    exit 2
}

# Resolve to absolute path so the task definition is portable across working dirs
$ScriptPath = (Resolve-Path $ScriptPath).Path

$action = New-ScheduledTaskAction -Execute 'powershell.exe' -Argument @(
    '-NoProfile'
    '-NonInteractive'
    '-WindowStyle Hidden'
    '-ExecutionPolicy Bypass'
    '-File'
    "`"$ScriptPath`""
) -Join ' '

# Triggers: at startup + every 1 minute thereafter, indefinitely
$startupTrigger = New-ScheduledTaskTrigger -AtStartup
$startupTrigger.Delay = 'PT2M'  # wait 2 minutes after boot to avoid contention with login

$repeatTrigger = New-ScheduledTaskTrigger -Once -At (Get-Date) `
    -RepetitionInterval (New-TimeSpan -Minutes 1) `
    -RepetitionDuration ([TimeSpan]::FromDays(3650))  # ~10 years

$settings = New-ScheduledTaskSettingsSet `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries `
    -StartWhenAvailable `
    -MultipleInstances IgnoreNew `
    -ExecutionTimeLimit (New-TimeSpan -Minutes 2)

$principal = New-ScheduledTaskPrincipal -UserId "$env:USERDOMAIN\$env:USERNAME" -LogonType S4U -RunLevel Limited

# Remove existing definition if present (idempotent)
if (Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue) {
    Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
    Write-Host "Removed existing task '$TaskName'." -ForegroundColor Yellow
}

Register-ScheduledTask `
    -TaskName $TaskName `
    -Action $action `
    -Trigger @($startupTrigger, $repeatTrigger) `
    -Settings $settings `
    -Principal $principal `
    -Description "Sample machine telemetry every minute for resource contention analysis. Writes CSV to OneDrive." | Out-Null

# Kick off the first run immediately so you can verify
Start-ScheduledTask -TaskName $TaskName

Start-Sleep -Seconds 3

# Verify by checking the CSV exists
$outDir = Join-Path $env:OneDrive "jarvis-trader-handoff\telemetry"
$today  = (Get-Date).ToString('yyyy-MM-dd')
$csv    = Join-Path $outDir "telemetry_$today.csv"

if (Test-Path $csv) {
    $lineCount = (Get-Content $csv | Measure-Object -Line).Lines
    Write-Host ""
    Write-Host "✅ Telemetry task installed and verified." -ForegroundColor Green
    Write-Host "   Task name:    $TaskName"
    Write-Host "   Script:       $ScriptPath"
    Write-Host "   Output CSV:   $csv ($lineCount lines so far)"
    Write-Host "   Cadence:      every 60 seconds"
    Write-Host ""
    Write-Host "To uninstall:  Unregister-ScheduledTask -TaskName '$TaskName' -Confirm:`$false"
} else {
    Write-Warning "Task registered but first sample didn't produce a CSV. Check:"
    Write-Warning "  - Does \$env:OneDrive resolve? (echo \$env:OneDrive)"
    Write-Warning "  - Are you in the 'Performance Monitor Users' group? (whoami /groups)"
    Write-Warning "  - Check error log at $outDir\telemetry_errors.log"
}
