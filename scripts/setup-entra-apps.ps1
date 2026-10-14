# Provisions the Microsoft Entra registrations used by the Lab 0 service client and configures APIM token validation.
[CmdletBinding()]
param(
    [Parameter(Mandatory)][string]$ResourceGroup,
    [string]$ApiAppDisplayName = 'PREL35 Published Agents API',
    [string]$ClientAppDisplayName = 'PREL35 Lab Client',
    [string]$ApimName = '',
    [switch]$RotateClientSecret
)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest
$root = Join-Path $PSScriptRoot '..'
$envFile = Join-Path $root '.env'
$policyFile = Join-Path $root 'infra' 'agents-api-policy.xml'
$appRoleValue = 'Agents.Invoke'

if (-not (Get-Command az -ErrorAction SilentlyContinue)) {
    throw 'Azure CLI was not found. Install Azure CLI, open a new PowerShell session, and run az login.'
}

function Invoke-AzCli {
    param([Parameter(Mandatory)][string[]]$Arguments)

    $output = & az @Arguments 2>&1
    $exitCode = $LASTEXITCODE
    if ($exitCode -ne 0) {
        $command = 'az ' + ($Arguments -join ' ')
        throw "Azure CLI command failed (exit $exitCode): $command`n$($output -join [Environment]::NewLine)"
    }
    return ($output -join [Environment]::NewLine)
}

function Get-SingleApp {
    param([Parameter(Mandatory)][string]$DisplayName)

    $apps = @(Invoke-AzCli -Arguments @(
        'ad', 'app', 'list', '--display-name', $DisplayName, '--output', 'json'
    ) | ConvertFrom-Json)
    if ($apps.Count -gt 1) {
        throw "More than one app registration is named '$DisplayName'. Use a unique display name."
    }
    return $apps | Select-Object -First 1
}

function Ensure-ServicePrincipal {
    param([Parameter(Mandatory)][string]$AppId)

    $servicePrincipals = @(Invoke-AzCli -Arguments @(
        'ad', 'sp', 'list', '--filter', "appId eq '$AppId'", '--output', 'json'
    ) | ConvertFrom-Json)
    if ($servicePrincipals.Count -gt 1) {
        throw "More than one service principal was returned for application ID $AppId."
    }
    if ($servicePrincipals.Count -eq 1) {
        return $servicePrincipals[0]
    }
    return Invoke-AzCli -Arguments @('ad', 'sp', 'create', '--id', $AppId, '--output', 'json') |
        ConvertFrom-Json
}

function Set-ApimNamedValue {
    param(
        [Parameter(Mandatory)][string]$Name,
        [Parameter(Mandatory)][string]$Value
    )

    $existing = @(Invoke-AzCli -Arguments @(
        'apim', 'nv', 'list', '--resource-group', $ResourceGroup, '--service-name', $ApimName,
        '--query', "[?name=='$Name']", '--output', 'json'
    ) | ConvertFrom-Json)
    if ($existing.Count -eq 0) {
        $null = Invoke-AzCli -Arguments @(
            'apim', 'nv', 'create', '--resource-group', $ResourceGroup, '--service-name', $ApimName,
            '--named-value-id', $Name, '--display-name', $Name, '--value', $Value, '--output', 'none'
        )
        return
    }
    $null = Invoke-AzCli -Arguments @(
        'apim', 'nv', 'update', '--resource-group', $ResourceGroup, '--service-name', $ApimName,
        '--named-value-id', $Name, '--value', $Value, '--output', 'none'
    )
}

function Get-EnvironmentValues {
    $values = [ordered]@{}
    if (Test-Path $envFile) {
        foreach ($line in Get-Content $envFile -Encoding utf8) {
            if ($line -match '^([^#][^=]*)=(.*)$') {
                $values[$Matches[1]] = $Matches[2]
            }
        }
    }
    return $values
}

function Set-EnvironmentValues {
    param(
        [Parameter(Mandatory)][System.Collections.IDictionary]$NewValues,
        [string[]]$RemoveKeys = @()
    )

    $values = Get-EnvironmentValues
    foreach ($key in $RemoveKeys) {
        $values.Remove($key)
    }
    foreach ($entry in $NewValues.GetEnumerator()) {
        $values[$entry.Key] = $entry.Value
    }
    $lines = foreach ($entry in $values.GetEnumerator()) {
        "$($entry.Key)=$($entry.Value)"
    }
    Set-Content -Path $envFile -Value $lines -Encoding utf8
}

