$ErrorActionPreference = 'Stop'
$root = Split-Path $PSScriptRoot -Parent
$global:roleCreated = $false
$global:roleCalls = 0
function global:azd {
    $global:LASTEXITCODE = 0
    switch ($args[2]) {
        'AGENT_EVIDENCE_AGENT_INSTANCE_IDENTITY_PRINCIPAL_ID' { return '00000000-0000-0000-0000-000000000002' }
        'AZURE_AI_PROJECT_ID' { return '/subscriptions/00000000-0000-0000-0000-000000000001/resourceGroups/fixture/providers/Microsoft.CognitiveServices/accounts/foundry-fixture/projects/fincrime' }
        'AZURE_SEARCH_SERVICE_NAME' { return 'search-fixture' }
    }
    throw 'Unexpected environment lookup.'
}
function global:az {
    $global:LASTEXITCODE = 0
    $scope = '/subscriptions/00000000-0000-0000-0000-000000000001/resourceGroups/fixture/providers/Microsoft.Search/searchServices/search-fixture'
    if ($args -notcontains $scope) { throw 'Role must be scoped only to the lab Search service.' }
    if ($args -contains '--all') { throw 'Azure CLI does not accept --all with an explicit scope.' }
    if (($args[0..2] -join ' ') -eq 'role assignment list') {
        if ($global:roleCreated) { return '["existing-role"]' }
        return '[]'
    }
    if (($args[0..2] -join ' ') -eq 'role assignment create') {
        foreach ($required in @('--assignee-object-id', '00000000-0000-0000-0000-000000000002',
            '--assignee-principal-type', 'ServicePrincipal', '1407120a-92aa-4202-b7e9-c0e197c71c8f')) {
            if ($args -notcontains $required) { throw "Role assignment is missing $required." }
        }
        $global:roleCreated = $true
        $global:roleCalls++
        return
    }
    throw 'Unexpected Azure command.'
}
& (Join-Path $root 'scripts/grant_evidence_access.ps1') -EnvironmentName fixture
& (Join-Path $root 'scripts/grant_evidence_access.ps1') -EnvironmentName fixture
if ($global:roleCalls -ne 1) { throw 'Rerunning must not create another role assignment.' }
'PASS: the deployed agent identity gets only Search Index Data Reader at Search scope; reruns preserve the assignment.'
