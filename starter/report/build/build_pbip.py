#!/usr/bin/env python
# =============================================================================
# build_pbip.py  -  Assemble a Power BI Project (PBIP) for a one-double-click open
# Label: [NET-NEW] · [Unverified]
#
# Produces starter/report/pbip/ = a complete PBIP:
#   gwmon.pbip                              (double-click target)
#   gwmon.Report/{definition.pbir, report.json}
#   gwmon.SemanticModel/{definition.pbism, definition/*.tmdl}
#
# HOW: reuses build_pbit.build_model() to emit the TMSL, converts it to TMDL via
# `pbi-tools.core convert`, drops in the v2 report, and writes the wrapper files.
# Opening gwmon.pbip in Power BI Desktop loads the model + the 14-page report
# together, so the manual pass is: repoint DirectLakeSource -> Save As .pbix.
#
# USAGE:  python build_pbip.py [--pbitools <exe>]   (auto-discovers pinned copy)
# =============================================================================
import argparse, json, os, shutil, subprocess, sys, tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
REPORT_DIR = os.path.normpath(os.path.join(HERE, ".."))
PBIP_DIR = os.path.join(REPORT_DIR, "pbip")
V2_REPORT = os.path.join(REPORT_DIR, "gateway_monitor_v2.report.json")

PBISM = '{\n  "version": "4.0",\n  "settings": {}\n}\n'
PBIR = ('{\n  "$schema": "https://developer.microsoft.com/json-schemas/fabric/gitIntegration/pbir/2.0.0/schema.json",\n'
        '  "version": "5.0",\n  "datasetReference": {\n    "byPath": {\n      "path": "../gwmon.SemanticModel"\n    }\n  }\n}\n')
PBIP = ('{\n  "$schema": "https://developer.microsoft.com/json-schemas/fabric/item/pbip/definition/1.0.0/schema.json",\n'
        '  "version": "1.0",\n  "artifacts": [\n    { "report": { "path": "gwmon.Report" } }\n  ],\n'
        '  "settings": {\n    "enableAutoRecovery": true\n  }\n}\n')


def resolve_pbitools(arg):
    pinned = os.path.join(os.path.expanduser("~"), ".local", "pbi-tools-core", "pbi-tools.core.exe")
    return arg or os.environ.get("PBITOOLS_CORE", "") or (pinned if os.path.exists(pinned) else "")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--pbitools", default="")
    args = ap.parse_args()
    tool = resolve_pbitools(args.pbitools)
    if not tool or not os.path.exists(tool):
        print("pbi-tools.core not found (need it for TMSL->TMDL). Set PBITOOLS_CORE or --pbitools.",
              file=sys.stderr)
        return 2

    sys.path.insert(0, HERE)
    import build_pbit  # noqa
    model = build_pbit.build_model(build_pbit.extract_measures())

    tmp = tempfile.mkdtemp(prefix="gwmon_tmsl_")
    tmsl = os.path.join(tmp, "model.json")
    json.dump(model, open(tmsl, "w"), indent=2)
    tmdl_out = os.path.join(tmp, "tmdl")
    rc = subprocess.call([tool, "convert", tmsl, tmdl_out])
    if rc != 0:
        print("convert failed rc=%d" % rc, file=sys.stderr)
        return rc

    # assemble
    if os.path.exists(PBIP_DIR):
        shutil.rmtree(PBIP_DIR)
    sm_def = os.path.join(PBIP_DIR, "gwmon.SemanticModel", "definition")
    rpt = os.path.join(PBIP_DIR, "gwmon.Report")
    os.makedirs(sm_def)
    os.makedirs(rpt)
    for root, _dirs, files in os.walk(tmdl_out):
        rel = os.path.relpath(root, tmdl_out)
        dst = sm_def if rel == "." else os.path.join(sm_def, rel)
        os.makedirs(dst, exist_ok=True)
        for f in files:
            shutil.copy2(os.path.join(root, f), os.path.join(dst, f))
    open(os.path.join(PBIP_DIR, "gwmon.SemanticModel", "definition.pbism"), "w").write(PBISM)
    shutil.copy2(V2_REPORT, os.path.join(rpt, "report.json"))
    open(os.path.join(rpt, "definition.pbir"), "w").write(PBIR)
    open(os.path.join(PBIP_DIR, "gwmon.pbip"), "w").write(PBIP)

    ntables = len(model["model"]["tables"])
    nmeas = len(model["model"]["tables"][-1]["measures"])
    print("PBIP assembled at %s (%d tables, %d measures)" % (PBIP_DIR, ntables, nmeas))
    return 0


if __name__ == "__main__":
    sys.exit(main())
