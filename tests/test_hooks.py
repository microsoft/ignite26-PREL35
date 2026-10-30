import re
from pathlib import Path
from subprocess import CompletedProcess
from unittest.mock import Mock

import pytest
import yaml

from scripts import common, hooks
from scripts.configure_evidence_agent import configure_evidence

ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize("failure", ["", "validate", "webiq"])
def test_predeploy_validates_before_webiq_and_propagates_failures(failure, monkeypatch):
    steps = []
    settings = {"existing": "resources"}
    monkeypatch.setattr(hooks, "discover_settings", lambda group: settings)

    def validate(*args, **kwargs):
        assert args == ("fixture", "fixture")
        assert kwargs == {"settings": settings, "validate_only": True}
        steps.append("validate")
        if failure == "validate":
            raise common.SetupError("target mismatch")

    def webiq(args):
        assert args == ["--if-needed"]
        steps.append("webiq")
        return int(failure == "webiq")

    monkeypatch.setattr(hooks, "configure_evidence", validate)
    monkeypatch.setattr(hooks.setup_webiq, "main", webiq)
    if failure:
        with pytest.raises(common.SetupError):
            hooks.predeploy("fixture", "fixture")
    else:
        hooks.predeploy("fixture", "fixture")
    assert steps == (["validate"] if failure == "validate" else ["validate", "webiq"])


def test_predeploy_validates_learner_setup_without_arm_deployment_history(
    lab_settings, azd_environment, azure_resources, monkeypatch,
):
    configure_evidence("fixture", settings=lab_settings)
    azd_environment["calls"].clear()
    webiq = Mock(return_value=0)
    monkeypatch.setattr(hooks.setup_webiq, "main", webiq)
    hooks.predeploy("fixture", "fixture")
    webiq.assert_called_once_with(["--if-needed"])
    assert all(args[2] in ("list", "get-value") for args in azd_environment["calls"])
    assert not any(call.args[:2] == ("deployment", "group") for call in azure_resources["read"].call_args_list)


def test_postprovision_reads_all_outputs_before_writing_and_setting_up_data(lab_settings, monkeypatch):
    steps = []

    def read(args):
        assert args[-2:] == ["--environment", "fixture"]
        steps.append(("read", args[3]))
        return CompletedProcess(args, 0, lab_settings[args[3]] + "\n", "")

    def command(args, **kwargs):
        if args[:3] == ["azd", "env", "get-value"]:
            return read(args)
        assert args[0:3] == ["uv", "run", "python"]
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


def test_azd_hooks_call_installed_python_entry_points_and_keep_interactive_input():
    config = yaml.safe_load((ROOT / "azure.yaml").read_text())
    assert config["hooks"]["predeploy"]["interactive"] is True
    for phase in ("predeploy", "postprovision"):
        hook = config["hooks"][phase]
        assert hook["shell"] == "sh"
        assert hook["run"].endswith(f"lab-hook {phase}")
        assert hook["run"].startswith("uv run ")
    output_names = re.findall(r"^output\s+(\w+)\s", (ROOT / "infra/main.bicep").read_text(), re.MULTILINE)
    assert set(hooks.OUTPUT_NAMES) == set(output_names)


def test_hook_requires_resource_group_and_returns_nonzero_on_failure(monkeypatch, capsys):
    monkeypatch.delenv("AZURE_RESOURCE_GROUP", raising=False)
    assert hooks.main(["predeploy"]) == 1
    assert "AZURE_RESOURCE_GROUP is required" in capsys.readouterr().err
    monkeypatch.setenv("AZURE_RESOURCE_GROUP", "fixture")
    monkeypatch.setenv("AZURE_ENV_NAME", "chosen")
    action = Mock(side_effect=common.SetupError("fixture failure"))
    monkeypatch.setattr(hooks, "predeploy", action)
    assert hooks.main(["predeploy"]) == 1
    action.assert_called_once_with("fixture", "chosen")
