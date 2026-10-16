# Skillable life cycle action: Execute Script in Cloud Platform (PowerShell), on Tearing Down, blocking.
# A deleted Foundry account stays soft-deleted for 48 hours, holding its name and model quota, so purge it.
$resourceGroup = '@lab.CloudResourceGroup(1).Name'
# The account can't be deleted while it still has projects.
Get-AzResource -ResourceGroupName $resourceGroup -ResourceType 'Microsoft.CognitiveServices/accounts/projects' |
    Remove-AzResource -Force | Out-Null
foreach ($account in Get-AzCognitiveServicesAccount -ResourceGroupName $resourceGroup) {
    Remove-AzCognitiveServicesAccount -ResourceGroupName $resourceGroup -Name $account.AccountName -Force
    Remove-AzCognitiveServicesAccount -ResourceGroupName $resourceGroup -Name $account.AccountName -Location $account.Location -InRemovedState -Force
}

# Lab 2 creates an app registration for On-Behalf-Of (scripts/setup_obo.ps1). Deleting it removes its secret,
# service principal and consent too.
Get-AzADApplication -DisplayName "prel35-agents-api-$resourceGroup" | Remove-AzADApplication
