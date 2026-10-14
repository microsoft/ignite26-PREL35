r"""Run one alert through the triage and reviewer agents, through the API Management gateway.

Usage: python .\src\lab0\python\investigate.py [ALERT_ID]
"""
import json
import sys
from pathlib import Path

from azure.identity import ClientSecretCredential
from openai import OpenAI

ROOT = Path(__file__).resolve().parents[3]
if not (ROOT / ".env").exists():
    sys.exit("No .env yet. Run: .\\scripts\\env.ps1 -ResourceGroup <your-resource-group>")
env = dict(line.split("=", 1) for line in (ROOT / ".env").read_text(encoding="utf-8-sig").splitlines() if "=" in line)
required = ("APIM_GATEWAY_URL", "ENTRA_TENANT_ID",
            "LAB_CLIENT_ID", "LAB_CLIENT_SECRET", "APIM_API_AUDIENCE")
missing = [name for name in required if not env.get(name, "").strip() or "<" in env[name]]
if missing:
    sys.exit(f"Lab 0 setup is incomplete: {', '.join(missing)}. "
             "Run scripts\\env.ps1 for deployment outputs; ask the deployment owner to provision "
             "the gateway client with scripts\\setup-entra-apps.ps1 on this VM.")
data = json.loads((ROOT / "data" / "cases.json").read_text(encoding="utf-8"))

alert_id = sys.argv[1] if len(sys.argv) > 1 else "ALT-1001"
case = next((c for c in data["cases"] if c["alert"]["id"] == alert_id), None)
if case is None:
    sys.exit(f"Unknown alert {alert_id}. Alerts: {', '.join(c['alert']['id'] for c in data['cases'])}")
case_file = json.dumps({**case, "screening_lists": data["screening_lists"]})

# Authenticate as the confidential Lab 0 service client.
credential = ClientSecretCredential(
    tenant_id=env["ENTRA_TENANT_ID"],
    client_id=env["LAB_CLIENT_ID"],
    client_secret=env["LAB_CLIENT_SECRET"],
)


def ask(agent: str, text: str) -> str:
    token = credential.get_token(f"{env['APIM_API_AUDIENCE']}/.default").token
    with OpenAI(base_url=f"{env['APIM_GATEWAY_URL'].rstrip('/')}/agents/{agent}", api_key=token) as client:
        output = client.responses.create(input=text).output_text
    if not output.strip():
        sys.exit(f"Agent {agent} returned no text. No recommendation is available; try the investigation again.")
    return output


print(f"{alert_id}: {case['alert']['title']} ({case['customer']['name']})\n")
triage = ask("triage", case_file)
print(f"Triage recommendation:\n{triage}\n")
review = ask("reviewer", f"Case file:\n{case_file}\n\nTriage recommendation:\n{triage}")
print(f"Reviewer:\n{review}\n")
print("The agents recommend. A named analyst decides.")
