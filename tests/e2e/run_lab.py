"""Guided, opt-in PREL35 walkthrough. Default: print the plan without changing anything.

    uv run --no-sync tests/e2e/run_lab.py --plan
    uv run --no-sync tests/e2e/run_lab.py --execute --resource-group PREL35

Uses only the standard library. Remove tests/e2e/ to remove this harness.
Live execution is interactive, billable, and changes the lab's agents and settings.
Optional exercises are skipped unless selected with --optional EXERCISE.
Read-only/manual checks are listed for review at the end, without pausing.
"""

import argparse
from collections import Counter
from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import re
import shlex
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

from walkthrough_console import Console

ROOT = Path(__file__).resolve().parents[2]
GUIDE = Path("instructions/skillable/content.md")
SOURCE_SHA256 = "09d8255baf232ddec38f971bf0ea2a90fc034d623b64705baa13b21afcba3d36"
ANSI = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]")
PROMPT = Path("src/agents/triage.md")
DATA_POLICY = Path("governance/data-policy.json")
REGISTER = Path("governance/use-cases.json")
OPTIONAL_GROUPS = ("editor", "colors", "tuning", "stale-evidence")
NOTES = (
    "Azure must already be provisioned. The runner never assigns roles or bypasses policy.",
    "Gateway updates need APIM permissions AND a Skillable policy allowing "
    "Microsoft.ApiManagement/service/namedValues at every inherited scope.",
    "Guide line 730 mentions two approved models; current infrastructure also approves "
    "the embedding and decision-1 models. The unapproved-model test is unchanged.",
    "The guide's caller-cost query variation (line 867) drops Caller during earlier "
    "aggregation. Review that variation with the proctor; do not claim it ran unchanged.",
)


class WalkthroughError(Exception):
    pass


class Cancelled(WalkthroughError):
    pass


@dataclass(frozen=True)
class Step:
    start: int
    end: int
    title: str
    kind: str = "command"
    args: tuple[str, ...] = ()
    expect: str = "success"
    detail: str = ""
    optional: str = ""
    interactive: bool = False


@dataclass(frozen=True)
class Outcome:
    number: int
    lab: str
    step: Step
    status: str
    elapsed: float = 0.0
    detail: str = ""


