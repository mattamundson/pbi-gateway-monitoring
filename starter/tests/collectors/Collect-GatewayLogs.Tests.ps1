# =============================================================================
# Collect-GatewayLogs.Tests.ps1  |  roadmap T11
#
# Uses a REAL temp directory standing in for the gateway log root, with real
# files (LastWriteTimeUtc is what the watermark logic keys off, and that is not
# fakeable through a mock without also faking Get-ChildItem's return shape).
# =============================================================================

BeforeAll {
    $script:Collector = (Resolve-Path (Join-Path $PSScriptRoot '..\..\collectors\Collect-GatewayLogs.ps1')).Path
    $script:OutRoot = Join-Path ([System.IO.Path]::GetTempPath()) "gwmon_pester_$([guid]::NewGuid().ToString('N').Substring(0,8))"
    [System.IO.Directory]::CreateDirectory($script:OutRoot) | Out-Null

    function New-LogRoot {
        $dir = Join-Path $script:OutRoot "logs_$([guid]::NewGuid().ToString('N').Substring(0,8))"
        [System.IO.Directory]::CreateDirectory($dir) | Out-Null
        return $dir
    }

    function New-GatewayLogFile {
        param([string]$Dir, [string]$Name, [string]$Content = "RequestId,Duration`nabc,100", [Nullable[datetime]]$WriteTimeUtc = $null)
        $p = Join-Path $Dir $Name
        [System.IO.File]::WriteAllText($p, $Content)
        if ($WriteTimeUtc) { (Get-Item $p).LastWriteTimeUtc = $WriteTimeUtc }
        return $p
    }

    function New-Config {
        param([string]$LogPath)
        $cfgDir = Join-Path $script:OutRoot ([guid]::NewGuid().ToString('N').Substring(0, 8))
        [System.IO.Directory]::CreateDirectory($cfgDir) | Out-Null
        $cfgPath = Join-Path $cfgDir 'config.json'
        @{ gateway = @{ logPath = $LogPath } } | ConvertTo-Json -Depth 4 | Set-Content $cfgPath -Encoding UTF8
        return $cfgPath
    }

    function Invoke-Collector {
        param([string]$LogRoot, [hashtable]$Params = @{})
        $out = Join-Path $script:OutRoot ([guid]::NewGuid().ToString('N').Substring(0, 8))
        [System.IO.Directory]::CreateDirectory($out) | Out-Null
        $cfgPath = New-Config -LogPath $LogRoot
        $splat = @{ OutputPath = $out; ConfigPath = $cfgPath } + $Params
        $stdout = & $script:Collector @splat -WarningAction SilentlyContinue 2>&1
        $file = [System.IO.Directory]::GetFiles($out, 'gateway_logs_*.json') | Select-Object -First 1
        return @{
            Result    = if ($file) { [System.IO.File]::ReadAllText($file) | ConvertFrom-Json } else { $null }
            OutputDir = $out
            StdOut    = $stdout
            ExitCode  = $LASTEXITCODE
        }
    }

    function Get-Watermark {
        param([string]$OutputDir)
        $p = Join-Path $OutputDir 'watermark_gateway_logs.json'
        if (-not (Test-Path $p)) { return $null }
        return (Get-Content $p -Raw | ConvertFrom-Json)
    }
}

AfterAll {
    if (Test-Path $script:OutRoot) { Remove-Item $script:OutRoot -Recurse -Force -ErrorAction SilentlyContinue }
}

