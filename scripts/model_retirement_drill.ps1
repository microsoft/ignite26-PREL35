# Lab 1 model retirement drill: evaluates the same agent instructions on the current model and on a
# candidate replacement, with the judge fixed to the current model, then restores the current model.
param(
    [string]$CandidateModel,
    # Python is provided on the lab VM; the .NET runner is an optional alternative.
    [ValidateSet('dotnet', 'python')][string]$Runner = 'python'
)
$ErrorActionPreference = 'Stop'
$PSNativeCommandUseErrorActionPreference = $false
$root = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..'))
$publish = Join-Path $PSScriptRoot 'create_agents.ps1'
$agentNames = @('triage', 'reviewer')

$environment = @{}
Get-Content (Join-Path $root '.env') -Encoding utf8 | ForEach-Object {
    $separatorIndex = $_.IndexOf('=')
    if ($separatorIndex -ge 0) {
        $environment[$_.Substring(0, $separatorIndex)] = $_.Substring($separatorIndex + 1)
    }
}
$currentModel = $environment['MODEL_DEPLOYMENT']
if ([string]::IsNullOrWhiteSpace($currentModel)) {
    throw 'Missing MODEL_DEPLOYMENT in .env. Run scripts/env.ps1 first.'
}
if ([string]::IsNullOrWhiteSpace($CandidateModel)) {
    $CandidateModel = $environment['CANDIDATE_MODEL_DEPLOYMENT']
}
if ([string]::IsNullOrWhiteSpace($CandidateModel)) {
    throw 'Missing CANDIDATE_MODEL_DEPLOYMENT in .env. Run scripts/env.ps1 again; if it is still missing, the lab environment was deployed before the drill existed and needs redeploying.'
}
if ($CandidateModel -eq $currentModel) {
    throw 'The candidate model deployment must differ from the current one.'
}

$runDirectory = Join-Path $root ".artifacts\lab1\model-drill-$([guid]::NewGuid().ToString('N'))"
New-Item -ItemType Directory -Path $runDirectory | Out-Null
Write-Host "Drill evidence: $runDirectory"
Write-Host "Current model: $currentModel. Candidate model: $CandidateModel. Judge (both runs): $currentModel."
Write-Host 'Do not run gateway investigations while the drill changes the latest agent versions.'

function Publish-Agents([string]$Model) {
    $versions = @{}
    foreach ($name in $agentNames) {
        $published = @(& $publish -AgentFile (Join-Path $PSScriptRoot "agents\$name.md") -ModelDeployment $Model -PassThru)
        if ($published.Count -ne 1 -or $published[0].name -ne $name -or
            [string]::IsNullOrWhiteSpace([string]$published[0].version) -or
            $published[0].definition.model -ne $Model) {
            throw "Publishing $name did not return a version that uses $Model."
        }
        $versions[$name] = [string]$published[0].version
    }
    return $versions
}

# Runs the chosen evaluation runner, shows its output, and returns its exit code.
function Invoke-Runner([string[]]$Arguments) {
    if ($Runner -eq 'python') {
        & python (Join-Path $root 'src\lab1\python\evaluate.py') @Arguments | Out-Host
    } else {
        & dotnet run --project (Join-Path $root 'src\lab1\dotnet\Evaluate.csproj') -- @Arguments | Out-Host
    }
    return $LASTEXITCODE
}

function Evaluate-Model([hashtable]$Versions, [string]$Model, [string]$Stage) {
    $output = Join-Path $runDirectory $Stage
    $code = Invoke-Runner @('--triage-version', $Versions.triage, '--reviewer-version', $Versions.reviewer,
        '--judge-model', $currentModel, '--output', $output)
    if ($code -notin 0, 1) {
        throw "The $Stage evaluation could not complete (exit $code). Inspect $output; an execution error is not a model comparison."
    }
    $pack = Get-Content -LiteralPath (Join-Path $output 'evidence.json') -Raw -Encoding utf8 | ConvertFrom-Json
    if ($pack.exit_code -ne $code -or $pack.runner -ne $Runner -or $pack.execution -ne 'live_pinned_foundry' -or
        $pack.mode -ne 'local_and_managed' -or
        $pack.managed_evaluation_status -ne 'completed' -or
        $pack.telemetry_status -ne 'flush_completed' -or
        $pack.managed_evaluation.judge_model_deployment -ne $currentModel -or
        $pack.agents.triage.version -ne $Versions.triage -or
        $pack.agents.reviewer.version -ne $Versions.reviewer -or
        $pack.agents.triage.model_deployment -ne $Model -or
        $pack.agents.reviewer.model_deployment -ne $Model) {
        throw "The $Stage evaluation did not produce a complete managed evaluation of the requested versions on $Model."
    }
    return $pack
}

function Find-PackItem([object]$Pack, [string]$Id) {
    return $Pack.items | Where-Object id -eq $Id | Select-Object -First 1
}

