# =============================================================================
# Collect-DiskSpool.Tests.ps1  |  roadmap T11 + T10
#
# The seven collectors run on CUSTOMER PRODUCTION GATEWAY HOSTS and had, before
# this suite, zero test coverage AND zero CI static analysis (lint.yml scanned
# only ./scripts). They were the least-verified code in the repository.
#
# This suite tests the shipped artifact AS SHIPPED -- it invokes the .ps1 with
# parameters rather than refactoring the collector into functions to suit the
# test. A collector that only works when restructured for testing is not the
# thing that runs in production.
#
# Filesystem state is REAL (temp directories, real files) rather than mocked.
# Only Get-PSDrive is faked, because free-space scenarios cannot be staged on a
# real volume. Mocking Test-Path/Get-ChildItem globally made the harness
# intercept its own plumbing and tested the test, not the collector.
#
# The failure-mode tests are the reason T10 exists: this collector reported a
# full or inaccessible spool directory as HEALTHY (size 0, no error recorded),
# silently defeating the spool-disk-critical-remediate alert.
# =============================================================================

BeforeAll {
    $script:Collector = (Resolve-Path (Join-Path $PSScriptRoot '..\..\collectors\Collect-DiskSpool.ps1')).Path
    $script:OutRoot = Join-Path ([System.IO.Path]::GetTempPath()) "gwmon_pester_$([guid]::NewGuid().ToString('N').Substring(0,8))"
    [System.IO.Directory]::CreateDirectory($script:OutRoot) | Out-Null

    # Create a real spool directory containing files of known total size.
    function New-SpoolDir {
        param([int[]]$FileSizes = @())
        $dir = Join-Path $script:OutRoot "spool_$([guid]::NewGuid().ToString('N').Substring(0,8))"
        [System.IO.Directory]::CreateDirectory($dir) | Out-Null
        $i = 0
        foreach ($size in $FileSizes) {
            [System.IO.File]::WriteAllBytes((Join-Path $dir "f$i.bin"), (New-Object byte[] $size))
            $i++
        }
        return $dir
    }

    # Invoke the collector and return its parsed JSON output.
    #
    # Uses .NET IO rather than Get-ChildItem/Get-Content deliberately: this helper
    # runs inside the same Pester scope as any mocks, so calling mocked cmdlets
    # here would intercept the harness's own plumbing.
    function Invoke-Collector {
        param([hashtable]$Params = @{})
        $out = Join-Path $script:OutRoot ([guid]::NewGuid().ToString('N').Substring(0, 8))
        [System.IO.Directory]::CreateDirectory($out) | Out-Null
        $splat = @{ OutputPath = $out; ConfigPath = (Join-Path $out 'no-config.json') } + $Params
        & $script:Collector @splat -WarningAction SilentlyContinue | Out-Null
        $file = [System.IO.Directory]::GetFiles($out, 'disk_spool_*.json') | Select-Object -First 1
        if (-not $file) { throw "collector produced no output file in $out" }
        return ([System.IO.File]::ReadAllText($file) | ConvertFrom-Json)
    }

    # CollectionErrors round-trips through JSON as $null when empty.
    function Get-ErrorCount {
        param($Result)
        if ($null -eq $Result.CollectionErrors) { return 0 }
        return @($Result.CollectionErrors).Count
    }
}

AfterAll {
    if (Test-Path $script:OutRoot) {
        Remove-Item $script:OutRoot -Recurse -Force -ErrorAction SilentlyContinue
    }
}

