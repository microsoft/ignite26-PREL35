# Learner setup for existing resources. Reads the repository's data; never deploys infrastructure or assigns roles.
param(
    [Parameter(Mandatory)][string]$ResourceGroup,
    [string]$FoundryName = '',
    [string]$SearchName = '',
    [string]$EnvironmentName = ''
)
$ErrorActionPreference = 'Stop'
if ($PSVersionTable.PSVersion.Major -lt 7) {
    throw @"
PowerShell $($PSVersionTable.PSVersion) detected. This setup script currently requires PowerShell 7 or later (pwsh).
Run 'pwsh' to open PowerShell 7, then rerun the same setup command.
If 'pwsh' is not recognized, install PowerShell 7:
https://learn.microsoft.com/powershell/scripting/install/installing-powershell-on-windows
"@
}
$root = Split-Path $PSScriptRoot -Parent

function Read-AzJson([string[]]$Arguments) {
    $result = & az @Arguments --output json
    if ($LASTEXITCODE) { throw "Could not read existing Azure resources: az $($Arguments -join ' ')." }
    return ($result | ConvertFrom-Json)
}

function Find-Resource([string]$Type, [string]$Name = '', [string]$Kind = '') {
    $matches = @($resources | Where-Object {
        $_.type -ieq $Type -and (-not $Name -or $_.name -ieq $Name) -and (-not $Kind -or $_.kind -ieq $Kind)
    })
    if ($matches.Count -ne 1) {
        throw "Expected one existing $Type resource in $ResourceGroup; found $($matches.Count). Use -FoundryName or -SearchName if needed. No infrastructure was changed."
    }
    return $matches[0]
}

function Invoke-Python([string[]]$Arguments) {
    & uv run --directory $root python @Arguments
    if ($LASTEXITCODE) {
        throw 'Data-plane setup failed. This script cannot grant missing Foundry, Search or embedding permissions. Resolve the error above before rerunning.'
    }
}

$group = Read-AzJson @('group', 'show', '--name', $ResourceGroup)
$resources = @(Read-AzJson @('resource', 'list', '--resource-group', $ResourceGroup))
$foundry = Find-Resource 'Microsoft.CognitiveServices/accounts' $FoundryName 'AIServices'
$search = Find-Resource 'Microsoft.Search/searchServices' $SearchName
$webIq = Find-Resource 'Microsoft.WebIQ/accounts'
$apim = Find-Resource 'Microsoft.ApiManagement/service'
$insights = Find-Resource 'Microsoft.Insights/components'
$projectId = "$($foundry.id)/projects/fincrime"
$null = Read-AzJson @('resource', 'show', '--ids', $projectId, '--api-version', '2025-06-01')
$gateway = Read-AzJson @('resource', 'show', '--ids', $apim.id, '--api-version', '2024-05-01')
$monitoring = Read-AzJson @('resource', 'show', '--ids', $insights.id, '--api-version', '2020-02-02')
$models = @(Read-AzJson @('cognitiveservices', 'account', 'deployment', 'list', '--resource-group', $ResourceGroup, '--name', $foundry.name))
foreach ($model in @('gpt-5-mini', 'gpt-5-nano', 'text-embedding-3-small')) {
    if ($model -notin @($models | ForEach-Object { ($_.name -split '/')[-1] })) {
        throw "Existing model deployment '$model' is missing. An organizer must provision it first; this script does not deploy models or update model policy."
    }
}
if (-not $gateway.properties.gatewayUrl -or -not $monitoring.properties.ConnectionString) {
    throw 'The existing gateway or monitoring settings could not be read. No environment settings were changed.'
}

