"""Temporary, standalone diagnostic for the investigator-webiq Foundry MCP connection.

Read-only by default. --test-write repeats the setup script's connection PUT using
WEBIQ_API_KEY from .env. It does not configure Search, invoke an agent, or run a web search.
Requires only Python 3.12+ and an authenticated Azure CLI.
"""
import argparse
import base64
import fnmatch
import json
import re
import shutil
import subprocess
import sys
import urllib.error
import urllib.parse
import urllib.request
import uuid
from datetime import datetime, timezone
from pathlib import Path

ARM = "https://management.azure.com"
MCP = "https://api.microsoft.ai/v3/mcp"
CONNECTION = "investigator-webiq"
WRITE_ACTION = "Microsoft.CognitiveServices/accounts/projects/connections/write"
REQUIRED = ("AZURE_RESOURCE_GROUP", "FOUNDRY_NAME", "FOUNDRY_PROJECT_NAME", "WEBIQ_RESOURCE_ID")


class DiagnosticError(Exception):
    pass


class NoRedirects(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def load_settings(path):
    if path.is_symlink() or not path.is_file():
        raise DiagnosticError("Use --env-file to select a regular lab .env file.")
    settings = {}
    for line in path.read_text(encoding="utf-8-sig").splitlines():
        match = re.match(r"^\s*(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*=(.*)$", line)
        if not match or match[1] not in (*REQUIRED, "WEBIQ_API_KEY"):
            continue
        value = match[2].strip()
        if value.startswith('"'):
            try:
                value = json.loads(value)
            except ValueError:
                raise DiagnosticError(f"Invalid quoting for {match[1]}; no value was printed.") from None
        elif len(value) >= 2 and value[0] == value[-1] == "'":
            value = value[1:-1]
        if not isinstance(value, str) or any(ord(char) < 32 for char in value):
            raise DiagnosticError(f"Invalid value for {match[1]}; no value was printed.")
        settings[match[1]] = value
    missing = [key for key in REQUIRED if not settings.get(key)]
    if missing:
        raise DiagnosticError(f"Missing settings: {', '.join(missing)}.")
    resource = re.fullmatch(
        r"/subscriptions/([0-9a-fA-F-]{36})/resourceGroups/([^/]+)/providers/Microsoft\.WebIQ/accounts/[^/]+",
        settings["WEBIQ_RESOURCE_ID"], re.IGNORECASE,
    )
    if not resource or resource[2].casefold() != settings["AZURE_RESOURCE_GROUP"].casefold():
        raise DiagnosticError("WEBIQ_RESOURCE_ID must identify a Web IQ account in AZURE_RESOURCE_GROUP.")
    settings["subscription"] = str(uuid.UUID(resource[1]))
    for name in ("FOUNDRY_NAME", "FOUNDRY_PROJECT_NAME"):
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]*", settings[name]):
            raise DiagnosticError(f"Invalid resource name in {name}.")
    group = urllib.parse.quote(settings["AZURE_RESOURCE_GROUP"], safe="")
    settings["project_id"] = (
        f"/subscriptions/{settings['subscription']}/resourceGroups/{group}"
        f"/providers/Microsoft.CognitiveServices/accounts/{settings['FOUNDRY_NAME']}"
        f"/projects/{settings['FOUNDRY_PROJECT_NAME']}"
    )
    return settings


