"""Lab 1 evaluation runner (Python). The .NET version in src\\lab1\\dotnet behaves the same way.

For every case in the evaluation dataset, the runner asks the triage agent for a recommendation and the reviewer
agent to check it, then gives the reviewer some deliberately flawed recommendations ("challenges"). It checks every
answer with deterministic rules, asks Foundry to score the answers with a judge model, and writes an Evidence Pack
with the release decision.

    python .\\src\\lab1\\python\\evaluate.py --triage-version 4 --reviewer-version 4
    python .\\src\\lab1\\python\\evaluate.py --replay .\\responses.json

Exit codes: 0 = the selected checks passed, 1 = a quality check failed, 2 = the run couldn't complete.
Only a live run with managed scoring can pass the release gate.
"""
import argparse
import hashlib
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

from opentelemetry import context, trace
from opentelemetry.trace import Status, StatusCode

import checks
import dataset as data
import evidence as pack_file
import managed_scoring
import telemetry
from foundry import Foundry

AGENTS = ("triage", "reviewer")
ROOT = Path(__file__).resolve().parents[3]
RUNNER_DIR = Path(__file__).resolve().parent


def parse_options(argv):
    """Reads the command line. Invalid options print usage and exit with code 2."""
    parser = argparse.ArgumentParser(description="Lab 1 evaluation runner.",
                                     epilog="Exit codes: 0 = the selected checks passed, 1 = a quality check failed, "
                                            "2 = the run couldn't complete.")
    parser.add_argument("--triage-version", help="Live run: the triage agent version to evaluate.")
    parser.add_argument("--reviewer-version", help="Live run: the reviewer agent version to evaluate.")
    parser.add_argument("--replay", help="Deterministic checks on recorded answers; no Azure calls.")
    parser.add_argument("--scoring", choices=("managed", "local"), help="Live default: managed. A replay is always local.")
    parser.add_argument("--judge-model", help="Default: MODEL_DEPLOYMENT from .env.")
    parser.add_argument("--dataset", help=r"Default: data\lab1-evaluations.json.")
    parser.add_argument("--output", help=r"Default: a new folder under .artifacts\lab1.")
    options = parser.parse_args(argv)
    options.scoring = options.scoring or ("managed" if options.replay is None else "local")

    # A replay has no agents to call and no judge to ask, so it can only run the deterministic checks.
    if options.replay is None and (options.triage_version is None or options.reviewer_version is None):
        parser.error("A live run needs --triage-version and --reviewer-version.")
    if options.replay is not None and (options.triage_version or options.reviewer_version or options.scoring != "local"):
        parser.error("--replay can't be combined with agent versions or managed scoring.")
    if options.scoring == "local" and options.judge_model is not None:
        parser.error("--judge-model needs managed scoring.")
    return options


