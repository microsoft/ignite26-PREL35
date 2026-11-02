import copy
import io
import json
import os
import tempfile
import unittest
import urllib.error
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest.mock import MagicMock, Mock, call, patch

import pytest

from scripts import setup_webiq as setup


FIXTURE_KEY = "test-only-key+/='\"\\$#"
FIXTURE_ENV = {
    "AZURE_RESOURCE_GROUP": "fixture",
    "FOUNDRY_NAME": "fixture-foundry",
    "FOUNDRY_PROJECT_NAME": "fincrime",
    "AZURE_SEARCH_SERVICE_NAME": "fixture-search",
    "AZURE_SEARCH_ENDPOINT": "https://fixture-search.search.windows.net",
    "AZURE_SEARCH_KNOWLEDGE_BASE_NAME": "fincrime-research",
    "WEBIQ_RESOURCE_ID": (
        "/subscriptions/00000000-0000-0000-0000-000000000001"
        "/resourceGroups/fixture/providers/Microsoft.WebIQ/accounts/fixture-webiq"
    ),
    "WEBIQ_ACCOUNT_NAME": "fixture-webiq",
}


def fixture_env(path):
    path.write_text("\n".join(f"{key}={value}" for key, value in FIXTURE_ENV.items()) + "\n")
    return setup.load_configuration(path)


def mcp_responses(tool_result, content_type):
    responses = []
    for request_id, result in (
        (1, {"protocolVersion": "2025-03-26"}),
        (None, None),
        (2, {"tools": [{"name": "web"}]}),
        (3, tool_result),
    ):
        body = json.dumps({"jsonrpc": "2.0", "id": request_id, "result": result})
        if content_type == "text/event-stream":
            body = f"event: message\r\ndata: {body}\r\n\r\n"
        response = io.BytesIO(body.encode() if request_id is not None else b"")
        response.headers = {"Content-Type": content_type, "Mcp-Session-Id": "fixture-mcp-session"}
        responses.append(response)
    return responses


def configured_webiq_state(settings):
    connection_url, source_url, kb_url = setup.target_urls(settings)
    return {
        connection_url: {"properties": {
            "target": setup.MCP_ENDPOINT, "category": "RemoteTool", "authType": "CustomKeys",
        }},
        source_url: {"kind": "mcpServer", "mcpServerParameters": {
            "serverURL": setup.MCP_ENDPOINT,
            "authentication": {"kind": "storedHeaders"},
            "tools": [{"name": "web"}],
        }},
        kb_url: {"knowledgeSources": [
            {"name": "fincrime-research-schemes"}, {"name": settings["source_name"]},
        ]},
    }


