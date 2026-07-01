# Engineering session log — 2026-07-01

**Scope:** functional verification of the repo ("does the tool actually work?"),
remediation of the bugs that surfaced, and a critical mapping of the tool against
the top-10 researched operator pain points. Curated from the working session; the
raw assistant transcript is intentionally **not** included (it carries unrelated
personal/environment context).

---

## 1. What this session did

1. Verified what actually runs vs. what is claimed — by executing the code, not
   reading the docs.
2. Found and fixed the bugs that verification surfaced (6 real defects).
3. Closed the U14–U16 code-quality items from `phase5_validation.md`.
4. Mapped the tool against all 10 researched pain points and found two holes.
5. Wired up the missing refresh-history leg (pain #2) and declared #5 a non-goal.

Prior work (the SWOT, the security scrub of git history, and the desk-verification
of the identity join against Microsoft docs) predates this log.

---

## 2. Verification results — what is proven vs. infra-gated

| Layer | How verified | Result |
|---|---|---|
| Python modules compile | `py_compile` (7 files) | ✅ |
| PowerShell parses | AST parser (all collectors) | ✅ |
| PSScriptAnalyzer | per-file, Errors only | ✅ Errors = 0 (benign warnings only) |
| Host-runnable collectors | **executed** `Collect-NetworkMetrics`, `Collect-EventLog`, `Collect-DiskSpool` | ✅ run clean after fixes |
| Tier-1 parser test | **executed** `test_parser.py` | ✅ 14/14 assertions pass |

**Infra-gated (compiles, cannot execute off a live gateway/Fabric):**
- Tier-2 Spark parser test (needs pyspark/delta/Java)
- The 3 PySpark notebooks (need a Fabric Lakehouse)
- 2 gateway-only collectors (`Collect-GatewayLogs`, `Get-GatewayInventory` — need the
  DataGateway module + a service principal; SP hits the known `Get-DataGatewayInfo` 401)
- `Collect-RefreshHistory` (needs a Power BI tenant + SP)
- The flagship identity join (needs a live tenant + Workspace Monitoring) —
  **desk-verified against Microsoft docs**, match rate still unknown

**CI caveat:** the Tier-1 test passes locally but has **never run in CI** — GitHub
Actions is billing-blocked on the account, so the workflow shows `startup_failure`
at 0s. Not a code problem; resolve Actions billing to make CI gate for real.

---

## 3. Bugs found by verification (6) and their fixes

| # | Bug | Found by | Fix | Status |
|---|---|---|---|---|
| 1 | `Collect-NetworkMetrics` latency probe crashes under StrictMode on PS7 (`ResponseTime` vs `Latency`) | running it | `PSObject.Properties` probe | ✅ verified |
| 2 | `.Count` on `$null` throws under StrictMode when a query returns nothing (EventLog + 3 more collectors) | running `Collect-EventLog` (0 events + error) | array-wrap query assignments | ✅ verified |
| 3 | `Get-GatewayInventory` used `goto` (invalid in PowerShell) in the interactive-auth fallback | linting | restructured branch, removed dead `$credential` | ✅ fixed |
| 4 | **U16** — collectors read *flat* config keys while the sample nests them (`gateway.logPath`); under StrictMode the flat read *throws*, so the documented override **crashed** the collector on a custom service account | config inspection + StrictMode repro | nested StrictMode-safe read + legacy-flat fallback | ✅ verified (all 3 cases) |
| 5 | **U14** — `cast_query_execution` silently cast unparseable numerics to null, masking data-quality loss | code review | additive `_cast_errors` column + Spark-free `cast_error_columns()` + 6 tests | ✅ verified (Spark-free logic) |
| 6 | **U15** — two time-window joins fan one query out to many correlated rows, inflating counts | code review | `row_number()` nearest-pick dedup per RequestId | ⚠️ **compile-checked only** — Spark semantics need the pilot |

Also: guarded the top-level pyspark import in `gateway_bronze_lib` so the pure-Python
helpers are importable Spark-free (they are now the true single source of truth for
the portable tests, instead of falling back to manual copies).

---

## 4. Pain-point coverage — the critical map

Tiers: ✅ proven · 📄 desk-verified · 🔵 designed/compile-checked (pilot-gated) · ⚠️ partial · ❌ gap

| # | Pain point | Status | Notes |
|---|---|---|---|
| 4 | PBIT breaks on schema drift | ✅ **proven** | column-name adaptive parser; unit-tested against mid-schema column / comma / quotes |
| 3 | Query → dataset/user attribution | 📄 **flagship** | KQL `RequestId→OperationId` + `EvaluationContext`; desk-verified vs MS docs; **match rate needs pilot** |
| 7 | Network / bandwidth blindspot | ✅ collector / 🔵 correlation | `Collect-NetworkMetrics` runs; host-level (not per-query, by design) |
| 9 | Disk spooler surprises | ✅ collector / 🔵 alert | `Collect-DiskSpool` runs clean |
| 1 | Real-time offline alerting | 🔵 designed | Activator `gateway-offline`; DSL unverified; heartbeat leans on inventory collector |
| 6 | Fleet / multi-gateway view | 🔵 designed | `gold_cluster_load` CV skew; depends on inventory collector (SP/401) |
| 10 | Credential / datasource drift | 🔵 designed, shakiest | relies on `Get-DataGatewayClusterDatasource`, itself `[Unverified]` |
| 2 | Opaque refresh triage | 🔵 **now wired** | Service-side leg (`Collect-RefreshHistory` → `bronze_refresh_history`) added this session; was previously half-built |
| 8 | Manual / brittle setup | ⚠️ partial | config-driven (U16 crash fixed) but no auto-discovery; full stack is arguably more setup than the PBIT |
| 5 | Mashup per-process memory | ❌ **non-goal** | host-level aggregate only; per-PID→dataset is `[Feasible-with-effort]` v2, now explicitly declared uncovered |

**Bottom line:** design-level ~8/10 addressed, #2 now wired, #5 declared out of scope.
Verified-level ~4 stand on proven ground today (#4 fully; #3 desk-verified; #7/#9
collectors run). The alerting/fleet/identity/triage layers are exactly what the
Phase 5 pilot exists to confirm.

---

## 5. Decisions made this session

- **Repo stays private** — internal team tests first before any public exposure.
- **Not going public** — the content PII/secret sweep came back clean (working tree +
  full git history), but public is deferred; the publish posts stay parked with
  `<REPO-URL>` placeholders.
- **Personal Gmail in commit author metadata** — accepted as-is (the maintainer's
  intentional standing git identity); not scrubbed.
- **U15 fix shipped despite being unrunnable here** — leaving a known fan-out was
  judged worse than a compile-checked textbook fix; flagged `[Fabric-verify-pending]`.
- **Raw assistant transcript NOT committed** — carries unrelated personal/environment
  context; this curated log is the substitute.

---

## 6. Recommended next actions (priority order)

1. **Run the Phase 5 pilot Task 1 (match rate)** — `docs/PILOT-GUIDE-START-HERE.md`.
   It's the single highest-value thing: it closes the identity join (#3) and now the
   refresh-triage (#2) at once. File the Step 1.8 report block per gateway.
2. **Validate SP auth early** — #1, #6, #10 all ride on the inventory/datasource
   collectors, which hit the known `Get-DataGatewayInfo` 401. Resolve that first or
   four pain points stay blocked.
3. **Get a Spark run** (Fabric notebook dry-run or local pyspark) to execute the
   Tier-2 test, the U15 dedup, and the new refresh-history ingest — the
   compile-checked-only code is the biggest residual risk.
4. **Fix GitHub Actions billing** so CI actually gates (tests pass locally but never
   run in CI).
5. **Add a refresh `requestId` ↔ gateway `RequestId` match-rate check** to the pilot
   (now that `Collect-RefreshHistory` exists) — it's the same open question as the
   identity join and confirms #2 end to end.
6. **v2 candidate:** a lightweight per-process Mashup sampler for #5 — a top-5 pain,
   currently fully uncovered.
