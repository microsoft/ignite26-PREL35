<#
Updates Azure Developer CLI with Windows Package Manager and updates its extensions without prompts.
Run as the VM user with winget and azd available; the installed package must be upgradeable by that user.
Use -WhatIf to preview without running either command.
Appends run context and upgrade output to LOCALAPPDATA\PREL35\azd-upgrade.log.
#>
#requires -Version 5.1
[CmdletBinding(SupportsShouldProcess = $true)]
param(
    [string]$LogPath
)

$ErrorActionPreference = 'Stop'
if ($env:OS -ne 'Windows_NT') {
    throw 'Azure Developer CLI upgrade requires Windows.'
}
if (-not $PSCmdlet.ShouldProcess('Azure Developer CLI (Microsoft.Azd) and its extensions', 'Upgrade with winget and azd')) {
    return
}
if ([string]::IsNullOrWhiteSpace($LogPath)) {
    $LogPath = Join-Path $env:LOCALAPPDATA 'PREL35\azd-upgrade.log'
}
$transcriptStarted = $false
try {
    $logDirectory = Split-Path -Path ([IO.Path]::GetFullPath($LogPath)) -Parent
    New-Item -ItemType Directory -Path $logDirectory -Force | Out-Null
    Start-Transcript -Path $LogPath -Append | Out-Null
    $transcriptStarted = $true
} catch {
    Write-Warning "Could not start upgrade log '$LogPath': $($_.Exception.Message)" -WarningAction Continue
}
try {
    Write-Host "Upgrade log: $LogPath"
    Write-Host "Run context: user=$env:USERNAME; session=$([Diagnostics.Process]::GetCurrentProcess().SessionId); PowerShell=$($PSVersionTable.PSVersion)"
    $winget = Get-Command winget -ErrorAction SilentlyContinue
    if (-not $winget) {
        throw 'winget is not available for this VM user. Install Windows App Installer before running the upgrade.'
    }
    Write-Host "winget command: $($winget.Source)"
    Write-Host 'Checking for an Azure Developer CLI upgrade.'
    try {
        # Windows PowerShell treats native stderr as error records; check winget's exit code instead.
        $ErrorActionPreference = 'Continue'
        $PSNativeCommandUseErrorActionPreference = $false
        $output = @(& winget upgrade --id Microsoft.Azd --exact --source winget --silent `
            --accept-package-agreements --accept-source-agreements --disable-interactivity 2>&1)
        $exitCode = $LASTEXITCODE
    } finally {
        $ErrorActionPreference = 'Stop'
        $global:LASTEXITCODE = 0
    }
    $output | ForEach-Object { Write-Host $_ }
    Write-Host "winget exit code: $exitCode"
    # winget UPDATE_NOT_APPLICABLE (0x8A15002B) means no newer version is available.
    if ($exitCode -in @(-1978335189, 2316632107)) {
        Write-Host 'Azure Developer CLI is already up to date.'
    } elseif ($exitCode -ne 0) {
        throw "Azure Developer CLI upgrade failed with winget exit code $exitCode. Check package availability, connectivity and installation permissions."
    } else {
        Write-Host 'Azure Developer CLI upgrade completed.'
    }

    $azd = Get-Command azd -ErrorAction SilentlyContinue
    if (-not $azd) {
        throw 'azd is not available for this VM user. Ensure Azure Developer CLI is on PATH before updating extensions.'
    }
    Write-Host "azd command: $($azd.Source)"
    Write-Host 'Updating Azure Developer CLI extensions.'
    try {
        $ErrorActionPreference = 'Continue'
        $PSNativeCommandUseErrorActionPreference = $false
        $output = @(& azd extension update --all --no-prompt 2>&1)
        $exitCode = $LASTEXITCODE
    } finally {
        $ErrorActionPreference = 'Stop'
        $global:LASTEXITCODE = 0
    }
    $output | ForEach-Object { Write-Host $_ }
    Write-Host "azd extension update exit code: $exitCode"
    if ($exitCode -ne 0) {
        throw "Azure Developer CLI extension update failed with exit code $exitCode."
    }
    Write-Host 'Azure Developer CLI extension update completed.'
} catch {
    Write-Host "Upgrade error: $($_.Exception.Message)"
    throw
} finally {
    if ($transcriptStarted) {
        try {
            Stop-Transcript | Out-Null
        } catch {
            Write-Warning "Could not close upgrade log '$LogPath': $($_.Exception.Message)" -WarningAction Continue
        }
    }
}
