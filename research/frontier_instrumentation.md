# Frontier Instrumentation for Power BI / Microsoft Fabric On-Premises Data Gateway Monitoring

*Research date: June 30 – July 1, 2026 | Audience: Principal data engineer building an open-source gateway monitoring tool*

---

## Executive Summary

The two hard ceilings of parsing the gateway's flat CSV logs — fuzzy query-to-identity attribution and invisible per-query network cost — are both breakable, but by different techniques. The **clearest immediate path to breaking Ceiling 1 (identity)** is a tri-source join: the gateway `QueryStart` log's `EvaluationContext` field (which already carries `datasetId` for Fabric/semantic-model workloads) cross-referenced with `XmlaRequestId` in Fabric Workspace Monitoring's Eventhouse. **Ceiling 2 (network)** is breakable on Windows exclusively via ETW's `Microsoft-Windows-TCPIP` and `Microsoft-Windows-Kernel-Network` kernel providers — the Windows-native substitute for what eBPF does on Linux. OTel .NET auto-instrumentation is the high-leverage mechanism for per-query span context within the gateway process itself.

---

## The Two Ceilings: Diagnosis and Feasibility Map

| Ceiling | Root Cause | Best Technique | Feasibility |
|---|---|---|---|
| **1. Query→identity attribution** | Gateway CSV logs expose only `RequestId` + timestamp; no `DatasetId`, `UserId`, or `ReportId` | Gateway `EvaluationContext` field (already has `artifactId`) + XmlaRequestId join to Fabric Workspace Monitoring | [Feasible-now] |
| **2. Network bandwidth/latency** | Gateway diagnostics explicitly disclaim network visibility (confirmed in MS docs) | ETW `Microsoft-Windows-TCPIP` + `Microsoft-Windows-Kernel-Network` kernel providers, per-PID | [Feasible-with-effort] |

---

## Avenue A: OpenTelemetry .NET Auto-Instrumentation on the Gateway Process

### A.1 — Can OTel .NET Auto-Instrumentation attach to a process you don't own?

