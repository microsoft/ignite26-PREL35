# Lab 0 - Setup

**Time:** about 20 minutes, after the deployment owner completes provisioning

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
investigate.py  ──(Lab client token)──►  API Management  ──(its managed identity)──►  Foundry project
                                         checks audience + app role,                    triage + reviewer agents
                                         rate limit per client                          gpt-5-mini
                                                                                              │
                                                                         Application Insights ◄┘ traces
```

- **Foundry** accepts Entra ID only: no API keys exist.
- **API Management** is the way apps reach the agents. Only agents published as gateway operations can be called
  through it. You also have direct access to Foundry, for the Foundry portal and the evaluations in Lab 1; in
  production, a private endpoint and no data-plane roles for people would close that path.
- **Lab 0 uses the gateway's latest published agent versions.** Lab 1 uses your interactive Azure identity to call
  Foundry directly and pin exact versions. It does not use the Lab client secret or test the gateway policy.
- The infrastructure is Bicep, deployed for you before the lab started. The agents are
  defined in scripts/agents, and you publish them in step 2.

## Topics covered

- Landing zone building blocks: identity, gateway, monitoring
- Infrastructure as code for the platform, and agents as versioned definitions
- Calling agents through a gateway, with no Foundry API keys; the Lab client uses an Entra client secret

## Prerequisites

Provided on the lab VM: Azure CLI, PowerShell 7, Python 3.12 with the packages in
requirements.txt, and this repository. Your resource group and sign-in details are on the
**Resources** tab of the lab instructions.

The Python client uses the provided environment. To use the alternative .NET client, you also need the .NET 10
SDK. Both clients run the same investigation.

Before attendees begin, the deployment owner must have configured the gateway and provisioned the Lab client
credential in the ignored `.env` on each learner VM. Resource-group Contributor permissions alone do not allow
attendees to complete the tenant setup below. Attendees start at Steps, not at the owner provisioning commands.

### Deployment owner: configure Microsoft Entra authentication for the gateway

The deployment owner completes this setup once, before attendees start the lab. There are two separate identities
in the request path:

| Request | Identity | Credential |
|---|---|---|
| Lab client to API Management | A confidential Microsoft Entra client service principal | A client secret used to obtain an application token for the API |
| API Management to Foundry | The API Management system-assigned managed identity | A managed identity token for `https://ai.azure.com` |

Do not create a client secret for API Management. The system-assigned managed identity in
`infra/main.bicep` is the credential for the Foundry connection and is assigned the
Foundry User role on the Foundry account.

#### Provision the client-facing API and lab client registrations

The deployment owner runs the setup script once after the Bicep deployment and before attendees start the lab:

```powershell
.\scripts\env.ps1 -ResourceGroup @lab.CloudResourceGroup(1).Name
.\scripts\setup-entra-apps.ps1 -ResourceGroup @lab.CloudResourceGroup(1).Name
```

Run these commands from the repository on the learner VM using an authorized owner identity, then sign out of
that identity before handing the VM to the attendee. The setup script writes a local `.env`; running it on an
owner's workstation does not provision the learner VM. The attendee must sign in as the learner in step 1.

The script requires:

- PowerShell 7 and Azure CLI.
- `az login` to the tenant and subscription that contain the lab resource group.
- Permission to create and update app registrations and service principals, expose and assign application
  permissions, and grant tenant admin consent.
- Permission to read the resource group and update its API Management service and API policy.

By default, the script creates or reuses single-tenant registrations named `PREL35 Published Agents API` and
`PREL35 Lab Client`. Pass `-ApiAppDisplayName`, `-ClientAppDisplayName`, or `-ApimName` when your environment needs
different or disambiguated names. Reruns reuse registrations with the selected names, preserve the existing
application-role ID and client secret, and update the existing API Management named values and policy.

Use distinct registration names for isolated learner environments. Reusing the default names across VMs reuses
the same tenant registrations; a different VM cannot recover an existing secret from Entra. Do not rotate a
shared registration's credentials to provision another learner, because rotation invalidates existing clients.

