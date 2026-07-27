# Task list -- pbi-gateway-monitoring

> **GENERATED FILE -- DO NOT EDIT.**
> Source of truth: the drain-loop ledger `~/.claude/loops/pbi-gateway-completion/prd.json`.
> Regenerate with `python scripts/render_tasklist.py`.
> Full rationale for every task: [`IMPLEMENTATION-ROADMAP.md`](IMPLEMENTATION-ROADMAP.md).

**6 / 50 complete (12%)**  `[##..................]`

### Workable right now (nothing blocking these)

- **T1** `XL` -- Build the collector -> OneLake transport
- **T6** `L` -- Implement Activity-Events attribution fallback (Method 2)
- **T9** `M` -- Collapse duplicated parsers; fix false test coverage
- **T11** `L` -- Pester suite for 7 collectors + bring them into CI lint scope
- **T12** `L` -- Cover the 15 untested bronze ingest functions
- **T13** `M` -- Pipeline run log + heartbeat + external dead-man's switch

---

## Phase 0 -- Correctness blockers

*Must land before the live pilot. Running the pilot against this pipeline would spend paid F2 capacity measuring something that cannot work end to end.*

`5/10 complete`

- [ ] **T1** `XL` Build the collector -> OneLake transport
- [ ] **T2** `M` Convert bronze to MERGE; author docs/data-contracts.md `blocked by T1`
- [x] **T3** `S` Fix credential-drift rule (wrong column AND wrong vocabulary) -- PR #8
- [x] **T4** `S` Reconcile alert spec to one source of truth -- PR #8
- [x] **T5** `M` Implement real SCD2 on gold_dim_gateway -- PR #9 (stacked on #8)
- [ ] **T6** `L` Implement Activity-Events attribution fallback (Method 2)
- [x] **T7** `S` Materialize schema-drift warnings to bronze_schema_warnings -- PR #9 (stacked on #8)
- [x] **T8** `S` Stop silently swallowing table-read failures -- PR #9 (stacked on #8)
- [ ] **T9** `M` Collapse duplicated parsers; fix false test coverage
- [ ] **T10** `M` Fix collector silent-failure modes and spool-path discovery `blocked by T11`

## Phase 1 -- Make the system observable to itself

*A monitoring tool that fails silently is worse than none, because it manufactures false confidence.*

`1/8 complete`

- [ ] **T11** `L` Pester suite for 7 collectors + bring them into CI lint scope
- [ ] **T12** `L` Cover the 15 untested bronze ingest functions
- [ ] **T13** `M` Pipeline run log + heartbeat + external dead-man's switch
- [x] **T14** `M` Validate Activator rules - schema AND semantics -- PR #8
- [ ] **T15** `M` Validate the KQL assets
- [ ] **T16** `M` Contract tests on the collector->parser boundary
- [ ] **T17** `L` Data-quality gates at each medallion layer (Great Expectations) `blocked by T2`
- [ ] **T18** `M` Golden-snapshot + property-based regression protection

## Phase 2 -- Live pilot

*Where the flagship claim becomes a number. Needs paid F2 capacity.*

`0/4 complete`

- [ ] **T19** `S` Pre-pilot capacity and state audit (gwmoncap01) `PARKED -- needs live F2 tenant`
- [ ] **T20** `M` Execute Track A - measure the flagship match rate `PARKED -- needs live F2 tenant` `blocked by T1, T2, T6`
- [ ] **T21** `M` Attribution-gap classification protocol (ceiling vs measured) `PARKED -- needs live F2 tenant` `blocked by T20`
- [ ] **T22** `M` Execute Track B and close the U-matrix -> docs/VALIDATED.md `PARKED -- needs live F2 tenant` `blocked by T20`

## Phase 3 -- Forward testing

*Earn the right to be trusted. Engineering effort is small; calendar time is not.*

`0/6 complete`

- [ ] **T23** `L` Fault-injection harness (Invoke-FaultInjection.ps1 + Test-FaultDetection.ps1) `PARKED -- needs live F2 tenant`
- [ ] **T24** `M` Alert log + operator verdict intake (gold_alert_log) `PARKED -- needs live F2 tenant`
- [ ] **T25** `M` Day-0 calibration - set thresholds from each tenant's own percentiles `PARKED -- needs live F2 tenant` `blocked by T24`
- [ ] **T26** `L` Shadow-mode operation (30-60 days calendar) `PARKED -- needs live F2 tenant` `blocked by T24, T25`
- [ ] **T27** `M` Precision/recall analysis + threshold tuning (3-6mo for rare rules) `PARKED -- needs live F2 tenant` `blocked by T26`
- [ ] **T28** `S` Canary promotion (one cluster live, rest shadow) `PARKED -- needs live F2 tenant` `blocked by T27`

## Phase 4 -- Enterprise readiness

*What a Fortune 500 gates on. Detail in docs/ENTERPRISE-READINESS.md.*

`0/8 complete`

- [ ] **T29** `L` Privacy posture: PSEUDONYMIZE_IDENTITY, retention, SECURITY-PRIVACY.md
- [ ] **T30** `M` Row-level security keyed on gateway/cluster/workspace
- [ ] **T31** `M` Fleet-shaped configuration (list-shaped, per-host overrides)
- [ ] **T32** `L` Infrastructure as code (Bicep/Terraform + fabric-cicd + Variable Libraries)
- [ ] **T33** `L` Fleet collector deployment (DSC/Intune/Ansible) + rollback `blocked by T1`
- [ ] **T34** `M` SemVer, CHANGELOG, migrations, compatibility matrix, support policy
- [ ] **T35** `M` Supply chain: pinned deps, CycloneDX SBOM, Dependabot, SECURITY.md
- [ ] **T36** `M` SLOs, error budget, runbook restructure

