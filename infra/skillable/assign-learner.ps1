# Role-assignment action only. Learners run scripts/setup_lab.ps1 on the VM to create and seed the index.
# Use a blocking Skillable Cloud Platform / Azure / PowerShell post-build action after ARM provisioning.
# Skillable supplies the Azure context and replaces the resource-group token. No VM files are needed.
param(
    [string]$ResourceGroup = '@lab.CloudResourceGroup(PREL35).Name',
    [string[]]$LearnerPrincipalId = @(),
    [string]$FoundryName = '',
    [string]$SearchName = '',
    [string]$LogPath = ''
)
$ErrorActionPreference = 'Stop'
if ($PSVersionTable.PSVersion.Major -lt 7) { throw 'Select PowerShell, not Legacy PowerShell, for this cloud action.' }
if (-not $LogPath) { $LogPath = Join-Path ([IO.Path]::GetTempPath()) "prel35-roles-$([guid]::NewGuid()).log" }
$LogPath = $ExecutionContext.SessionState.Path.GetUnresolvedProviderPathFromPSPath($LogPath)
[IO.Directory]::CreateDirectory((Split-Path $LogPath -Parent)) | Out-Null
Start-Transcript -LiteralPath $LogPath -Append | Out-Null
$step = 'Starting role assignments'
$assignmentOwner = 'PREL35 Skillable lifecycle'
$roleNames = @{
    '53ca6127-db72-4b80-b1b0-d745d6d5456d' = 'Foundry User'
    '25fbc0a9-bd7c-42a3-aa1a-3b75d497ee68' = 'Cognitive Services Contributor'
    '7ca78c08-252a-4471-8644-bb5ff32d4ba0' = 'Search Service Contributor'
    '8ebe5a00-799e-43f5-93ac-243d3dce84a7' = 'Search Index Data Contributor'
    '1407120a-92aa-4202-b7e9-c0e197c71c8f' = 'Search Index Data Reader'
    '5e0bd9bd-7b93-4f28-af87-19fc36ad61bd' = 'Cognitive Services OpenAI User'
    'b24988ac-6180-42a0-ab88-20f7382dd24c' = 'Contributor'
}

function Write-SetupStep([string]$Message) {
    $script:step = $Message
    Write-Host ("[{0}] {1}" -f (Get-Date -Format 'yyyy-MM-dd HH:mm:ss'), $Message)
}

function Invoke-SetupRequest([string]$Method, [string]$Uri, [object]$Body) {
    $address = [uri]$Uri
    if ($address.Scheme -ne 'https' -or $address.Host -ne 'management.azure.com' -or
        $address.UserInfo -or $address.Port -ne 443) { throw 'Refusing to send Azure credentials to an unexpected endpoint.' }
    for ($attempt = 0; $attempt -lt 10; $attempt++) {
        $token = (Get-AzAccessToken -ResourceUrl 'https://management.azure.com/' -ErrorAction Stop).Token
        if ($token -is [securestring]) { $token = [pscredential]::new('token', $token).GetNetworkCredential().Password }
        if (-not $token) { throw 'Skillable did not provide an Azure management access token.' }
        $parameters = @{
            Uri = $Uri; Method = $Method; Headers = @{ Authorization = "Bearer $token" }
            SkipHttpErrorCheck = $true; MaximumRedirection = 0; TimeoutSec = 90; ErrorAction = 'Stop'
        }
        if ($null -ne $Body) {
            $parameters.Body = [Text.Encoding]::UTF8.GetBytes(($Body | ConvertTo-Json -Depth 20 -Compress))
            $parameters.ContentType = 'application/json; charset=utf-8'
        }
        try { $response = Invoke-WebRequest @parameters }
        catch { throw "$Method $($address.AbsolutePath) did not complete. Check connectivity and rerun." }
        $status = [int]$response.StatusCode
        if ($status -ge 200 -and $status -lt 300) {
            if ($response.Content) { return ($response.Content | ConvertFrom-Json -AsHashtable) }
            return @{}
        }
        if ($status -in 403, 429, 500, 502, 503, 504 -and $attempt -lt 9) {
            Write-Host "Waiting for Azure permissions/availability: $Method $($address.AbsolutePath) (HTTP $status)."
            Start-Sleep -Seconds ([Math]::Min([Math]::Pow(2, $attempt + 1), 30))
            continue
        }
        $detail = 'No structured service error was returned.'
        try {
            $errorBody = $response.Content | ConvertFrom-Json -AsHashtable
            $detail = "$($errorBody.error.code): $($errorBody.error.message)"
        } catch {}
        throw "$Method $($address.AbsolutePath) failed (HTTP $status). $detail"
    }
}

