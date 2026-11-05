# Decision-1 triage

Decision-1 supplies typed triage recommendations through an APIM-hosted MCP tool.
The `triage_systemone` prompt agent handles one case at a time and uses GPT to
explain the recommendation. The model, agent and tool use the lab's Foundry
project and API Management gateway.

## One agent invocation, one case

`triage_systemone` is a GPT prompt agent. Foundry stores its name as
`triage-systemone`; the existing APIM alias retains the underscore.

1. The caller supplies one complete permitted case: alert, customer, transactions,
   prior cases, documents and screening lists.
2. GPT calls `asses_next_step` with `{"cases": [one_case]}`.
3. APIM invokes Decision-1 to select a disposition.
4. GPT explains that actual choice with citations, gaps and conflicts.
5. The caller verifies that the output preserves the service's disposition.
   A named human analyst remains responsible for the decision.

Its output is one JSON object with `disposition`, `reasons`, `gaps`
and `conflicts`. It does not assign priority.
Unavailable inference produces a null disposition, not `request_info`.

The GPT instructions are in
[`src/agents/triage_systemone.md`](../src/agents/triage_systemone.md).
An explanation is a post-hoc account supported by the records, not access to
Decision-1's internal reasoning. A correct label can still receive a poor explanation.

## The decision instructions

[`src/prompts/decision1.md`](../src/prompts/decision1.md) is the canonical Markdown
rubric. It separates established red flags from missing information, explains
screening identity checks, distinguishes independent evidence from customer
uploads, and recognizes routine activity established by transaction history.

The deployed APIM named value `systemone-triage-rules` contains this Markdown.
The policy uses the same typed choice criteria as
[`src/decision1_client.py`](../src/decision1_client.py). Responses identify the rules with
`rules_version: triage-systemone-v2`.

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
existing APIM policy. Golden labels are never sent to the model.

Prompt tuning uses the local file without deployment.
[`infra/main.bicep`](../infra/main.bicep) includes
[`infra/systemone.bicep`](../infra/systemone.bicep), which loads the Markdown into
the APIM named value during provisioning. It also
defines the backing REST API, native MCP tool, project-managed-identity connection,
and agent route.

Provision through the root `azure.yaml` and the lab's azd environment.
Publish the prompt agent separately using the command below.
The REST/MCP policies permit the project's managed identity and the operator/learner
principal IDs supplied to Bicep. Deployment parameters control gateway settings
and model capacity; set them to the intended values before provisioning.

## What batching actually means

The REST/MCP assessment API accepts arrays of 1..50 cases and returns one
correlated assessment per case. Its implementation makes separate Decision-1 calls;
it is not a native array-of-requests model endpoint.

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

```bash
# All 14 golden cases, independent calls, repeated three times.
uv run python src/evaluate_decisions.py --tenant-id <lab-tenant-id> --repeat 3

# Inspect a particular existing case while refining instructions.
uv run python src/evaluate_decisions.py --tenant-id <lab-tenant-id> --alert-id ALT-1010

# Diagnostic only: demonstrate shared-state multi-question behavior.
uv run python src/evaluate_decisions.py --tenant-id <lab-tenant-id> --mode shared --repeat 3
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
| Bulk APIM API, one full run | 14/14 | Separate serial traversal |

Shared-state runs missed ALT-1008's escalation in all three repetitions.
These are prompt-development measurements on the same small, known dataset, not
held-out accuracy or latency guarantees. Warm-up and network conditions affect
timings. Decision-1 is a model and
its probabilities are not established as calibrated confidence.

## Compare speed and cost with gpt-5-mini

```bash
uv run python src/compare_models.py --repeat 3
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

## Publish and exercise the single-case agent

`uv run create-agents` publishes `triage-systemone` with the other agents and
attaches the `asses_next_step` MCP tool through the `alert-disposition-mcp` project
connection.

Optional synthetic live checks stay under `tests/systemone/`. Set
`AZURE_TENANT_ID` to the lab tenant before using these runners.

```bash
# One case, one actual GPT -> MCP -> Decision-1 assessment.
uv run python tests/systemone/run_agent.py ALT-1001

# All 14 decisions through the bulk API; no GPT.
uv run python tests/systemone/run_api.py --mode decisions

# One REST and one native MCP call.
uv run python tests/systemone/run_api.py --mode smoke
```

The optional `--mode rest` and `--mode mcp` matrices also exercise API arrays
of 1, 14, 28 and 50; they do not turn the prompt agent into a batch agent.
The REST fault matrix includes an intentionally oversized synthetic record.
Use it only when those failure checks are wanted.

The agent runner verifies the original case before approving the read-only tool,
checks the actual executed call and returned case hash, and rejects changed facts
or additional cases. JSON object order and whole-number formatting do not change
case meaning; numeric booleans are not accepted as replacements for true/false.
At most two explanation-only repairs may reuse the same assessment without
calling the decision tool again. Failed/debug runs retain their diagnostics
but are not accepted results without `verified-result.json`.

Generated test evidence and synthetic tool permissions remain under ignored
`tests/systemone/artifacts/`. They never write analyst decisions or learner
approval records. All agent inference calls go through
the APIM alias; direct model calls are only for the explicit evaluator.

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
