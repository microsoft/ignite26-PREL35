# Writes the lab environment values (deployment outputs) to .env at the repository root.
# Needs only Reader on the resource group, so learners run it in Lab 0.
param(
    [Parameter(Mandatory)][string]$ResourceGroup
)
$ErrorActionPreference = 'Stop'

# The latest deployment with outputs: Skillable names its deployment differently from scripts/deploy.ps1.
$outputs = az deployment group list --resource-group $ResourceGroup --query "sort_by([?properties.outputs != null], &properties.timestamp)[-1].properties.outputs" | ConvertFrom-Json
if (-not $outputs) { throw "No lab deployment found in resource group $ResourceGroup." }

# ARM returns output names with changed casing (apiM_GATEWAY_URL), so restore them.
$lines = @("AZURE_RESOURCE_GROUP=$ResourceGroup") + @(
    $outputs.PSObject.Properties |
        Where-Object { $_.Name -ine 'AZURE_RESOURCE_GROUP' } |
        ForEach-Object { "$($_.Name.ToUpper())=$($_.Value.value)" }
)
$envFile = Join-Path $PSScriptRoot '../.env'
Set-Content -Path $envFile -Value $lines -Encoding utf8
$lines | Where-Object { $_ -notmatch '^APPLICATIONINSIGHTS' } | ForEach-Object { Write-Host $_ }
Write-Host "Wrote $(Resolve-Path $envFile)"
