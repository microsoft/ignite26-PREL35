"""Python implementations of the azd lifecycle hooks."""
import argparse
import os

from scripts.common import SetupError, cli, command
from scripts.env import refresh_environment
from scripts.setup_lab import setup_search

OUTPUT_NAMES = (
    "FOUNDRY_PROJECT_ENDPOINT", "MODEL_DEPLOYMENT", "APIM_GATEWAY_URL", "APIM_NAME",
    "AZURE_RESOURCE_GROUP", "APPLICATIONINSIGHTS_CONNECTION_STRING", "APPLICATIONINSIGHTS_NAME",
    "FOUNDRY_NAME", "FOUNDRY_PROJECT_NAME", "AZURE_SEARCH_SERVICE_NAME", "AZURE_SEARCH_ENDPOINT",
    "AZURE_SEARCH_LOCATION", "AZURE_SEARCH_INDEX_NAME", "AZURE_SEARCH_CONNECTION_NAME",
    "AZURE_SEARCH_CONNECTION_ID", "AZURE_SEARCH_KNOWLEDGE_BASE_NAME",
    "INVESTIGATOR_PLANNING_DEPLOYMENT", "INVESTIGATOR_EMBEDDING_ENDPOINT",
    "INVESTIGATOR_EMBEDDING_DEPLOYMENT", "INVESTIGATOR_EMBEDDING_MODEL", "INVESTIGATOR_EMBEDDING_DIMENSIONS",
    "WEBIQ_RESOURCE_ID", "WEBIQ_ACCOUNT_NAME",
)


def postprovision(resource_group, environment_name):
    settings = {}
    for name in OUTPUT_NAMES:
        args = ["azd", "env", "get-value", name]
        if environment_name:
            args += ["--environment", environment_name]
        result = command(args)
        if not result.stdout.strip():
            raise SetupError(f"Could not read azd environment value {name}.")
        settings[name] = result.stdout.strip()
    if settings["AZURE_RESOURCE_GROUP"].casefold() != resource_group.casefold():
        raise SetupError("Deployment outputs target a different resource group. No settings were changed.")
    refresh_environment(resource_group, environment_name, settings=settings)
    command(["uv", "run", "create-agents"], show=True)
    setup_search(settings)


@cli
def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("phase", choices=("postprovision",))
    parser.parse_args(argv)
    resource_group = os.environ.get("AZURE_RESOURCE_GROUP", "")
    if not resource_group:
        raise SetupError("AZURE_RESOURCE_GROUP is required. Select the lab's azd environment first.")
    environment_name = os.environ.get("AZURE_ENV_NAME", "")
    postprovision(resource_group, environment_name)


if __name__ == "__main__":
    raise SystemExit(main())
