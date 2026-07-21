# Fabric Activator Alerting Rules

> **GENERATED FILE -- DO NOT EDIT.**
> Source of truth: [`activator-rules.json`](activator-rules.json).
> Regenerate with `python starter/alerting/generate_rules_md.py`.
> CI fails if this file is stale (see `.github/workflows/tests.yml`).

Every rule below is validated on each push by
[`starter/tests/validate_alert_rules.py`](../tests/validate_alert_rules.py)
against [`starter/schemas/table_contracts.json`](../schemas/table_contracts.json):
each rule's `source_table` must exist and be materialized, every column it
references (in both the condition and the message template) must be real, string
comparisons must fall inside the column's declared vocabulary, and a
percent-scaled column may not be compared against a 0-1 fraction.

**Why that matters:** before this validation existed, **4 of 7 rules could never
fire** and 2 more rendered broken alert messages. See each rule's *Fix history*.

---

## Data source configuration

| Item | Value |
|---|---|
| Trigger mode | When the source Delta table is updated (after the Gold notebook completes) |
| Gold refresh cadence | 5 minutes |
| Entity key | Per-rule (see `grouping`) so Activator tracks alert state independently |

**Capacity requirement.** `[CORRECTED 2026-07-21]` Earlier revisions of this
document asserted "Activator requires F8+". Microsoft documents no hard SKU
minimum for Activator itself beyond a supported Fabric capacity (F-SKU or
Trial). The F8 guidance applies to running an **always-on Eventhouse** without
overage, which is a Phase 6 (streaming) concern, not a prerequisite for these
rules against Delta tables.

---

**9 rules defined: 7 live, 2 disabled pending an unmet dependency.**

| # | Rule | Pain | Source table | Status |
|---|---|---|---|---|
| 1 | `gateway-offline` | #1 real-time offline alerting | `gold_gateway_health` | live |
| 2 | `spool-disk-forecast-low` | #9 disk spooler surprises (proactive) | `gold_predictions` | **disabled** |
| 3 | `spool-disk-critical-remediate` | #9 disk spooler surprises (remediation) | `bronze_disk_spool` | live |
| 4 | `credential-drift` | #10 credential state drift | `bronze_gateway_datasources` | live |
| 5 | `error-rate-spike` | #2 unified failure triage | `gold_gateway_health` | live |
| 6 | `cpu-anomaly` | #5 mashup CPU/memory bloat | `gold_predictions` | **disabled** |
| 7 | `mashup-container-runaway` | #5 mashup memory/CPU bloat, per-process visibility | `gold_mashup_health` | live |
| 8 | `schema-drift` | #4 gateway upgrades silently change log columns | `bronze_schema_warnings` | live |
| 9 | `network-saturation` | #7 network saturation blind spot (differentiator #4) | `gold_gateway_health` | live |

## Rule 1: `gateway-offline`

**Pain point:** #1 real-time offline alerting  
**Tier:** 1  
**Source table:** `gold_gateway_health`  
**Grouping:** per GatewayObjectId

No existing tool -- FPM, the PBIT template, or any third party -- provides proactive gateway-offline alerting without a Logic App or custom Power Automate flow. This is pain point #1's most-requested feature, implemented on native Fabric Activator with no Logic App.

```
WHEN heartbeat_age_minutes > 10

Transition: fires on state change to offline (Activator 'Becomes true', NOT 'Is true' -- 'Is true' re-fires every evaluation cycle and causes an alert storm during an extended outage)
```

**Action:** `notify` -> Teams, email

> Gateway {GatewayObjectId} ({GatewayNodeName}, cluster {GatewayClusterName}) offline: no heartbeat for {heartbeat_age_minutes}m. Last seen {last_heartbeat_utc}.

**Remediation.** Check the gateway host is powered on and the PBIEgwService Windows service is running. If the host is up but the service is down, see the runbook's gateway-offline decision tree. Datasets bound to this node will fail scheduled refresh until it returns.

