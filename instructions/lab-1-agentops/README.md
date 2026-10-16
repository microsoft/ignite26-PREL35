# Lab 1 - AgentOps

**Time:** about 40 minutes

**Objective:** Operate agents and models through production-grade, governed processes.

## What this lab covers

In Lab 0 you ran one alert and saw that the answer can change between runs. One run tells you nothing about
whether an agent is good enough to release. In this lab you measure the agents against alerts whose correct outcome
is known, and let a **release gate** decide whether a change can ship.

| Piece | What it is |
|---|---|
| **Golden dataset** | [data/golden.jsonl](../../data/golden.jsonl): the 14 alerts, each with its expected disposition |
| **Evaluators** | Code checks in [src/evaluate.py](../../src/evaluate.py), plus an AI judge in Foundry |
| **Thresholds** | [evals/thresholds.json](../../evals/thresholds.json): the score each metric needs, who owns it, and whether it blocks a release |
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

[Lab 0](../lab-0-setup/README.md) completed: you have a `.env` file and both agents are published.

## Steps

### 1. Run the release gate on the current agents (7 min)

```powershell
uv run src/evaluate.py --foundry
```

It runs all 14 golden alerts through triage and reviewer, through the gateway, and takes about a minute.
`--foundry` also starts an evaluation in Foundry, where an AI judge scores whether triage followed its instructions.
You look at that in step 6.

You see `Release gate: PASS` and a score for each metric. Open the Evidence Pack folder it names, and open
`report.md`: each metric against its threshold, and the outcome for each alert. Often all 14 are right. When one is
wrong it's usually ALT-1006, where triage asks for more information instead of closing, and the gate still passes:
13 of 14 is above the 85% that the accuracy owner requires.

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

Open [.github/workflows/release-gate.yml](../../.github/workflows/release-gate.yml). It runs the commands you ran:
connect, publish the agents from the commit, run the gate, and keep the Evidence Pack. A failing gate fails the
job, so the change can't be merged or released.

### If you have time

Make the smaller model good enough. Open the `report.md` of the `gpt-5-nano` run from step 4 and pick an alert it
got wrong. If it got all of them right, take ALT-1006: a sanctions name match where the date of birth doesn't
match, so it should be closed. Edit [src/agents/triage.md](../../src/agents/triage.md), then publish on
`gpt-5-nano` and run the gate:

```powershell
uv run scripts/create_agents.py gpt-5-nano
uv run src/evaluate.py
```

Did you fix it without breaking another alert? Run the gate twice: one correct run can be luck. When you're done,
restore the prompt and the approved model, as Lab 2 starts from them:

```powershell
git checkout src/agents/triage.md
uv run scripts/create_agents.py
```

## Next

Continue to [Lab 2 - Security and trust](../lab-2-security-and-trust/README.md).
