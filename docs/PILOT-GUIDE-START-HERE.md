# Pilot Guide — Start Here (explained simply, step by step)

This is your hand-holding checklist to test the most important part of the tool.
No prior Fabric-admin experience assumed. Do the steps in order. Total time: ~30–45 min.

**What you're trying to prove:** that we can take a mystery ID from the gateway's
logs and use it to find out **which report/dataset and which person** caused a slow
or failed query. Right now the gateway logs only show a code like `req-1234`. We
think that code secretly matches a record in another Fabric log that *does* know the
person and dataset. If they match, the tool's biggest promise is real.

Think of it like this: the gateway hands you a **coat-check ticket number** but no
name. Somewhere else there's a **guest list** with that same ticket number next to
the guest's name. If the numbers line up, you can name the guest. That's the test.

> You will need: an account that is a **Fabric admin / capacity admin** and can
> turn on tenant features. You said you have this. Everything below happens in a
> web browser at **app.fabric.microsoft.com** unless it says otherwise.

---

## TASK 1 — The big test: can we name the guest? (do this first)

### Step 1.1 — Make a safe place to experiment
1. Go to **https://app.fabric.microsoft.com** and sign in.
2. On the left, click **Workspaces** → **+ New workspace**.
3. Name it `gateway-pilot`. Under **Advanced**, make sure it's assigned to a
   **Fabric capacity (F-something, like F8 or higher)**. Click **Apply**.
   - *Why:* a workspace is just a folder/project. We use a fresh one so we don't
     touch anything real.

### Step 1.2 — Turn on "Workspace Monitoring" (the guest list)
1. Open your new `gateway-pilot` workspace.
2. Top-right, click **Workspace settings** (the gear).
3. Find **Monitoring** in the left menu of that settings panel.
4. Toggle **Workspace monitoring** to **On**. Click **Save/Enable**.
   - It may take a few minutes to finish setting up. It creates a hidden database
     called an **Eventhouse** that records what happens in this workspace.
   - *Why:* this is the "guest list" that knows the user + dataset for each query.
   - *Note:* if it says it conflicts with **Log Analytics**, that's fine — just
     use this (Workspace Monitoring), not Log Analytics.

### Step 1.3 — Make something happen so there's data to look at
1. Put (or create) a **semantic model** (a Power BI dataset) in this workspace that
   connects to a source **through your on-premises data gateway**.
   - Easiest: take an existing report/dataset that already uses your gateway and
     move a copy into this workspace, OR publish a small Power BI Desktop file that
     uses a gateway source.
2. **Refresh** that dataset once (click the **↻ refresh** icon next to it).
   - *Why:* the refresh makes the gateway do work, which writes a log line with a
     ticket number — and also writes a matching line to the guest list.

