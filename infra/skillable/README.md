# Skillable setup

How the dev lab profile ([224908](https://labondemand.com/LabProfile/224908)) uses this repository.
Each learner gets one Windows VM and one Azure resource group (Cloud Slice) where they are Contributor.

| In Skillable Studio | From this repository |
|---|---|
| **Resource template** on the resource group | [infra/main.bicep](../main.bicep), compiled to ARM JSON (below). No parameters needed |
| **Life cycle action**: Execute Script in Cloud Platform, PowerShell, after the template deploys, blocking | [assign-learner.ps1](assign-learner.ps1): gives the learner **Foundry User** on their resource group |
| **Access control policy** | [acp-v2.json](acp-v2.json) |
| **Instructions** | [instructions/skillable/content.md](../../instructions/skillable/content.md), linked from GitHub |
| **Lab VM** | Azure CLI, PowerShell 7, Python 3.12, `pip install -r requirements.txt`, this repository cloned |

Lab 0 and Lab 1 use Python by default. The .NET clients are optional and require the .NET 10 SDK on the VM.
When preparing or refreshing the VM image, install the packages from the current
[requirements.txt](../../requirements.txt), including the Azure Monitor exporter used in Lab 1. An existing image
does not gain updated packages just because the repository was updated. Before publishing the image, check:

```powershell
python --version
python -c "import azure.identity, azure.monitor.opentelemetry, openai, requests; print('Lab imports available')"
```

Verify that the image reports Python 3.12 and that the imports succeed. These checks confirm prerequisites,
not Azure sign-in or connectivity; learners verify those in Lab 0.

After changing the Lab 1 runners, run the focused offline reliability checks. They do not publish agents or call
Azure:

```powershell
python .\tests\lab1\check_reliability.py
# Optional .NET path, with the .NET 10 SDK:
dotnet run --project .\tests\lab1\dotnet
```

## Resource template

The template loads the gateway policy from a separate file, so paste the compiled JSON rather than the Bicep:

```powershell
az bicep build --file infra/main.bicep --stdout | Set-Clipboard
```

## Instructions

Skillable reads one Markdown file. After changing any lab README, rebuild it and commit:

```powershell
python scripts/build_skillable.py
```

Linking needs a GitHub account that is a full admin of the repository.

## Access control policy

[acp-v2.json](acp-v2.json) denies every resource type, SKU and location the lab doesn't deploy. It allows:

| Resource | Limit |
|---|---|
| Foundry account (`Microsoft.CognitiveServices/accounts`) | kind `AIServices`, SKU `S0` |
| Model deployments | `gpt-5-mini` and `gpt-4.1-mini` (Lab 1 retirement drill candidate), `GlobalStandard`, up to 60K tokens per minute each |
| Foundry project and its connections | |
| API Management | `BasicV2`, 1 unit, and anything inside it (APIs, operations, policies, backends, named values) |
| Log Analytics, Application Insights, and the smart detection alert rule and action group it creates | |
| Role assignments | Made by the template and the life cycle action. The learner is Contributor, so can't make them |

Locations: the resource group's region, plus `global` for the alert rule and action group.

When a lab adds a resource, change the policy and save it in Studio as a **new name** (`acp-v2`, …): Skillable
caches policies by name, so an edit under the same name may not apply. Then retest the whole lab end to end with
the new policy before asking to publish.
