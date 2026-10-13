"""Deterministic checks. They use no model, so the same answer always gets the same verdict.

They check the answer's form and its citations; whether the evidence supports the reasoning is left to managed
scoring. Mirrors Checks.cs and Json.cs in the .NET runner.
"""
import json
import re

DISPOSITIONS = ("escalate", "close_false_positive", "request_info")
PRIORITIES = ("critical", "high", "medium", "low")

# A citation is a bracketed group such as [T-00018] or [DOC-201, T-00018]; the IDs inside look like ABC-123.
BRACKETED_GROUP = re.compile(r"\[([^\[\]\r\n]+)\]")
EVIDENCE_ID = re.compile(r"\b[A-Z]+-\d+\b")


def text(value):
    """The value if it's a string, otherwise an empty string."""
    return value if isinstance(value, str) else ""


def text_list(value):
    """A list of nonempty strings. An empty list stays empty; anything else gives None."""
    if not isinstance(value, list) or not all(isinstance(item, str) and item.strip() for item in value):
        return None
    return value


def check(name, passed, detail):
    """The result of one deterministic check on one agent answer."""
    return {"name": name, "passed": bool(passed), "detail": detail}


def triage(answer, expected, valid):
    """Checks a triage answer.

    Valid JSON, allowed values, an approved disposition, a citation on every reason, only IDs that exist in the
    case, and the evidence the dataset requires.
    """
    checks = []
    body = parse_object(answer, checks)
    if body is None:
        return checks

    disposition = text(body.get("disposition"))
    reasons = text_list(body.get("reasons"))
    checks.append(check("disposition", disposition in DISPOSITIONS, "Disposition must be a supported value."))
    checks.append(check("priority", text(body.get("priority")) in PRIORITIES, "Priority must be a supported value."))
    checks.append(check("reasons", bool(reasons), "Reasons must be a nonempty array of nonempty strings."))
    checks.append(check("expected_disposition", disposition in expected["accepted_dispositions"],
                        "Disposition must match an approved expectation."))
    if not reasons:
        return checks

    # Collect the IDs cited in each reason, and note whether any reason has none.
    cited = set()
    every_reason_cited = True
    for reason in reasons:
        ids = cited_ids(reason)
        every_reason_cited = every_reason_cited and bool(ids)
        cited.update(ids)
    checks.append(check("citations_present", every_reason_cited,
                        "Every reason must cite at least one evidence ID in brackets."))
    checks.append(check("citations_valid", cited <= valid,
                        "All cited IDs must exist in the supplied case or screening lists."))

    # Each required group lists alternatives: citing any one of them satisfies the group.
    for group in expected["required_citation_groups"]:
        checks.append(check("required_evidence:" + "|".join(group), any(i in cited for i in group),
                            "At least one ID from this approved evidence group must be cited."))
    return checks


def reviewer(answer, expected_verdict=None):
    """Checks a reviewer answer.

    Valid JSON, a verdict that matches its issues, and, for a reviewer challenge, the verdict the dataset expects.
    Without an expected verdict, agreeing with triage proves nothing on its own.
    """
    checks = []
    body = parse_object(answer, checks)
    if body is None:
        return checks

    verdict = text(body.get("verdict"))
    issues = text_list(body.get("issues"))
    checks.append(check("verdict", verdict in ("agree", "disagree"), "Verdict must be agree or disagree."))
    checks.append(check("issues", issues is not None,
                        "Issues must be an array of nonempty strings; an empty array is allowed."))
    checks.append(check("verdict_consistent",
                        issues is not None and (verdict == "agree" and not issues or verdict == "disagree" and issues),
                        "Agreement must have no issues; disagreement must explain at least one issue."))
    if expected_verdict is not None:
        checks.append(check("expected_verdict", verdict == expected_verdict,
                            "Verdict must match the approved challenge expectation."))
    return checks


def valid_ids(node, ids=None):
    """Every "id" value in a case file, including its screening lists: the IDs an answer may cite."""
    ids = set() if ids is None else ids
    if isinstance(node, dict):
        if text(node.get("id")):
            ids.add(node["id"])
        for value in node.values():
            valid_ids(value, ids)
    elif isinstance(node, list):
        for value in node:
            valid_ids(value, ids)
    return ids


def cited_ids(reason):
    """The IDs inside the brackets of a reason, such as [T-00018] or [DOC-201, T-00018]."""
    return [i for group in BRACKETED_GROUP.findall(reason) for i in EVIDENCE_ID.findall(group)]


def parse_object(answer, checks):
    """Parses the answer and records whether it's a JSON object; returns None when it isn't."""
    try:
        body = json.loads(answer)
    except ValueError:
        body = None
    if not isinstance(body, dict):
        body = None
    checks.append(check("json_object", body is not None, "Agent must return a JSON object without Markdown fences."))
    return body
