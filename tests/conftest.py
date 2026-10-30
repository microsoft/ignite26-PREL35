import copy
from subprocess import CompletedProcess
from unittest.mock import Mock

import pytest

from scripts import common, configure_evidence_agent, deploy, env, set_gateway, setup_lab, setup_obo, setup_webiq

GROUP_ID = "/subscriptions/00000000-0000-0000-0000-000000000001/resourceGroups/fixture"


@pytest.fixture
def lab_root(tmp_path, monkeypatch):
    for module in (common, configure_evidence_agent, deploy, env, set_gateway, setup_lab, setup_obo, setup_webiq):
        monkeypatch.setattr(module, "ROOT", tmp_path)
    return tmp_path


@pytest.fixture
def azure_resources(monkeypatch):
    resources = [
        {"name": "foundry-fixture", "id": f"{GROUP_ID}/providers/Microsoft.CognitiveServices/accounts/foundry-fixture",
         "type": "Microsoft.CognitiveServices/accounts", "kind": "AIServices"},
        {"name": "search-fixture", "type": "Microsoft.Search/searchServices", "location": "eastus"},
        {"name": "webiq-fixture", "id": f"{GROUP_ID}/providers/Microsoft.WebIQ/accounts/webiq-fixture",
         "type": "Microsoft.WebIQ/accounts"},
        {"name": "apim-fixture", "id": f"{GROUP_ID}/providers/Microsoft.ApiManagement/service/apim-fixture",
         "type": "Microsoft.ApiManagement/service"},
        {"name": "insights-fixture", "id": f"{GROUP_ID}/providers/Microsoft.Insights/components/insights-fixture",
         "type": "Microsoft.Insights/components"},
    ]
    state = {
        "group": {"name": "fixture", "id": GROUP_ID, "location": "eastus2"},
        "resources": resources,
        "models": [{"name": name} for name in ("gpt-5-mini", "gpt-5-nano", "text-embedding-3-small")],
        "gateway": {"properties": {"gatewayUrl": "https://gateway-fixture.example"}},
        "monitoring": {"properties": {"ConnectionString": "InstrumentationKey=fixture"}},
    }

    def read(*args):
        if args[:2] == ("group", "show"):
            return copy.deepcopy(state["group"])
        if args[:2] == ("resource", "list"):
            return copy.deepcopy(state["resources"])
        if args[:2] == ("resource", "show"):
            resource_id = args[args.index("--ids") + 1]
            if resource_id.endswith("/projects/fincrime"):
                return {}
            if resource_id.endswith("/service/apim-fixture"):
                return copy.deepcopy(state["gateway"])
            if resource_id.endswith("/components/insights-fixture"):
                return copy.deepcopy(state["monitoring"])
        if args[:4] == ("cognitiveservices", "account", "deployment", "list"):
            return copy.deepcopy(state["models"])
        raise AssertionError(f"Unexpected Azure call (learners must never provision or assign roles): {args}")

    state["read"] = Mock(side_effect=read)
    monkeypatch.setattr(setup_lab, "az_json", state["read"])
    monkeypatch.setattr(configure_evidence_agent, "az_json", state["read"])
    return state


@pytest.fixture
def lab_settings(azure_resources):
    return setup_lab.discover_settings("fixture")


@pytest.fixture
def azd_environment(monkeypatch):
    state = {"values": {}, "exists": True, "fail_key": "", "calls": []}

    def run(args, **kwargs):
        state["calls"].append(args)
        prefix = args[:3]
        if prefix == ["azd", "env", "list"]:
            output = '[{"Name":"fixture"}]' if state["exists"] else "[]"
        elif prefix == ["azd", "env", "get-value"]:
            key = args[3]
            if key not in state["values"]:
                return CompletedProcess(args, 1, "", "")
            output = state["values"][key]
        elif prefix == ["azd", "env", "set"]:
            if args[3] == state["fail_key"]:
                raise common.SetupError("Could not save azd setting.")
            state["values"][args[3]] = args[4]
            output = ""
        elif prefix in (["azd", "env", "new"], ["azd", "env", "select"]):
            output = ""
        else:
            raise AssertionError(f"Unexpected azd call: {args}")
        return CompletedProcess(args, 0, output, "Update available: azd fixture notice")

    state["run"] = Mock(side_effect=run)
    monkeypatch.setattr(configure_evidence_agent, "command", state["run"])
    return state
