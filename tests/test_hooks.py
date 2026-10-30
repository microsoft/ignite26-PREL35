import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path
from subprocess import CompletedProcess
from unittest.mock import Mock

import pytest
import yaml

from scripts import common, hooks

ROOT = Path(__file__).resolve().parents[1]


def test_postprovision_reads_all_outputs_before_writing_and_setting_up_data(lab_settings, monkeypatch):
    steps = []

    def read(args):
        assert args[-2:] == ["--environment", "fixture"]
        steps.append(("read", args[3]))
        return CompletedProcess(args, 0, lab_settings[args[3]] + "\n", "")

    def command(args, **kwargs):
        if args[:3] == ["azd", "env", "get-value"]:
            return read(args)
        assert args == ["uv", "run", "create-agents"]
        steps.append(("prompts", args[-1]))

    monkeypatch.setattr(hooks, "command", command)
    refresh = Mock(side_effect=lambda *args, **kwargs: steps.append(("refresh", kwargs["settings"])))
    index = Mock(side_effect=lambda settings: steps.append(("index", settings)))
    monkeypatch.setattr(hooks, "refresh_environment", refresh)
    monkeypatch.setattr(hooks, "setup_search", index)
    hooks.postprovision("fixture", "fixture")
    assert [step[0] for step in steps] == ["read"] * len(hooks.OUTPUT_NAMES) + ["refresh", "prompts", "index"]
    refresh.assert_called_once_with("fixture", "fixture", settings=lab_settings)
    index.assert_called_once_with(lab_settings)


@pytest.mark.parametrize("failure", ["missing-output", "wrong-group", "refresh", "prompts", "index"])
def test_postprovision_stops_at_first_failure(failure, lab_settings, monkeypatch):
    steps = []
    if failure == "wrong-group":
        lab_settings["AZURE_RESOURCE_GROUP"] = "other"

    def command(args, **kwargs):
        if args[0] == "azd":
            value = "" if failure == "missing-output" and args[3] == hooks.OUTPUT_NAMES[-1] else lab_settings[args[3]]
            return CompletedProcess(args, 0, value, "")
        steps.append("prompts")
        if failure == "prompts":
            raise common.SetupError("fixture failure")

    def action(stage):
        def run(*args, **kwargs):
            steps.append(stage)
            if failure == stage:
                raise common.SetupError("fixture failure")
        return run

    monkeypatch.setattr(hooks, "command", command)
    monkeypatch.setattr(hooks, "refresh_environment", action("refresh"))
    monkeypatch.setattr(hooks, "setup_search", action("index"))
    with pytest.raises(common.SetupError):
        hooks.postprovision("fixture", "fixture")
    assert steps == {
        "missing-output": [], "wrong-group": [], "refresh": ["refresh"],
        "prompts": ["refresh", "prompts"], "index": ["refresh", "prompts", "index"],
    }[failure]


def test_azd_only_runs_postprovision_and_uses_platform_launchers():
    config = yaml.safe_load((ROOT / "azure.yaml").read_text())
    assert set(config["hooks"]) == {"postprovision"}
    for platform, shell in (("windows", "pwsh"), ("posix", "sh")):
        hook = config["hooks"]["postprovision"][platform]
        assert hook["shell"] == shell
        assert hook["run"].splitlines()[0] == "uv run lab-hook postprovision"
    output_names = re.findall(r"^output\s+(\w+)\s", (ROOT / "infra/main.bicep").read_text(), re.MULTILINE)
    assert set(hooks.OUTPUT_NAMES) == set(output_names)


@pytest.mark.parametrize("platform,shell", [("windows", "pwsh"), ("posix", "sh")])
@pytest.mark.parametrize("exit_code", [0, 19])
@pytest.mark.parametrize("runner", ["shell", "azd"])
def test_hook_launchers_run_python_command_from_project_root_and_propagate_exit_code(
    tmp_path, platform, shell, exit_code, runner,
):
    phase = "postprovision"
    executable = shutil.which(shell)
    if not executable or (platform == "posix" and os.name == "nt"):
        pytest.skip(f"{shell} is not available for this platform")
    azd = shutil.which("azd")
    if runner == "azd" and not azd:
        pytest.skip("azd is not available")
    root = tmp_path / "lab checkout"
    root.mkdir()
    tools = tmp_path / "tools"
    tools.mkdir()
    shim = tools / "uv_stub.py"
    shim.write_text(
        "import json, os, sys\n"
        "print(json.dumps({'args': sys.argv[1:], 'cwd': os.getcwd()}))\n"
        "sys.exit(int(os.environ['HOOK_EXIT_CODE']))\n",
        encoding="utf-8",
    )
    if os.name == "nt":
        (tools / "uv.cmd").write_text(f'@"{sys.executable}" "{shim}" %*\n', encoding="utf-8")
    else:
        launcher = tools / "uv"
        launcher.write_text(f"#!{sys.executable}\n" + shim.read_text(), encoding="utf-8")
        launcher.chmod(0o755)
    hook = yaml.safe_load((ROOT / "azure.yaml").read_text())["hooks"][phase][platform]
    arguments = [executable, "-NoProfile", "-NonInteractive", "-Command"] if shell == "pwsh" else [executable, "-c"]
    command = [*arguments, hook["run"]]
    if runner == "azd":
        (root / "azure.yaml").write_text(yaml.safe_dump({
            "name": "hook-fixture", "hooks": {phase: {platform: hook}},
        }), encoding="utf-8")
        environment = root / ".azure" / "fixture"
        environment.mkdir(parents=True)
        (environment / ".env").write_text("AZURE_ENV_NAME=fixture\n", encoding="utf-8")
        command = [azd, "hooks", "run", phase, "--platform", platform, "--environment", "fixture", "--no-prompt"]
    result = subprocess.run(
        command, cwd=root,
        env={**os.environ, "PATH": str(tools) + os.pathsep + os.environ.get("PATH", ""),
             "HOOK_EXIT_CODE": str(exit_code)},
        capture_output=True, text=True, timeout=30, check=False,
    )
    assert (result.returncode == 0) == (exit_code == 0), result.stderr
    if runner == "shell":
        assert result.returncode == exit_code
    output = next(json.loads(line) for line in result.stdout.splitlines() if line.startswith('{"args":'))
    assert output == {"args": ["run", "lab-hook", phase], "cwd": str(root)}


def test_hook_requires_resource_group_and_returns_nonzero_on_failure(monkeypatch, capsys):
    monkeypatch.delenv("AZURE_RESOURCE_GROUP", raising=False)
    assert hooks.main(["postprovision"]) == 1
    assert "AZURE_RESOURCE_GROUP is required" in capsys.readouterr().err
    monkeypatch.setenv("AZURE_RESOURCE_GROUP", "fixture")
    monkeypatch.setenv("AZURE_ENV_NAME", "chosen")
    action = Mock(side_effect=common.SetupError("fixture failure"))
    monkeypatch.setattr(hooks, "postprovision", action)
    assert hooks.main(["postprovision"]) == 1
    action.assert_called_once_with("fixture", "chosen")