$account = Invoke-AzCli -Arguments @('account', 'show', '--output', 'json') | ConvertFrom-Json
$tenantId = [string]$account.tenantId
if ([string]::IsNullOrWhiteSpace($tenantId)) {
    throw 'Azure CLI did not return a tenant ID. Run az login and select the intended subscription.'
}

if ([string]::IsNullOrWhiteSpace($ApimName)) {
    $apimNames = @((Invoke-AzCli -Arguments @(
        'apim', 'list', '--resource-group', $ResourceGroup, '--query', '[].name', '--output', 'tsv'
    ) -split '\r?\n') | Where-Object { -not [string]::IsNullOrWhiteSpace($_) })
    if ($apimNames.Count -ne 1) {
        throw "Expected one API Management service in resource group '$ResourceGroup', found $($apimNames.Count). Pass -ApimName explicitly."
    }
    $ApimName = $apimNames[0]
}

$apiApp = Get-SingleApp -DisplayName $ApiAppDisplayName
if (-not $apiApp) {
    $apiApp = Invoke-AzCli -Arguments @(
        'ad', 'app', 'create', '--display-name', $ApiAppDisplayName,
        '--sign-in-audience', 'AzureADMyOrg', '--output', 'json'
    ) | ConvertFrom-Json
}
$apiApp = Invoke-AzCli -Arguments @('ad', 'app', 'show', '--id', $apiApp.id, '--output', 'json') |
    ConvertFrom-Json
$apiAudience = "api://$($apiApp.appId)"
$appRoles = @($apiApp.appRoles)
$matchingRoles = @($appRoles | Where-Object { $_.value -eq $appRoleValue })
if ($matchingRoles.Count -gt 1) {
    throw "The API registration has more than one '$appRoleValue' app role. Remove the duplicate roles and rerun."
}
$appRole = $matchingRoles | Select-Object -First 1
if (-not $appRole) {
    $appRole = [ordered]@{
        allowedMemberTypes = @('Application')
        description = 'Allows the Lab 0 service client to invoke published agents through API Management.'
        displayName = 'Invoke published agents'
        id = [guid]::NewGuid().ToString()
        isEnabled = $true
        value = $appRoleValue
    }
    $appRoles += $appRole
}
elseif ('Application' -notin @($appRole.allowedMemberTypes)) {
    throw "The existing '$appRoleValue' app role does not allow Application members. Correct it or choose a different API registration."
}

# Disable the delegated scope from the previous Lab 0 implementation before removing its grants and definition.
$oauth2PermissionScopes = @($apiApp.api.oauth2PermissionScopes)
$obsoleteScopes = @($oauth2PermissionScopes | Where-Object { $_.value -eq 'access_as_user' })
foreach ($scope in $obsoleteScopes) {
    $scope.isEnabled = $false
}
$apiPatch = @{
    identifierUris = @($apiAudience)
    appRoles = @($appRoles)
    api = @{ oauth2PermissionScopes = @($oauth2PermissionScopes) }
} | ConvertTo-Json -Depth 20 -Compress
$null = Invoke-AzCli -Arguments @(
    'rest', '--method', 'PATCH',
    '--uri', "https://graph.microsoft.com/v1.0/applications/$($apiApp.id)",
    '--headers', 'Content-Type=application/json', '--body', $apiPatch, '--output', 'none'
)
$apiServicePrincipal = Ensure-ServicePrincipal -AppId $apiApp.appId

$clientApp = Get-SingleApp -DisplayName $ClientAppDisplayName
if (-not $clientApp) {
    $clientApp = Invoke-AzCli -Arguments @(
        'ad', 'app', 'create', '--display-name', $ClientAppDisplayName,
        '--sign-in-audience', 'AzureADMyOrg', '--output', 'json'
    ) | ConvertFrom-Json
}
$clientApp = Invoke-AzCli -Arguments @('ad', 'app', 'show', '--id', $clientApp.id, '--output', 'json') |
    ConvertFrom-Json

