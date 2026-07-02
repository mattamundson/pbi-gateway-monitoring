#!/usr/bin/env python3
"""
build_report.py  |  Label: [NET-NEW]
Generates the enhanced "Nexus Gateway & Fabric Observatory" PBIP project:
  - Nexus_Gateway_Observatory.Report/  (PBIR: report.json, definition.pbir, theme, page defs)
  - Nexus_Gateway_Observatory.Dataset/ (TMDL semantic model over DirectLake gold tables)
  - Nexus_Gateway_Observatory.pbip     (project pointer)

Design source: starter/report/REPORT-DESIGN-SPEC.md
Honesty: PBIR/TMDL JSON is structurally valid and references real gold/silver table
+ measure names, but MUST be opened once in Power BI Desktop (Sept 2024+) to finalize
DirectLake bindings and re-serialize queryRef/visualType exactly. No PBIX is emitted
here (proprietary binary; Desktop/Fabric-only) — see REPORT-DESIGN-SPEC.md "PBIX headwind".
"""
import json
import os
import pathlib

ROOT = pathlib.Path(__file__).resolve().parent
PROJ = "Nexus_Gateway_Observatory"
REPORT_DIR = ROOT / f"{PROJ}.Report"
DATASET_DIR = ROOT / f"{PROJ}.Dataset"

# ---- Nexus brand palette -----------------------------------------------------
BG = "#0B1F2A"
PRIMARY = "#00B3A4"
OK = "#2FBF71"
WARN = "#F4A83B"
CRIT = "#E5484D"
INFO = "#4C8DFF"
GRID = "#243B47"
TEXT = "#E8F1F2"

