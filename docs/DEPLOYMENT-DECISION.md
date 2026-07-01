# Deployment Decision Guide — Which Path Do I Use?

This repo contains **two complementary deployment stories**. Pick one (or combine) using the decision tree below. This exists because a fork can otherwise be confused about whether to run the `docs/`+`scripts/` (FPM) path or the `starter/`+`kql/` (build-new) path.

---

## The two paths

| | **Path A — Adopt FPM** | **Path B — Build-New** |
|---|---|---|
| **Folders** | `docs/`, `scripts/` | `starter/` (collectors, notebooks, kql, alerting) |
| **What it is** | Deploy Microsoft's maintained Fabric Platform Monitoring, plus this repo's node-deploy + health-validate scripts | A standalone Fabric-native tool: your own collectors → Delta medallion → report + the v2 ceiling-breakers |
| **Storage** | Eventhouse / KQL (FPM-native) | OneLake Delta medallion (+ Eventhouse for KQL intelligence) |
| **Effort to first value** | Low (~hours) — FPM Jumpstart deploys in minutes; scripts automate node setup | Medium (~days) — you run the medallion + wire the model |
| **Maintained by** | Microsoft (FPM core) + you (the deploy scripts) | You (fork owner) |
| **Ceiling-breakers (identity join, ETW network)** | Add them on top via `starter/kql/` against FPM's Eventhouse | Built into the design |
| **Best when** | You want gateway observability fast and are happy on FPM's KQL stack | You want Delta/OneLake control, custom analytics, and the differentiators as first-class |

---

## Decision tree

```
START
  │
  ├─ Do you need the identity-join + network-cost ceiling-breakers as core?
  │     │
  │     ├─ NO, I just need solid gateway observability quickly
  │     │      └─► PATH A (Adopt FPM). Use docs/ + scripts/.
  │     │          Optionally add starter/kql/01_identity_join.kql later.
  │     │
  │     └─ YES, the differentiators are the point
  │            │
  │            ├─ Are you already running FPM, or want Microsoft to own collection?
  │            │      └─► HYBRID. Keep FPM for collection (Path A scripts),
  │            │          bridge its Eventhouse into your Lakehouse
  │            │          (starter/notebooks 01_bronze USE_FPM_BRIDGE=True),
  │            │          run all starter/kql/ intelligence on top.
  │            │
  │            └─ Do you want full Delta/OneLake control + custom analytics?
  │                   └─► PATH B (Build-New). Use starter/ end to end.
  │                       Enable Workspace Monitoring for the identity join.
  │
  └─ Unsure? ─► Start PATH A (cheap, reversible), then graduate to HYBRID
               once you want the differentiators. Nothing in Path A blocks Path B.
```

---

## Recommended default

**Hybrid**, for most teams with the stated profile (Fabric + Azure tenant admin, wants the differentiators):

1. **Collection** — let FPM own it (Microsoft-maintained). Deploy nodes with `scripts/Deploy-FpmGatewayNode.ps1`, validate with `scripts/Test-FpmGatewayNode.ps1`.
2. **Bridge** — expose FPM's Eventhouse as Delta via [Eventhouse OneLake availability](https://learn.microsoft.com/en-us/fabric/real-time-intelligence/event-house-onelake-availability); ingest with `starter/notebooks/01_bronze_ingest.py` (`USE_FPM_BRIDGE=True`).
3. **Differentiate** — enable Workspace Monitoring, run `starter/kql/01_identity_join.kql` (identity), `02_anomaly_forecast.kql` (predictive), `03_diffpatterns_triage.kql` (auto-RCA).
4. **Act** — wire `starter/alerting/` Activator rules.

This gets Microsoft-maintained collection + your differentiating intelligence, without re-implementing plumbing or forking FPM.

---

## What each path does NOT give you

- **Path A alone:** no identity join, no per-query network cost, no Delta-native custom analytics (until you add `starter/kql/` + collectors).
- **Path B alone:** you own collector maintenance; more setup effort than FPM Jumpstart.
- **Neither, in v1:** VNet gateway monitoring (descoped — see `research/phase0_scope.md` §4), per-DirectQuery UserId, Dataflow Gen1 / Paginated Report attribution ([Blocked-by-platform]).

---

*When in doubt, start cheap (Path A), keep the differentiators one step away (Hybrid). Full build (Path B) is for teams that want Delta/OneLake ownership from day one.*
