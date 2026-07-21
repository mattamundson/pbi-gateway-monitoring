#!/usr/bin/env python
"""Render docs/TASKLIST.md from the loop ledger.

The ledger (~/.claude/loops/pbi-gateway-completion/prd.json) is the source of
truth for task state -- it is what the drain loop reads and writes. This renders
it as a checklist a human can watch get ticked off, so the two can never
disagree about what is done.

Same discipline as starter/alerting/generate_rules_md.py: one source of truth,
one generated view, --check in CI.

Usage:
    python scripts/render_tasklist.py            # rewrite docs/TASKLIST.md
    python scripts/render_tasklist.py --check    # exit 1 if stale
"""

from __future__ import annotations

import argparse
import io
import json
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_REPO = os.path.dirname(_HERE)

LEDGER = os.environ.get(
    "GATEWAYMON_LEDGER",
    os.path.expanduser("~/.claude/loops/pbi-gateway-completion/prd.json"),
)
OUT = os.path.join(_REPO, "docs", "TASKLIST.md")

PHASES = {
    0: ("Phase 0 -- Correctness blockers", "Must land before the live pilot. Running the pilot against this pipeline would spend paid F2 capacity measuring something that cannot work end to end."),
    1: ("Phase 1 -- Make the system observable to itself", "A monitoring tool that fails silently is worse than none, because it manufactures false confidence."),
    2: ("Phase 2 -- Live pilot", "Where the flagship claim becomes a number. Needs paid F2 capacity."),
    3: ("Phase 3 -- Forward testing", "Earn the right to be trusted. Engineering effort is small; calendar time is not."),
    4: ("Phase 4 -- Enterprise readiness", "What a Fortune 500 gates on. Detail in docs/ENTERPRISE-READINESS.md."),
    5: ("Phase 5 -- Report completeness", "Deliberately after Phase 4 -- pages are table stakes, the join is the moat."),
    6: ("Phase 6 -- Autonomy L1: real-time detection", "First net-new capability. All GA Fabric primitives."),
    7: ("Phase 7 -- Autonomy L2: prediction", "Start cheap (native KQL), escalate only on evidence."),
    8: ("Phase 8 -- Autonomy L3: explanation", "Best value-per-effort in the roadmap. **v1.0 can ship here.**"),
    9: ("Phase 9 -- Autonomy L4: self-healing", "No vendor-supported path -- Fabric UDFs cannot reach on-prem gateways. A distinct systems-integration project."),
    10: ("Phase 10 -- Release and adoption", ""),
}

STATUS_ICON = {
    "done": "x",
    "open": " ",
}


def _status(t: dict) -> tuple[str, str]:
    if t.get("passes"):
        return "x", ""
    st = t.get("status")
    if st == "tenant-gated":
        return " ", " `PARKED -- needs live F2 tenant`"
    if t.get("depends_on"):
        return " ", ""
    return " ", ""


def render() -> str:
    with io.open(LEDGER, encoding="utf-8") as fh:
        d = json.load(fh)

    tasks = d["tasks"]
    by_id = {t["id"]: t for t in tasks}
    done = [t for t in tasks if t.get("passes")]

    # A task is workable now if not done and every dependency is done.
    def blocked_by(t):
        return [x for x in t.get("depends_on", []) if not by_id.get(x, {}).get("passes")]

    o = io.StringIO()
    o.write("# Task list -- pbi-gateway-monitoring\n\n")
    o.write("> **GENERATED FILE -- DO NOT EDIT.**\n")
    o.write("> Source of truth: the drain-loop ledger `~/.claude/loops/pbi-gateway-completion/prd.json`.\n")
    o.write("> Regenerate with `python scripts/render_tasklist.py`.\n")
    o.write("> Full rationale for every task: [`IMPLEMENTATION-ROADMAP.md`](IMPLEMENTATION-ROADMAP.md).\n\n")

    pct = round(100 * len(done) / len(tasks))
    bar = "#" * (pct // 5) + "." * (20 - pct // 5)
    o.write(f"**{len(done)} / {len(tasks)} complete ({pct}%)**  `[{bar}]`\n\n")

    # Next-up: unblocked, not done, lowest phase first.
    nxt = [
        t for t in tasks
        if not t.get("passes")
        and t.get("status") != "tenant-gated"
        and not blocked_by(t)
    ]
    nxt.sort(key=lambda t: (t["phase"], t["id"].zfill(4)))
    if nxt:
        o.write("### Workable right now (nothing blocking these)\n\n")
        for t in nxt[:6]:
            o.write(f"- **{t['id']}** `{t['size']}` -- {t['title']}\n")
        o.write("\n---\n\n")

    for ph in sorted(PHASES):
        group = [t for t in tasks if t["phase"] == ph]
        if not group:
            continue
        title, blurb = PHASES[ph]
        gdone = sum(1 for t in group if t.get("passes"))
        o.write(f"## {title}\n\n")
        if blurb:
            o.write(f"*{blurb}*\n\n")
        o.write(f"`{gdone}/{len(group)} complete`\n\n")
        for t in sorted(group, key=lambda x: x["id"].zfill(4)):
            mark, note = _status(t)
            bl = blocked_by(t)
            if bl and not t.get("passes"):
                note += f" `blocked by {', '.join(bl)}`"
            pr = t.get("closed_in")
            if pr:
                note += f" -- {pr}"
            o.write(f"- [{mark}] **{t['id']}** `{t['size']}` {t['title']}{note}\n")
        o.write("\n")

    o.write("---\n\n## Discovery log\n\n")
    o.write("Findings surfaced *while* draining, that no prior backlog contained. ")
    o.write("This is why the loop is a discovery-drain and not a fixed checklist.\n\n")
    for f in d.get("discovery", {}).get("found", []):
        o.write(f"- **[{f.get('severity','?').upper()}]** (pass {f.get('pass')}, via {f.get('task')}) {f['finding']}\n")

    o.write("\n---\n\n## Constraints the loop operates under\n\n")
    for c in d.get("hard_constraints", []):
        o.write(f"- {c}\n")
    o.write(f"\n**Stop condition:** {d['done_predicate']}\n")
    return o.getvalue()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true")
    args = ap.parse_args()

    if not os.path.exists(LEDGER):
        print(f"[SKIP] ledger not present at {LEDGER}")
        return 0

    rendered = render()
    if args.check:
        if not os.path.exists(OUT):
            print(f"[FAIL] {OUT} missing")
            return 1
        cur = io.open(OUT, encoding="utf-8").read()
        if cur.replace("\r\n", "\n") != rendered.replace("\r\n", "\n"):
            print("[FAIL] docs/TASKLIST.md is stale. Run: python scripts/render_tasklist.py")
            return 1
        print("[OK] docs/TASKLIST.md in sync with the ledger")
        return 0

    io.open(OUT, "w", encoding="utf-8", newline="\n").write(rendered)
    print(f"[OK] wrote {OUT}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
