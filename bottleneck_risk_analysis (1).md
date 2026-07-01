# Jarvis Automation Flow — A Priori Bottleneck Risks

Generated before any real telemetry exists. Every claim labeled `[Assumption]`, `[Inference]`, or `[Unverified]`. These are hypotheses to validate against real data over the next 2-4 weeks, not conclusions.

Ranked by severity × likelihood.

---

## Risk 1 — OneDrive as single point of failure

**[Inference] · Severity: HIGH · Likelihood: HIGH**

Every cross-machine data path in the stack goes through OneDrive:

```
local jarvis-trader  →  OneDrive  →  cloud crons  →  notifications
     ↑                                                       ↓
     └────────── you read tearsheets via ────────────────────┘
```

Nine files matter (`sync_manifest.json`, `telemetry_*.csv`, `processes_*.csv`, `strategies_current.jsonl`, `verdicts/*.json`, `tracker/state.json`, `tracker/history.jsonl`, `sync_precheck_history.jsonl`, plus tracker HTML/JSON outputs).

If OneDrive is paused, slow, quota-exhausted, or the account gets throttled — everything downstream reports "no data" or stale data, and you might not notice because the audit stack itself uses OneDrive to notify you.

**Mitigation to consider:**
- Point telemetry to a local-only path AND also mirror to OneDrive. If OneDrive breaks, you still have local data for forensics.
- Add a monthly manual check: does the file I expect to be at cloud path X actually appear there?
- The Sunday pre-check is a partial defense (it fires push if sync is broken) but only for the sync manifest, not for the telemetry files.

**Why this is #1:** the ENTIRE stack fails silently if OneDrive fails silently. Every other risk assumes OneDrive works.

---

## Risk 2 — Phase 3 backtest runs orphan the tracker

**[Inference] · Severity: HIGH · Likelihood: MEDIUM-HIGH**

Your Phase 3 robustness battery is long-running (CPCV alone can be hours). Realistic scenario:

- Sunday afternoon: you kick off a Phase 3 batch expecting it to finish before dinner
- It takes longer than expected, still running Sunday 8 PM
- Sunday 8 PM pre-check runs → sees `sync_manifest.json` from your last sync (say, Friday) → reports "FRESH" because staleness is only ~55h
- Sunday 11 PM: Phase 3 finally finishes, writes new verdicts to local `verdicts/`, but you go to bed without running `Sync-JarvisHandoff.ps1`
- Monday 6 AM: tracker reads OneDrive, sees FRIDAY's verdicts, produces a tearsheet that omits your latest Phase 3 results
- You act on the tearsheet believing it reflects the weekend's work

The freshness gate protects against 96h+ staleness but not this pattern (24-48h stale that includes fresh unsynced work).

**Mitigation to consider:**
- Make `Sync-JarvisHandoff.ps1` the LAST line of every phase script (chained via `&&` or explicit end-of-run). Never rely on manual invocation.
- Add a `--wait-if-running` flag to the sync script that detects an in-flight `jarvis_funnel.run` Python process and blocks until it finishes.

**Why this ranks high:** it's silent, plausible, and the audit stack won't catch it because "24h stale but no fresh unsynced data" looks identical to "24h stale with fresh unsynced work."

---

## Risk 3 — Telemetry samplers get disabled by Windows Update

**[Unverified] · Severity: MEDIUM · Likelihood: MEDIUM**

Windows major updates occasionally reset scheduled task states, especially for tasks running under `S4U` (Service for User) principals — which is what my installers use.

Symptom: Contention audit and top-3-killers audits start reporting "no telemetry" on some future Monday. If you're not watching, you assume the audits are broken; actually the samplers are.

**Mitigation to consider:**
- The drift audit (cron `1659eea3`) checks scheduled-task health, but only for CLOUD crons, not local Windows Task Scheduler entries. There's an audit gap here.
- Add to the drift audit: read the OneDrive telemetry dir and flag "last CSV file older than 24h" as 🔴. That catches sampler death regardless of cause.
- Set a monthly manual reminder to run `Get-ScheduledTask -TaskName '*Telemetry*'`.

**Fix in this session below** — I'll extend the drift audit prompt.

---

## Risk 4 — Category misclassification hides real killers

**[Inference] · Severity: MEDIUM · Likelihood: HIGH**

The process sampler categorizes by name + cmdline regex. Known blind spots I've already thought of:

- **Databricks via `spark-submit`:** the cmdline will show `java -Xmx...` not `databricks` — categorized as `other`, not `python_databricks`.
- **VS Code Python debug sessions:** Python subprocess launched via VS Code has cmdline like `python -X pyRepr ...` with the actual script buried deep. Won't match the `jarvis` regex.
- **PowerShell hosting Python (Anaconda Prompt):** `powershell.exe` at top, Python underneath. Categorized as `other`.
- **Docker containers:** everything inside a container reports as `docker` regardless of what it's actually running. If you ever containerize your backtests, all attribution is lost.

