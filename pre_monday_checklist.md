# Pre-Monday Configuration Gap Checklist

Walk this once before Monday July 6. Should take 15-20 minutes if everything is set up; longer if you catch gaps.

Order matters. Each section depends on the previous ones passing.

---

## Gap 1: Path assumptions I baked in

Every script and cron I wrote assumes these paths. Verify each — one wrong path silently breaks the entire stack.

```powershell
# 1a. jarvis-trader repo root
$env:JARVIS_TRADER_ROOT
# Expected: something like C:\src\jarvis-trader
# If empty or wrong:
[Environment]::SetEnvironmentVariable('JARVIS_TRADER_ROOT', 'C:\src\jarvis-trader', 'User')
# Restart PowerShell after setting.

# 1b. OneDrive root — is $env:OneDrive populated?
$env:OneDrive
# Expected: C:\Users\matt\OneDrive or similar
# If empty: OneDrive client isn't configured for your Windows user. Sign in first.

# 1c. Does the handoff folder exist yet?
Test-Path (Join-Path $env:OneDrive "jarvis-trader-handoff")
# If False: create it now. Empty is fine — scripts will populate it.
New-Item -ItemType Directory -Path (Join-Path $env:OneDrive "jarvis-trader-handoff") -Force

# 1d. Does OneDrive actually sync files in that folder?
"test $(Get-Date -Format o)" | Out-File (Join-Path $env:OneDrive "jarvis-trader-handoff\_sync_test.txt")
# Then check onedrive.com in a browser (or right-click the file in Explorer, look for cloud icon).
# If the file doesn't reach the cloud, the entire cloud cron stack is broken before it starts.
```

**Common gap:** Corporate OneDrive with a differently-named tenant folder (e.g. `OneDrive - CompanyName` vs `OneDrive`). If so, all scripts need `-OneDriveRoot` overrides. Tell me your actual `$env:OneDrive` value and I'll patch every script and cron in one turn.

---

## Gap 2: Scripts saved where PowerShell can find them

The installers use `$PSScriptRoot` to locate sibling scripts. If they're not together, install fails silently.

```powershell
# Recommended location: C:\src\jarvis-trader\scripts\ (versioned with your repo)
# Or anywhere else, as long as ALL scripts are in ONE directory.

# Verify all 7 files present in the same directory:
$scriptsDir = "C:\src\jarvis-trader\scripts"   # adjust to yours
Get-ChildItem $scriptsDir -Filter *.ps1 | Select-Object Name
Get-ChildItem $scriptsDir -Filter *.py | Select-Object Name

# Expected files:
#   machine_audit.ps1                         (one-shot spec dump)
#   Sync-JarvisHandoff.ps1                    (local → OneDrive bridge)
#   Sample-MachineTelemetry.ps1               (per-minute machine sampler)
#   Install-TelemetryTask.ps1                 (installer for machine sampler)
#   Sample-ProcessTelemetry.ps1               (per-5min process sampler)
#   Install-ProcessTelemetryTask.ps1          (installer for process sampler)
#   analyze_contention.py                     (ad-hoc analyzer, optional)
```

**Common gap:** downloaded from Perplexity into `Downloads/` and never moved. Windows Task Scheduler will run tasks with those absolute paths — if you later delete or move files from Downloads, tasks silently fail.

---

## Gap 3: PowerShell execution policy

Task Scheduler runs scripts non-interactively. If execution policy blocks `.ps1`, tasks fail with cryptic errors.

```powershell
# Check current policy
Get-ExecutionPolicy -List
# CurrentUser should be at least RemoteSigned or Bypass. Undefined/Restricted will break tasks.

# If Restricted or Undefined for CurrentUser:
Set-ExecutionPolicy -Scope CurrentUser -ExecutionPolicy RemoteSigned
```

The installers pass `-ExecutionPolicy Bypass` in the task action, so this usually isn't needed — but I want you to verify because the failure mode is silent.

---

## Gap 4: Performance counter permissions

`Sample-MachineTelemetry.ps1` reads Windows performance counters. Some counters (`\System\Processor Queue Length` specifically) require you be in the `Performance Monitor Users` group.

```powershell
# Check current group membership
whoami /groups | Select-String "Performance Monitor Users|Administrators"

# If you're an Administrator, you already have access — no action needed.
# If not, add yourself:
net localgroup "Performance Monitor Users" "$env:USERNAME" /add
# Then log out and back in (group membership only applies to new sessions).
```

**Verify without waiting for a full sample:**
```powershell
Get-Counter -Counter '\System\Processor Queue Length' -MaxSamples 1
# Should return a value. If access denied, fix group membership.
```

---

## Gap 5: Scheduled tasks actually installed

You must run `Install-TelemetryTask.ps1` AND `Install-ProcessTelemetryTask.ps1` — from **elevated** PowerShell — before Monday.

```powershell
# From an elevated (admin) PowerShell session:
cd C:\src\jarvis-trader\scripts   # or wherever
.\Install-TelemetryTask.ps1
.\Install-ProcessTelemetryTask.ps1

# Verify both are registered and ran successfully:
Get-ScheduledTask | Where-Object { $_.TaskName -match 'Telemetry' } |
    Select-Object TaskName, State, @{n='LastRun';e={(Get-ScheduledTaskInfo $_).LastRunTime}},
                                   @{n='LastResult';e={(Get-ScheduledTaskInfo $_).LastTaskResult}}

# Expected:
#   MachineTelemetrySampler   Ready   <recent time>   0
#   ProcessTelemetrySampler   Ready   <recent time>   0
# LastResult = 0 means success. Any other number means it errored.
```

