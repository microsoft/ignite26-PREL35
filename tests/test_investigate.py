import json
from pathlib import Path
import runpy
import sys
from types import ModuleType
from unittest.mock import Mock

import pytest


@pytest.fixture
def investigation(tmp_path, monkeypatch):
    root = Path(__file__).resolve().parents[1]
    for relative in ("src/investigate.py", "data/cases.json", "governance/data-policy.json", "evals/prices.json"):
        path = tmp_path / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes((root / relative).read_bytes())
    (tmp_path / ".env").write_text("APIM_GATEWAY_URL=https://fixture.azure-api.net\n")
    credential = Mock()
    credential.return_value.get_token.return_value.token = "fixture-token"
    client = Mock()
    monkeypatch.setattr("azure.identity.DefaultAzureCredential", credential)
    monkeypatch.setattr("openai.OpenAI", client)
    output = ModuleType("console_output")
    output.color = lambda text, *args, **kwargs: text
    monkeypatch.setitem(sys.modules, "console_output", output)
    module = runpy.run_path(str(tmp_path / "src/investigate.py"), run_name="investigate_under_test")
    return module, client


def test_json_mode_hint_preserves_case_fields_for_gateway_routing(investigation):
    module, client = investigation
    case = {"alert": {"score": 30}, "documents": [], "response_format": "text"}
    module["ask"]("triage", json.dumps(case))
    arguments = client.return_value.responses.create.call_args.kwargs
    request = json.loads(arguments["input"])
    assert request == {**case, "response_format": "json"}
    assert client.call_args.kwargs["default_headers"] == {"x-cost-center": "financial-crime-operations"}


@pytest.mark.parametrize("text", [
    "Case file:\n{}\n\nTriage recommendation:\n{}",
    "Fixture caller attack text",
    '["fixture input"]',
])
def test_non_case_inputs_keep_original_text_and_get_json_mode_hint(investigation, text):
    module, client = investigation
    module["ask"]("reviewer", text)
    assert client.return_value.responses.create.call_args.kwargs["input"] == text + "\n\nResponse format: JSON."
