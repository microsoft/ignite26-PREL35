# Post-clone VM setup hook, run by Skillable as LabUser.
#requires -Version 5.1
[CmdletBinding(SupportsShouldProcess = $true)]
param()

$ErrorActionPreference = 'Stop'
foreach ($step in @('lifecycle_pin_terminal.ps1', 'lifecycle_clear_recycle_bin.ps1')) {
    try {
        & (Join-Path $PSScriptRoot $step) -WhatIf:$WhatIfPreference
    } catch {
        Write-Warning "Optional VM setup step '$step' failed: $($_.Exception.Message)" -WarningAction Continue
    }
}
