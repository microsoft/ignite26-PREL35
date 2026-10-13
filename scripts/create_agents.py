"""Create or update the Foundry prompt agents from src/agents/<name>.md.

Each run adds a new version of each agent.

Usage: python scripts/create_agents.py [MODEL_DEPLOYMENT]
"""
import sys
import time
from pathlib import Path

from azure.ai.projects import AIProjectClient
from azure.ai.projects.models import PromptAgentDefinition, Reasoning
from azure.core.exceptions import HttpResponseError
from azure.identity import DefaultAzureCredential

ROOT = Path(__file__).resolve().parent.parent
env = dict(line.split("=", 1) for line in (ROOT / ".env").read_text(encoding="utf-8-sig").splitlines() if "=" in line)

project = AIProjectClient(endpoint=env["FOUNDRY_PROJECT_ENDPOINT"], credential=DefaultAzureCredential())
model = sys.argv[1] if len(sys.argv) > 1 else env["MODEL_DEPLOYMENT"]
AGENTS = ROOT / "src" / "agents"

for path in sorted(AGENTS.glob("*.md")):
    # Low reasoning effort keeps each call to a few seconds; the release gate checks it's still accurate enough.
    definition = PromptAgentDefinition(
        model=model, instructions=path.read_text(encoding="utf-8"), reasoning=Reasoning(effort="low")
    )
    # New role assignments take a few minutes to reach the Foundry data plane, so the first calls can get 403.
    for attempt in range(10):
        try:
            agent = project.agents.create_version(agent_name=path.stem, definition=definition)
            break
        except HttpResponseError as e:
            if e.status_code != 403 or attempt == 9:
                raise
            print(f"Waiting for role assignment to apply ({e.status_code})...", file=sys.stderr)
            time.sleep(30)
    print(f"{agent.name} version {agent.version} ({model})")
