import json
import os
from pathlib import Path
import shutil
import subprocess
import xml.etree.ElementTree as ET

import pytest


ROOT = Path(__file__).resolve().parents[1]
PWSH = shutil.which("pwsh")
TERMINAL = "Microsoft.WindowsTerminal_8wekyb3d8bbwe!App"
pytestmark = pytest.mark.skipif(not PWSH, reason="PowerShell is required")


@pytest.fixture
def desktop(tmp_path):
    harness = tmp_path / "desktop.ps1"
    harness.write_text(r"""
$ErrorActionPreference = 'Stop'
$env:OS = $env:TEST_OS
$env:USERNAME = $env:TEST_USER
$env:LOCALAPPDATA = $env:TEST_LOCALAPPDATA

Add-Type -Namespace PREL35 -Name DesktopShell -MemberDefinition @'
public static System.IntPtr GetShellWindow() {
    if (System.Environment.GetEnvironmentVariable("DESKTOP_AVAILABLE") == "0")
        return System.IntPtr.Zero;
    if (System.Environment.GetEnvironmentVariable("SHELL_STOPPED") == "1" &&
        System.Environment.GetEnvironmentVariable("SHELL_READY") != "1")
        return System.IntPtr.Zero;
    return new System.IntPtr(1);
}
public static uint GetWindowThreadProcessId(System.IntPtr window, out uint processId) {
    processId = System.Environment.GetEnvironmentVariable("SHELL_READY") == "1" ? 424243u : 424242u;
    return 1;
}
public static System.IntPtr FindWindow(string className, string windowName) {
    return GetShellWindow();
}
'@
function Get-Process {
    [CmdletBinding()]
    param([int]$Id)
    if ($Id -notin @(424242, 424243)) { throw 'Unexpected process lookup' }
    [pscustomobject]@{
        Id = $Id
        ProcessName = $env:SHELL_PROCESS_NAME
        SessionId = [Diagnostics.Process]::GetCurrentProcess().SessionId + [int]$env:SHELL_SESSION_OFFSET
    }
}
function Stop-Process {
    [CmdletBinding()]
    param([int]$Id, [switch]$Force)
    if ($Id -ne 424242) { throw 'An unrelated process would be stopped' }
    [IO.File]::WriteAllText($env:STOP_LOG, [string]$Id)
    $env:SHELL_STOPPED = '1'
    if ($env:AUTO_RESTART -eq '1') { $env:SHELL_READY = '1' }
}
function Wait-Process {
    [CmdletBinding()]
    param($InputObject, $Timeout)
    if ($InputObject.Id -ne 424242) { throw 'Unexpected process wait' }
    if ($env:FAIL_SHELL_WAIT -eq '1') { throw 'Fixture shell wait failure' }
}
function Start-Process {
    [CmdletBinding()]
    param($FilePath)
    if ($env:FAIL_SHELL_START -eq '1') { throw 'Fixture Explorer start failure' }
    [IO.File]::WriteAllText($env:START_LOG, $FilePath)
    if ($env:SHELL_NEVER_READY -ne '1') { $env:SHELL_READY = '1' }
}
$script:clock = 0
function Get-Date {
    $now = ([datetime]'2026-01-01').AddSeconds($script:clock)
    $script:clock += 10
    return $now
}
function Start-Sleep { param($Milliseconds) }

function New-Object {
    param($ComObject)
    if ($ComObject -eq 'WScript.Shell') {
        $folders = [pscustomobject]@{}
        $folders | Add-Member -MemberType ScriptMethod -Name Item -Value {
            param($name)
            if ($name -ne 'Desktop') { throw 'Unexpected special folder' }
            return $env:TEST_DESKTOP
        }
        $shell = [pscustomobject]@{ SpecialFolders = $folders }
        $shell | Add-Member -MemberType ScriptMethod -Name CreateShortcut -Value {
            param($path)
            $shortcut = [pscustomobject]@{
                Path = $path; TargetPath = ''; Arguments = ''; Description = ''; IconLocation = ''
            }
            if (Microsoft.PowerShell.Management\Test-Path -LiteralPath $path) {
                $shortcut = Get-Content -LiteralPath $path -Raw | ConvertFrom-Json
            }
            $shortcut | Add-Member -MemberType ScriptMethod -Name Save -Value {
                if ($env:FAIL_SHORTCUT -eq '1') { throw 'Fixture shortcut failure' }
                $this | ConvertTo-Json | Set-Content -LiteralPath $this.Path -Encoding UTF8
            }
            return $shortcut
        }
        return $shell
    }
    if ($ComObject -ne 'Shell.Application') { throw 'Unexpected COM object' }
    $shell = [pscustomobject]@{}
    $shell | Add-Member -MemberType ScriptMethod -Name Namespace -Value {
        param($id)
        if ($id -ne 10) { throw 'Unexpected shell namespace' }
        $folder = [pscustomobject]@{}
        $folder | Add-Member -MemberType ScriptMethod -Name Items -Value {
            [pscustomobject]@{ Count = [int]$env:ITEM_COUNT }
        }
        $folder
    }
    $shell
}
function Clear-RecycleBin {
    [CmdletBinding()]
    param([switch]$Force)
    if (-not $Force) { throw 'Cleanup would prompt' }
    if ($env:FAIL_CLEAR -eq '1') { throw 'Fixture cleanup failure' }
    [IO.File]::WriteAllText($env:CLEAR_LOG, 'cleared')
}
function Get-StartApps {
    if ($env:TERMINAL_PRESENT -ne '0') {
        [pscustomobject]@{ AppID = 'Microsoft.WindowsTerminal_8wekyb3d8bbwe!App' }
    }
}
function Get-AppxPackage {
    param($Name)
    if ($Name -ne 'Microsoft.WindowsTerminal') { throw 'Unexpected package lookup' }
    if ($env:TERMINAL_PACKAGE_PRESENT -ne '0') {
        [pscustomobject]@{ InstallLocation = $env:TERMINAL_PACKAGE_PATH }
    }
}
function Get-ItemProperty {
    param($LiteralPath)
    if ($LiteralPath -eq 'HKLM:\SOFTWARE\Microsoft\Windows NT\CurrentVersion') {
        return [pscustomobject]@{ CurrentBuildNumber = $env:WINDOWS_BUILD; UBR = $env:WINDOWS_REVISION }
    }
    if ($LiteralPath.StartsWith('HKLM:')) { return ($env:MACHINE_POLICY | ConvertFrom-Json) }
    if ($LiteralPath.StartsWith('HKCU:')) { return ($env:USER_POLICY | ConvertFrom-Json) }
    throw 'Unexpected registry read'
}
function Test-Path {
    param($LiteralPath, $PathType)
    if ($LiteralPath.StartsWith('HKLM:')) { return $env:MACHINE_POLICY -ne '{}' }
    if ($LiteralPath.StartsWith('HKCU:')) { return $env:USER_POLICY -ne '{}' }
    if ($PathType) {
        return Microsoft.PowerShell.Management\Test-Path -LiteralPath $LiteralPath -PathType $PathType
    }
    Microsoft.PowerShell.Management\Test-Path -LiteralPath $LiteralPath
}
function New-Item {
    param($Path, $ItemType, [switch]$Force)
    if ($Path.StartsWith('HKCU:')) {
        if ($env:USER_POLICY -ne '{}') { throw 'Existing policy key was recreated' }
        return
    }
    if ($Path.StartsWith('HKLM:')) { throw 'Unexpected machine registry write' }
    Microsoft.PowerShell.Management\New-Item -Path $Path -ItemType $ItemType -Force:$Force
}
function New-ItemProperty {
    param($Path, $Name, $Value, $PropertyType, [switch]$Force)
    if ($Path -ne 'HKCU:\Software\Policies\Microsoft\Windows\Explorer') {
        throw 'Unexpected registry write'
    }
    if ($env:FAIL_START_POLICY -eq '1' -and $Name -eq 'ConfigureStartPinsJSON') {
        throw 'Fixture Start policy failure'
    }
    if ($env:FAIL_TASKBAR_POLICY -eq '1' -and $Name -eq 'StartLayoutFile') {
        throw 'Fixture taskbar policy failure'
    }
    @{ name = $Name; value = $Value; type = $PropertyType } |
        ConvertTo-Json -Compress | Add-Content -LiteralPath $env:POLICY_LOG -Encoding UTF8
}
function Export-StartLayout {
    param($Path)
    if ($env:FAIL_EXPORT -eq '1') { throw 'Fixture export failure' }
    Set-Content -LiteralPath $Path -Value $env:START_LAYOUT -Encoding UTF8
}
function Remove-Item {
    param($LiteralPath, [switch]$Force)
    if ($env:FAIL_EXPORT_CLEANUP -eq '1') { throw 'Fixture export cleanup failure' }
    Microsoft.PowerShell.Management\Remove-Item -LiteralPath $LiteralPath -Force:$Force
}
function Get-Command {
    [CmdletBinding()]
    param([string]$Name)
    if ($Name -eq 'winget' -and $env:WINGET_PRESENT -eq '0') { return }
    Microsoft.PowerShell.Core\Get-Command @PSBoundParameters
}
function winget {
    param([Parameter(ValueFromRemainingArguments = $true)][string[]]$Arguments)
    if ($env:WINGET_PRESENT -eq '0') { throw 'Unexpected call to unavailable winget' }
    $Arguments | ConvertTo-Json | Set-Content -LiteralPath $env:UPGRADE_LOG -Encoding UTF8
    if ($env:WINGET_STDERR -eq '1') { Write-Error 'Fixture winget diagnostic' }
    Write-Output 'Fixture winget result'
    $global:LASTEXITCODE = [long]$env:WINGET_EXIT_CODE
}

$arguments = @{}
if ($env:PREVIEW -eq '1') { $arguments.WhatIf = $true }
if ($env:TASKBAR_ONLY -eq '1') { $arguments.TaskbarOnly = $true }
& $env:LIFECYCLE_SCRIPT @arguments
""", encoding="utf-8")
    desktop_path = tmp_path / "redirected desktop"
    desktop_path.mkdir()
    package_path = tmp_path / "Terminal package"
    package_path.mkdir()
    (package_path / "WindowsTerminal.exe").touch()
    environment = dict(
        os.environ, TEST_OS="Windows_NT", TEST_USER="LabUser",
        TEST_DESKTOP=str(desktop_path), FAIL_SHORTCUT="0",
        TERMINAL_PACKAGE_PATH=str(package_path), TERMINAL_PACKAGE_PRESENT="1",
        TEST_LOCALAPPDATA=str(tmp_path / "local"), ITEM_COUNT="3",
        WINDOWS_BUILD="26100", WINDOWS_REVISION="4770", TERMINAL_PRESENT="1",
        MACHINE_POLICY="{}", USER_POLICY="{}",
        START_LAYOUT=json.dumps({"pinnedList": [{"desktopAppId": "Microsoft.Windows.Explorer"}]}),
        CLEAR_LOG=str(tmp_path / "clear.txt"), POLICY_LOG=str(tmp_path / "policy.jsonl"),
        TMPDIR=str(tmp_path), TEMP=str(tmp_path), TMP=str(tmp_path),
        FAIL_CLEAR="0", FAIL_EXPORT="0", PREVIEW="0", TASKBAR_ONLY="0",
        FAIL_START_POLICY="0", FAIL_TASKBAR_POLICY="0", FAIL_EXPORT_CLEANUP="0",
        WINDIR=str(tmp_path / "Windows"), DESKTOP_AVAILABLE="1", SHELL_STOPPED="0", SHELL_READY="0",
        SHELL_PROCESS_NAME="explorer", SHELL_SESSION_OFFSET="0", AUTO_RESTART="0",
        FAIL_SHELL_WAIT="0", FAIL_SHELL_START="0", SHELL_NEVER_READY="0",
        STOP_LOG=str(tmp_path / "stop.txt"), START_LOG=str(tmp_path / "start.txt"),
        UPGRADE_LOG=str(tmp_path / "upgrade.json"), WINGET_PRESENT="1",
        WINGET_EXIT_CODE="0", WINGET_STDERR="0",
    )

    def run(script="pin_terminal", **extra):
        source = ROOT / "infra/skillable" / f"lifecycle_{script}.ps1"
        return subprocess.run(
            [PWSH, "-NoProfile", "-NonInteractive", "-File", str(harness)],
            env=environment | {"LIFECYCLE_SCRIPT": str(source)} | extra,
            capture_output=True, text=True, timeout=30,
        )

    return run, tmp_path