def main(argv=None):
    options = parse_options(argv)
    versions = {"triage": options.triage_version, "reviewer": options.reviewer_version}
    pack = pack_file.new_pack()
    exit_code = 2
    output_dir = None
    telemetry_on = False
    run_span = None
    run_context = None

    try:
        # 1. Read the input files, and reject anything that couldn't produce a meaningful result.
        dataset_path = Path(options.dataset or ROOT / "data" / "lab1-evaluations.json").resolve()
        cases_path = ROOT / "data" / "cases.json"
        dataset = data.load_json(dataset_path)
        case_files = data.load_cases(cases_path)
        data.validate(dataset, case_files)
        replay = None if options.replay is None else data.load_replay(options.replay, dataset)

        # 2. Create a new Evidence Pack folder. An existing folder is refused, so earlier evidence is never
        #    overwritten.
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        folder = Path(options.output or ROOT / ".artifacts" / "lab1" / f"{stamp}-{pack['run_id']}").resolve()
        if folder.exists():
            raise ValueError("The output path must not already exist; choose a new directory.")
        folder.mkdir(parents=True)
        output_dir = folder

        pack["execution"] = "live_pinned_foundry" if replay is None else "offline_replay"
        pack["mode"] = "local_and_managed" if options.scoring == "managed" else "local_deterministic"
        pack["managed_evaluation_status"] = "not_started" if options.scoring == "managed" else "skipped"
        pack["dataset_version"] = dataset["version"]
        pack["dataset_sha256"] = sha256(dataset_path.read_bytes())
        pack["cases_sha256"] = sha256(cases_path.read_bytes())
        pack["runner_sha256"] = sha256(b"".join(path.read_bytes() for path in sorted(RUNNER_DIR.glob("*.py"))))
        pack["replay_sha256"] = None if options.replay is None else sha256(Path(options.replay).read_bytes())
        pack_file.save(folder, pack)

        # 3. Live run: connect to Foundry and Application Insights, and pin the exact agent versions to evaluate.
        foundry = None
        judge_model = None
        instructions = {}
        if replay is None:
            settings = load_dotenv(ROOT / ".env")
            foundry = Foundry(required(settings, "FOUNDRY_PROJECT_ENDPOINT"))
            if options.scoring == "managed":
                judge_model = options.judge_model or required(settings, "MODEL_DEPLOYMENT")
            telemetry.start(required(settings, "APPLICATIONINSIGHTS_CONNECTION_STRING"), foundry.endpoint)
            telemetry_on = True
            pack["telemetry_status"] = "enabled"

            # Every span in this run belongs to one trace. Its ID is in the Evidence Pack, for Application Insights.
            run_span = telemetry.tracer.start_span("lab1.evaluation_run", attributes={
                "lab1.run_id": pack["run_id"], "lab1.runner": pack["runner"],
                "lab1.dataset_version": dataset["version"], "lab1.scoring": options.scoring})
            run_context = context.attach(trace.set_span_in_context(run_span))
            pack["trace_id"] = format(run_span.get_span_context().trace_id, "032x")

            pack["agents"] = {}
            for agent in AGENTS:
                model, instructions[agent] = foundry.get_agent_version(agent, versions[agent])
                pack["agents"][agent] = {"version": versions[agent], "model_deployment": model,
                                         "instructions_sha256": sha256(instructions[agent].encode("utf-8"))}
                run_span.set_attribute(f"lab1.{agent}.version", versions[agent])
            pack_file.save(folder, pack)

        def ask(item_id, agent, input_text):
            """Asks an agent for an answer, or reads its recorded answer in a replay. Each live call gets a span."""
            if replay is not None:
                return {"text": replay[item_id], "response_id": None, "request_id": None, "duration_ms": 0.0}
            with telemetry.tracer.start_as_current_span("lab1.agent") as span:
                span.set_attributes({"lab1.run_id": pack["run_id"], "lab1.item_id": item_id,
                                     "gen_ai.agent.name": agent, "gen_ai.agent.version": versions[agent]})
                reply = foundry.ask(agent, versions[agent], input_text)
                span.set_attributes({"gen_ai.response.id": reply["response_id"],
                                     "foundry.request_id": reply["request_id"] or ""})
                reply["trace_id"] = format(span.get_span_context().trace_id, "032x")
                reply["span_id"] = format(span.get_span_context().span_id, "016x")
                return reply

        def record(item_id, agent, alert_id, input_text, reply, results):
            """Records an answer and its check results, prints the verdict, and saves the Evidence Pack so far."""
            pack["items"].append({
                "id": item_id, "agent": agent, "alert_id": alert_id, "input": input_text, "output": reply["text"],
                "response_id": reply["response_id"], "request_id": reply["request_id"],
                "duration_ms": reply["duration_ms"], "trace_id": reply.get("trace_id"),
                "span_id": reply.get("span_id"), "checks": results})
            passed = sum(r["passed"] for r in results)
            print(f"{'PASS' if passed == len(results) else 'FAIL'} {item_id}: {passed}/{len(results)} checks, "
                  f"{reply['duration_ms']:.0f} ms")
            for result in results:
                if not result["passed"]:
                    print(f"  {result['name']}: {result['detail']}")
            telemetry.record_item(pack["run_id"], item_id, agent, reply["duration_ms"], results)
            pack_file.save(folder, pack)

        # 4. Run every case: triage recommends, then the reviewer checks that recommendation.
        for expected in dataset["cases"]:
            alert_id = expected["alert_id"]
            case_input = compact(case_files[alert_id])
            triage_id = data.triage_item(alert_id)
            triage = ask(triage_id, "triage", case_input)
            record(triage_id, "triage", alert_id, case_input, triage,
                   checks.triage(triage["text"], expected, checks.valid_ids(case_files[alert_id])))

            review_input = reviewer_input(case_input, triage["text"])
            review_id = data.workflow_review_item(alert_id)
            review = ask(review_id, "reviewer", review_input)
            record(review_id, "reviewer", alert_id, review_input, review, checks.reviewer(review["text"]))

        # 5. Run the reviewer challenges: authored recommendations where the right verdict is known in advance.
        for challenge in dataset["reviewer_challenges"]:
            review_input = reviewer_input(compact(case_files[challenge["alert_id"]]), compact(challenge["recommendation"]))
            item_id = data.challenge_item(challenge["id"])
            review = ask(item_id, "reviewer", review_input)
            record(item_id, "reviewer", challenge["alert_id"], review_input, review,
                   checks.reviewer(review["text"], challenge["expected_verdict"]))

        # 6. Local gate: every deterministic check must pass.
        all_checks = [c for item in pack["items"] for c in item["checks"]]
        passed_checks = sum(c["passed"] for c in all_checks)
        pack["summary"] = {"items": len(pack["items"]), "checks": len(all_checks), "passed": passed_checks,
                           "failed": len(all_checks) - passed_checks}
        pack["local_gate"] = "pass" if passed_checks == len(all_checks) else "fail"
        exit_code = 0 if pack["local_gate"] == "pass" else 1
        print(f"Local gate: {pack['local_gate'].upper()}.")

        # 7. Managed gate: Foundry scores the recorded answers. The release gate needs both gates to pass.
        if options.scoring == "managed":
            print("Scoring the recorded answers in Foundry (the agents aren't called again).")
            managed = pack["managed_evaluation"] = pack_file.new_managed_evaluation(judge_model)
            pack["managed_evaluation_status"] = "running"
            with telemetry.tracer.start_as_current_span("lab1.managed_evaluation") as span:
                try:
                    managed_scoring.run(foundry, managed, pack["items"], instructions, pack["run_id"],
                                        lambda: pack_file.save(folder, pack))
                finally:
                    span.set_attributes({"foundry.eval.id": managed["evaluation_id"] or "",
                                         "foundry.eval.run_id": managed["run_id"] or "",
                                         "lab1.managed_gate": managed["gate"]})
            pack["managed_evaluation_status"] = managed["status"]
            agent_of = {item["id"]: item["agent"] for item in pack["items"]}
            for item in managed["items"]:
                for score in item["metrics"]:
                    telemetry.record_score(pack["run_id"], item["item_id"], agent_of[item["item_id"]], score)
            pack["release_gate"] = "pass" if pack["local_gate"] == "pass" and managed["gate"] == "pass" else "fail"
            exit_code = 0 if pack["release_gate"] == "pass" else 1
            print(f"Managed gate: {managed['gate'].upper()}; every evaluator must score at least "
                  f"{managed_scoring.MINIMUM_SCORE}/5 on every item.")
        else:
            print("Local-only run: it can't approve a release.")

    except Exception as error:  # noqa: BLE001 - any failure means no valid assessment, so exit code 2
        # Anything unexpected (bad input, sign-in, network, Foundry) means no valid assessment: never a pass.
        exit_code = 2
        pack["error"] = str(error)
        pack["release_gate"] = "execution_error"
        if pack["summary"] is None:
            pack["local_gate"] = "execution_error"
        if pack["managed_evaluation_status"] == "running":
            pack["managed_evaluation_status"] = "execution_error"
        print(f"Evaluation could not complete: {error}", file=sys.stderr)

    finally:
        # 8. Close the run's trace and send all telemetry before the process exits. If it can't be sent, the run
        #    can't pass: its traces would be missing.
        telemetry.record_run(pack["release_gate"])
        if run_span is not None:
            run_span.set_attributes({"lab1.local_gate": pack["local_gate"], "lab1.release_gate": pack["release_gate"]})
            if exit_code != 0:
                run_span.set_status(Status(StatusCode.ERROR,
                                           "Quality gate failed." if exit_code == 1 else "Run couldn't complete."))
            run_span.end()
            context.detach(run_context)
        if telemetry_on:
            sent = telemetry.flush()
            pack["telemetry_status"] = "flush_completed" if sent else "flush_failed"
            if not sent:
                exit_code = 2
                pack["release_gate"] = "execution_error"
                pack["error"] = pack["error"] or "Telemetry couldn't be sent to Application Insights."
                print("Telemetry couldn't be sent to Application Insights, so this run can't pass.", file=sys.stderr)

        # Only a complete live run, with both gates passed and its telemetry sent, is release-ready.
        pack["release_ready"] = (exit_code == 0 and pack["release_gate"] == "pass"
                                 and pack["execution"] == "live_pinned_foundry"
                                 and pack["telemetry_status"] == "flush_completed")
        pack["exit_code"] = exit_code
        pack["finished_at"] = pack_file.now()
        if output_dir is not None:
            try:
                pack_file.save(output_dir, pack)
                print(f"Evidence Pack: {output_dir}")
            except OSError as error:
                exit_code = 2
                print(f"Could not save the Evidence Pack: {error}", file=sys.stderr)
    return exit_code


def compact(value):
    """JSON without spaces, as the agents receive it."""
    return json.dumps(value, separators=(",", ":"))


def reviewer_input(case_file, recommendation):
    """The reviewer's input: the case file, then the recommendation to check."""
    return f"Case file:\n{case_file}\n\nTriage recommendation:\n{recommendation}"


def sha256(content):
    """A SHA-256 fingerprint, so an approver can tell whether an input changed between runs."""
    return hashlib.sha256(content).hexdigest()


def load_dotenv(path):
    """Reads the KEY=value lines that scripts\\env.ps1 writes to .env."""
    if not path.exists():
        raise ValueError(r"No .env yet. Run: .\scripts\env.ps1 -ResourceGroup <your-resource-group>")
    lines = path.read_text(encoding="utf-8-sig").splitlines()
    return dict(line.split("=", 1) for line in lines if "=" in line)


def required(settings, name):
    value = settings.get(name, "")
    if not value.strip():
        raise ValueError(rf"Missing {name} in .env. Run .\scripts\env.ps1 again.")
    return value


if __name__ == "__main__":
    sys.exit(main())
