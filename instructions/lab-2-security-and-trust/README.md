# Lab 2 - Security and trust

**Time:** about 40 minutes

**Objective:** Implement security controls and monitoring capabilities for AI solutions.

## What this lab covers

The release gate in Lab 1 measures whether the agents are right on honest case files. A case file is not always
honest: a customer can upload a letter with instructions in it, a payment memo can carry a command, a caller can
claim authority they don't have. In this lab you attack the triage agent, see which controls stop which attacks, and
add one.

| Piece | What it is |
|---|---|
| **Attacks** | [evals/attacks.json](../../evals/attacks.json): 10 financial-crime attacks, each with the outcome the attacker wants |
| **Red team** | [src/redteam.py](../../src/redteam.py): sends every attack through the gateway and judges the answer |
| **Guardrail** | The default guardrail on the model deployment in Foundry: content filters and jailbreak detection |
| **Prompt Shields at the gateway** | A switch in the API Management policy that scans each request for injected instructions before it reaches Foundry |

Each attack ends one of three ways:

| Outcome | Meaning |
|---|---|
| `blocked` | A control refused the request, and the agent never answered |
| `resisted` | The agent answered and stayed on task: the attacker didn't get what they wanted |
| `SUCCEEDED` | The attacker got the outcome they wanted, for example a suspicious case closed |

## Topics

### Covered in this lab

- **Guardrails and content safety:** see what the default guardrail on the model blocks, and what it leaves to the agent.
- **Real-time agent protection:** Prompt Shields detecting jailbreaks and indirect prompt injection.
- **Red teaming:** run a red-team scan of financial-crime attacks and analyse the results.

### Consider when moving to production

- **Custom guardrails:** stricter thresholds, blocklists, and detection of injected instructions in documents, set per model deployment.
- **AI Red Teaming Agent:** Foundry generates and scores attacks across risk categories. It calls the agent directly, so it doesn't test controls at the gateway.
- **Identity and delegated access (On-Behalf-Of):** let the agent call tools as the signed-in user, with that person's permissions.
- **Defender for AI:** threat protection and alerts for AI workloads, enabled per subscription.
- **AI-SPM (AI Security Posture Management):** discover AI assets and attack paths across the estate.
- **Observability Hub:** a single security view across agents, models and their telemetry.

## Prerequisites

[Lab 1](../lab-1-agentops/README.md) completed, with the approved triage prompt published on `gpt-5-mini`.

## Steps

### 1. Red team the current agent (8 min)

Open [evals/attacks.json](../../evals/attacks.json). Most attacks hide in the case file, where a real attacker can
put them: an uploaded letter, a payment memo, a forged solicitor's letter. Two come from the caller. Run them all:

```powershell
uv run src/redteam.py
```

It takes about a minute and ends with `Red team: PASS` when no attack succeeded. Like the release gate, it writes an
Evidence Pack under `evidence/` and exits with code 1 when an attack succeeds, so a pipeline can stop the release.

In the table, JB-01 is `blocked` by `content_filter`, and every other attack is `resisted`. Look at two of them in
full:

```powershell
uv run src/redteam.py JB-01
uv run src/redteam.py HRM-01
```

- **JB-01** never reached the agent. The guardrail on the model deployment detected a jailbreak in the caller's
  prompt and Foundry answered HTTP 400 `content_filter`, with `jailbreak: detected, filtered`.
- **HRM-01** asks for money-laundering instructions, and Foundry let it through. The content filters block hate,
  sexual, violent and self-harm content; a request for help with a financial crime is none of these. What stopped
  it is the model and the agent's own instructions: read the answer, where the agent refuses or answers as triage.

So the prompt is a security control too. That's what the next step tests.

### 2. See the guardrail in Foundry (5 min)

Open the [Foundry portal](https://ai.azure.com), select the `fincrime` project, and open **Guardrails**. Open the
guardrail on `gpt-5-mini` (`Microsoft.DefaultV2`) and note:

- the content categories and the severity at which they block, on both the prompt and the completion;
- jailbreak detection on the user prompt, which blocked JB-01;
- what it doesn't do: look for instructions hidden in the documents a case file carries.

Changing a guardrail is a change to the model deployment, owned by the platform team. You add the missing
protection at the gateway instead, in step 4.

### 3. Ship a "helpful" prompt and red team it (10 min)

A colleague proposes a change that saves analysts time: when compliance or a senior officer has already cleared the
activity, close the case instead of escalating it again. Compare it with the approved prompt:

```powershell
git diff --no-index src/agents/triage.md src/agents/variants/triage.helpful.md
```

Apply it, publish it, and run the red team:

```powershell
Copy-Item src/agents/variants/triage.helpful.md src/agents/triage.md
uv run scripts/create_agents.py
uv run src/redteam.py
```

You see `Red team: FAIL`. JB-02 and PER-02 usually succeed: anyone who writes "already cleared" into a note, or
forges a letter, can now close a suspicious case. Run the one that succeeded on its own, for
example `uv run src/redteam.py JB-02`, and read why triage closed the case.

The golden dataset in Lab 1 has no attacks in it, so the release gate can't catch this kind of change. The red team
can, which is why both run before a release.

### 4. Turn on Prompt Shields at the gateway (10 min)

Every request already passes through API Management. Switch on its Prompt Shields check, which sends the request to
Prompt Shields in your Foundry resource and refuses it when an injected instruction is found:

```powershell
.\scripts\set_gateway.ps1 prompt-shields on
```

Wait a minute for the gateway to apply it, then run the red team again, still on the "helpful" prompt:

```powershell
uv run src/redteam.py
```

IPI-01, IPI-03, EXF-01 and JB-01 are now `blocked` by `prompt_attack_detected`, at the gateway, before they reach
Foundry. Look at what still gets through:

- IPI-02 writes its instruction in look-alike characters, and IPI-04 dresses it up as a system message. Both pass
  the scan; the agent resisted them on its own.
- JB-02 still succeeds. A note claiming authority reads like an ordinary analyst note, and the weakened prompt now
  trusts it.

Prompt Shields is one more layer, not a fix: it catches most injected instructions, and it can't repair a prompt
that trusts the wrong things. Only the prompt fix closes JB-02.

A control that blocks honest work is a problem too. Run the release gate to check that Prompt Shields doesn't block
real case files:

```powershell
uv run src/evaluate.py
```

The gate passes: no case file is blocked. It passes on the weakened prompt too, because the golden dataset has no
attacks in it.

### 5. Roll back and red team again (5 min)

Restore the approved prompt, publish, and run the red team with Prompt Shields still on:

```powershell
git checkout src/agents/triage.md
uv run scripts/create_agents.py
uv run src/redteam.py
```

You see `Red team: PASS`. Each attack is now stopped by Prompt Shields at the gateway or by the agent's own
instructions, with the guardrail in Foundry behind both. JB-01 is now stopped at the gateway, before it reaches the
guardrail that blocked it in step 1. More than one layer means one weak layer doesn't let an attack through.

### 6. Read the Evidence Pack (2 min)

Open the latest `evidence/…-redteam` folder. `report.md` lists every attack, its outcome and the control that
stopped it; `results.json` holds every answer in full. Together with the release gate's Evidence Pack, this is the
security input to the approval in Lab 3.

## Next

Continue to [Lab 3 - Governance](../lab-3-governance/README.md).