@pytest.mark.parametrize("count", ["0", "3"])
def test_recycle_bin_cleanup_only_clears_a_nonempty_bin(desktop, count):
    run, root = desktop
    result = run("clear_recycle_bin", ITEM_COUNT=count)
    assert result.returncode == 0, result.stdout + result.stderr
    assert (root / "clear.txt").exists() is (count != "0")
    assert ("already empty" if count == "0" else "emptied for LabUser") in result.stdout


def test_recycle_bin_cleanup_errors_are_not_reported_as_success(desktop):
    run, root = desktop
    result = run("clear_recycle_bin", FAIL_CLEAR="1")
    assert result.returncode != 0
    assert "Fixture cleanup failure" in result.stderr
    assert "emptied for LabUser" not in result.stdout
    assert not (root / "clear.txt").exists()


@pytest.mark.parametrize("script", ["clear_recycle_bin", "pin_terminal"])
@pytest.mark.parametrize("extra,message", [
    ({"TEST_USER": "SYSTEM"}, "Run this lifecycle action as LabUser"),
    ({"TEST_OS": "Linux"}, "requires Windows"),
    ({"PREVIEW": "1"}, "What if:"),
])
def test_desktop_scripts_reject_wrong_context_and_support_preview(desktop, script, extra, message):
    run, root = desktop
    result = run(script, **extra)
    assert (result.returncode == 0) is ("PREVIEW" in extra)
    assert message in result.stdout + result.stderr
    assert not (root / "clear.txt").exists()
    assert not (root / "policy.jsonl").exists()
    assert not (root / "local").exists()
    assert not (root / "stop.txt").exists()
    assert not (root / "start.txt").exists()
    assert not list((root / "redirected desktop").iterdir())


