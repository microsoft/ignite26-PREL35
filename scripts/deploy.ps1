# Deploys the complete lab; -IndexOnly sets up existing research data-plane objects without redeploying infrastructure.
# Run by the Skillable lifecycle action (or by you, to test in your own subscription).
# Needs Owner or Role Based Access Control Administrator on the resource group, because it assigns roles.
param(
    [Parameter(Mandatory)][string]$ResourceGroup,
    [string]$LearnerPrincipalId = '',
    [string]$InvestigatorSearchLocation = '',
    [switch]$IndexOnly,
    [string]$OperatorPrincipalId = '',
    [string]$LogPath = ''
)
$ErrorActionPreference = 'Stop'
$root = Join-Path $PSScriptRoot '..'

if (-not $LogPath) {
    $LogPath = Join-Path $root "logs/deploy-$(Get-Date -Format 'yyyyMMdd-HHmmss-fff')-$PID.log"
}
$LogPath = $ExecutionContext.SessionState.Path.GetUnresolvedProviderPathFromPSPath($LogPath)
try {
    [System.IO.Directory]::CreateDirectory((Split-Path $LogPath -Parent)) | Out-Null
    Start-Transcript -LiteralPath $LogPath -Append | Out-Null
} catch {
    throw "Could not start deployment log at '$LogPath': $($_.Exception.Message)"
}

function Write-DeploymentStep {
    param([string]$Message)
    Write-Host ("[{0}] {1}" -f (Get-Date -Format 'yyyy-MM-dd HH:mm:ss'), $Message)
}

try {
    Write-DeploymentStep "Starting deployment for resource group '$ResourceGroup' (IndexOnly=$IndexOnly)."
    Write-DeploymentStep "Log file: $LogPath"

    if (-not $IndexOnly) {
        Write-DeploymentStep 'Resolving the operator object ID.'
        # The identity running this script creates the agents, so it gets Foundry User too.
        $operatorId = $OperatorPrincipalId.Trim()
        if (-not $operatorId) {
            $accountJson = az account show
            if ($LASTEXITCODE) { throw 'Could not read the Azure CLI account. Run az login before deploying.' }
            $account = $accountJson | ConvertFrom-Json
            $operatorId = switch ($account.user.type) {
                'user' { az ad signed-in-user show --query id -o tsv }
                'servicePrincipal' { az ad sp show --id $account.user.name --query id -o tsv }
                default { throw 'Could not identify the Azure CLI account type. Supply -OperatorPrincipalId with the operator object ID.' }
            }
            if ($LASTEXITCODE -or [string]::IsNullOrWhiteSpace($operatorId)) {
                throw 'Could not find the operator object ID using Azure CLI. Supply -OperatorPrincipalId with the operator object ID.'
            }
        }
        Write-DeploymentStep "Using operator object ID: $operatorId"

        $deploymentParameters = @(
            "learnerPrincipalId=$LearnerPrincipalId"
            "operatorPrincipalId=$operatorId"
        )
        if ($InvestigatorSearchLocation) {
            $deploymentParameters += "investigatorSearchLocation=$InvestigatorSearchLocation"
        }

        Write-DeploymentStep 'Deploying infrastructure and role assignments.'
        az deployment group create `
            --resource-group $ResourceGroup `
            --name main `
            --template-file (Join-Path $root 'infra/main.bicep') `
            --parameters @deploymentParameters `
            --output none 2>&1 | Out-Host
        if ($LASTEXITCODE) { throw "Bicep deployment failed (exit code $LASTEXITCODE)." }
        Write-DeploymentStep 'Infrastructure and role assignments deployed.'

        Write-DeploymentStep 'Writing environment settings and configuring the evidence deployment target.'
        & (Join-Path $PSScriptRoot 'env.ps1') -ResourceGroup $ResourceGroup
        Write-DeploymentStep 'Environment settings saved.'

        Write-DeploymentStep 'Publishing prompt agents.'
        uv run --directory $root python (Join-Path $PSScriptRoot 'create_agents.py') 2>&1 | Out-Host
        if ($LASTEXITCODE) { throw "Agent creation failed (exit code $LASTEXITCODE)." }
        Write-DeploymentStep 'Prompt agents published.'
    } else {
        Write-DeploymentStep 'IndexOnly: skipping infrastructure deployment, role assignments, environment writes and agent publication.'
    }

    Write-DeploymentStep 'Reading and checking deployment outputs for Search setup.'
    $outputs = & (Join-Path $PSScriptRoot 'env.ps1') -ResourceGroup $ResourceGroup -PassThru
    foreach ($name in @('AZURE_SEARCH_ENDPOINT', 'AZURE_SEARCH_INDEX_NAME', 'AZURE_SEARCH_KNOWLEDGE_BASE_NAME', 'INVESTIGATOR_EMBEDDING_ENDPOINT', 'INVESTIGATOR_EMBEDDING_DEPLOYMENT', 'INVESTIGATOR_PLANNING_DEPLOYMENT')) {
        if (-not $outputs.$name.value) {
            throw "Missing $name. Provision the lab infrastructure before setting up the index and knowledge base."
        }
    }
    Write-DeploymentStep 'Creating the Search index, seeding documents and setting up the knowledge base.'
    uv run --directory $root python (Join-Path $PSScriptRoot 'create_search_index.py') `
        --endpoint $outputs.AZURE_SEARCH_ENDPOINT.value `
        --index $outputs.AZURE_SEARCH_INDEX_NAME.value `
        --embedding-endpoint $outputs.INVESTIGATOR_EMBEDDING_ENDPOINT.value `
        --embedding-deployment $outputs.INVESTIGATOR_EMBEDDING_DEPLOYMENT.value `
        --knowledge-base $outputs.AZURE_SEARCH_KNOWLEDGE_BASE_NAME.value `
        --planning-deployment $outputs.INVESTIGATOR_PLANNING_DEPLOYMENT.value 2>&1 | Out-Host
    if ($LASTEXITCODE) { throw "Search index or knowledge base setup failed (exit code $LASTEXITCODE)." }
    Write-DeploymentStep 'Deployment completed successfully.'
} catch {
    Write-DeploymentStep "ERROR: $($_.Exception.ToString())"
    Write-Host $_.InvocationInfo.PositionMessage
    Write-Host $_.ScriptStackTrace
    Write-DeploymentStep "Deployment failed. See log file: $LogPath"
    throw
} finally {
    Stop-Transcript | Out-Null
}
