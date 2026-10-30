import os
from subprocess import CompletedProcess
from unittest.mock import Mock

import pytest

from scripts import common, configure_evidence_agent as evidence, env


@pytest.mark.parametrize("split", [False, True])
def test_environment_merges_single_or_split_deployments_and_normalizes_output_names(split, lab_settings, monkeypatch):
    baseline = {key: {"value": value} for key, value in lab_settings.items() if not key.startswith(("AZURE_SEARCH", "WEBIQ"))}
    baseline["apiM_GATEWAY_URL"] = baseline.pop("APIM_GATEWAY_URL")
    research = {key: {"value": value} for key, value in lab_settings.items() if key.startswith(("AZURE_SEARCH", "WEBIQ"))}
    baseline["INVESTIGATOR_EMBEDDING_DIMENSIONS"] = {"value": 1536}
    outputs = [baseline, research] if split else [{**baseline, **research}]
    read = Mock(return_value=outputs)
    monkeypatch.setattr(env, "az_json", read)
    settings = env.deployment_settings("fixture")
    assert settings == lab_settings
    read.assert_called_once()


def test_environment_uses_latest_complete_outputs_and_ignores_unrelated_deployments(monkeypatch):
    output_sets = [
        {"FOUNDRY_PROJECT_ENDPOINT": {"value": "old"}, "APIM_GATEWAY_URL": {"value": "old"}},
        {"FOUNDRY_PROJECT_ENDPOINT": {"value": "latest"}, "APIM_GATEWAY_URL": {"value": "latest"}},
        {"UNRELATED": {"value": "ignored"}},
    ]
    monkeypatch.setattr(env, "az_json", lambda *args: output_sets)
    assert env.deployment_settings("fixture") == {
        "FOUNDRY_PROJECT_ENDPOINT": "latest", "APIM_GATEWAY_URL": "latest", "AZURE_RESOURCE_GROUP": "fixture",
    }


def test_no_lab_deployment_is_an_error(monkeypatch):
    monkeypatch.setattr(env, "az_json", lambda *args: [])
    with pytest.raises(common.SetupError, match="No lab deployment"):
        env.deployment_settings("fixture")


def test_refresh_preserves_secrets_comments_and_custom_values(lab_root, lab_settings, monkeypatch, capsys):
    path = lab_root / ".env"
    path.write_text(
        '# organizer settings\nWEBIQ_API_KEY="fixture-secret"\nOBO_SCOPE=keep\n'
        'export APIM_GATEWAY_URL=stale\nCOUNT=\nLENGTH=\nCOUNT=custom\n',
        encoding="utf-8-sig",
    )
    configure = Mock()
    monkeypatch.setattr(evidence, "configure_evidence", configure)
    monkeypatch.setattr(env.shutil, "which", lambda name: name)
    env.refresh_environment("fixture", "named-environment", settings=lab_settings)
    saved = path.read_text()
    assert saved.count("APIM_GATEWAY_URL=") == 1
    assert "COUNT=\n" not in saved and "LENGTH=\n" not in saved
    assert "COUNT=custom" in saved and "OBO_SCOPE=keep" in saved
    assert "# organizer settings" in saved and 'WEBIQ_API_KEY="fixture-secret"' in saved
    assert common.read_settings(path)["APIM_GATEWAY_URL"] == "https://gateway-fixture.example"
    configure.assert_called_once_with("fixture", "named-environment", settings=lab_settings)
    output = capsys.readouterr()
    assert "fixture-secret" not in str(output)
    assert "InstrumentationKey=fixture" not in str(output)
    if os.name != "nt":
        assert path.stat().st_mode & 0o077 == 0


def test_settings_are_created_atomically_and_reject_line_injection(lab_root):
    path = lab_root / ".env"
    common.write_settings(path, {"APIM_NAME": "fixture"})
    before = path.read_text()
    with pytest.raises(common.SetupError, match="Invalid value"):
        common.write_settings(path, {"APIM_NAME": "fixture\nWEBIQ_API_KEY=wrong"})
    assert path.read_text() == before
    assert list(lab_root.glob(".webiq-*")) == []


def test_settings_refuse_linked_env(lab_root):
    target = lab_root / "target"
    target.write_text("untouched")
    path = lab_root / ".env"
    try:
        path.symlink_to(target)
    except OSError:
        pytest.skip("Symlink creation is not permitted by this Windows account")
    with pytest.raises(common.SetupError, match="linked"):
        common.write_settings(path, {"APIM_NAME": "fixture"})
    assert target.read_text() == "untouched"


