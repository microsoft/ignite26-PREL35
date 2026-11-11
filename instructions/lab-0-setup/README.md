# Lab 0 - Setup

> [!NOTE]
> **Time:** about 20 minutes

## What this lab covers

![Contoso head of financial crime operations banner](../images/0-0-contoso-head-of-financial-crime-operations-banner.png)

> **Contoso Bank's Head of Financial Crime Operations asks:** *"Most of these alerts are false positives. Can the
> agents close the case?"*
>
> No. A human analyst makes the final decision on every alert. In this lab you set up the platform the agents run on, and
> run the first alert in the queue: Harbour Lane Bakery.

You connect to your lab environment, tour it as a small AI landing zone, deploy the agents, and run your first
alert. Labs 1 to 4 use this setup.

### The use case

![Contoso financial crime operations logo horizontal](../images/contoso-financial-crime-operations-logo-horizontal.png)

Contoso Bank's transaction monitoring system raises **alerts**: activity that might be money laundering, sanctions
evasion or fraud. A human analyst investigates every alert, and most turn out to be false positives. Three agents help:

| Agent | What it does |
| ----- | ------------ |
| **Triage** | Reads the case file and recommends a disposition (escalate, close as a false positive, or request more information), citing the evidence |
| **Reviewer** | Checks the triage recommendation before a human analyst sees it, and disagrees if the evidence doesn't support it |
| **Evidence** | Researches what a pattern usually means, using only approved topics, never the customer's identity. You use it in Lab 3 |

> [!NOTE]
> No agent can close a case. A human analyst accepts or overrules each recommendation.

The data is synthetic. The 14 alerts are in [`data/cases.json`](../../data/cases.json), introduced in the [introduction](../introduction.md).

### What's deployed in your resource group

![Investigate auth flow diagram](../images/0-0-investigate-auth-flow-diagram.png)

<details>
<summary><strong>📋 What's pre-configured (click to expand for details)</strong></summary>

* **Microsoft Foundry** - Hosts the **fincrime** project, agent versions, evaluations, and model deployments: **gpt-5-mini** for triage and review, **gpt-5-nano** for smaller-model comparisons, **text-embedding-3-small** for vector embeddings, and **decision-1** for typed recommendations and probabilities.
* **Azure AI Search (Foundry IQ)** - Basic tier; lab setup creates and seeds the **fincrime-schemes** index with **financial-crime** typology notes and creates the **fincrime-research** knowledge base for evidence research.
* **Web IQ** - Adds public-web grounding from approved official sources. You connect it to the knowledge base during Lab 0.
* **Azure API Management** - Basic v2 gateway for triage and reviewer calls, providing identity checks, rate limits, configurable Prompt Shields, model routing, and token-cost attribution.
* **Application Insights and Log Analytics** - Collect agent traces, call durations, and gateway token metrics for investigating behavior and comparing costs by team and caller.
* **Azure Policy** - Restricts model deployments to the lab's approved models.
* **Evidence agent** - A hosted Python agent you deploy during Lab 0. Produces cited research briefs through the knowledge base, using approved topics rather than customer identities or case records.

</details>

> [!NOTE]
> **Environment hardening**
> - **Microsoft Foundry** accepts Entra ID only, so no API keys exist.
> - **API Management** is the way apps reach the agents. Only agents published as gateway operations can be called through it. You also have direct access to Foundry, for the Foundry portal and the evaluations in Lab 1; in production, a private endpoint and no data-plane roles for people would close that path.
> - The infrastructure is [Bicep](../../infra/main.bicep), deployed for you before the lab started. The agents are defined in [`src/agents`](../../src/agents), and you publish them in step 2.

## Topics covered

- Landing zone building blocks: identity, gateway, monitoring
- Infrastructure as code for the platform, and agents as versioned definitions
- Calling agents through a gateway, with no keys

## Prerequisites

Provided on the lab VM: Azure CLI, Azure Developer CLI (`azd`) with the extensions listed in [`azure.yaml`](../../azure.yaml), PowerShell, uv with a Python 3.12 environment holding the packages in [`pyproject.toml`](../../pyproject.toml), and this repository. Your resource group and sign-in details are on the **Resources** tab of the lab instructions.