function Get-SetupCollection([string]$Uri) {
    while ($Uri) {
        $page = Invoke-SetupRequest GET $Uri
        foreach ($item in $page.value) { $item }
        $Uri = $page.nextLink
    }
}

function Get-SetupRoleName([string]$Scope, [string]$Principal, [string]$Role) {
    # Match ARM guid(scope, principal, role) so reruns use the same assignment name.
    $namespace = ([guid]'11fb06fb-712d-4ddd-98c7-e71bbd588830').ToByteArray()
    [Array]::Reverse($namespace, 0, 4); [Array]::Reverse($namespace, 4, 2); [Array]::Reverse($namespace, 6, 2)
    $sha = [Security.Cryptography.SHA1]::Create()
    try { $bytes = $sha.ComputeHash($namespace + [Text.Encoding]::UTF8.GetBytes("$Scope-$Principal-$Role"))[0..15] }
    finally { $sha.Dispose() }
    $bytes[6] = ($bytes[6] -band 15) -bor 80
    $bytes[8] = ($bytes[8] -band 63) -bor 128
    [Array]::Reverse($bytes, 0, 4); [Array]::Reverse($bytes, 4, 2); [Array]::Reverse($bytes, 6, 2)
    return [guid]::new([byte[]]$bytes).ToString()
}

function Set-SetupRole([string]$Scope, [string]$Principal, [string]$Role, [string]$PrincipalType) {
    $parsed = [guid]::Empty
    if (-not [guid]::TryParse($Principal, [ref]$parsed) -or $parsed -eq [guid]::Empty) { throw 'A required identity has no valid object ID.' }
    $existing = @(Get-SetupCollection "https://management.azure.com$Scope/providers/Microsoft.Authorization/roleAssignments?api-version=2022-04-01&`$filter=atScope()" | Where-Object {
        $_.properties.principalId -eq $Principal -and $_.properties.roleDefinitionId -like "*/$Role"
    })
    if ($existing.Count) { Write-Host "Already assigned: $($roleNames[$Role]) -> $Principal on $Scope."; return }
    $name = Get-SetupRoleName $Scope $Principal $Role
    Invoke-SetupRequest PUT "https://management.azure.com$Scope/providers/Microsoft.Authorization/roleAssignments/${name}?api-version=2022-04-01" @{
        properties = @{
            roleDefinitionId = "/subscriptions/$script:subscription/providers/Microsoft.Authorization/roleDefinitions/$Role"
            principalId = $Principal; principalType = $PrincipalType
            description = $assignmentOwner
        }
    } | Out-Null
    Write-Host "Assigned: $($roleNames[$Role]) -> $Principal on $Scope."
}

function Select-SetupResource($Resources, [string]$Type, [string]$Name = '') {
    $matches = @($Resources | Where-Object { $_.type -eq $Type -and (-not $Name -or $_.name -eq $Name) })
    if ($matches.Count -ne 1) { throw "Expected one $Type resource in '$ResourceGroup'; found $($matches.Count). Supply the matching resource name if needed." }
    return $matches[0]
}

