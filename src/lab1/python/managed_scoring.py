"""Foundry-managed scoring: a judge model rates each recorded answer with Foundry's built-in evaluators.

The agents aren't called again; Foundry receives the answers the runner already recorded. Mirrors ManagedScoring.cs.
"""
import time
from urllib.parse import quote

from checks import text
from foundry import remaining_seconds

POLICY_VERSION = "1"
MINIMUM_SCORE = 4
EVALUATORS = ("groundedness", "relevance")

# How often to check on the run, and when to give up. Tests shorten these.
POLL_SECONDS = 5
TIMEOUT_SECONDS = 600


def run(foundry, managed, items, instructions, run_id, save):
    """Scores the recorded answers and fills in the `managed` section of the Evidence Pack.

    `save` runs after each step, so the evaluation and run IDs are in the Evidence Pack even if the run later
    fails or times out.
    """
    deadline = time.monotonic() + TIMEOUT_SECONDS

    # 1. Register the evaluation: the evaluators, their judge model, and which fields each one reads.
    evaluation = foundry.post("openai/v1/evals", definition(managed["judge_model_deployment"]), deadline=deadline)
    managed["evaluation_id"] = text(evaluation.get("id"))
    save()
    remaining_seconds(deadline)

    # 2. Start a run over the recorded answers. The judge sees each agent's instructions, its input and its
    #    answer, but never the dataset's expected answer.
    rows = [{"item": {"id": item["id"],
                      "query": f"Agent instructions:\n{instructions[item['agent']]}\n\nTask input:\n{item['input']}",
                      "context": item["input"],
                      "response": item["output"]}} for item in items]
    started = foundry.post(f"openai/v1/evals/{managed['evaluation_id']}/runs", {
        "name": f"Lab 1 {run_id}",
        "metadata": {"lab_run_id": run_id},
        "data_source": {"type": "jsonl", "source": {"type": "file_content", "content": rows}},
    }, deadline=deadline)
    managed["run_id"] = text(started.get("id"))

    # 3. Wait for the run to finish.
    run_path = f"openai/v1/evals/{managed['evaluation_id']}/runs/{managed['run_id']}"
    current = started
    while True:
        managed["status"] = text(current.get("status"))
        managed["report_url"] = text(current.get("report_url")) or None
        save()
        remaining_seconds(deadline)
        if managed["status"] == "completed":
            break
        if managed["status"] not in ("queued", "in_progress"):
            raise RuntimeError(f"Foundry evaluation run {managed['run_id']} ended as {managed['status']}. "
                               "Open its report in Foundry.")
        time.sleep(min(POLL_SECONDS, remaining_seconds(deadline)))
        current = foundry.get(run_path, deadline=deadline)

    # 4. Read every scored row. Foundry returns them in pages of up to 100.
    page = foundry.get(f"{run_path}/output_items?limit=100&order=asc", deadline=deadline)
    scored = list(page.get("data") or [])
    while page.get("has_more") is True:
        page = foundry.get(f"{run_path}/output_items?limit=100&order=asc&after={quote(text(page.get('last_id')), safe='')}",
                           deadline=deadline)
        scored.extend(page.get("data") or [])

    # 5. Apply the lab's threshold to every score.
    remaining_seconds(deadline)
    managed["items"] = score(scored, [item["id"] for item in items])
    remaining_seconds(deadline)
    managed["gate"] = "pass" if all(item["passed"] for item in managed["items"]) else "fail"
    save()


def score(rows, item_ids):
    """Turns Foundry's scored rows into per-item results.

    An item passes when every evaluator scores at least MINIMUM_SCORE out of 5 and Foundry also marks the score as
    passed. A missing, duplicate or unreadable score means the scoring is incomplete, so no gate can be computed.
    """
    unscored = set(item_ids)
    results = []
    for row in rows:
        source = row.get("datasource_item") if isinstance(row, dict) else None
        item_id = text(source.get("id")) if isinstance(source, dict) else ""
        require(item_id in unscored, f"Foundry returned an unknown or duplicate item: {item_id}.")
        unscored.remove(item_id)
        require((row.get("sample") or {}).get("error") is None, f"Foundry couldn't score {item_id}.")

        metrics = [metric(item_id, result) for result in row.get("results") or [] if isinstance(result, dict)]
        require(sorted(m["name"] for m in metrics) == sorted(EVALUATORS),
                f"Foundry didn't return exactly one {' and '.join(EVALUATORS)} score for {item_id}.")
        results.append({"item_id": item_id, "passed": all(m["passed"] for m in metrics), "metrics": metrics})
    require(not unscored, f"Foundry didn't score {', '.join(sorted(unscored))}.")
    return results


def metric(item_id, result):
    """Reads one evaluator's score, and applies the lab's threshold on top of Foundry's own pass mark."""
    name = text(result.get("name"))
    value = result.get("score")
    service_passed = result.get("passed")
    is_number = isinstance(value, (int, float)) and not isinstance(value, bool)
    require(is_number and 1 <= value <= 5 and isinstance(service_passed, bool),
            f"Foundry returned an unreadable {name} score for {item_id}.")
    return {"name": name, "score": float(value), "service_passed": service_passed,
            "passed": service_passed and value >= MINIMUM_SCORE, "reason": text(result.get("reason")) or None}


def definition(judge_model):
    """The evaluation definition.

    Both evaluators read the query and the answer; Groundedness also reads the case file as context, to judge
    whether the answer is supported by it.
    """
    string_field = {"type": "string"}
    criteria = []
    for name in EVALUATORS:
        mapping = {"query": "{{item.query}}", "response": "{{item.response}}"}
        if name == "groundedness":
            mapping["context"] = "{{item.context}}"
        criteria.append({"type": "azure_ai_evaluator", "name": name, "evaluator_name": f"builtin.{name}",
                         "initialization_parameters": {"model": judge_model}, "data_mapping": mapping})
    return {
        "name": f"Lab 1 evidence quality (policy {POLICY_VERSION})",
        "data_source_config": {"type": "custom", "item_schema": {
            "type": "object",
            "properties": {"id": string_field, "query": string_field, "context": string_field, "response": string_field},
            "required": ["id", "query", "context", "response"]}},
        "testing_criteria": criteria,
    }


def require(condition, message):
    if not condition:
        raise RuntimeError(message)
