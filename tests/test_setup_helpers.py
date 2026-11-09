import json
from subprocess import CompletedProcess
from unittest.mock import Mock

import pytest

from scripts import common, console_output, set_gateway, setup_obo
from scripts.build_skillable import OUT, build, convert


@pytest.mark.parametrize("name,value", [
    ("prompt-shields", "on"), ("caller-identity", "obo"), ("model-routing", "off"),
    ("routing-max-score", "5"), ("token-limit", "2000"),
])
def test_gateway_updates_only_requested_switch(name, value, lab_root, monkeypatch):
    (lab_root / ".env").write_text('AZURE_RESOURCE_GROUP="fixture"\nAPIM_NAME=apim-fixture\n')
    update = Mock()
    monkeypatch.setattr(set_gateway, "command", update)
    assert set_gateway.main([name, value]) == 0
    update.assert_called_once_with([
        "az", "apim", "nv", "update", "--resource-group", "fixture", "--service-name", "apim-fixture",
        "--named-value-id", name, "--value", value, "--output", "none",
    ], show=False)


@pytest.mark.parametrize("debug", [False, True])
def test_gateway_reports_azure_failure_only_in_debug_mode(debug, lab_root, monkeypatch, capsys):
    (lab_root / ".env").write_text("AZURE_RESOURCE_GROUP=fixture\nAPIM_NAME=apim-fixture\n")
    diagnostic = "ERROR: (AuthorizationFailed) Cannot update this named value."
    monkeypatch.setattr(common.subprocess, "run", lambda *args, **kwargs: CompletedProcess(
        args[0], 1, "", diagnostic,
    ))
    args = ["prompt-shields", "on"] + (["--debug"] if debug else [])
    assert set_gateway.main(args) == 1
    captured = capsys.readouterr()
    assert (diagnostic in captured.err) is debug
    assert "az apim nv failed (exit code 1)" in captured.err
    assert "The gateway applies it" not in captured.out


@pytest.mark.parametrize("args", [["unknown", "on"], ["token-limit", "-1"], ["model-routing", "on; echo bad"]])
def test_gateway_rejects_bad_inputs_before_azure_calls(args, lab_root, monkeypatch):
    update = Mock()
    monkeypatch.setattr(set_gateway, "command", update)
    with pytest.raises(SystemExit) as error:
        set_gateway.main(args)
    assert error.value.code == 2
    update.assert_not_called()


def test_gateway_requires_lab_settings(lab_root, monkeypatch):
    update = Mock()
    monkeypatch.setattr(set_gateway, "command", update)
    assert set_gateway.main(["prompt-shields", "on"]) == 1
    update.assert_not_called()


def test_obo_sets_scope_pre_authorization_and_keeps_secret_only_in_gateway(lab_root, monkeypatch, capsys):
    path = lab_root / ".env"
    path.write_text("AZURE_RESOURCE_GROUP=fixture\nAPIM_NAME=apim-fixture\nOBO_SCOPE=old\n# keep\n")
    monkeypatch.setattr(setup_obo, "az_json", lambda *args: {"appId": "app-id", "id": "object-id"})
    requests = []

    def run(args):
        requests.append(args)
        return CompletedProcess(args, 0, "test-secret" if "credential" in args else "", "")

    monkeypatch.setattr(setup_obo, "command", run)
    assert setup_obo.main([]) == 0
    patches = [json.loads(args[args.index("--body") + 1]) for args in requests if "--body" in args]
    assert len(patches) == 2
    scope_id = patches[0]["api"]["oauth2PermissionScopes"][0]["id"]
    assert patches[1]["api"]["preAuthorizedApplications"][0]["delegatedPermissionIds"] == [scope_id]
    assert "--secret" in requests[-1] and "test-secret" in requests[-1]
    assert common.read_settings(path)["OBO_SCOPE"] == "api://app-id/access_as_user"
    assert path.read_text().count("OBO_SCOPE=") == 1
    assert "# keep" in path.read_text()
    assert "test-secret" not in path.read_text() + str(capsys.readouterr())


def test_obo_failed_consent_stops_before_secret_or_gateway_updates(lab_root, monkeypatch):
    path = lab_root / ".env"
    before = "AZURE_RESOURCE_GROUP=fixture\nAPIM_NAME=apim-fixture\n"
    path.write_text(before)
    monkeypatch.setattr(setup_obo, "az_json", lambda *args: {"appId": "app-id", "id": "object-id"})
    requests = []

    def run(args):
        requests.append(args)
        if "grant" in args:
            raise common.SetupError("Consent failed")
        return CompletedProcess(args, 0, "", "")

    monkeypatch.setattr(setup_obo, "command", run)
    assert setup_obo.main([]) == 1
    assert not any("credential" in args or "apim" in args for args in requests)
    assert path.read_text() == before


@pytest.mark.parametrize("disabled", ["NO_COLOR", "UV_NO_COLOR", "TERM", "redirected", "enabled"])
def test_console_colors_respect_terminal_and_environment(disabled, monkeypatch):
    for key in ("NO_COLOR", "UV_NO_COLOR", "TERM"):
        monkeypatch.delenv(key, raising=False)
    if disabled == "TERM":
        monkeypatch.setenv("TERM", "dumb")
    elif disabled in ("NO_COLOR", "UV_NO_COLOR"):
        monkeypatch.setenv(disabled, "1")
    stream = Mock()
    stream.isatty.return_value = disabled != "redirected"
    rendered = console_output.color("Ready", "green", stream=stream)
    assert rendered == ("\033[32mReady\033[0m" if disabled == "enabled" else "Ready")


@pytest.mark.parametrize("markdown,expected", [
    ("See [the file](../../a.md) and [docs](https://x.y).", "See the file and [docs](https://x.y)."),
    ("Use +++<lab-username>+++", "Use +++@lab.CloudPortalCredential(User1).Username+++"),
    ("uv run setup-lab --resource-group <your-resource-group>",
     "uv run setup-lab --resource-group @lab.CloudResourceGroup(PREL35).Name"),
    ("![Foundry: Agents](../images/0-4-foundry-agents.png) and [Lab 1](../lab-1/README.md)",
     "!IMAGE[0-4-foundry-agents.png](instructions361796/0-4-foundry-agents.png) and Lab 1"),
])
def test_skillable_guidance_keeps_python_commands_and_replaces_tokens(markdown, expected):
    assert convert(markdown) == expected


def test_skillable_guide_matches_the_lab_readmes():
    built = OUT.read_text(encoding="utf-8").replace("\r\n", "\n")
    assert build() == built, "Run uv run scripts/build_skillable.py after editing a lab README, not content.md"
