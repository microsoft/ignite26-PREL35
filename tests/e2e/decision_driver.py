"""Tester-only adapter for the real decision/intake workflow; no human approval is recorded."""

import argparse
import importlib
import json
from pathlib import Path
import sys
import tempfile
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
ARTIFACTS = Path("tests/e2e/artifacts")
DECISIONS = ARTIFACTS / "decisions.jsonl"
REGISTER = ARTIFACTS / "use-cases.json"
ACTOR = "lab-test-automation"


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("approve", "override", "intake"))
    parser.add_argument("target")
    args = parser.parse_args(argv)
    sys.path.insert(0, str(ROOT / "src"))
    decide = importlib.import_module("decide")
    module = decide if args.command != "intake" else importlib.import_module("intake")
    directory = ROOT / ARTIFACTS
    for path in (directory, ROOT / DECISIONS, ROOT / REGISTER):
        if any(parent.is_symlink() for parent in (path, *path.parents) if parent.is_relative_to(ROOT)):
            raise ValueError("Refusing linked test artifact paths.")
    directory.mkdir(parents=True, exist_ok=True)
    print("LAB TEST ONLY: simulated input/actor; real agents and governance checks remain in use.")
    print(f"Test records stay in {ARTIFACTS}; learner decisions and approvals are untouched.")

    with tempfile.TemporaryDirectory(prefix="workflow-", dir=directory) as temporary:
        draft = Path(temporary)
        original_color = module.color

        def test_color(text, *positional, **keywords):
            text = text.replace(f"You are signed in as {ACTOR}.", f"Simulated actor: {ACTOR}.")
            return original_color(text, *positional, **keywords)

        with (patch.object(sys, "argv", [str(ROOT / "src" / f"{module.__name__}.py"), args.target]),
              patch.object(module, "whoami", return_value=ACTOR),
              patch.object(module, "color", side_effect=test_color),
              patch.object(module, "DECISIONS", draft / "decisions.jsonl")):
            if args.command == "intake":
                records = (ROOT / DECISIONS).read_text(encoding="utf-8").splitlines() if (ROOT / DECISIONS).exists() else []
                records = [json.loads(line) for line in records]
                if any(row.get("mode") != "lab-test" or row.get("analyst") != ACTOR for row in records):
                    raise ValueError("The isolated test ledger contains a non-test decision.")
                module.DECISIONS.write_text("".join(json.dumps(row) + "\n" for row in records), encoding="utf-8")
                register = draft / "use-cases.json"
                register.write_bytes((ROOT / "governance/use-cases.json").read_bytes())
                with patch.object(module, "REGISTER", register):
                    module.main()
                rows = json.loads(register.read_text(encoding="utf-8"))
                approval = next(row for row in rows if row["id"] == args.target)["approval"]
                approval["mode"] = "lab-test"
                (ROOT / REGISTER).write_text(json.dumps(rows, indent=2) + "\n", encoding="utf-8")
            else:
                answers = iter(["y", "Scripted lab test: exercise recommendation acceptance."] if args.command == "approve"
                               else ["n", "request_info", "", "Scripted lab test: exercise the override branch and required reason."])

                def answer(question):
                    try:
                        value = next(answers)
                    except StopIteration:
                        raise RuntimeError("The decision workflow requested an unexpected extra input.") from None
                    print(question + value)
                    return value

                with patch("builtins.input", side_effect=answer):
                    module.main()
                row = json.loads(module.DECISIONS.read_text(encoding="utf-8"))
                if row["decision"] not in decide.DISPOSITIONS:
                    raise ValueError("No valid triage disposition was returned; no test decision was saved.")
                row["mode"] = "lab-test"
                with (ROOT / DECISIONS).open("a", encoding="utf-8") as stream:
                    stream.write(json.dumps(row) + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