try {
    Write-SetupStep "Starting Skillable role setup. Log file: $LogPath"
    $script:subscription = "$((Get-AzContext).Subscription.Id)"
    if (-not $script:subscription) { throw 'No Skillable Azure context. Select Execute Script in Cloud Platform / Azure / PowerShell.' }
    if (-not $ResourceGroup -or $ResourceGroup.Contains('@lab.')) { throw 'The resource-group token was not replaced. Configure the PREL35 cloud resource group or supply -ResourceGroup.' }

    Write-SetupStep 'Finding the existing lab resources and managed identities.'
    $groupId = "/subscriptions/$script:subscription/resourceGroups/$ResourceGroup"
    $resources = @(Get-SetupCollection "https://management.azure.com$groupId/resources?api-version=2021-04-01")
    $foundry = Select-SetupResource $resources 'Microsoft.CognitiveServices/accounts' $FoundryName
    $search = Select-SetupResource $resources 'Microsoft.Search/searchServices' $SearchName
    $webIq = Select-SetupResource $resources 'Microsoft.WebIQ/accounts'
    $projectId = "$($foundry.id)/projects/fincrime"
    $project = Invoke-SetupRequest GET "https://management.azure.com${projectId}?api-version=2025-06-01"
    $searchDetails = Invoke-SetupRequest GET "https://management.azure.com$($search.id)?api-version=2025-05-01"

    Write-SetupStep 'Finding the lab users.'
    if (-not $LearnerPrincipalId.Count) {
        $learnerRoleIds = @(foreach ($roleName in @('LOD Reader', 'LOD Contributor')) {
            $roleFilter = [uri]::EscapeDataString("roleName eq '$roleName'")
            $roles = @(Get-SetupCollection "https://management.azure.com/subscriptions/$script:subscription/providers/Microsoft.Authorization/roleDefinitions?api-version=2022-04-01&`$filter=$roleFilter")
            if ($roles.Count -gt 1) { throw "Role '$roleName' was not found uniquely. Supply -LearnerPrincipalId." }
            foreach ($role in $roles) { $role.id }
        })
        if (-not $learnerRoleIds.Count) { throw "Neither 'LOD Reader' nor 'LOD Contributor' was found. Supply -LearnerPrincipalId." }
        $assignments = @(Get-SetupCollection "https://management.azure.com/subscriptions/$script:subscription/providers/Microsoft.Authorization/roleAssignments?api-version=2022-04-01&`$filter=atScope()")
        $LearnerPrincipalId = @($assignments | Where-Object {
            $_.properties.principalType -eq 'User' -and $_.properties.roleDefinitionId -in $learnerRoleIds
        } | ForEach-Object { $_.properties.principalId } | Select-Object -Unique)
    }
    if (-not $LearnerPrincipalId.Count) { throw "No learner user has 'LOD Reader' or 'LOD Contributor' at subscription scope. Supply -LearnerPrincipalId." }

    Write-SetupStep "Granting scoped setup permissions to $($LearnerPrincipalId.Count) lab user(s)."
    foreach ($learner in $LearnerPrincipalId) {
        Set-SetupRole $foundry.id $learner '53ca6127-db72-4b80-b1b0-d745d6d5456d' 'User'
        Set-SetupRole $projectId $learner '25fbc0a9-bd7c-42a3-aa1a-3b75d497ee68' 'User'
        Set-SetupRole $search.id $learner '7ca78c08-252a-4471-8644-bb5ff32d4ba0' 'User'
        Set-SetupRole $search.id $learner '8ebe5a00-799e-43f5-93ac-243d3dce84a7' 'User'
        Set-SetupRole $webIq.id $learner 'b24988ac-6180-42a0-ab88-20f7382dd24c' 'User'
    }
    Write-SetupStep 'Checking the managed-identity permissions used by Search and Toolbox.'
    Set-SetupRole $foundry.id $searchDetails.identity.principalId '5e0bd9bd-7b93-4f28-af87-19fc36ad61bd' 'ServicePrincipal'
    Set-SetupRole $search.id $project.identity.principalId '1407120a-92aa-4202-b7e9-c0e197c71c8f' 'ServicePrincipal'
    Write-SetupStep 'Role assignments completed. No index, documents, knowledge bases or agents were created.'
    Write-Host 'Next, the learner runs: pwsh -File .\scripts\setup_lab.ps1 -ResourceGroup <lab-resource-group>'
    $true
} catch {
    $message = "Skillable setup failed during '$step': $($_.Exception.Message)"
    Write-Host $message
    Write-Host $_.ScriptStackTrace
    throw $message
} finally {
    Stop-Transcript | Out-Null
}
