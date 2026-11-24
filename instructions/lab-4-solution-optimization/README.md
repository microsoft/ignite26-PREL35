# Lab 4 - Solution Optimization

> [!NOTE]
> **Time:** about 40 minutes
>
> **Objective:** Optimize performance, capacity, and cost.

## What this lab covers

![Contoso finance controller banner](../images/4-0-contoso-finance-controller-banner.png)

> **Contoso Bank's Finance Controller asks:** *"How much does investigating each alert cost compared with a manual investigation, and which team pays?"*
>
> You answer with numbers, and test a cheaper model on the alerts that matter. ALT-1005, a payment from Nordwind
> Trading to a counterparty on a sanctions list, is the one a cheaper model must not miss.

The agents are approved and running. Now someone asks what they cost, who is paying, and whether it could be
cheaper without being worse. In this lab you answer with numbers: the cost of every release-gate run, a cheaper
model for part of the work and what it does to quality, a decision model that chooses without generating an
explanation, a limit on what any caller can consume, and the bill split by the teams that ran up the tokens.

| Piece | What it is |
| ----- | ---------- |
| **Prices** | [evals/prices.json](../../evals/prices.json): the price per million tokens of each model, including cached input |
| **Model routing** | A gateway switch that sends low-risk alerts to `triage-lite`: the triage instructions on `gpt-5-nano` |
| **Decision model** | `decision-1`: a typed recommendation and probabilities, with GPT used only when an explanation is needed |
| **Token limit** | A gateway policy that caps the tokens each caller can use per minute |
| **Cost attribution** | The gateway records the tokens of every call by cost center, caller and agent, in Application Insights |

## Topics

### Covered in this lab

- **Model selection and routing:** route requests across models and compare cost against quality.
- **Decision models:** separate choosing a disposition from writing an explanation, and measure the cost of each.
- **Gateway policies:** token limits per caller as a gateway policy.
- **FinOps and cost attribution:** cost per run and per alert, and token cost attributed to cost centers and callers.

### Consider when moving to production

- **Semantic caching:** the gateway answers a request from a cache when a similar one was answered before. It needs an embeddings model and a Redis cache, and pays off when requests repeat. Every case file here is different, so it would rarely hit.
- **PTU and capacity planning:** size provisioned throughput (PTU) against measured demand. Not available in this lab environment.
- **Budgets and alerts:** Azure Cost Management budgets per subscription or resource group, and alerts on the token metrics before a team overspends.
- **Cost optimization:** the wider set of levers, from model choice to batching and quotas.

## Prerequisites

[Lab 3](../lab-3-governance/README.md) completed.

## Steps

### 1. What a release costs \(5 min\)

<details>
<summary>Case and model inputs</summary>

You reuse the 14 alerts described in Lab 1, now with Lab 3's data policy enforced. Each triage call receives its filtered case and screening lists; the reviewer also receives triage's answer. The golden answers remain outside the model inputs. You are measuring the cost of the same investigations, not introducing a new dataset.

</details>

- [] Run the release gate:

```powershell
uv run src/evaluate.py
```

Under the scores you see the cost of the run, about 3 cents for 14 alerts, and the cost per alert.

- [] Open the `report.md` it names. The last column shows which model triaged each alert, and the last line the total.

The cost comes from the tokens each call used and evals/prices.json. Output tokens cost eight times as much as input tokens, and input the model saw recently, such as the same instructions and screening lists, is cached and costs a tenth. Prompt caching happens on its own; nothing in this lab sets it up.

### 2. Route low\-risk alerts to a smaller model \(8 min\)

`gpt-5-nano` costs a fifth of `gpt-5-mini`. The gateway can send alerts with a low risk score to `triage-lite`, which has the same instructions on the smaller model.

- [] Turn routing on, wait a minute, and run the gate:

```powershell
uv run set-gateway model-routing on
uv run src/evaluate.py
```

- [] Compare the result with step 1:

- **Cost:** about a tenth lower. Only the four alerts scored 65 or less go to `triage-lite`, and the reviewer still runs on `gpt-5-mini` for every alert.
- **Quality:** the blocking metrics usually still pass, sometimes right at the threshold, when the smaller model gets one of its four alerts wrong. Look at the alerts on `gpt-5-nano` in the report. They include ALT-1005, a sanctions hit. ALT-1005 scores only 56, because the risk score measures how unusual the activity is. Missing a sanctions hit is still one of the most serious mistakes triage can make.
- **Reviewer agreement:** usually lower. The reviewer disagrees more often with the smaller model's reasons, and every disagreement is work for a human analyst, which costs far more than the tokens saved.

- [] Turn routing off again:

```powershell
uv run set-gateway model-routing off
```

### 3. Try a decision model \(10 min\)

A language model such as GPT writes its answer as text. A **decision model** picks from a fixed set of options using criteria you give it. Here, `decision-1` picks `escalate`, `close_false_positive` or `request_info` from the case evidence, and returns the choice with a probability for each option. It writes no explanation, so you pay for GPT only when a human analyst needs to read the reasons.

