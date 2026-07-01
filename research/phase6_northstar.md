# Phase 6 — North Star: Making the Gateway Monitor Truly State-of-the-Art

**What this is:** a forward-looking evolution plan that takes the v1–v3 tool (collectors → Delta medallion → report + Activator) and pushes it to the frontier of what is *actually achievable in Microsoft Fabric / Windows in 2026* — not speculation. Every capability carries a feasibility grade and a primary source.

**Feasibility key:** `[Feasible-now]` · `[Feasible-with-effort]` · `[Experimental]` · `[Blocked-by-platform]` · `[Unverified]`

Full evidence: [`frontier_instrumentation.md`](./frontier_instrumentation.md) (OTel/ETW/tracing) and [`frontier_intelligence.md`](./frontier_intelligence.md) (AIOps).

---

## 0. The headline: I was wrong about a "permanent" ceiling — and that's good news

Across this project I repeatedly labeled **query→identity attribution as "inherently fuzzy"** — a permanent design limit of gateway logs. **Frontier research refutes that.** It is breakable *today*, with no new infrastructure:

- The gateway `QueryStart` log's **`RequestId` is byte-identical to `XmlaRequestId` / `OperationId`** in Fabric **Workspace Monitoring**'s Eventhouse table `PowerBIDatasetsWorkspace`. A KQL join on that key returns **`ExecutingUser` (UPN), `ItemId` (DatasetId), DAX text, and AS-engine CPU/duration** — the exact fields the CSV lacks ([MS Fabric semantic model operations](https://learn.microsoft.com/en-us/fabric/enterprise/powerbi/semantic-model-operations); [Chris Webb / Fabric CAT](https://blog.crossjoin.co.uk/2024/09/01/finding-power-bi-semantic-model-refresh-operations-in-gateway-logs/)). `[Feasible-now]`
- Even without Workspace Monitoring, the gateway's **`EvaluationContext` field already carries `datasetId`** for Fabric Semantic Model / Dataflow Gen2 / Power Platform workloads ([MS Learn gateway perf](https://learn.microsoft.com/en-us/data-integration/gateway/service-gateway-performance)). `[Feasible-now]`

This flips differentiator #3 from "best-effort/fuzzy" to **near-exact for the common case** (Fabric semantic models). The honest residual: `UserId` per *DirectQuery* execution and Dataflow Gen1 / Paginated Reports remain `[Blocked-by-platform]`. But the "which dataset / which user triggered this" question — the #3 pain point — is now answerable. **This single finding is the most important result of the entire project.**

---

## 1. Two ceilings, reassessed

| Ceiling | Old verdict (mine) | Frontier verdict | How |
|---|---|---|---|
| **Query→identity** | "inherently fuzzy" | **Breakable now** | `RequestId`↔`XmlaRequestId` join to Workspace Monitoring; `EvaluationContext` datasetId |
| **Per-query network cost** | "host-level only" | **Breakable with effort** | ETW `Microsoft-Windows-TCPIP`/`Kernel-Network` per-PID bytes+RTT, time-correlated to `QueryTrackingId` |

Neither requires modifying Microsoft's gateway. Both are grounded in primary docs.

---

## 2. The architecture leap: OTLP-native telemetry bus

Today's tool parses CSV. The state-of-the-art version treats the gateway host as a **fully instrumented node emitting OpenTelemetry** — metrics, traces, logs (and eventually profiles) — through one wire protocol to any backend. OTel reached [CNCF Graduation (May 2026)](https://byteiota.com/opentelemetry-cncf-graduation-developer-guide/); Azure Monitor OTLP ingestion is [GA (June 2026)](https://techcommunity.microsoft.com/blog/azureobservabilityblog/direct-opentelemetry-ingestion-into-azure-monitor-is-now-generally-available/4524044).

```
Gateway Host (Windows)
  Microsoft.PowerBI.EnterpriseGateway.exe ─┐  CLR profiler (OTel .NET auto-instr.)
  Mashup.Container.NetFX45.exe (×N)        ─┤  → per-query HTTP/SQL spans
  ETW consumer (TCPIP + Kernel-Network)    ─┤  → per-PID bytes/RTT metrics
  CSV filelog receiver (existing parser)   ─┘  → log records
                    │  OTLP
                    ▼
          OTel Collector (local sidecar)
                    │  fan-out (add a backend = add an exporter block, zero code)
        ┌───────────┼───────────────────────────┐
        ▼           ▼                           ▼
  Azure Monitor   Fabric Eventhouse (ADX exp.)  Prometheus/Grafana
                    │  KQL join on OperationId==RequestId
                    ▼
        Fabric Workspace Monitoring  →  identity (ExecutingUser, DatasetId, DAX)
```
Sources: [OTel .NET zero-code](https://opentelemetry.io/docs/zero-code/dotnet/), [ADX/Eventhouse OTel exporter](https://github.com/open-telemetry/opentelemetry-collector-contrib/blob/main/exporter/azuredataexplorerexporter/README.md), [ETW TcpIp_SendIPV4](https://learn.microsoft.com/en-us/windows/win32/ETW/tcpip-sendipv4).

**Why it matters:** bespoke CSV parsing is a dead-end that breaks on every gateway upgrade (pain #4). An OTLP-native design future-proofs the tool, interoperates with the entire observability ecosystem, and makes the schema-adaptive parser a *fallback*, not the core.

---

## 3. The four intelligence layers (passive → predictive → self-explaining → self-healing)

The signals are only half the tool. The frontier is what you *do* with them. All four layers are Fabric-native.

### Layer 1 — Detection (replace static thresholds)
- **`series_decompose_anomalies()`** on CPU/spool/duration — seasonality-aware, scores outliers, processes thousands of series in seconds. Drop-in for Eventhouse. `[Feasible-now]` ([KQL anomaly detection](https://learn.microsoft.com/en-us/kusto/query/anomaly-detection?view=microsoft-fabric))
- **`diffpatterns` / `autocluster`** — during a failure window, auto-surface "78% of errors = GW02 + OracleDB + MashupCrash vs 12% baseline." Automated top-offender attribution, no ML training. `[Feasible-now]` ([anomaly diagnosis](https://learn.microsoft.com/en-us/kusto/query/anomaly-diagnosis?view=microsoft-fabric))

### Layer 2 — Prediction (from "it broke" to "it will break")
- **`series_decompose_forecast()`** — "spool disk exhausts in ~2h at current trajectory" → Activator fires *before* the outage. `[Feasible-now]`
- **SynapseML Isolation Forest** — unsupervised multivariate anomaly across CPU+spool+duration+RTT+memory jointly; works day one. `[Feasible-now]` batch ([Isolation Forest MVAD](https://learn.microsoft.com/en-us/fabric/data-science/isolation-forest-multivariate-anomaly-detection))
- **LightGBM refresh-failure classifier** — P(failure|features) heatmap in the fleet view; needs 4–8 weeks labeled history. `[Feasible-with-effort]` ([predictive maintenance](https://learn.microsoft.com/en-us/fabric/data-science/predictive-maintenance))
- **GAT multivariate model via KQL Python plugin** — real-time inference inside Eventhouse. `[Feasible-with-effort]` ([MVAD tutorial](https://learn.microsoft.com/en-us/fabric/real-time-intelligence/multivariate-anomaly-detection))

### Layer 3 — Explanation (the "Jarvis" layer)
- **Fabric Data Agent (NL2KQL)** — GA. Operator asks "why did the Sales refresh fail at 3 AM?" → agent correlates gateway errors + network + Event Log + refresh history across ≤5 sources and answers in plain English with citations. `[Feasible-now]` ([Data Agent](https://learn.microsoft.com/en-us/fabric/data-science/concept-data-agent))
- **LLM alert enrichment** — SynapseML `OpenAIChatCompletion`/AI Functions turn a raw Activator alert into `{likely_cause, confidence, recommended_actions, runbook_url}`, labeled AI-hypothesis-not-confirmed with evidence citations. `[Feasible-with-effort]` ([AI Functions](https://learn.microsoft.com/en-us/fabric/data-science/ai-services/how-to-use-openai-ai-functions))
- **Eventhouse Remote MCP server** — expose the telemetry as an MCP tool any AI agent (Copilot Studio, AI Foundry) can reason over. `[Feasible-now]` read-only ([MCP Eventhouse](https://learn.microsoft.com/en-us/fabric/real-time-intelligence/mcp-remote-eventhouse))

### Layer 4 — Self-healing (closed loop, with guardrails)
- **Activator → User Data Function → REST** — spool threshold → UDF restarts gateway / clears spool / re-validates creds → writes audit → posts Teams Adaptive Card. `[Feasible-now]` ([Activator+UDF tutorial](https://learn.microsoft.com/en-us/fabric/real-time-hub/business-events/tutorial-business-events-event-stream-user-data-function-activator))
- **Guardrails (the responsible-Jarvis bar):** Tier 1 autonomous (notify, read-only health check); Tier 2 human-in-loop (restart, spool cleanup — Adaptive Card approve/deny); Tier 3 recommend-only (scale-out, migration). **Circuit breaker:** >3 same-remediations/30 min → suppress + escalate. Activator's stateful transitions + impact-preview prevent remediation storms.

---

## 4. Consolidated "state-of-the-art" capability map

| Capability | Layer | Feasibility | Breaks a ceiling / pain |
|---|---|---|---|
| RequestId↔XmlaRequestId identity join | Ingest | `[Feasible-now]` | **Ceiling 1 (identity), pain #3** |
| EvaluationContext datasetId parse | Ingest | `[Feasible-now]` | Ceiling 1 partial, pain #3 |
| ETW per-PID network bytes+RTT | Ingest | `[Feasible-with-effort]` | **Ceiling 2 (network), pain #7** |
| OTel .NET CLR profiler spans | Ingest | `[Feasible-with-effort]` | per-query latency; identity if traceparent `[Unverified]` |
| OTLP Collector → Eventhouse/Azure Monitor | Bus | `[Feasible-now→effort]` | future-proofs, kills pain #4 fragility |
| series_decompose_anomalies | Detect | `[Feasible-now]` | proactive vs pain #1/#5 |
| diffpatterns/autocluster | Detect | `[Feasible-now]` | auto-attribution, pain #2 |
| series_decompose_forecast | Predict | `[Feasible-now]` | pre-failure alerting, pain #9 |
| Isolation Forest MVAD | Predict | `[Feasible-now]` batch | pain #5/#6 |
| LightGBM failure classifier | Predict | `[Feasible-with-effort]` | pain #2 |
| Fabric Data Agent NL2KQL | Explain | `[Feasible-now]` | pain #2/#8 (usability) |
| LLM alert enrichment | Explain | `[Feasible-with-effort]` | pain #2 |
| Activator→UDF self-healing | Heal | `[Feasible-now]` | pain #1/#9/#10 |
| eBPF on Windows | — | `[Blocked-by-platform]` | (use ETW instead) |
| W3C traceparent PBI→gateway | — | `[Unverified]` | would make identity exact end-to-end |
| Per-OPDG CU attribution | Cost | `[Unverified]` | proxy via query-volume↔CU-timepoint join |

---

## 5. Evolution roadmap (v1 shipped-shape → v6 frontier)

- **v1 (built):** collectors + schema-adaptive medallion + report + basic Activator offline alert.
- **v2:** ETW network collector (Ceiling 2) + `series_decompose_anomalies`/`forecast` + `diffpatterns` triage. *Mostly `[Feasible-now]`.*
- **v3:** **Identity join to Workspace Monitoring** (Ceiling 1) + Fabric Data Agent NL ops + Activator→UDF Tier-2 self-healing with approval cards.
- **v4:** OTLP-native bus (OTel Collector sidecar; CSV becomes fallback) + Isolation Forest MVAD + LLM alert enrichment.
- **v5:** LightGBM/GAT predictive failure + query fingerprinting + capacity/CU advisory.
- **v6 (frontier):** OTel .NET CLR profiler for true per-query distributed spans; Granger-causal RCA; MCP-exposed agentic ops. Carries the `[Experimental]`/`[Unverified]` items — gated on pilot validation.

Sequencing rule: **highest pain ÷ effort first.** v2–v3 are almost entirely `[Feasible-now]` and deliver the two ceiling breaks + the intelligence that operators have begged for. v4+ is where it becomes genuinely novel vs. anything shipping today.

---

## 6. What makes this state-of-the-art (and what would be hype)

**Genuinely SOTA, defensible:**
- First gateway monitor to **break identity attribution** (RequestId join) and **per-query network cost** (ETW) — both confirmed-missing in all 10 existing tools.
- First to be **OTLP-native** rather than CSV-bound — interoperates with the whole observability ecosystem and survives gateway upgrades.
- First with **predictive** (forecast saturation) + **self-explaining** (Data Agent/LLM RCA) + **self-healing** (Activator→UDF with guardrails) layers, all Fabric-native.

**What would be hype — explicitly NOT claimed:**
- Not "fully autonomous ops." Human-in-loop for anything destructive; circuit breakers mandatory.
- Not eBPF on Windows (blocked) — ETW is the honest equivalent.
- Not exact per-DirectQuery user attribution (blocked) — dataset+session, not per-visual user.
- Not per-OPDG CU billing (unconfirmed) — proxy estimate only, labeled.
- LLM RCA is a *hypothesis with citations*, never an autonomous decision-maker.

---

## 7. Immediate next 3 actions (when a Fabric env is available)

1. **Prove the identity join** — enable Workspace Monitoring on a test workspace, run the `OperationId==RequestId` KQL join, confirm `ExecutingUser`/`ItemId` populate. This validates the project's biggest claim (Phase 5 item, elevated).
2. **Stand up `series_decompose_anomalies` + `forecast`** on real gateway telemetry — the cheapest, highest-visible intelligence win; converts static thresholds to predictive alerts in ~2 days.
3. **Wire one Activator→UDF Tier-2 loop** (offline → approval card → restart → audit) with the circuit breaker — proves the self-healing pattern end-to-end before scaling it.

---

*Phase 6 north-star complete. The two "permanent" ceilings are not permanent. Every frontier capability is grounded in a primary source and honestly graded; the sci-fi is labeled as such. This is the plan for a tool built for the future, not a copy of what already exists.*