def test_terminal_shortcut_launches_registered_app_from_redirected_desktop(desktop):
    run, root = desktop
    result = run()
    assert result.returncode == 0, result.stdout + result.stderr
    shortcut = json.loads(
        (root / "redirected desktop/Windows Terminal.lnk").read_text(encoding="utf-8-sig")
    )
    assert Path(shortcut["TargetPath"]) == root / "Windows/explorer.exe"
    assert shortcut["Arguments"] == f"shell:AppsFolder\\{TERMINAL}"
    assert shortcut["IconLocation"] == f"{root / 'Terminal package/WindowsTerminal.exe'},0"
    assert "desktop shortcut created" in result.stdout


def test_existing_terminal_desktop_shortcut_icon_is_fixed_without_changing_launch_settings(desktop):
    run, root = desktop
    shortcut = root / "redirected desktop/Windows Terminal.lnk"
    original = {
        "Path": str(shortcut), "TargetPath": "custom-terminal.exe", "Arguments": "--custom",
        "Description": "Customized shortcut", "IconLocation": "explorer.exe,0",
    }
    shortcut.write_text(json.dumps(original))
    result = run()
    assert result.returncode == 0, result.stdout + result.stderr
    updated = json.loads(shortcut.read_text(encoding="utf-8-sig"))
    assert updated == original | {"IconLocation": f"{root / 'Terminal package/WindowsTerminal.exe'},0"}
    assert "launch settings preserved" in result.stdout


