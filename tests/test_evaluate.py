import importlib.util
import json
from pathlib import Path
import runpy
import sys
from types import ModuleType
from unittest.mock import Mock

import openai
import pytest


@pytest.fixture
def evaluation(monkeypatch):
    root = Path(__file__).resolve().parents[1]
    platform = ModuleType("investigate")
    platform.ROOT = root
    platform.data = json.loads((root / "data/cases.json").read_text())
    platform.cases = {case["alert"]["id"]: case for case in platform.data["cases"]}
    platform.credential = Mock()
    platform.env = {"FOUNDRY_PROJECT_ENDPOINT": "https://fixture.invalid/api/projects/fincrime"}
    platform.investigate = Mock()
    platform.case_file = Mock()
    platform.policy_hash = Mock()
    output = ModuleType("console_output")
    output.color = lambda text, *args, **kwargs: text
    monkeypatch.setitem(sys.modules, "investigate", platform)
    monkeypatch.setitem(sys.modules, "console_output", output)
    spec = importlib.util.spec_from_file_location("evaluation_under_test", root / "src/evaluate.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def bad_request(code):
    return openai.BadRequestError(
        "Fixture rejection",
        response=Mock(status_code=400, request=Mock(), headers={}),
        body={"code": code, "message": "Fixture rejection"},
    )


def test_prompt_shields_block_is_a_failed_golden_result_not_an_exception(evaluation):
    evaluation.investigate.side_effect = bad_request("prompt_attack_detected")
    result = evaluation.run_one({"alert_id": "ALT-1008", "expected_disposition": "escalate"})
    assert result["blocked_by"] == "prompt_attack_detected"
    assert result["alert_id"] == "ALT-1008"
    assert result["correct"] is False
    assert result["triage"] == result["review"] == ""
    assert evaluation.metrics([result])["citation_validity"] == 0
    assert evaluation.metrics([result])["escalation_recall"] == 0
    assert result["tokens"] == result["cost"] == 0


def test_other_bad_requests_are_not_misclassified_as_prompt_shields(evaluation):
    error = bad_request("invalid_request")
    evaluation.investigate.side_effect = error
    with pytest.raises(openai.BadRequestError) as raised:
        evaluation.run_one({"alert_id": "ALT-1008", "expected_disposition": "escalate"})
    assert raised.value is error


def test_successful_compound_document_citation_keeps_usage_and_score(evaluation):
    evaluation.investigate.return_value = {
        "triage": '{"disposition":"escalate","reasons":["Recruitment evidence [DOC-1008-01]"]}',
        "review": '{"verdict":"agree"}',
        "tokens": 42,
        "cost": 0.001,
        "triage_model": "gpt-5-mini",
    }
    result = evaluation.run_one({"alert_id": "ALT-1008", "expected_disposition": "escalate"})
    assert result["correct"] is True
    assert result["cited"] is True
    assert result["invalid_citations"] == []
    assert result["tokens"] == 42
    assert result["cost"] == 0.001
    assert "blocked_by" not in result


def test_blocked_gate_writes_failed_evidence_pack_and_names_the_alert(evaluation, monkeypatch, tmp_path, capsys):
    root = evaluation.ROOT
    (tmp_path / "data").mkdir()
    (tmp_path / "evals").mkdir()
    (tmp_path / "data/golden.jsonl").write_text(
        '{"alert_id":"ALT-1008","expected_disposition":"escalate"}\n'
    )
    (tmp_path / "evals/thresholds.json").write_bytes((root / "evals/thresholds.json").read_bytes())
    platform = sys.modules["investigate"]
    platform.ROOT = tmp_path
    platform.policy_hash.return_value = "fixture-policy-hash"
    platform.investigate.side_effect = bad_request("prompt_attack_detected")
    client = Mock()
    agent = client.return_value.agents.get.return_value.versions.latest
    agent.version = "1"
    agent.definition.model = "gpt-5-mini"
    monkeypatch.setattr("azure.ai.projects.AIProjectClient", client)
    monkeypatch.setattr("subprocess.run", Mock(return_value=Mock(stdout="fixture-commit")))
    monkeypatch.setattr(sys, "argv", ["evaluate.py"])
    with pytest.raises(SystemExit) as raised:
        runpy.run_path(str(root / "src/evaluate.py"), run_name="__main__")
    assert raised.value.code == 1
    packs = list((tmp_path / "evidence").iterdir())
    assert len(packs) == 1
    manifest = json.loads((packs[0] / "manifest.json").read_text())
    assert manifest["verdict"] == "FAIL"
    assert manifest["blocked_alerts"] == [{"alert_id": "ALT-1008", "by": "prompt_attack_detected"}]
    results = json.loads((packs[0] / "results.json").read_text())
    assert results[0]["blocked_by"] == "prompt_attack_detected"
    report = (packs[0] / "report.md").read_text()
    assert report.startswith("# Release gate: FAIL")
    assert "Prompt Shields blocked legitimate golden alerts: ALT-1008" in report
    assert "Token and cost totals exclude blocked investigations" in report
    output = capsys.readouterr().out
    assert "Prompt Shields blocked golden alerts: ALT-1008" in output
    assert "Evidence Pack:" in output
