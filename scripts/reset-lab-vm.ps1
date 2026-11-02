#Requires -Version 5.1
<#
.SYNOPSIS
Resets the current Windows user's lab checkout and clears Edge browsing data.
.EXAMPLE
.\scripts\reset-lab-vm.ps1 -WhatIf
.EXAMPLE
.\scripts\reset-lab-vm.ps1
.EXAMPLE
.\scripts\reset-lab-vm.ps1 -BaselineRef <reviewed-commit>
.NOTES
Run as the learner, not as another administrator. Close other terminals, VS Code,
and lab processes first. Sign out of personal Edge sync before running.
Edge profiles, settings, extensions and favorites are preserved. Cookies, cache,
history, site storage, saved sessions, passwords and autofill data are removed.
Profiles without a favorites file receive Azure portal and Microsoft Foundry favorites.
This is local cleanup, not a Windows factory reset or Azure resource teardown.
#>
[CmdletBinding(SupportsShouldProcess = $true, ConfirmImpact = 'High')]
param(
    [string] $BaselineRef
)

$ErrorActionPreference = 'Stop'
if ($env:OS -ne 'Windows_NT') {
    throw 'Run this script on the Windows lab VM as the learner.'
}

function Invoke-Git {
    param([string[]] $Arguments)
    $output = & git -C $script:LabRoot @Arguments 2>&1
    if ($LASTEXITCODE -ne 0) {
        throw "Git failed: $output"
    }
    return $output
}

function Assert-NoReparsePoints {
    param([string] $Path)
    if (-not (Test-Path -LiteralPath $Path)) {
        return
    }
    $item = Get-Item -LiteralPath $Path -Force
    if ($item.Attributes -band [IO.FileAttributes]::ReparsePoint) {
        throw "Refusing to clean a symlink or junction: $Path"
    }
    if ($item.PSIsContainer) {
        foreach ($child in Get-ChildItem -LiteralPath $Path -Force) {
            Assert-NoReparsePoints -Path $child.FullName
        }
    }
}

function Invoke-Logout {
    param([string] $Command, [string[]] $Arguments)
    if (-not (Get-Command $Command -ErrorAction SilentlyContinue)) {
        throw "$Command is missing; sign-out cannot be verified."
    }
    $previousErrorActionPreference = $ErrorActionPreference
    try {
        # Windows PowerShell wraps native stderr as error records, even for this benign response.
        $ErrorActionPreference = 'Continue'
        $PSNativeCommandUseErrorActionPreference = $false
        $output = @(& $Command @Arguments 2>&1)
        $exitCode = $LASTEXITCODE
    }
    finally {
        $ErrorActionPreference = $previousErrorActionPreference
    }
    $message = ($output | ForEach-Object { $_.ToString() }) -join "`n"
    if ($exitCode -ne 0 -and $Command -eq 'az' -and
        $Arguments.Count -eq 1 -and $Arguments[0] -eq 'logout' -and
        $message.Trim() -match '^(?:ERROR:\s*)?There are no active accounts\.$') {
        Write-Host 'Azure CLI is already signed out.'
        return
    }
    $output | ForEach-Object { Write-Host $_ }
    if ($exitCode -ne 0) {
        throw "$Command sign-out failed. Resolve it and rerun before saving the image."
    }
}

$script:LabRoot = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..'))
if (-not (Test-Path -LiteralPath (Join-Path $LabRoot 'azure.yaml') -PathType Leaf)) {
    throw 'The script must be in the scripts directory of the lab checkout.'
}
$gitDirectory = Join-Path $LabRoot '.git'
if (-not (Test-Path -LiteralPath $gitDirectory -PathType Container)) {
    throw 'Expected a standalone lab repository, not a linked worktree.'
}
$topLevel = [IO.Path]::GetFullPath([string](Invoke-Git @('rev-parse', '--show-toplevel')))
if ($topLevel -ne $LabRoot) {
    throw 'The lab directory must be the Git repository root.'
}
if (-not $BaselineRef) {
    $commitCount = [int](Invoke-Git @('rev-list', '--count', 'HEAD'))
    if ($commitCount -ne 1) {
        throw 'This is not a single-commit packaged lab. Supply -BaselineRef with a reviewed baseline commit.'
    }
    $BaselineRef = 'HEAD'
}
$baseline = [string](Invoke-Git @('rev-parse', '--verify', '--end-of-options', "$BaselineRef^{commit}"))
Invoke-Git @('cat-file', '-e', "${baseline}:azure.yaml") | Out-Null

