#!/usr/bin/env python
"""Generate activator-rules.md from activator-rules.json (roadmap T4).

The JSON is the source of truth. The Markdown is a rendering of it for humans.

WHY THIS EXISTS
---------------
On 2026-07-21 the two files disagreed about their own subject matter:
  * activator-rules.json hardcoded a 3-minute offline threshold; the .md and
    config.sample.json both documented 10 minutes; phase5_validation.md's
    acceptance test expected ~2-3 minutes. Three numbers, one alert.
  * The .md documented 5 rules including network-saturation. The JSON had 7,
    including three the .md never mentioned, and no network-saturation at all.
Nobody could say which file was correct because neither was generated from the
other. Now one is.

Usage:
    python generate_rules_md.py            # rewrite the .md
    python generate_rules_md.py --check    # CI: exit 1 if the .md is stale
"""

from __future__ import annotations

import argparse
import io
import json
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
RULES_PATH = os.path.join(_HERE, "activator-rules.json")
MD_PATH = os.path.join(_HERE, "activator-rules.md")

HEADER = """# Fabric Activator Alerting Rules

> **GENERATED FILE -- DO NOT EDIT.**
> Source of truth: [`activator-rules.json`](activator-rules.json).
> Regenerate with `python starter/alerting/generate_rules_md.py`.
> CI fails if this file is stale (see `.github/workflows/tests.yml`).

Every rule below is validated on each push by
[`starter/tests/validate_alert_rules.py`](../tests/validate_alert_rules.py)
against [`starter/schemas/table_contracts.json`](../schemas/table_contracts.json):
each rule's `source_table` must exist and be materialized, every column it
references (in both the condition and the message template) must be real, string
comparisons must fall inside the column's declared vocabulary, and a
percent-scaled column may not be compared against a 0-1 fraction.

**Why that matters:** before this validation existed, **4 of 7 rules could never
fire** and 2 more rendered broken alert messages. See each rule's *Fix history*.

---

## Data source configuration

| Item | Value |
|---|---|
| Trigger mode | When the source Delta table is updated (after the Gold notebook completes) |
| Gold refresh cadence | 5 minutes |
| Entity key | Per-rule (see `grouping`) so Activator tracks alert state independently |

**Capacity requirement.** `[CORRECTED 2026-07-21]` Earlier revisions of this
document asserted "Activator requires F8+". Microsoft documents no hard SKU
minimum for Activator itself beyond a supported Fabric capacity (F-SKU or
Trial). The F8 guidance applies to running an **always-on Eventhouse** without
overage, which is a Phase 6 (streaming) concern, not a prerequisite for these
rules against Delta tables.

---
"""

FOOTER = """
---

## Implementation notes

1. **"Becomes true" vs "Is true".** Use *Becomes true* for every stateful rule.
   *Is true* re-fires on each evaluation cycle and produces an alert storm for
   the duration of an outage.

2. **Route P1 to a channel, not to people.** Activator throttles Teams and email
   at **30 messages per recipient per hour** but **500 per item per hour**.
   During a fleet-wide outage, per-recipient routing silently drops messages
   exactly when they matter most.

3. **Own the ingestion under a service principal.** Activator's Power BI-sourced
   ingestion is owned by a single user identity. If that user loses access or
   rotates credentials, **ingestion and rule evaluation stop with no
   notification**.

4. **Thresholds here are defaults, not calibrations.** Every numeric threshold
   is a generic starting point calibrated against nothing. Roadmap T25 (Day-0
   calibration) sets them per-tenant from that gateway's own observed
   percentiles. Do not treat these numbers as validated.

5. **Testing.** Roadmap T23 builds `Invoke-FaultInjection.ps1` to induce each
   condition safely and measure real time-to-detect. Until then these rules are
   `[Unverified]` against live behavior.

6. **Lifecycle landmine.** Activator items using Power BI or Blob Storage as a
   source, or a **User Data Function as an action**, currently break Fabric's
   deployment-pipeline and Git integration. The rules here read from Delta
   tables, which is the safe side of that gap -- but `spool-disk-critical-remediate`
   would walk into it once its UDF action is wired.

---

*Generated from `activator-rules.json`. Rule prose, thresholds, and remediation
steps are fields in that file -- edit them there.*
"""


