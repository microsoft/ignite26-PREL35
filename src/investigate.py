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
cases = {c["alert"]["id"]: c for c in data["cases"]}

# Your own Entra ID token: the gateway checks it, then calls Foundry with its managed identity.
credential = DefaultAzureCredential()


def ask(agent: str, text: str):
    # After scripts/setup_obo.ps1 (Lab 2), the token is for the gateway's own API instead of for Foundry.
    token = credential.get_token(env.get("OBO_SCOPE", "https://ai.azure.com/.default")).token
    # The gateway limits calls per minute; retries wait for the limit to reset.
    client = OpenAI(base_url=f"{env['APIM_GATEWAY_URL']}/agents/{agent}", api_key=token, max_retries=8)
    return client.responses.create(input=text)


def investigate(alert_id: str) -> dict:
    """Returns the triage and reviewer answers as text, plus the tokens the two calls used."""
    case_file = json.dumps({**cases[alert_id], "screening_lists": data["screening_lists"]})
    triage = ask("triage", case_file)
    review = ask("reviewer", f"Case file:\n{case_file}\n\nTriage recommendation:\n{triage.output_text}")
    return {
        "triage": triage.output_text,
        "review": review.output_text,
        "tokens": triage.usage.total_tokens + review.usage.total_tokens,
    }


if __name__ == "__main__":
    alert_id = sys.argv[1] if len(sys.argv) > 1 else "ALT-1001"
    if alert_id not in cases:
        sys.exit(f"Unknown alert {alert_id}. Alerts: {', '.join(cases)}")
    case = cases[alert_id]
    print(f"{alert_id}: {case['alert']['title']} ({case['customer']['name']})\n")
    result = investigate(alert_id)
    print(f"Triage recommendation:\n{result['triage']}\n")
    print(f"Reviewer:\n{result['review']}\n")
    print("The agents recommend. A named analyst decides.")
