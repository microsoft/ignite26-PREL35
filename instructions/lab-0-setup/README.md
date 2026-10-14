# Lab 0 - Setup

**Time:** about 20 minutes, after the deployment owner completes provisioning

## What this lab covers

You connect to your lab environment, tour it as a small AI landing zone, and run your first alert through two
agents. Everything you deploy, measure and protect in Labs 1 to 4 builds on this.

### The use case

A bank's transaction monitoring system raises **alerts**: activity that might be money laundering, sanctions
evasion or fraud. An analyst investigates every alert, and most turn out to be false positives. Two agents help:

| Agent | What it does |
|---|---|
| **Triage** | Reads the case file and recommends a disposition (escalate, close as a false positive, or request more information), citing the evidence |
| **Reviewer** | Checks the triage recommendation before a human sees it, and disagrees if the evidence doesn't support it |

**The agents recommend. A named analyst decides.** No agent can close a case.

The data is synthetic: 14 alerts for a fictional bank, in [data/cases.json](../../data/cases.json).

### What's deployed in your resource group

```text
investigate.py  ──(Lab client token)──►  API Management  ──(its managed identity)──►  Foundry project
                                         checks audience + app role,                    triage + reviewer agents
                                         rate limit per client                          gpt-5-mini
                                                                                              │
                                                                         Application Insights ◄┘ traces
```

- **Foundry** accepts Entra ID only: no API keys exist.
- **API Management** is the way apps reach the agents. Only agents published as gateway operations can be called
  through it. You also have direct access to Foundry, for the Foundry portal and the evaluations in Lab 1; in
  production, a private endpoint and no data-plane roles for people would close that path.
- **Lab 0 uses the gateway's latest published agent versions.** Lab 1 uses your interactive Azure identity to call
  Foundry directly and pin exact versions. It does not use the Lab client secret or test the gateway policy.
- The infrastructure is [Bicep](../../infra/main.bicep), deployed for you before the lab started. The agents are
  defined in [scripts/agents](../../scripts/agents), and you publish them in step 2.

## Topics covered

- Landing zone building blocks: identity, gateway, monitoring
- Infrastructure as code for the platform, and agents as versioned definitions
- Calling agents through a gateway, with no Foundry API keys; the Lab client uses an Entra client secret

## Prerequisites

Provided on the lab VM: Azure CLI, PowerShell 7, Python 3.12 with the packages in
[requirements.txt](../../requirements.txt), and this repository. Your resource group and sign-in details are on the
**Resources** tab of the lab instructions.

The Python client uses the provided environment. To use the alternative .NET client, you also need the .NET 10
SDK. Both clients run the same investigation.

