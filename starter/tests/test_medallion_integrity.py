#!/usr/bin/env python
"""Tier 1 tests for medallion integrity fixes (roadmap T7, T8).

Pure Python -- no Spark, no network. Covers the two defects where the pipeline
would fail SILENTLY:

  T8  read_bronze/read_silver classified every failure as "table missing" and
      returned None, so a permissions error or corrupt Delta log silently
      dropped an entire data source from the medallion.

  T7  schema drift was print()ed to notebook stdout and discarded, despite the
      function's own docstring claiming it was persisted for alerting. Pain
      point #4 is exactly this signal.
"""

from __future__ import annotations

import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_NB = os.path.join(os.path.dirname(_HERE), "notebooks")
sys.path.insert(0, _NB)

from gateway_bronze_lib import classify_read_failure  # noqa: E402

_FAILURES: list[str] = []


def check(label: str, got, want) -> None:
    if got == want:
        print(f"[ok  ] {label}")
    else:
        _FAILURES.append(label)
        print(f"[FAIL] {label}\n         want={want!r}\n         got ={got!r}")


# ---------------------------------------------------------------------------
# T8 -- read-failure classification
# ---------------------------------------------------------------------------
class _FakeAnalysisException(Exception):
    pass


def test_classify_read_failure() -> None:
    print("\n-- T8: read-failure classification " + "-" * 36)

    # Benign: the table genuinely is not there yet.
    for msg in (
        "Path does not exist: abfss://ws@onelake/lh.Lakehouse/Tables/silver_x",
        "[PATH_NOT_FOUND] Path does not exist.",
        "`/Tables/gold_y` is not a Delta table.",
        "[DELTA_MISSING_DELTA_TABLE] is not a Delta table",
        "Table or view not found: bronze_z",
        "Unable to infer schema for Parquet. It must be specified manually.",
    ):
        check(
            f"missing-table: {msg[:44]!r}",
            classify_read_failure(_FakeAnalysisException(msg)),
            "missing",
        )

    # NOT benign: the table exists but we cannot read it. These previously all
    # returned None and vanished the data source.
    for msg in (
        "java.nio.file.AccessDeniedException: Operation failed: Forbidden",
        "Status code 403, AuthorizationPermissionMismatch",
        "Permission denied: /Tables/silver_query_execution",
        "Invalid_token: the access token expired",
        "AADSTS700082: credential has expired",
        "Checksum mismatch reading _delta_log/00000000000000000003.json",
        "Corrupt Parquet footer detected",
        "Operation timed out after 30000 ms",
    ):
        check(
            f"hard-failure: {msg[:44]!r}",
            classify_read_failure(_FakeAnalysisException(msg)),
            "error",
        )

    # Fail-closed: an exception we have never seen must NOT be assumed benign.
    check(
        "unknown exception classifies as error (fail-closed)",
        classify_read_failure(ValueError("something entirely new")),
        "error",
    )

    # A message containing BOTH a missing marker and a hard marker is a hard
    # failure -- a permissions error while probing a path must not be softened
    # into "does not exist".
    check(
        "hard marker wins over missing marker",
        classify_read_failure(
            _FakeAnalysisException("Path does not exist -- Access Denied (403)")
        ),
        "error",
    )


# ---------------------------------------------------------------------------
# T7 -- schema drift detection
# ---------------------------------------------------------------------------
def _load_detect_schema_drift():
    """Import detect_schema_drift from the bronze notebook without executing
    its Spark autorun block."""
    os.environ["GATEWAYMON_AUTORUN"] = "0"
    import importlib.util

    path = os.path.join(_NB, "01_bronze_ingest.py")
    src = open(path, encoding="utf-8").read()

    # The notebook creates a SparkSession at import time; this is a Spark-free
    # tier. Execute only the pure function's source.
    start = src.index("_INTERNAL_COLS = {")
    end = src.index("def _emit_schema_warnings(")
    ns: dict = {}
    exec(compile(src[start:end], "01_bronze_ingest.py:drift", "exec"), ns)
    return ns["detect_schema_drift"]


def test_detect_schema_drift() -> None:
    print("\n-- T7: schema drift detection " + "-" * 41)
    detect = _load_detect_schema_drift()

    # known_cols maps source header -> (delta_name, type), per the notebook.
    known = {
        "Gateway Object Id": ("GatewayObjectId", "string"),
        "Request Id": ("RequestId", "string"),
        "Duration": ("QueryExecutionDuration", "long"),
    }
    internal = {
        "_source_file": "f",
        "_log_type": "t",
        "_gateway_host": "h",
        "_ingested_at": "i",
        "_extra_cols": "{}",
    }

    def rec(**cols):
        return [{**internal, **cols}]

    clean = rec(GatewayObjectId="g", RequestId="r", QueryExecutionDuration=1)
    d = detect(clean, known, "QueryExecution")
    check("clean schema -> severity none", d["severity"], "none")
    check("clean schema -> no missing", d["missing"], [])
    check("clean schema -> no added", d["added"], [])

    dropped = rec(GatewayObjectId="g", RequestId="r")
    d = detect(dropped, known, "QueryExecution")
    check("dropped column -> severity missing", d["severity"], "missing")
    check("dropped column identified", d["missing"], ["QueryExecutionDuration"])

    added = rec(
        GatewayObjectId="g", RequestId="r", QueryExecutionDuration=1, NewMetricMs=5
    )
    d = detect(added, known, "QueryExecution")
    check("added column -> severity added", d["severity"], "added")
    check("added column identified", d["added"], ["NewMetricMs"])
    check("added column -> nothing missing", d["missing"], [])

    # A rename presents as one column vanishing and another appearing. This is
    # the highest-value case and the OLD code could not see it at all, because
    # it only ever checked the missing direction.
    renamed = rec(GatewayObjectId="g", RequestId="r", ExecutionDurationMs=1)
    d = detect(renamed, known, "QueryExecution")
    check("rename -> severity both", d["severity"], "both")
    check("rename -> old name in missing", d["missing"], ["QueryExecutionDuration"])
    check("rename -> new name in added", d["added"], ["ExecutionDurationMs"])

    check("empty records -> severity none", detect([], known, "X")["severity"], "none")

    # Internal bookkeeping columns must never be reported as drift.
    d = detect(clean, known, "QueryExecution")
    check(
        "internal _-prefixed columns excluded from added",
        [c for c in d["added"] if c.startswith("_")],
        [],
    )


def main() -> int:
    print("=" * 72)
    print("MEDALLION INTEGRITY TESTS (roadmap T7, T8)")
    print("=" * 72)

    test_classify_read_failure()
    test_detect_schema_drift()

    print()
    print("-" * 72)
    if _FAILURES:
        print(f"RESULT: FAIL ({len(_FAILURES)}): {', '.join(_FAILURES)}")
        return 1
    print("RESULT: PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