### Step 1.4 — Get the gateway's "ticket numbers"
You need a few rows from the gateway's own log file. On the **Windows computer where
the gateway is installed** (ask whoever manages it, or do it yourself if that's you):
1. Open **File Explorer**.
2. In the address bar paste this and press Enter:
   `C:\Windows\ServiceProfiles\PBIEgwService\AppData\Local\Microsoft\On-premises data gateway`
   - If that folder doesn't exist, the gateway may use a different service account —
     replace `PBIEgwService` with the account name, or ask your gateway admin.
3. Open the **Report** subfolder. Find a file that starts with **`QueryStartReport`**
   (and one starting with `QueryExecutionReport`).
4. Right-click → **Open with → Notepad**. You'll see rows of comma-separated text.
   The first row is the column names; look for the **`RequestId`** column.
5. Copy 2–3 of those `RequestId` values (they look like GUIDs or `req-...`). Keep the
   whole file handy too — you'll paste a few rows to me in TASK 2.

### Step 1.5 — Run the matching query
1. Back in Fabric, open your `gateway-pilot` workspace. You'll see a new item that
   is the **Eventhouse / KQL Database** created by monitoring (name contains
   "Monitoring"). Click it.
2. Click **New → KQL Queryset** (or the **Explore your data / query** button).
3. Open the file **`starter/kql/01_identity_join.kql`** from our repo
   (github.com/mattamundson/pbi-gateway-monitoring). Copy the **first query block**
   (the part under "Core join").
4. Paste it into the KQL query window.
5. **Important:** this query expects the gateway logs to already be in a table called
   `bronze_query_execution`. For this first quick test you probably don't have that
   yet — so instead do the **simple sanity version** below. Paste THIS instead and
   run it (click **Run**):

   ```kql
   PowerBIDatasetsWorkspace
   | where Timestamp > ago(1d)
   | take 20
   | project Timestamp, OperationId, OperationName, ExecutingUser, ItemName
   ```
   - *Why:* this just peeks at the "guest list." You're checking two things:
     (a) does data show up at all, and (b) do you see real values in
     **ExecutingUser** (a person) and **ItemName** (a dataset).

6. Now the actual match. Take ONE `RequestId` you copied in Step 1.4 and run:

   ```kql
   PowerBIDatasetsWorkspace
   | where OperationId == "PASTE-YOUR-REQUESTID-HERE"
   | project Timestamp, OperationId, ExecutingUser, ItemName, ItemId
   ```
   - Replace `PASTE-YOUR-REQUESTID-HERE` with the real value (keep the quotes).

### Step 1.6 — Read the single-row result
One `RequestId` gives you a yes/no. You're looking for one of:
- ✅ **A row came back with ExecutingUser and ItemName filled in.** → the flagship
  claim holds. Proceed to Step 1.7 to measure *how often* it holds.
- ⚠️ **The guest list has data, but this RequestId returned nothing.** → close;
  we may need `EvaluationContext` instead. Still run Step 1.7, then grab a
  QueryStart row (TASK 2).
- ❌ **Empty / error / no data at all.** → note the exact message; still run 1.7.

### Step 1.7 — Measure the match rate (THE number — this is what to report)
One row is anecdote; the **match rate** is the result. This computes it directly, and
it's fully self-contained — you do **not** need the `bronze_query_execution` table yet.

1. From the `QueryExecutionReport...` file (Step 1.4), pull **10–30 `RequestId`
   values** from a recent window (more is better; a mix of refresh *and* DirectQuery
   if you have both).
2. Paste them into the `datatable` below (one per line, keep the quotes and commas),
   then **Run**:

   ```kql
   // Paste your gateway RequestId values here — one per line, quoted, comma-separated.
   let gatewayRequestIds = datatable(RequestId: string)
   [
       "PASTE-REQUESTID-1",
       "PASTE-REQUESTID-2",
       "PASTE-REQUESTID-3"
       // ...add the rest
   ];
   let attributed =
       PowerBIDatasetsWorkspace
       | where Timestamp > ago(2d)
       | where isnotempty(ExecutingUser)
       | distinct OperationId, ExecutingUser, ItemName;
   gatewayRequestIds
   | join kind=leftouter attributed on $left.RequestId == $right.OperationId
   | summarize
       Total        = count(),
       Matched      = countif(isnotempty(ExecutingUser)),
       MatchRatePct = round(100.0 * countif(isnotempty(ExecutingUser)) / count(), 1)
   ```
   - `MatchRatePct` is **the one number** we can't get without a live tenant.
   - *Tip:* widen `ago(2d)` if your RequestIds are older than two days.
3. **Optional DirectQuery split** — if some of your RequestIds are DirectQuery and
   some are refresh, tag them in a second column so we learn whether DirectQuery
   attributes (the open question). Ask and I'll hand you the tagged-datatable variant.

### Step 1.8 — File the result as a pilot report
When you have the number, **don't summarize it in chat** — file it so every gateway's
result is captured in the same shape and we can compare across the fleet. In the repo,
go to **Issues → New issue → "Pilot report"** and fill the form. If you'd rather paste
it here, copy this block and fill it in — it maps 1:1 to the issue form:

```text
Environment:            <gateway version, Fabric SKU, standard/VNet, node count>
Identity-join result:   <matched | partially matched | did NOT match | didn't test>
Match rate (Step 1.7):  <MatchRatePct>%  (Matched <n> of <Total>)
DirectQuery attributed: <yes | no | didn't test>
EvaluationContext:      <starts with { (JSON) | base64 blob | empty>   (from TASK 2)
Schema corrections:     <any column name that differed, or "none">
Notebooks/collectors:   <ran 01→02→03? errors? or "didn't run">
Anything else:          <coverage gaps, surprises, cmdlet corrections>
```

That's the big test. Everything else below is quick copy-paste info gathering.

---

## TASK 2 — Send me a few real log rows (5 min)

1. From the `QueryStartReport...` file you opened in Step 1.4, copy the **header row
   + 3 data rows** into your reply to me.
2. I specifically need to see the **`EvaluationContext`** column value. It will look
   like one of these:
   - starts with `{` → it's plain JSON (good).
   - a long blob of letters/numbers with no spaces → it's base64 (also fine).
   - empty → that row is a non-Fabric workload.
3. **Redact anything sensitive** (server names, secrets) — replace with `XXXX`.
   Keep the `EvaluationContext` structure intact so I can see its shape.

*Why:* I coded the parser to handle both JSON and base64, but I want to confirm
which one your gateway actually produces and what the exact field names inside it are.

---

## TASK 3 — Confirm the guest-list column names (2 min)

In the same KQL window from Step 1.5, run:
```kql
PowerBIDatasetsWorkspace
| getschema
| project ColumnName, ColumnType
```
Copy me the list of column names it returns.

*Why:* I want to make sure the columns I join on (`OperationId`, `ExecutingUser`,
`ItemId`, `ItemName`) are named exactly that in your tenant.

---

## TASK 4 — Confirm a PowerShell command name (2 min)

On the gateway Windows machine (or any machine with the gateway PowerShell module):
1. Open **PowerShell 7** (search "pwsh" in the Start menu).
2. Paste and run:
   ```powershell
   Get-Command -Module DataGateway | Select-Object Name
   ```
3. Copy me the list of command names it prints.

*Why:* I guessed one command name (`Get-DataGatewayClusterDatasource`) and marked it
"unverified." This tells me the real names so the inventory collector is correct.

---

## TASK 5 — Describe your gateways in plain words (1 min)

Just answer these in your reply — no tools needed:
1. Are your gateways **"on-premises data gateway (standard)"** or **"VNet"** gateways?
   (VNet ones run in Azure with no server you log into.)
2. Is it **one gateway**, or a **cluster** (several gateways grouped together)?
3. Roughly **how many** gateways total do you have? (1? 3? 20?)

*Why:* the "fleet view" and "is one node overloaded" features only matter if you have
multiple gateways. And we skipped VNet in v1 — if you're mostly VNet, we re-plan.

---

## TASK 6 — Peek at the alerting screen (5 min, optional)

1. In Fabric, click **+ New → Activator** (or find **Activator** in the create menu).
2. Try to create **one** simple rule from `starter/alerting/activator-rules.json`
   (the `gateway-offline` one): pick a data source, set a condition, pick an action
   (like send a Teams message).
3. You don't have to finish it. Just take a **screenshot** of the rule-building
   screen and send it to me.

*Why:* Microsoft doesn't fully document the exact rule format. Seeing your actual
screen lets me write the other rules to match what you really see.

---

## The most important advice (please read)

**Do TASK 1 first — including the Step 1.7 match rate — and stop there if you're short
on time.** That number decides what's worth building next. We have already built a lot
of well-grounded design and code; the single highest-value thing now is *proof from your
real environment*, not more features.

**File the Step 1.8 report block even if it's partial.** A one-gateway result with a
real match rate beats a long message with none. If three teammates each run Task 1 on a
different gateway and file the block, we get a fleet-wide picture in the same shape —
that's exactly what turns the `[Unverified]` labels into verified ones and builds v3 on
solid ground.

---

## After the pilot — going from "proven" to "deployed"

This guide only validates the **flagship capability** (the identity join). Once
Task 1 succeeds, the full end-to-end deployment path is:

1. **Deploy order** — follow the 6-step sequence in the [repo README](../README.md#deploy-order)
   (stand up Lakehouse + Eventhouse → deploy `starter/collectors/*.ps1` →
   run `starter/notebooks/01→02→03` → enable Workspace Monitoring → add KQL +
   Activator rules).
2. **Graduate reference → production** — work through
   [`research/phase5_validation.md`](../research/phase5_validation.md): the U1–U16
   confirmation matrix (every `[Unverified]`/`[Assumption]` with a test and a
   fix) and the per-differentiator acceptance tests. Completing it is the
   documented go/no-go gate for production.
3. **Record what you found** — capture the results in a new `docs/VALIDATED.md`
   (gateway version, EvaluationContext encoding, attribution match-rate,
   DirectLake eligibility) so the next person forking this repo inherits
   verified facts instead of `[Unverified]` labels.

If you're deciding between building this tool from scratch vs. adopting Microsoft's
FPM first, read [`docs/DEPLOYMENT-DECISION.md`](DEPLOYMENT-DECISION.md) before step 1.