**Pain evidence.** "No clue why, and no notification either" -- https://community.fabric.microsoft.com/t5/Service/Gateway-status-emails-or-alerts/m-p/344928

<details><summary>Fix history</summary>

Threshold was 3 minutes against a 5-minute collector interval -- it would fire on every normal collection gap. Reconciled to 10 to match config.sample.json and activator-rules.md (roadmap T4). Message referenced {blast_radius}, which is not a column anywhere and would have rendered literally in every alert; replaced with real columns.

</details>

---

## Rule 2: `spool-disk-forecast-low`  `[DISABLED]`

**Pain point:** #9 disk spooler surprises (proactive)  
**Tier:** 1  
**Source table:** `gold_predictions`  
**Grouping:** per GatewayHostName

> **This rule is disabled and will not fire.**  
> roadmap T42 -- gold_predictions is not materialized. 02_anomaly_forecast.kql is a reference query that no scheduled job runs, so nothing writes min_forecast_free_gb. Re-enable when the forecast job lands.

```
WHEN min_forecast_free_gb < 10

Transition: fires when 2h forecast crosses floor (proactive, before exhaustion)
```

**Action:** `notify` -> Teams

> Gateway {GatewayHostName} spool disk forecast to hit {min_forecast_free_gb}GB within {forecast_horizon_minutes}m.

<details><summary>Fix history</summary>

Was enabled while pointing at a table nothing writes -- it appeared live on the board and could never fire. Now honestly disabled with the unblocking condition recorded.

</details>

---

## Rule 3: `spool-disk-critical-remediate`

**Pain point:** #9 disk spooler surprises (remediation)  
**Tier:** 2  
**Source table:** `bronze_disk_spool`  
**Grouping:** per GatewayHostName

The gateway writes compressed query results to a spool directory. When that disk fills, queries fail with cryptic errors that never mention disk. No existing tool monitors it proactively. See https://learn.microsoft.com/en-us/data-integration/gateway/service-gateway-performance

```
WHEN FreeSpacePct < 5

Circuit breaker: max 3 fires / 30 min / host
```

**Action:** `user_data_function`

> Gateway {GatewayHostName} spool volume {SpoolDriveLetter} at {FreeSpacePct}% free ({SpoolDirSizeBytes} bytes spooled). Approve spool clear?

**Approval gate:** Teams Adaptive Card (Approve/Deny), 15-min timeout -> defer + ticket

**Remediation.** Delete spool files older than the retention window, or expand the volume. If bronze_disk_spool.StreamBeforeRequestCompletes_Warning is true, spool size reporting is unreliable (SpoolingTotalDataSize reads 0 while streaming is active) -- trust FreeSpacePct over SpoolDirSizeBytes in that case.

*Note: The only rule that was already correct. Its UDF action cannot execute until the on-prem action bridge exists (roadmap T45) -- Fabric User Data Functions do not support on-premises gateways.*

---

## Rule 4: `credential-drift`

**Pain point:** #10 credential state drift  
**Tier:** 1  
**Source table:** `bronze_gateway_datasources`  
**Grouping:** per DatasourceId

Gateway datasource credentials go invalid between refresh runs (OAuth token expiry, service-account lockout, UNC path changes). The REST datasource status endpoint GET /gateways/{id}/datasources/{id}/status exists, but no tool uses it proactively for alerting.

```
WHEN Status != 'Live'

Transition: requires 3 consecutive intervals in a non-Live state (debounce -- a transient probe failure is not credential drift). At the 15-minute inventory interval this is a deliberately slow ~45-minute alert.
```

**Action:** `notify` -> Teams

> Datasource {DatasourceName} ({DatasourceType}) on cluster {GatewayClusterName} status={Status}. Likely credential/OAuth expiry.

**Remediation.** Re-enter the datasource credentials: Power BI Service -> Settings -> Manage connections and gateways -> select the gateway -> Manage datasources -> select the datasource -> Edit credentials. Any dataset using this datasource will fail refresh until resolved.

<details><summary>Fix history</summary>

