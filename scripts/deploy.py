"""Provision the complete lab with azd, or repair only its existing Search data-plane objects."""
import argparse
import json
import logging
import re
from datetime import datetime
from pathlib import Path

from scripts.common import ROOT, LOGGER, SetupError, az_json, check_target, cli, command, message
from scripts.env import deployment_settings
from scripts.setup_lab import setup_search


def provision(resource_group, learner_principal_id="", investigator_search_location="",
              operator_principal_id="", environment_name=""):
    group = az_json("group", "show", "--name", resource_group)
    subscription = group["id"].split("/")[2]
    operator = operator_principal_id.strip()
    if not operator:
        account = az_json("account", "show")
        user = account["user"]
        if user["type"] == "user":
            identity = az_json("ad", "signed-in-user", "show")
        elif user["type"] == "servicePrincipal":
            identity = az_json("ad", "sp", "show", "--id", user["name"])
        else:
            raise SetupError("Supply --operator-principal-id with the operator object ID.")
        operator = identity.get("id", "")
    if not operator:
        raise SetupError("Could not find the operator object ID. Supply --operator-principal-id.")
    environment_name = environment_name or re.sub("[^a-z0-9-]", "-", resource_group.lower())
    environments = json.loads(command(["azd", "env", "list", "--output", "json"]).stdout)
    if not any(env["Name"] == environment_name for env in environments):
        command([
            "azd", "env", "new", environment_name, "--subscription", subscription,
            "--location", group["location"], "--no-prompt",
        ])
    settings = {
        "AZURE_SUBSCRIPTION_ID": subscription, "AZURE_RESOURCE_GROUP": resource_group,
        "AZURE_LOCATION": group["location"], "AZURE_PRINCIPAL_ID": operator,
        "LEARNER_PRINCIPAL_ID": learner_principal_id,
    }
    current = {}
    for key in ("AZURE_SUBSCRIPTION_ID", "AZURE_RESOURCE_GROUP"):
        result = command(["azd", "env", "get-value", key, "--environment", environment_name], allow_failure=True)
        current[key] = result.stdout.strip() if result.returncode == 0 else ""
    check_target(current, settings, current, f"The azd environment '{environment_name}'")
    if investigator_search_location:
        settings["INVESTIGATOR_SEARCH_LOCATION"] = investigator_search_location
    for key, value in settings.items():
        command(["azd", "env", "set", key, value, "--environment", environment_name])
    command(["azd", "env", "select", environment_name])
    message("Deploying infrastructure and role assignments with azd; its hook will publish prompts and set up Search.")
    command(["azd", "provision", "--environment", environment_name, "--no-prompt"], show=True)


@cli
def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--resource-group", required=True)
    parser.add_argument("--learner-principal-id", default="")
    parser.add_argument("--investigator-search-location", default="")
    parser.add_argument("--operator-principal-id", default="")
    parser.add_argument("--environment-name", default="")
    parser.add_argument("--index-only", action="store_true", help="Repair Search/KB without provisioning or writing settings.")
    parser.add_argument("--log-path", type=Path)
    args = parser.parse_args(argv)
    log_path = args.log_path or ROOT / "logs" / f"deploy-{datetime.now():%Y%m%d-%H%M%S-%f}.log"
    log_path = log_path.resolve()
    log_path.parent.mkdir(parents=True, exist_ok=True)
    handler = logging.FileHandler(log_path, encoding="utf-8")
    handler.setFormatter(logging.Formatter("[%(asctime)s] %(message)s"))
    previous_level = LOGGER.level
    LOGGER.setLevel(logging.INFO)
    LOGGER.addHandler(handler)
    try:
        message(f"Starting deployment for resource group '{args.resource_group}' (IndexOnly={args.index_only}).")
        message(f"Log file: {log_path}")
        if args.index_only:
            message("IndexOnly: skipping infrastructure deployment, role assignments, environment writes and prompt agents.")
            setup_search(deployment_settings(args.resource_group))
        else:
            provision(
                args.resource_group, args.learner_principal_id, args.investigator_search_location,
                args.operator_principal_id, args.environment_name,
            )
        message("Deployment completed successfully.", "green")
    except Exception as error:
        if isinstance(error, SetupError):
            LOGGER.error(str(error))
        message(f"Deployment failed. See log file: {log_path}", "red", error=True)
        raise
    finally:
        LOGGER.removeHandler(handler)
        LOGGER.setLevel(previous_level)
        handler.close()


if __name__ == "__main__":
    raise SystemExit(main())