The API registration exposes the `Agents.Invoke` application permission. The second registration is the
confidential service client used by the Python and .NET investigation clients. The script creates or reuses service
principals for both registrations, assigns that application role to the client service principal, and thereby
grants tenant admin consent idempotently.

On first setup, the script creates a one-year client secret and writes it only to the ignored root `.env`. Ordinary
reruns do not create or replace the secret. If the secret expires or is exposed, rotate it explicitly:

```powershell
.\scripts\setup-entra-apps.ps1 -ResourceGroup @lab.CloudResourceGroup(1).Name -RotateClientSecret
```

Rotation replaces the app registration's existing credentials, so update every authorized runtime from the local
`.env` written by that command. The script never prints the secret.

The script merges these values into the ignored root `.env`:

```text
ENTRA_TENANT_ID=<tenant-id>
APIM_API_APP_OBJECT_ID=<api-app-registration-object-id>
APIM_API_CLIENT_ID=<apim-api-client-id>
APIM_API_SERVICE_PRINCIPAL_ID=<api-service-principal-object-id>
APIM_API_AUDIENCE=api://<apim-api-client-id>
APIM_API_APP_ROLE_ID=<application-role-id>
APIM_API_APP_ROLE=Agents.Invoke
LAB_CLIENT_APP_OBJECT_ID=<confidential-client-app-registration-object-id>
LAB_CLIENT_ID=<confidential-client-application-id>
LAB_CLIENT_SERVICE_PRINCIPAL_ID=<confidential-client-service-principal-object-id>
LAB_CLIENT_SECRET=<client-secret>
```

See `.env.example` for every deployment and identity variable. `ENTRA_TENANT_ID` selects the
single tenant. `APIM_API_CLIENT_ID` and `APIM_API_APP_OBJECT_ID` identify the API registration, while
`APIM_API_SERVICE_PRINCIPAL_ID` identifies its tenant instance. `APIM_API_AUDIENCE` is the token audience.
`APIM_API_APP_ROLE_ID` and `APIM_API_APP_ROLE` identify the application permission required by API Management.
The corresponding `LAB_CLIENT_*` values identify and authenticate the confidential client registration and service
principal.

> **Secret handling:** Never commit or share the real `.env`. Keep `.env.example` placeholder-only. For a shared or
> production runtime, move `LAB_CLIENT_SECRET` to an approved secret store instead of copying `.env`. Rotate the
> credential immediately if it is exposed and before its one-year expiry.

#### Configure the two trust boundaries

For the inbound boundary, the setup script updates API Management to validate tokens from `ENTRA_TENANT_ID`,
require `APIM_API_AUDIENCE`, and require the `Agents.Invoke` value in the token's `roles` claim. The policy is
`infra/agents-api-policy.xml`; the per-caller rate limit remains after token
validation.

For the outbound boundary, keep the existing system-assigned managed identity on API Management and its
least-privilege Foundry User role assignment. The policy's `authentication-managed-identity` resource remains
`https://ai.azure.com`. Do not pass the caller's token to Foundry and do not store a Foundry key or client secret.

The Python and .NET clients use `ENTRA_TENANT_ID`, `LAB_CLIENT_ID`, and `LAB_CLIENT_SECRET` to request
`APIM_API_AUDIENCE/.default`. API Management validates the dedicated audience and app role, then uses its own
managed identity for the separate call to Foundry.

After redeploying Bicep, rerun the owner setup script: the template resets `apim-api-audience` to a bootstrap value.
Refreshing `.env` alone does not repair that gateway configuration. Keep the owner identity separate from the
learner's interactive identity used for agent publication and Lab 1 evaluations.

## Steps

### 1. Sign in and connect (3 min)

Open a terminal in the repository folder and sign in for the interactive setup and publishing steps. Use the
username ++@lab.CloudPortalCredential(User1).Username++ and the password ++@lab.CloudPortalCredential(User1).Password++:

```powershell
az login
```

Write your environment's values to `.env`, using your resource group name:

```powershell
.\scripts\env.ps1 -ResourceGroup @lab.CloudResourceGroup(1).Name
```