Was 'datasource_status != Online' -- a column that exists nowhere, compared to a value outside the vocabulary (the collector emits Live/Unknown/Error, never Online), with three message placeholders that were also not columns of this table. Pain point #10 had never been capable of firing. Now keyed on the real column and vocabulary.

</details>

---

## Rule 5: `error-rate-spike`

**Pain point:** #2 unified failure triage  
**Tier:** 1  
**Source table:** `gold_gateway_health`  
**Grouping:** per GatewayObjectId

The query_count_5min > 10 leg is deliberate: without a volume floor, a single failure in a 2-query window reads as a 50% error rate and pages someone at 3am over nothing.

```
WHEN error_rate_pct > 25 AND query_count_5min > 10
```

**Action:** `notify` -> Teams

> Gateway {GatewayObjectId} error rate {error_rate_pct}% over {query_count_5min} queries in 5m (avg {duration_avg_ms}ms, p95 {duration_p95_ms}ms).

**Remediation.** Open the report's Failure Triage page for this gateway. Common causes in order of frequency: datasource connectivity, credential expiry (cross-check the credential-drift rule), network timeout (cross-check network-saturation).

<details><summary>Fix history</summary>

Was 'failure_rate_5m > 0.25 AND query_count_5m > 10' against bronze_query_execution. Neither column existed on any table; the correct columns are error_rate_pct and query_count_5min on gold_gateway_health. error_rate_pct is PERCENT-scaled, so the intended 25% threshold is 25, not 0.25 -- as originally written it would have fired on any window with a single error. The validator's scale check now blocks that specific confusion.

</details>

*Deferred: The original action was run_notebook llm_alert_enrichment (LLM root-cause enrichment). Downgraded to notify: that notebook does not exist. Restore when the Data Agent explanation layer lands (roadmap T44).*

---

## Rule 6: `cpu-anomaly`  `[DISABLED]`

**Pain point:** #5 mashup CPU/memory bloat  
**Tier:** 1  
**Source table:** `gold_predictions`  
**Grouping:** per GatewayObjectId

> **This rule is disabled and will not fire.**  
> roadmap T42 -- gold_predictions is not materialized. series_decompose_anomalies is a reference query that no scheduled job runs, so nothing writes anomaly_score. Re-enable when the anomaly job lands.

```
WHEN anomaly_score > 2.5
```

**Action:** `notify` -> Teams

> Gateway {GatewayObjectId} {metric} anomaly (score {anomaly_score}) vs seasonal baseline.

<details><summary>Fix history</summary>

Was enabled against a table nothing writes.

</details>

---

## Rule 7: `mashup-container-runaway`

**Pain point:** #5 mashup memory/CPU bloat, per-process visibility  
**Tier:** 1  
**Source table:** `gold_mashup_health`  
**Grouping:** per GatewayObjectId / HostName

```
WHEN peak_working_set_mb > 6000

Transition: fires when a mashup container's working set crosses the runaway threshold

Threshold:  6000 MB. Tune per host RAM -- see roadmap T25 (Day-0 calibration sets this from the host's own p99 rather than a shared default).

Circuit breaker: max 3 fires / 30 min / host
```

**Action:** `notify` -> Teams

> Runaway mashup container on {HostName} ({GatewayObjectId}): peak {peak_working_set_mb}MB across {distinct_processes} processes, peak CPU {peak_cpu_pct}%.

<details><summary>Fix history</summary>

Condition carried an inline '// MB; tune per host RAM' comment. No expression surface accepts inline comments, so the condition was unparseable as written; the prose also tokenized into phantom column references. Moved to a threshold_note field.

</details>

---

## Rule 8: `schema-drift`

**Pain point:** #4 gateway upgrades silently change log columns  
**Tier:** 1  
**Source table:** `bronze_schema_warnings`  
**Grouping:** per log_type

A gateway version upgrade can rename or drop a column in the CSV logs. The schema-adaptive parser absorbs the change without failing, which is correct for availability but means the change is otherwise INVISIBLE -- downstream silver transforms and DAX measures reference columns by name and start returning nulls with no error. This is pain point #4, and until roadmap T7 the signal was print()ed to notebook stdout and discarded.

