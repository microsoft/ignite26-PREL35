# Lab 3 - Governance

**Time:** about 40 minutes

**Objective:** Put policy, process and data controls around AI so teams can ship safely at scale.

## What this lab covers

> **Contoso Bank's Money Laundering Reporting Officer asks:** "Who approved these agents, and is a person really
> deciding each alert?" **The Data Protection Officer adds:** "What customer data does each agent see, and does any
> of it leave the bank?"
>
> You answer both, and decide two alerts yourself: ALT-1008, Lukas Weber, whose account is passing money to a crypto
> exchange, and ALT-1014, Pellham Motors.

Labs 1 and 2 produced evidence: the release gate and the red team. Evidence only matters if someone has to look at it before an agent goes live, and if it still describes what's running. In this lab you take the triage use case through intake, and add the controls that intake asks for.

| Piece | What it is |
| ----- | ---------- |
| **Use-case register** | [`governance/use-cases.json`](../../governance/use-cases.json): each AI use case, its owner, risk tier and approval |
| **Intake** | [`src/intake.py`](../../src/intake.py): approves a use case only when its evidence covers what's running now |
| **Data policy** | [`governance/data-policy.json`](../../governance/data-policy.json): which confidential fields and documents each agent may receive, and why |
| **Evidence agent** | Public-web research that never sees the customer's identity: the bank's decision on external grounding, enforced in code |
| **Human in the loop** | [`src/decide.py`](../../src/decide.py): the agents recommend, a named human analyst decides, and the decision is recorded |
| **Azure Policy** | An assignment on your resource group that denies any model the bank hasn't approved |

## Topics

### Covered in this lab

- **Azure Policy for AI:** restrict allowed models, and see a non-compliant deployment denied.
- **Demand intake and governance processes:** take a use case through intake, using the Evidence Packs from Labs 1 and 2 as the approval input.
- **Human-in-the-loop:** the agents recommend, a named human analyst approves or overrules, and every decision is recorded.
- **Data policy and management:** classification and access rules on the data the agents receive.
- **External grounding:** public-web research limited to approved topics, so no customer data leaves the bank.

### Consider when moving to production

- **Governance maturity and toolchain:** where your organisation sits today and which tools support each stage.
- **Cloud Adoption Framework (CAF) for AI:** the reference approach for planning and governing AI adoption.
- **Responsible AI principles:** the principles that the guardrails in [Lab 2](../lab-2-security-and-trust/README.md) enforce.
- **Access control:** least-privilege roles for who can deploy models, call agents and read data. Assigning roles needs Owner or User Access Administrator, which is outside a lab's permissions. See also On-Behalf-Of in Lab 2.
- **Durable approvals:** an approval that waits hours or days for a person, survives restarts and resumes the workflow, for example with Durable Functions.
- **Grounding data governance:** when agents retrieve from a search index or Foundry IQ, apply classification and document-level access in the index, not only in the request.

## Prerequisites

Lab 2 completed, with the approved triage prompt published.

## Steps

### 1. Ask for approval \(5 min\)

Open [`governance/use-cases.json`](../../governance/use-cases.json). `UC-001` is the triage use case: high risk, owned by the Head of Financial Crime Operations, and not approved yet. Ask for approval:

```powershell
uv run src/intake.py UC-001
```

It's refused, with a reason for each failed check:

| Check | Why it can fail now |
| ----- | ------------------- |
| Release gate passed on the published agents | The last gate ran on an earlier triage version, before Lab 2 republished it |
| Red team passed on the published agents | The evidence must be for the version that callers get today |
| Data policy enforced | The agents still receive every field in the case file |
| A named human analyst decided alerts on the published agents | Nobody has used the decision step yet |

Evidence describes one agent version under one configuration. When either changes, the evidence is out of date, and intake says so. The next steps fix each failure.

### 2. Enforce the data policy \(10 min\)

Open [`governance/data-policy.json`](../../governance/data-policy.json). Two customer fields are classified confidential:

- **Account number:** no agent receives it. Triaging an alert never needs it, and a field the agent never sees can't leak, whatever an attacker writes into a case file (`EXF-01` in Lab 2).
- **Date of birth:** only triage and the reviewer receive it, for a stated purpose. In `ALT-1006` the customer has the same name as a person on the sanctions list, and the dates of birth are what tell them apart.

The policy also covers documents. Restricted documents (classification 3) go to no agent. `ALT-1005`, Nordwind Trading, has one: a production order from law enforcement, limited to senior investigators and the MLRO. If an agent quoted it in a recommendation, the customer could be tipped off. With the policy enforced, the agents see only that `DOC-203` exists and is withheld, so they can't mistake it for a case with no documents.

Withholding a field the agents need breaks the work; giving every agent every field makes a leak more likely. The policy records which agent may see what, and why.

- [] At the top of the file, change `"enforce": false` to `"enforce": true` and save it. From now on, every call strips the withheld fields before the case file reaches an agent. The data policy changed, so the evidence from Labs 1 and 2 no longer describes what's running. Run both again:

```powershell
uv run src/evaluate.py
uv run src/redteam.py
```

The gate passes with about the same scores as in Lab 1: the agents didn't need the account number. Both Evidence Packs now record the data policy they ran under (`data_policy` in `manifest.json`).

### 3. Research before you decide \(7 min\)

`ALT-1008` is next in the queue. Lukas Weber's account received dozens of small payments from strangers and forwarded them to a crypto exchange within hours. He says a remote "payment assistant" job told him to. Is he laundering money, or was he recruited as a money mule without knowing it?

The human analyst wants to know how this pattern usually works. The obvious way, searching the web for the customer's name, would send personal data outside the bank, and the DPO won't allow it. The bank decided on a narrower option instead, and the evidence agent enforces it in code:

- Its research tool accepts only one of eight [approved topics](../../src/agents/evidence/topics.json) (`src/agents/evidence/topics.json`), such as `money-mule-recruitment`. Names, account numbers and case text never reach the knowledge base or the web.
- Public sources are limited to official sites (`justice.gov`, `fincen.gov`), and public cases are comparisons, never evidence about this customer.

- [] Ask for a brief, from the repository folder. The scenario describes the pattern without naming anyone, and the brief takes up to three minutes:

```powershell
azd ai agent invoke evidence-agent "Review this fictional, PII-free scenario: unfamiliar incoming transfers are forwarded to a cryptocurrency exchange; the holder says a remote payment-assistant job requires this, but no employer contract or sales records are supplied. Provide a cited evidence brief with alternatives and missing evidence, not a decision." --protocol responses --new-session
```

The brief keeps what was supplied apart from the explanation, the public context and the alternatives, and ends with the evidence that's missing. It doesn't recommend a decision: that comes next, from triage and from you.

### 4. Decide an alert \(9 min\)

The agents recommend; they never act. 

- [] Run `ALT-1008` through the decision step:

```powershell
uv run src/decide.py ALT-1008
```

You see triage's recommendation with its reasons, and the reviewer's verdict. Nothing happens to the case until you answer **Approve the recommendation? [y/n]**. You aren't asked for your name: the decision is recorded under your Entra ID sign-in. If triage recommends `escalate`, answer `y`: the brief told you what a mule case looks like and what's missing, and establishing that is an investigator's job. A reason is optional when you approve.

- [] Now overrule the agents on `ALT-1014`, Pellham Motors, the car dealer from Lab 2:

```powershell
uv run src/decide.py ALT-1014
```

Answer `n`, choose a different decision, and try to leave the reason empty: you can't. Overruling the agents, or approving against the reviewer, needs a reason an auditor can read later.

Open `evidence/decisions.jsonl`: one line per decision, with who decided, when, what the agents recommended, and the agent versions they ran on.

### 5. Approve the use case \(3 min\)

```powershell
uv run src/intake.py UC-001
```

Every check passes, and the approval is recorded in [governance/use-cases.json](../../governance/use-cases.json): who approved it, when, the agent versions, the data policy, and the Evidence Packs it rests on.

Don't publish anything now. If anyone publishes a new triage version later, intake refuses the use case again until the gate, the red team and a decision have run on that version.

### 6. See Azure Policy deny a model \(6 min\)

Intake governs a use case. Azure Policy governs the platform underneath it, for every team. In the **Azure portal** +++<azure-portal-url>+++, open your resource group, select **Policies**, and open **Only approved AI models can be deployed**. It uses the built-in policy *Foundry model deployments should only use approved models*, with the effect **Deny** and four approved models: `gpt-5-mini` and `gpt-5-nano` for the agents, `text-embedding-3-small` for the Search knowledge base, and `decision-1`, a preview decision model.

![Azure Policy: the Only approved AI models can be deployed assignment](../images/3-6-policy-wide.png)

![Azure Policy: the assignment's parameters, with the effect Deny](../images/3-6-policy.png)

Try to deploy a model that isn't approved:

```powershell
az cognitiveservices account deployment create -g <your-resource-group> -n <foundry-name> `
    --deployment-name unapproved --model-name gpt-4.1-mini --model-version 2025-04-14 --model-format OpenAI `
    --sku-name GlobalStandard --sku-capacity 1
```

The deployment is refused. The error code depends on what your lab account may do:

- **`RequestDisallowedByPolicy`:** your account can deploy models, and Azure Policy stopped this one. The error names the policy assignment `approved-ai-models`. It may also name the lab environment's own policy, which limits what any lab can deploy.
- **`AuthorizationFailed`:** your account can't deploy models at all, so Azure refused the request before it checked any policy.

These are two separate controls. Role-based access decides *who* can deploy; Azure Policy decides *what* can be deployed, even by someone who is allowed to deploy. Nobody needs to remember the approved list: the platform enforces it, whoever deploys and however they do it.

## Click next to continue

Continue to [Lab 4 - Solution Optimization](../lab-4-solution-optimization/README.md).