`decision-1` is already deployed in your Foundry resource. Its rubric is in `src/decisions/prompts/decision1.md`. The agent that writes its explanations, `triage-systemone`, was published in Lab 0. These scripts make billable calls with your Azure CLI sign-in.

#### Choose, explain, review

<details>
<summary>ALT-1001: case and model inputs</summary>

This is ALT-1001, the bakery with five near-threshold cash deposits, a prior seasonal clearance and no supporting documents. Decision-1 receives the case and screening lists with the data policy enforced, plus the local triage rubric and the three permitted dispositions. The GPT explanation agent gets that same filtered case and Decision-1's choice. Decision-1 then reviews the case, chosen disposition and written reasons; the golden answer is not an input.

</details>

- [] Run Harbour Lane Bakery through all three steps:

```powershell
uv run python src/decisions/triage_systemone.py ALT-1001
```

Decision-1 picks the disposition. The GPT agent then writes cited reasons, gaps and conflicts, but it can't change the disposition. Finally, Decision-1 reviews the recommendation. **What was called, and what it cost** lists each call, including any extra GPT call to repair the explanation.

- [] Compare the cost of the decision alone with the total, and check that the explanation cites records from the case file. Add `--json` to see the full record.

#### Choose and review without GPT

<details>
<summary>ALT-1008: case and model inputs</summary>

This is ALT-1008, Lukas Weber's ten incoming payments, crypto-exchange payment and customer-uploaded remote-job message. Decision-1 triage receives the filtered case and screening lists. Its reviewer receives the case and proposed disposition, but no written reasons or triage confidence. For `--challenge`, the script uses the golden answer locally to select the two wrong dispositions, then sends each to the reviewer as a separate proposal without revealing that it is wrong.

</details>

- [] Run Lukas Weber's case with Decision-1 only:

```powershell
uv run python src/decisions/investigate_decisions.py ALT-1008 --challenge
```

You see the disposition, the reviewer's verdict, and the confidence of each. The expected disposition is `escalate`. `--challenge` also asks the reviewer about the two wrong dispositions, and it should reject both. No GPT call is made, so there's no written explanation.

- [] Check that the reviewer rejected both wrong dispositions, and consider whether a human analyst reviewing this case would need a written explanation.

#### Compare the two approaches

- [] Start the comparison on seven of the 14 alerts. It takes 1 to 1.5 minutes, so read on while it runs:

```powershell
uv run python src/decisions/compare_investigations.py ALT-1001 ALT-1002 ALT-1005 ALT-1006 ALT-1008 ALT-1010 ALT-1013 --workers 2
```

The script runs the same seven cases, with the data policy applied, through both approaches: first the GPT agents through the gateway, then Decision-1 directly. The alerts include escalations, false positives, requests for more information, a real sanctions hit and a false name match. The expected answers are used only for scoring.

<details>
<summary>What's in these seven alerts?</summary>

| Alert | Case records | Golden answer |
| --- | --- | --- |
| ALT-1001 | The bakery's five near-threshold cash deposits and a prior seasonal investigation closed without action. | `escalate` |
| ALT-1002 | Oliver Brandt's property-sale payment and solicitor's statement `DOC-201`. | `close_false_positive` |
| ALT-1005 | Nordwind's payment to Viktor Salazar-Orlov, counterparty identity records and sanctions entry `SAN-0091`. | `escalate` |
| ALT-1006 | Counterparty identity records and a sanctions entry with the same name but different dates of birth and nationalities. | `close_false_positive` |
| ALT-1008 | Lukas Weber's incoming payments, crypto-exchange payment and customer-uploaded job message. | `escalate` |
| ALT-1010 | Tobias Klein's EUR 38,000 incoming payment, incomplete customer checks and empty document list. | `request_info` |
| ALT-1013 | Rosalind Achebe's cash withdrawals, overdue customer checks and empty document list. | `request_info` |

Both paths receive each case separately, with the same screening lists and data-policy filtering. In ALT-1005, restricted document `DOC-203` is reduced to its ID and a withheld marker: neither path can use the production order's contents. GPT's reviewer sees the written recommendation; Decision-1's reviewer sees only the proposed disposition. Neither path receives the golden-answer column above.

</details>

- [] When it finishes, compare **Cost per alert**, **Triage matches golden**, **Escalation recall** and **Reviewer disagrees**. Check whether both approaches escalated ALT-1005, the sanctions hit. In **Different results**, a check mark means triage matched the expected answer.

Decision-1 is cheaper ($0.042 per million input tokens, and output is free), but the human analyst deciding gets a label without reasons, and the calls skip the gateway's controls. Results are saved in `evidence/investigation-comparison/`. To compare all 14 alerts, run the command again without the alert IDs.

### 4. Limit tokens per caller \(5 min\)

A single runaway script can use the capacity every team shares. The gateway limits the tokens each caller can use per minute; the default is high.

<details>
<summary>ALT-1001: case and model inputs</summary>

You rerun ALT-1001 with the same bakery profile, deposits, prior case and screening lists as before, now with Lab 3's data policy enforced. Triage sees the filtered case; the reviewer also sees triage's answer. No extra evidence is added. Only the gateway's token allowance changes, so this exercise is about waiting and retries, not a new alert.