function Get-Disposition([object]$Item) {
    try {
        $value = ($Item.output | ConvertFrom-Json).disposition
        if ([string]::IsNullOrWhiteSpace($value)) { return '(none)' }
        return $value
    } catch {
        return '(invalid JSON)'
    }
}

function Get-FailedChecks([object]$Item) {
    $failed = @($Item.checks | Where-Object { $_.passed -eq $false } | ForEach-Object { $_.name })
    if ($failed.Count -eq 0) { return '-' }
    return $failed -join ', '
}

function Get-LowestScore([object]$Pack, [string]$Id) {
    $result = $Pack.managed_evaluation.items | Where-Object item_id -eq $Id | Select-Object -First 1
    return ($result.metrics | Measure-Object -Property score -Minimum).Minimum
}

function Get-Summary([object]$Pack) {
    $latency = ($Pack.items | Measure-Object -Property duration_ms -Average).Average
    return "local $($Pack.local_gate), managed $($Pack.managed_evaluation.gate), release $($Pack.release_gate); " +
        "$($Pack.summary.passed)/$($Pack.summary.checks) checks; mean agent latency $([math]::Round($latency)) ms"
}

Write-Host "Publishing the current instructions on the current model ($currentModel)."
$currentVersions = Publish-Agents $currentModel
$current = Evaluate-Model $currentVersions $currentModel 'current'
$candidatePublished = $false
try {
    Write-Host "Publishing the same instructions on the candidate model ($CandidateModel)."
    $candidatePublished = $true
    $candidateVersions = Publish-Agents $CandidateModel
    $candidate = Evaluate-Model $candidateVersions $CandidateModel 'candidate'
} finally {
    if ($candidatePublished) {
        Write-Host "Restoring the current model ($currentModel) as the latest agent versions."
        $restoredVersions = Publish-Agents $currentModel
        Write-Host "Restored: triage version $($restoredVersions.triage), reviewer version $($restoredVersions.reviewer). Use these versions from now on."
    }
}
foreach ($name in $agentNames) {
    if ($candidate.agents.$name.instructions_sha256 -ne $current.agents.$name.instructions_sha256) {
        throw "The $name instructions changed during the drill, so the runs don't isolate the model change."
    }
}

$rows = foreach ($item in $current.items) {
    $other = Find-PackItem $candidate $item.id
    $row = [ordered]@{ Item = $item.id }
    if ($item.agent -eq 'triage') {
        $before = Get-Disposition $item
        $after = Get-Disposition $other
        $row.Current = $before
        $row.Candidate = $after
        $row.Changed = if ($before -ne $after) { 'yes' } else { '' }
    } else {
        $row.Current = ''
        $row.Candidate = ''
        $row.Changed = ''
    }
    $row.'Current failures' = Get-FailedChecks $item
    $row.'Candidate failures' = Get-FailedChecks $other
    $row.'Lowest score' = "$(Get-LowestScore $current $item.id) -> $(Get-LowestScore $candidate $item.id)"
    [pscustomobject]$row
}

$report = [Text.StringBuilder]::new()
[void]$report.AppendLine('# Model retirement drill').AppendLine()
[void]$report.AppendLine("- Current ($currentModel): $(Get-Summary $current)")
[void]$report.AppendLine("- Candidate ($CandidateModel): $(Get-Summary $candidate)")
[void]$report.AppendLine("- Judge for both runs: $currentModel").AppendLine()
[void]$report.AppendLine('| Item | Current disposition | Candidate disposition | Changed | Current failures | Candidate failures | Lowest managed score |')
[void]$report.AppendLine('|---|---|---|---|---|---|---|')
foreach ($row in $rows) {
    [void]$report.AppendLine("| $($row.Item) | $($row.Current) | $($row.Candidate) | $($row.Changed) | $($row.'Current failures') | $($row.'Candidate failures') | $($row.'Lowest score') |")
}
[void]$report.AppendLine().AppendLine('Evidence Packs: [current](./current/summary.md), [candidate](./candidate/summary.md).')
[IO.File]::WriteAllText((Join-Path $runDirectory 'comparison.md'), $report.ToString())

Write-Host ''
Write-Host "Current   ($currentModel): $(Get-Summary $current)"
Write-Host "Candidate ($CandidateModel): $(Get-Summary $candidate)"
$rows | Select-Object Item, Current, Candidate, Changed, 'Candidate failures', 'Lowest score' |
    Format-Table -AutoSize | Out-Host
Write-Host 'Failures on the current model are in comparison.md and the current Evidence Pack.'
Write-Host "Comparison saved to $(Join-Path $runDirectory 'comparison.md')"
Write-Host "Candidate release gate: $($candidate.release_gate). The drill doesn't switch models: the latest agent versions use $currentModel again."
