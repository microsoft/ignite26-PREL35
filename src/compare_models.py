"""Compare Decision-1 and gpt-5-mini on the golden cases: speed, cost and agreement with the golden labels.

Usage: uv run python src/compare_models.py [--repeat 3] [--scale 1] [--workers 8]

Both models receive the same Markdown rubric, the same label criteria and the same policy-enforced case files.
Golden labels are never sent to either model. Evidence is written under evidence/decision1-comparison/.
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
from openai import OpenAI

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "src"))
from src.decision1_client import CRITERIA, DecisionClient  # noqa: E402

DECISION_INPUT_USD_PER_M = 0.042


def price_decision(input_tokens):
    # Only the input rate is published for Decision-1; its ~1 output token per case is not priced.
    return input_tokens * DECISION_INPUT_USD_PER_M / 1_000_000


def price_gpt(prices, usage):
    cached = (usage.get("cached") or 0)
    return ((usage["input"] - cached) * prices["input"] + cached * prices["cached_input"]
            + usage["output"] * prices["output"]) / 1_000_000


def summarize(rows, expected):
    ok = [r for r in rows if r["disposition"] == expected[r["alert_id"]]]
    esc = [r for r in rows if expected[r["alert_id"]] == "escalate"]
    return {"correct": len(ok), "count": len(rows), "accuracy": len(ok) / len(rows),
            "escalation_recall": (sum(r["disposition"] == "escalate" for r in esc) / len(esc)) if esc else None,
            "misses": sorted({r["alert_id"] for r in rows if r["disposition"] != expected[r["alert_id"]]})}


def run_decision(client, cases, workers):
    result = client.classify(cases, workers=workers)
    tokens = sum((q.get("usage") or {}).get("input_tokens", 0) for q in result["requests"])
    rows = [{"alert_id": r["alert_id"], "disposition": r.get("disposition")} for r in result["rows"]]
    return {"seconds": result["seconds"], "rows": rows, "input_tokens": tokens,
            "output_tokens": sum((q.get("usage") or {}).get("output_tokens", 0) for q in result["requests"]),
            "latencies": [q["seconds"] for q in result["requests"]]}


def run_gpt(client, model, effort, rubric, cases, workers):
    system = (rubric + "\n\n## Labels\n" + "\n".join(f"- {k}: {v}" for k, v in CRITERIA.items())
              + '\n\nAnswer with only a JSON object: {"disposition": "<label>"}.')

    def one(case):
        begin = time.perf_counter()
        try:
            r = client.responses.create(model=model, instructions=system, input="Case file (JSON):\n" + json.dumps(case, ensure_ascii=False),
                                        reasoning={"effort": effort}, text={"format": {"type": "json_object"}})
            label = json.loads(r.output_text).get("disposition")
            cached = r.usage.input_tokens_details.cached_tokens if r.usage.input_tokens_details else 0
            usage = {"input": r.usage.input_tokens, "output": r.usage.output_tokens, "cached": cached}
        except Exception as error:  # a failed call counts as a miss, not a crash
            label, usage = None, {"input": 0, "output": 0, "cached": 0, "error": str(error)[:200]}
        return {"alert_id": case["alert"]["id"], "disposition": label}, usage, time.perf_counter() - begin

    started = time.perf_counter()
    with ThreadPoolExecutor(max_workers=workers) as pool:
        out = list(pool.map(one, cases))
    return {"seconds": time.perf_counter() - started, "rows": [o[0] for o in out],
            "usage": [o[1] for o in out], "latencies": [o[2] for o in out]}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repeat", type=int, default=3)
    parser.add_argument("--scale", type=int, default=1, help="Repeat the 14 cases this many times per run (unique IDs).")
    parser.add_argument("--workers", type=int, default=8)
    parser.add_argument("--model", default="gpt-5-mini")
    parser.add_argument("--effort", default="low", choices=("minimal", "low", "medium", "high"))
    parser.add_argument("--tenant-id", default=os.environ.get("AZURE_TENANT_ID"))
    args = parser.parse_args(argv)
    if not 1 <= args.repeat <= 10 or not 1 <= args.scale <= 20 or not 1 <= args.workers <= 16:
        parser.error("repeat 1..10, scale 1..20, workers 1..16.")

    import investigate as platform
    golden = [json.loads(line) for line in (ROOT / "data/golden.jsonl").read_text(encoding="utf-8").splitlines()]
    expected = {r["alert_id"]: r["expected_disposition"] for r in golden}
    policy = {**platform.data_policy(), "enforce": True}
    base = [json.loads(platform.case_file(platform.cases[r["alert_id"]], "triage", policy)) for r in golden]
    cases, key = [], {}
    for n in range(args.scale):
        for case in base:
            copy = json.loads(json.dumps(case))
            if n:
                copy["alert"]["id"] = f'{case["alert"]["id"]}#{n}'
            key[copy["alert"]["id"]] = expected[case["alert"]["id"]]
            cases.append(copy)
    prices = json.loads((ROOT / "evals/prices.json").read_text())["models"][args.model]
    rubric = (ROOT / "src/prompts/decision1.md").read_text(encoding="utf-8")
    credential = AzureCliCredential(tenant_id=args.tenant_id) if args.tenant_id else platform.credential
    token = credential.get_token("https://cognitiveservices.azure.com/.default").token
    endpoint = platform.env["FOUNDRY_PROJECT_ENDPOINT"].split("/api/projects/")[0]
    decision = DecisionClient(endpoint, "decision-1", rubric, token, timeout=60)
    gpt = OpenAI(base_url=endpoint + "/openai/v1", api_key=token, max_retries=6)

    decision.classify(base[:1])  # warm both connections before timing
    run_gpt(gpt, args.model, args.effort, rubric, base[:1], 1)

    stats = {"Decision-1": [], args.model: []}
    for i in range(args.repeat):
        d = run_decision(decision, cases, args.workers)
        g = run_gpt(gpt, args.model, args.effort, rubric, cases, args.workers)
        d["cost"] = price_decision(d["input_tokens"])
        g["cost"] = sum(price_gpt(prices, u) for u in g["usage"])
        d["tokens"], g["tokens"] = d["input_tokens"] + d["output_tokens"], sum(u["input"] + u["output"] for u in g["usage"])
        stats["Decision-1"].append(d)
        stats[args.model].append(g)
        print(f"Run {i + 1}/{args.repeat} done.", flush=True)

    n = len(cases)
    print(f"\n{n} cases per run, {args.repeat} runs, {args.workers} parallel calls; medians shown.\n")
    header = f"{'Model':<14}{'Wall time':>11}{'Per case':>10}{'p95 call':>10}{'Tokens/case':>13}{'Cost/case':>12}{'Per 1M cases':>14}{'Accuracy':>10}{'Esc. recall':>13}"
    print(header + "\n" + "-" * len(header))
    report = {}
    for name, runs in stats.items():
        scores = [summarize(r["rows"], key) for r in runs]
        wall = median(r["seconds"] for r in runs)
        lat = sorted(x for r in runs for x in r["latencies"])
        cost = median(r["cost"] for r in runs) / n
        acc = median(s["accuracy"] for s in scores)
        rec = [s["escalation_recall"] for s in scores if s["escalation_recall"] is not None]
        misses = sorted({m for s in scores for m in s["misses"]})
        print(f"{name:<14}{wall:>10.2f}s{wall / n * 1000:>8.0f}ms{lat[int(.95 * (len(lat) - 1))] * 1000:>8.0f}ms"
              f"{median(r['tokens'] for r in runs) / n:>13.0f}{'$' + format(cost, '.6f'):>12}"
              f"{'$' + format(cost * 1_000_000, ',.0f'):>14}{acc:>10.0%}{(median(rec) if rec else 0):>13.0%}")
        if misses:
            print(f"  missed: {', '.join(misses)}")
        report[name] = {"runs": runs, "scores": scores, "median_wall_seconds": wall, "cost_per_case_usd": cost}
    print("\nCaveats: 14 known cases are indicative only. Decision-1 cost uses $0.042 per 1M input tokens and ignores its"
          f" ~1 output token. {args.model} cost includes reasoning tokens (effort={args.effort}).")
    out = ROOT / "evidence" / "decision1-comparison" / f"{datetime.now(timezone.utc):%Y%m%d-%H%M%S}"
    out.mkdir(parents=True)
    (out / "results.json").write_text(json.dumps({"effort": args.effort, "workers": args.workers, "cases": n,
                                                  "report": report}, indent=2) + "\n", encoding="utf-8")
    print("Evidence:", out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
