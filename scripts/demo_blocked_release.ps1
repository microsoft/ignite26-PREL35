param(
    [Parameter(Mandatory)][ValidateNotNullOrEmpty()][string]$ReviewerVersion,
    # Python is provided on the lab VM; the .NET runner is an optional alternative.
    [ValidateSet('dotnet', 'python')][string]$Runner = 'python'
)
$ErrorActionPreference = 'Stop'
$PSNativeCommandUseErrorActionPreference = $false
$root = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..'))
$publish = Join-Path $PSScriptRoot 'create_agents.ps1'
$triageFile = Join-Path $PSScriptRoot 'agents\triage.md'
$regressionFile = Join-Path $PSScriptRoot 'agents\examples\triage-regression.md'
$runDirectory = Join-Path $root ".artifacts\lab1\blocked-release-$([guid]::NewGuid().ToString('N'))"
New-Item -ItemType Directory -Path $runDirectory | Out-Null
$backup = Join-Path $runDirectory 'triage-before.md'
Copy-Item -LiteralPath $triageFile -Destination $backup
Write-Host "Exercise evidence and original instructions: $runDirectory"
Write-Host 'Do not run gateway investigations while this exercise changes the latest triage version.'

function Publish-Triage {
    $published = @(& $publish -AgentFile $triageFile -PassThru)
    if ($published.Count -ne 1 -or $published[0].name -ne 'triage' -or
        [string]::IsNullOrWhiteSpace([string]$published[0].version)) {
        throw 'Publishing did not return exactly one triage version.'
    }
    return [string]$published[0].version
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

function Evaluate-Triage([string]$Version, [string]$Stage, [int]$ExpectedExitCode) {
    $output = Join-Path $runDirectory $Stage
    $evaluationExitCode = Invoke-Runner @('--triage-version', $Version, '--reviewer-version', $ReviewerVersion, '--output', $output)
    if ($evaluationExitCode -ne $ExpectedExitCode) {
        throw "$Stage returned exit code $evaluationExitCode; expected $ExpectedExitCode. Inspect its Evidence Pack. An execution error is not a demonstrated quality rejection."
    }
    $pack = Get-Content -LiteralPath (Join-Path $output 'evidence.json') -Raw -Encoding utf8 | ConvertFrom-Json
    if ($pack.exit_code -ne $ExpectedExitCode -or $pack.runner -ne $Runner -or $pack.execution -ne 'live_pinned_foundry' -or
        $pack.mode -ne 'local_and_managed' -or
        $pack.managed_evaluation_status -ne 'completed' -or
        $pack.telemetry_status -ne 'flush_completed' -or
        $pack.agents.triage.version -ne $Version -or
        $pack.agents.reviewer.version -ne $ReviewerVersion) {
        throw "$Stage did not produce a complete managed evaluation of the requested agent versions."
    }
    if ($ExpectedExitCode -eq 0) {
        if ($pack.local_gate -ne 'pass' -or $pack.managed_evaluation.gate -ne 'pass' -or
            $pack.release_gate -ne 'pass' -or $pack.release_ready -ne $true) {
            throw "$Stage did not pass the combined release gate."
        }
    } else {
        $failedTriageChecks = @($pack.items | Where-Object agent -eq 'triage' |
            ForEach-Object { $_.checks } | Where-Object { $_.passed -eq $false })
        if ($pack.local_gate -ne 'fail' -or $pack.release_gate -ne 'fail' -or $pack.release_ready -ne $false -or
            'citations_present' -notin $failedTriageChecks.name -or
            'expected_disposition' -notin $failedTriageChecks.name) {
            throw 'The candidate did not demonstrate the intended missing-citation and wrong-disposition regression.'
        }
    }
    return $pack
}

Write-Host 'Publishing and evaluating the original triage instructions.'
$baselineVersion = Publish-Triage
$baseline = Evaluate-Triage -Version $baselineVersion -Stage 'baseline' -ExpectedExitCode 0
$instructionsChanged = $false
$restoredVersion = $null
try {
    Write-Host 'Publishing the deliberately faulty triage candidate.'
    $instructionsChanged = $true
    Copy-Item -LiteralPath $regressionFile -Destination $triageFile -Force
    $candidateVersion = Publish-Triage
    $candidate = Evaluate-Triage -Version $candidateVersion -Stage 'candidate' -ExpectedExitCode 1
    Write-Host 'Release blocked: the candidate has uncited reasons and incorrect dispositions.'
    Write-Host 'Managed scores are recorded too, but they do not override failing deterministic checks.'
} finally {
    if ($instructionsChanged) {
        Copy-Item -LiteralPath $backup -Destination $triageFile -Force
        Write-Host 'Original triage instructions restored locally. Publishing the restored version.'
        $restoredVersion = Publish-Triage
    }
}

Write-Host 'Evaluating the restored triage version with the same reviewer.'
$restored = Evaluate-Triage -Version $restoredVersion -Stage 'restored' -ExpectedExitCode 0
if ($restored.agents.triage.instructions_sha256 -ne $baseline.agents.triage.instructions_sha256) {
    throw 'Restored deployed instructions do not match the baseline. Inspect both Evidence Packs before continuing.'
}
Write-Host 'Exercise complete: baseline passed, candidate was rejected, and the original instructions passed again.'
Write-Host "Compare the baseline, candidate, and restored Evidence Packs in $runDirectory"
