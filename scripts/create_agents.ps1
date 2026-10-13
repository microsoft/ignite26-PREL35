# Creates or updates the Foundry prompt agents from scripts/agents/<name>.md.
# Run by scripts/deploy.ps1 after the Bicep deployment. Each run adds a new version of each agent.
# -ModelDeployment publishes on another existing deployment instead of MODEL_DEPLOYMENT from .env.
param(
    [string]$AgentFile,
    [string]$ModelDeployment,
    [switch]$PassThru
)
$ErrorActionPreference = 'Stop'
$root = Join-Path $PSScriptRoot '..'

if ($AgentFile) {
    $agentDefinition = Get-Item -LiteralPath $AgentFile
    if ($agentDefinition.PSIsContainer -or $agentDefinition.Extension -ne '.md') {
        throw 'AgentFile must be a Markdown file containing agent instructions.'
    }
    $agentDefinitions = @($agentDefinition)
} else {
    $agentDefinitions = @(Get-ChildItem (Join-Path $PSScriptRoot 'agents') -Filter '*.md' -File | Sort-Object Name)
}
if ($agentDefinitions.Count -eq 0) {
    throw 'No agent definitions found.'
}

$environment = @{}
Get-Content (Join-Path $root '.env') -Encoding utf8 | ForEach-Object {
    $separatorIndex = $_.IndexOf('=')
    if ($separatorIndex -ge 0) {
        $environment[$_.Substring(0, $separatorIndex)] = $_.Substring($separatorIndex + 1)
    }
}

$required = @('FOUNDRY_PROJECT_ENDPOINT')
if (-not $PSBoundParameters.ContainsKey('ModelDeployment')) {
    $required += 'MODEL_DEPLOYMENT'
}
foreach ($name in $required) {
    if (-not $environment.ContainsKey($name) -or [string]::IsNullOrWhiteSpace($environment[$name])) {
        throw "Missing $name in .env. Run scripts/env.ps1 first."
    }
}
if ($PSBoundParameters.ContainsKey('ModelDeployment')) {
    if ([string]::IsNullOrWhiteSpace($ModelDeployment)) {
        throw 'ModelDeployment must name an existing model deployment.'
    }
    $model = $ModelDeployment
} else {
    $model = $environment.MODEL_DEPLOYMENT
}

$accessToken = az account get-access-token --scope 'https://ai.azure.com/.default' --query accessToken -o tsv
if ($LASTEXITCODE -or [string]::IsNullOrWhiteSpace($accessToken)) {
    throw 'Failed to get a Foundry access token. Run az login and try again.'
}

$headers = @{ Authorization = "Bearer $($accessToken.Trim())" }
$projectEndpoint = $environment.FOUNDRY_PROJECT_ENDPOINT.TrimEnd('/')
$uri = "$projectEndpoint/agents?api-version=v1"
$agentDefinitions | ForEach-Object {
    $agentName = $_.BaseName
    $definition = @{
        kind = 'prompt'
        model = $model
        instructions = Get-Content $_.FullName -Raw -Encoding utf8
    }
    $body = @{
        name = $agentName
        definition = $definition
    } | ConvertTo-Json -Depth 4
    $versionUri = "$projectEndpoint/agents/$([uri]::EscapeDataString($agentName))/versions?api-version=v1"
    $versionBody = @{ definition = $definition } | ConvertTo-Json -Depth 4

    for ($attempt = 0; $attempt -lt 10; $attempt++) {
        try {
            try {
                $createdAgent = Invoke-RestMethod -Method Post -Uri $uri -Headers $headers -ContentType 'application/json' -Body $body
                $agent = $createdAgent.versions.latest
            } catch {
                $statusCode = if ($_.Exception.Response) { [int]$_.Exception.Response.StatusCode } else { $null }
                if ($statusCode -ne 409) {
                    throw
                }
                $agent = Invoke-RestMethod -Method Post -Uri $versionUri -Headers $headers -ContentType 'application/json' -Body $versionBody
            }
            if ($null -eq $agent -or $agent.name -ne $agentName -or
                [string]::IsNullOrWhiteSpace([string]$agent.version)) {
                throw "Foundry did not return a valid published version for agent $agentName."
            }
            break
        } catch {
            $statusCode = if ($_.Exception.Response) { [int]$_.Exception.Response.StatusCode } else { $null }
            if ($statusCode -ne 403 -or $attempt -eq 9) {
                throw
            }

            [Console]::Error.WriteLine("Waiting for role assignment to apply ($statusCode)...")
            Start-Sleep -Seconds 30
        }
    }

    Write-Host "$($agent.name) version $($agent.version)"
    if ($PassThru) {
        Write-Output $agent
    }
}
