"""Offline checks for starting the generated walkthrough at a numbered step."""

import contextlib
import io
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

import run_lab as lab


class StartStepTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.write(lab.GUIDE, (lab.ROOT / lab.GUIDE).read_bytes())
        self.write(lab.PROMPT, b"Approved prompt\n")
        self.write("src/agents/variants/triage.concise.md", b"Concise prompt\n")
        self.write("src/agents/variants/triage.helpful.md", b"Helpful prompt\n")
        self.write(lab.DATA_POLICY, b'{"enforce": true}\n')
        self.write(lab.REGISTER, b'[{"id": "UC-001", "approval": {"fixture": true}}]\n')
        self.write(".env", b"AZURE_RESOURCE_GROUP=fixture\n")
        self.steps = lab.build_steps()

    def write(self, relative, content):
        path = self.root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
        return path

    def number(self, predicate):
        return next(index for index, step in enumerate(self.steps, 1) if predicate(step))

    @contextlib.contextmanager
    def preflight_environment(self, baseline=b"Approved prompt\n", code=0):
        with (patch.object(lab.sys.stdin, "isatty", return_value=True),
              patch.object(lab.shutil, "which", return_value="fixture-tool"),
              patch.object(lab, "confirm", return_value=True) as confirm,
              patch.object(lab.subprocess, "run",
                           return_value=subprocess.CompletedProcess([], code, stdout=baseline)) as git,
              contextlib.redirect_stdout(io.StringIO())):
            yield git, confirm

    def test_cli_defaults_to_first_step_and_passes_explicit_selection(self):
        for arguments, expected in (([], 1), (["--start-step", "17"], 17)):
            with self.subTest(arguments=arguments), patch.object(lab, "Runner") as runner:
                runner.return_value.run.return_value = 0
                self.assertEqual(lab.main(arguments), 0)
                self.assertEqual(runner.call_args.kwargs["start_step"], expected)
                runner.return_value.run.assert_called_once_with(execute=False)

    def test_cli_rejects_invalid_steps_before_creating_runner(self):
        for value in ("0", "-1", str(len(self.steps) + 1), "one", "1.5"):
            with (self.subTest(value=value), patch.object(lab, "Runner") as runner,
                  contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as stopped):
                lab.main(["--execute", "--start-step", value])
            self.assertEqual(stopped.exception.code, 2)
            runner.assert_not_called()

    def test_every_start_step_executes_only_its_inclusive_suffix(self):
        for number in range(1, len(self.steps) + 1):
            with self.subTest(number=number):
                runner = lab.Runner(self.root, start_step=number, optional=lab.OPTIONAL_GROUPS)
                with (patch.object(runner, "preflight"),
                      patch.object(runner, "action", return_value="PASS") as action,
                      contextlib.redirect_stdout(io.StringIO())):
                    self.assertEqual(runner.run(execute=True), 0)
                self.assertEqual([call.args[0] for call in action.call_args_list], self.steps[number - 1:])
                self.assertEqual([item.number for item in runner.outcomes], list(range(1, len(self.steps) + 1)))
                self.assertTrue(all(item.status == "NOT RUN" for item in runner.outcomes[:number - 1]))
                self.assertTrue(all(item.status == "PASS" for item in runner.outcomes[number - 1:]))

    def test_partial_plan_has_no_side_effects_and_keeps_source_numbering(self):
        number = self.number(lambda step: step.kind == "enforce")
        runner = lab.Runner(self.root, start_step=number)
        forbidden = AssertionError("Partial plan attempted live work")
        output = io.StringIO()
        with (contextlib.redirect_stdout(output),
              patch.object(lab.subprocess, "run", side_effect=forbidden),
              patch.object(lab.subprocess, "Popen", side_effect=forbidden),
              patch.object(lab.shutil, "which", side_effect=forbidden),
              patch.object(lab.time, "sleep", side_effect=forbidden),
              patch.object(lab.urllib.request, "build_opener", side_effect=forbidden),
              patch("builtins.input", side_effect=forbidden),
              patch.object(Path, "write_bytes", side_effect=forbidden),
              patch.object(Path, "write_text", side_effect=forbidden)):
            self.assertEqual(runner.run(), 0)
        self.assertIn(f"Selected steps: {number}-{len(self.steps)}", output.getvalue())
        self.assertEqual(runner.outcomes[number - 1].status, "PLAN")
        self.assertTrue(all(item.status == "NOT RUN" and "--start-step" in item.detail
                            for item in runner.outcomes[:number - 1]))

    def test_resumed_preflight_preserves_enforcement_and_approval(self):
        number = self.number(lambda step: step.expect == "intake-approve")
        runner = lab.Runner(self.root, start_step=number)
        before = {path: path.read_bytes() for path in self.root.rglob("*") if path.is_file()}
        with self.preflight_environment() as (git, confirm):
            runner.preflight()
        self.assertEqual({path: path.read_bytes() for path in before}, before)
        git.assert_called_once()
        self.assertEqual(git.call_args.args[0][1:], ["show", "HEAD:src/agents/triage.md"])
        confirm.assert_called_once()

    def test_full_start_still_requires_fresh_policy(self):
        runner = lab.Runner(self.root)
        with self.preflight_environment() as (git, confirm):
            with self.assertRaisesRegex(lab.WalkthroughError, "before data-policy"):
                runner.preflight()
        git.assert_not_called()
        confirm.assert_not_called()

    def test_resume_requires_setup_artifacts_without_replaying_setup(self):
        number = self.number(lambda step: "create-agents" in step.args)
        for missing in ("env", "baseline"):
            with self.subTest(missing=missing):
                runner = lab.Runner(self.root, "fixture", start_step=number)
                env = self.root / ".env"
                if missing == "env":
                    env.unlink()
                else:
                    self.write(".env", b"AZURE_RESOURCE_GROUP=fixture\n")
                with self.preflight_environment(code=1) as (git, confirm):
                    with self.assertRaisesRegex(lab.WalkthroughError, "existing .env|Git baseline"):
                        runner.preflight()
                self.assertEqual(git.call_count, int(missing == "baseline"))
                confirm.assert_not_called()

    def test_resume_does_not_overwrite_unrelated_prompt_edits(self):
        number = self.number(lambda step: step.kind == "copy")
        path = self.write(lab.PROMPT, b"Unrelated work\n")
        runner = lab.Runner(self.root, start_step=number)
        with self.preflight_environment():
            with self.assertRaisesRegex(lab.WalkthroughError, "unrelated edits"):
                runner.preflight()
        self.assertEqual(path.read_bytes(), b"Unrelated work\n")

    def test_resumed_rollback_keeps_approved_prompt_on_success_and_startup_bytes_on_error(self):
        number = self.number(lambda step: step.kind == "restore")
        for outcome in ("success", "error", "cancelled"):
            with self.subTest(outcome=outcome):
                failure = outcome != "success"
                path = self.write(lab.PROMPT, b"Concise prompt\n")
                runner = lab.Runner(self.root, start_step=number)
                with self.preflight_environment():
                    runner.preflight()
                self.assertEqual(runner.approved_prompt, b"Approved prompt\n")
                runner.steps = runner.steps[:number - 1] + [
                    self.steps[number - 1],
                    lab.Step(433, 437, "Publish restored prompt", args=("uv", "run", "create-agents")),
                    lab.Step(433, 437, "Finish", args=("fixture",)),
                ]
                responses = [(0, ""), lab.Cancelled("Cancelled fixture") if outcome == "cancelled"
                             else (2 if failure else 0, "")]
                with (patch.object(runner, "preflight"),
                      patch.object(runner, "process", side_effect=responses),
                      contextlib.redirect_stdout(io.StringIO())):
                    self.assertEqual(runner.run(execute=True), int(failure))
                self.assertEqual(path.read_bytes(), b"Concise prompt\n" if failure else b"Approved prompt\n")

    def test_windows_resume_matches_git_baseline_and_restores_checkout_newlines(self):
        number = self.number(lambda step: step.kind == "restore")
        for startup in (b"Approved prompt\r\n", b"Concise prompt\r\n"):
            with self.subTest(startup=startup):
                self.write(lab.PROMPT, startup)
                self.write("src/agents/variants/triage.concise.md", b"Concise prompt\r\n")
                runner = lab.Runner(self.root, start_step=number)
                with self.preflight_environment():
                    runner.preflight()
                runner.action(self.steps[number - 1])
                self.assertEqual((self.root / lab.PROMPT).read_bytes(), b"Approved prompt\r\n")

    def test_partial_report_keeps_earlier_steps_and_cancelled_suffix_not_run(self):
        number = len(self.steps)
        for cancelled in (False, True):
            with self.subTest(cancelled=cancelled):
                runner = lab.Runner(self.root, start_step=number)
                output = io.StringIO()
                with (patch.object(runner, "preflight",
                                   side_effect=lab.Cancelled("Cancelled fixture") if cancelled else None),
                      contextlib.redirect_stdout(output)):
                    self.assertEqual(runner.run(execute=True), int(cancelled))
                report = output.getvalue().split("Walkthrough report")[-1]
                self.assertIn("[NOT RUN] Step 01:", report)
                self.assertNotIn("Automated flow finished", report)
                self.assertIn("INCOMPLETE" if cancelled else "Selected flow finished", report)
                if cancelled:
                    self.assertTrue(all(item.status == "NOT RUN" for item in runner.outcomes))

    def test_optional_selection_does_not_renumber_steps_or_replay_prior_optional_actions(self):
        number = self.number(lambda step: step.optional == "tuning")
        for optional in ((), ("tuning",)):
            with self.subTest(optional=optional):
                runner = lab.Runner(self.root, start_step=number, optional=optional)
                with (patch.object(runner, "preflight"),
                      patch.object(runner, "action", return_value="PASS") as action,
                      contextlib.redirect_stdout(io.StringIO())):
                    self.assertEqual(runner.run(execute=True), 0)
                self.assertEqual(runner.outcomes[number - 1].number, number)
                self.assertEqual(runner.outcomes[number - 1].status, "PASS" if optional else "SKIPPED OPTIONAL")
                self.assertTrue(all(call.args[0] in self.steps[number - 1:] for call in action.call_args_list))

    def test_resumed_source_drift_still_blocks_startup(self):
        self.write(lab.GUIDE, b"Changed guide\n")
        with self.assertRaisesRegex(lab.WalkthroughError, "instructions changed"):
            lab.Runner(self.root, start_step=len(self.steps))

    def test_refreshed_cost_query_and_caller_review_are_planned_but_not_verified(self):
        number = self.number(lambda step: step.title == "Attribute cost by cost center")
        runner = lab.Runner(self.root, start_step=number)
        output = io.StringIO()
        with (contextlib.redirect_stdout(output),
              patch.object(runner, "process", side_effect=AssertionError("Plan executed a command"))):
            self.assertEqual(runner.run(), 0)
        text = output.getvalue()
        self.assertIn('Caller = tostring(customDimensions["Caller"])', text)
        self.assertIn("| summarize tokens = sum(valueSum) by CostCenter, Caller, Agent, name", text)
        self.assertIn("Attribute cost by caller", text)
        self.assertIn("by Caller", " ".join(text.split()))
        self.assertNotIn("query discrepancy", text)
        self.assertTrue(all(item.status == "PLAN" for item in runner.outcomes[number - 1:]))

    def test_refreshed_web_iq_and_policy_guidance_is_visible_without_execution(self):
        runner = lab.Runner(self.root)
        output = io.StringIO()
        with (contextlib.redirect_stdout(output),
              patch.object(runner, "process", side_effect=AssertionError("Plan executed a command")),
              patch("builtins.input", side_effect=AssertionError("Plan requested input"))):
            self.assertEqual(runner.run(), 0)
        text = " ".join(output.getvalue().split())
        self.assertIn("Select Skip on the 'You're ready to use WebIQ' screen", text)
        self.assertIn("billing resource shown in the terminal", text)
        self.assertIn("paste it into the hidden prompt", text)
        self.assertIn("Web IQ calls are billable", text)
        self.assertIn("AuthorizationFailed demonstrates role-based refusal instead", text)
        self.assertIn("policy remains unverified", text)
        self.assertTrue(all(item.status == "PLAN" for item in runner.outcomes))

    def test_resume_in_gateway_exercise_reports_recovery_when_command_fails(self):
        number = self.number(lambda step: step.kind == "logged-investigation")
        runner = lab.Runner(self.root, start_step=number)
        output = io.StringIO()
        with (self.preflight_environment(),
              patch.object(runner, "process", return_value=(2, "")),
              contextlib.redirect_stdout(output)):
            self.assertEqual(runner.run(execute=True), 1)
        self.assertIn("uv run set-gateway token-limit 200000", output.getvalue())
        self.assertEqual(json.loads((self.root / lab.DATA_POLICY).read_text()), {"enforce": True})


class DecisionModelTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.write(lab.GUIDE, (lab.ROOT / lab.GUIDE).read_bytes())
        self.write("data/golden.jsonl", (lab.ROOT / "data/golden.jsonl").read_bytes())
        self.write(lab.REGISTER, b'[{"id":"UC-001","approval":null}]\n')
        self.write("evidence/decisions.jsonl", b"Existing learner records\n")
        self.golden = {row["alert_id"]: row["expected_disposition"] for row in
                       map(json.loads, (self.root / "data/golden.jsonl").read_text().splitlines())}
        self.runner = lab.Runner(self.root)
        self.reports = 0

    def write(self, relative, content):
        path = self.root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
        return path

    def step(self, expect, optional=""):
        return next(step for step in self.runner.steps if step.expect == expect and step.optional == optional)

    def report(self, step):
        alerts = [arg for arg in step.args if arg.startswith("ALT-")] or list(self.golden)
        rows = [{"alert_id": alert, "expected": self.golden[alert],
                 "triage": {"status": "ok", "disposition": self.golden[alert]},
                 "review": {"status": "ok", "verdict": "agree"}} for alert in alerts]
        stats = {"alerts": len(alerts), "triage_correct": len(alerts), "reviewer_agrees": len(alerts),
                 "reviewer_disagrees": 0, "unavailable": 0}
        if step.expect == "choice-review":
            challenges = [{"alert_id": alert, "expected": self.golden[alert], "recommended": label,
                           "review": {"status": "ok", "verdict": "disagree"}}
                          for alert in alerts for label in ("escalate", "close_false_positive", "request_info")
                          if label != self.golden[alert]]
            return {"rows": rows, "challenges": challenges, "gpt_calls": 0,
                    "summary": stats | {"challenges": len(challenges), "challenges_caught": len(challenges),
                                        "challenges_unavailable": 0}}
        classic = [{"alert_id": row["alert_id"], "expected": row["expected"], "status": "ok",
                    "disposition": row["expected"], "verdict": "agree"} for row in rows]
        return {"alerts": alerts, "workers": 2, "classic": {"rows": classic}, "decision1": {"rows": rows},
                "metrics": {"Classic (GPT)": dict(stats), "Decision-1": dict(stats)}}

    def run_report(self, step, report, code=0):
        def process(args, **kwargs):
            self.assertEqual(args, list(step.args))
            self.assertFalse(kwargs["interactive"])
            self.reports += 1
            self.write(Path(lab.DECISION_REPORTS[step.expect]) / f"run-{self.reports}" / "results.json",
                       json.dumps(report).encode())
            return code, "Fixture output streamed\n"

        with patch.object(self.runner, "process", side_effect=process), contextlib.redirect_stdout(io.StringIO()):
            return self.runner.command(step)

    def test_new_commands_follow_source_and_optional_runs_do_not_replay_publication(self):
        source = [command for _, _, command in self.runner.guide.commands if "src/decisions/" in command]
        required = [step for step in self.runner.steps
                    if step.expect in (*lab.DECISION_REPORTS, "choice-explain") and not step.optional]
        self.assertEqual(len(source), len(required))
        self.assertTrue(all(any(self.runner.guide.matches(command, step) for step in required) for command in source))
        comparison = self.step("investigation-compare")
        self.assertEqual(len([arg for arg in comparison.args if arg.startswith("ALT-")]), 7)
        self.assertEqual(comparison.args[-2:], ("--workers", "2"))
        self.assertIn("ALT-1005", comparison.args)
        start = self.runner.steps.index(self.step("choice-explain")) + 1
        for groups in ((), ("decision-json",), ("full-comparison",), ("decision-json", "full-comparison")):
            with self.subTest(groups=groups):
                runner = lab.Runner(self.root, start_step=start, optional=groups)
                with (patch.object(runner, "preflight"),
                      patch.object(runner, "action", return_value="PASS") as action,
                      contextlib.redirect_stdout(io.StringIO())):
                    self.assertEqual(runner.run(execute=True), 0)
                executed = [call.args[0] for call in action.call_args_list]
                self.assertTrue(all(step in executed for step in required))
                self.assertFalse(any("create-agents" in step.args for step in executed))
                for step in (self.step("choice-json", "decision-json"),
                             self.step("investigation-compare", "full-comparison")):
                    self.assertEqual(step in executed, step.optional in groups)
                    index = runner.steps.index(step)
                    self.assertEqual(runner.outcomes[index].number, index + 1)
                    self.assertEqual(runner.outcomes[index].status,
                                     "PASS" if step.optional in groups else "SKIPPED OPTIONAL")

    def test_successful_reports_use_real_commands_without_writing_learner_approvals(self):
        paths = (lab.REGISTER, Path("evidence/decisions.jsonl"))
        before = {path: (self.root / path).read_bytes() for path in paths}
        for step in (self.step("choice-review"), self.step("investigation-compare"),
                     self.step("investigation-compare", "full-comparison")):
            with self.subTest(title=step.title):
                self.assertEqual(self.run_report(step, self.report(step)), "PASS")
        self.assertEqual({path: (self.root / path).read_bytes() for path in paths}, before)
        self.assertFalse((self.root / "tests/e2e/artifacts/decisions.jsonl").exists())

    def test_quality_mismatches_are_observed_but_must_match_pure_runner_exit(self):
        step = self.step("choice-review")
        for kind in ("triage", "challenge"):
            report = self.report(step)
            if kind == "triage":
                report["rows"][0]["triage"]["disposition"] = "request_info"
                report["summary"]["triage_correct"] = 0
            else:
                report["challenges"][0]["review"]["verdict"] = "agree"
                report["summary"]["challenges_caught"] -= 1
            with self.subTest(kind=kind):
                self.assertEqual(self.run_report(step, report, 1), "OBSERVED FAIL")
                with self.assertRaisesRegex(lab.WalkthroughError, "exit code"):
                    self.run_report(step, report, 0)
        step = self.step("investigation-compare")
        report = self.report(step)
        report["classic"]["rows"][0]["disposition"] = next(
            label for label in ("escalate", "request_info") if label != report["classic"]["rows"][0]["expected"])
        report["metrics"]["Classic (GPT)"]["triage_correct"] -= 1
        self.assertEqual(self.run_report(step, report), "OBSERVED FAIL")

    def test_unavailable_calls_are_rejected_even_when_exit_is_zero(self):
        for expect, path in (("choice-review", ("rows", 0, "triage")),
                             ("choice-review", ("rows", 0, "review")),
                             ("choice-review", ("challenges", 0, "review")),
                             ("investigation-compare", ("classic", "rows", 0)),
                             ("investigation-compare", ("decision1", "rows", 0, "review"))):
            step = self.step(expect)
            report = self.report(step)
            row = report
            for key in path:
                row = row[key]
            row["status"] = "unavailable"
            with self.subTest(expect=expect, path=path):
                with self.assertRaisesRegex(lab.WalkthroughError, "unavailable"):
                    self.run_report(step, report)

    def test_report_must_be_fresh_complete_and_consistent(self):
        for expect in lab.DECISION_REPORTS:
            step = self.step(expect)
            self.run_report(step, self.report(step))
            with (self.subTest(expect=expect, issue="stale"),
                  patch.object(self.runner, "process", return_value=(0, "")),
                  self.assertRaisesRegex(lab.WalkthroughError, "fresh Decision-1 report")):
                self.runner.command(step)
            for issue in ("duplicate", "metric", "golden"):
                report = self.report(step)
                rows = report["rows"] if expect == "choice-review" else report["decision1"]["rows"]
                if issue == "duplicate":
                    rows.append(rows[0])
                elif issue == "metric":
                    stats = report["summary"] if expect == "choice-review" else report["metrics"]["Decision-1"]
                    stats["triage_correct"] -= 1
                else:
                    rows[0]["expected"] = "made-up"
                with self.subTest(expect=expect, issue=issue), self.assertRaises(lab.WalkthroughError):
                    self.run_report(step, report)
        step = self.step("choice-review")
        report = self.report(step)
        report["challenges"].pop()
        with self.assertRaisesRegex(lab.WalkthroughError, "challenges"):
            self.run_report(step, report)

    def test_readable_explanation_requires_a_completed_reviewer(self):
        step = self.step("choice-explain")
        output = (f"ALT-1001: Decision-1 chooses {self.golden['ALT-1001']} (90% confident)\n"
                  "Reviewer: agree (90% confident)\n\nWhat was called, and what it cost\n"
                  "  1. Decision-1 chooses the disposition  0.1 s  $0.00001\n\n"
                  "Reasons\n  - Fixture reason [TX-001]\n")
        self.assertEqual(self.runner.check_result(step, 0, output, None), "PASS")
        for incomplete in ("ALT-1001: Decision-1 was unavailable (fixture)\n",
                           output.replace("Reviewer: agree (90% confident)\n", "")):
            with self.subTest(output=incomplete), self.assertRaisesRegex(lab.WalkthroughError, "unavailable"):
                self.runner.check_result(step, 0, incomplete, None)
        self.assertEqual(self.runner.check_result(
            step, 0, output.replace("Reviewer: agree", "Reviewer: disagree"), None), "OBSERVED FAIL")

    def test_optional_json_checks_actual_outcomes_despite_trailing_cli_text(self):
        step = self.step("choice-json", "decision-json")
        record = {"alert_id": "ALT-1001", "assessment": {"status": "ok", "disposition": self.golden["ALT-1001"]},
                  "explanation": {"disposition": self.golden["ALT-1001"], "reasons": ["Fixture [TX-001]"]},
                  "review": {"status": "ok", "verdict": "agree"}}
        output = json.dumps(record) + "\nThe agents recommend. A named analyst decides.\n"
        self.assertEqual(self.runner.check_result(step, 0, output, None), "PASS")
        record["review"]["status"] = "unavailable"
        with self.assertRaisesRegex(lab.WalkthroughError, "unavailable"):
            self.runner.check_result(step, 0, json.dumps(record), None)


if __name__ == "__main__":
    unittest.main()
