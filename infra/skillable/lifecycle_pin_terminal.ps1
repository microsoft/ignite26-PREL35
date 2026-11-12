<#
Run as LabUser in a Windows VM lifecycle action, using Windows PowerShell.
Creates a Windows Terminal desktop shortcut independently of pinning and Explorer availability.
Sets the Terminal icon on new and existing shortcuts, preserving existing launch settings.
Configures the taskbar first, then attempts Start pinning without making Start failures fatal.
Start pinning requires 24H2 with KB5062660 (26100.4770) or later; otherwise it warns and skips Start.
Use -TaskbarOnly to leave Start unchanged without attempting it.
Refreshes the current desktop's Explorer shell when available, without signing LabUser out.
Noninteractive runs save the layouts and warn that pin appearance remains unverified.
The desktop/taskbar briefly disappear and File Explorer windows may close.
Use -WhatIf to preview without creating shortcuts, writing layouts/policies, or restarting Explorer.
#>
#requires -Version 5.1
[CmdletBinding(SupportsShouldProcess = $true)]
param(
    [switch]$TaskbarOnly
)

$ErrorActionPreference = 'Stop'
if ($env:OS -ne 'Windows_NT') {
    throw 'Terminal pinning requires Windows.'
}
if ($env:USERNAME -ne 'LabUser') {
    throw 'Run this lifecycle action as LabUser, not Administrator or SYSTEM.'
}

$version = Get-ItemProperty -LiteralPath 'HKLM:\SOFTWARE\Microsoft\Windows NT\CurrentVersion'
$build = [int]$version.CurrentBuildNumber
if ($build -lt 22000) {
    throw 'This lifecycle script requires Windows 11.'
}
$supportsStartPins = $build -gt 26100 -or ($build -eq 26100 -and [int]$version.UBR -ge 4770)
$terminalId = 'Microsoft.WindowsTerminal_8wekyb3d8bbwe!App'
if (-not (Get-StartApps | Where-Object { $_.AppID -eq $terminalId })) {
    throw 'Windows Terminal is not registered for LabUser. Install it before running this lifecycle action.'
}

if ($PSCmdlet.ShouldProcess('LabUser Desktop', 'Create or update Windows Terminal shortcut icon, preserving existing launch settings')) {
    $shortcutShell = New-Object -ComObject WScript.Shell
    $desktop = [string]$shortcutShell.SpecialFolders.Item('Desktop')
    if ([string]::IsNullOrWhiteSpace($desktop)) {
        throw 'The Windows shell could not locate LabUser''s Desktop.'
    }
    $shortcutPath = Join-Path $desktop 'Windows Terminal.lnk'
    $existingShortcut = Test-Path -LiteralPath $shortcutPath
    $shortcut = $shortcutShell.CreateShortcut($shortcutPath)
    if (-not $existingShortcut) {
        $shortcut.TargetPath = Join-Path $env:WINDIR 'explorer.exe'
        $shortcut.Arguments = "shell:AppsFolder\$terminalId"
        $shortcut.Description = 'Open Windows Terminal'
    }
    try {
        $package = Get-AppxPackage -Name Microsoft.WindowsTerminal | Select-Object -First 1
        if (-not $package -or [string]::IsNullOrWhiteSpace($package.InstallLocation)) {
            throw 'Windows Terminal package location is unavailable for LabUser.'
        }
        $iconPath = Join-Path $package.InstallLocation 'WindowsTerminal.exe'
        if (-not (Test-Path -LiteralPath $iconPath -PathType Leaf)) {
            throw "Windows Terminal icon executable not found: $iconPath"
        }
        $shortcut.IconLocation = "$iconPath,0"
    } catch {
        Write-Warning "Terminal shortcut icon was not updated: $($_.Exception.Message)" -WarningAction Continue
    }
    $shortcut.Save()
    if ($existingShortcut) {
        Write-Host "Existing desktop shortcut updated; launch settings preserved: $shortcutPath"
    } else {
        Write-Host "Windows Terminal desktop shortcut created: $shortcutPath"
    }
}

