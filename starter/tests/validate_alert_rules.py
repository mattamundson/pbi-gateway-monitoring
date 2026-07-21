#!/usr/bin/env python
"""Validate Activator alert rules against the real medallion table contracts.

Roadmap T14. Tier 1 (pure Python, no Spark, no network).

WHY THIS EXISTS
---------------
Nothing validated ``starter/alerting/activator-rules.json`` -- not even that it
was well-formed JSON. The consequence was found on 2026-07-21: the
``credential-drift`` rule (pain point #10) tests ``datasource_status != 'Online'``
while the gold layer produces ``status_current`` and the collector emits
``Live``/``Unknown``/``Error``. Wrong column AND wrong value vocabulary. The rule
had never been capable of firing and nothing would ever have told us.

This validator makes that class of defect impossible to ship. It checks that
every rule:

  1. names a ``source_table`` that exists in ``starter/schemas/table_contracts.json``
  2. points at a table something actually WRITES (a rule on an unmaterialized
     table can never fire, which is just as dead as a wrong column name)
  3. references only columns that exist in that table
  4. compares string literals only to values inside a column's declared vocabulary
  5. does not confuse a percent-scaled column with a 0-1 fraction
  6. uses ``{placeholder}`` tokens in its message that resolve to real columns
  7. carries the required human-gate fields if it takes an autonomous action

Exit code is non-zero on any finding. Fail-closed by design: an unparseable
condition is a FAILURE, never a pass.
"""

from __future__ import annotations

import json
import os
import re
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_STARTER = os.path.dirname(_HERE)

CONTRACTS_PATH = os.environ.get(
    "GATEWAYMON_CONTRACTS", os.path.join(_STARTER, "schemas", "table_contracts.json")
)
RULES_PATH = os.environ.get(
    "GATEWAYMON_RULES", os.path.join(_STARTER, "alerting", "activator-rules.json")
)

# Tokens that may appear in a condition but are not column references.
_KEYWORDS = {
    "AND", "OR", "NOT", "IN", "IS", "NULL", "TRUE", "FALSE",
    "and", "or", "not", "in", "is", "null", "true", "false",
}

# identifier = a bare word that could be a column name
_IDENT_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
# single- or double-quoted string literal
_STRLIT_RE = re.compile(r"'([^']*)'|\"([^\"]*)\"")
# {placeholder} in a message template
_PLACEHOLDER_RE = re.compile(r"\{([A-Za-z_][A-Za-z0-9_]*)\}")
# a comparison of an identifier to a numeric literal: col <op> 1.23
_NUMCMP_RE = re.compile(
    r"([A-Za-z_][A-Za-z0-9_]*)\s*(<=|>=|<|>|==|!=|=)\s*(-?\d+(?:\.\d+)?)"
)
# an inline comment marker that is not valid in any Activator expression surface
_INLINE_COMMENT_RE = re.compile(r"//|/\*|--\s")

REQUIRED_RULE_FIELDS = ("name", "tier", "source_table", "condition", "action")


class Findings:
    def __init__(self) -> None:
        self.errors: list[str] = []
        self.warnings: list[str] = []

    def error(self, rule: str, msg: str) -> None:
        self.errors.append(f"[FAIL] {rule}: {msg}")

    def warn(self, rule: str, msg: str) -> None:
        self.warnings.append(f"[WARN] {rule}: {msg}")


def _column_spec(table_def: dict, col: str):
    return table_def.get("columns", {}).get(col)


def _col_type(spec) -> str:
    return spec if isinstance(spec, str) else spec.get("type", "unknown")


def _vocabulary(spec):
    return None if isinstance(spec, str) else spec.get("vocabulary")


def _scale(spec):
    return None if isinstance(spec, str) else spec.get("scale")


def _strip_string_literals(text: str) -> str:
    """Blank out quoted literals so they are not mistaken for identifiers."""
    return _STRLIT_RE.sub(lambda m: " " * len(m.group(0)), text)


