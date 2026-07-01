# Gateway Monitor — Power BI Report (PBIR)

> **[Unverified — requires Power BI Desktop open to finalize bindings]**  
> **Label: [NET-NEW]**
>
> This PBIR report skeleton is structurally valid JSON and references real measure/column
> names from `measures.dax` and the gold Delta table schema. It **must be opened in
> Power BI Desktop once** to finalize measure bindings, fix any queryRef encoding
> differences, and validate DirectLake connectivity before publishing.

---

## Files

| File | Purpose |
|------|---------|
| `gateway_monitor.report.json` | PBIR enhanced report definition (4 pages, hand-authored) |
| `definition.pbir` | PBIR pointer to the semantic model (relative path `../gateway_monitor.Dataset`) |
| `README.md` | This file |

---

## Report pages

### Page 1 — Fleet Overview
**Job:** "Is every gateway node alive and healthy right now?"

| Visual | Measure / Column | Source table |
|--------|-----------------|--------------|
| KPI: Heartbeat Age | `Heartbeat Age (minutes)` | `gold_gateway_health` |
| KPI: Nodes Offline | `Nodes Offline` | `gold_gateway_health` |
| KPI: Node Count | `Node Count` | `gold_dim_gateway` |
| KPI: Cluster Count | `Cluster Count` | `gold_dim_gateway` |
| KPI: Avg CPU % | `Avg Gateway CPU %` | `gold_gateway_health` |
| KPI: Avg Mem | `Avg Gateway Mem (MB)` | `gold_gateway_health` |
| KPI: Network Util % | `Network Utilization %` | `gold_gateway_health` |
| KPI: Load Skew | `Cluster Load Skew Score` | `gold_cluster_load` |
| KPI: Hottest Node % | `Hottest Node Query %` | `gold_cluster_load` |
| KPI: CPU Gap | `System CPU - Gateway CPU Gap %` | `gold_gateway_health` |
| Matrix: Fleet Status | Node × Cluster × Version × Status | `gold_dim_gateway` |
| Bar: Cluster Load | Cluster Load Skew Score by cluster | `gold_cluster_load` |
| Line: Latency P95 | `Network Latency P95 (ms)` | `gold_gateway_health` |

**Slicers:** `GatewayClusterName`, `GatewayNodeName`

---

### Page 2 — Query & Identity
**Job:** "Who is running what queries, and how long do they take?"

| Visual | Measure / Column | Source table |
|--------|-----------------|--------------|
| KPI: Total Queries | `Total Queries` | `gold_query_performance` |
| KPI: Avg Duration | `Avg Query Duration (ms)` | `gold_query_performance` |
| KPI: P95 Duration | `P95 Query Duration (ms)` | `gold_query_performance` |
| KPI: Max Duration | `Max Query Duration (ms)` | `gold_query_performance` |
| KPI: Error Rate % | `Gateway Error Rate %` | `gold_query_performance` |
| Line: Duration Trend | Avg Query Duration by hour | `gold_query_performance` |
| Stacked Bar: Attribution | Attribution % by confidence level | `silver_identity_attribution` |
| Bar: Top DataSources | Avg Duration by DataSource | `gold_query_performance` |
| Bar: By Artifact | Total Queries by artifact_id | `silver_identity_attribution` |
| Matrix: Query Detail | DataSource × QueryType × artifact_id | `gold_query_performance` |

**Slicers:** `DataSource`, `QueryType`

> ⚠ Attribution disclaimer: "Best-effort only. Paginated Reports, Dataflow Gen1,
> and DirectQuery sessions are not reliably attributed." This label appears in-page.

---

### Page 3 — Failures & Triage
**Job:** "What failed, why, and which layer caused it?"

