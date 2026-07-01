# Hardware Verdict Scaffolding

**Read this first: I am NOT filling in real machine specs. I don't have them.**

This document is the template your `machine_audit.ps1` output will match, plus the verdict logic I'll apply the moment you paste real numbers. The goal is to make the verdict instantaneous once you have data — not to fabricate a verdict from thin air.

---

## Template: what your JSON will look like

When you run `machine_audit.ps1` in PowerShell, it emits this shape. Placeholder values are marked `<...>`:

```json
{
  "timestamp_utc":        "<ISO 8601 datetime>",
  "hostname":             "<COMPUTERNAME>",
  "os":                   "<e.g. Microsoft Windows 11 Pro 10.0.22631 (build 22631)>",
  "cpu":                  "<e.g. AMD Ryzen 9 5950X 16-Core Processor — 16C/32T @ 3400MHz>",
  "ram_total_gb":         "<e.g. 64.0>",
  "ram_free_gb_now":      "<e.g. 42.3>",
  "disk_c_free_gb":       "<e.g. 780.2>",
  "disk_c_total_gb":      "<e.g. 1862.9>",
  "gpu":                  "<e.g. NVIDIA GeForce RTX 3080 — 10.0GB>",
  "chromium_procs_count": "<int, e.g. 12>",
  "chromium_ram_gb_now":  "<e.g. 3.42>",
  "python":               "<e.g. Python 3.11.5>",
  "git":                  "<e.g. git version 2.42.0.windows.2>",
  "latency_to_pplx_ms":   "<int or null>"
}
```

**Paste the actual JSON to me and I run the verdict below in the same reply.**

---

## Verdict logic I'll apply

Six dimensions, each scored independently. Final verdict is the worst dimension — a machine with 128 GB RAM but a 4-core CPU is still CPU-bound.

### 1. RAM verdict

Your realistic concurrent workload (jarvis-trader Phase 3 + Comet + Chrome + Databricks SQL editor + IBKR TWS + VS Code):

| ram_total_gb | Verdict | Reasoning |
|---|---|---|
| < 16 | 🔴 UNDER | Phase 3 CPCV alone can peak 6-10 GB. You'll swap constantly with your normal workload. Upgrade before running the funnel at scale. |
| 16-31 | 🟡 TIGHT | Workable if you're disciplined about not running Phase 3 + Comet agent + Databricks simultaneously. Contention audit's `both_significant` verdict will be common. |
| 32-63 | ✅ GOOD | Comfortable for your workload. This is the sweet spot for a dev workstation running everything you run. |
| 64+ | ✅ EXCELLENT | Overprovisioned in a good way. You'll rarely hit RAM contention. Focus optimization elsewhere. |

Also check `ram_free_gb_now`:
- < 3 GB free at the moment of the audit means you're currently under pressure. Note what's running when you ran the audit.
- Difference `ram_total - ram_free` shows your steady-state footprint.

### 2. CPU verdict

Look at cores/threads AND clock speed. Phase 3 CPCV is embarrassingly parallel — more cores directly buys faster batches.

| Cores/Threads | Clock | Verdict |
|---|---|---|
| ≥ 12C / 24T | ≥ 3.0 GHz base | ✅ EXCELLENT — Phase 3 batches will fly |
| 8-11C / 16-23T | ≥ 3.0 GHz base | ✅ GOOD — solid for backtests, some parallelism overhead |
| 6-7C / 12-15T | ≥ 3.0 GHz | 🟡 TIGHT — Phase 3 will be slow, especially CPCV with n_groups=6 |
| 4C / 8T or fewer | any | 🔴 UNDER — either parallelize less aggressively or move backtests to a beefier machine |

Apple Silicon (M1/M2/M3/M4) equivalents: M1/M2 = ~8C mid-tier, M2 Pro/Max = ~10-12C good, M3 Max = excellent. But you're on Windows per your background, so unlikely to apply.

### 3. Disk verdict

Phase 3 reads bar data heavily. SSD is table stakes. Also check headroom.

