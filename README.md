# Power BI / Fabric Gateway Performance Monitoring Kit

An end-to-end toolkit for monitoring **On-Premises Data Gateway** performance in Power BI / Microsoft Fabric — built on Microsoft's maintained **Fabric Platform Monitoring (FPM)** solution accelerator for collection, with an optional custom **ADLS → Fabric Lakehouse (PySpark/Delta) → DirectLake** analytics layer for retention and bespoke analysis.

> **Not an official Microsoft product.** This kit orchestrates and documents the use of the community FPM solution accelerator and standard Microsoft APIs. No official support; validate in a non-production environment first.

---

## What's in here

```
.
├── README.md                              ← you are here (start + sequence)
├── docs/
│   ├── gateway-monitoring-runbook.md      ← the full architecture & build runbook (Part A custom build + Part B hybrid)
│   └── FPM-Phase0-Deployment-Reference.md ← cited, step-by-step FPM deployment reference (prereqs, tenant settings, scripts, gotchas)
└── scripts/
    ├── Deploy-FpmGatewayNode.ps1          ← per-node deployer (folder layout, script sync, modules, setup, Task Scheduler import w/ SID+path rewrite)
    └── Test-FpmGatewayNode.ps1            ← per-node health validator (tasks, log freshness, config, gateway process) + fleet sweep
```

## Recommended sequence

1. **Read the decision first.** Open `docs/gateway-monitoring-runbook.md` → **Part B → B.0 (the gate)**. Confirm you have **Fabric F8+ capacity** (F16 recommended) and can provision a **service principal + Entra security group + Azure Key Vault**. If not, use **Part A** (pure-custom ADLS/PySpark build) instead.

2. **Stand up FPM (Phase 0).** Follow `docs/FPM-Phase0-Deployment-Reference.md`:
   - Prerequisites (SP, security group, Key Vault, RBAC, capacity).
   - Fabric tenant settings (note the **renamed** "Service principals can call Fabric public APIs").
   - Gateway **Admin** role for the SP.
   - Run the **Setup notebook** then the **Gateway Config notebook** → download `config.json`.

3. **Deploy to each gateway node.** On every node (PowerShell 7, elevated):
   ```powershell
   # dry run first — inspects rewritten Task Scheduler XML without registering tasks
   .\scripts\Deploy-FpmGatewayNode.ps1 -ConfigSourcePath .\config.json -WhatIfTasks

   # commit
   .\scripts\Deploy-FpmGatewayNode.ps1 -ConfigSourcePath .\config.json -RunAsUser "DOMAIN\svc-gwmon"
   ```
   The SP secret is **machine-bound** — run the deployer on every node; do not copy a populated config.

4. **Validate.** On each node, or as a fleet sweep:
   ```powershell
   .\scripts\Test-FpmGatewayNode.ps1                       # single node, colorized table + exit code
   .\scripts\Test-FpmGatewayNode.ps1 -JsonOut .\health.json # machine-readable

   # whole cluster from one console:
   Invoke-Command -ComputerName GW01,GW02,GW03 -FilePath .\scripts\Test-FpmGatewayNode.ps1 |
       Select Computer,GatewayId,Overall,Pass,Warn,Fail | Format-Table
   ```
   Green = heartbeat online within ~1 min, Queries page populating after a gateway job, System Counters within ~10 min.

5. **(Optional) Custom analytics — Part B of the runbook.** Shortcut FPM's Eventhouse/Lakehouse gateway tables into your own Fabric Lakehouse and run the PySpark medallion (silver/gold) + DirectLake model for long-term retention and bespoke decomposition (spool vs source-read, baseline-vs-outlier, capacity overlay). **Do not customize FPM items in place** — its updates may revert changes; build in your own lakehouse and shortcut in.

## Key caveats (read before you trust it)

- **VNet gateways are NOT supported** — on-prem data gateway only (scripts need local log-file access).
- **Network blind spot.** Gateway logs do not capture bandwidth/latency — the most common real bottleneck. Add OS-level NIC/disk/CPU telemetry separately (runbook Part A, Phase 10).
- **Live bug (June 2026):** `Get-DataGatewayInfo.ps1` can 401 via SP; fix merged — pull latest FPM `main` and ensure the SP has gateway Admin role ([GitHub issue #321](https://github.com/microsoft/fabric-toolbox/issues/321)).
- **Scripts not parse-tested on Windows here** — validate with `-WhatIfTasks` (deployer) on one node before cluster-wide rollout. Structure was statically checked; no live `pwsh` run was performed.

## Sources

- Fabric Platform Monitoring (maintained successor): https://github.com/microsoft/fabric-toolbox/tree/main/monitoring/fabric-platform-monitoring
- Microsoft Learn — Monitor & optimize gateway performance: https://learn.microsoft.com/en-us/data-integration/gateway/service-gateway-performance
- RuiRomano/pbigtwmonitor (deprecated predecessor): https://github.com/RuiRomano/pbigtwmonitor
- FUAM (tenant ops monitoring, complementary): https://github.com/microsoft/fabric-toolbox/tree/main/monitoring/fabric-unified-admin-monitoring

---

*Generated as a working kit. Treat the PowerShell as a starting point — review and test in your environment before production use.*

## License

MIT — see [LICENSE](LICENSE).
