# Deploys the Lab 0 landing zone into an existing resource group and creates the agents.
# Run by the Skillable lifecycle action (or by you, to test in your own subscription).
# Needs Owner or Role Based Access Control Administrator on the resource group, because it assigns roles.
# The owner must separately run setup-entra-apps.ps1 on the learner VM before Lab 0 gateway calls.
param(
    [Parameter(Mandatory)][string]$ResourceGroup,
    [string]$LearnerPrincipalId = ''
)
$ErrorActionPreference = 'Stop'
$root = Join-Path $PSScriptRoot '..'

# The identity running this script creates the agents, so it gets Foundry User too.
$account = az account show | ConvertFrom-Json
if ($account.user.type -eq 'user') {
    $operatorId = az ad signed-in-user show --query id -o tsv
    $operatorType = 'User'
} else {
    $operatorId = az ad sp show --id $account.user.name --query id -o tsv
    $operatorType = 'ServicePrincipal'
}

az deployment group create `
    --resource-group $ResourceGroup `
    --name main `
    --template-file (Join-Path $root 'infra' 'main.bicep') `
    --parameters learnerPrincipalId=$LearnerPrincipalId operatorPrincipalId=$operatorId operatorPrincipalType=$operatorType `
    --output none
if ($LASTEXITCODE) { throw 'Bicep deployment failed.' }

& (Join-Path $PSScriptRoot 'env.ps1') -ResourceGroup $ResourceGroup
& (Join-Path $PSScriptRoot 'create_agents.ps1')
Write-Host 'Resources and agents deployed. The owner must run scripts\setup-entra-apps.ps1 on the learner VM to configure Lab 0 gateway authentication; rerun it after every Bicep redeployment.'
