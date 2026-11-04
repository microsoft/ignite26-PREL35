# Lab 4 - Solution Optimization

**Time:** about 40 minutes

**Objective:** Optimize performance, capacity, and cost.

## What this lab covers

> **Contoso Bank's Finance Controller asks:** "What does each alert cost compared with an analyst doing it by hand,
> and which team pays?"
>
> You answer with numbers, and test a cheaper model on the alerts that matter. ALT-1005, a payment from Nordwind
> Trading to a counterparty on a sanctions list, is the one a cheaper model must not miss.

The agents are approved and running. Now someone asks what they cost, who is paying, and whether it could be
cheaper without being worse. In this lab you answer with numbers: the cost of every release-gate run, a cheaper
model for part of the work and what it does to quality, a limit on what any caller can consume, and the bill split
by the teams that ran up the tokens.

| Piece | What it is |
|---|---|
| **Prices** | [evals/prices.json](../../evals/prices.json): the price per million tokens of each model, including cached input |
| **Model routing** | A gateway switch that sends low-risk alerts to `triage-lite`: the triage instructions on `gpt-5-nano` |
| **Token limit** | A gateway policy that caps the tokens each caller can use per minute |
| **Cost attribution** | The gateway records the tokens of every call by cost center, caller and agent, in Application Insights |

## Topics

### Covered in this lab

- **Model selection and routing:** route requests across models and compare cost against quality.
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

### 1. What a release costs (5 min)

Run the release gate:

```powershell
uv run src/evaluate.py
```

Under the scores you see the cost of the run and the cost per alert: about 3 cents for 14 alerts. Open the
`report.md` it names: the last column shows which model triaged each alert, and the last line the total.

The cost comes from the tokens each call used and [evals/prices.json](../../evals/prices.json). Output tokens cost
eight times as much as input tokens, and input the model saw recently, such as the same instructions and screening
lists, is cached and costs a tenth. Prompt caching happens on its own; nothing in this lab sets it up.

### 2. Route low-risk alerts to a smaller model (12 min)

`gpt-5-nano` costs a fifth of `gpt-5-mini`. The gateway can send alerts with a low risk score to `triage-lite`, which
has the same instructions on the smaller model. Turn it on, wait a minute, and run the gate:

```powershell
uv run set-gateway model-routing on
uv run src/evaluate.py
```

Compare the result with step 1:

- **Cost:** about a tenth lower. Only the four alerts scored 65 or less go to `triage-lite`, and the reviewer still
  runs on `gpt-5-mini` for every alert.
- **Quality:** the blocking metrics usually still pass, sometimes right at the threshold, when the smaller model gets
  one of its four alerts wrong. Look at the alerts on `gpt-5-nano` in the report: they include ALT-1005, a sanctions
  hit. A risk score measures how unusual the activity is, not how serious a mistake would be.
- **Reviewer agreement:** usually lower. The reviewer disagrees more often with the smaller model's reasons, and
  every disagreement is work for an analyst, which costs far more than the tokens saved.

The gate turns "is the cheaper model good enough?" into numbers anyone can check. Turn routing off again:

```powershell
uv run set-gateway model-routing off
```

### 3. Limit tokens per caller (8 min)

A single runaway script can use the capacity every team shares. The gateway limits the tokens each caller can use
per minute; the default is high. Set it below what one alert needs:

```powershell
uv run set-gateway token-limit 3000
```

Wait a minute, then run one alert with the client's retries shown:

```powershell
$env:OPENAI_LOG = 'info'
uv run src/investigate.py ALT-1001
Remove-Item Env:OPENAI_LOG
```

The triage call uses most of the 3,000 tokens. The reviewer call gets HTTP 429 from the gateway, with a
`Retry-After` header, and the client waits and retries until the minute resets. The work still finishes, slowly:
the limit protects everyone else's capacity, not the caller's deadline. Restore the default:

```powershell
uv run set-gateway token-limit 200000
```

### 4. Attribute the cost (12 min)

Every call through the gateway carries a cost center (`x-cost-center`): the release gate charges model validation,
the red team security testing, and an analyst's investigation financial crime operations. The gateway records the
tokens of each call by cost center, caller and agent.

In the [Azure portal](https://portal.azure.com), open the Application Insights resource (`appi-…`) in your resource
group and select **Logs**. Switch to **KQL mode** if needed, paste this query into the query editor and select
**Run**. Token metrics take a few minutes to arrive, so recent calls may not show at first:

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

Model validation is most of the bill: you ran the gate more often today than anyone triaged an alert. Then change
`by CostCenter` on the last `summarize` line to `by Caller` to see the cost per person.

A call that sends no cost center is recorded as `unassigned`: it still counts, but nobody can be charged for it. The
lab's scripts always send one. In production, the gateway would refuse calls without one, or derive it from the
caller's identity, because a header alone is whatever the caller says it is.

### 5. Wrap up (3 min)

You can now answer the Finance Controller with the three numbers a FinOps review asks for: what each alert costs
(from the gate), who consumed it (per cost center and caller, from the gateway), and what a cheaper option would do
to quality (from the gate, with routing on).

## You've completed the labs

Every Contoso Bank stakeholder now has an answer, backed by something you produced:

| Asked by | Their question | Your answer, and where it's recorded |
|---|---|---|
| Head of Financial Crime Operations | Can the agents just close alerts? | No. They recommend, a named analyst decides: `evidence/decisions.jsonl` |
| Head of Model Risk Management | How do we know a change is still good enough? | The release gate on every change: the Evidence Pack from Lab 1 |
| Chief Information Security Officer | What stops a letter telling the agent to close its own case? | The prompt, Prompt Shields at the gateway and the guardrail: the red team Evidence Pack from Lab 2 |
| Data Protection Officer | What does each agent see, and does anything leave the bank? | The data policy, and research by approved topic only: `governance/data-policy.json` |
| Money Laundering Reporting Officer | Who approved this? | Intake, on current evidence: `governance/use-cases.json` |
| Finance Controller | What does it cost, and who pays? | Cost per alert and per cost center: the gate report and Application Insights |

The same path applies to any agent you take to production: decide the boundary, measure every change, attack it
before someone else does, govern what it sees and who approved it, and know what it costs.
