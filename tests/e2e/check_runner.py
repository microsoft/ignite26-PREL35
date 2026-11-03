"""Offline runner checks. Run directly; not collected as part of the project test suite.

    uv run --no-sync tests/e2e/check_runner.py

All offline e2e checks:
    uv run --no-sync python -B -m unittest discover -s tests/e2e -p "check_*.py"
"""

import contextlib
import io
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch
import urllib.error

import run_lab as lab
import walkthrough_console as console


class RunnerChecks(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.write(lab.GUIDE, (lab.ROOT / lab.GUIDE).read_bytes())
        self.write(lab.PROMPT, b"Approved starting prompt\n")
        self.write("src/agents/variants/triage.concise.md", b"Concise fixture\n")
        self.write(lab.DATA_POLICY, {"enforce": False, "fields": {"fixture": {"agents": []}}})
        self.write(lab.REGISTER, [{"id": "UC-001", "approval": None}])
        self.write("data/golden.jsonl", b'{"alert_id":"A"}\n{"alert_id":"B"}\n')
        self.write("evals/attacks.json", [{"id": "X"}, {"id": "Y"}])
        self.runner = lab.Runner(self.root, "fixture")
        self.runner.original_prompt = (self.root / lab.PROMPT).read_bytes()

    def write(self, relative, value):
        path = self.root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        if isinstance(value, bytes):
            path.write_bytes(value)
        else:
            path.write_text(json.dumps(value), encoding="utf-8")
        return path

    def make_pack(self, verdict="PASS", *, redteam=False, name="new"):
        directory = Path("evidence") / (name + ("-redteam" if redteam else ""))
        manifest = {"verdict": verdict, "agents": {"triage": {"version": 1}}, "checks": ["fixture"]}
        self.write(directory / "manifest.json", manifest)
        rows = [{"id": "X", "outcome": "SUCCEEDED" if verdict == "FAIL" else "resisted"},
                {"id": "Y", "outcome": "blocked"}] if redteam else [{"alert_id": "A"}, {"alert_id": "B"}]
        self.write(directory / "results.json", rows)
        label = "Red team" if redteam else "Release gate"
        self.write(directory / "report.md", f"# {label}: {verdict}\n".encode())
        return directory

    def quiet(self, callback, *args, **kwargs):
        with contextlib.redirect_stdout(io.StringIO()):
            return callback(*args, **kwargs)

    def test_plan_is_complete_and_has_no_execution_side_effects(self):
        output = io.StringIO()
        forbidden = AssertionError("Plan attempted a side effect")
        with (contextlib.redirect_stdout(output),
              patch.object(lab.subprocess, "Popen", side_effect=forbidden),
              patch.object(lab.subprocess, "run", side_effect=forbidden),
              patch.object(lab.shutil, "which", side_effect=forbidden),
              patch.object(lab.time, "sleep", side_effect=forbidden),
              patch.object(lab.urllib.request, "build_opener", side_effect=forbidden),
              patch("builtins.input", side_effect=forbidden),
              patch.object(Path, "write_text", side_effect=forbidden),
              patch.object(Path, "write_bytes", side_effect=forbidden)):
            self.assertEqual(self.runner.run(), 0)
        for phase in ("Lab 0", "Lab 1", "Lab 2", "Lab 3", "Lab 4"):
            self.assertIn(phase, output.getvalue())
        self.assertIn("43 command/query blocks mapped", output.getvalue())
        self.assertIn("Kusto", output.getvalue())

    def test_source_drift_blocks_runner_creation(self):
        with (self.root / lab.GUIDE).open("ab") as stream:
            stream.write(b"\nChanged instructions\n")
        with self.assertRaisesRegex(lab.WalkthroughError, "instructions changed"):
            lab.Runner(self.root)

    def test_windows_line_endings_do_not_count_as_source_drift(self):
        path = self.root / lab.GUIDE
        # The checkout may already be CRLF (core.autocrlf), so normalize before converting.
        path.write_bytes(path.read_bytes().replace(b"\r\n", b"\n").replace(b"\n", b"\r\n"))
        lab.Runner(self.root)

    def test_removed_command_block_mapping_is_detected(self):
        steps = [step for step in self.runner.steps if not (step.start <= 251 and step.end >= 253)]
        with self.assertRaisesRegex(lab.WalkthroughError, "251"):
            self.runner.guide.check_coverage(steps)

    def test_missing_command_inside_a_still_mapped_block_is_detected(self):
        steps = [step for step in self.runner.steps if not (step.start == 411 and step.expect == "pack-fail")]
        with self.assertRaisesRegex(lab.WalkthroughError, "414"):
            self.runner.guide.check_coverage(steps)

    def test_mismatched_environment_is_not_retargeted(self):
        path = self.write(".env", b"AZURE_RESOURCE_GROUP=another-lab\nWEBIQ_API_KEY=do-not-print\n")
        before = path.read_bytes()
        with self.assertRaisesRegex(lab.WalkthroughError, "differs from .env"):
            self.runner.resolve(("uv", "run", "setup-lab", "--resource-group", "{resource_group}"))
        self.assertEqual(path.read_bytes(), before)

    def test_unresolved_placeholders_block_commands(self):
        with self.assertRaisesRegex(lab.WalkthroughError, "Missing FOUNDRY_NAME"):
            self.runner.resolve(("az", "{FOUNDRY_NAME}"))

    def test_preexisting_user_prompt_changes_are_not_overwritten(self):
        path = self.root / lab.PROMPT
        path.write_bytes(b"User edits\n")
        with self.assertRaisesRegex(lab.WalkthroughError, "changed outside"):
            self.runner.set_prompt(b"Variant\n")
        self.assertEqual(path.read_bytes(), b"User edits\n")

    def test_unexpected_error_stops_order_and_restores_only_owned_prompt(self):
        self.runner.steps = [
            lab.Step(333, 337, "variant", "copy", args=("src/agents/variants/triage.concise.md",)),
            lab.Step(333, 337, "publish", args=("uv", "run", "create-agents")),
            lab.Step(333, 337, "failure", args=("fixture-failure",)),
            lab.Step(355, 359, "must not run", args=("fixture-sentinel",)),
        ]
        calls = []

        def fake_process(args, **kwargs):
            calls.append(args)
            return (2, "Fixture crash") if args[0] == "fixture-failure" else (0, "")

        output = io.StringIO()
        with (patch.object(self.runner, "preflight"),
              patch.object(self.runner, "process", side_effect=fake_process),
              contextlib.redirect_stdout(output)):
            result = self.runner.run(execute=True)
        self.assertEqual(result, 1)
        self.assertEqual(calls, [["uv", "run", "create-agents"], ["fixture-failure"]])
        self.assertEqual((self.root / lab.PROMPT).read_bytes(), self.runner.original_prompt)
        self.assertIn("approved agents", self.runner.recovery)
        report = output.getvalue().split("Walkthrough report")[-1]
        self.assertIn("[ERROR] Step 03: failure", report)
        self.assertIn("Unexpected result (exit 2)", report)
        self.assertIn("[NOT RUN] Step 04: must not run", report)

    def test_cancelled_optional_edit_restores_the_explicitly_owned_edit(self):
        self.runner.steps = [lab.Step(425, 425, "edit", "edit", detail="Fixture edit")]

        def edit_and_cancel(*args, **kwargs):
            (self.root / lab.PROMPT).write_bytes(b"User's optional exercise edit\n")
            raise lab.Cancelled("Fixture cancellation")

        with patch.object(self.runner, "preflight"), patch.object(lab, "confirm", side_effect=edit_and_cancel):
            result = self.quiet(self.runner.run, execute=True)
        self.assertEqual(result, 1)
        self.assertEqual((self.root / lab.PROMPT).read_bytes(), self.runner.original_prompt)

    def test_cancelled_checkpoint_is_not_recorded_as_complete(self):
        self.runner.steps = [lab.Step(194, 208, "portal", "manual", detail="Fixture check")]
        output = io.StringIO()
        with (patch.object(self.runner, "preflight", side_effect=lab.Cancelled("Fixture cancellation")),
              contextlib.redirect_stdout(output)):
            result = self.runner.run(execute=True)
        self.assertEqual(result, 1)
        self.assertIn("INCOMPLETE", output.getvalue())
        self.assertIn("[NOT RUN] Step 01: portal", output.getvalue())
        self.assertIn("Fixture cancellation", output.getvalue().split("Walkthrough report")[-1])
        self.assertNotIn("Automated flow finished", output.getvalue())

    def test_unselected_optional_group_executes_no_commands_or_prompts(self):
        self.runner.steps = [
            lab.Step(157, 159, "editor", args=("code", "."), optional="editor"),
            lab.Step(343, 343, "editor again", args=("code", "."), optional="editor"),
        ]
        with (patch.object(self.runner, "preflight"),
              patch.object(lab, "confirm", return_value=False) as confirm,
              patch.object(self.runner, "process") as process):
            self.assertEqual(self.quiet(self.runner.run, execute=True), 0)
        confirm.assert_not_called()
        process.assert_not_called()
        self.assertTrue(all(result.status == "SKIPPED OPTIONAL" for result in self.runner.outcomes))

    def test_selected_optional_group_runs_without_an_extra_confirmation(self):
        self.runner = lab.Runner(self.root, "fixture", optional=("editor",))
        self.runner.steps = [lab.Step(157, 159, "editor", args=("code", "."), optional="editor")]
        with (patch.object(self.runner, "preflight"),
              patch.object(lab, "confirm") as confirm,
              patch.object(self.runner, "process", return_value=(0, "")) as process):
            self.assertEqual(self.quiet(self.runner.run, execute=True), 0)
        confirm.assert_not_called()
        process.assert_called_once_with(["code", "."], interactive=False)
        self.assertEqual(self.runner.outcomes[0].status, "PASS")

    def test_read_only_reviews_do_not_pause_and_are_reported_as_unverified(self):
        self.runner.steps = [
            lab.Step(194, 208, "portal review", "manual", detail="Inspect the portal fixture."),
            lab.Step(215, 217, "next command", args=("fixture",)),
        ]
        output = io.StringIO()
        with (patch.object(self.runner, "preflight"),
              patch.object(lab, "confirm", side_effect=AssertionError("Unnecessary prompt")),
              patch.object(self.runner, "process", return_value=(0, "")) as process,
              contextlib.redirect_stdout(output)):
            self.assertEqual(self.runner.run(execute=True), 0)
        process.assert_called_once()
        report = output.getvalue().split("Walkthrough report")[-1]
        self.assertIn("[REVIEW NEEDED] Step 01: portal review", report)
        self.assertIn("Inspect the portal fixture.", report)
        self.assertIn(f"{lab.GUIDE}:194-208", report)
        self.assertIn("[PASS] Step 02: next command", report)
        self.assertIn("1 manual review item(s) remain unverified", report)

    def test_runner_colors_follow_terminal_and_global_opt_outs_not_child_settings(self):
        self.runner.child_env["UV_NO_COLOR"] = "1"
        for is_tty, env, colored in (
            (True, {}, True),
            (False, {}, False),
            (True, {"NO_COLOR": ""}, False),
            (True, {"UV_NO_COLOR": "1"}, False),
            (True, {"TERM": "dumb"}, False),
        ):
            with self.subTest(is_tty=is_tty, env=env):
                output = io.StringIO()
                with (contextlib.redirect_stdout(output),
                      patch.object(output, "isatty", return_value=is_tty),
                      patch.object(console.sys, "platform", "linux"),
                      patch.dict(console.os.environ, env, clear=True)):
                    self.runner.say("Fixture completed", "PASS")
                self.assertEqual("\033[" in output.getvalue(), colored)
                self.assertIn("[PASS] Fixture completed", lab.ANSI.sub("", output.getvalue()))

    def test_console_wraps_prose_but_keeps_commands_and_queries_copyable(self):
        output = io.StringIO()
        command = "uv run fixture --long-value " + "x" * 70
        query = "customEvents | where " + "x" * 70
        prose = "A readable explanation with several words that should wrap on a narrow terminal."
        with (contextlib.redirect_stdout(output),
              patch.object(console.shutil, "get_terminal_size",
                           return_value=console.os.terminal_size((48, 24)))):
            self.runner.console.heading("Fixture lab")
            self.runner.console.message(prose)
            self.runner.console.block("Command", command, command=True)
            self.runner.console.block("Kusto query", query)
        text = output.getvalue()
        self.assertIn("=" * 48, text)
        self.assertIn("    > " + command, text)
        self.assertIn("    " + query, text)
        wrapped = text.split("  Command:")[0]
        self.assertTrue(all(len(line) <= 48 for line in wrapped.splitlines()))
        self.assertIn(prose, " ".join(wrapped.split()))
        self.assertNotIn("\033[", text)

    def test_windows_console_enables_ansi_or_falls_back_to_plain_text(self):
        import ctypes

        for supported in (True, False):
            with self.subTest(supported=supported):
                output, kernel = io.StringIO(), Mock()
                kernel.GetStdHandle.return_value = 42
                kernel.GetConsoleMode.return_value = supported
                kernel.SetConsoleMode.return_value = True
                with (contextlib.redirect_stdout(output),
                      patch.object(output, "isatty", return_value=True),
                      patch.object(console.sys, "platform", "win32"),
                      patch.dict(console.os.environ, {}, clear=True),
                      patch.object(ctypes, "WinDLL", return_value=kernel, create=True)):
                    console.Console().message("Windows fixture", "PASS")
                self.assertEqual("\033[" in output.getvalue(), supported)
                if supported:
                    kernel.SetConsoleMode.assert_called_once_with(42, 0x0004)
                else:
                    kernel.SetConsoleMode.assert_not_called()

    def test_fresh_failed_pack_is_an_expected_teaching_outcome(self):
        before = self.runner.pack_snapshot()
        self.make_pack("FAIL")
        self.assertEqual(self.quiet(self.runner.check_pack, before, 1, "pack-fail", False), "EXPECTED FAIL")

    def test_failed_process_without_new_evidence_is_not_an_expected_failure(self):
        self.make_pack("FAIL")
        before = self.runner.pack_snapshot()
        with self.assertRaisesRegex(lab.WalkthroughError, "fresh Evidence Pack"):
            self.runner.check_pack(before, 1, "pack-fail", False)

    def test_evidence_and_exit_status_must_agree(self):
        before = self.runner.pack_snapshot()
        self.make_pack("PASS")
        with self.assertRaisesRegex(lab.WalkthroughError, "does not match"):
            self.runner.check_pack(before, 1, "pack-observe", False)

    def test_incomplete_or_duplicate_dataset_results_are_rejected(self):
        for rows in ([{"alert_id": "A"}], [{"alert_id": "A"}, {"alert_id": "B"}, {"alert_id": "A"}]):
            with self.subTest(rows=rows):
                before = self.runner.pack_snapshot()
                directory = self.make_pack("PASS")
                self.write(directory / "results.json", rows)
                with self.assertRaisesRegex(lab.WalkthroughError, "every golden alert"):
                    self.runner.check_pack(before, 0, "pack-pass", False)

    def test_nondeterministic_red_team_failure_is_observed_not_passed(self):
        before = self.runner.pack_snapshot()
        self.make_pack("FAIL", redteam=True)
        self.assertEqual(self.quiet(self.runner.check_pack, before, 1, "pack-observe", True), "OBSERVED FAIL")

    def test_successful_weak_prompt_is_not_reported_as_expected_failure(self):
        before = self.runner.pack_snapshot()
        self.make_pack("PASS")
        with self.assertRaisesRegex(lab.WalkthroughError, "not reproduced"):
            self.quiet(self.runner.check_pack, before, 0, "pack-fail", False)

    def test_individual_attack_requires_completed_attack_output(self):
        step = lab.Step(557, 559, "fixture", args=("uv", "run", "src/redteam.py", "JB-02"), expect="attack")
        with self.assertRaises(lab.WalkthroughError):
            self.runner.check_result(step, 1, "Traceback: fixture SDK error", None)
        output = "JB-02: Fixture\nPayload: fixture\nSUCCEEDED\nAgent answer:\nFixture response\n"
        self.assertEqual(self.runner.check_result(step, 1, output, None), "OBSERVED FAIL")

    def test_policy_denial_is_distinct_from_missing_permission_or_success(self):
        step = lab.Step(734, 738, "fixture", expect="policy-deny")
        with self.assertRaises(lab.WalkthroughError):
            self.runner.check_result(step, 1, "ResourceGroupNotFound", None)
        self.assertEqual(self.quiet(self.runner.check_result, step, 1, "AuthorizationFailed", None), "REVIEW NEEDED")
        self.assertEqual(self.runner.check_result(
            step, 1, "RequestDisallowedByPolicy: approved-ai-models policy denied this request", None
        ), "EXPECTED FAIL")
        with self.assertRaisesRegex(lab.WalkthroughError, "CREATED"):
            self.runner.check_result(step, 0, "", None)

    def test_policy_create_is_never_sent_without_specific_confirmation(self):
        step = next(step for step in self.runner.steps if step.expect == "policy-deny")
        self.write(".env", b"AZURE_RESOURCE_GROUP=fixture\nFOUNDRY_NAME=foundry-fixture\n")
        with patch.object(lab, "confirm", return_value=False), patch.object(self.runner, "process") as process:
            with self.assertRaises(lab.Cancelled):
                self.runner.command(step)
        process.assert_not_called()

    def test_intake_exit_one_requires_completed_governance_checks(self):
        step = lab.Step(667, 669, "fixture", expect="intake-deny")
        with self.assertRaises(lab.WalkthroughError):
            self.runner.check_result(step, 1, "ERROR: access denied", None)
        text = "\n".join([
            "Release gate passed on the published agents", "Red team passed on the published agents",
            "Data policy enforced", "A named analyst decided alerts on the published agents",
            "Not approved. Fix the failures and run intake again.",
        ])
        self.assertEqual(self.runner.check_result(step, 1, text, None), "EXPECTED FAIL")

    def test_scripted_decision_commands_do_not_prompt_and_validate_test_records(self):
        agents = {"triage": {"version": "1", "model": "gpt-5-mini"}}
        self.runner.tested_agents = agents
        for expect, alert, approved, disposition in (
            ("decision-approve", "ALT-1008", True, "escalate"),
            ("decision-override", "ALT-1014", False, "request_info"),
        ):
            step = lab.Step(825, 827, "fixture decision", args=("uv", "run", "src/decide.py", alert),
                            expect=expect, interactive=True)

            def record(args, **kwargs):
                self.assertFalse(kwargs["interactive"])
                self.assertEqual(args[2:4], ["tests/e2e/decision_driver.py", expect.removeprefix("decision-")])
                decision = {
                    "alert": alert, "analyst": "lab-test-automation", "mode": "lab-test",
                    "approved_recommendation": approved, "decision": disposition,
                    "recommendation": "escalate", "reviewer": "agree", "reason": "Fixture test reason",
                    "agents": agents,
                }
                path = self.root / self.runner.decision_path()
                path.parent.mkdir(parents=True, exist_ok=True)
                with path.open("a") as stream:
                    stream.write(json.dumps(decision) + "\n")
                return 0, ""

            with self.subTest(expect=expect), patch.object(self.runner, "process", side_effect=record):
                self.assertEqual(self.runner.command(step), "PASS")

    def test_interactive_decision_accepts_approval_even_in_override_exercise(self):
        self.runner.interactive_decisions = True
        step = lab.Step(833, 835, "fixture override", args=("uv", "run", "src/decide.py", "ALT-1014"),
                        expect="decision-override", interactive=True)
        self.write("evidence/decisions.jsonl", (json.dumps({
            "alert": "ALT-1014", "analyst": "fixture-analyst", "approved_recommendation": True,
            "decision": "escalate", "recommendation": "escalate", "reviewer": "agree", "reason": "",
            "agents": {"triage": {"version": "1"}},
        }) + "\n").encode())
        self.assertEqual(self.runner.check_result(step, 0, "", 0), "PASS")
        self.assertEqual(self.runner.command_arguments(step), step.args)

    def test_scripted_decision_rejects_wrong_provenance_versions_and_branch(self):
        agents = {"triage": {"version": "1"}}
        self.runner.tested_agents = agents
        step = lab.Step(833, 835, "fixture override", args=("uv", "run", "src/decide.py", "ALT-1014"),
                        expect="decision-override")
        record = {
            "alert": "ALT-1014", "analyst": "lab-test-automation", "mode": "lab-test",
            "approved_recommendation": False, "decision": "request_info",
            "recommendation": "escalate", "reviewer": "agree", "reason": "Fixture reason",
            "agents": agents,
        }
        for change in (
            {"mode": "human"}, {"analyst": "fixture-human"},
            {"agents": {"triage": {"version": "old"}}}, {"reason": ""},
            {"approved_recommendation": True, "decision": "escalate"},
        ):
            with self.subTest(change=change):
                self.write(self.runner.decision_path(), (json.dumps(record | change) + "\n").encode())
                with self.assertRaises(lab.WalkthroughError):
                    self.runner.check_result(step, 0, "", 0)

    def test_scripted_intake_is_explicitly_marked_and_rejects_human_approval(self):
        step = lab.Step(843, 845, "fixture approval", args=("uv", "run", "src/intake.py", "UC-001"),
                        expect="intake-approve")
        self.assertEqual(self.runner.command_arguments(step), (
            "uv", "run", "tests/e2e/decision_driver.py", "intake", "UC-001"))
        for mode, actor, accepted in (
            ("lab-test", "lab-test-automation", True), ("human", "fixture-analyst", False),
        ):
            path = self.root / self.runner.register_path()
            before = path.read_bytes() if path.exists() else b""
            self.write(self.runner.register_path(), [{"id": "UC-001", "approval": {
                "mode": mode, "approved_by": actor, "agents": {"triage": {"version": "1"}},
                "evidence": ["fixture-gate", "fixture-redteam"],
            }}])
            if accepted:
                self.assertEqual(self.runner.check_result(step, 0, "", before), "PASS")
            else:
                with self.assertRaises(lab.WalkthroughError):
                    self.runner.check_result(step, 0, "", before)

    def test_interactive_children_inherit_terminal_without_canned_input(self):
        with (patch.object(lab.shutil, "which", return_value="/fixture/uv"),
              patch.object(lab.subprocess, "run", return_value=subprocess.CompletedProcess([], 0)) as run):
            self.runner.process(["uv", "run", "src/decide.py", "ALT-1001"], interactive=True)
        self.assertFalse(run.call_args.kwargs["capture_output"])
        for key in ("stdin", "input", "stdout", "stderr"):
            self.assertNotIn(key, run.call_args.kwargs)

    def test_local_fake_command_streams_output_and_preserves_return_code(self):
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            code, captured = self.runner.process(
                [sys.executable, "-c", "import sys; print('fixture output', flush=True); sys.exit(3)"])
        self.assertEqual(code, 3)
        self.assertIn("fixture output", output.getvalue())
        self.assertIn("fixture output", captured)
        self.assertLess(output.getvalue().index("Command output:"), output.getvalue().index("fixture output"))

    def test_private_token_capture_has_no_formatted_or_raw_output(self):
        output = io.StringIO()
        with (contextlib.redirect_stdout(output),
              patch.object(lab.shutil, "which", return_value="/fixture/az"),
              patch.object(lab.subprocess, "run",
                           return_value=subprocess.CompletedProcess([], 0, stdout="fixture-private-token"))):
            code, token = self.runner.process(["az", "account", "get-access-token"], private=True)
        self.assertEqual((code, token), (0, "fixture-private-token"))
        self.assertEqual(output.getvalue(), "")

    def test_http_probe_keeps_token_private_and_refuses_redirects(self):
        self.write(".env", b"AZURE_RESOURCE_GROUP=fixture\nAPIM_GATEWAY_URL=https://fixture.azure-api.net\n")
        output = io.StringIO()
        with (patch.object(self.runner, "process", return_value=(0, "fake-token\n")),
              patch.object(lab.urllib.request, "build_opener") as build,
              contextlib.redirect_stdout(output)):
            build.return_value.open.side_effect = urllib.error.HTTPError(
                "https://fixture.azure-api.net", 404, "Not Found", {}, None)
            self.assertEqual(self.runner.probe("unpublished", "404"), "EXPECTED FAIL")
        request = build.return_value.open.call_args.args[0]
        self.assertEqual(request.get_header("Authorization"), "Bearer fake-token")
        handler = build.call_args.args[0]()
        self.assertIsNone(handler.redirect_request(None, None, 302, "", {}, "https://example.invalid"))
        self.assertNotIn("fake-token", output.getvalue())

    def test_untrusted_gateway_does_not_receive_a_token(self):
        self.write(".env", b"AZURE_RESOURCE_GROUP=fixture\nAPIM_GATEWAY_URL=https://fixture.azure-api.net.example.org\n")
        with patch.object(self.runner, "process") as process:
            with self.assertRaisesRegex(lab.WalkthroughError, "azure-api.net"):
                self.runner.probe("unpublished", "404")
        process.assert_not_called()

    def test_enforcement_changes_only_the_enforce_field(self):
        path = self.root / lab.DATA_POLICY
        original = json.loads(path.read_text())
        self.runner.action(lab.Step(691, 691, "fixture", "enforce"))
        updated = json.loads(path.read_text())
        self.assertTrue(updated.pop("enforce"))
        original.pop("enforce")
        self.assertEqual(updated, original)


if __name__ == "__main__":
    unittest.main(verbosity=2)
