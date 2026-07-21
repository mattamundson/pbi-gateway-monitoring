# Report Buildout Plan — v2 (branded, expanded, RuiRomano + fuam-basic informed)

> **Status note (2026-07-21 reconciliation):** this plan and the `gwmon` PBIP report it
> describes predate the tenant-breadth pipeline (`00_tenant_extract.py` /
> `01a_tenant_silver_gold.py` / `04_capacity_bridge.py`) and the `Nexus_Gateway_Observatory`
> report that superseded it on `main` as the current report artifact — Nexus has its own
> Tenant Overview / Timeline / Refresh Analytics pages this plan doesn't cover, built against a
> wider table set. This doc is kept as **reference source material**: `gwmon` fully authored six
> RuiRomano-parity pages Nexus still lacks (Logs Explorer, Mashups Logs, Requests, Counters
> Deep-Dive, Gateway Profile, Alerting State — see the table below), which is real, reusable
> design/measure work for whoever ports those pages into Nexus next (see `docs/MVP-ROADMAP.md`
> M7). Treat everything below as *the `gwmon` report's own plan*, not the current state of the
> report on `main`.

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
| 6 | **Historical Trends** | "previous data" — health/error/volume over time | line + area + MoM cards | Error Rate % (7d avg), Query Volume MoM %, Avg CPU % (rolling 7d), Health Trend Direction | facts x `dim_date` | G2/G6, RuiRomano trending | **authored** |
| 7 | **Mashups Profiles** | per-process RAM/CPU | matrix + bar + runaway flag | Mashup RAM Peak, Mashup CPU %, Runaway Container Count | gold_mashup_health | **G5 (proven-local)**, RuiRomano | **authored** |
| 8 | **Mashups Logs** | mashup op detail | filtered table + trend | (log rollups) | bronze/silver mashup | RuiRomano | **authored** |
| 9 | **Logs Explorer** | raw error/info, filterable | slicers + table + error trend | Error Rate % | bronze/silver event log | G2, RuiRomano Logs | **authored** |
| 10 | **Requests** | request throughput/duration | histogram + throughput line | (request rollups) | silver | RuiRomano Requests | **authored** |
| 11 | **Counters Deep-Dive** | CPU/mem/handles over time | small-multiples lines | Avg CPU % (rolling 7d) | gold_gateway_health x dim_date | RuiRomano Counters | **authored** |
| 12 | **Gateway Profile** | inventory/config/version/cred-drift | table + cards + SCD history | (dim rollups) | gold_dim_gateway | G10, RuiRomano Profile | **authored** |
| 13 | **Alerting State** | which Activator rules armed/firing | rule status + heartbeat-age | Heartbeat Age | activator-rules + gold_gateway_health | G1 | **authored** (live firing gated) |
| 14 | **Tenant Breadth** | capacity/activity/refreshables | fuam-style | Active Users (30d), Dataset Refresh Count, Capacity CU % | bronze_activity/capacity | fuam | **authored, data-gated (admin-API)** |

**All 14 pages are now authored** into `starter/report/gateway_monitor_v2.report.json` (13 report
pages / 105 visuals; page numbering above counts Executive Overview as the new landing page).
`[Unverified]` until one Desktop pass finalizes bindings + `dim_date`.

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
| Report definition | **PBIR JSON** (`*.report.json`, hand-authored convention) | headless (me) | **`gateway_monitor_v2.report.json` — all 14 pages / 105 visuals** |
| Brand theme | theme JSON | headless (me) | yes |
| Measures | DAX | headless (me) | `measures_v2.dax` (50 measures total across both files) |
| **Semantic model `.pbit`** | compiled template binary | **headless — `build_pbit.py` + pbi-tools.core** | **yes — `starter/report/dist/gwmon_model.pbit`** (12 tables, 50 measures, DirectLake) |
| **`.pbix`** (model-bearing) | compiled binary | **Power BI Desktop only** | no — categorically Desktop-only, see §5 |

## 5. CAN / CANNOT — honest feasibility + headwinds

**CAN do headlessly — DONE in this PR:**
- Author/expand **PBIR** pages (**all 14, 105 visuals**), a **brand theme**, and **50 DAX measures**
  as source-controlled text.
