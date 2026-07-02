# Session log — 2026-07-02 — Repo finalization + U19 runbook

**Scope:** Wrap up internal-test readiness, make a permanent local clone, restore the marketing
drafts, and produce a runbook for the one remaining live validation (U19).

> Companion raw transcript: `docs/session-logs/raw/2026-07-02-session-full-transcript.jsonl`
> (full session JSONL snapshot). This markdown is the readable digest of the same session.

---

## Current state

- Repo is **internal-test-ready** and private on GitHub: `github.com/mattamundson/pbi-gateway-monitoring`.
- **Permanent local clone created:** `C:\Users\mattm\Code\pbi-gateway-monitoring` (was previously only
  an ephemeral scratchpad clone). Tracks `origin/main`, working tree clean.
- The flagship claim **U19** (identity join) remains **`[Unverified]` / desk-verified** — never run live
  because trial Fabric capacity can't provision the Workspace Monitoring Eventhouse (Session 3 finding).

## Key decisions

- **License:** proprietary / all-rights-reserved internal-use (switched from MIT in a prior session).
- **Marketing drafts:** re-committed into the repo as tracked *internal* drafts (not for publication
  while the repo is private). Reversed the earlier archive-out decision at Amo's request.
- **U19 proof path:** short-lived **pay-as-you-go Fabric F2** capacity — spin up, prove, **pause**
  (~a few dollars), rather than a ~$260/mo reserved commitment. Runbook written and approved.
- **Threat model clarified:** repo is Amo's **personal, private, single-viewer** repo — so committing
  the session transcript + environment details is acceptable (no external testers in scope for this
  copy). Earlier redaction caution was calibrated to the wrong (multi-tester) model.

## What shipped this session (commits on `main`)

| Commit | Summary |
|---|---|
| `1078701` | Internal-test readiness pass (coherence fixes, tester-facing defects, proprietary license) — 24 files |
| `584489e` | Wire mashup + datasource bronze ingest; correct the parser over-claim header |
| `75ce33f` | Session 3 live-tenant finding: Pain #3 confirmed a trial-capacity blocker; VPN/network ruled out |
| `620051a` | Restore marketing drafts (`docs/publish/*.md`) as tracked internal drafts |

(Plus the earlier `95db70c` — paste-and-go `PILOT-identity-join-test.kql`.)

## Live-tenant attempt (Session 3, 2026-07-02) — recap

Drove the real Fabric tenant in-browser to run the flagship pilot. Findings (full detail in
`LIVE-TENANT-FINDINGS.md`):

- `Gateway-Pilot` had **Workspace Monitoring OFF** — the actual prerequisite gap; enabled it.
- Enabling on **trial capacity** created the Eventhouse/KQL DB/Eventstream items but the backend
  **never provisioned** (`Failed to update ingestion for fabric monitoring`; persistent
  `Can't access the Eventhouse`). Root cause: **trial-capacity limit (Pain #3)**, not a code bug.
- **VPN/network ruled out with hard evidence:** cluster host resolves to a public Azure IP, TCP 443
  succeeds, system DNS is the ISP resolver (not the VPN's) — the Kusto backend itself returned app
  errors, i.e. a capacity problem, not connectivity. (Fabric's "check your VPN" text is a red herring.)
- The block-2 match (the query returning real `ExecutingUser`/`ItemName`) **never ran**, so no real
  user/dataset data was produced.

## Blocked / open

- **U19 live proof** — needs three things assembled at once, none on the assistant:
  1. Paid **F2+ capacity** on `Gateway-Pilot`.
  2. A semantic model **refreshing through the on-prem gateway** in that workspace.
  3. A **`RequestId`** copied from the gateway host's `QueryStartReport*.csv` (that host is not this
     machine).
- Alternative not yet explored: **Azure Log Analytics** path (join on `XmlaRequestId`) that avoids the
  Fabric Eventhouse.

## Runbook (approved) — prove U19, then pause

Saved at `C:\Users\mattm\.claude\plans\do-you-have-access-cryptic-wadler.md`. Shape:

- **Part A (Amo):** create F2 in Azure Portal → assign `Gateway-Pilot` to it → enable Workspace
  Monitoring (should provision on F2, unlike trial).
- **Part B (Amo):** refresh a gateway-routed model → grab 2–3 RequestIds from the gateway host CSV →
  wait ~15 min.
- **Part C (assistant):** drive `starter/kql/PILOT-identity-join-test.kql` in the browser; block 2
  returning populated `ExecutingUser`/`ItemName` for a real RequestId = **U19 proven live**.
- **Part D (assistant):** on a match, flip U19 → verified in `research/phase5_validation.md` (row L60),
  `LIVE-TENANT-FINDINGS.md` (new Session 4 + tally), `PRODUCTIZATION.md` (L24); leave unrelated
  `[Unverified]` labels alone. Then Amo **pauses** the F2 to stop billing.

## Suggested next action

Amo: stand up the F2 capacity and produce one traceable gateway refresh, then return with
"monitoring's on, I refreshed, here are the RequestIds: …" — the assistant runs Part C from there.