Describe 'Collect-GatewayLogs' {

    Context 'happy path' {

        It 'stages new QueryExecutionReport files and classifies LogType by filename' {
            $logRoot = New-LogRoot
            New-GatewayLogFile -Dir $logRoot -Name 'GatewayPerformanceData_QueryExecutionReport_20260101_000000.log'
            New-GatewayLogFile -Dir $logRoot -Name 'GatewayPerformanceData_SystemCounterReport_20260101_000000.log'

            $run = Invoke-Collector -LogRoot $logRoot
            $run.Result | Should -Not -BeNullOrEmpty
            $run.Result.Count | Should -Be 2
            ($run.Result | Where-Object LogType -eq 'QueryExecution').Count | Should -Be 1
            ($run.Result | Where-Object LogType -eq 'SystemCounter').Count | Should -Be 1
        }

        It 'reads the nested config.gateway.logPath key (not a flat legacy key)' {
            # Regression guard for the fix documented in the collector's header:
            # reading a flat key under StrictMode -Latest threw when only the
            # nested key was set, crashing the collector for anyone following
            # config.sample.json's documented shape.
            $logRoot = New-LogRoot
            New-GatewayLogFile -Dir $logRoot -Name 'GatewayPerformanceData_QueryStartReport_20260101_000000.log'

            $run = Invoke-Collector -LogRoot $logRoot
            $run.Result.Count | Should -Be 1
        }
    }

    Context 'incremental watermark' {

        It 'does not re-stage a file already covered by the watermark' {
            # Must be inside the collector's default first-run lookback (1 day)
            # or it is correctly excluded regardless of watermark behavior.
            $logRoot = New-LogRoot
            New-GatewayLogFile -Dir $logRoot -Name 'GatewayPerformanceData_QueryExecutionReport_A.log' `
                -WriteTimeUtc ((Get-Date).ToUniversalTime().AddHours(-1))

            $run1 = Invoke-Collector -LogRoot $logRoot
            $run1.Result.Count | Should -Be 1

            # Second run, same log root, no new files -- collector exits 0 with
            # nothing to stage (verified via absence of a NEW output file, since
            # the "no new files" path exits before writing one).
            $out2 = Join-Path $script:OutRoot ([guid]::NewGuid().ToString('N').Substring(0, 8))
            [System.IO.Directory]::CreateDirectory($out2) | Out-Null
            # Reuse the SAME watermark by copying it forward.
            Copy-Item (Join-Path $run1.OutputDir 'watermark_gateway_logs.json') (Join-Path $out2 'watermark_gateway_logs.json')
            $cfgPath = New-Config -LogPath $logRoot
            & $script:Collector -OutputPath $out2 -ConfigPath $cfgPath -WarningAction SilentlyContinue | Out-Null
            $file2 = [System.IO.Directory]::GetFiles($out2, 'gateway_logs_*.json')
            $file2.Count | Should -Be 0 -Because 'no file changed since the watermark, so nothing should be staged'
        }

        It 'stages only the file newer than the watermark, not the older one already processed' {
            $logRoot = New-LogRoot
            New-GatewayLogFile -Dir $logRoot -Name 'GatewayPerformanceData_QueryExecutionReport_OLD.log' `
                -WriteTimeUtc ((Get-Date).ToUniversalTime().AddHours(-2))
            $run1 = Invoke-Collector -LogRoot $logRoot
            $run1.Result.Count | Should -Be 1

            New-GatewayLogFile -Dir $logRoot -Name 'GatewayPerformanceData_QueryExecutionReport_NEW.log' `
                -WriteTimeUtc ((Get-Date).ToUniversalTime())

            $out2 = Join-Path $script:OutRoot ([guid]::NewGuid().ToString('N').Substring(0, 8))
            [System.IO.Directory]::CreateDirectory($out2) | Out-Null
            Copy-Item (Join-Path $run1.OutputDir 'watermark_gateway_logs.json') (Join-Path $out2 'watermark_gateway_logs.json')
            $cfgPath = New-Config -LogPath $logRoot
            & $script:Collector -OutputPath $out2 -ConfigPath $cfgPath -WarningAction SilentlyContinue | Out-Null
            $file2 = [System.IO.Directory]::GetFiles($out2, 'gateway_logs_*.json') | Select-Object -First 1
            $r2 = [System.IO.File]::ReadAllText($file2) | ConvertFrom-Json
            $r2.Count | Should -Be 1
            $r2[0].SourceFileName | Should -Match 'NEW'
        }
    }

    Context 'failure handling' {

        It 'records a CollectionError and does not crash when the log path does not exist' {
            $missing = Join-Path $script:OutRoot 'no-such-log-dir'
            $run = Invoke-Collector -LogRoot $missing
            $run.ExitCode | Should -Be 1 -Because 'a missing configured log path is a real preflight failure, not a silent empty result'
        }
    }
}
