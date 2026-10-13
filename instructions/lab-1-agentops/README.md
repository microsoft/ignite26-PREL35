# Lab 1 - AgentOps

**Objective:** Operate agents and models through production-grade, governed processes.

## Topics

### Covered in this lab

- **Evaluation framework and datasets:** build an evaluation dataset and run evaluators against the agent.
- **Release gates:** run the evaluation-and-gate script that a CI/CD pipeline runs on every change, and see a release pass or get blocked on its score.
- **Evidence Pack:** the artefact the release gate produces for approvers and auditors.
- **Observability:** traces and quality metrics for the agent.
- **Model retirement:** re-run the same evaluation on a replacement model before switching, and compare the results.

### Consider when moving to production

- **Maturity model and lifecycle:** where your organisation sits today and what the next stage requires.
- **CI/CD for prompts and agents:** wire the release gate you ran into a pipeline such as GitHub Actions, so it runs on every push. The pipeline needs its own Azure identity (a service principal), which is outside a lab's permissions, so this lab walks through it rather than doing it hands-on.
- **Model lifecycle policy:** track retirement dates, control automatic version upgrades, and decide who approves a model switch.

## Prerequisites

- Complete [Lab 0](../lab-0-setup/README.md), including sign-in, `.env`, and publishing both agents.
- One of the two evaluation runners. They behave the same way and write the same Evidence Pack:
  - [Python](../../src/lab1/python/evaluate.py): Python 3.12 with the packages in
    [requirements.txt](../../requirements.txt), provided on the lab VM. This is the default path below.
  - [.NET](../../src/lab1/dotnet/Program.cs): an alternative if the .NET 10 SDK is available.
- The versions of triage and reviewer you want to evaluate. The publish command prints these version numbers.
- Permission to submit Foundry evaluations and read their results.
- `MODEL_DEPLOYMENT` and `APPLICATIONINSIGHTS_CONNECTION_STRING` in the repository's `.env`, written by
  `scripts\env.ps1`. The existing model deployment is also used as the evaluation judge unless you specify another
  existing deployment.

The runners combine deterministic checks, Foundry-managed scoring, an Evidence Pack, and correlated Application
Insights telemetry. They run from the lab VM's terminal and use the resources from Lab 0. Each one is a short
program you can read top to bottom: its main file walks through the run in numbered steps. A local-only or replay
pass is **not** release approval.

## Steps

### 1. Inspect the evaluation dataset

Open [data/lab1-evaluations.json](../../data/lab1-evaluations.json). It contains four approved seed expectations:

| Alert | Accepted disposition | Required citations |
|---|---|---|
| ALT-1002 | `close_false_positive` | DOC-201 and T-00018 |
| ALT-1005 | `escalate` | SAN-0091 and T-00056 |
| ALT-1007 | `escalate` | PEP-2210 and either T-00078 or T-00079 |
| ALT-1010 | `request_info` or `escalate` | T-00108 |

The dataset also supplies reviewer challenges: missing citations, invented citations, an unsafe sanctions-case
closure, and a supported recommendation. Expected answers are used only for scoring; they are not sent to agents.
The deliberately authored challenge recommendation is sent to reviewer, without its expected verdict.

These are synthetic lab expectations, not real banking decisions. When adding a case, review its evidence and
record its accepted dispositions and required citation groups. Increment the dataset version when expectations
change. Do not use an agent's output as its own expected answer.

### 2. Run the evaluation

From the repository folder, use the version numbers printed when you published the agents. With Python:

```powershell
python .\src\lab1\python\evaluate.py --triage-version 1 --reviewer-version 1
```

Or with the optional .NET 10 SDK:

```powershell
dotnet run --project .\src\lab1\dotnet -- --triage-version 1 --reviewer-version 1
```

Every option in this lab works the same way in both runners. The rest of this lab shows the Python command; for
.NET, replace `python .\src\lab1\python\evaluate.py` with `dotnet run --project .\src\lab1\dotnet --`.
For either exercise script, add `-Runner dotnet` to select .NET; `-Runner python` explicitly selects the default.

Replace `1` with your actual versions. The runner verifies and pins both agent versions, calls triage followed by
reviewer for each case, then runs the reviewer challenges. Evaluations use your Entra ID token and the direct
Foundry path permitted in Lab 0; they do not test the gateway policy.