def _fmt_rule(i: int, r: dict) -> str:
    out = io.StringIO()
    enabled = r.get("enabled", True)
    status = "" if enabled else "  `[DISABLED]`"
    out.write(f"\n## Rule {i}: `{r['name']}`{status}\n\n")
    out.write(f"**Pain point:** {r.get('pain', 'n/a')}  \n")
    out.write(f"**Tier:** {r.get('tier', 'n/a')}  \n")
    out.write(f"**Source table:** `{r['source_table']}`  \n")
    out.write(f"**Grouping:** {r.get('grouping', 'n/a')}\n\n")

    if not enabled:
        out.write(
            f"> **This rule is disabled and will not fire.**  \n"
            f"> {r.get('blocked_by', 'no reason recorded')}\n\n"
        )

    if r.get("background"):
        out.write(f"{r['background']}\n\n")

    out.write("```\n")
    out.write(f"WHEN {r['condition']}\n")
    if r.get("transition"):
        out.write(f"\nTransition: {r['transition']}\n")
    if r.get("threshold_note"):
        out.write(f"\nThreshold:  {r['threshold_note']}\n")
    if r.get("circuit_breaker"):
        out.write(f"\nCircuit breaker: {r['circuit_breaker']}\n")
    out.write("```\n\n")

    action = r.get("action", {})
    atype = action.get("type", "n/a")
    out.write(f"**Action:** `{atype}`")
    if action.get("channels"):
        out.write(f" -> {', '.join(action['channels'])}")
    out.write("\n\n")
    if action.get("message"):
        out.write(f"> {action['message']}\n\n")
    if action.get("approval"):
        out.write(f"**Approval gate:** {action['approval']}\n\n")

    if r.get("remediation"):
        out.write(f"**Remediation.** {r['remediation']}\n\n")
    if r.get("evidence"):
        out.write(f"**Pain evidence.** {r['evidence']}\n\n")
    if r.get("_fixed"):
        out.write(f"<details><summary>Fix history</summary>\n\n{r['_fixed']}\n\n</details>\n\n")
    if r.get("_note"):
        out.write(f"*Note: {r['_note']}*\n\n")
    if r.get("_deferred"):
        out.write(f"*Deferred: {r['_deferred']}*\n\n")

    out.write("---\n")
    return out.getvalue()


def render() -> str:
    with open(RULES_PATH, encoding="utf-8") as fh:
        doc = json.load(fh)

    rules = doc["rules"]
    live = [r for r in rules if r.get("enabled", True)]
    blocked = [r for r in rules if not r.get("enabled", True)]

    out = io.StringIO()
    out.write(HEADER)
    out.write(
        f"\n**{len(rules)} rules defined: {len(live)} live, "
        f"{len(blocked)} disabled pending an unmet dependency.**\n\n"
    )

    out.write("| # | Rule | Pain | Source table | Status |\n")
    out.write("|---|---|---|---|---|\n")
    for i, r in enumerate(rules, 1):
        st = "live" if r.get("enabled", True) else "**disabled**"
        out.write(
            f"| {i} | `{r['name']}` | {r.get('pain', '')} "
            f"| `{r['source_table']}` | {st} |\n"
        )

    for i, r in enumerate(rules, 1):
        out.write(_fmt_rule(i, r))

    out.write(FOOTER)
    return out.getvalue()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument(
        "--check",
        action="store_true",
        help="exit 1 if activator-rules.md differs from what would be generated",
    )
    args = ap.parse_args()

    rendered = render()

    if args.check:
        if not os.path.exists(MD_PATH):
            print(f"[FAIL] {MD_PATH} does not exist; run without --check")
            return 1
        with open(MD_PATH, encoding="utf-8") as fh:
            current = fh.read()
        if current.replace("\r\n", "\n") != rendered.replace("\r\n", "\n"):
            print(
                "[FAIL] activator-rules.md is STALE relative to "
                "activator-rules.json.\n"
                "       Someone edited the generated file, or edited the JSON "
                "without regenerating.\n"
                "       Fix: python starter/alerting/generate_rules_md.py"
            )
            return 1
        print("[OK] activator-rules.md is in sync with activator-rules.json")
        return 0

    with open(MD_PATH, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(rendered)
    print(f"[OK] wrote {MD_PATH}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
