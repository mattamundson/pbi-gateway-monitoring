<!-- LinkedIn post — paste the body below the divider straight into LinkedIn.
     ~300 words / well under the 3,000-char limit. Swap <REPO-URL> for the real link before posting. -->

---

For years, Power BI / Fabric gateway operators have lived with a blind spot:

The gateway logs tell you a query was slow, spilled to disk, or failed — but never **who** ran it or **which dataset** caused it. So when the gateway is drowning at 9am, you can see *that* it's drowning, not *whose* refresh to go fix.

Turns out that blind spot isn't permanent.

The gateway's `RequestId` is the same value the Analysis Services engine calls `XmlaRequestId` — which Fabric **Workspace Monitoring** exposes as `OperationId`. Join on it and you inherit every field the gateway log lacks: the user, the dataset, the DAX text, engine CPU vs. gateway time.

And this isn't a hack I'm hoping works — it's in Microsoft's own documentation. The *Semantic model operation logs* reference literally says:

> "OperationId — … Same as XmlaRequestId."

Chris Webb (Microsoft / Fabric CAT) showed the other half: `XmlaRequestId` matches the gateway log's `RequestId`. Chain the two, and "query → identity attribution," long treated as impossible on the gateway's flat logs, just… falls out. No gateway changes. No custom instrumentation.

What it unlocks:
🔹 Top offending users by gateway load
🔹 Top datasets by spool pressure
🔹 Failed queries **with their owner** — the report ops teams have wanted for years

Honest caveats: needs Workspace Monitoring enabled; covers Fabric semantic-model refresh + query (DirectQuery-through-gateway is an open question I'd love someone to confirm); Dataflow Gen1 and Paginated Reports don't flow through it.

I've open-sourced the full write-up + a copy-paste KQL query + a reference implementation. If you run an on-prem gateway, I'd genuinely love to know your real-world **match rate**.

👉 <REPO-URL>

#PowerBI #MicrosoftFabric #DataEngineering #Analytics #BusinessIntelligence