def test_unavailable_terminal_icon_warns_without_preventing_shortcut_creation(desktop):
    run, root = desktop
    result = run(TERMINAL_PACKAGE_PRESENT="0")
    assert result.returncode == 0, result.stdout + result.stderr
    assert "Terminal shortcut icon was not updated" in result.stdout
    assert (root / "redirected desktop/Windows Terminal.lnk").exists()


def test_terminal_shortcut_save_failure_is_reported(desktop):
    run, root = desktop
    result = run(FAIL_SHORTCUT="1")
    assert result.returncode != 0
    assert "Fixture shortcut failure" in result.stderr
    assert "desktop shortcut created" not in result.stdout
    assert not (root / "redirected desktop/Windows Terminal.lnk").exists()


@pytest.mark.parametrize("extra,terminal_succeeds,cleanup_succeeds", [
    ({}, True, True),
    ({"TERMINAL_PRESENT": "0"}, False, True),
    ({"FAIL_CLEAR": "1"}, True, False),
])
def test_vm_setup_runs_both_steps_and_warns_without_failing(desktop, extra, terminal_succeeds, cleanup_succeeds):
    run, root = desktop
    result = run("setup_vm", **extra)
    assert result.returncode == 0, result.stdout + result.stderr
    assert (root / "redirected desktop/Windows Terminal.lnk").exists() is terminal_succeeds
    assert (root / "stop.txt").exists() is terminal_succeeds
    assert (root / "clear.txt").exists() is cleanup_succeeds
    assert (root / "upgrade.json").exists()
    assert ("Optional VM setup step" in result.stdout) is not (terminal_succeeds and cleanup_succeeds)