- **Pack a model-bearing `.pbit` template** (`build_pbit.py` -> `pbi-tools.core compile ... PBIT`):
  `starter/report/dist/gwmon_model.pbit` — 12 tables, 50 measures, DirectLake partitions,
  compatibilityLevel 1604, a valid OPC package (verified: `DataModelSchema` parses). Reproducible
  from the `.dax` files with one command. **This is the "pbix artifact" that is actually
  producible without Desktop.**
- Build on RuiRomano + fuam feature sets — both **MIT**, license-clean into this repo with their
  MIT notices retained (`NOTICE`).

**CANNOT do headlessly — and exactly why (the real, remaining headwinds):**
1. **A model-bearing `.pbix` (as opposed to `.pbit`).** This is a *categorical* platform limit, not
   a tooling gap I can close. A `.pbix` embeds a *serialized Analysis Services tabular database* that
   **only Power BI Desktop's engine writes**. I installed and tested the one headless packer that
   exists, `pbi-tools.core` 1.2.0 — its own `--help` states: *"the PBIX output is supported only for
   report-only projects (thin reports), and **PBIT for projects containing a data model**."* I
   exhausted this route: the tool packs our model to **`.pbit`** cleanly (shipped) but **refuses
   model `.pbix` by design**. A thin (report-only) `.pbix` is possible but needs a *live published
   dataset* connection — tenant-gated. **Bottleneck resolution (one manual step):** open
   `gwmon_model.pbit` in Desktop, load `gateway_monitor_v2.report.json`, *Save As `.pbix`* (~2 min).
2. **Visual render + DAX correctness** cannot be verified without opening Desktop. Everything here is
   therefore `[Unverified]` (the repo's existing convention) until one Desktop pass. The `.pbit`
   *packs* but its DAX/DirectLake binding is proven only on open against a live Lakehouse.
3. **The data behind the pages.** *Gateway depth* trending needs the collectors run on a real host +
   the F2 medallion load (`dim_date` + multi-period fact data). *Tenant breadth* (page 14) needs the
   **admin-API grant** — the same Amo-gated SPN that blocks G3's live proof. Pages + measures are
   authored now; their data is gated.

**Net:** the report *source* (PBIR + theme + model + measures) **and a model `.pbit`** are fully
built headlessly and shipped. The three remaining headwinds are (a) the model **`.pbix`**
specifically — one Desktop *Save As* away, categorically Desktop-only; (b) the visual/DAX proof —
same Desktop pass; (c) the real data — the F2/admin-API tenant grant. None are blocked on more
headless engineering; all three converge on a single Power BI Desktop session + the tenant grant.

## 6. What shipped in this PR
- `docs/REPORT-BUILDOUT-PLAN.md` (this plan).
- `starter/report/gateway_monitor_v2.report.json` — **the branded v2 report: all 14 pages / 105
  visuals** (existing 4 rebranded + 10 new), `[Unverified]`.
- `starter/report/theme/gwmon-brand.theme.json` (brand theme).
- `starter/semantic-model/measures_v2.dax` (exec/historical/mashup/tenant measures, `[Unverified]`).
- `starter/report/pages_v2/executive_overview.page.json` (standalone Exec Overview page source).
- `starter/report/build/build_pbit.py` + `build/README.md` — **headless `.pbit` builder**.
- `starter/report/dist/gwmon_model.pbit` — **the packed DirectLake model template** (12 tables,
  50 measures), reproducible via the builder.
- `NOTICE` (MIT attributions to RuiRomano + GT-Analytics).

## 7. Next steps (what's left, and who owns it)
1. **Model `.pbix`:** one Desktop *Save As* from `gwmon_model.pbit` + `gateway_monitor_v2.report.json`
   (you — categorically Desktop-only, see §5.1). Everything up to that step is done headlessly.
2. **`dim_date` + DirectLake endpoint** finalize in Desktop (unblocks all trending; the `.pbit`'s
   `DirectLakeSource` M expression is a labeled placeholder to repoint).
3. **Tenant breadth (page 14 data):** unblock with the admin-API grant (the F2/SPN gate).
4. **Proof:** the F2 pilot validates bindings + real data — flips the report + `.pbit`
   `[Unverified] -> [Proven]`.
