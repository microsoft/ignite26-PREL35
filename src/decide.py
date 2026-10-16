"""Human in the loop: the agents recommend, a named analyst decides, and the decision is recorded.

Nothing happens to a case until the analyst decides. The analyst is identified by their Entra ID sign-in, not by
what they type, and every decision is appended to evidence/decisions.jsonl.

Usage: python src/decide.py ALERT_ID
"""
import base64
import json
import sys
from datetime import datetime

from azure.ai.projects import AIProjectClient

from evaluate import parse, published
from investigate import ROOT, cases, credential, env, investigate

DECISIONS = ROOT / "evidence" / "decisions.jsonl"
DISPOSITIONS = ("escalate", "close_false_positive", "request_info")


def claims(token: str) -> dict:
    payload = token.split(".")[1]
    return json.loads(base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4)))


def whoami() -> str:
    """The signed-in person, from their Entra ID token."""
    c = claims(credential.get_token("https://ai.azure.com/.default").token)
    return c.get("upn") or c.get("unique_name") or c.get("preferred_username") or c["oid"]


def ask_until(question: str, valid) -> str:
    while (answer := input(question).strip()) not in valid:
        print(f"Answer one of: {', '.join(valid)}")
    return answer


# Self-check of the token parsing, with no calls to Azure.
_token = "x." + base64.urlsafe_b64encode(json.dumps({"upn": "ana@contoso.example"}).encode()).decode().rstrip("=") + ".y"
assert claims(_token)["upn"] == "ana@contoso.example"

if __name__ == "__main__":
    alert_id = sys.argv[1] if len(sys.argv) > 1 else sys.exit("Usage: python src/decide.py ALERT_ID")
    if alert_id not in cases:
        sys.exit(f"Unknown alert {alert_id}. Alerts: {', '.join(cases)}")
    analyst = whoami()
    agents = published(AIProjectClient(endpoint=env["FOUNDRY_PROJECT_ENDPOINT"], credential=credential))

    print(f"{alert_id}: {cases[alert_id]['alert']['title']} ({cases[alert_id]['customer']['name']})\n")
    result = investigate(alert_id)
    triage, review = parse(result["triage"]), parse(result["review"])
    print(f"Triage recommends: {triage.get('disposition', 'no answer')} (priority {triage.get('priority', '-')})")
    for reason in triage.get("reasons", []):
        print(f"  - {reason}")
    print(f"Reviewer: {review.get('verdict', 'no answer')}")
    for issue in review.get("issues", []):
        print(f"  - {issue}")

    print(f"\nNothing happens to this case until you decide. You are signed in as {analyst}.")
    approved = ask_until("Approve the recommendation? [y/n] ", ("y", "n")) == "y"
    disposition = triage.get("disposition") if approved else ask_until(f"Your decision [{'/'.join(DISPOSITIONS)}]: ", DISPOSITIONS)
    # Overruling the agents, or going against the reviewer, needs a reason that an auditor can read later.
    needs_reason = not approved or review.get("verdict") != "agree"
    reason = input("Reason" + (" (required): " if needs_reason else " (optional): ")).strip()
    while needs_reason and not reason:
        reason = input("A reason is required: ").strip()

    decision = {
        "alert": alert_id,
        "decided": datetime.now().isoformat(timespec="seconds"),
        "analyst": analyst,
        "recommendation": triage.get("disposition"),
        "reviewer": review.get("verdict"),
        "decision": disposition,
        "approved_recommendation": approved,
        "reason": reason,
        "agents": agents,
    }
    DECISIONS.parent.mkdir(exist_ok=True)
    with DECISIONS.open("a", encoding="utf-8") as f:
        f.write(json.dumps(decision) + "\n")
    print(f"\nRecorded: {alert_id} {disposition}, decided by {analyst}. See {DECISIONS.relative_to(ROOT)}.")
