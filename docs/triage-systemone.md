# Decision-1 triage

Decision-1 supplies typed triage recommendations from code. The
[`triage_systemone.py`](../src/decisions/triage_systemone.py) runner orchestrates three steps for
one alert, and the `triage_systemone` prompt agent only explains the result. Nothing besides the
agent version is deployed: the Decision-1 deployment already in the lab's Foundry project is
called directly with your Entra ID token.

## One alert, three steps

`triage_systemone` is a GPT prompt agent. Foundry stores its name as `triage-systemone`.

1. The runner sends Decision-1 one complete permitted case (alert, customer, transactions,
   prior cases, documents and screening lists) and gets the disposition and probabilities.
2. The runner calls the agent once, with the case and the Decision-1 recommendation. GPT
   writes the reasons, gaps and conflicts, with citations. It cannot change the disposition.
3. The runner verifies that the disposition is unchanged and that every reason cites a
   record of the case (one explanation-only repair is allowed), then Decision-1 reviews the
   recommendation. A named human analyst remains responsible for the decision.

The agent's output is one JSON object with `disposition`, `reasons`, `gaps` and `conflicts`.
It does not assign priority. If Decision-1 is unavailable, the runner stops with the failure
and does not call the agent; an outage is not `request_info`.

The GPT instructions are in
[`src/agents/triage_systemone.md`](../src/agents/triage_systemone.md).
An explanation is a post-hoc account supported by the records, not access to
Decision-1's internal reasoning. A correct label can still receive a poor explanation.

## The decision instructions

[`src/decisions/prompts/decision1.md`](../src/decisions/prompts/decision1.md) is the canonical Markdown
rubric. It separates established red flags from missing information, explains
screening identity checks, distinguishes independent evidence from customer
uploads, and recognizes routine activity established by transaction history.

The code loads this Markdown at run time and uses the typed choice criteria in
[`src/decisions/decision1_client.py`](../src/decisions/decision1_client.py).

Each isolated inference has this shape:

```json
{
  "model": "decision-1",
  "state": {"alert": {}, "customer": {}, "transactions": []},
  "questions": {
    "disposition": {
      "type": "choice",
      "instructions": "<the full Markdown rubric>",
      "criteria": {
        "escalate": "<established red-flag criterion>",
        "close_false_positive": "<positive explanation or false-screening criterion>",
        "request_info": "<insufficient-information criterion>"
      }
    }
  }
}
```

The abbreviated state above is illustrative: real requests include all permitted
case sections. The service also accepts a JSON-serialized state, as used by the
existing JSON serialization. Golden labels are never sent to the model.

Prompt tuning uses the local file without deployment.

## What batching actually means

`DecisionClient.classify` accepts any number of cases and makes one Decision-1 call per case;
Decision-1 has no native array-of-requests endpoint. The calls run with bounded concurrency,
and the client backs off when the deployment's requests-per-minute limit answers 429.

Live capability checks against the existing Decision-1 deployment found:

| Model request | Result |
| --- | --- |
| One JSON object as `state`, one choice question | Accepted |
| Top-level array of independent request objects | Rejected, HTTP 400 |
| One array-containing state, one named choice question per case | Accepted; one answer per named question |

The last form shares all evidence across questions. It is not equivalent to isolated
case classification. It produced missed escalations on these fixtures despite
instructions to restrict each question to its designated case. The default evaluator
therefore runs independent requests with bounded concurrency, rather than combining
unrelated case evidence.