# ---- 12 pages: (display, job-to-be-done, [ (visualType, title, measure/field) ... ]) ----
PAGES = [
    ("Executive Cockpit", "Is anything on fire right now?", [
        ("card", "Open Risks", "[Open Risk Count]"),
        ("card", "Nodes Offline", "[Nodes Offline]"),
        ("card", "Match Rate %", "[Match Rate %]"),
        ("card", "Refresh Success %", "[Refresh Success %]"),
        ("card", "Gateway Error %", "[Gateway Error Rate %]"),
        ("card", "Active Items", "[Active Items]"),
        ("donutChart", "Fleet Status", "gold_dim_gateway[status_current]"),
        ("card", "Top Risk", "[Top Risk (text)]"),
        ("lineChart", "Error Rate (7d)", "[Gateway Error Rate %] by Date"),
    ]),
    ("Tenant Overview", "How much is in my tenant, and where?", [
        ("card", "Capacities", "[Capacity Count (tenant)]"),
        ("card", "Workspaces", "[Workspace Count (tenant)]"),
        ("card", "Reports", "[Reports (tenant)]"),
        ("card", "Semantic Models", "[Semantic Models (tenant)]"),
        ("card", "Notebooks", "[Notebooks (tenant)]"),
        ("card", "Pipelines", "[Pipelines (tenant)]"),
        ("card", "Lakehouses", "[Lakehouses (tenant)]"),
        ("map", "Capacity Regions", "gold_capacities[Region]"),
        ("lineClusteredColumnComboChart", "Consumption MoM", "[Activities (period)] + [Activities MoM %] by Date"),
        ("lineClusteredColumnComboChart", "Active Users MoM", "[Unique Active Users] + [Active Users MoM %] by Date"),
    ]),
    ("Timeline", "What happened on a given day?", [
        ("lineChart", "Activities", "[Activities (period)] by Date"),
        ("areaChart", "Unique Users", "[Unique Active Users] by Date"),
        ("lineChart", "Avg Refresh Duration (min)", "[Avg Refresh Duration (min)] by Date"),
        ("areaChart", "Refresh Activities", "[Refresh Failure Count] by Date"),
        ("lineChart", "Items Created", "[Active Items] by Date"),
    ]),
    ("Fleet Overview", "Is every gateway node alive and balanced?", [
        ("card", "Node Count", "[Node Count]"),
        ("card", "Cluster Count", "[Cluster Count]"),
        ("card", "Avg Gateway CPU %", "[Avg Gateway CPU %]"),
        ("card", "Load Skew", "[Cluster Load Skew Score]"),
        ("card", "Hottest Node %", "[Hottest Node Query %]"),
        ("matrix", "Fleet Status", "gold_dim_gateway by Node/Cluster/Version/Status"),
        ("clusteredBarChart", "Cluster Load Skew", "[Cluster Load Skew Score] by cluster"),
        ("lineChart", "Latency P95", "[Network Latency P95 (ms)] by Date"),
    ]),
    ("Query & Identity", "Who ran what, and how fast? (FLAGSHIP)", [
        ("card", "Total Queries", "[Total Queries]"),
        ("card", "Match Rate %", "[Match Rate %]"),
        ("card", "Avg Duration (ms)", "[Avg Query Duration (ms)]"),
        ("card", "P95 Duration (ms)", "[P95 Query Duration (ms)]"),
        ("card", "Error Rate %", "[Gateway Error Rate %]"),
        ("stackedColumnChart", "Attribution Confidence", "[Attribution % Evaluation Context]/[Attribution % Fuzzy Time Window]/[Attribution % Unattributed]"),
        ("clusteredBarChart", "Top Users by Load", "[Total Queries] by ExecutingUser"),
        ("clusteredBarChart", "Top Datasets", "[Total Queries] by ItemName"),
        ("matrix", "Query Detail", "DataSource x QueryType x artifact_id"),
    ]),
    ("Failures & Triage", "What failed, why, and which layer?", [
        ("card", "Total Errors", "[Total Errors]"),
        ("card", "Error Rate %", "[Gateway Error Rate %]"),
        ("card", "Triage Coverage %", "[Triage Coverage %]"),
        ("card", "Failures w/ OS Event", "[Failures With OS Event]"),
        ("lineChart", "Error Rate Trend", "[Gateway Error Rate %] by Date"),
        ("clusteredBarChart", "Failure Layer", "count by failure_layer"),
        ("matrix", "Triage Detail", "DataSource x ErrorMessage x layer x confidence"),
    ]),
    ("Capacity & Spool", "Are we running out of disk or CPU?", [
        ("card", "Avg CPU %", "[Avg Gateway CPU %]"),
        ("card", "Avg Mem (MB)", "[Avg Gateway Mem (MB)]"),
        ("card", "CPU Gap %", "[System CPU - Gateway CPU Gap %]"),
        ("card", "Spool Free %", "[Spool Free Space %]"),
        ("card", "Spool Status", "[Spool Disk Status]"),
        ("card", "Data Spooled (GB)", "[Total Data Spooled (GB)]"),
        ("lineChart", "CPU Trend", "[Avg Gateway CPU %] by Date"),
        ("lineChart", "Spool Free Trend", "[Spool Free Space %] by Date"),
        ("scatterChart", "Spool vs Duration", "[Spool Throughput (MB/s)] vs [Avg Query Duration (ms)]"),
    ]),
    ("Mashup Health", "Which mashup container is eating the server?", [
        ("card", "Peak Working Set (MB)", "[Peak Mashup Working Set (MB)]"),
        ("card", "Runaway Containers", "[Runaway Mashup Containers]"),
        ("card", "Avg Working Set (MB)", "[Avg Mashup Working Set (MB)]"),
        ("lineChart", "Working Set Trend", "[Peak Mashup Working Set (MB)] by Date"),
        ("clusteredBarChart", "By Gateway", "[Peak Mashup Working Set (MB)] by GatewayObjectId"),
    ]),
    ("Network & Bandwidth", "Is the network the real bottleneck?", [
        ("card", "Network Util %", "[Network Utilization %]"),
        ("card", "Network Status", "[Network Status]"),
        ("card", "Latency P95 (ms)", "[Network Latency P95 (ms)]"),
        ("lineChart", "Utilization Trend", "[Network Utilization %] by Date"),
        ("lineChart", "Latency Trend", "[Network Latency P95 (ms)] by Date"),
        ("scatterChart", "Spool Size vs Transfer", "[Total Data Spooled (GB)] vs [Network Utilization %]"),
    ]),
    ("Credential & Datasource Health", "Which datasource credentials are drifting?", [
        ("card", "Datasource Count", "[Datasource Count]"),
        ("card", "Datasources In Error", "[Datasources In Error]"),
        ("matrix", "Datasource Status", "gold_gateway_datasources by name/type/status/last-updated"),
        ("clusteredBarChart", "Status by Gateway", "count by GatewayClusterName x Status"),
    ]),
    ("Refresh Analytics", "How healthy are refreshes across the tenant?", [
        ("card", "Refresh Success %", "[Refresh Success %]"),
        ("card", "Refresh Failures", "[Refresh Failure Count]"),
        ("card", "Avg Duration (min)", "[Avg Refresh Duration (min)]"),
        ("clusteredBarChart", "Failures by Dataset", "[Refresh Failure Count] by DatasetName"),
        ("columnChart", "Duration Distribution", "[Avg Refresh Duration (min)] by DatasetName"),
        ("matrix", "Refresh Detail", "Workspace x Dataset x Status x Duration"),
    ]),
    ("About · Lineage & Honesty", "What is this, and how much do we trust it?", [
        ("textbox", "Data lineage", "collector -> bronze -> silver -> gold -> page map"),
        ("textbox", "Status legend", "[Verified] / [Desk-Verified] / [Unverified] / [STUB]"),
        ("textbox", "Attribution methodology", "RequestId == XmlaRequestId; best-effort disclaimer"),
    ]),
]


