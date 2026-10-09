# Skillable setup

How the dev lab profile ([224908](https://labondemand.com/LabProfile/224908)) uses this repository.
Each learner gets one Windows VM and one Azure resource group (Cloud Slice) where they are Contributor.

| In Skillable Studio | From this repository |
|---|---|
| **Resource template** on the resource group | [infra/main.bicep](../main.bicep), compiled to ARM JSON (below). No parameters needed |
| **Life cycle action**: Execute Script in Cloud Platform, PowerShell, after the template deploys, blocking | [assign-learner.ps1](assign-learner.ps1): gives the learner **Foundry User** on their resource group |
| **Access control policy** | [acp-v1.json](acp-v1.json) |
| **Instructions** | [instructions/skillable/content.md](../../instructions/skillable/content.md), linked from GitHub |
| **Lab VM** | Azure CLI, PowerShell 7, Python 3.12, `pip install -r requirements.txt`, this repository cloned |

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

[acp-v1.json](acp-v1.json) denies every resource type, SKU and location the lab doesn't deploy. It allows:

| Resource | Limit |
|---|---|
| Foundry account (`Microsoft.CognitiveServices/accounts`) | kind `AIServices`, SKU `S0` |
| Model deployment | `gpt-5-mini`, `GlobalStandard`, up to 60K tokens per minute |
| Foundry project and its connections | |
| API Management | `BasicV2`, 1 unit, and anything inside it (APIs, operations, policies, backends, named values) |
| Log Analytics, Application Insights, and the smart detection alert rule and action group it creates | |
| Role assignments | Made by the template and the life cycle action. The learner is Contributor, so can't make them |

Locations: the resource group's region, plus `global` for the alert rule and action group.

When a lab adds a resource, change the policy and save it in Studio as a **new name** (`acp-v2`, …): Skillable
caches policies by name, so an edit under the same name may not apply. Then retest the whole lab end to end with
the new policy before asking to publish.
