"""Guide an organizer through Web IQ key creation and configure both independent MCP integrations."""

import argparse
import getpass
import json
import os
import re
import subprocess
import sys
import tempfile
import urllib.error
import urllib.parse
import urllib.request
import warnings
import webbrowser
from pathlib import Path

from azure.identity import AzureCliCredential

ROOT = Path(__file__).resolve().parent.parent
PORTAL = "https://webiq.microsoft.ai/profiles"
MCP_ENDPOINT = "https://api.microsoft.ai/v3/mcp"
CONNECTION_NAME = "investigator-webiq"
SEARCH_API_VERSION = "2026-08-01-preview"
ARM_API_VERSION = "2026-09-01"
ARM_SCOPE = "https://management.azure.com/.default"
SEARCH_SCOPE = "https://search.azure.com/.default"
ENV_ENTRY = re.compile(r"^\s*(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*=(.*)$")
REQUIRED = (
    "AZURE_RESOURCE_GROUP", "FOUNDRY_NAME", "FOUNDRY_PROJECT_NAME",
    "AZURE_SEARCH_SERVICE_NAME", "AZURE_SEARCH_ENDPOINT",
    "AZURE_SEARCH_KNOWLEDGE_BASE_NAME", "WEBIQ_RESOURCE_ID", "WEBIQ_ACCOUNT_NAME",
)


class SetupError(Exception):
    """A message safe to display without exposing credentials or service responses."""


class NoRedirects(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def load_configuration(path):
    if not path.is_file() or path.is_symlink():
        raise SetupError("A regular .env file is required. Run deployment or scripts/env.ps1 first.")
    settings = {}
    for line in path.read_text(encoding="utf-8-sig").splitlines():
        match = ENV_ENTRY.match(line)
        if match and match[1] in (*REQUIRED, "WEBIQ_API_KEY"):
            value = match[2].strip()
            if value.startswith('"'):
                try:
                    value = json.loads(value)
                except ValueError:
                    raise SetupError("A required .env setting has invalid quoting.") from None
            elif len(value) >= 2 and value[0] == value[-1] == "'":
                value = value[1:-1]
            settings[match[1]] = value
    missing = [name for name in REQUIRED if not isinstance(settings.get(name), str) or not settings[name]]
    if missing:
        raise SetupError(f"Missing deployment outputs in .env: {', '.join(missing)}.")
    resource = re.fullmatch(
        r"/subscriptions/([0-9a-fA-F-]{36})/resourceGroups/([^/]+)/providers/Microsoft\.WebIQ/accounts/([^/]+)",
        settings["WEBIQ_RESOURCE_ID"], re.IGNORECASE,
    )
    if (
        not resource
        or resource[2].lower() != settings["AZURE_RESOURCE_GROUP"].lower()
        or resource[3].lower() != settings["WEBIQ_ACCOUNT_NAME"].lower()
    ):
        raise SetupError("WEBIQ_RESOURCE_ID must identify the deployed Web IQ account in AZURE_RESOURCE_GROUP.")
    for name in ("FOUNDRY_NAME", "FOUNDRY_PROJECT_NAME", "AZURE_SEARCH_SERVICE_NAME"):
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]*", settings[name]):
            raise SetupError(f"Invalid deployment output {name}.")
    kb_name = settings["AZURE_SEARCH_KNOWLEDGE_BASE_NAME"]
    if not re.fullmatch(r"[a-z0-9][a-z0-9-]{0,121}", kb_name):
        raise SetupError("Invalid knowledge-base name in .env.")
    endpoint = f"https://{settings['AZURE_SEARCH_SERVICE_NAME']}.search.windows.net"
    if settings["AZURE_SEARCH_ENDPOINT"].rstrip("/") != endpoint:
        raise SetupError("AZURE_SEARCH_ENDPOINT must match AZURE_SEARCH_SERVICE_NAME over HTTPS.")
    # Construct management paths from the billing resource, never from a supplied URL.
    prefix = f"/subscriptions/{resource[1]}/resourceGroups/{resource[2]}"
    settings["connection_id"] = (
        f"{prefix}/providers/Microsoft.CognitiveServices/accounts/{settings['FOUNDRY_NAME']}"
        f"/projects/{settings['FOUNDRY_PROJECT_NAME']}/connections/{CONNECTION_NAME}"
    )
    settings["source_name"] = f"{kb_name}-webiq"
    settings["search_endpoint"] = endpoint
    return settings


def validate_key(key):
    if not isinstance(key, str) or not key or len(key) > 8192 or any(not 33 <= ord(c) <= 126 for c in key):
        raise SetupError("The key must be nonempty text without spaces, line breaks or control characters.")
    return key


