# Contributing to pbi-gateway-monitoring

The highest-value contribution is running the pilot in your Fabric + gateway tenant and reporting what you find. Code contributions, collector fixes, and visual improvements are also welcome.

---

## The most important contribution: run the pilot

Every `[Unverified]` label in this repo is a question that requires a live Fabric tenant to answer. The community is the only path to resolving them.

### File a pilot-report issue

After running any part of the kit against a real gateway, open an issue using the **[pilot-report template](.github/ISSUE_TEMPLATE/pilot-report.yml)**.

You do not need to run everything. Task 1 (the identity-join test) alone is the single most valuable data point — it either confirms or refutes the flagship capability. See [`QUICKSTART.md`](QUICKSTART.md) for the 30-minute version, or [`docs/PILOT-GUIDE-START-HERE.md`](docs/PILOT-GUIDE-START-HERE.md) for the full walkthrough.

### What the pilot needs most

These are the specific `[Unverified]` items the community can unblock, in priority order:

1. **Identity join match rate (highest priority)** — run `starter/kql/01_identity_join.kql` and report whether `RequestId == OperationId` yields `ExecutingUser` + `ItemName`. What percentage of refresh queries are covered? Does it extend to DirectQuery-through-gateway?

2. **EvaluationContext encoding** — open a `QueryStartReport*.csv` from your gateway. Is the `EvaluationContext` column raw JSON (starts with `{`) or base64-encoded? Does `artifactId` come through non-null for Fabric semantic models?

3. **Workspace Monitoring column names** — run the schema check in your Eventhouse KQL database:
   ```kql
   PowerBIDatasetsWorkspace | getschema | project ColumnName, ColumnType
   ```
   Report whether any column names differ from what the KQL queries in `starter/kql/` assume.

4. **DataGateway cmdlet names** — on the gateway host in PowerShell 7:
   ```powershell
   Get-Command -Module DataGateway | Select-Object Name
   ```
   Report the full list so the collectors' cmdlet calls can be verified.

5. **Activator DSL** — does the alerting rule syntax in `starter/alerting/activator-rules.json` match the current Fabric Activator rule format? Report the version and any field name differences.

6. **Collector path correctness** — does `Collect-GatewayLogs.ps1` find the right log directory on your service account + Windows version combination? The default path assumes `PBIEgwService` — non-default service accounts will need a different path.

---

## Other ways to contribute

### Submit collector fixes

If a collector fails on your gateway version or service account configuration:

1. Open an issue describing the failure (include the gateway version, PowerShell version, and error message).
2. If you have a fix, submit a PR targeting the specific collector in `starter/collectors/`.
3. Apply the correct label (see Labeling convention below).

### Add report visuals

The PBIP skeleton in `starter/report/` needs visuals finalized in Power BI Desktop after a first tenant deploy. If you have deployed the kit and built useful pages, submit the updated `.pbip` files via PR.

### Fix KQL queries

If a KQL query in `starter/kql/` returns unexpected results in your tenant (wrong column names, schema mismatch, wrong join cardinality), open an issue with the error and your `getschema` output. PRs with corrected queries are welcome.

### Improve documentation

Corrections to factual claims, clearer prerequisite steps, gateway-version-specific caveats, and additional deployment paths are all valuable. Keep the honesty labeling intact — do not remove `[Unverified]` labels unless the item is confirmed by a tenant pilot report.

---

## Dev setup — running the tests locally

The test suite has two tiers. Run both before submitting a PR that touches Python or notebook code.

### Tier 1 — Pure-Python (no Spark required)

Tests the schema-adaptive parser logic in isolation. Runs anywhere Python 3.9+ is available.

```bash
python starter/tests/test_parser.py
```

No additional dependencies beyond the standard library. This is the fastest feedback loop for parser changes.

### Tier 2 — Spark integration (requires JDK + PySpark)

Tests the full medallion pipeline (bronze → silver → gold) against synthetic data on a real local Spark instance.

**Prerequisites:**
- JDK 8, 11, or 17. **JDK 21+ is not supported by Spark 3.5** — `pyspark` will fail to start with JDK 21. Install JDK 17 and set `JAVA_HOME` accordingly.
- Python 3.9+

```bash
pip install -r requirements-dev.txt
python starter/tests/test_parser_spark.py
python starter/deploy/run_local_smoke.py
```

`run_local_smoke.py` runs the full medallion end-to-end on synthetic gateway logs and asserts all three stages (bronze → silver → gold) produce non-empty Delta tables. All stages currently PASS on Spark 3.5.1 + JDK 17.

---

## Labeling convention

Every claim and every code file in this repo carries an explicit label. Contributors must follow the same convention — do not submit PRs that add unlabeled claims or code.

The canonical label set (and the required-when-confirming-a-pilot workflow) lives in the
README's **[Honesty charter](README.md#honesty-charter)** — do not redefine it here. The two
labels you'll use most often when adding a new feature:

- **`[Built-Unverified]`** — implemented and locally tested, but not yet run in a live Fabric
  tenant with real data. Apply to any new feature until a pilot report confirms it. (Note: some
  older text in this repo uses the shorthand `[Unverified]` — treat it as equivalent to
  `[Built-Unverified]`.)
- **`[STUB]` / `[NET-NEW]` / `[ADAPTED]`** — as defined in the README charter, for scaffold code,
  original logic, and logic adapted from a named upstream source respectively.

When a pilot report confirms a previously `[Built-Unverified]` item, update the label in the
relevant file and update the status column in [`PAIN-POINT-COVERAGE.md`](PAIN-POINT-COVERAGE.md).
Include a link to the confirming pilot-report issue in the PR description.

---

## PR checklist

Before submitting a pull request, confirm all of the following:

- [ ] Both test tiers pass (`test_parser.py` and, if touching notebooks/medallion code, `test_parser_spark.py` + `run_local_smoke.py`)
- [ ] All new claims and new code carry the correct label (`[Unverified]`, `[STUB]`, `[NET-NEW]`, or `[ADAPTED]`)
- [ ] No secrets, credentials, connection strings, or tenant IDs are present in any file
- [ ] If a pilot report confirmed a previously `[Unverified]` item, `PAIN-POINT-COVERAGE.md` is updated with the new status and a link to the confirming issue
- [ ] Relative markdown links are used throughout (no absolute repo URLs)
- [ ] The PR description describes what changed, why, and which test tier(s) were run

---

## Licensing of contributions

This project is licensed under the **[Apache License 2.0](LICENSE)**. Per Apache-2.0 §5, any
contribution you intentionally submit for inclusion in the work is licensed under those same terms,
with no additional conditions, unless you explicitly state otherwise. There is no separate CLA.

If you submit code adapted from another project, say so in the PR, keep its original copyright and
license headers intact, and add the attribution to [`NOTICE`](NOTICE) — Apache-2.0 §4(d) requires
`NOTICE` to travel with redistributions.

---

## Code of conduct

This project follows the [Contributor Covenant Code of Conduct](https://www.contributor-covenant.org/version/2/1/code_of_conduct/). Be direct, be honest, and cite your sources — the same standard the research pipeline holds itself to.
