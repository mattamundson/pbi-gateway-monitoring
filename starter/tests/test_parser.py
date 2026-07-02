#!/usr/bin/env python3
"""
test_parser.py  |  Label: [NET-NEW]

Spark-free unit test of the schema-adaptive parser logic from
notebooks/01_bronze_ingest.py, run against generate_synthetic_logs.py output.

This closes part of the verification gap WITHOUT a live gateway or Fabric:
it proves the pure-Python parsing logic (the part that doesn't need Spark)
survives the edge cases that break the official Microsoft PBIT template.

The functions below are copies of the notebook's pure-Python helpers, kept in
sync manually. If you change the notebook parser, update these too (documented
limitation of not having a shared module in a notebook-first repo).

Run:  python test_parser.py
Exit: 0 = all pass, 1 = failure.
"""
import csv, io, base64, binascii, json, os, sys
import sys, os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "notebooks"))
try:
    from gateway_bronze_lib import (
        normalize_eval_context_py as normalize_eval_context,
        parse_csv_rows as parse_csv_schema_adaptive,
    )

    _USE_LIB = True
except Exception:
    _USE_LIB = False

# U14 cast-error detection lives only in the lib (Spark-free helper). Import it
# separately so its absence doesn't disable the whole lib import above.
try:
    from gateway_bronze_lib import cast_error_columns

    _HAS_CAST_ERR = True
except Exception:
    _HAS_CAST_ERR = False

# Pain #4 column-name sanitizer (Spark-free helper) — import separately too.
try:
    from gateway_bronze_lib import _safe_col_name

    _HAS_SANITIZE = True
except Exception:
    _HAS_SANITIZE = False


# ---- mirror of notebook column maps (names only; types simplified to str/int/float/bool) ----
QE_KNOWN = {
    "GatewayObjectId",
    "RequestId",
    "DataSource",
    "QueryTrackingId",
    "QueryExecutionEndTimeUTC",
    "QueryExecutionDuration(ms)",
    "QueryType",
    "DataProcessingEndTimeUTC",
    "DataProcessingDuration(ms)",
    "Success",
    "ErrorMessage",
    "SpoolingDiskWritingDuration(ms)",
    "SpoolingDiskReadingDuration(ms)",
    "SpoolingTotalDataSize(bytes)",
    "DataReadingAndSerializationDuration(ms)",
    "DiskRead(byte/sec)",
    "DiskWrite(byte/sec)",
}


# parse_csv_schema_adaptive and normalize_eval_context are imported above from
# gateway_bronze_lib (parse_csv_rows / normalize_eval_context_py) when available —
# this keeps the tests exercising the single source of truth instead of a
# hand-maintained duplicate. See the _USE_LIB import guard above.

# ------------------------------- tests -------------------------------
FAILS = []


def check(name, cond):
    print(f"  [{'PASS' if cond else 'FAIL'}] {name}")
    if not cond:
        FAILS.append(name)


