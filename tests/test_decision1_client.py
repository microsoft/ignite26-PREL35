import copy
import urllib.error
from unittest.mock import patch

import pytest

from src.decisions.decision1_client import DecisionClient, read_answer
from src.decisions.evaluate_decisions import score


def answer(choice="escalate"):
    return {"type": "choice", "choice": choice, "confidence": .8,
            "probabilities": {"escalate": .8, "request_info": .1, "close_false_positive": .1}}


@pytest.mark.parametrize("change", [
    {"choice": "invented"}, {"confidence": True}, {"confidence": float("nan")},
    {"probabilities": {"escalate": 1}}, {"type": "score"},
])
def test_invalid_typed_decisions_are_not_business_labels(change):
    with pytest.raises(ValueError):
        read_answer({**answer(), **change})


def test_isolated_calls_never_share_case_state_and_preserve_order():
    cases = [{"alert": {"id": f"ALT-{i}"}, "transactions": []} for i in range(3)]
    original = copy.deepcopy(cases)
    calls = []

    def post(body):
        calls.append(body)
        return {"answers": {"disposition": answer()}}

    client = DecisionClient("https://fixture", "decision-1", "Rubric", "not-a-real-token")
    with patch.object(client, "_post", side_effect=post):
        result = client.classify(cases, workers=2)
    assert cases == original
    assert [row["alert_id"] for row in result["rows"]] == ["ALT-0", "ALT-1", "ALT-2"]
    assert len(calls) == 3 and all(set(call["state"]) == {"alert", "transactions"} for call in calls)
    assert all(set(call["questions"]) == {"disposition"} for call in calls)


def test_shared_state_requires_one_correlated_named_answer_per_case():
    cases = [{"alert": {"id": "ALT-1"}}, {"alert": {"id": "ALT-2"}}]
    client = DecisionClient("https://fixture", "decision-1", "Rubric", "not-a-real-token")
    with patch.object(client, "_post", return_value={"answers": {"ALT-1": answer()}}):
        result = client.classify(cases, shared=True)
    assert len(result["rows"]) == 2
    assert all(row["status"] == "unavailable" and row["disposition"] is None for row in result["rows"])
    with patch.object(client, "_post", return_value={"answers": {c["alert"]["id"]: answer() for c in cases}}) as post:
        result = client.classify(cases, shared=True)
    body = post.call_args.args[0]
    assert body["state"] == {"cases": cases}
    assert "state.cases[1]" in body["questions"]["ALT-2"]["instructions"]
    assert [r["alert_id"] for r in result["rows"]] == ["ALT-1", "ALT-2"]


def test_upstream_errors_are_explicit_and_never_request_info():
    client = DecisionClient("https://fixture", "decision-1", "Rubric", "not-a-real-token")
    error = urllib.error.HTTPError("https://fixture", 429, "rate limited", {}, None)
    with patch.object(client, "_post", side_effect=error):
        result = client.classify([{"alert": {"id": "ALT-1"}}])
    assert result["rows"] == [{"alert_id": "ALT-1", "status": "unavailable",
                              "disposition": None, "error": "http_429"}]


def test_scoring_keeps_errors_in_the_denominator_and_rejects_missing_rows():
    golden = [{"alert_id": "ALT-1", "expected_disposition": "escalate"},
              {"alert_id": "ALT-2", "expected_disposition": "request_info"}]
    rows = [{"alert_id": "ALT-1", "status": "unavailable", "disposition": None},
            {"alert_id": "ALT-2", "status": "ok", "disposition": "request_info"}]
    metrics = score(rows, golden)
    assert metrics["accuracy"] == .5 and metrics["escalation_recall"] == 0 and metrics["unavailable"] == 1
    with pytest.raises(ValueError):
        score(rows[:1], golden)


def test_rate_limited_calls_back_off_then_succeed_but_other_errors_do_not_retry():
    client = DecisionClient("https://fixture", "decision-1", "Rubric", "not-a-real-token", retries=2)
    limited = urllib.error.HTTPError("https://fixture", 429, "Too Many Requests", {}, None)
    ok = type("Response", (), {"__enter__": lambda s: s, "__exit__": lambda s, *a: None,
                                "read": lambda s: b'{"model": "microsoft-decision-1"}'})()
    with patch("src.decisions.decision1_client.time.sleep") as sleep, \
            patch("urllib.request.urlopen", side_effect=[limited, ok]) as opener:
        assert client._post({})["model"] == "microsoft-decision-1"
    assert opener.call_count == 2 and sleep.call_count == 1
    with patch("src.decisions.decision1_client.time.sleep"), \
            patch("urllib.request.urlopen", side_effect=urllib.error.HTTPError("u", 500, "x", {}, None)) as opener:
        with pytest.raises(urllib.error.HTTPError):
            client._post({})
    assert opener.call_count == 1
