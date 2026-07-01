# Fabric Activator Alerting Rules
## Label: [NET-NEW]

**Pain points addressed:**
- #1 — No real-time gateway-offline alerting (DIFFERENTIATOR #1)
- #7 — Network saturation alerting
- #9 — Disk spool free-space alerting
- #10 — Credential drift detection

**[Unverified]** Fabric Activator rule syntax is evolving as of June 2026.  
The rule DSL and configuration approach described here are based on the Fabric Activator documentation:  
[learn.microsoft.com/en-us/fabric/real-time-intelligence/activator-introduction](https://learn.microsoft.com/en-us/fabric/real-time-intelligence/activator-introduction)  
**All rules require Phase 5 live-environment validation.** The exact JSON/visual configuration format must be confirmed against the Activator version in your Fabric tenant.

**[Assumption]** Activator is available at F8+ Fabric capacity.  
**[Assumption]** The `gold_gateway_health` Delta table is used as the Activator data source, refreshed every 5 minutes by the Gold notebook pipeline.

---

## Data Source Configuration

| Item | Value |
|---|---|
| **Activator data source** | Delta table: `gold_gateway_health` |
| **OneLake path** | `abfss://<workspace>@onelake.dfs.fabric.microsoft.com/<lakehouse>.Lakehouse/Tables/gold_gateway_health` |
| **Activator trigger mode** | When Delta table is updated (after Gold notebook completes) |
| **Key column for entity identification** | `GatewayObjectId` (one alert per node) |

---

## Rule 1: gateway-offline
### Purpose: Gateway Node Offline Alert (DIFFERENTIATOR #1, Pain #1)

**Trigger:** A gateway node's heartbeat has not been seen for longer than the configured threshold.

**Background:**  
No existing tool (FPM, PBIT template, or any other) provides proactive offline alerting without a Logic App or custom Power Automate. This rule implements Pain #1's top requested feature using native Fabric Activator — zero Logic App required.

```
Rule Name:     gateway-offline
Entity:        GatewayObjectId (one alert instance per unique gateway node)
Data Source:   gold_gateway_health (Delta table, updated every 5 min)

Condition:
  WHEN heartbeat_age_minutes > <OFFLINE_THRESHOLD_MINUTES>
  -- Configured in config.json → alerting.gatewayOfflineThresholdMinutes
  -- Default: 10 minutes (two missed 5-minute collection intervals)

Deduplication:
  - Do not re-alert for the same GatewayObjectId until status returns to Live,
    then goes offline again. (Activator "Becomes true" trigger recommended.)

Alert Action:
  1. Send email to: <configured recipient list from config.json>
  2. Post Teams webhook message (optional, configure in config.json)

Suggested message template:
  "ALERT: Gateway node {{GatewayNodeName}} (cluster: {{GatewayClusterName}})
   has been OFFLINE for {{heartbeat_age_minutes}} minutes.
   Last seen: {{last_heartbeat_utc}}.
   Affected datasource count: {{DatasourceCount}}.
   This may cause scheduled refresh failures for datasets using this gateway.
   Dashboard: <link to report Fleet Overview page>"

Reference pain evidence:
  - "No clue why, and no notification either" (2018, community.fabric.microsoft.com)
  - https://community.fabric.microsoft.com/t5/Service/Gateway-status-emails-or-alerts/m-p/344928
```

---

## Rule 2: disk-spool-low
### Purpose: Spool Disk Space Alert (Pain #9)

**Trigger:** Spool drive free space falls below a warning or critical threshold.

**Background:**  
The gateway writes compressed query results to a spool directory. When disk fills, queries fail with cryptic errors that do not mention disk. No existing tool monitors this proactively.  
Source: [learn.microsoft.com](https://learn.microsoft.com/en-us/data-integration/gateway/service-gateway-performance)

```
Rule Name:     disk-spool-low
Entity:        GatewayObjectId

Condition (WARNING level):
  WHEN spool_free_pct < <SPOOL_WARN_THRESHOLD_PCT>
  -- Default: 15%
  -- Configured: config.json → alerting.spoolWarnThresholdPct

Condition (CRITICAL level):
  WHEN spool_free_pct < <SPOOL_ERROR_THRESHOLD_PCT>
  -- Default: 5%
  -- Configured: config.json → alerting.spoolErrorThresholdPct

Alert Action (WARNING):
  Teams message: "WARNING: Spool disk on {{GatewayNodeName}} is at
  {{spool_free_pct}}% free ({{spool_dir_size_bytes}} bytes in spool dir).
  Consider deleting old spool files or expanding disk before gateway runs."

Alert Action (CRITICAL):
  Email + Teams: "CRITICAL: Spool disk on {{GatewayNodeName}} at
  {{spool_free_pct}}% free. Gateway may stop accepting large query results.
  Immediate action required."

Note on StreamBeforeRequestCompletes:
  If spool_stream_mode_warn = true in the data, add to alert message:
  "Note: StreamBeforeRequestCompletes=true is set; spool size reporting
  may be inaccurate (SpoolingTotalDataSize=0 when streaming is active)."
```

---

## Rule 3: credential-drift
### Purpose: Datasource Credential State Drift (Pain #10)

**Trigger:** A gateway datasource has been in "Unknown" or "Error" status for N consecutive collection intervals.

**Background:**  
Gateway datasource credentials can become invalid between refresh runs (OAuth token expiry, service account lockout, UNC path changes). The REST datasource status API exists but no tool uses it proactively for alerting.  
REST endpoint: `GET /gateways/{id}/datasources/{id}/status`

```
Rule Name:     credential-drift
Entity:        DatasourceId (or GatewayObjectId if datasource-level not available)

Condition:
  WHEN status_current = "Unknown" OR status_current = "Error"
  FOR 3 consecutive collection intervals
  (i.e., the condition persists ≥15 minutes, not a transient failure)
  -- Configured: config.json → alerting.credentialDriftConsecutiveIntervals

[Assumption] gold_gateway_health includes datasource status summary.
             If datasource status is tracked separately in a
             gold_datasource_health table, update the data source reference.

Alert Action:
  Teams message: "CREDENTIAL ALERT: Datasource {{DatasourceName}}
  on gateway {{GatewayClusterName}} has status {{status_current}}
  for the last {{N}} collection intervals.
  This will cause refresh failures for any dataset using this datasource.
  Action: Re-enter datasource credentials in Power BI Service
  → Settings → Gateways → <gateway> → Manage datasources."
```

---

## Rule 4: error-rate-spike
### Purpose: Query Error Rate Spike Alert (Pain #2)

**Trigger:** Error rate for a gateway node exceeds threshold in the current 5-minute window.

```
Rule Name:     error-rate-spike
Entity:        GatewayObjectId

Condition:
  WHEN error_rate_pct > <ERROR_RATE_THRESHOLD_PCT>
  -- Default: 25%
  -- Configured: config.json → alerting.errorRateThresholdPct

Alert Action:
  Teams message: "ALERT: Gateway {{GatewayNodeName}} error rate is
  {{error_rate_pct}}% ({{error_count_5min}} errors in last 5 minutes).
  Dashboard Failure Triage page: <link>
  Common causes: datasource connectivity, credential expiry, network timeout."
```

---

## Rule 5: network-saturation
### Purpose: Network Utilization Alert (Pain #7, Differentiator #4)

**Trigger:** NIC utilization exceeds threshold for a sustained window.  
**Note:** Only available when Collect-NetworkMetrics.ps1 is deployed (v2+).

```
Rule Name:     network-saturation
Entity:        GatewayObjectId

Condition:
  WHEN network_utilization_pct_avg > <NETWORK_WARN_THRESHOLD_PCT>
  -- Default: 80%
  -- Configured: config.json → alerting.networkSaturationThresholdPct

Alert Action:
  Email: "WARNING: Gateway {{GatewayNodeName}} NIC utilization is
  {{network_utilization_pct_avg}}%. This may indicate the gateway→cloud
  bandwidth is saturating during large dataset refreshes.
  Current latency: {{latency_ms_p95}}ms (P95).
  Consider scheduling large refreshes off-peak or adding bandwidth."
```

---

## Configuration Summary

All thresholds are configured in `config/config.json` under the `alerting` section:

```json
{
  "alerting": {
    "gatewayOfflineThresholdMinutes": 10,
    "spoolWarnThresholdPct": 15.0,
    "spoolErrorThresholdPct": 5.0,
    "credentialDriftConsecutiveIntervals": 3,
    "errorRateThresholdPct": 25.0,
    "networkSaturationThresholdPct": 80.0,
    "notificationEmails": ["admin@your-org.com"],
    "teamsWebhookUrl": ""
  }
}
```

---

## Implementation Notes

1. **Activator setup**: Create a new Activator item in the same Fabric workspace as the Lakehouse. Connect it to the `gold_gateway_health` Delta table.

2. **Entity key**: Set `GatewayObjectId` as the entity key so Activator tracks alert state per gateway node independently.

3. **"Becomes true" vs "Is true"**: For the gateway-offline rule, use the "Becomes true" trigger (fires once when condition transitions from false to true) rather than "Is true" (fires every evaluation). This prevents alert storms during extended outages.

4. **Testing in Phase 5**: Simulate a gateway offline by stopping the collection script and confirming the Activator fires after the threshold window. Simulate spool disk low by temporarily setting the threshold near current free space.

5. **Known limitation**: Activator is not available in Power BI Premium Per User (PPU) license. Requires Fabric capacity F8+.

---

*References: [Fabric Activator introduction](https://learn.microsoft.com/en-us/fabric/real-time-intelligence/activator-introduction), [phase3_painpoints.md](../../../research/phase3_painpoints.md), [phase4_architecture.md](../../../research/phase4_architecture.md) §5.2*
