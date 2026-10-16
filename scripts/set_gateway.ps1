# Sets a gateway switch (an API Management named value) in your environment.
# Usage: .\scripts\set_gateway.ps1 prompt-shields on
#        .\scripts\set_gateway.ps1 caller-identity obo
#        .\scripts\set_gateway.ps1 model-routing on
#        .\scripts\set_gateway.ps1 token-limit 2000
param(
    [Parameter(Mandatory)][ValidateSet('prompt-shields', 'caller-identity', 'model-routing', 'routing-max-score', 'token-limit')][string]$Name,
    [Parameter(Mandatory)][ValidatePattern('^(on|off|gateway|obo|\d+)$')][string]$Value
)
$ErrorActionPreference = 'Stop'

$settings = @{}
Get-Content (Join-Path $PSScriptRoot '../.env') | ForEach-Object { $k, $v = $_ -split '=', 2; $settings[$k] = $v }

az apim nv update --resource-group $settings.AZURE_RESOURCE_GROUP --service-name $settings.APIM_NAME `
    --named-value-id $Name --value $Value --output none
if ($LASTEXITCODE) { throw "Could not set $Name." }
Write-Host "$Name = $Value. The gateway applies it within about a minute."
