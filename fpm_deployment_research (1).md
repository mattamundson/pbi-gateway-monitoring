# FPM Gateway Monitoring — Phase 0 Pilot Deployment Reference

**Source**: Microsoft Fabric Platform Monitoring (FPM) solution accelerator  
**GitHub repo**: [microsoft/fabric-toolbox — monitoring/fabric-platform-monitoring](https://github.com/microsoft/fabric-toolbox/tree/main/monitoring/fabric-platform-monitoring)  
**Scope**: Gateway Monitoring module only (Phase 0 pilot)  
**Last verified against repo**: main branch, June 2026  

> **⚠ CAUTION (from official README):** FPM is **not an official Microsoft product**. It is a community solution accelerator. No official Microsoft support is available, and there is a risk that things might break. See [README caution notice](https://github.com/microsoft/fabric-toolbox/tree/main/monitoring/fabric-platform-monitoring#introduction).

---

## Executive Summary

The Gateway Monitoring module of FPM streams heartbeat and log data from an On-Premises Data Gateway host machine to Microsoft Fabric using two Eventstreams, an Eventhouse (KQL database), a Power BI report, and a set of PowerShell scripts running on the gateway server. Prerequisites include a registered Entra ID service principal, an Entra security group, an Azure Key Vault with three secrets, specific Fabric tenant settings, and gateway admin role assignment. The actual Fabric item creation is handled by a single setup notebook. The gateway-side scripts are installed manually on each gateway node.

---

## 1. PREREQUISITES

### 1.1 Fabric Capacity

The [FPM README](https://github.com/microsoft/fabric-toolbox/tree/main/monitoring/fabric-platform-monitoring#requirements-and-estimated-workloads) states:

> **Microsoft Fabric Capacity of F8 or higher, recommended F16**  
> *(The capacity size needed will depend on the amount of logs sent and processed by the system.)*

- **Minimum**: F8 SKU
- **Recommended**: F16 SKU

This sizing requirement is documented directly in the FPM README and is not derived from general Microsoft Fabric capacity documentation. The README does not provide further rationale for these thresholds beyond the note about log volume dependency.

### 1.2 Service Principal Registration

**Steps** ([Microsoft Learn — Register an application in Microsoft Entra ID](https://learn.microsoft.com/en-us/entra/identity-platform/quickstart-register-app)):

1. Sign in to the [Microsoft Entra admin center](https://entra.microsoft.com) as at least an **Application Developer**.
2. Browse to **Entra ID > App registrations** and select **New registration**.
3. Enter a meaningful name (e.g., `FPM-GatewayMonitoring-SP`).
4. Under **Supported account types**, select **Single tenant only — \<your tenant\>**.
5. Select **Register**.
6. On the app's **Overview** page, record the **Application (client) ID** and the **Directory (tenant) ID**.
7. Under **Manage**, go to **Certificates & secrets > Client secrets > New client secret**.
8. Set an expiry period and select **Add**. **Copy the secret value immediately** — it is only shown once.

> **Important (from [Microsoft Learn — Enable SP for admin APIs](https://learn.microsoft.com/en-us/fabric/admin/enable-service-principal-admin-apis)):** The app registration **must not** have any admin-consent required permissions for Power BI set in the Azure portal. The SP authenticates via its security group membership in the tenant settings — not via API permissions.

### 1.3 Entra ID Security Group

([FPM README — Requirements](https://github.com/microsoft/fabric-toolbox/tree/main/monitoring/fabric-platform-monitoring#requirements-and-estimated-workloads), [Microsoft Learn — Enable SP for admin APIs](https://learn.microsoft.com/en-us/fabric/admin/enable-service-principal-admin-apis)):

1. In **Azure portal > Microsoft Entra ID > Groups**, create a new group:
   - **Group type**: Security
   - **Group name**: e.g., `FPM-ServicePrincipals`
2. Add the service principal (app registration) as a **member** of this group.
3. Record the group's Object ID — it will be referenced in Fabric tenant settings.

> This security group is the identity used in all four Fabric tenant settings below and must be explicitly added to each relevant tenant setting.

### 1.4 Azure Key Vault

([FPM README — Requirements](https://github.com/microsoft/fabric-toolbox/tree/main/monitoring/fabric-platform-monitoring#requirements-and-estimated-workloads), [Microsoft Learn — What is Azure Key Vault?](https://learn.microsoft.com/en-us/azure/key-vault/general/basic-concepts))

Create an Azure Key Vault and add exactly **three secrets** with any names of your choosing (the names are referenced as parameters in the Setup notebook):

| Secret Purpose | Suggested Name (configurable) | Value |
|---|---|---|
| Tenant ID | e.g., `fpm-tenant-id` | Your Entra tenant GUID |
| App (Client) ID of the Service Principal | e.g., `fpm-client-id` | App registration's Application (client) ID |
| Secret of the Service Principal | e.g., `fpm-client-secret` | The client secret value from §1.2 step 8 |

> These exact names are entered into the Setup notebook variables `key_vault_tenant_id`, `key_vault_client_id`, and `key_vault_client_secret` (see §4.1).

### 1.5 Key Vault RBAC Role Assignment — "Key Vault Secrets User"

([FPM README — Requirements](https://github.com/microsoft/fabric-toolbox/tree/main/monitoring/fabric-platform-monitoring#requirements-and-estimated-workloads), [Microsoft Learn — Azure RBAC for Key Vault](https://learn.microsoft.com/en-us/azure/key-vault/general/rbac-guide))

The user **executing the Setup notebook** must have at minimum the **"Key Vault Secrets User"** role on the Key Vault.

**Role definition** ([Microsoft Learn — Azure built-in roles for Key Vault data plane operations](https://learn.microsoft.com/en-us/azure/key-vault/general/rbac-guide#azure-built-in-roles-for-key-vault-data-plane-operations)):

> **Key Vault Secrets User** — *Read secret contents including secret portion of a certificate with private key. Only works for key vaults that use the 'Azure role-based access control' permission model.*  
> Role ID: `4633458b-17de-408a-b874-0445c86b69e6`

**Assignment steps** (Azure portal):
1. Go to your Key Vault resource in the Azure portal.
2. Select **Access control (IAM)**.
3. Select **Add > Add role assignment**.
4. Search for and select **Key Vault Secrets User**.
5. Assign to the user account (or managed identity) that will run the Setup notebook.
6. Select **Review + assign**.

> **Note**: As of API version 2026-02-01, Azure RBAC is the default access control model for newly created Key Vaults. If your vault uses the legacy **Access Policies** model, grant **Get** and **List** permissions on Secrets instead.

---

## 2. FABRIC TENANT SETTINGS

All four settings are configured in the **Fabric Admin Portal** at:  
`https://app.fabric.microsoft.com/admin-portal/tenantSettings`  
(Admin portal → **Tenant settings**)

For each setting, select **Specific security groups**, add the Entra security group from §1.3, and select **Apply**.

### 2.1 Developer Settings Section

Navigate to **Tenant settings → Developer settings**.

#### Setting 1: "Service principals can call Fabric public APIs"

([Microsoft Learn — Developer tenant settings](https://learn.microsoft.com/en-us/fabric/admin/service-admin-portal-developer))

> *Use a service principal to access Fabric public APIs that include create, read, update, and delete (CRUD) operations, and are protected by a Fabric permission model.*

- **Enabled by default** for new tenants as of mid-2025.
- Restrict to the FPM security group using **Specific security groups**.

> **⚠ NAME CHANGE NOTE**: The [FPM README](https://github.com/microsoft/fabric-toolbox/tree/main/monitoring/fabric-platform-monitoring#requirements-and-estimated-workloads) references this setting as **"Service principals can use Fabric APIs"**. That label was the previous name. Between May–June 2025, Microsoft split this into two separate settings. The equivalent setting is now called **"Service principals can call Fabric public APIs"** ([Changes to Fabric Tenant Settings — nickyvv.com](https://www.nickyvv.com/2025/08/changes-to-fabric-tenant-settings-for-apis.html), [Microsoft Learn — Developer tenant settings](https://learn.microsoft.com/en-us/fabric/admin/service-admin-portal-developer)). The HTML anchor `#service-principals-can-use-fabric-apis` on the developer settings page redirects to `#service-principals-can-call-fabric-public-apis`, confirming the rename.

### 2.2 Admin API Settings Section

Navigate to **Tenant settings → Admin API settings**.

#### Setting 2: "Service principals can access read-only admin APIs"

([Microsoft Learn — Tenant settings index — Admin API settings](https://learn.microsoft.com/en-us/fabric/admin/tenant-settings-index#admin-api-settings), [FPM README](https://github.com/microsoft/fabric-toolbox/tree/main/monitoring/fabric-platform-monitoring#requirements-and-estimated-workloads))

> *Web apps registered in Microsoft Entra ID can use service principals, rather than user credentials, to authenticate to read-only admin APIs. To allow an app to use a service principal as an authentication method, the service principal must be added to an allowed security group.*

#### Setting 3: "Enhance admin APIs responses with detailed metadata"

([Microsoft Learn — Tenant settings index — Admin API settings](https://learn.microsoft.com/en-us/fabric/admin/tenant-settings-index#admin-api-settings))

> *Users and service principals allowed to call Power BI admin APIs may get detailed metadata about Power BI items. For example, responses from GetScanResult APIs will contain the names of semantic model tables and columns.*  
> **Note**: For this setting to apply to service principals, "Service principals can access read-only admin APIs" must also be enabled.

#### Setting 4: "Enhance admin APIs responses with DAX and mashup expressions"

([Microsoft Learn — Tenant settings index — Admin API settings](https://learn.microsoft.com/en-us/fabric/admin/tenant-settings-index#admin-api-settings))

> *Users and service principals eligible to call Power BI admin APIs will get detailed metadata about queries and expressions comprising Power BI items.*

**Summary table:**

| Setting Name (exact UI label) | Section | Default | Action |
|---|---|---|---|
| Service principals can call Fabric public APIs | Developer settings | Enabled (new tenants) | Restrict to SP security group |
| Service principals can access read-only admin APIs | Admin API settings | Disabled | Enable, add SP security group |
| Enhance admin APIs responses with detailed metadata | Admin API settings | Disabled | Enable, add SP security group |
| Enhance admin APIs responses with DAX and mashup expressions | Admin API settings | Disabled | Enable, add SP security group |

---

## 3. FABRIC WORKSPACE & GATEWAY ADMIN SETUP

### 3.1 Fabric Workspace

([FPM README — Requirements](https://github.com/microsoft/fabric-toolbox/tree/main/monitoring/fabric-platform-monitoring#requirements-and-estimated-workloads), [Microsoft Learn — Roles in workspaces](https://learn.microsoft.com/en-us/fabric/fundamentals/roles-workspaces))

1. Create a Fabric workspace (or use an existing one) assigned to the F8+ capacity.
2. **Add the service principal explicitly as a Member** of the workspace:  
   - Go to workspace **Settings → Manage access → Add people or groups**.
   - Search for the service principal by its application name.
   - Assign the **Member** role.
3. Also add the Entra security group containing the SP as **Member** (belt-and-suspenders).

> **Important**: The [FPM README](https://github.com/microsoft/fabric-toolbox/tree/main/monitoring/fabric-platform-monitoring#requirements-and-estimated-workloads) explicitly states: *"Add the Service Principal explicitly."* Adding only the security group may not be sufficient — the SP itself must be directly named in workspace membership for the Setup notebook to run correctly.

### 3.2 On-Premises Data Gateway — Admin Role Assignment

([FPM README — Requirements](https://github.com/microsoft/fabric-toolbox/tree/main/monitoring/fabric-platform-monitoring#requirements-and-estimated-workloads), [Microsoft Learn — Manage security roles of an on-premises data gateway](https://learn.microsoft.com/en-us/data-integration/gateway/manage-security-roles))

The service principal requires the **Admin** role on the gateway cluster(s) to be monitored.

**Steps**:
1. Navigate to the [Power Platform admin center](https://admin.powerplatform.microsoft.com).
2. Select **Data (preview) → On-premises data gateways** tab (or navigate to the **On-premises data gateways** section).
3. Select the gateway cluster you want to monitor.
4. In the top ribbon, select **Manage users**.
5. Add the service principal and assign the **Admin** role.

> **Role definition** ([Microsoft Learn — Manage security roles](https://learn.microsoft.com/en-us/data-integration/gateway/manage-security-roles)):  
> *"An admin can manage and update the on-premises data gateway. An admin is allowed to create connections (data sources) on the gateway. An admin is allowed to manage (add/delete) users..."*

> **⚠ Scope restriction** ([FPM README](https://github.com/microsoft/fabric-toolbox/tree/main/monitoring/fabric-platform-monitoring#gateway-monitoring)):  
> *"Only works with On-Premises Data Gateway and not with VNet Gateways."* This is a hard platform limitation — the FPM Gateway Monitoring module cannot monitor Virtual Network (VNet) gateways.

> **Known gotcha** (from [Fabric Community — FPM Gateways module thread](https://community.fabric.microsoft.com/t5/Real-Time-Intelligence/Fabric-Platform-Monitoring-accelerator-Gateways-module/m-p/4884544)): The `Get-DataGatewayInfo.ps1` script has experienced 401 errors even when permissions appear correct, due to limitations in service principal access to certain gateway admin API endpoints. As of June 2026, a fix was merged via a PR. Ensure the repo is at the latest main-branch commit before deploying.

---

## 4. FABRIC ITEM SETUP

### 4.1 Running the Setup Notebook

([FPM README — Fabric initial setup](https://github.com/microsoft/fabric-toolbox/tree/main/monitoring/fabric-platform-monitoring#fabric-initial-setup), [Setup notebook source](https://github.com/microsoft/fabric-toolbox/blob/main/monitoring/fabric-platform-monitoring/setup/Fabric%20Platform%20Monitoring%20Setup.ipynb))

1. In the target Fabric workspace, create a new notebook (or import the `Fabric Platform Monitoring Setup.ipynb`).
2. The notebook installs the `fabric-deployment-tool` pip package.
3. Fill in the **Variables** section (Cell 3 and Cell 5):

   ```python
   workspace_name = "Microsoft Fabric Platform Monitoring"  # or your workspace name
   capacity_name = ""  # Only needed if workspace doesn't exist yet

   INSTALL_CAPACITY_MODULE = True    # Set False to skip capacity module
   INSTALL_GATEWAY_MODULE = True     # MUST be True for gateway monitoring
   INSTALL_ACTIVITY_EVENTS_MODULE = True
   INSTALL_INVENTORY_MODULE = True

   key_vault_uri = "https://<your-keyvault-name>.vault.azure.net/"
   key_vault_tenant_id = "<secret-name-for-tenant-id>"       # e.g., "fpm-tenant-id"
   key_vault_client_id = "<secret-name-for-client-id>"       # e.g., "fpm-client-id"
   key_vault_client_secret = "<secret-name-for-client-secret>" # e.g., "fpm-client-secret"
   ```

4. Run all cells. The notebook:
   - Connects to the FPM GitHub repo (microsoft/fabric-toolbox, main branch)
   - Iterates through all items in the source, replaces workspace/eventhouse IDs dynamically
   - Deploys all Fabric items into the target workspace
   - Runs `fabDeploymentTool.update_capcity_events_eventstream(workspace_name)` to configure the Capacity Events Eventstream source

5. After the run completes, `fabDeploymentTool.mapping_table` shows a table of all deployed items.

### 4.2 Items Created by the Setup Notebook

([FPM README — List of items used](https://github.com/microsoft/fabric-toolbox/tree/main/monitoring/fabric-platform-monitoring#list-of-items-used), [Fabric Jumpstart — FPM Gateway Monitoring](https://jumpstart.fabric.microsoft.com/catalog/fpm-gateway-monitoring/))

**Eventstreams** (for Gateway Monitoring):
- `GatewayMonitoringHeartbeat` — CustomEndpoint source (`Heartbeat`); destination: Eventhouse `GatewaysHeartbeat` table via DirectIngestion
- `GatewayMonitoringReports` — CustomEndpoint source (`Reports`); destination: Eventhouse `GatewayReports-Raw` table via DirectIngestion

**Eventstreams** (other modules, deployed if enabled):
- `CapacityUtilizationEvents` — for RTH Capacity Events (Capacity module)

**Eventhouse**:
- `Fabric Platform Monitoring` — Container for all KQL databases, one per module:
  - `Gateway Monitoring` KQL DB — contains `GatewaysHeartbeat` and `GatewayReports-Raw` ingest tables, plus derived views: `GatewayNodeStatusWithInfo`, `QueryExecutionUnified`, `QueryConnections`, `SystemCounters`

**Notebooks** (one per module, each with a corresponding Pipeline):
- `Monitoring Audit Logs` — Extracts audit logs from the API; recommended every 5 min
- `Monitoring Extraction Refreshables` — Extracts refreshable data; recommended every 5 min
- `Monitoring Extraction Scanner` — Full inventory via Scanner API; recommended every 120 min
- `Monitoring Extraction Inventory` — Tenant-level metadata (capacities, gateways, connections, domains, etc.); recommended every 30 min

**Pipelines**: One per notebook (for scheduling and tracking).

**Power BI Semantic Model + Report** (`Gateway Monitoring`):
- DirectQuery semantic model over the KQL DB
- Six report pages: **Gateways**, **Jobs**, **Job Details**, **Queries**, **Running Jobs**, **System Counters**
- Page-level auto-refresh: Jobs/Queries/System Counters at 5 min; Running Jobs at 10 sec; Gateways at 30 sec
- Post-deployment action required: set up a user credential in the semantic model to authenticate to the KQL DB

### 4.3 Running the Gateway Config Notebook

([FPM README — Script deployment and setup](https://github.com/microsoft/fabric-toolbox/tree/main/monitoring/fabric-platform-monitoring#the-setup-configuration), [Gateway Config notebook source](https://github.com/microsoft/fabric-toolbox/blob/main/monitoring/fabric-platform-monitoring/setup/Gateway%20Config.ipynb))

The **Gateway Config** notebook generates the `config.json` file required by the gateway-side PowerShell scripts.

1. In the same Fabric workspace, open the `Gateway Config` notebook (deployed by the Setup notebook).
2. Fill in the variables:

   ```python
   HeartbeatEnable = True
   HeartbeatInterval = 1        # seconds between heartbeat messages
   ReportSendInterval = 5       # seconds between report uploads
   ReportRetention = 10         # days of local report retention
   TenatId = "<your-tenant-guid>"   # Note: typo "Tenat" is in the notebook source
   AppId = "<your-sp-client-id>"
   ```

3. Run all cells. The notebook:
   - Resolves the Event Hub connection strings from the two Eventstreams via Fabric REST API
   - Generates `./builtin/config.json` with the full configuration including connection strings, SP block, and default gateway log path

4. Download `config.json` from the notebook's **Built-in Resources** panel (Files section).

**The generated config.json structure** includes:

```json
{
  "GatewayId": "",
  "GatewayLogsPath": ["C:\\Windows\\ServiceProfiles\\PBIEgwService\\AppData\\Local\\Microsoft\\On-premises data gateway"],
  "HeartbeatEnable": true,
  "HeartbeatInterval": 1,
  "ReportSendInterval": 5,
  "ReportRetention": 10,
  "VerboseLogSendInterval": 600,
  "ServicePrincipal": {
    "TennatId": "<tenant-id>",
    "AppId": "<app-id>",
    "SecretText": ""
  },
  "EventHubs": {
    "UploadReports": true,
    "ConnectionStrings": [
      {"Report": "Heartbeat", "EventHubConnectionString": "<heartbeat-connection-string>"},
      {"Report": "Reports", "EventHubConnectionString": "<reports-connection-string>"}
    ]
  },
  "Lakehouse": {
    "UploadReports": false,
    "UploadLogs": false,
    "WorkspaceName": "",
    "LakehouseName": ""
  },
  "ConnectionProperties": {
    "MaximumRetryCount": 3,
    "RetryIntervalSec": 1
  }
}
```

> **Note**: `SecretText` is intentionally left blank in the generated file. It is populated interactively on the gateway machine by `Setup-UpdateConfiguration.ps1`, which encrypts it using a machine-specific key. Never check a populated `SecretText` into source control.

---

## 5. GATEWAY NODE SCRIPT SETUP

### 5.1 PowerShell Prerequisites

([FPM README — Script deployment](https://github.com/microsoft/fabric-toolbox/tree/main/monitoring/fabric-platform-monitoring#script-deployment-and-setup-in-the-gateway-nodes-optional))

- **PowerShell 7+** is required (`#requires -Version 7` is declared in all scripts)
- The scripts must be run on the **gateway host machine** under an account that can read gateway log files at `C:\Windows\ServiceProfiles\PBIEgwService\AppData\Local\Microsoft\On-premises data gateway`

### 5.2 Directory Layout

1. Create a root folder on the gateway machine, e.g., `C:\GatewayMonitoring\`
2. Copy all PowerShell scripts from the repo's `/gateway/PowerShellScript/` folder into this directory:
   - `Setup-UpdateConfiguration.ps1` / `.cmd`
   - `Run-GatewayHeartbeat.ps1` / `.cmd`
   - `Run-UploadGatewayLogs.ps1` / `.cmd`
   - `Get-DataGatewayInfo.ps1` / `.cmd`
   - `Install-DataGatewayAuto.ps1` / `.cmd`
   - `modules/` subfolder (contains `Utils.psm1` and `UploadGatewayLogs.psm1`)
3. Create a `/configs/` subfolder inside the script root
4. Copy `config.json` (from §4.3) into `/configs/config.json`

### 5.3 Running Setup-UpdateConfiguration.ps1

([Setup-UpdateConfiguration.ps1 source](https://raw.githubusercontent.com/microsoft/fabric-toolbox/main/monitoring/fabric-platform-monitoring/gateway/PowerShellScript/Setup-UpdateConfiguration.ps1))

Run from PowerShell 7:
```powershell
pwsh -File ".\Setup-UpdateConfiguration.ps1"
```

The script interactively prompts:

1. **Install Az.Accounts module?** (Y/N)
   - `Az.Accounts` — manages credentials and common config for all Azure modules ([Microsoft Learn — Install Azure PowerShell](https://learn.microsoft.com/en-us/powershell/azure/install-azps-windows))
2. **Install Az.Storage module?** (Y/N)
   - `Az.Storage` — provides cmdlets for Azure Storage (Lakehouse connectivity) ([Microsoft Learn — Az.Storage](https://learn.microsoft.com/en-us/cli/azure/storage))
3. **Install DataGateway module?** (Y/N)
   - `DataGateway` — manages On-premises data gateways and Power BI data sources ([Microsoft Learn — PowerShell Cmdlets for On-premises data gateway](https://learn.microsoft.com/en-us/powershell/gateway/overview))
4. **Install MicrosoftPowerBIMgmt module?** (Y/N)
   - Required only for the `Get-DataGatewayInfo` script

5. **Change default gateway log path?** — Defaults to `C:\Windows\ServiceProfiles\PBIEgwService\AppData\Local\Microsoft\On-premises data gateway`. Confirm or set a custom path.

6. **GatewayId auto-detection** — The script reads the first `*Report_*.log` file in the gateway logs folder to extract the `GatewayObjectId`. If no report file is found yet (e.g., no jobs have run), you will be prompted to enter the Gateway ID manually (GUID, visible in the Power Platform admin center gateway details page).

7. **Service Principal Secret** — Enter the SP client secret when prompted. The script encrypts it using a machine-specific key (`ConvertTo-SecureWithMachineKey`) and writes the encrypted value to `config.json`. **This must be run on the machine where the scripts will execute** — the encrypted secret is machine-bound.

After completion, `config.json` in `/configs/` is updated with the GatewayId and encrypted SecretText.

### 5.4 Script Descriptions

([FPM README — Script descriptions](https://github.com/microsoft/fabric-toolbox/tree/main/monitoring/fabric-platform-monitoring#run-gatewayheartbeat-script), script source files)

#### Run-GatewayHeartbeat.ps1

([Source](https://raw.githubusercontent.com/microsoft/fabric-toolbox/main/monitoring/fabric-platform-monitoring/gateway/PowerShellScript/Run-GatewayHeartbeat.ps1))

- **Purpose**: Checks whether the `Microsoft.PowerBI.EnterpriseGateway` process is running and sends a heartbeat JSON message to the `GatewayMonitoringHeartbeat` Eventstream at every `HeartbeatInterval` seconds (default: 1 second).
- **Payload includes**: `GatewayId`, `ProcessName`, `ProductVersion`, `FileVersion`, `Responding` (bool), `StartTime`, `ServerTimestampUTC`
- **Behavior**: Runs as a continuous loop (`do { ... } while ($true)`). Automatically restarts on failure (up to 5 errors within 1 minute before breaking).
- **Required modules**: `Az.Accounts`, `Az.Storage`
- **Error logging**: Writes errors to `.\logs\Heartbeat.log`

#### Run-UploadGatewayLogs.ps1

([Source](https://raw.githubusercontent.com/microsoft/fabric-toolbox/main/monitoring/fabric-platform-monitoring/gateway/PowerShellScript/Run-UploadGatewayLogs.ps1))

- **Purpose**: The primary data movement script. Tails gateway log and report files, uploads Report files (`*Report_*.log`) to the `GatewayMonitoringReports` Eventstream (and optionally to a Lakehouse), and uploads verbose log files to the Lakehouse. Runs every `ReportSendInterval` seconds (default: 5 seconds).
- **Behavior**: Continuous loop. Moves report files to a `Temp` subfolder before upload. Filters files modified since last run.
- **Required modules**: `Az.Accounts`, `Az.Storage`
- **Error logging**: Writes errors to `.\logs\GatewayMonitoring.log`

#### Get-DataGatewayInfo.ps1

([Source](https://raw.githubusercontent.com/microsoft/fabric-toolbox/main/monitoring/fabric-platform-monitoring/gateway/PowerShellScript/Get-DataGatewayInfo.ps1))

- **Purpose**: Calls the Fabric REST API (`https://api.fabric.microsoft.com/v1/gateways/`) using the service principal to retrieve gateway cluster and node metadata (OS, cores, memory, version, status). Sends the result to the `GatewayMonitoringReports` Eventstream as a `GatewayNodeInfo` log type.
- **Authentication**: Uses `Connect-AzAccount -ServicePrincipal` with the SP credentials from `config.json`, then obtains a Fabric Bearer token via `Get-AzAccessToken`.
- **Run frequency**: Intended as a low-frequency job — the [FPM README](https://github.com/microsoft/fabric-toolbox/tree/main/monitoring/fabric-platform-monitoring#get-datagatewayone) states *"we can run this once per week or even lower rate."*
- **Required modules**: `Az.Accounts`
- **Known issue**: Has experienced 401 errors due to service principal gateway API access limitations. Verify the fix is included in your repo version before deploying ([Community thread](https://community.fabric.microsoft.com/t5/Real-Time-Intelligence/Fabric-Platform-Monitoring-accelerator-Gateways-module/m-p/4884544), [GitHub issue #321](https://github.com/microsoft/fabric-toolbox/issues/321)).

### 5.5 Task Scheduler Setup

([Task Scheduler XML templates](https://github.com/microsoft/fabric-toolbox/tree/main/monitoring/fabric-platform-monitoring/gateway/TaskSchedulers), [FPM README — Schedule the Scripts](https://github.com/microsoft/fabric-toolbox/tree/main/monitoring/fabric-platform-monitoring#schedule-the-scripts))

Import the provided XML templates into Windows Task Scheduler (Task Scheduler → Action → Import Task). The templates are UTF-16 encoded Windows Task Scheduler XML files. You must update:
- The `<UserId>` SID to match the account that will run the scripts
- The `<Arguments>` path if you placed the scripts in a different directory than the template's default (`C:\GatewayMonitoring\rt-gateway-log\PowerShell Script\`)

| Task Scheduler Template | Script | Trigger | Cadence | Notes |
|---|---|---|---|---|
| `Gateway-Heartbeat.xml` | `Run-GatewayHeartbeat.ps1` | Boot trigger | On startup, runs continuously | `ExecutionTimeLimit: PT72H`; restarts on failure every 1 min (up to 3 times); `Hidden: true` |
| `Gateway-Upload Logs.xml` | `Run-UploadGatewayLogs.ps1` | Boot trigger | On startup, runs continuously (internal loop every `ReportSendInterval` sec) | `ExecutionTimeLimit: PT72H`; restarts on failure every 1 min (up to 3 times) |
| `Gateway-NodeInfo.xml` | `Get-DataGatewayInfo.ps1` | Boot trigger + weekly (every Sunday) | On startup + weekly | `ScheduleByWeek: Sunday, WeeksInterval: 1` |

> **Key insight from XML analysis**: Both `Run-GatewayHeartbeat` and `Run-UploadGatewayLogs` use **boot triggers** (not time-based repeating triggers) because the scripts themselves implement internal timing loops. The Task Scheduler simply starts them on machine boot and restarts them if they crash. `Get-DataGatewayInfo` uses a **weekly calendar trigger** (Sunday) in addition to a boot trigger, consistent with the README's "once per week or lower" recommendation.

> **Run-as context**: All three tasks use `<LogonType>Password</LogonType>` and `<RunLevel>HighestAvailable</RunLevel>`. The running account must have read access to the gateway log folder and the PowerShell script directory.

---

## 6. VALIDATION STEPS

([FPM README — Gateways report page](https://github.com/microsoft/fabric-toolbox/tree/main/monitoring/fabric-platform-monitoring#gateways), [Fabric Jumpstart — FPM Gateway Monitoring](https://jumpstart.fabric.microsoft.com/catalog/fpm-gateway-monitoring/))

After scripts are running, validate data flow in this order:

### Step 1: Heartbeat in Real-Time Dashboard (~1 minute)

1. In Fabric, open the `Gateway Monitoring` Power BI Report (or the Real-Time Dashboard if deployed).
2. Navigate to the **Gateways** page.
3. Within approximately **1 minute** of `Run-GatewayHeartbeat.ps1` starting, you should see:
   - The gateway node name listed
   - The **heartbeat indicator** showing the gateway as online/responding
   - `Responding: true` and the `ProductVersion` of the gateway process

If no heartbeat appears after 2–3 minutes:
- Check `.\logs\Heartbeat.log` on the gateway machine for errors
- Verify the Eventstream `GatewayMonitoringHeartbeat` is receiving events (check the Eventstream topology in Fabric)
- Verify `config.json` has correct `EventHubConnectionString` values

### Step 2: Gateway Report Queries page populating (~5–10 minutes)

1. Navigate to the **Queries** page in the Gateway Monitoring report.
2. If gateway jobs have run (semantic model refreshes, dataflow refreshes, etc.), rows should appear in the query table.

> **Note**: Queries only appear if the gateway is processing work. A newly deployed, idle gateway will show no query data until jobs run through it.

### Step 3: System Counters page populating

1. Navigate to the **System Counters** page.
2. System counter data comes from the gateway's verbose log files, uploaded by `Run-UploadGatewayLogs.ps1`. These are generated by the gateway process on a regular cadence.
3. If `VerboseLogSendInterval` in config.json is set to 600 seconds (default), allow up to ~10 minutes for first data.

### Step 4: Jobs and Running Jobs pages

1. Navigate to the **Jobs** page — shows completed jobs.
2. Navigate to the **Running Jobs** page — shows jobs currently in progress (auto-refreshes every 10 seconds).

### KQL DB Validation (optional, direct)

If you have KQL query access to the `Gateway Monitoring` KQL DB in the Eventhouse, query directly:
```kusto
GatewaysHeartbeat
| order by ServerTimestampUTC desc
| take 10
```
This confirms raw data is landing in the database.

---

## 7. DOCUMENTED GOTCHAS AND CAUTIONS

### 7.1 Customizations May Be Reverted on Update

([FPM README — CAUTION block](https://github.com/microsoft/fabric-toolbox/tree/main/monitoring/fabric-platform-monitoring#fabric-initial-setup))

> *"No changes are made to any additional item in the workspace or eventhouse. But if you customize the default ones (Notebook, Policies, Tables, Functions, etc), the change could be reverted back or the update could fail."*

If you modify the default notebooks, KQL policies, tables, or functions deployed by FPM, those changes may be overwritten when you re-run the Setup notebook to pull an updated version of FPM.

**Mitigation**: Document all customizations. Re-apply them after each FPM update. Consider creating new KQL functions/tables with custom names rather than modifying the FPM defaults.

### 7.2 Capacity Events Module Is in Public Preview

([FPM README — CAUTION block](https://github.com/microsoft/fabric-toolbox/tree/main/monitoring/fabric-platform-monitoring#modules-included-with-the-fabric-platform-monitoring))

> *"At the moment the Capacity Events are in Public Preview. Any change to this event source will be reflected in the solution over time. Please update your solution if the Capacity Events source is updated."*

This applies to the Capacity Utilization module, not the Gateway Monitoring module. However, if both modules are deployed together (as in the default Setup notebook), updates to the Capacity Events API may require a re-deployment of the full solution.

### 7.3 VNet Gateways Are NOT Supported

([FPM README — Gateway Monitoring module description](https://github.com/microsoft/fabric-toolbox/tree/main/monitoring/fabric-platform-monitoring#modules-included-with-the-fabric-platform-monitoring))

> *"Only works with On-Premises Data Gateway and not with VNet Gateways."*

This is a hard architectural limitation. The PowerShell scripts access local gateway log files on the gateway host machine — a deployment model that is incompatible with VNet gateways (which are managed, serverless, and have no accessible host machine).

### 7.4 SecretText Encryption Is Machine-Bound

([Setup-UpdateConfiguration.ps1 source](https://raw.githubusercontent.com/microsoft/fabric-toolbox/main/monitoring/fabric-platform-monitoring/gateway/PowerShellScript/Setup-UpdateConfiguration.ps1))

The SP client secret in `config.json` is encrypted using `ConvertTo-SecureWithMachineKey`, a machine-specific encryption method. The encrypted `SecretText` value **cannot be copied** from one machine to another. Each gateway node requires its own execution of `Setup-UpdateConfiguration.ps1` to generate a valid encrypted secret.

### 7.5 Get-DataGatewayInfo Known 401 Issue (June 2026)

([Fabric Community thread](https://community.fabric.microsoft.com/t5/Real-Time-Intelligence/Fabric-Platform-Monitoring-accelerator-Gateways-module/m-p/4884544), [GitHub issue #321](https://github.com/microsoft/fabric-toolbox/issues/321))

The `Get-DataGatewayInfo.ps1` script has experienced 401 Unauthorized errors when calling the Fabric gateway APIs via service principal, even when all permissions are correctly configured. This was reported as a known bug and a fix was merged in June 2026. Ensure you are running the latest main branch. If you encounter 401s, verify:
- The SP security group is explicitly added to the "Service principals can access admin APIs used for updates" tenant setting
- The SP has the Admin role on the gateway cluster in the Power Platform admin center

### 7.6 Tenant Setting Name Change (May–June 2025)

([Microsoft Learn — Developer tenant settings](https://learn.microsoft.com/en-us/fabric/admin/service-admin-portal-developer), [Changes to Fabric Tenant Settings — nickyvv.com](https://www.nickyvv.com/2025/08/changes-to-fabric-tenant-settings-for-apis.html))

The FPM README references **"Service principals can use Fabric APIs"** under Developer Settings. This setting was renamed and split in May–June 2025. The current equivalent setting is **"Service principals can call Fabric public APIs"**. The old single setting has been replaced by two settings:
1. "Service principals can create workspaces, connections, and deployment pipelines" (disabled by default)
2. "Service principals can call Fabric public APIs" (enabled by default)

For FPM, ensure setting #2 is enabled for the SP security group.

### 7.7 GatewayId Auto-Detection Requires Existing Log Files

([Setup-UpdateConfiguration.ps1 source](https://raw.githubusercontent.com/microsoft/fabric-toolbox/main/monitoring/fabric-platform-monitoring/gateway/PowerShellScript/Setup-UpdateConfiguration.ps1))

The setup script attempts to auto-detect the GatewayId by reading a `*Report_*.log` file. If no gateway jobs have ever run, no report files exist yet and you must enter the GatewayId manually. Find it in the Power Platform admin center under the gateway cluster details.

### 7.8 Semantic Model Requires Manual Credential Setup

([FPM README — Power BI Gateway Report](https://github.com/microsoft/fabric-toolbox/tree/main/monitoring/fabric-platform-monitoring#power-bi-gateway-report-optional))

The Gateway Monitoring Power BI report is deployed automatically, but:
> *"The only action needed is to set up a user in the Semantic Model to connect to the KQL DB."*

After deployment, open the semantic model settings in the Fabric workspace and configure a user credential to authenticate the DirectQuery connection to the KQL database.

---

## 8. GAPS AND UNVERIFIED ITEMS

The following items were not fully documented in the repo or Microsoft Learn sources at the level needed for a complete Phase 0 checklist:

| Item | Status | Notes |
|---|---|---|
| Exact Eventstream source configuration steps (CustomEndpoint topology) | **[Repo-confirmed]** — handled automatically by Gateway Config notebook | No manual Eventstream config needed for gateway module |
| KQL DB schema (table definitions, update policies) | **[Repo-confirmed]** — deployed automatically by Setup notebook from repo source | Not documented in README; review `/src/` folder in repo for KQL scripts |
| Minimum Azure subscription permissions to create Key Vault | **[Unverified]** | Requires at minimum Contributor on the resource group; Key Vault Data Access Administrator to assign RBAC roles |
| Whether the SP secret in Key Vault needs rotation setup | **[Unverified]** | The README does not address secret rotation. Implement Azure Key Vault automatic secret rotation policy separately. |
| Network connectivity requirements from gateway machine to Eventstream endpoint | **[Unverified]** | The EventHub-compatible connection string uses AMQP over port 5671/5672 or HTTPS port 443. Ensure outbound firewall rules allow traffic to the Eventstream endpoint hostnames. |
| Support for multiple gateway nodes in one deployment | **[Inference]** — Each node runs its own scripts against the same Eventstream endpoints; the config.json stores a single GatewayId per file, so each node needs its own config | Not explicitly documented in README |
| Required PowerShell execution policy setting | **[Unverified]** — Scripts call `Unblock-File` for module .psm1 files but don't document required execution policy | Set `Set-ExecutionPolicy RemoteSigned` on the gateway machine or run with `-ExecutionPolicy Bypass` |
| High-availability gateway cluster behavior | **[Unverified]** — README mentions gateway nodes but doesn't address whether all nodes in a cluster must run their own script instances | [Inference]: Each node should run its own script instance |

---

## 9. QUICK-REFERENCE CHECKLIST

### Pre-Deployment (complete before touching Fabric)
- [ ] Fabric capacity F8+ confirmed (F16 recommended)
- [ ] Entra ID app registration created; Application (client) ID + Tenant ID + client secret recorded
- [ ] Entra ID security group created; SP added as member
- [ ] Azure Key Vault created with three secrets (Tenant ID, Client ID, Client Secret)
- [ ] "Key Vault Secrets User" RBAC role granted to notebook-running user on Key Vault

### Fabric Tenant Settings (Fabric Admin Portal)
- [ ] **Developer settings → "Service principals can call Fabric public APIs"** — Enabled for SP security group
- [ ] **Admin API settings → "Service principals can access read-only admin APIs"** — Enabled for SP security group
- [ ] **Admin API settings → "Enhance admin APIs responses with detailed metadata"** — Enabled for SP security group
- [ ] **Admin API settings → "Enhance admin APIs responses with DAX and mashup expressions"** — Enabled for SP security group

### Fabric Workspace Setup
- [ ] Workspace created on F8+ capacity
- [ ] SP added explicitly as **Member** of workspace
- [ ] SP security group added as **Member** of workspace

### Gateway Admin Role
- [ ] SP assigned **Admin** role on the gateway cluster(s) via Power Platform admin center

### Fabric Item Deployment
- [ ] Setup notebook variables filled in (workspace, capacity, KV URI, secret names)
- [ ] Setup notebook executed successfully; all items deployed
- [ ] Gateway Config notebook variables filled in (HeartbeatInterval=1, ReportSendInterval=5, TenatId, AppId)
- [ ] Gateway Config notebook executed; config.json downloaded from Built-in Resources

### Gateway Node Script Deployment (repeat per node)
- [ ] PowerShell 7+ installed on gateway machine
- [ ] Script files copied to gateway machine; `/configs/` subfolder created; `config.json` placed in `/configs/`
- [ ] `Setup-UpdateConfiguration.ps1` executed: PowerShell modules installed, GatewayId confirmed, SP secret entered and encrypted
- [ ] Task Scheduler tasks imported/created for Heartbeat (boot), Upload Logs (boot), NodeInfo (weekly)
- [ ] Tasks configured to run under appropriate account with gateway log file access

### Validation
- [ ] Heartbeat visible in Gateways report page within ~1 minute
- [ ] Queries page populating (after gateway processes jobs)
- [ ] System Counters page populating (~10 minutes after script start)
- [ ] Semantic model credential configured (if needed for report refresh)

---

*All factual claims in this document are cited to their source URL. Where information could not be confirmed from authoritative sources, items are labeled [Unverified] or [Inference].*
