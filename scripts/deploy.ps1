# Deploys the complete lab; -IndexOnly sets up existing research data-plane objects without redeploying infrastructure.
# Run by the Skillable lifecycle action (or by you, to test in your own subscription).
# Needs Owner or Role Based Access Control Administrator on the resource group, because it assigns roles.
param(
    [Parameter(Mandatory)][string]$ResourceGroup,
    [string]$LearnerPrincipalId = '',
    [string]$InvestigatorSearchLocation = '',
    [switch]$IndexOnly
)
$ErrorActionPreference = 'Stop'
$root = Join-Path $PSScriptRoot '..'

if (-not $IndexOnly) {
    # The identity running this script creates the agents, so it gets Foundry User too.
    $account = az account show | ConvertFrom-Json
    $operatorId = if ($account.user.type -eq 'user') {
        az ad signed-in-user show --query id -o tsv
    } else {
        az ad sp show --id $account.user.name --query id -o tsv
    }

    $deploymentParameters = @(
        "learnerPrincipalId=$LearnerPrincipalId"
        "operatorPrincipalId=$operatorId"
    )
    if ($InvestigatorSearchLocation) {
        $deploymentParameters += "investigatorSearchLocation=$InvestigatorSearchLocation"
    }

    az deployment group create `
        --resource-group $ResourceGroup `
        --name main `
        --template-file (Join-Path $root 'infra/main.bicep') `
        --parameters @deploymentParameters `
        --output none
    if ($LASTEXITCODE) { throw 'Bicep deployment failed.' }

    & (Join-Path $PSScriptRoot 'env.ps1') -ResourceGroup $ResourceGroup
    uv run --directory $root python (Join-Path $PSScriptRoot 'create_agents.py')
    if ($LASTEXITCODE) { throw 'Agent creation failed.' }
}

$outputs = & (Join-Path $PSScriptRoot 'env.ps1') -ResourceGroup $ResourceGroup -PassThru
foreach ($name in @('AZURE_SEARCH_ENDPOINT', 'AZURE_SEARCH_INDEX_NAME', 'AZURE_SEARCH_KNOWLEDGE_BASE_NAME', 'INVESTIGATOR_EMBEDDING_ENDPOINT', 'INVESTIGATOR_EMBEDDING_DEPLOYMENT', 'INVESTIGATOR_PLANNING_DEPLOYMENT')) {
    if (-not $outputs.$name.value) {
        throw "Missing $name. Provision the lab infrastructure before setting up the index and knowledge base."
    }
}
uv run --directory $root python (Join-Path $PSScriptRoot 'create_search_index.py') `
    --endpoint $outputs.AZURE_SEARCH_ENDPOINT.value `
    --index $outputs.AZURE_SEARCH_INDEX_NAME.value `
    --embedding-endpoint $outputs.INVESTIGATOR_EMBEDDING_ENDPOINT.value `
    --embedding-deployment $outputs.INVESTIGATOR_EMBEDDING_DEPLOYMENT.value `
    --knowledge-base $outputs.AZURE_SEARCH_KNOWLEDGE_BASE_NAME.value `
    --planning-deployment $outputs.INVESTIGATOR_PLANNING_DEPLOYMENT.value
if ($LASTEXITCODE) { throw 'Search index or knowledge base setup failed.' }