Describe 'Collect-DiskSpool' {

    Context 'disk free-space thresholds' {

        It 'reports OK when free space is above both thresholds' {
            Mock -CommandName Get-PSDrive -MockWith { [pscustomobject]@{ Free = 500GB; Used = 500GB } }
            $r = Invoke-Collector -Params @{ SpoolPath = (New-SpoolDir) }

            $r.AlertLevel | Should -Be 'OK'
            $r.DiskInfo.FreeSpacePct | Should -Be 50
            (Get-ErrorCount $r) | Should -Be 0
        }

        It 'reports WARNING below the warn threshold' {
            Mock -CommandName Get-PSDrive -MockWith { [pscustomobject]@{ Free = 10GB; Used = 90GB } }
            $r = Invoke-Collector -Params @{ SpoolPath = (New-SpoolDir) }
            $r.AlertLevel | Should -Be 'WARNING'
        }

        It 'reports ERROR below the error threshold' {
            Mock -CommandName Get-PSDrive -MockWith { [pscustomobject]@{ Free = 2GB; Used = 98GB } }
            $r = Invoke-Collector -Params @{ SpoolPath = (New-SpoolDir) }
            $r.AlertLevel | Should -Be 'ERROR'
            $r.AlertMessage | Should -Not -BeNullOrEmpty
        }

        It 'honours custom thresholds' {
            Mock -CommandName Get-PSDrive -MockWith { [pscustomobject]@{ Free = 30GB; Used = 70GB } }
            $r = Invoke-Collector -Params @{
                SpoolPath = (New-SpoolDir); WarnThresholdPct = 40.0; ErrorThresholdPct = 20.0
            }
            $r.AlertLevel | Should -Be 'WARNING'
        }

        It 'records a CollectionError when the drive cannot be read' {
            Mock -CommandName Get-PSDrive -MockWith { throw 'Cannot find drive Z' }
            $r = Invoke-Collector -Params @{ SpoolPath = (New-SpoolDir) }
            (Get-ErrorCount $r) | Should -BeGreaterThan 0
        }
    }

    Context 'spool directory measurement' {

        It 'sums the size of spool files' {
            Mock -CommandName Get-PSDrive -MockWith { [pscustomobject]@{ Free = 500GB; Used = 500GB } }
            $r = Invoke-Collector -Params @{ SpoolPath = (New-SpoolDir -FileSizes @(1024, 2048)) }

            $r.SpoolDirSizeBytes | Should -Be 3072
            $r.SpoolPathExists | Should -BeTrue
        }

        It 'reports zero for a genuinely empty spool directory' {
            Mock -CommandName Get-PSDrive -MockWith { [pscustomobject]@{ Free = 500GB; Used = 500GB } }
            $r = Invoke-Collector -Params @{ SpoolPath = (New-SpoolDir) }

            $r.SpoolDirSizeBytes | Should -Be 0
            $r.SpoolPathExists | Should -BeTrue
            (Get-ErrorCount $r) | Should -Be 0 -Because 'an empty spool dir is genuinely healthy'
        }

        # ---------------------------------------------------------------------
        # T10 REGRESSION TESTS
        #
        # These encode the defect that makes spool alerting untrustworthy: the
        # collector could not distinguish "spool directory is empty" from "spool
        # directory is unreadable" or "the configured path is wrong". All three
        # produced SpoolDirSizeBytes = 0, AlertLevel = OK, and an EMPTY
        # CollectionErrors array -- a full or inaccessible spool reported as
        # perfectly healthy, defeating spool-disk-critical-remediate entirely.
        #
        # The default spool path is a hardcoded guess at the PBIEgwService
        # account's AppData, so "the configured path is wrong" is the EXPECTED
        # state on any gateway running under a custom service account.
        # ---------------------------------------------------------------------

        It 'records a CollectionError when the spool path does not exist' {
            Mock -CommandName Get-PSDrive -MockWith { [pscustomobject]@{ Free = 500GB; Used = 500GB } }
            $missing = Join-Path $script:OutRoot 'definitely-not-here\Spooler'

            $r = Invoke-Collector -Params @{ SpoolPath = $missing }

            $r.SpoolPathExists | Should -BeFalse
            (Get-ErrorCount $r) | Should -BeGreaterThan 0 -Because `
            ('a wrong or missing spool path is a COLLECTION FAILURE, not a healthy empty directory. ' +
                'Reporting size 0 with no error makes a misconfigured collector indistinguishable from a healthy one.')
        }

        It 'does not report a size of 0 as healthy when the path is wrong' {
            Mock -CommandName Get-PSDrive -MockWith { [pscustomobject]@{ Free = 500GB; Used = 500GB } }
            $missing = Join-Path $script:OutRoot 'definitely-not-here\Spooler'

            $r = Invoke-Collector -Params @{ SpoolPath = $missing }

            # Either the size is null (unknown) or an error is recorded. What is
            # NOT acceptable is size=0 with a clean bill of health.
            ($null -eq $r.SpoolDirSizeBytes -or (Get-ErrorCount $r) -gt 0) | Should -BeTrue -Because `
                'a measurement that did not happen must never be reported as the number zero.'
        }

        It 'records a CollectionError when the spool directory is unreadable' {
            Mock -CommandName Get-PSDrive -MockWith { [pscustomobject]@{ Free = 500GB; Used = 500GB } }
            # NON-TERMINATING error, deliberately. An earlier version of this test
            # used `throw`, which -ErrorAction SilentlyContinue does not suppress --
            # so it passed against the UNFIXED collector and proved nothing. Access
            # denied on a directory walk is a non-terminating error written to the
            # error stream, and THAT is what SilentlyContinue swallowed. A test
            # that cannot fail against its own defect is not a test.
            #
            # The collector is the only Get-ChildItem caller in this scope (the
            # harness uses .NET IO), so an unfiltered mock is unambiguous.
            Mock -CommandName Get-ChildItem -MockWith {
                Write-Error 'Access to the path is denied.'
            }

            $r = Invoke-Collector -Params @{ SpoolPath = (New-SpoolDir -FileSizes @(4096)) }

            (Get-ErrorCount $r) | Should -BeGreaterThan 0 -Because `
            ('under a non-default gateway service account the spool directory is frequently unreadable. ' +
                '-ErrorAction SilentlyContinue made that identical to an empty directory, so a FULL spool reported 0 bytes and OK.')
            $r.SpoolDirSizeBytes | Should -BeNullOrEmpty -Because `
                'a partial byte count understates spool pressure, which is exactly what the disk alert exists to catch'
        }

        It 'survives an unreadable drive and still writes its output file' {
            # Regression: the summary line dereferenced $result.DiskInfo.FreeSpacePct
            # unconditionally. With DiskInfo $null (drive read failed) that threw
            # under StrictMode -Latest, so the collector exited non-zero and the
            # scheduled task recorded a failure -- discarding the very
            # CollectionError that explained the drive problem.
            Mock -CommandName Get-PSDrive -MockWith { throw 'Cannot find drive Z' }

            { Invoke-Collector -Params @{ SpoolPath = (New-SpoolDir) } } | Should -Not -Throw
        }

        It 'distinguishes not-measured from measured-zero' {
            Mock -CommandName Get-PSDrive -MockWith { [pscustomobject]@{ Free = 500GB; Used = 500GB } }

            $measured = Invoke-Collector -Params @{ SpoolPath = (New-SpoolDir) }
            $unmeasured = Invoke-Collector -Params @{
                SpoolPath = (Join-Path $script:OutRoot 'definitely-not-here\Spooler')
            }

            # The whole point: these two states must not serialise identically.
            $measured.SpoolDirSizeBytes | Should -Be 0
            $unmeasured.SpoolDirSizeBytes | Should -BeNullOrEmpty
            (Get-ErrorCount $measured) | Should -Be 0
            (Get-ErrorCount $unmeasured) | Should -BeGreaterThan 0
        }
    }

    Context 'gateway config probe' {

        It 'records a CollectionError when the config probe fails' {
            Mock -CommandName Get-PSDrive -MockWith { [pscustomobject]@{ Free = 500GB; Used = 500GB } }
            Mock -CommandName Get-Content -MockWith {
                throw [System.UnauthorizedAccessException]::new('denied')
            } -ParameterFilter { $Path -like '*GatewayCore.dll.config' }

            $r = Invoke-Collector -Params @{ SpoolPath = (New-SpoolDir) }

            # Only asserts when the probe actually ran -- the gateway config path
            # is absent on a CI runner, so Test-Path short-circuits before
            # Get-Content. Guarded so this is meaningful on a gateway host and
            # inert elsewhere, rather than falsely green in both.
            if ($r.PSObject.Properties.Name -contains 'ConfigProbeAttempted' -and $r.ConfigProbeAttempted) {
                (Get-ErrorCount $r) | Should -BeGreaterThan 0 -Because `
                ('a failed StreamBeforeRequestCompletes probe means spool metrics may be silently unreliable. ' +
                    'It was logged at Write-Verbose -- invisible under a scheduled task -- and unlike every other ' +
                    'catch block in this script was NOT added to CollectionErrors.')
            }
            else {
                Set-ItResult -Skipped -Because 'gateway config file not present on this host; probe did not run'
            }
        }
    }

    Context 'output contract' {

        It 'emits every field the bronze ingest contract expects' {
            Mock -CommandName Get-PSDrive -MockWith { [pscustomobject]@{ Free = 500GB; Used = 500GB } }
            $r = Invoke-Collector -Params @{ SpoolPath = (New-SpoolDir) }

            # These names are consumed by 01_bronze_ingest.py:ingest_disk_spool
            # and pinned in starter/schemas/table_contracts.json.
            foreach ($f in 'CollectedAtUtc', 'GatewayHostName', 'DiskInfo', 'SpoolDirSizeBytes',
                'AlertLevel', 'AlertMessage', 'StreamBeforeRequestCompletes_Warning', 'CollectionErrors') {
                $r.PSObject.Properties.Name | Should -Contain $f
            }
            foreach ($f in 'DriveLetter', 'FreeSpaceBytes', 'TotalSpaceBytes', 'FreeSpacePct') {
                $r.DiskInfo.PSObject.Properties.Name | Should -Contain $f
            }
        }

        It 'emits a round-trippable ISO-8601 UTC timestamp' {
            Mock -CommandName Get-PSDrive -MockWith { [pscustomobject]@{ Free = 500GB; Used = 500GB } }
            $r = Invoke-Collector -Params @{ SpoolPath = (New-SpoolDir) }
            { [datetime]::Parse($r.CollectedAtUtc) } | Should -Not -Throw
        }
    }
}