$directory = Join-Path $env:LOCALAPPDATA 'PREL35\Shell'
$startFile = Join-Path $directory 'StartLayout.json'
$taskbarFile = Join-Path $directory 'TaskbarLayout.xml'
$policy = 'HKCU:\Software\Policies\Microsoft\Windows\Explorer'
$machinePolicy = 'HKLM:\Software\Policies\Microsoft\Windows\Explorer'
$machineSettings = $null
if (Test-Path -LiteralPath $machinePolicy) {
    $machineSettings = Get-ItemProperty -LiteralPath $machinePolicy
    if ($machineSettings.StartLayoutFile -or $machineSettings.LockedStartLayout) {
        throw 'A machine-wide taskbar layout policy already exists. Resolve it before adding learner pins.'
    }
}
$userSettings = $null
if (Test-Path -LiteralPath $policy) {
    $userSettings = Get-ItemProperty -LiteralPath $policy
    if (($userSettings.StartLayoutFile -and $userSettings.StartLayoutFile -ne $taskbarFile) -or
        ($userSettings.LockedStartLayout -and $userSettings.StartLayoutFile -ne $taskbarFile)) {
        throw 'An existing user taskbar layout policy would be overwritten. Existing settings were not changed.'
    }
}
$target = if ($TaskbarOnly) { 'LabUser taskbar (Start unchanged)' } else { 'LabUser Start menu and taskbar' }
if (-not $PSCmdlet.ShouldProcess($target, 'Configure Windows Terminal pins and refresh Explorer when available, preserving existing pins')) {
    return
}

if (-not ('PREL35.DesktopShell' -as [type])) {
    Add-Type -Namespace PREL35 -Name DesktopShell -MemberDefinition @'
[System.Runtime.InteropServices.DllImport("user32.dll")]
public static extern System.IntPtr GetShellWindow();
[System.Runtime.InteropServices.DllImport("user32.dll")]
public static extern uint GetWindowThreadProcessId(System.IntPtr window, out uint processId);
[System.Runtime.InteropServices.DllImport("user32.dll", CharSet = System.Runtime.InteropServices.CharSet.Unicode)]
public static extern System.IntPtr FindWindow(string className, string windowName);
'@
}
function Get-LearnerShellProcess {
    $window = [PREL35.DesktopShell]::GetShellWindow()
    if ($window -eq [IntPtr]::Zero) {
        throw 'Run this action in LabUser''s interactive desktop session; no Explorer shell is available here.'
    }
    [uint32]$shellProcessId = 0
    [PREL35.DesktopShell]::GetWindowThreadProcessId($window, [ref]$shellProcessId) | Out-Null
    $process = Get-Process -Id $shellProcessId -ErrorAction Stop
    if ($process.ProcessName -ne 'explorer' -or
        $process.SessionId -ne [Diagnostics.Process]::GetCurrentProcess().SessionId) {
        throw 'The desktop shell is not Windows Explorer in this session. No process was stopped.'
    }
    return $process
}
$taskbarXml = @'
<?xml version="1.0" encoding="utf-8"?>
<LayoutModificationTemplate
    xmlns="http://schemas.microsoft.com/Start/2014/LayoutModification"
    xmlns:defaultlayout="http://schemas.microsoft.com/Start/2014/FullDefaultLayout"
    xmlns:taskbar="http://schemas.microsoft.com/Start/2014/TaskbarLayout"
    Version="1">
  <CustomTaskbarLayoutCollection>
    <defaultlayout:TaskbarLayout>
      <taskbar:TaskbarPinList>
        <taskbar:UWA AppUserModelID="Microsoft.WindowsTerminal_8wekyb3d8bbwe!App" PinGeneration="1" />
      </taskbar:TaskbarPinList>
    </defaultlayout:TaskbarLayout>
  </CustomTaskbarLayoutCollection>
</LayoutModificationTemplate>
'@
if (-not $supportsStartPins) {
    $taskbarXml = $taskbarXml.Replace(' PinGeneration="1"', '')
}
New-Item -ItemType Directory -Path $directory -Force | Out-Null
Set-Content -LiteralPath $taskbarFile -Value $taskbarXml -Encoding UTF8
if (-not (Test-Path -LiteralPath $policy)) {
    New-Item -Path $policy -Force | Out-Null
}
New-ItemProperty -Path $policy -Name StartLayoutFile -Value $taskbarFile -PropertyType ExpandString -Force | Out-Null
New-ItemProperty -Path $policy -Name LockedStartLayout -Value 1 -PropertyType DWord -Force | Out-Null
Write-Host 'Windows Terminal taskbar pin layout configured for LabUser.'

