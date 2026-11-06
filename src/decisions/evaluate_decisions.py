"""Evaluate Decision-1 choices on the golden cases, without GPT, reviewer or infrastructure changes.

Usage: uv run python src/decisions/evaluate_decisions.py [--repeat 3] [--mode isolated|shared]
"""
import argparse
import hashlib
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from statistics import median

from azure.identity import AzureCliCredential

ROOT = Path(__file__).resolve().parents[2]
if __package__ in (None, ""):
    sys.path.insert(0, str(ROOT))
    sys.path.insert(0, str(ROOT / "src"))
from scripts.console_output import color
from src.decisions.compare_models import price_decision
from src.decisions.decision1_client import DecisionClient
from src.decisions.display import disposition, heading, mark, print_steps

PROMPT = ROOT / "src/decisions/prompts/decision1.md"


def score(rows, golden):
    expected = {r["alert_id"]: r["expected_disposition"] for r in golden}
    if len(rows) != len(expected) or {r["alert_id"] for r in rows} != set(expected):
        raise ValueError("Results must match the selected golden cases exactly.")
    scored = [{**row, "expected": expected[row["alert_id"]],
               "correct": row["status"] == "ok" and row["disposition"] == expected[row["alert_id"]]} for row in rows]
    escalations = [r for r in scored if r["expected"] == "escalate"]
    return {
        "correct": sum(r["correct"] for r in scored), "count": len(scored),
        "accuracy": sum(r["correct"] for r in scored) / len(scored),
        "escalation_recall": sum(r["correct"] for r in escalations) / len(escalations) if escalations else None,
        "unavailable": sum(r["status"] != "ok" for r in scored), "rows": scored,
    }


def run_cost(result):
    return price_decision(sum((q.get("usage") or {}).get("input_tokens", 0) for q in result["requests"]))


def show(number, args, result, scored):
    """One readable table per run: each case, what Decision-1 chose, and the time and cost of the calls."""
    heading(f"Run {number} of {args.repeat}: Decision-1 against the golden cases ({args.mode})")
    print(f"{'Alert':9} {'Expected':22} {'Decision-1':22} Time")
    for row, request in zip(scored["rows"], result["requests"]) if args.mode == "isolated" else ((r, {}) for r in scored["rows"]):
        got = row["disposition"] if row["status"] == "ok" else f"unavailable ({row.get('error')})"
        seconds = f"{request['seconds']:>4.1f}s" if request else ""
        print(f"{row['alert_id']:9} {disposition(row['expected'], 22)} "
              f"{disposition(got, 22) if row['status'] == 'ok' else color(f'{got:<22}', 'red')} {seconds} {mark(row['correct'])}")
    count = len(scored["rows"])
    print_steps([{"what": f"Decision-1 classifies {count} case{'s' * (count != 1)}"
                          + (f", {min(args.workers, count)} in parallel" if args.mode == "isolated" else " in one shared request"),
                  "seconds": result["seconds"], "cost_usd": run_cost(result), "model": "decision-1"}])
    good = scored["correct"] == scored["count"]
    print(f"  {color(f'{scored['correct']}/{scored['count']} correct', 'green' if good else 'red')}"
          f", {scored['unavailable']} unavailable. No GPT calls.", flush=True)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--prompt", type=Path, default=PROMPT)
    parser.add_argument("--mode", choices=("isolated", "shared"), default="isolated",
                        help="Parallel independent cases, or one shared state with a named question per case.")
    parser.add_argument("--repeat", type=int, default=1)
    parser.add_argument("--workers", type=int, default=8)
    parser.add_argument("--alert-id", action="append", help="Limit to specific existing golden alerts; repeat this option.")
    parser.add_argument("--tenant-id", default=os.environ.get("AZURE_TENANT_ID"))
    args = parser.parse_args(argv)
    if not 1 <= args.repeat <= 20 or not 1 <= args.workers <= 16:
        parser.error("repeat must be 1..20 and workers 1..16.")
    import investigate as platform
    golden = [json.loads(line) for line in (ROOT / "data/golden.jsonl").read_text(encoding="utf-8").splitlines()]
    if args.alert_id:
        if set(args.alert_id) - {r["alert_id"] for r in golden}:
            parser.error("An alert ID is not in the golden dataset.")
        golden = [r for r in golden if r["alert_id"] in args.alert_id]
    policy = {**platform.data_policy(), "enforce": True}
    cases = [json.loads(platform.case_file(platform.cases[r["alert_id"]], "triage", policy)) for r in golden]
    instructions = args.prompt.read_text(encoding="utf-8")
    credential = AzureCliCredential(tenant_id=args.tenant_id) if args.tenant_id else platform.credential
    token = credential.get_token("https://cognitiveservices.azure.com/.default").token
    endpoint = platform.env["FOUNDRY_PROJECT_ENDPOINT"].split("/api/projects/")[0]
    client = DecisionClient(endpoint, "decision-1", instructions, token)
    runs = []
    for i in range(args.repeat):
        result = client.classify(cases, workers=args.workers, shared=args.mode == "shared")
        scored = score(result["rows"], golden)
        runs.append({**result, **scored, "cost_usd": run_cost(result)})
        show(i + 1, args, result, scored)
    output = ROOT / "evidence" / "decision1" / datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S-%f")
    output.mkdir(parents=True)
    report = {
        "prompt_file": str(args.prompt), "prompt_sha256": hashlib.sha256(instructions.encode()).hexdigest(),
        "criteria": client.question()["criteria"], "mode": args.mode, "runs": runs,
        "median_seconds": median(r["seconds"] for r in runs),
        "gpt_calls": 0, "decision_cost_usd": median(r["cost_usd"] for r in runs),
        "notes": ["Golden labels are not included in model inputs.",
                  "Shared mode exposes all cases to every question; it is not isolated native request batching.",
                  "These choice-only metrics do not replace the full release gate or score explanation quality."],
    }
    (output / "prompt.md").write_text(instructions, encoding="utf-8")
    (output / "results.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print("\nEvidence:", output)
    return 0 if all(r["correct"] == r["count"] for r in runs) else 1


if __name__ == "__main__":
    raise SystemExit(main())
