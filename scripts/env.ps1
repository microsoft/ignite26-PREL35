# Writes the lab environment values (deployment outputs) to .env at the repository root.
# Needs only Reader on the resource group, so learners run it in Lab 0.
param(
    [Parameter(Mandatory)][string]$ResourceGroup
)
$ErrorActionPreference = 'Stop'

$outputs = az deployment group show --resource-group $ResourceGroup --name main --query properties.outputs | ConvertFrom-Json
if (-not $outputs) { throw "No 'main' deployment found in resource group $ResourceGroup." }

# ARM returns output names with changed casing (apiM_GATEWAY_URL), so restore them.
$lines = $outputs.PSObject.Properties | ForEach-Object { "$($_.Name.ToUpper())=$($_.Value.value)" }
$envFile = Join-Path $PSScriptRoot '..' '.env'
Set-Content -Path $envFile -Value $lines -Encoding utf8
$lines | Where-Object { $_ -notmatch '^APPLICATIONINSIGHTS' } | ForEach-Object { Write-Host $_ }
Write-Host "Wrote $(Resolve-Path $envFile)"
