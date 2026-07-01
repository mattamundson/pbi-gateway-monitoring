<#
.SYNOPSIS
    Register Sample-ProcessTelemetry.ps1 to run every 5 minutes via Task Scheduler.
    Companion to Install-TelemetryTask.ps1 — the two schedulers coexist.

.DESCRIPTION
    Idempotent. Requires elevated PowerShell.

.PARAMETER ScriptPath
    Full path to Sample-ProcessTelemetry.ps1. Default assumes it's alongside this installer.

.PARAMETER TaskName
    Default 'ProcessTelemetrySampler'

.PARAMETER IntervalMinutes
    Sample cadence. Default 5.
#>

[CmdletBinding()]
param(
    [string]$ScriptPath = (Join-Path $PSScriptRoot 'Sample-ProcessTelemetry.ps1'),
    [string]$TaskName   = 'ProcessTelemetrySampler',
    [int]$IntervalMinutes = 5
)

$ErrorActionPreference = 'Stop'

if (-not (Test-Path $ScriptPath)) {
    Write-Error "Sampler script not found: $ScriptPath"
    exit 2
}
$ScriptPath = (Resolve-Path $ScriptPath).Path

$action = New-ScheduledTaskAction -Execute 'powershell.exe' -Argument (@(
    '-NoProfile'
    '-NonInteractive'
    '-WindowStyle Hidden'
    '-ExecutionPolicy Bypass'
    '-File'
    "`"$ScriptPath`""
) -Join ' ')

$startupTrigger = New-ScheduledTaskTrigger -AtStartup
$startupTrigger.Delay = 'PT3M'  # wait 3 minutes after boot

$repeatTrigger = New-ScheduledTaskTrigger -Once -At (Get-Date) `
    -RepetitionInterval (New-TimeSpan -Minutes $IntervalMinutes) `
    -RepetitionDuration ([TimeSpan]::FromDays(3650))

$settings = New-ScheduledTaskSettingsSet `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries `
    -StartWhenAvailable `
    -MultipleInstances IgnoreNew `
    -ExecutionTimeLimit (New-TimeSpan -Minutes 3)

$principal = New-ScheduledTaskPrincipal -UserId "$env:USERDOMAIN\$env:USERNAME" -LogonType S4U -RunLevel Limited

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
    -Description "Sample top-N processes every $IntervalMinutes minutes for weekly resource-killer identification." | Out-Null

Start-ScheduledTask -TaskName $TaskName
Start-Sleep -Seconds 3

$outDir = Join-Path $env:OneDrive "jarvis-trader-handoff\telemetry"
$today  = (Get-Date).ToString('yyyy-MM-dd')
$csv    = Join-Path $outDir "processes_$today.csv"

if (Test-Path $csv) {
    $lineCount = (Get-Content $csv | Measure-Object -Line).Lines
    Write-Host ""
    Write-Host "✅ Process telemetry task installed." -ForegroundColor Green
    Write-Host "   Task name:  $TaskName"
    Write-Host "   Script:     $ScriptPath"
    Write-Host "   Output CSV: $csv ($lineCount lines so far)"
    Write-Host "   Cadence:    every $IntervalMinutes minutes, top 10 processes per sample"
} else {
    Write-Warning "Task registered but first sample didn't produce a CSV. Check $outDir\processes_errors.log"
}