def build_steps():
    def uv(start, end, title, *args, **kwargs):
        return Step(start, end, title, args=("uv", "run", *args), **kwargs)

    def manual(start, end, title, detail, **kwargs):
        return Step(start, end, title, "manual", detail=detail, **kwargs)

    def publish(start, end, *args, **kwargs):
        return uv(start, end, "Publish agent versions", "create-agents", *args, **kwargs)

    def gate(start, end, expect="pack-pass", *args, **kwargs):
        return uv(start, end, "Run the release gate", "src/evaluate.py", *args,
                  expect=expect, **kwargs)

    def team(start, end, expect="pack-observe", *args, **kwargs):
        return uv(start, end, "Run the red team", "src/redteam.py", *args,
                  expect=expect, **kwargs)

    def switch(start, end, name, value):
        return uv(start, end, f"Set {name} = {value}", "set-gateway", name, value)

    def restore(start, end, **kwargs):
        return Step(start, end, "Restore the approved starting prompt", "restore", **kwargs)

    return [
        manual(3, 5, "Sign in to the VM",
               "Use the Resources tab credentials on the lab VM; do not enter credentials here."),
        Step(142, 144, "Use the checkout as the working directory", "cwd"),
        manual(146, 154, "Prepare Azure sign-in",
               "Sign in as the learner for this lab, not a personal account. The next command is interactive."),
        Step(148, 150, "Sign in to Azure", args=("az", "login"), interactive=True),
        Step(157, 159, "Open VS Code", args=("code", "."), optional="editor"),
        uv(163, 165, "Prepare the existing lab", "setup-lab", "--resource-group", "{resource_group}"),
        publish(174, 176),
        Step(180, 182, "Demonstrate color opt-out for child commands", "colors", optional="colors"),
        manual(194, 208, "Tour Azure and Foundry",
               "Inspect keyless Foundry, gateway operations/policy and monitoring; read triage's instructions "
               "in the fincrime project. Confirm the selected subscription and resource group."),
        uv(215, 217, "Investigate ALT-1001", "src/investigate.py", "ALT-1001"),
        uv(219, 221, "Repeat the investigation as requested in prose", "src/investigate.py", "ALT-1001"),
        Step(228, 230, "Expect HTTP 401 without a token", "probe", args=("triage", "401")),
        Step(236, 239, "Acquire a token privately; expect HTTP 404", "probe", args=("unpublished", "404")),
        manual(243, 247, "Check your understanding",
               "Explain the gateway's downstream identity, how agent changes are published, and who decides alerts."),
        gate(306, 308, "pack-pass", "--foundry"),
        manual(314, 320, "Inspect baseline release evidence",
               "Open the new report.md and compare every blocking metric with its threshold."),
        Step(325, 327, "Compare the concise prompt", args=(
            "git", "--no-pager", "diff", "--no-index",
            "src/agents/triage.md", "src/agents/variants/triage.concise.md"), expect="diff"),
        Step(333, 337, "Apply the concise prompt", "copy", args=("src/agents/variants/triage.concise.md",)),
        publish(333, 337),
        gate(333, 337, "pack-fail"),
        Step(343, 343, "Open reports in VS Code", args=("code", "."), optional="editor"),
        manual(339, 349, "Review the failed gate and its exit code",
               "Inspect citation validity, escalation recall and token savings. The preceding gate verified "
               "exit code 1 (the guide's $LASTEXITCODE check) against its new Evidence Pack."),
        restore(355, 359),
        publish(355, 359),
        gate(355, 359),
        publish(369, 372, "gpt-5-nano"),
        gate(369, 372, "pack-observe"),
        publish(378, 380),
        manual(382, 396, "Inspect a passing Evidence Pack",
               "Open report.md, manifest.json and results.json; inspect versions, model, hashes and source commit."),
        manual(398, 417, "Inspect traces and the Foundry AI judge",
               "Run the displayed Kusto query in Application Insights Logs, then inspect Triage task adherence "
               "in Foundry Evaluations. This is a human-verified portal step."),
        manual(419, 421, "Review the release pipeline",
               "Read .github/workflows/release-gate.yml. Do not execute a deployment pipeline."),
        Step(425, 425, "Edit the smaller-model prompt yourself", "edit", optional="tuning",
             detail="Review the nano report; edit only src/agents/triage.md for the optional exercise."),
        publish(427, 430, "gpt-5-nano", optional="tuning"),
        gate(427, 430, "pack-observe", optional="tuning"),
        gate(432, 432, "pack-observe", optional="tuning"),
        restore(434, 437, optional="tuning"),
        publish(434, 437, optional="tuning"),
        manual(493, 498, "Read the attack cases",
               "Open evals/attacks.json and identify document-borne versus caller-borne attacks."),
        team(502, 504, "pack-observe", "--foundry"),
        team(512, 516, "attack", "JB-01"),
        team(512, 516, "attack", "HRM-01"),
        team(512, 516, "attack", "HRM-02"),
        manual(524, 531, "Inspect the Foundry guardrail",
               "Read Microsoft.DefaultV2 on gpt-5-mini: content categories, severity and jailbreak detection."),
        Step(540, 542, "Compare the helpful prompt", args=(
            "git", "--no-pager", "diff", "--no-index",
            "src/agents/triage.md", "src/agents/variants/triage.helpful.md"), expect="diff"),
        Step(547, 551, "Apply the helpful prompt", "copy", args=("src/agents/variants/triage.helpful.md",)),
        publish(547, 551),
        team(547, 551, "pack-fail"),
        team(555, 559, "attack", "JB-02"),
        switch(569, 571, "prompt-shields", "on"),
        team(575, 577),
        manual(579, 585, "Inspect layered protections",
               "Compare prompt_attack_detected blocks with IPI-02, IPI-04 and JB-02. "
               "Record the outcomes actually observed, not the guide's illustrative outcomes."),
        gate(589, 591),
        restore(599, 603),
        publish(599, 603),
        team(599, 603),
        manual(607, 613, "Review red-team evidence and the Foundry scan",
               "Read the latest red-team report/results and the Foundry scan by category/disguise. "
               "Wait for the Foundry scan to complete; starting it does not prove it finished."),
        manual(663, 665, "Review use-case intake",
               "Read UC-001 in governance/use-cases.json before requesting approval."),
        uv(667, 669, "Observe the initial intake refusal", "src/intake.py", "UC-001", expect="intake-deny"),
        manual(685, 691, "Review the confidential-field policy",
               "Read governance/data-policy.json and explain which agents may receive each field. "
               "The next action sets only enforce to true."),
        Step(691, 691, "Enforce the data policy", "enforce"),
        gate(693, 696),
        team(693, 696),
        manual(700, 702, "Prepare a real analyst approval",
               "The next command uses your Entra identity. Review the recommendation and make your own decision; "
               "the exercise asks you to approve ALT-1001. No answers are supplied by the runner."),
        uv(704, 706, "Decide ALT-1001 interactively", "src/decide.py", "ALT-1001",
           expect="decision-approve", interactive=True),
        manual(708, 710, "Prepare a real analyst override",
               "For ALT-1014, exercise the override: answer n, choose a different decision, try an empty reason, "
               "then supply your own meaningful reason. The runner never fabricates a decision."),
        uv(712, 714, "Decide ALT-1014 interactively", "src/decide.py", "ALT-1014",
           expect="decision-override", interactive=True),
        manual(716, 718, "Inspect recorded decisions",
               "Open evidence/decisions.jsonl; verify identity, reasons, timestamps and published versions."),
        uv(722, 724, "Request use-case approval again", "src/intake.py", "UC-001", expect="intake-approve"),
        manual(726, 726, "Optionally demonstrate stale evidence",
               "Republishing invalidates evidence. This optional experiment repeats the gate, red team, "
               "a REAL analyst decision and intake afterward to restore the approved state; these runs are billable.",
               optional="stale-evidence"),
        publish(726, 726, optional="stale-evidence"),
        uv(726, 726, "Observe intake rejecting stale evidence", "src/intake.py", "UC-001",
           expect="intake-deny", optional="stale-evidence"),
        gate(726, 726, optional="stale-evidence"),
        team(726, 726, optional="stale-evidence"),
        uv(726, 726, "Refresh the human decision on the new versions", "src/decide.py", "ALT-1001",
           expect="decision-approve", interactive=True, optional="stale-evidence"),
        uv(726, 726, "Refresh use-case approval after the optional experiment", "src/intake.py", "UC-001",
           expect="intake-approve", optional="stale-evidence"),
        manual(726, 730, "Inspect Azure Policy",
               "Inspect approved-ai-models in the portal. The guide's two-model description is stale; "
               "read the actual approved list without changing it."),
        Step(734, 738, "Verify Azure Policy denies the unapproved model", args=(
            "az", "cognitiveservices", "account", "deployment", "create",
            "-g", "{resource_group}", "-n", "{FOUNDRY_NAME}", "--deployment-name", "unapproved",
            "--model-name", "gpt-4.1-mini", "--model-version", "2025-04-14", "--model-format", "OpenAI",
            "--sku-name", "GlobalStandard", "--sku-capacity", "1"), expect="policy-deny"),
        gate(793, 795),
        manual(797, 799, "Read the cost baseline",
               "Inspect the new Evidence Pack's total and per-alert cost and evals/prices.json."),
        switch(805, 808, "model-routing", "on"),
        gate(805, 808, "pack-observe"),
        manual(810, 816, "Compare routed cost and quality",
               "Compare the new report with the baseline, including triage-lite alerts and reviewer agreement."),
        switch(818, 820, "model-routing", "off"),
        switch(826, 828, "token-limit", "3000"),
        Step(832, 836, "Investigate with scoped OPENAI_LOG=info", "logged-investigation",
             args=("uv", "run", "src/investigate.py", "ALT-1001")),
        switch(840, 842, "token-limit", "200000"),
        manual(846, 865, "Attribute cost by cost center",
               "Run the displayed Kusto query in Application Insights Logs and inspect unassigned costs."),
        manual(867, 869, "Review the caller-attribution query discrepancy",
               "The suggested one-line Caller substitution loses customDimensions during aggregation. "
               "Ask the proctor for the corrected caller-preserving query and verify it in the portal. "
               "If unavailable, leave this review item unresolved; it is not an automatic pass."),
        manual(874, 879, "Wrap up Labs 0-4",
               "Explain cost per alert, cost attribution and the cost/quality tradeoff from your actual results."),
        manual(906, 914, "Understand Web IQ and its cost/data boundary",
               "First-time preparation runs billable checks using a fixed public DOJ topic. "
               "Do not send customer data. Prepare the real Web IQ profile/key privately when prompted."),
        uv(920, 922, "Configure Web IQ and the hosted-agent environment",
           "configure-evidence-agent", interactive=True),
        Step(946, 948, "Deploy all hosted-agent services", args=("azd", "deploy"), interactive=True),
        manual(957, 962, "Confirm hosted evidence-agent deployment",
               "In the existing fincrime project, verify evidence-agent is hosted. "
               "Do not invoke it or connect it to investigation in this exercise."),
    ]


