$ErrorActionPreference = 'Stop'
$root = Split-Path $PSScriptRoot -Parent
$action = Join-Path $root 'infra/skillable/assign-learner.ps1'
$teardown = Join-Path $root 'infra/skillable/remove-setup-roles.ps1'
$log = Join-Path ([IO.Path]::GetTempPath()) "skillable-test-$([guid]::NewGuid()).log"
$global:subscriptionFixture = '00000000-0000-0000-0000-000000000001'
$global:learnerFixture = '00000000-0000-0000-0000-000000000003'
$global:secondLearnerFixture = '00000000-0000-0000-0000-000000000009'
$global:nonLearnerFixture = '00000000-0000-0000-0000-000000000010'
$global:groupFixture = "/subscriptions/$subscriptionFixture/resourceGroups/fixture"
$global:foundryFixture = "$groupFixture/providers/Microsoft.CognitiveServices/accounts/foundry-fixture"
$global:searchFixture = "$groupFixture/providers/Microsoft.Search/searchServices/search-fixture"
$global:readerRoleFixture = "/subscriptions/$subscriptionFixture/providers/Microsoft.Authorization/roleDefinitions/00000000-0000-0000-0000-000000000005"
$global:contributorRoleFixture = "/subscriptions/$subscriptionFixture/providers/Microsoft.Authorization/roleDefinitions/00000000-0000-0000-0000-000000000008"
$global:tokenFixture = 'fixture-token-not-a-jwt'
$global:state = @{
    roles = @(); roleWrites = 0; roleDeletes = 0; deniedRoles = $false; missingContext = $false; missingIdentity = $false
    learnerRoleNames = @('LOD Reader'); noLearners = $false; mixedLearners = $false
    removedGroup = $false; deniedDeletes = $false; roleLookups = 0
}
function global:Get-AzContext {
    if ($global:state.missingContext) { return $null }
    return @{ Subscription = @{ Id = $global:subscriptionFixture } }
}
function global:Get-AzAccessToken {
    param($ResourceUrl, $ErrorAction)
    if ($ResourceUrl -ne 'https://management.azure.com/') { throw 'The role-only action must not acquire data-plane tokens.' }
    return @{ Token = $global:tokenFixture }
}
function global:Start-Sleep { param($Seconds) }
function global:Invoke-WebRequest {
    param($Uri, $Method, $Headers, $Body, $ContentType, $SkipHttpErrorCheck, $MaximumRedirection, $TimeoutSec, $ErrorAction)
    if ($Headers.Authorization -ne "Bearer $global:tokenFixture" -or $MaximumRedirection -ne 0 -or
        ([uri]$Uri).Host -ne 'management.azure.com') { throw 'Authentication or ARM-only handling changed.' }
    $path = ([uri]$Uri).AbsolutePath
    $json = if ($Body -is [byte[]]) { [Text.Encoding]::UTF8.GetString($Body) | ConvertFrom-Json -AsHashtable } else { $null }
    $status = 200
    $reply = @{}
    if ($path -eq "$global:groupFixture/resources") {
        if ($global:state.removedGroup) {
            return @{ StatusCode = 404; Content = '{"error":{"code":"ResourceGroupNotFound"}}' }
        }
        $reply.value = @(
            @{ name = 'foundry-fixture'; id = $global:foundryFixture; type = 'Microsoft.CognitiveServices/accounts' },
            @{ name = 'search-fixture'; id = $global:searchFixture; type = 'Microsoft.Search/searchServices' },
            @{ name = 'webiq-fixture'; id = "$global:groupFixture/providers/Microsoft.WebIQ/accounts/webiq-fixture"; type = 'Microsoft.WebIQ/accounts' }
        )
    } elseif ($path -eq "$global:foundryFixture/projects/fincrime") {
        $reply.identity = @{ principalId = $(if ($global:state.missingIdentity) { '' } else { '00000000-0000-0000-0000-000000000006' }) }
    } elseif ($path -eq $global:searchFixture) {
        $reply.identity = @{ principalId = '00000000-0000-0000-0000-000000000007' }
    } elseif ($path -like '*/roleDefinitions') {
        $global:state.roleLookups++
        $filter = [System.Web.HttpUtility]::ParseQueryString(([uri]$Uri).Query)['$filter']
        if ($filter -notmatch "^roleName eq '(LOD Reader|LOD Contributor)'$") { throw 'Unexpected learner role lookup.' }
        $roleName = $Matches[1]
        $reply.value = @(if ($roleName -in $global:state.learnerRoleNames) {
            @{ id = $(if ($roleName -eq 'LOD Reader') { $global:readerRoleFixture } else { $global:contributorRoleFixture }) }
        })
    } elseif ($path -like '*/roleAssignments' -and $Method -eq 'GET') {
        $scope = $path -replace '/providers/Microsoft.Authorization/roleAssignments$', ''
        if ($scope -eq "/subscriptions/$global:subscriptionFixture") {
            $reply.value = @(if (-not $global:state.noLearners) {
                foreach ($roleName in $global:state.learnerRoleNames) {
                    @{ properties = @{
                        principalType = 'User'; principalId = $global:learnerFixture
                        roleDefinitionId = $(if ($roleName -eq 'LOD Reader') { $global:readerRoleFixture } else { $global:contributorRoleFixture })
                    } }
                }
                if ($global:state.mixedLearners) {
                    @{ properties = @{ principalType = 'User'; principalId = $global:secondLearnerFixture; roleDefinitionId = $global:contributorRoleFixture } }
                    @{ properties = @{ principalType = 'ServicePrincipal'; principalId = $global:nonLearnerFixture; roleDefinitionId = $global:readerRoleFixture } }
                    @{ properties = @{ principalType = 'User'; principalId = $global:nonLearnerFixture; roleDefinitionId = "$global:readerRoleFixture-unrelated" } }
                }
            })
        } else {
            $reply.value = @($global:state.roles | Where-Object {
                $_.scope -eq $scope -or $scope.StartsWith("$($_.scope)/")
            })
        }
    } elseif ($path -like '*/roleAssignments/*' -and $Method -eq 'PUT') {
        if ($global:state.deniedRoles) { $status = 403; $reply.error = @{ code = 'AuthorizationFailed'; message = 'Cannot assign roles.' } }
        else {
            $global:state.roleWrites++
            $scope = $path -replace '/providers/Microsoft.Authorization/roleAssignments/[^/]+$', ''
            $json.properties.scope = $scope
            $global:state.roles += @{ id = $path; scope = $scope; properties = $json.properties }
        }
    } elseif ($path -like '*/roleAssignments/*' -and $Method -eq 'DELETE') {
        if ($global:state.deniedDeletes) {
            $status = 403; $reply.error = @{ code = 'AuthorizationFailed'; message = 'Cannot remove roles.' }
        } else {
            if (@($global:state.roles | Where-Object { $_.id -eq $path }).Count -ne 1) { throw 'Deleting an unknown assignment.' }
            $global:state.roleDeletes++
            $global:state.roles = @($global:state.roles | Where-Object { $_.id -ne $path })
            $status = 204
        }
    } else { throw "The role-only action made an unexpected request: $Method $Uri" }
    return @{ StatusCode = $status; Content = ($reply | ConvertTo-Json -Depth 20 -Compress) }
}

