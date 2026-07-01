# Resource Contention Audit — Run-Book

## What this answers

Not "does my machine meet Comet's minimum specs?" That's the static audit (`machine_audit.ps1`).

This answers: **"During my actual peak trading + agent usage hours, what's fighting for resources, and is it Comet, jarvis-trader, or something else?"**

The static audit is a snapshot. This is a continuous time-series.

## Architecture

```
┌─────────────────────────────────────────────────────────┐
│  Sample-MachineTelemetry.ps1                            │
│  Runs every 60s via Windows Task Scheduler              │
│  Writes one CSV row to                                  │
│    OneDrive/jarvis-trader-handoff/telemetry/            │
│      telemetry_YYYY-MM-DD.csv                           │
└─────────────────────────────────────────────────────────┘
                          ↓ samples accumulate
┌─────────────────────────────────────────────────────────┐
│  ON-DEMAND: analyze_contention.py                       │
│  Run locally when you want a deep dive                  │
│  Outputs markdown report + worst-hour forensic CSV      │
└─────────────────────────────────────────────────────────┘
                          ↓
┌─────────────────────────────────────────────────────────┐
│  WEEKLY: cron 579dc58b                                  │
│  Mondays 8:00 AM CT (after Jarvis tracker + drift audit)│
│  Reads telemetry from OneDrive, classifies, emails      │
│  digest with peak hours + attribution verdict           │
└─────────────────────────────────────────────────────────┘
```

## What we sample and why

These are **contention signals**, not vanity metrics:

| Signal | Why it matters |
|---|---|
| `cpu_queue` | Threads waiting for CPU. >2 = real contention, not just high utilization. |
| `ram_avail_gb` | Windows' "memory pressure" sense. <2GB = paging risk imminent. |
| `ram_committed_pct` | Commit charge. >85% = already swapping. |
| `disk_queue` | I/O queue depth. >2 = real disk-bound contention. Critical for Phase 3 backtests reading bars. |
| `disk_pct` | % time disk busy. |
| `net_kbps_in/out` | Network throughput. Surfaces IBKR streaming + Databricks pulls + agent traffic. |
| `chrome_ram_gb` | Chrome/Edge/Comet RAM sum — the "is Comet eating my machine" signal. |
| `python_ram_gb` | Python RAM sum — the "is jarvis-trader eating my machine" signal. |
| `top3_procs` | Top 3 by RSS, for forensics when neither Chrome nor Python dominate. |

## Setup (one-time, ~5 minutes)

1. Save both `Sample-MachineTelemetry.ps1` and `Install-TelemetryTask.ps1` to the same directory. Recommended: `C:\src\jarvis-trader\scripts\` so they're versioned with your repo.

2. **Open PowerShell as Administrator** (required to register a scheduled task).

3. Run the installer:
   ```powershell
   cd C:\src\jarvis-trader\scripts
   .\Install-TelemetryTask.ps1
   ```

4. Verify. The installer kicks off the first sample immediately and confirms:
   ```
   ✅ Telemetry task installed and verified.
      Task name:    MachineTelemetrySampler
      Output CSV:   C:\Users\matt\OneDrive\jarvis-trader-handoff\telemetry\telemetry_2026-06-30.csv (2 lines so far)
      Cadence:      every 60 seconds
   ```

5. Wait ~5 minutes, then check the CSV grew:
   ```powershell
   Get-Content "$env:OneDrive\jarvis-trader-handoff\telemetry\telemetry_*.csv" |
       Measure-Object -Line
   ```
   Should show 5+ lines. If not, see Troubleshooting below.

## Using the data

### Weekly automatic email (cron `579dc58b`)
First report arrives Monday morning at 8:00 AM CT after at least 3 days of samples. Will be very thin the first week; more useful starting week 2.

### Ad-hoc deep dive (anytime)
```powershell
# Default: last 7 days
python C:\src\jarvis-trader\scripts\analyze_contention.py

# Last 14 days
python analyze_contention.py --days 14

