"""Offline runner checks. Run directly; not collected as part of the project test suite.

    uv run --no-sync tests/e2e/check_runner.py
"""

import contextlib
import io
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
import urllib.error

import run_lab as lab


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
        for phase in ("Lab 0", "Lab 1", "Lab 2", "Lab 3", "Lab 4", "Temporary lab"):
            self.assertIn(phase, output.getvalue())
        self.assertIn("42 command/query blocks mapped", output.getvalue())
        self.assertIn("Kusto", output.getvalue())

    def test_source_drift_blocks_runner_creation(self):
        with (self.root / lab.GUIDE).open("ab") as stream:
            stream.write(b"\nChanged instructions\n")
        with self.assertRaisesRegex(lab.WalkthroughError, "instructions changed"):
            lab.Runner(self.root)

    def test_windows_line_endings_do_not_count_as_source_drift(self):
        path = self.root / lab.GUIDE
        path.write_bytes(path.read_bytes().replace(b"\n", b"\r\n"))
        lab.Runner(self.root)

    def test_removed_command_block_mapping_is_detected(self):
        steps = [step for step in self.runner.steps if not (step.start <= 946 and step.end >= 948)]
        with self.assertRaisesRegex(lab.WalkthroughError, "946"):
            self.runner.guide.check_coverage(steps)

    def test_missing_command_inside_a_still_mapped_block_is_detected(self):
        steps = [step for step in self.runner.steps if not (step.start == 333 and step.expect == "pack-fail")]
        with self.assertRaisesRegex(lab.WalkthroughError, "336"):
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

        with patch.object(self.runner, "preflight"), patch.object(self.runner, "process", side_effect=fake_process):
            result = self.quiet(self.runner.run, execute=True)
        self.assertEqual(result, 1)
        self.assertEqual(calls, [["uv", "run", "create-agents"], ["fixture-failure"]])
        self.assertEqual((self.root / lab.PROMPT).read_bytes(), self.runner.original_prompt)
        self.assertIn("approved agents", self.runner.recovery)

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
        with (patch.object(self.runner, "preflight"),
              patch.object(lab, "confirm", side_effect=lab.Cancelled("Fixture cancellation")),
              contextlib.redirect_stdout(output)):
            result = self.runner.run(execute=True)
        self.assertEqual(result, 1)
        self.assertIn("INCOMPLETE", output.getvalue())
        self.assertNotIn("Walkthrough finished", output.getvalue())

    def test_declined_optional_group_executes_no_commands(self):
        self.runner.steps = [
            lab.Step(157, 159, "editor", args=("code", "."), optional="editor"),
            lab.Step(343, 343, "editor again", args=("code", "."), optional="editor"),
        ]
        with (patch.object(self.runner, "preflight"),
              patch.object(lab, "confirm", return_value=False) as confirm,
              patch.object(self.runner, "process") as process):
            self.assertEqual(self.quiet(self.runner.run, execute=True), 0)
        confirm.assert_called_once()
        process.assert_not_called()
        self.assertTrue(all(result[1] == "SKIPPED OPTIONAL" for result in self.runner.outcomes))

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
            self.runner.check_result(step, 1, "AuthorizationFailed", None)
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
