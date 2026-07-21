#!/usr/bin/env python
"""Prove the alert-rule validator actually catches things (roadmap T14).

Tier 1 (pure Python, no Spark, no network).

A validator that only ever passes is indistinguishable from a no-op. This suite
injects one deliberately broken rule per defect class and asserts the validator
rejects it AND says why. Every defect class here is one that shipped in this
repository at some point -- these are regression tests, not hypotheticals.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile

_HERE = os.path.dirname(os.path.abspath(__file__))
_STARTER = os.path.dirname(_HERE)
VALIDATOR = os.path.join(_HERE, "validate_alert_rules.py")
CONTRACTS = os.path.join(_STARTER, "schemas", "table_contracts.json")
REAL_RULES = os.path.join(_STARTER, "alerting", "activator-rules.json")

_GOOD_RULE = {
    "name": "probe-rule",
    "tier": 1,
    "pain": "test",
    "source_table": "gold_gateway_health",
    "condition": "heartbeat_age_minutes > 10",
    "action": {
        "type": "notify",
        "channels": ["Teams"],
        "message": "Gateway {GatewayObjectId} down for {heartbeat_age_minutes}m.",
    },
    "autonomous": True,
}


def _run(rules_doc) -> tuple[int, str]:
    with tempfile.NamedTemporaryFile(
        "w", suffix=".json", delete=False, encoding="utf-8"
    ) as fh:
        json.dump(rules_doc, fh)
        path = fh.name
    try:
        env = dict(os.environ)
        env["GATEWAYMON_RULES"] = path
        env["GATEWAYMON_CONTRACTS"] = CONTRACTS
        proc = subprocess.run(
            [sys.executable, VALIDATOR],
            capture_output=True,
            text=True,
            env=env,
        )
        return proc.returncode, proc.stdout + proc.stderr
    finally:
        os.unlink(path)


def _mutate(**overrides) -> dict:
    rule = json.loads(json.dumps(_GOOD_RULE))
    for k, v in overrides.items():
        if v is _DELETE:
            rule.pop(k, None)
        else:
            rule[k] = v
    return {"rules": [rule]}


class _Delete:
    pass


_DELETE = _Delete()

CASES = [
    (
        "control: a correct rule passes",
        _mutate(),
        0,
        "PASS",
    ),
    (
        "condition references a nonexistent column",
        _mutate(condition="datasource_status != 'Live'"),
        1,
        "is not a column of",
    ),
    (
        "string literal outside the column vocabulary",
        _mutate(
            source_table="bronze_gateway_datasources",
            condition="Status != 'Online'",
            action={
                "type": "notify",
                "channels": ["Teams"],
                "message": "ds {DatasourceName} is {Status}",
            },
        ),
        1,
        "not in that column's vocabulary",
    ),
    (
        "percent column compared to a 0-1 fraction",
        _mutate(condition="error_rate_pct > 0.25"),
        1,
        "looks like a 0-1 fraction",
    ),
    (
        "message placeholder that is not a column",
        _mutate(
            action={
                "type": "notify",
                "channels": ["Teams"],
                "message": "Gateway down. Blast radius: {blast_radius}",
            }
        ),
        1,
        "would render literally",
    ),
    (
        "enabled rule on an unmaterialized table",
        _mutate(
            source_table="gold_predictions",
            condition="anomaly_score > 2.5",
            action={
                "type": "notify",
                "channels": ["Teams"],
                "message": "anomaly {anomaly_score}",
            },
        ),
        1,
        "can never fire",
    ),
    (
        "source_table that does not exist at all",
        _mutate(source_table="gold_imaginary"),
        1,
        "is not in table_contracts.json",
    ),
    (
        "missing source_table entirely",
        _mutate(source_table=_DELETE),
        1,
        "missing required field",
    ),
    (
        "inline comment in the condition",
        _mutate(condition="heartbeat_age_minutes > 10  // tune per host"),
        1,
        "inline comment",
    ),
    (
        "autonomous remediation with no human gate",
        _mutate(
            action={
                "type": "user_data_function",
                "udf": "restart_gateway",
                "message": "restarting {GatewayObjectId}",
            },
            autonomous=True,
        ),
        1,
        "must be human-gated",
    ),
    (
        "disabled rule with no blocked_by reason",
        _mutate(
            source_table="gold_predictions",
            condition="anomaly_score > 2.5",
            enabled=False,
            action={
                "type": "notify",
                "channels": ["Teams"],
                "message": "anomaly {anomaly_score}",
            },
        ),
        1,
        "records no 'blocked_by' reason",
    ),
]


def main() -> int:
    print("=" * 72)
    print("ALERT-RULE VALIDATOR SELF-TEST (roadmap T14)")
    print("=" * 72)

    failures = 0

    for label, doc, want_code, want_substr in CASES:
        code, out = _run(doc)
        ok = code == want_code and want_substr in out
        status = "ok  " if ok else "FAIL"
        print(f"[{status}] {label}")
        if not ok:
            failures += 1
            print(f"         expected exit={want_code} and {want_substr!r}")
            print(f"         got      exit={code}")
            for line in out.splitlines():
                if line.startswith("[FAIL]") or line.startswith("RESULT"):
                    print(f"         | {line}")

    # The committed rule set must itself be clean.
    env = dict(os.environ)
    env.pop("GATEWAYMON_RULES", None)
    env.pop("GATEWAYMON_CONTRACTS", None)
    proc = subprocess.run(
        [sys.executable, VALIDATOR], capture_output=True, text=True, env=env
    )
    if proc.returncode == 0:
        print("[ok  ] the committed activator-rules.json validates clean")
    else:
        failures += 1
        print("[FAIL] the committed activator-rules.json does NOT validate")
        print(proc.stdout)

    print()
    print("-" * 72)
    if failures:
        print(f"RESULT: FAIL ({failures} case(s))")
        return 1
    print(f"RESULT: PASS ({len(CASES) + 1} cases)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
