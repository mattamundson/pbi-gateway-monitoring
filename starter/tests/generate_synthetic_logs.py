#!/usr/bin/env python3
"""
generate_synthetic_logs.py  |  Label: [NET-NEW]

Generates synthetic On-Premises Data Gateway performance log CSVs that mimic the
real schema AND deliberately include the edge cases that break naive parsers:

  - commas inside quoted ErrorMessage fields          (breaks line.split(","))
  - escaped double-quotes inside fields                (RFC-4180 "")
  - a NEW column added mid-schema                      (breaks positional parsing -> pain #4)
  - base64-encoded EvaluationContext                   (breaks direct JSON parse -> pain #3)
  - direct-JSON EvaluationContext                      (the other encoding)
  - null / empty / "NULL" values                       (cast robustness)

Purpose: let the schema-adaptive bronze parser be tested WITHOUT a live gateway
or Spark. Real column names are from MS Learn:
  https://learn.microsoft.com/en-us/data-integration/gateway/service-gateway-performance

Usage:
    python generate_synthetic_logs.py [output_dir]
Outputs QueryExecution / QueryStart / SystemCounter CSVs into output_dir.
"""
import csv, io, base64, json, os, sys, random
from datetime import datetime, timedelta, timezone


def _utc(offset_min=0):
    return (datetime.now(timezone.utc) - timedelta(minutes=offset_min)).strftime(
        "%Y-%m-%dT%H:%M:%SZ"
    )


