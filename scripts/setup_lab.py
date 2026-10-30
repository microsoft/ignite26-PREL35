"""Prepare an existing lab's Search index and knowledge base, without publishing prompt agents."""
import argparse
import shutil
import sys

from scripts import create_search_index, setup_webiq
from scripts.common import ROOT, SetupError, az_json, check_target, cli, message, read_settings, write_settings
from scripts.configure_evidence_agent import configure_evidence


def find_resource(resources, resource_group, resource_type, name="", kind=""):
    matches = [
        resource for resource in resources
        if resource["type"].casefold() == resource_type.casefold()
        and (not name or resource["name"].casefold() == name.casefold())
        and (not kind or resource.get("kind", "").casefold() == kind.casefold())
    ]
    if len(matches) != 1:
        raise SetupError(
            f"Expected one existing {resource_type} resource in {resource_group}; found {len(matches)}. "
            "Use --foundry-name or --search-name if needed. No infrastructure was changed."
        )
    return matches[0]


def discover_settings(resource_group, foundry_name="", search_name=""):
    group = az_json("group", "show", "--name", resource_group)
    resources = az_json("resource", "list", "--resource-group", resource_group)
    foundry = find_resource(resources, resource_group, "Microsoft.CognitiveServices/accounts", foundry_name, "AIServices")
    search = find_resource(resources, resource_group, "Microsoft.Search/searchServices", search_name)
    webiq = find_resource(resources, resource_group, "Microsoft.WebIQ/accounts")
    apim = find_resource(resources, resource_group, "Microsoft.ApiManagement/service")
    insights = find_resource(resources, resource_group, "Microsoft.Insights/components")
    project_id = f"{foundry['id']}/projects/fincrime"
    az_json("resource", "show", "--ids", project_id, "--api-version", "2025-06-01")
    gateway = az_json("resource", "show", "--ids", apim["id"], "--api-version", "2024-05-01")
    monitoring = az_json("resource", "show", "--ids", insights["id"], "--api-version", "2020-02-02")
    models = az_json("cognitiveservices", "account", "deployment", "list",
                     "--resource-group", resource_group, "--name", foundry["name"])
    for model in ("gpt-5-mini", "gpt-5-nano", "text-embedding-3-small"):
        if model not in {deployment["name"].split("/")[-1] for deployment in models}:
            raise SetupError(
                f"Existing model deployment '{model}' is missing. An organizer must provision it first; "
                "this script does not deploy models or update model policy."
            )
    if not gateway.get("properties", {}).get("gatewayUrl") or not monitoring.get("properties", {}).get("ConnectionString"):
        raise SetupError("The existing gateway or monitoring settings could not be read. No settings were changed.")
    return {
        "AZURE_RESOURCE_GROUP": group["name"],
        "FOUNDRY_NAME": foundry["name"],
        "FOUNDRY_PROJECT_NAME": "fincrime",
        "FOUNDRY_PROJECT_ENDPOINT": f"https://{foundry['name']}.services.ai.azure.com/api/projects/fincrime",
        "MODEL_DEPLOYMENT": "gpt-5-mini",
        "APIM_NAME": apim["name"],
        "APIM_GATEWAY_URL": gateway["properties"]["gatewayUrl"],
        "APPLICATIONINSIGHTS_NAME": insights["name"],
        "APPLICATIONINSIGHTS_CONNECTION_STRING": monitoring["properties"]["ConnectionString"],
        "AZURE_SEARCH_SERVICE_NAME": search["name"],
        "AZURE_SEARCH_ENDPOINT": f"https://{search['name']}.search.windows.net",
        "AZURE_SEARCH_LOCATION": search["location"],
        "AZURE_SEARCH_INDEX_NAME": "fincrime-schemes",
        "AZURE_SEARCH_CONNECTION_NAME": "investigator-search",
        "AZURE_SEARCH_CONNECTION_ID": f"{project_id}/connections/investigator-search",
        "AZURE_SEARCH_KNOWLEDGE_BASE_NAME": "fincrime-research",
        "INVESTIGATOR_PLANNING_DEPLOYMENT": "gpt-5-mini",
        "INVESTIGATOR_EMBEDDING_ENDPOINT": f"https://{foundry['name']}.openai.azure.com",
        "INVESTIGATOR_EMBEDDING_DEPLOYMENT": "text-embedding-3-small",
        "INVESTIGATOR_EMBEDDING_MODEL": "text-embedding-3-small",
        "INVESTIGATOR_EMBEDDING_DIMENSIONS": "1536",
        "WEBIQ_RESOURCE_ID": webiq["id"],
        "WEBIQ_ACCOUNT_NAME": webiq["name"],
    }


