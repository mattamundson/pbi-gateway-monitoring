# =============================================================================
# CollectorHealth.ps1  |  Label: [NET-NEW]  |  roadmap T10
#
# Shared health-sidecar writer, dot-sourced by every collector.
#
# WHY THIS EXISTS
#   Five of the seven collectors built a $CollectionErrors array with care, and
#   nothing anywhere read it. Every bronze ingest function selected the payload
#   columns and dropped the error channel. A collector that had been failing for
#   weeks -- wrong spool path, denied Security event log, expired service
#   principal, unreadable log directory -- still wrote a JSON file, still landed
#   rows in bronze, and was INDISTINGUISHABLE from a healthy one.
#
#   The monitoring tool could not monitor itself. That is the worst class of
#   defect an observability product can ship, because it fails in the direction
#   of false confidence: the dashboard is green precisely because the collector
#   is blind.
#
# WHY A SIDECAR RATHER THAN A WRAPPER FIELD
#   The seven collectors emit seven different JSON shapes (single object, array
#   of records, NDJSON). Adding a uniform health wrapper would change every
#   payload shape and every bronze ingest function that parses it. A sidecar is
#   additive: one uniform file per run, parsed by ONE ingest function, with zero
#   blast radius on existing payload parsing.
#
# THE THREE STATES IT DISTINGUISHES
#   OK      - ran, produced records, no errors
#   DEGRADED- ran, but recorded errors OR produced zero records
#   (absent)- produced no sidecar at all: the collector did not run. This is the
#             state that matters most and the one no payload table can express,
#             because a dead collector's failure signature is the ABSENCE of
#             rows -- identical to a quiet gateway. It is caught downstream by
#             comparing against COLLECTOR_ROSTER in 01_bronze_ingest.py.
#
# Deliberately dependency-free and failure-tolerant: health reporting must never
# be the thing that breaks a collection run.
# =============================================================================

function Write-CollectorHealth {
    [CmdletBinding()]
    param(
        # Must match a name in COLLECTOR_ROSTER (01_bronze_ingest.py) or the run
        # will be reported as a roster gap even though the collector ran.
        [Parameter(Mandatory = $true)]
        [string]$CollectorName,

        [Parameter(Mandatory = $true)]
        [string]$OutputPath,

        # Errors the collector accumulated. Empty array = ran clean.
        [Parameter(Mandatory = $false)]
        [string[]]$CollectionErrors = @(),

        # How many payload records this run produced. $null means the collector
        # does not track a count; 0 means it genuinely produced none, which is a
        # DEGRADED signal -- a collector that matches nothing is usually
        # misconfigured, not idle.
        [Parameter(Mandatory = $false)]
        [Nullable[int]]$RecordCount = $null,

        # Free-form per-collector context (paths probed, filters applied). Kept
        # as a string map so the sidecar schema never has to change.
        [Parameter(Mandatory = $false)]
        [hashtable]$Context = @{},

        [Parameter(Mandatory = $false)]
        [datetime]$CollectedAtUtc = ((Get-Date).ToUniversalTime())
    )

    try {
        $errs = @($CollectionErrors | Where-Object { -not [string]::IsNullOrWhiteSpace($_) })

        $status = if ($errs.Count -gt 0) { "DEGRADED" }
        elseif ($null -ne $RecordCount -and $RecordCount -eq 0) { "DEGRADED" }
        else { "OK" }

        $health = [ordered]@{
            CollectorName    = $CollectorName
            CollectedAtUtc   = $CollectedAtUtc.ToString("o")
            GatewayHostName  = $env:COMPUTERNAME
            Status           = $status
            ErrorCount       = $errs.Count
            CollectionErrors = $errs
            RecordCount      = $RecordCount
            # Stamped so a sidecar written by an old collector version is
            # identifiable after a partial gateway-host upgrade.
            SchemaVersion    = 1
            Context          = $Context
        }

        if (-not (Test-Path $OutputPath)) {
            New-Item -ItemType Directory -Path $OutputPath -Force | Out-Null
        }

        $stamp = $CollectedAtUtc.ToString("yyyyMMdd_HHmmss")
        $file = Join-Path $OutputPath "collector_health_${CollectorName}_${stamp}.json"
        $health | ConvertTo-Json -Depth 6 | Out-File $file -Encoding UTF8

        Write-Verbose "Collector health: $CollectorName=$status ($($errs.Count) error(s)) -> $file"
    }
    catch {
        # Never let health reporting fail the collection it is reporting on. A
        # lost sidecar degrades to the "did not run" state, which is already the
        # loudest alert -- strictly safer than aborting a working collector.
        Write-Warning "Could not write collector health sidecar for ${CollectorName}: $_"
    }
}