class Guide:
    def __init__(self, path):
        raw = path.read_bytes().replace(b"\r\n", b"\n")
        if hashlib.sha256(raw).hexdigest() != SOURCE_SHA256:
            raise WalkthroughError("The lab instructions changed. Regenerate this runner with the lab-walkthrough skill.")
        self.lines = raw.decode("utf-8").splitlines()
        self.blocks = []
        self.commands = []
        self.context = {}
        lab, heading, opened = "Welcome", "", None
        for number, line in enumerate(self.lines, 1):
            if line.startswith("# Lab ") or line.startswith("# Temporary"):
                lab = line[2:]
            if line.startswith("##"):
                heading = line.lstrip("# ").strip()
            self.context[number] = (lab, heading)
            match = re.fullmatch(r"\s*(?:>\s*)?```(powershell|kusto)\s*", line)
            if match:
                opened = (number, match[1])
            elif opened and re.fullmatch(r"\s*(?:>\s*)?```\s*", line):
                self.blocks.append((*opened, number))
                opened = None
        for start, language, end in self.blocks:
            if language != "powershell":
                continue
            pending, first = "", start + 1
            for number in range(start + 1, end):
                line = re.sub(r"^> ?", "", self.lines[number - 1]).strip()
                if not pending:
                    first = number
                pending += line.rstrip("`").rstrip()
                if line.endswith("`"):
                    pending += " "
                elif pending:
                    self.commands.append((first, number, pending))
                    pending = ""

    @staticmethod
    def matches(command, step):
        if command.startswith("cd "):
            return step.kind == "cwd"
        if command.startswith("$env:UV_NO_COLOR"):
            return step.kind == "colors"
        if command.startswith(("$env:OPENAI_LOG", "Remove-Item Env:OPENAI_LOG")):
            return step.kind == "logged-investigation"
        if command.startswith("$token ="):
            return step.kind == "probe" and step.args[0] == "unpublished"
        if command.startswith("curl.exe "):
            agent = "unpublished" if "/agents/unpublished/" in command else "triage"
            return step.kind == "probe" and step.args[0] == agent
        args = shlex.split(command)
        if args[:2] == ["git", "checkout"]:
            return step.kind == "restore"
        if args[:1] == ["Copy-Item"]:
            return step.kind == "copy" and step.args == (args[1],) and args[2] == PROMPT.as_posix()
        args = [arg.replace("@lab.CloudResourceGroup(PREL35).Name", "{resource_group}")
                .replace("@lab.CloudResourceTemplate(PREL35Ignite).Outputs[FOUNDRY_NAME]", "{FOUNDRY_NAME}")
                for arg in args]
        if args[:2] == ["git", "diff"]:
            args.insert(1, "--no-pager")
        return tuple(args) == step.args and step.kind in ("command", "logged-investigation")

    def check_coverage(self, steps):
        missing = [
            str(start) for start, _, end in self.blocks
            if not any(step.start <= start and step.end >= end for step in steps)
        ]
        if missing:
            raise WalkthroughError("Unmapped command blocks at guide lines: " + ", ".join(missing))
        missing_commands = [
            str(start) for start, end, command in self.commands
            if not any(step.start <= start and step.end >= end and self.matches(command, step) for step in steps)
        ]
        if missing_commands:
            raise WalkthroughError("Unmapped individual commands at guide lines: " + ", ".join(missing_commands))

    def queries(self, step):
        return [
            "\n".join(self.lines[start:end - 1])
            for start, language, end in self.blocks
            if language == "kusto" and step.start <= start and step.end >= end
        ]