def theme():
    return {
        "name": "NexusGatewayObservatory",
        "dataColors": [PRIMARY, INFO, OK, WARN, CRIT, "#9B7BF0", "#00D4C0", "#7A93A3"],
        "background": BG, "foreground": TEXT, "tableAccent": PRIMARY,
        "good": OK, "neutral": WARN, "bad": CRIT,
        "visualStyles": {
            "*": {"*": {
                "background": [{"color": {"solid": {"color": "#102A38"}}, "transparency": 0}],
                "border": [{"color": {"solid": {"color": GRID}}, "show": True}],
                "title": [{"fontColor": {"solid": {"color": TEXT}}, "fontSize": 11, "fontFamily": "Segoe UI Semibold"}],
                "labels": [{"color": {"solid": {"color": TEXT}}}],
            }},
            "page": {"*": {"background": [{"color": {"solid": {"color": BG}}, "transparency": 0}]}},
        },
    }


def make_visual(vtype, title, field, x, y, w, h):
    # Minimal but structurally-valid PBIR visualContainer. Desktop re-serializes
    # queryRef/config on first open (see R1-R3 caveats).
    return {
        "x": x, "y": y, "z": 0, "width": w, "height": h,
        "config": json.dumps({
            "name": title.replace(" ", "_"),
            "singleVisual": {
                "visualType": vtype,
                "drillFilterOtherVisuals": True,
                "vcObjects": {"title": [{"properties": {"text": {"expr": {"Literal": {"Value": f"'{title}'"}}}}}]},
            },
        }),
        "filters": "[]",
        "_field_hint": field,  # human-readable binding target for Desktop wiring
    }


def build_page(display, job, visuals):
    # grid layout: cards row across top, larger visuals below
    containers = []
    cards = [v for v in visuals if v[0] in ("card", "textbox")]
    charts = [v for v in visuals if v[0] not in ("card", "textbox")]
    cx = 200
    for i, (vt, title, fld) in enumerate(cards):
        containers.append(make_visual(vt, title, fld, cx + (i % 6) * 178, 40 + (i // 6) * 90, 170, 80))
    top = 40 + (len(cards) // 6 + 1) * 90
    for i, (vt, title, fld) in enumerate(charts):
        col = i % 2
        row = i // 2
        containers.append(make_visual(vt, title, fld, 200 + col * 545, top + row * 210, 535, 200))
    return {
        "name": display, "displayName": display,
        "displayOption": "FitToPage", "width": 1280, "height": 720,
        "_job_to_be_done": job,
        "visualContainers": containers,
    }


def main():
    (REPORT_DIR / "StaticResources" / "SharedResources" / "BaseThemes").mkdir(parents=True, exist_ok=True)
    DATASET_DIR.mkdir(parents=True, exist_ok=True)

    # theme
    (REPORT_DIR / "StaticResources" / "SharedResources" / "BaseThemes" / "nexus_theme.json").write_text(
        json.dumps(theme(), indent=2))

    # report.json
    report = {
        "$schema": "https://developer.microsoft.com/json-schemas/fabric/gitIntegration/pbir/definition/report/1.0.0/schema.json",
        "name": "Nexus Gateway & Fabric Observatory",
        "_label": "[NET-NEW] enhanced report — 12 pages, gateway depth + tenant breadth",
        "_honesty_note": "PBIR structurally valid; open once in Desktop to finalize DirectLake bindings. No PBIX emitted (Desktop/Fabric-only). Tenant pages render empty until the tenant-extract pipeline lands gold_capacities/activities/inventory/refreshables.",
        "version": "5.0",
        "themeCollection": {"baseTheme": {"name": "nexus_theme", "reportVersionAtImport": "5.0", "type": "SharedResources"}},
        "pages": [build_page(d, j, v) for (d, j, v) in PAGES],
        "config": json.dumps({"version": "5.0", "activePageName": PAGES[0][0]}),
    }
    (REPORT_DIR / "report.json").write_text(json.dumps(report, indent=2))

    # definition.pbir -> points at the sibling dataset folder
    (REPORT_DIR / "definition.pbir").write_text(json.dumps({
        "$schema": "https://developer.microsoft.com/json-schemas/fabric/gitIntegration/pbir/2.0.0/schema.json",
        "version": "5.0",
        "datasetReference": {"byPath": {"path": f"../{PROJ}.Dataset"}},
    }, indent=2))

    # .pbip project pointer
    (ROOT / f"{PROJ}.pbip").write_text(json.dumps({
        "$schema": "https://developer.microsoft.com/json-schemas/fabric/pbip/pbipProperties/1.0.0/schema.json",
        "version": "1.0",
        "artifacts": [{"report": {"path": f"{PROJ}.Report"}}],
        "settings": {"enableAutoRecovery": True},
    }, indent=2))

    print(f"Report: {len(PAGES)} pages, {sum(len(v) for _,_,v in PAGES)} visuals -> {REPORT_DIR/'report.json'}")


if __name__ == "__main__":
    main()
