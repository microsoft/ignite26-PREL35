"""Optional single-case APIM -> GPT -> MCP -> Decision-1 proof using synthetic fixtures.

Approves only an unchanged read-only tool argument. No learner approvals are written.
Usage: uv run python tests/systemone/run_agent.py [ALT-1001]
"""
import argparse
import json
import re
import time
from datetime import datetime, timezone
from pathlib import Path

from azure.identity import AzureCliCredential
from openai import OpenAI

from run_api import GATEWAY, TENANT, batches, json_numbers, validate_assessments

ROOT = Path(__file__).resolve().parent
AGENT_URL = GATEWAY + "/agents/triage_systemone"
IDS = re.compile(r"\b[A-Z]{1,4}-\d{3,5}(?:-[A-Z0-9]+)*\b")


def approved_arguments(item, case):
    if item.server_label != "disposition" or item.name != "asses_next_step":
        raise ValueError("Unexpected MCP server or tool; no approval.")
    arguments = json.loads(item.arguments)
    if json.dumps(json_numbers(arguments), sort_keys=True, allow_nan=False) != json.dumps(
            json_numbers({"cases": [case]}), sort_keys=True, allow_nan=False):
        raise ValueError("Tool must receive exactly the one unchanged input case; no approval.")
    return arguments


def completed_response(client, **kwargs):
    response = None
    with client.responses.create(**kwargs, stream=True) as stream:
        for event in stream:
            if event.type == "response.completed":
                response = event.response
            elif event.type in ("response.failed", "response.incomplete"):
                raise ValueError(f"Agent response did not complete: {event.type}")
    if response is None:
        raise ValueError("No completed agent response.")
    return response


def record_ids(value):
    if isinstance(value, dict):
        return {v for k, v in value.items() if k == "id" and isinstance(v, str)} | {
            item for v in value.values() for item in record_ids(v)}
    if isinstance(value, list):
        return {item for v in value for item in record_ids(v)}
    return set()


def unpack(output):
    if isinstance(output, str):
        output = json.loads(output)
    if isinstance(output, dict):
        if "assessments" in output:
            return output
        if "structuredContent" in output:
            return unpack(output["structuredContent"])
        if "content" in output:
            return unpack("".join(c["text"] for c in output["content"] if c["type"] == "text"))
    raise ValueError("No actual decision assessments in the MCP output.")


def validate_explanation(text, case, assessment):
    explanation = json.loads(text)
    assert set(explanation) == {"disposition", "reasons", "gaps", "conflicts"}
    assert explanation["disposition"] == assessment["disposition"], "GPT changed the service disposition."
    valid_ids = record_ids(case)
    for key in ("reasons", "gaps", "conflicts"):
        assert isinstance(explanation[key], list) and all(isinstance(s, str) for s in explanation[key])
        for sentence in explanation[key]:
            assert not set(IDS.findall(sentence)) - valid_ids, "Wrong-case citation."
            citations = set(re.findall(r"\[([^\[\]]+)\]", sentence))
            assert not citations - valid_ids, "Unknown citation."
            if key == "reasons":
                assert citations, "Each reason must cite a supporting record."
    if assessment["status"] == "ok":
        assert explanation["reasons"], "A successful decision needs reasons."
    else:
        assert explanation["disposition"] is None and not explanation["reasons"] and explanation["gaps"]
    return {**explanation, "alert_id": assessment["alert_id"], "assessment": assessment}


def run(case, output):
    token = AzureCliCredential(tenant_id=TENANT).get_token("https://ai.azure.com/.default").token
    responses = []
    started = time.monotonic()
    with OpenAI(base_url=AGENT_URL, api_key=token, timeout=180, max_retries=0,
                default_headers={"x-cost-center": "systemone-synthetic-tests"}) as client:
        try:
            first = completed_response(client,
                                       input=json.dumps(case) + "\n\nReturn JSON.", max_output_tokens=8192)
            responses.append(first.model_dump(mode="json"))
            approvals = [item for item in first.output if item.type == "mcp_approval_request"]
            assert len(approvals) == 1, ("Expected one real tool approval", first.output_text)
            approval = approvals[0]
            arguments = approved_arguments(approval, case)
            final = completed_response(client, previous_response_id=first.id, max_output_tokens=8192,
                                       input=[{"type": "mcp_approval_response", "approval_request_id": approval.id, "approve": True}])
            responses.append(final.model_dump(mode="json"))
            calls = [item for response in responses for item in response["output"] if item["type"] == "mcp_call"]
            assert len(calls) == 1 and not calls[0].get("error"), calls
            assert calls[0]["server_label"] == "disposition" and calls[0]["name"] == "asses_next_step"
            assert json.loads(calls[0]["arguments"]) == arguments
            tool_result = unpack(calls[0]["output"])
            assessment = validate_assessments(tool_result, arguments["cases"], mcp=True)[0]
            for attempt in range(3):
                try:
                    accepted = validate_explanation(final.output_text, case, assessment)
                    break
                except (AssertionError, ValueError) as error:
                    if attempt == 2:
                        raise
                    final = completed_response(client, previous_response_id=final.id, max_output_tokens=8192,
                        input="Repair only the explanation JSON: " + str(error)
                        + ". Use the actual assessment and original case already in this response chain. "
                        "Do not call tools or change the disposition. Cite only actual supporting record IDs in square brackets.")
                    responses.append(final.model_dump(mode="json"))
                    assert not any(i.type in ("mcp_call", "mcp_approval_request") for i in final.output), "Repair attempted reassessment."
            result = {
                "case_count": 1, "input_case": case, "accepted": accepted,
                "tool_result": tool_result, "executed_mcp_calls": 1, "exact_input_forwarding": True,
                "synthetic_test_approval": True, "seconds": time.monotonic() - started,
                "explanation_repairs": len(responses) - 2,
            }
            (output / "verified-result.json").write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
            print(f"{case['alert']['id']}: {accepted['disposition']}, one real MCP call, "
                  f"unchanged case, {result['seconds']:.2f}s.", flush=True)
            return result
        finally:
            (output / "responses.json").write_text(json.dumps(responses, indent=2) + "\n", encoding="utf-8")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("alert_id", nargs="?", default="ALT-1001")
    args = parser.parse_args(argv)
    cases = {case["alert"]["id"]: case for case in batches(14)}
    if args.alert_id not in cases:
        parser.error("Choose an existing synthetic alert ID.")
    output = ROOT / "artifacts" / (datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S-%f") + "-agent-" + args.alert_id)
    output.mkdir(parents=True)
    try:
        run(cases[args.alert_id], output)
    finally:
        print("Evidence:", output)


if __name__ == "__main__":
    main()