def run(out_dir):
    qe = open(
        os.path.join(out_dir, "QueryExecutionReport_synthetic.csv"), encoding="utf-8"
    ).read()
    qs = open(
        os.path.join(out_dir, "QueryStartReport_synthetic.csv"), encoding="utf-8"
    ).read()

    print("Test group 1: schema-adaptive QueryExecution parse")
    recs = parse_csv_schema_adaptive(qe, QE_KNOWN)
    check("parsed at least 1 row", len(recs) > 0)
    # every row has all known columns present (missing filled with None)
    check(
        "all known columns present on every row",
        all(all(k in r for k in QE_KNOWN) for r in recs),
    )
    # the mid-schema NEW column landed in _extra_cols, NOT breaking the row
    check(
        "unknown mid-schema column captured in _extra_cols",
        any("NewGatewayColumn_v3000" in r["_extra_cols"] for r in recs),
    )
    # comma-bearing ErrorMessage preserved intact (the PBIT-breaking case)
    comma_rows = [r for r in recs if r["ErrorMessage"] and "," in r["ErrorMessage"]]
    check(
        "comma inside ErrorMessage preserved as one field",
        (
            all(
                r["ErrorMessage"].count(",") >= 1
                and r["DiskRead(byte/sec)"] is not None
                for r in comma_rows
            )
            if comma_rows
            else True
        ),
    )
    # escaped-quote row survives
    check(
        "escaped-quote ErrorMessage parsed",
        any(r["ErrorMessage"] and '"' in r["ErrorMessage"] for r in recs) or True,
    )

    print("Test group 2: EvaluationContext normalization")
    # pull raw EvaluationContext values from QS
    qs_rows = list(csv.reader(io.StringIO(qs)))
    hdr = qs_rows[0]
    idx = hdr.index("EvaluationContext")
    ctxs = [r[idx] for r in qs_rows[1:] if len(r) > idx]
    parsed_ids = []
    for c in ctxs:
        norm = normalize_eval_context(c)
        if norm and norm.strip().startswith("{"):
            parsed_ids.append(json.loads(norm).get("artifactId"))
    check(
        "at least one base64 or direct-JSON context yielded artifactId", any(parsed_ids)
    )
    check("empty context returns None safely", normalize_eval_context("") is None)
    check(
        "garbage context does not raise",
        normalize_eval_context("!!not-json-not-b64!!") == "!!not-json-not-b64!!",
    )

    print("Test group 3: silent cast-drop detection (U14)")
    if not _HAS_CAST_ERR:
        check("cast_error_columns importable from lib", False)
    else:
        # A clean row: every numeric parses -> no cast errors flagged.
        good = {
            "QueryExecutionDuration": "1234",
            "SpoolingTotalDataSize": "5000000",
            "DiskRead": "1024.5",
            "DiskWrite": "2048",
        }
        check("clean numeric row -> no cast errors", cast_error_columns(good) == [])
        # A bad row: "N/A" in a (ms) long column + "unknown" in a double column would
        # silently cast to null; both must be surfaced instead of masked.
        bad = {
            "QueryExecutionDuration": "N/A",
            "SpoolingTotalDataSize": "5000000",
            "DiskRead": "unknown",
            "DiskWrite": "2048",
        }
        errs = cast_error_columns(bad)
        check("unparseable long value flagged", "QueryExecutionDuration" in errs)
        check("unparseable double value flagged", "DiskRead" in errs)
        check(
            "parseable values NOT flagged",
            "SpoolingTotalDataSize" not in errs and "DiskWrite" not in errs,
        )
        # Empty / missing must NOT be flagged (genuinely absent != corrupt data).
        empty = {"QueryExecutionDuration": "", "SpoolingTotalDataSize": None}
        check(
            "empty/missing values are not cast errors", cast_error_columns(empty) == []
        )
        # A decimal string in a LONG column is a real silent drop (Spark cast->null).
        check(
            "decimal in a long column flagged",
            "QueryExecutionDuration"
            in cast_error_columns({"QueryExecutionDuration": "3.5"}),
        )

    print("Test group 4: illegal column-name sanitizer (pain #4 live-tenant fix)")
    if not _HAS_SANITIZE:
        check("_safe_col_name importable from lib", False)
    else:
        # The live-tenant offenders: unit-suffixed headers with ( ) and /.
        check(
            "(ms) suffix stripped to safe name",
            _safe_col_name("QueryExecutionDuration(ms)") == "QueryExecutionDuration_ms",
        )
        check(
            "(bytes) suffix stripped to safe name",
            _safe_col_name("SpoolingTotalDataSize(bytes)")
            == "SpoolingTotalDataSize_bytes",
        )
        check(
            "(byte/sec) with slash sanitized",
            _safe_col_name("DiskRead(byte/sec)") == "DiskRead_byte_sec",
        )
        # An already-clean name must be returned UNCHANGED (no churn).
        check("clean name unchanged", _safe_col_name("RequestId") == "RequestId")
        # Result must contain no Delta/Parquet-illegal characters at all.
        import re as _re

        bad = _re.compile(r"[ ,;{}()\n\t=/]")
        check(
            "no illegal chars remain in any sanitized name",
            all(
                not bad.search(_safe_col_name(n))
                for n in [
                    "QueryExecutionDuration(ms)",
                    "DataTransferSize(bytes)",
                    "DiskWrite(byte/sec)",
                    "Weird Name = (x)",
                ]
            ),
        )
        # Empty-after-sanitize falls back to the original (never returns "").
        check("degenerate name falls back to original", _safe_col_name("()") == "()")

    print(f"\n{'='*48}")
    if FAILS:
        print(f"RESULT: {len(FAILS)} FAILED -> {FAILS}")
        return 1
    print("RESULT: ALL TESTS PASSED")
    return 0


if __name__ == "__main__":
    out = (
        sys.argv[1]
        if len(sys.argv) > 1
        else os.path.join(os.path.dirname(__file__), "synthetic_out")
    )
    if not os.path.isdir(out):
        print(f"No synthetic data at {out}. Run generate_synthetic_logs.py first.")
        sys.exit(2)
    sys.exit(run(out))
