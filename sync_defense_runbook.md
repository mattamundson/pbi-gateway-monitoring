# Local → OneDrive Sync Defense

## The threat model

The cloud Jarvis tracker (cron `4215f7d9`) runs Monday 6 AM Central and consumes whatever happens to be in OneDrive at that moment. If your local trading engine produced fresh verdicts but they never made it to OneDrive, **the tracker will silently report stale data**. You'd act on Monday's tearsheet thinking it reflected Friday's runs.

Two failure modes drive this:

| # | Failure | How it happens |
|---|---|---|
| A | Local artifacts never copied into the OneDrive folder | Your engine writes to `C:\src\jarvis-trader\results\` not the OneDrive sync folder. No automatic bridge. |
| B | Files copied but OneDrive client hasn't uploaded | OneDrive paused (battery saver, metered network, manual pause), files locked open, account quota full, sync token expired |

Both produce identical symptoms — cloud sees stale data — but require different fixes.

## Three-layer defense

```
┌─────────────────────────────────────────────────────────────┐
│  Layer 1: Sync-JarvisHandoff.ps1                            │
│  WHEN: End of every Phase 2/3 / verdict-producing run       │
│  WHERE: Your machine                                        │
│  PURPOSE: Bridge local → OneDrive, verify upload, write     │
│           sync_manifest.json with timestamps + git SHA      │
└─────────────────────────────────────────────────────────────┘
                          ↓ writes sync_manifest.json
┌─────────────────────────────────────────────────────────────┐
│  Layer 3: Sunday 8 PM CT staleness pre-check (cron 55ac8fb7)│
│  WHEN: Sunday night, 10h before Monday tracker              │
│  WHERE: Cloud                                               │
│  PURPOSE: Read sync_manifest.json + count actual verdicts.  │
│           If stale/broken, push notification to your phone  │
│           so you have time to fix it before Monday.         │
└─────────────────────────────────────────────────────────────┘
                          ↓
