"""Compare the classic investigation (GPT triage and reviewer agents) with the Decision-1 investigation.

Usage: uv run python src/compare_investigations.py [ALERT_ID ...] [--workers 4]

Both run every alert through a triage step and then a reviewer step, on the same policy-enforced case files.
The classic run calls the published triage and reviewer agents through the API Management gateway; the Decision-1
run is src/investigate_decisions.py. Golden labels are never sent to either. Evidence is written under
evidence/investigation-comparison/.
"""
import argparse
import json
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from statistics import median

from azure.identity import AzureCliCredential

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "src"))
from src.compare_models import price_decision  # noqa: E402
from src.decision1_client import DecisionClient  # noqa: E402
from src.decision1_reviewer import Decision1Reviewer  # noqa: E402
from src.investigate_decisions import TRIAGE_PROMPT, run_pipeline  # noqa: E402


def run_classic(platform, parse, alert_ids, workers, expected):
    def one(alert_id):
        begin = time.perf_counter()
        try:
            result = platform.investigate(alert_id)
        except Exception as error:  # a failed call counts as unavailable, not a crash
            return {"alert_id": alert_id, "expected": expected.get(alert_id), "status": "unavailable",
                    "disposition": None, "verdict": None, "error": str(error)[:200],
                    "seconds": time.perf_counter() - begin, "tokens": 0, "cost": 0.0}
        triage, review = parse(result["triage"]), parse(result["review"])
        return {"alert_id": alert_id, "expected": expected.get(alert_id), "status": "ok",
                "disposition": triage.get("disposition"), "verdict": review.get("verdict"),
                "issues": review.get("issues", []), "seconds": time.perf_counter() - begin,
                "tokens": result["tokens"], "cost": result["cost"], "triage_model": result["triage_model"]}

    started = time.perf_counter()
    with ThreadPoolExecutor(max_workers=workers) as pool:
        rows = list(pool.map(one, alert_ids))
    return {"rows": rows, "seconds": time.perf_counter() - started}


def classic_metrics(run):
    rows = run["rows"]
    return metrics(rows, run["seconds"], [r["seconds"] for r in rows], sum(r["tokens"] for r in rows),
                   sum(r["cost"] for r in rows))


def decision_metrics(run):
    rows = [{"alert_id": r["alert_id"], "expected": r["expected"],
             "status": "ok" if r["triage"]["status"] == r["review"]["status"] == "ok" else "unavailable",
             "disposition": r["triage"].get("disposition"), "verdict": r["review"].get("verdict")} for r in run["rows"]]
    latencies = [r["triage_seconds"] + r["review"].get("seconds", 0) for r in run["rows"]]
    return metrics(rows, run["seconds"], latencies, run["input_tokens"], price_decision(run["input_tokens"]))