Before attendees begin, the deployment owner must have configured the gateway and provisioned the Lab client
credential in the ignored `.env` on each learner VM. Resource-group Contributor permissions alone do not allow
attendees to complete the tenant setup below. Attendees start at [Steps](#steps), not at the owner provisioning commands.

### Deployment owner: configure Microsoft Entra authentication for the gateway

The deployment owner completes this setup once, before attendees start the lab. There are two separate identities
in the request path:

| Request | Identity | Credential |
|---|---|---|
| Lab client to API Management | A confidential Microsoft Entra client service principal | A client secret used to obtain an application token for the API |
| API Management to Foundry | The API Management system-assigned managed identity | A managed identity token for `https://ai.azure.com` |

Do not create a client secret for API Management. The system-assigned managed identity in
[`infra/main.bicep`](../../infra/main.bicep) is the credential for the Foundry connection and is assigned the
Foundry User role on the Foundry account.

#### Provision the client-facing API and lab client registrations

The deployment owner runs the setup script once after the Bicep deployment and before attendees start the lab:

```powershell
.\scripts\env.ps1 -ResourceGroup <your-resource-group>
.\scripts\setup-entra-apps.ps1 -ResourceGroup <your-resource-group>
```

Run these commands from the repository on the learner VM using an authorized owner identity, then sign out of
that identity before handing the VM to the attendee. The setup script writes a local `.env`; running it on an
owner's workstation does not provision the learner VM. The attendee must sign in as the learner in step 1.

The script requires:

- PowerShell 7 and Azure CLI.
- `az login` to the tenant and subscription that contain the lab resource group.
- Permission to create and update app registrations and service principals, expose and assign application
  permissions, and grant tenant admin consent.
- Permission to read the resource group and update its API Management service and API policy.

By default, the script creates or reuses single-tenant registrations named `PREL35 Published Agents API` and
`PREL35 Lab Client`. Pass `-ApiAppDisplayName`, `-ClientAppDisplayName`, or `-ApimName` when your environment needs
different or disambiguated names. Reruns reuse registrations with the selected names, preserve the existing
application-role ID and client secret, and update the existing API Management named values and policy.

Use distinct registration names for isolated learner environments. Reusing the default names across VMs reuses
the same tenant registrations; a different VM cannot recover an existing secret from Entra. Do not rotate a
shared registration's credentials to provision another learner, because rotation invalidates existing clients.

The API registration exposes the `Agents.Invoke` application permission. The second registration is the
confidential service client used by the Python and .NET investigation clients. The script creates or reuses service
principals for both registrations, assigns that application role to the client service principal, and thereby
grants tenant admin consent idempotently.

On first setup, the script creates a one-year client secret and writes it only to the ignored root `.env`. Ordinary
reruns do not create or replace the secret. If the secret expires or is exposed, rotate it explicitly:

```powershell
.\scripts\setup-entra-apps.ps1 -ResourceGroup <your-resource-group> -RotateClientSecret
```

Rotation replaces the app registration's existing credentials, so update every authorized runtime from the local
`.env` written by that command. The script never prints the secret.

The script merges these values into the ignored root `.env`:

```text
ENTRA_TENANT_ID=<tenant-id>
APIM_API_APP_OBJECT_ID=<api-app-registration-object-id>
APIM_API_CLIENT_ID=<apim-api-client-id>
APIM_API_SERVICE_PRINCIPAL_ID=<api-service-principal-object-id>
APIM_API_AUDIENCE=api://<apim-api-client-id>
APIM_API_APP_ROLE_ID=<application-role-id>
APIM_API_APP_ROLE=Agents.Invoke
LAB_CLIENT_APP_OBJECT_ID=<confidential-client-app-registration-object-id>
LAB_CLIENT_ID=<confidential-client-application-id>
LAB_CLIENT_SERVICE_PRINCIPAL_ID=<confidential-client-service-principal-object-id>
LAB_CLIENT_SECRET=<client-secret>
```

See [`.env.example`](../../.env.example) for every deployment and identity variable. `ENTRA_TENANT_ID` selects the
single tenant. `APIM_API_CLIENT_ID` and `APIM_API_APP_OBJECT_ID` identify the API registration, while
`APIM_API_SERVICE_PRINCIPAL_ID` identifies its tenant instance. `APIM_API_AUDIENCE` is the token audience.
`APIM_API_APP_ROLE_ID` and `APIM_API_APP_ROLE` identify the application permission required by API Management.
The corresponding `LAB_CLIENT_*` values identify and authenticate the confidential client registration and service
principal.

> **Secret handling:** Never commit or share the real `.env`. Keep `.env.example` placeholder-only. For a shared or
> production runtime, move `LAB_CLIENT_SECRET` to an approved secret store instead of copying `.env`. Rotate the
> credential immediately if it is exposed and before its one-year expiry.

#### Configure the two trust boundaries

For the inbound boundary, the setup script updates API Management to validate tokens from `ENTRA_TENANT_ID`,
require `APIM_API_AUDIENCE`, and require the `Agents.Invoke` value in the token's `roles` claim. The policy is
[`infra/agents-api-policy.xml`](../../infra/agents-api-policy.xml); the per-caller rate limit remains after token
validation.

For the outbound boundary, keep the existing system-assigned managed identity on API Management and its
least-privilege Foundry User role assignment. The policy's `authentication-managed-identity` resource remains
`https://ai.azure.com`. Do not pass the caller's token to Foundry and do not store a Foundry key or client secret.

The Python and .NET clients use `ENTRA_TENANT_ID`, `LAB_CLIENT_ID`, and `LAB_CLIENT_SECRET` to request
`APIM_API_AUDIENCE/.default`. API Management validates the dedicated audience and app role, then uses its own
managed identity for the separate call to Foundry.

After redeploying Bicep, rerun the owner setup script: the template resets `apim-api-audience` to a bootstrap value.
Refreshing `.env` alone does not repair that gateway configuration. Keep the owner identity separate from the
learner's interactive identity used for agent publication and Lab 1 evaluations.

## Steps

### 1. Sign in and connect (3 min)

Open a terminal in the repository folder and sign in for the interactive setup and publishing steps. Use the
username `<lab-username>` and the password `<lab-password>`:

```powershell
az login
```

Write your environment's values to `.env`, using your resource group name:

```powershell
.\scripts\env.ps1 -ResourceGroup <your-resource-group>
```

You see the project endpoint, the model deployments and the gateway URL.

This refresh preserves the Entra values already provisioned by the deployment owner. Do not print or share `.env`.
If the investigation client reports incomplete setup, ask the deployment owner to provision this VM; do not run
tenant-administration commands as the learner. The investigation clients do not prompt an attendee to sign in.

### 2. Publish the agents (2 min)

Each agent is a Markdown file of instructions in [scripts/agents](../../scripts/agents). Publishing creates a new version of
each agent in Foundry:

```powershell
.\scripts\create_agents.ps1
```

On the first publication, you see `reviewer version 1` and `triage version 1`. If an agent already exists, the command
adds a new version instead of recreating the agent; earlier versions are preserved. Use the version numbers printed
by the command. In Lab 1 you change an agent and publish another version the same way.

Record both version numbers for Lab 1. Publishing changes the versions reached through the gateway immediately;
it is not gated release approval. Do not run Lab 0 investigations while Lab 1's exercises temporarily publish
candidate versions.

### 3. Tour the landing zone (5 min)

In the [Azure portal](https://portal.azure.com), open your resource group and find:

| Resource | Look for |
|---|---|
| Foundry (`aif-…`) | **Resource Management > Keys and Endpoint**: local (key) authentication is disabled |
| API Management (`apim-…`) | **APIs > Published agents**: one operation per agent. Open the policy: token check, rate limit, managed identity |
| Application Insights (`appi-…`) | Where agent traces land. You use it in Lab 1 |

Then open the [Foundry portal](https://ai.azure.com), select the `fincrime` project, and open **Agents**. You see
**triage** and **reviewer**. Open triage and read its instructions: they're the same text as
[scripts/agents/triage.md](../../scripts/agents/triage.md).

### 4. Run your first investigation (5 min)

From the repository folder, run the [Python client](../../src/lab0/python/investigate.py):

```powershell
python .\src\lab0\python\investigate.py ALT-1001
```

Alternatively, with the .NET 10 SDK available, run the [.NET client](../../src/lab0/dotnet/Program.cs):

```powershell
dotnet run --project .\src\lab0\dotnet -- ALT-1001
```

Both clients read the repository's `.env` and authenticate the confidential `LAB_CLIENT_ID` with
`LAB_CLIENT_SECRET`. They request an application token using the API's `/.default` scope, with the assigned
`Agents.Invoke` role, then call triage followed by
reviewer through API Management. They display recommendations only; neither client closes a case.

ALT-1001 is a bakery making cash deposits just under the reporting threshold. You see the triage recommendation,
with the evidence IDs it relied on, then the reviewer's verdict.

Run it a second time. The wording, and sometimes the recommendation, can change between runs. That variation is
why Lab 1 measures agents with evaluations instead of trusting a single run.

### 5. Get refused on purpose (4 min)

Call the gateway without a token:

```powershell
$gw = (Get-Content .env | Select-String '^APIM_GATEWAY_URL=').Line.Split('=', 2)[1]
curl.exe -s -o NUL -w "%{http_code}`n" -X POST "$gw/agents/triage/responses"
```

You get `401`: the gateway rejects the request before it reaches Foundry.

If a normal investigation fails, distinguish the failure from this intentional refusal:

- **Incomplete setup:** the local `.env` is missing a required deployment or client value, or still has example
  placeholders. Ask the deployment owner to finish provisioning.
- **Token acquisition fails:** the owner checks the tenant, client registration, secret expiry, and app-role assignment.
- **An authenticated gateway call is refused:** the owner checks its audience and role configuration, especially
  after a Bicep redeployment.
- **Foundry or model calls fail:** inspect the actual error; a refusal, rate limit, or empty answer is not a recommendation.

Only the `triage` and `reviewer` operations are published. Requests to other operation paths are not routed to
Foundry.

### 6. Check your understanding (1 min)

- The Lab client token reaches API Management. What identity does API Management use to reach Foundry?
- Where would you change the triage agent's behaviour, and how would that change reach Foundry?
- Why does Lab 1 call Foundry directly with pinned versions instead of using this gateway path?
- Who decides the outcome of an alert?

## Next

Continue to [Lab 1 - AgentOps](../lab-1-agentops/README.md).
