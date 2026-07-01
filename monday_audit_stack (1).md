# Monday-morning audit stack — consolidated reference

You now have a full Monday-morning audit pipeline. This doc supersedes the piecemeal runbooks — read this one; the others are still valid for deep dives on individual components.

## The full pipeline

```
Sunday 8:00 PM CT   55ac8fb7   Jarvis sync pre-check          (in-app + push if bad)
                              → appends to sync_precheck_history.jsonl

Monday  6:00 AM CT   4215f7d9   Jarvis funnel weekly tracker   (in-app)
                              → freshness-gated; writes tracker/history.jsonl
                              
Monday  7:00 AM CT   1659eea3   Computer-stack drift audit     (in-app + email)
                              → connectors, scheduled tasks, custom skills
                              
Monday  8:00 AM CT   579dc58b   Resource contention audit      (in-app + email)
                              → machine-level severity, attribution verdict
                              
Monday  8:15 AM CT   e7454d09   Top-3 resource killers         (in-app + email)
                              → process-category ranking, cmdline hints
                              
Monday  8:30 AM CT   a9d6a4bb   Jarvis sync pattern audit      (in-app + email)
                              → historical failure patterns, host attribution
```

**6/15 cloud cron slots used.** Plus 2 local Windows Task Scheduler entries (`MachineTelemetrySampler`, `ProcessTelemetrySampler`, plus your existing sync entry if you haven't disabled it).

## What each audit answers

| Cron | The question it answers |
|---|---|
| Sunday pre-check | "Is my Monday tracker about to fail because sync is stale RIGHT NOW?" |
| Weekly tracker | "What moved in my strategy tiers this week?" |
| Drift audit | "Did any of my connectors or scheduled tasks break silently?" |
| Contention audit | "How much resource pressure did I hit this week, and what drove it?" |
| Top-3 killers | "Which specific processes ate the most RAM × hours?" |
| Sync pattern audit | "Are my sync failures random noise, or is there a pattern the pre-check missed?" |

Read them in order Monday morning. The first two are about the trading pipeline itself; the next four are about the system supporting it.

## Data files that back the audits

All under `OneDrive/jarvis-trader-handoff/`:

| File | Written by | Read by |
|---|---|---|
| `sync_manifest.json` | `Sync-JarvisHandoff.ps1` (local) | Pre-check (55ac8fb7), Tracker (4215f7d9) |
| `telemetry/telemetry_YYYY-MM-DD.csv` | `Sample-MachineTelemetry.ps1` (local, per-minute) | Contention audit (579dc58b), Top-3 killers (e7454d09) |
| `telemetry/processes_YYYY-MM-DD.csv` | `Sample-ProcessTelemetry.ps1` (local, per-5min) | Top-3 killers (e7454d09) |
| `telemetry/sync_precheck_history.jsonl` | Pre-check (55ac8fb7, appends) | Sync pattern audit (a9d6a4bb) |
| `tracker/history.jsonl` | Tracker (4215f7d9, appends) | Sync pattern audit (a9d6a4bb) |
| `cards/strategies_current.jsonl` | Your local jarvis-trader workflow | Tracker (4215f7d9) |
| `verdicts/*__verdict.json` | Your local Phase 3 workflow | Tracker (4215f7d9), Pre-check (55ac8fb7) |

## Local scripts you need to install

In order of importance:

| # | Script | Purpose | Cadence | Priority |
|---|---|---|---|---|
| 1 | `Sync-JarvisHandoff.ps1` | Bridge local → OneDrive, write manifest | After every Phase 2/3 run | **Critical** — without it, tracker blocks |
| 2 | `Sample-MachineTelemetry.ps1` + `Install-TelemetryTask.ps1` | Per-minute machine snapshot | Every 60s via Task Scheduler | **High** — contention audit needs 3+ days of data |
| 3 | `Sample-ProcessTelemetry.ps1` + `Install-ProcessTelemetryTask.ps1` | Per-5min top-N process capture | Every 5min via Task Scheduler | **Medium** — top-3 killers audit needs this |
| 4 | `machine_audit.ps1` | One-shot spec dump | Manual, once | **Low** — I still need this to close the static hardware audit |
| 5 | `analyze_contention.py` | Ad-hoc contention deep-dive | Manual, on-demand | **Low** — same info arrives via email weekly |

## Installation sequence (recommended)

Open elevated PowerShell:

```powershell
cd C:\src\jarvis-trader\scripts   # or wherever you saved the files

# One-shot hardware audit (paste output back to Perplexity)
.\machine_audit.ps1

# Enable the sync bridge
[Environment]::SetEnvironmentVariable('JARVIS_TRADER_ROOT', 'C:\src\jarvis-trader', 'User')
# (test it manually once)
.\Sync-JarvisHandoff.ps1 -Verbose

# Install the two samplers
.\Install-TelemetryTask.ps1
.\Install-ProcessTelemetryTask.ps1
```

Verify:
```powershell
Get-ScheduledTask | Where-Object { $_.TaskName -match 'Telemetry' } |
    Select-Object TaskName, State, LastRunTime
```
Both should show `State = Ready` and a recent `LastRunTime`.

## Expected timeline for useful data

| Time from now | What's meaningful |
|---|---|
| T+0 (right after install) | Sync manifest present; first telemetry samples land |
| T+24h | First contention audit run has real data (3 days recommended for stable statistics; this is the first partial pass) |
| T+1 week (Mon Jul 6) | First full Monday audit stack fires. Tracker + drift + contention + killers all have ≥3 days data. Pattern audit will report "N=1, deferring". |
| T+2 weeks | Contention/killers audits stable. Pattern audit still says "N=2, deferring". |
| T+4 weeks | Pattern audit hits N=4 threshold, starts detecting first patterns. Take with heavy skepticism. |
| T+8 weeks | Pattern detection meaningful — can distinguish real trends from noise. |
| T+12 weeks | You'll know whether the entire stack is worth keeping or should be pruned. |

## Silent-failure modes to watch for

These are things NONE of the audits catch (yet):

- **OneDrive stops syncing but files still write locally.** Manifest updates, all telemetry writes succeed locally, but the cloud never sees them. The pre-check would detect this only if it also checks manifest read via the cloud-facing connector vs local write time. [Inference — not currently implemented.]
- **Telemetry Task Scheduler tasks silently disabled by Windows Defender / SmartScreen.** The audits will say "no telemetry" but you might miss the notification. Add a monthly reminder to run `Get-ScheduledTask -TaskName '*Telemetry*'` manually.
- **Category misclassification.** If you run jarvis-trader without the `jarvis` marker in cmdline, it lands as `python_other`. Not caught by any audit — you'd notice only by reading the killer report and thinking "wait, why is python_jarvis showing 0 hours?"
- **Clock skew.** If your machine's clock drifts significantly, staleness calculations get weird. The audits trust local timestamps.

## Rollback / pause

Any cron: *"Pause cron [id]"* or *"Delete cron [id]"* to Perplexity.

Local task: `Unregister-ScheduledTask -TaskName 'MachineTelemetrySampler' -Confirm:$false` (same for ProcessTelemetrySampler).

To pause ALL monitoring temporarily: pause the 6 cloud crons and the 2 local tasks. Nothing depends on them being on; skipping a week is fine.

## Honest cost estimate

[Inference — based on tool-call shape, not published pricing]

Per week, all 6 crons combined:
- ~80-120 tool calls total
- ~4-6 file writes to OneDrive
- ~4 email sends

Roughly equivalent to a single mid-sized research task. Trivial vs the compute cost of a Phase 3 robustness battery.

Sampler storage on OneDrive: ~150 KB/day for machine telemetry + ~50 KB/day for process telemetry + ~1 KB/week for history logs = **~200 MB/year**. Negligible.

## Honest caveats

- The entire stack assumes OneDrive is your source of truth. If OneDrive is unreliable in your environment, everything downstream is unreliable. Cheapest hedge: also `robocopy` the telemetry dir to a second location weekly.
- The "top-3 killers" categorization is name+cmdline heuristic. If your Databricks setup runs Python via `spark-submit` you'll see it as `python_other` because the Databricks cmdline marker won't match. Tune the category regex in `Sample-ProcessTelemetry.ps1` as you discover misclassifications.
- The sync pattern audit's usefulness is proportional to how often sync actually fails. If your setup is rock-solid you'll get "no patterns" reports forever — that's the correct outcome. Consider pausing it after 12 weeks of clean data.
