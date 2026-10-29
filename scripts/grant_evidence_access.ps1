param([string]$EnvironmentName = $env:AZURE_ENV_NAME)
$ErrorActionPreference = 'Stop'
function Get-DeploymentValue([string]$Name) {
    $value = azd env get-value $Name --environment $EnvironmentName
    if ($LASTEXITCODE -or -not $value) { throw "The evidence deployment did not publish $Name." }
    return "$value"
}
$principal = Get-DeploymentValue 'AGENT_EVIDENCE_AGENT_INSTANCE_IDENTITY_PRINCIPAL_ID'
$projectId = Get-DeploymentValue 'AZURE_AI_PROJECT_ID'
$search = Get-DeploymentValue 'AZURE_SEARCH_SERVICE_NAME'
$parsedPrincipal = [guid]::Empty
if (-not [guid]::TryParse($principal, [ref]$parsedPrincipal)) { throw 'The deployed evidence identity is not valid.' }
$groupScope = $projectId -replace '/providers/Microsoft.CognitiveServices/accounts/[^/]+/projects/[^/]+$', ''
if ($groupScope -eq $projectId -or $search -notmatch '^[a-z0-9][a-z0-9-]+[a-z0-9]$') {
    throw 'The evidence Search scope is not valid.'
}
$scope = "$groupScope/providers/Microsoft.Search/searchServices/$search"
$role = '1407120a-92aa-4202-b7e9-c0e197c71c8f'
$assignments = @(az role assignment list --scope $scope --query "[?principalId=='$principal' && ends_with(roleDefinitionId, '/$role')].id" --output json | ConvertFrom-Json)
if ($LASTEXITCODE) { throw 'Could not check the evidence Search role.' }
if (-not $assignments.Count) {
    az role assignment create --assignee-object-id $principal --assignee-principal-type ServicePrincipal `
        --role $role --scope $scope --output none
    if ($LASTEXITCODE) { throw 'Could not grant the evidence identity Search Index Data Reader. The deployer needs role-assignment permission on this Search service.' }
}
Write-Host 'Evidence agent has Search Index Data Reader access on the lab Search service.'