**Yes — this is its explicit design goal.** The [OpenTelemetry .NET Automatic Instrumentation](https://opentelemetry.io/docs/zero-code/dotnet/) project instruments any .NET application "without having to modify their source code" using the [CLR Profiling API](https://learn.microsoft.com/en-us/dotnet/framework/unmanaged-api/profiling/setting-up-a-profiling-environment). It does not require access to source code or NuGet packages in the target project. The mechanism works by setting machine-level environment variables that the CLR reads at process startup:

```powershell
# For .NET Framework (used by Microsoft.PowerBI.EnterpriseGateway)
COR_ENABLE_PROFILING = 1
COR_PROFILER         = {918728DD-259F-4A6A-AC2B-B85E1B658318}
COR_PROFILER_PATH_32 = $INSTALL_DIR/win-x86/OpenTelemetry.AutoInstrumentation.Native.dll
COR_PROFILER_PATH_64 = $INSTALL_DIR/win-x64/OpenTelemetry.AutoInstrumentation.Native.dll
```

When these are set as **machine-level** (`[System.EnvironmentVariableTarget]::Machine`) environment variables in the Windows registry under `HKLM\SYSTEM\CurrentControlSet\Control\Session Manager\Environment`, they apply to **all** subsequently-launched CLR processes on that machine, including `Microsoft.PowerBI.EnterpriseGateway.exe` and each `Mashup.Container.NetFX45.exe` child process — after a service restart. The [Microsoft .NET profiling docs](https://learn.microsoft.com/en-us/dotnet/framework/unmanaged-api/profiling/setting-up-a-profiling-environment) confirm: *"If you set the variables in the system environment, they will apply to all managed applications that start after that point."*

The latest release is [v1.13.0-beta.1](https://github.com/open-telemetry/opentelemetry-dotnet-instrumentation) (as of mid-2026). The project is active and CI-tested on Windows Server 2022 and 2025 x64.

**Feasibility label: [Feasible-with-effort]**

Effort = Windows Service restart required; the gateway service must be restarted for CLR env vars to take effect. This is a controlled, planned operation. The PowerShell install module (`OpenTelemetry.DotNet.Auto.psm1`) automates the env-var injection step.

---

### A.2 — What does OTel .NET Auto-Instrumentation actually capture without source changes?

Per the [OTel .NET Available Instrumentations page](https://opentelemetry.io/docs/zero-code/dotnet/instrumentations/) (last updated April 23, 2026 for v1.15.0):

**Traces (via bytecode injection / CLR profiler):**

| Instrumentation ID | Library instrumented | Type | Relevance to gateway |
|---|---|---|---|
| `HTTPCLIENT` | `System.Net.Http.HttpClient` + `HttpWebRequest` | source | Gateway's outbound HTTP calls to Power BI service — **very high** |
| `ADONET` | ADO.NET (all `IDbCommand` implementors) | bytecode | SQL queries sent by Mashup engine to SQL Server, Oracle, etc. — **high** |
| `SQLCLIENT` | `Microsoft.Data.SqlClient` / `System.Data.SqlClient` | bytecode | SQL Server specifically — **high** |
| `AZURE` | Azure SDK | source | Any Azure connector calls — medium |
| `GRPCNETCLIENT` | `Grpc.Net.Client` | source | gRPC calls — medium |
| `WCFCLIENT` | WCF client calls | bytecode | Gateway ↔ Power BI service (WCF-based) — **potentially high** |
| `WCFSERVICE` | WCF service | bytecode | Inbound WCF from Power BI service — **potentially high** |

**Metrics (runtime, no source changes required):**

- `PROCESS` — CPU time, memory, thread count per process
- `NETRUNTIME` — GC pauses, heap sizes, JIT compilations, thread pool saturation
- `ASPNETCORE` / `HTTPCLIENT` — request rate, error rate, latency histograms

**Each span carries:** service name, trace ID, span ID, parent span ID, start time, duration, destination host:port, HTTP status code, DB statement (optional), and any baggage/W3C TraceContext headers propagated from the caller.

**What it does NOT capture without source changes:**
- The gateway's internal `RequestId` or `QueryTrackingId` — those live in gateway-managed data structures only accessible to its own code
- `UserId` — the gateway receives this from the Power BI service upstream; it is not exposed in outbound HTTP header payloads by default
- Mashup M formula context — the Mashup engine's internal query plan is opaque

**Feasibility label for span capture: [Feasible-with-effort]**

---

### A.3 — Per-query duration + destination from spans: does this break the ceilings?

**Ceiling 2 (network cost):** Partially. The `HTTPCLIENT` instrumentation captures **per-call HTTP duration** including DNS resolution + TCP connect + TLS handshake + first-byte time, with the destination URL as a span attribute. The `ADONET`/`SQLCLIENT` instrumentations capture **database round-trip duration**. These are not raw bytes-on-wire, but they give per-query round-trip latency to each datasource, which is more actionable than NIC-level counters. **This does not capture bytes transferred — only latency.**

**Ceiling 1 (identity):** Partially. Spans from HTTP calls will carry any **W3C TraceContext** headers that were injected by the Power BI service upstream. If the Power BI service propagates `traceparent` headers into its WCF/HTTP calls to the gateway (currently [Unverified] — no public documentation confirms this), then OTel auto-instrumentation would capture a true distributed trace ID that correlates a gateway span to the upstream service request. Without that, spans from the gateway are isolated islands.

---

### A.4 — Risks of instrumenting a Microsoft-owned process

1. **Supportability:** Microsoft's support team may decline to assist with gateway issues if the CLR profiler is active. This is the standard position for third-party profilers on production Microsoft services. No explicit EULA prohibition has been found, but enterprise support contracts may exclude profiler-modified deployments. **Risk: Medium.**

2. **Stability:** The OTel .NET profiler uses monkey-patching (IL rewriting) at the bytecode level. If the gateway's managed code uses patterns that conflict with the injected stubs (reflection, custom AppDomain isolation), it could cause `NullReferenceException` or assembly load failures. The project includes a `RuleEngine` that validates before instrumenting and backs off rather than crashing if unsupported scenarios are detected. **Risk: Low-to-medium** with `OTEL_DOTNET_AUTO_FAIL_FAST_ENABLED=false` (default).

3. **Gateway update regression:** When Microsoft updates the gateway binary, the CLR profiler re-applies to the new version. If the update changes internal assembly structure, instrumentation could silently fail or crash. A testing stage in the monitoring tool's deployment is advisable. **Risk: Medium.**

4. **Mashup.Container child processes:** These are spawned by the gateway and inherit machine-level env vars. Each spawned container will also be instrumented. This is the desired behavior but multiplies the OTLP traffic (one trace per Mashup container per query). Service names must be disambiguated via `OTEL_SERVICE_NAME` or `OTEL_DOTNET_AUTO_EXCLUDE_PROCESSES`.

---

### A.5 — OTLP export path into Fabric-friendly sinks

As of June 2, 2026, [Azure Monitor's direct OTLP ingestion is generally available](https://techcommunity.microsoft.com/blog/azureobservabilityblog/direct-opentelemetry-ingestion-into-azure-monitor-is-now-generally-available/4524044). The path is:

```
Gateway process (OTel .NET auto-instrumentation)
  → OTLP/HTTP (port 4318) 
  → OTel Collector (local sidecar on gateway host)
  → [Fan-out]
       ├─ Azure Monitor OTLP endpoint → Log Analytics (traces+logs) + Azure Monitor Workspace (metrics/Prometheus)
       ├─ Azure Data Explorer / Fabric Eventhouse (via ADX exporter, beta status, traces+metrics+logs)
       └─ Application Insights (distributed tracing UI, E2E performance investigation)
```

The [OTel Collector Azure Data Explorer exporter](https://github.com/open-telemetry/opentelemetry-collector-contrib/blob/main/exporter/azuredataexplorerexporter/README.md) is in **beta** for traces, metrics, and logs, and explicitly supports "Real time analytics in Fabric" (Eventhouse). Per the [Azure Data Explorer OTel connector docs](https://learn.microsoft.com/en-us/azure/data-explorer/open-telemetry-connector): *"This connector can be used in Real-Time Intelligence in Microsoft Fabric."*

The Fabric Eventstream also supports a **Custom Endpoint** (Apache Kafka protocol or HTTP) that can receive arbitrary streaming data including custom telemetry — allowing an OTel Collector to push spans into an Eventhouse KQL database directly, where they can be correlated with Workspace Monitoring data via KQL joins.

**Feasibility label for OTLP→Eventhouse pipeline: [Feasible-with-effort]**

---

## Avenue B: eBPF / ETW — Host-Level Deep Network + Syscall Tracing

### B.1 — eBPF on Windows: Honest 2026 Maturity Assessment

**[Blocked-by-platform] for production use on Windows.**

The [eBPF for Windows project](https://microsoft.github.io/ebpf-for-windows/) (Microsoft) remains *"a work-in-progress"* as of mid-2026. A [February 2024 GitHub discussion](https://github.com/microsoft/ebpf-for-windows/discussions/3285) from a Microsoft maintainer confirmed: *"We have not published any MSFT signed binaries yet so you will have to use test-signing mode."* This means enabling Windows test-signing mode — which disables Secure Boot protection on the kernel and violates most enterprise security policies. No production-signed GA release has been announced as of the research date.

**Grafana Beyla** (the most capable eBPF auto-instrumentation tool for HTTP/gRPC/NET apps) explicitly requires [Linux with kernel ≥ 5.8 with BTF enabled](https://github.com/grafana/beyla). The [Grafana community](https://community.grafana.com/t/what-are-the-basics-of-ebpf-and-beyla/142212) is explicit: *"As of now, Grafana Beyla does not support Windows, as it relies on the Linux kernel's eBPF capabilities."* Beyla is **Linux-only**.

**Pixie** (CNCF) is Kubernetes-only, Linux-only. **Cilium/Hubble** is Linux-only.

**The real Windows-native path is ETW (Event Tracing for Windows).**

---

### B.2 — ETW as the Windows-Native eBPF Equivalent: Breaking Ceiling 2

ETW is the correct technology to break the network-cost ceiling on Windows. It is production-ready, zero-overhead when idle, kernel-resident, and does not require test-signing mode.

**Key ETW providers for network monitoring:**

#### `Microsoft-Windows-TCPIP` (kernel provider)
- Enabled via `EVENT_TRACE_FLAG_NETWORK_TCPIP` in the NT Kernel logger session
- Events include `TcpIp_SendIPV4` (Event Type 10) and `TcpIp_TypeGroup1` Receive (Event Type 11)
- Per the [TcpIp_SendIPV4 class documentation](https://learn.microsoft.com/en-us/windows/win32/ETW/tcpip-sendipv4): each send event contains `PID` (process ID), `size` (bytes), source/destination IP:port, and a `connid` (connection identifier for correlation)
- This gives **per-process, per-connection bytes sent and received** in real time

#### `Microsoft-Windows-Kernel-Network` (analytic channel)
- Event IDs 10 and 11: **Number of bytes transmitted/received** per connection, attributable to `ProcessId`
- Event IDs 12 (attempted connection) and 15 (established connection): source/dest IP, port, PID
- This provides **connection-level attribution** to the gateway PID and each Mashup.Container child PID

#### Correlation technique (TCB-based join)
Per [ETWAnalyzer's TCP documentation](https://github.com/Siemens-Healthineers/ETWAnalyzer/blob/main/ETWAnalyzer/Documentation/DumpTCPCommand.md) from Siemens Healthineers (a production ETW TCP analysis tool):
- The `TCB` (Transmission Control Block) value in ETW events is a stable handle for a TCP connection's lifetime
- **TCP Rundown events** at session start emit all *currently active* TCP connections with their `TCB`, `PID`, and IP:port tuple
- Subsequent `Send`/`Receive` events carry the same `TCB`, enabling a full join: `TCB → PID → gateway RequestId` via time-window correlation

**What this enables for Ceiling 2:**
- Per-query bytes sent to the data source (SQL Server, SAP, etc.) attributable to the gateway PID
- Per-query TCP retransmission count and induced latency (RTT measurement)
- TCP template detection (DataCenter vs Internet — indicating whether the link is treated as low-latency)
- Connection establishment latency (time from TCP SYN to established, per `connid`)

**What it does NOT give:**
- Application-layer query context (which SQL statement corresponds to which TCP stream) — that correlation requires either OTel spans (from Avenue A) or a time-window join with gateway log `QueryExecutionStartTimeUTC`
- TLS payload inspection (encrypted traffic remains opaque to kernel-level ETW)

**Feasibility label: [Feasible-with-effort]**

The effort is: building a real-time ETW consumer in C# or Python (`pywintrace` library) that subscribes to the TCPIP/Kernel-Network providers, filters by the gateway PID and its child Mashup.Container PIDs, and emits per-connection metrics as OTel metrics via the OTLP exporter.

---

### B.3 — ETW WCF / RPC tracing (bonus)
- `Microsoft-Windows-WCF` ETW provider: traces WCF service calls including the endpoint, operation name, and duration — relevant because the gateway exposes a WCF endpoint to the Power BI service
- `Microsoft-Windows-RPC` ETW provider: captures RPC client/server calls at the interface+method level

These can provide additional context about what the Power BI service is asking the gateway to do, at the WCF operation name level.

---

## Avenue C: Distributed Tracing Correlation for Identity Attribution

### C.1 — Does the Power BI / Fabric request carry a traceable correlation ID end-to-end?

**Partially confirmed. The `EvaluationContext` field in gateway QueryStart logs is the key.**

Per the [Microsoft Learn gateway performance monitoring page](https://learn.microsoft.com/en-us/data-integration/gateway/service-gateway-performance) (last updated June 12, 2025):

> **EvaluationContext**: Contains the `artifactId` (for example, `datasetid` for semantic models, `dataflowsId`, and so on) along with extra trace IDs depending on the artifact. **This field only populates for supported workloads in Fabric, Power Platform, Azure Analysis Services, and certain connectors in Azure Logic Apps. These workloads include Semantic Models, Dataflow Gen2, and Power Platform dataflows.**

This is a **major underdocumented finding**: for Fabric Semantic Model workloads (the most common Power BI gateway use case), **`EvaluationContext` already contains the `datasetId`** in the `QueryStart` log. This partially breaks Ceiling 1 without any instrumentation. The field does not populate for Power BI Dataflow Gen1 or Power BI Paginated Reports (blocked for those workloads).

**What EvaluationContext provides:**
- `datasetId` (= `ItemId` in Workspace Monitoring) for Semantic Model workloads
- Additional trace IDs depending on workload (exact schema is not fully public)

**What it still does NOT provide:**
- `UserId` — the identity of the Power BI user who triggered the query
- `ReportId` / `VisualId` — which report visual generated the DAX/DirectQuery

---

### C.2 — XmlaRequestId / OperationId Join: Closing the Loop

Per the [Chris Webb analysis](https://blog.crossjoin.co.uk/2024/09/01/finding-power-bi-semantic-model-refresh-operations-in-gateway-logs/) (Power BI CAT team, September 2024) and confirmed by the [Fabric Workspace Monitoring semantic model operation logs schema](https://learn.microsoft.com/en-us/fabric/enterprise/powerbi/semantic-model-operations):

The join key is **`XmlaRequestId`** (Analysis Services session identifier):
- In **gateway logs**: `RequestId` column = `XmlaRequestId`
- In **Fabric Workspace Monitoring Eventhouse** table (`PowerBIDatasetsWorkspace`): `OperationId` column = `XmlaRequestId`
- In **Azure Log Analytics** (older path): `XmlaRequestId` column

**Sample KQL join (Fabric Workspace Monitoring)**:
```kql
let gateway_requests = externaldata(RequestId:string, QueryExecutionDuration:long, DataSource:string)
    [@"https://<storage>/gateway-logs/QueryExecution.csv"] with (format="csv");

PowerBIDatasetsWorkspace
| where OperationName in ("QueryBegin", "QueryEnd", "ExecutionMetrics")
| join kind=inner gateway_requests on $left.OperationId == $right.RequestId
| project Timestamp, ExecutingUser, ItemId, ItemName, OperationId, QueryExecutionDuration, DataSource
```

This join provides:
- `ExecutingUser` — the UPN of the user who triggered the query (**breaks Ceiling 1**)
- `ItemId` / `ItemName` — the semantic model (dataset) name and ID
- `CapacityId`, `WorkspaceId` — full organizational context
- `CpuTimeMs`, `DurationMs` — server-side AS engine metrics
- `EventText` — the actual DAX query text (if configured)

**The Workspace Monitoring Eventhouse** also includes a `CorrelationId` column that "can be used to identify correlated events between multiple tables" — enabling cross-workload tracing.

**Feasibility label: [Feasible-now]**

The join works today with either:
1. Azure Log Analytics connected to the Fabric workspace (older, per [Log Analytics config guide](https://learn.microsoft.com/en-us/power-bi/transform-model/log-analytics/desktop-log-analytics-configure))
2. Fabric Workspace Monitoring Eventhouse (newer, preferred — real-time streaming, KQL-native, 30-day retention, per [Workspace Monitoring overview](https://learn.microsoft.com/en-my/fabric/fundamentals/workspace-monitoring-overview))

**Limitation:** Workspace Monitoring is mutually exclusive with Log Analytics on the same workspace. The tool must support both sinks.

---

### C.3 — Power BI Activity Log: UserId for query-level attribution

The [Power BI Admin Activity Log](https://learn.microsoft.com/en-us/power-bi/guidance/admin-activity-log) (accessible via `Get-PowerBIActivityEvent` or REST `getActivityEvents`) contains `UserId`, `DatasetId`, `WorkspaceId`, and activity type per event. However:

- **Granularity:** Activity log events represent report views and dataset refreshes as single events, not individual DirectQuery executions — so there is no per-DirectQuery record
- **Latency:** Near-real-time (15-30 min lag typical); not suitable for inline per-query attribution
- **Use case:** Best for session-level attribution (who refreshed which dataset at what time) rather than sub-second query attribution

**Feasibility label: [Feasible-now] for session attribution, [Blocked-by-platform] for per-DirectQuery attribution**

---

### C.4 — W3C TraceContext propagation from Power BI service to gateway

**[Unverified — no public documentation confirms this.**]

If the Power BI service were to inject W3C `traceparent` headers into its WCF/HTTP calls to the gateway, OTel auto-instrumentation on the gateway (Avenue A) would automatically pick up the `traceId` and `spanId`, creating a genuine distributed trace from the Power BI service through the gateway to the data source. This would be the gold standard.

Microsoft has been rolling out OTel-based distributed tracing internally across Azure services, but no public documentation confirms that the Power BI service currently propagates W3C TraceContext to the on-premises gateway. This is an open area to verify by:
1. Enabling OTel auto-instrumentation on the gateway (Avenue A)
2. Examining captured HTTP/WCF spans for the presence of `traceparent` or `x-ms-client-request-id` headers in the incoming calls from the Power BI service

The `x-ms-client-request-id` header is a Microsoft-specific correlation header present in most Azure services. It may map to the gateway's `RequestId` and would be captured by OTel `HTTPCLIENT` instrumentation as a span attribute.

---

## Avenue D: Modern Telemetry Pipeline Design — OTel-Native Architecture

### D.1 — OpenTelemetry as the Universal Telemetry Bus (2026 State)

OpenTelemetry reached [CNCF Graduation on May 21, 2026](https://byteiota.com/opentelemetry-cncf-graduation-developer-guide/) — the same maturity tier as Kubernetes. As of early 2026, it is natively integrated across Azure Monitor, AWS X-Ray, Google Cloud, Datadog, New Relic, Honeycomb, Jaeger, and hundreds of other platforms. OTLP is now the de facto wire protocol for observability ([per analysis of the 2026 OTel ecosystem](https://techbytes.app/posts/opentelemetry-2026-unified-observability-standard/)).

**The fourth signal — Continuous Profiling** — entered [CNCF Public Alpha on March 26, 2026](https://dev.to/x4nent/opentelemetry-profiles-public-alpha-ebpf-fourth-signal-collector-v01510-and-opamp-fleet-6g3), making OTel the first open standard unifying metrics, traces, logs, and profiling under a single SDK, OTLP wire protocol, and semantic convention layer.

**Semantic conventions relevant to the gateway:**
- `db.*` — database client calls (`db.system`, `db.statement`, `db.operation`, `db.name`) — v1 now **stable/locked**
- `http.*` — HTTP client/server (`http.request.method`, `http.response.status_code`, `url.full`) — v1 **stable/locked**
- `process.*` — CPU, memory, thread count
- `system.network.*` — bytes sent/received (used for custom ETW-to-OTel bridge metrics)

**Why OTLP-native design future-proofs the tool:**
- A single `otelcol` sidecar on the gateway host receives all signals (spans from CLR profiler, metrics from ETW bridge, logs from gateway CSV files parsed in real-time)
- The collector's fan-out pipeline routes to any sink (Fabric Eventhouse, Azure Monitor, Prometheus, Grafana Tempo) without changing the gateway-side instrumentation
- Adding a new backend (e.g., OpenSearch, SigNoz) requires only a new exporter configuration — zero code changes in the monitoring tool
- OTel Semantic Conventions ensure that database query duration from the gateway interoperates with the same schema as database metrics from any other OTel-instrumented service in the estate

**Collector pipeline architecture for the gateway monitoring tool:**
```
┌─────────────────────────────────────────────────┐
│  Gateway Host (Windows Server)                  │
│                                                 │
│  ┌────────────────────────┐                     │
│  │ Microsoft.PowerBI.     │  CLR Profiler        │
│  │ EnterpriseGateway.exe  │──(OTel auto-instr.)─►│
│  └────────────────────────┘                     │
│                                                 │
│  ┌────────────────────────┐                     │
│  │ Mashup.Container.      │  CLR Profiler        │
│  │ NetFX45.exe (×N)       │──(OTel auto-instr.)─►│ OTLP/HTTP
│  └────────────────────────┘              ▼      │
│                                   ┌───────────┐ │
│  ┌────────────────────────┐       │  OTel     │ │
│  │ ETW Consumer Service   │       │  Collector│ │──► Azure Monitor (GA OTLP)
│  │ (Microsoft-Windows-    │──────►│  Sidecar  │ │──► Fabric Eventhouse (ADX exporter)
│  │  TCPIP + Kernel-Net)   │       │           │ │──► Prometheus / Grafana
│  └────────────────────────┘       └───────────┘ │
│                                                 │
│  ┌────────────────────────┐                     │
│  │ Gateway CSV Log Parser │────────────────────►│  (legacy enrichment layer)
│  │ (existing tool)        │                     │
│  └────────────────────────┘                     │
└─────────────────────────────────────────────────┘
                    │
                    ▼ (external)
        ┌────────────────────────────┐
        │  Fabric Workspace          │
        │  Monitoring Eventhouse     │
        │  (KQL join on OperationId  │
        │   = RequestId for          │
        │   identity attribution)    │
        └────────────────────────────┘
```

---

### D.2 — Real-Time Push / Webhook / Event Path for Gateway State

**Honest assessment: No push path exists from the gateway itself today.**

The on-premises data gateway has no native webhook, EventGrid, or streaming API. The CSV log files are written on a configurable aggregation interval (default 5 minutes). Near-real-time monitoring via log parsing requires tail-based polling — either:
- A local file watcher on the `ReportFilePath` directory that emits new rows to the OTel Collector's filelog receiver
- The OTel Collector's `filelog` receiver (available in the Collector contrib distribution) can be configured to tail CSV files and emit new rows as log records in near-real-time

**The Power BI Admin API** (`getActivityEvents`) has a ~15-30 minute latency and is poll-only (no webhook/streaming). There is no SSE or WebSocket endpoint. **[Blocked-by-platform] for sub-minute gateway event streaming via public APIs.**

**The Fabric Eventstream** (Real-Time Intelligence) can ingest data via a Custom Endpoint (Kafka-protocol or HTTP), enabling a push model if a local OTel Collector or Kafka producer is deployed on the gateway host. The Eventstream can deliver to the same Eventhouse where Workspace Monitoring data lands — enabling a single KQL store for all observability data.

**Feasibility label for true push model: [Feasible-with-effort]** (requires local OTel Collector with filelog receiver + Eventstream custom endpoint on the gateway host side; no code changes in the gateway)

---

## Consolidated Feasibility Table

| Technique | Breaks Ceiling 1 (Identity)? | Breaks Ceiling 2 (Network)? | Feasibility | Primary Source |
|---|---|---|---|---|
| **EvaluationContext `datasetId` parsing** | ✅ Partial (dataset, not user) | ✗ | [Feasible-now] | [MS Learn Gateway Perf](https://learn.microsoft.com/en-us/data-integration/gateway/service-gateway-performance) |
| **XmlaRequestId → Workspace Monitoring Eventhouse join** | ✅ Full (`UserId`, `DatasetId`, DAX) | ✗ | [Feasible-now] | [MS Fabric Workspace Monitoring](https://learn.microsoft.com/en-my/fabric/fundamentals/workspace-monitoring-overview) / [Crossjoin blog](https://blog.crossjoin.co.uk/2024/09/01/finding-power-bi-semantic-model-refresh-operations-in-gateway-logs/) |
| **OTel .NET auto-instrumentation (CLR profiler on gateway)** | ✅ Partial (HTTP/WCF spans; full if PBI propagates traceparent) | ✅ Partial (latency, not bytes) | [Feasible-with-effort] | [OTel .NET zero-code](https://opentelemetry.io/docs/zero-code/dotnet/) / [GitHub](https://github.com/open-telemetry/opentelemetry-dotnet-instrumentation) |
| **ETW `Microsoft-Windows-TCPIP` kernel provider** | ✗ | ✅ Full (bytes, retransmits, RTT per PID) | [Feasible-with-effort] | [MS Learn ETW TcpIp_SendIPV4](https://learn.microsoft.com/en-us/windows/win32/ETW/tcpip-sendipv4) / [ETWAnalyzer TCP docs](https://github.com/Siemens-Healthineers/ETWAnalyzer/blob/main/ETWAnalyzer/Documentation/DumpTCPCommand.md) |
| **ETW `Microsoft-Windows-Kernel-Network`** | ✗ | ✅ Full (bytes per process, connection events) | [Feasible-with-effort] | [ETW Kernel-Network forensics](https://nasbench.medium.com/finding-detection-and-forensic-goodness-in-etw-providers-7c7a2b5b5f4f) |
| **Grafana Beyla (eBPF)** | ✗ | ✗ (Linux-only) | [Blocked-by-platform] | [Grafana Beyla](https://grafana.com/oss/beyla-ebpf/) |
| **eBPF for Windows (Microsoft project)** | ✗ | ✗ (no prod-signed binaries) | [Blocked-by-platform] | [eBPF for Windows](https://microsoft.github.io/ebpf-for-windows/) / [prod-readiness discussion](https://github.com/microsoft/ebpf-for-windows/discussions/3285) |
| **OTel Collector → Azure Monitor OTLP (GA)** | ✗ (infrastructure) | ✗ (infrastructure) | [Feasible-now] | [Azure Monitor OTLP GA](https://techcommunity.microsoft.com/blog/azureobservabilityblog/direct-opentelemetry-ingestion-into-azure-monitor-is-now-generally-available/4524044) |
| **OTel Collector → Fabric Eventhouse (ADX exporter)** | ✗ (infrastructure) | ✗ (infrastructure) | [Feasible-with-effort] | [ADX OTel connector](https://learn.microsoft.com/en-us/azure/data-explorer/open-telemetry-connector) / [ADX exporter README](https://github.com/open-telemetry/opentelemetry-collector-contrib/blob/main/exporter/azuredataexplorerexporter/README.md) |
| **Power BI Activity Log (UserId + DatasetId)** | ✅ Session-level only | ✗ | [Feasible-now] for sessions; [Blocked-by-platform] for per-query | [MS Learn Activity Log](https://learn.microsoft.com/en-us/power-bi/guidance/admin-activity-log) |
| **OTel Profiles (4th signal, eBPF profiling)** | ✗ | ✗ (Linux eBPF, public alpha) | [Experimental] | [OTel Profiles alpha announcement](https://dev.to/x4nent/opentelemetry-profiles-public-alpha-ebpf-fourth-signal-collector-v01510-and-opamp-fleet-6g3) |
| **W3C TraceContext from Power BI service** | ✅ Would be exact if confirmed | ✗ | [Unverified] | No public documentation found |

---

## Top 5 Techniques to Break the Ceilings

### 1. XmlaRequestId → Fabric Workspace Monitoring Eventhouse Join [Feasible-now]
**Breaks: Ceiling 1 (identity) completely.**

The `RequestId` in gateway CSV logs is identical to `OperationId` (`XmlaRequestId`) in the Fabric Workspace Monitoring Eventhouse. A KQL join on this key yields `ExecutingUser` (UPN), `ItemId` (DatasetId), `ItemName`, `CpuTimeMs`, and DAX query text — the exact fields missing from the gateway CSV. This works today with zero changes to the gateway. The [crossjoin.co.uk blog](https://blog.crossjoin.co.uk/2024/09/01/finding-power-bi-semantic-model-refresh-operations-in-gateway-logs/) provides a working KQL template. Workspace Monitoring is in preview but generally available and shipping in Fabric, with 30-day retention and real-time streaming.

**Implementation:** Parse gateway `QueryStart.csv` → emit `RequestId` → KQL join against `PowerBIDatasetsWorkspace` in Eventhouse on `OperationId == RequestId`.

---

### 2. Gateway `EvaluationContext` Field Parsing [Feasible-now]
**Breaks: Ceiling 1 (identity) partially — `DatasetId` without requiring Workspace Monitoring.**

The `QueryStart` log already carries `EvaluationContext` which contains `datasetId` for Fabric Semantic Model, Dataflow Gen2, and Power Platform workloads. This field is parsed from the existing CSV logs — no new infrastructure required. It does not provide `UserId`, but combined with the Power BI Activity Log's session-level records, `DatasetId + time-window` narrows attribution significantly. For tenants who cannot enable Workspace Monitoring (cost, policy), this is the available-now fallback.

---

### 3. OTel .NET Auto-Instrumentation (CLR Profiler) on the Gateway Process [Feasible-with-effort]
**Breaks: Ceiling 2 (network latency) partially; Ceiling 1 potentially if Power BI propagates W3C TraceContext.**

By setting machine-level `COR_ENABLE_PROFILING` / `COR_PROFILER` / `COR_PROFILER_PATH_64` env vars (via the OTel PowerShell installer or registry write) and restarting the gateway Windows service, OTel .NET auto-instrumentation attaches to both `Microsoft.PowerBI.EnterpriseGateway.exe` and each `Mashup.Container.NetFX45.exe` child process. This emits spans for every outbound HTTP call and SQL/ADO.NET query — giving per-query latency to each datasource with destination host, HTTP status code, and DB statement, exported via OTLP to a local OTel Collector → Azure Monitor / Eventhouse. Risks are moderate (Microsoft support position, update regression) and manageable with `RuleEngine` safeguards and a pre-update testing gate.

**Documentation:** [opentelemetry.io/docs/zero-code/dotnet/](https://opentelemetry.io/docs/zero-code/dotnet/), [opentelemetry.io/docs/zero-code/dotnet/instrumentations/](https://opentelemetry.io/docs/zero-code/dotnet/instrumentations/), [CLR profiling setup](https://learn.microsoft.com/en-us/dotnet/framework/unmanaged-api/profiling/setting-up-a-profiling-environment)

---

### 4. ETW `Microsoft-Windows-TCPIP` + `Microsoft-Windows-Kernel-Network` Real-Time Consumer [Feasible-with-effort]
**Breaks: Ceiling 2 (network cost) completely.**

A lightweight ETW consumer process on the gateway host subscribes to `Microsoft-Windows-TCPIP` (for per-connection bytes and retransmits) and `Microsoft-Windows-Kernel-Network` (for connection lifecycle and per-process byte counters), filtered by the gateway PID and child Mashup.Container PIDs. The `TCB` handle provides stable connection-level correlation. Per-connection bytes (send + receive) are then time-window-correlated with gateway `QueryTrackingId` to attribute network cost to individual queries. Results are emitted as OTel metrics (custom `system.network.gateway.*` instruments) to the same OTel Collector.

The [ETWAnalyzer project](https://github.com/Siemens-Healthineers/ETWAnalyzer) (Siemens Healthineers, production use) demonstrates this is viable in a production enterprise context. The [TcpIp_SendIPV4 ETW event schema](https://learn.microsoft.com/en-us/windows/win32/ETW/tcpip-sendipv4) is stable and well-documented.

**Note:** This gives TCP-level bytes and retransmit-induced latency, not application-layer query payload sizes. Application-layer bytes require the OTel `HTTPCLIENT` + `ADONET` instrumentation from Technique 3.

---

### 5. OTel Collector Local Sidecar with Fabric Eventhouse / Azure Monitor OTLP Sink [Feasible-now → Feasible-with-effort]
**Enables: A unified, OTLP-native telemetry bus that future-proofs the tool and enables real-time streaming.**

The [Azure Monitor OTLP ingestion went GA on June 2, 2026](https://techcommunity.microsoft.com/blog/azureobservabilityblog/direct-opentelemetry-ingestion-into-azure-monitor-is-now-generally-available/4524044). The [OTel Collector Azure Data Explorer exporter](https://github.com/open-telemetry/opentelemetry-collector-contrib/blob/main/exporter/azuredataexplorerexporter/README.md) supports Fabric Eventhouse in beta. A local OTel Collector sidecar on the gateway host unifies: CLR profiler spans (Avenue A), ETW-derived network metrics (Avenue B), and gateway CSV logs (parsed via `filelog` receiver) into a single OTLP pipeline. This is the architectural "north star" — all data flows through one wire format, one schema (OTel semantic conventions), routable to any backend without code changes. The Fabric Eventstream Custom Endpoint can receive this data and land it in the same Eventhouse KQL database as Workspace Monitoring, enabling a single-pane-of-glass KQL join.

---

## What Remains [Blocked-by-platform] or [Unverified]

| Gap | Status | Notes |
|---|---|---|
| eBPF on Windows (Beyla, Pixie, etc.) | [Blocked-by-platform] | Linux-only; eBPF-for-Windows has no prod-signed binaries as of mid-2026 |
| W3C TraceContext from Power BI service → gateway | [Unverified] | Would enable exact distributed trace if confirmed; needs empirical test |
| `UserId` in per-DirectQuery gateway log | [Blocked-by-platform] | Power BI service does not pass user identity to gateway in current architecture; available only via Workspace Monitoring join |
| Push/webhook from gateway (no polling) | [Blocked-by-platform] | No native push path; filelog tail + OTel Collector is the workaround |
| OTel Profiles (4th signal) on Windows | [Experimental] | Public alpha as of March 2026; Linux eBPF-based; Windows support [Unverified] |
| Power BI service propagating `x-ms-client-request-id` to gateway | [Unverified] | Presence in WCF headers to gateway needs empirical verification |

---

*Research compiled June 30 – July 1, 2026. All URLs verified as live at time of research.*
