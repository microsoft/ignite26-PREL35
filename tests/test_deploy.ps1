$ErrorActionPreference = 'Stop'
$root = Split-Path $PSScriptRoot -Parent
$logFile = Join-Path ([System.IO.Path]::GetTempPath()) "prel35-deploy-$([guid]::NewGuid()).log"
$global:mode = 'index'
$global:deployments = 0
$global:agents = 0
$global:indexes = 0
$global:writes = 0
$global:failIndex = $false
$global:operatorAccountType = 'user'
$global:operatorLookup = '00000000-0000-0000-0000-000000000001'
$global:operatorLookupExitCode = 0
$global:accountExitCode = 0
$global:identityLookups = 0
$global:deploymentOperatorId = ''
$global:deploymentOutputReads = 0
$global:emitAzdNotice = $false
$global:failAzdSet = $false
$envFile = Join-Path $root '.env'
$before = if (Test-Path $envFile) { (Get-FileHash $envFile).Hash } else { $null }
$fixtureKeyLine = 'WEBIQ_API_KEY="test-only-webiq-key"'
$global:fixtureEnvPath = $envFile
$global:fixtureEnv = @('# organizer settings', $fixtureKeyLine, 'OBO_SCOPE=keep', 'FOUNDRY_PROJECT_ENDPOINT=stale')
$global:fixtureAzd = @{}
$global:azdWrites = 0