# Configure a confidential service client and replace only its permission entry for this API.
$resourceAccess = @($clientApp.requiredResourceAccess | Where-Object { $_.resourceAppId -ne $apiApp.appId })
$resourceAccess += @{
    resourceAppId = [string]$apiApp.appId
    resourceAccess = @(@{ id = [string]$appRole.id; type = 'Role' })
}
$clientPatch = @{
    isFallbackPublicClient = $false
    publicClient = @{ redirectUris = @() }
    requiredResourceAccess = @($resourceAccess)
} | ConvertTo-Json -Depth 20 -Compress
$null = Invoke-AzCli -Arguments @(
    'rest', '--method', 'PATCH',
    '--uri', "https://graph.microsoft.com/v1.0/applications/$($clientApp.id)",
    '--headers', 'Content-Type=application/json', '--body', $clientPatch, '--output', 'none'
)
$clientApp = Invoke-AzCli -Arguments @('ad', 'app', 'show', '--id', $clientApp.id, '--output', 'json') |
    ConvertFrom-Json
$clientServicePrincipal = Ensure-ServicePrincipal -AppId $clientApp.appId

# Remove the obsolete delegated consent grant, preserving any unrelated scopes in the same grant.
$grantFilter = [uri]::EscapeDataString(
    "clientId eq '$($clientServicePrincipal.id)' and resourceId eq '$($apiServicePrincipal.id)'"
)
$delegatedGrants = @(Invoke-AzCli -Arguments @(
    'rest', '--method', 'GET',
    '--uri', "https://graph.microsoft.com/v1.0/oauth2PermissionGrants?`$filter=$grantFilter",
    '--output', 'json'
) | ConvertFrom-Json | Select-Object -ExpandProperty value)
foreach ($grant in $delegatedGrants) {
    $remainingScopes = @(($grant.scope -split '\s+') | Where-Object {
        -not [string]::IsNullOrWhiteSpace($_) -and $_ -ne 'access_as_user'
    })
    if ($remainingScopes.Count -eq 0) {
        $null = Invoke-AzCli -Arguments @(
            'rest', '--method', 'DELETE',
            '--uri', "https://graph.microsoft.com/v1.0/oauth2PermissionGrants/$($grant.id)",
            '--output', 'none'
        )
    }
    elseif ($remainingScopes.Count -ne @(($grant.scope -split '\s+') | Where-Object {
        -not [string]::IsNullOrWhiteSpace($_)
    }).Count) {
        $grantPatch = @{ scope = $remainingScopes -join ' ' } | ConvertTo-Json -Compress
        $null = Invoke-AzCli -Arguments @(
            'rest', '--method', 'PATCH',
            '--uri', "https://graph.microsoft.com/v1.0/oauth2PermissionGrants/$($grant.id)",
            '--headers', 'Content-Type=application/json', '--body', $grantPatch, '--output', 'none'
        )
    }
}

# Remove the now-disabled delegated scope while preserving any unrelated API scopes.
if ($obsoleteScopes.Count -gt 0) {
    $remainingApiScopes = @($oauth2PermissionScopes | Where-Object { $_.value -ne 'access_as_user' })
    $removeScopePatch = @{
        api = @{ oauth2PermissionScopes = @($remainingApiScopes) }
    } | ConvertTo-Json -Depth 20 -Compress
    $null = Invoke-AzCli -Arguments @(
        'rest', '--method', 'PATCH',
        '--uri', "https://graph.microsoft.com/v1.0/applications/$($apiApp.id)",
        '--headers', 'Content-Type=application/json', '--body', $removeScopePatch, '--output', 'none'
    )
}

# An app-role assignment is the tenant admin consent grant for an application permission.
$assignmentFilter = [uri]::EscapeDataString("resourceId eq '$($apiServicePrincipal.id)'")
$assignments = @(Invoke-AzCli -Arguments @(
    'rest', '--method', 'GET',
    '--uri', "https://graph.microsoft.com/v1.0/servicePrincipals/$($clientServicePrincipal.id)/appRoleAssignments?`$filter=$assignmentFilter",
    '--output', 'json'
) | ConvertFrom-Json | Select-Object -ExpandProperty value)
$hasAssignment = @($assignments | Where-Object {
    $_.appRoleId -eq $appRole.id -and $_.principalId -eq $clientServicePrincipal.id
}).Count -gt 0
if (-not $hasAssignment) {
    $assignmentBody = @{
        principalId = [string]$clientServicePrincipal.id
        resourceId = [string]$apiServicePrincipal.id
        appRoleId = [string]$appRole.id
    } | ConvertTo-Json -Compress
    $null = Invoke-AzCli -Arguments @(
        'rest', '--method', 'POST',
        '--uri', "https://graph.microsoft.com/v1.0/servicePrincipals/$($clientServicePrincipal.id)/appRoleAssignments",
        '--headers', 'Content-Type=application/json', '--body', $assignmentBody, '--output', 'none'
    )
}

