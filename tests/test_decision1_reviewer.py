import urllib.error
from unittest.mock import patch

import pytest

from src.decisions.decision1_client import DecisionClient
from src.decisions.decision1_reviewer import VERDICTS, Decision1Reviewer, reviewer_instructions
from src.decisions.investigate_decisions import summarize, wrong_dispositions

CASE = {"alert": {"id": "ALT-1"}, "transactions": []}


def verdict(choice="agree", confidence=.9):
    other = "disagree" if choice == "agree" else "agree"
    return {"answers": {"verdict": {"type": "choice", "choice": choice, "confidence": confidence,
                                    "probabilities": {choice: confidence, other: round(1 - confidence, 2)}}},
            "usage": {"input_tokens": 10, "output_tokens": 1}}


def reviewer():
    return Decision1Reviewer("https://fixture", "decision-1", "not-a-real-token", instructions="Rubric")


def test_reviewer_returns_a_verdict_and_confidence_but_no_issues():
    with patch.object(DecisionClient, "_post", return_value=verdict("disagree", .8)) as post:
        result = reviewer().review(CASE, {"disposition": "escalate", "reasons": ["Cash [T-1]."], "confidence": .99})
    assert result["status"] == "ok" and result["verdict"] == "disagree" and result["confidence"] == .8
    assert "issues" not in result
    body = post.call_args.args[0]
    assert set(body["questions"]) == {"verdict"} and body["questions"]["verdict"]["criteria"] == VERDICTS
    # Triage's own confidence must not anchor the reviewer.
    assert body["state"] == {"case": CASE, "triage_recommendation": {
        "disposition": "escalate", "reasons": ["Cash [T-1]."]}}


def test_service_failures_have_no_verdict_and_never_default_to_agree():
    error = urllib.error.HTTPError("https://fixture", 503, "unavailable", {}, None)
    with patch.object(DecisionClient, "_post", side_effect=error):
        result = reviewer().review(CASE, {"disposition": "escalate"})
    assert {k: v for k, v in result.items() if k != "seconds"} == {
        "alert_id": "ALT-1", "status": "unavailable", "verdict": None, "error": "http_503"}
    with patch.object(DecisionClient, "_post", return_value=verdict("escalate")):
        assert reviewer().review(CASE, {"disposition": "escalate"})["verdict"] is None


def test_the_recommendation_must_use_a_triage_label():
    with pytest.raises(ValueError):
        reviewer().review(CASE, {"disposition": "invented"})


def test_review_many_keeps_the_order_given():
    cases = [{"alert": {"id": f"ALT-{i}"}} for i in range(4)]
    with patch.object(DecisionClient, "_post", return_value=verdict()):
        results = reviewer().review_many([(c, {"disposition": "escalate"}) for c in cases], workers=3)
    assert [r["alert_id"] for r in results] == ["ALT-0", "ALT-1", "ALT-2", "ALT-3"]


def test_reviewer_instructions_reuse_the_triage_rules_without_the_triage_task():
    text = reviewer_instructions()
    assert "## Triage rules to apply" in text and "### 1. Escalate an established red flag" in text
    assert "Select one recommendation" not in text


def test_challenges_are_the_wrong_labels_and_the_summary_counts_unavailable_results():
    assert wrong_dispositions("escalate") == ["close_false_positive", "request_info"]
    row = lambda triage, review: {"expected": "escalate", "triage": triage, "review": review}
    ok = {"status": "ok", "disposition": "escalate"}
    rows = [row(ok, {"status": "ok", "verdict": "agree"}),
            row(ok, {"status": "unavailable", "verdict": None}),
            row({"status": "unavailable"}, {"status": "unavailable", "verdict": None})]
    challenges = [{"review": {"status": "ok", "verdict": "disagree"}},
                  {"review": {"status": "ok", "verdict": "agree"}},
                  {"review": {"status": "unavailable", "verdict": None}}]
    assert summarize(rows, challenges) == {
        "alerts": 3, "triage_correct": 2, "reviewer_agrees": 1, "reviewer_disagrees": 0, "unavailable": 2,
        "challenges": 3, "challenges_caught": 1, "challenges_unavailable": 1}
