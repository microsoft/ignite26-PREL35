# Infrastructure

[main.bicep](main.bicep) deploys the lab landing zone into one resource group: Foundry (account, project,
gpt-5-mini and gpt-5-nano deployments), API Management (Basic v2) as the gateway in front of the agents, Log
Analytics and Application Insights. It also includes Azure AI Search, Web IQ, an embedding deployment and the
research connections and roles. [agents-api-policy.xml](agents-api-policy.xml) is the gateway policy.

Foundry agents aren't Azure resources, so Bicep can't create them. [scripts/create_agents.py](../scripts/create_agents.py)
creates the prompt agents from top-level Markdown files in [src/agents](../src/agents) after the deployment.
The standalone hosted evidence agent is deployed separately with azd, as described below.

## Deploy

Skillable provisions the ARM template before the learner starts. Then it executes lifecycle hooks to setup permissions. To test in your own subscription:

```powershell
az group create --name rg-prel35-lab --location eastus2
uv sync
uv run deploy-lab --resource-group rg-prel35-lab --learner-principal-id <learner-object-id>
```

To deploy with Azure Developer CLI into an existing resource group:

```powershell
uv sync
azd auth login
azd env new
azd env set AZURE_RESOURCE_GROUP rg-prel35-lab
azd up
```

The Python `postprovision` hook writes deployment outputs to `.env`, creates the Foundry prompt agents,
sets up the research index and knowledge base, and configures the hosted evidence deployment target.
`deploy-lab` uses `azd provision`; it does not deploy Bicep through a separate Azure CLI path.

The identity running it needs Owner or Role Based Access Control Administrator on the resource group, because the
template assigns **Foundry User** to API Management's managed identity, the learner and itself.
Skillable can keep the learner at **Reader** on the resource group. When `learnerPrincipalId` is supplied,
Bicep also grants **Cognitive Services Contributor** on the Foundry project, **Search Service Contributor**
and **Search Index Data Contributor** on the lab Search service, and **Contributor** on the Web IQ account.
The Foundry and Search roles let the learner save
an existing Web IQ API key in both integrations and run the knowledge-base check, without resource-group
Contributor or role-assignment permissions. The contributor roles permit management of those scoped resources,
not just API-key updates. Creating a key in the Web IQ portal is separate; portal profile access must still
be verified independently of the Azure role assignments. The deployment takes about 15 to 20 minutes, mostly API Management.

## Skillable post-build setup

After ARM has created the resources, paste
[`infra/skillable/assign-learner.ps1`](skillable/assign-learner.ps1) into a blocking Skillable **Cloud Platform / Azure /
PowerShell** post-build action. It runs with Skillable's provisioning credentials, not the learner's VM login.
Run it after the resource deployment has finished.

Replace the old Foundry-only setup action; do not run both. The script is standalone and needs no
arguments in Skillable: the resource-group token and automatic learner discovery provide the defaults.

The action **assigns roles only**. It discovers learners with subscription-level `LOD Reader` or
`LOD Contributor` and grants **Foundry User** on Foundry, **Search Service Contributor** and
**Search Index Data Contributor** on the lab Search service. These let learners create the index and upload
its documents. It retains the project-scoped **Cognitive Services Contributor** and Web IQ account
**Contributor** permissions used by the local setup. It also checks the Search and project managed identities'
existing scoped permissions. It never gives learners permission to assign roles.

No documents are bundled in the action, and it creates no index, knowledge base, Toolbox or agent.
It needs no VM files, Python or azd. Its timestamped progress
and errors are written to the action output and a transcript whose path is printed at startup.
For the matching role cleanup, paste
[`infra/skillable/remove-setup-roles.ps1`](skillable/remove-setup-roles.ps1) into a blocking **Cloud Platform / Azure /
PowerShell** action on **Tearing Down**, before deleting resources. Replace the old Foundry-only role cleanup.
The new setup marks assignments it creates; teardown removes only those marked assignments at their exact
resource scopes. Permissions that already existed, including grants from the old script, are left alone.
Teardown still works if the learner's LOD assignment has already been removed.
Keep the separate Foundry deletion/purge action in [`infra/skillable/teardown.ps1`](skillable/teardown.ps1);
the new role cleanup does not delete resources.

Both role actions are maintained directly in `infra/skillable/`; there are no generated root copies or
template-building steps.

After the action finishes, the learner signs in to the lab subscription and runs this in a terminal
from the repository root on the VM:

