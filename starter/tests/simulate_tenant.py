#!/usr/bin/env python3
"""
simulate_tenant.py  |  Label: [NET-NEW] [Simulated — not tenant-verified]
=============================================================================

Correlated multi-source simulator for the On-Premises Data Gateway + Fabric
Workspace Monitoring pipeline.

WHAT THIS PROVES (structurally):
  - The identity join (pain #3) can structurally execute: gateway RequestId ==
    PowerBIDatasetsWorkspace OperationId, producing ExecutingUser + ItemName.
  - The triage join (pain #2) fires on correlated failure + refresh data.
  - EvaluationContext encoding variety (base64 + direct JSON) is handled.
  - ~15% gateway events intentionally have NO matching Workspace Monitoring row,
    producing a realistic ~85% match-rate rather than a fake 100%.

WHAT THIS CANNOT PROVE [Simulated]:
  - Real Microsoft EvaluationContext encoding specifics on a live tenant.
  - True PowerBIDatasetsWorkspace column names / schema (confirmed desk-verified
    against MS Learn 2025-11-20 but not live-tenant tested).
  - Actual tenant match-rate (depends on gateway routing, non-Fabric workloads,
    Log Analytics vs Workspace Monitoring co-existence).
  - Activator alert triggers or KQL diffpatterns behaviour in Fabric Eventhouse.

A real-tenant pilot remains the final gate before [Verified] status.

Usage:
    python simulate_tenant.py [output_dir] [--n-events N] [--seed SEED]

Outputs to output_dir (default: starter/tests/sim_out/):
    QueryStartReport_sim.csv
    QueryExecutionReport_sim.csv
    PowerBIDatasetsWorkspace_sim.csv
    SystemCounter_sim.csv
    MashupProcesses_sim.ndjson
    NetworkMetrics_sim.csv
    RefreshHistory_sim.json
=============================================================================
"""

import argparse
import base64
import csv
import io
import json
import os
import random
import sys
import uuid
from datetime import datetime, timedelta, timezone

# ── Seed / reproducibility ────────────────────────────────────────────────────
DEFAULT_SEED = 42
DEFAULT_N_EVENTS = 200
SIM_DAYS = 3
MATCH_RATE = 0.85          # ~85% of gateway events get a Workspace Monitoring row

# ── Fake domain data ──────────────────────────────────────────────────────────
DATASETS = [
    {"id": "ds-fabric-001", "name": "Finance Actuals v2", "workspace": "ws-finance-001"},
    {"id": "ds-fabric-002", "name": "Sales Pipeline",      "workspace": "ws-sales-001"},
    {"id": "ds-fabric-003", "name": "HR Headcount",        "workspace": "ws-hr-001"},
    {"id": "ds-fabric-004", "name": "Supply Chain KPIs",   "workspace": "ws-ops-001"},
    {"id": "ds-fabric-005", "name": "Exec Dashboard",      "workspace": "ws-exec-001"},
    {"id": "ds-fabric-006", "name": "Risk & Compliance",   "workspace": "ws-risk-001"},
]
USERS = [
    "alice@contoso.com",
    "bob@contoso.com",
    "carol@contoso.com",
    "dave@contoso.com",
    "eve@contoso.com",
    "frank@contoso.com",
    "grace@contoso.com",
    "heidi@contoso.com",
    "ivan@contoso.com",
    "judy@contoso.com",
]
DATASOURCES = ["SqlProd", "OracleFin", "SharePointOnPrem", "PostgresAnalytics", "TeradataEDW"]
GATEWAY_NODES = ["gw-001", "gw-002", "gw-003"]
CAPACITY_IDS = ["cap-A1-sku", "cap-F64-sku"]
OPERATION_NAMES = ["QueryEnd", "ExecutionMetrics", "RefreshEnd"]

ERROR_MESSAGES = [
    "",  # success — no error
    "Timeout expired, retry failed, code 42",
    'Login failed for user "svc-etl", account locked',
    "The operation was canceled.",
    "Spool limit exceeded: 512 MB",
    "Network path not reachable: OracleFin.prod.contoso.com",
    "Credential refresh required: OAuth token expired",
]

# ── Time helpers ──────────────────────────────────────────────────────────────
SIM_BASE_TIME = datetime(2026, 6, 28, 0, 0, 0, tzinfo=timezone.utc)


