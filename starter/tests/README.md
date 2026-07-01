# Starter Tests — two-tier verification

Closes the verification gap without a live gateway or Fabric. Both tiers exercise
the **shared** bronze logic in `../notebooks/gateway_bronze_lib.py` (single source
of truth), against synthetic logs that embed the exact edge cases that break the
official Microsoft PBIT template.

## Files
- `generate_synthetic_logs.py` — writes QueryExecution / QueryStart / SystemCounter
  CSVs with: comma-in-ErrorMessage, escaped quotes, a mid-schema NEW column,
  base64 **and** direct-JSON EvaluationContext, and NULL values.
- `test_parser.py` — **Tier 1 (portable, Spark-free).** Runs anywhere Python runs
  (CI, laptop). Tests the pure-Python fallback parser + EvaluationContext normalizer.
- `test_parser_spark.py` — **Tier 2 (PySpark).** Runs the real native-Spark path
  (`read_gateway_csv` + casts + UDF-free `add_artifact_identity`) — the exact code
  the Fabric notebook runs.

## Run

```bash
# generate data first
python generate_synthetic_logs.py

# Tier 1 — portable (no Spark needed)
python test_parser.py

# Tier 2 — PySpark (needs pyspark + Java 11/17; NOT Java 21+/25 with Spark 3.5)
export JAVA_HOME=/path/to/jdk-17
python test_parser_spark.py
```

## Verified results (this repo, Spark 3.5.1 + JDK 17)

Both tiers **PASS**:
- Tier 1: 8/8 — schema-adaptive parse, mid-schema column → `_extra_cols`,
  comma/quoted ErrorMessage intact, base64+direct-JSON context → artifactId,
  null/garbage handled safely.
- Tier 2: 6/6 — native Spark CSV read, comma rows intact, new column survived,
  numeric rename + boolean cast, EvaluationContext (both encodings) → `artifact_id`.

## Environment notes
- **Java:** Spark 3.5 supports JDK 8/11/17. JDK 21+/25 fails with
  `getSubject is not supported`. Use JDK 17.
- **No Python UDFs:** identity extraction uses native Spark SQL (`unbase64`,
  `decode`, `get_json_object`) — faster than a UDF and avoids cloudpickle issues
  on newer Python (e.g. 3.14).
- These tests validate parsing/transform logic. They do **not** validate the
  identity JOIN to Workspace Monitoring or Activator rules — those need a live
  tenant (see `research/phase5_validation.md`).
