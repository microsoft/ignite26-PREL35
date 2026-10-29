# Writes the lab environment values (deployment outputs) to .env at the repository root.
# Needs only Reader on the resource group, so learners run it in Lab 0.
param(
    [Parameter(Mandatory)][string]$ResourceGroup,
    [switch]$PassThru
)
$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'console_output.ps1')

# Include outputs from earlier partial deployments when updating an existing lab.
$outputSets = @(az deployment group list --resource-group $ResourceGroup --query "sort_by([?properties.provisioningState=='Succeeded' && properties.outputs != null], &properties.timestamp)[].properties.outputs" | ConvertFrom-Json)
$outputs = $outputSets | Where-Object { $_.FOUNDRY_PROJECT_ENDPOINT -and $_.APIM_GATEWAY_URL } | Select-Object -Last 1
if (-not $outputs) { throw "No lab deployment found in resource group $ResourceGroup." }

$researchOutputs = $outputSets | Where-Object { $_.AZURE_SEARCH_ENDPOINT.value -and $_.WEBIQ_RESOURCE_ID.value } | Select-Object -Last 1
if ($researchOutputs) {
    foreach ($property in $researchOutputs.PSObject.Properties) {
        $outputs | Add-Member -NotePropertyName $property.Name -NotePropertyValue $property.Value -Force
    }
}

if ($PassThru) { return $outputs }

# ARM returns output names with changed casing (apiM_GATEWAY_URL), so restore them.
$lines = @("AZURE_RESOURCE_GROUP=$ResourceGroup") + @(
    $outputs.PSObject.Properties |
        Where-Object { $_.Name -ine 'AZURE_RESOURCE_GROUP' } |
        ForEach-Object { "$($_.Name.ToUpper())=$($_.Value.value)" }
)
$envFile = Join-Path $PSScriptRoot '../.env'
$outputNames = @($lines | ForEach-Object { ($_ -split '=', 2)[0] })
$preserved = @(if (Test-Path $envFile) {
    Get-Content $envFile | Where-Object {
        $_ -notmatch '^\s*(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*=' -or $Matches[1] -notin $outputNames
    }
})
Set-Content -Path $envFile -Value ($preserved + $lines) -Encoding utf8
$visibleValues = @($lines | Where-Object { $_ -notmatch '^APPLICATIONINSIGHTS' } | ForEach-Object {
    $name, $value = $_ -split '=', 2
    [pscustomobject]@{ Setting = $name; Value = $value }
})
$width = [Math]::Max('Setting'.Length, ($visibleValues | ForEach-Object { $_.Setting.Length } | Measure-Object -Maximum).Maximum)
Write-LabMessage ("{0}  Value" -f 'Setting'.PadRight($width))
Write-Host ("{0}  -----" -f ('-' * $width))
foreach ($entry in $visibleValues) {
    Write-Host ("{0}  {1}" -f $entry.Setting.PadRight($width), $entry.Value)
}
Write-LabMessage "Wrote $(Resolve-Path $envFile)" -Color Green