class WebIqSetupTests(unittest.TestCase):
    def test_if_needed_checks_deployed_configuration_not_local_key(self):
        for missing, saved_key in ((None, False), ("connection", True), ("source", False), ("attachment", True)):
            with self.subTest(missing=missing), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                path = root / ".env"
                settings = fixture_env(path)
                if saved_key:
                    setup.save_settings(path, {"WEBIQ_API_KEY": FIXTURE_KEY})
                before = path.read_bytes()
                state = configured_webiq_state(settings)
                connection_url, source_url, kb_url = setup.target_urls(settings)
                if missing == "connection":
                    del state[connection_url]
                elif missing == "source":
                    del state[source_url]
                elif missing == "attachment":
                    state[kb_url]["knowledgeSources"].pop()
                output = io.StringIO()
                with (
                    patch.object(setup, "ROOT", root),
                    patch.object(setup.subprocess, "run", return_value=Mock(returncode=0)),
                    patch.object(setup, "AzureCliCredential"),
                    patch.object(setup, "azure_request", side_effect=lambda credential, method, url, *a, **k: state.get(url)) as request,
                    patch.object(setup, "capture_key", return_value=FIXTURE_KEY) as capture,
                    patch.object(setup, "configure_webiq") as configure,
                    patch.object(setup, "check_webiq_search") as web_search,
                    patch.object(setup, "check_knowledge_base_search") as kb_search,
                    redirect_stdout(output), redirect_stderr(output),
                ):
                    self.assertEqual(setup.main(["--if-needed"]), 0)
                    self.assertTrue(all(call.args[1] == "GET" for call in request.call_args_list))
                    if missing is None:
                        self.assertEqual(path.read_bytes(), before)
                        capture.assert_not_called()
                        configure.assert_not_called()
                        web_search.assert_not_called()
                        kb_search.assert_not_called()
                        self.assertIn("already configured", output.getvalue())
                    else:
                        self.assertEqual(capture.call_count, 0 if saved_key else 1)
                        configure.assert_called_once()
                        self.assertEqual(web_search.call_args_list, [
                            call(FIXTURE_KEY, validate_only=True), call(FIXTURE_KEY),
                        ])
                        kb_search.assert_called_once()
                self.assertNotIn(FIXTURE_KEY, output.getvalue())

    def test_if_needed_repairs_wrong_authentication_and_missing_web_tool(self):
        with tempfile.TemporaryDirectory() as directory:
            settings = fixture_env(Path(directory) / ".env")
        connection_url, source_url, kb_url = setup.target_urls(settings)
        for changed in ("connection-auth", "source-auth", "web-tool", "retired-source"):
            with self.subTest(changed=changed):
                state = configured_webiq_state(settings)
                if changed == "connection-auth":
                    state[connection_url]["properties"]["authType"] = "AAD"
                elif changed == "source-auth":
                    state[source_url]["mcpServerParameters"]["authentication"] = {"kind": "foundryConnection"}
                elif changed == "web-tool":
                    state[source_url]["mcpServerParameters"]["tools"] = []
                else:
                    state[kb_url]["knowledgeSources"].append({"name": "fincrime-research-web"})
                request = Mock(side_effect=lambda method, url, *a, **k: state[url])
                self.assertFalse(setup.webiq_is_configured(settings, state[kb_url], request))

    def test_if_needed_does_not_treat_access_errors_as_missing_setup(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            fixture_env(root / ".env")
            output = io.StringIO()
            with (
                patch.object(setup, "ROOT", root),
                patch.object(setup.subprocess, "run", return_value=Mock(returncode=0)),
                patch.object(setup, "AzureCliCredential"),
                patch.object(setup, "azure_request", side_effect=setup.SetupError("Azure GET failed (HTTP 403).")),
                patch.object(setup, "capture_key") as capture,
                patch.object(setup, "configure_webiq") as configure,
                redirect_stdout(output), redirect_stderr(output),
            ):
                self.assertEqual(setup.main(["--if-needed"]), 1)
                capture.assert_not_called()
                configure.assert_not_called()
            self.assertIn("HTTP 403", output.getvalue())

    def test_key_rotation_preserves_other_settings_comments_and_private_permissions(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / ".env"
            fixture_env(path)
            path.write_text(path.read_text() + '# organizer settings\nOBO_SCOPE=keep\nWEBIQ_API_KEY=old\nWEBIQ_API_KEY=duplicate\n')
            for key in (FIXTURE_KEY, "rotated-test-key"):
                setup.save_settings(path, {"WEBIQ_API_KEY": key})
                settings = setup.load_configuration(path)
                self.assertEqual(settings["WEBIQ_API_KEY"], key)
                self.assertEqual(settings["FOUNDRY_NAME"], FIXTURE_ENV["FOUNDRY_NAME"])
                text = path.read_text()
                self.assertIn("# organizer settings\nOBO_SCOPE=keep\n", text)
                self.assertEqual(text.count("WEBIQ_API_KEY="), 1)
                if os.name != "nt":
                    self.assertEqual(path.stat().st_mode & 0o777, 0o600)
            self.assertEqual([p.name for p in path.parent.iterdir()], [".env"])

    def test_invalid_key_or_target_is_rejected_without_showing_values(self):
        for key in ("", "with spaces", "line\nbreak", "\x00secret", "x" * 8193, None):
            with self.subTest(key=repr(key)[:30]), self.assertRaises(setup.SetupError):
                setup.validate_key(key)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / ".env"
            for name, value in (
                ("AZURE_SEARCH_ENDPOINT", "https://fixture-search.search.windows.net.example.org"),
                ("AZURE_RESOURCE_GROUP", "another-resource-group"),
                ("FOUNDRY_PROJECT_NAME", "project?redirect=elsewhere"),
            ):
                fixture_env(path)
                setup.save_settings(path, {name: value})
                with self.subTest(name=name), self.assertRaises(setup.SetupError) as raised:
                    setup.load_configuration(path)
                self.assertNotIn(value, str(raised.exception))

    def test_guided_flow_opens_browser_and_uses_hidden_prompt(self):
        output = io.StringIO()
        with (
            patch.object(setup.sys.stdin, "isatty", return_value=True),
            patch.object(setup.webbrowser, "open", return_value=False) as browser,
            patch.object(setup.getpass, "getpass", return_value=FIXTURE_KEY) as hidden,
            redirect_stdout(output),
        ):
            self.assertEqual(setup.capture_key(FIXTURE_ENV), FIXTURE_KEY)
        browser.assert_called_once_with(setup.PORTAL, new=2)
        hidden.assert_called_once()
        self.assertIn("Open the link", output.getvalue())
        self.assertNotIn(FIXTURE_KEY, output.getvalue())
        with patch.object(setup.sys.stdin, "isatty", return_value=False), self.assertRaises(setup.SetupError):
            setup.capture_key(FIXTURE_ENV)

    def test_independent_credentials_and_reruns_preserve_knowledge_base(self):
        with tempfile.TemporaryDirectory() as directory:
            settings = fixture_env(Path(directory) / ".env")
        connection_url, source_url, kb_url = setup.target_urls(settings)
        original = {
            "name": "fincrime-research",
            "@odata.etag": '"original"',
            "knowledgeSources": [
                {"name": "fincrime-research-schemes", "alwaysQuerySource": True},
                {"name": "fincrime-research-web"},
            ],
            "models": [{"kind": "azureOpenAI", "azureOpenAIParameters": {"deploymentId": "gpt-5-mini"}}],
            "retrieveDefaults": {"maxRuntimeInSeconds": 45, "maxOutputSizeInTokens": 5000},
            "retrievalInstructions": "Keep this authored instruction.",
        }
        state = {kb_url: copy.deepcopy(original)}
        puts = []

        def request(method, url, scope, body=None, **options):
            if method == "PUT":
                puts.append((url, copy.deepcopy(body), options))
                state[url] = copy.deepcopy(body)
                return {}
            return copy.deepcopy(state.get(url))

        for key in (FIXTURE_KEY, "rotated-test-key"):
            existing = setup.check_targets(settings, request)
            with redirect_stdout(io.StringIO()):
                setup.configure_webiq(settings, key, existing, request)
            self.assertEqual(state[connection_url]["properties"]["credentials"]["keys"], {"x-apikey": key})
            parameters = state[source_url]["mcpServerParameters"]
            self.assertEqual(parameters["authentication"], {
                "kind": "storedHeaders", "storedHeadersParameters": {"headers": {"x-apikey": key}},
            })
            self.assertEqual([tool["name"] for tool in parameters["tools"]], ["web"])
            self.assertEqual(len(state[kb_url]["knowledgeSources"]), 2)
            self.assertNotIn("fincrime-research-web", [source["name"] for source in state[kb_url]["knowledgeSources"]])
            for name in ("models", "retrieveDefaults", "retrievalInstructions"):
                self.assertEqual(state[kb_url][name], original[name])
            self.assertEqual(state[kb_url]["knowledgeSources"][0], original["knowledgeSources"][0])
        kb_puts = [entry for entry in puts if entry[0] == kb_url]
        self.assertEqual(len(kb_puts), 1)
        self.assertEqual(kb_puts[0][2], {"etag": '"original"'})
        self.assertNotIn("@odata.etag", kb_puts[0][1])

    def test_verification_accepts_write_only_headers_but_rejects_wrong_authentication(self):
        with tempfile.TemporaryDirectory() as directory:
            settings = fixture_env(Path(directory) / ".env")
        original = {"knowledgeSources": [
            {"name": "fincrime-research-schemes"}, {"name": settings["source_name"]},
        ]}
        for authentication, accepted in (
            ({"kind": "storedHeaders"}, True),
            ({"kind": "storedHeaders", "storedHeadersParameters": {"headers": {}}}, True),
            ({"kind": "foundryConnection"}, False),
            ({}, False),
        ):
            with self.subTest(authentication=authentication):
                request = Mock(side_effect=[
                    {}, {},
                    {"properties": {
                        "target": setup.MCP_ENDPOINT, "category": "RemoteTool", "authType": "CustomKeys",
                    }},
                    {"mcpServerParameters": {
                        "serverURL": setup.MCP_ENDPOINT,
                        "authentication": authentication,
                        "tools": [{"name": "web"}],
                    }},
                    original,
                ])
                with redirect_stdout(io.StringIO()):
                    if accepted:
                        setup.configure_webiq(settings, FIXTURE_KEY, original, request)
                    else:
                        with self.assertRaisesRegex(setup.SetupError, "Saved configuration could not be verified"):
                            setup.configure_webiq(settings, FIXTURE_KEY, original, request)

    def test_configuration_failure_stops_before_changing_knowledge_base(self):
        with tempfile.TemporaryDirectory() as directory:
            settings = fixture_env(Path(directory) / ".env")
        request = Mock(side_effect=[{}, setup.SetupError("Source creation failed.")])
        original = {"knowledgeSources": [{"name": "fincrime-research-schemes"}]}
        with redirect_stdout(io.StringIO()), self.assertRaisesRegex(setup.SetupError, "Source creation failed"):
            setup.configure_webiq(settings, FIXTURE_KEY, original, request)
        self.assertEqual(request.call_count, 2)
        self.assertEqual(original["knowledgeSources"], [{"name": "fincrime-research-schemes"}])

    def test_existing_unrelated_connection_is_not_overwritten(self):
        with tempfile.TemporaryDirectory() as directory:
            settings = fixture_env(Path(directory) / ".env")
        request = Mock(side_effect=[
            {"knowledgeSources": [{"name": "schemes"}]},
            {"properties": {"category": "RemoteTool", "target": "https://another.example/mcp"}},
        ])
        with self.assertRaisesRegex(setup.SetupError, "not overwritten"):
            setup.check_targets(settings, request)
        self.assertTrue(all(call.args[0] == "GET" for call in request.call_args_list))

    def test_http_errors_never_expose_response_body_or_credentials(self):
        credential = Mock()
        credential.get_token.return_value.token = "test-access-token"
        for code in (400, 403, 412, 503):
            with self.subTest(code=code):
                error = urllib.error.HTTPError(
                    "https://management.azure.com/test", code, FIXTURE_KEY, {},
                    io.BytesIO(json.dumps({"error": {"message": FIXTURE_KEY}}).encode()),
                )
                opener = Mock()
                opener.open.side_effect = error
                with patch.object(setup.urllib.request, "build_opener", return_value=opener):
                    with self.assertRaises(setup.SetupError) as raised:
                        setup.azure_request(credential, "PUT", "https://management.azure.com/test", setup.ARM_SCOPE)
                self.assertNotIn(FIXTURE_KEY, str(raised.exception))
                self.assertNotIn("test-access-token", str(raised.exception))

    def test_main_keeps_saved_key_after_partial_failure_without_disclosing_it(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            fixture_env(root / ".env")
            output = io.StringIO()
            with (
                patch.object(setup, "ROOT", root),
                patch.object(setup.subprocess, "run", return_value=Mock(returncode=0)),
                patch.object(setup, "AzureCliCredential"),
                patch.object(setup, "check_targets", return_value={"knowledgeSources": [{"name": "schemes"}]}),
                patch.object(setup, "capture_key", return_value=FIXTURE_KEY) as capture,
                patch.object(setup, "check_webiq_search"),
                patch.object(setup, "configure_webiq", side_effect=RuntimeError(FIXTURE_KEY)),
                redirect_stdout(output), redirect_stderr(output),
            ):
                self.assertEqual(setup.main([]), 1)
                self.assertEqual(setup.load_configuration(root / ".env")["WEBIQ_API_KEY"], FIXTURE_KEY)
                self.assertEqual(setup.main(["--use-saved-key"]), 1)
                self.assertEqual(capture.call_count, 1)
            self.assertNotIn(FIXTURE_KEY, output.getvalue())
            self.assertIn("Error details are withheld", output.getvalue())

    def test_saved_key_is_reused_and_no_run_only_skips_search_checks(self):
        for arguments in ([], ["--force"], ["--use-saved-key"], ["--no-run"], ["--force", "--no-run"]):
            with self.subTest(arguments=arguments), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                path = root / ".env"
                fixture_env(path)
                setup.save_settings(path, {"WEBIQ_API_KEY": FIXTURE_KEY})
                settings = setup.load_configuration(path)
                knowledge_base = {"knowledgeSources": [{"name": "schemes"}]}
                output = io.StringIO()
                with (
                    patch.object(setup, "ROOT", root),
                    patch.object(setup.subprocess, "run", return_value=Mock(returncode=0)),
                    patch.object(setup, "AzureCliCredential"),
                    patch.object(setup, "check_targets", return_value=knowledge_base) as targets,
                    patch.object(setup, "capture_key") as capture,
                    patch.object(setup, "configure_webiq") as configure,
                    patch.object(setup, "check_webiq_search") as web_search,
                    patch.object(setup, "check_knowledge_base_search") as kb_search,
                    redirect_stdout(output), redirect_stderr(output),
                ):
                    self.assertEqual(setup.main(arguments), 0)
                targets.assert_called_once()
                capture.assert_not_called()
                configure.assert_called_once()
                if "--no-run" in arguments:
                    web_search.assert_called_once_with(FIXTURE_KEY, validate_only=True)
                    kb_search.assert_not_called()
                else:
                    self.assertEqual(web_search.call_args_list, [
                        call(FIXTURE_KEY, validate_only=True), call(FIXTURE_KEY),
                    ])
                    kb_search.assert_called_once()
                self.assertEqual(configure.call_args.args[:3], (settings, FIXTURE_KEY, knowledge_base))
                self.assertEqual(setup.load_configuration(path)["WEBIQ_API_KEY"], FIXTURE_KEY)
                self.assertNotIn(FIXTURE_KEY, output.getvalue())

    def test_direct_mcp_search_supports_json_and_sse_with_structured_or_text_results(self):
        payload = {"webResults": [{"title": "Money Mule Initiative", "url": "https://www.justice.gov/civil"}]}
        for content_type in ("application/json", "text/event-stream"):
            for result in (
                {"structuredContent": payload},
                {"content": [{"type": "text", "text": json.dumps(payload)}]},
            ):
                with self.subTest(content_type=content_type, structured="structuredContent" in result):
                    opener = Mock()
                    opener.open.side_effect = mcp_responses(result, content_type)
                    output = io.StringIO()
                    with patch.object(setup.urllib.request, "build_opener", return_value=opener), redirect_stdout(output):
                        setup.check_webiq_search(FIXTURE_KEY)
                    self.assertEqual(opener.open.call_count, 4)
                    request = opener.open.call_args.args[0]
                    self.assertEqual(request.full_url, setup.MCP_ENDPOINT)
                    self.assertEqual(request.get_header("X-apikey"), FIXTURE_KEY)
                    self.assertEqual(request.get_header("Mcp-session-id"), "fixture-mcp-session")
                    call = json.loads(request.data)
                    self.assertEqual(call["method"], "tools/call")
                    self.assertEqual(call["params"]["arguments"]["contentFormat"], "passage")
                    self.assertEqual(call["params"]["arguments"]["query"], setup.PUBLIC_TEST_QUERY)
                    self.assertIn("1 web results", output.getvalue())
                    self.assertNotIn(FIXTURE_KEY, output.getvalue())

    def test_direct_mcp_search_rejects_tool_errors_and_empty_results_without_leaking_keys(self):
        for result in (
            {"isError": True, "content": [{"type": "text", "text": FIXTURE_KEY}]},
            {"structuredContent": {"webResults": []}},
        ):
            with self.subTest(tool_error=bool(result.get("isError"))):
                opener = Mock()
                opener.open.side_effect = mcp_responses(result, "application/json")
                with (
                    patch.object(setup.urllib.request, "build_opener", return_value=opener),
                    redirect_stdout(io.StringIO()),
                    self.assertRaises(setup.SetupError) as raised,
                ):
                    setup.check_webiq_search(FIXTURE_KEY)
                self.assertNotIn(FIXTURE_KEY, str(raised.exception))

    def test_direct_mcp_http_error_does_not_retry_or_expose_server_body(self):
        opener = Mock()
        opener.open.side_effect = urllib.error.HTTPError(
            setup.MCP_ENDPOINT, 403, FIXTURE_KEY, {}, io.BytesIO(FIXTURE_KEY.encode()),
        )
        with (
            patch.object(setup.urllib.request, "build_opener", return_value=opener),
            redirect_stdout(io.StringIO()),
            self.assertRaises(setup.SetupError) as raised,
        ):
            setup.check_webiq_search(FIXTURE_KEY)
        opener.open.assert_called_once()
        self.assertIn("HTTP 403", str(raised.exception))
        self.assertNotIn(FIXTURE_KEY, str(raised.exception))

    def test_knowledge_base_check_isolates_webiq_and_requires_successful_webiq_references(self):
        with tempfile.TemporaryDirectory() as directory:
            settings = fixture_env(Path(directory) / ".env")
        source_name = settings["source_name"]
        knowledge_base = {"knowledgeSources": [
            {"name": "schemes"}, {"name": source_name}, {"name": "another-mcp-source"},
        ]}
        success = {
            "activity": [{
                "type": "mcpServer", "id": 1, "knowledgeSourceName": source_name,
                "mcpServerArguments": {"toolName": "web"},
            }],
            "references": [{"type": "mcpServer", "id": "0", "activitySource": 1}],
        }
        cases = [(success, True)]
        for changes in (
            {"error": {"message": FIXTURE_KEY}},
            {"references": []},
            {"references": [{"type": "searchIndex", "activitySource": 1}]},
            {"activity": [{**success["activity"][0], "error": {"message": FIXTURE_KEY}}]},
            {"activity": [{**success["activity"][0], "knowledgeSourceName": "another-mcp-source"}]},
        ):
            cases.append(({**success, **changes}, False))
        for result, accepted in cases:
            with self.subTest(accepted=accepted, result=result):
                request = Mock(side_effect=[
                    copy.deepcopy(knowledge_base), {"kind": "searchIndex"}, {"kind": "mcpServer"}, result,
                ])
                output = io.StringIO()
                with redirect_stdout(output):
                    if accepted:
                        setup.check_knowledge_base_search(settings, request)
                    else:
                        with self.assertRaises(setup.SetupError) as raised:
                            setup.check_knowledge_base_search(settings, request)
                        self.assertNotIn(FIXTURE_KEY, str(raised.exception))
                method, url, scope, body = request.call_args.args
                self.assertEqual(method, "POST")
                self.assertIn("/retrieve?api-version=", url)
                self.assertEqual(scope, setup.SEARCH_SCOPE)
                self.assertNotIn("maxOutputSizeInTokens", body)
                parameters = {entry["knowledgeSourceName"]: entry for entry in body["knowledgeSourceParams"]}
                self.assertTrue(parameters["schemes"]["neverQuerySource"])
                self.assertTrue(parameters["another-mcp-source"]["neverQuerySource"])
                self.assertNotIn("neverQuerySource", parameters[source_name])
                self.assertEqual(body["messages"][0]["content"][0]["text"], setup.PUBLIC_TEST_QUERY)
                self.assertEqual(request.call_count, 4)
                self.assertTrue(all(call.args[0] == "GET" for call in request.call_args_list[:-1]))
                self.assertNotIn(FIXTURE_KEY, output.getvalue())

    def test_failed_search_keeps_saved_configuration_and_returns_failure(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path = root / ".env"
            fixture_env(path)
            setup.save_settings(path, {"WEBIQ_API_KEY": FIXTURE_KEY})
            output = io.StringIO()
            with (
                patch.object(setup, "ROOT", root),
                patch.object(setup.subprocess, "run", return_value=Mock(returncode=0)),
                patch.object(setup, "AzureCliCredential"),
                patch.object(setup, "check_targets", return_value={"knowledgeSources": [{"name": "schemes"}]}),
                patch.object(setup, "configure_webiq"),
                patch.object(setup, "check_webiq_search"),
                patch.object(setup, "check_knowledge_base_search", side_effect=RuntimeError(FIXTURE_KEY)),
                redirect_stdout(output), redirect_stderr(output),
            ):
                self.assertEqual(setup.main([]), 1)
            self.assertEqual(setup.load_configuration(path)["WEBIQ_API_KEY"], FIXTURE_KEY)
            self.assertIn("WEBIQ_CONNECTION_ID=", path.read_text())
            self.assertIn("testing the knowledge-base", output.getvalue())
            self.assertNotIn(FIXTURE_KEY, output.getvalue())

    def test_force_without_saved_key_stops_without_prompt_or_configuration_changes(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path = root / ".env"
            fixture_env(path)
            before = path.read_bytes()
            output = io.StringIO()
            with (
                patch.object(setup, "ROOT", root),
                patch.object(setup.subprocess, "run", return_value=Mock(returncode=0)),
                patch.object(setup, "AzureCliCredential"),
                patch.object(setup, "check_targets", return_value={"knowledgeSources": [{"name": "schemes"}]}),
                patch.object(setup, "capture_key") as capture,
                patch.object(setup, "configure_webiq") as configure,
                redirect_stdout(output), redirect_stderr(output),
            ):
                self.assertEqual(setup.main(["--force"]), 1)
            capture.assert_not_called()
            configure.assert_not_called()
            self.assertEqual(path.read_bytes(), before)
            self.assertIn("No saved WEBIQ_API_KEY", output.getvalue())

    def test_tracked_or_unignored_env_stops_before_prompt_or_azure_requests(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path = root / ".env"
            fixture_env(path)
            before = path.read_bytes()
            with (
                patch.object(setup, "ROOT", root),
                patch.object(setup.subprocess, "run", return_value=Mock(returncode=1)),
                patch.object(setup, "check_targets") as targets,
                patch.object(setup, "capture_key") as capture,
                redirect_stderr(io.StringIO()),
            ):
                self.assertEqual(setup.main([]), 1)
            targets.assert_not_called()
            capture.assert_not_called()
            self.assertEqual(path.read_bytes(), before)

    def test_research_example_matches_case_evidence_without_customer_data_in_query(self):
        data = json.loads((setup.ROOT / "data/cases.json").read_text())
        cases = {case["alert"]["id"]: case for case in data["cases"]}
        for example in data["public_research_examples"]:
            case = cases[example["alert_id"]]
            query = example["public_query"]
            for private_value in (
                case["alert"]["id"], case["customer"]["name"], case["customer"]["id"],
                case["customer"]["account_number"], case["customer"]["date_of_birth"],
            ):
                self.assertNotIn(private_value, query)
            self.assertEqual(case["alert"]["rule"], "mule_pattern")
            self.assertTrue(any("remote payment-assistant" in doc["content"] for doc in case["documents"]))
            self.assertTrue(any(
                transaction["direction"] == "out" and transaction["counterparty_type"] == "business"
                for transaction in case["transactions"]
            ))


@pytest.fixture
def webiq_main(tmp_path, monkeypatch):
    path = tmp_path / ".env"
    fixture_env(path)
    monkeypatch.setattr(setup, "ROOT", tmp_path)
    monkeypatch.setattr(setup.subprocess, "run", Mock(return_value=Mock(returncode=0)))
    monkeypatch.setattr(setup, "AzureCliCredential", MagicMock())
    monkeypatch.setattr(setup, "check_targets", Mock(return_value={
        "knowledgeSources": [{"name": "schemes"}],
    }))
    configure = Mock()
    monkeypatch.setattr(setup, "configure_webiq", configure)
    return path, configure


def test_hidden_prompt_retries_malformed_keys_without_echoing_them(monkeypatch, capsys):
    monkeypatch.setattr(setup.sys.stdin, "isatty", lambda: True)
    browser = Mock()
    monkeypatch.setattr(setup.webbrowser, "open", browser)
    hidden = Mock(side_effect=["", "wrong key with spaces", "\x00private", FIXTURE_KEY])
    monkeypatch.setattr(setup.getpass, "getpass", hidden)
    assert setup.capture_key(FIXTURE_ENV) == FIXTURE_KEY
    assert hidden.call_count == 4
    browser.assert_called_once()
    output = capsys.readouterr()
    assert output.err.count("Try again") == 3
    assert "wrong key" not in output.err
    assert FIXTURE_KEY not in output.out + output.err


@pytest.mark.parametrize("saved", [False, True])
def test_interactive_setup_retries_rejected_keys_before_saving_or_configuring(
    webiq_main, monkeypatch, capsys, saved,
):
    path, configure = webiq_main
    if saved:
        setup.save_settings(path, {"WEBIQ_API_KEY": "rejected-saved-key"})
    monkeypatch.setattr(setup.sys.stdin, "isatty", lambda: True)
    capture = Mock(side_effect=(["rejected-paste", FIXTURE_KEY] if saved else [
        "first-rejected-paste", "rejected-paste", FIXTURE_KEY,
    ]))
    monkeypatch.setattr(setup, "capture_key", capture)
    checks = Mock(side_effect=[
        setup.KeyValidationError("Web IQ rejected the key."),
        setup.KeyValidationError("Web IQ rejected the key."),
        None,
    ])
    monkeypatch.setattr(setup, "check_webiq_search", checks)

    def configure_with_accepted_key(settings, key, *args):
        assert key == FIXTURE_KEY
        assert setup.load_configuration(path)["WEBIQ_API_KEY"] == FIXTURE_KEY
        assert "rejected" not in path.read_text()

    configure.side_effect = configure_with_accepted_key
    assert setup.main(["--no-run"]) == 0
    assert capture.call_count == (2 if saved else 3)
    assert checks.call_count == 3
    assert all(call.kwargs == {"validate_only": True} for call in checks.call_args_list)
    configure.assert_called_once()
    output = capsys.readouterr()
    assert output.err.count("Paste another key") == 2
    assert FIXTURE_KEY not in output.out + output.err
    assert "rejected-paste" not in output.out + output.err


def test_ctrl_c_cancels_key_retry_without_saving_or_configuring(webiq_main, monkeypatch, capsys):
    path, configure = webiq_main
    before = path.read_bytes()
    monkeypatch.setattr(setup.sys.stdin, "isatty", lambda: True)
    monkeypatch.setattr(setup.webbrowser, "open", Mock())
    hidden = Mock(side_effect=["bad key", "rejected-key", KeyboardInterrupt])
    monkeypatch.setattr(setup.getpass, "getpass", hidden)
    monkeypatch.setattr(setup, "check_webiq_search", Mock(side_effect=setup.KeyValidationError("Key rejected.")))
    assert setup.main([]) == 1
    assert hidden.call_count == 3
    assert path.read_bytes() == before
    configure.assert_not_called()
    assert "cancelled" in capsys.readouterr().err


@pytest.mark.parametrize("arguments,interactive", [
    ([], False), (["--use-saved-key"], True), (["--force"], True),
])
def test_automated_saved_key_failures_do_not_prompt(webiq_main, monkeypatch, arguments, interactive):
    path, configure = webiq_main
    setup.save_settings(path, {"WEBIQ_API_KEY": FIXTURE_KEY})
    before = path.read_bytes()
    monkeypatch.setattr(setup.sys.stdin, "isatty", lambda: interactive)
    capture = Mock()
    monkeypatch.setattr(setup, "capture_key", capture)
    monkeypatch.setattr(setup, "check_webiq_search", Mock(side_effect=setup.KeyValidationError("Key rejected.")))
    assert setup.main(arguments) == 1
    capture.assert_not_called()
    configure.assert_not_called()
    assert path.read_bytes() == before


@pytest.mark.parametrize("failure_stage", ["key-check", "azure-configuration"])
def test_unrelated_failures_do_not_trigger_key_retry(webiq_main, monkeypatch, failure_stage):
    _, configure = webiq_main
    monkeypatch.setattr(setup.sys.stdin, "isatty", lambda: True)
    capture = Mock(return_value=FIXTURE_KEY)
    monkeypatch.setattr(setup, "capture_key", capture)
    check = Mock()
    monkeypatch.setattr(setup, "check_webiq_search", check)
    failure = setup.SetupError("Service access failed.")
    if failure_stage == "key-check":
        check.side_effect = failure
    else:
        configure.side_effect = failure
    assert setup.main([]) == 1
    capture.assert_called_once()
    check.assert_called_once_with(FIXTURE_KEY, validate_only=True)
    assert configure.call_count == int(failure_stage == "azure-configuration")


def test_key_preflight_checks_access_without_searching(monkeypatch):
    opener = Mock()
    opener.open.side_effect = mcp_responses({}, "application/json")[:3]
    monkeypatch.setattr(setup.urllib.request, "build_opener", Mock(return_value=opener))
    setup.check_webiq_search(FIXTURE_KEY, validate_only=True)
    methods = [json.loads(call.args[0].data)["method"] for call in opener.open.call_args_list]
    assert methods == ["initialize", "notifications/initialized", "tools/list"]


@pytest.mark.parametrize("status", [401, 403, 429, 500])
def test_only_direct_webiq_auth_failures_are_retryable(monkeypatch, status):
    opener = Mock()
    opener.open.side_effect = urllib.error.HTTPError(
        setup.MCP_ENDPOINT, status, FIXTURE_KEY, {}, io.BytesIO(FIXTURE_KEY.encode()),
    )
    monkeypatch.setattr(setup.urllib.request, "build_opener", Mock(return_value=opener))
    with pytest.raises(setup.SetupError) as raised:
        setup.check_webiq_search(FIXTURE_KEY, validate_only=True)
    assert isinstance(raised.value, setup.KeyValidationError) == (status in (401, 403))
    assert FIXTURE_KEY not in str(raised.value)


def test_key_without_web_access_is_retryable(monkeypatch):
    responses = mcp_responses({}, "application/json")[:2]
    response = io.BytesIO(json.dumps({"id": 2, "result": {"tools": []}}).encode())
    response.headers = {"Content-Type": "application/json"}
    opener = Mock()
    opener.open.side_effect = [*responses, response]
    monkeypatch.setattr(setup.urllib.request, "build_opener", Mock(return_value=opener))
    with pytest.raises(setup.KeyValidationError, match="does not expose the web tool"):
        setup.check_webiq_search(FIXTURE_KEY, validate_only=True)


if __name__ == "__main__":
    unittest.main()
