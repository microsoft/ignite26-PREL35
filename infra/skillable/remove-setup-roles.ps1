# Removes only role assignments created by assign-learner.ps1. Does not delete resources.
# Use a blocking Skillable Cloud Platform / Azure / PowerShell action on Tearing Down, before resource deletion.
# Skillable supplies the Azure context and replaces the resource-group token. No VM files are needed.
param(
    [string]$ResourceGroup = '@lab.CloudResourceGroup(PREL35).Name',
    [string]$LogPath = ''
)
$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'
$logReady = $false
$step = 'Starting role teardown'
$assignmentOwner = 'PREL35 Skillable lifecycle'
$roleNames = @{
    '53ca6127-db72-4b80-b1b0-d745d6d5456d' = 'Foundry User'
    '25fbc0a9-bd7c-42a3-aa1a-3b75d497ee68' = 'Cognitive Services Contributor'
    '7ca78c08-252a-4471-8644-bb5ff32d4ba0' = 'Search Service Contributor'
    '8ebe5a00-799e-43f5-93ac-243d3dce84a7' = 'Search Index Data Contributor'
    '1407120a-92aa-4202-b7e9-c0e197c71c8f' = 'Search Index Data Reader'
    '5e0bd9bd-7b93-4f28-af87-19fc36ad61bd' = 'Cognitive Services OpenAI User'
    'b24988ac-6180-42a0-ab88-20f7382dd24c' = 'Contributor'
    '312a565d-c81f-4fd8-895a-4e21e48d571c' = 'API Management Service Contributor'
}

function Write-SetupLog([string]$Message) {
    if ($script:logReady) {
        Add-Content -LiteralPath $LogPath -Value ("[{0}] {1}" -f (Get-Date -Format 'yyyy-MM-dd HH:mm:ss'), $Message) -Encoding utf8
    }
}

function Write-SetupStep([string]$Message) {
    $script:step = $Message
    Write-SetupLog $Message
}

function Invoke-SetupRequest([string]$Method, [string]$Uri) {
    $address = [uri]$Uri
    if ($address.Scheme -ne 'https' -or $address.Host -ne 'management.azure.com' -or
        $address.UserInfo -or $address.Port -ne 443) { throw 'Refusing to send Azure credentials to an unexpected endpoint.' }
    for ($attempt = 0; $attempt -lt 10; $attempt++) {
        Write-SetupLog "$Method $($address.AbsolutePath) (attempt $($attempt + 1))."
        $token = (Get-AzAccessToken -ResourceUrl 'https://management.azure.com/' -ErrorAction Stop).Token
        if ($token -is [securestring]) { $token = [pscredential]::new('token', $token).GetNetworkCredential().Password }
        if (-not $token) { throw 'Skillable did not provide an Azure management access token.' }
        $parameters = @{
            Uri = $Uri; Method = $Method; Headers = @{ Authorization = "Bearer $token" }
            SkipHttpErrorCheck = $true; MaximumRedirection = 0; TimeoutSec = 90; ErrorAction = 'Stop'
        }
        try { $response = Invoke-WebRequest @parameters }
        catch {
            $detail = $_.Exception.GetBaseException().Message.Replace($token, '[redacted]')
            throw "$Method $($address.AbsolutePath) did not complete: $detail"
        }
        $status = [int]$response.StatusCode
        if ($status -eq 404) { return @{} }
        if ($status -ge 200 -and $status -lt 300) {
            if ($response.Content) { return ($response.Content | ConvertFrom-Json -AsHashtable) }
            return @{}
        }
        if ($status -in 403, 429, 500, 502, 503, 504 -and $attempt -lt 9) {
            Write-SetupLog "Waiting for Azure permissions/availability: $Method $($address.AbsolutePath) (HTTP $status)."
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

function Remove-SetupRoles([string]$Scope) {
    $assignments = @(Get-SetupCollection "https://management.azure.com$Scope/providers/Microsoft.Authorization/roleAssignments?api-version=2022-04-01&`$filter=atScope()")
    foreach ($assignment in $assignments) {
        $role = ([string]$assignment.properties.roleDefinitionId).Split('/')[-1]
        if ($assignment.properties.description -ne $assignmentOwner -or
            $assignment.properties.scope -ne $Scope -or -not $roleNames.ContainsKey($role)) { continue }
        $name = Get-SetupRoleName $Scope $assignment.properties.principalId $role
        $expectedId = "$Scope/providers/Microsoft.Authorization/roleAssignments/$name"
        if ($assignment.id -ne $expectedId) { throw "Refusing to remove an unexpected role assignment on $Scope." }
        Invoke-SetupRequest DELETE "https://management.azure.com${expectedId}?api-version=2022-04-01" | Out-Null
        Write-SetupLog "Removed: $($roleNames[$role]) -> $($assignment.properties.principalId) on $Scope."
    }
}

try {
    if ($PSVersionTable.PSVersion.Major -lt 7) { throw 'Select PowerShell, not Legacy PowerShell, for this cloud action.' }
    if (-not $LogPath) { $LogPath = Join-Path ([IO.Path]::GetTempPath()) "prel35-roles-$([guid]::NewGuid()).log" }
    $LogPath = $ExecutionContext.SessionState.Path.GetUnresolvedProviderPathFromPSPath($LogPath)
    [IO.Directory]::CreateDirectory((Split-Path $LogPath -Parent)) | Out-Null
    $logReady = $true
    Write-SetupStep "Starting Skillable role teardown. Log file: $LogPath"
    $script:subscription = "$((Get-AzContext).Subscription.Id)"
    if (-not $script:subscription) { throw 'No Skillable Azure context. Select Execute Script in Cloud Platform / Azure / PowerShell.' }
    if (-not $ResourceGroup -or $ResourceGroup.Contains('@lab.')) { throw 'The resource-group token was not replaced. Configure the PREL35 cloud resource group or supply -ResourceGroup.' }

    Write-SetupStep 'Finding the existing lab resources.'
    $groupId = "/subscriptions/$script:subscription/resourceGroups/$ResourceGroup"
    $resources = @(Get-SetupCollection "https://management.azure.com$groupId/resources?api-version=2021-04-01")
    Write-SetupStep 'Removing only role assignments created by the matching Skillable setup action.'
    foreach ($resource in $resources) {
        switch ($resource.type) {
            'Microsoft.CognitiveServices/accounts' {
                Remove-SetupRoles $resource.id
                Remove-SetupRoles "$($resource.id)/projects/fincrime"
            }
            'Microsoft.Search/searchServices' { Remove-SetupRoles $resource.id }
            'Microsoft.WebIQ/accounts' { Remove-SetupRoles $resource.id }
            'Microsoft.ApiManagement/service' { Remove-SetupRoles $resource.id }
        }
    }
    Write-SetupStep 'Role cleanup completed. Pre-existing permissions and Azure resources were left unchanged.'
    $true
} catch {
    $message = "Skillable teardown failed during '$step': $($_.Exception.Message)"
    $stack = $_.ScriptStackTrace
    $summary = $message -replace '\s+', ' '
    if ($summary.Length -gt 1500) { $summary = $summary.Substring(0, 1500) + ' [truncated]' }
    try { Write-SetupLog "$message`n$stack" } catch {}
    throw $summary
}