def metrics(rows, wall, latencies, tokens, cost):
    escalations = [r for r in rows if r["expected"] == "escalate"]
    return {
        "alerts": len(rows), "wall_seconds": wall, "median_alert_seconds": median(latencies),
        "tokens_per_alert": tokens / len(rows), "cost_per_alert_usd": cost / len(rows),
        "triage_correct": sum(r["disposition"] == r["expected"] for r in rows),
        "escalation_recall": sum(r["disposition"] == "escalate" for r in escalations) / len(escalations) if escalations else None,
        "reviewer_agrees": sum(r["verdict"] == "agree" for r in rows),
        "reviewer_disagrees": sum(r["verdict"] == "disagree" for r in rows),
        "unavailable": sum(r["status"] != "ok" for r in rows),
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("alert_ids", nargs="*", help="Alerts to compare; all golden alerts by default.")
    parser.add_argument("--workers", type=int, default=4, help="Parallel alerts for both runs (the gateway limits GPT calls per minute).")
    parser.add_argument("--tenant-id", default=os.environ.get("AZURE_TENANT_ID"))
    args = parser.parse_args(argv)
    if not 1 <= args.workers <= 16:
        parser.error("workers must be 1..16.")
    import investigate as platform
    from evaluate import parse
    if set(args.alert_ids) - set(platform.cases):
        parser.error("An alert ID is not in the case data.")
    golden = {r["alert_id"]: r["expected_disposition"] for r in map(
        json.loads, (ROOT / "data/golden.jsonl").read_text(encoding="utf-8").splitlines())}
    alert_ids = args.alert_ids or [a for a in platform.cases if a in golden]

    # Both runs get the production data policy, whatever governance/data-policy.json currently says.
    policy = {**platform.data_policy(), "enforce": True}
    platform.data_policy = lambda: policy
    credential = AzureCliCredential(tenant_id=args.tenant_id) if args.tenant_id else platform.credential
    platform.credential = credential
    token = credential.get_token("https://cognitiveservices.azure.com/.default").token
    endpoint = platform.env["FOUNDRY_PROJECT_ENDPOINT"].split("/api/projects/")[0]
    triage = DecisionClient(endpoint, "decision-1", TRIAGE_PROMPT.read_text(encoding="utf-8"), token, timeout=60)
    reviewer = Decision1Reviewer(endpoint, "decision-1", token, timeout=60)
    run_pipeline(platform, triage, reviewer, alert_ids[:1], policy, 1)  # warm the connection before timing

    print(f"{len(alert_ids)} alerts, {args.workers} in parallel. Running the classic investigation...", flush=True)
    classic = run_classic(platform, parse, alert_ids, args.workers, golden)
    print("Running the Decision-1 investigation...", flush=True)
    decision = run_pipeline(platform, triage, reviewer, alert_ids, policy, args.workers, golden)
    results = {"Classic (GPT)": classic_metrics(classic), "Decision-1": decision_metrics(decision)}

    rows = [
        ("Wall time", lambda m: f"{m['wall_seconds']:.1f}s"),
        ("Per alert (triage + review)", lambda m: f"{m['median_alert_seconds']:.1f}s"),
        ("Tokens per alert", lambda m: f"{m['tokens_per_alert']:,.0f}"),
        ("Cost per alert", lambda m: f"${m['cost_per_alert_usd']:.5f}"),
        ("Triage matches golden", lambda m: f"{m['triage_correct']}/{m['alerts']}"),
        ("Escalation recall", lambda m: "-" if m["escalation_recall"] is None else f"{m['escalation_recall']:.0%}"),
        ("Reviewer agrees", lambda m: f"{m['reviewer_agrees']}/{m['alerts']}"),
        ("Reviewer disagrees", lambda m: str(m["reviewer_disagrees"])),
        ("Unavailable", lambda m: str(m["unavailable"])),
    ]
    print(f"\n{'':30}" + "".join(f"{name:>16}" for name in results))
    for label, fmt in rows:
        print(f"{label:30}" + "".join(f"{fmt(m):>16}" for m in results.values()))
    classic_by_alert = {r["alert_id"]: r for r in classic["rows"]}
    for r in decision["rows"]:
        c = classic_by_alert[r["alert_id"]]
        if c["disposition"] != r["triage"].get("disposition") or c["verdict"] != r["review"].get("verdict"):
            print(f"  {r['alert_id']}: classic {c['disposition']} / {c['verdict']}, "
                  f"Decision-1 {r['triage'].get('disposition')} / {r['review'].get('verdict')}, expected {r['expected']}")
    print("\nCaveats: 14 known alerts are indicative only, and one run each. Classic cost includes reasoning tokens; "
          "Decision-1 cost uses $0.042 per 1M input tokens and ignores its ~1 output token per call. The classic reviewer "
          "also returns issues; Decision-1 returns a verdict and confidence only.")

    output = ROOT / "evidence" / "investigation-comparison" / datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S-%f")
    output.mkdir(parents=True)
    report = {"workers": args.workers, "alerts": alert_ids, "metrics": results, "classic": classic,
              "decision1": {k: v for k, v in decision.items() if k != "review_cases"}}
    (output / "results.json").write_text(json.dumps(report, indent=2, default=str) + "\n", encoding="utf-8")
    print("Evidence:", output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
