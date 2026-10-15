# Skillable life cycle action: Execute Script in Cloud Platform (PowerShell), after the resource template deploys, blocking.
# The learner is Contributor, which has no Foundry data-plane rights. Foundry User lets them create and run agents.
# By ID (Foundry User): the role's display name differs between tenants. A role ID needs -Scope, not -ResourceGroupName.
# Skillable has no replacement token for the learner's object ID, so look it up from the sign-in name.
# Stop on any error so a failed assignment fails the blocking action instead of passing silently.
$ErrorActionPreference = 'Stop'
$resourceGroup = '@lab.CloudResourceGroup(1).Name'
$upn = '@lab.CloudPortalCredential(User1).Username'
$learner = Get-AzADUser -UserPrincipalName $upn
if (-not $learner) { throw "Learner '$upn' not found in Entra ID: check the credential token and that the script's identity can read users." }
New-AzRoleAssignment -ObjectId $learner.Id -RoleDefinitionId '53ca6127-db72-4b80-b1b0-d745d6d5456d' `
    -Scope (Get-AzResourceGroup -Name $resourceGroup).ResourceId | Out-Null
