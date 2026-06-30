#requires -Version 7
<#
.SYNOPSIS
    Local PSScriptAnalyzer lint for the kit's PowerShell scripts.

.DESCRIPTION
    Runs PSScriptAnalyzer over ./scripts using the repo's
    PSScriptAnalyzerSettings.psd1. Use this instead of (or alongside) the CI
    workflow — handy when no GitHub Actions runner is available.

    Installs PSScriptAnalyzer for the current user if it's missing.
    Exits 1 if any Error-severity finding is present (warnings are reported
    but non-blocking), so it can also gate a pre-commit hook.

.PARAMETER Path
    Folder to scan. Default: the scripts folder next to this file.

.PARAMETER FailOnWarning
    Also fail (exit 1) on Warning-severity findings, not just Errors.

.EXAMPLE
    pwsh ./scripts/lint.ps1

.EXAMPLE
    pwsh ./scripts/lint.ps1 -FailOnWarning
#>
[CmdletBinding()]
param(
    [string] $Path,
    [switch] $FailOnWarning
)

$ErrorActionPreference = "Stop"

# Resolve paths relative to the repo root (this script lives in ./scripts)
$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Definition
$repoRoot  = Split-Path -Parent $scriptDir
if (-not $Path) { $Path = $scriptDir }
$settings = Join-Path $repoRoot "PSScriptAnalyzerSettings.psd1"

# Ensure analyzer is available
if (-not (Get-Module -ListAvailable -Name PSScriptAnalyzer)) {
    Write-Host "Installing PSScriptAnalyzer (CurrentUser)..." -ForegroundColor Cyan
    Install-Module -Name PSScriptAnalyzer -Force -Scope CurrentUser -AllowClobber
}
Import-Module PSScriptAnalyzer

$invoke = @{ Path = $Path; Recurse = $true }
if (Test-Path $settings) { $invoke.Settings = $settings }
else { Write-Host "[!] Settings file not found ($settings) — using defaults." -ForegroundColor Yellow }

Write-Host "Analyzing $Path ..." -ForegroundColor Cyan
$results = Invoke-ScriptAnalyzer @invoke

if (-not $results) {
    Write-Host "No issues found." -ForegroundColor Green
    exit 0
}

$results |
    Sort-Object Severity, ScriptName, Line |
    Format-Table -AutoSize Severity, ScriptName, Line, RuleName, Message |
    Out-String | Write-Host

$errors   = @($results | Where-Object Severity -eq 'Error')
$warnings = @($results | Where-Object Severity -eq 'Warning')
Write-Host ("Summary: {0} error(s), {1} warning(s), {2} info." -f `
    $errors.Count, $warnings.Count, @($results | Where-Object Severity -eq 'Information').Count) -ForegroundColor Gray

if ($errors.Count -gt 0)               { Write-Host "FAIL: error-severity findings present." -ForegroundColor Red; exit 1 }
if ($FailOnWarning -and $warnings.Count -gt 0) { Write-Host "FAIL: warnings present (-FailOnWarning)." -ForegroundColor Red; exit 1 }
Write-Host "PASS." -ForegroundColor Green
exit 0
