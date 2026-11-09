from src.decisions.compare_investigations import show_differences


def test_golden_marks_are_separate_from_reviewer_agreement(capsys):
    classic = [{"alert_id": "ALT-1006", "disposition": "request_info", "verdict": "agree"}]
    decision = [{"alert_id": "ALT-1006", "expected": "close_false_positive",
                 "triage": {"disposition": "close_false_positive"}, "review": {"verdict": "agree"}}]

    show_differences(classic, decision)

    output = capsys.readouterr().out
    assert "ALT-1006 | Golden: close_false_positive" in output
    assert "Classic (GPT) request_info \u2717 | Reviewer: agree" in output
    assert "Decision-1    close_false_positive \u2713 | Reviewer: agree" in output


def test_missing_results_are_not_marked_as_wrong_or_agreeing(capsys):
    classic = [{"alert_id": "ALT-1005", "disposition": None, "verdict": None}]
    decision = [{"alert_id": "ALT-1005", "expected": "escalate",
                 "triage": {"disposition": "escalate"}, "review": {"verdict": None}}]

    show_differences(classic, decision)

    output = capsys.readouterr().out
    assert "Classic (GPT) unavailable | Reviewer: unavailable" in output
    assert "Decision-1    escalate \u2713 | Reviewer: unavailable" in output
    assert "unavailable \u2717" not in output