def checked_json(path):
    try:
        return json.loads(path.read_text(encoding="utf-8-sig"))
    except (ValueError, OSError) as error:
        raise WalkthroughError(f"Cannot read valid JSON from {path.name}: {type(error).__name__}.") from None


def confirm(message, *, word=None):
    console = Console()
    console.message(message, "CHECKPOINT")
    if word:
        return input(console.styled(
            f"  Type {word} to continue; anything else cancels: ", "CHECKPOINT"
        )).strip() == word
    while True:
        prompt = "[Enter] verified/done, [q] cancel: "
        answer = input(console.styled("  " + prompt, "CHECKPOINT")).strip().lower()
        if answer == "q":
            raise Cancelled("Cancelled at a human checkpoint.")
        if answer == "":
            return True


class Runner:
    def __init__(self, root=ROOT, resource_group="", *, optional=()):
        self.root = root.resolve()
        self.resource_group = resource_group
        self.guide = Guide(self.root / GUIDE)
        self.steps = build_steps()
        self.guide.check_coverage(self.steps)
        self.outcomes = []
        self.optional_groups = frozenset(optional)
        if self.optional_groups - set(OPTIONAL_GROUPS):
            raise WalkthroughError("Unknown optional exercise; see --help for the supported groups.")
        self.child_env = {}
        self.recovery = {}
        self.original_prompt = None
        self.owned_prompt = None
        self.console = Console()

    def say(self, text, status=""):
        self.console.message(text, status)

    def safe_path(self, relative):
        path = self.root / relative
        if path.is_symlink() or not path.resolve().is_relative_to(self.root):
            raise WalkthroughError(f"Refusing a linked/outside-checkout path: {relative}")
        return path

    def settings(self):
        path = self.safe_path(".env")
        allowed = {"AZURE_RESOURCE_GROUP", "FOUNDRY_NAME", "APIM_NAME", "APIM_GATEWAY_URL"}
        settings = {}
        if path.exists():
            for line in path.read_text(encoding="utf-8-sig").splitlines():
                match = re.match(r"^\s*([A-Z_][A-Z_0-9]*)\s*=\s*(.*?)\s*$", line)
                if match and match[1] in allowed:
                    value = match[2]
                    if value.startswith('"'):
                        try:
                            value = json.loads(value)
                        except ValueError:
                            raise WalkthroughError(f"Invalid .env quoting for {match[1]}.") from None
                    elif value.startswith("'") and value.endswith("'"):
                        value = value[1:-1]
                    if not isinstance(value, str) or any(c in value for c in "\r\n"):
                        raise WalkthroughError(f"Invalid .env value for {match[1]}.")
                    settings[match[1]] = value
        saved = settings.get("AZURE_RESOURCE_GROUP", "")
        if saved and self.resource_group and saved.casefold() != self.resource_group.casefold():
            raise WalkthroughError("The requested resource group differs from .env. No lab was retargeted.")
        group = self.resource_group or saved
        if group:
            settings["resource_group"] = group
        return settings

    def resolve(self, args):
        settings = self.settings()
        result = []
        for arg in args:
            if arg.startswith("{") and arg.endswith("}"):
                key = arg[1:-1]
                if not settings.get(key):
                    raise WalkthroughError(f"Missing {key}; supply --resource-group or complete setup-lab first.")
                arg = settings[key]
            if "@lab." in arg or "\n" in arg or "\r" in arg:
                raise WalkthroughError("An argument contains an unresolved lab token or newline.")
            result.append(arg)
        return result

    def process(self, args, *, interactive=False, private=False, overrides=None):
        executable = shutil.which(args[0])
        if not executable:
            raise WalkthroughError(f"Missing executable: {args[0]}. Install the documented VM prerequisites separately.")
        env = {**os.environ, "PYTHONUNBUFFERED": "1", "PYTHONIOENCODING": "utf-8",
               **self.child_env, **(overrides or {})}
        command = [executable, *args[1:]]
        if interactive or private:
            if not private:
                self.say("Interactive command: answer its prompts below.", "CHECKPOINT")
            result = subprocess.run(
                command, cwd=self.root, env=env, shell=False, check=False,
                capture_output=private, text=True, encoding="utf-8", errors="replace",
            )
            if private and result.returncode:
                raise WalkthroughError("Private Azure token acquisition failed. Check sign-in; token output was not logged.")
            if not private:
                print(flush=True)
            return result.returncode, result.stdout or ""
        self.console.message("Command output:", style="muted")
        output = []
        with subprocess.Popen(command, cwd=self.root, env=env, shell=False,
                              stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                              text=True, encoding="utf-8", errors="replace") as child:
            try:
                for line in child.stdout:
                    print(line, end="", flush=True)
                    output.append(line)
                print(flush=True)
                return child.wait(), ANSI.sub("", "".join(output))
            except BaseException:
                child.terminate()
                try:
                    child.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    child.kill()
                    child.wait()
                raise

    def preflight(self):
        if not sys.stdin.isatty():
            raise WalkthroughError("Live execution requires an interactive terminal for real human decisions and secrets.")
        for tool in ("git", "az", "uv", "azd"):
            if not shutil.which(tool):
                raise WalkthroughError(f"Missing {tool}; prepare the documented VM prerequisites first.")
        settings = self.settings()
        if not settings.get("resource_group"):
            raise WalkthroughError("Live execution needs --resource-group or AZURE_RESOURCE_GROUP in .env.")
        for relative in (PROMPT, DATA_POLICY, REGISTER, Path("evidence")):
            self.safe_path(relative)
        code, _ = self.process(["git", "diff", "--quiet", "HEAD", "--",
                                str(PROMPT), str(DATA_POLICY), str(REGISTER)])
        if code:
            raise WalkthroughError("Prompt/governance files have existing edits. Use a fresh lab checkout; nothing was overwritten.")
        if checked_json(self.root / DATA_POLICY).get("enforce") is not False:
            raise WalkthroughError("The walkthrough starts before data-policy enforcement. Existing policy was not reset.")
        register = checked_json(self.root / REGISTER)
        use_case = next((item for item in register if item.get("id") == "UC-001"), None)
        if not use_case or use_case.get("approval"):
            raise WalkthroughError("UC-001 is missing or already approved. Use a fresh lab; approvals were not reset.")
        self.original_prompt = (self.root / PROMPT).read_bytes()
        if not confirm(
            f"Target resource group: {settings['resource_group']}\n"
            "This runs billable evaluations/research/deployment and changes the lab. "
            "Use the learner account and confirm the Skillable APIM policy/roles are fixed. "
            "Existing Evidence Packs and decisions will be retained.", word="RUN"
        ):
            raise Cancelled("Live execution was not confirmed.")

    def set_prompt(self, content):
        path = self.safe_path(PROMPT)
        expected = self.owned_prompt if self.owned_prompt is not None else self.original_prompt
        if expected is None or path.read_bytes() != expected:
            raise WalkthroughError("The triage prompt changed outside this runner; refusing to overwrite it.")
        path.write_bytes(content)
        self.owned_prompt = content if content != self.original_prompt else None

    def pack_snapshot(self):
        return {path: (path.stat().st_mtime_ns, path.read_bytes())
                for path in self.safe_path("evidence").glob("*/manifest.json")}

    def check_pack(self, before, code, expect, redteam):
        after = self.pack_snapshot()
        fresh = [path for path, signature in after.items()
                 if before.get(path) != signature and path.parent.name.endswith("-redteam") == redteam]
        if len(fresh) != 1:
            raise WalkthroughError("The command did not produce exactly one fresh Evidence Pack; exit code alone is not evidence.")
        path = fresh[0]
        manifest = checked_json(path)
        rows = checked_json(path.parent / "results.json")
        if not isinstance(manifest, dict) or not isinstance(rows, list) or not all(isinstance(row, dict) for row in rows):
            raise WalkthroughError("The Evidence Pack has invalid manifest/result types.")
        verdict = manifest.get("verdict")
        report = (path.parent / "report.md").read_text(encoding="utf-8")
        label = "Red team" if redteam else "Release gate"
        if verdict not in ("PASS", "FAIL") or code != (verdict == "FAIL"):
            raise WalkthroughError("Evidence verdict does not match the process exit code.")
        if not report.startswith(f"# {label}: {verdict}") or not manifest.get("agents"):
            raise WalkthroughError("The fresh Evidence Pack is incomplete or inconsistent.")
        if redteam:
            expected = {item["id"] for item in checked_json(self.root / "evals/attacks.json")}
            if len(rows) != len(expected) or {row.get("id") for row in rows} != expected:
                raise WalkthroughError("The red-team Evidence Pack does not cover every documented attack.")
            if any(row.get("outcome") not in ("blocked", "resisted", "SUCCEEDED") for row in rows):
                raise WalkthroughError("The red-team result contains an unknown outcome.")
            if (verdict == "FAIL") != any(row["outcome"] == "SUCCEEDED" for row in rows):
                raise WalkthroughError("The red-team verdict contradicts the attack results.")
        else:
            expected = {json.loads(line)["alert_id"] for line in
                        (self.root / "data/golden.jsonl").read_text(encoding="utf-8").splitlines() if line.strip()}
            if len(rows) != len(expected) or {row.get("alert_id") for row in rows} != expected or not manifest.get("checks"):
                raise WalkthroughError("The release Evidence Pack does not cover every golden alert and metric.")
        self.say(f"Evidence: {path.parent.relative_to(self.root)} ({verdict})")
        if expect == "pack-pass" and verdict != "PASS":
            raise WalkthroughError("The required gate failed. Inspect its evidence; the runner will not weaken thresholds.")
        if expect == "pack-fail" and verdict != "FAIL":
            raise WalkthroughError("The teaching outcome was not reproduced: this intentionally weak prompt passed.")
        return "EXPECTED FAIL" if expect == "pack-fail" else "OBSERVED FAIL" if verdict == "FAIL" else "PASS"

    def check_result(self, step, code, output, before):
        if step.expect.startswith("pack-"):
            return self.check_pack(before, code, step.expect, "src/redteam.py" in step.args)
        if step.expect == "diff":
            if code in (0, 1):
                return "PASS"
        elif step.expect == "attack":
            prefix = output.partition("Agent answer:")[0]
            outcomes = re.findall(r"(?m)^(blocked|resisted|SUCCEEDED)(?: \([^\r\n]*\))?\s*$", prefix)
            if (outcomes and "Agent answer:" in output
                    and re.search(rf"(?m)^{re.escape(step.args[-1])}:", output)
                    and code == (outcomes[-1] == "SUCCEEDED")):
                return "OBSERVED FAIL" if code else "PASS"
        elif step.expect == "intake-deny":
            checks = ("Release gate passed on the published agents", "Red team passed on the published agents",
                      "Data policy enforced", "A named analyst decided alerts on the published agents")
            if code == 1 and all(check in output for check in checks) and "Not approved. Fix the failures" in output:
                return "EXPECTED FAIL"
        elif step.expect == "policy-deny":
            if code and "RequestDisallowedByPolicy" in output and "policy" in output.lower():
                return "EXPECTED FAIL"
            if code == 0:
                raise WalkthroughError("The unapproved deployment was CREATED. Stop and notify the organizer; no automatic deletion was attempted.")
        elif step.expect.startswith("decision-") and code == 0:
            path = self.root / "evidence/decisions.jsonl"
            lines = path.read_text(encoding="utf-8").splitlines()
            if len(lines) == before + 1:
                decision = json.loads(lines[-1])
                approval = step.expect == "decision-approve"
                if (decision.get("alert") == step.args[-1] and decision.get("analyst")
                        and decision.get("approved_recommendation") is approval
                        and (approval or decision.get("reason", "").strip())):
                    return "PASS"
            raise WalkthroughError("The recorded human decision did not complete the guide's approve/override exercise. It was not altered.")
        elif step.expect == "intake-approve" and code == 0:
            path = self.root / REGISTER
            register = checked_json(path)
            approval = next(item for item in register if item["id"] == "UC-001").get("approval")
            if (path.read_bytes() != before and approval and approval.get("approved_by")
                    and approval.get("agents") and approval.get("evidence")):
                return "PASS"
        elif step.expect == "success" and code == 0:
            return "PASS"
        hint = " Retry the switch command with --debug to see the Azure error." if "set-gateway" in step.args else ""
        raise WalkthroughError(f"Unexpected result (exit {code}); expected {step.expect}.{hint}")

    def probe(self, agent, expected):
        gateway = self.settings().get("APIM_GATEWAY_URL", "")
        parsed = urllib.parse.urlsplit(gateway)
        if (parsed.scheme != "https" or not (parsed.hostname or "").endswith(".azure-api.net")
                or parsed.username or parsed.password or parsed.port not in (None, 443)
                or parsed.path not in ("", "/") or parsed.query or parsed.fragment):
            raise WalkthroughError("APIM_GATEWAY_URL must be the lab's HTTPS azure-api.net gateway.")
        headers = {}
        if agent == "unpublished":
            _, token = self.process(
                ["az", "account", "get-access-token", "--resource", "https://ai.azure.com",
                 "--query", "accessToken", "-o", "tsv"], private=True,
            )
            if not token.strip() or "\n" in token.strip() or "\r" in token.strip():
                raise WalkthroughError("Token acquisition returned an invalid token; its contents were not logged.")
            headers["Authorization"] = "Bearer " + token.strip()
        request = urllib.request.Request(gateway.rstrip("/") + f"/agents/{agent}/responses",
                                         data=b"", headers=headers, method="POST")
        # Never forward a bearer token through a redirect to another host.
        class NoRedirect(urllib.request.HTTPRedirectHandler):
            def redirect_request(self, req, fp, code, msg, response_headers, newurl):
                return None

        try:
            with urllib.request.build_opener(NoRedirect).open(request, timeout=60) as response:
                status = response.status
        except urllib.error.HTTPError as error:
            status = error.code
            error.close()
        if status != int(expected):
            raise WalkthroughError(f"Gateway probe returned HTTP {status}, expected {expected}.")
        self.say(f"Observed HTTP {status}")
        return "EXPECTED FAIL"

    def wait_for_gateway(self):
        for remaining in range(60, 0, -10):
            self.say(f"Gateway propagation: {remaining}s remaining...", "WAIT")
            time.sleep(10)

    def command(self, step):
        args = self.resolve(step.args)
        if step.expect == "policy-deny" and not confirm(
            "This sends a REAL model-create request, expected to be denied. "
            "If policy is misconfigured it can create a billable deployment.", word="TEST DENY"
        ):
            raise Cancelled("The model-create policy test was not authorized.")
        before = None
        if step.expect.startswith("pack-"):
            before = self.pack_snapshot()
        elif step.expect.startswith("decision-"):
            path = self.safe_path("evidence/decisions.jsonl")
            before = len(path.read_text(encoding="utf-8").splitlines()) if path.exists() else 0
        elif step.expect == "intake-approve":
            before = self.safe_path(REGISTER).read_bytes()
        if "create-agents" in args and (args[-1] == "gpt-5-nano" or self.owned_prompt is not None):
            self.recovery["approved agents"] = "Restore the approved local prompt, then: uv run create-agents"
        if "set-gateway" in args:
            name, value = args[-2:]
            if name in ("model-routing", "token-limit"):
                default = "off" if name == "model-routing" else "200000"
                self.recovery[name] = f"uv run set-gateway {name} {default}"
        code, output = self.process(args, interactive=step.interactive)
        status = self.check_result(step, code, output, before)
        if "create-agents" in args and args[-1] != "gpt-5-nano" and self.owned_prompt is None:
            self.recovery.pop("approved agents", None)
        if "set-gateway" in args:
            self.wait_for_gateway()
            if args[-2:] in (["model-routing", "off"], ["token-limit", "200000"]):
                self.recovery.pop(args[-2], None)
        return status

    def action(self, step):
        if step.kind == "command":
            return self.command(step)
        if step.kind == "manual":
            for query in self.guide.queries(step):
                self.console.block("Kusto query - run in the portal", query)
            self.say("Listed for review in the final report; continuing without a prompt.", "REVIEW NEEDED")
            return "REVIEW NEEDED"
        if step.kind == "cwd":
            self.say(f"Working directory for every child command: {self.root}")
        elif step.kind == "colors":
            self.child_env["UV_NO_COLOR"] = "1"
            self.say("UV_NO_COLOR=1 applies only to child processes in this walkthrough.")
        elif step.kind == "copy":
            content = self.safe_path(step.args[0]).read_bytes()
            self.set_prompt(content)
        elif step.kind == "restore":
            self.set_prompt(self.original_prompt)
        elif step.kind == "edit":
            path = self.safe_path(PROMPT)
            current = path.read_bytes()
            if current != self.original_prompt:
                raise WalkthroughError("The optional edit must start from the approved prompt.")
            try:
                confirm(step.detail)
            finally:
                # The edit checkpoint explicitly owns changes the user makes here, including on cancellation.
                changed = path.read_bytes()
                if changed != current:
                    self.owned_prompt = changed
        elif step.kind == "enforce":
            path = self.safe_path(DATA_POLICY)
            policy = checked_json(path)
            policy["enforce"] = True
            path.write_text(json.dumps(policy, indent=2) + "\n", encoding="utf-8")
        elif step.kind == "probe":
            return self.probe(*step.args)
        elif step.kind == "logged-investigation":
            code, output = self.process(self.resolve(step.args), overrides={"OPENAI_LOG": "info"})
            if code:
                raise WalkthroughError(f"Throttled investigation failed (exit {code}).")
            self.say("Review the actual client retry/429 behavior above; it was not automatically verified. "
                     "OPENAI_LOG was scoped to this child and is now restored.", "REVIEW NEEDED")
            return "REVIEW NEEDED"
        else:
            raise WalkthroughError(f"Unimplemented action: {step.kind}")
        return "PASS"

    def preview(self, step):
        if step.args and step.kind in ("command", "logged-investigation"):
            return " ".join(step.args)
        return {
            "cwd": "Python: run all children with cwd=repository root",
            "copy": f"Python file copy: {step.args[0]} -> {PROMPT}" if step.args else "",
            "restore": "Python: restore verified approved prompt bytes (no Git checkout)",
            "enforce": "Python JSON edit: governance/data-policy.json enforce=true",
            "colors": "Scoped environment: UV_NO_COLOR=1",
            "probe": "Python HTTPS POST; token captured privately only for the 404 probe",
            "manual": "Review without pausing: " + step.detail,
            "edit": "Human prompt-edit checkpoint: " + step.detail,
        }.get(step.kind, step.kind)

    def run(self, *, execute=False):
        current_lab = None
        complete = False
        exit_code = 1
        issues = []
        try:
            self.console.heading("PREL35 | full guided walkthrough")
            self.say("LIVE EXECUTION - interactive, billable, changes lab state." if execute
                     else "PLAN ONLY - no commands or changes.", "NOTE" if execute else "PLAN")
            for note in NOTES:
                self.say(note, "NOTE")
            if execute:
                self.preflight()
            for index, step in enumerate(self.steps, 1):
                lab, heading = self.guide.context[step.start]
                if lab != current_lab:
                    current_lab = lab
                    self.console.heading(lab)
                self.console.step(index, len(self.steps), step, heading, self.preview(step), source=GUIDE)
                if "set-gateway" in step.args:
                    self.say("Then wait 60s for gateway propagation.", "WAIT")
                if not execute:
                    for query in self.guide.queries(step):
                        self.console.block("Kusto query - run in the portal", query)
                    self.outcomes.append(Outcome(index, lab, step, "PLAN"))
                    continue
                started = time.monotonic()
                try:
                    if step.optional and step.optional not in self.optional_groups:
                        status = "SKIPPED OPTIONAL"
                    else:
                        status = self.action(step)
                except (WalkthroughError, OSError, ValueError, urllib.error.URLError, KeyboardInterrupt, EOFError) as error:
                    self.outcomes.append(Outcome(
                        index, lab, step, "ERROR", time.monotonic() - started,
                        str(error) or "Cancelled.",
                    ))
                    raise
                elapsed = time.monotonic() - started
                detail = ""
                if status == "REVIEW NEEDED":
                    detail = step.detail or "Review the actual retry/429 behavior in this command's output."
                self.outcomes.append(Outcome(index, lab, step, status, elapsed, detail))
                self.say(f"{step.title} ({elapsed:.1f}s)", status)
            complete = True
            exit_code = 0
        except (WalkthroughError, OSError, ValueError, urllib.error.URLError, KeyboardInterrupt, EOFError) as error:
            issues.append(str(error) or "Cancelled.")
            self.say(issues[-1], "ERROR")
        finally:
            if execute and self.owned_prompt is not None:
                try:
                    self.set_prompt(self.original_prompt)
                    self.say("Restored only the runner-owned local prompt change.")
                except (WalkthroughError, OSError) as error:
                    complete = False
                    exit_code = 1
                    issues.append(f"Local restoration needs review: {error}")
            if execute and self.recovery:
                complete = False
                exit_code = 1
            if execute:
                for index in range(len(self.outcomes), len(self.steps)):
                    step = self.steps[index]
                    lab, _ = self.guide.context[step.start]
                    self.outcomes.append(Outcome(index + 1, lab, step, "NOT RUN"))
            self.console.heading("Walkthrough report")
            for issue in issues:
                self.say(issue, "ERROR")
            if execute and self.recovery:
                self.say("Cloud state may be partway through an exercise. No automatic cloud rollback was attempted.",
                         "ERROR")
                for recovery in self.recovery.values():
                    self.say("Recovery: " + recovery)
            for lab in dict.fromkeys(item.lab for item in self.outcomes):
                entries = [item for item in self.outcomes if item.lab == lab]
                counts = Counter(item.status for item in entries)
                self.console.message(lab, style="command")
                self.console.message(", ".join(f"{count} {status}" for status, count in counts.items())
                                     + f" | elapsed {sum(item.elapsed for item in entries):.1f}s", indent=4)
                if execute:
                    for item in entries:
                        self.console.message(
                            f"Step {item.number:02}: {item.step.title}"
                            + ("" if item.status == "NOT RUN" else f" ({item.elapsed:.1f}s)"),
                            item.status, indent=4,
                        )
                        if item.detail:
                            self.console.message(item.detail, indent=6)
                            self.console.message(
                                f"Source: {GUIDE}:{item.step.start}-{item.step.end}", indent=6,
                                style="muted",
                            )
            if not execute:
                self.say(f"{len(self.steps)} planned actions; {len(self.guide.blocks)} command/query blocks mapped; "
                         f"{len(self.guide.commands)} terminal commands individually accounted for. "
                         "No subprocesses, prompts, writes, waits or Azure calls were made.", "PLAN")
            elif complete:
                pending = sum(item.status == "REVIEW NEEDED" for item in self.outcomes)
                self.say(f"Automated flow finished; {pending} manual review item(s) remain unverified. "
                         "OBSERVED FAIL entries are real quality/security outcomes, not successful gates.")
            else:
                self.say("INCOMPLETE: later steps were not executed. Evidence and human decisions were retained.", "ERROR")
        return exit_code


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--plan", action="store_true", help="Print source-mapped coverage without side effects (default).")
    mode.add_argument("--execute", action="store_true", help="Run the billable walkthrough with real human checkpoints.")
    parser.add_argument("--resource-group", default="", help="Existing lab resource group; must match .env if present.")
    parser.add_argument("--optional", action="append", choices=OPTIONAL_GROUPS, default=[],
                        help="Include an optional exercise; repeat to select several. Default: skip all.")
    args = parser.parse_args(argv)
    try:
        return Runner(resource_group=args.resource_group, optional=args.optional).run(execute=args.execute)
    except (WalkthroughError, OSError) as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
