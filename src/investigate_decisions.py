"""Investigate alerts with Decision-1 triage, then Decision-1 review of each recommendation. No GPT.

Usage: uv run python src/investigate_decisions.py [ALERT_ID ...] [--challenge] [--workers 8]
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

ROOT = Path(__file__).resolve().parents[1]
if __package__ in (None, ""):
    sys.path.insert(0, str(ROOT))
from src.decision1_client import CRITERIA, DecisionClient
from src.decision1_reviewer import Decision1Reviewer

TRIAGE_PROMPT = ROOT / "src/prompts/decision1.md"


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
                     "triage_seconds": triaged["requests"][i]["seconds"]})
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

    challenges = []
    if args.challenge:
        pairs = [(row, label) for row in rows if row["expected"] for label in wrong_dispositions(row["expected"])]
        reviews = reviewer.review_many(
            [(review_cases[alert_ids.index(row["alert_id"])], {"disposition": label}) for row, label in pairs],
            workers=args.workers)
        challenges = [{"alert_id": row["alert_id"], "expected": row["expected"], "recommended": label, "review": review}
                      for (row, label), review in zip(pairs, reviews)]

    print(f"{'Alert':9} {'Triage':26} {'Reviewer':14} Expected")
    for row in rows:
        t, r = row["triage"], row["review"]
        triage_text = f"{t['disposition']} ({t['confidence']:.2f})" if t["status"] == "ok" else f"unavailable ({t['error']})"
        review_text = f"{r['verdict']} ({r['confidence']:.2f})" if r["status"] == "ok" else f"unavailable ({r['error']})"
        print(f"{row['alert_id']:9} {triage_text:26} {review_text:14} {row['expected'] or '-'}")
    for c in challenges:
        r = c["review"]
        if r["verdict"] != "disagree":
            shown = f"{r['verdict']} ({r['confidence']:.2f})" if r["status"] == "ok" else f"unavailable ({r['error']})"
            print(f"  Challenge missed: {c['alert_id']} was given {c['recommended']} (expected {c['expected']}); reviewer {shown}")
    summary = summarize(rows, challenges)
    print("\n" + ", ".join(f"{key.replace('_', ' ')}: {value}" for key, value in summary.items()))

    output = ROOT / "evidence" / "decision1-investigations" / datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S-%f")
    output.mkdir(parents=True)
    report = {"summary": summary, "rows": rows, "challenges": challenges,
              "triage_seconds": triaged["seconds"], "gpt_calls": 0,
              "notes": ["Golden labels are not included in model inputs.",
                        "The reviewer sees the triage disposition but not the triage confidence."]}
    (output / "results.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print("Evidence:", output)
    clean = (summary["triage_correct"] == summary["alerts"] and summary["reviewer_agrees"] == summary["alerts"]
             and summary.get("challenges_caught", 0) == summary.get("challenges", 0)
             and not summary["unavailable"] and not summary.get("challenges_unavailable"))
    return 0 if clean else 1


if __name__ == "__main__":
    raise SystemExit(main())