| disk_c_free_gb / disk_c_total_gb | Verdict |
|---|---|
| Free > 20% AND total ≥ 500 GB | ✅ GOOD |
| Free 10-20% OR total < 500 GB | 🟡 TIGHT — telemetry CSVs + backtest results will eat headroom |
| Free < 10% | 🔴 UNDER — free up space before Monday, or point telemetry to a different drive |

Whether it's SSD vs HDD isn't in the audit JSON. [Assumption] I'm going to assume SSD because you're a dev — flag if that's wrong.

### 4. GPU verdict

Not relevant for your current workload. Everything you're doing is CPU + RAM + I/O bound. GPU only matters if you later add ML training. I'll note it and move on.

### 5. Network verdict

`latency_to_pplx_ms`:

| Latency | Verdict |
|---|---|
| < 50 ms | ✅ Excellent |
| 50-150 ms | ✅ Fine for cron work, cloud agent tasks will feel snappy |
| 150-300 ms | 🟡 Cloud agent tasks will feel laggy in Comet's local-browser-tool bridge |
| > 300 ms | 🔴 Something is wrong. Corporate VPN? Wifi issues? |

If `latency_to_pplx_ms` is `null`, ICMP is blocked (common on corporate networks). Not a real problem for the audit stack, but I can't verify network quality.

### 6. Current-load verdict

`chromium_procs_count` and `chromium_ram_gb_now`:

| Chromium footprint at audit time | Verdict |
|---|---|
| 0 procs, 0 GB | You ran the audit with Chrome closed. Fine, but the top-3-killers audit will need Chrome running to be meaningful. |
| 1-5 procs, < 2 GB | Light usage. Consistent with occasional Comet agent tasks. |
| 6-15 procs, 2-6 GB | Normal Chrome/Comet steady-state. |
| > 15 procs OR > 8 GB | You have a lot of tabs open. The top-3-killers audit will almost certainly rank Chromium #1. Not necessarily bad — just be aware. |

---

## Composite verdict I'll deliver

Once you paste real numbers, expect one of these four framings back:

**✅ HEADROOM**: RAM ≥ 32, CPU ≥ 8C/16T, Disk healthy. You can run Phase 3 + Comet + everything simultaneously without meaningful contention. Comet vs web-app choice is purely functional, not resource-driven.

**🟢 SUFFICIENT**: 16-31 GB RAM OR 6-7C CPU, otherwise fine. Workable but you'll see the `both_significant` verdict in some contention audits. Don't run Phase 3 with Comet agent tasks simultaneously. Prefer web-app cron for agent work when you're mid-backtest.

**🟡 CONSTRAINED**: 8-15 GB RAM OR 4C CPU. Real bottleneck. Phase 3 will be slow AND crowd out other workloads. Options: (a) upgrade RAM (cheapest fix), (b) throttle Phase 3 concurrency in your engine, (c) offload backtests to a cloud VM.

**🔴 INADEQUATE**: < 8 GB RAM OR 2C CPU OR full disk. Fundamental mismatch with your workload. The Monday audit stack will still work but will spend its life reporting contention. Fix hardware before scaling the funnel.

I'll also give you 2-3 specific actions based on the exact numbers, not the bucket.

---

## Why I did NOT just make up numbers

Your operating principles explicitly say:

> **Accuracy over fluency.** Never invent APIs, prices, business rules, legal claims, product specs, statistics, citations, or technical behavior.

Machine specs are technical specs. Inventing them to produce a plausible-sounding verdict is exactly the failure mode you set that rule to prevent. A verdict on invented specs is negative-value: you might act on it.

Cost of running the actual audit: **~30 seconds in PowerShell.** No approval, no admin required. Once you paste the JSON, the verdict is deterministic — I don't need to think about it, just look it up in the table above.

If you genuinely cannot run PowerShell right now (traveling, no access to the machine, etc.), tell me what numbers you *do* know off the top of your head — even rough ballparks — and I'll give you a preliminary verdict with `[Assumption]` labels on every load-bearing claim. That's honest partial data. Fabricated data is dishonest and full.
