import json
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from src.decisions.triage_systemone import check_explanation, investigate

CASE = {"alert": {"id": "ALT-1"}, "customer": {"id": "C-1"}, "transactions": [{"id": "T-1"}, {"id": "T-2"}]}
ASSESSMENT = {"alert_id": "ALT-1", "status": "ok", "disposition": "escalate"}


def explanation(**changes):
    return json.dumps({"disposition": "escalate", "reasons": ["Large cash deposit [T-1]"], "gaps": [], "conflicts": [],
                       **changes})


def test_check_explanation_accepts_cited_reasons_including_a_list_in_one_bracket():
    assert check_explanation(explanation(), CASE, ASSESSMENT)["disposition"] == "escalate"
    assert check_explanation(explanation(reasons=["Deposits [T-1, T-2] [C-1]"]), CASE, ASSESSMENT)


@pytest.mark.parametrize("changes,message", [
    ({"disposition": "close_false_positive"}, "changed"),
    ({"reasons": ["No citation"]}, "cite"),
    ({"reasons": ["Wrong case [T-99]"]}, "cite"),
    ({"reasons": []}, "needs reasons"),
])
def test_check_explanation_rejects_changed_or_unsupported_answers(changes, message):
    with pytest.raises(ValueError, match=message):
        check_explanation(explanation(**changes), CASE, ASSESSMENT)


def fixtures(rows, answers):
    platform = Mock(cases={"ALT-1": object()}, cost=Mock(return_value=0.001))
    platform.case_file.return_value = json.dumps(CASE)
    decision = Mock(classify=Mock(return_value={"rows": rows, "requests": [{"usage": {"input_tokens": 1000}}]}))
    reviewer = Mock(review=Mock(return_value={"status": "ok", "verdict": "agree", "confidence": .9, "seconds": .4,
                                              "usage": {"input_tokens": 3000}}))
    responses = [SimpleNamespace(id=f"r{i}", model="gpt-5-mini", output_text=text) for i, text in enumerate(answers)]
    openai = Mock(responses=Mock(create=Mock(side_effect=responses)))
    project = Mock(get_openai_client=Mock(return_value=openai))
    return platform, decision, reviewer, project, openai


def test_decision_then_one_agent_call_then_review_with_timed_steps():
    platform, decision, reviewer, project, openai = fixtures([ASSESSMENT], [explanation()])
    result = investigate(platform, decision, reviewer, project, "ALT-1", {})
    assert [step["what"] for step in result["steps"]] == [
        "Decision-1 chooses the disposition", "Agent writes the reasons", "Decision-1 reviews the reasons"]
    sent = json.loads(openai.responses.create.call_args.kwargs["input"].split("\n\n")[0])
    assert sent["decision_1"]["disposition"] == "escalate" and sent["case"] == CASE
    assert result["total_cost_usd"] == pytest.approx(sum(step["cost_usd"] for step in result["steps"]))
    assert result["review"]["verdict"] == "agree"


def test_one_repair_is_shown_as_its_own_step_and_a_second_failure_stops():
    platform, decision, reviewer, project, openai = fixtures([ASSESSMENT], [explanation(reasons=["No citation"]), explanation()])
    result = investigate(platform, decision, reviewer, project, "ALT-1", {})
    assert "Agent corrects its reasons" in [step["what"] for step in result["steps"]]
    platform, decision, reviewer, project, _ = fixtures([ASSESSMENT], [explanation(reasons=["x"])] * 2)
    with pytest.raises(ValueError, match="cite"):
        investigate(platform, decision, reviewer, project, "ALT-1", {})


def test_unavailable_decision_skips_the_agent_and_review():
    platform, decision, reviewer, project, openai = fixtures(
        [{"alert_id": "ALT-1", "status": "unavailable", "disposition": None, "error": "http_429"}], [])
    result = investigate(platform, decision, reviewer, project, "ALT-1", {})
    assert result["explanation"] is None and len(result["steps"]) == 1
    openai.responses.create.assert_not_called()
    reviewer.review.assert_not_called()