def test_vm_setup_preview_does_not_change_desktop_or_empty_recycle_bin(desktop):
    run, root = desktop
    result = run("setup_vm", PREVIEW="1")
    assert result.returncode == 0, result.stdout + result.stderr
    assert "What if:" in result.stdout
    assert not list((root / "redirected desktop").iterdir())
    assert not (root / "local").exists()
    assert not (root / "clear.txt").exists()
    assert not (root / "stop.txt").exists()
    assert not (root / "upgrade.json").exists()


@pytest.mark.parametrize("exit_code,message", [
    ("0", "upgrade completed"),
    ("-1978335189", "already up to date"),
    ("2316632107", "already up to date"),
])
def test_azd_upgrade_is_noninteractive_and_accepts_already_current(desktop, exit_code, message):
    run, root = desktop
    result = run("upgrade_azd", WINGET_EXIT_CODE=exit_code, WINGET_STDERR="1")
    assert result.returncode == 0, result.stdout + result.stderr
    arguments = json.loads((root / "upgrade.json").read_text(encoding="utf-8-sig"))
    assert arguments[:6] == ["upgrade", "--id", "Microsoft.Azd", "--exact", "--source", "winget"]
    assert "--silent" in arguments
    assert "--disable-interactivity" in arguments
    assert "--accept-package-agreements" in arguments
    assert "--accept-source-agreements" in arguments
    assert message in result.stdout


@pytest.mark.parametrize("extra,message", [
    ({"WINGET_EXIT_CODE": "5"}, "upgrade failed with winget exit code 5"),
    ({"WINGET_PRESENT": "0"}, "winget is not available"),
])
def test_azd_upgrade_failures_are_explicit(desktop, extra, message):
    run, _ = desktop
    result = run("upgrade_azd", **extra)
    assert result.returncode != 0
    assert message in result.stderr
    assert "upgrade completed" not in result.stdout


def test_vm_setup_upgrade_failure_warns_without_failing_other_steps(desktop):
    run, root = desktop
    result = run("setup_vm", WINGET_EXIT_CODE="5")
    assert result.returncode == 0, result.stdout + result.stderr
    assert "lifecycle_upgrade_azd.ps1" in result.stdout
    assert "upgrade failed with winget exit code 5" in result.stdout
    assert (root / "redirected desktop/Windows Terminal.lnk").exists()
    assert (root / "clear.txt").exists()


@pytest.mark.parametrize("terminal_already_pinned,user_policy", [
    (False, {}),
    (True, {"HideRecommendedSection": 1}),
])
def test_terminal_policies_preserve_start_pins_and_append_to_taskbar(desktop, terminal_already_pinned, user_policy):
    run, root = desktop
    pins = [{"desktopAppId": "Microsoft.Windows.Explorer"}]
    if terminal_already_pinned:
        pins.append({"packagedAppId": TERMINAL})
    result = run(START_LAYOUT=json.dumps({"pinnedList": pins}), USER_POLICY=json.dumps(user_policy))
    assert result.returncode == 0, result.stdout + result.stderr
    rows = [json.loads(line) for line in (root / "policy.jsonl").read_text(encoding="utf-8-sig").splitlines()]
    values = {row["name"]: row for row in rows}
    assert values["ConfigureStartPins"]["value"] == 1
    assert values["ConfigureStartPins"]["type"] == "DWord"
    assert values["ConfigureStartPinsJSON"]["type"] == "ExpandString"
    start = json.loads(Path(values["ConfigureStartPinsJSON"]["value"]).read_text(encoding="utf-8-sig"))
    assert start["pinnedList"] == [{"desktopAppId": "Microsoft.Windows.Explorer"}, {"packagedAppId": TERMINAL}]
    assert start["applyOnce"] is True
    taskbar = ET.parse(values["StartLayoutFile"]["value"])
    collection = taskbar.find("{*}CustomTaskbarLayoutCollection")
    assert collection is not None
    assert "PinListPlacement" not in collection.attrib
    assert taskbar.find(".//{*}UWA").attrib == {"AppUserModelID": TERMINAL, "PinGeneration": "1"}
    assert values["LockedStartLayout"]["value"] == 1
    assert "Explorer refreshed without signing out" in result.stdout
    assert not list(root.glob("PREL35-start-*.json"))