## Steps

### 1. Sign in and connect \(3 min\)

- [] Open a **PowerShell** terminal (Click Start -> type `Terminal`). Navigate to the repository folder, where every command in these labs runs:

```powershell
cd C:\Users\LabUser\Desktop\ignite26-PREL35
```

- [] Sign in to Azure using username `<lab-username>` and the access token `<lab-access-token>`.

```powershell
az login
```

You can skip the "Automatically sign in to Microsoft sites like Bing and Outlook" option when prompted.

Optionally, open the repository in VS Code:

```powershell
code .
```

- [] Prepare the existing lab using your resource group name:

```powershell
uv run setup-lab --resource-group <your-resource-group>
```

uv installs the Python dependencies automatically. Setup saves `.env`, creates and seeds the Search index,
and creates its knowledge base.

### 2. Publish the agents \(2 min\)

Each agent is a Markdown file of instructions in `src/agents`. Publishing creates a new version of each agent in Foundry:

```powershell
uv run create-agents
```

> [!TIP]
> To disable colors in the terminal output, set the environment variable `UV_NO_COLOR` to `1` using
>
> ```powershell
> $env:UV_NO_COLOR = "1"
> ```

- [] You see four agents:

```text
reviewer version 1 (gpt-5-mini)
triage version 1 (gpt-5-mini)
triage-systemone version 1 (gpt-5-mini)
triage-lite version 1 (gpt-5-nano)
```

`triage-lite` has the same instructions as triage on a smaller model; you use it in Lab 4. In Lab 1 you change an
agent and publish version 2 the same way.
`triage-systemone` explains recommendations made by the decision model; you try it in Lab 4.

### 3. Deploy the evidence agent \(4 min\)

Triage and the reviewer are prompt agents, made of instructions only. The evidence agent is a **hosted agent**, Python code
in [`src/agents/evidence`](../../src/agents/evidence) that Foundry runs for you. It researches through the Search knowledge base you created in
step 1, which combines the bank's typology notes with official public sources from **Web IQ**.

- [] Connect Web IQ and prepare the deployment:

```powershell
uv run configure-evidence-agent
```

If you're asked for a key, the command opens the Web IQ portal.

<details>
<summary><strong>🔑 Lab credentials (click to expand when you need to sign in)</strong></summary>

If you need to sign in to any Azure use the following credentials:

- **Username**: `<lab-username>`
- **Temporary Access Pass**: `<lab-access-token>`

</details>

<details>
<summary>Click to expand Web IQ screenshots</summary>

