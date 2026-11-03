"""Release gate: run the golden alerts through the agents, score them, and write an Evidence Pack.

Exits with 1 when a blocking threshold fails, so a pipeline can stop the release.

Usage: python src/evaluate.py [--foundry]
  --foundry  also start a Foundry evaluation with an AI judge, and record it in the Evidence Pack
"""
import hashlib
import json
import re
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path

import openai
from azure.ai.projects import AIProjectClient

import investigate as platform
from console_output import color
from investigate import ROOT, case_file, cases, credential, data, env, investigate, policy_hash

GOLDEN = ROOT / "data" / "golden.jsonl"
THRESHOLDS = ROOT / "evals" / "thresholds.json"
CITATION = re.compile(r"\b[A-Z]{1,4}-\d{3,5}(?:-\d+)*\b")


def parse(text: str) -> dict:
    """The agents are told to return JSON only; anything else scores as no answer."""
    match = re.search(r"\{.*\}", text, re.DOTALL)
    try:
        return json.loads(match.group()) if match else {}
    except json.JSONDecodeError:
        return {}


def ids(value) -> set:
    """Every "id" in a case file: the only things an agent may cite."""
    if isinstance(value, dict):
        return {v for k, v in value.items() if k == "id"} | {i for v in value.values() for i in ids(v)}
    if isinstance(value, list):
        return {i for v in value for i in ids(v)}
    return set()


def score(row: dict, result: dict) -> dict:
    triage, review = parse(result["triage"]), parse(result["review"])
    cited = set(CITATION.findall(json.dumps(triage.get("reasons", []))))
    valid = ids(cases[row["alert_id"]]) | ids(data["screening_lists"])
    return {
        "alert_id": row["alert_id"],
        "expected": row["expected_disposition"],
        "disposition": triage.get("disposition"),
        "correct": triage.get("disposition") == row["expected_disposition"],
        "invalid_citations": sorted(cited - valid),
        "cited": bool(cited),
        "reviewer": review.get("verdict"),
        "tokens": result["tokens"],
        "cost": result.get("cost", 0.0),
        "model": result.get("triage_model"),
        "seconds": result["seconds"],
        "triage": result["triage"],
        "review": result["review"],
    }


def metrics(rows: list) -> dict:
    escalations = [r for r in rows if r["expected"] == "escalate"]
    return {
        "disposition_accuracy": sum(r["correct"] for r in rows) / len(rows),
        "escalation_recall": sum(r["correct"] for r in escalations) / len(escalations),
        "citation_validity": sum(r["cited"] and not r["invalid_citations"] for r in rows) / len(rows),
        "reviewer_agreement": sum(r["reviewer"] == "agree" for r in rows) / len(rows),
    }


def markdown_table(headers: list[str], rows: list[list]) -> list[str]:
    """Align Markdown columns for both terminal output and the saved report."""
    cells = [headers, *[[str(value) for value in row] for row in rows]]
    widths = [max(3, *(len(cell) for cell in column)) for column in zip(*cells)]
    lines = ["| " + " | ".join(cell.ljust(width) for cell, width in zip(row, widths)) + " |" for row in cells]
    lines.insert(1, "| " + " | ".join("-" * width for width in widths) + " |")
    return lines


def run_one(row: dict) -> dict:
    start = time.time()
    blocked_by = ""
    try:
        result = investigate(row["alert_id"])
    except openai.BadRequestError as error:
        if error.code != "prompt_attack_detected":
            raise
        blocked_by = "prompt_attack_detected"
        result = {"triage": "", "review": "", "tokens": 0}
    scored = score(row, {**result, "seconds": round(time.time() - start, 1)})
    if blocked_by:
        scored["blocked_by"] = blocked_by
    return scored


def published(project: AIProjectClient) -> dict:
    """The agent versions that callers get now: what an Evidence Pack must have tested."""
    agents = {}
    for name in ("triage", "reviewer", "triage-lite"):
        latest = project.agents.get(agent_name=name).versions.latest
        agents[name] = {"version": latest.version, "model": latest.definition.model}
    return agents