def _ts(base: datetime, offset_seconds: int) -> str:
    return (base + timedelta(seconds=offset_seconds)).strftime("%Y-%m-%dT%H:%M:%SZ")


def _mint_request_id() -> str:
    """Mint a GUID-style RequestId identical in structure to real gateway logs."""
    return str(uuid.uuid4()).upper()


def _mint_tracking_id() -> str:
    return str(uuid.uuid4()).upper()


# ── EvaluationContext helpers ─────────────────────────────────────────────────
def _eval_context(artifact_id: str, kind: str = "SemanticModel", encoding: str = "json") -> str:
    """Produce EvaluationContext in base64 or direct-JSON form (or empty for non-Fabric)."""
    obj = {"artifactId": artifact_id, "artifactKind": kind}
    js = json.dumps(obj)
    if encoding == "b64":
        return base64.b64encode(js.encode()).decode()
    elif encoding == "json":
        return js
    else:
        return ""  # non-Fabric workload


# ── Main event pool generator ─────────────────────────────────────────────────
def generate_event_pool(n: int, seed: int):
    """
    Generate n correlated query events.  Each event carries shared IDs that
    link gateway logs ↔ PowerBIDatasetsWorkspace ↔ refresh-history.
    Returns a list of event dicts.
    """
    rng = random.Random(seed)

    events = []
    day_span = SIM_DAYS * 24 * 3600  # total seconds in simulation window

    for i in range(n):
        request_id = _mint_request_id()
        tracking_id = _mint_tracking_id()
        dataset = rng.choice(DATASETS)
        user = rng.choice(USERS)
        datasource = rng.choice(DATASOURCES)
        gw_node = rng.choice(GATEWAY_NODES)
        query_type = rng.choice(["Refresh", "DirectQuery", "Refresh", "Refresh"])  # skew to refresh
        capacity = rng.choice(CAPACITY_IDS)

        # Spread events across the simulation window
        start_offset = rng.randint(0, day_span)
        duration_ms = rng.randint(200, 120_000)  # 0.2s – 2 min
        end_offset = start_offset + duration_ms // 1000

        start_ts = _ts(SIM_BASE_TIME, start_offset)
        end_ts = _ts(SIM_BASE_TIME, end_offset)

        # ~25% failures
        success = rng.random() > 0.25
        error = "" if success else rng.choice(ERROR_MESSAGES[1:])

        # EvaluationContext: mix of base64, direct JSON, and empty (non-Fabric)
        r = rng.random()
        if r < 0.40:
            ctx_enc = "b64"
        elif r < 0.75:
            ctx_enc = "json"
        else:
            ctx_enc = ""  # non-Fabric

        eval_ctx = _eval_context(dataset["id"], encoding=ctx_enc)

        # Spool: heavier for Refresh, lighter for DirectQuery
        spool = rng.randint(0, 500_000_000) if query_type == "Refresh" else rng.randint(0, 5_000_000)

        # ~15% of events are non-Fabric / paginated → no Workspace Monitoring row
        has_ws_monitoring = rng.random() < MATCH_RATE

        events.append({
            "request_id": request_id,
            "tracking_id": tracking_id,
            "dataset": dataset,
            "user": user,
            "datasource": datasource,
            "gw_node": gw_node,
            "query_type": query_type,
            "capacity": capacity,
            "start_offset": start_offset,
            "end_offset": end_offset,
            "start_ts": start_ts,
            "end_ts": end_ts,
            "duration_ms": duration_ms,
            "success": success,
            "error": error,
            "eval_ctx": eval_ctx,
            "spool": spool,
            "has_ws_monitoring": has_ws_monitoring,
        })

    return events


# ── Gateway QueryStartReport ──────────────────────────────────────────────────
def gen_query_start_report(events, rng) -> str:
    cols = [
        "GatewayObjectId",
        "RequestId",
        "QueryTrackingId",
        "QueryStartTimeUTC",
        "QueryType",
        "DataSource",
        "EvaluationContext",
    ]
    buf = io.StringIO()
    w = csv.writer(buf, quoting=csv.QUOTE_MINIMAL)
    w.writerow(cols)
    for ev in events:
        w.writerow([
            ev["gw_node"],
            ev["request_id"],
            ev["tracking_id"],
            ev["start_ts"],
            ev["query_type"],
            ev["datasource"],
            ev["eval_ctx"],
        ])
    return buf.getvalue()


