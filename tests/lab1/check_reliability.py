"""Focused offline checks. Run: python .\\tests\\lab1\\check_reliability.py"""
import json
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src" / "lab1" / "python"))

import evidence
import managed_scoring
from foundry import Foundry


def require(condition, message):
    if not condition:
        raise AssertionError(message)


def evidence_save_failure():
    with tempfile.TemporaryDirectory() as directory:
        folder = Path(directory)
        pack = evidence.new_pack()
        evidence.save(folder, pack)
        (folder / "summary.md").unlink()
        (folder / "summary.md").mkdir()
        pack.update(release_ready=True, release_gate="pass", exit_code=0)
        try:
            evidence.save(folder, pack)
        except OSError:
            pass
        else:
            raise AssertionError("A blocked summary must fail the save.")
        saved = json.loads((folder / "evidence.json").read_text(encoding="utf-8"))
        require(saved["release_ready"] is False and saved["exit_code"] == 2,
                "A failed summary save left a release-ready JSON file.")
        require(saved["release_gate"] == "execution_error" and saved["error"],
                "The save failure must be recorded in the JSON.")
        require(not list(folder.glob("*.tmp")), "Handled failures must remove temporary files.")


def serialization_preserves_previous_pack():
    with tempfile.TemporaryDirectory() as directory:
        folder = Path(directory)
        pack = evidence.new_pack()
        evidence.save(folder, pack)
        before = (folder / "evidence.json").read_bytes()
        pack["unserializable"] = object()
        try:
            evidence.save(folder, pack)
        except TypeError:
            pass
        else:
            raise AssertionError("The unsupported value must fail serialization.")
        require((folder / "evidence.json").read_bytes() == before,
                "A serialization error truncated the previous Evidence Pack.")


def scoring_probe(late_stage=None, queued=False, auth_delay=0):
    clock = [0.0]
    requests_seen = []
    saved = []
    row = {"datasource_item": {"id": "triage/example"}, "results": [
        {"name": name, "score": 5, "passed": True} for name in managed_scoring.EVALUATORS]}
    responses = [
        {"id": "eval-example"},
        {"id": "run-example", "status": "queued" if queued else "completed"},
        {"data": [row], "has_more": True, "last_id": "row-1"},
        {"data": [], "has_more": False},
    ]

    def token(_scope):
        clock[0] += auth_delay
        return SimpleNamespace(token="offline-placeholder")

    def request(method, url, **kwargs):
        stage = len(requests_seen)
        requests_seen.append((method, url, kwargs["timeout"], clock[0]))
        clock[0] += 2 if late_stage == stage else 0.1
        return SimpleNamespace(ok=True, headers={}, json=lambda: responses[stage])

    def sleep(seconds):
        clock[0] += seconds

    client = Foundry("https://offline.invalid/api/projects/example",
                     credential=SimpleNamespace(get_token=token),
                     session=SimpleNamespace(request=request))
    managed = evidence.new_managed_evaluation("fixed-judge")
    items = [{"id": "triage/example", "agent": "triage", "input": "synthetic", "output": "recorded"}]
    with patch("time.monotonic", side_effect=lambda: clock[0]), patch("time.sleep", side_effect=sleep), \
            patch.object(managed_scoring, "TIMEOUT_SECONDS", 1):
        try:
            managed_scoring.run(client, managed, items, {"triage": "instructions"}, "example",
                                lambda: saved.append(dict(managed)))
        except TimeoutError:
            require(late_stage is not None or queued or auth_delay, "An on-time run timed out.")
            require(managed["gate"] != "pass", "A timed-out run approved its scores.")
            if late_stage is not None and late_stage >= 1:
                require(any(p["run_id"] == "run-example" for p in saved),
                        "Known remote run IDs must survive a timeout.")
        else:
            require(late_stage is None and not queued and not auth_delay, "Scoring accepted a late result.")
            require(managed["gate"] == "pass" and len(managed["items"]) == 1,
                    "An on-time paginated result must pass.")
        for _method, _url, timeout, started in requests_seen:
            require(0 < timeout <= 1 - started, "An HTTP call exceeded the remaining scoring budget.")
        if queued:
            require(len(requests_seen) == 2 and clock[0] <= 1,
                    "Polling must stop at the deadline, without another HTTP call.")
        if auth_delay:
            require(not requests_seen, "No request may start after authentication exhausts the budget.")


def main():
    probes = [
        ("summary save failure", evidence_save_failure),
        ("serialization preserves evidence", serialization_preserves_previous_pack),
        ("on-time scoring and pagination", scoring_probe),
        *[(f"late scoring stage {stage}", lambda stage=stage: scoring_probe(late_stage=stage))
          for stage in range(4)],
        ("polling budget", lambda: scoring_probe(queued=True)),
        ("authentication budget", lambda: scoring_probe(auth_delay=2)),
    ]
    failed = 0
    for name, probe in probes:
        try:
            probe()
            print(f"PASS {name}")
        except (AssertionError, OSError, ValueError, IndexError, RuntimeError) as error:
            failed += 1
            print(f"FAIL {name}: {error}", file=sys.stderr)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