def test_configure_sets_role_free_environment_and_reuses_supplied_outputs(lab_settings, azd_environment, monkeypatch):
    monkeypatch.setattr(evidence, "deployment_settings", Mock(side_effect=AssertionError("Outputs must be reused")))
    evidence.configure_evidence("fixture", settings=lab_settings)
    assert azd_environment["values"]["AZD_AGENT_SKIP_ROLE_ASSIGNMENTS"] == "true"
    assert azd_environment["values"]["AZURE_SEARCH_ENDPOINT"] == "https://search-fixture.search.windows.net"
    assert azd_environment["calls"][-1] == ["azd", "env", "select", "fixture"]


@pytest.mark.parametrize("key", [
    "AZURE_SUBSCRIPTION_ID", "AZURE_RESOURCE_GROUP", "AZURE_AI_PROJECT_ID", "FOUNDRY_PROJECT_ENDPOINT",
])
def test_configure_does_not_overwrite_another_lab(key, lab_settings, azd_environment):
    azd_environment["values"][key] = "another-lab"
    with pytest.raises(common.SetupError, match="different lab"):
        evidence.configure_evidence("fixture", settings=lab_settings)
    assert not any(args[:3] in (["azd", "env", "set"], ["azd", "env", "select"]) for args in azd_environment["calls"])


def test_predeploy_validation_is_read_only_and_detects_stale_values(lab_settings, azd_environment):
    evidence.configure_evidence("fixture", settings=lab_settings)
    azd_environment["calls"].clear()
    evidence.configure_evidence("fixture", settings=lab_settings, validate_only=True)
    assert all(args[2] in ("list", "get-value") for args in azd_environment["calls"])
    azd_environment["values"]["AZURE_AI_MODEL_DEPLOYMENT_NAME"] = "stale"
    with pytest.raises(common.SetupError, match="does not match"):
        evidence.configure_evidence("fixture", settings=lab_settings, validate_only=True)
    assert azd_environment["values"]["AZURE_AI_MODEL_DEPLOYMENT_NAME"] == "stale"


def test_missing_azd_environment_is_created_only_outside_validation(lab_settings, azd_environment):
    azd_environment["exists"] = False
    with pytest.raises(common.SetupError, match="environment is missing"):
        evidence.configure_evidence("fixture", settings=lab_settings, validate_only=True)
    assert len(azd_environment["calls"]) == 1
    evidence.configure_evidence("fixture", settings=lab_settings)
    assert any(args[:3] == ["azd", "env", "new"] for args in azd_environment["calls"])


def test_failed_settings_write_stops_configuration(lab_settings, azd_environment):
    azd_environment["fail_key"] = "AZURE_AI_MODEL_DEPLOYMENT_NAME"
    with pytest.raises(common.SetupError, match="Could not save"):
        evidence.configure_evidence("fixture", settings=lab_settings)
    assert not any(args[:3] == ["azd", "env", "select"] for args in azd_environment["calls"])


def test_native_stderr_notice_does_not_hide_success_or_ignore_failure(monkeypatch, capsys):
    native = Mock(side_effect=[
        CompletedProcess([], 0, "value", "Update available"),
        CompletedProcess([], 7, "", "private credential"),
    ])
    monkeypatch.setattr(common.subprocess, "run", native)
    assert common.command(["azd", "env", "get-value", "key"]).stdout == "value"
    with pytest.raises(common.SetupError, match="exit code 7") as error:
        common.command(["azd", "env", "set", "key", "private credential"])
    assert "private credential" not in str(error.value)
    assert "private credential" not in str(capsys.readouterr())


def test_failed_azure_read_and_missing_tool_are_actionable(monkeypatch):
    monkeypatch.setattr(common.subprocess, "run", Mock(side_effect=FileNotFoundError))
    with pytest.raises(common.SetupError, match="az is not installed"):
        common.az_json("group", "show")
    monkeypatch.setattr(common.subprocess, "run", Mock(return_value=CompletedProcess([], 0, "not json", "")))
    with pytest.raises(common.SetupError, match="valid JSON"):
        common.az_json("group", "show")


def test_command_resolves_windows_cli_launchers_and_keeps_arguments_separate(monkeypatch):
    launcher = r"C:\Program Files\Azure\az.cmd"
    monkeypatch.setattr(common.shutil, "which", lambda name: launcher)
    native = Mock(return_value=CompletedProcess([], 0, "{}", ""))
    monkeypatch.setattr(common.subprocess, "run", native)
    common.az_json("group", "show", "--name", "lab group")
    assert native.call_args.args[0] == [launcher, "group", "show", "--name", "lab group", "--output", "json"]
    assert native.call_args.kwargs.get("shell", False) is False
