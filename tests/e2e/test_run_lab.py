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


if __name__ == "__main__":
    unittest.main()