┌─────────────────────────────────────────────────────────────┐
│  Layer 2: Monday 6 AM CT tracker (cron 4215f7d9)            │
│  WHEN: Monday morning                                       │
│  WHERE: Cloud                                               │
│  PURPOSE: Re-checks manifest as hard gate. If >96h stale,   │
│           refuses to compute a meaningless diff and notifies│
│           with a 🔴 BLOCKED message instead. If 24-96h stale│
│           proceeds with ⚠️ STALE prefix.                    │
└─────────────────────────────────────────────────────────────┘
```

## Layer 1: install Sync-JarvisHandoff.ps1

### One-time setup

1. Save `Sync-JarvisHandoff.ps1` somewhere stable. Recommendation: `C:\src\jarvis-trader\scripts\Sync-JarvisHandoff.ps1` (committed to git).
2. Set the env var so the script knows your repo root (one-time, persistent):
   ```powershell
   [Environment]::SetEnvironmentVariable('JARVIS_TRADER_ROOT', 'C:\src\jarvis-trader', 'User')
   ```
   Adjust the path if your repo lives elsewhere. Restart PowerShell after.
3. Confirm `$env:OneDrive` resolves to your OneDrive root (run `echo $env:OneDrive`). If empty, you'll need to pass `-OneDriveRoot` explicitly.

### How to invoke

Pick one of three integration patterns:

**Manual (simplest, most likely to be forgotten):**
```powershell
.\Sync-JarvisHandoff.ps1 -Verbose
```

**Chained at the end of your phase runs (recommended):**
```powershell
python -m jarvis_funnel.run --phase 3 ; if ($LASTEXITCODE -eq 0) { .\Sync-JarvisHandoff.ps1 }
```

**Git post-commit hook (most automatic, fires whenever you commit verdicts):**
In `C:\src\jarvis-trader\.git\hooks\post-commit` (create if missing):
```bash
#!/bin/sh
powershell -ExecutionPolicy Bypass -File "C:/src/jarvis-trader/scripts/Sync-JarvisHandoff.ps1" -SkipFlush
```

### Expected output

JSON status object. Look for `"ok": true`. Exit code 0 = clean. Exit code 1 = something failed (sync incomplete or copy errors). Exit code 2 = bad config (paths wrong).

### What the manifest contains

Each successful run writes `{OneDrive}/jarvis-trader-handoff/sync_manifest.json`:

```json
{
  "synced_at_utc": "2026-07-04T19:23:11Z",
  "synced_at_local": "2026-07-04T14:23:11-05:00",
  "hostname": "MATT-DEV",
  "local_root": "C:\\src\\jarvis-trader",
  "onedrive_root": "C:\\Users\\matt\\OneDrive\\jarvis-trader-handoff",
  "git_sha": "abc123...",
  "verdict_count": 23,
  "newest_verdict_utc": "2026-07-04T18:45:02Z",
  "sync_results": [ ... ]
}
```

This is the source of truth for Layers 2 and 3.

## Layer 2: Monday tracker freshness gate

Already deployed. Cron `4215f7d9`. Behavior:

| Staleness | Action |
|---|---|
| ≤ 24h | Run normally, no special prefix |
| 24-96h | Run with ⚠️ STALE prefix on the notification + freshness header in the digest |
| > 96h | **Refuse to run.** Send 🔴 BLOCKED notification with suggested fix command. |
| Manifest missing | Treated as > 96h |

It also cross-checks: counts actual `*__verdict.json` files in OneDrive vs `verdict_count` in the manifest. If they disagree, flags as `sync_mismatch` in the digest (means OneDrive thinks fewer files exist than your last sync claimed — usually means upload is mid-flight).

## Layer 3: Sunday-night pre-check

Already deployed. Cron `55ac8fb7`. Runs Sunday 8 PM CT (10 hours before Monday tracker).

Three outcomes:
- ✅ FRESH — quiet in-app confirmation
- 🟡 STALE — push to phone with suggested fix
- 🔴 BROKEN — push to phone, urgent

The push channel is intentional here. You explicitly approved push usage scope in your preferences via this audit setup. If you want to drop push and keep in-app only, tell me and I'll update the cron.

## All active crons after this round

| ID | Name | Cadence | Channels |
|---|---|---|---|
| `55ac8fb7` | Jarvis sync pre-check | Sun 8:00 PM CT | in-app + push |
| `4215f7d9` | Jarvis funnel weekly tracker | Mon 6:00 AM CT | in-app |
| `1659eea3` | Computer-stack drift audit | Mon 7:00 AM CT | in-app + email |

You're at 3/15 cron slots used.

## Operational quick reference

| Situation | Command |
|---|---|
| Just finished a Phase 3 run | `.\Sync-JarvisHandoff.ps1 -Verbose` |
| Sunday night I got a 🟡 STALE warning | Same: `.\Sync-JarvisHandoff.ps1` |
| OneDrive is paused | Click OneDrive system tray → Resume sync, then run the script |
| I want to manually trigger the Monday tracker now | Tell Perplexity: *"Run cron 4215f7d9 now"* |
| Disable pre-check temporarily | *"Pause cron 55ac8fb7"* |
| Reset the manifest after a major rewrite | Delete `OneDrive/jarvis-trader-handoff/sync_manifest.json`, then run `Sync-JarvisHandoff.ps1` once |

## Rollback paths

| Component | Rollback |
|---|---|
| Sync-JarvisHandoff.ps1 too aggressive (e.g. -Mirror deleted files) | Don't use `-Mirror`. Default behavior is additive. |
| Freshness gate blocking on a false positive | Tell Perplexity: *"Trigger cron 4215f7d9 now with freshness check disabled"* — I'll inject a one-shot override. Permanent disable: ask me to update the cron prompt to remove the gate. |
| Sunday pre-check too noisy | *"Pause cron 55ac8fb7"* or *"Change Sunday pre-check to in-app only"* |
| Revert to fully local tracker | Re-enable the Windows Task Scheduler entry (you haven't disabled it yet, per the migration doc) |

## Honest caveats

- [Unverified] The OneDrive "cloud sync complete" attribute check in `Sync-JarvisHandoff.ps1` uses Windows file attribute bit `0x400000` (recall-on-data-access). This is documented behavior for OneDrive Files On-Demand but I have not personally validated it on your specific OneDrive client version. If the verification step false-positives or false-negatives, tell me and I'll switch to a different detection (e.g. polling the OneDrive status API or just trusting a fixed timeout).
- [Inference] Robocopy `/MIR` mode (enabled with `-Mirror`) will delete files in OneDrive that no longer exist locally. The script is **off by default** for this exact reason — you don't want a botched local rename to delete legitimate verdicts in OneDrive. Only pass `-Mirror` when you intentionally want a clean state.
- [Inference] OneDrive's `OneDrive.exe /background` flag is the documented way to nudge sync, but Microsoft has rotated the supported flags before. If the flush stops working in a future OneDrive update, the script still completes — it just relies on natural sync timing within the timeout window.
- The freshness gate uses `sync_manifest.json` as ground truth. If you bypass `Sync-JarvisHandoff.ps1` and copy files manually, the manifest goes stale and the tracker will block you. This is the intended trade-off — explicit contract over silent corruption.
