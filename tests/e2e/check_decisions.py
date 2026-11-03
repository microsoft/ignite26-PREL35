"""Offline checks for the optional tester adapter; run directly, without pytest."""

import contextlib
import importlib.util
import io
import json
from pathlib import Path
import sys
import tempfile
from types import ModuleType
import unittest
from unittest.mock import Mock, patch

import decision_driver as driver


class DecisionChecks(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.agents = {"triage": {"version": "1", "model": "gpt-5-mini"}}
        platform = ModuleType("investigate")
        platform.ROOT = self.root
        platform.cases = {
            alert: {"alert": {"title": "Fixture"}, "customer": {"name": "Fixture"}}
            for alert in ("ALT-1008", "ALT-1014")
        }
        platform.credential = Mock()
        platform.env = {"FOUNDRY_PROJECT_ENDPOINT": "https://fixture.invalid"}
        platform.investigate = Mock(return_value={
            "triage": '{"disposition":"escalate","priority":"high","reasons":[]}',
            "review": '{"verdict":"agree","issues":[]}',
        })
        platform.data_policy = lambda: {"enforce": True}
        platform.policy_hash = lambda: "fixture-policy"
        evaluation = ModuleType("evaluate")
        evaluation.parse = json.loads
        evaluation.published = Mock(return_value=self.agents)
        output = ModuleType("console_output")
        output.color = lambda text, *args, **kwargs: text
        projects = ModuleType("azure.ai.projects")
        projects.AIProjectClient = Mock()
        self.enterContext(patch.dict(sys.modules, {
            "investigate": platform, "evaluate": evaluation, "console_output": output,
            "azure.ai.projects": projects,
        }))
        self.enterContext(patch.object(sys, "path", sys.path.copy()))
        self.enterContext(patch.object(driver, "ROOT", self.root))
        for name in ("decide", "intake"):
            spec = importlib.util.spec_from_file_location(
                name, Path(driver.__file__).resolve().parents[2] / "src" / f"{name}.py")
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            self.enterContext(patch.dict(sys.modules, {name: module}))
        self.decide, self.intake = sys.modules["decide"], sys.modules["intake"]
        self.enterContext(patch.object(self.decide, "whoami", side_effect=AssertionError("Human identity used")))
        self.enterContext(patch.object(self.intake, "whoami", side_effect=AssertionError("Human identity used")))
        for name in ("fixture-gate", "fixture-redteam"):
            folder = self.root / "evidence" / name
            folder.mkdir(parents=True)
            (folder / "manifest.json").write_text(json.dumps({
                "verdict": "PASS", "agents": self.agents, "data_policy": {"sha256": "fixture-policy"},
            }))
        self.decide.DECISIONS.write_text('{"analyst":"existing-human"}\n')
        self.intake.REGISTER.parent.mkdir()
        self.intake.REGISTER.write_text(json.dumps([{
            "id": "UC-001", "name": "Fixture", "risk_tier": "high", "owner": "Fixture",
        }]))
        self.human_decisions = self.decide.DECISIONS.read_bytes()
        self.human_register = self.intake.REGISTER.read_bytes()

    def run_driver(self, *args):
        with contextlib.redirect_stdout(io.StringIO()):
            return driver.main(list(args))

    def tearDown(self):
        self.assertEqual(self.decide.DECISIONS.read_bytes(), self.human_decisions)
        self.assertEqual(self.intake.REGISTER.read_bytes(), self.human_register)

    def test_both_branches_use_real_workflow_and_keep_labeled_records_isolated(self):
        self.run_driver("approve", "ALT-1008")
        self.run_driver("override", "ALT-1014")
        records = [json.loads(line) for line in (self.root / driver.DECISIONS).read_text().splitlines()]
        self.assertEqual([row["approved_recommendation"] for row in records], [True, False])
        self.assertEqual([row["decision"] for row in records], ["escalate", "request_info"])
        self.assertTrue(all(row["mode"] == "lab-test" and row["analyst"] == driver.ACTOR and row["reason"] for row in records))
        self.assertEqual(sys.modules["investigate"].investigate.call_count, 2)

    def test_intake_uses_real_checks_and_only_saves_test_approval(self):
        self.run_driver("approve", "ALT-1008")
        self.run_driver("intake", "UC-001")
        approval = json.loads((self.root / driver.REGISTER).read_text())[0]["approval"]
        self.assertEqual(approval["mode"], "lab-test")
        self.assertEqual(approval["approved_by"], driver.ACTOR)
        self.assertEqual(approval["agents"], self.agents)

    def test_failed_gate_does_not_write_any_approval(self):
        self.run_driver("approve", "ALT-1008")
        manifest = self.root / "evidence/fixture-gate/manifest.json"
        data = json.loads(manifest.read_text())
        data["verdict"] = "FAIL"
        manifest.write_text(json.dumps(data))
        with self.assertRaises(SystemExit):
            self.run_driver("intake", "UC-001")
        self.assertFalse((self.root / driver.REGISTER).exists())

    def test_human_decisions_cannot_satisfy_test_intake(self):
        path = self.root / driver.DECISIONS
        path.parent.mkdir(parents=True)
        path.write_text(json.dumps({"analyst": "fixture-human", "agents": self.agents}) + "\n")
        with self.assertRaises(ValueError):
            self.run_driver("intake", "UC-001")
        self.assertFalse((self.root / driver.REGISTER).exists())

    def test_intake_rejects_stale_evidence_failed_redteam_and_unenforced_policy(self):
        self.run_driver("approve", "ALT-1008")
        for change in ("stale-agents", "stale-policy", "failed-redteam", "unenforced-policy"):
            with self.subTest(change=change):
                for name in ("fixture-gate", "fixture-redteam"):
                    manifest = {
                        "verdict": "PASS", "agents": self.agents,
                        "data_policy": {"sha256": "fixture-policy"},
                    }
                    if name == "fixture-gate" and change == "stale-agents":
                        manifest["agents"] = {"triage": {"version": "old"}}
                    if name == "fixture-gate" and change == "stale-policy":
                        manifest["data_policy"]["sha256"] = "old-policy"
                    if name == "fixture-redteam" and change == "failed-redteam":
                        manifest["verdict"] = "FAIL"
                    (self.root / "evidence" / name / "manifest.json").write_text(json.dumps(manifest))
                with patch.object(self.intake, "data_policy", return_value={"enforce": change != "unenforced-policy"}):
                    with self.assertRaises(SystemExit):
                        self.run_driver("intake", "UC-001")
                self.assertFalse((self.root / driver.REGISTER).exists())

    def test_agent_failure_preserves_test_ledger_and_restores_patches(self):
        self.run_driver("approve", "ALT-1008")
        path = self.root / driver.DECISIONS
        before = path.read_bytes()
        argv, identity, decisions, color = sys.argv, self.decide.whoami, self.decide.DECISIONS, self.decide.color
        with patch.object(self.decide, "investigate", side_effect=RuntimeError("Fixture agent failure")):
            with self.assertRaisesRegex(RuntimeError, "Fixture agent failure"):
                self.run_driver("override", "ALT-1014")
        self.assertEqual(path.read_bytes(), before)
        self.assertIs(sys.argv, argv)
        self.assertIs(self.decide.whoami, identity)
        self.assertEqual(self.decide.DECISIONS, decisions)
        self.assertIs(self.decide.color, color)
        self.assertEqual(list((self.root / driver.ARTIFACTS).glob("workflow-*")), [])

    def test_missing_test_decision_does_not_borrow_existing_human_decision(self):
        with self.assertRaises(SystemExit):
            self.run_driver("intake", "UC-001")
        self.assertFalse((self.root / driver.REGISTER).exists())

    def test_invalid_agent_answer_is_not_saved_as_test_decision(self):
        sys.modules["investigate"].investigate.return_value["triage"] = '{"disposition":null}'
        with self.assertRaises(ValueError):
            self.run_driver("approve", "ALT-1008")
        self.assertFalse((self.root / driver.DECISIONS).exists())

    def test_learner_entry_point_keeps_real_identity_and_interactive_input(self):
        path = self.root / "evidence/interactive-fixture.jsonl"
        with (patch.object(self.decide, "DECISIONS", path),
              patch.object(self.decide, "whoami", return_value="fixture-analyst") as whoami,
              patch.object(sys, "argv", ["src/decide.py", "ALT-1008"]),
              patch("builtins.input", side_effect=["y", "Reviewed fixture"]) as answer,
              contextlib.redirect_stdout(io.StringIO())):
            self.decide.main()
        row = json.loads(path.read_text())
        self.assertEqual(row["analyst"], "fixture-analyst")
        self.assertNotIn("mode", row)
        whoami.assert_called_once()
        self.assertEqual(answer.call_count, 2)


if __name__ == "__main__":
    unittest.main()