![Web IQ: select the Azure resource for your lab's subscription](../images/0-3-webiq-resource.png)

![Web IQ: Profile Management, with Create API Key under Authentication](../images/0-3-webiq-api-key.png)

</details>

Select the profile linked to the billing resource shown in the terminal. When you see "You're ready to use WebIQ", select **Skip** on that screen. Then create an API key with the **web** service enabled, and paste it into the hidden prompt. The key is saved in `.env`, which Git ignores.

- [] When the command reports that Web IQ and the evidence-agent environment are ready, deploy:

```powershell
azd deploy
```

> [!TIP]
> Foundry builds the agent remotely, which takes a few minutes. Leave it running, open a second terminal in the
> repository folder, and carry on with step 4. You use the evidence agent in Lab 3.

### 4. Tour the landing zone \(3 min\)

- [] Open the **Azure portal** `<azure-portal-url>`. Sign in using `<lab-username>` and the access token `<lab-access-token>`.

Find your resource group <your-resource-group> and deployed resources:

![Your resource group, with the Foundry, API Management and Application Insights resources](../images/0-4-resource-group.png)

| Resource | Look for |
| -------- | -------- |
| Foundry (`<foundry-name>`) | **Resource Management &gt; Keys and Endpoint**: local (key) authentication is disabled |
| API Management (`<apim-name>`) | **APIs &gt; Published agents**: one operation per agent. Select **All operations**, then the **&lt;/&gt;** icon under **Inbound processing** to open the policy: token check, rate limit, managed identity |
| Application Insights (`<appinsights-name>`) | Where agent traces land. You use it in Lab 1 |

<details>
<summary>Click to expand Environment check screenshots</summary>

![Foundry: Keys and Endpoint, with API key authentication disabled](../images/0-4-foundry-keys.png)

![API Management: APIs, with Published agents](../images/0-4-apim-published-agents.png)

![API Management: the Published agents API, with All operations selected](../images/0-4-apim-policy.png)

</details>

Open the **Foundry portal** at `https://ai.azure.com`, or open your Foundry resource in the Azure portal and select **Go to Foundry Portal**.

- [] Open the Foundry Portal

Select the `fincrime` project, and open **Build** ──► **Agents**. You see the four agents you published: **triage**, **reviewer**, **triage-lite** and **triage-systemone**. Open triage and read its instructions. They're the same text as [`src/agents/triage.md`](../../src/agents/triage.md). When `azd deploy` has finished, **Agents** also shows **evidence-agent** as a hosted agent.

![Foundry agents list](../images/0-4-foundry-agents-list.png)

### 5. Run your first investigation \(4 min\)

The first alert in the queue is ALT-1001. Harbour Lane Bakery Ltd has been depositing cash just under the reporting
threshold every few days. Deposits kept just under the threshold can be a sign of structuring, where someone splits
cash to avoid a report.

<details>
<summary>What's in ALT-1001?</summary>

**Harbour Lane Bakery Ltd** is a bakery with complete customer checks and an expected monthly volume of GBP 30,000. The alert flags five cash deposits between GBP 9,400 and GBP 9,900 over six days: repeated amounts just below the lab's reporting threshold.

The case contains 17 transactions. The five deposits are `T-00001` to `T-00005`, alongside 12 other transactions. A previous investigation, `CASE-7781`, closed a seasonal cash increase without action. The document list is empty.

**What the models receive:** triage gets this alert, the customer profile, transactions, prior cases and documents, plus the bank's sanctions, politically exposed person (PEP), high-risk jurisdiction and counterparty identity records. The reviewer gets its own copy of the case and triage's answer. At this stage the data policy is not enforced, so no case fields are withheld; Lab 3 changes that.

The script loads these records for you from `data/cases.json`.

</details>

- [] In the terminal, run the investigation script for ALT-1001:

```powershell
uv run src/investigate.py ALT-1001
```

You see the triage recommendation, then the reviewer's verdict. Triage usually recommends `escalate`, and lists the
IDs of the deposits it relied on, such as `T-00001`, so every claim points to something in the case file. The reviewer
usually agrees. Nothing happens to the case. A human analyst decides it, and you do that in Lab 3.

Run it a second time. The wording, and sometimes the recommendation, can change between runs. Lab 1 measures this variation with evaluations across all 14 alerts.

### 6. Get refused on purpose \(3 min\)

The gateway only accepts requests that carry an Entra ID access token in the `Authorization` header.
- [] Call it without one:

```powershell
curl.exe -s -o NUL -w "%{http_code}`n" -X POST "<apim-gateway-url>/agents/triage/responses"
```

You get `401`, because the gateway rejects the request before it reaches Foundry.

- [] Now get an Entra ID token for your signed-in user and call an agent that isn't published:

```powershell
$token = az account get-access-token --resource https://ai.azure.com --query accessToken -o tsv
curl.exe -s -o NUL -w "%{http_code}`n" -X POST "<apim-gateway-url>/agents/unpublished/responses" -H "Authorization: Bearer $token"
```

You get `404`, because only agents published as gateway operations can be reached.

### 7. Check your understanding \(1 min\)

<details>
<summary>Your token reaches API Management. What identity does API Management use to reach Foundry?</summary>

*Its own managed identity. Callers never get access to Foundry through the gateway.*

</details>

<details>
<summary>Where would you change the triage agent's behavior, and how would that change reach Foundry?</summary>

*In `src/agents/triage.md`, published as a new version with `uv run create-agents`.*

</details>

<details>
<summary>Who decides the outcome of an alert?</summary>

*A named human analyst, who accepts or overrules what the agents recommend.*

</details>

## Click next to continue

Continue to [Lab 1 - AgentOps](../lab-1-agentops/README.md).