try {
    $result = & $action -ResourceGroup fixture -LogPath $log 6>$null
    if ($result -ne $true -or $state.roleWrites -ne 7) { throw 'Setup must assign five scoped learner roles and two managed-identity roles only.' }
    foreach ($expected in @(
        @{ scope = $foundryFixture; role = '53ca6127-db72-4b80-b1b0-d745d6d5456d' },
        @{ scope = $searchFixture; role = '7ca78c08-252a-4471-8644-bb5ff32d4ba0' },
        @{ scope = $searchFixture; role = '8ebe5a00-799e-43f5-93ac-243d3dce84a7' }
    )) {
        if (@($state.roles | Where-Object {
            $_.scope -eq $expected.scope -and $_.properties.principalId -eq $learnerFixture -and
            $_.properties.roleDefinitionId -like "*/$($expected.role)"
        }).Count -ne 1) { throw 'The LOD Reader learner must be able to create and seed the index and use Foundry.' }
    }
    $writes = $state.roleWrites
    $state.roles[0].properties.Remove('description')
    & $action -ResourceGroup fixture -LogPath $log 6>$null | Out-Null
    if ($state.roleWrites -ne $writes -or $state.roles[0].properties.description) {
        throw 'Reruns must not duplicate assignments or take ownership of pre-existing permissions.'
    }
    foreach ($names in @(@('LOD Contributor'), @('LOD Reader', 'LOD Contributor'))) {
        $state.learnerRoleNames = $names
        $state.mixedLearners = $names.Count -eq 2
        & $action -ResourceGroup fixture -LogPath $log 6>$null | Out-Null
    }
    if (@($state.roles | Where-Object { $_.properties.principalId -eq $secondLearnerFixture }).Count -ne 5 -or
        @($state.roles | Where-Object { $_.properties.principalId -eq $nonLearnerFixture }).Count) {
        throw 'Mixed discovery must deduplicate learners and exclude other principals.'
    }
    $state.learnerRoleNames = @()
    & $action -ResourceGroup fixture -LearnerPrincipalId $learnerFixture -LogPath $log 6>$null | Out-Null
    $ownedCount = @($state.roles | Where-Object { $_.properties.description -eq 'PREL35 Skillable lifecycle' }).Count
    $state.noLearners = $true
    $lookups = $state.roleLookups
    $state.deniedDeletes = $true
    $failed = $false
    try { & $teardown -ResourceGroup fixture -LogPath $log 6>$null | Out-Null }
    catch {
        if ($_.Exception.Message -notlike 'Skillable teardown failed during *') { throw }
        $failed = $true
    }
    if (-not $failed -or $state.roleDeletes) { throw 'Teardown must report role deletion failures.' }
    $state.deniedDeletes = $false
    $result = & $teardown -ResourceGroup fixture -LogPath $log 6>$null
    if ($result -ne $true -or $state.roleDeletes -ne $ownedCount -or $state.roles.Count -ne 1 -or
        $state.roles[0].properties.description -or $state.roleLookups -ne $lookups) {
        throw 'Teardown must remove only owned grants, even after the LOD role or learner assignment disappears.'
    }
    & $teardown -ResourceGroup fixture -LogPath $log 6>$null | Out-Null
    $state.removedGroup = $true
    & $teardown -ResourceGroup fixture -LogPath $log 6>$null | Out-Null
    if ($state.roleDeletes -ne $ownedCount -or $state.roles.Count -ne 1) {
        throw 'Repeated teardown and an already-deleted resource group must be harmless.'
    }
    $state.removedGroup = $false
    foreach ($scenario in @('role-denied', 'missing-context', 'no-learners', 'no-lod-roles', 'missing-identity')) {
        $state.learnerRoleNames = if ($scenario -eq 'no-lod-roles') { @() } else { @('LOD Reader') }
        $state.noLearners = $scenario -eq 'no-learners'
        $state.deniedRoles = $scenario -eq 'role-denied'
        $state.missingContext = $scenario -eq 'missing-context'
        $state.missingIdentity = $scenario -eq 'missing-identity'
        if ($state.deniedRoles) { $state.roles = @() }
        $failed = $false
        try { & $action -ResourceGroup fixture -LogPath $log 6>$null | Out-Null }
        catch {
            if ($_.Exception.Message -notlike 'Skillable setup failed during *') { throw }
            $failed = $true
        }
        if (-not $failed) { throw "Setup must stop on $scenario." }
    }
    $text = [IO.File]::ReadAllText($log)
    if ($text.Contains($tokenFixture) -or -not $text.Contains('HTTP 403') -or -not $text.Contains('Search Index Data Contributor')) {
        throw 'Role logs must explain permissions and errors without exposing tokens.'
    }
    'PASS: LOD Reader/Contributor setup, scoped index access, owned-grant teardown, preserved existing permissions, repeat safety and logged failures.'
} finally {
    Remove-Item -LiteralPath $log -ErrorAction SilentlyContinue
}
