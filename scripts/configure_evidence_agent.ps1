param(
    [Parameter(Mandatory)][string]$ResourceGroup,
    [string]$EnvironmentName = '',
    [object]$DeploymentOutputs = $null,
    [switch]$ValidateOnly
)
$ErrorActionPreference = 'Stop'
$outputs = if ($null -ne $DeploymentOutputs) { $DeploymentOutputs } else {
    & (Join-Path $PSScriptRoot 'env.ps1') -ResourceGroup $ResourceGroup -PassThru
}
foreach ($key in @('FOUNDRY_PROJECT_ENDPOINT', 'FOUNDRY_NAME', 'FOUNDRY_PROJECT_NAME',
    'INVESTIGATOR_PLANNING_DEPLOYMENT', 'AZURE_SEARCH_ENDPOINT', 'AZURE_SEARCH_SERVICE_NAME',
    'AZURE_SEARCH_KNOWLEDGE_BASE_NAME')) {
    if (-not $outputs.$key.value) { throw "Missing $key in the lab deployment outputs." }
}
$group = az group show --name $ResourceGroup --query '{id:id,location:location}' --output json | ConvertFrom-Json
if ($LASTEXITCODE -or -not $group.id) { throw 'Could not read the lab resource group.' }
$subscription = ($group.id -split '/')[2]
$projectId = "$($group.id)/providers/Microsoft.CognitiveServices/accounts/$($outputs.FOUNDRY_NAME.value)/projects/$($outputs.FOUNDRY_PROJECT_NAME.value)"
$expected = [ordered]@{
    AZURE_SUBSCRIPTION_ID = $subscription
    AZURE_RESOURCE_GROUP = $ResourceGroup
    AZURE_LOCATION = $group.location
    AZURE_AI_PROJECT_ID = $projectId
    FOUNDRY_PROJECT_ENDPOINT = $outputs.FOUNDRY_PROJECT_ENDPOINT.value
    AZURE_AI_MODEL_DEPLOYMENT_NAME = $outputs.INVESTIGATOR_PLANNING_DEPLOYMENT.value
    AZURE_SEARCH_SERVICE_NAME = $outputs.AZURE_SEARCH_SERVICE_NAME.value
    AZURE_SEARCH_ENDPOINT = $outputs.AZURE_SEARCH_ENDPOINT.value.TrimEnd('/')
    AZURE_SEARCH_KNOWLEDGE_BASE_NAME = $outputs.AZURE_SEARCH_KNOWLEDGE_BASE_NAME.value
    AZD_AGENT_SKIP_ROLE_ASSIGNMENTS = 'true'
}
if (-not $EnvironmentName) {
    $EnvironmentName = ($ResourceGroup.ToLowerInvariant() -replace '[^a-z0-9-]', '-')
}
$environments = @(azd env list --output json | ConvertFrom-Json)
if ($LASTEXITCODE) { throw 'Could not list azd environments.' }
$exists = $EnvironmentName -in $environments.Name
if (-not $exists) {
    if ($ValidateOnly) { throw 'The lab azd environment is missing. Run scripts/env.ps1 for this resource group first.' }
    azd env new $EnvironmentName --subscription $subscription --location $group.location --no-prompt
    if ($LASTEXITCODE) { throw 'Could not create the lab azd environment.' }
}
$current = @{}
foreach ($key in $expected.Keys) {
    $previousPreference = $ErrorActionPreference
    try {
        # Windows PowerShell treats native stderr notices as errors, even when the command succeeds.
        $ErrorActionPreference = 'Continue'
        $value = azd env get-value $key --environment $EnvironmentName 2>$null
        $readExitCode = $LASTEXITCODE
    } finally {
        $ErrorActionPreference = $previousPreference
    }
    $current[$key] = if ($readExitCode) { '' } else { "$value" }
}
foreach ($key in @('AZURE_SUBSCRIPTION_ID', 'AZURE_RESOURCE_GROUP', 'AZURE_AI_PROJECT_ID', 'FOUNDRY_PROJECT_ENDPOINT')) {
    if ($current[$key] -and $current[$key] -ine $expected[$key]) {
        throw "The azd environment '$EnvironmentName' targets a different lab ($key). It has not been changed."
    }
}
foreach ($key in $expected.Keys) {
    if ($current[$key] -ceq $expected[$key]) { continue }
    if ($ValidateOnly) { throw "The azd setting $key does not match the lab. Run scripts/env.ps1 for this resource group first." }
    azd env set $key $expected[$key] --environment $EnvironmentName
    if ($LASTEXITCODE) { throw "Could not save azd setting $key." }
}
if (-not $ValidateOnly) {
    azd env select $EnvironmentName
    if ($LASTEXITCODE) { throw 'Could not select the lab azd environment.' }
    Write-Host "Evidence agent deployment is configured for '$EnvironmentName'. Run azd deploy."
}
