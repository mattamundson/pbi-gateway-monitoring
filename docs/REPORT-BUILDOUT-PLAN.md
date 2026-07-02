# Report Buildout Plan — v2 (branded, expanded, RuiRomano + fuam-basic informed)

**Goal.** Take the current 4-page report from "standard" to a substantially more advanced,
higher-value, *own-branded* Power BI report — more pages, historical/time-series depth, and
tenant breadth — building on the two reference solutions Amo cited. Deliver as source-controlled
**PBIR** now; produce **.pbix** via the one Desktop step described below.

**Benchmarks (both MIT-licensed — cleared to build on; attributions in `NOTICE`):**
- [RuiRomano/pbigtwmonitor](https://github.com/RuiRomano/pbigtwmonitor) — 7 operator pages: Logs,
  Queries, Gateway Profile, Counters, Requests, Mashups Profiles, Mashups Logs; historical via
  `NumberDays`/`SinceDate`. **Our direct parity target.**
- [GT-Analytics/fuam-basic](https://github.com/GT-Analytics/fuam-basic) — a holistic overview
  report, DirectLake over Delta, notebook one-click deploy. **Our polish/landing/deploy target**
  (its Fabric-admin *domains* are Track B — we borrow the pattern, selectively, per below).

**Scope of "previous data" (Amo, 2026-07-02): BOTH — gateway depth + tenant breadth.**
- *Gateway depth*: historical gateway logs / counters / queries / requests / mashups over time
  (what RuiRomano trends). Needs a `dim_date` and a multi-period load.
- *Tenant breadth*: fuam-style capacity / activity / inventory / refreshables. **Admin-API-gated.**

---

## 1. Target page inventory (v2)

Legend — **Status**: `authored` (in this PR, `[Unverified]`) · `specced` (below, author next) ·
`gated` (needs tenant/admin-API data before it's meaningful).

| # | Page | Purpose | Key visuals | Measures | Data source | G-pain / parity | Status |
|---|---|---|---|---|---|---|---|
| 1 | **Executive Overview** | single-glance estate health (fuam landing) | 5 KPI cards + 2 trend lines | Fleet Health Score, Gateways Online, Open Failures (24h), Attribution Rate %, Capacity Headroom % | gold_gateway_health, silver_triage, gold_cluster_load | fuam overview | **authored** |
| 2 | Fleet Overview | node health now | existing 17 visuals | (existing) | gold_gateway_health | G6 | existing (rebrand) |
| 3 | Query & Identity | query -> user/dataset | existing 14 | (existing) + Attribution Rate % | silver_identity_attribution | **G3 flagship** | existing (rebrand) |
| 4 | Failures & Triage | why a refresh failed | existing 13 | (existing) + Refresh Success Rate % | silver_triage | G2 | existing (rebrand) |
| 5 | Capacity & Spool | capacity + disk | existing 18 | (existing) | gold_cluster_load | G9 | existing (rebrand) |
| 6 | **Historical Trends** | "previous data" — health/error/volume over time | line + area + MoM cards | Error Rate % (7d avg), Query Volume MoM %, Avg CPU % (rolling 7d), Health Trend Direction | facts x `dim_date` | G2/G6, RuiRomano trending | specced |
| 7 | **Mashups Profiles** | per-process RAM/CPU | matrix + bar + runaway flag | Mashup RAM Peak, Mashup CPU %, Runaway Container Count | gold_mashup_health | **G5 (proven-local)**, RuiRomano | specced |
| 8 | **Mashups Logs** | mashup op detail | filtered table + trend | (log rollups) | bronze/silver mashup | RuiRomano | specced |
| 9 | **Logs Explorer** | raw error/info, filterable | slicers + table + error trend | Error Rate % | bronze/silver event log | G2, RuiRomano Logs | specced |
| 10 | **Requests** | request throughput/duration | histogram + throughput line | (request rollups) | silver | RuiRomano Requests | specced |
| 11 | **Counters Deep-Dive** | CPU/mem/handles over time | small-multiples lines | Avg CPU % (rolling 7d) | gold_gateway_health x dim_date | RuiRomano Counters | specced |
| 12 | **Gateway Profile** | inventory/config/version/cred-drift | table + cards + SCD history | (dim rollups) | gold_dim_gateway | G10, RuiRomano Profile | specced |
| 13 | **Alerting State** | which Activator rules armed/firing | rule status + heartbeat-age | Heartbeat Age | activator-rules + gold_gateway_health | G1 | specced (live firing gated) |
| 14 | **Tenant Breadth** | capacity/activity/refreshables | fuam-style | Active Users (30d), Dataset Refresh Count, Capacity CU % | bronze_activity/capacity | fuam | **gated (admin-API)** |

Unique advantage to preserve: pages 3 + 4 (identity attribution + triage) — RuiRomano has no
equivalent. That is the repo's differentiator; the buildout enhances, never dilutes it.

## 2. Historical data model (required for pages 6/11 and all trending)

Trending needs a **`dim_date`** dimension related to the fact tables' event-time columns
(`event_time`, `heartbeat_time`, query start). Add `dim_date` to the semantic model (a standard
calculated/date table), mark as date table, relate 1:* to each fact. Until it exists, the
time-intelligence measures in `measures_v2.dax` return blank — they are authored, not yet wired.
This is a Desktop/TMDL step (modeling), not a report step.

## 3. Branding

`starter/report/theme/gwmon-brand.theme.json` — a full Power BI theme (Nexus teal `#01696F`
primary, structured `dataColors`, card/kpi/line styles, page background, text classes). Apply in
Desktop: *View > Themes > Browse for themes*. Our own layout language: KPI-card header row,
6px-radius bordered cards, left-aligned Segoe UI Semibold titles.

## 4. Delivery formats — what ships where

| Artifact | Format | Produced by | In this PR |
|---|---|---|---|
| Report definition | **PBIR JSON** (`*.report.json`, hand-authored convention) | headless (me) | theme + Exec Overview page + plan; pages 6-14 specced |
| Brand theme | theme JSON | headless (me) | yes |
| Measures | DAX | headless (me) | `measures_v2.dax` |
| Semantic model | TMDL + `dim_date` | Desktop modeling | no (specced) |
| **`.pbix`** | compiled binary | **Power BI Desktop only** | no — see §5 |

## 5. CAN / CANNOT — honest feasibility + headwinds

**CAN do headlessly (shipped or specced here):**
- Author/expand **PBIR** pages, a **brand theme**, and **DAX measures** as source-controlled text.
- Build on RuiRomano + fuam feature sets — both **MIT**, so adaptation into this (proprietary) repo
  is license-clean provided we retain their MIT notices (added to `NOTICE`).

**CANNOT do headlessly — and exactly why:**
1. **`.pbix` (compiled binary).** A `.pbix` embeds a *compiled Analysis Services tabular model*
   that only Power BI Desktop's engine serializes. Desktop **is installed** here
   (`C:\Program Files\Microsoft Power BI Desktop\bin\PBIDesktop.exe`) but is **GUI-only** — no
   headless/CLI compile — and `pbi-tools` (which can compile PBIP->PBIX against the installed
   engine) is **not installed**. **Bottleneck resolution:** open the PBIP in Desktop and
   *Save As .pbix* (~2 min, you), **or** authorize me to install + attempt `pbi-tools`
   (works against the local engine, but its PBIX compile lags the newest PBIR schema — medium risk).
2. **Visual render + DAX correctness** cannot be verified without opening Desktop. Everything here
   is therefore `[Unverified]` (the repo's existing convention) until one Desktop pass.
3. **The data behind the pages.** *Gateway depth* trending needs the collectors run on a real host
   + the F2 medallion load (a `dim_date` + multi-period fact data). *Tenant breadth* (page 14) needs
   the **admin-API grant** — the same Amo-gated SPN that blocks G3's live proof. Pages are authorable
   now; their data is gated.

**Net:** the report *source* (PBIR + theme + model + measures) is fully buildable headlessly and is
being built. The `.pbix`, the visual proof, and the real data are the three headwinds — all
downstream of one Power BI Desktop session and the F2/admin-API tenant grant.

## 6. What shipped in this PR
- `docs/REPORT-BUILDOUT-PLAN.md` (this plan).
- `starter/report/theme/gwmon-brand.theme.json` (brand theme).
- `starter/semantic-model/measures_v2.dax` (exec/historical/mashup/tenant measures, `[Unverified]`).
- `starter/report/pages_v2/executive_overview.page.json` (first new page, 15 visuals, PBIR).
- `NOTICE` (MIT attributions to RuiRomano + GT-Analytics).

## 7. Next steps (pick any)
1. **Author pages 6-13** as PBIR (headless, `[Unverified]`) — the reports loop (`pbi-gateway-reports`)
   tracks this backlog; or I do a focused pass now.
2. **`.pbix`:** you Save-As in Desktop, or authorize the `pbi-tools` install attempt.
3. **`dim_date` + model** finalize in Desktop (unblocks all trending).
4. **Tenant breadth (page 14):** unblock with the admin-API grant.
5. **Proof:** the F2 pilot validates bindings + real data — flips the whole report `[Unverified] -> [Proven]`.
