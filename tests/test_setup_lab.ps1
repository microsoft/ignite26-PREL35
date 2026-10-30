$ErrorActionPreference = 'Stop'
$root = Split-Path $PSScriptRoot -Parent
$envFile = [IO.Path]::GetFullPath((Join-Path $root '.env'))
$before = if (Test-Path $envFile) { (Get-FileHash $envFile).Hash } else { $null }
$global:setupRoot = $root
$global:setupEnvFile = $envFile
$global:setupEnvironment = @('WEBIQ_API_KEY="fixture-secret"')
$global:setupAzd = @{}
$global:setupSteps = [System.Collections.Generic.List[string]]::new()
$global:setupFailure = ''
$global:setupSaved = $null
$global:setupGroup = '/subscriptions/00000000-0000-0000-0000-000000000001/resourceGroups/fixture'
$global:setupFoundry = "$setupGroup/providers/Microsoft.CognitiveServices/accounts/foundry-fixture"
function global:Test-Path {
    param([string]$Path)
    if ([IO.Path]::GetFullPath($Path) -eq $global:setupEnvFile) { return $true }
    Microsoft.PowerShell.Management\Test-Path @PSBoundParameters
}
function global:Get-Content {
    param([string]$Path)
    if ([IO.Path]::GetFullPath($Path) -eq $global:setupEnvFile) { return $global:setupEnvironment }
    Microsoft.PowerShell.Management\Get-Content @PSBoundParameters
}
function global:Get-Item {
    param([string]$Path, [switch]$Force)
    if ([IO.Path]::GetFullPath($Path) -eq $global:setupEnvFile) { return @{ Attributes = [IO.FileAttributes]::Normal } }
    Microsoft.PowerShell.Management\Get-Item @PSBoundParameters
}
function global:az {
    $global:LASTEXITCODE = 0
    $command = $args -join ' '
    if ($command -like 'group show *') {
        return (@{ name = 'fixture'; id = $global:setupGroup; location = 'eastus2' } | ConvertTo-Json)
    }
    if ($command -like 'resource list *') {
        return (@(
            @{ name = 'foundry-fixture'; id = $global:setupFoundry; type = 'Microsoft.CognitiveServices/accounts'; kind = 'AIServices' },
            @{ name = 'search-fixture'; type = 'Microsoft.Search/searchServices'; location = 'eastus' },
            @{ name = 'webiq-fixture'; id = "$global:setupGroup/providers/Microsoft.WebIQ/accounts/webiq-fixture"; type = 'Microsoft.WebIQ/accounts' },
            @{ name = 'apim-fixture'; id = "$global:setupGroup/providers/Microsoft.ApiManagement/service/apim-fixture"; type = 'Microsoft.ApiManagement/service' },
            @{ name = 'insights-fixture'; id = "$global:setupGroup/providers/Microsoft.Insights/components/insights-fixture"; type = 'Microsoft.Insights/components' }
        ) | ConvertTo-Json)
    }
    if ($command -like 'resource show *') {
        if ($command -like '*/projects/fincrime*') { return '{}' }
        if ($command -like '*/service/apim-fixture*') { return '{"properties":{"gatewayUrl":"https://gateway-fixture.example"}}' }
        if ($command -like '*/components/insights-fixture*') { return '{"properties":{"ConnectionString":"InstrumentationKey=fixture"}}' }
    }
    if ($command -like 'cognitiveservices account deployment list *') {
        $names = if ($global:setupFailure -eq 'model') { @('gpt-5-mini', 'gpt-5-nano') } else { @('gpt-5-mini', 'gpt-5-nano', 'text-embedding-3-small') }
        return (@($names | ForEach-Object { @{ name = $_ } }) | ConvertTo-Json)
    }
    throw "Learner setup must not deploy infrastructure, assign roles or depend on ARM deployment outputs: $command"
}
function global:azd {
    $global:LASTEXITCODE = 0
    switch ($args[0..1] -join ' ') {
        'env list' { return '[{"Name":"fixture"}]' }
        'env get-value' {
            if ($global:setupAzd.ContainsKey($args[2])) { return $global:setupAzd[$args[2]] }
            $global:LASTEXITCODE = 1
            return
        }
        'env set' { $global:setupAzd[$args[2]] = $args[3]; return }
        'env select' { $global:setupSteps.Add('configure'); return }
    }
    throw 'Learner setup should configure azd, not deploy the hosted agent yet.'
}
function global:uv {
    $global:LASTEXITCODE = 0
    if ($args -contains '-c') {
        $payload = @($input) -join "`n"
        $global:setupSaved = $payload | ConvertFrom-Json
        $global:setupSteps.Add('save-env')
        return
    }
    if (@($args | Where-Object { "$_" -like '*create_agents.py' }).Count) {
        $global:setupSteps.Add('prompts')
        return
    }
    if (@($args | Where-Object { "$_" -like '*create_search_index.py' }).Count) {
        $global:setupSteps.Add('index')
        foreach ($expected in @('--data', (Join-Path $global:setupRoot 'data/investigator/schemes.json'),
            '--endpoint', 'https://search-fixture.search.windows.net', '--knowledge-base', 'fincrime-research')) {
            if ($args -notcontains $expected) { throw "Local index setup must pass $expected." }
        }
        if ($global:setupFailure -eq 'index') { $global:LASTEXITCODE = 1 }
        return
    }
    throw 'Unexpected Python invocation in learner setup.'
}

try {
    $messages = & (Join-Path $root 'scripts/setup_lab.ps1') -ResourceGroup fixture 6>&1
    if (($setupSteps -join ',') -ne 'save-env,configure,prompts,index' -or
        $setupAzd.AZD_AGENT_SKIP_ROLE_ASSIGNMENTS -ne 'true' -or
        $setupAzd.AZURE_AI_PROJECT_ID -ne "$setupFoundry/projects/fincrime" -or
        -not $setupSaved.AZURE_SEARCH_ENDPOINT -or
        ($messages | Out-String) -like '*fixture-secret*') {
        throw 'Learner setup must bind the correct lab, publish prompts, seed from local data and preserve secrets.'
    }
    foreach ($scenario in @('target', 'model', 'index')) {
        $setupSteps.Clear()
        $global:setupFailure = $scenario
        $global:setupEnvironment = if ($scenario -eq 'target') { @('AZURE_RESOURCE_GROUP=other-lab') } else { @() }
        $caught = ''
        try { & (Join-Path $root 'scripts/setup_lab.ps1') -ResourceGroup fixture 6>$null }
        catch { $caught = $_.Exception.Message }
        $expected = switch ($scenario) {
            'target' { '*.env targets a different lab*' }
            'model' { '*model deployment*missing*' }
            'index' { '*Data-plane setup failed*' }
        }
        if (-not $caught -or $caught -notlike $expected -or
            ($scenario -ne 'index' -and $setupSteps.Count)) { throw "Setup did not stop safely on $scenario." }
    }
    $after = if ([IO.File]::Exists($envFile)) { (Get-FileHash $envFile).Hash } else { $null }
    if ($before -ne $after) { throw 'Mocked setup checks changed the real .env.' }
    'PASS: learner setup uses local corpus, configures role-free azd, avoids provisioning/RBAC/deployment-history dependencies and stops on failures.'
} finally {
    Remove-Item Function:\global:Test-Path, Function:\global:Get-Content, Function:\global:Get-Item -ErrorAction SilentlyContinue
}
