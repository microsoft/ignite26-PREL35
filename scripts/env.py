"""Read deployment outputs and refresh the local lab environment."""
import argparse
import shutil

from scripts.common import ROOT, SetupError, az_json, cli, message, write_settings


def deployment_settings(resource_group):
    output_sets = az_json(
        "deployment", "group", "list", "--resource-group", resource_group, "--query",
        "sort_by([?properties.provisioningState=='Succeeded' && properties.outputs != null], "
        "&properties.timestamp)[].properties.outputs",
    )
    normalized = [
        {name.upper(): str(output["value"]) for name, output in outputs.items() if output.get("value") is not None}
        for outputs in output_sets
    ]
    baseline = next((outputs for outputs in reversed(normalized)
                     if outputs.get("FOUNDRY_PROJECT_ENDPOINT") and outputs.get("APIM_GATEWAY_URL")), None)
    if baseline is None:
        raise SetupError(f"No lab deployment found in resource group {resource_group}.")
    research = next((outputs for outputs in reversed(normalized)
                     if outputs.get("AZURE_SEARCH_ENDPOINT") and outputs.get("WEBIQ_RESOURCE_ID")), {})
    return {**baseline, **research, "AZURE_RESOURCE_GROUP": resource_group}


def refresh_environment(resource_group, environment_name="", *, configure=True, settings=None):
    settings = deployment_settings(resource_group) if settings is None else settings
    path = ROOT / ".env"
    write_settings(path, settings)
    # Print only deployment outputs, never preserved custom settings or credentials.
    visible = {key: value for key, value in settings.items() if not key.startswith("APPLICATIONINSIGHTS")}
    width = max(len("Setting"), *(len(key) for key in visible))
    message(f"{'Setting':<{width}}  Value")
    for key, value in visible.items():
        print(f"{key:<{width}}  {value}")
    message(f"Wrote {path}", "green")
    if configure and shutil.which("azd"):
        from scripts.configure_evidence_agent import configure_evidence
        configure_evidence(resource_group, environment_name, settings=settings)
    return settings


@cli
def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--resource-group", required=True)
    parser.add_argument("--environment-name", default="")
    args = parser.parse_args(argv)
    refresh_environment(args.resource_group, args.environment_name)


if __name__ == "__main__":
    raise SystemExit(main())
