# Computer Stack Run-Book

Everything you need to operate the new setup. Treat this as living documentation — update it when you add automations or change surfaces.

---

## Active scheduled tasks

| ID | Name | Cadence | Surface | Purpose |
|---|---|---|---|---|
| `4215f7d9` | Jarvis funnel weekly tracker | Mondays 6:00 AM CT | Web-app cron | Reads OneDrive cards/verdicts, computes week-over-week tier deltas, writes HTML tearsheet, notifies in-app |
| `1659eea3` | Weekly Computer-stack drift audit | Mondays 7:00 AM CT | Web-app cron | Probes connectors, scheduled tasks, custom skills for drift/breakage. Email + in-app digest. |

Both are exact-time (no jitter), background=false (so they can produce file artifacts).

## Surface routing rules — the keep-in-mind version

| Pattern | Surface |
|---|---|
| Scheduled / unattended / Monday morning | **Web-app cron** |
| Touches your live logged-in tabs (Fabric, Databricks, IBKR, GitHub PRs, Vercel, Linear) | **Comet** |
| Heavy compute on bars data (Phase 2/3 backtests) | **Local CLI** (neither surface) |
| Mid-research dedup checks against existing strategy cards | **Comet sidebar** with `@tab` |
| Deliverable generation (.xlsx, .pdf, .pptx, .html) | **Web-app** |
| Hands-on interactive review of tearsheet + verdicts | **Comet** |

## Jarvis funnel — which surface for which phase

| Phase | Activity | Surface |
|---|---|---|
| 1 | Repo scan | Local CLI / Comet |
| 1 | OneDrive bookmark export read | Web-app |
| 1 | X bookmark dedup | Comet (needs your X session) |
| 1 | Card generation (`strategies_v1.jsonl`) | Web-app |
| 2 | Smoke-test config gen + writeup | Web-app |
| 2 | Smoke-test backtest execution | Local CLI |
| 3 | Robustness battery execution | Local CLI |
| 3 | Results synthesis + Tier writeup | Web-app |
| 4 | Combination analysis | Web-app |
| Tracker | Weekly status | **Web-app cron (4215f7d9)** |
| Ad-hoc | Tier promotion/demotion review | Comet |

## Local machine self-audit

Run `machine_audit.ps1` (delivered alongside this run-book). Paste the JSON block back into Perplexity and ask for the verdict against Comet's realistic resource floor.

Realistic floor (Chromium-fork baseline + agent overhead, [Inference]):
- 16 GB RAM if you also run Chrome / VS Code / Databricks SQL editor concurrently
- 5 GB free disk for the Comet profile + extension data
- Modern multi-core x64 or Apple Silicon (you're on Windows so x64)
- Broadband with <100ms latency to perplexity.ai

If your machine is well above the floor, run Comet as your default browser. If it's near the floor, prefer the web app for agent work and use Comet only for mid-browsing assistant calls.

## Operational commands

### List your crons
Just ask Perplexity: *"List all my scheduled tasks across sessions"*

### Pause / delete a cron
*"Pause cron 4215f7d9"* or *"Delete cron 1659eea3"*

### Add a new cron
Describe the task and cadence. Match the routing rules above when choosing the surface.

### Trigger the drift audit ad-hoc
*"Run the drift audit now"* — runs the same logic as the scheduled task without waiting for Monday.

## Maintenance cadence

| When | Action |
|---|---|
| First Monday of each month | The drift audit will prompt you to confirm/update your automation inventory. Reply with the list. |
| After modifying a user skill | Manually trigger the drift audit, or wait for Monday |
| After connecting a new service | Add it to the connector list in the drift audit task prompt (or just let it auto-discover via `list_external_tools`) |
| Quarterly | Review this run-book. Delete obsolete crons. Re-evaluate surface routing as Comet / Web-app features evolve. |

## Rollback paths

| Component | Rollback |
|---|---|
| Cloud Jarvis tracker (4215f7d9) | `Enable-ScheduledTask` on the local Windows task; pause/delete the cloud cron |
| Drift audit (1659eea3) | Pause/delete; no local equivalent existed |
| Comet as default browser | Switch default back to Chrome in Windows settings |

## Cost expectations

[Inference — based on tool-call shape, not a published price sheet]

- Jarvis tracker: ~15-25 tool calls per run, mostly file reads. Light.
- Drift audit: ~20-40 tool calls per run, connector probes + skill grep. Light.
- Combined weekly cost: roughly equivalent to two small research tasks. Negligible compared to a Phase 3 robustness battery on your local machine.

If either run starts costing materially more, that's a signal something is wrong (recursion, unexpected file growth, connector timeouts) — investigate, don't accept.