function global:azd {
    $global:LASTEXITCODE = 0
    switch ($args[0..1] -join ' ') {
        'env list' { return '[{"Name":"fixture"}]' }
        'env select' { return }
        'env get-value' {
            if ($global:emitAzdNotice) {
                Write-Error -Message 'Update available: azd fixture notice' -ErrorId NativeCommandError
            }
            if ($global:fixtureAzd.ContainsKey($args[2])) { return $global:fixtureAzd[$args[2]] }
            $global:LASTEXITCODE = 1
            return
        }
        'env set' {
            if ($global:failAzdSet) { $global:LASTEXITCODE = 1; return }
            $global:azdWrites++
            $global:fixtureAzd[$args[2]] = $args[3]
            return
        }
    }
    throw 'Unexpected azd command.'
}

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
    if ($command -like 'group show *') {
        return '{"id":"/subscriptions/00000000-0000-0000-0000-000000000001/resourceGroups/fixture","location":"eastus2"}'
    }
    if ($command -like 'deployment group list *') {
        $global:deploymentOutputReads++
        $baseline = @{
            FOUNDRY_PROJECT_ENDPOINT = @{ value = 'https://foundry.example/api/projects/fincrime' }
            APIM_GATEWAY_URL = @{ value = 'https://gateway.example' }
            FOUNDRY_NAME = @{ value = 'foundry-fixture' }
            FOUNDRY_PROJECT_NAME = @{ value = 'fincrime' }
        }
        $research = @{
            AZURE_SEARCH_ENDPOINT = @{ value = 'https://example.search.windows.net' }
            AZURE_SEARCH_SERVICE_NAME = @{ value = 'example' }
            AZURE_SEARCH_INDEX_NAME = @{ value = 'fincrime-schemes' }
            AZURE_SEARCH_KNOWLEDGE_BASE_NAME = @{ value = 'fincrime-research' }
            WEBIQ_RESOURCE_ID = @{ value = '/fixture/webiq' }
            INVESTIGATOR_EMBEDDING_ENDPOINT = @{ value = 'https://example.openai.azure.com' }
            INVESTIGATOR_EMBEDDING_DEPLOYMENT = @{ value = 'text-embedding-3-small' }
            INVESTIGATOR_PLANNING_DEPLOYMENT = @{ value = 'gpt-5-mini' }
        }
        if ($global:mode -eq 'default') {
            return (ConvertTo-Json -InputObject @($baseline + $research) -Depth 10)
        }
        return (@($baseline, $research) | ConvertTo-Json -Depth 10)
    }
    if ($global:mode -eq 'default') {
        if ($command -eq 'account show') {
            $global:identityLookups++
            $global:LASTEXITCODE = $global:accountExitCode
            return (@{ user = @{ type = $global:operatorAccountType; name = 'fixture-client-id' } } | ConvertTo-Json)
        }
        if ($command -like 'ad signed-in-user show *' -or $command -like 'ad sp show *') {
            if ($global:operatorAccountType -eq 'servicePrincipal' -and
                $command -notlike 'ad sp show --id fixture-client-id *') {
                throw 'Service principal lookup must use the signed-in client ID.'
            }
            if ($global:operatorAccountType -eq 'user' -and $command -notlike 'ad signed-in-user show *') {
                throw 'User lookup must use the signed-in user.'
            }
            $global:identityLookups++
            $global:LASTEXITCODE = $global:operatorLookupExitCode
            return $global:operatorLookup
        }
        if ($command -like 'deployment group create *') {
            if ($command -match '(?i)enableinvestigator') { throw 'Deployment must not require an enable flag.' }
            $global:deploymentOperatorId = @($args | Where-Object { "$_" -like 'operatorPrincipalId=*' }) -join ''
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
        if ($global:failIndex) {
            Write-Warning 'fixture index warning'
            & (Get-Process -Id $PID).Path -NoLogo -NoProfile -Command "Write-Output 'fixture index stdout'; [Console]::Error.WriteLine('fixture index stderr'); exit 1"
            $global:LASTEXITCODE = $LASTEXITCODE
        } else {
            Write-Output 'fixture index output'
        }
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

$savedJsonDefaults = $PSDefaultParameterValues.Clone()
try {
    foreach ($noEnumerate in @($false, $true)) {
        $PSDefaultParameterValues['ConvertFrom-Json:NoEnumerate'] = $noEnumerate
        foreach ($global:mode in @('default', 'index')) {
            $outputs = & (Join-Path $root 'scripts/env.ps1') -ResourceGroup fixture -PassThru
            if ($outputs -is [array] -or $outputs.PSObject.Properties.Name -contains 'Count' -or
                $outputs.FOUNDRY_PROJECT_ENDPOINT.value -ne 'https://foundry.example/api/projects/fincrime' -or
                $outputs.APIM_GATEWAY_URL.value -ne 'https://gateway.example' -or
                $outputs.AZURE_SEARCH_ENDPOINT.value -ne 'https://example.search.windows.net') {
                throw 'Deployment outputs must be one merged output object, not array metadata, for single and split deployments.'
            }
        }
    }
} finally {
    $PSDefaultParameterValues = $savedJsonDefaults
}
$global:mode = 'index'

& (Join-Path $root 'scripts/deploy.ps1') -ResourceGroup fixture -IndexOnly -LogPath $logFile
if ($global:indexes -ne 1 -or $global:deployments -ne 0 -or $global:agents -ne 0 -or $global:writes -ne 0) {
    throw 'Index-only mode performed unexpected work.'
}
$global:mode = 'default'
$savedFixtureEnv = $global:fixtureEnv
$global:fixtureEnv += @('COUNT=', 'LENGTH=', 'LONGLENGTH=', 'RANK=', 'SYNCROOT=', 'ISREADONLY=', 'ISFIXEDSIZE=', 'ISSYNCHRONIZED=', 'COUNT=custom')
$outputReadsBefore = $global:deploymentOutputReads
$deploymentMessages = & (Join-Path $root 'scripts/deploy.ps1') -ResourceGroup fixture -LogPath $logFile 6>&1
$global:fixtureEnv = $savedFixtureEnv
if ($global:indexes -ne 2 -or $global:deployments -ne 1 -or $global:agents -ne 1 -or $global:writes -ne 1) {
    throw 'The default deployment must set up the index and knowledge base too.'
}
if ($global:deploymentOutputReads - $outputReadsBefore -ne 2) {
    throw 'Environment export must pass its outputs to evidence configuration instead of reading them a second time.'
}
if ($global:writtenLines -notcontains $fixtureKeyLine -or $global:writtenLines -notcontains 'OBO_SCOPE=keep' -or
    $global:writtenLines -notcontains '# organizer settings' -or
    ($deploymentMessages | Out-String) -match 'test-only-webiq-key') {
    throw 'Environment refresh must preserve organizer settings without printing the Web IQ key.'
}
if ($global:writtenLines -notcontains 'FOUNDRY_PROJECT_ENDPOINT=https://foundry.example/api/projects/fincrime' -or
    $global:writtenLines -notcontains 'APIM_GATEWAY_URL=https://gateway.example' -or
    $global:writtenLines -notcontains 'AZURE_SEARCH_ENDPOINT=https://example.search.windows.net' -or
    $global:writtenLines -notcontains 'COUNT=custom' -or
    @($global:writtenLines | Where-Object { $_ -match '^(COUNT|LENGTH|LONGLENGTH|RANK|SYNCROOT|ISREADONLY|ISFIXEDSIZE|ISSYNCHRONIZED)=$' }).Count) {
    throw 'Environment export must write deployment values, remove old empty metadata and preserve nonempty custom settings.'
}
$deploymentLog = [System.IO.File]::ReadAllText($logFile)
foreach ($step in @('Starting deployment', 'IndexOnly: skipping', 'Resolving the operator object ID',
    'Deploying infrastructure and role assignments', 'Writing environment settings',
    'Publishing prompt agents', 'Reading and checking deployment outputs', 'Creating the Search index',
    'fixture index output', 'Deployment completed successfully')) {
    if (-not $deploymentLog.Contains($step)) { throw "Deployment log is missing step/output: $step." }
}
if ($deploymentLog.Contains('test-only-webiq-key')) { throw 'The deployment log must not expose the preserved Web IQ key.' }
if ($global:deploymentOperatorId -ne "operatorPrincipalId=$($global:operatorLookup)") {
    throw 'The discovered user object ID must be passed to Bicep for operator role assignments.'
}
$lookupsBefore = $global:identityLookups
$explicitOperator = '00000000-0000-0000-0000-000000000002'
& (Join-Path $root 'scripts/deploy.ps1') -ResourceGroup fixture -OperatorPrincipalId $explicitOperator -LogPath $logFile 6>$null
if ($global:identityLookups -ne $lookupsBefore -or
    $global:deploymentOperatorId -ne "operatorPrincipalId=$explicitOperator") {
    throw 'An explicit operator object ID must skip CLI identity lookups and be passed to Bicep.'
}
$global:operatorAccountType = 'servicePrincipal'
$global:operatorLookup = '00000000-0000-0000-0000-000000000003'
& (Join-Path $root 'scripts/deploy.ps1') -ResourceGroup fixture -LogPath $logFile 6>$null
if ($global:deploymentOperatorId -ne "operatorPrincipalId=$($global:operatorLookup)") {
    throw 'The service principal object ID must be passed to Bicep instead of its client ID.'
}
$global:operatorAccountType = 'user'
foreach ($scenario in @('empty', 'lookup-failed', 'account-failed')) {
    $global:operatorLookup = if ($scenario -eq 'empty') { '' } else { '00000000-0000-0000-0000-000000000001' }
    $global:operatorLookupExitCode = if ($scenario -eq 'lookup-failed') { 1 } else { 0 }
    $global:accountExitCode = if ($scenario -eq 'account-failed') { 1 } else { 0 }
    $deploymentsBefore = $global:deployments
    $indexesBefore = $global:indexes
    $writesBefore = $global:writes
    $failed = $false
    try {
        & (Join-Path $root 'scripts/deploy.ps1') -ResourceGroup fixture -LogPath $logFile 6>$null
    } catch {
        if ($_.Exception.Message -notlike 'Could not *') { throw }
        $failed = $true
    }
    if (-not $failed -or $global:deployments -ne $deploymentsBefore -or
        $global:indexes -ne $indexesBefore -or $global:writes -ne $writesBefore) {
        throw "Operator discovery failure ($scenario) must stop before deployment or setup."
    }
}
$global:operatorLookupExitCode = 0
$global:accountExitCode = 0
$deploymentLog = [System.IO.File]::ReadAllText($logFile)
if (-not $deploymentLog.Contains('ERROR:') -or
    -not $deploymentLog.Contains('Could not find the operator object ID') -or
    -not $deploymentLog.Contains('Could not read the Azure CLI account')) {
    throw 'Early Azure CLI failures must be recorded before the deployment stops.'
}
$savedSubscription = $global:fixtureAzd.AZURE_SUBSCRIPTION_ID
$global:fixtureAzd.AZURE_SUBSCRIPTION_ID = '00000000-0000-0000-0000-000000000099'
$writesBefore = $global:azdWrites
$failed = $false
try {
    & (Join-Path $root 'scripts/configure_evidence_agent.ps1') -ResourceGroup fixture
} catch {
    if ($_.Exception.Message -notlike '*targets a different lab*') { throw }
    $failed = $true
}
if (-not $failed -or $global:azdWrites -ne $writesBefore) {
    throw 'Configuring evidence must not overwrite an environment bound to another subscription.'
}
$global:fixtureAzd.AZURE_SUBSCRIPTION_ID = $savedSubscription
$global:emitAzdNotice = $true
& (Join-Path $root 'scripts/configure_evidence_agent.ps1') -ResourceGroup fixture -ValidateOnly
if ($global:azdWrites -ne $writesBefore) { throw 'Predeploy validation must not rewrite azd settings.' }
$savedModel = $global:fixtureAzd.AZURE_AI_MODEL_DEPLOYMENT_NAME
$global:fixtureAzd.AZURE_AI_MODEL_DEPLOYMENT_NAME = 'outdated-model'
$global:failAzdSet = $true
$failed = $false
try {
    & (Join-Path $root 'scripts/configure_evidence_agent.ps1') -ResourceGroup fixture
} catch {
    if ($_.Exception.Message -ne 'Could not save azd setting AZURE_AI_MODEL_DEPLOYMENT_NAME.') { throw }
    $failed = $true
} finally {
    $global:failAzdSet = $false
    $global:emitAzdNotice = $false
    $global:fixtureAzd.AZURE_AI_MODEL_DEPLOYMENT_NAME = $savedModel
}
if (-not $failed) { throw 'Ignoring an azd update notice must not ignore a failed settings write.' }
if ($global:fixtureAzd.AZD_AGENT_SKIP_ROLE_ASSIGNMENTS -ne 'true') {
    throw 'Learner azd deployment must disable automatic role assignments.'
}
$global:mode = 'index'
$global:failIndex = $true
$failed = $false
try {
    & (Join-Path $root 'scripts/deploy.ps1') -ResourceGroup fixture -IndexOnly -LogPath $logFile
} catch {
    if ($_.Exception.Message -notlike '*knowledge base setup failed*') { throw }
    $failed = $true
}
if (-not $failed) { throw 'Failed data-plane setup must stop deployment.' }
$deploymentLog = [System.IO.File]::ReadAllText($logFile)
foreach ($detail in @('fixture index stdout', 'fixture index stderr', 'fixture index warning',
    'knowledge base setup failed (exit code 1)', 'deploy.ps1', 'Deployment failed. See log file:')) {
    if (-not $deploymentLog.Contains($detail)) { throw "Failure log is missing detail: $detail." }
}

$global:mode = 'default'
$global:failIndex = $false
$global:missingOutput = ''
$global:hookOutputNames = @(Select-String -Path (Join-Path $root 'infra/main.bicep') -Pattern '^output\s+(\w+)\s' |
    ForEach-Object { $_.Matches[0].Groups[1].Value })
function global:azd {
    if ($args[1] -eq 'list') { $global:LASTEXITCODE = 0; return '[{"Name":"fixture"}]' }
    if ($args[1] -eq 'select') { $global:LASTEXITCODE = 0; return }
    if ($args -contains '--environment') {
        $global:LASTEXITCODE = 0
        if ($args[1] -eq 'set') { $global:fixtureAzd[$args[2]] = $args[3]; return }
        return $global:fixtureAzd[$args[2]]
    }
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
$hookLogPath = [regex]::Match(($hookMessages | Out-String), '(?m)^\[[^\]]+\] Log file: (.+)$').Groups[1].Value.Trim()
if (-not $hookLogPath -or
    (Split-Path $hookLogPath -Parent) -ne [System.IO.Path]::GetFullPath((Join-Path $root 'logs')) -or
    -not [System.IO.File]::ReadAllText($hookLogPath).Contains('Deployment completed successfully')) {
    throw 'The default deployment log must be saved under the repository logs directory.'
}
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
Remove-Item -LiteralPath $logFile, $hookLogPath
'PASS: operator discovery, deployment and IndexOnly behavior, persistent step/output/error logs, secret preservation and azd output checks.'