```
WHEN severity != 'added'

Transition: fires on any new row -- rows are written ONLY when drift is detected, so every row is actionable by construction
```

**Action:** `notify` -> Teams

> Gateway log schema drift in {log_type} (severity {severity}): missing [{missing_columns}], added [{added_columns}]. Expected {expected_column_count} columns, saw {actual_column_count}. Downstream transforms referencing the missing columns are now returning nulls.

**Remediation.** Compare missing_columns against the current gateway version's documented schema (https://learn.microsoft.com/en-us/data-integration/gateway/service-gateway-performance). If a column was renamed, add the mapping to the parser's column contract. If dropped, audit which silver transforms and DAX measures reference it -- they are now silently producing nulls.

*Note: Condition excludes severity='added' because a purely additive change is forward-compatible -- mergeSchema absorbs it and nothing downstream breaks. Additive drift is still recorded in the table for audit, just not paged on.*

---

## Rule 9: `network-saturation`

**Pain point:** #7 network saturation blind spot (differentiator #4)  
**Tier:** 2  
**Source table:** `gold_gateway_health`  
**Grouping:** per GatewayObjectId

The gateway-to-cloud NIC is a documented Microsoft blind spot -- no first-party tool surfaces it. Sustained saturation during large refreshes manifests to operators as slow or timing-out queries with no visible cause.

```
WHEN network_utilization_pct_avg > 80

Transition: requires 3 consecutive intervals above threshold (sustained saturation, not a single burst)
```

**Action:** `notify` -> email, Teams

> Gateway {GatewayObjectId} ({GatewayNodeName}) NIC utilization {network_utilization_pct_avg}% sustained. Latency p95 {latency_ms_p95}ms, avg {latency_ms_avg}ms. Large refreshes may be saturating the gateway-to-cloud link.

**Remediation.** Schedule large refreshes off-peak, or add bandwidth. Correlate with gold_query_performance.spool_total_bytes_sum for the same window to confirm a refresh is the driver.

<details><summary>Fix history</summary>

This rule existed ONLY in activator-rules.md and was absent from the JSON entirely -- part of the 5-vs-7 rule-count discrepancy (roadmap T4). Ported in and validated: network_utilization_pct_avg, latency_ms_p95 and latency_ms_avg are all real gold_gateway_health columns.

</details>

---

---

## Implementation notes

1. **"Becomes true" vs "Is true".** Use *Becomes true* for every stateful rule.
   *Is true* re-fires on each evaluation cycle and produces an alert storm for
   the duration of an outage.

2. **Route P1 to a channel, not to people.** Activator throttles Teams and email
   at **30 messages per recipient per hour** but **500 per item per hour**.
   During a fleet-wide outage, per-recipient routing silently drops messages
   exactly when they matter most.

3. **Own the ingestion under a service principal.** Activator's Power BI-sourced
   ingestion is owned by a single user identity. If that user loses access or
   rotates credentials, **ingestion and rule evaluation stop with no
   notification**.

4. **Thresholds here are defaults, not calibrations.** Every numeric threshold
   is a generic starting point calibrated against nothing. Roadmap T25 (Day-0
   calibration) sets them per-tenant from that gateway's own observed
   percentiles. Do not treat these numbers as validated.

5. **Testing.** Roadmap T23 builds `Invoke-FaultInjection.ps1` to induce each
   condition safely and measure real time-to-detect. Until then these rules are
   `[Unverified]` against live behavior.

6. **Lifecycle landmine.** Activator items using Power BI or Blob Storage as a
   source, or a **User Data Function as an action**, currently break Fabric's
   deployment-pipeline and Git integration. The rules here read from Delta
   tables, which is the safe side of that gap -- but `spool-disk-critical-remediate`
   would walk into it once its UDF action is wired.

---

*Generated from `activator-rules.json`. Rule prose, thresholds, and remediation
steps are fields in that file -- edit them there.*
