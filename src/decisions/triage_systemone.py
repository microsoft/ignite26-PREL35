"""Investigate one alert in three steps orchestrated here: Decision-1 chooses, the triage-systemone agent explains, Decision-1 reviews.

Usage: uv run python src/decisions/triage_systemone.py [ALERT_ID] [--tenant-id ID] [--json]

The agent cannot change the disposition. Publish the agent first with
`uv run create-agents`. Nothing else is deployed: Decision-1 is called from this process with your Entra ID token.
"""
import argparse
import json
import os
import re
import sys
import time
from pathlib import Path

from azure.ai.projects import AIProjectClient
from azure.identity import AzureCliCredential

ROOT = Path(__file__).resolve().parents[2]
if __package__ in (None, ""):
    sys.path.insert(0, str(ROOT))
    sys.path.insert(0, str(ROOT / "src"))
from scripts.console_output import color
from src.decisions.compare_models import price_decision
from src.decisions.decision1_client import DecisionClient
from src.decisions.decision1_reviewer import Decision1Reviewer

TRIAGE_PROMPT = ROOT / "src/decisions/prompts/decision1.md"
AGENT = "triage-systemone"
REPAIRS = 1
CITATION = re.compile(r"\[([^\[\]]+)\]")


def cited_ids(reason):
    """Record IDs in square brackets; one bracket may hold several, as [T-1, T-2]."""
    return {part for group in CITATION.findall(reason) for part in re.split(r"[,\s]+", group) if part}


def record_ids(value):
    if isinstance(value, dict):
        return {v for k, v in value.items() if k == "id" and isinstance(v, str)} | {
            item for v in value.values() for item in record_ids(v)}
    if isinstance(value, list):
        return {item for v in value for item in record_ids(v)}
    return set()


def check_explanation(text, case, assessment):
    """The agent's answer must keep Decision-1's disposition, and every reason must cite a record of this case."""
    explanation = json.loads(text)
    if set(explanation) != {"disposition", "reasons", "gaps", "conflicts"}:
        raise ValueError("The answer must have exactly disposition, reasons, gaps and conflicts.")
    if explanation["disposition"] != assessment["disposition"]:
        raise ValueError("The agent changed the Decision-1 disposition.")
    valid = record_ids(case)
    for reason in explanation["reasons"]:
        cited = cited_ids(reason)
        if not cited or cited - valid:
            raise ValueError(f"Each reason must cite a record of this case: {reason}")
    if not explanation["reasons"]:
        raise ValueError("A successful assessment needs reasons.")
    return explanation


def investigate(platform, decision, reviewer, project, alert_id, policy):
    """Decision-1 chooses, the agent explains, Decision-1 reviews. Returns the result record, with its timed steps."""
    case = json.loads(platform.case_file(platform.cases[alert_id], "triage", policy))
    steps = []

    began = time.perf_counter()
    result = decision.classify([case], workers=1)
    row = result["rows"][0]
    assessment = {k: v for k, v in row.items() if k != "error" or v}
    tokens = ((result["requests"][0].get("usage") or {}).get("input_tokens") or 0)
    steps.append({"what": "Decision-1 chooses the disposition", "model": "decision-1",
                  "seconds": time.perf_counter() - began, "cost_usd": price_decision(tokens), "decision": True})
    if assessment["status"] != "ok":
        return finish(alert_id, assessment, None, None, None, steps)

    client = project.get_openai_client()
    reference = {"agent_reference": {"name": AGENT, "type": "agent_reference"}}
    began = time.perf_counter()
    response = client.responses.create(
        input=json.dumps({"case": case, "decision_1": assessment}) + "\n\nReturn JSON.", extra_body=reference)
    steps.append({"what": "Agent writes the reasons", "model": response.model,
                  "seconds": time.perf_counter() - began, "cost_usd": platform.cost(response)})
    for attempt in range(REPAIRS + 1):
        try:
            explanation = check_explanation(response.output_text, case, assessment)
            break
        except ValueError as error:
            if attempt == REPAIRS:
                raise
            began = time.perf_counter()
            response = client.responses.create(
                previous_response_id=response.id, extra_body=reference,
                input=f"Return JSON. Repair only the explanation: {error}. Cite record IDs of this case in square "
                      "brackets. Keep the disposition.")
            steps.append({"what": "Agent corrects its reasons", "model": response.model,
                          "seconds": time.perf_counter() - began, "cost_usd": platform.cost(response)})

    reviewer_case = json.loads(platform.case_file(platform.cases[alert_id], "reviewer", policy))
    review = reviewer.review(reviewer_case, {"disposition": explanation["disposition"], "reasons": explanation["reasons"]})
    if review["status"] == "ok":
        steps.append({"what": "Decision-1 reviews the reasons", "model": "decision-1", "seconds": review["seconds"],
                      "cost_usd": price_decision((review.get("usage") or {}).get("input_tokens") or 0)})
    return finish(alert_id, assessment, explanation, review, response.model, steps)


