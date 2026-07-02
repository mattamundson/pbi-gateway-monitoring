#!/usr/bin/env python
# =============================================================================
# build_pbit.py  -  Headless PBIT (Power BI template) builder for gwmon
# Label: [NET-NEW]
#
# WHAT THIS DOES
#   Regenerates a DirectLake semantic-model *.pbit* (Power BI template) with ZERO
#   Power BI Desktop involvement. It:
#     1. parses every measure out of ../../semantic-model/measures.dax + measures_v2.dax
#     2. assembles a TMSL model (12 tables, DirectLake partitions, a _Measures table)
#     3. writes a minimal PbixProj folder
#     4. packs it into a .pbit via pbi-tools.core (compile ... PBIT)
#
# WHY .pbit AND NOT .pbix
#   A model-bearing *.pbix* embeds a *serialized Analysis Services database* that
#   ONLY Power BI Desktop's engine can write. The one headless tool that exists,
#   pbi-tools.core, states this in its own --help:
#       "the PBIX output is supported only for report-only projects (thin reports),
#        and PBIT for projects containing a data model."
#   So a model .pbix is categorically Desktop-only; a .pbit (template) is the
#   correct, fully-headless deliverable and is in fact the better distributable:
#   it opens in Desktop, prompts for the Lakehouse connection, and refreshes.
#
# HONESTY LABEL: [Unverified]
#   The TMSL packs and the .pbit is a valid OPC package, but DAX correctness and
#   the DirectLake binding are only proven when opened in Desktop against a live
#   Fabric Lakehouse (the same F2/tenant gate that blocks the rest of the tool).
#
# USAGE
#   python build_pbit.py --pbitools <path-to-pbi-tools.core.exe> [--out <file.pbit>]
#   (download pbi-tools.core from https://github.com/pbi-tools/pbi-tools/releases)
# =============================================================================
import argparse, json, os, re, subprocess, sys, tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
SM = os.path.normpath(os.path.join(HERE, "..", "..", "semantic-model"))

# Curated table -> columns (matches report queryRefs + 02/03 notebook output).
SCHEMA = {
 "gold_gateway_health": [("GatewayObjectId","string"),("last_heartbeat_utc","dateTime"),("heartbeat_age_minutes","int64"),
    ("cpu_utilization_pct","double"),("cpu_pct_avg","double"),("mem_mb_avg","double"),("system_cpu_pct","double"),
    ("network_util_pct","double"),("network_latency_p95_ms","double"),("spool_free_pct","double"),("_partition_date","dateTime")],
 "gold_query_performance": [("QueryType","string"),("DataSource","string"),("query_count","int64"),("error_count","int64"),
    ("duration_ms_avg","double"),("duration_ms_p95","double"),("duration_ms_max","double"),("spool_total_bytes_sum","double"),
    ("spool_writing_ms_avg","double"),("spooled_query_count","int64"),("hour_utc","dateTime"),("_partition_date","dateTime")],
 "gold_cluster_load": [("GatewayClusterName","string"),("cpu_utilization_pct","double"),("queries_per_node_cv","double"),
    ("load_skew_classification","string"),("hottest_node_query_pct","double")],
 "gold_dim_gateway": [("GatewayObjectId","string"),("GatewayClusterName","string"),("GatewayNodeName","string"),
    ("Version","string"),("Status","string"),("is_current","boolean"),("valid_from","dateTime"),("valid_to","dateTime")],
 "gold_mashup_health": [("container_pid","string"),("peak_working_set_mb","double"),("avg_cpu_pct","double"),("is_runaway","boolean")],
 "silver_triage": [("event_time","dateTime"),("root_cause_class","string"),("failure_layer","string"),
    ("triage_confidence","string"),("refresh_status","string"),("is_failure","boolean"),("evt_message","string"),("EventId","string"),
    ("DataSource","string"),("ErrorMessage","string"),("GatewayObjectId","string")],
 "silver_identity_attribution": [("attribution_kind","string"),("artifact_id","string"),("DataSource","string"),("attribution_confidence","string")],
 "dim_date": [("Date","dateTime"),("Year","int64"),("MonthName","string"),("MonthNumber","int64")],
 "bronze_activity_events": [("UserId","string"),("event_time","dateTime"),("Activity","string")],
 "bronze_capacity_refreshables": [("refresh_time","dateTime"),("DatasetId","string"),("status","string")],
 "bronze_capacity_metrics": [("timestamp","dateTime"),("cu_percentage","double"),("CapacityId","string")],
}


def _parse_bracket(txt):
    """measures.dax style:  [Name] := <expr>"""
    out = {}
    parts = re.split(r'(?m)^\[([^\]]+)\]\s*:=', txt)
    for i in range(1, len(parts), 2):
        out[parts[i].strip()] = parts[i + 1].strip()
    return out


def _parse_eq(txt):
    """measures_v2.dax style:  Name = <expr>  (col-0 boundary, VAR/RETURN excluded)"""
    out = {}; cur = None; buf = []
    def flush():
        if cur:
            out[cur] = "\n".join(buf).strip()
    for ln in txt.splitlines():
        s = ln.strip()
        m = re.match(r'^([A-Za-z][A-Za-z0-9 %()/._-]*?)\s*=\s*(.*)$', ln)
        boundary = (m and not ln.startswith(("//", " ", "\t"))
                    and not s.startswith(("VAR ", "RETURN")) and "==" not in ln)
        if boundary:
            flush(); cur = m.group(1).strip(); buf = [m.group(2)]
        elif cur is not None:
            buf.append(ln)
    flush(); return out