# ── Gateway QueryExecutionReport ──────────────────────────────────────────────
def gen_query_execution_report(events, rng) -> str:
    cols = [
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
    ]
    buf = io.StringIO()
    w = csv.writer(buf, quoting=csv.QUOTE_MINIMAL)
    w.writerow(cols)
    for ev in events:
        dp_dur = rng.randint(10, ev["duration_ms"])
        w.writerow([
            ev["gw_node"],
            ev["request_id"],
            ev["datasource"],
            ev["tracking_id"],
            ev["end_ts"],
            ev["duration_ms"],
            ev["query_type"],
            ev["end_ts"],
            dp_dur,
            str(ev["success"]),
            ev["error"],
            rng.randint(0, 5000),
            rng.randint(0, 3000),
            ev["spool"],
            rng.randint(0, 20000),
            round(rng.uniform(0, 5e7), 1),
            round(rng.uniform(0, 5e7), 1),
        ])
    return buf.getvalue()


# ── PowerBIDatasetsWorkspace (mock Workspace Monitoring) ──────────────────────
def gen_pbi_datasets_workspace(events, rng) -> str:
    """
    Mock of the PowerBIDatasetsWorkspace Fabric Workspace Monitoring table.

    Column names are desk-verified against MS Learn (semantic-model-operations,
    updated 2026-02-17): OperationId, ExecutingUser, ItemId, ItemName,
    WorkspaceId, CapacityId, CpuTimeMs, DurationMs.

    ~85% of events get a row here (MATCH_RATE).  The 15% that don't simulate
    non-Fabric workloads (paginated reports, Dataflow Gen1) where the gateway
    fires but no Workspace Monitoring event is emitted.

    [Simulated — not tenant-verified]
    """
    cols = [
        "Timestamp",
        "OperationId",           # == gateway RequestId for the matched 85%
        "OperationName",
        "ExecutingUser",
        "ItemId",
        "ItemName",
        "WorkspaceId",
        "CapacityId",
        "DurationMs",
        "CpuTimeMs",
        "EventText",             # DAX / XMLA snippet (abbreviated)
    ]
    buf = io.StringIO()
    w = csv.writer(buf, quoting=csv.QUOTE_MINIMAL)
    w.writerow(cols)
    for ev in events:
        if not ev["has_ws_monitoring"]:
            continue  # intentional gap — non-Fabric workload
        # Workspace Monitoring OperationId == gateway RequestId (THE JOIN KEY)
        op_name = rng.choice(OPERATION_NAMES)
        cpu_ms = int(ev["duration_ms"] * rng.uniform(0.3, 0.9))
        event_text = (
            f"EVALUATE SUMMARIZECOLUMNS('Date'[Year], {ev['dataset']['name']}[Revenue])"
            if op_name in ("QueryEnd", "ExecutionMetrics")
            else ""
        )
        w.writerow([
            ev["end_ts"],
            ev["request_id"],      # ← THE JOIN KEY: OperationId == RequestId
            op_name,
            ev["user"],            # ExecutingUser — breaks the attribution ceiling
            ev["dataset"]["id"],   # ItemId
            ev["dataset"]["name"], # ItemName
            ev["dataset"]["workspace"],
            ev["capacity"],
            ev["duration_ms"],
            cpu_ms,
            event_text,
        ])
    return buf.getvalue()


# ── SystemCounter ──────────────────────────────────────────────────────────────
def gen_system_counter(rng, n_samples: int = 120) -> str:
    """Correlated system counter metrics across the 3 gateway nodes."""
    cols = [
        "GatewayObjectId",
        "CounterTimeUTC",
        "SystemCPUPercent",
        "SystemMEMUsedPercent",
        "GatewayCPUPercent",
        "GatewayMEMKb",
    ]
    buf = io.StringIO()
    w = csv.writer(buf, quoting=csv.QUOTE_MINIMAL)
    w.writerow(cols)
    # One sample every 5 minutes per node across the sim window
    interval = 300  # seconds
    for t in range(0, SIM_DAYS * 24 * 3600, interval):
        for node in GATEWAY_NODES:
            w.writerow([
                node,
                _ts(SIM_BASE_TIME, t),
                round(rng.uniform(5, 92), 1),
                round(rng.uniform(20, 85), 1),
                round(rng.uniform(2, 78), 1),
                rng.randint(500_000, 8_000_000),
            ])
    return buf.getvalue()


