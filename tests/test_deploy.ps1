$ErrorActionPreference = 'Stop'
$root = Split-Path $PSScriptRoot -Parent
$global:mode = 'index'
$global:deployments = 0
$global:agents = 0
$global:indexes = 0
$global:writes = 0
$global:failIndex = $false
$envFile = Join-Path $root '.env'
$before = if (Test-Path $envFile) { (Get-FileHash $envFile).Hash } else { $null }
$fixtureKeyLine = 'WEBIQ_API_KEY="test-only-webiq-key"'
$global:fixtureEnvPath = $envFile
$global:fixtureEnv = @('# organizer settings', $fixtureKeyLine, 'OBO_SCOPE=keep', 'FOUNDRY_PROJECT_ENDPOINT=stale')

function global:Get-Content {
    param([string]$Path, [switch]$Raw)
    if ([System.IO.Path]::GetFullPath($Path) -eq $global:fixtureEnvPath) {
        return $global:fixtureEnv
    }
    Microsoft.PowerShell.Management\Get-Content @PSBoundParameters
}

function global:az {
    $global:LASTEXITCODE = 0
    $command = $args -join ' '
    if ($command -like 'deployment group list *') {
        $baseline = @{
            FOUNDRY_PROJECT_ENDPOINT = @{ value = 'https://foundry.example/api/projects/fincrime' }
            APIM_GATEWAY_URL = @{ value = 'https://gateway.example' }
        }
        $research = @{
            AZURE_SEARCH_ENDPOINT = @{ value = 'https://example.search.windows.net' }
            AZURE_SEARCH_INDEX_NAME = @{ value = 'fincrime-schemes' }
            AZURE_SEARCH_KNOWLEDGE_BASE_NAME = @{ value = 'fincrime-research' }
            WEBIQ_RESOURCE_ID = @{ value = '/fixture/webiq' }
            INVESTIGATOR_EMBEDDING_ENDPOINT = @{ value = 'https://example.openai.azure.com' }
            INVESTIGATOR_EMBEDDING_DEPLOYMENT = @{ value = 'text-embedding-3-small' }
            INVESTIGATOR_PLANNING_DEPLOYMENT = @{ value = 'gpt-5-mini' }
        }
        if ($global:mode -eq 'default') {
            return (@($baseline + $research) | ConvertTo-Json -Depth 10)
        }
        return (@($baseline, $research) | ConvertTo-Json -Depth 10)
    }
    if ($global:mode -eq 'default') {
        if ($command -eq 'account show') { return '{"user":{"type":"user"}}' }
        if ($command -like 'ad signed-in-user show *') { return '00000000-0000-0000-0000-000000000001' }
        if ($command -like 'deployment group create *') {
            if ($command -match '(?i)enableinvestigator') { throw 'Deployment must not require an enable flag.' }
            $global:deployments++
            return
        }
    }
    throw "Unexpected Azure command in $global:mode mode."
}

function global:uv {
    $global:LASTEXITCODE = 0
    if (@($args | Where-Object { "$_" -like '*create_search_index.py' }).Count -eq 1) {
        foreach ($value in @('--knowledge-base', 'fincrime-research', '--planning-deployment', 'gpt-5-mini')) {
            if ($args -notcontains $value) { throw "Missing setup argument $value." }
        }
        $global:indexes++
        if ($global:failIndex) { $global:LASTEXITCODE = 1 }
        return
    }
    if ($global:mode -eq 'default' -and @($args | Where-Object { "$_" -like '*create_agents.py' }).Count -eq 1) {
        $global:agents++
        return
    }
    throw "Unexpected uv command in $global:mode mode."
}

function global:Set-Content {
    param([string]$Path, [object]$Value, [string]$Encoding)
    $global:writes++
    $global:writtenLines = @($Value)
}
function global:Resolve-Path {
    param([string]$Path)
    return [System.IO.Path]::GetFullPath($Path)
}