if (-not $env:LOCALAPPDATA) {
    throw 'LOCALAPPDATA is missing; cannot locate the learner Edge profile.'
}
$edgeRoot = Join-Path $env:LOCALAPPDATA 'Microsoft\Edge\User Data'
Assert-NoReparsePoints -Path $gitDirectory
Assert-NoReparsePoints -Path $edgeRoot
$edgeDataPaths = @()
if (Test-Path -LiteralPath $edgeRoot) {
    foreach ($profile in Get-ChildItem -LiteralPath $edgeRoot -Directory -Force) {
        if ($profile.Name -notmatch '^(Default|Profile \d+|Guest Profile|System Profile)$' -and
            -not (Test-Path -LiteralPath (Join-Path $profile.FullName 'Preferences'))) {
            continue
        }
        foreach ($relativePath in @(
            'Network\Cookies', 'Network\Cookies-journal', 'Cookies', 'Cookies-journal',
            'Cache', 'Code Cache', 'GPUCache', 'DawnCache',
            'Service Worker', 'Local Storage', 'Session Storage', 'IndexedDB',
            'WebStorage', 'File System', 'Storage', 'blob_storage',
            'Sessions', 'Current Session', 'Current Tabs', 'Last Session', 'Last Tabs',
            'History', 'History-journal', 'Visited Links', 'Top Sites', 'Top Sites-journal',
            'Login Data', 'Login Data-journal', 'Login Data For Account',
            'Login Data For Account-journal', 'Web Data', 'Web Data-journal'
        )) {
            $edgeDataPaths += Join-Path $profile.FullName $relativePath
        }
    }
    foreach ($relativePath in @('ShaderCache', 'GrShaderCache', 'GraphiteDawnCache')) {
        $edgeDataPaths += Join-Path $edgeRoot $relativePath
    }
}
foreach ($item in Get-ChildItem -LiteralPath $LabRoot -Force) {
    if ($item.Name -notin @('.git', '.venv')) {
        Assert-NoReparsePoints -Path $item.FullName
    }
}

# Keep the installed Python environment and this script if it is not in the baseline.
$cleanArguments = @('clean', '-fdx', '-e', '.venv/', '-e', 'scripts/reset-lab-vm.ps1')
Write-Host "Lab checkout: $LabRoot"
Write-Host "Restore tracked files to: $baseline"
Write-Host 'Remove untracked/ignored files (including .env, .azure, evidence and logs):'
Invoke-Git @('clean', '-ndx', '-e', '.venv/', '-e', 'scripts/reset-lab-vm.ps1') |
    ForEach-Object { Write-Host $_ }
Write-Host "Clear Edge cookies, cache, history, site storage, sessions, passwords and autofill at: $edgeRoot"
Write-Host 'Preserve Edge profiles, settings, extensions and favorites; seed Azure favorites only if missing.'
Write-Host 'Sign out az/azd; clear clipboard and PowerShell history for this user.'
Write-Warning 'Close other terminals, VS Code and lab processes. Sign out of Edge sync first; otherwise sync may restore personal data.'

if (-not $PSCmdlet.ShouldProcess(
    "$LabRoot and the current Windows user's browser/session data",
    'Discard lab work and reset for a fresh learner'
)) {
    return
}

Invoke-Logout -Command 'az' -Arguments @('logout')
Invoke-Logout -Command 'az' -Arguments @('account', 'clear')
Invoke-Logout -Command 'azd' -Arguments @('auth', 'logout')

# Stop only explicit Edge PIDs owned by this Windows user.
$currentSid = [Security.Principal.WindowsIdentity]::GetCurrent().User.Value
foreach ($process in Get-CimInstance Win32_Process -Filter "Name = 'msedge.exe'") {
    $owner = Invoke-CimMethod -InputObject $process -MethodName GetOwnerSid
    if ($owner.ReturnValue -ne 0) {
        throw "Cannot determine the owner of Edge PID $($process.ProcessId). Close Edge and rerun."
    }
    if ($owner.Sid -eq $currentSid) {
        Stop-Process -Id $process.ProcessId -Force -ErrorAction SilentlyContinue
        if (Get-Process -Id $process.ProcessId -ErrorAction SilentlyContinue) {
            throw "Edge PID $($process.ProcessId) is still running."
        }
    }
}

