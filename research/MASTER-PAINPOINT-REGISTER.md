# Master Pain-Point Register

**One source of truth.** This register merges and de-duplicates two independent research tracks:

- **Track G — Gateway-operator lens** (this repo's primary scope): `research/phase3_painpoints.md` — top 10 On-Premises Data Gateway operator pain points, two-pass evidence pipeline, scored and classified PRODUCT GAP vs DISCOVERY DEFICIT.
- **Track B — Broad Power BI lens** (developer/end-user): a separate online sweep of Reddit / G2 / Capterra / practitioner sources capturing the loudest *report-developer* pains (DAX, perf, version control, lineage, cost).

**Why merge them:** the two tracks ask different questions (operator vs. developer), but where they *overlap* is the strongest possible market signal — a pain felt from both ends of the estate. Those overlaps (§3) are the strategic validation for this repo's build direction.

**Scope decision (2026-07-02):** the build target is the **gateway-operator track**. Track B items are retained here only to (a) prove the overlap and (b) mark explicitly-out-of-scope developer pains so we never scope-creep into them. See `DECISIONS.md`.

**Honesty labels** (repo convention): 🔴 PRODUCT GAP (no tool solves it) · 🟡 DISCOVERY DEFICIT (a path exists, no tool surfaces it) · `[Desk-Verified]` · `[Built-Unverified]` · `[Unverified]`.

---

## 1. Track G — Gateway-operator pain points (BUILD TARGET)

Source: `research/phase3_painpoints.md`. Coverage status pulled from `PAIN-POINT-COVERAGE.md` (as of live-tenant session 2026-07-01).

| # | Pain point | Score | Class | This repo's answer | Build status |
|---|---|---|---|---|---|
| G1 | **No real-time gateway-health alerting** — outages discovered only when users complain ([Fabric Community 2018→2025](https://community.fabric.microsoft.com/t5/Service/Gateway-status-emails-or-alerts/m-p/344928)) | 5/5 | 🔴 | `starter/alerting/activator-rules.json` (gateway-offline rule) | 🟠 Built / Unverified (Activator DSL `[Unverified]`) |
| G2 | **Opaque refresh failures** — can't separate gateway vs source vs network ([MS Known Issue #844](https://github.com/MicrosoftDocs/fabric-docs/blob/main/docs/known-issues/known-issue-844-intermittent-refresh-failure-gateway.md)) | 5/5 | 🔴 | `starter/kql/03_diffpatterns_triage.kql` + silver 3-way join (gateway + eventlog + refresh) | 🟠 Built / Unverified — needs real refresh-history + event-log data |
| G3 | **Zero query attribution** — gateway logs carry only `RequestId`, no DatasetId/ReportId/UserId ([3Cloud 2023](https://3cloudsolutions.com/resources/monitoring-power-bi-on-premises-data-gateway-performance/)) | 5/5 | 🔴 | **FLAGSHIP:** `starter/kql/01_identity_join.kql` (`RequestId == XmlaRequestId`) → `add_artifact_identity` | Join key `[Desk-Verified]`; live proof 🟠 Built / Unverified — **blocked** by trial Spark throttle (needs F2+) |
| G4 | **Gateway Performance PBIT breaks on upgrade** — `DataFormat.Error: more columns than expected` on schema drift ([Fabric Community](https://community.fabric.microsoft.com/t5/Desktop/Gateway-Performance-Monitoring-PBIT-Dataformat-error/td-p/1386477)) | 4/5 | 🔴 | Schema-adaptive parser `gateway_bronze_lib.read_gateway_csv` (name-based, PERMISSIVE, mergeSchema) | ⚠️ **Regression (live tenant):** Fabric Load-to-Tables rejects `(ms)`/`(bytes)` headers → fix: route via notebook path. Parser itself 🟢 proven on local Spark |
| G5 | **Mashup engine memory/CPU bloat** — `Mashup.Container.NetFX45.exe` eats RAM, no per-process view ([Fabric Community](https://community.fabric.microsoft.com/t5/Service/On-Prem-gatewy-Mashup-Container-NetFX45-exe-killing-server-memory/td-p/2400000)) | 4/5 | 🔴 | `Collect-MashupProcesses.ps1` + `read_mashup_processes` + `gold_mashup_health` | 🟢 **Proven (local Spark)** — runaway containers flagged; real process names need host confirmation |
| G6 | **No multi-gateway / fleet view** — 5–50+ gateways managed blind ([3Cloud](https://3cloudsolutions.com/resources/monitoring-power-bi-on-premises-data-gateway-performance/)) | 4/5 | 🟡/🔴 | `03_gold_aggregate.py` fleet rollup + load-skew CV | 🟢 **Proven (local Spark)** — real multi-node CV needs tenant |
| G7 | **Network / bandwidth blindspot** — MS docs confirm gateway diagnostics exclude network ([MS Learn](https://learn.microsoft.com/en-us/data-integration/gateway/service-gateway-performance)) | 4/5 | 🔴 | `Collect-NetworkMetrics.ps1` (NIC + latency); per-query ETW = v2 roadmap | 🟠 Built / Unverified — host-level only, never run on real host |
| G8 | **Manual, brittle setup** — hidden AppData paths, XML config edits, service-account path drift ([MS Learn](https://learn.microsoft.com/en-us/data-integration/gateway/service-gateway-performance)) | 3/5 | 🟡 | `Deploy_GatewayMonitor.ipynb` + `Teardown_*` + config model | 🟠 Built / Unverified — partial improvement, not a clean fix |
| G9 | **Disk spooler surprises** — spool dir fills disk, no proactive alert ([MS Learn](https://learn.microsoft.com/en-us/data-integration/gateway/service-gateway-performance)) | 3/5 | 🔴 | `Collect-DiskSpool.ps1` + `02_anomaly_forecast.kql` + Activator disk rule | 🟡 Built / Local-tested — proactive forecast unproven in tenant |
| G10 | **Credential / datasource state drift** — "green tick" tests at save time, not refresh time ([Fabric Community](https://community.fabric.microsoft.com/t5/Service/Credential-Missing/m-p/1289813)) | 3/5 | 🟡 | `Get-GatewayInventory.ps1` datasource-status + Activator credential rule | 🟠 Built / Unverified — uses REST path no tool uses; cmdlet name `[Unverified]` |

**Top-5 unmet needs** (PRODUCT GAP × whitespace, from `phase3_painpoints.md`): G1 alerting · G2 failure triage · G3 query attribution · G7 network metrics · G4 upgrade-resilient parser.

---

## 2. Track B — Broad Power BI pain points (context / out-of-scope guardrail)

Source: online sweep (Reddit r/PowerBI, [G2](https://www.g2.com/products/microsoft-microsoft-power-bi/reviews), [Capterra](https://www.capterra.com/p/176586/Power-BI/reviews/), practitioner blogs). Tagged: **[Addressable]** by an external tool · **[Platform-locked]** (only Microsoft can fix) · **[Partial]**. The "Scope" column records why each is *not* a build target here.

| # | Pain point | Fixable? | Scope vs this repo |
|---|---|---|---|
| B1 | **DAX debugging & "wrong totals"** (filter context) ([r/PowerBI](https://www.reddit.com/r/PowerBI/comments/1kl0dy0/what_frustrates_you_about_power_bi/)) | [Partial] | Out — developer/authoring, not gateway ops |
| B2 | **Slow performance / refresh at scale** ([G2](https://www.g2.com/products/microsoft-microsoft-power-bi/reviews)) | [Addressable] | **Partial overlap → see §3 (O1)** |
| B3 | **No native version control / weak DevOps** ([Digital by Default](https://digitalbydefault.ai/blog/power-bi-microsoft-analytics-review-2026)) | [Addressable] | Out — PBIP/TMDL dev workflow |
| B4 | **Model hygiene / cleanup** (unused columns/measures) | [Addressable] | Out — Measure Killer owns it |
| B5 | **Lineage: report → source hard to trace** ([MS lineage docs](https://learn.microsoft.com/en-us/power-bi/collaborate-share/service-data-lineage)) | [Addressable] | **Partial overlap → see §3 (O2)** |
| B6 | **Licensing / capacity cost confusion** ([Graphed](https://www.graphed.com/blog/why-is-power-bi-so-bad)) | [Partial] | Out — FinOps, different tool |
| B7 | **Refresh failures hard to diagnose** ([Wicked Smart Data](https://www.wickedsmartdata.com/articles/power-bi-gateway-complete-guide-to-connecting-on-premises-data-to-the-cloud)) | [Addressable] | **Direct overlap → see §3 (O1)** |
| B8 | **Cluttered UI / no shortcuts / month sort** ([YouTube](https://www.youtube.com/watch?v=r4sUOj-GRHI)) | [Platform-locked] | Out — Microsoft-only |
| B9 | **Windows-only Desktop / no co-authoring** ([Workflow Automation](https://workflowautomation.net/reviews/power-bi)) | [Platform-locked] | Out — Microsoft-only |
| B10 | **Export matrix to Excel with formatting** ([r/PowerBI](https://www.reddit.com/r/PowerBI/comments/i84tyj/)) | [Platform-locked] | Out — Microsoft-only |

---

## 3. The two overlap themes — and how they validate this build

Only two themes appear in **both** the operator track and the developer track. A pain that surfaces independently from opposite ends of the estate is the strongest signal that it is real, structural, and under-served — exactly where a new tool should aim.

### Overlap O1 — Refresh-failure diagnosis is undiagnosable  →  validates G2 (triage) + G3 (attribution)
- **Operator side (G2/G3):** the gateway operator can't tell *why* a refresh failed — gateway vs source vs network — and can't even name *which dataset/user* a slow query belongs to, because the gateway log carries only `RequestId`. Confirmed as a MS [Known Issue #844](https://github.com/MicrosoftDocs/fabric-docs/blob/main/docs/known-issues/known-issue-844-intermittent-refresh-failure-gateway.md) with "no workarounds provided."
- **Developer side (B7/B2):** the report developer independently reports the *same event* as "my refresh is slow/failing and I have no idea why" — one of the loudest broad-Power BI complaints on G2/Reddit.
- **Why this validates the build:** two personas describe **one underlying failure** from opposite ends and *neither* can currently connect the dots. This repo's flagship — the `RequestId == XmlaRequestId` identity join (`starter/kql/01_identity_join.kql`, [`docs/TECHNIQUE-query-identity-attribution.md`](docs/TECHNIQUE-query-identity-attribution.md)) — is precisely the bridge: it stitches the operator's gateway log to the developer's dataset/user/DAX. Solving G3 is what makes G2 tractable, and it simultaneously answers B7 for the developer. **This is the single highest-leverage capability in the repo, and the overlap is the proof.** Join key is `[Desk-Verified]` in [MS semantic model operations docs](https://learn.microsoft.com/en-us/fabric/enterprise/powerbi/semantic-model-operations); live match-rate proof is the open pilot question.

### Overlap O2 — Lineage / attribution gap  →  validates G3 (query→identity)
- **Operator side (G3):** "no information is available regarding the report name or dataset name" for a gateway query ([3Cloud](https://3cloudsolutions.com/resources/monitoring-power-bi-on-premises-data-gateway-performance/)) — the operator has a query with no owner.
- **Developer side (B5):** "tracing lineage from a report back to the original SQL is hard" — the developer has an asset with no clear downstream/upstream map ([MS lineage docs](https://learn.microsoft.com/en-us/power-bi/collaborate-share/service-data-lineage)).
- **Why this validates the build:** both are the *same missing edge* — the link between a running query/artifact and its identity/owner — viewed from different altitudes. The identity join that solves G3 produces exactly this edge at the gateway layer. It won't build a full semantic-lineage graph (that's the out-of-scope `pbi-observatory` track and Measure Killer's turf), but it delivers the operator-relevant slice: **query → dataset → workspace → user**. The developer-side demand (B5) confirms the edge is valuable well beyond gateway ops, which de-risks adoption.

### What the overlap does NOT justify
The other 8 broad pains (B1, B3, B4, B6, B8–B10) have **no operator-side counterpart** — they're authoring, licensing, or Microsoft-platform-locked issues. They are deliberately **out of scope** for this repo (see `DECISIONS.md` non-goals). Recording them here is the guardrail against scope creep, not a backlog.

---

## 4. Register maintenance
- **Authoritative detail** for Track G lives in `research/phase3_painpoints.md`; **build/proof status** lives in `PAIN-POINT-COVERAGE.md`; this register is the merged index that links them and holds the cross-track overlap analysis.
- When a pilot upgrades a coverage status (🟠→🟢), update `PAIN-POINT-COVERAGE.md` first, then reflect the status column here.
- Track B is context only — do not add build tasks for B-items without first proving an operator-side overlap and recording it in §3.