$existingEnvironment = Get-EnvironmentValues
$existingSecret = if ($existingEnvironment.Contains('LAB_CLIENT_SECRET')) {
    [string]$existingEnvironment['LAB_CLIENT_SECRET']
} else {
    ''
}
$environmentClientId = if ($existingEnvironment.Contains('LAB_CLIENT_ID')) {
    [string]$existingEnvironment['LAB_CLIENT_ID']
} else {
    ''
}
$hasPasswordCredential = @($clientApp.passwordCredentials).Count -gt 0
$clientSecret = $existingSecret
if (-not [string]::IsNullOrWhiteSpace($existingSecret) -and
    -not [string]::IsNullOrWhiteSpace($environmentClientId) -and
    $environmentClientId -ne $clientApp.appId -and
    $hasPasswordCredential -and
    -not $RotateClientSecret) {
    throw "LAB_CLIENT_SECRET in .env belongs to a different LAB_CLIENT_ID. Rerun with -RotateClientSecret to create a credential for the selected client explicitly."
}
if ($RotateClientSecret -or -not $hasPasswordCredential) {
    $credential = Invoke-AzCli -Arguments @(
        'ad', 'app', 'credential', 'reset', '--id', $clientApp.appId,
        '--display-name', 'Lab 0 client secret', '--years', '1', '--output', 'json'
    ) | ConvertFrom-Json
    $clientSecret = [string]$credential.password
    if ([string]::IsNullOrWhiteSpace($clientSecret)) {
        throw 'Azure CLI created a client credential but did not return its secret value.'
    }
}
elseif ([string]::IsNullOrWhiteSpace($existingSecret)) {
    throw "The Lab client already has a credential, but LAB_CLIENT_SECRET is missing from .env. The secret cannot be recovered. Rerun with -RotateClientSecret to replace it explicitly."
}

Set-ApimNamedValue -Name 'tenant-id' -Value $tenantId
Set-ApimNamedValue -Name 'apim-api-audience' -Value $apiAudience
Set-ApimNamedValue -Name 'apim-api-app-role' -Value $appRoleValue
$policyBody = @{
    properties = @{
        format = 'rawxml'
        value = Get-Content $policyFile -Raw -Encoding utf8
    }
} | ConvertTo-Json -Depth 10 -Compress
$subscriptionId = [uri]::EscapeDataString([string]$account.id)
$resourceGroupSegment = [uri]::EscapeDataString($ResourceGroup)
$apimNameSegment = [uri]::EscapeDataString($ApimName)
$policyUri = "https://management.azure.com/subscriptions/$subscriptionId/resourceGroups/$resourceGroupSegment/providers/Microsoft.ApiManagement/service/$apimNameSegment/apis/agents/policies/policy?api-version=2024-05-01"
$null = Invoke-AzCli -Arguments @(
    'rest', '--method', 'PUT', '--uri', $policyUri, '--headers', 'Content-Type=application/json',
    '--body', $policyBody, '--output', 'none'
)

Set-EnvironmentValues -NewValues ([ordered]@{
    ENTRA_TENANT_ID = $tenantId
    APIM_API_APP_OBJECT_ID = [string]$apiApp.id
    APIM_API_CLIENT_ID = [string]$apiApp.appId
    APIM_API_SERVICE_PRINCIPAL_ID = [string]$apiServicePrincipal.id
    APIM_API_AUDIENCE = $apiAudience
    APIM_API_APP_ROLE_ID = [string]$appRole.id
    APIM_API_APP_ROLE = $appRoleValue
    LAB_CLIENT_APP_OBJECT_ID = [string]$clientApp.id
    LAB_CLIENT_ID = [string]$clientApp.appId
    LAB_CLIENT_SERVICE_PRINCIPAL_ID = [string]$clientServicePrincipal.id
    LAB_CLIENT_SECRET = $clientSecret
}) -RemoveKeys @('APIM_API_SCOPE_ID', 'APIM_API_SCOPE')

Write-Host "Configured API registration: $ApiAppDisplayName ($($apiApp.appId))"
Write-Host "Configured confidential client: $ClientAppDisplayName ($($clientApp.appId))"
Write-Host "Granted application role: $appRoleValue"
Write-Host "Configured API Management: $ApimName"
Write-Host "Wrote registration values and the client secret to $(Resolve-Path $envFile)"
if ($RotateClientSecret) {
    Write-Host 'Rotated the Lab client secret because -RotateClientSecret was specified.'
}