The local checks cover JSON shape, allowed values, expected dispositions, citations, required evidence, and
reviewer challenge verdicts. Each reason needs bracketed evidence IDs such as `[T-00018]`. Groups such as
`[DOC-201, T-00018]` or `[T-00078; T-00079]` are also accepted, and every recognized ID is checked. Customer and alert
IDs from the supplied case can anchor profile and missing-information statements, such as `[C-010]`; IDs from other
cases cannot. Plain IDs in prose and collection labels such as `[documents, prior_cases]` do not count as citations.
Citation checks establish that an ID exists, not that it supports the reasoning; the reviewer and managed
evaluators assess the semantic quality.
The end-to-end reviewer output is checked for valid shape and consistent verdict/issues. Its agreement with
triage is not treated as proof of correctness.

After recording the agent outputs, the runner submits them to Foundry's managed evaluation service. It does not
invoke the agents a second time. The judge receives the deployed agent instructions, task input, and recorded output,
but not the dataset's expected dispositions or reviewer verdicts.

The managed criteria are:

| Evaluator | What it measures | Required score |
|---|---|---|
| Groundedness | Whether the response is grounded in the supplied case/task context | At least 4/5 on every item |
| Relevance | Whether the response addresses the task | At least 4/5 on every item |

Every deterministic check and every managed criterion must pass. The runner also requires the service's own
`passed` flag, even if the numeric score meets the lab threshold. It checks that all submitted items have exactly
one result for each evaluator; missing, duplicate, malformed, or errored scores are execution errors, not passes.
It uses a ten-minute scoring budget for submission, polling, and retrieval of all result pages. Python limits
each connection/read wait to the remaining budget and rejects results returned after the deadline. Authentication,
OS networking, or a response that keeps streaming can still delay the return of a blocking call; this is not a
hard process deadline. A timed-out remote run may continue in Foundry; any IDs already received remain in the
Evidence Pack so you can inspect it.

To select a different **existing** judge deployment:

```powershell
python .\src\lab1\python\evaluate.py --triage-version 1 --reviewer-version 1 --judge-model <existing-deployment>
```

For a quicker live run of deterministic checks only:

```powershell
python .\src\lab1\python\evaluate.py --triage-version 1 --reviewer-version 1 --scoring local
```

Local-only live runs still emit telemetry but cannot pass the combined release gate.

| Exit code | Meaning |
|---|---|
| `0` | The selected checks passed; only a complete managed live run can pass the combined release gate |
| `1` | A deterministic check or managed quality criterion failed |
| `2` | Input, authentication, evaluation service, result validation, evidence writing, or telemetry flushing failed |

### 3. Inspect the Evidence Pack

The runner prints the new output directory under `.artifacts\lab1`. Open `summary.md` for the score table and
`evidence.json` for per-item inputs, outputs, failures, durations, response/request IDs, dataset and case-data hashes,
the runner used and its hash, agent versions, deployed prompt hashes, model deployment names, run/trace IDs, and
individual agent span IDs. Managed results include the judge deployment, policy version and threshold, the
evaluators, evaluation/run IDs, report URL, and per-item scores with Foundry's verdict and the judge's explanation.

The runner hash fingerprints the executing DLL for .NET, or the concatenated Python source files in filename order
for Python. Compare it within the same runner and build environment, not across languages. It is not a complete
fingerprint of installed dependencies or the VM.

#### View scores in Foundry

