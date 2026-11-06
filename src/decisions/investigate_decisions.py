"""Investigate alerts with Decision-1 triage, then Decision-1 review of each recommendation. No GPT.

Usage: uv run python src/decisions/investigate_decisions.py [ALERT_ID ...] [--challenge] [--workers 8]
  --challenge  also give the reviewer every wrong disposition for each alert; it should disagree with all of them
"""
import argparse
import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

from azure.identity import AzureCliCredential

ROOT = Path(__file__).resolve().parents[2]
if __package__ in (None, ""):
    sys.path.insert(0, str(ROOT))
    sys.path.insert(0, str(ROOT / "src"))
from src.decisions.decision1_client import CRITERIA, DecisionClient
from scripts.console_output import color
from src.decisions.compare_models import price_decision
from src.decisions.decision1_reviewer import Decision1Reviewer
from src.decisions.display import DISPOSITION_COLORS, heading, mark, paint, print_steps, usd

TRIAGE_PROMPT = ROOT / "src/decisions/prompts/decision1.md"


def wrong_dispositions(expected):
    return [label for label in CRITERIA if label != expected]


def run_pipeline(platform, triage, reviewer, alert_ids, policy, workers, golden=None):
    """Triage every alert, then review each recommendation. A failed triage call has nothing to review."""
    triage_cases = [json.loads(platform.case_file(platform.cases[a], "triage", policy)) for a in alert_ids]
    review_cases = [json.loads(platform.case_file(platform.cases[a], "reviewer", policy)) for a in alert_ids]
    started = time.perf_counter()
    triaged = triage.classify(triage_cases, workers=workers)
    recommendations = [{"disposition": row["disposition"]} if row["status"] == "ok" else None for row in triaged["rows"]]
    runnable = [(review_cases[i], rec) for i, rec in enumerate(recommendations) if rec]
    reviewed = iter(reviewer.review_many(runnable, workers=workers))
    rows = []
    for i, alert_id in enumerate(alert_ids):
        review = next(reviewed) if recommendations[i] else {
            "alert_id": alert_id, "status": "unavailable", "verdict": None, "error": "no_recommendation"}
        rows.append({"alert_id": alert_id, "title": platform.cases[alert_id]["alert"]["title"],
                     "expected": (golden or {}).get(alert_id), "triage": triaged["rows"][i], "review": review,
                     "triage_seconds": triaged["requests"][i]["seconds"],
                     "triage_tokens": (triaged["requests"][i].get("usage") or {}).get("input_tokens", 0)})
    input_tokens = sum((q.get("usage") or {}).get("input_tokens", 0) for q in triaged["requests"]) + sum(
        ((r["review"].get("usage") or {}).get("input_tokens", 0)) for r in rows)
    return {"rows": rows, "review_cases": review_cases, "triage": triaged, "input_tokens": input_tokens,
            "seconds": time.perf_counter() - started}


def summarize(rows, challenges=()):
    ok = [r for r in rows if r["triage"]["status"] == "ok" and r["review"]["status"] == "ok"]
    summary = {
        "alerts": len(rows),
        "triage_correct": sum(r["triage"]["status"] == "ok" and r["triage"]["disposition"] == r["expected"] for r in rows),
        "reviewer_agrees": sum(r["review"]["verdict"] == "agree" for r in ok),
        "reviewer_disagrees": sum(r["review"]["verdict"] == "disagree" for r in ok),
        "unavailable": len(rows) - len(ok),
    }
    if challenges:
        summary["challenges"] = len(challenges)
        summary["challenges_caught"] = sum(c["review"]["verdict"] == "disagree" for c in challenges)
        summary["challenges_unavailable"] = sum(c["review"]["status"] != "ok" for c in challenges)
    return summary


