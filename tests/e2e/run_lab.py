"""Guided, opt-in PREL35 walkthrough. Default: print the plan without changing anything.

    uv run --no-sync tests/e2e/run_lab.py --plan
    uv run --no-sync tests/e2e/run_lab.py --execute --resource-group PREL35
    uv run --no-sync tests/e2e/run_lab.py --execute --resource-group PREL35 --start-step 17

Uses only the standard library. Remove tests/e2e/ to remove this harness.
Live execution is interactive, billable, and changes the lab's agents and settings.
Optional exercises are skipped unless selected with --optional EXERCISE.
--start-step N starts at the inclusive step number shown by --plan.
Skipped prerequisites must already be complete; no earlier actions are replayed.
Read-only/manual checks are listed for review at the end, without pausing.
Decisions and intake use labeled lab-test automation unless --interactive-decisions is supplied.
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
SOURCE_SHA256 = "b3c2ae78037d9a90c29b9e019e14dee34cd727381f2f7650140e6476a6f8748e"
ANSI = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]")
PROMPT = Path("src/agents/triage.md")
DATA_POLICY = Path("governance/data-policy.json")
REGISTER = Path("governance/use-cases.json")
OPTIONAL_GROUPS = ("editor", "colors", "tuning", "decision-json", "full-comparison")
DECISION_REPORTS = {
    "choice-review": "evidence/decision1-investigations",
    "investigation-compare": "evidence/investigation-comparison",
}
NOTES = (
    "Azure must already be provisioned. The runner never assigns roles or bypasses policy.",
    "Gateway updates need APIM permissions AND a Skillable policy allowing "
    "Microsoft.ApiManagement/service/namedValues at every inherited scope.",
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
        Step(360, 362, "Use the checkout as the working directory", "cwd"),
        manual(364, 370, "Prepare Azure sign-in",
               "Sign in as the learner for this lab, not a personal account. The next command is interactive."),
        Step(366, 368, "Sign in to Azure", args=("az", "login"), interactive=True),
        Step(374, 376, "Open VS Code", args=("code", "."), optional="editor"),
        uv(380, 382, "Prepare the existing lab", "setup-lab", "--resource-group", "{resource_group}"),
        publish(391, 393),
        Step(397, 399, "Demonstrate color opt-out for child commands", "colors", optional="colors"),
        manual(401, 412, "Review the four published prompt agents",
               "Inspect reviewer, triage, triage-systemone and triage-lite. The Decision-1 explanation runner "
               "uses triage-systemone in Lab 4; the local Decision-1 calls do not create agents."),
        uv(420, 436, "Configure Web IQ and the hosted-agent environment",
           "configure-evidence-agent", interactive=True,
           detail="If asked for a key, select the Web IQ profile linked to the billing resource shown "
                  "in the terminal. Select Skip on the 'You're ready to use WebIQ' screen, create an "
                  "API key with the web service enabled, and paste it into the hidden prompt. "
                  "The key is saved in Git-ignored .env; Web IQ calls are billable."),
        Step(439, 441, "Deploy the hosted evidence agent", args=("azd", "deploy"), interactive=True),
        manual(447, 490, "Tour Azure and Foundry",
               "Inspect keyless Foundry, gateway operations/policy and monitoring; read triage's instructions "
               "in the fincrime project. Confirm the selected subscription and resource group."),
        uv(501, 503, "Investigate ALT-1001", "src/investigate.py", "ALT-1001"),
        uv(509, 509, "Repeat the investigation as requested in prose", "src/investigate.py", "ALT-1001"),
        Step(516, 518, "Expect HTTP 401 without a token", "probe", args=("triage", "401")),
        Step(524, 527, "Acquire a token privately; expect HTTP 404", "probe", args=("unpublished", "404")),
        manual(531, 540, "Check your understanding",
               "Explain the gateway's downstream identity, how agent changes are published, and who decides alerts."),
        gate(660, 662, "pack-pass", "--foundry"),
        manual(668, 674, "Inspect baseline release evidence",
               "Open the new report.md and compare every blocking metric with its threshold."),
        Step(679, 681, "Compare the concise prompt", args=(
            "git", "--no-pager", "diff", "--no-index",
            "src/agents/triage.md", "src/agents/variants/triage.concise.md"), expect="diff"),
        Step(687, 691, "Apply the concise prompt", "copy", args=("src/agents/variants/triage.concise.md",)),
        publish(687, 691),
        gate(687, 691, "pack-fail"),
        Step(697, 697, "Open reports in VS Code", args=("code", "."), optional="editor"),
        manual(693, 703, "Review the failed gate and its exit code",
               "Inspect citation validity, escalation recall and token savings. The preceding gate verified "
               "exit code 1 (the guide's $LASTEXITCODE check) against its new Evidence Pack."),
        restore(709, 713),
        publish(709, 713),
        gate(709, 713),
        publish(723, 726, "gpt-5-nano"),
        gate(723, 726, "pack-observe"),
        publish(732, 734),
        manual(736, 765, "Inspect a passing Evidence Pack",
               "Open report.md, manifest.json and results.json; inspect versions, model, hashes and source commit."),
        manual(767, 798, "Inspect traces and the Foundry AI judge",
               "Run the displayed Kusto query in Application Insights Logs, then open the Release gate run in "
               "Foundry Evaluations and read Task adherence and its reasons. This portal review remains unverified."),
        manual(802, 804, "Review the release pipeline",
               "Read .github/workflows/release-gate.yml. Do not execute a deployment pipeline."),
        manual(806, 815, "Check your understanding",
               "Explain why one successful response is insufficient, what a reviewer adds, and why prompt "
               "changes need evaluation just as model changes do. Expand the guide's answers to compare."),
        Step(819, 822, "Edit the smaller-model prompt yourself", "edit", optional="tuning",
             detail="Review the nano report; edit only src/agents/triage.md for the optional exercise."),
        publish(825, 828, "gpt-5-nano", optional="tuning"),
        gate(825, 828, "pack-observe", optional="tuning"),
        gate(830, 830, "pack-observe", optional="tuning"),
        restore(834, 837, optional="tuning"),
        publish(834, 837, optional="tuning"),
        manual(949, 953, "Read the attack cases",
               "Open evals/attacks.json and identify document-borne versus caller-borne attacks."),
        team(955, 957, "pack-observe", "--foundry"),
        team(965, 969, "attack", "JB-01"),
        team(965, 969, "attack", "HRM-01"),
        team(965, 969, "attack", "HRM-02"),
        manual(977, 986, "Inspect the Foundry guardrail",
               "Read Microsoft.DefaultV2 on gpt-5-mini: content categories, severity and jailbreak detection."),
        Step(995, 997, "Compare the helpful prompt", args=(
            "git", "--no-pager", "diff", "--no-index",
            "src/agents/triage.md", "src/agents/variants/triage.helpful.md"), expect="diff"),
        Step(1002, 1006, "Apply the helpful prompt", "copy", args=("src/agents/variants/triage.helpful.md",)),
        publish(1002, 1006),
        team(1002, 1006, "pack-fail"),
        team(1012, 1014, "attack", "JB-02"),
        switch(1024, 1026, "prompt-shields", "on"),
        team(1030, 1032),
        manual(1034, 1040, "Inspect layered protections",
               "Compare prompt_attack_detected blocks with IPI-02, IPI-04 and JB-02. "
               "Record the outcomes actually observed, not the guide's illustrative outcomes."),
        gate(1044, 1046),
        restore(1054, 1058),
        publish(1054, 1058),
        team(1054, 1058),
        manual(1062, 1070, "Review red-team evidence and the Foundry scan",
               "Read the latest red-team report/results and the Foundry scan by category/disguise. "
               "Wait for the Foundry scan to complete; starting it does not prove it finished."),
        manual(1072, 1081, "Check your understanding",
               "Explain why normal-task quality does not prove security, why documents are input rather than "
               "authority, and how security layers complement each other. Expand the guide's answers to compare."),
        manual(1172, 1174, "Review use-case intake",
               "Read UC-001 in governance/use-cases.json before requesting approval."),
        uv(1176, 1178, "Observe the initial intake refusal", "src/intake.py", "UC-001", expect="intake-deny"),
        manual(1211, 1222, "Review the data policy",
               "Read governance/data-policy.json and explain which agents may receive each field and document. "
               "The next action sets only enforce to true."),
        Step(1222, 1222, "Enforce the data policy", "enforce"),
        gate(1224, 1227),
        team(1224, 1227),
        manual(1231, 1240, "Review the evidence agent's data boundary",
               "Research goes by approved topic only, to official sources. The next command sends a fictional, "
               "PII-free scenario to the hosted agent; Web IQ calls are billable."),
        Step(1242, 1244, "Ask the hosted evidence agent for a brief", args=(
            "azd", "ai", "agent", "invoke", "evidence-agent",
            "Review this fictional, PII-free scenario: unfamiliar incoming transfers are forwarded to a "
            "cryptocurrency exchange; the holder says a remote payment-assistant job requires this, but no "
            "employer contract or sales records are supplied. Provide a cited evidence brief with alternatives "
            "and missing evidence, not a decision.",
            "--protocol", "responses", "--new-session")),
        manual(1248, 1252, "Review the decision acceptance exercise",
               "Default mode scripts a labeled lab-test acceptance for ALT-1008. With --interactive-decisions, "
               "review the recommendation and choose yourself under your Entra identity."),
        uv(1254, 1256, "Decide ALT-1008", "src/decide.py", "ALT-1008",
           expect="decision-approve", interactive=True),
        manual(1260, 1260, "Review the decision override exercise",
               "Default mode scripts a labeled lab-test override to request_info for ALT-1014 with a test reason. "
               "With --interactive-decisions, the guide suggests n and a reason, but either valid choice is accepted."),
        uv(1262, 1264, "Decide ALT-1014", "src/decide.py", "ALT-1014",
           expect="decision-override", interactive=True),
        manual(1268, 1268, "Inspect recorded decisions",
               "Scripted records are in tests/e2e/artifacts/decisions.jsonl; interactive records are in "
               "evidence/decisions.jsonl. Verify actor, reasons, timestamps and published versions."),
        uv(1272, 1274, "Request use-case approval again", "src/intake.py", "UC-001", expect="intake-approve"),
        manual(1280, 1282, "Inspect Azure Policy",
               "Inspect approved-ai-models in the portal and read its approved list without changing it."),
        Step(1288, 1301, "Test the unapproved model deployment", args=(
            "az", "cognitiveservices", "account", "deployment", "create",
            "-g", "{resource_group}", "-n", "{FOUNDRY_NAME}", "--deployment-name", "unapproved",
            "--model-name", "gpt-4.1-mini", "--model-version", "2025-04-14", "--model-format", "OpenAI",
            "--sku-name", "GlobalStandard", "--sku-capacity", "1"), expect="policy-deny",
             detail="RequestDisallowedByPolicy demonstrates policy denial. AuthorizationFailed demonstrates "
                    "role-based refusal instead; the policy remains unverified and needs portal review."),
        manual(1303, 1312, "Check your understanding",
               "Explain why approvals need reassessment, how purpose-limited data access differs from model "
               "storage/training and service processing/retention, and how meaningful oversight combines LLM "
               "recommendations with deterministic information. Expand the guide's answers to compare."),
        gate(1400, 1402),
        manual(1404, 1408, "Read the cost baseline",
               "Inspect the new Evidence Pack's total and per-alert cost and evals/prices.json."),
        switch(1416, 1419, "model-routing", "on"),
        gate(1416, 1419, "pack-observe"),
        manual(1421, 1427, "Compare routed cost and quality",
               "Compare the new report with the baseline, including triage-lite alerts and reviewer agreement."),
        switch(1431, 1433, "model-routing", "off"),
        uv(1451, 1453, "Decision-1 chooses, GPT explains, Decision-1 reviews",
           "python", "src/decisions/triage_systemone.py", "ALT-1001", expect="choice-explain"),
        manual(1455, 1462, "Review explanation cost and citations",
               "Compare the choosing call with the total time and cost. Read reasons, gaps and conflicts "
               "and check citations; GPT writes an explanation, not another vote."),
        uv(1462, 1462, "Inspect the optional full Decision-1 explanation record",
           "python", "src/decisions/triage_systemone.py", "ALT-1001", "--json",
           expect="choice-json", optional="decision-json"),
        uv(1468, 1470, "Decision-1 triage and challenged review",
           "python", "src/decisions/investigate_decisions.py", "ALT-1008", "--challenge",
           expect="choice-review"),
        manual(1472, 1479, "Review the challenged verdicts",
               "Inspect escalation, reviewer agreement and both wrong-disposition challenges. "
               "Consider whether the analyst needs a written explanation; no GPT is called on this path."),
        uv(1487, 1489, "Compare seven investigations",
           "python", "src/decisions/compare_investigations.py",
           "ALT-1001", "ALT-1002", "ALT-1005", "ALT-1006", "ALT-1008", "ALT-1010", "ALT-1013",
           "--workers", "2", expect="investigation-compare",
           detail="Allow about 1-1.5 minutes. Classic GPT runs first, then Decision-1. Both use the same "
                  "policy-filtered cases; GPT writes reasons and reviewer issues, Decision-1 returns "
                  "typed choices and verdicts. Read along while the command streams its output."),
        manual(1501, 1507, "Review golden matches, reviewer verdicts and cost",
               "Read cost and time per alert, triage matches, escalation recall and unavailable calls. "
               "Check ALT-1005 should escalate. A correctness checkmark is separate from reviewer agreement. "
               "Inspect evidence/investigation-comparison; Decision-1 output tokens are free."),
        uv(1509, 1509, "Optionally compare all 14 investigations",
           "python", "src/decisions/compare_investigations.py", "--workers", "2",
           expect="investigation-compare", optional="full-comparison"),
        switch(1517, 1519, "token-limit", "3000"),
        Step(1523, 1527, "Investigate with scoped OPENAI_LOG=info", "logged-investigation",
             args=("uv", "run", "src/investigate.py", "ALT-1001")),
        switch(1533, 1535, "token-limit", "200000"),
        manual(1541, 1563, "Attribute cost by cost center",
               "Run the displayed Kusto query in Application Insights Logs and inspect unassigned costs."),
        manual(1567, 1567, "Attribute cost by caller",
               "Change by CostCenter on the query's last summarize line to by Caller, run it again in the portal, "
               "and check the cost per person."),
        manual(1572, 1577, "Wrap up solution optimization",
               "Explain cost per alert, cost attribution, the cost/quality tradeoff, and when a generated "
               "explanation adds value, using your actual results."),
        manual(1579, 1588, "Check your understanding",
               "Explain how to compare end-to-end value, when to separate a decision from its explanation, "
               "and how to attribute spending across all calling paths. Expand the guide's answers to compare."),
        manual(1590, 1637, "Wrap up Labs 0-4",
               "Answer each stakeholder from the evidence you produced."),
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
            if line.startswith("# Lab "):
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
    def __init__(self, root=ROOT, resource_group="", *, optional=(), interactive_decisions=False,
                 start_step=1):
        self.root = root.resolve()
        self.resource_group = resource_group
        self.guide = Guide(self.root / GUIDE)
        self.steps = build_steps()
        if not 1 <= start_step <= len(self.steps):
            raise WalkthroughError(f"--start-step must be between 1 and {len(self.steps)}.")
        self.start_step = start_step
        self.guide.check_coverage(self.steps)
        self.outcomes = []
        self.interactive_decisions = interactive_decisions
        self.tested_agents = None
        self.optional_groups = frozenset(optional)
        if self.optional_groups - set(OPTIONAL_GROUPS):
            raise WalkthroughError("Unknown optional exercise; see --help for the supported groups.")
        self.child_env = {}
        self.recovery = {}
        self.original_prompt = None
        self.approved_prompt = None
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
        if self.start_step == 1:
            if checked_json(self.root / DATA_POLICY).get("enforce") is not False:
                raise WalkthroughError("The walkthrough starts before data-policy enforcement. Existing policy was not reset.")
            register = checked_json(self.root / REGISTER)
            use_case = next((item for item in register if item.get("id") == "UC-001"), None)
            if not use_case or use_case.get("approval"):
                raise WalkthroughError("UC-001 is missing or already approved. Use a fresh lab; approvals were not reset.")
        self.original_prompt = (self.root / PROMPT).read_bytes()
        self.approved_prompt = self.original_prompt
        if any("setup-lab" in step.args for step in self.steps[:self.start_step - 1]):
            if not self.safe_path(".env").is_file():
                raise WalkthroughError("Starting after setup-lab requires its existing .env. "
                                       "Complete setup first; it was not run automatically.")
            baseline = subprocess.run(
                [shutil.which("git"), "show", f"HEAD:{PROMPT.as_posix()}"],
                cwd=self.root, shell=False, check=False, capture_output=True,
            )
            if baseline.returncode:
                raise WalkthroughError("Starting after setup-lab requires its existing Git baseline. "
                                       "Complete setup first; it was not run automatically.")
            # Git stores LF even when the learner's Windows checkout uses CRLF.
            self.approved_prompt = baseline.stdout.replace(b"\r\n", b"\n")
            if b"\r\n" in self.original_prompt:
                self.approved_prompt = self.approved_prompt.replace(b"\n", b"\r\n")
            remaining = self.steps[self.start_step - 1:]
            if any(step.kind in ("copy", "restore", "edit")
                   and (not step.optional or step.optional in self.optional_groups) for step in remaining):
                known = [self.approved_prompt]
                for name in ("concise", "helpful"):
                    path = self.safe_path(f"src/agents/variants/triage.{name}.md")
                    if path.exists():
                        known.append(path.read_bytes())
                if self.original_prompt not in known:
                    raise WalkthroughError("The triage prompt has unrelated edits; refusing to overwrite them. "
                                           "Preserve your work before starting a prompt-changing exercise.")
            variant = False
            for step in self.steps[:self.start_step - 1]:
                if step.optional and step.optional not in self.optional_groups:
                    continue
                if step.kind in ("copy", "edit", "restore"):
                    variant = step.kind != "restore"
                if "create-agents" in step.args:
                    if variant or step.args[-1] == "gpt-5-nano":
                        self.recovery["approved agents"] = (
                            "Restore the approved local prompt, then: uv run create-agents"
                        )
                    else:
                        self.recovery.pop("approved agents", None)
                if "set-gateway" in step.args and step.args[-2] in ("model-routing", "token-limit"):
                    name, value = step.args[-2:]
                    default = "off" if name == "model-routing" else "200000"
                    if value == default:
                        self.recovery.pop(name, None)
                    else:
                        self.recovery[name] = f"uv run set-gateway {name} {default}"
        if not confirm(
            f"Target resource group: {settings['resource_group']}\n"
            "This runs billable evaluations/research/deployment and changes the lab. "
            "Use the learner account and confirm the Skillable APIM policy/roles are fixed. "
            "Existing Evidence Packs and decisions will be retained. "
            + ("Decisions and approval use real human input." if self.interactive_decisions else
               "Decisions and approval use labeled lab-test automation, not human review."), word="RUN"
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

    def decision_snapshot(self, expect):
        paths = (self.safe_path(path) for path in
                 self.safe_path(DECISION_REPORTS[expect]).glob("*/results.json"))
        return {path: (path.stat().st_mtime_ns, path.read_bytes()) for path in paths}

    def golden_choices(self, step):
        golden = {row["alert_id"]: row["expected_disposition"] for row in
                  (json.loads(line) for line in self.safe_path("data/golden.jsonl").read_text(
                      encoding="utf-8").splitlines() if line.strip())}
        requested = [arg for arg in step.args if arg.startswith("ALT-")] or list(golden)
        if not requested or any(alert not in golden for alert in requested):
            raise WalkthroughError("The Decision-1 exercise needs matching golden alerts.")
        return {alert: golden[alert] for alert in requested}

    def check_choices(self, rows, golden, *, nested):
        if (not isinstance(rows, list) or not all(isinstance(row, dict) for row in rows)
                or len(rows) != len(golden) or {row.get("alert_id") for row in rows} != set(golden)):
            raise WalkthroughError("The Decision-1 report does not cover exactly the requested alerts.")
        stats = {"alerts": len(rows), "triage_correct": 0, "reviewer_agrees": 0,
                 "reviewer_disagrees": 0, "unavailable": 0}
        for row in rows:
            triage, review = (row.get("triage"), row.get("review")) if nested else (row, row)
            if (not isinstance(triage, dict) or not isinstance(review, dict)
                    or triage.get("status") != "ok" or review.get("status") != "ok"):
                raise WalkthroughError(f"{row['alert_id']}: unavailable triage or reviewer; exercise incomplete.")
            if (triage.get("disposition") not in ("escalate", "close_false_positive", "request_info")
                    or review.get("verdict") not in ("agree", "disagree")
                    or row.get("expected") != golden[row["alert_id"]]):
                raise WalkthroughError("The Decision-1 report contains an invalid choice, verdict or golden answer.")
            stats["triage_correct"] += triage["disposition"] == row["expected"]
            stats["reviewer_agrees"] += review["verdict"] == "agree"
            stats["reviewer_disagrees"] += review["verdict"] == "disagree"
        return stats

    def check_decision_report(self, step, code, before):
        fresh = [path for path, signature in self.decision_snapshot(step.expect).items()
                 if before.get(path) != signature]
        if len(fresh) != 1:
            raise WalkthroughError("The command did not produce exactly one fresh Decision-1 report.")
        report = checked_json(fresh[0])
        if not isinstance(report, dict):
            raise WalkthroughError("The Decision-1 report must be an object.")
        golden = self.golden_choices(step)
        if step.expect == "choice-review":
            stats = self.check_choices(report.get("rows"), golden, nested=True)
            challenges = report.get("challenges")
            pairs = {(alert, label) for alert, expected in golden.items()
                     for label in ("escalate", "close_false_positive", "request_info") if label != expected}
            required = pairs if "--challenge" in step.args else set()
            if (report.get("gpt_calls") != 0 or not isinstance(challenges, list)
                    or not all(isinstance(item, dict) for item in challenges)
                    or len(challenges) != len(required)
                    or {(item.get("alert_id"), item.get("recommended")) for item in challenges} != required):
                raise WalkthroughError("The Decision-1 report is missing the requested wrong-disposition challenges.")
            caught = 0
            for item in challenges:
                review = item.get("review")
                if (not isinstance(review, dict) or review.get("status") != "ok"
                        or review.get("verdict") not in ("agree", "disagree")
                        or item.get("expected") != golden[item["alert_id"]]):
                    raise WalkthroughError("A Decision-1 challenge reviewer is unavailable or invalid.")
                caught += review["verdict"] == "disagree"
            if challenges:
                stats.update(challenges=len(challenges), challenges_caught=caught, challenges_unavailable=0)
            summary = report.get("summary")
            if not isinstance(summary, dict) or any(summary.get(key) != value for key, value in stats.items()):
                raise WalkthroughError("The Decision-1 summary contradicts its results.")
            clean = stats["triage_correct"] == stats["reviewer_agrees"] == len(golden) and caught == len(challenges)
            if code != (0 if clean else 1):
                raise WalkthroughError("Decision-1 evidence does not match the process exit code.")
        else:
            alerts = report.get("alerts")
            requested = [arg for arg in step.args if arg.startswith("ALT-")]
            if (code != 0 or not isinstance(alerts, list) or not all(isinstance(alert, str) for alert in alerts)
                    or len(alerts) != len(golden) or set(alerts) != set(golden)
                    or requested and alerts != requested or report.get("workers") != 2
                    or not isinstance(report.get("metrics"), dict)):
                raise WalkthroughError("The investigation comparison is incomplete or used different alerts/workers.")
            clean = True
            for name, key, nested in (("Classic (GPT)", "classic", False), ("Decision-1", "decision1", True)):
                run, metrics = report.get(key), report["metrics"].get(name)
                if not isinstance(run, dict) or not isinstance(metrics, dict):
                    raise WalkthroughError(f"The comparison is missing {name} results.")
                stats = self.check_choices(run.get("rows"), golden, nested=nested)
                if any(metrics.get(key) != value for key, value in stats.items()):
                    raise WalkthroughError(f"The {name} comparison metrics contradict its results.")
                clean = clean and stats["triage_correct"] == stats["reviewer_agrees"] == len(golden)
        self.say(f"Evidence: {fresh[0].parent.relative_to(self.root)}")
        return "PASS" if clean else "OBSERVED FAIL"

    def check_explanation(self, step, code, output):
        golden = self.golden_choices(step)
        alert = next(iter(golden))
        if code != 0:
            raise WalkthroughError(f"Decision-1 explanation failed (exit {code}).")
        if step.expect == "choice-json":
            try:
                record, _ = json.JSONDecoder().raw_decode(output.lstrip())
            except ValueError:
                raise WalkthroughError("The command did not print a complete Decision-1 JSON record.") from None
            if not isinstance(record, dict) or record.get("alert_id") != alert:
                raise WalkthroughError("The explanation record does not match the requested alert.")
            assessment, explanation, review = (record.get(key) for key in ("assessment", "explanation", "review"))
            if (not isinstance(explanation, dict) or not explanation.get("reasons")
                    or not isinstance(assessment, dict) or explanation.get("disposition") != assessment.get("disposition")):
                raise WalkthroughError("The Decision-1 explanation is missing or changed the chosen disposition.")
        else:
            choice = re.search(rf"(?m)^{re.escape(alert)}: Decision-1 chooses (\w+)", output)
            verdict = re.search(r"(?m)^Reviewer: (agree|disagree) \(", output)
            if (not choice or not verdict or "\nReasons\n" not in output
                    or "What was called, and what it cost" not in output):
                raise WalkthroughError("The Decision-1 explanation or reviewer is unavailable or incomplete.")
            assessment = {"status": "ok", "disposition": choice[1]}
            review = {"status": "ok", "verdict": verdict[1]}
        stats = self.check_choices([{"alert_id": alert, "expected": golden[alert],
                                    "triage": assessment, "review": review}], golden, nested=True)
        return "PASS" if stats["triage_correct"] == stats["reviewer_agrees"] == 1 else "OBSERVED FAIL"

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
        self.tested_agents = manifest["agents"]
        if expect == "pack-pass" and verdict != "PASS":
            raise WalkthroughError("The required gate failed. Inspect its evidence; the runner will not weaken thresholds.")
        if expect == "pack-fail" and verdict != "FAIL":
            raise WalkthroughError("The teaching outcome was not reproduced: this intentionally weak prompt passed.")
        return "EXPECTED FAIL" if expect == "pack-fail" else "OBSERVED FAIL" if verdict == "FAIL" else "PASS"

    def check_result(self, step, code, output, before):
        if step.expect.startswith("pack-"):
            return self.check_pack(before, code, step.expect, "src/redteam.py" in step.args)
        if step.expect in DECISION_REPORTS:
            return self.check_decision_report(step, code, before)
        if step.expect in ("choice-explain", "choice-json"):
            return self.check_explanation(step, code, output)
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
            decision_check = "A named analyst decided alerts on the published agents"
            checks = ("Release gate passed on the published agents", "Red team passed on the published agents",
                      "Data policy enforced", decision_check)
            if code == 1 and all(check in output for check in checks) and "Not approved. Fix the failures" in output:
                return "EXPECTED FAIL"
        elif step.expect == "policy-deny":
            if code and "RequestDisallowedByPolicy" in output and "policy" in output.lower():
                return "EXPECTED FAIL"
            # A Reader learner is refused by role-based access before policy is evaluated: the guide explains it,
            # but it doesn't prove the policy, so it needs a review rather than counting as the expected denial.
            if code and "AuthorizationFailed" in output:
                self.say("Refused by role-based access before policy was evaluated; policy denial not shown.",
                         "REVIEW NEEDED")
                return "REVIEW NEEDED"
            if code == 0:
                raise WalkthroughError("The unapproved deployment was CREATED. Stop and notify the organizer; no automatic deletion was attempted.")
        elif step.expect.startswith("decision-") and code == 0:
            path = self.safe_path(self.decision_path())
            lines = path.read_text(encoding="utf-8").splitlines()
            if len(lines) == before + 1:
                decision = json.loads(lines[-1])
                approved = decision.get("approved_recommendation")
                valid = ("escalate", "close_false_positive", "request_info")
                mode = "human" if self.interactive_decisions else "lab-test"
                scripted_valid = self.interactive_decisions or (
                    decision.get("analyst") == "lab-test-automation"
                    and approved is (step.expect == "decision-approve")
                    and (approved or decision.get("decision") == "request_info")
                    and bool(decision.get("reason", "").strip())
                )
                if (decision.get("alert") == step.args[-1] and decision.get("analyst")
                        and isinstance(approved, bool)
                        and decision.get("decision") in valid
                        and (not approved or decision.get("decision") == decision.get("recommendation"))
                        and (approved and decision.get("reviewer") == "agree" or decision.get("reason", "").strip())
                        and decision.get("mode", "human") == mode
                        and decision.get("agents")
                        and (self.tested_agents is None or decision["agents"] == self.tested_agents)
                        and scripted_valid):
                    return "PASS"
            raise WalkthroughError("The decision record is missing, invalid, or does not match the selected mode/versions. It was not altered.")
        elif step.expect == "intake-approve" and code == 0:
            path = self.safe_path(self.register_path())
            register = checked_json(path)
            approval = next(item for item in register if item["id"] == "UC-001").get("approval")
            if (path.read_bytes() != before and approval and approval.get("approved_by")
                    and approval.get("agents") and approval.get("evidence")
                    and approval.get("mode", "human") == ("human" if self.interactive_decisions else "lab-test")
                    and (self.interactive_decisions or approval["approved_by"] == "lab-test-automation")):
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

    def decision_path(self):
        return Path("evidence/decisions.jsonl" if self.interactive_decisions else "tests/e2e/artifacts/decisions.jsonl")

    def register_path(self):
        return REGISTER if self.interactive_decisions else Path("tests/e2e/artifacts/use-cases.json")

    def command_arguments(self, step):
        if not self.interactive_decisions:
            if step.expect.startswith("decision-"):
                return ("uv", "run", "tests/e2e/decision_driver.py", step.expect.removeprefix("decision-"), step.args[-1])
            if step.expect.startswith("intake-"):
                return ("uv", "run", "tests/e2e/decision_driver.py", "intake", step.args[-1])
        return step.args

    def command(self, step):
        args = self.resolve(self.command_arguments(step))
        if step.expect == "policy-deny":
            self.say("Testing policy denial with a real GlobalStandard deployment create request. "
                     "Creation alone does not incur token charges.", "NOTE")
        before = None
        if step.expect.startswith("pack-"):
            before = self.pack_snapshot()
        elif step.expect in DECISION_REPORTS:
            before = self.decision_snapshot(step.expect)
        elif step.expect.startswith("decision-"):
            path = self.safe_path(self.decision_path())
            before = len(path.read_text(encoding="utf-8").splitlines()) if path.exists() else 0
        elif step.expect == "intake-approve":
            path = self.safe_path(self.register_path())
            before = path.read_bytes() if path.exists() else b""
        approved = self.approved_prompt if self.approved_prompt is not None else self.original_prompt
        publishing_variant = "create-agents" in args and self.safe_path(PROMPT).read_bytes() != approved
        if "create-agents" in args and (args[-1] == "gpt-5-nano" or publishing_variant):
            self.recovery["approved agents"] = "Restore the approved local prompt, then: uv run create-agents"
        if "set-gateway" in args:
            name, value = args[-2:]
            if name in ("model-routing", "token-limit"):
                default = "off" if name == "model-routing" else "200000"
                self.recovery[name] = f"uv run set-gateway {name} {default}"
        interactive = step.interactive and (not step.expect.startswith("decision-") or self.interactive_decisions)
        code, output = self.process(args, interactive=interactive)
        status = self.check_result(step, code, output, before)
        if "setup-lab" in args:
            code, _ = self.process(["git", "diff", "--quiet", "HEAD", "--",
                                    str(PROMPT), str(DATA_POLICY), str(REGISTER)])
            if code:
                raise WalkthroughError("Git baseline check failed or prompt/governance files have existing edits. "
                                       "Nothing was overwritten.")
        if "create-agents" in args and args[-1] != "gpt-5-nano" and not publishing_variant:
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
            self.set_prompt(self.approved_prompt if self.approved_prompt is not None else self.original_prompt)
        elif step.kind == "edit":
            path = self.safe_path(PROMPT)
            current = path.read_bytes()
            approved = self.approved_prompt if self.approved_prompt is not None else self.original_prompt
            if current != approved:
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
            return shlex.join(self.command_arguments(step))
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
            self.console.heading("PREL35 | " + ("full guided walkthrough" if self.start_step == 1
                                              else "selected-step walkthrough"))
            self.say("LIVE EXECUTION - interactive, billable, changes lab state." if execute
                     else "PLAN ONLY - no commands or changes.", "NOTE" if execute else "PLAN")
            self.say(f"Selected steps: {self.start_step}-{len(self.steps)} (inclusive).", "NOTE")
            if self.start_step > 1:
                self.say("Earlier steps are NOT RUN, not verified. Complete their prerequisites first, "
                         "including sign-in, setup and cloud configuration. Nothing is replayed automatically.", "NOTE")
            self.say("Decision mode: real interactive analyst decisions." if self.interactive_decisions else
                     "Decision mode: LAB TEST automation; decisions and intake are not human approvals.", "NOTE")
            self.say("The guide's setup-lab step initializes Git as the current user. "
                     "Git checks run afterward; plan mode never initializes Git.", "NOTE")
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
                if step.kind == "command" and step.detail:
                    self.say(step.detail, "NOTE")
                if index < self.start_step:
                    detail = f"Before --start-step {self.start_step}; prerequisite completion is not verified."
                    self.outcomes.append(Outcome(index, lab, step, "NOT RUN", detail=detail))
                    self.say(detail, "NOT RUN")
                    continue
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
                if not self.interactive_decisions and (step.expect.startswith("decision-") or step.expect.startswith("intake-")):
                    detail = "LAB TEST automation; not a human decision or use-case approval."
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
            if execute and self.owned_prompt is not None and (not complete or self.recovery):
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
                self.say(f"{len(self.steps) - self.start_step + 1} planned actions; "
                         f"{len(self.guide.blocks)} command/query blocks mapped; "
                         f"{len(self.guide.commands)} terminal commands individually accounted for. "
                         "No subprocesses, prompts, writes, waits or Azure calls were made.", "PLAN")
            elif complete:
                pending = sum(item.status == "REVIEW NEEDED" for item in self.outcomes)
                scope = ("Automated flow finished" if self.start_step == 1 else
                         f"Selected flow finished (steps {self.start_step}-{len(self.steps)}); "
                         "earlier steps were not run or verified; this is not a full-lab completion")
                self.say(f"{scope}; {pending} manual review item(s) remain unverified. "
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
    parser.add_argument("--start-step", type=int, default=1, metavar="N",
                        help="Start at inclusive step N from --plan (1-based; default: 1). "
                             "Earlier prerequisites must already be complete; they are not replayed.")
    parser.add_argument("--optional", action="append", choices=OPTIONAL_GROUPS, default=[],
                        help="Include an optional exercise; repeat to select several. Default: skip all.")
    parser.add_argument("--interactive-decisions", action="store_true",
                        help="Use real analyst prompts instead of scripted lab-test decisions and approval.")
    args = parser.parse_args(argv)
    if not 1 <= args.start_step <= len(build_steps()):
        parser.error(f"--start-step must be between 1 and {len(build_steps())}.")
    try:
        return Runner(resource_group=args.resource_group, optional=args.optional,
                      interactive_decisions=args.interactive_decisions,
                      start_step=args.start_step).run(execute=args.execute)
    except (WalkthroughError, OSError) as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