| Visual | Measure / Column | Source table |
|--------|-----------------|--------------|
| KPI: Total Errors | `Total Errors` | `gold_query_performance` |
| KPI: Error Rate % | `Gateway Error Rate %` | `gold_query_performance` |
| KPI: Error Rate 5min | `Error Rate 5min %` | `gold_gateway_health` |
| KPI: Triage Coverage | `Triage Coverage %` | `silver_triage` |
| KPI: OS Event Failures | `Failures With OS Event` | `silver_triage` |
| Line: Error Rate Trend | Error Rate % by date | `gold_query_performance` |
| Bar: Failure Layer | Failures by failure_layer | `silver_triage` |
| Matrix: Triage Detail | DataSource × ErrorMessage × layer × confidence | `silver_triage` |
| Matrix: OS Events | evt_message × EventId × GatewayObjectId | `silver_triage` |

**Slicers:** `failure_layer`, `triage_confidence`

---

### Page 4 — Capacity & Spool
**Job:** "Are we running out of disk or CPU? Forecast the next crisis."

| Visual | Measure / Column | Source table |
|--------|-----------------|--------------|
| KPI: Avg CPU % | `Avg Gateway CPU %` | `gold_gateway_health` |
| KPI: Avg Mem | `Avg Gateway Mem (MB)` | `gold_gateway_health` |
| KPI: CPU Gap | `System CPU - Gateway CPU Gap %` | `gold_gateway_health` |
| KPI: Spool Free % | `Spool Free Space %` | `gold_gateway_health` |
| KPI: Spool Status | `Spool Disk Status` | `gold_gateway_health` |
| KPI: Spool Throughput | `Spool Throughput (MB/s)` | `gold_query_performance` |
| KPI: Data Spooled | `Total Data Spooled (GB)` | `gold_query_performance` |
| KPI: Spooled Query % | `Spooled Query %` | `gold_query_performance` |
| KPI: Network Util % | `Network Utilization %` | `gold_gateway_health` |
| KPI: Network Status | `Network Status` | `gold_gateway_health` |
| Line: CPU Trend | Avg CPU % by date | `gold_gateway_health` |
| Line: Spool Trend | Spool Free % by date | `gold_gateway_health` |
| Line: Network Trend | Network Utilization % by date | `gold_gateway_health` |
| Scatter: Spool vs Duration | Throughput vs Avg Duration by DataSource | `gold_query_performance` |
| Bar: Spooled % by DS | Spooled Query % by DataSource | `gold_query_performance` |

---

## How to deploy

### Option A — Fabric Git Integration (recommended)
1. Connect workspace to a Git repo in Fabric (Workspace Settings → Git Integration).
2. Commit this `starter/report/` folder to the connected branch.
3. In Fabric → Git Integration → Update All.
4. Open the report in Power BI Desktop to verify DirectLake bindings.
5. Publish back to Fabric service.

### Option B — Power BI Desktop import
1. Open Power BI Desktop (May 2024+ for PBIR enhanced format support).
2. File → Open → Browse to `gateway_monitor.report.json`.
3. Desktop will prompt to connect the semantic model. Point to the DirectLake
   semantic model over `gold_gateway_health`, `gold_query_performance`,
   `gold_cluster_load`, `gold_dim_gateway`, `silver_triage`,
   `silver_identity_attribution` in the deployed Lakehouse.
4. Fix any binding errors in the Fields pane (measure name mismatches show red).
5. Save as PBIX or re-export as PBIP, then publish to workspace.

---

## [Unverified] items requiring Desktop validation

| ID | Issue |
|----|-------|
| R1 | `queryRef` encoding in `projections` — exact format validated by Desktop, not hand-authoring |
| R2 | `singleVisual.visualType` strings (e.g. `"clusteredBarChart"`) — confirm against Desktop export |
| R3 | `config` JSON string escaping inside `visualContainers` — Desktop re-serializes on open |
| R4 | `definition.pbir` relative path `../gateway_monitor.Dataset` — adjust to match your dataset folder name |
| R5 | DirectLake semantic model setup: mark-as-date-table, date table relationship for time-intelligence measures |

---

*Generated by: deployable scaffolding build — 2026-06-30*  
*All [Unverified] items pending Phase 5 Fabric tenant pilot.*
