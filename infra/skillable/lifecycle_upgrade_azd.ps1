<#
Updates Azure Developer CLI with Windows Package Manager, without interactive prompts.
Run as the VM user with winget available; the installed package must be upgradeable by that user.
Use -WhatIf to preview without running winget.
#>
#requires -Version 5.1
[CmdletBinding(SupportsShouldProcess = $true)]
param()

$ErrorActionPreference = 'Stop'
if ($env:OS -ne 'Windows_NT') {
    throw 'Azure Developer CLI upgrade requires Windows.'
}
if (-not $PSCmdlet.ShouldProcess('Azure Developer CLI (Microsoft.Azd)', 'Upgrade with winget')) {
    return
}
if (-not (Get-Command winget -ErrorAction SilentlyContinue)) {
    throw 'winget is not available for this VM user. Install Windows App Installer before running the upgrade.'
}

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
# winget UPDATE_NOT_APPLICABLE (0x8A15002B) means no newer version is available.
if ($exitCode -in @(-1978335189, 2316632107)) {
    Write-Host 'Azure Developer CLI is already up to date.'
} elseif ($exitCode -ne 0) {
    throw "Azure Developer CLI upgrade failed with winget exit code $exitCode. Check package availability, connectivity and installation permissions."
} else {
    Write-Host 'Azure Developer CLI upgrade completed.'
}
