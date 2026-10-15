# Skillable life cycle action: Execute Script in Cloud Platform (PowerShell), Post-Build, blocking.
# The learner is LOD Contributor, which has no Foundry data-plane rights. Foundry User lets them create and run agents.
# The script's identity can't read Entra ID users, so find the learner from their existing LOD Contributor assignment
# and create the assignment through ARM, neither of which needs Microsoft Graph.
# Stop on any error so a failed assignment fails the blocking action instead of passing silently.
$ErrorActionPreference = 'Stop'
$resourceGroup = '@lab.CloudResourceGroup(ResourceGroup1).Name'
$learnerRole = 'LOD Contributor'
$foundryUser = '53ca6127-db72-4b80-b1b0-d745d6d5456d' # By ID: the display name differs between tenants.

$sub = (Get-AzContext).Subscription.Id
$learnerRoleId = (Get-AzRoleDefinition -Name $learnerRole).Id
if (-not $learnerRoleId) { throw "Role '$learnerRole' not found in subscription $sub." }
$assignments = (Invoke-AzRestMethod -Method GET `
    -Path "/subscriptions/$sub/providers/Microsoft.Authorization/roleAssignments?api-version=2022-04-01&`$filter=atScope()").Content |
    ConvertFrom-Json
$learners = $assignments.value.properties |
    Where-Object { $_.principalType -eq 'User' -and $_.roleDefinitionId -like "*/$learnerRoleId" } |
    Select-Object -ExpandProperty principalId -Unique
if (-not $learners) { throw "No user has '$learnerRole' on subscription $sub." }

$scope = (Get-AzResourceGroup -Name $resourceGroup).ResourceId
foreach ($id in $learners) {
    $body = @{ properties = @{
        roleDefinitionId = "/subscriptions/$sub/providers/Microsoft.Authorization/roleDefinitions/$foundryUser"
        principalId = $id; principalType = 'User' } } | ConvertTo-Json
    $r = Invoke-AzRestMethod -Method PUT -Payload $body `
        -Path "$scope/providers/Microsoft.Authorization/roleAssignments/$([guid]::NewGuid())?api-version=2022-04-01"
    # 409: the learner already has the role (the action was rerun).
    if ($r.StatusCode -notin 200, 201, 409) { throw "Assigning Foundry User to $id failed ($($r.StatusCode)): $($r.Content)" }
    Write-Output "Foundry User -> $id on $resourceGroup ($($r.StatusCode))"
}