foreach ($path in $edgeDataPaths) {
    if (Test-Path -LiteralPath $path) {
        Remove-Item -LiteralPath $path -Recurse -Force
    }
}
Invoke-Git @('reset', '--hard', $baseline) | ForEach-Object { Write-Host $_ }
Invoke-Git $cleanArguments | ForEach-Object { Write-Host $_ }

if (Get-Module PSReadLine) {
    $historyPath = (Get-PSReadLineOption).HistorySavePath
    Set-PSReadLineOption -HistorySaveStyle SaveNothing
    [Microsoft.PowerShell.PSConsoleReadLine]::ClearHistory()
    if ($historyPath -and (Test-Path -LiteralPath $historyPath)) {
        Remove-Item -LiteralPath $historyPath -Force
    }
}
# The other PowerShell edition may have its own saved history.
foreach ($edition in @('PowerShell', 'WindowsPowerShell')) {
    $historyDirectory = Join-Path $env:APPDATA "Microsoft\$edition\PSReadLine"
    if (Test-Path -LiteralPath $historyDirectory) {
        Assert-NoReparsePoints -Path $historyDirectory
        foreach ($file in Get-ChildItem -LiteralPath $historyDirectory -Filter '*_history.txt' -File) {
            Remove-Item -LiteralPath $file.FullName -Force
        }
    }
}
Clear-History
& powershell.exe -NoProfile -NonInteractive -STA -Command {
    $ErrorActionPreference = 'Stop'
    Add-Type -AssemblyName System.Windows.Forms
    [System.Windows.Forms.Clipboard]::Clear()
}
if ($LASTEXITCODE -ne 0) {
    throw 'Clipboard cleanup failed. Clear it manually before saving the image.'
}

foreach ($path in $edgeDataPaths) {
    if (Test-Path -LiteralPath $path) {
        throw "Edge browsing data remains or was recreated: $path. Close Edge/background startup and rerun."
    }
}
$remaining = @(Invoke-Git @('clean', '-ndx', '-e', '.venv/', '-e', 'scripts/reset-lab-vm.ps1'))
if ($remaining.Count -gt 0 -or (Invoke-Git @('diff', '--name-only', 'HEAD'))) {
    throw 'Some lab files remain modified or could not be removed. Do not save the image yet.'
}

$bookmarks = @{
    version = 1
    roots = @{
        bookmark_bar = @{
            id = '1'; name = 'Favorites bar'; type = 'folder'
            children = @(
                @{ id = '4'; name = 'Azure portal'; type = 'url'; url = 'https://portal.azure.com/' },
                @{ id = '5'; name = 'Microsoft Foundry'; type = 'url'; url = 'https://ai.azure.com/' }
            )
        }
        other = @{ id = '2'; name = 'Other favorites'; type = 'folder'; children = @() }
        synced = @{ id = '3'; name = 'Mobile favorites'; type = 'folder'; children = @() }
    }
}
$profileDirectory = Join-Path $edgeRoot 'Default'
if (-not (Test-Path -LiteralPath (Join-Path $profileDirectory 'Bookmarks'))) {
    New-Item -ItemType Directory -Path $profileDirectory -Force | Out-Null
    [IO.File]::WriteAllText(
        (Join-Path $profileDirectory 'Bookmarks'),
        ($bookmarks | ConvertTo-Json -Depth 8),
        (New-Object System.Text.UTF8Encoding($false))
    )
    Write-Host 'Added Edge favorites: https://portal.azure.com/ and https://ai.azure.com/'
}
Write-Host 'Local lab reset complete. Installed tools and .venv were preserved; no Azure resources were deleted.'
Write-Warning 'Close this terminal. Verify website and Edge profile sign-out, other browsers/apps and Windows clipboard history (Win+V) before using Skillable commit/save VM image. Windows SSO can sign you back in; this script does not reset Windows accounts, Edge profile identity, other app credentials or remote sessions.'