**Common gap:** running the installer from non-elevated PowerShell. It'll fail with a clear error, but you have to actually read the output. Don't just run it and walk away.

---

## Gap 6: First telemetry data actually landing

Even after install, verify the CSVs are growing:

```powershell
$telemetryDir = Join-Path $env:OneDrive "jarvis-trader-handoff\telemetry"
$today = Get-Date -Format "yyyy-MM-dd"

# Should show 2 files with recent timestamps and growing line counts
Get-ChildItem $telemetryDir | Format-Table Name, Length, LastWriteTime

# Wait 5 minutes, then re-run. Line count should grow.
Get-Content (Join-Path $telemetryDir "telemetry_$today.csv") | Measure-Object -Line
Get-Content (Join-Path $telemetryDir "processes_$today.csv") | Measure-Object -Line

# After 1 hour: telemetry_*.csv should have ~60 lines. processes_*.csv should have ~120 lines
# (60/5 samples × 10 processes each).
```

**If a file exists but line count doesn't grow:** check `$telemetryDir\*errors.log` for clues. Most common cause: script path in Task Scheduler action doesn't resolve (you moved the file).

---

## Gap 7: Sync bridge tested end-to-end

Before Monday, do one full-cycle test of the sync bridge, even without real jarvis-trader data:

```powershell
# Create dummy inputs to prove the bridge works
$root = "C:\src\jarvis-trader"   # your actual JARVIS_TRADER_ROOT
New-Item -ItemType Directory -Force -Path "$root\results","$root\verdicts","$root\cards" | Out-Null
'{"test": true}' | Out-File "$root\verdicts\test__verdict.json"

# Run the sync
.\Sync-JarvisHandoff.ps1 -Verbose

# Verify:
#  - Output shows "ok: true"
#  - $env:OneDrive\jarvis-trader-handoff\sync_manifest.json exists
#  - $env:OneDrive\jarvis-trader-handoff\verdicts\test__verdict.json exists
#  - The manifest has synced_at_utc within the last minute
Get-Content (Join-Path $env:OneDrive "jarvis-trader-handoff\sync_manifest.json") | ConvertFrom-Json

# Clean up the test verdict when done
Remove-Item "$root\verdicts\test__verdict.json"
```

**Common gap:** OneDrive is set to "Files On-Demand" and the manifest write appears complete locally but the cloud copy is still pending. Give it 2-3 minutes to propagate, then re-verify the file appears on onedrive.com.

---

## Gap 8: Old local Jarvis tracker task still enabled

If you had a Windows Task Scheduler entry for the OLD local `python -m jarvis_funnel.tracker` job, it and the new cloud cron will BOTH run Monday 6 AM. Fine functionally but wasteful and produces double notifications.

```powershell
# Find any old jarvis-related tasks
Get-ScheduledTask | Where-Object {
    $_.TaskName -match 'jarvis|funnel|tracker' -and $_.TaskName -notmatch 'Telemetry'
} | Select-Object TaskName, State

# Disable (don't delete — keep as rollback path for 4 weeks)
Disable-ScheduledTask -TaskName "<name-from-above>"
```

**Skip this step if you never had a local tracker task.** If in doubt, run the query — takes 1 second.

---

## Gap 9: Push notifications actually enabled in your Perplexity account

Two crons (`55ac8fb7` Sunday pre-check, `579dc58b` contention audit — wait, only the pre-check pushes) will attempt push notifications when things break. If push isn't enabled in your account, you get in-app only and might miss urgent Sunday-night alerts.

Verify in Perplexity settings: notifications → push enabled for this device.

Not something I can check remotely — but easy to forget.

---

## Gap 10: The pattern audit deferral (do NOT fix this)

Cron `a9d6a4bb` (Jarvis sync pattern audit) is intentionally gated until Aug 31 via a trigger check. Monday morning it will silently skip.

If you see it listed but `next_run` looks weird or state is confusing, that's expected. Don't try to "fix" it before Aug 31.

---

## What passing this checklist looks like

If all 10 gaps are cleared, on Saturday July 4 at 10 AM CT you should get a setup review email that says:

> ✅ **Cron stack review · 0 broken, 0 degraded, 6 ready**

If you get flags in the Saturday email, they'll tell you exactly which gap you missed. This checklist and that email are the same information — this is the pre-flight, that's the flight check.

---

## Honest gap-detection limits

Things I cannot check even with this checklist:
- **OneDrive cloud sync bandwidth limits** — if you're on a metered connection, sync will lag and freshness gates will false-alarm. No local test for this.
- **Windows Update pending restart** — a pending restart can silently disable scheduled tasks after the next reboot. Check `Get-PendingReboot` (requires the PSWindowsUpdate module) or just note if you've been putting off a restart.
- **Antivirus interference** — Windows Defender occasionally quarantines PowerShell scripts that fetch performance counters at high frequency. If samples inexplicably stop working, check Defender's quarantine.
- **The scripts themselves may have bugs** — I've written them carefully but haven't run them on real Windows. First-run failures are possible. `machine_audit.ps1` is the smallest and safest to test first.
