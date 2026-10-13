"""The Evidence Pack: everything an approver or auditor needs to check one run. Mirrors EvidencePack.cs.

It's written as evidence.json (all details) and summary.md (readable), and saved again after every step, so even a
failed run leaves evidence. The field names and order match the .NET runner, so the lab scripts read either one.
"""
import json
import os
import sys
import tempfile
import uuid
from datetime import datetime, timezone

import managed_scoring


def now():
    return datetime.now(timezone.utc).isoformat()


def new_pack():
    return {
        "schema_version": "3",
        "runner": "python",
        "run_id": uuid.uuid4().hex,
        "started_at": now(),
        "finished_at": None,
        # What was evaluated: live agents or recorded answers, which checks ran, and fingerprints of every input.
        "execution": "not_started",
        "mode": "local_deterministic",
        "dataset_version": None,
        "dataset_sha256": None,
        "cases_sha256": None,
        "runner_sha256": None,
        "replay_sha256": None,
        "agents": None,
        "trace_id": None,
        # The verdicts. Only a live run with both gates passed and its telemetry sent is release-ready.
        "local_gate": "execution_error",
        "managed_evaluation_status": "not_started",
        "managed_evaluation": None,
        "release_gate": "not_evaluated",
        "release_ready": False,
        "telemetry_status": "disabled",
        # Always False: the runner can't see whether Application Insights ingested the telemetry. Check it there.
        "telemetry_ingestion_verified": False,
        "exit_code": 2,
        "error": None,
        "summary": None,
        "items": [],
    }


def new_managed_evaluation(judge_model):
    """The managed scoring settings, the Foundry evaluation it created, and the result."""
    return {
        "policy_version": managed_scoring.POLICY_VERSION,
        "judge_model_deployment": judge_model,
        "evaluators": list(managed_scoring.EVALUATORS),
        "minimum_score": managed_scoring.MINIMUM_SCORE,
        "evaluation_id": None,
        "run_id": None,
        "status": "not_started",
        "report_url": None,
        "gate": "execution_error",
        "items": [],
    }


def save(directory, pack):
    """Writes evidence.json and summary.md to the run's folder, replacing the previous save."""
    content = json.dumps(without_none(pack), indent=2)
    markdown = summary_markdown(pack)
    try:
        # Each replacement is atomic. Commit the authoritative JSON verdict only after the summary succeeds.
        write_atomic(directory, "summary.md", markdown)
        write_atomic(directory, "evidence.json", content)
    except OSError as error:
        pack["release_ready"] = False
        pack["release_gate"] = "execution_error"
        pack["exit_code"] = 2
        pack["error"] = f"{pack['error'] or ''} Evidence Pack save failed: {error}".strip()

        # Either file may be unwritable. Try each independently to leave a failed verdict wherever possible.
        for name, text in (("summary.md", summary_markdown(pack)),
                           ("evidence.json", json.dumps(without_none(pack), indent=2))):
            try:
                write_atomic(directory, name, text)
            except OSError as recovery_error:
                print(f"Could not record the save failure in {name}: {recovery_error}", file=sys.stderr)
        raise


def write_atomic(directory, name, content):
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=directory,
                                         prefix=f"{name}.", suffix=".tmp", delete=False) as file:
            temporary = file.name
            file.write(content)
        os.replace(temporary, os.path.join(directory, name))
    finally:
        if temporary is not None and os.path.exists(temporary):
            os.remove(temporary)


def without_none(value):
    """Leaves out empty fields, as the .NET runner does."""
    if isinstance(value, dict):
        return {key: without_none(item) for key, item in value.items() if item is not None}
    if isinstance(value, list):
        return [without_none(item) for item in value]
    return value


def summary_markdown(pack):
    lines = [
        "# Lab 1 Evidence Pack",
        "",
        f"- Runner: {pack['runner']}",
        f"- Execution: {pack['execution']}",
        f"- Local gate: {pack['local_gate']}",
        f"- Managed scoring: {pack['managed_evaluation_status']}",
        f"- Release gate: {pack['release_gate']}",
        f"- Release ready: {'yes' if pack['release_ready'] else 'no'}",
        f"- Telemetry: {pack['telemetry_status']} (confirm it arrived in Application Insights)",
        f"- Run ID: {pack['run_id']}",
        f"- Trace ID: {pack['trace_id'] or 'none'}",
        f"- Dataset version: {pack['dataset_version'] or 'not loaded'}",
        "",
        "| Item | Agent | Passed checks | Total checks | Duration (ms) |",
        "|---|---|---:|---:|---:|",
    ]
    for item in pack["items"]:
        passed = sum(c["passed"] for c in item["checks"])
        lines.append(f"| {item['id']} | {item['agent']} | {passed} | {len(item['checks'])} | {item['duration_ms']:.0f} |")

    managed = pack["managed_evaluation"]
    if managed:
        lines += [
            "",
            "## Managed scores",
            "",
            f"Every evaluator must score at least {managed['minimum_score']}/5 on every item. "
            f"Judge: {managed['judge_model_deployment']}.",
            "",
            f"Foundry report: {managed['report_url'] or 'not available'}",
            "",
            "| Item | Evaluator | Score | Passed |",
            "|---|---|---:|---|",
        ]
        for item in managed["items"]:
            for metric in item["metrics"]:
                lines.append(f"| {item['item_id']} | {metric['name']} | {metric['score']:g} | "
                             f"{'yes' if metric['passed'] else 'no'} |")
    lines += ["", "Inputs, answers, check details and correlation IDs are in [evidence.json](./evidence.json).", ""]
    return "\n".join(lines)
