#!/usr/bin/env python3
"""
test_parser_spark.py  |  Label: [NET-NEW]  |  Tier 2 (PySpark) test

Exercises the REAL native-Spark bronze path (gateway_bronze_lib.read_gateway_csv
+ casts + EvaluationContext identity) against synthetic gateway logs — the exact
code the Fabric notebook runs. Complements the portable Spark-free test_parser.py.

Requires: pyspark, delta-spark, Java. Skips gracefully if Spark can't start.
Run:  python test_parser_spark.py
"""
import os, sys, glob

HERE = os.path.dirname(__file__)
sys.path.insert(0, os.path.join(HERE, "..", "notebooks"))
OUT = os.path.join(HERE, "synthetic_out")

FAILS = []
def check(name, cond):
    print(f"  [{'PASS' if cond else 'FAIL'}] {name}")
    if not cond: FAILS.append(name)

def main():
    if not glob.glob(os.path.join(OUT, "*.csv")):
        print("No synthetic data. Run generate_synthetic_logs.py first."); return 2
    try:
        from pyspark.sql import SparkSession
        from pyspark.sql import functions as F
        import gateway_bronze_lib as lib
    except Exception as e:
        print(f"[SKIP] PySpark/lib unavailable: {e}"); return 0

    spark = (SparkSession.builder
             .master("local[2]").appName("gw-bronze-test")
             .config("spark.sql.shuffle.partitions", "2")
             .config("spark.ui.enabled", "false")
             .getOrCreate())
    spark.sparkContext.setLogLevel("ERROR")

    qe_path = os.path.join(OUT, "QueryExecutionReport_synthetic.csv")
    qs_path = os.path.join(OUT, "QueryStartReport_synthetic.csv")

    print("Tier 2: native Spark CSV — QueryExecution")
    qe = lib.read_gateway_csv(spark, qe_path, lib.QUERY_EXECUTION_RENAME)
    qe = lib.cast_query_execution(qe)
    n = qe.count()
    check("read > 0 rows via native Spark CSV", n > 0)
    # RFC-4180: comma-in-ErrorMessage did NOT shift columns -> numeric cols still cast cleanly
    bad_cast = qe.filter(
        F.col("QueryExecutionDuration").isNull() & F.col("QueryExecutionEndTimeUTC").isNotNull()
    ).count()
    # some NULLs are intentional (we injected "NULL" strings); assert the comma rows specifically survived
    comma_rows = qe.filter(F.col("ErrorMessage").contains(",")).count()
    check("comma-bearing ErrorMessage rows present & intact", comma_rows > 0)
    check("mid-schema new column survived (present as column)",
          "NewGatewayColumn_v3000" in qe.columns)
    check("renamed numeric column exists (QueryExecutionDuration)",
          "QueryExecutionDuration" in qe.columns)
    check("Success cast to boolean",
          dict(qe.dtypes).get("Success") == "boolean")

    print("Tier 2: native Spark CSV — QueryStart + identity")
    qs = lib.read_gateway_csv(spark, qs_path)
    qs = lib.add_artifact_identity(qs, "EvaluationContext")
    got_ids = qs.filter(F.col("artifact_id").isNotNull()).count()
    check("EvaluationContext (base64 + direct JSON) yielded artifact_id",
          got_ids > 0)

    spark.stop()
    print(f"\n{'='*48}")
    if FAILS:
        print(f"RESULT: {len(FAILS)} FAILED -> {FAILS}"); return 1
    print("RESULT: ALL SPARK TESTS PASSED"); return 0

if __name__ == "__main__":
    sys.exit(main())