@pytest.mark.parametrize("build,revision", [("22631", "1"), ("26100", "4769")])
def test_taskbar_only_works_on_older_windows_without_reading_or_changing_start(desktop, build, revision):
    run, root = desktop
    start_policy = json.dumps({"ConfigureStartPins": 1, "ConfigureStartPinsJSON": "C:\\existing.json"})
    result = run(TASKBAR_ONLY="1", WINDOWS_BUILD=build, WINDOWS_REVISION=revision,
                 FAIL_EXPORT="1", MACHINE_POLICY=start_policy, USER_POLICY=start_policy)
    assert result.returncode == 0, result.stdout + result.stderr
    rows = [json.loads(line) for line in (root / "policy.jsonl").read_text(encoding="utf-8-sig").splitlines()]
    values = {row["name"]: row for row in rows}
    assert set(values) == {"StartLayoutFile", "LockedStartLayout"}
    taskbar_file = Path(values["StartLayoutFile"]["value"])
    taskbar = ET.parse(taskbar_file)
    assert taskbar.find(".//{*}UWA").attrib == {"AppUserModelID": TERMINAL}
    assert "PinListPlacement" not in taskbar.find("{*}CustomTaskbarLayoutCollection").attrib
    assert not (taskbar_file.parent / "StartLayout.json").exists()
    assert not list(root.glob("PREL35-start-*.json"))
    assert "Start pins were not changed" in result.stdout


def test_taskbar_only_still_refuses_conflicting_taskbar_policy(desktop):
    run, root = desktop
    result = run(TASKBAR_ONLY="1", WINDOWS_BUILD="22631",
                 USER_POLICY='{"StartLayoutFile": "C:\\\\existing.xml"}')
    assert result.returncode != 0
    assert "would be overwritten" in result.stderr
    assert not (root / "policy.jsonl").exists()
    assert not (root / "local").exists()
    assert (root / "redirected desktop/Windows Terminal.lnk").exists()


def test_taskbar_only_preview_does_not_export_start_or_change_settings(desktop):
    run, root = desktop
    result = run(TASKBAR_ONLY="1", WINDOWS_BUILD="22631", FAIL_EXPORT="1", PREVIEW="1")
    assert result.returncode == 0, result.stdout + result.stderr
    assert "What if:" in result.stdout
    assert not (root / "policy.jsonl").exists()
    assert not (root / "local").exists()
    assert not (root / "stop.txt").exists()


@pytest.mark.parametrize("auto_restart", ["0", "1"])
def test_shell_refresh_targets_only_the_desktop_explorer_and_keeps_session_signed_in(desktop, auto_restart):
    run, root = desktop
    result = run(TASKBAR_ONLY="1", AUTO_RESTART=auto_restart)
    assert result.returncode == 0, result.stdout + result.stderr
    assert (root / "stop.txt").read_text() == "424242"
    assert (root / "start.txt").exists() is (auto_restart == "0")
    if auto_restart == "0":
        assert Path((root / "start.txt").read_text()).name == "explorer.exe"
    assert "Explorer refreshed without signing out" in result.stdout
    assert "pin appearance has not been automatically verified" in result.stdout


@pytest.mark.parametrize("extra,message", [
    ({"DESKTOP_AVAILABLE": "0"}, "interactive desktop session"),
    ({"SHELL_PROCESS_NAME": "notepad"}, "not Windows Explorer"),
    ({"SHELL_SESSION_OFFSET": "1"}, "not Windows Explorer"),
])
def test_missing_or_unrelated_shell_is_never_stopped(desktop, extra, message):
    run, root = desktop
    result = run(**extra)
    assert result.returncode == 0, result.stdout + result.stderr
    assert message in result.stdout
    assert "Explorer refresh skipped" in result.stdout
    assert "pin appearance is unverified" in result.stdout
    assert (root / "redirected desktop/Windows Terminal.lnk").exists()
    assert not (root / "stop.txt").exists()
    assert not (root / "start.txt").exists()
    assert (root / "policy.jsonl").exists()
    assert "Explorer refreshed" not in result.stdout