See Microsoft's
[Decision-1 API documentation](https://learn.microsoft.com/en-us/azure/foundry/foundry-models/how-to/use-foundry-models-microsoft-decision)
for typed questions and shared-state requests.

## Fast prompt evaluation

These commands make billable calls against the lab deployment. Use the
root `.env` and a CLI login in the lab tenant. No GPT, reviewer, provisioning or new
role assignment is involved.

The lab template deploys `decision-1` with a capacity of 30, which is also its requests-per-minute
limit. That covers a full 14-case evaluation and a 14-alert investigation; larger runs (`--challenge`,
`--repeat`) make the client back off after HTTP 429 and mark calls it cannot complete as unavailable.

```bash
# All 14 golden cases, independent calls. Add --repeat 3 to see run-to-run variation.
uv run python src/decisions/evaluate_decisions.py --tenant-id <lab-tenant-id>

# Inspect a particular existing case while refining instructions.
uv run python src/decisions/evaluate_decisions.py --tenant-id <lab-tenant-id> --alert-id ALT-1010

# Diagnostic only: demonstrate shared-state multi-question behavior.
uv run python src/decisions/evaluate_decisions.py --tenant-id <lab-tenant-id> --mode shared
```

The default is eight concurrent isolated calls. `--workers` adjusts concurrency
without changing deployment capacity. `--prompt` compares another local rubric.
Generated evidence under ignored `evidence/decision1/` includes the exact prompt,
its SHA-256, criteria, per-case results, unavailable statuses, usage and timings.
A run exits nonzero if any case is unavailable or differs from its golden label.

The rubric recognizes routine activity established by bank records. For example,
recurring payroll supported by transaction history and the business profile does
not require an external document simply because an attached schedule was uploaded
by the customer.

Measured runs with the final prompt:

| Path | Golden matches | Observed seconds |
| --- | --- | --- |
| Independent calls, three full runs | 14/14, 14/14, 14/14 | 3.79, 1.38, 1.21 |
| Shared-state questions, three full runs | 13/14, 12/14, 12/14 | 1.36, 1.40, 1.32 |

Shared-state runs missed ALT-1008's escalation in all three repetitions.
These are prompt-development measurements on the same small, known dataset, not
held-out accuracy or latency guarantees. Warm-up and network conditions affect
timings. Decision-1 is a model and
its probabilities are not established as calibrated confidence.

## Review a recommendation with Decision-1

`src/decisions/decision1_reviewer.py` asks Decision-1 whether a triage recommendation is the
one the bank's rules support. It returns only a verdict (`agree` or `disagree`)
and a confidence, with no issue list. The rubric in `src/decisions/prompts/decision1_reviewer.md`
is followed by the triage rules from `decision1.md`, so the rules are defined once.
The reviewer sees the recommended disposition and any reasons, never the triage
confidence. A service failure has no verdict and is never read as `agree`.

`src/decisions/investigate_decisions.py` triages each alert with Decision-1, then reviews each
recommendation using the reviewer's own case view under the data policy. No GPT is involved.

```bash
# Triage and review all 14 cases.
uv run python src/decisions/investigate_decisions.py --tenant-id <lab-tenant-id>

# Also give the reviewer every wrong disposition; it should disagree with all of them.
uv run python src/decisions/investigate_decisions.py --tenant-id <lab-tenant-id> --challenge
```

Evidence goes to ignored `evidence/decision1-investigations/`. A run exits nonzero if triage
differs from a golden label, the reviewer disagrees with a correct recommendation or agrees
with a wrong one, or any call is unavailable.

## Compare speed and cost with gpt-5-mini

```bash
uv run python src/decisions/compare_models.py --repeat 3
```

Both models receive the same rubric, labels and policy-enforced case files, with
eight concurrent calls. The table reports median wall time, per-case time, p95
call time, tokens, cost per case and per million cases, golden accuracy and
escalation recall. Decision-1 is priced at $0.042 per 1M input tokens (its single
output token is not priced); `gpt-5-mini` uses `evals/prices.json` and includes
reasoning tokens. Use `--scale 10` for a larger batch and `--effort` to change
GPT reasoning. Evidence is written under ignored `evidence/decision1-comparison/`.

A measured run on the 14 golden cases (median of three):

| Model | Wall time | Tokens per case | Cost per case | Accuracy |
| --- | --- | --- | --- | --- |
| Decision-1 | 1.10 s | 2,306 | $0.000097 | 14/14 |
| gpt-5-mini (low effort) | 9.08 s | 2,549 | $0.000698 | 14/14 |

These are indicative results on a small, known dataset; timings vary with
warm-up, rate limits and network conditions.

## Compare the two investigations

`src/decisions/compare_investigations.py` runs every golden alert through the classic investigation (GPT `triage` and
`reviewer` agents through API Management) and through `investigate_decisions.py`, both on the same
policy-enforced case files, and prints a side-by-side table. Evidence goes to `evidence/investigation-comparison/`.

```bash
uv run python src/decisions/compare_investigations.py --workers 4
```

One run on the 14 golden alerts:

| | Classic (GPT) | Decision-1 |
|---|---|---|
| Wall time | 47.3 s | 2.8 s |
| Cost per alert | $0.00298 | $0.00020 |
| Triage matches golden | 13/14 | 14/14 |
| Reviewer agrees | 12/14 | 14/14 |

The classic reviewer also returns issues; Decision-1 returns a verdict and confidence only. Fourteen alerts and
one run are indicative, not statistical.

## Publish and exercise the single-case agent

```bash
# Publish the agents; no new Azure resources.
uv run create-agents

# One alert: Decision-1 chooses, the agent explains, Decision-1 reviews.
uv run python src/decisions/triage_systemone.py ALT-1001
```

Set `AZURE_TENANT_ID` to the lab tenant first. The runner requires the unchanged service
disposition and a citation of a case record for every reason. Failures stop with an error
rather than a guess.

The output lists each call in order with its time and cost: Decision-1 choosing the
disposition, the agent writing the reasons (plus any repair), and the Decision-1 review.
Pass `--json` for the full record.

Decision-1 and the agent both get the same policy-filtered case file, so Decision-1 never
sees data the data policy withholds from triage. Agent calls go to the Foundry project
directly; they do not pass through the API Management gateway, which has no route for this
agent.

## Why keep GPT at all?

If labels and probabilities are sufficient, do not call GPT. The direct evaluator
demonstrates that Decision-1 can make these choices without a follow-up LLM.
GPT is optional narrative generation, not a corrective judge or another vote.
The additional calls add cost, latency and explanation-quality risks.

These choice-only evaluations are not the complete release gate. They do not score
priority, explanation quality, reviewer agreement or semantic citation support.
The isolated runner verifies citation membership, not whether every cited record
logically proves the associated claim. The full release gate also checks citations,
escalation recall and reviewer agreement. Decision-1 pricing and total-cost savings remain
unverified; this is an optional experiment, not a production-readiness claim.