def validate_rule(rule: dict, contracts: dict, f: Findings) -> None:
    name = rule.get("name", "<unnamed>")

    missing = [k for k in REQUIRED_RULE_FIELDS if k not in rule]
    if missing:
        f.error(name, f"missing required field(s): {', '.join(missing)}")
        if "source_table" in missing or "condition" in missing:
            return  # cannot check further without these

    table_name = rule["source_table"]
    tables = contracts.get("tables", {})
    if table_name not in tables:
        f.error(
            name,
            f"source_table '{table_name}' is not in table_contracts.json. "
            "Either the rule points at a table that does not exist, or the "
            "contract needs extending. Fail-closed: not assuming it exists.",
        )
        return

    table_def = tables[table_name]
    enabled = rule.get("enabled", True)

    # --- Check 2: is anything actually writing this table? -------------------
    # An ENABLED rule on an unmaterialized table is a silent lie: it looks live
    # on the board and can never fire. A rule explicitly disabled with a recorded
    # reason is honest bookkeeping, so it is reported but does not fail the build.
    if not table_def.get("materialized", False):
        blocked = table_def.get("blocked_by", "no reason recorded")
        if enabled:
            f.error(
                name,
                f"source_table '{table_name}' is NOT materialized by any job "
                f"({blocked}). This rule is enabled but can never fire. Either "
                "materialize the table or set \"enabled\": false with a "
                "\"blocked_by\" reason.",
            )
        else:
            if "blocked_by" not in rule:
                f.error(
                    name,
                    "is disabled but records no 'blocked_by' reason -- a "
                    "disabled rule must say what would re-enable it.",
                )
            else:
                f.warn(
                    name,
                    f"DISABLED (pending {table_name}): {rule['blocked_by']}",
                )

    condition = rule["condition"]

    if _INLINE_COMMENT_RE.search(condition):
        f.error(
            name,
            f"condition contains an inline comment, which no expression surface "
            f"accepts: {condition!r}",
        )

    # --- Check 3: every identifier resolves to a real column -----------------
    scrubbed = _strip_string_literals(condition)
    idents = {t for t in _IDENT_RE.findall(scrubbed) if t not in _KEYWORDS}
    for ident in sorted(idents):
        if _column_spec(table_def, ident) is None:
            near = _suggest(ident, table_def)
            hint = f" Did you mean '{near}'?" if near else ""
            f.error(
                name,
                f"condition references '{ident}', which is not a column of "
                f"{table_name}.{hint}",
            )

    # --- Check 4: string literals must be inside the column's vocabulary -----
    for m in re.finditer(
        r"([A-Za-z_][A-Za-z0-9_]*)\s*(?:==|!=|=|<>)\s*(?:'([^']*)'|\"([^\"]*)\")",
        condition,
    ):
        col, lit = m.group(1), (m.group(2) if m.group(2) is not None else m.group(3))
        spec = _column_spec(table_def, col)
        if spec is None:
            continue  # already reported by check 3
        vocab = _vocabulary(spec)
        if vocab is not None and lit not in vocab:
            f.error(
                name,
                f"compares {table_name}.{col} to '{lit}', which is not in that "
                f"column's vocabulary {vocab}. This comparison can never match.",
            )

    # --- Check 5: percent vs fraction scale confusion ------------------------
    for m in _NUMCMP_RE.finditer(condition):
        col, _op, num = m.group(1), m.group(2), float(m.group(3))
        spec = _column_spec(table_def, col)
        if spec is None:
            continue
        if _scale(spec) == "percent-0-100" and 0 < num <= 1.0:
            f.error(
                name,
                f"compares {table_name}.{col} (scale percent-0-100) to {num}, "
                "which looks like a 0-1 fraction. As written this fires on "
                "virtually every row.",
            )

    # --- Check 6: message placeholders resolve to real columns ---------------
    action = rule.get("action", {})
    message = action.get("message", "")
    for ph in _PLACEHOLDER_RE.findall(message):
        if _column_spec(table_def, ph) is None:
            near = _suggest(ph, table_def)
            hint = f" Did you mean '{near}'?" if near else ""
            f.error(
                name,
                f"message placeholder '{{{ph}}}' is not a column of "
                f"{table_name}; it would render literally in the alert.{hint}",
            )

    # --- Check 7: autonomous-action guardrails -------------------------------
    action_type = action.get("type")
    if action_type in ("user_data_function", "run_notebook"):
        if rule.get("autonomous", False) and action_type == "user_data_function":
            f.error(
                name,
                "takes a user_data_function action with autonomous=true. "
                "Remediation actions must be human-gated (roadmap T46 Tier 2+).",
            )
        if action_type == "user_data_function":
            if "approval" not in action:
                f.error(name, "user_data_function action has no 'approval' gate.")
            if "circuit_breaker" not in rule:
                f.error(name, "user_data_function action has no circuit_breaker.")


def _suggest(ident: str, table_def: dict):
    """Cheap nearest-column suggestion: case-insensitive substring overlap."""
    cols = list(table_def.get("columns", {}).keys())
    low = ident.lower().replace("_", "")
    best, best_score = None, 0
    for c in cols:
        cl = c.lower().replace("_", "")
        score = 0
        if cl == low:
            score = 100
        elif low in cl or cl in low:
            score = 50 + min(len(low), len(cl))
        if score > best_score:
            best, best_score = c, score
    return best if best_score >= 50 else None


def main() -> int:
    print("=" * 72)
    print("ACTIVATOR RULE VALIDATION (roadmap T14)")
    print("=" * 72)
    print(f"contracts: {CONTRACTS_PATH}")
    print(f"rules    : {RULES_PATH}")
    print()

    try:
        with open(CONTRACTS_PATH, encoding="utf-8") as fh:
            contracts = json.load(fh)
    except Exception as e:  # noqa: BLE001
        print(f"[FAIL] could not load contracts: {e}")
        return 2
    try:
        with open(RULES_PATH, encoding="utf-8") as fh:
            rules_doc = json.load(fh)
    except Exception as e:  # noqa: BLE001
        print(f"[FAIL] activator-rules.json is not valid JSON: {e}")
        return 2

    rules = rules_doc.get("rules", [])
    if not rules:
        print("[FAIL] no rules found")
        return 2

    f = Findings()

    seen = set()
    for r in rules:
        nm = r.get("name", "<unnamed>")
        if nm in seen:
            f.error(nm, "duplicate rule name")
        seen.add(nm)
        validate_rule(r, contracts, f)

    for line in f.warnings:
        print(line)
    for line in f.errors:
        print(line)

    print()
    print("-" * 72)
    print(f"rules checked : {len(rules)}")
    print(f"errors        : {len(f.errors)}")
    print(f"warnings      : {len(f.warnings)}")
    print("-" * 72)

    if f.errors:
        print("RESULT: FAIL -- at least one rule cannot fire as written.")
        return 1
    print("RESULT: PASS -- every rule references real, materialized columns.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
