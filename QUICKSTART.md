# QUICKSTART — Run the Pilot (30 min)

The one test that matters. It proves the flagship capability: that the gateway's log
ticket number (`RequestId`) matches a Fabric Workspace Monitoring record that knows the
**user** and **dataset**. If it matches, the tool's core promise is real.

> Full hand-holding version: [`docs/PILOT-GUIDE-START-HERE.md`](docs/PILOT-GUIDE-START-HERE.md).
> This is the condensed path. **Requires:** Fabric admin, F8+ capacity.

---

## Task 1 — The identity-join test

**1. Make a test workspace**
- https://app.fabric.microsoft.com → **Workspaces → + New workspace**
- Name `gateway-pilot`, assign to your **F8+ capacity**, Apply.

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

**5. Run the match** — in the Eventhouse/KQL DB created in `gateway-pilot`, **New → KQL Queryset**:

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
for both known *and* new/unknown columns. Confirmed on a live tenant 2026-07-01 —
see [`LIVE-TENANT-FINDINGS.md`](LIVE-TENANT-FINDINGS.md) → Pain #4.

## What happens next
Paste the results (even an error) back to the assistant. That flips the biggest
`[Unverified]` labels to verified and unblocks finalizing the report + one-click bundle.
See [`PRODUCTIZATION.md`](PRODUCTIZATION.md) for the full path to marketable.