# Different telemetry directory
python analyze_contention.py --telemetry-dir "D:\backup\telemetry"
```

Outputs:
- `contention_report_YYYY-MM-DD.md` — markdown report you can read
- `worst_hour_YYYY-MM-DD_HH.csv` — full sample dump of the single worst hour, for forensics

### Reading the attribution verdict

The cloud email and local report both produce one of four verdicts:

| Verdict | What it means | Action |
|---|---|---|
| `python_dominant` | jarvis-trader is your contention source | Surface choice (Comet vs web app) is moot. Options: more RAM, better backtest parallelism, throttle Phase 3 concurrency, or move heavy backtests to off-hours. |
| `chromium_dominant` | Comet / Chrome is your contention source | Route more agent work to the web-app cloud surface. Close unused Chrome windows. Audit your Comet extensions for memory hogs. |
| `both_significant` | They're both eating you alive | Don't run Phase 3 + Comet agent tasks simultaneously. Or buy RAM (16 → 32 GB is the cheapest path). |
| `neither_dominant_check_top3_procs` | Something else (Databricks, Docker, IBKR TWS, OneDrive itself) is the culprit | Open the worst-hour CSV; `top3_procs` column names names. |

## Tuning thresholds

The defaults are sensible for a 16-32 GB Windows dev box. If you're on a 64+ GB workstation, the RAM thresholds are too low (you'll never see RED). If you're on 8 GB, they're too high.

**Adjust in two places (keep them synced):**

1. `analyze_contention.py` — top of file, the `THRESHOLDS` block
2. Cron `579dc58b` task prompt — the "STEP 2: PARSE AND CLASSIFY" section

If you change one without the other, your local report and your weekly email will disagree. The drift audit (cron `1659eea3`) doesn't currently catch this — worth adding to its scope later if it becomes a problem.

## Cost / footprint

| Resource | Cost |
|---|---|
| CPU during sample (~2s every 60s) | ~3% of one core average. Negligible. |
| Disk for daily CSV | ~150 KB/day, ~55 MB/year. Negligible. |
| OneDrive sync bandwidth | One file rewrite per minute — small (~10 KB) but constant. If you're on a metered connection, consider pointing `-OutDir` to a local-only folder and only syncing weekly. |
| Cloud cron credits | ~10-15 tool calls per weekly run. Light. |

## Troubleshooting

### No CSV being created after install

Check the error log:
```powershell
Get-Content "$env:OneDrive\jarvis-trader-handoff\telemetry\telemetry_errors.log" -Tail 20
```

Common causes:
- **Performance counter access denied** — add yourself to the `Performance Monitor Users` local group:
  ```powershell
  net localgroup "Performance Monitor Users" "$env:USERNAME" /add
  ```
  Then log out and back in.
- **`$env:OneDrive` is empty** — OneDrive isn't configured for your user. Either configure it or pass `-OutDir` explicitly when invoking the sampler.
- **Task is registered but not running** — check the History tab in Task Scheduler. If it says "Last result: 0x1" it's a script error; if "Last run: never" the trigger isn't firing.

### Task Scheduler shows the task as disabled
Re-run `Install-TelemetryTask.ps1` from an elevated prompt. It's idempotent — re-running deletes and recreates cleanly.

### Samples missing for a period
The script writes daily CSVs. Missing days mean:
- Machine was off / asleep (expected)
- Task got disabled (check Task Scheduler)
- OneDrive sync paused and you're looking at the cloud view (check local file)
- Disk full or quota hit (check error log)

## Rollback

### Uninstall the sampler
```powershell
Unregister-ScheduledTask -TaskName 'MachineTelemetrySampler' -Confirm:$false
```

CSV files stay where they are — delete the `telemetry/` folder if you want them gone.

### Pause the weekly email
Tell Perplexity: *"Pause cron 579dc58b"* or *"Delete cron 579dc58b"*

### Keep telemetry off OneDrive
Edit `Install-TelemetryTask.ps1` and change the `-OutDir` default to a local path like `C:\telemetry`. The weekly cloud cron will then report "no telemetry" — disable the cron in that case, or set up a separate mechanism to sync the CSVs.

## All active scheduled tasks now

| ID | Name | Cadence | Surface |
|---|---|---|---|
| `MachineTelemetrySampler` | Per-minute sampler | 60s | Windows Task Scheduler |
| `55ac8fb7` | Jarvis sync pre-check | Sun 8:00 PM CT | Cloud |
| `4215f7d9` | Jarvis funnel weekly tracker | Mon 6:00 AM CT | Cloud |
| `1659eea3` | Computer-stack drift audit | Mon 7:00 AM CT | Cloud |
| `579dc58b` | Weekly contention audit | Mon 8:00 AM CT | Cloud |

Cloud crons: 4/15 slots used.

## Honest caveats

- [Inference] The 100 Mbps network cap in the threshold defaults is a guess. If your link is 1 Gbps you'll basically never trip net_pressure; if it's a metered 4G hotspot you'll trip it constantly. Set `NET_CAP_MBPS` in `analyze_contention.py` to your real link speed.
- [Unverified] Performance counter availability varies slightly between Windows builds. `\System\Processor Queue Length` is sometimes reported as 0 even under load on Windows 11 — if your samples consistently show `cpu_queue=0` even during a Phase 3 run, the counter isn't working and you'll get false-clean CPU readings. Workaround: rely on `cpu_pct` alone in the threshold logic.
- [Inference] The attribution heuristic compares mean Chrome/Python RAM during RED vs GREEN samples. It's directional, not causal. A correlation between RED events and high Python RAM doesn't prove Python caused the contention — it could be that whenever you run Python you also have Chrome open. The forensic CSV gives you the per-sample top-3 processes to disambiguate.
- The sampler captures **what's running**, not **what triggered the spike**. If you want event-driven correlation (e.g. "this hour was bad because the IBKR feed had a buffer flush"), you'd need application-level logging on top of this.
