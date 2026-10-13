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

Provided on the lab VM: Azure CLI, PowerShell 7, uv with a Python 3.12 environment holding the packages in
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
uv run scripts/create_agents.py
```

You see `reviewer version 1 (gpt-5-mini)` and `triage version 1 (gpt-5-mini)`. In Lab 1 you change an agent and publish version 2 the same way.

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
uv run src/investigate.py ALT-1001
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

**Time:** about 40 minutes

**Objective:** Operate agents and models through production-grade, governed processes.

## What this lab covers

In Lab 0 you ran one alert and saw that the answer can change between runs. One run tells you nothing about
whether an agent is good enough to release. In this lab you measure the agents against alerts whose correct outcome
is known, and let a **release gate** decide whether a change can ship.

| Piece | What it is |
|---|---|
| **Golden dataset** | data/golden.jsonl: the 14 alerts, each with its expected disposition |
| **Evaluators** | Code checks in src/evaluate.py, plus an AI judge in Foundry |
| **Thresholds** | evals/thresholds.json: the score each metric needs, who owns it, and whether it blocks a release |
| **Evidence Pack** | The folder each gate run writes: what was tested, how it scored, and the verdict |

The gate checks four things:

| Metric | Meaning | Required | Blocks a release |
|---|---|---|---|
| `disposition_accuracy` | The recommendation matches the expected outcome | 85% | Yes |
| `escalation_recall` | Every alert that should be escalated was escalated | 100% | Yes |
| `citation_validity` | Every recommendation cites evidence, and every cited ID exists in the case file | 100% | Yes |
| `reviewer_agreement` | The reviewer accepted the triage recommendation | 70% | No |

## Topics

### Consider when moving to production

- **Maturity model and lifecycle:** where your organisation sits today and what the next stage requires.
- **CI/CD for prompts and agents:** wire the release gate you ran into a pipeline such as GitHub Actions, so it runs on every push. The pipeline needs its own Azure identity (a service principal), which is outside a lab's permissions, so this lab walks through it rather than doing it hands-on.
- **Model retirement:** plan for model versions being retired; re-run the same evaluation against the replacement before switching.

### Covered in this lab

- **Evaluation framework and datasets:** score the agents on a golden dataset with code checks and an AI judge.
- **Release gates:** run the evaluation-and-gate script that a CI/CD pipeline runs on every change, and see a release pass or get blocked on its score.
- **Evidence Pack:** the artefact the release gate produces for approvers and auditors.
- **Observability:** traces and quality metrics for each agent version.

## Prerequisites

Lab 0 completed: you have a `.env` file and both agents are published.

## Steps

### 1. Run the release gate on the current agents (7 min)

```powershell
uv run src/evaluate.py --foundry
```

It runs all 14 golden alerts through triage and reviewer, through the gateway, and takes about a minute.
`--foundry` also starts an evaluation in Foundry, where an AI judge scores whether triage followed its instructions.
You look at that in step 6.

You see `Release gate: PASS` and a score for each metric. Open the Evidence Pack folder it names, and open
`report.md`. One alert, ALT-1006, is usually wrong: triage asks for more information where the expected outcome is
to close. The gate still passes, because 13 of 14 is above the 85% that the accuracy owner requires.

> Your scores can differ slightly from run to run. That's why the thresholds leave room on accuracy, and none on
> missed escalations.

### 2. Ship a prompt "improvement" (8 min)

A colleague proposes a shorter triage prompt: fewer tokens, faster answers, lower cost. Compare it with the current
one:

```powershell
git diff --no-index src/agents/triage.md src/agents/variants/triage.concise.md
```

Apply it, publish it as a new version of the agent, and run the gate:

```powershell
Copy-Item src/agents/variants/triage.concise.md src/agents/triage.md
uv run scripts/create_agents.py
uv run src/evaluate.py
```

You see `Release gate: FAIL`. Open the new `report.md`:

- `citation_validity` is 0: the short prompt no longer asks for evidence, so no recommendation can be traced.
- `escalation_recall` is below 100%: it tells the agent that most alerts are false positives, so some suspicious
  cases are no longer escalated.
- The tokens used are lower. The change does what it promised, and it still can't ship.

The command ended with exit code 1 (check with `$LASTEXITCODE`). In a pipeline, that stops the release.

### 3. Roll back and pass the gate (5 min)

Restore the prompt from source control, publish, and run the gate again:

```powershell
git checkout src/agents/triage.md
uv run scripts/create_agents.py
uv run src/evaluate.py
```

You see `Release gate: PASS`. Foundry kept every version: the failed one is still there, and the agents now run a
newer version with the restored prompt.

### 4. Handle a model retirement (7 min)

Model versions are retired on a schedule, so sooner or later the agents must move to a different model. The same gate
answers whether a replacement is good enough. Publish both agents on `gpt-5-nano` and run it:

```powershell
uv run scripts/create_agents.py gpt-5-nano
uv run src/evaluate.py
```

Read the result. The smaller model is close to the accuracy threshold, so you may see a pass or a fail: either
way, the decision is made by the scores, not by opinion. Then go back to the approved model:

```powershell
uv run scripts/create_agents.py
```

### 5. Open the Evidence Pack (4 min)

Each gate run wrote a folder under `evidence/`. Open the latest passing one:

| File | What it holds |
|---|---|
| `report.md` | The verdict, the score against each threshold and its owner, and the outcome per alert |
| `manifest.json` | Exactly what was tested: agent versions and models, a hash of the dataset and of the thresholds, the source commit |
| `results.json` | The full answer of both agents for every alert |

This is what an approver signs and an auditor reads later. You use it as the approval input in Lab 3.

### 6. See the versions in the traces and in Foundry (6 min)

Every agent call was traced. In the [Azure portal](https://portal.azure.com), open the Application Insights
resource (`appi-…`) in your resource group, select **Logs**, and run:

```kusto
dependencies
| where name startswith "invoke_agent "
| extend agent = tostring(customDimensions["gen_ai.agent.name"]),
         version = toint(customDimensions["gen_ai.agent.version"]),
         model = tostring(customDimensions["gen_ai.response.model"])
| summarize calls = count(), avg_seconds = round(avg(duration) / 1000, 1) by agent, version, model
| order by agent asc, version asc
```

You see one row per agent version: the concise prompt was the fastest version, and it was the one the gate blocked.

Then open the [Foundry portal](https://ai.azure.com), select the `fincrime` project, and open **Evaluations**.
Open **Triage task adherence** and the run from step 1: the AI judge's score and its reasoning for each alert.

### 7. The same gate in a pipeline (3 min)

Open .github/workflows/release-gate.yml. It runs the commands you ran:
connect, publish the agents from the commit, run the gate, and keep the Evidence Pack. A failing gate fails the
job, so the change can't be merged or released.

### If you have time

ALT-1006 is a sanctions name match where the date of birth doesn't match, so it should be closed. Edit
src/agents/triage.md to get it right, publish, and run the gate. Did you fix it
without breaking another alert?

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
