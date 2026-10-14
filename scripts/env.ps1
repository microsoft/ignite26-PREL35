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
# Preserve Microsoft Entra values, including the client secret, already written by scripts/setup-entra-apps.ps1.
$envFile = Join-Path $PSScriptRoot '..' '.env'
$values = [ordered]@{}
if (Test-Path $envFile) {
    Get-Content $envFile -Encoding utf8 | ForEach-Object {
        if ($_ -match '^([^#][^=]*)=(.*)$') {
            $values[$Matches[1]] = $Matches[2]
        }
    }
}
# Remove values from the previous delegated/device-code client flow.
$values.Remove('APIM_API_SCOPE_ID')
$values.Remove('APIM_API_SCOPE')
$outputs.PSObject.Properties | ForEach-Object {
    $values[$_.Name.ToUpper()] = $_.Value.value
}
$lines = $values.GetEnumerator() | ForEach-Object { "$($_.Key)=$($_.Value)" }
Set-Content -Path $envFile -Value $lines -Encoding utf8
$lines | Where-Object { $_ -notmatch '(?i)(SECRET|PASSWORD|TOKEN|CONNECTION_STRING)=' } |
    ForEach-Object { Write-Host $_ }
Write-Host "Wrote $(Resolve-Path $envFile)"
