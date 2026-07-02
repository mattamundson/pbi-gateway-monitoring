# QUICKSTART — Run the Pilot (30 min)

The one test that matters. It proves the flagship capability: that the gateway's log
ticket number (`RequestId`) matches a Fabric Workspace Monitoring record that knows the
**user** and **dataset**. If it matches, the tool's core promise is real.

> Full hand-holding version: [`docs/PILOT-GUIDE-START-HERE.md`](docs/PILOT-GUIDE-START-HERE.md).
> This is the condensed path. **Requires:** Fabric admin, F2+ capacity for the identity-join
> (trial capacity can't provision the Workspace Monitoring Eventhouse — see the
> step-by-step [`docs/RUNBOOK-F2-capacity-for-match-rate.md`](docs/RUNBOOK-F2-capacity-for-match-rate.md) to rent F2 for ~1 hour and pause it right after).
> test; F8+ only for the optional Activator/alerting step.

---

## Task 1 — The identity-join test

**1. Make a test workspace**
- https://app.fabric.microsoft.com → **Workspaces → + New workspace**
- Name `gateway-pilot`, assign to your **F2+ capacity** (F8+ only needed later for the
  optional Activator/alerting step), Apply.

**2. Turn on Workspace Monitoring** (the "guest list")
- Open workspace → **Workspace settings** (gear) → **Monitoring** → toggle **On** → Save.
- Wait a few minutes for it to provision an Eventhouse.

**3. Generate activity**
- Put a semantic model that uses your **on-prem gateway** into this workspace.
- Click **Refresh** on it once.

**4. Get a ticket number from the gateway**
- On the gateway's Windows host, open in File Explorer:
  `C:\Windows\ServiceProfiles\PBIEgwService\AppData\Local\Microsoft\On-premises data gateway\Report`
- Open a `QueryStartReport*` file in Notepad; find the **`RequestId`** column; copy 2–3 values.

**5. Run the match** — in the Eventhouse/KQL DB created in `gateway-pilot`, **New → KQL Queryset**.
Copy-paste source (no need to retype): [`starter/kql/PILOT-identity-join-test.kql`](starter/kql/PILOT-identity-join-test.kql).

Peek first:
```kql
PowerBIDatasetsWorkspace
| where Timestamp > ago(1d)
| take 20
| project Timestamp, OperationId, OperationName, ExecutingUser, ItemName
```
Then the actual match:
```kql
PowerBIDatasetsWorkspace
| where OperationId == "PASTE-YOUR-REQUESTID-HERE"
| project Timestamp, OperationId, ExecutingUser, ItemName, ItemId
```

**6. Report the result** — ✅ matched (user + dataset shown) / ⚠️ data exists but RequestId
returned nothing / ❌ empty or error (include the message).

**7. Get the flagship NUMBER (the match rate)** — steps 5–6 prove the join *works* on a
sample; this quantifies it across all queries. Land the gateway logs first
(`starter/notebooks/01_bronze_ingest.py`, which now routes through the sanitizing
`gateway_bronze_lib` — **don't** use Fabric Load-to-Tables on the raw CSV), then run
[`starter/kql/04_identity_match_rate.kql`](starter/kql/04_identity_match_rate.kql):
- **Block A** → `total_gateway_queries`, `attributed_queries`, **`match_rate_pct`** ← the number.
- **Block B** → the Refresh-vs-DirectQuery split (DirectQuery coverage is the open question).
- **Block C** → sample unattributed queries if the rate is low.
The filters (`lookback` / `workspace_filter`) are pushed down before the join, so keep
`15m` + one workspace on a throttled capacity. Paste the numbers into the pilot issue.

---

## The 3 quick extras (2 min each — resolve the biggest [Unverified] facts)

1. **Log sample** — paste the header + 3 rows of `QueryStartReport`, especially the
   `EvaluationContext` column (redact sensitive values). Is it `{JSON}` or base64?
2. **Column names** — run and paste:
   ```kql
   PowerBIDatasetsWorkspace | getschema | project ColumnName, ColumnType
   ```
3. **Cmdlet check** — on the gateway host, PowerShell 7:
   ```powershell
   Get-Command -Module DataGateway | Select-Object Name
   ```

---

## Caveat before you start
The match only works if your gateway processes **Fabric/Power BI semantic-model** workloads
(not Dataflow Gen1 / Paginated Reports). If the peek query returns empty, Workspace
Monitoring likely hasn't captured activity yet — wait 10–15 min after a refresh and retry
before concluding it failed. The `RequestId == OperationId` join is confirmed in Microsoft
docs but **[Unverified] in your specific tenant** — that's exactly what this test resolves.

## ⚠️ When you ingest the gateway CSV — do NOT use Fabric "Load to Tables"
The raw gateway performance CSV has headers with unit suffixes —
`QueryExecutionDuration(ms)`, `SpoolingTotalDataSize(bytes)`, `DiskRead(byte/sec)`.
Fabric's **Load to Tables** shortcut rejects them at schema-inference with
`AnalysisException: InvalidColumnName` (Spark/Delta forbid `( ) / , ; { } =` in column
names) — this happens **before** any of our parsing code runs, so it can't be caught there.

**Route the CSV through the notebook path instead:** `starter/notebooks/01_bronze_ingest.py`
→ `read_gateway_csv()`, which auto-sanitizes those headers (`(ms)`→`_ms`, `(bytes)`→`_bytes`)
for both known *and* new/unknown columns. The `InvalidColumnName` bug itself was confirmed
on a live tenant 2026-07-01; the sanitizer fix was proven on local Spark (off-tenant
follow-up) — see [`LIVE-TENANT-FINDINGS.md`](LIVE-TENANT-FINDINGS.md) → Pain #4.

## What happens next
Paste the results (even an error) back to the assistant. That flips the biggest
`[Unverified]` labels to verified and unblocks finalizing the report + one-click bundle.
See [`PRODUCTIZATION.md`](PRODUCTIZATION.md) for the full path to marketable.