if ($TaskbarOnly) {
    Write-Host 'Start pins were not changed.'
} else {
    $export = $null
    try {
        if (-not $supportsStartPins) {
            throw 'Start pinning needs Windows 11 24H2 with KB5062660 (26100.4770) or later.'
        }
        if ($machineSettings.ConfigureStartPins -or $machineSettings.ConfigureStartPinsJSON) {
            throw 'A machine-wide Start pin policy already exists and was not overwritten.'
        }
        if (($userSettings.ConfigureStartPins -and $userSettings.ConfigureStartPinsJSON -ne $startFile) -or
            ($userSettings.ConfigureStartPinsJSON -and $userSettings.ConfigureStartPinsJSON -ne $startFile)) {
            throw 'An existing user Start pin policy would be overwritten; it was left unchanged.'
        }
        $export = Join-Path ([IO.Path]::GetTempPath()) ('PREL35-start-' + [guid]::NewGuid().ToString('N') + '.json')
        Export-StartLayout -Path $export
        $layout = Get-Content -LiteralPath $export -Raw | ConvertFrom-Json
        if ($layout.pinnedList -isnot [array]) {
            throw 'The exported Start layout does not contain a pinnedList array.'
        }
        if (-not ($layout.pinnedList | Where-Object {
            $_.packagedAppId -eq $terminalId -or $_.desktopAppId -eq $terminalId
        })) {
            $layout.pinnedList = @($layout.pinnedList) + @([pscustomobject]@{ packagedAppId = $terminalId })
        }
        $layout | Add-Member -NotePropertyName applyOnce -NotePropertyValue $true -Force
        $startJson = $layout | ConvertTo-Json -Depth 100
        Set-Content -LiteralPath $startFile -Value $startJson -Encoding UTF8
        New-ItemProperty -Path $policy -Name ConfigureStartPinsJSON -Value $startFile -PropertyType ExpandString -Force | Out-Null
        New-ItemProperty -Path $policy -Name ConfigureStartPins -Value 1 -PropertyType DWord -Force | Out-Null
        Write-Host 'Windows Terminal Start pin layout configured for LabUser.'
    } catch {
        Write-Warning "Start pinning was not confirmed: $($_.Exception.Message) Taskbar setup remains configured." -WarningAction Continue
    } finally {
        if ($export) {
            try {
                if (Test-Path -LiteralPath $export) {
                    Remove-Item -LiteralPath $export -Force
                }
            } catch {
                Write-Warning "Could not remove temporary Start export '$export': $($_.Exception.Message)" -WarningAction Continue
            }
        }
    }
}
try {
    $explorer = Get-LearnerShellProcess
} catch {
    Write-Warning "Explorer refresh skipped: $($_.Exception.Message) Terminal layouts remain configured; pin appearance is unverified. Check them in LabUser's next desktop session." -WarningAction Continue
    return
}
Write-Host "Refreshing Windows Explorer (PID $($explorer.Id)); LabUser remains signed in."
Stop-Process -Id $explorer.Id -Force -ErrorAction Stop
try {
    Wait-Process -InputObject $explorer -Timeout 10 -ErrorAction Stop
} finally {
    $autoRestartDeadline = (Get-Date).AddSeconds(2)
    while ([PREL35.DesktopShell]::GetShellWindow() -eq [IntPtr]::Zero -and
           (Get-Date) -lt $autoRestartDeadline) {
        Start-Sleep -Milliseconds 100
    }
    if ([PREL35.DesktopShell]::GetShellWindow() -eq [IntPtr]::Zero) {
        Start-Process -FilePath (Join-Path $env:WINDIR 'explorer.exe') -ErrorAction Stop
    }
}

$deadline = (Get-Date).AddSeconds(15)
$ready = $false
do {
    $window = [PREL35.DesktopShell]::GetShellWindow()
    if ($window -ne [IntPtr]::Zero -and
        [PREL35.DesktopShell]::FindWindow('Shell_TrayWnd', $null) -ne [IntPtr]::Zero) {
        [uint32]$newShellProcessId = 0
        [PREL35.DesktopShell]::GetWindowThreadProcessId($window, [ref]$newShellProcessId) | Out-Null
        if ($newShellProcessId -ne $explorer.Id) {
            $ready = $true
            break
        }
    }
    Start-Sleep -Milliseconds 250
} while ((Get-Date) -lt $deadline)
if (-not $ready) {
    throw 'Explorer refresh did not restore the desktop and taskbar in time. Layout files remain configured, but their application is unverified.'
}
Write-Host 'Explorer refreshed without signing out. Check that the Terminal pin is visible; pin appearance has not been automatically verified.'