**Mitigation to consider:**
- After 2 weeks of data, look at the top-3-killers report specifically for `other` and `python_other`. If they rank high, the categorization needs tuning.
- Add cmdline snippets to the "other" category logging so you can see WHAT that catch-all contains.

**Why medium not high:** doesn't break the system, just muddies attribution. You'll see there's a problem, just not exactly whose.

---

## Risk 5 — Trigger check race on sync pre-check + tracker

**[Inference] · Severity: MEDIUM · Likelihood: LOW-MEDIUM**

Sequence Monday morning:
1. 6:00 AM tracker (`4215f7d9`) runs → reads `sync_manifest.json` → if <96h stale, reads verdicts → writes `tracker/history.jsonl`
2. 6:05 AM you wake up, see the tracker notification, run `Sync-JarvisHandoff.ps1` to fix a stale manifest, kick off tracker again manually
3. Now `tracker/history.jsonl` has TWO entries for the same week — one from the auto-run (stale data), one from your manual re-run (fresh data)
4. Sync pattern audit (deferred until Aug 31, but eventually): sees the double entry, tries to pair with pre-check history, gets confused.

Same class of issue: manual re-runs after automatic runs pollute the append-only history logs.

**Mitigation to consider:**
- Change `tracker/history.jsonl` writes to be UPSERT by `week_id` instead of append. Same for `sync_precheck_history.jsonl` (keyed by `checked_at_local` date).
- Or accept the double-entry and have the pattern audit dedupe on read.

**Low-medium likelihood** because it requires you to actively manual-re-run, which most weeks you won't.

---

## Risk 6 — IBKR TWS resource bloat during market hours

**[Assumption] · Severity: MEDIUM · Likelihood: HIGH during trading hours, LOW otherwise**

TWS (Trader Workstation) is a Java app known to grow to 4-8 GB RSS after a full trading day of ticks. If you leave it running Sunday overnight (which many algo traders do to capture Sunday open at 5 PM CT), it can be the #1 resource killer without you realizing.

**Prediction:** your first top-3-killers report (assuming market has been open during the sample window) will show `trading_tws` in the top 3, with p95 RSS somewhere between 3-6 GB, active hours 40+ per week.

If it's in the top 3 but not causing RED contention events, ignore it — it's just being TWS. If it correlates with RED events, you have a real bloat issue worth restarting TWS between sessions.

**No mitigation needed pre-emptively** — just an expectation-setter so you don't panic when TWS shows up.

---

## Risk 7 — Cron budget drift

**[Inference] · Severity: LOW · Likelihood: MEDIUM**

You have 7 of 15 cron slots used. Each new capability we add tempts adding another cron. At 15 you hit a hard limit and can't add anything without removing something first.

Also: 6 emails/notifications every Monday morning is already a lot. If any of them get noisy (e.g. the contention audit fires ⚠️ every week because your baseline is naturally busy), you'll start ignoring them — noise defeats the whole point.

**Mitigation to consider:**
- Cap the Monday stack at these 6. Any new capability should replace an existing one, not add.
- After 4 weeks of data: if any audit has been ✅ every week, downgrade it to monthly. If any has been consistently noisy, tune thresholds.

---

## Risk 8 — I've been building blind

**[Unverified] · Severity: LOW · Likelihood: 100%**

Every script I've written, I've written on my Linux sandbox against my mental model of Windows. Realistic first-run failure rate: 20-40% of scripts will have at least one bug that only surfaces on real Windows (path separator issues, PS 5.1 vs 7 syntax, Get-Counter access patterns).

**Mitigation:**
- `machine_audit.ps1` is the smallest and safest to test first (it's read-only, no side effects). Run it, see if it crashes, THEN try the samplers.
- If any script fails on first run, paste the exact error to me and I'll patch it. Don't spend more than 10 minutes debugging any single script yourself.

---

## Summary table

| # | Risk | Severity | Likelihood | Fix ready? |
|---|---|---|---|---|
| 1 | OneDrive single-point-of-failure | HIGH | HIGH | Extending drift audit below |
| 2 | Phase 3 orphans the tracker | HIGH | MED-HIGH | Documented workflow change |
| 3 | Samplers disabled by Windows Update | MED | MED | Extending drift audit below |
| 4 | Category misclassification | MED | HIGH | Observe first, tune later |
| 5 | Manual re-runs pollute history | MED | LOW-MED | Documented, no action yet |
| 6 | TWS bloat | MED | HIGH in-hours | Expectation-set only |
| 7 | Cron budget drift | LOW | MED | Discipline, not code |
| 8 | Untested code | LOW | 100% | Report bugs, I'll patch |

Risks 1 and 3 are actionable in this session — I'll extend the drift audit to catch them.