def setup_search(settings):
    required = (
        "AZURE_SEARCH_ENDPOINT", "AZURE_SEARCH_INDEX_NAME", "INVESTIGATOR_EMBEDDING_ENDPOINT",
        "INVESTIGATOR_EMBEDDING_DEPLOYMENT", "AZURE_SEARCH_KNOWLEDGE_BASE_NAME", "INVESTIGATOR_PLANNING_DEPLOYMENT",
    )
    for key in required:
        if not settings.get(key):
            raise SetupError(f"Missing {key}. Provision the lab before setting up the index and knowledge base.")
    documents = ROOT / "data/investigator/schemes.json"
    message(f"Creating and seeding the index from {documents}, then setting up the knowledge base.")
    try:
        create_search_index.main([
            "--data", str(documents),
            "--endpoint", settings["AZURE_SEARCH_ENDPOINT"],
            "--index", settings["AZURE_SEARCH_INDEX_NAME"],
            "--embedding-endpoint", settings["INVESTIGATOR_EMBEDDING_ENDPOINT"],
            "--embedding-deployment", settings["INVESTIGATOR_EMBEDDING_DEPLOYMENT"],
            "--knowledge-base", settings["AZURE_SEARCH_KNOWLEDGE_BASE_NAME"],
            "--planning-deployment", settings["INVESTIGATOR_PLANNING_DEPLOYMENT"],
        ])
    except Exception as error:
        raise SetupError(
            "Data-plane setup failed. This script cannot grant missing Foundry, Search or embedding permissions. "
            "Resolve the error before rerunning."
        ) from error


def offer_webiq():
    if not sys.stdin.isatty():
        message("Web IQ needs interactive setup. Run uv run setup-webiq in a terminal.", "yellow")
        return
    message("Web IQ adds public-web research and can incur Web IQ, model and Search charges.", "yellow")
    answer = input("Set up Web IQ now? [y/N]: ").strip().lower()
    if answer in ("y", "yes"):
        if setup_webiq.main(["--if-needed"]):
            raise SetupError("Web IQ setup did not finish. Rerun uv run setup-webiq.")
    else:
        message("Web IQ setup skipped. Run uv run setup-webiq when you have its API key.", "yellow")


@cli
def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--resource-group", required=True)
    parser.add_argument("--foundry-name", default="")
    parser.add_argument("--search-name", default="")
    parser.add_argument("--environment-name", default="")
    args = parser.parse_args(argv)
    message("Reading the existing lab resources.")
    settings = discover_settings(args.resource_group, args.foundry_name, args.search_name)
    path = ROOT / ".env"
    check_target(
        read_settings(path), settings,
        ("AZURE_RESOURCE_GROUP", "FOUNDRY_PROJECT_ENDPOINT", "WEBIQ_RESOURCE_ID"), ".env",
    )
    write_settings(path, settings)
    message("Saved lab settings; preserved custom .env entries without printing their values.", "green")
    if shutil.which("azd"):
        configure_evidence(args.resource_group, args.environment_name, settings=settings)
    else:
        message("azd is not installed. Install it and rerun setup before deploying the hosted evidence agent.", "yellow")
    setup_search(settings)
    offer_webiq()
    message("Lab setup finished. No infrastructure, roles, prompt agents or gateway settings were changed.", "green")
    message("Next, run azd deploy to create the connection, Toolbox and hosted evidence agent.")


if __name__ == "__main__":
    raise SystemExit(main())
