"""Create or update the Foundry prompt agents from src/agents/<name>.md.

Each run adds a new version of each agent.

Usage: uv run create-agents [MODEL_DEPLOYMENT]
"""
import argparse
import sys
import time
from pathlib import Path

from azure.ai.projects import AIProjectClient
from azure.ai.projects.models import PromptAgentDefinition, Reasoning
from azure.core.exceptions import HttpResponseError
from azure.identity import DefaultAzureCredential

ROOT = Path(__file__).resolve().parent.parent
if __package__ in (None, ""):
    sys.path.insert(0, str(ROOT))
from scripts.console_output import color


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("model", nargs="?", help="Override MODEL_DEPLOYMENT from .env.")
    args = parser.parse_args(argv)
    env = dict(
        line.split("=", 1) for line in (ROOT / ".env").read_text(encoding="utf-8-sig").splitlines() if "=" in line
    )
    project = AIProjectClient(endpoint=env["FOUNDRY_PROJECT_ENDPOINT"], credential=DefaultAzureCredential())
    model = args.model or env["MODEL_DEPLOYMENT"]
    agents_path = ROOT / "src" / "agents"
    # Lab 4 routes low-risk alerts to triage-lite on the smaller model.
    agents = [(path.stem.replace("_", "-"), path, model) for path in sorted(agents_path.glob("*.md"))]
    agents.append(("triage-lite", agents_path / "triage.md", "gpt-5-nano"))

    for name, path, agent_model in agents:
        # Low reasoning effort keeps calls short; the release gate checks accuracy.
        definition = PromptAgentDefinition(
            model=agent_model, instructions=path.read_text(encoding="utf-8"), reasoning=Reasoning(effort="low"),
            text={"format": {"type": "json_object"}},
        )
        # New role assignments can take a few minutes to reach the Foundry data plane.
        for attempt in range(10):
            try:
                agent = project.agents.create_version(agent_name=name, definition=definition)
                break
            except HttpResponseError as e:
                if e.status_code != 403 or attempt == 9:
                    raise
                print(color(f"Waiting for role assignment to apply ({e.status_code})...", "yellow", stream=sys.stderr),
                      file=sys.stderr)
                time.sleep(30)
        print(f"{color(agent.name, 'green')} version {agent.version} ({color(agent_model, 'cyan')})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