def _clean(e):
    lines = e.splitlines()
    while lines and (lines[-1].strip() == "" or lines[-1].strip().startswith("//")):
        lines.pop()
    # strip non-ascii (dax comment box-drawing chars) so TMSL stays clean
    return "".join(c for c in "\n".join(lines).strip() if ord(c) < 128)


def extract_measures():
    m1 = _parse_bracket(open(os.path.join(SM, "measures.dax"), encoding="utf-8").read())
    m2 = _parse_eq(open(os.path.join(SM, "measures_v2.dax"), encoding="utf-8").read())
    out = {}
    for k, v in list(m1.items()) + list(m2.items()):
        if k.startswith(("VAR ", "RETURN")):
            continue
        v = _clean(v)
        if v:
            out[k] = v
    return out


def build_model(measures):
    def col(n, dt):
        c = {"name": n, "dataType": dt, "sourceColumn": n, "summarizeBy": "none"}
        return c
    tables = []
    for t, cols in SCHEMA.items():
        cobjs = [col(n, dt) for n, dt in cols]
        if t == "dim_date":
            for c in cobjs:
                if c["name"] == "Date":
                    c["dataCategory"] = "PaddedDateTableDates"
        tables.append({
            "name": t, "columns": cobjs,
            "partitions": [{"name": t, "mode": "directLake",
                "source": {"type": "entity", "entityName": t, "schemaName": "dbo",
                           "expressionSource": "DirectLakeSource"}}],
        })
    tables.append({
        "name": "_Measures", "isHidden": True,
        "columns": [{"name": "_", "dataType": "int64", "sourceColumn": "[_]",
                     "summarizeBy": "none", "isHidden": True}],
        "partitions": [{"name": "_Measures", "mode": "import",
                        "source": {"type": "calculated", "expression": "ROW(\"_\", 1)"}}],
        "measures": [{"name": n, "expression": e} for n, e in sorted(measures.items())],
    })
    return {
        "name": "gwmon_directlake_model", "compatibilityLevel": 1604,
        "model": {
            "culture": "en-US", "defaultPowerBIDataSourceVersion": "powerBI_V3",
            "sourceQueryCulture": "en-US",
            "expressions": [{
                "name": "DirectLakeSource", "kind": "m",
                "expression": (
                    "let\n"
                    "    // [Unverified] Placeholder DirectLake source. In Power BI Desktop, repoint\n"
                    "    // this to your deployed Lakehouse SQL analytics endpoint (Fabric workspace).\n"
                    "    Source = Sql.Database(\"<lakehouse-sql-endpoint>.datawarehouse.fabric.microsoft.com\", \"gwmon_lakehouse\")\n"
                    "in\n    Source"),
            }],
            "tables": tables,
            "relationships": [
                {"name": "date_health", "fromTable": "gold_gateway_health", "fromColumn": "last_heartbeat_utc",
                 "toTable": "dim_date", "toColumn": "Date", "crossFilteringBehavior": "oneDirection"},
                {"name": "date_triage", "fromTable": "silver_triage", "fromColumn": "event_time",
                 "toTable": "dim_date", "toColumn": "Date", "crossFilteringBehavior": "oneDirection"},
            ],
            "annotations": [{"name": "__PBI_TimeIntelligenceEnabled", "value": "0"}],
        },
    }


PARTS = {
    ".pbixproj.json": '{ "version": "0.12", "created": "2026-07-02", "settings": {} }',
    "Version.txt": "1.28",
    "ReportMetadata.json": '{ "Version": 3, "AutoCreatedRelationships": [], "FileDescription": "", "CreatedFrom": "Cloud", "CreatedFromRelease": "2.130" }',
    "ReportSettings.json": '{ "Version": 3, "UseStylableVisualContainerHeader": true }',
}


def main():
    ap = argparse.ArgumentParser()
    # Auto-discover pbi-tools.core: --pbitools > $PBITOOLS_CORE > pinned ~/.local copy.
    _pinned = os.path.join(os.path.expanduser("~"), ".local", "pbi-tools-core", "pbi-tools.core.exe")
    _default = os.environ.get("PBITOOLS_CORE", "") or (_pinned if os.path.exists(_pinned) else "")
    ap.add_argument("--pbitools", default=_default,
                    help="path to pbi-tools.core.exe (default: $PBITOOLS_CORE or ~/.local/pbi-tools-core)")
    ap.add_argument("--out", default=os.path.join(HERE, "..", "dist", "gwmon_model.pbit"))
    args = ap.parse_args()

    measures = extract_measures()
    model = build_model(measures)
    nmeas = len(model["model"]["tables"][-1]["measures"])
    print("parsed measures: %d | tables: %d" % (nmeas, len(model["model"]["tables"])))

    work = tempfile.mkdtemp(prefix="gwmon_ppj_")
    os.makedirs(os.path.join(work, "Model"), exist_ok=True)
    json.dump(model, open(os.path.join(work, "Model", "database.json"), "w"), indent=2)
    for name, content in PARTS.items():
        open(os.path.join(work, name), "w").write(content)

    out = os.path.normpath(args.out)
    os.makedirs(os.path.dirname(out), exist_ok=True)
    if not args.pbitools or not os.path.exists(args.pbitools):
        print("PbixProj written to: %s" % work)
        print("NOTE: pbi-tools.core not provided; skipping pack. "
              "Re-run with --pbitools <exe> to emit the .pbit.", file=sys.stderr)
        return 0
    rc = subprocess.call([args.pbitools, "compile", work, out, "PBIT", "True"])
    if rc == 0 and os.path.exists(out):
        print("PBIT written: %s (%d bytes)" % (out, os.path.getsize(out)))
    else:
        print("pbi-tools.core compile failed rc=%d" % rc, file=sys.stderr)
    return rc


if __name__ == "__main__":
    sys.exit(main())