def gen_query_execution(n=20, add_extra_column=True):
    """QueryExecution report. Includes a comma-bearing ErrorMessage + an extra column."""
    base_cols = [
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
    # Simulate a gateway upgrade that inserts a NEW column mid-schema (pain #4)
    if add_extra_column:
        base_cols.insert(7, "NewGatewayColumn_v3000")  # unexpected, mid-schema

    buf = io.StringIO()
    w = csv.writer(buf, quoting=csv.QUOTE_MINIMAL)
    w.writerow(base_cols)
    errs = [
        "",  # success rows
        "Timeout expired, retry failed, code 42",  # commas!
        'Login failed for user "svc-etl", account locked',  # embedded quotes + comma
        "The operation was canceled.",
    ]
    for i in range(n):
        # Rows 1 and 2 deterministically carry the comma-bearing edge cases this
        # generator exists to embed (see module docstring) -- leaving err selection
        # entirely to random.choice() let CI occasionally emit zero comma-bearing
        # rows, failing the "comma rows survived" assertion for a reason that had
        # nothing to do with the parser (flaky, not a real defect).
        if i == 0:
            success, err = False, errs[1]  # "Timeout expired, retry failed, code 42"
        elif i == 1:
            success, err = False, errs[2]  # embedded quotes + comma
        else:
            success = random.random() > 0.25
            err = "" if success else random.choice(errs[1:])
        row = {
            "GatewayObjectId": f"gw-{random.randint(1,3):03d}",
            "RequestId": f"req-{1000+i}",
            "DataSource": random.choice(["SqlProd", "OracleFin", "SharePointOnPrem"]),
            "QueryTrackingId": f"qt-{2000+i}",
            "QueryExecutionEndTimeUTC": _utc(i),
            "QueryExecutionDuration(ms)": random.randint(50, 90000),
            "QueryType": random.choice(["Refresh", "DirectQuery"]),
            "DataProcessingEndTimeUTC": _utc(i),
            "DataProcessingDuration(ms)": random.randint(10, 40000),
            "Success": str(success),
            "ErrorMessage": err,
            "SpoolingDiskWritingDuration(ms)": random.randint(0, 5000),
            "SpoolingDiskReadingDuration(ms)": random.randint(0, 3000),
            "SpoolingTotalDataSize(bytes)": random.randint(0, 500_000_000),
            "DataReadingAndSerializationDuration(ms)": random.randint(0, 20000),
            "DiskRead(byte/sec)": round(random.uniform(0, 5e7), 1),
            "DiskWrite(byte/sec)": round(random.uniform(0, 5e7), 1),
        }
        if add_extra_column:
            row["NewGatewayColumn_v3000"] = random.choice(["", "someflag", "42"])
        # occasional explicit NULLs
        if random.random() < 0.1:
            row["SpoolingTotalDataSize(bytes)"] = "NULL"
        w.writerow([row.get(c, "") for c in base_cols])
    return buf.getvalue()


def gen_query_start(n=20):
    """QueryStart. Half base64 EvaluationContext, half direct JSON, some empty."""
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
    for i in range(n):
        ctx_obj = {
            "artifactId": f"dataset-{random.randint(1,5)}",
            "artifactKind": "SemanticModel",
        }
        ctx_json = json.dumps(ctx_obj)
        r = random.random()
        if r < 0.4:
            ctx = base64.b64encode(ctx_json.encode()).decode()  # base64 variant
        elif r < 0.8:
            ctx = ctx_json  # direct JSON variant
        else:
            ctx = ""  # non-Fabric workload
        w.writerow(
            [
                f"gw-{random.randint(1,3):03d}",
                f"req-{1000+i}",
                f"qt-{2000+i}",
                _utc(i),
                random.choice(["Refresh", "DirectQuery"]),
                random.choice(["SqlProd", "OracleFin"]),
                ctx,
            ]
        )
    return buf.getvalue()


def gen_system_counter(n=30):
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
    for i in range(n):
        w.writerow(
            [
                f"gw-{random.randint(1,3):03d}",
                _utc(i * 5),
                round(random.uniform(5, 99), 1),
                round(random.uniform(20, 95), 1),
                round(random.uniform(1, 80), 1),
                random.randint(500_000, 8_000_000),
            ]
        )
    return buf.getvalue()


def gen_mashup_processes(n=40):
    """Per-process mashup/gateway samples as newline-delimited JSON (pain #5).
    Mimics Collect-MashupProcesses.ps1 output. Includes a runaway container."""
    import json as _json

    lines = []
    for i in range(n):
        is_mashup = random.random() > 0.3
        # inject an occasional runaway container (high working set)
        runaway = is_mashup and random.random() < 0.15
        ws = random.randint(6000, 14000) if runaway else random.randint(150, 3000)
        rec = {
            "CollectedAtUtc": _utc(i),
            "GatewayObjectId": f"gw-{random.randint(1,3):03d}",
            "HostName": f"GWHOST{random.randint(1,3)}",
            "ProcessId": 1000 + i,
            "ProcessName": (
                "Microsoft.Mashup.Container.NetFX45"
                if is_mashup
                else "Microsoft.PowerBI.EnterpriseGateway"
            ),
            "IsMashupContainer": is_mashup,
            "WorkingSetMB": float(ws),
            "PrivateBytesMB": float(ws + random.randint(50, 500)),
            "CpuPercent": round(random.uniform(0, 95), 2),
            "ThreadCount": random.randint(10, 120),
            "HandleCount": random.randint(200, 3000),
            "LogicalCores": 8,
        }
        lines.append(_json.dumps(rec))
    return "\n".join(lines) + "\n"


def gen_refresh_history(n=16):
    """Power BI Service refresh history — mimics Collect-RefreshHistory.ps1 output
    (pain #2, the Service-side leg of the triage join).

    Emits the SAME wrapper shape the collector writes (CollectedAtUtc + a nested
    RefreshRecords array) as ONE pretty-printed JSON object, so it exercises the
    multiLine=True read path in 01_bronze_ingest.ingest_refresh_history().

    RequestIds overlap the QueryExecution fixture space (req-1000..req-1019) so the
    exact-RequestId triage join can match failed gateway queries. Two non-matching
    RequestIds (req-9001/9002) simulate refreshes with no corresponding gateway
    query (Service saw the refresh; the gateway leg is absent)."""
    import json as _json

    statuses = ["Completed", "Failed", "Completed", "Failed", "Unknown"]
    exc = '{"errorCode":"ModelRefreshFailed_CredentialsNotSpecified"}'
    records = []
    # Overlapping RequestIds (req-1000..req-100(n-1)) — high overlap with failed QE rows.
    for i in range(n):
        status = statuses[i % len(statuses)]
        records.append(
            {
                "CollectedAtUtc": _utc(0),
                "RequestId": f"req-{1000 + i}",
                "DatasetId": f"dataset-{(i % 5) + 1}",
                "DatasetName": f"Finance Model {(i % 5) + 1}",
                "WorkspaceId": f"ws-{(i % 3) + 1:03d}",
                "RefreshType": "Scheduled" if i % 2 else "ViaApi",
                "Status": status,
                "StartTime": _utc(i + 6),
                "EndTime": _utc(i),
                "ServiceExceptionJson": exc if status == "Failed" else "",
            }
        )
    # Non-matching refreshes (no gateway query with these RequestIds).
    for j, rid in enumerate(("req-9001", "req-9002")):
        records.append(
            {
                "CollectedAtUtc": _utc(0),
                "RequestId": rid,
                "DatasetId": f"dataset-{j + 1}",
                "DatasetName": f"Orphan Model {j + 1}",
                "WorkspaceId": "ws-001",
                "RefreshType": "Scheduled",
                "Status": "Failed",
                "StartTime": _utc(j + 6),
                "EndTime": _utc(j),
                "ServiceExceptionJson": '{"errorCode":"DMTS_MonikerWithUnboundDataSources"}',
            }
        )
    wrapper = {
        "CollectedAtUtc": _utc(0),
        "LookbackHours": 24,
        "RefreshCount": len(records),
        "CollectionErrors": [],
        "RefreshRecords": records,
    }
    # Pretty-printed (indent=2), matching PowerShell ConvertTo-Json's multi-line output.
    return _json.dumps(wrapper, indent=2) + "\n"


def main():
    # Fixed seed: CI runs must be reproducible, and an unseeded generator can
    # (rarely) fail an edge-case assertion through bad luck rather than a real bug.
    random.seed(1337)
    out = (
        sys.argv[1]
        if len(sys.argv) > 1
        else os.path.join(os.path.dirname(__file__), "synthetic_out")
    )
    os.makedirs(out, exist_ok=True)
    files = {
        "QueryExecutionReport_synthetic.csv": gen_query_execution(),
        "QueryStartReport_synthetic.csv": gen_query_start(),
        "SystemCounterReport_synthetic.csv": gen_system_counter(),
        "MashupProcesses_synthetic.json": gen_mashup_processes(),
        "refresh_history_synthetic.json": gen_refresh_history(),
    }
    for name, content in files.items():
        with open(os.path.join(out, name), "w", newline="", encoding="utf-8") as f:
            f.write(content)
        print(f"wrote {os.path.join(out, name)} ({len(content)} bytes)")
    print(
        "\nEdge cases embedded: comma-in-ErrorMessage, escaped quotes, mid-schema new column,"
        " base64 + direct-JSON EvaluationContext, NULL values."
    )


if __name__ == "__main__":
    main()
