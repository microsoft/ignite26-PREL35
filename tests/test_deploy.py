from subprocess import CompletedProcess
from unittest.mock import Mock

import pytest

from scripts import common, deploy

GROUP = {
    "id": "/subscriptions/00000000-0000-0000-0000-000000000001/resourceGroups/fixture",
    "location": "eastus2",
}


@pytest.fixture
def deployment_cli(lab_root, monkeypatch):
    state = {"type": "user", "operator": "operator-object-id", "calls": [], "values": {}, "exists": True}

    def az_json(*args):
        state["calls"].append(args)
        if args[:2] == ("group", "show"):
            return GROUP
        if args == ("account", "show"):
            return {"user": {"type": state["type"], "name": "operator-app-id"}}
        if args == ("ad", "signed-in-user", "show") or args == ("ad", "sp", "show", "--id", "operator-app-id"):
            return {"id": state["operator"]}
        raise AssertionError(f"Unexpected Azure call: {args}")

    def command(args, **kwargs):
        state["calls"].append(args)
        if args[:3] == ["azd", "env", "list"]:
            output = '[{"Name":"fixture"}]' if state["exists"] else "[]"
        elif args[:3] == ["azd", "env", "get-value"]:
            return CompletedProcess(args, 0, state["values"].get(args[3], ""), "")
        elif args[:3] == ["azd", "env", "set"]:
            state["values"][args[3]] = args[4]
            output = ""
        else:
            assert args[:3] in (
                ["azd", "env", "new"], ["azd", "env", "select"], ["azd", "provision", "--environment"],
            )
            output = ""
        return CompletedProcess(args, 0, output, "")

    monkeypatch.setattr(deploy, "az_json", az_json)
    monkeypatch.setattr(deploy, "command", command)
    monkeypatch.setattr(deploy, "setup_search", Mock(side_effect=AssertionError("azd's hook owns normal Search setup")))
    return state


@pytest.mark.parametrize("account_type", ["user", "servicePrincipal"])
def test_normal_deployment_uses_azd_and_all_investigator_resources(account_type, deployment_cli, lab_root):
    deployment_cli["type"] = account_type
    assert deploy.main([
        "--resource-group", "fixture", "--learner-principal-id", "learner-id",
        "--investigator-search-location", "westus",
    ]) == 0
    values = deployment_cli["values"]
    assert values["AZURE_PRINCIPAL_ID"] == "operator-object-id"
    assert values["LEARNER_PRINCIPAL_ID"] == "learner-id"
    assert values["INVESTIGATOR_SEARCH_LOCATION"] == "westus"
    assert deployment_cli["calls"][-1] == ["azd", "provision", "--environment", "fixture", "--no-prompt"]
    assert not any("deployment" in args for args in deployment_cli["calls"])
    logs = list((lab_root / "logs").glob("deploy-*.log"))
    assert len(logs) == 1
    assert "Deployment completed successfully" in logs[0].read_text()


def test_explicit_operator_skips_identity_lookup_and_creates_environment(deployment_cli):
    deployment_cli["exists"] = False
    deploy.provision("fixture", operator_principal_id="explicit-operator")
    assert ("account", "show") not in deployment_cli["calls"]
    assert deployment_cli["values"]["AZURE_PRINCIPAL_ID"] == "explicit-operator"
    assert any(args[:3] == ["azd", "env", "new"] for args in deployment_cli["calls"])


@pytest.mark.parametrize("failure", ["unknown-type", "empty-operator", "wrong-group", "wrong-subscription"])
def test_deployment_stops_before_writes_when_identity_or_target_is_invalid(failure, deployment_cli):
    if failure == "unknown-type":
        deployment_cli["type"] = "unknown"
    elif failure == "empty-operator":
        deployment_cli["operator"] = ""
    else:
        key = "AZURE_RESOURCE_GROUP" if failure == "wrong-group" else "AZURE_SUBSCRIPTION_ID"
        deployment_cli["values"][key] = "other"
    assert deploy.main(["--resource-group", "fixture"]) == 1
    assert not any(args[:3] == ["azd", "env", "set"] or args[0:2] == ["azd", "provision"]
                   for args in deployment_cli["calls"])


def test_index_repair_never_provisions_writes_settings_or_publishes_agents(lab_root, lab_settings, monkeypatch):
    index = Mock()
    monkeypatch.setattr(deploy, "setup_search", index)
    monkeypatch.setattr(deploy, "deployment_settings", lambda group: lab_settings)
    monkeypatch.setattr(deploy, "command", Mock(side_effect=AssertionError("Repair must not call azd")))
    assert deploy.main(["--resource-group", "fixture", "--index-only"]) == 0
    index.assert_called_once_with(lab_settings)
    assert not (lab_root / ".env").exists()


def test_failed_repair_is_logged_and_returns_nonzero(lab_root, lab_settings, monkeypatch, capsys):
    monkeypatch.setattr(deploy, "deployment_settings", lambda group: lab_settings)
    monkeypatch.setattr(deploy, "setup_search", Mock(side_effect=common.SetupError("Search setup failed")))
    path = lab_root / "nested" / "repair.log"
    assert deploy.main(["--resource-group", "fixture", "--index-only", "--log-path", str(path)]) == 1
    assert "Deployment failed" in path.read_text()
    assert "Search setup failed" in path.read_text()
    assert "Deployment completed successfully" not in path.read_text()
    assert "Search setup failed" in capsys.readouterr().err
    assert common.LOGGER.handlers == []
