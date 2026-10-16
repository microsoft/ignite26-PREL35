# Lab 3 - Governance

**Time:** about 40 minutes

**Objective:** Put policy, process and data controls around AI so teams can ship safely at scale.

## What this lab covers

Labs 1 and 2 produced evidence: the release gate and the red team. Evidence only matters if someone has to look at
it before an agent goes live, and if it still describes what's running. In this lab you take the triage use case
through intake, and add the controls that intake asks for.

| Piece | What it is |
|---|---|
| **Use-case register** | [governance/use-cases.json](../../governance/use-cases.json): each AI use case, its owner, risk tier and approval |
| **Intake** | [src/intake.py](../../src/intake.py): approves a use case only when its evidence covers what's running now |
| **Data policy** | [governance/data-policy.json](../../governance/data-policy.json): which confidential fields each agent may receive, and why |
| **Human in the loop** | [src/decide.py](../../src/decide.py): the agents recommend, a named analyst decides, and the decision is recorded |
| **Azure Policy** | An assignment on your resource group that denies any model the bank hasn't approved |

## Topics

### Covered in this lab

- **Azure Policy for AI:** restrict allowed models, and see a non-compliant deployment denied.
- **Demand intake and governance processes:** take a use case through intake, using the Evidence Packs from Labs 1 and 2 as the approval input.
- **Human-in-the-loop:** the agents recommend, a named analyst approves or overrules, and every decision is recorded.
- **Data policy and management:** classification and access rules on the data the agents receive.

### Consider when moving to production

- **Governance maturity and toolchain:** where your organisation sits today and which tools support each stage.
- **Cloud Adoption Framework (CAF) for AI:** the reference approach for planning and governing AI adoption.
- **Responsible AI principles:** the principles that the guardrails in Lab 2 enforce.
- **Access control:** least-privilege roles for who can deploy models, call agents and read data. Assigning roles needs Owner or User Access Administrator, which is outside a lab's permissions. See also On-Behalf-Of in Lab 2.
- **Durable approvals:** an approval that waits hours or days for a person, survives restarts and resumes the workflow, for example with Durable Functions.
- **Grounding data governance:** when agents retrieve from a search index or Foundry IQ, apply classification and document-level access in the index, not only in the request.

## Prerequisites

[Lab 2](../lab-2-security-and-trust/README.md) completed, with the approved triage prompt published.

## Steps

### 1. Ask for approval (5 min)

Open [governance/use-cases.json](../../governance/use-cases.json). UC-001 is the triage use case: high risk, owned
by the Head of Financial Crime Operations, and not approved yet. Ask for approval:

```powershell
uv run src/intake.py UC-001
```

It's refused, with a reason for each failed check:

| Check | Why it can fail now |
|---|---|
| Release gate passed on the published agents | The last gate ran on an earlier triage version, before Lab 2 republished it |
| Red team passed on the published agents | The evidence must be for the version that callers get today |
| Data policy enforced | The agents still receive every field in the case file |
| A named analyst decided alerts on the published agents | Nobody has used the decision step yet |

Evidence describes one agent version under one configuration. When either changes, the evidence is out of date, and
intake says so. The next three steps fix each failure.

### 2. Enforce the data policy (12 min)

Open [governance/data-policy.json](../../governance/data-policy.json). Two customer fields are classified
confidential:

- **Account number:** no agent receives it. Triaging an alert never needs it, and a field the agent never sees can't
  leak, whatever an attacker writes into a case file (EXF-01 in Lab 2).
- **Date of birth:** only triage and the reviewer receive it, for a stated purpose. In ALT-1006 the customer has the
  same name as a person on the sanctions list, and the dates of birth are what tell them apart.

Withholding a field the agents need breaks the work; giving every agent every field makes a leak more likely. The
policy records which agent may see what, and why.

Set `"enforce": true` and save the file. From now on, every call strips the withheld fields before the case file
reaches an agent. The data policy changed, so the evidence from Labs 1 and 2 no longer describes what's running. Run
both again:

```powershell
uv run src/evaluate.py
uv run src/redteam.py
```

The gate passes with about the same scores as in Lab 1: the agents didn't need the account number. Both Evidence Packs now
record the data policy they ran under (`data_policy` in `manifest.json`).

### 3. Decide an alert (10 min)

The agents recommend; they never act. Run an alert through the decision step:

```powershell
uv run src/decide.py ALT-1001
```

You see triage's recommendation with its reasons, and the reviewer's verdict. Nothing happens to the case until you
answer. You aren't asked for your name: the decision is recorded under your Entra ID sign-in. Approve it.

Now overrule the agents on another alert:

```powershell
uv run src/decide.py ALT-1014
```

Answer `n`, choose a different decision, and try to leave the reason empty: you can't. Overruling the agents, or
approving against the reviewer, needs a reason an auditor can read later.

Open `evidence/decisions.jsonl`: one line per decision, with who decided, when, what the agents recommended, and
the agent versions they ran on.

### 4. Approve the use case (3 min)

```powershell
uv run src/intake.py UC-001
```

Every check passes, and the approval is recorded in [governance/use-cases.json](../../governance/use-cases.json):
who approved it, when, the agent versions, the data policy, and the Evidence Packs it rests on. Publish a new triage
version and run intake again, and it's refused until the evidence catches up.

### 5. See Azure Policy deny a model (10 min)

Intake governs a use case. Azure Policy governs the platform underneath it, for every team. In the
[Azure portal](https://portal.azure.com), open your resource group, select **Policies**, and open
**Only approved AI models can be deployed**. It uses the built-in policy *Foundry model deployments should only use
approved models*, with the effect **Deny** and two approved models: `gpt-5-mini` and `gpt-5-nano`.

Try to deploy a model that isn't approved:

```powershell
$account = az cognitiveservices account list -g <your-resource-group> --query "[0].name" -o tsv
az cognitiveservices account deployment create -g <your-resource-group> -n $account `
    --deployment-name unapproved --model-name gpt-4.1-mini --model-version 2025-04-14 --model-format OpenAI `
    --sku-name GlobalStandard --sku-capacity 1
```

The deployment is refused, and the error names the policy assignment `approved-ai-models` that denied it. It may also name the lab environment's own policy, which limits what any lab can deploy. Nobody
needs to remember the approved list: the platform enforces it, whoever deploys and however they do it.

## Next

Continue to [Lab 4 - Solution Optimization](../lab-4-solution-optimization/README.md).
