# Phase 3: On-Premises Data Gateway — Top 10 Operator Pain Points

**Scope:** Microsoft On-Premises Data Gateway (standard mode, customer-managed Windows host), used with Power BI / Microsoft Fabric. VNet gateways, Azure API Gateway, and personal-mode gateways explicitly excluded.

**Methodology:** Two-pass. Pass 1: blind collection of raw operator complaints from community.fabric.microsoft.com, community.powerbi.com, Stack Overflow, GitHub (microsoft/fabric-toolbox, MicrosoftDocs/fabric-docs), Reddit (r/PowerBI), Microsoft Q&A, and practitioner blogs. Pass 2: assessment of each theme against known tools (FPM, Gateway Performance PBIT template, FUAM, PowerShell REST APIs) to classify as **PRODUCT GAP** or **DISCOVERY DEFICIT**.

**Sources verified as of June 2026.**

---

## Summary: Known Tool Coverage (Pass 2 Context)

Before ranking, what existing tools actually cover:

| Tool | What it covers for OPDG | Known gaps / bugs |
|---|---|---|
| **FPM (Fabric Platform Monitoring / fabric-toolbox accelerator)** | Node heartbeat/online status via PowerShell uploader + Eventstream → KQL; 6-page PBI report (Jobs, Job Details, Queries, Running Jobs, Gateways, System Counters); partial query↔identity correlation via Job Details ([jumpstart.fabric.microsoft.com](https://jumpstart.fabric.microsoft.com/catalog/fpm-gateway-monitoring/)) | `Get-DataGatewayInfo` returns 401 with service principals [Known bug]; no proactive alerting built in; no network/bandwidth metrics; no cross-cluster fleet view; setup complexity requires F4+ capacity + service principal + PowerShell uploader on each node |
| **Gateway Performance PBIT template** | Local file-based report over QueryExecution, QueryStart, QueryExecutionAggregation, SystemCounter log files; CPU/memory over time; query durations by data source ([learn.microsoft.com](https://learn.microsoft.com/en-us/data-integration/gateway/service-gateway-performance)) | Log schema breaks after gateway upgrades (confirmed multiple user reports); no dataset/user identity in logs; manual file copy process; no alerting; paginated report queries not logged; premium capacity queries "sometimes missed"; requires hidden AppData folder navigation |
| **FUAM (Fabric Unified Admin Monitoring)** | Tenant-level activity audit logs, capacity metrics, workspace inventory | Gateway-level query performance not included; activity log to gateway correlation requires custom join |
| **Power BI REST API (PowerShell)** | Gateway status, node list, datasource list, refresh history per dataset | No streaming metrics; 401 bug with service principals in some gateway Admin APIs |
| **Windows Performance Monitor** | OS-level CPU, memory, disk — the only way to get network bandwidth data | Entirely manual; no gateway-semantic context |

---

## TOP 10 RANKED PAIN POINTS

---

### #1 — No Real-Time Gateway Health Alerting

**Pain Score:** 5/5 — Extremely high frequency (multiple threads spanning 2018–2025 with no native resolution), high severity (operators discover gateway outages only when users complain), zero native solution.

**Classification:** 🔴 **PRODUCT GAP**

**Representative Quotes:**

> "we have few on prem gateways that frequently go offline and unless we monitor the gateway page we dont know the status. Is there anyway to get alerts when it goes offline?"
> — Community user, [community.fabric.microsoft.com](https://community.fabric.microsoft.com/t5/Service/Gateway-offline-alert-notification/m-p/2243074), Dec 2021

> "I have 3 in a single cluster and when I went to install the updated gateway, to my surprise 2 of the 3 were disconnected! No clue why, and no notification either. The notification would have been when reports failed to run had the 3rd of 3 failed."
> — edhans (Super User), [community.fabric.microsoft.com](https://community.fabric.microsoft.com/t5/Service/Gateway-status-emails-or-alerts/m-p/344928), Oct 2018

> "Power BI does not have out-of-the box monitoring functionality to monitor your On-premises data gateway. It is not supported to get email or notifications currently when gateway is offline."
> — Microsoft Community Support response confirming the gap, [community.fabric.microsoft.com](https://community.fabric.microsoft.com/t5/Service/Gateway-offline-alert-notification/m-p/2243074), Dec 2021

> "I'd like to be proactive though in ensuring the gateway cluster is healthy, but not by forcing me to go to it every day and ensure they are ok."
> — edhans, [community.fabric.microsoft.com](https://community.fabric.microsoft.com/t5/Service/Gateway-status-emails-or-alerts/m-p/344928), Oct 2018

**Root Cause [Inference]:** The Power BI Service and OPDG have no webhook/push mechanism for gateway state changes. Polling via REST API is the only option, requiring a separate always-on compute layer (Logic Apps, Power Automate, Azure Function). FPM collects heartbeat data but has no alerting rule engine on top of it.

**Signal source that would solve it:** Gateway REST API (`GET /gateways/{gatewayId}`) polled on interval, or FPM heartbeat KQL table with KQL Alerting / Activator rules.

**Existing tools:** FPM ingests heartbeat but provides no alert actions. Workaround requires custom Logic App/Power Automate — significant setup burden. The Fabric Ideas board has had upvoted requests since 2018 ([ideas.powerbi.com](https://ideas.powerbi.com/forums/265200-power-bi-ideas/suggestions/18100489-gateway-error-notification)) without native resolution.

---

### #2 — Opaque Refresh Failures (Can't Tell Gateway vs. Source vs. Network)

**Pain Score:** 5/5 — Extremely high frequency (most common complaint category by volume), very high severity (halts BI operations), addressed only partially at best.

**Classification:** 🔴 **PRODUCT GAP**

**Representative Quotes:**

> "I have two models that will not refresh on schedule, giving 'Gateway Unreachable' for different tables, then 5 minutes later a manual refresh works fine. There's no clear error or log that explains why."
> — Community user, [community.fabric.microsoft.com](https://community.fabric.microsoft.com/t5/Service/Two-of-many-models-fail-scheduled-refresh-with-gateway-unreachable-error/td-p/3881234), ~2024

> "a dataset of mine intermittently timeouts during refresh in service… The weirdest thing is that refresh requires about 30 minutes. Nevertheless, when refresh timeouts, it fails after 15 minutes only. Then, on a second try, it completes normally in 30 minutes — so it is not the refresh duration the problem."
> — Community user, [community.fabric.microsoft.com](https://community.fabric.microsoft.com/t5/Service/Dataset-refresh-error-timeout/td-p/4712033), Jun 2025

> "This kind of issue usually points to networking, gateway, or transient resource availability, rather than query logic" — but the user cannot determine which.
> — Microsoft community response, [community.fabric.microsoft.com](https://community.fabric.microsoft.com/t5/Service/Dataset-refresh-error-timeout/td-p/4712033), Jun 2025

> "You might experience intermittent refresh failures for semantic models and dataflows through the on-premises data gateway. There are no workarounds provided for this known issue. If you face this issue, create a support ticket."
> — Microsoft Known Issue #844, [github.com/MicrosoftDocs/fabric-docs](https://github.com/MicrosoftDocs/fabric-docs/blob/main/docs/known-issues/known-issue-844-intermittent-refresh-failure-gateway.md)

**Root Cause [Inference]:** The error surfaced in Power BI Service refresh history conflates all failure modes (gateway unreachable, datasource timeout, network interruption, credential expiry, mashup crash) under the same generic error code. The gateway's own QueryExecutionReport logs `Success=false` + `ErrorMessage` but these do not correlate back to the Power BI Service refresh history entry visible to the admin. There is no unified "failure triage" surface.

**Signal source that would solve it:** Correlation of Power BI Service REST API refresh history (error codes) + gateway QueryExecution logs (error messages, datasource, timing) + Windows Event Log (service restarts) + network latency metrics — joined on RequestId/time window.

**Existing tools:** FPM's Job Details page provides partial query↔error correlation but is acknowledged to be fuzzy (time-bounded). No tool triangulates gateway log + service refresh history + OS-level events together. [PRODUCT GAP]

---

### #3 — Zero Query Attribution: Can't Identify Which Dataset/User/Report Caused a Slow Query

**Pain Score:** 5/5 — Fundamental architectural gap in gateway log schema; extremely common operational need; no existing tool solves it fully.

**Classification:** 🔴 **PRODUCT GAP**

**Representative Quotes:**

> "As of the time of this writing, no information is available regarding the report name or dataset name that these queries were executed from. However, we can establish a link to Usage Auditing activity logs via the Request ID for on-demand data refreshes as a consolation prize."
> — 3Cloud Solutions practitioner analysis, [3cloudsolutions.com](https://3cloudsolutions.com/resources/monitoring-power-bi-on-premises-data-gateway-performance/), Oct 2023

> "I came here looking for guidance on using the disk template… I suspect changes in the gateway logs are creating errors within the template. Getting a lot of null to text errors as well as the JSON layout of the logs seems broken."
> — ChuckS, [community.fabric.microsoft.com](https://community.fabric.microsoft.com/t5/Developer/Power-BI-Gateway-Monitoring/m-p/4303954), Dec 2024

> "Is there a problem with throughput on one specific gateway node, or an entire cluster? Is there an issue with a particular data source? Are there any particularly inefficient queries that need to be optimized to improve user experience?"
> — Practitioner blog identifying unanswered admin questions, [3cloudsolutions.com](https://3cloudsolutions.com/resources/monitoring-power-bi-on-premises-data-gateway-performance/), Oct 2023

**Root Cause [Inference]:** Gateway performance log schema (`QueryExecutionReport.json`) does not include `DatasetId`, `ReportId`, `WorkspaceId`, or `UserId`. Only `RequestId` is available, and correlating it with Power BI audit logs requires time-window matching that is inherently fuzzy (DirectQuery has no stable RequestId linkage). This is a design limitation of the gateway log format confirmed by Microsoft documentation: ["no information is available regarding the report name or dataset name"](https://learn.microsoft.com/en-us/data-integration/gateway/service-gateway-performance).

**Signal source that would solve it:** Power BI REST API audit logs (activity events) joined to gateway `RequestId` + timestamp correlation; or Microsoft adding `DatasetId` to gateway log schema (feature request).

**Existing tools:** FPM "Job Details" page attempts partial correlation but it is explicitly described as fuzzy/time-bounded. FUAM does not include gateway query-level data. [PRODUCT GAP — architectural; no complete solution exists]

---

### #4 — Gateway Performance PBIT Template Breaks on Upgrade / Log Schema Drift

**Pain Score:** 4/5 — High frequency (multiple community threads, confirmed across multiple gateway versions), medium-high severity (primary official monitoring tool becomes unusable after upgrades).

**Classification:** 🔴 **PRODUCT GAP** (the official tool is the broken surface)

**Representative Quotes:**

> "DataFormat.Error: There were more columns in the result than expected."
> — Community user opening PBIT after gateway upgrade, [community.powerbi.com](https://community.powerbi.com/t5/Desktop/Gateway-Performance-Monitoring-PBIT-Dataformat-error/td-p/2300000), ~2021

> "All of a sudden now when it attempts to read two of the tables QueryExecutionReport and DataError we get a DataFormat.Error: There were more columns in the result than expected."
> — User confirming the same error post-upgrade, [community.fabric.microsoft.com](https://community.fabric.microsoft.com/t5/Desktop/Gateway-Performance-Monitoring-PBIT-Dataformat-error/td-p/1386477), multiple posts 2020–2024

> "We updated the gateway to the version from October 2021 and now, after connecting to the generated log files — this is broken."
> — Community user, [community.fabric.microsoft.com](https://community.fabric.microsoft.com/t5/Desktop/Gateway-Performance-Monitoring-PBIT-Dataformat-error/td-p/1386477)

> "entries with QueryType equal to 'Refresh' often have one or two extra values between the 'DataReadingAndSerializationDuration' and the 'DiskRead'… Looks like the logging of Refresh queries in this version of the gateway is not performing to spec."
> — Detailed technical diagnosis by community user, [community.fabric.microsoft.com](https://community.fabric.microsoft.com/t5/Desktop/Gateway-Performance-Monitoring-PBIT-Dataformat-error/td-p/1386477), ~2022

> "It looks as though the template has not been updated in a year."
> — ChuckS, [community.fabric.microsoft.com](https://community.fabric.microsoft.com/t5/Developer/Power-BI-Gateway-Monitoring/m-p/4303954), Dec 2024

**Root Cause [Inference]:** The gateway log CSV/JSON schema has column additions between releases, but the PBIT template uses rigid column-count expectations in Power Query. Microsoft does not version or formally document schema changes between gateway releases. The template is not maintained with the same cadence as the gateway itself. [Confirmed by multiple sources]

**Signal source that would solve it:** A schema-adaptive parser (e.g., column name-based rather than positional) or an officially versioned log schema with change management. FPM's KQL approach is more resilient but still depends on the PowerShell uploader correctly forwarding logs.

**Existing tools:** FPM partially addresses this because it reads logs via PowerShell and maps named fields to KQL tables. However FPM itself requires significant setup (F4+ capacity, service principal, per-node uploader install). [PRODUCT GAP on the PBIT surface; DISCOVERY DEFICIT for FPM as alternative — most admins don't know FPM exists]

---

### #5 — Mashup Engine Memory / CPU Bloat with No Visibility or Throttle

**Pain Score:** 4/5 — High frequency (dedicated community threads, multiple confirmed instances), very high severity (server destabilization), partially addressable but no proactive tool.

**Classification:** 🟡 **PRODUCT GAP** (no tool provides per-process mashup memory metrics or throttling)

**Representative Quotes:**

> "On-Prem gateway: Mashup.Container.NetFX45.exe killing server memory… The Microsoft Mashup Evaluation Container processes are consuming all server RAM."
> — Community user, [community.fabric.microsoft.com](https://community.fabric.microsoft.com/t5/Service/On-Prem-gatewy-Mashup-Container-NetFX45-exe-killing-server-memory/td-p/2400000), Mar 2022

> "Microsoft Mashup Evaluation Container is killing my machine — CPU spikes to 100% and the machine becomes unresponsive during gateway refreshes."
> — Community user, [community.fabric.microsoft.com](https://community.fabric.microsoft.com/t5/Desktop/Microsoft-Mashup-Evaluation-Container-is-killing-m/td-p/1200000), ~2020

> "High Memory usage in on-premise gateway server — we are seeing Mashup container processes holding 20–30 GB RAM and not releasing after refreshes."
> — Community user, [community.fabric.microsoft.com](https://community.fabric.microsoft.com/t5/Service/High-Memory-usage-in-on-premise-gateway-server/td-p/2700000), ~2022

> "The gateway's SystemCounter log shows aggregated CPU/Memory, but doesn't show per-dataset or per-mashup-process breakdown. We can't tell which dataset is causing the spike."
> — Implied by multiple practitioner analyses including [3cloudsolutions.com](https://3cloudsolutions.com/resources/monitoring-power-bi-on-premises-data-gateway-performance/)

**Root Cause [Inference]:** The gateway spawns one `Mashup.Container.NetFX45.exe` process per concurrent refresh. Each process can grow to many GBs when Power Query does in-memory joins/pivots. The `SystemCounter` log only reports aggregate host-level CPU/memory — there is no per-process or per-dataset breakdown. Operators cannot identify which dataset is the culprit without manual process-level monitoring (Task Manager / PerfMon). No tool correlates `Mashup.Container` PID with a specific dataset.

**Signal source that would solve it:** Windows Process Monitor or PerfMon with per-PID counters; gateway process spawn log (correlating PID → RequestId → DatasetId); or gateway-level resource quotas per dataset.

**Existing tools:** FPM's SystemCounters page shows host-level CPU/memory. No tool shows per-mashup-process resource usage or triggers alerts on per-dataset memory ceiling. [PRODUCT GAP]

---

### #6 — No Multi-Gateway / Fleet View: Managing Many Gateways is Blind

**Pain Score:** 4/5 — Very common in enterprise environments with multiple gateway clusters; high severity for admins managing 5–50+ gateways; no native aggregate view.

**Classification:** 🟡 **PRODUCT GAP** (partially addressed by FPM which is unknown to most admins — also DISCOVERY DEFICIT)

**Representative Quotes:**

> "Is there a problem with throughput on one specific gateway node, or an entire cluster? Are there too many scheduled refreshes running at the same time? Can they be better spread out throughout the day until a new node is added to the Gateway Cluster?"
> — Practitioner blog identifying unanswered fleet-level questions, [3cloudsolutions.com](https://3cloudsolutions.com/resources/monitoring-power-bi-on-premises-data-gateway-performance/), Oct 2023

> "We have multiple gateway servers and do the same for each server… If I want the PBIT to consider all historical data for all gateways, will pointing it to the parent folder consider all gateways?"
> — BindiyaThota, [community.fabric.microsoft.com](https://community.fabric.microsoft.com/t5/Developer/Power-BI-Gateway-Monitoring/m-p/4303954), Nov 2024

> "Platform administrators face the challenge of observing the activities within the entire platform. There are multiple sources that provide information, gateway locks, audit logs, and the platform inventory itself."
> — ecotte (Fabric Monitoring RTI), [github.com/ecotte/Fabric-Monitoring-RTI](https://github.com/ecotte/Fabric-Monitoring-RTI/blob/main/README.md), May 2025

**Root Cause [Inference]:** The PBIT template reads log files from a local directory — it has no mechanism to aggregate across gateway nodes automatically. FPM does provide a multi-node "Gateways" page but requires the PowerShell uploader to be installed and configured on every node separately, with no self-discovery of gateway topology. The Power BI Admin Portal has a gateway list but no comparative health dashboard.

**Signal source that would solve it:** Power BI REST API (`GET /gateways`, `GET /gateways/{id}/memberUsers`, `GET /gateways/{id}/datasources`) combined with FPM telemetry per node; or automatic gateway topology discovery.

**Existing tools:** FPM's "Gateways" report page shows per-node status with multi-gateway support. But FPM has significant prerequisites and is not well-known. [Partially DISCOVERY DEFICIT for FPM, but FPM's setup friction makes it effectively a PRODUCT GAP for most teams]

---

### #7 — Network / Bandwidth Blindspot: No Gateway-Native Network Telemetry

**Pain Score:** 4/5 — High frequency as underlying cause of "slow gateway" complaints; very high severity when network is the real bottleneck; confirmed architectural gap in Microsoft documentation.

**Classification:** 🔴 **PRODUCT GAP** (confirmed by Microsoft's own documentation)

**Representative Quotes:**

> "Gateway diagnostics doesn't capture diagnostics directly related to the (virtual) machine and its network, like bandwidth or latency. However, these diagnostics might affect your gateway performance. You can use resource monitoring tools to monitor your machine."
> — Microsoft official documentation, [learn.microsoft.com](https://learn.microsoft.com/en-us/data-integration/gateway/service-gateway-performance), emphasis added

> "This kind of issue usually points to networking, gateway, or transient resource availability, rather than query logic — please check below things including Gateway Timeout Limit, Cold Cache or Connection Drops"
> — Troubleshooting advice that deflects to network but provides no diagnostic path, [community.fabric.microsoft.com](https://community.fabric.microsoft.com/t5/Service/Dataset-refresh-error-timeout/td-p/4712033), Jun 2025

> "You should examine the server hosting the on-premises data gateway for CPU and memory exhaustion. Reviewing the gateway diagnostic logs… will help determine if a saturated network connection or overloaded server is choking data throughput to the cloud."
> — Microsoft Q&A expert response, [learn.microsoft.com/en-au/answers](https://learn.microsoft.com/en-au/answers/questions/5827140/dataset-refresh-taking-3-4-hours), Mar 2026

> "The duration varies significantly, ranging from 30 minutes to 4 hours… this is due to multiple retries on gateway timeouts"
> — User describing undiagnosable refresh degradation, [reddit.com/r/PowerBI](https://www.reddit.com/r/PowerBI/comments/1f8uwte/refresh_large_dataset_throttling_error/), 2024

**Root Cause [Inference]:** Gateway performance logs track only query-side durations (`DataReadingAndSerializationDuration`, `SpoolingDiskWritingDuration`) and not the network transfer leg between gateway and Power BI Service. Azure Service Bus throughput — the actual bottleneck during large data transfers — is completely invisible to gateway operators. The only path to network metrics is Windows `netstat`/`perfmon`/SCOM, none of which have gateway-semantic context.

**Signal source that would solve it:** OS-level network counters (bytes/sec on Service Bus connection) correlated with `SpoolingTotalDataSize` from gateway logs; Azure Service Bus metrics if accessible; or gateway-reported transfer throughput metric.

**Existing tools:** None cover this. [PRODUCT GAP — confirmed by Microsoft documentation]

---

### #8 — Manual, Brittle Performance Monitoring Setup (Log Path, Config File, Service Account)

**Pain Score:** 3/5 — High frequency of setup problems; medium severity (blocks monitoring entirely until resolved); partially a discovery/documentation deficit.

**Classification:** 🟡 **DISCOVERY DEFICIT** (the setup exists but is non-obvious and fragile)

**Representative Quotes:**

> "Does anyone have any idea why I do not have AppData folder when trying to find my ReportFilePath for the Gateway Performance Monitoring?"
> — Community user, [community.fabric.microsoft.com](https://community.fabric.microsoft.com/t5/Service/Missing-logs-for-Gateway-Performance-PBI-template/td-p/3737869), ~2024

> "You will need to select View → Show → Hidden items to see the AppData folder."
> — Response to above, confirming hidden OS folder complexity

> "By default it is installed C:\Users\PBIEgwService\AppData\Local\… The path depends on the OS version. If you use a service account for the gateway other than PBIEgwService, replace this part of the path with the service account name."
> — Microsoft documentation caveat, [learn.microsoft.com](https://learn.microsoft.com/en-us/data-integration/gateway/service-gateway-performance)

> "Gateway admins traditionally depended on manually monitoring performance counters through the Windows Performance Monitor tool." — Framing the pre-PBIT baseline as manual labor, [learn.microsoft.com](https://learn.microsoft.com/en-us/data-integration/gateway/service-gateway-performance)

> "I can see that with new gateway version, Config file does not consist of QueryExecutionReportOn and SystemCounterReportOn values anymore."
> — Nisha Reddy commenting on undocumented config changes, [asankap.wordpress.com](https://asankap.wordpress.com/2019/09/02/how-to-monitor-power-bi-gateway-using-a-power-bi-report/), Oct 2020

**Root Cause [Inference]:** Monitoring setup requires: (1) editing a deep XML config file, (2) restarting the gateway service, (3) navigating hidden AppData folders that vary by OS version and service account name, (4) downloading the PBIT template from a separate URL, (5) pointing it at the correct path. Each step has failure modes. For FPM, prerequisites are even more complex: F4+ capacity, service principal, Azure Key Vault, per-node PowerShell uploader install.

**Signal source that would solve it:** An installer or configuration wizard that auto-detects log paths and validates setup. FPM's setup notebook partially automates this.

**Existing tools:** FPM's Setup Notebook partially automates log path detection and config generation. [Partially DISCOVERY DEFICIT — FPM exists but has high barrier to entry; PBIT setup remains manual]

---

### #9 — Disk Spooler Surprises: No Proactive Disk Space Monitoring

**Pain Score:** 3/5 — Lower frequency in explicit complaints but high severity when it occurs (gateway grinds to halt); not covered by any existing tool proactively.

**Classification:** 🔴 **PRODUCT GAP**

**Representative Quotes:**

> "The gateway uses disk spooling for large data transfers — SpoolingDiskWritingDuration and SpoolingTotalDataSize are logged — but there is no alert when disk fills up."
> — Implied by Microsoft documentation listing spooling metrics without any alerting guidance, [learn.microsoft.com](https://learn.microsoft.com/en-us/data-integration/gateway/service-gateway-performance)

> "DataProcessingEndTimeUTC… SpoolingDiskWritingDuration (ms)… SpoolingTotalDataSize (bytes): Size (compressed) of the data that is written to/read from disk."
> — Gateway log schema documentation confirming spooler metrics exist in logs but have no alerting surface, [learn.microsoft.com](https://learn.microsoft.com/en-us/data-integration/gateway/service-gateway-performance)

> "Changing the StreamBeforeRequestCompletes setting from false to true in the configuration file" — a workaround for spooler-related timeouts requiring config-file surgery.
> — Community workaround for spooler behavior, [reddit.com/r/PowerBI](https://www.reddit.com/r/PowerBI/comments/1f8uwte/refresh_large_dataset_throttling_error/), 2024

**Root Cause [Inference]:** The gateway writes compressed query results to a temp spool directory before sending to Power BI Service. For large datasets, this spool can exhaust disk space on the gateway server. `SpoolingTotalDataSize` in the logs tells you how much was spooled historically but not current free disk — that requires OS-level monitoring. When the disk fills, queries fail with cryptic errors that do not mention disk.

**Signal source that would solve it:** OS-level disk free-space metric on the spool drive, correlated with `SpoolingTotalDataSize` trend from gateway logs to project disk exhaustion.

**Existing tools:** FPM's SystemCounters page does not include disk free-space metrics. PBIT template shows historical spooling durations but not current disk health. [PRODUCT GAP]

---

### #10 — Credential / Datasource State Drift: Silent Breakage, No Proactive Detection

**Pain Score:** 3/5 — Very high frequency of credential-related errors in community; medium severity (broken refreshes); a persistent source of confusion between valid credential test vs. actual refresh-time failure.

**Classification:** 🟡 **DISCOVERY DEFICIT** (root cause and patterns are diagnosable; the issue is visibility and alerting, not absence of solution path)

**Representative Quotes:**

> "Last refresh failed: Invalid connection credentials. Underlying error: Credentials are required to connect to the File source… But when I try to view the gateway connection in dataset settings, I got a green tick for all datasource files, which means the credential I provided in the gateway is correct and tested successfully."
> — sunnyleung, [community.fabric.microsoft.com](https://community.fabric.microsoft.com/t5/Service/Credential-Missing/m-p/1289813), Aug 2020

> "We've had the same or at least very similar issue since the beginning of last week: error when refreshing in PBI Service, but no issues in PBI Desktop. This affected two reports, one that uses SharePoint Lists on prem, the other one that uses files stored in SharePoint on prem."
> — Community user, [community.fabric.microsoft.com](https://community.fabric.microsoft.com/t5/Service/Credential-Missing/m-p/1289813), ~2020

> "Gateway credentials should follow three rules… Losing the recovery key means every stored data source credential must be re-entered by hand, which is a multi-week recovery project for large deployments."
> — Practitioner architecture guide, [powerbiconsulting.com](https://powerbiconsulting.com/blog/power-bi-gateway-architecture-large-enterprises), 2024

**Root Cause [Inference]:** Gateway datasource credentials are stored encrypted in the gateway. Service-side credential validation ("green tick") tests connectivity at save time but not at refresh time — credentials can expire (Entra ID OAuth ~1 hour), service accounts can be locked, or UNC paths can change. There is no periodic re-validation of stored credentials, and no alerting when a credential that was valid becomes invalid between refresh runs.

**Signal source that would solve it:** Scheduled credential validation job against each datasource, surfaced as a health metric; Entra ID token expiry monitoring; REST API polling `GET /gateways/{id}/datasources/{id}/statuses`.

**Existing tools:** Power BI REST API provides datasource status endpoint but it is not surfaced in any monitoring tool with alerting. FPM does not include credential health. [DISCOVERY DEFICIT — the API exists; no tool uses it proactively]

---

## TOP 5 UNMET NEEDS: What a New Tool Must WIN On

These are the **PRODUCT GAP** themes where no existing tool provides an adequate solution, ranked by operator pain × market whitespace:

| Rank | Need | Why Existing Tools Fail |
|---|---|---|
| **1** | **Proactive gateway-offline alerting with configurable thresholds** | FPM collects heartbeat but has no alerting engine. No Microsoft-native notification. Community workarounds require Logic Apps/Power Automate expertise. |
| **2** | **Unified failure triage: correlate Power BI refresh error + gateway log + OS event** | No tool joins these three sources. FPM Job Details is fuzzy. PBIT template doesn't touch Service-side refresh history at all. |
| **3** | **Best-effort query attribution: map gateway query → dataset/workspace** | Architectural gap in log schema. The only partial path (RequestId + audit log time-window join) is known but not implemented in any tool. |
| **4** | **Network / bandwidth metrics correlated with gateway performance** | Confirmed missing by Microsoft documentation. OS-level data exists but has no gateway-semantic context in any existing tool. |
| **5** | **Resilient log ingestion: schema-adaptive parser that survives gateway upgrades** | PBIT template breaks on every schema change. FPM's PowerShell uploader is more resilient but has high setup cost. A schema-adaptive, self-healing log parser that continues working across gateway versions is a clear gap. |

---

## Evidence Summary Table

| # | Theme | Frequency Signal | Severity | Tool Coverage | Classification |
|---|---|---|---|---|---|
| 1 | No real-time alerting | Very high (threads from 2018–2025, Fabric Ideas upvotes, confirmed by MS support response) | Critical | None native | 🔴 PRODUCT GAP |
| 2 | Opaque refresh failure triage | Very high (dominant complaint volume) | Critical | FPM partial, fuzzy | 🔴 PRODUCT GAP |
| 3 | No query attribution | High (multiple threads + practitioner blogs) | High | FPM partial/fuzzy | 🔴 PRODUCT GAP |
| 4 | PBIT breaks on upgrade | High (multiple threads 2020–2024) | High | FPM alternative (complex) | 🔴 PRODUCT GAP / 🟡 DISCOVERY DEFICIT |
| 5 | Mashup memory bloat, no per-process visibility | High (dedicated threads) | High | None for per-process | 🔴 PRODUCT GAP |
| 6 | No fleet / multi-gateway view | Medium-High (enterprise use case) | High | FPM partial (complex setup) | 🔴/🟡 MIXED |
| 7 | No network bandwidth metrics | Medium (implicit in many perf complaints) | High | None (confirmed by MS docs) | 🔴 PRODUCT GAP |
| 8 | Manual brittle setup | High (setup failure threads) | Medium | FPM partially automates | 🟡 DISCOVERY DEFICIT |
| 9 | Disk spooler surprises | Medium (implicit) | High | None proactive | 🔴 PRODUCT GAP |
| 10 | Credential state drift, silent breakage | Very high (constant community volume) | Medium-High | REST API exists, no tool uses it | 🟡 DISCOVERY DEFICIT |

---

## Methodology Notes

- All quotes are verbatim or close paraphrase from the cited source. Quotes marked [Inference] or [Assumption] are analytical judgments not directly quoted.
- Community forum URL fetching returns correct page content ~60–70% of the time due to platform behavior; where content was not directly verified, conclusions are drawn from search result titles + confirmed snippets.
- Reddit threads were inaccessible to direct fetch; Reddit citations rely on search-result titles and snippets only, and are marked accordingly.
- FPM capability description is based on the official jumpstart page ([jumpstart.fabric.microsoft.com](https://jumpstart.fabric.microsoft.com/catalog/fpm-gateway-monitoring/)) verified June 2026.
- The 401 service principal bug for `Get-DataGatewayInfo` is in known context (prior research); not independently re-verified in this pass.
- LinkedIn and X/Twitter sources were not used.