def foundry_evaluation(project: AIProjectClient, golden: list) -> dict:
    """Start a Foundry evaluation: an AI judge scores whether triage followed its instructions.

    It takes a few minutes, so this doesn't wait: see the result in the Foundry portal, under Evaluations.
    """
    client = project.get_openai_client()
    name = "Triage task adherence"
    # One evaluation holds every run, so runs can be compared in the Foundry portal.
    evaluation = next((e for e in client.evals.list() if e.name == name), None) or client.evals.create(
        name=name,
        data_source_config={
            "type": "custom",
            "item_schema": {"type": "object", "properties": {"query": {"type": "string"}}, "required": ["query"]},
            "include_sample_schema": True,
        },
        testing_criteria=[{
            "type": "azure_ai_evaluator",
            "name": "Task adherence",
            "evaluator_name": "builtin.task_adherence",
            "initialization_parameters": {"deployment_name": env["MODEL_DEPLOYMENT"]},
            "data_mapping": {"query": "{{item.query}}", "response": "{{sample.output_items}}"},
        }],
    )
    content = [
        {"item": {"query": case_file(cases[r["alert_id"]], "triage")}}
        for r in golden
    ]
    run = client.evals.runs.create(
        eval_id=evaluation.id,
        name=f"Release gate {datetime.now():%Y-%m-%d %H:%M}",
        data_source={
            "type": "azure_ai_target_completions",
            "source": {"type": "file_content", "content": content},
            "input_messages": {
                "type": "template",
                "template": [{"type": "message", "role": "user", "content": {"type": "input_text", "text": "{{item.query}}"}}],
            },
            "target": {"type": "azure_ai_agent", "name": "triage"},
        },
    )
    return {"evaluation": name, "run": run.name, "eval_id": evaluation.id, "run_id": run.id}


# Self-check of the scoring, with no calls to Azure.
_row = {"alert_id": "ALT-1001", "expected_disposition": "escalate"}
_good = {"triage": '{"disposition": "escalate", "reasons": ["Structuring [T-00001]"]}', "review": '{"verdict": "agree"}', "tokens": 1, "seconds": 1}
_bad = {"triage": 'Sure! {"disposition": "close_false_positive", "reasons": ["Fine [T-99999]"]}', "review": "", "tokens": 1, "seconds": 1}
assert score(_row, _good)["correct"] and not score(_row, _good)["invalid_citations"]
assert not score(_row, _bad)["correct"] and score(_row, _bad)["invalid_citations"] == ["T-99999"]
assert metrics([score(_row, _good), score(_row, _bad)])["escalation_recall"] == 0.5
_document_row = {"alert_id": "ALT-1008", "expected_disposition": "escalate"}
for _document_id, _invalid in (
    ("DOC-1008-01", []),
    ("DOC-1008-02", ["DOC-1008-02"]),
    ("DOC-1008-01-99", ["DOC-1008-01-99"]),
):
    _document_result = {**_good, "triage": json.dumps({
        "disposition": "escalate", "reasons": [f"Remote-job recruitment message [{_document_id}]"],
    })}
    _document_score = score(_document_row, _document_result)
    assert _document_score["cited"] and _document_score["invalid_citations"] == _invalid