# ── Mashup Processes (NDJSON) ─────────────────────────────────────────────────
def gen_mashup_processes(rng, n: int = 60) -> str:
    """
    Per-process mashup/gateway samples — pain #5.
    Includes 2 injected runaway containers (WorkingSetMB > 6000).
    [Simulated — not tenant-verified]
    """
    lines = []
    for i in range(n):
        is_mashup = rng.random() > 0.3
        runaway = is_mashup and (i in (12, 37))  # deterministic runaways
        ws = rng.randint(6200, 13500) if runaway else rng.randint(150, 2800)
        offset = rng.randint(0, SIM_DAYS * 24 * 3600)
        rec = {
            "CollectedAtUtc": _ts(SIM_BASE_TIME, offset),
            "GatewayObjectId": rng.choice(GATEWAY_NODES),
            "HostName": f"GWHOST{rng.randint(1, 3)}",
            "ProcessId": 2000 + i,
            "ProcessName": (
                "Microsoft.Mashup.Container.NetFX45"
                if is_mashup
                else "Microsoft.PowerBI.EnterpriseGateway"
            ),
            "IsMashupContainer": is_mashup,
            "WorkingSetMB": float(ws),
            "PrivateBytesMB": float(ws + rng.randint(50, 500)),
            "CpuPercent": round(rng.uniform(0, 95), 2),
            "ThreadCount": rng.randint(10, 120),
            "HandleCount": rng.randint(200, 3000),
            "LogicalCores": 8,
        }
        lines.append(json.dumps(rec))
    return "\n".join(lines) + "\n"


# ── Network Metrics ───────────────────────────────────────────────────────────
def gen_network_metrics(rng) -> str:
    """
    Simulated NIC / relay latency metrics per gateway node.
    Correlates with SystemCounter timeframe.
    [Simulated — not tenant-verified]
    """
    cols = [
        "GatewayHostName",
        "CollectedAtUTC",
        "NicName",
        "BytesTotalPerSec",
        "UtilizationPct",
        "LatencyMs_PBIRelay",
        "CurrentBandwidthBps",
    ]
    buf = io.StringIO()
    w = csv.writer(buf, quoting=csv.QUOTE_MINIMAL)
    w.writerow(cols)
    interval = 300
    for t in range(0, SIM_DAYS * 24 * 3600, interval):
        for idx, node in enumerate(GATEWAY_NODES):
            # gw-003 has consistently higher latency → load skew signal
            latency = rng.uniform(5, 25) if idx < 2 else rng.uniform(80, 220)
            w.writerow([
                f"GWHOST{idx + 1}",
                _ts(SIM_BASE_TIME, t),
                "Ethernet0",
                rng.randint(1_000_000, 50_000_000),
                round(rng.uniform(2, 80), 1),
                round(latency, 1),
                1_000_000_000,  # 1 Gbps link
            ])
    return buf.getvalue()