def test_noninteractive_vm_setup_creates_shortcut_and_cleans_recycle_bin(desktop):
    run, root = desktop
    result = run("setup_vm", DESKTOP_AVAILABLE="0", FAIL_EXPORT="1")
    assert result.returncode == 0, result.stdout + result.stderr
    assert (root / "redirected desktop/Windows Terminal.lnk").exists()
    assert (root / "policy.jsonl").exists()
    assert (root / "clear.txt").exists()
    assert not (root / "stop.txt").exists()
    assert "Explorer refresh skipped" in result.stdout
    assert "Start pinning was not confirmed" in result.stdout


@pytest.mark.parametrize("extra,message", [
    ({"FAIL_SHELL_WAIT": "1"}, "Fixture shell wait failure"),
    ({"FAIL_SHELL_START": "1"}, "Fixture Explorer start failure"),
    ({"SHELL_NEVER_READY": "1"}, "did not restore the desktop and taskbar"),
])
def test_shell_refresh_failures_are_reported_without_claiming_application(desktop, extra, message):
    run, root = desktop
    result = run(**extra)
    assert result.returncode != 0
    assert message in result.stderr
    assert "Explorer refreshed without signing out" not in result.stdout
    if "FAIL_SHELL_WAIT" in extra:
        assert (root / "start.txt").exists()


@pytest.mark.parametrize("extra,message", [
    ({"WINDOWS_BUILD": "22631"}, "Start pinning needs"),
    ({"WINDOWS_REVISION": "4769"}, "Start pinning needs"),
    ({"MACHINE_POLICY": '{"ConfigureStartPins": 1}'}, "machine-wide Start pin policy"),
    ({"USER_POLICY": '{"ConfigureStartPinsJSON": "C:\\\\existing.json"}'}, "left unchanged"),
    ({"FAIL_EXPORT": "1"}, "Fixture export failure"),
    ({"START_LAYOUT": '{"pinnedList": "invalid"}'}, "pinnedList array"),
    ({"FAIL_START_POLICY": "1"}, "Fixture Start policy failure"),
])
def test_start_failures_warn_without_failing_taskbar_setup(desktop, extra, message):
    run, root = desktop
    result = run(**extra)
    assert result.returncode == 0, result.stdout + result.stderr
    assert message in result.stdout + result.stderr
    assert "Start pinning was not confirmed" in result.stdout + result.stderr
    assert "Start pin layout configured" not in result.stdout
    rows = [json.loads(line) for line in (root / "policy.jsonl").read_text(encoding="utf-8-sig").splitlines()]
    values = {row["name"]: row for row in rows}
    assert set(values) == {"StartLayoutFile", "LockedStartLayout"}
    assert ET.parse(values["StartLayoutFile"]["value"]).find(".//{*}UWA").get("AppUserModelID") == TERMINAL
    assert not list(root.glob("PREL35-start-*.json"))


def test_taskbar_errors_still_fail_without_claiming_success(desktop):
    run, _ = desktop
    result = run(FAIL_TASKBAR_POLICY="1")
    assert result.returncode != 0
    assert "Fixture taskbar policy failure" in result.stderr
    assert "pin layout configured" not in result.stdout
    assert "Start pinning was not confirmed" not in result.stdout + result.stderr


def test_start_export_cleanup_failure_warns_without_failing_the_action(desktop):
    run, root = desktop
    result = run(FAIL_EXPORT_CLEANUP="1")
    assert result.returncode == 0, result.stdout + result.stderr
    assert "Could not remove temporary Start export" in result.stdout + result.stderr
    assert "Fixture export cleanup failure" in result.stdout + result.stderr
    assert "taskbar pin layout configured" in result.stdout
    assert len(list(root.glob("PREL35-start-*.json"))) == 1


@pytest.mark.parametrize("extra,message", [
    ({"WINDOWS_BUILD": "19045"}, "requires Windows 11"),
    ({"TERMINAL_PRESENT": "0"}, "not registered for LabUser"),
    ({"MACHINE_POLICY": '{"StartLayoutFile": "C:\\\\existing.xml"}'}, "machine-wide"),
    ({"USER_POLICY": '{"StartLayoutFile": "C:\\\\existing.xml"}'}, "would be overwritten"),
])
def test_terminal_setup_fails_without_changing_policies_or_layouts(desktop, extra, message):
    run, root = desktop
    result = run(**extra)
    assert result.returncode != 0
    assert message in result.stderr
    assert not (root / "policy.jsonl").exists()
    assert not (root / "local").exists()
    assert not list(root.glob("PREL35-start-*.json"))
