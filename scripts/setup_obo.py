"""Create the gateway's On-Behalf-Of app registration and store its short-lived secret in APIM.

Requires a tenant that permits app registrations and consent. Not yet tested end to end.
"""
import argparse
import json
import uuid
from datetime import date, timedelta

from scripts.common import ROOT, SetupError, az_json, cli, command, message, read_settings, write_settings

AZURE_CLI = "04b07795-8ddb-461a-bbee-02f9e1bf7b46"
FOUNDRY_RESOURCE = "18a66f5f-dbdf-4c17-9dd7-1634712a9cbe"
FOUNDRY_SCOPE = "1a7925b5-f871-417a-9b8b-303f9f29fa10"


@cli
def main(argv=None):
    argparse.ArgumentParser(description=__doc__).parse_args(argv)
    path = ROOT / ".env"
    settings = read_settings(path)
    for key in ("AZURE_RESOURCE_GROUP", "APIM_NAME"):
        if not settings.get(key):
            raise SetupError(f"Missing {key}. Run uv run setup-lab first.")
    group = settings["AZURE_RESOURCE_GROUP"]
    name = f"prel35-agents-api-{group}"
    app = az_json("ad", "app", "create", "--display-name", name, "--sign-in-audience", "AzureADMyOrg")
    command(["az", "ad", "sp", "create", "--id", app["appId"], "--output", "none"])
    scope_id = str(uuid.uuid4())
    api = {"oauth2PermissionScopes": [{
        "id": scope_id, "value": "access_as_user", "type": "User", "isEnabled": True,
        "adminConsentDisplayName": "Call the agents as the signed-in user",
        "adminConsentDescription": "Lets the gateway call Foundry agents on behalf of the signed-in user.",
        "userConsentDisplayName": "Call the agents as you",
        "userConsentDescription": "Lets the gateway call Foundry agents on your behalf.",
    }]}
    url = f"https://graph.microsoft.com/v1.0/applications/{app['id']}"
    command([
        "az", "rest", "--method", "patch", "--url", url,
        "--body", json.dumps({"identifierUris": [f"api://{app['appId']}"], "api": api}), "--output", "none",
    ])
    api["preAuthorizedApplications"] = [{"appId": AZURE_CLI, "delegatedPermissionIds": [scope_id]}]
    command(["az", "rest", "--method", "patch", "--url", url, "--body", json.dumps({"api": api}), "--output", "none"])
    command([
        "az", "ad", "app", "permission", "add", "--id", app["appId"], "--api", FOUNDRY_RESOURCE,
        "--api-permissions", f"{FOUNDRY_SCOPE}=Scope", "--output", "none",
    ])
    command([
        "az", "ad", "app", "permission", "grant", "--id", app["appId"],
        "--api", FOUNDRY_RESOURCE, "--scope", "user_impersonation", "--output", "none",
    ])
    secret = command([
        "az", "ad", "app", "credential", "reset", "--id", app["appId"], "--display-name", "lab",
        "--end-date", (date.today() + timedelta(days=2)).isoformat(), "--query", "password", "--output", "tsv",
    ]).stdout.strip()
    if not secret:
        raise SetupError("No app secret was returned. Gateway settings have not been changed.")
    for key, value in (("obo-client-id", app["appId"]), ("obo-client-secret", secret)):
        args = [
            "az", "apim", "nv", "update", "--resource-group", group, "--service-name", settings["APIM_NAME"],
            "--named-value-id", key, "--value", value, "--output", "none",
        ]
        if key == "obo-client-secret":
            args += ["--secret", "true"]
        command(args)
    scope = f"api://{app['appId']}/access_as_user"
    write_settings(path, {"OBO_SCOPE": scope})
    message(f"Created app registration '{name}' ({app['appId']}).", "green")
    message(f"Exposes {scope}; may call Foundry as the signed-in user.")
    message("The secret is stored in the gateway and expires in 2 days.", "yellow")


if __name__ == "__main__":
    raise SystemExit(main())
