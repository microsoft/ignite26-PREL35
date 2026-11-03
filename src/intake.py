"""Demand intake: check a use case against its evidence, and record the approval in the register.

A use case is approved only when its evidence covers what is running now: the published agent versions and the
data policy in force. Exits with 1 when a check fails, so nothing is recorded.

Usage: python src/intake.py USE_CASE_ID
"""
import json
import sys
from datetime import datetime

from azure.ai.projects import AIProjectClient

from console_output import color
from decide import DECISIONS, whoami
from evaluate import published
from investigate import ROOT, credential, data_policy, env, policy_hash

REGISTER = ROOT / "governance" / "use-cases.json"


def latest_pack(suffix: str):
    """The newest Evidence Pack of a kind: release gate packs have no suffix, red team packs end in -redteam."""
    packs = [p for p in (ROOT / "evidence").glob("*") if (p / "manifest.json").exists() and p.name.endswith(suffix)]
    packs = [p for p in packs if suffix or not p.name.endswith("-redteam")]
    return max(packs, key=lambda p: p.name, default=None)


def check_pack(name: str, pack, agents: dict) -> list:
    """Problems with one Evidence Pack, as sentences an approver can act on. Empty means it covers what runs now."""
    if pack is None:
        return [f"No {name} Evidence Pack. Run it."]
    manifest = json.loads((pack / "manifest.json").read_text(encoding="utf-8"))
    problems = []
    if manifest["verdict"] != "PASS":
        problems.append(f"The latest {name} ({pack.name}) failed.")
    tested = {n: a["version"] for n, a in manifest["agents"].items()}
    running = {n: a["version"] for n, a in agents.items()}
    if tested != running:
        problems.append(f"The latest {name} tested {tested}, but {running} is published. Run it again.")
    if (manifest.get("data_policy") or {}).get("sha256") != policy_hash():
        problems.append(f"The latest {name} ran under a different data policy. Run it again.")
    return problems


# Self-check of the pack checks, with no calls to Azure.
assert check_pack("release gate", None, {}) == ["No release gate Evidence Pack. Run it."]

def main():
    use_case_id = sys.argv[1] if len(sys.argv) > 1 else sys.exit(
        color("Usage: python src/intake.py USE_CASE_ID", "red", stream=sys.stderr)
    )
    register = json.loads(REGISTER.read_text(encoding="utf-8"))
    use_case = next((u for u in register if u["id"] == use_case_id), None) or sys.exit(
        color(f"Unknown use case {use_case_id}", "red", stream=sys.stderr)
    )
    agents = published(AIProjectClient(endpoint=env["FOUNDRY_PROJECT_ENDPOINT"], credential=credential))
    gate, redteam = latest_pack(""), latest_pack("-redteam")

    decided = [json.loads(line) for line in DECISIONS.read_text(encoding="utf-8").splitlines()] if DECISIONS.exists() else []
    checks = {
        "Release gate passed on the published agents": check_pack("release gate", gate, agents),
        "Red team passed on the published agents": check_pack("red team", redteam, agents),
        "Data policy enforced": [] if data_policy()["enforce"] else ["governance/data-policy.json has enforce: false."],
        "A named analyst decided alerts on the published agents": [] if any(
            {n: a["version"] for n, a in d["agents"].items()} == {n: a["version"] for n, a in agents.items()} for d in decided
        ) else ["No decision recorded with src/decide.py on the published agents."],
    }

    print(color(f"{use_case['id']}: {use_case['name']} (risk tier {use_case['risk_tier']}, owner {use_case['owner']})", "cyan"))
    print(color("Published:", "cyan") + " " + ", ".join(f"{n} v{a['version']} ({a['model']})" for n, a in agents.items()) + "\n")
    for check, problems in checks.items():
        print(color("FAIL" if problems else "PASS", "red" if problems else "green") + f"  {check}")
        for problem in problems:
            print(f"      {problem}")
    if any(checks.values()):
        sys.exit("\n" + color("Not approved. Fix the failures and run intake again.", "red", stream=sys.stderr))

    use_case["approval"] = {
        "approved_by": whoami(),
        "approved": datetime.now().isoformat(timespec="seconds"),
        "agents": agents,
        "data_policy_sha256": policy_hash(),
        "evidence": [gate.relative_to(ROOT).as_posix(), redteam.relative_to(ROOT).as_posix()],
    }
    REGISTER.write_text(json.dumps(register, indent=2) + "\n", encoding="utf-8")
    print("\n" + color(f"Approved. Recorded in {REGISTER.relative_to(ROOT)}.", "green"))


if __name__ == "__main__":
    sys.exit(main())