& (Join-Path $root 'scripts/deploy.ps1') -ResourceGroup fixture -IndexOnly
if ($global:indexes -ne 1 -or $global:deployments -ne 0 -or $global:agents -ne 0 -or $global:writes -ne 0) {
    throw 'Index-only mode performed unexpected work.'
}
$global:mode = 'default'
$deploymentMessages = & (Join-Path $root 'scripts/deploy.ps1') -ResourceGroup fixture 6>&1
if ($global:indexes -ne 2 -or $global:deployments -ne 1 -or $global:agents -ne 1 -or $global:writes -ne 1) {
    throw 'The default deployment must set up the index and knowledge base too.'
}
if ($global:writtenLines -notcontains $fixtureKeyLine -or $global:writtenLines -notcontains 'OBO_SCOPE=keep' -or
    $global:writtenLines -notcontains '# organizer settings' -or
    ($deploymentMessages | Out-String) -match 'test-only-webiq-key') {
    throw 'Environment refresh must preserve organizer settings without printing the Web IQ key.'
}
$global:mode = 'index'
$global:failIndex = $true
$failed = $false
try {
    & (Join-Path $root 'scripts/deploy.ps1') -ResourceGroup fixture -IndexOnly
} catch {
    if ($_.Exception.Message -notlike '*knowledge base setup failed*') { throw }
    $failed = $true
}
if (-not $failed) { throw 'Failed data-plane setup must stop deployment.' }

$global:mode = 'default'
$global:failIndex = $false
$global:missingOutput = ''
$global:hookOutputNames = @(Select-String -Path (Join-Path $root 'infra/main.bicep') -Pattern '^output\s+(\w+)\s' |
    ForEach-Object { $_.Matches[0].Groups[1].Value })
function global:azd {
    if (($args[0..1] -join ' ') -ne 'env get-value' -or $args[2] -notin $global:hookOutputNames) {
        throw 'Unexpected azd output lookup.'
    }
    $global:LASTEXITCODE = 0
    if ($args[2] -eq $global:missingOutput) { $global:LASTEXITCODE = 1; return }
    return "fixture-$($args[2])"
}
function global:python {
    $global:LASTEXITCODE = 0
    $global:agents++
}
$env:AZURE_RESOURCE_GROUP = 'fixture'
$yaml = Get-Content (Join-Path $root 'azure.yaml') -Raw
$body = [regex]::Match($yaml, '(?ms)^    run: \|\r?\n(?<body>.*)$').Groups['body'].Value
if (-not $body) { throw 'Postprovision hook not found.' }
$hook = [scriptblock]::Create(($body -replace '(?m)^      ', ''))
$hookMessages = & $hook 6>&1
$actualNames = @($global:writtenLines | Where-Object { $_ -match '^\w+=' } |
    ForEach-Object { ($_ -split '=', 2)[0] })
if (Compare-Object @($global:hookOutputNames + @('WEBIQ_API_KEY', 'OBO_SCOPE')) $actualNames) {
    throw 'The azd hook must write every main.bicep output to .env.'
}
if ($global:writtenLines -notcontains $fixtureKeyLine -or $global:writtenLines -notcontains '# organizer settings' -or
    $global:writtenLines -contains 'FOUNDRY_PROJECT_ENDPOINT=stale' -or
    ($hookMessages | Out-String) -match 'test-only-webiq-key') {
    throw 'The azd hook must preserve the key and comments, replace old outputs, and never print the key.'
}
$global:missingOutput = 'WEBIQ_ACCOUNT_NAME'
$writesBefore = $global:writes
$indexesBefore = $global:indexes
$failed = $false
try {
    & $hook 6>$null
} catch {
    if ($_.Exception.Message -ne 'Could not read azd environment value WEBIQ_ACCOUNT_NAME.') { throw }
    $failed = $true
}
if (-not $failed -or $global:writes -ne $writesBefore -or $global:indexes -ne $indexesBefore) {
    throw 'A missing research output must stop the hook before writing .env or running setup.'
}
$after = if (Test-Path $envFile) { (Get-FileHash $envFile).Hash } else { $null }
if ($before -ne $after) { throw 'The real .env changed.' }
'PASS: standard deployment sets up research; IndexOnly is isolated; the azd hook exports every output and stops on missing values or setup failure.'
