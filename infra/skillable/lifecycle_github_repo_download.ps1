<#
Run on the Windows VM. Create a tightly scoped fine-grained GitHub PAT:
1. Open https://github.com/settings/personal-access-tokens/new
   (Settings > Developer settings > Personal access tokens > Fine-grained tokens).
2. Name it "PREL35 VM source download" and choose a short expiration covering the lab runs.
3. Set Resource owner to microsoft. Under Repository access, choose Only select repositories
   and select ignite26-PREL35 only.
4. Under Repository permissions, grant Contents: Read-only. Metadata: Read-only is automatic.
   Do not grant write, Actions, Workflows, organization, or account permissions.
5. Generate the token. If organization approval is required, wait for approval before using it.
   If microsoft is unavailable as an owner, check organization membership/token policy;
   do not substitute a broadly scoped classic PAT.
6. In the copy pasted into Skillable's VM lifecycle action, replace PASTE_YOUR_PAT_HERE
   in the GitHubToken parameter below with the token. Do not put the real token in this repository.
   When running the file directly, you can instead pass -GitHubToken or supply GITHUB_TOKEN.
   Never commit it, log it, persist it in the VM's user/system environment, or save it in the image.
   Rotate it before expiry or revoke it when no longer needed.
Docs: https://docs.github.com/en/authentication/keeping-your-account-and-data-secure/managing-your-personal-access-tokens
#>
[CmdletBinding()]
param(
    [string]$TargetPath = 'C:\Users\LabUser\Desktop\ignite26-PREL35',
    [ValidateNotNullOrEmpty()][string]$Ref = 'main',
    [string]$GitHubToken = 'PASTE_YOUR_PAT_HERE'
)
$ErrorActionPreference = 'Stop'
if ($GitHubToken -eq 'PASTE_YOUR_PAT_HERE') { $GitHubToken = $env:GITHUB_TOKEN }
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
if ([string]::IsNullOrWhiteSpace($GitHubToken)) {
    throw 'Replace PASTE_YOUR_PAT_HERE in the Skillable action copy, or supply -GitHubToken / GITHUB_TOKEN with read-only Contents access to microsoft/ignite26-PREL35.'
}
$parent = Split-Path $TargetPath -Parent
[IO.Directory]::CreateDirectory($parent) | Out-Null
$temporary = Join-Path $parent ("ignite26-PREL35-download-" + [guid]::NewGuid().ToString('N'))
[IO.Directory]::CreateDirectory($temporary) | Out-Null

try {
    $zip = Join-Path $temporary 'repo.zip'
    $headers = @{
        Authorization = "Bearer $GitHubToken"
        Accept = 'application/vnd.github+json'
        'X-GitHub-Api-Version' = '2022-11-28'
    }
    $zipUrl = 'https://api.github.com/repos/microsoft/ignite26-PREL35/zipball/' + [uri]::EscapeDataString($Ref)
    Write-Host "Downloading microsoft/ignite26-PREL35 ($Ref)."
    try {
        Invoke-WebRequest -Uri $zipUrl -Headers $headers -OutFile $zip -UseBasicParsing
    } catch {
        throw 'GitHub download failed. Check connectivity, the ref, and repository access for the supplied token.'
    } finally {
        $headers.Clear()
        $GitHubToken = $null
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
    Write-Host 'Run uv run setup-lab as LabUser, following the lab instructions, to initialize Git and prepare the lab.'
} finally {
    Remove-Item -LiteralPath $temporary -Recurse -Force
}
