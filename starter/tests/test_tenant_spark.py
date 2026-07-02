#!/usr/bin/env python3
"""
test_tenant_spark.py  |  Label: [NET-NEW]  |  Tier 2 (PySpark) test

Exercises the tenant-breadth pipeline + the U15 fan-out guardrail against a REAL
local Spark engine (not the Spark-free mock in test_tenant_extract.py):

  1. Tenant transforms feed a real Spark DataFrame + Delta write (00 -> 01a shapes).
  2. build_silver_network_correlated-style nearest-match dedup: prove a 1-query x
     many-NIC-sample window join collapses to ONE row (U15), using real Window fns.

Requires: pyspark, delta-spark, Java 17. Skips gracefully if Spark can't start.
Run:  python test_parser_spark.py  (this file mirrors that harness)
"""
import os
import sys
import tempfile

HERE = os.path.dirname(__file__)
sys.path.insert(0, os.path.join(HERE, "..", "notebooks"))

FAILS = []


def check(name, cond):
    print(f"  [{'PASS' if cond else 'FAIL'}] {name}")
    if not cond:
        FAILS.append(name)


def main():
    try:
        from pyspark.sql import SparkSession, functions as F
        from pyspark.sql.window import Window
        import importlib.util
    except Exception as e:  # noqa: BLE001
        print(f"[SKIP] PySpark unavailable: {e}")
        return 0

    # Load the tenant transform module (pure-Python fns are Spark-independent, but
    # we feed their output into real Spark to prove the Delta write + dtypes).
    spec = importlib.util.spec_from_file_location(
        "tsg", os.path.join(HERE, "..", "notebooks", "01a_tenant_silver_gold.py"))
    tsg = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(tsg)
    spec2 = importlib.util.spec_from_file_location(
        "ext", os.path.join(HERE, "..", "notebooks", "00_tenant_extract.py"))
    ext = importlib.util.module_from_spec(spec2)
    spec2.loader.exec_module(ext)

    try:
        builder = (SparkSession.builder.master("local[2]").appName("tenant-test")
                   .config("spark.sql.shuffle.partitions", "2")
                   .config("spark.ui.enabled", "false"))
        # Delta if available; plain parquet otherwise (the write path is what matters).
        try:
            from delta import configure_spark_with_delta_pip  # noqa: F401
            builder = (builder
                       .config("spark.sql.extensions", "io.delta.sql.DeltaSparkSessionExtension")
                       .config("spark.sql.catalog.spark_catalog",
                               "org.apache.spark.sql.delta.catalog.DeltaCatalog"))
        except Exception:  # noqa: BLE001
            pass
        spark = builder.getOrCreate()
    except Exception as e:  # noqa: BLE001
        print(f"[SKIP] Spark session could not start: {e}")
        return 0
    spark.sparkContext.setLogLevel("ERROR")
    tmp = tempfile.mkdtemp(prefix="tenant_spark_")

    # 1) Tenant transforms -> real Spark DataFrame -> Delta/Parquet write
    print("Tier 2: tenant transforms -> Spark write")
    inv = tsg.transform_inventory([ext._mock_scan_result()])
    ref = tsg.transform_refreshables(ext._mock_refreshables())
    inv_df = spark.createDataFrame(inv)
    ref_df = spark.createDataFrame(ref)
    check("gold_inventory DataFrame has rows", inv_df.count() == 4)
    check("gold_inventory has ItemKind column", "ItemKind" in inv_df.columns)
    check("refreshables duration_seconds numeric", any(
        c[0] == "duration_seconds" and c[1] in ("double", "bigint", "long")
        for c in ref_df.dtypes))
    fmt = "delta" if any("delta" in str(v).lower()
                         for v in spark.sparkContext.getConf().getAll()) else "parquet"
    inv_df.write.format(fmt).mode("overwrite").save(os.path.join(tmp, "gold_inventory"))
    check("gold write + read round-trips",
          spark.read.format(fmt).load(os.path.join(tmp, "gold_inventory")).count() == 4)

    # 2) U15 nearest-match dedup, proven in real Spark:
    #    one query (RequestId=R1) vs 3 NIC samples within window -> exactly 1 output row.
    print("Tier 2: U15 fan-out guardrail (nearest-match dedup)")
    q = spark.createDataFrame([("R1", 1000)], ["RequestId", "qe_ts"]).alias("qe")
    nic = spark.createDataFrame(
        [("R1", 995), ("R1", 1002), ("R1", 1200)], ["nRequestId", "nic_ts"]).alias("net")
    joined = (q.join(nic, F.abs(F.col("qe.qe_ts") - F.col("net.nic_ts")) <= 300, "left")
              .withColumn("_gap", F.abs(F.col("qe.qe_ts") - F.col("net.nic_ts")))
              .withColumn("_rank", F.row_number().over(
                  Window.partitionBy(F.col("qe.RequestId")).orderBy(F.col("_gap").asc_nulls_last())))
              .filter(F.col("_rank") == 1))
    out_rows = joined.count()
    nearest = joined.select("nic_ts").collect()[0][0]
    check("fan-out collapsed to 1 row (U15)", out_rows == 1)
    check("kept the NEAREST NIC sample (1002, gap 2)", nearest == 1002)

    # 3) U14 cast quarantine (needs real pyspark types) — bad numeric flagged,
    #    legitimate null NOT flagged.
    print("Tier 2: U14 cast-error quarantine")
    try:
        import importlib.util as _il
        _s = _il.spec_from_file_location("bi", os.path.join(HERE, "..", "notebooks", "01_bronze_ingest.py"))
        _bi = _il.module_from_spec(_s); _s.loader.exec_module(_bi)
        from pyspark.sql.types import DoubleType
        _dbl = DoubleType()
        _v, _ok = _bi._cast_value("N/A", _dbl)
        check("bad numeric flagged (cast_ok=False)", _v is None and _ok is False)
        _v, _ok = _bi._cast_value("3.14", _dbl)
        check("good numeric parses, not flagged", _v == 3.14 and _ok is True)
        _v, _ok = _bi._cast_value("", _dbl)
        check("empty is legitimate null, not flagged", _v is None and _ok is True)
    except Exception as e:  # noqa: BLE001
        check(f"U14 cast checks loadable ({e})", False)

    spark.stop()
    if FAILS:
        print(f"\nRESULT: {len(FAILS)} FAILED: {FAILS}")
        return 1
    print("\n================================================\nRESULT: ALL TESTS PASSED")
    return 0


if __name__ == "__main__":
    sys.exit(main())