if __name__ == "__main__":
    platform.COST_CENTER = "model-validation"  # the release gate's tokens are charged to model validation
    golden = [json.loads(line) for line in GOLDEN.read_text(encoding="utf-8").splitlines() if line.strip()]
    thresholds = json.loads(THRESHOLDS.read_text(encoding="utf-8"))
    project = AIProjectClient(endpoint=env["FOUNDRY_PROJECT_ENDPOINT"], credential=credential)
    agents = published(project)

    print(color(f"Evaluating {len(golden)} alerts:", "cyan") + " "
          + ", ".join(f"{n} v{a['version']} ({a['model']})" for n, a in agents.items()))
    foundry = foundry_evaluation(project, golden) if "--foundry" in sys.argv else None
    with ThreadPoolExecutor(max_workers=7) as pool:
        rows = list(pool.map(run_one, golden))

    blocked = [r for r in rows if r.get("blocked_by")]
    values = metrics(rows)
    checks = [
        {"metric": name, "value": round(values[name], 2), **rule, "passed": values[name] >= rule["min"]}
        for name, rule in thresholds.items()
    ]
    passed = not blocked and all(c["passed"] for c in checks if c["blocking"])

    # The Evidence Pack: everything an approver or auditor needs to see what was tested and how it scored.
    pack = ROOT / "evidence" / datetime.now().strftime("%Y%m%d-%H%M%S")
    pack.mkdir(parents=True)
    commit = subprocess.run(["git", "rev-parse", "HEAD"], capture_output=True, text=True, cwd=ROOT).stdout.strip()
    manifest = {
        "verdict": "PASS" if passed else "FAIL",
        "created": datetime.now().isoformat(timespec="seconds"),
        "agents": agents,
        "dataset": {"file": "data/golden.jsonl", "alerts": len(golden), "sha256": hashlib.sha256(GOLDEN.read_bytes()).hexdigest()},
        "thresholds": {"file": "evals/thresholds.json", "sha256": hashlib.sha256(THRESHOLDS.read_bytes()).hexdigest()},
        "data_policy": {"file": "governance/data-policy.json", "sha256": policy_hash()},
        "git_commit": commit,
        "checks": checks,
        "tokens": sum(r["tokens"] for r in rows),
        "cost_usd": round(sum(r["cost"] for r in rows), 4),
        "blocked_alerts": [{"alert_id": r["alert_id"], "by": r["blocked_by"]} for r in blocked],
        "foundry_evaluation": foundry,
    }
    (pack / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    (pack / "results.json").write_text(json.dumps(rows, indent=2), encoding="utf-8")

    report = [
        f"# Release gate: {manifest['verdict']}",
        "",
        "Agents tested: " + ", ".join(f"{n} version {a['version']} on {a['model']}" for n, a in agents.items()),
        "",
        *markdown_table(
            ["Metric", "Value", "Required", "Blocking", "Result", "Owner"],
            [[c["metric"], c["value"], f">= {c['min']}", "yes" if c["blocking"] else "no",
              "PASS" if c["passed"] else "FAIL", c["owner"]] for c in checks],
        ),
        "",
        *markdown_table(
            ["Alert", "Expected", "Recommended", "Reviewer", "Invalid citations", "Triage model", "Blocked by"],
            [[r["alert_id"], r["expected"], f"{r['disposition']}{'' if r['correct'] else ' ✗'}",
              r["reviewer"], ", ".join(r["invalid_citations"]) or "-", r["model"],
              r.get("blocked_by", "-")] for r in rows],
        ),
        "",
        f"Tokens used: {manifest['tokens']}. Cost: ${manifest['cost_usd']:.4f}, ${manifest['cost_usd'] / len(rows):.4f} per alert.",
        *([f"Foundry evaluation (AI judge): {foundry['evaluation']}, run {foundry['run']}"] if foundry else []),
    ]
    if blocked:
        report.extend([
            "",
            "FAIL: Prompt Shields blocked legitimate golden alerts: "
            + ", ".join(r["alert_id"] for r in blocked) + ". These are failed evaluations, not passing controls.",
            "Token and cost totals exclude blocked investigations, including any calls completed before the block.",
        ])
    (pack / "report.md").write_text("\n".join(report) + "\n", encoding="utf-8")

    for line in report[:4 + 2 + len(checks)]:
        if line.startswith("# "):
            print(color("# Release gate:", "cyan"), color(manifest["verdict"], "green" if passed else "red"))
        else:
            print(re.sub(r"(?<=\| )(PASS|FAIL)(?= +\|)",
                         lambda match: color(match[0], "green" if match[0] == "PASS" else "red"), line))
    print(f"\n{color('Cost:', 'cyan')} ${manifest['cost_usd']:.4f}, ${manifest['cost_usd'] / len(rows):.4f} per alert ({manifest['tokens']} tokens)")
    if blocked:
        print(color("Prompt Shields blocked golden alerts: " + ", ".join(r["alert_id"] for r in blocked), "red"))
        print("Token and cost totals exclude blocked investigations, including any calls completed before the block.")
    print(f"\n{color('Evidence Pack:', 'cyan')} {pack.relative_to(ROOT)}")
    if foundry:
        print(f"{color('Foundry evaluation started:', 'cyan')} {foundry['run']}. See it in the Foundry portal, under Evaluations.")
    sys.exit(0 if passed else 1)
