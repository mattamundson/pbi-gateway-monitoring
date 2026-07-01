# Attributing Power BI Gateway Queries to a User and Dataset

*A technique for On-Premises Data Gateway operators, grounded in Microsoft's own documentation.*

> **Status:** The join key described here is **verified against Microsoft's published documentation** (desk-verified 2026-07-01), not yet against a live tenant. If you run it in production, please [file a pilot report](../.github/ISSUE_TEMPLATE/pilot-report.yml) — the match rate you observe is the one number this writeup can't give you.

---

## The problem every gateway operator hits

The Power BI / Microsoft Fabric On-Premises Data Gateway writes performance logs — `QueryExecutionReport`, `QueryStartReport`, and friends. They tell you a query was slow, spilled to disk, or failed. They do **not** tell you the two things you actually need to act:

- **Which dataset** caused it, and
- **Which user** triggered it.

The gateway's `QueryExecutionReport` exposes `RequestId`, `DataSource`, `QueryType`, `QueryExecutionDuration`, `SpoolingTotalDataSize`, `Success`, `ErrorMessage` — and a timestamp. There is no `DatasetId`, no `UserId`, no `ReportId`. So when a gateway is saturating at 9am, you can see *that* it's drowning, but not *whose* refresh or *which* model to go fix. This has been treated as a permanent limitation of the gateway's flat-log schema.

## The insight: `RequestId` is not an orphan

It isn't permanent. The gateway's `RequestId` is the **same value** that the Analysis Services engine calls `XmlaRequestId`, which in turn surfaces in **Fabric Workspace Monitoring** as `OperationId`. That gives you a join key across a boundary everyone assumed was sealed:

```
Gateway log            AS engine / Log Analytics       Fabric Workspace Monitoring
QueryExecutionReport   XmlaRequestId                   PowerBIDatasetsWorkspace
    RequestId    ═══════════ (same value) ═══════════      OperationId
```

Once you can join gateway `RequestId` → `OperationId`, you inherit every identity field the engine logs: `ExecutingUser`, `ItemId` (the dataset), `ItemName`, `WorkspaceId`, `CapacityId`, engine `CpuTimeMs` / `DurationMs`, and the DAX/XMLA text itself.

## The evidence (primary sources, not inference)

Two independent, citable facts close the chain:

1. **`OperationId` == `XmlaRequestId`** — stated verbatim in Microsoft's own reference for the semantic-model operation logs. The [Semantic model operation logs](https://learn.microsoft.com/en-us/fabric/enterprise/powerbi/semantic-model-operations) page describes the `OperationId` column as: *"Unique identifier of the operation or the request. **Same as `XmlaRequestId`**."* The same page documents `ExecutingUser`, `ItemId`, `ItemName`, `WorkspaceId`, `CapacityId`, `CpuTimeMs`, `DurationMs`, and `EventText` on the same `PowerBIDatasetsWorkspace` table.

2. **`XmlaRequestId` == gateway `RequestId`** — demonstrated by Chris Webb (Microsoft / Fabric CAT) in [Finding Power BI semantic model refresh operations in gateway logs](https://blog.crossjoin.co.uk/2024/09/01/finding-power-bi-semantic-model-refresh-operations-in-gateway-logs/): *"you can match the `XmlaRequestId` value from Log Analytics to the value in the `RequestId` column in the log file."*

Chain the two and you have gateway `RequestId` → `OperationId` → identity. No change to the gateway. No custom instrumentation.

## The query

Prerequisite: **Workspace Monitoring enabled** on the workspace(s) whose semantic models refresh through the gateway (it lands `PowerBIDatasetsWorkspace` in an Eventhouse KQL DB). Land your gateway logs into the same Eventhouse (or reach them via a OneLake shortcut) as `bronze_query_execution`, then:

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
| project
    QueryExecutionEndTimeUTC,
    ExecutingUser,                          // WHO
    ItemName, ItemId,                       // WHICH dataset
    DataSource, QueryType,
    GatewayDurationMs = QueryExecutionDuration,
    EngineCpuMs = CpuTimeMs,
    SpooledBytes = SpoolingTotalDataSize,
    Success, ErrorMessage,
    RequestId = OperationId
| order by QueryExecutionEndTimeUTC desc
```

## What it unlocks

- **Top offending users by gateway load** — `summarize sum(GatewayDurationMs) by ExecutingUser`.
- **Top datasets by spool pressure** — `summarize sum(SpooledBytes) by ItemName`.
- **Failed queries *with their owner*** — the report operators have wanted for years: `where not(Success) | project ExecutingUser, ItemName, DataSource, ErrorMessage`.
- **Gateway time vs engine time** side by side — is the gateway the bottleneck, or the model?

## Honest scope and limits

- **Prerequisite:** Workspace Monitoring must be on (it's mutually exclusive with Log Analytics on the same workspace — if you use Log Analytics, join on its `XmlaRequestId` column instead).
- **Timing:** gateway logs flush to disk infrequently, so this is near-real-time at best, not live.
- **Covered:** Fabric semantic-model refresh and query. **DirectQuery is an open question** — Chris Webb's write-up suggests the same `XmlaRequestId` covers DirectQuery DAX queries too; confirm it in your tenant before relying on it.
- **Not covered (`[Blocked-by-platform]`):** Dataflow Gen1 and Paginated Reports don't flow through this join at all.
- **Fallback without Workspace Monitoring:** the gateway `QueryStartReport`'s `EvaluationContext` field already carries `artifactId` (the dataset) for Fabric semantic-model / Dataflow Gen2 / Power Platform workloads. That gives you *dataset* attribution (not user) with no Workspace Monitoring dependency — parse it with `parse_json(EvaluationContext).artifactId`.

## Reference implementation

A full Fabric-native monitoring kit built on this technique — collectors, a bronze→silver→gold medallion, the KQL above, and a Phase-5 validation matrix — is at **[pbi-gateway-monitoring](../README.md)**. Everything is labeled `[Unverified]`/`[Feasible-now]` per an honesty charter; the identity join is the flagship, and the one number still missing is the real-tenant **match rate**. If you have a live gateway, that's the contribution that would help most.