</details>

- [] Set the limit below what one alert needs:

```powershell
uv run set-gateway token-limit 3000
```

- [] Wait a minute, then run one alert with the client's retries shown:

```powershell
$env:OPENAI_LOG = 'info'
uv run src/investigate.py ALT-1001
Remove-Item Env:OPENAI_LOG
```

The triage call uses most of the 3,000 tokens. The reviewer call gets HTTP 429 from the gateway, with a `Retry-After` header, and the client waits and retries until the minute resets. The work still finishes, just more slowly. The limit keeps capacity free for other callers.

- [] Restore the default:

```powershell
uv run set-gateway token-limit 200000
```

### 5. Attribute the cost \(9 min\)

Every call through the gateway carries a cost center (`x-cost-center`). The release gate charges model validation, the red team security testing, and a human analyst's investigation financial crime operations. The gateway records the tokens of each call by cost center, caller and agent.

- [] In the **Azure portal** `<azure-portal-url>`, open the Application Insights resource (`<appinsights-name>`) in your resource group and select **Logs**. Switch to **KQL mode** if needed, paste this query into the query editor and select **Run**.

Token metrics take a few minutes to arrive, so recent calls may not show at first:

```kusto
let prices = datatable(Agent: string, input: real, cached: real, output: real) [
    "triage", 0.25, 0.025, 2.0,
    "reviewer", 0.25, 0.025, 2.0,
    "triage-lite", 0.05, 0.005, 0.4
];
customMetrics
| where name in ("Prompt Tokens", "Prompt Cached Tokens", "Completion Tokens")
| extend CostCenter = tostring(customDimensions["Cost center"]), Agent = tostring(customDimensions["Agent"]),
         Caller = tostring(customDimensions["Caller"])
| summarize tokens = sum(valueSum) by CostCenter, Caller, Agent, name
| evaluate pivot(name, sum(tokens))
| lookup prices on Agent
| extend usd = ((['Prompt Tokens'] - ['Prompt Cached Tokens']) * input + ['Prompt Cached Tokens'] * cached + ['Completion Tokens'] * output) / 1000000
| summarize tokens = sum(['Prompt Tokens'] + ['Completion Tokens']), usd = round(sum(usd), 4) by CostCenter
| order by usd desc
```

Model validation costs the most because you ran the release gate more often than you triaged single alerts.

![Logs: token cost by cost center](../images/4-4-cost-by-cost-center.png)

- [] Change `by CostCenter` on the last `summarize` line to `by Caller` and run the query again to see the cost per person.

A call that sends no cost center is recorded as `unassigned`. It still counts, but nobody can be charged for it. The lab's gateway clients always send one. In production, the gateway would refuse calls without one, or derive it from the caller's identity, because callers can put any value in a header.

### 6. Wrap up \(3 min\)

You can now answer the Finance Controller with the three numbers a FinOps review asks for: what each alert costs
(from the gate), who consumed it (per cost center and caller, from the gateway), and what a cheaper option would do
to quality (from the gate, with routing on). The decision-model experiment also shows which
alerts need a written explanation, and what that explanation adds to the cost.

### 7. Check your understanding \(1 min\)

<details>
<summary>What should you compare before deciding that a cheaper model is a better choice?</summary>

*Cost per alert, the gate's quality scores, and how often the reviewer disagrees. In step 2, routing cut cost by about a tenth, but each extra disagreement is work for a human analyst, which costs more than the tokens saved.*

</details>

<details>
<summary>When is separating a decision from its explanation useful?</summary>

*When most alerts need only a disposition. Decision-1 picks the disposition cheaply, and you pay for a GPT explanation only when a human analyst needs to read the reasons.*

</details>

<details>
<summary>What do you need to know to hold teams accountable for AI spending?</summary>

*The tokens each call used, the price of each model, and which cost center and caller made the call. Calls that skip the gateway, like the direct Decision-1 calls in step 3, need their own tracking.*

</details>

## You've completed the labs

Each stakeholder's question, and where you recorded the answer:

| Asked by | Their question | Your answer, and where it's recorded |
| -------- | -------------- | ------------------------------------ |
| Head of Financial Crime Operations | Can the agents close the case? | No. A human analyst makes the final decision, recorded in `evidence/decisions.jsonl` |
| Head of Model Risk Management | How do we know a change is still good enough? | The release gate on every change: the Evidence Pack from Lab 1 |
| Chief Information Security Officer | What stops a letter telling the agent to close its own case? | The prompt, Prompt Shields at the gateway and the guardrail: the red team Evidence Pack from Lab 2 |
| Data Protection Officer | What does each agent see, and does anything leave the bank? | The data policy, and research by approved topic only: `governance/data-policy.json` |
| Money Laundering Reporting Officer | Who approved this? | Intake, on current evidence: `governance/use-cases.json` |
| Finance Controller | What does it cost, and who pays? | Cost per alert and per cost center: the gate report and Application Insights |

You can follow the same steps for any agent you take to production.
