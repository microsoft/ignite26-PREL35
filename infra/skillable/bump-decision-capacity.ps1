# Raises the decision-1 deployment capacity (requests per minute) so full lab runs are not throttled.
# Use a blocking Skillable Cloud Platform / Azure / PowerShell post-build action after ARM provisioning.
# Skillable supplies the Azure context and replaces the resource-group token. No VM files are needed.
param(
    [string]$ResourceGroup = '@lab.CloudResourceGroup(PREL35).Name',
    [string]$FoundryName = '',
    [int]$Capacity = 30,
    [string]$DeploymentName = 'decision-1'
)
$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'

function Invoke-ArmRequest([string]$Method, [string]$Path, [object]$Body) {
    $parameters = @{ Method = $Method; Path = $Path; ErrorAction = 'Stop' }
    if ($null -ne $Body) { $parameters.Payload = ($Body | ConvertTo-Json -Depth 20 -Compress) }
    for ($attempt = 0; $attempt -lt 6; $attempt++) {
        $response = Invoke-AzRestMethod @parameters
        if ($response.StatusCode -in 429, 500, 502, 503, 504 -and $attempt -lt 5) {
            Start-Sleep -Seconds ([Math]::Min([Math]::Pow(2, $attempt + 1), 30))
            continue
        }
        return $response
    }
}

function Assert-Success($Response, [string]$Action) {
    if ($Response.StatusCode -lt 200 -or $Response.StatusCode -ge 300) {
        $summary = ("$Action failed (HTTP $($Response.StatusCode)). $($Response.Content)" -replace '\s+', ' ')
        if ($summary.Length -gt 1500) { $summary = $summary.Substring(0, 1500) + ' [truncated]' }
        throw $summary
    }
}

if ($PSVersionTable.PSVersion.Major -lt 7) { throw 'Select PowerShell, not Legacy PowerShell, for this cloud action.' }
$subscription = "$((Get-AzContext).Subscription.Id)"
if (-not $subscription) { throw 'No Skillable Azure context. Select Execute Script in Cloud Platform / Azure / PowerShell.' }
if (-not $ResourceGroup -or $ResourceGroup.Contains('@lab.')) { throw 'The resource-group token was not replaced. Configure the PREL35 cloud resource group or supply -ResourceGroup.' }

$group = "/subscriptions/$subscription/resourceGroups/$ResourceGroup"
if (-not $FoundryName) {
    $accounts = Invoke-ArmRequest GET "$group/providers/Microsoft.CognitiveServices/accounts?api-version=2025-06-01"
    Assert-Success $accounts 'Listing Foundry accounts'
    $names = @(($accounts.Content | ConvertFrom-Json).value | ForEach-Object { $_.name })
    if ($names.Count -ne 1) { throw "Expected one Foundry account in '$ResourceGroup'; found $($names.Count). Supply -FoundryName." }
    $FoundryName = $names[0]
}
$path = "$group/providers/Microsoft.CognitiveServices/accounts/$FoundryName/deployments/${DeploymentName}?api-version=2025-06-01"

$existing = Invoke-ArmRequest GET $path
if ($existing.StatusCode -eq 404) { return $true }
Assert-Success $existing "Reading $DeploymentName"
$deployment = $existing.Content | ConvertFrom-Json -AsHashtable
if ([int]$deployment.sku.capacity -ge $Capacity) { return $true }

# The rate limit only refreshes on a new deployment, so recreate it instead of patching in place.
Assert-Success (Invoke-ArmRequest DELETE $path) "Deleting $DeploymentName"
for ($wait = 0; $wait -lt 40; $wait++) {
    if ((Invoke-ArmRequest GET $path).StatusCode -eq 404) { break }
    Start-Sleep -Seconds 5
}
Assert-Success (Invoke-ArmRequest PUT $path @{
    sku = @{ name = $deployment.sku.name; capacity = $Capacity }
    properties = @{ model = $deployment.properties.model }
}) "Recreating $DeploymentName"
for ($wait = 0; $wait -lt 40; $wait++) {
    $state = (Invoke-ArmRequest GET $path).Content | ConvertFrom-Json -AsHashtable
    if ($state.properties.provisioningState -eq 'Succeeded') { return $true }
    if ($state.properties.provisioningState -in 'Failed', 'Canceled') { throw "$DeploymentName provisioning $($state.properties.provisioningState)." }
    Start-Sleep -Seconds 5
}
throw "$DeploymentName did not finish provisioning in time."
