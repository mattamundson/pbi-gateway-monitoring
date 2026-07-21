# Enterprise Readiness — what a Fortune 500 gates on

**Purpose.** `PRODUCTIZATION.md` answers "is this a product?"; `MVP-ROADMAP.md` answers "does it
work?". This answers a third, different question: **"would a large enterprise's procurement,
legal, security, and platform teams actually let it in the door?"** Those teams reject on criteria
that have nothing to do with whether the code is good, and they reject *early* — usually before a
single engineer evaluates the tech.

Ordered by *what stops adoption soonest*, not by effort.

> **Sequencing caveat, stated up front.** Everything below is downstream of the one open question:
> **the flagship identity join's real match rate is still unmeasured** (`LIVE-TENANT-FINDINGS.md`).
> If that number comes back weak, the value proposition changes and enterprise scaffolding built
> around it is wasted. **Do the F2 pilot first** (`docs/RUNBOOK-F2-pilot.md`), then work this list.
> The one exception is Tier 0 item 1 (licensing), which is free, takes minutes, and is pure
> downside if left as-is.

---

## The good news: the differentiator is real and verified against primary sources

Confirmed 2026-07-21 against Microsoft's own current documentation — this is the reason the tool
has a defensible reason to exist at all:

- Microsoft's semantic-model operation log schema documents `OperationId` as **"Same as
  `XmlaRequestId`"**, and the same table carries **`ExecutingUser`** ("the user running the
  operation"). The join key this tool is built on is **Microsoft-documented, not community
  folklore**. ([Semantic model operation logs](https://learn.microsoft.com/en-us/fabric/enterprise/powerbi/semantic-model-operations))
- **No Microsoft product performs that join.** The gateway's own CSV logs carry `RequestId` but
  have *no user-identity field of any kind*; Workspace Monitoring carries `ExecutingUser` but *no
  gateway telemetry*. Microsoft's own FPM gateway accelerator has no UPN field and makes no
  mention of enriching from Workspace Monitoring.
- **No third-party tool does it either** — Power BI Sentinel, SummitView, and both FUAM variants
  are inventory/lineage/governance tools, not query-identity-attribution tools.
- **VNet gateway monitoring is a Microsoft-acknowledged blind spot** — the VNet gateway limitations
  doc has *zero* mention of monitoring/diagnostics/logging, and a direct customer question on MS Q&A
  about VNet gateway performance statistics got a non-answer redirect. The D5 descope is
  well-founded and, if anything, understated.

**Implication:** the moat is the join, not the report. Protect and prove that; treat pages as
table stakes.

---

## Tier 0 — Blocks adoption at intake (cheap, high-consequence)

### 1. ~~The licensing contradiction~~ — ✅ **RESOLVED 2026-07-21**
`LICENSE` previously read *"PROPRIETARY AND CONFIDENTIAL … No license, express or implied, is
granted to any party to use, copy, modify, merge, publish, distribute…"* while `README.md`
advertised a **"fork-ready open-source accelerator,"** the repo shipped `CONTRIBUTING.md` and a
pilot-report issue template, and `NOTICE` positioned the work as built on two **MIT-licensed**
community projects. An enterprise OSS-review board rejects that combination automatically — the
stated intent (fork it, contribute back) was legally impossible under the actual license.

**Resolved in favour of the open-accelerator path:**
- `LICENSE` → **Apache License 2.0**. Chosen over MIT deliberately: Apache-2.0 carries an **express
  patent grant** (§3) and a **trademark clause** (§6), which is specifically what enterprise
  open-source review boards look for. MIT has neither.
- `NOTICE` → now Apache-§4(d)-conformant (product name + copyright + license pointer, then the
  third-party attributions), so it correctly travels with redistributions.
- `README.md` License section → rewritten to state the actual grant and obligations, and to point
  adopters at this document before enterprise deployment.
- `CONTRIBUTING.md` → adds an explicit contribution-licensing section (Apache-2.0 §5 inbound=outbound,
  no separate CLA) and instructions for attributing adapted third-party code.

**Why this was the right call for the stated goal:** community pilot reports are the only realistic
mechanism for converting the `[Unverified]` matrix into verified status — no single operator has
access to every gateway version, tenant shape, and Fabric SKU. A proprietary license forecloses
exactly that.

### 2. Privacy posture on the flagship feature — the DPIA conversation
The tool's headline capability joins gateway queries to **real user UPNs** (`ExecutingUser`) and
persists them in a customer-owned lakehouse **permanently** (bronze 90d / gold 365d per current
config, with no privacy-driven expiry).

Context that makes this load-bearing, not theoretical:
- **Workspace Monitoring's own native retention is 30 days, and the Eventhouse is read-only.** So
  the marginal privacy risk this tool introduces is *not* novel data collection — Microsoft already
  exposes `ExecutingUser` — it is **durability and joinability**: copying it into permanent,
  queryable, joined storage. That is the honest framing, and it is exactly what a DPIA scrutinizes.
- **Microsoft's own May/June 2026 gateway release consent-gates comparable data.** Gateway
  diagnostics (explicitly including Mashup logs, which Microsoft's docs call *"potentially
  sensitive"*) now require **both** gateway-admin and tenant-admin consent, upload to a
  Microsoft-managed store, and are **purged after 48 hours**. ([Configure log files — admin consent
  for gateway diagnostics](https://learn.microsoft.com/en-us/data-integration/gateway/service-gateway-log-files))
  Microsoft applies dual-consent + a 48-hour ceiling to this class of data for its own support
  pipeline; this tool currently applies neither. *(Scope caveat: that consent feature is currently
  scoped to Dataflow Gen2 artifacts, not the Query Execution CSVs the collectors read — so it does
  not gate this architecture today. It is cited as directional precedent, not a current control.)*

**What to build (all three are modest work, and together they turn a blocker into a selling point):**
- `PSEUDONYMIZE_IDENTITY` config switch — hash/tokenize `ExecutingUser` by default, with real UPNs
  strictly opt-in. Most operator value (which *dataset*, which *pattern*, which *gateway*) survives
  pseudonymization; naming the human is rarely what fixes the refresh.
- **Configurable, privacy-driven retention** on the identity-bearing tables specifically, separate
  from the operational retention already configured.
- `docs/SECURITY-PRIVACY.md` stating plainly: what personal data is processed, where it lands, how
  long it lives, how to purge on a DSAR, and that the adopter is the data controller. Cite the
  Microsoft consent precedent above as the anchor — there is no Microsoft-authored DPIA naming
  these fields to cite instead (checked; it does not exist).

> Framed correctly this is an **advantage**: native retention is 30 days, and enterprises genuinely
> need longer history. "Durable gateway history with a defensible privacy model" is a stronger
> pitch than either "we keep everything forever" or "use the 30-day native tool."

---

## Tier 1 — Blocks the platform team (real engineering)

### 3. Row-level security — every viewer currently sees every gateway
The Nexus semantic model defines **no roles and no RLS**. A Fortune 500 runs gateways across
business units, geographies, and often legal entities with contractual data-separation obligations.
"Everyone with report access sees every query on every gateway" fails that immediately — and
becomes far more acute once `ExecutingUser` is in the model (see Tier 0.2).

**Build:** RLS roles keyed on gateway/cluster/workspace, mapped to Entra groups.

### 4. The config shape assumes one gateway, one workspace
`config.sample.json` carries a single `gateway.logPath`, a single `spoolPath`, one `workspaceId`,
one `lakehousePath`. An enterprise estate is tens-to-hundreds of gateway hosts across multiple
clusters, and often more than one Fabric workspace/capacity.

**Build:** a gateway *inventory* config (list-shaped, per-host overrides, per-cluster grouping) —
and confirm the medallion's grain genuinely holds at that cardinality rather than assuming it does.

### 5. No infrastructure-as-code, and collectors deploy by hand
There is **no Terraform, Bicep, or ARM** in the repo. Deployment is PowerShell scripts plus Fabric
notebooks a human runs. Enterprise platform teams deploy through pipelines with reviewable,
reproducible, drift-detectable state — they do not hand-run notebooks in production, and they will
not hand-copy a collector script onto 100 Windows hosts.

**Build:** (a) Bicep or Terraform for the Azure/Fabric side (capacity, Key Vault, SPN, workspace,
lakehouse); (b) a real fleet-deployment path for the collectors — DSC, Intune, GPO, or Ansible —
with a documented upgrade and rollback story.

### 6. No versioning, changelog, or upgrade contract
No `CHANGELOG`, no `VERSION`, no tags, no SemVer, no breaking-change policy. An enterprise adopting
a tool needs to know what version they run, what changed, whether an upgrade is safe, and how long
a version is supported. This is also what makes the `[Unverified]` → `[Verified against gateway
vX.Y]` promise in `research/phase5_validation.md` §5 actually mean something.

**Build:** SemVer + tags + `CHANGELOG.md` + a stated support/compatibility policy (which gateway
versions and Fabric SKUs a release is validated against).

---

## Tier 2 — Coverage expansion (only after the above)

### 7. VNet data gateways
Confirmed above as a genuine, Microsoft-unaddressed blind spot — which makes it simultaneously the
biggest *opportunity* and a real *cost*: there is no host to run PowerShell on, so it needs an
architecturally separate REST/portal-pull collector (per `DECISIONS.md` D5). Quantitative
VNet-vs-standard adoption share in large enterprises is **[UNVERIFIED]** — no reliable figure found;
do not size this work on an assumed split. Validate demand with actual pilot adopters first.

---

## What this does *not* need

Worth stating, because it is where effort naturally drifts: **more report pages are not the gap.**
The six RuiRomano-parity pages noted in `MVP-ROADMAP.md` M7 are worth porting for completeness, but
no Fortune 500 rejects a monitoring tool for having 12 pages instead of 18. They reject it for
licensing, privacy, RLS, and deployment automation — the four things above.

---

*Status: assessment dated 2026-07-21. Landscape claims verified against Microsoft Learn primary
sources and GitHub repository metadata; items marked `[UNVERIFIED]` could not be confirmed and are
flagged rather than estimated.*