$envFile = Join-Path $root '.env'
$existingLines = @()
if (Test-Path $envFile) {
    if ((Get-Item $envFile -Force).Attributes -band [IO.FileAttributes]::ReparsePoint) {
        throw 'Refusing to overwrite a linked .env file.'
    }
    $existingLines = @(Get-Content $envFile)
}
$updates = [ordered]@{
    AZURE_RESOURCE_GROUP = $group.name
    FOUNDRY_NAME = $foundry.name
    FOUNDRY_PROJECT_NAME = 'fincrime'
    FOUNDRY_PROJECT_ENDPOINT = "https://$($foundry.name).services.ai.azure.com/api/projects/fincrime"
    MODEL_DEPLOYMENT = 'gpt-5-mini'
    APIM_NAME = $apim.name
    APIM_GATEWAY_URL = $gateway.properties.gatewayUrl
    APPLICATIONINSIGHTS_NAME = $insights.name
    APPLICATIONINSIGHTS_CONNECTION_STRING = $monitoring.properties.ConnectionString
    AZURE_SEARCH_SERVICE_NAME = $search.name
    AZURE_SEARCH_ENDPOINT = "https://$($search.name).search.windows.net"
    AZURE_SEARCH_LOCATION = $search.location
    AZURE_SEARCH_INDEX_NAME = 'fincrime-schemes'
    AZURE_SEARCH_CONNECTION_NAME = 'investigator-search'
    AZURE_SEARCH_CONNECTION_ID = "$projectId/connections/investigator-search"
    AZURE_SEARCH_KNOWLEDGE_BASE_NAME = 'fincrime-research'
    INVESTIGATOR_PLANNING_DEPLOYMENT = 'gpt-5-mini'
    INVESTIGATOR_EMBEDDING_ENDPOINT = "https://$($foundry.name).openai.azure.com"
    INVESTIGATOR_EMBEDDING_DEPLOYMENT = 'text-embedding-3-small'
    INVESTIGATOR_EMBEDDING_MODEL = 'text-embedding-3-small'
    INVESTIGATOR_EMBEDDING_DIMENSIONS = '1536'
    WEBIQ_RESOURCE_ID = $webIq.id
    WEBIQ_ACCOUNT_NAME = $webIq.name
}
foreach ($line in $existingLines) {
    if ($line -match '^\s*(?:export\s+)?(AZURE_RESOURCE_GROUP|FOUNDRY_PROJECT_ENDPOINT|WEBIQ_RESOURCE_ID)\s*=\s*(.*)$') {
        $name, $value = $Matches[1], $Matches[2].Trim().Trim([char[]]@('"', "'"))
        if ($value -and $value -ine $updates[$name]) {
            throw ".env targets a different lab ($name). Use the correct lab folder; its settings have not been changed."
        }
    }
}
if (-not (Test-Path $envFile)) { $null = New-Item -ItemType File -Path $envFile }
$updates | ConvertTo-Json -Compress | & uv run --directory $root python -c `
    "import json, sys; from pathlib import Path; from scripts.setup_webiq import save_settings; save_settings(Path('.env'), json.load(sys.stdin))"
if ($LASTEXITCODE) { throw 'Could not save .env. No agents or knowledge bases were updated.' }
Write-Host 'Saved settings from existing resources; preserved custom .env entries without printing their values.'

if (Get-Command azd -ErrorAction SilentlyContinue) {
    $deploymentOutputs = [pscustomobject]@{}
    foreach ($key in $updates.Keys) {
        $deploymentOutputs | Add-Member -NotePropertyName $key -NotePropertyValue @{ value = $updates[$key] }
    }
    & (Join-Path $PSScriptRoot 'configure_evidence_agent.ps1') -ResourceGroup $ResourceGroup `
        -EnvironmentName $EnvironmentName -DeploymentOutputs $deploymentOutputs
} else {
    Write-Warning 'azd is not installed. Index and prompt-agent setup can continue; install azd and rerun setup before deploying the hosted evidence agent.'
}

Write-Host 'Publishing the prompt agents.'
Invoke-Python @((Join-Path $PSScriptRoot 'create_agents.py'))

$documents = Join-Path $root 'data/investigator/schemes.json'
Write-Host "Creating and seeding the index from $documents, then setting up the knowledge base."
Invoke-Python @(
    (Join-Path $PSScriptRoot 'create_search_index.py'),
    '--data', $documents,
    '--endpoint', $updates.AZURE_SEARCH_ENDPOINT,
    '--index', $updates.AZURE_SEARCH_INDEX_NAME,
    '--embedding-endpoint', $updates.INVESTIGATOR_EMBEDDING_ENDPOINT,
    '--embedding-deployment', $updates.INVESTIGATOR_EMBEDDING_DEPLOYMENT,
    '--knowledge-base', $updates.AZURE_SEARCH_KNOWLEDGE_BASE_NAME,
    '--planning-deployment', $updates.INVESTIGATOR_PLANNING_DEPLOYMENT
)

Write-Host 'Post-provisioning setup finished. No infrastructure, role assignments or gateway settings were changed.'
Write-Host 'Web IQ setup is separate: run ./scripts/setup_webiq.ps1 when you have its API key.'
Write-Host 'Next, run azd deploy from the repository root to create the connection, Toolbox and hosted evidence agent. No hosted-agent role assignment is needed.'
