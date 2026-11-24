<# Run on the Windows VM to download the public repository without GitHub credentials. #>
[CmdletBinding()]
param(
    [string]$TargetPath = 'C:\Users\LabUser\Desktop\ignite26-PREL35',
    [ValidateNotNullOrEmpty()][string]$Ref = 'main'
)
$ErrorActionPreference = 'Stop'
$TargetPath = $ExecutionContext.SessionState.Path.GetUnresolvedProviderPathFromPSPath($TargetPath)
function Assert-LabDestination {
    if (Test-Path -LiteralPath $TargetPath) {
        $destination = Get-Item -LiteralPath $TargetPath -Force
        if (-not $destination.PSIsContainer -or
            ($destination.Attributes -band [IO.FileAttributes]::ReparsePoint) -or
            @(Get-ChildItem -LiteralPath $TargetPath -Force).Count -ne 0) {
            throw "The destination already exists but is not an empty, unlinked folder: $TargetPath. Existing files were not changed."
        }
    }
}
Assert-LabDestination
$parent = Split-Path $TargetPath -Parent
[IO.Directory]::CreateDirectory($parent) | Out-Null
$temporary = Join-Path $parent ("ignite26-PREL35-download-" + [guid]::NewGuid().ToString('N'))
[IO.Directory]::CreateDirectory($temporary) | Out-Null

try {
    $zip = Join-Path $temporary 'repo.zip'
    $headers = @{
        Accept = 'application/vnd.github+json'
        'X-GitHub-Api-Version' = '2022-11-28'
    }
    $zipUrl = 'https://api.github.com/repos/microsoft/ignite26-PREL35/zipball/' + [uri]::EscapeDataString($Ref)
    Write-Host "Downloading microsoft/ignite26-PREL35 ($Ref)."
    try {
        Invoke-WebRequest -Uri $zipUrl -Headers $headers -OutFile $zip -UseBasicParsing
    } catch {
        throw 'GitHub download failed. Check connectivity, the ref, repository visibility, and GitHub API rate limits.'
    }

    $extracted = Join-Path $temporary 'extracted'
    Expand-Archive -LiteralPath $zip -DestinationPath $extracted
    $entries = @(Get-ChildItem -LiteralPath $extracted -Force)
    if ($entries.Count -ne 1 -or -not $entries[0].PSIsContainer) {
        throw 'Expected one repository folder in the GitHub source ZIP.'
    }
    $source = $entries[0].FullName
    foreach ($required in @('README.md', 'pyproject.toml', 'azure.yaml', 'src/decide.py', 'instructions/skillable/content.md')) {
        if (-not (Test-Path -LiteralPath (Join-Path $source $required) -PathType Leaf)) {
            throw "The source ZIP is missing $required."
        }
    }
    foreach ($forbidden in @('.git', '.env', '.azure', '.venv', 'evidence', 'tests/e2e/artifacts')) {
        if (Test-Path -LiteralPath (Join-Path $source $forbidden)) {
            throw "The source ZIP contains unexpected local state: $forbidden."
        }
    }
    Assert-LabDestination
    if (Test-Path -LiteralPath $TargetPath) {
        foreach ($entry in Get-ChildItem -LiteralPath $source -Force) {
            $destination = Join-Path $TargetPath $entry.Name
            if ($entry.PSIsContainer) {
                [IO.Directory]::Move($entry.FullName, $destination)
            } else {
                [IO.File]::Move($entry.FullName, $destination)
            }
        }
    } else {
        [IO.Directory]::Move($source, $TargetPath)
    }
    Write-Host "Lab sources ready at $TargetPath."
    $setupScript = Join-Path $TargetPath 'infra/skillable/lifecycle_setup_vm.ps1'
    try {
        if (-not (Test-Path -LiteralPath $setupScript -PathType Leaf)) {
            throw "VM setup hook not found: $setupScript"
        }
        $global:LASTEXITCODE = 0
        Push-Location -LiteralPath $TargetPath
        try {
            & $setupScript
            if ($LASTEXITCODE -ne 0) {
                throw "VM setup hook returned exit code $LASTEXITCODE."
            }
        } finally {
            Pop-Location
        }
    } catch {
        Write-Warning "Optional VM setup skipped or failed: $($_.Exception.Message)"
    } finally {
        $global:LASTEXITCODE = 0
    }
    Write-Host 'Run uv run setup-lab as LabUser, following the lab instructions, to initialize Git and prepare the lab.'
} finally {
    Remove-Item -LiteralPath $temporary -Recurse -Force
}