You see the project endpoint, the model deployments and the gateway URL.

This refresh preserves the Entra values already provisioned by the deployment owner. Do not print or share `.env`.
If the investigation client reports incomplete setup, ask the deployment owner to provision this VM; do not run
tenant-administration commands as the learner. The investigation clients do not prompt an attendee to sign in.

### 2. Publish the agents (2 min)

Each agent is a Markdown file of instructions in scripts/agents. Publishing creates a new version of
each agent in Foundry:

```powershell
.\scripts\create_agents.ps1
```

On the first publication, you see `reviewer version 1` and `triage version 1`. If an agent already exists, the command
adds a new version instead of recreating the agent; earlier versions are preserved. Use the version numbers printed
by the command. In Lab 1 you change an agent and publish another version the same way.

Record both version numbers for Lab 1. Publishing changes the versions reached through the gateway immediately;
it is not gated release approval. Do not run Lab 0 investigations while Lab 1's exercises temporarily publish
candidate versions.

### 3. Tour the landing zone (5 min)

In the [Azure portal](https://portal.azure.com), open your resource group and find:

| Resource | Look for |
|---|---|
| Foundry (`aif-…`) | **Resource Management > Keys and Endpoint**: local (key) authentication is disabled |
| API Management (`apim-…`) | **APIs > Published agents**: one operation per agent. Open the policy: token check, rate limit, managed identity |
| Application Insights (`appi-…`) | Where agent traces land. You use it in Lab 1 |

Then open the [Foundry portal](https://ai.azure.com), select the `fincrime` project, and open **Agents**. You see
**triage** and **reviewer**. Open triage and read its instructions: they're the same text as
scripts/agents/triage.md.

### 4. Run your first investigation (5 min)

From the repository folder, run the Python client:

```powershell
python .\src\lab0\python\investigate.py ALT-1001
```

Alternatively, with the .NET 10 SDK available, run the .NET client:

```powershell
dotnet run --project .\src\lab0\dotnet -- ALT-1001
```

Both clients read the repository's `.env` and authenticate the confidential `LAB_CLIENT_ID` with
`LAB_CLIENT_SECRET`. They request an application token using the API's `/.default` scope, with the assigned
`Agents.Invoke` role, then call triage followed by
reviewer through API Management. They display recommendations only; neither client closes a case.

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

If a normal investigation fails, distinguish the failure from this intentional refusal:

- **Incomplete setup:** the local `.env` is missing a required deployment or client value, or still has example
  placeholders. Ask the deployment owner to finish provisioning.
- **Token acquisition fails:** the owner checks the tenant, client registration, secret expiry, and app-role assignment.
- **An authenticated gateway call is refused:** the owner checks its audience and role configuration, especially
  after a Bicep redeployment.
- **Foundry or model calls fail:** inspect the actual error; a refusal, rate limit, or empty answer is not a recommendation.

Only the `triage` and `reviewer` operations are published. Requests to other operation paths are not routed to
Foundry.

### 6. Check your understanding (1 min)

- The Lab client token reaches API Management. What identity does API Management use to reach Foundry?
- Where would you change the triage agent's behaviour, and how would that change reach Foundry?
- Why does Lab 1 call Foundry directly with pinned versions instead of using this gateway path?
- Who decides the outcome of an alert?

## Next

Continue to Lab 1 - AgentOps.

===

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

- Complete Lab 0, including sign-in, `.env`, and publishing both agents.
- One of the two evaluation runners. They behave the same way and write the same Evidence Pack:
  - Python: Python 3.12 with the packages in
    requirements.txt, provided on the lab VM. This is the default path below.
  - .NET: an alternative if the .NET 10 SDK is available.
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

Open data/lab1-evaluations.json. It contains four approved seed expectations:

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

Use scripts/demo_blocked_release.ps1 to make a controlled, reversible change
to triage. The script backs up your current triage instructions, publishes and
evaluates them as the baseline, then temporarily replaces them with the
deliberately faulty lab example. That candidate always recommends
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

Then run the drill:

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
