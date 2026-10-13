"""Calls the Foundry project REST API with your Entra ID token. Mirrors Foundry.cs.

The runner calls Foundry directly rather than through the API Management gateway, because the gateway always
uses an agent's latest version and an evaluation must pin exact versions.
"""
import time
from urllib.parse import quote, urlsplit

import requests
from azure.identity import DefaultAzureCredential

from checks import text

SCOPE = "https://ai.azure.com/.default"


def remaining_seconds(deadline):
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise TimeoutError("Foundry scoring took longer than the time limit. Its known IDs are in the Evidence Pack.")
    return remaining


class Foundry:
    def __init__(self, endpoint, credential=None, session=None):
        """Connects to the project. Tests can pass their own credential and HTTP session."""
        url = urlsplit(endpoint)
        if url.scheme != "https" or not url.hostname or url.username or url.query or url.fragment:
            raise ValueError("FOUNDRY_PROJECT_ENDPOINT must be an HTTPS URL without credentials, query, or fragment.")
        self.endpoint = endpoint.rstrip("/") + "/"
        self.credential = credential or DefaultAzureCredential()
        self.session = session or requests.Session()

    def get_agent_version(self, agent, version):
        """Reads a published agent version, and checks that it's a prompt agent with a model and instructions.

        Returns the model deployment and the instructions.
        """
        body, _, _ = self.send("GET", f"agents/{agent}/versions/{quote(version, safe='')}?api-version=v1")
        definition = body.get("definition") if isinstance(body.get("definition"), dict) else {}
        model, instructions = text(definition.get("model")), text(definition.get("instructions"))
        if text(body.get("version")) != version or definition.get("kind") != "prompt" or not model or not instructions:
            raise RuntimeError(f"Agent {agent} version {version} isn't a prompt agent with a model and instructions.")
        return model, instructions

    def ask(self, agent, version, input_text):
        """Sends one input to an exact agent version and returns its answer, with IDs and duration."""
        request = {"agent_reference": {"type": "agent_reference", "name": agent, "version": version},
                   "input": input_text, "stream": False}
        body, request_id, duration_ms = self.send("POST", "openai/v1/responses", request)
        if body.get("status") != "completed":
            raise RuntimeError(f"Agent {agent} didn't complete its response.")

        # The answer is the text of the message items. Other items, such as reasoning, are skipped.
        answer = "".join(text(part.get("text"))
                         for item in body.get("output") or [] if isinstance(item, dict) and item.get("type") == "message"
                         for part in item.get("content") or [] if isinstance(part, dict) and part.get("type") == "output_text")
        if not answer.strip():
            raise RuntimeError(f"Agent {agent} returned no text.")
        return {"text": answer, "response_id": text(body.get("id")), "request_id": request_id, "duration_ms": duration_ms}

    def get(self, path, *, deadline=None):
        return self.send("GET", path, deadline=deadline)[0]

    def post(self, path, body, *, deadline=None):
        return self.send("POST", path, body, deadline=deadline)[0]

    def send(self, method, path, body=None, *, deadline=None):
        """Sends one request with a fresh token, and returns the JSON body, request ID and duration."""
        if deadline is not None:
            remaining_seconds(deadline)
        token = self.credential.get_token(SCOPE).token
        # Authentication also consumes the scoring budget. Requests limits connection/read waits, not total time;
        # managed_scoring checks the deadline again before accepting results.
        timeout = 300 if deadline is None else min(300, remaining_seconds(deadline))
        started = time.perf_counter()
        response = self.session.request(method, self.endpoint + path, json=body, timeout=timeout,
                                        headers={"Authorization": f"Bearer {token}"})
        duration_ms = (time.perf_counter() - started) * 1000

        # Foundry returns a request ID that support can use to find the call.
        request_id = response.headers.get("apim-request-id")
        if not response.ok:
            raise RuntimeError(f"Foundry returned HTTP {response.status_code} for {method} {path}; "
                               f"request ID: {request_id or 'none'}.")
        result = response.json()
        if not isinstance(result, dict):
            raise RuntimeError(f"Foundry returned something other than a JSON object for {path}.")
        return result, request_id, duration_ms
