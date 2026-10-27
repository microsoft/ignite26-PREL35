"""Run one alert through the triage and reviewer agents, through the API Management gateway.

Usage: python src/investigate.py [ALERT_ID] [--debug]
  --debug  show each agent's full API response on stderr (may contain sensitive case data)
"""
import argparse
import copy
import hashlib
import json
import sys
from pathlib import Path

from azure.identity import DefaultAzureCredential
from openai import OpenAI

from console_output import color

ROOT = Path(__file__).resolve().parent.parent
if not (ROOT / ".env").exists():
    sys.exit(color("No .env yet. Run: .\\scripts\\env.ps1 -ResourceGroup <your-resource-group>",
                   "red", stream=sys.stderr))
env = dict(line.split("=", 1) for line in (ROOT / ".env").read_text(encoding="utf-8-sig").splitlines() if "=" in line)
data = json.loads((ROOT / "data" / "cases.json").read_text(encoding="utf-8"))
cases = {c["alert"]["id"]: c for c in data["cases"]}
DATA_POLICY = ROOT / "governance" / "data-policy.json"


def data_policy() -> dict:
    return json.loads(DATA_POLICY.read_text(encoding="utf-8"))


def policy_hash() -> str:
    """Evidence records the data policy it was produced under, so a policy change invalidates it."""
    return hashlib.sha256(DATA_POLICY.read_bytes()).hexdigest()


def case_file(case: dict, agent: str) -> str:
    """What an agent receives: the case and the screening lists, minus the fields the data policy withholds from it."""
    content = copy.deepcopy({**case, "screening_lists": data["screening_lists"]})
    policy = data_policy()
    for path, rule in policy["fields"].items() if policy["enforce"] else []:
        if agent in rule["agents"]:
            continue
        *parents, key = path.split(".")
        node = content
        for parent in parents:
            node = node.get(parent) or {}
        if key in node:
            node[key] = f"[withheld: {rule['class']}]"
    return json.dumps(content)

# Your own Entra ID token: the gateway checks it, then calls Foundry with its managed identity.
credential = DefaultAzureCredential()
PRICES = json.loads((ROOT / "evals" / "prices.json").read_text(encoding="utf-8"))["models"]
# Who pays for the tokens (Lab 4): the gateway attributes every call to this cost center. Scripts that do other
# work, such as the release gate, set their own.
COST_CENTER = "financial-crime-operations"


def ask(agent: str, text: str):
    # After scripts/setup_obo.ps1 (Lab 2), the token is for the gateway's own API instead of for Foundry.
    token = credential.get_token(env.get("OBO_SCOPE", "https://ai.azure.com/.default")).token
    # The gateway limits calls per minute; retries wait for the limit to reset.
    client = OpenAI(base_url=f"{env['APIM_GATEWAY_URL']}/agents/{agent}", api_key=token, max_retries=8,
                    default_headers={"x-cost-center": COST_CENTER})
    return client.responses.create(input=text)


def cost(response) -> float:
    """What one call cost, in USD, from its token use and the model that answered it.

    Input tokens the model has seen recently (the same instructions, the same screening lists) are cached, and billed
    at a tenth of the price.
    """
    price = next(p for name, p in sorted(PRICES.items(), key=lambda kv: -len(kv[0])) if response.model.startswith(name))
    usage = response.usage
    cached = usage.input_tokens_details.cached_tokens if usage.input_tokens_details else 0
    return ((usage.input_tokens - cached) * price["input"] + cached * price["cached_input"]
            + usage.output_tokens * price["output"]) / 1_000_000


def investigate(alert_id: str, *, debug: bool = False) -> dict:
    """Returns the triage and reviewer answers as text, plus the tokens, models and cost of the two calls."""
    triage = ask("triage", case_file(cases[alert_id], "triage"))
    if debug:
        print(f"{color('Triage response:', 'yellow', stream=sys.stderr)}\n{triage.model_dump_json(indent=2)}\n",
              file=sys.stderr, flush=True)
    review = ask("reviewer", f"Case file:\n{case_file(cases[alert_id], 'reviewer')}\n\nTriage recommendation:\n{triage.output_text}")
    if debug:
        print(f"{color('Reviewer response:', 'yellow', stream=sys.stderr)}\n{review.model_dump_json(indent=2)}\n",
              file=sys.stderr, flush=True)
    return {
        "triage": triage.output_text,
        "review": review.output_text,
        "tokens": triage.usage.total_tokens + review.usage.total_tokens,
        "triage_model": triage.model,
        "cost": cost(triage) + cost(review),
    }


def format_answer(text: str) -> str:
    """Indent JSON answers without changing non-JSON responses."""
    try:
        return json.dumps(json.loads(text), indent=2, ensure_ascii=False)
    except json.JSONDecodeError:
        return text


# Self-check of the pricing, with no calls to Azure.
class _Usage:
    input_tokens, output_tokens = 2_000_000, 1_000_000
    input_tokens_details = type("D", (), {"cached_tokens": 1_000_000})


assert round(cost(type("R", (), {"model": "gpt-5-nano-2025-08-07", "usage": _Usage})), 6) == 0.455
assert round(cost(type("R", (), {"model": "gpt-5-mini-2025-08-07", "usage": _Usage})), 6) == 2.275


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("alert_id", nargs="?", default="ALT-1001", help="alert to investigate (default: %(default)s)")
    parser.add_argument("--debug", action="store_true",
                        help="show full agent API responses on stderr (may contain sensitive case data)")
    args = parser.parse_args()
    alert_id = args.alert_id
    if alert_id not in cases:
        sys.exit(color(f"Unknown alert {alert_id}. Alerts: {', '.join(cases)}", "red", stream=sys.stderr))
    case = cases[alert_id]
    print(color(f"{alert_id}: {case['alert']['title']} ({case['customer']['name']})", "cyan") + "\n")
    result = investigate(alert_id, debug=args.debug)
    print(f"{color('Triage recommendation:', 'cyan')}\n{format_answer(result['triage'])}\n")
    print(f"{color('Reviewer:', 'cyan')}\n{format_answer(result['review'])}\n")
    print("The agents recommend. A named analyst decides.")
