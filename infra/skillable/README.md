# Skillable setup

How the dev lab profile ([224908](https://labondemand.com/LabProfile/224908)) uses this repository.
Each learner gets one Windows VM and one Azure resource group (Cloud Slice) where they are Contributor.

| In Skillable Studio | From this repository |
|---|---|
| **Resource template** on the resource group | [infra/main.bicep](../main.bicep), compiled to ARM JSON (below). No parameters needed |
| **Life cycle action**: Execute Script in Cloud Platform, PowerShell, after the template deploys, blocking | [assign-learner.ps1](assign-learner.ps1): gives the learner **Foundry User** on their resource group |
| **Life cycle action**: Execute Script in Cloud Platform, PowerShell, on Tearing Down, blocking | [teardown.ps1](teardown.ps1): deletes and purges the Foundry account, which Skillable requires for `Microsoft.CognitiveServices/accounts` |
| **Access control policy** | [acp-v4.json](acp-v4.json) |
| **Instructions** | [instructions/skillable/content.md](../../instructions/skillable/content.md), linked from GitHub |
| **Lab VM** | Azure CLI, PowerShell 7, [uv](https://docs.astral.sh/uv/), this repository cloned, and in it `uv venv --python 3.12; uv pip install -r requirements.txt` |
| **Subscription** | Access to `gpt-5-mini` and `gpt-5-nano` (`GlobalStandard`). Without it the template fails with `SpecialFeatureOrQuotaIdRequired`; ask Skillable to enable both models |

## Resource template

The template loads the gateway policy from a separate file, so paste the compiled JSON rather than the Bicep:

```powershell
az bicep build --file infra/main.bicep --stdout | Set-Clipboard
```

## Instructions

Skillable reads one Markdown file. After changing any lab README, rebuild it and commit:

```powershell
uv run scripts/build_skillable.py
```

Linking needs a GitHub account that is a full admin of the repository.

## Access control policy

[acp-v4.json](acp-v4.json) denies every resource type, name, SKU and location the lab doesn't deploy, following the
restrictions Skillable requires for each resource type. It allows:

| Resource | Limit |
|---|---|
| Foundry account (`Microsoft.CognitiveServices/accounts`) | name `aif-*`, kind `AIServices`, SKU `S0` |
| Model deployments | `gpt-5-mini` and `gpt-5-nano`, `GlobalStandard`, up to 60K tokens per minute each |
| Foundry project and its connections | |
| API Management | name `apim-*`, `BasicV2`, 1 unit, and anything inside it (APIs, operations, policies, backends, named values) |
| Log Analytics | name `log-*`, SKU `PerGB2018` |
| Application Insights, and the smart detection alert rule and action group it creates | |
| Template deployments (`Microsoft.Resources/deployments`) | So the learner can run the Bicep |
| Role assignments | Made by the template and the life cycle action. The learner is Contributor, so can't make them |
| Policy assignment `approved-ai-models` | Lab 3: made by the template. Built-in "Foundry model deployments should only use approved models", Deny, allowing `gpt-5-mini` and `gpt-5-nano`. Deleted with the resource group |

Every resource must be in the learner's resource group. Locations: the resource group's region, plus `global` for
the alert rule and action group.

When a lab adds a resource, change the policy and save it in Studio as a **new name** (`acp-v5`, …): Skillable
caches policies by name, so an edit under the same name may not apply. Then retest the whole lab end to end with
the new policy before asking to publish.

## App registration (Lab 2, On-Behalf-Of)

In Lab 2 the learner runs [scripts/setup_obo.ps1](../../scripts/setup_obo.ps1), which creates one Entra ID app
registration. Skillable needs these details to tear it down:

| | |
|---|---|
| Created by | The learner, with the Azure CLI, during Lab 2 |
| Name | `prel35-agents-api-<learner resource group name>`, single tenant |
| Exposes | One delegated scope, `api://<app id>/access_as_user`, pre-authorised for the Azure CLI |
| Permission | Delegated `user_impersonation` on Azure Machine Learning Services (`18a66f5f-dbdf-4c17-9dd7-1634712a9cbe`), which is the resource Foundry accepts tokens for |
| Consent | The script grants it for the tenant (`az ad app permission grant`). **This needs a consent-capable role; confirm the learner has it, or the life cycle action must do this step** |
| Credential | One client secret, valid 2 days, stored only as an API Management named value in the learner's resource group |
| Also created | The app's service principal (enterprise application) and one delegated permission grant |
| Teardown | [teardown.ps1](teardown.ps1) deletes the app registration by name, which removes the secret, service principal and grant |

This has not been run yet: it can't be tested in the Microsoft corporate tenant.
