"""Evaluate Decision-1 choices on the golden cases, without GPT, reviewer or infrastructure changes.

Usage: uv run python src/evaluate_decisions.py [--repeat 3] [--mode isolated|shared]
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

ROOT = Path(__file__).resolve().parents[1]
if __package__ in (None, ""):
    sys.path.insert(0, str(ROOT))
from src.decision1_client import DecisionClient

PROMPT = ROOT / "src/prompts/decision1.md"


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
        runs.append({**result, **scored})
        print(f"Run {i + 1}: {scored['correct']}/{scored['count']} correct, "
              f"{scored['unavailable']} unavailable, {result['seconds']:.2f}s ({args.mode}); no GPT.", flush=True)
        for row in scored["rows"]:
            if not row["correct"]:
                print(f"  {row['alert_id']}: expected {row['expected']}, got {row['disposition']} ({row['status']})", flush=True)
    output = ROOT / "evidence" / "decision1" / datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S-%f")
    output.mkdir(parents=True)
    report = {
        "prompt_file": str(args.prompt), "prompt_sha256": hashlib.sha256(instructions.encode()).hexdigest(),
        "criteria": client.question()["criteria"], "mode": args.mode, "runs": runs,
        "median_seconds": median(r["seconds"] for r in runs),
        "gpt_calls": 0, "decision_cost_usd": None,
        "notes": ["Golden labels are not included in model inputs.",
                  "Shared mode exposes all cases to every question; it is not isolated native request batching.",
                  "These choice-only metrics do not replace the full release gate or score explanation quality."],
    }
    (output / "prompt.md").write_text(instructions, encoding="utf-8")
    (output / "results.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print("Evidence:", output)
    return 0 if all(r["correct"] == r["count"] for r in runs) else 1


if __name__ == "__main__":
    raise SystemExit(main())