```powershell
uv run setup-lab --resource-group <lab-resource-group>
```

[`setup_lab.py`](../scripts/setup_lab.py) reads [`data/investigator/schemes.json`](../data/investigator/schemes.json)
from the local repository, creates `fincrime-schemes`, generates embeddings and uploads the eight synthetic
notes, and creates the knowledge source and `fincrime-research` knowledge base.
It saves lab settings while preserving custom `.env` entries and configures the lab's azd environment.
It discovers existing resources directly; successful ARM deployment-output records are not required.
It never deploys infrastructure, assigns roles or changes gateway controls.
It offers Web IQ setup after the knowledge base is ready. Accept to open the existing hidden-key workflow,
or decline and run `uv run setup-webiq` later. Noninteractive runs print that command instead of waiting for input.
Newly assigned roles may need time to take effect; if setup reports
HTTP 403, wait for the permissions to take effect and rerun it.

## Deploy the evidence agent

After normal lab setup, run this from the repository root:

```powershell
azd deploy
```

Learner `uv run setup-lab`, normal `uv run deploy-lab` setup and `uv run lab-env` environment loading configure and select the lab's azd
environment automatically when azd is installed. No manual `azd env set` commands are needed. Existing environments
that target another subscription, resource group or Foundry project are not overwritten. For an older lab, run
`uv run lab-env --resource-group <your-lab-resource-group>` once to refresh its deployment settings.

Use azd 1.37.1 or later with `azure.ai.agents` 1.0.0-beta.17 or later and `azure.ai.connections` and
`azure.ai.toolboxes` 1.0.0-beta.8 or later, signed in to the lab's subscription.
The learner needs the scoped Foundry/project permissions granted during setup, not permission to assign roles.
Environment loading sets `AZD_AGENT_SKIP_ROLE_ASSIGNMENTS=true` to disable azd's automatic role grants.

Before deploying, azd checks the deployed Web IQ connection, knowledge source and knowledge-base attachment.
If setup is missing or incomplete, it runs the interactive Web IQ setup automatically: reuse `WEBIQ_API_KEY`
from `.env`, or guide the user through the portal and hidden key prompt, then run the normal search checks.
This first-time setup also needs permission to manage Foundry connections and Search knowledge sources.
Setup must finish before agent deployment proceeds. In a noninteractive terminal without a saved key, deployment
stops with instructions rather than waiting for input. Already configured integrations are left unchanged,
without requiring a local key or running another search. This metadata check does not verify whether a stored key has expired.

Deployment uploads the Python source and builds dependencies on the hosted platform; no local Python 3.13
installation, Docker build, new registry or model deployment is required. It reuses the existing Foundry project
and `gpt-5-mini`. [`azure.yaml`](../azure.yaml) declares three services in dependency order:
the `evidence-research` connection, `evidence-tools` Toolbox and hosted `evidence-agent`.
The connection uses **ProjectManagedIdentity** with the Search token audience.
The Toolbox exposes only `knowledge_base_retrieve`; its version-specific endpoint is passed to the agent.
The project identity has **Search Index Data Reader** from privileged setup. The hosted agent needs no
per-agent Search role, and there is no role-assignment post-deploy hook.
Use `azd deploy` from the repository root to deploy all three services; a targeted `azd deploy evidence-agent`
does not create or refresh its dependencies.
`azd deploy` does not provision infrastructure, reset gateway controls, republish prompt agents or connect
evidence to the triage/reviewer workflow.

The [evidence prompt](../src/agents/evidence/instructions.md) is versioned alongside the other prompts and packaged
inside each hosted agent version. Its nested directory keeps it out of the prompt-agent publisher. The source
package includes only the agent code, prompt, fixed topics and requirements; it excludes `.env` and local environments.
The hosted agent receives no Web IQ API key. It authenticates to Foundry Toolbox using its own Entra identity;
Toolbox uses the project identity to call the knowledge-base MCP endpoint. `FoundryToolbox` forwards the
hosting platform's request context without exposing the raw retrieval tool to the model.
Search uses the independently configured Web IQ knowledge source.

