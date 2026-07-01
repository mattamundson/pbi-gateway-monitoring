# Jarvis Tracker → Web-app Cron Migration

**Status:** Cloud cron scheduled and active.
**Cron ID:** `4215f7d9`
**Cadence:** Mondays 6:00 AM Central · UTC `0 11 * * 1` · exact-time, no jitter
**Next run:** Monday, July 6, 2026 at 6:00 AM CDT

## What changed

| Before | After |
|---|---|
| Windows Task Scheduler runs `python -m jarvis_funnel.tracker --sync-root C:/Users/matt/OneDrive/jarvis-trader-handoff --notify` | Web-app cron agent runs in Perplexity cloud, reads OneDrive via connector, writes HTML/JSON tearsheet back to OneDrive, notifies in-app |
| Requires laptop on Monday 6am | Runs whether your laptop is on, off, asleep, or you're traveling |
| Uses local `funnel_gate.py` SHA for verdicts | **Does NOT recompute verdicts.** Only reads existing `verdicts/*.json` produced by your local Phase 3 runs and tracks deltas |

## Critical design constraint — read this

The web-app cron is a **status/reporting layer only**. It does not run backtests, recompute verdicts, or replace `funnel_gate.py`. Those still happen on your machine during Phase 2/3 work. The cron just consumes the artifacts you've already committed to `OneDrive/jarvis-trader-handoff/verdicts/` and produces the weekly delta tearsheet.

**Therefore:** your local Phase 2/3 workflow remains unchanged. Make sure verdicts get written to OneDrive (not just to a local-only results dir) so the cron can see them.

## OneDrive layout the cron expects

```
jarvis-trader-handoff/
├── cards/
│   └── strategies_current.jsonl       # live card list (latest)
├── verdicts/
│   └── {hypothesis_id}__verdict.json  # per-strategy verdict
├── results/                            # raw backtest results (cron reads opportunistically)
├── tracker/                            # cron WRITES here
│   ├── week_YYYY-WW.html
│   ├── week_YYYY-WW.json
│   ├── state.json                      # last-run snapshot, used for week-over-week diff
│   ├── history.jsonl                   # append-only summary log
│   └── latest.html                     # (legacy from local tracker — cron does not maintain)
```

If your local tracker.py was writing a different layout, update the cron task prompt or align the layout. Confirm with `ls "C:\Users\matt\OneDrive\jarvis-trader-handoff"` in PowerShell.

## Disable the local Task Scheduler entry

Run in PowerShell (admin not required for query, may be required for disable depending on how the task was registered):

```powershell
# 1. Find the existing task — adjust the name match if yours is different
Get-ScheduledTask | Where-Object { $_.TaskName -match 'jarvis|funnel|tracker' } |
    Select-Object TaskName, TaskPath, State

# 2. Disable it (keeps the task definition around in case you want to revert)
Disable-ScheduledTask -TaskName "<exact-name-from-step-1>"

# 3. Verify
Get-ScheduledTask -TaskName "<exact-name-from-step-1>" | Select-Object State
# Expected: State = Disabled
```

**Do not delete** the task for at least 4 weeks. If the cloud cron has a bad run, re-enabling the local task is your fastest rollback:

```powershell
Enable-ScheduledTask -TaskName "<exact-name-from-step-1>"
```

## First-run expectations (Monday July 6)

- `tracker/state.json` won't exist yet in the cloud cron's reference frame (it lives in your local sync, but the cron will re-read it fresh).
- **First week's diff may show all strategies as "new_entries"** if the cron can't find or parse the previous `state.json`. This is expected — it self-corrects week 2.
- If anything breaks, the cron's task prompt explicitly instructs it to notify you via in-app rather than silently fail.

## Verifying the first run

After 6:05 AM CT Monday:

1. Check Perplexity for an in-app notification titled `Jarvis tracker · Week 2026-W28 · …`
2. Open `OneDrive/jarvis-trader-handoff/tracker/week_2026-W28.html`
3. Confirm `state.json` was updated with this week's snapshot

If no notification by 6:30 AM CT, the cron probably hit an error before reaching `send_notification`. Check the cron task history in the Tasks UI.

## Reverting / pausing the cloud cron

```text
Just tell Perplexity: "Pause cron 4215f7d9" or "Delete cron 4215f7d9"
```

Or in any session:
```text
List my scheduled tasks   →   identify 4215f7d9   →   delete or update
```

## Known limitations

- [Inference] The cron has no access to your local jarvis-trader git SHA, so the `funnel_gate.py` version embedded in verdicts must already be inside each `verdict.json` your local pipeline writes. The cron just reads it, doesn't re-derive it.
- [Inference] `near_threshold` calculation uses thresholds.md from the skill — if you change thresholds.md, the cron will pick up the new values on next run (it loads the skill at the start of each invocation).
- [Unverified] OneDrive connector `export_files` behavior for overwriting `state.json` and appending to `history.jsonl` — first run will confirm whether it overwrites cleanly or creates `state (1).json` style duplicates. If it duplicates, we'll switch to a single `state-latest.json` naming pattern.
