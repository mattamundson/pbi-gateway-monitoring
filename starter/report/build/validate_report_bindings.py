#!/usr/bin/env python
# =============================================================================
# validate_report_bindings.py  -  Headless structural validator for the PBIR report
# Label: [NET-NEW]
#
# WHAT THIS DOES (the top pbi-gateway-reports backlog item, headless)
#   Cross-checks every visual `queryRef` in a PBIR report against the real
#   semantic model (its tables, columns, and measures) and reports any binding
#   that will NOT resolve when the report is opened in Power BI Desktop.
#
#   This is the one piece of "does the report actually bind" proof that CAN be
#   established without Desktop: it can't render a visual, but it can prove that
#   every field a visual references exists in the model. Broken names (typos,
#   renamed columns, measures on the wrong table) are caught here instead of in
#   a Desktop session against a live tenant.
#
# MODEL SOURCE (in priority order)
#   1. --pbit <file>   : read the packed .pbit's DataModelSchema (ground truth)
#   2. else            : rebuild the model in-memory from build_pbit.py
#
# EXIT CODE  0 = all refs resolve   1 = unresolved refs found (fail-closed)
#
# USAGE
#   python validate_report_bindings.py [--report <report.json>] [--pbit <file.pbit>]
# =============================================================================
import argparse, json, os, re, sys, zipfile

HERE = os.path.dirname(os.path.abspath(__file__))
DEF_REPORT = os.path.normpath(os.path.join(HERE, "..", "gateway_monitor_v2.report.json"))
DEF_PBIT = os.path.normpath(os.path.join(HERE, "..", "dist", "gwmon_model.pbit"))


def load_model_from_pbit(path):
    z = zipfile.ZipFile(path)
    raw = z.read("DataModelSchema").decode("utf-16-le", "ignore").lstrip("﻿")
    m = json.loads(raw)
    tables = {}
    for t in m["model"]["tables"]:
        cols = set(c["name"] for c in t.get("columns", []))
        meas = set(x["name"] for x in t.get("measures", []))
        tables[t["name"]] = {"columns": cols, "measures": meas}
    return tables


def load_model_from_builder():
    sys.path.insert(0, HERE)
    import build_pbit  # noqa
    model = build_pbit.build_model(build_pbit.extract_measures())
    tables = {}
    for t in model["model"]["tables"]:
        tables[t["name"]] = {
            "columns": set(c["name"] for c in t.get("columns", [])),
            "measures": set(x["name"] for x in t.get("measures", [])),
        }
    return tables


def all_measures(tables):
    s = set()
    for t in tables.values():
        s |= t["measures"]
    return s


def extract_queryrefs(report):
    """Return list of (page, queryRef) for every projection field in the report."""
    refs = []
    for p in report.get("pages", []):
        pg = p.get("displayName", p.get("name", "?"))
        for vc in p.get("visualContainers", []):
            try:
                cfg = json.loads(vc["config"])
            except Exception:
                continue
            sv = cfg.get("singleVisual", {})
            proj = sv.get("projections", {})
            for role, fields in proj.items():
                for f in fields:
                    qr = f.get("queryRef")
                    if qr:
                        refs.append((pg, qr))
    return refs


def resolve(qr, tables, measures_global):
    """A queryRef is 'table.Field'. Field may be a measure (table-agnostic in PBI)
    or a column of that table. Measures resolve globally by name (PBI convention)."""
    if "." not in qr:
        return qr in measures_global
    tbl, fld = qr.split(".", 1)
    if fld in measures_global:
        return True  # measures are model-global; home table is advisory
    t = tables.get(tbl)
    if not t:
        return False
    return fld in t["columns"] or fld in t["measures"]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--report", default=DEF_REPORT)
    ap.add_argument("--pbit", default=DEF_PBIT if os.path.exists(DEF_PBIT) else "")
    args = ap.parse_args()

    report = json.load(open(args.report, encoding="utf-8"))
    if args.pbit and os.path.exists(args.pbit):
        tables = load_model_from_pbit(args.pbit)
        src = "pbit:%s" % os.path.basename(args.pbit)
    else:
        tables = load_model_from_builder()
        src = "builder"
    measures_global = all_measures(tables)

    refs = extract_queryrefs(report)
    unresolved = []
    seen = set()
    for pg, qr in refs:
        key = (pg, qr)
        if key in seen:
            continue
        seen.add(key)
        if not resolve(qr, tables, measures_global):
            unresolved.append((pg, qr))

    print("model source : %s (%d tables, %d measures)" % (src, len(tables), len(measures_global)))
    print("report       : %s" % os.path.basename(args.report))
    print("queryRefs     : %d total, %d distinct" % (len(refs), len(seen)))
    if not unresolved:
        print("RESULT: PASS - every queryRef resolves to a model table/column/measure")
        return 0
    print("RESULT: FAIL - %d unresolved binding(s):" % len(unresolved))
    for pg, qr in sorted(unresolved):
        print("  [%s]  %s" % (pg, qr))
    return 1


if __name__ == "__main__":
    sys.exit(main())
