$ErrorActionPreference = 'Stop'
$root = Split-Path $PSScriptRoot -Parent
$yaml = Get-Content (Join-Path $root 'azure.yaml') -Raw
$section = [regex]::Match($yaml, '(?ms)^  predeploy:\r?\n(?<body>.*?)^  postprovision:').Groups['body'].Value
if ($section -notmatch '(?m)^    interactive: true\r?$') {
    throw 'The predeploy hook must attach the terminal for hidden Web IQ key input.'
}
$body = [regex]::Match($section, '(?m)^    run: (?<body>.+)').Groups['body'].Value
if (-not $body) { throw 'Predeploy hook not found.' }
$hook = [scriptblock]::Create($body)
$fixture = Join-Path ([System.IO.Path]::GetTempPath()) "prel35-predeploy-$([guid]::NewGuid())"
$previousLocation = Get-Location
try {
    New-Item -ItemType Directory -Path (Join-Path $fixture 'scripts') | Out-Null
    Set-Content (Join-Path $fixture 'scripts/configure_evidence_agent.ps1') @'
param([string]$ResourceGroup, [string]$EnvironmentName, [switch]$ValidateOnly)
if (-not $ValidateOnly) { throw 'Predeploy must validate the existing environment.' }
$global:calls.Add('validate')
if ($global:failConfigure) { throw 'fixture target mismatch' }
'@
    Set-Content (Join-Path $fixture 'scripts/setup_webiq.ps1') @'
param([switch]$IfNeeded)
if (-not $IfNeeded) { throw 'Predeploy must preserve configured Web IQ integrations.' }
$global:calls.Add('webiq')
if ($global:failSetup) { throw 'fixture setup failed' }
'@
    $global:fixtureRoot = $fixture
    function global:git { return $global:fixtureRoot }
    foreach ($failure in @('', 'configure', 'setup')) {
        $global:calls = [System.Collections.Generic.List[string]]::new()
        $global:failConfigure = $failure -eq 'configure'
        $global:failSetup = $failure -eq 'setup'
        $caught = ''
        try { & $hook } catch { $caught = $_.Exception.Message }
        $expected = if ($failure -eq 'configure') { 'validate' } else { 'validate,webiq' }
        if (($global:calls -join ',') -ne $expected) { throw 'Predeploy ran prerequisites in the wrong order.' }
        if (($failure -eq '' -and $caught) -or ($failure -ne '' -and $caught -notlike 'fixture *')) {
            throw 'Predeploy must stop if environment validation or Web IQ setup fails.'
        }
    }
    $global:forwarded = @()
    function global:uv { $global:forwarded = @($args); $global:LASTEXITCODE = 0 }
    & (Join-Path $root 'scripts/setup_webiq.ps1') -IfNeeded
    if ($global:forwarded -notcontains '--if-needed') { throw 'PowerShell must forward the conditional-setup flag.' }
} finally {
    Set-Location $previousLocation
    Remove-Item -LiteralPath $fixture -Recurse -Force
}
'PASS: predeploy supports hidden input, validates the target first, ensures Web IQ only if needed, and stops on failures.'