def finish(alert_id, assessment, explanation, review, agent_model, steps):
    return {"alert_id": alert_id, "assessment": assessment, "explanation": explanation, "review": review,
            "agent_model": agent_model, "steps": steps,
            "total_seconds": sum(step["seconds"] for step in steps),
            "total_cost_usd": sum(step["cost_usd"] for step in steps)}


def report(result):
    """Print what happened in a form a learner can read; --json prints the full record."""
    assessment, explanation, review = result["assessment"], result["explanation"], result["review"]
    if explanation is None:
        print(color(f"{result['alert_id']}: Decision-1 was unavailable ({assessment.get('error', 'unknown error')})", "red"))
        return
    print(color(f"{result['alert_id']}: Decision-1 chooses {assessment['disposition']}", "cyan")
          + (f" ({assessment['confidence']:.0%} confident)" if assessment.get("confidence") is not None else ""))
    if review and review["status"] == "ok":
        verdict = color(review["verdict"], "green" if review["verdict"] == "agree" else "red")
        print(f"Reviewer: {verdict} ({review['confidence']:.0%} confident)")
    print("\nWhat was called, and what it cost")
    for number, step in enumerate(result["steps"], 1):
        print(f"  {number}. {step['what']:<48} {step['seconds']:>5.1f} s  ${step['cost_usd']:.5f}  {step['model']}")
    print(f"     {'Total':<48} {result['total_seconds']:>5.1f} s  ${result['total_cost_usd']:.5f}")
    decide = next((step for step in result["steps"] if step.get("decision")), None)
    if decide:
        print(f"\nThe disposition took {decide['seconds']:.1f} s and ${decide['cost_usd']:.5f} from Decision-1. "
              "The agent is called afterwards only to write the reasons from the records.")
    for title, items in (("Reasons", explanation["reasons"]), ("Gaps", explanation["gaps"]),
                         ("Conflicts", explanation["conflicts"])):
        if items:
            print(f"\n{title}")
            for item in items:
                print(f"  - {item}")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("alert_id", nargs="?", default="ALT-1001")
    parser.add_argument("--tenant-id", default=os.environ.get("AZURE_TENANT_ID"))
    parser.add_argument("--json", action="store_true", help="print the full result record instead of the summary")
    args = parser.parse_args(argv)
    import investigate as platform
    if args.alert_id not in platform.cases:
        parser.error("Unknown alert ID.")
    credential = AzureCliCredential(tenant_id=args.tenant_id) if args.tenant_id else platform.credential
    token = credential.get_token("https://cognitiveservices.azure.com/.default").token
    foundry = platform.env["FOUNDRY_PROJECT_ENDPOINT"]
    decision = DecisionClient(foundry.split("/api/projects/")[0], "decision-1", TRIAGE_PROMPT.read_text(encoding="utf-8"), token)
    reviewer = Decision1Reviewer(foundry.split("/api/projects/")[0], "decision-1", token)
    project = AIProjectClient(endpoint=foundry, credential=credential)
    policy = {**platform.data_policy(), "enforce": True}
    result = investigate(platform, decision, reviewer, project, args.alert_id, policy)
    if args.json:
        print(json.dumps(result, indent=2))
    else:
        report(result)
    print("\nThe agents recommend. A named analyst decides.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
