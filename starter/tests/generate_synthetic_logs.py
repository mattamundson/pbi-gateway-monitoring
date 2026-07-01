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
    return (datetime.now(timezone.utc) - timedelta(minutes=offset_min)).strftime("%Y-%m-%dT%H:%M:%SZ")

def gen_query_execution(n=20, add_extra_column=True):
    """QueryExecution report. Includes a comma-bearing ErrorMessage + an extra column."""
    base_cols = [
        "GatewayObjectId","RequestId","DataSource","QueryTrackingId",
        "QueryExecutionEndTimeUTC","QueryExecutionDuration(ms)","QueryType",
        "DataProcessingEndTimeUTC","DataProcessingDuration(ms)","Success","ErrorMessage",
        "SpoolingDiskWritingDuration(ms)","SpoolingDiskReadingDuration(ms)",
        "SpoolingTotalDataSize(bytes)","DataReadingAndSerializationDuration(ms)",
        "DiskRead(byte/sec)","DiskWrite(byte/sec)",
    ]
    # Simulate a gateway upgrade that inserts a NEW column mid-schema (pain #4)
    if add_extra_column:
        base_cols.insert(7, "NewGatewayColumn_v3000")  # unexpected, mid-schema

    buf = io.StringIO()
    w = csv.writer(buf, quoting=csv.QUOTE_MINIMAL)
    w.writerow(base_cols)
    errs = [
        "",  # success rows
        "Timeout expired, retry failed, code 42",          # commas!
        'Login failed for user "svc-etl", account locked', # embedded quotes + comma
        "The operation was canceled.",
    ]
    for i in range(n):
        success = random.random() > 0.25
        err = "" if success else random.choice(errs[1:])
        row = {
            "GatewayObjectId": f"gw-{random.randint(1,3):03d}",
            "RequestId": f"req-{1000+i}",
            "DataSource": random.choice(["SqlProd","OracleFin","SharePointOnPrem"]),
            "QueryTrackingId": f"qt-{2000+i}",
            "QueryExecutionEndTimeUTC": _utc(i),
            "QueryExecutionDuration(ms)": random.randint(50, 90000),
            "QueryType": random.choice(["Refresh","DirectQuery"]),
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
    cols = ["GatewayObjectId","RequestId","QueryTrackingId","QueryStartTimeUTC",
            "QueryType","DataSource","EvaluationContext"]
    buf = io.StringIO(); w = csv.writer(buf, quoting=csv.QUOTE_MINIMAL)
    w.writerow(cols)
    for i in range(n):
        ctx_obj = {"artifactId": f"dataset-{random.randint(1,5)}", "artifactKind": "SemanticModel"}
        ctx_json = json.dumps(ctx_obj)
        r = random.random()
        if r < 0.4:
            ctx = base64.b64encode(ctx_json.encode()).decode()   # base64 variant
        elif r < 0.8:
            ctx = ctx_json                                        # direct JSON variant
        else:
            ctx = ""                                             # non-Fabric workload
        w.writerow([f"gw-{random.randint(1,3):03d}", f"req-{1000+i}", f"qt-{2000+i}",
                    _utc(i), random.choice(["Refresh","DirectQuery"]),
                    random.choice(["SqlProd","OracleFin"]), ctx])
    return buf.getvalue()

def gen_system_counter(n=30):
    cols = ["GatewayObjectId","CounterTimeUTC","SystemCPUPercent",
            "SystemMEMUsedPercent","GatewayCPUPercent","GatewayMEMKb"]
    buf = io.StringIO(); w = csv.writer(buf, quoting=csv.QUOTE_MINIMAL)
    w.writerow(cols)
    for i in range(n):
        w.writerow([f"gw-{random.randint(1,3):03d}", _utc(i*5),
                    round(random.uniform(5,99),1), round(random.uniform(20,95),1),
                    round(random.uniform(1,80),1), random.randint(500_000, 8_000_000)])
    return buf.getvalue()

def main():
    out = sys.argv[1] if len(sys.argv) > 1 else os.path.join(os.path.dirname(__file__), "synthetic_out")
    os.makedirs(out, exist_ok=True)
    files = {
        "QueryExecutionReport_synthetic.csv": gen_query_execution(),
        "QueryStartReport_synthetic.csv": gen_query_start(),
        "SystemCounterReport_synthetic.csv": gen_system_counter(),
    }
    for name, content in files.items():
        with open(os.path.join(out, name), "w", newline="", encoding="utf-8") as f:
            f.write(content)
        print(f"wrote {os.path.join(out, name)} ({len(content)} bytes)")
    print("\nEdge cases embedded: comma-in-ErrorMessage, escaped quotes, mid-schema new column,"
          " base64 + direct-JSON EvaluationContext, NULL values.")

if __name__ == "__main__":
    main()
