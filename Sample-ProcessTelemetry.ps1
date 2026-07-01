<#
.SYNOPSIS
    Per-process telemetry sample. Captures top-N processes by RSS.
    Companion to Sample-MachineTelemetry.ps1 — runs at a slower cadence
    (default every 5 minutes) since per-process detail is only useful
    for identifying sustained hogs.

.DESCRIPTION
    Writes one CSV row PER PROCESS (in the top-N) per sample to a daily
    rotating file. Each row includes:

    - ts_utc / ts_local
    - process_name        (e.g. 'chrome', 'python')
    - pid
    - rss_gb              (Working Set in GB)
    - cpu_pct             (CPU % since last snapshot for this PID)
    - category            (mapped to a semantic group)
    - cmdline_hint        (first 200 chars of command line, if available)

    Categories are inferred by process name to answer "was it Comet or a
    stale REPL?" without ambiguity:

        browser_comet     : comet
        browser_chrome    : chrome
        browser_edge      : msedge
        browser_other     : brave, firefox, opera
        python_jarvis     : python/pythonw with 'jarvis' or 'jarvis-trader' in cmdline
        python_databricks : python with 'databricks' in cmdline
        python_other      : python without a matched marker
        trading_tws       : tws, ibgateway, javaw with 'jts' hint
        docker            : docker, com.docker.*
        vscode            : code
        onedrive          : onedrive
        other             : everything else in top-N

    The category mapping is deliberately conservative — 'python_jarvis'
    requires a cmdline marker so a bare `python` REPL doesn't get
    misattributed. If cmdline access fails, it falls back to python_other.

.PARAMETER TopN
    Number of processes to capture per sample. Default 10.

.PARAMETER OutDir
    Default: $env:OneDrive\jarvis-trader-handoff\telemetry
#>

[CmdletBinding()]
param(
    [int]$TopN = 10,
    [string]$OutDir = (Join-Path $env:OneDrive "jarvis-trader-handoff\telemetry")
)

$ErrorActionPreference = 'Continue'
$sampleStart = Get-Date

try {
    if (-not (Test-Path $OutDir)) { New-Item -ItemType Directory -Path $OutDir -Force | Out-Null }

    # Fetch all processes with RSS > 50MB — enough headroom to guarantee we get
    # top-N even if some are excluded for permission errors
    $procs = Get-Process | Where-Object { $_.WorkingSet64 -gt 50MB } |
             Sort-Object WorkingSet64 -Descending |
             Select-Object -First ($TopN * 2)  # oversample; we'll trim after cmdline enrichment

    # Fetch cmdlines via CIM (Get-Process doesn't expose them reliably)
    $pidList = $procs.Id
    $cimProcs = @{}
    try {
        Get-CimInstance Win32_Process -Filter (
            '(' + (($pidList | ForEach-Object { "ProcessId=$_" }) -join ' OR ') + ')'
        ) -ErrorAction Stop | ForEach-Object {
            $cimProcs[[int]$_.ProcessId] = $_.CommandLine
        }
    } catch {
        # Permission denied on some PIDs is fine — we'll skip cmdline for those
    }

    function Get-Category {
        param([string]$Name, [string]$Cmdline)
        $n = $Name.ToLower()
        $c = if ($Cmdline) { $Cmdline.ToLower() } else { '' }

        switch -Regex ($n) {
            '^comet$'                       { return 'browser_comet' }
            '^chrome$'                      { return 'browser_chrome' }
            '^msedge$'                      { return 'browser_edge' }
            '^(brave|firefox|opera)$'       { return 'browser_other' }
            '^(python|pythonw|py)$'         {
                if ($c -match 'jarvis[-_]?trader|jarvis_funnel|\bjarvis\b') { return 'python_jarvis' }
                if ($c -match 'databricks|pyspark')                        { return 'python_databricks' }
                return 'python_other'
            }
            '^(tws|ibgateway)$'             { return 'trading_tws' }
            '^javaw?$'                      {
                if ($c -match 'jts|ibgateway|tws') { return 'trading_tws' }
                return 'other'
            }
            '^(docker|com\.docker)'         { return 'docker' }
            '^code$'                        { return 'vscode' }
            '^onedrive$'                    { return 'onedrive' }
            default                         { return 'other' }
        }
    }

    # Build rows with category enrichment, then take real top-N by RSS
    $rows = foreach ($p in $procs) {
        $cmdline = $cimProcs[$p.Id]
        $category = Get-Category -Name $p.ProcessName -Cmdline $cmdline
        [PSCustomObject]@{
            ts_utc        = $sampleStart.ToUniversalTime().ToString('o')
            ts_local      = $sampleStart.ToString('yyyy-MM-ddTHH:mm:ss')
            hostname      = $env:COMPUTERNAME
            process_name  = $p.ProcessName
            pid           = $p.Id
            rss_gb        = [math]::Round($p.WorkingSet64 / 1GB, 3)
            cpu_seconds   = [math]::Round($p.CPU, 1)  # cumulative CPU — deltas computed downstream
            category      = $category
            cmdline_hint  = if ($cmdline) { $cmdline.Substring(0, [Math]::Min(200, $cmdline.Length)) } else { '' }
        }
    }

    $rows = $rows | Sort-Object rss_gb -Descending | Select-Object -First $TopN

    $dayStamp = $sampleStart.ToString('yyyy-MM-dd')
    $csvPath  = Join-Path $OutDir "processes_$dayStamp.csv"
    $needsHeader = -not (Test-Path $csvPath)

    if ($needsHeader) {
        $rows | Export-Csv -Path $csvPath -NoTypeInformation -Encoding UTF8
    } else {
        $rows | Export-Csv -Path $csvPath -NoTypeInformation -Encoding UTF8 -Append
    }
}
catch {
    $errPath = Join-Path $OutDir "processes_errors.log"
    "$($sampleStart.ToString('o'))  $($_.Exception.Message)" |
        Add-Content -Path $errPath -Encoding UTF8 -ErrorAction SilentlyContinue
}