The research tool accepts only [approved topics](../src/agents/evidence/topics.json), not free-text queries.
Code maps each topic to fixed library and public-context questions, so no case facts, identifiers or conversation history reach
the knowledge base or its public-web source. Public search is instructed to use official domains; the wrapper
also discards references outside `justice.gov` and `fincen.gov`, and drops telemetry.
The domain instructions are not a server-enforced search restriction.
Each brief allows at most four knowledge-base MCP calls and 180 seconds of research, without automatic retries;
the knowledge base can make multiple source calls internally. Repeated topics reuse the current brief's result.
The two questions do not force source selection: if either source type is missing, the brief reports partial research.

The brief separates supplied observations, attributed explanations, cited context, alternatives and missing evidence.
It reports partial or unavailable research rather than inventing sources, and makes no case disposition.
Research incurs model, Search and potentially Web IQ charges. Use only synthetic, de-identified inputs.

[Three original synthetic scenarios](../data/evidence/scenarios.json) cover payment-assistant recruitment,
inconsistent trade documents and rapid procurement transfers supported by a contract and reconciliation.
The last scenario checks that the agent recognizes a supported legitimate explanation instead of manufacturing suspicion.
To try a short example in a new session:

```powershell
azd ai agent invoke evidence-agent "Review this fictional, PII-free scenario: unfamiliar incoming transfers are forwarded to a cryptocurrency exchange; the holder says a remote payment-assistant job requires this, but no employer contract or sales records are supplied. Provide a cited evidence brief with alternatives and missing evidence, not a decision." --protocol responses --new-session
```

## Investigator resources

Every standard deployment includes the research resources through `main.bicep`; no enable flag or separate
azd deployment is needed. Its internal [investigator.bicep](investigator.bicep) module creates Basic Azure AI Search, Web IQ and the Foundry project connection
`investigator-search`. The connection uses the project's managed identity with **Search Index Data Reader** access
on the Search service. Search has its own managed identity with **Cognitive Services OpenAI User** access on Foundry
for query vectorization and knowledge-base query planning. These Search integrations use managed identities, not API keys.

The `models` list in `main.bicep` deploys and approves all three models, including
`text-embedding-3-small` version 1 (Standard, capacity 10). The two GPT decision models retain GlobalStandard.
Embeddings use 1,536 dimensions.