# ── Refresh History ───────────────────────────────────────────────────────────
def gen_refresh_history(events, rng) -> str:
    """
    Correlated refresh history.  For failed Refresh-type events we emit a
    matching refresh record so the triage join (pain #2) can fire.
    Also emits 5 orphan records (no matching gateway event) to test partial-match.
    [Simulated — not tenant-verified]
    """
    statuses = ["Completed", "Failed", "Completed", "Failed", "Unknown"]
    exc_json = '{"errorCode":"ModelRefreshFailed_CredentialsNotSpecified"}'

    failed_refresh_events = [
        ev for ev in events
        if not ev["success"] and ev["query_type"] == "Refresh"
    ][:40]  # cap at 40 to keep output manageable

    records = []
    for i, ev in enumerate(failed_refresh_events):
        status = "Failed" if not ev["success"] else "Completed"
        records.append({
            "CollectedAtUtc": ev["end_ts"],
            "RequestId": ev["request_id"],   # ← exact RequestId match for triage
            "DatasetId": ev["dataset"]["id"],
            "DatasetName": ev["dataset"]["name"],
            "WorkspaceId": ev["dataset"]["workspace"],
            "RefreshType": "Scheduled" if i % 2 else "ViaApi",
            "Status": status,
            "StartTime": ev["start_ts"],
            "EndTime": ev["end_ts"],
            "ServiceExceptionJson": exc_json if status == "Failed" else "",
        })

    # Add some time-window-only matches (different RequestId, close time)
    succeeded_events = [ev for ev in events if ev["success"] and ev["query_type"] == "Refresh"][:10]
    for ev in succeeded_events:
        records.append({
            "CollectedAtUtc": ev["end_ts"],
            "RequestId": _mint_request_id(),  # different RequestId → time-window match only
            "DatasetId": ev["dataset"]["id"],
            "DatasetName": ev["dataset"]["name"],
            "WorkspaceId": ev["dataset"]["workspace"],
            "RefreshType": "Scheduled",
            "Status": "Completed",
            "StartTime": ev["start_ts"],
            "EndTime": ev["end_ts"],
            "ServiceExceptionJson": "",
        })

    # Orphan records — no corresponding gateway event
    for j in range(5):
        records.append({
            "CollectedAtUtc": _ts(SIM_BASE_TIME, rng.randint(0, SIM_DAYS * 24 * 3600)),
            "RequestId": f"ORPHAN-{_mint_request_id()}",
            "DatasetId": rng.choice(DATASETS)["id"],
            "DatasetName": f"Orphan Model {j + 1}",
            "WorkspaceId": "ws-orphan-001",
            "RefreshType": "Scheduled",
            "Status": "Failed",
            "StartTime": _ts(SIM_BASE_TIME, j * 3600),
            "EndTime": _ts(SIM_BASE_TIME, j * 3600 + 300),
            "ServiceExceptionJson": '{"errorCode":"DMTS_MonikerWithUnboundDataSources"}',
        })

    wrapper = {
        "CollectedAtUtc": _ts(SIM_BASE_TIME, SIM_DAYS * 24 * 3600),
        "LookbackHours": SIM_DAYS * 24,
        "RefreshCount": len(records),
        "CollectionErrors": [],
        "RefreshRecords": records,
    }
    return json.dumps(wrapper, indent=2) + "\n"


# ── Main ──────────────────────────────────────────────────────────────────────
def main():
    parser = argparse.ArgumentParser(description="[Simulated] Correlated multi-source simulator")
    parser.add_argument("output_dir", nargs="?",
                        default=os.path.join(os.path.dirname(__file__), "sim_out"))
    parser.add_argument("--n-events", type=int, default=DEFAULT_N_EVENTS)
    parser.add_argument("--seed", type=int, default=DEFAULT_SEED)
    args = parser.parse_args()

    os.makedirs(args.output_dir, exist_ok=True)
    rng = random.Random(args.seed)

    print(f"[Simulated] Generating {args.n_events} correlated events (seed={args.seed})...")
    events = generate_event_pool(args.n_events, args.seed)

    matched = sum(1 for e in events if e["has_ws_monitoring"])
    unmatched = args.n_events - matched
    print(f"[Simulated] Event pool: {matched} with WS Monitoring row / {unmatched} intentional gaps "
          f"(expected match-rate ~{matched / args.n_events:.1%})")

    files = {
        "QueryStartReport_sim.csv": gen_query_start_report(events, rng),
        "QueryExecutionReport_sim.csv": gen_query_execution_report(events, rng),
        "PowerBIDatasetsWorkspace_sim.csv": gen_pbi_datasets_workspace(events, rng),
        "SystemCounter_sim.csv": gen_system_counter(rng),
        "MashupProcesses_sim.ndjson": gen_mashup_processes(rng),
        "NetworkMetrics_sim.csv": gen_network_metrics(rng),
        "RefreshHistory_sim.json": gen_refresh_history(events, rng),
    }

    for fname, content in files.items():
        fpath = os.path.join(args.output_dir, fname)
        with open(fpath, "w", newline="", encoding="utf-8") as f:
            f.write(content)
        size_kb = len(content.encode()) / 1024
        print(f"  wrote {fpath}  ({size_kb:.1f} KB)")

    print(f"\n[Simulated] All outputs in: {args.output_dir}")
    print("[Simulated — not tenant-verified] IDs are structurally correct; "
          "real Microsoft schema may differ.")


if __name__ == "__main__":
    main()
