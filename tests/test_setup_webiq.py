import copy
import io
import json
import os
import tempfile
import unittest
import urllib.error
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest.mock import Mock, patch

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


class WebIqSetupTests(unittest.TestCase):
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
                    web_search.assert_not_called()
                    kb_search.assert_not_called()
                else:
                    web_search.assert_called_once_with(FIXTURE_KEY)
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


if __name__ == "__main__":
    unittest.main()