When adding a model to an existing allowlist, Foundry may temporarily reject it while the policy update
propagates. [Microsoft's guidance](https://learn.microsoft.com/en-us/azure/foundry/how-to/model-deployment-policy)
is to allow at least 15 minutes for policy assignments to take effect. If the approved model is still rejected,
retry the same provisioning command after that window; do not disable the policy or allow an entire publisher.

The Python post-provision hook calls the shared [Search setup](../scripts/setup_lab.py). It creates
the `fincrime-schemes` HNSW vector index and uploads the eight synthetic notes from
[data/investigator/schemes.json](../data/investigator/schemes.json). Document embeddings are generated during setup;
the index vectorizer uses Search's identity to embed text queries. Embedding calls are billable.

The same entry point creates the `fincrime-research-schemes` knowledge source over that index and the
`fincrime-research` knowledge base. It reuses the existing `gpt-5-mini` deployment for query planning with low
reasoning effort and returns extracted source content rather than a synthesized answer. Retrieval defaults limit
each request to 45 seconds, four documents and 5,000 output tokens (the service minimum); callers can override these defaults.
Only documents marked synthetic are eligible for the index source. Add the Web IQ MCP source through the organizer setup below.
The index includes a semantic configuration for its title, content and analyst guidance. Semantic ranking uses
Search's free plan, limited to 1,000 requests per service per month.

Knowledge bases and knowledge sources are Search data-plane objects, not native ARM resources. Setup uses
the `2026-08-01-preview` REST API because [LLM query planning for an index source requires the preview API](https://learn.microsoft.com/en-us/azure/search/agentic-retrieval-how-to-create-knowledge-base).
Index creation continues using the stable API. Retrieval incurs model and Search charges.

To repeat index and knowledge-base setup without deploying infrastructure, rewriting `.env` or publishing agent versions:

```powershell
uv run deploy-lab --resource-group rg-prel35-lab --index-only
```

Use this command if infrastructure provisioning succeeded but index setup failed. When infrastructure is unchanged,
azd can skip provisioning hooks rather than retrying index setup.

Unchanged notes are skipped without regenerating embeddings. Changed notes are upserted by ID; setup never deletes
the index or other documents. Existing additional knowledge-source references are preserved on reruns,
except the retired built-in Bing source `fincrime-research-web`, which is detached without deleting its source object.
A failed embedding, document upload or knowledge-base setup stops the command instead of reporting success.

Search defaults to `eastus`, independently of the resource group's and Foundry's region.
If Search needs a different region, pass `--investigator-search-location <region>` to `uv run deploy-lab`.
For azd, set `INVESTIGATOR_SEARCH_LOCATION` to that region.

## Set up Web IQ

After deployment, sign in with `az login` and run this in an interactive terminal. Use an identity that can
create Foundry project connections and has **Search Service Contributor** access on the Search service.

The default knowledge-base check also requires **Search Index Data Reader** (or **Search Index Data Contributor**).

```powershell
uv run setup-webiq
```

If `.env` already contains `WEBIQ_API_KEY`, the script reuses it without opening a browser or prompting.
Otherwise, it opens the [Web IQ profile portal](https://webiq.microsoft.ai/profiles), displays the deployed billing
resource ID, and guides you to select or connect that resource and create a key with the **web** service enabled.
Paste the key into the hidden terminal prompt, not into chat. It is saved locally as plaintext `WEBIQ_API_KEY` in the ignored `.env`.
Use an organizer-controlled shared key: project members can access credentials stored in a Foundry connection.
Protect `.env` and restrict project and Search administration access. On Unix, the script writes `.env` with owner-only permissions.

Following the [Web IQ MCP documentation](https://learn.microsoft.com/en-us/web-iq/integrations/mcp), both integrations
use `https://api.microsoft.ai/v3/mcp` and the `x-apikey` header, without an `APPID` query parameter:

- Foundry project connection **`investigator-webiq`** uses `RemoteTool` / `CustomKeys`, with `x-apikey` as the credential name.
- Search knowledge source **`fincrime-research-webiq`** uses `mcpServer` / `storedHeaders` with its own copy of the key and only the `web` tool.

The source description guides query planning to request `contentFormat=passage`, up to four results and
2,000 characters per result. Full-document formats (`text`, `html`, `markdown`) require separate Web IQ access.
These are planner instructions, not enforced runtime limits.

These are independent integrations. The knowledge source does not reference the Foundry connection, so it also
works with direct Search retrieval. The script adds it to `fincrime-research`, preserving the existing index source,
GPT-mini model and retrieval settings. Any retired built-in Bing source reference is detached; its source object is not deleted.
See [MCP knowledge-source authentication](https://learn.microsoft.com/en-us/azure/search/agentic-knowledge-source-how-to-mcp-server#authentication-options).
The managed identities used for Search and embeddings remain unchanged.

The script also saves the MCP endpoint, connection name/ID and source name in `.env`. Environment refreshes and
standard deployments preserve these settings and the key without printing them.
To rerun setup without opening the browser or prompting, reuse the saved key and update both integrations:

```powershell
uv run setup-webiq --force
```

`--force` is an alias for `--use-saved-key`. Target and access checks still apply.
The flag requires a saved key; if none is saved, the script stops and asks you to run normal setup first.

To rotate the key, create a replacement in the portal and update `WEBIQ_API_KEY` in `.env`, or remove that entry
and rerun normal setup to paste the replacement into hidden input. Both integrations are updated;
revoke the old key only after setup succeeds. Stored API-key headers are write-only and aren't checked by reading them back.
By default, setup checks the saved configuration, runs a direct Web IQ MCP search, and queries the knowledge base
through its Web IQ MCP source. Both checks use a fixed, identifier-free DOJ money-mule topic and can incur Web IQ,
model and Search charges. The direct search requests four passage results of up to 2,000 characters each.
The knowledge-base request allows 45 seconds, four output documents and an output size of 5,000. It disables all
other sources for that request only, preserving the saved index source and knowledge-base settings.
The command fails if a check fails or returns no results; the saved configuration remains available for a rerun.
No automatic search retries are made. The checks do not invoke a Foundry agent or connect the investigator workflow.

To configure and verify the saved settings without running either search:

```powershell
uv run setup-webiq --no-run
# Or reuse the saved key explicitly:
uv run setup-webiq --force --no-run
```

`--no-run` skips both search checks, not the configuration changes.

Once added, the knowledge base can select Web IQ for retrieval. Web research can incur charges and sends queries
outside Azure geographic/compliance boundaries. Use only approved public-topic queries, never customer data or full
case prompts. [The ALT-1008 example](../data/README.md#a-case-with-useful-public-research) provides a realistic fictional
case and a fixed public query with verified DOJ context.
