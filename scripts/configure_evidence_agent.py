"""Bind azd to an existing lab without assigning hosted-agent roles."""
import argparse
import json
import re

from scripts.common import az_json, check_target, cli, command, message, SetupError
from scripts.env import deployment_settings

REQUIRED = (
    "FOUNDRY_PROJECT_ENDPOINT", "FOUNDRY_NAME", "FOUNDRY_PROJECT_NAME",
    "INVESTIGATOR_PLANNING_DEPLOYMENT", "AZURE_SEARCH_ENDPOINT", "AZURE_SEARCH_SERVICE_NAME",
    "AZURE_SEARCH_KNOWLEDGE_BASE_NAME",
)


def configure_evidence(resource_group, environment_name="", *, settings=None, validate_only=False):
    settings = deployment_settings(resource_group) if settings is None else settings
    for key in REQUIRED:
        if not settings.get(key):
            raise SetupError(f"Missing {key} in the lab deployment outputs.")
    group = az_json("group", "show", "--name", resource_group)
    if not group.get("id"):
        raise SetupError("Could not read the lab resource group.")
    subscription = group["id"].split("/")[2]
    project_id = (
        f"{group['id']}/providers/Microsoft.CognitiveServices/accounts/{settings['FOUNDRY_NAME']}"
        f"/projects/{settings['FOUNDRY_PROJECT_NAME']}"
    )
    expected = {
        "AZURE_SUBSCRIPTION_ID": subscription,
        "AZURE_RESOURCE_GROUP": resource_group,
        "AZURE_LOCATION": group["location"],
        "AZURE_AI_PROJECT_ID": project_id,
        "FOUNDRY_PROJECT_ENDPOINT": settings["FOUNDRY_PROJECT_ENDPOINT"],
        "AZURE_AI_MODEL_DEPLOYMENT_NAME": settings["INVESTIGATOR_PLANNING_DEPLOYMENT"],
        "AZURE_SEARCH_SERVICE_NAME": settings["AZURE_SEARCH_SERVICE_NAME"],
        "AZURE_SEARCH_ENDPOINT": settings["AZURE_SEARCH_ENDPOINT"].rstrip("/"),
        "AZURE_SEARCH_KNOWLEDGE_BASE_NAME": settings["AZURE_SEARCH_KNOWLEDGE_BASE_NAME"],
        "AZD_AGENT_SKIP_ROLE_ASSIGNMENTS": "true",
    }
    environment_name = environment_name or re.sub("[^a-z0-9-]", "-", resource_group.lower())
    environments = json.loads(command(["azd", "env", "list", "--output", "json"]).stdout)
    exists = any(env["Name"] == environment_name for env in environments)
    if not exists:
        if validate_only:
            raise SetupError("The lab azd environment is missing. Run uv run lab-env first.")
        command([
            "azd", "env", "new", environment_name, "--subscription", subscription,
            "--location", group["location"], "--no-prompt",
        ])
    current = {}
    for key in expected:
        result = command(
            ["azd", "env", "get-value", key, "--environment", environment_name], allow_failure=True,
        )
        current[key] = result.stdout.strip() if result.returncode == 0 else ""
    check_target(
        current, expected,
        ("AZURE_SUBSCRIPTION_ID", "AZURE_RESOURCE_GROUP", "AZURE_AI_PROJECT_ID", "FOUNDRY_PROJECT_ENDPOINT"),
        f"The azd environment '{environment_name}'",
    )
    for key, value in expected.items():
        if current[key] == value:
            continue
        if validate_only:
            raise SetupError(f"The azd setting {key} does not match the lab. Run uv run lab-env first.")
        command(["azd", "env", "set", key, value, "--environment", environment_name])
    if not validate_only:
        command(["azd", "env", "select", environment_name])
        message(f"Evidence agent deployment is configured for '{environment_name}'. Run azd deploy.", "green")


@cli
def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--resource-group", required=True)
    parser.add_argument("--environment-name", default="")
    parser.add_argument("--validate-only", action="store_true")
    args = parser.parse_args(argv)
    configure_evidence(args.resource_group, args.environment_name, validate_only=args.validate_only)


if __name__ == "__main__":
    raise SystemExit(main())
