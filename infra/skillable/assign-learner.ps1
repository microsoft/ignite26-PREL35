# Skillable life cycle action: Execute Script in Cloud Platform (PowerShell), after the resource template deploys, blocking.
# The learner is Contributor, which has no Foundry data-plane rights. Foundry User lets them create and run agents.
# By ID (Foundry User): the role's display name differs between tenants.
# Skillable has no replacement token for the learner's object ID, so look it up from the sign-in name.
$resourceGroup = '@lab.CloudResourceGroup(1).Name'
$learner = Get-AzADUser -UserPrincipalName '@lab.CloudPortalCredential(User1).Username'
New-AzRoleAssignment -ObjectId $learner.Id -RoleDefinitionId '53ca6127-db72-4b80-b1b0-d745d6d5456d' -ResourceGroupName $resourceGroup | Out-Null