def show(rows, challenges, summary, result, workers, challenge_seconds, challenge_tokens):
    """Readable output: one line per alert, what was called with time and cost, then the totals."""
    heading("Decision-1 investigation")
    print(f"{'Alert':9} {'Decision-1 triage':27} {'Reviewer':18} {'Time':>6} {'Cost':>9}  Expected")
    for row in rows:
        t, r = row["triage"], row["review"]
        triage_text = f"{t['disposition']} ({t['confidence']:.0%})" if t["status"] == "ok" else f"unavailable ({t['error']})"
        review_text = f"{r['verdict']} ({r['confidence']:.0%})" if r["status"] == "ok" else f"unavailable ({r['error']})"
        seconds = row["triage_seconds"] + (r.get("seconds") or 0)
        cost = price_decision(row["triage_tokens"] + ((r.get("usage") or {}).get("input_tokens", 0)))
        right = row["expected"] and t.get("disposition") == row["expected"]
        print(f"{row['alert_id']:9} "
              f"{paint(triage_text, DISPOSITION_COLORS.get(t.get('disposition')), 27)} "
              f"{paint(review_text, {'agree': 'green', 'disagree': 'red'}.get(r.get('verdict')), 18)} "
              f"{seconds:>5.1f}s {usd(cost):>9}  {row['expected'] or '-'} {mark(right) if row['expected'] else ''}")
    for c in challenges:
        r = c["review"]
        if r["verdict"] != "disagree":
            shown = f"{r['verdict']} ({r['confidence']:.0%})" if r["status"] == "ok" else f"unavailable ({r['error']})"
            print(color(f"  Challenge missed: {c['alert_id']} was given {c['recommended']} (expected {c['expected']}); "
                        f"reviewer {shown}", "red"))

    review_tokens = sum((r["review"].get("usage") or {}).get("input_tokens", 0) for r in rows)
    triage_tokens = sum(r["triage_tokens"] for r in rows)
    count = len(rows)
    steps = [{"what": f"Decision-1 triages {count} alert{'s' * (count != 1)}, {min(workers, count)} in parallel",
              "seconds": result["triage"]["seconds"], "cost_usd": price_decision(triage_tokens), "model": "decision-1"},
             {"what": "Decision-1 reviews each recommendation",
              "seconds": max(result["seconds"] - result["triage"]["seconds"], 0),
              "cost_usd": price_decision(review_tokens), "model": "decision-1"}]
    if challenges:
        steps.append({"what": f"Reviewer is challenged with {len(challenges)} wrong dispositions",
                      "seconds": challenge_seconds, "cost_usd": price_decision(challenge_tokens), "model": "decision-1"})
    print_steps(steps)
    print(f"  No GPT calls. About {usd(sum(s['cost_usd'] for s in steps) / count)} per alert.")

    heading("Result")
    good = summary["triage_correct"] == summary["alerts"]
    print(f"  Triage matches the expected disposition: {color(str(summary['triage_correct']), 'green' if good else 'red')}"
          f"/{summary['alerts']}")
    print(f"  Reviewer agrees: {color(str(summary['reviewer_agrees']), 'green')}, "
          f"disagrees: {color(str(summary['reviewer_disagrees']), 'red' if summary['reviewer_disagrees'] else 'green')}, "
          f"unavailable: {color(str(summary['unavailable']), 'red' if summary['unavailable'] else 'green')}")
    if challenges:
        caught = summary["challenges_caught"] == summary["challenges"]
        print(f"  Wrong dispositions caught by the reviewer: "
              f"{color(str(summary['challenges_caught']), 'green' if caught else 'red')}/{summary['challenges']}")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("alert_ids", nargs="*", help="Alerts to investigate; all by default.")
    parser.add_argument("--challenge", action="store_true")
    parser.add_argument("--workers", type=int, default=8)
    parser.add_argument("--tenant-id", default=os.environ.get("AZURE_TENANT_ID"))
    args = parser.parse_args(argv)
    if not 1 <= args.workers <= 16:
        parser.error("workers must be 1..16.")
    import investigate as platform
    if set(args.alert_ids) - set(platform.cases):
        parser.error("An alert ID is not in the case data.")
    golden = {r["alert_id"]: r["expected_disposition"] for r in map(
        json.loads, (ROOT / "data/golden.jsonl").read_text(encoding="utf-8").splitlines())}
    alert_ids = args.alert_ids or [a for a in platform.cases if a in golden]
    policy = {**platform.data_policy(), "enforce": True}
    credential = AzureCliCredential(tenant_id=args.tenant_id) if args.tenant_id else platform.credential
    token = credential.get_token("https://cognitiveservices.azure.com/.default").token
    endpoint = platform.env["FOUNDRY_PROJECT_ENDPOINT"].split("/api/projects/")[0]
    triage = DecisionClient(endpoint, "decision-1", TRIAGE_PROMPT.read_text(encoding="utf-8"), token)
    reviewer = Decision1Reviewer(endpoint, "decision-1", token)
    result = run_pipeline(platform, triage, reviewer, alert_ids, policy, args.workers, golden)
    rows, review_cases, triaged = result["rows"], result["review_cases"], result["triage"]

    challenges, challenge_seconds, challenge_tokens = [], 0.0, 0
    if args.challenge:
        pairs = [(row, label) for row in rows if row["expected"] for label in wrong_dispositions(row["expected"])]
        began = time.perf_counter()
        reviews = reviewer.review_many(
            [(review_cases[alert_ids.index(row["alert_id"])], {"disposition": label}) for row, label in pairs],
            workers=args.workers)
        challenge_seconds = time.perf_counter() - began
        challenge_tokens = sum(((r.get("usage") or {}).get("input_tokens", 0)) for r in reviews)
        challenges = [{"alert_id": row["alert_id"], "expected": row["expected"], "recommended": label, "review": review}
                      for (row, label), review in zip(pairs, reviews)]

    summary = summarize(rows, challenges)
    show(rows, challenges, summary, result, args.workers, challenge_seconds, challenge_tokens)

    output = ROOT / "evidence" / "decision1-investigations" / datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S-%f")
    output.mkdir(parents=True)
    report = {"summary": summary, "rows": rows, "challenges": challenges,
              "triage_seconds": triaged["seconds"], "gpt_calls": 0,
              "notes": ["Golden labels are not included in model inputs.",
                        "The reviewer sees the triage disposition but not the triage confidence."]}
    (output / "results.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print("\nEvidence:", output)
    clean = (summary["triage_correct"] == summary["alerts"] and summary["reviewer_agrees"] == summary["alerts"]
             and summary.get("challenges_caught", 0) == summary.get("challenges", 0)
             and not summary["unavailable"] and not summary.get("challenges_unavailable"))
    return 0 if clean else 1


if __name__ == "__main__":
    raise SystemExit(main())
