# Lab 0 - Setup

**Time:** about 20 minutes

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

The data is synthetic: 14 alerts for a fictional bank, in data/cases.json.

### What's deployed in your resource group

```text
investigate.py  ──(your Entra ID token)──►  API Management  ──(its managed identity)──►  Foundry project
                                            checks token,                                triage + reviewer agents
                                            rate limit per caller                        gpt-5-mini
                                                                                              │
                                                                         Application Insights ◄┘ traces
```

- **Foundry** accepts Entra ID only: no API keys exist.
- **API Management** is the way apps reach the agents. Only agents published as gateway operations can be called
  through it. You also have direct access to Foundry, for the Foundry portal and the evaluations in Lab 1; in
  production, a private endpoint and no data-plane roles for people would close that path.
- The infrastructure is Bicep, deployed for you before the lab started. The agents are
  defined in src/agents, and you publish them in step 2.

## Topics covered

- Landing zone building blocks: identity, gateway, monitoring
- Infrastructure as code for the platform, and agents as versioned definitions
- Calling agents through a gateway, with no keys

## Prerequisites

Provided on the lab VM: Azure CLI, PowerShell 7, Python 3.12 with the packages in
requirements.txt, and this repository. Your resource group and sign-in details are on the
**Resources** tab of the lab instructions.

## Steps

### 1. Sign in and connect (3 min)

Open a terminal in the repository folder and sign in. Use the username ++@lab.CloudPortalCredential(User1).Username++ and the password
++@lab.CloudPortalCredential(User1).Password++:

```powershell
az login
```

Write your environment's values to `.env`, using your resource group name:

```powershell
.\scripts\env.ps1 -ResourceGroup @lab.CloudResourceGroup(1).Name
```

You see the project endpoint, the model deployment and the gateway URL.

### 2. Publish the agents (2 min)

Each agent is a Markdown file of instructions in src/agents. Publishing creates a new version of
each agent in Foundry:

```powershell
python scripts/create_agents.py
```

You see `reviewer version 1` and `triage version 1`. In Lab 1 you change an agent and publish version 2 the same way.

### 3. Tour the landing zone (5 min)

In the [Azure portal](https://portal.azure.com), open your resource group and find:

| Resource | Look for |
|---|---|
| Foundry (`aif-…`) | **Resource Management > Keys and Endpoint**: local (key) authentication is disabled |
| API Management (`apim-…`) | **APIs > Published agents**: one operation per agent. Open the policy: token check, rate limit, managed identity |
| Application Insights (`appi-…`) | Where agent traces land. You use it in Lab 1 |

Then open the [Foundry portal](https://ai.azure.com), select the `fincrime` project, and open **Agents**. You see
**triage** and **reviewer**. Open triage and read its instructions: they're the same text as
src/agents/triage.md.

### 4. Run your first investigation (5 min)

```powershell
python src/investigate.py ALT-1001
```

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

Now call an agent that isn't published, with a valid token:

```powershell
$token = az account get-access-token --resource https://ai.azure.com --query accessToken -o tsv
curl.exe -s -o NUL -w "%{http_code}`n" -X POST "$gw/agents/unpublished/responses" -H "Authorization: Bearer $token"
```

You get `404`: only agents published as gateway operations can be reached.

### 6. Check your understanding (1 min)

- Your token reaches API Management. What identity does API Management use to reach Foundry?
- Where would you change the triage agent's behaviour, and how would that change reach Foundry?
- Who decides the outcome of an alert?

## Next

Continue to Lab 1 - AgentOps.

===

# Lab 1 - AgentOps

**Objective:** Operate agents and models through production-grade, governed processes.

## Topics

### Covered in this lab

- **Evaluation framework and datasets:** build an evaluation dataset and run evaluators against the agent.
- **Release gates:** run the evaluation-and-gate script that a CI/CD pipeline runs on every change, and see a release pass or get blocked on its score.
- **Evidence Pack:** the artefact the release gate produces for approvers and auditors.
- **Observability:** traces and quality metrics for the agent.

### Consider when moving to production

- **Maturity model and lifecycle:** where your organisation sits today and what the next stage requires.
- **CI/CD for prompts and agents:** wire the release gate you ran into a pipeline such as GitHub Actions, so it runs on every push. The pipeline needs its own Azure identity (a service principal), which is outside a lab's permissions, so this lab walks through it rather than doing it hands-on.
- **Model retirement:** plan for model versions being retired; re-run the same evaluation against the replacement before switching.

## Prerequisites

- _TBD_

## Steps

1. _TBD_

## Next

Continue to Lab 2 - Security and trust.

===

# Lab 2 - Security and trust

**Objective:** Implement security controls and monitoring capabilities for AI solutions.

## Topics

### Covered in this lab

- **Guardrails and content safety:** configure content filters and see harmful content blocked.
- **Real-time agent protection:** Prompt Shields detecting jailbreaks and indirect prompt injection.
- **Red teaming:** run an automated red-team scan and analyse the results.

### Consider when moving to production

- **Identity and delegated access (On-Behalf-Of):** let the agent call tools as the signed-in user. Needs Entra ID app registrations and consent, which are outside a lab's permissions.
- **Defender for AI:** threat protection and alerts for AI workloads, enabled per subscription.
- **AI-SPM (AI Security Posture Management):** discover AI assets and attack paths across the estate.
- **Observability Hub:** a single security view across agents, models and their telemetry.

## Prerequisites

- _TBD_

## Steps

1. _TBD_

## Next

Continue to Lab 3 - Governance.

===

# Lab 3 - Governance

**Objective:** Put policy, process and data controls around AI so teams can ship safely at scale.

## Topics

### Covered in this lab

- **Azure Policy for AI:** restrict allowed models and deployment settings, and see a non-compliant deployment denied.
- **Demand intake and governance processes:** take a use case through intake, using the Evidence Pack from Lab 1 as the approval input.
- **Human-in-the-loop:** the agent pauses before a high-impact action and a person approves or rejects it.
- **Data policy and management:** classification and access rules on the agent's grounding data.

### Consider when moving to production

- **Governance maturity and toolchain:** where your organisation sits today and which tools support each stage.
- **Cloud Adoption Framework (CAF) for AI:** the reference approach for planning and governing AI adoption.
- **Responsible AI principles:** the principles that the guardrails in Lab 2 enforce.
- **Access control:** least-privilege roles for who can deploy models, call agents and read data. Assigning roles needs Owner or User Access Administrator, which is outside a lab's permissions. See also On-Behalf-Of in Lab 2.

## Prerequisites

- _TBD_

## Steps

1. _TBD_

## Next

Continue to Lab 4 - Solution Optimization.

===

# Lab 4 - Solution Optimization

**Objective:** Optimize performance, capacity, and cost.

## Topics

### Covered in this lab

- **Model selection and routing:** route requests across models and compare cost against quality.
- **Gateway policies and semantic caching:** token limits and semantic caching as gateway policies.
- **Chargeback and financial analytics:** attribute token usage to teams and view cost.

### Consider when moving to production

- **PTU and capacity planning:** size provisioned throughput (PTU) against measured demand. Not available in this lab environment.
- **Prompt and KV caching:** reduce repeated-prefix cost and latency at the model layer.
- **Cost optimization:** the wider set of levers, from model choice to batching and quotas.

## Prerequisites

- _TBD_

## Steps

1. _TBD_

## Next

You've completed all the labs.
