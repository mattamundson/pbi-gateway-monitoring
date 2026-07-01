<!-- Fabric Community forum post. Paste the title into the subject line and the body below.
     Suggested board: Fabric Community > Data Engineering, or Power BI > Service.
     Swap <REPO-URL> for the real link before posting. -->

# Title

Attributing gateway queries to a **user + dataset** by joining `RequestId` → Workspace Monitoring `OperationId`

# Body

**The problem**

The On-Premises Data Gateway performance logs (`QueryExecutionReport` etc.) expose `RequestId`, `DataSource`, `QueryType`, duration, spool size, success/error — but no `DatasetId`, `UserId`, or `ReportId`. So you can see the gateway is saturating, but not which model or which user to go fix. This has generally been treated as a hard limit of the gateway's flat-log schema.

**The insight**

It's joinable. The gateway `RequestId` is the same value the AS engine calls `XmlaRequestId`, which Fabric Workspace Monitoring surfaces as `OperationId` on the `PowerBIDatasetsWorkspace` table. Two documented links close the chain:

1. **`OperationId` == `XmlaRequestId`** — stated verbatim in the MS Learn [*Semantic model operation logs*](https://learn.microsoft.com/en-us/fabric/enterprise/powerbi/semantic-model-operations) reference: *"OperationId — … Same as XmlaRequestId."* That table also carries `ExecutingUser`, `ItemId`, `ItemName`, `WorkspaceId`, `CapacityId`, `CpuTimeMs`, `DurationMs`, `EventText`.
2. **`XmlaRequestId` == gateway `RequestId`** — shown by Chris Webb here: [Finding Power BI semantic model refresh operations in gateway logs](https://blog.crossjoin.co.uk/2024/09/01/finding-power-bi-semantic-model-refresh-operations-in-gateway-logs/).

**The query** (Workspace Monitoring enabled; gateway logs landed as `bronze_query_execution` in the same Eventhouse):

```kusto
let lookback = 24h;
let gw =
    bronze_query_execution
    | where QueryExecutionEndTimeUTC > ago(lookback)
    | project RequestId, GatewayObjectId, DataSource, QueryType,
              QueryExecutionDuration, SpoolingTotalDataSize, Success, ErrorMessage,
              QueryExecutionEndTimeUTC;
PowerBIDatasetsWorkspace
| where Timestamp > ago(lookback)
| where OperationName in ("QueryEnd", "ExecutionMetrics", "RefreshEnd")
| project Timestamp, OperationId, ExecutingUser, ItemId, ItemName,
          WorkspaceId, CapacityId, CpuTimeMs, DurationMs, EventText
| join kind=inner gw on $left.OperationId == $right.RequestId
| project QueryExecutionEndTimeUTC, ExecutingUser, ItemName, ItemId,
          DataSource, QueryType,
          GatewayDurationMs = QueryExecutionDuration,
          EngineCpuMs = CpuTimeMs, SpooledBytes = SpoolingTotalDataSize,
          Success, ErrorMessage
| order by QueryExecutionEndTimeUTC desc
```

**Known limits**
- Requires Workspace Monitoring (mutually exclusive with Log Analytics on the same workspace — if you're on Log Analytics, join on its `XmlaRequestId` column instead).
- Covers Fabric semantic-model refresh + query. **DirectQuery-through-gateway is an open question** — Webb's post implies the same `XmlaRequestId` covers DQ DAX queries; I haven't confirmed it end-to-end through a gateway.
- Dataflow Gen1 and Paginated Reports don't flow through this join.
- Gateway logs flush infrequently → near-real-time at best.

**What I'm hoping to learn**

I've desk-verified the join key against the docs above but **not yet against a live tenant**. If any of you run an on-prem gateway with Workspace Monitoring on: **what match rate do you see?** (i.e., of gateway queries in a window, what % get an `ExecutingUser` via this join — and does DirectQuery attribute?) Full write-up, reference implementation, and a structured "pilot report" issue form here: <REPO-URL>
