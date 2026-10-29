# Infrastructure

[main.bicep](main.bicep) deploys the lab landing zone into one resource group: Foundry (account, project,
gpt-5-mini and gpt-5-nano deployments), API Management (Basic v2) as the gateway in front of the agents, Log
Analytics and Application Insights. It also includes Azure AI Search, Web IQ, an embedding deployment and the
research connections and roles. [agents-api-policy.xml](agents-api-policy.xml) is the gateway policy.

Foundry agents aren't Azure resources, so Bicep can't create them. [scripts/create_agents.py](../scripts/create_agents.py)
creates them from [src/agents](../src/agents) after the deployment.

## Deploy

The Skillable lifecycle action runs this for each learner. To test in your own subscription:

```powershell
az group create --name rg-prel35-lab --location eastus2
uv venv --python 3.12; uv pip install -r requirements.txt
.\scripts\deploy.ps1 -ResourceGroup rg-prel35-lab -LearnerPrincipalId <learner-object-id>
```

To deploy with Azure Developer CLI into an existing resource group:

```powershell
uv sync
azd auth login
azd env new
azd env set AZURE_RESOURCE_GROUP rg-prel35-lab
azd up
```

The `postprovision` hook writes the deployment outputs to `.env`, creates the Foundry agents and
calls `scripts/deploy.ps1 -IndexOnly` to set up the research index and knowledge base.

The identity running it needs Owner or Role Based Access Control Administrator on the resource group, because the
template assigns **Foundry User** to API Management's managed identity, the learner and itself. The learner only
needs Contributor. The deployment takes about 15 to 20 minutes, mostly API Management.

## Investigator resources

Every standard deployment includes the research resources through `main.bicep`; no enable flag or separate
azd deployment is needed. Its internal [investigator.bicep](investigator.bicep) module creates Basic Azure AI Search, Web IQ and the Foundry project connection
`investigator-search`. The connection uses the project's managed identity with **Search Index Data Reader** access
on the Search service. Search has its own managed identity with **Cognitive Services OpenAI User** access on Foundry
for query vectorization and knowledge-base query planning. Neither connection uses an API key.

The `models` list in `main.bicep` deploys and approves all three models, including
`text-embedding-3-small` version 1 (Standard, capacity 10). The two GPT decision models retain GlobalStandard.
Embeddings use 1,536 dimensions.

When adding a model to an existing allowlist, Foundry may temporarily reject it while the policy update
propagates. [Microsoft's guidance](https://learn.microsoft.com/en-us/azure/foundry/how-to/model-deployment-policy)
is to allow at least 15 minutes for policy assignments to take effect. If the approved model is still rejected,
retry the same provisioning command after that window; do not disable the policy or allow an entire publisher.

The post-provision hook calls [scripts/deploy.ps1](../scripts/deploy.ps1) with `-IndexOnly`. That entry point creates
the `fincrime-schemes` HNSW vector index and uploads the eight synthetic notes from
[data/investigator/schemes.json](../data/investigator/schemes.json). Document embeddings are generated during setup;
the index vectorizer uses Search's identity to embed text queries. Embedding calls are billable.

The same entry point creates the `fincrime-research-schemes` knowledge source over that index and the
`fincrime-research` knowledge base. It reuses the existing `gpt-5-mini` deployment for query planning with low
reasoning effort and returns extracted source content rather than a synthesized answer. Retrieval defaults limit
each request to 45 seconds, four documents and 5,000 output tokens (the service minimum); callers can override these defaults.
Only documents marked synthetic are eligible for the index source. Web IQ setup is a separate organizer step below.
The index includes a semantic configuration for its title, content and analyst guidance. Semantic ranking uses
Search's free plan, limited to 1,000 requests per service per month.

Knowledge bases and knowledge sources are Search data-plane objects, not native ARM resources. Setup uses
the `2026-08-01-preview` REST API because [LLM query planning for an index source requires the preview API](https://learn.microsoft.com/en-us/azure/search/agentic-retrieval-how-to-create-knowledge-base).
Index creation continues using the stable API. Retrieval incurs model and Search charges.

To repeat index and knowledge-base setup without deploying infrastructure, rewriting `.env` or publishing agent versions:

```powershell
.\scripts\deploy.ps1 -ResourceGroup rg-prel35-lab -IndexOnly
```

Use this command if infrastructure provisioning succeeded but index setup failed. When infrastructure is unchanged,
azd can skip provisioning hooks rather than retrying index setup.

Unchanged notes are skipped without regenerating embeddings. Changed notes are upserted by ID; setup never deletes
the index or other documents. Existing additional knowledge-source references are preserved on reruns.
A failed embedding, document upload or knowledge-base setup stops the command instead of reporting success.

Search defaults to `eastus`, independently of the resource group's and Foundry's region.
If Search needs a different region, pass `-InvestigatorSearchLocation <region>` to `scripts/deploy.ps1`.
For azd, set `INVESTIGATOR_SEARCH_LOCATION` to that region.

## Set up Web IQ

After deployment, sign in with `az login` and run this in an interactive terminal. Use an identity that can
create Foundry project connections and has **Search Service Contributor** access on the Search service:

```powershell
.\scripts\setup_webiq.ps1
```

The script opens the [Web IQ profile portal](https://webiq.microsoft.ai/profiles), displays the deployed billing
resource ID, and guides you to select or connect that resource and create a key with the **web** service enabled.
Paste the key into the hidden terminal prompt, not into chat. It is saved locally as plaintext `WEBIQ_API_KEY` in the ignored `.env`.
Use an organizer-controlled shared key: project members can access credentials stored in a Foundry connection.
Protect `.env` and restrict project and Search administration access. On Unix, the script writes `.env` with owner-only permissions.

Following the [Web IQ MCP documentation](https://learn.microsoft.com/en-us/web-iq/integrations/mcp), both integrations
use `https://api.microsoft.ai/v3/mcp` and the `x-apikey` header, without an `APPID` query parameter:

- Foundry project connection **`investigator-webiq`** uses `RemoteTool` / `CustomKeys`, with `x-apikey` as the credential name.
- Search knowledge source **`fincrime-research-webiq`** uses `mcpServer` / `storedHeaders` with its own copy of the key and only the `web` tool.

These are independent integrations. The knowledge source does not reference the Foundry connection, so it also
works with direct Search retrieval. The script adds it to `fincrime-research`, preserving the existing index source,
GPT-mini model and retrieval settings. See [MCP knowledge-source authentication](https://learn.microsoft.com/en-us/azure/search/agentic-knowledge-source-how-to-mcp-server#authentication-options).
The managed identities used for Search and embeddings remain unchanged.

The script also saves the MCP endpoint, connection name/ID and source name in `.env`. Environment refreshes and
standard deployments preserve these settings and the key without printing them.
To finish a partially completed setup using the saved key:

```powershell
.\scripts\setup_webiq.ps1 -UseSavedKey
```

To rotate the key, create a replacement in the portal and rerun without `-UseSavedKey`. Both integrations are updated;
revoke the old key only after setup succeeds. The script checks the saved configuration, but does not run a web search
or connect the investigator agent workflow.

Once added, the knowledge base can select Web IQ for retrieval. Web research can incur charges and sends queries
outside Azure geographic/compliance boundaries. Use only approved public-topic queries, never customer data or full
case prompts. [The ALT-1008 example](../data/README.md#a-case-with-useful-public-research) provides a realistic fictional
case and a fixed public query with verified DOJ context.
