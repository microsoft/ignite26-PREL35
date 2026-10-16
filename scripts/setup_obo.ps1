# Lab 2: creates the app registration the gateway needs for On-Behalf-Of, and gives the gateway its secret.
# NOT YET TESTED END TO END: it needs a tenant where you can create app registrations and grant consent.
#
# On-Behalf-Of: the gateway swaps the caller's token for one that acts as the caller, so Foundry applies that
# person's own permissions instead of the gateway's.
$ErrorActionPreference = 'Stop'

$settings = @{}
$envFile = Join-Path $PSScriptRoot '../.env'
Get-Content $envFile | ForEach-Object { $k, $v = $_ -split '=', 2; $settings[$k] = $v }
$resourceGroup = $settings.AZURE_RESOURCE_GROUP

# Well-known IDs: the Azure CLI (the client you sign in with) and the resource Foundry accepts tokens for.
$azureCli = '04b07795-8ddb-461a-bbee-02f9e1bf7b46'
$foundryResource = '18a66f5f-dbdf-4c17-9dd7-1634712a9cbe' # Azure Machine Learning Services (https://ml.azure.com)
$foundryScope = '1a7925b5-f871-417a-9b8b-303f9f29fa10'    # user_impersonation

# 1. The app registration that represents the gateway API. The name lets the lab teardown find it.
$name = "prel35-agents-api-$resourceGroup"
$app = az ad app create --display-name $name --sign-in-audience AzureADMyOrg | ConvertFrom-Json
if (-not $app) { throw 'Could not create the app registration.' }
az ad sp create --id $app.appId --output none

# 2. Expose one scope, access_as_user, and let the Azure CLI request it without a consent prompt.
$scopeId = [guid]::NewGuid().ToString()
$api = @{
    oauth2PermissionScopes = @(@{
        id = $scopeId; value = 'access_as_user'; type = 'User'; isEnabled = $true
        adminConsentDisplayName = 'Call the agents as the signed-in user'
        adminConsentDescription = 'Lets the gateway call Foundry agents on behalf of the signed-in user.'
        userConsentDisplayName = 'Call the agents as you'
        userConsentDescription = 'Lets the gateway call Foundry agents on your behalf.'
    })
}
$body = Join-Path ([IO.Path]::GetTempPath()) 'obo-api.json'
@{ identifierUris = @("api://$($app.appId)"); api = $api } | ConvertTo-Json -Depth 5 | Set-Content $body
az rest --method patch --url "https://graph.microsoft.com/v1.0/applications/$($app.id)" --body "@$body" --output none
# A scope must exist before a client can be pre-authorised for it, so this is a second update.
$api.preAuthorizedApplications = @(@{ appId = $azureCli; delegatedPermissionIds = @($scopeId) })
@{ api = $api } | ConvertTo-Json -Depth 5 | Set-Content $body
az rest --method patch --url "https://graph.microsoft.com/v1.0/applications/$($app.id)" --body "@$body" --output none
Remove-Item $body

# 3. Let the gateway call Foundry as the signed-in user, and consent to it.
az ad app permission add --id $app.appId --api $foundryResource --api-permissions "$foundryScope=Scope" --output none
az ad app permission grant --id $app.appId --api $foundryResource --scope user_impersonation --output none
if ($LASTEXITCODE) { throw 'Consent failed. The app registration exists, but an administrator must grant consent.' }

# 4. A short-lived secret, stored only in the gateway.
$secret = az ad app credential reset --id $app.appId --display-name lab --end-date (Get-Date).AddDays(2).ToString('yyyy-MM-dd') --query password -o tsv
az apim nv update -g $resourceGroup --service-name $settings.APIM_NAME --named-value-id obo-client-id --value $app.appId --output none
az apim nv update -g $resourceGroup --service-name $settings.APIM_NAME --named-value-id obo-client-secret --value $secret --secret true --output none

# 5. The app now asks for a token for the gateway API instead of for Foundry.
Add-Content $envFile "OBO_SCOPE=api://$($app.appId)/access_as_user"

Write-Host "Created app registration '$name' ($($app.appId))"
Write-Host "  exposes:     api://$($app.appId)/access_as_user"
Write-Host "  may call:    Foundry, as the signed-in user"
Write-Host "  secret:      stored in the gateway, expires in 2 days"