1. Open the [Foundry portal](https://ai.azure.com) and select your `fincrime` project.
2. Select **Evaluation** in the left navigation.
3. Find the run named **Lab 1 `<run_id>`**, matching the `run_id` in your Evidence Pack.
4. Open the run to see aggregate Groundedness and Relevance scores.
5. Inspect the individual rows for each recorded response's scores and the judge's explanations.

For a direct link, open `managed_evaluation.report_url` from `evidence.json`, or use the Foundry report URL in
`summary.md`. Only live managed-scoring runs appear in Foundry; offline replay and `--scoring local` do not create
managed evaluation runs.

Foundry may display its default passing threshold of **3/5**. This lab requires **at least 4/5 for every criterion
on every item**, plus all deterministic checks. Use the Evidence Pack's combined release gate rather than the
portal's aggregate score or pass label to decide whether the lab criteria were met.

For more detail, see [View evaluation results in the Microsoft Foundry portal](https://learn.microsoft.com/azure/foundry/how-to/evaluate-results).

Evidence is saved after each completed item and on handled execution failures once an output directory has been
created. Each file is replaced atomically, with the authoritative `evidence.json` verdict written last, after
`summary.md`. A save failure produces exit `2` and attempts to record an execution-error verdict in both files.
If storage is unavailable, an older snapshot may remain; the two files are not a single transaction. Never
approve from the summary alone: require both process exit `0` and `release_ready=true` in the final JSON.

Authentication or service errors also produce an execution-error result, never a passing score. Recorded outputs
are synthetic lab data; the runner does not record access tokens or `.env` contents.

`release_ready` can be `true` only for a live run with a passing local gate, passing managed scores, and a completed
telemetry flush without reported exporter errors. It represents this lab's quality gate, not automatic publication
or authority to close a case. Local-only runs, replay, incomplete evaluations, and execution errors always leave it
`false`.

An exporter flush is not proof that Azure has ingested the data. `telemetry_ingestion_verified` remains `false`;
verify receipt in step 7. Agent and judge deployment names are recorded, but the underlying model versions and
built-in evaluator implementation versions are not pinned. This limits cross-run reproducibility when deployments
or platform evaluators change.

### 4. See a release get blocked

Use [scripts/demo_blocked_release.ps1](../../scripts/demo_blocked_release.ps1) to make a controlled, reversible change
to triage. The script backs up your current [triage instructions](../../scripts/agents/triage.md), publishes and
evaluates them as the baseline, then temporarily replaces them with the
[deliberately faulty lab example](../../scripts/agents/examples/triage-regression.md). That candidate always recommends
`close_false_positive` with no evidence citations.

Use the reviewer version from your successful evaluation in step 2:

```powershell
.\scripts\demo_blocked_release.ps1 -ReviewerVersion 1
```

Replace `1` with your actual reviewer version. The script publishes only triage; reviewer is neither edited nor
republished, and its version stays fixed across all three evaluations. The script uses the Python runner; add
`-Runner dotnet` to use the .NET runner instead. This also applies to the model retirement drill in step 5.

**Do not run Lab 0 investigations during this exercise.** Publishing a new triage version temporarily changes the
agent's latest endpoint, including the gateway operation that selects triage by name. In this lab, the gate rejects
release approval after evaluating a published candidate; it does not prevent that candidate version from being
created. A production pipeline should evaluate a staging candidate before changing production routing.

The exercise performs three managed live runs:

| Run | Expected result | What to inspect |
|---|---|---|
| Baseline | Exit `0`, release gate `pass` | Original instructions pass the current dataset and both managed criteria |
| Candidate | Exit `1`, release gate `fail`, `release_ready=false` | Missing citations and wrong dispositions block approval |
| Restored | Exit `0`, release gate `pass` | Original instructions are published as a new version and their hash matches the baseline |

The baseline must pass before the faulty candidate is published. If it fails, inspect that Evidence Pack and
resolve the failure first; the script does not introduce the regression.

A failing baseline is not the intended candidate rejection. Check the recorded outputs and failed checks first.
If a reason is genuinely uncited, an ID is invented, or a screening claim has no connection to the customer or
transaction, improve the agent instructions and publish a fresh version. Do not remove required evidence or lower
the thresholds to make the exercise pass. Generic managed scores can pass even when deterministic contract checks
fail; a passing managed gate never overrides those failures.

If you change reviewer instructions while improving the baseline, publish only reviewer and use its newly printed
version when restarting the exercise:

```powershell
.\scripts\create_agents.ps1 -AgentFile .\scripts\agents\reviewer.md
.\scripts\demo_blocked_release.ps1 -ReviewerVersion 2
```

Replace `2` with the actual printed reviewer version. The exercise publishes the current local triage instructions
as its new baseline and keeps that reviewer version fixed.

For the faulty candidate, inspect the deterministic failures:

- `citations_present`: every reason lacks an evidence ID.
- `required_evidence:...`: required evidence groups are not cited.
- `expected_disposition`: a false-positive recommendation is not accepted for ALT-1005, ALT-1007, or ALT-1010.

Reviewer should also challenge the faulty recommendations. Managed Groundedness and Relevance scores may fail, but
their wording and scores vary. Even high managed scores cannot override the deterministic failures. The script
requires the intended missing-citation and wrong-disposition failures rather than treating any error as proof that
the gate worked.

The script prints an exercise directory under `.artifacts\lab1\blocked-release-...`, containing `baseline`,
`candidate`, and `restored` Evidence Packs plus `triage-before.md`, the original local instructions. Use each pack's
run ID or report URL to compare its results in Foundry, and its trace ID to inspect Application Insights.

Original instructions are restored and republished in a `finally` block, including when the candidate evaluation
fails unexpectedly. A network or service error (exit `2`) is **not** the intended blocked release. If the candidate
unexpectedly passes, the script reports that the regression was not demonstrated and still restores the original.
If the restored version fails its evaluation, inspect the restored pack; do not claim a successful recovery.

If the terminal is closed or restoration publication fails, restore the local instructions from the printed backup
path and publish only triage:

```powershell
Copy-Item -LiteralPath '<exercise-directory>\triage-before.md' -Destination .\scripts\agents\triage.md -Force
.\scripts\create_agents.ps1 -AgentFile .\scripts\agents\triage.md
```

Use the actual backup path. Never promote the faulty version or reuse this demonstration helper as a CI gate:
the helper succeeds when it has demonstrated a rejection and restored the original. CI must use the evaluation
runner's exit code for the candidate being considered. The agents still recommend; a named analyst decides.

### 5. Run a model retirement drill

> **Fictional notice for this lab:** the model version behind your `gpt-5-mini` deployment is scheduled for
> retirement. Evaluate a replacement and decide whether to switch before the retirement date.

Real retirement dates are published in
[Foundry Models lifecycle and support policy](https://learn.microsoft.com/azure/foundry/openai/concepts/model-retirements).

Each agent version records the **deployment** it runs on, so switching models means publishing new agent versions that
point to another deployment. Your environment includes a pre-provisioned candidate, `gpt-4.1-mini`, which is named by
`CANDIDATE_MODEL_DEPLOYMENT` in `.env`.

**Predict first.** Before you run the drill, write down:

- Which of the four alerts will change disposition?
- Will the candidate pass the release gate?
- Will it be faster or slower than the current model?

Then run the [drill](../../scripts/model_retirement_drill.ps1):

```powershell
.\scripts\model_retirement_drill.ps1
```

The drill takes about 10 to 15 minutes. It:

1. Publishes your current triage and reviewer instructions on the current model, and evaluates them.
2. Publishes the **same** instructions on the candidate model, and evaluates them with the same dataset and gate.
3. Republishes the instructions on the current model, so the latest agent versions use it again.
4. Prints a comparison table and saves it as `comparison.md`, next to both Evidence Packs.

The judge stays on the current model for both runs. If the judge changed with the agents, you would change both the
system under test and the measuring instrument at once, and couldn't tell which caused a difference.

The current model doesn't need to pass the gate for the comparison to be useful: the drill reports what changes when
only the model changes. It checks that both runs used identical instructions and the expected deployments, and stops
on execution errors (exit `2`) rather than treating them as a comparison. The latest agent versions are restored
even if the candidate run fails. Use the restored version numbers that the drill prints from now on.

**Do not run Lab 0 investigations during the drill.** While the candidate is published, the gateway's agent
operations use it.

Compare your predictions with the results:

| Look at | Question to discuss |
|---|---|
| **Changed** column | Did any disposition flip? Is the new disposition still acceptable for that alert? |
| Failure columns | Does the candidate break the output contract (JSON, citations), or the decision itself? |
| Lowest managed score | Did evidence quality drop, even where the disposition held? |
| Mean agent latency | Is the candidate faster, and does that matter if quality drops? |

A candidate can match every triage disposition and still fail. Check the reviewer challenges too: if the reviewer
accepts a recommendation it should reject, the safety net behind triage is weaker, even though triage looks fine.
Read the judge's explanations for any low score in the candidate's Evidence Pack or in Foundry.

You can also compare the two runs in Foundry: open **Evaluation**, select both **Lab 1 `<run_id>`** runs (the
`run_id` values are in each Evidence Pack), and select **Compare**.

A single run per model can differ by chance, as you saw in Lab 0. Treat one changed disposition as a reason to look
closer, not as proof. In production, you would repeat important cases and only switch through the same release gate:
publish agent versions on the new deployment, require them to pass, and keep the previous agent versions for
rollback. The agents still recommend; a named analyst decides.

### 6. Replay recorded outputs without Azure calls

For deterministic evaluator checks, prepare a JSON object whose property names are `triage/<alert-id>`,
`reviewer/workflow-<alert-id>`, and `reviewer/<challenge-id>`. Each value is the corresponding raw agent output as a
JSON string. Include every item in the dataset, for example:

```json
{
  "triage/ALT-1002": "{\"disposition\":\"close_false_positive\",\"priority\":\"low\",\"reasons\":[\"Matching sale proceeds [DOC-201] [T-00018].\"]}",
  "reviewer/workflow-ALT-1002": "{\"verdict\":\"agree\",\"issues\":[]}",
  "reviewer/missing-citation": "{\"verdict\":\"disagree\",\"issues\":[\"The reason has no citation.\"]}"
}
```

This example shows the format only; add the remaining outputs before running:

```powershell
python .\src\lab1\python\evaluate.py --replay .\responses.json
```

Replay validates scoring without authentication or agent calls. Its Evidence Pack is explicitly labelled
`offline_replay`, contains no deployed-agent provenance, and cannot approve a release.

Replay never starts Azure authentication, managed scoring, or telemetry exporters. You cannot combine it with
managed-scoring options or live agent versions.

Use `--dataset <path>` for an alternative reviewed dataset, `--output <new-directory>` to choose where evidence is
written, or `--help` for usage. An existing output path is refused so earlier evidence is not overwritten.

### 7. Inspect traces and quality metrics

Open Application Insights (`appi-…`) from Lab 0 and select **Logs**. Copy the `trace_id` from your live run's
Evidence Pack. Allow time for ingestion, then inspect the correlated root, agent calls, HTTP dependencies, and
managed scoring:

```kusto
union requests, dependencies
| where cloud_RoleName == "ignite-lab1-evaluation"
| where operation_Id == "<trace-id>"
| project timestamp, name, id, operation_ParentId, success, duration, customDimensions
| order by timestamp asc
```

Inspect individual judge scores for that run:

```kusto
dependencies
| where cloud_RoleName == "ignite-lab1-evaluation"
| where operation_Id == "<trace-id>" and name == "lab1.managed_score"
| project timestamp,
    item = tostring(customDimensions["lab1.item_id"]),
    evaluator = tostring(customDimensions["lab1.evaluator"]),
    score = todouble(customDimensions["lab1.score"]),
    passed = tostring(customDimensions["lab1.passed"])
```

Inspect metrics across runs:

```kusto
customMetrics
| where cloud_RoleName == "ignite-lab1-evaluation"
| where name in ("lab1.agent.duration", "lab1.local.checks", "lab1.managed.score", "lab1.runs")
| project timestamp, name, value, customDimensions
| order by timestamp desc
```

Run and item IDs are attached to traces, not metric dimensions, to avoid high-cardinality metrics. Telemetry contains
identifiers, durations, gate states, and numeric scores; it does not contain case files, agent instructions,
recommendations, judge explanations, access tokens, or the connection string. Traces are unsampled for this small lab
dataset, and HTTP instrumentation is restricted to the configured Foundry project.

The console runner ends its spans and flushes the exporters before exiting. Exporter errors or a failed flush
produce exit code `2` and cannot pass the release gate. If no rows appear after ingestion time, check the `.env`
connection string, connectivity, and Application Insights resource. Do not treat a locally completed flush as proof
of remote receipt.

Both runners report as `ignite-lab1-evaluation`. To tell them apart, use the `lab1.runner` attribute (`dotnet` or
`python`) on the `lab1.evaluation_run` span.

### 8. Discuss the remaining release process

- Capture underlying model versions and repeat important cases to measure variability.
- Calibrate the judge and thresholds against reviewed examples. A managed quality score is evidence for an approver,
  not proof of compliance or financial-crime expertise.
- Wire the combined gate into CI with an appropriate identity and documented approval policy. Provisioning that
  identity is outside the lab's permissions; a managed model deployment must remain available to the pipeline.
- Control automatic model version upgrades on each deployment, and record the underlying model version in the
  Evidence Pack, so an agent version can't change behaviour silently.

## Next

Continue to [Lab 2 - Security and trust](../lab-2-security-and-trust/README.md).