def az_json(*args):
    executable = shutil.which("az")
    if not executable:
        raise DiagnosticError("Azure CLI is not on PATH. Open a terminal where az login works.")
    try:
        result = subprocess.run(
            [executable, *args, "--output", "json"], capture_output=True,
            encoding="utf-8", errors="replace", timeout=60, check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        raise DiagnosticError("Azure CLI could not run or timed out.") from None
    if result.returncode:
        raise DiagnosticError("Azure CLI failed. Check az login and access to the subscription in .env.")
    try:
        return json.loads(result.stdout)
    except ValueError:
        raise DiagnosticError("Azure CLI returned invalid JSON; its output was withheld.") from None


def request(method, url, token, body=None):
    parsed = urllib.parse.urlsplit(url)
    if parsed.scheme != "https" or parsed.netloc != "management.azure.com":
        raise DiagnosticError("Refusing to send credentials outside management.azure.com.")
    req = urllib.request.Request(
        url, method=method, headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
        data=json.dumps(body).encode("utf-8") if body is not None else None,
    )
    try:
        with urllib.request.build_opener(NoRedirects()).open(req, timeout=60) as response:
            return response.status, json.loads(response.read() or b"{}"), response.headers
    except urllib.error.HTTPError as error:
        try:
            payload = json.loads(error.read(65536))
        except ValueError:
            payload = {}
        return error.code, payload, error.headers
    except (urllib.error.URLError, TimeoutError, OSError):
        raise DiagnosticError("ARM request failed before an HTTP response. Check proxy, DNS and network access.") from None


def safe_message(text, secrets):
    if not isinstance(text, str):
        return ""
    variants = set()
    for secret in secrets:
        if secret:
            variants.update((secret, json.dumps(secret)[1:-1], urllib.parse.quote(secret, safe="")))
    for secret in sorted(variants, key=len, reverse=True):
        text = text.replace(secret, "[redacted]")
    text = re.sub(r"\beyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\b", "[redacted-token]", text)
    return "".join(char for char in text if char.isprintable())[:2000]


def report(label, result, *, show_message=False, secrets=()):
    status, payload, headers = result
    print(f"{label}: HTTP {status}")
    code = ""
    if isinstance(payload, dict) and isinstance(payload.get("error"), dict):
        code = payload["error"].get("code", "")
        if isinstance(code, str) and re.fullmatch(r"[A-Za-z][A-Za-z0-9_.-]{0,100}", code):
            print(f"  Azure error code: {code}")
        if show_message:
            text = safe_message(payload["error"].get("message", ""), secrets)
            if text:
                print(f"  Azure error message (known credentials redacted): {text}")
    for name in ("x-ms-request-id", "x-ms-correlation-request-id", "request-id"):
        value = headers.get(name, "")
        try:
            value = str(uuid.UUID(value))
        except (ValueError, AttributeError):
            continue
        print(f"  {name}: {value}")
    if status == 401 and code == "RequestDisallowedByAzure":
        print("  Azure platform enforcement denied this write. A missing MFA claim is one possible cause.")
        print("  ARM reads can succeed while writes require MFA. The error message or Entra sign-in logs can confirm why.")
    elif status == 401:
        print("  Authentication or downstream credential validation failed; this alone does not establish missing RBAC.")
    elif status == 403:
        print("  Access was denied. Check the Azure error code, effective permissions and any deny assignments/policies.")
    elif status == 404:
        print("  Resource is missing or not visible to this identity.")
    # Full response bodies and raw CLI output are never printed.


def write_permission(payload):
    for entry in payload.get("value", []):
        allowed = any(fnmatch.fnmatchcase(WRITE_ACTION.lower(), action.lower()) for action in entry.get("actions", []))
        excluded = any(fnmatch.fnmatchcase(WRITE_ACTION.lower(), action.lower()) for action in entry.get("notActions", []))
        if allowed and not excluded:
            return True
    return False


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    default_env = Path(__file__).resolve().parent.parent / ".env"
    parser.add_argument("--env-file", type=Path, default=default_env)
    parser.add_argument("--api-version", default="2026-09-01")
    parser.add_argument("--test-write", action="store_true", help="Create/update investigator-webiq using the saved key.")
    parser.add_argument("--show-error-message", action="store_true", help="Print Azure error messages with known keys/tokens redacted.")
    args = parser.parse_args(argv)
    try:
        settings = load_settings(args.env_file)
        subscription = settings["subscription"]
        account = az_json("account", "show", "--subscription", subscription)
        token = az_json(
            "account", "get-access-token", "--subscription", subscription, "--resource", ARM,
        )["accessToken"]
        def show(label, result):
            report(
                label, result, show_message=args.show_error_message,
                secrets=(token, settings.get("WEBIQ_API_KEY", "")),
            )
        print(f"Subscription: {subscription}")
        print(f"Tenant: {account['tenantId']}")
        print(f"Target: {settings['FOUNDRY_NAME']}/{settings['FOUNDRY_PROJECT_NAME']}/{CONNECTION}")
        print(f"Saved Web IQ key: {'present' if settings.get('WEBIQ_API_KEY') else 'absent'}")
        try:
            claims = json.loads(base64.urlsafe_b64decode(token.split(".")[1] + "==="))
            audience = claims.get("aud") in (
                ARM, ARM + "/", "https://management.core.windows.net", "https://management.core.windows.net/",
            )
            print(f"Token audience is ARM: {audience}")
            print(f"Token tenant matches CLI tenant: {claims.get('tid') == account['tenantId']}")
            methods = claims.get("amr", [])
            print(f"Token explicitly lists MFA in amr: {isinstance(methods, list) and 'mfa' in methods}")
            print("  Absence of amr/mfa alone is not proof that MFA was not satisfied.")
            print(f"Token expiry (UTC): {datetime.fromtimestamp(claims['exp'], timezone.utc).isoformat()}")
        except (ValueError, KeyError, IndexError, TypeError, OverflowError, OSError):
            print("Token metadata unavailable; the token itself was not printed.")
        project_url = ARM + settings["project_id"]
        project = request("GET", project_url + "?api-version=2025-06-01", token)
        show("Read Foundry project", project)
        permissions = request(
            "GET", project_url + "/providers/Microsoft.Authorization/permissions?api-version=2022-04-01", token,
        )
        show("Read effective project permissions", permissions)
        if permissions[0] == 200:
            permitted = write_permission(permissions[1])
            print(f"  Connection-write action appears allowed: {permitted}")
            print("  Permission patterns are advisory; conditions and deny assignments may still apply.")
            if permissions[1].get("nextLink"):
                print("  Additional permission pages exist; the result above is incomplete.")
        url = project_url + f"/connections/{CONNECTION}?api-version={urllib.parse.quote(args.api_version, safe='')}"
        existing = request("GET", url, token)
        show(f"Read MCP connection ({args.api_version})", existing)
        if args.test_write:
            key = settings.get("WEBIQ_API_KEY")
            if not key:
                raise DiagnosticError("--test-write requires WEBIQ_API_KEY in .env. Do not pass keys as CLI arguments.")
            if any(not 33 <= ord(char) <= 126 for char in key) or len(key) > 8192:
                raise DiagnosticError("The saved Web IQ key has invalid characters or length.")
            if existing[0] == 200:
                properties = existing[1].get("properties", {})
                if (properties.get("target") != MCP or properties.get("category") != "RemoteTool"
                        or properties.get("authType") != "CustomKeys"):
                    raise DiagnosticError("The existing connection has different settings; refusing to overwrite it.")
            elif existing[0] != 404:
                raise DiagnosticError("The connection could not be inspected; refusing to attempt a write.")
            print("Explicit write test: updating only investigator-webiq with the saved .env key.")
            written = request("PUT", url, token, {"properties": {
                "category": "RemoteTool", "authType": "CustomKeys", "target": MCP,
                "credentials": {"keys": {"x-apikey": key}},
            }})
            show("Create/update MCP connection", written)
            return 0 if written[0] in (200, 201) else 1
        print("Read-only diagnostic finished. No settings were changed or searches run.")
        print("To reproduce the failing PUT, rerun with --test-write (updates the connection using the saved key).")
        return 0 if project[0] == 200 and existing[0] == 200 else 1
    except DiagnosticError as error:
        print(f"Stopped: {error}", file=sys.stderr)
    except (KeyboardInterrupt, EOFError):
        print("Diagnostic cancelled.", file=sys.stderr)
    except Exception:
        print("Diagnostic stopped unexpectedly. Error details were withheld to protect credentials.", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
