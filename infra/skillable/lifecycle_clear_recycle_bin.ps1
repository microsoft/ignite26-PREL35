<#
Run as LabUser in a Windows VM lifecycle action.
Permanently empties that user's Recycle Bin on all drives, without a confirmation prompt.
Use -WhatIf to preview without deleting anything.
#>
#requires -Version 5.1
[CmdletBinding(SupportsShouldProcess = $true)]
param()

$ErrorActionPreference = 'Stop'
if ($env:OS -ne 'Windows_NT') {
    throw 'Recycle Bin cleanup requires Windows.'
}
if ($env:USERNAME -ne 'LabUser') {
    throw 'Run this lifecycle action as LabUser, not Administrator or SYSTEM.'
}
if (-not $PSCmdlet.ShouldProcess('LabUser Recycle Bin on all drives', 'Permanently delete recycled items')) {
    return
}

$shell = New-Object -ComObject Shell.Application
$recycleBin = $shell.Namespace(10)
if ($null -eq $recycleBin) {
    throw 'The Windows shell could not open the Recycle Bin.'
}
if ($recycleBin.Items().Count -eq 0) {
    Write-Host 'Recycle Bin is already empty.'
    return
}

Clear-RecycleBin -Force -ErrorAction Stop
Write-Host 'Recycle Bin emptied for LabUser.'