def save_settings(path, updates):
    lines = path.read_text(encoding="utf-8-sig").splitlines()
    kept = [line for line in lines if not ((match := ENV_ENTRY.match(line)) and match[1] in updates)]
    kept += [f"{name}={json.dumps(value) if name == 'WEBIQ_API_KEY' else value}" for name, value in updates.items()]
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", dir=path.parent, prefix=".webiq-", suffix=".env", delete=False,
        ) as file:
            temporary = Path(file.name)
            file.write("\n".join(kept) + "\n")
        os.replace(temporary, path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def capture_key(settings):
    if not sys.stdin.isatty():
        raise SetupError("Run setup in an interactive terminal for hidden key input, or use --use-saved-key.")
    print(f"Web IQ billing resource: {settings['WEBIQ_RESOURCE_ID']}")
    print(f"1. Sign in at {PORTAL}.")
    print("2. Select the profile linked to this billing resource, or connect the resource if it is not listed.")
    print("3. Create an API key in that profile with the web service enabled, then copy it.")
    print("The key will be saved in the ignored .env file and shared with this Foundry project and Search service.")
    try:
        opened = webbrowser.open(PORTAL, new=2)
    except (webbrowser.Error, OSError):
        opened = False
    if not opened:
        print("A browser could not be opened automatically. Open the link above in your browser.")
    with warnings.catch_warnings():
        warnings.simplefilter("error", getpass.GetPassWarning)
        try:
            key = getpass.getpass("Paste the Web IQ API key (hidden): ")
        except getpass.GetPassWarning:
            raise SetupError("Hidden input is unavailable. Run setup in an interactive terminal.") from None
    return validate_key(key)


def azure_request(credential, method, url, scope, body=None, *, allow_not_found=False, etag=None):
    headers = {
        "Authorization": f"Bearer {credential.get_token(scope).token}",
        "Content-Type": "application/json",
    }
    if etag:
        headers["If-Match"] = etag
    if scope == SEARCH_SCOPE and method == "PUT":
        headers["Prefer"] = "return=representation"
    request = urllib.request.Request(
        url, data=None if body is None else json.dumps(body).encode("utf-8"),
        headers=headers, method=method,
    )
    try:
        with urllib.request.build_opener(NoRedirects()).open(request, timeout=60) as response:
            payload = response.read()
            return json.loads(payload) if payload else {}
    except urllib.error.HTTPError as error:
        if error.code == 404 and allow_not_found:
            return None
        if error.code == 412:
            raise SetupError("The knowledge base changed during setup. Rerun to merge the latest settings.") from None
        # Service error bodies can echo a submitted key; never include them in terminal output.
        raise SetupError(
            f"Azure {method} failed (HTTP {error.code}). Check sign-in, resource access and API support, then rerun."
        ) from None


def target_urls(settings):
    kb_name = settings["AZURE_SEARCH_KNOWLEDGE_BASE_NAME"]
    return (
        f"https://management.azure.com{urllib.parse.quote(settings['connection_id'], safe='/')}?api-version={ARM_API_VERSION}",
        f"{settings['search_endpoint']}/knowledgesources/{settings['source_name']}?api-version={SEARCH_API_VERSION}",
        f"{settings['search_endpoint']}/knowledgebases/{kb_name}?api-version={SEARCH_API_VERSION}",
    )


def check_targets(settings, request):
    connection_url, source_url, kb_url = target_urls(settings)
    knowledge_base = request("GET", kb_url, SEARCH_SCOPE, allow_not_found=True)
    if not knowledge_base or not knowledge_base.get("knowledgeSources"):
        raise SetupError("The knowledge base needs its index source first. Run scripts/deploy.ps1 -IndexOnly.")
    connection = request("GET", connection_url, ARM_SCOPE, allow_not_found=True)
    if connection:
        properties = connection.get("properties", {})
        if properties.get("target") != MCP_ENDPOINT or properties.get("category") != "RemoteTool":
            raise SetupError("investigator-webiq already names a different connection; it was not overwritten.")
    source = request("GET", source_url, SEARCH_SCOPE, allow_not_found=True)
    if source and (source.get("kind") != "mcpServer" or source.get("mcpServerParameters", {}).get("serverURL") != MCP_ENDPOINT):
        raise SetupError("The Web IQ source name is already in use for another source; it was not overwritten.")
    return knowledge_base


def configure_webiq(settings, key, knowledge_base, request):
    connection_url, source_url, kb_url = target_urls(settings)
    print(f"Configuring Foundry MCP connection {CONNECTION_NAME}.")
    request("PUT", connection_url, ARM_SCOPE, {
        "properties": {
            "category": "RemoteTool",
            "authType": "CustomKeys",
            "target": MCP_ENDPOINT,
            "credentials": {"keys": {"x-apikey": key}},
        },
    })
    print(f"Configuring MCP knowledge source {settings['source_name']}.")
    request("PUT", source_url, SEARCH_SCOPE, {
        "name": settings["source_name"],
        "kind": "mcpServer",
        "description": (
            "Public financial-crime typology research through Web IQ. "
            "Public sources provide general context, not evidence about fictional customers."
        ),
        "resultsProcessing": "rerank",
        "mcpServerParameters": {
            "serverURL": MCP_ENDPOINT,
            "authentication": {
                "kind": "storedHeaders",
                "storedHeadersParameters": {"headers": {"x-apikey": key}},
            },
            "tools": [{
                "name": "web",
                "outputParsing": {"kind": "auto"},
                "resultsProcessing": "none",
                "maxOutputTokens": 2000,
            }],
        },
    })
    source_name = settings["source_name"]
    if not any(source["name"] == source_name for source in knowledge_base["knowledgeSources"]):
        updated = {name: value for name, value in knowledge_base.items() if not name.startswith("@odata.")}
        updated["knowledgeSources"] = [*knowledge_base["knowledgeSources"], {"name": source_name}]
        request("PUT", kb_url, SEARCH_SCOPE, updated, etag=knowledge_base.get("@odata.etag"))
    connection = request("GET", connection_url, ARM_SCOPE).get("properties", {})
    source = request("GET", source_url, SEARCH_SCOPE).get("mcpServerParameters", {})
    saved_base = request("GET", kb_url, SEARCH_SCOPE)
    expected_sources = {item["name"] for item in knowledge_base["knowledgeSources"]} | {source_name}
    if (
        connection.get("target") != MCP_ENDPOINT
        or connection.get("category") != "RemoteTool"
        or connection.get("authType") != "CustomKeys"
        or source.get("serverURL") != MCP_ENDPOINT
        or source.get("authentication", {}).get("kind") != "storedHeaders"
        or "x-apikey" not in source.get("authentication", {}).get("storedHeadersParameters", {}).get("headers", {})
        or not any(tool["name"] == "web" for tool in source.get("tools", []))
        or not expected_sources.issubset({item["name"] for item in saved_base.get("knowledgeSources", [])})
    ):
        raise SetupError("Saved configuration could not be verified. Rerun setup before using Web IQ.")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--use-saved-key", action="store_true", help="Reuse WEBIQ_API_KEY from .env without browser or prompt.")
    args = parser.parse_args(argv)
    path = ROOT / ".env"
    stage = "checking local settings"
    try:
        settings = load_configuration(path)
        ignored = subprocess.run(
            ["git", "check-ignore", "--quiet", "--", ".env"], cwd=ROOT,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False,
        )
        if ignored.returncode != 0:
            raise SetupError(".env must be ignored and untracked in Git before a key can be stored.")
        with AzureCliCredential() as credential:
            def request(*params, **kwargs):
                return azure_request(credential, *params, **kwargs)

            stage = "checking Azure access and the existing knowledge base"
            knowledge_base = check_targets(settings, request)
            stage = "reading the Web IQ key"
            key = validate_key(settings.get("WEBIQ_API_KEY")) if args.use_saved_key else capture_key(settings)
            stage = "saving the key in .env"
            save_settings(path, {"WEBIQ_API_KEY": key})
            stage = "configuring the independent Foundry and Search integrations"
            configure_webiq(settings, key, knowledge_base, request)
            stage = "saving connection settings in .env"
            save_settings(path, {
                "WEBIQ_MCP_ENDPOINT": MCP_ENDPOINT,
                "WEBIQ_CONNECTION_NAME": CONNECTION_NAME,
                "WEBIQ_CONNECTION_ID": settings["connection_id"],
                "WEBIQ_KNOWLEDGE_SOURCE_NAME": settings["source_name"],
            })
        print("Web IQ configuration saved. The existing index source and knowledge-base settings were preserved.")
        print("No web search was run. Use only approved public-topic queries, never customer data or full case prompts.")
        return 0
    except (KeyboardInterrupt, EOFError):
        print("Web IQ setup cancelled. Rerun setup to finish any partially applied configuration.", file=sys.stderr)
    except SetupError as error:
        print(f"Web IQ setup stopped while {stage}: {error}", file=sys.stderr)
    except Exception:
        print(
            f"Web IQ setup stopped while {stage}. Check Azure sign-in and access, then rerun. "
            "Error details are withheld to protect the key.",
            file=sys.stderr,
        )
    return 1


if __name__ == "__main__":
    sys.exit(main())
