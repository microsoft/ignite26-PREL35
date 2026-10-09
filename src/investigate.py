"""Run one alert through the triage and reviewer agents, through the API Management gateway.

Usage: python src/investigate.py [ALERT_ID]
"""
import json
import sys
from pathlib import Path

from azure.identity import DefaultAzureCredential
from openai import OpenAI

ROOT = Path(__file__).resolve().parent.parent
if not (ROOT / ".env").exists():
    sys.exit("No .env yet. Run: .\\scripts\\env.ps1 -ResourceGroup <your-resource-group>")
env = dict(line.split("=", 1) for line in (ROOT / ".env").read_text(encoding="utf-8-sig").splitlines() if "=" in line)
data = json.loads((ROOT / "data" / "cases.json").read_text(encoding="utf-8"))

alert_id = sys.argv[1] if len(sys.argv) > 1 else "ALT-1001"
case = next((c for c in data["cases"] if c["alert"]["id"] == alert_id), None)
if case is None:
    sys.exit(f"Unknown alert {alert_id}. Alerts: {', '.join(c['alert']['id'] for c in data['cases'])}")
case_file = json.dumps({**case, "screening_lists": data["screening_lists"]})

# Your own Entra ID token: the gateway checks it, then calls Foundry with its managed identity.
token = DefaultAzureCredential().get_token("https://ai.azure.com/.default").token


def ask(agent: str, text: str) -> str:
    client = OpenAI(base_url=f"{env['APIM_GATEWAY_URL']}/agents/{agent}", api_key=token)
    return client.responses.create(input=text).output_text


print(f"{alert_id}: {case['alert']['title']} ({case['customer']['name']})\n")
triage = ask("triage", case_file)
print(f"Triage recommendation:\n{triage}\n")
review = ask("reviewer", f"Case file:\n{case_file}\n\nTriage recommendation:\n{triage}")
print(f"Reviewer:\n{review}\n")
print("The agents recommend. A named analyst decides.")