## Phase 5 -- Report completeness

*Deliberately after Phase 4 -- pages are table stakes, the join is the moat.*

`0/3 complete`

- [ ] **T37** `M` Automate model validation (Tabular Editor BPA, TOM checks, PBI Inspector)
- [ ] **T38** `L` Report parity pages + historical/trend pages `blocked by T37`
- [ ] **T39** `M` Human validation gates (Desktop QA, DirectLake caveats, RLS click-through) `blocked by T30, T38`

## Phase 6 -- Autonomy L1: real-time detection

*First net-new capability. All GA Fabric primitives.*

`0/2 complete`

- [ ] **T40** `L` Stream telemetry via Eventstream custom endpoint (L1 detect) `blocked by T1`
- [ ] **T41** `M` Activator rules as code (REST/Terraform round-trip)

## Phase 7 -- Autonomy L2: prediction

*Start cheap (native KQL), escalate only on evidence.*

`0/2 complete`

- [ ] **T42** `M` Scheduled KQL anomaly detection + forecasting -> gold_predictions (L2) `blocked by T40`
- [ ] **T43** `L` GATE: escalate to multivariate/AutoML only if T42 under-detects `blocked by T42`

## Phase 8 -- Autonomy L3: explanation

*Best value-per-effort in the roadmap. **v1.0 can ship here.***

`0/1 complete`

- [ ] **T44** `M` Fabric Data Agent for root-cause explanation (L3) `blocked by T40`

## Phase 9 -- Autonomy L4: self-healing

*No vendor-supported path -- Fabric UDFs cannot reach on-prem gateways. A distinct systems-integration project.*

`0/3 complete`

- [ ] **T45** `L` GATE: decide + document the on-prem action bridge (Arc/Relay/poll) `blocked by T44`
- [ ] **T46** `L` Closed-loop remediation with tiered approval (L4) `blocked by T45`
- [ ] **T47** `M` Autonomy safety validation (dry-run, blast radius, kill switch) `blocked by T46`

## Phase 10 -- Release and adoption

`0/3 complete`

- [ ] **T48** `M` Application + security review pack `blocked by T29, T35`
- [ ] **T49** `S` v1.0.0 release `blocked by T48`
- [ ] **T50** `M` Fork onboarding + pilot-adopter program `blocked by T49`

---

## Discovery log

Findings surfaced *while* draining, that no prior backlog contained. This is why the loop is a discovery-drain and not a fixed checklist.

- **[CRITICAL]** (pass 1, via T14) The validator's first run found 4 of 7 rules could NEVER fire, not 1 as believed. credential-drift (nonexistent column + value outside vocabulary + 3 broken message placeholders); error-rate-spike (both columns nonexistent, AND a percent-vs-fraction scale error that would have fired on any window with a single error); spool-disk-forecast-low and cpu-anomaly (both enabled against gold_predictions, which no job writes). 2 more emitted broken output: gateway-offline rendered a literal {blast_radius}, mashup-container-runaway carried an inline comment that made its condition unparseable.
- **[HIGH]** (pass 1, via T4) gateway-offline threshold was 3 minutes against a 5-minute collector interval -- it would have fired on every normal collection gap. Reconciled to 10 min.
- **[MEDIUM]** (pass 1, via T4) network-saturation existed ONLY in activator-rules.md, never in the JSON. Ported in; its columns (network_utilization_pct_avg, latency_ms_p95) are real. Rule count reconciled 5-vs-7 -> 8.
- **[MEDIUM]** (pass 1, via T14) activator-rules.md claimed 'Activator requires F8+'. Microsoft documents no hard SKU minimum for Activator beyond a supported capacity; F8 guidance applies to an always-on Eventhouse. Corrected in the generated doc with the correction marked.
- **[MEDIUM]** (pass 2, via T7) _emit_schema_warnings' own docstring claimed the warning was 'captured in a Delta table for Activator alerting'. It was not -- a doc lie on top of the missing implementation. Also only checked the MISSING direction, so a column RENAME could not be recognized as a rename and purely additive drift was invisible.
- **[HIGH]** (pass 2, via T5) The two existing Tier 2.5 dim assertions (count==3, all is_current==True) are exactly what the broken SCD1 implementation passed. A single-run test structurally cannot distinguish overwrite from merge. Lesson: assertions must be able to FAIL against the defect they nominally cover.
- **[CRITICAL]** (pass 3, via T11) Collect-DiskSpool.ps1:202 dereferences $result.DiskInfo.FreeSpacePct when DiskInfo is $null. When the drive read fails, DiskInfo stays null and the script CRASHES under Set-StrictMode -Version Latest instead of writing its output file -- so a drive-read failure produces NO telemetry at all, not even the CollectionErrors record it carefully built. Found by the first Pester run.
- **[HIGH]** (pass 3, via T11) Happy-path run records a CollectionError (expected 0, got 1), and an empty spool dir yields SpoolDirSizeBytes=null rather than 0. Both indicate the collector's error/measurement bookkeeping is wrong even on the nominal path. Under investigation in T10.

---

## Constraints the loop operates under

- NEVER push or merge to main. Feature branches + PRs only; merges are Amo's call.
- Never git add -A. Pathspec commits only.
- Archive, never delete.
- CI must be green on the branch before a task is marked passes:true.
- A task that needs the live F2 tenant parks with status=tenant-gated; it does not block the drain.

**Stop condition:** All tasks with status != 'tenant-gated' have passes:true AND a discovery pass finds 0 new blocking items AND CI is green on the working branch.
