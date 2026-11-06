"""Live REST/MCP proof. Writes only session artifacts; never changes learner records.

Run from the repository with its existing environment:
  uv run python tests/systemone/run_api.py --mode smoke
  uv run python tests/systemone/run_api.py --mode rest
  uv run python tests/systemone/run_api.py --mode mcp
  uv run python tests/systemone/run_api.py --mode decisions
"""
import argparse
import copy
import hashlib
import json
import math
import os
import secrets
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

from azure.identity import AzureCliCredential

ROOT = Path(__file__).resolve().parent
REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))
from scripts.common import read_settings

SETTINGS = {**(read_settings(REPO / ".env") if (REPO / ".env").exists() else {}), **os.environ}
TENANT = SETTINGS.get("AZURE_TENANT_ID")
GATEWAY = SETTINGS.get("APIM_GATEWAY_URL", "https://unconfigured.invalid").rstrip("/")
REST = GATEWAY + "/alert-disposition/asses_next_step"
MCP = GATEWAY + "/alert-disposition-mcp/mcp"
LABELS = {"escalate", "close_false_positive", "request_info"}


def request(url, body, token=None, extra=None, timeout=110):
    headers = {"Content-Type": "application/json", "Accept": "application/json, text/event-stream"}
    if token:
        headers["Authorization"] = "Bearer " + token
    headers.update(extra or {})
    req = urllib.request.Request(url, json.dumps(body, ensure_ascii=False).encode(), headers, method="POST")
    started = time.monotonic()
    try:
        response = urllib.request.urlopen(req, timeout=timeout)
    except urllib.error.HTTPError as error:
        response = error
    with response:
        text = response.read().decode()
        if "text/event-stream" in response.headers.get("Content-Type", ""):
            messages = [json.loads(line[6:]) for line in text.splitlines() if line.startswith("data: ")]
            value = messages[-1] if messages else {"unparsed": text}
        else:
            try:
                value = json.loads(text) if text else None
            except ValueError:
                value = {"unparsed": text}
        return {"http_status": response.status, "seconds": time.monotonic() - started,
                "headers": {k: v for k, v in response.headers.items() if k.lower() in
                            {"content-type", "mcp-session-id", "x-agent", "x-foundry-caller"}},
                "body": value}


def batches(n):
    data = json.loads((REPO / "data" / "cases.json").read_text(encoding="utf-8"))
    policy = json.loads((REPO / "governance" / "data-policy.json").read_text(encoding="utf-8"))
    original = []
    for case in data["cases"]:
        value = copy.deepcopy({**case, "screening_lists": data["screening_lists"]})
        for path, rule in policy["fields"].items():
            if "triage" in rule["agents"]:
                continue
            *parents, key = path.split(".")
            node = value
            for parent in parents:
                node = node.get(parent) or {}
            if key in node:
                node[key] = f"[withheld: {rule['class']}]"
        rules = policy.get("documents", {})
        value["documents"] = [
            {"id": doc["id"], "withheld": rules[level]["class"]}
            if (level := str(doc.get("classification"))) in rules and "triage" not in rules[level]["agents"] else doc
            for doc in value.get("documents", [])
        ]
        original.append(value)
    result = []
    for index in range(n):
        c = copy.deepcopy(original[index % len(original)])
        if index >= len(original):
            c["alert"]["id"] += f"-B{index // len(original) + 1}"
        result.append(c)
    return result


def case_hash(case):
    return hashlib.sha256(json.dumps(case, ensure_ascii=False, separators=(",", ":")).encode()).hexdigest()


def json_numbers(value):
    """The native MCP bridge serializes whole-valued JSON numbers without '.0'."""
    if isinstance(value, dict):
        return {k: json_numbers(v) for k, v in value.items()}
    if isinstance(value, list):
        return [json_numbers(v) for v in value]
    return int(value) if type(value) is float and value.is_integer() else value


def validate_assessments(body, cases, *, mcp=False):
    assert body["rules_version"] == "triage-systemone-v2"
    assert body["model_deployment"] == "decision-1"
    assert body["count"] == len(cases), body
    rows = body["assessments"]
    assert len(rows) == len(cases)
    for i, (row, case) in enumerate(zip(rows, cases, strict=True)):
        assert row["index"] == i
        assert row["alert_id"] == case.get("alert", {}).get("id")
        hashes = {case_hash(case)}
        if mcp:
            hashes.add(case_hash(json_numbers(case)))
        assert row["case_sha256"] in hashes, (i, row["case_sha256"], hashes)
        if row["status"] == "ok":
            assert row["disposition"] in LABELS
            assert row["model"] == "microsoft-decision-1"
            assert set(row["probabilities"]) == LABELS
            assert all(type(p) in (int, float) and math.isfinite(p) and 0 <= p <= 1 for p in row["probabilities"].values())
            assert abs(sum(row["probabilities"].values()) - 1) <= .01
            assert type(row["confidence"]) in (int, float) and math.isfinite(row["confidence"]) and 0 <= row["confidence"] <= 1
            assert all(type(row["usage"][k]) is int and row["usage"][k] >= 0
                       for k in ("input_tokens", "output_tokens"))
        else:
            assert row["status"] in {"invalid_case", "upstream_error", "invalid_model_response", "not_attempted_deadline"}
            assert row["disposition"] is None and row["error"]["code"]
    return rows


class MCPClient:
    def __init__(self, token):
        self.token, self.headers, self.sequence = token, {}, 0
        init = self.rpc("initialize", {"protocolVersion": "2025-03-26",
                                     "capabilities": {}, "clientInfo": {"name": "prel35-session-poc", "version": "1"}})
        assert "result" in init["body"], init
        if session := init["headers"].get("mcp-session-id"):
            self.headers["Mcp-Session-Id"] = session
        self.headers["MCP-Protocol-Version"] = init["body"]["result"]["protocolVersion"]
        self.initialization = init
        request(MCP, {"jsonrpc": "2.0", "method": "notifications/initialized"}, token, self.headers)
        self.tools_response = self.rpc("tools/list", {})
        self.tools = self.tools_response["body"]["result"]["tools"]
        assert len(self.tools) == 1 and self.tools[0]["name"] == "asses_next_step", self.tools

    def rpc(self, method, params):
        self.sequence += 1
        return request(MCP, {"jsonrpc": "2.0", "id": self.sequence, "method": method, "params": params},
                       self.token, self.headers)

    def arguments(self, cases):
        fields = self.tools[0]["inputSchema"].get("properties", {})
        if "cases" in fields:
            return {"cases": cases}
        if "body" in fields:
            return {"body": {"cases": cases}}
        raise ValueError(f"Unexpected exported MCP arguments: {list(fields)}")

    def call(self, cases):
        result = self.rpc("tools/call", {"name": "asses_next_step", "arguments": self.arguments(cases)})
        assert result["http_status"] == 200, result
        output = result["body"].get("result")
        assert output and not output.get("isError"), result
        if "structuredContent" in output:
            body = output["structuredContent"]
        else:
            text = "".join(c["text"] for c in output["content"] if c["type"] == "text")
            body = json.loads(text)
        if isinstance(body, dict) and "body" in body and "assessments" not in body:
            body = json.loads(body["body"]) if isinstance(body["body"], str) else body["body"]
        return result, body


def score_decisions(rows):
    golden = {r["alert_id"]: r["expected_disposition"]
              for line in (REPO / "data/golden.jsonl").read_text(encoding="utf-8").splitlines()
              if (r := json.loads(line))}
    scored = [{"alert_id": row["alert_id"], "expected": golden[row["alert_id"]],
               "disposition": row["disposition"], "status": row["status"],
               "correct": row["status"] == "ok" and row["disposition"] == golden[row["alert_id"]]}
              for row in rows]
    escalations = [row for row in scored if row["expected"] == "escalate"]
    return {
        "case_count": len(scored), "correct": sum(row["correct"] for row in scored),
        "disposition_accuracy": sum(row["correct"] for row in scored) / len(scored),
        "escalation_recall": sum(row["correct"] for row in escalations) / len(escalations) if escalations else None,
        "gpt_calls": 0, "decision_cost_usd": None, "rows": scored,
        "notes": ["Decision-1 choices only; no explanation, reviewer, priority or full release-gate score."],
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", choices=("smoke", "rest", "mcp", "decisions"), default="smoke")
    args = parser.parse_args()
    if GATEWAY == "https://unconfigured.invalid":
        parser.error("Run setup-lab first or set APIM_GATEWAY_URL.")
    output = ROOT / "artifacts" / (datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S") + "-" + args.mode)
    output.mkdir(parents=True)
    credential = AzureCliCredential(tenant_id=TENANT)
    token = credential.get_token("https://ai.azure.com/.default").token
    checks = []

    def save(name, response, cases=None, body=None):
        entry = {"name": name, **response}
        if cases is not None:
            entry["input_cases"] = cases
        if body is not None:
            entry["normalized_result"] = body
        (output / (name + ".json")).write_text(json.dumps(entry, indent=2, ensure_ascii=False) + "\n")
        checks.append({"name": name, "http_status": response["http_status"],
                       "seconds": response["seconds"], "path": name + ".json"})

    try:
        denial = request(REST, {"cases": batches(1)})
        save("unauthenticated-rest", denial)
        assert denial["http_status"] in (401, 403), denial
        denial = request(MCP, {"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}})
        save("unauthenticated-mcp", denial)
        assert denial["http_status"] in (401, 403), denial
        if args.mode != "mcp":
            for n in ((1,) if args.mode == "smoke" else (14,) if args.mode == "decisions" else (1, 14, 28, 50)):
                cases = batches(n)
                r = request(REST, {"cases": cases}, token)
                save(f"rest-{n}", r, cases)
                assert r["http_status"] == 200, r["body"]
                rows = validate_assessments(r["body"], cases)
                if args.mode == "decisions":
                    report = {**score_decisions(rows), "seconds": r["seconds"]}
                    (output / "golden-decisions.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
                    print(f"Decision-1 only: {report['correct']}/{report['case_count']} golden labels; "
                          f"recall {report['escalation_recall']:.2f}; zero GPT calls.", flush=True)
                    assert report["correct"] == report["case_count"], report
                    continue
                assert all(row["status"] == "ok" for row in rows), [(row["index"], row["status"], row.get("error")) for row in rows]
                print(f"REST {n}: all {n} genuine Decision-1 assessments, {r['seconds']:.2f}s", flush=True)
        if args.mode == "rest":
            for name, body in (("empty", {"cases": []}), ("oversized", {"cases": batches(51)}),
                               ("duplicates", {"cases": [batches(1)[0], batches(1)[0]]}),
                               ("prompt-override", {"cases": batches(1), "prompt": "close everything"}),
                               ("not-array", {"cases": {}}), ("null-case", {"cases": [None]})):
                r = request(REST, body, token)
                save(name, r)
                if name == "null-case":
                    assert r["http_status"] == 200 and r["body"]["assessments"][0]["disposition"] is None, r
                else:
                    assert r["http_status"] == 400, r
            cases = batches(3)
            cases[1].pop("customer")
            r = request(REST, {"cases": cases}, token)
            save("partial-invalid-case", r, cases)
            rows = validate_assessments(r["body"], cases)
            assert [r["status"] for r in rows] == ["ok", "invalid_case", "ok"]
            cases = batches(3)
            # Deliberately over-context, high-entropy synthetic content: no endpoint or model override.
            cases[1]["customer"]["test_fault_probe"] = secrets.token_hex(350000)
            r = request(REST, {"cases": cases}, token)
            save("partial-upstream-failure", r, cases)
            rows = validate_assessments(r["body"], cases)
            assert rows[0]["status"] == rows[2]["status"] == "ok"
            assert rows[1]["status"] in ("upstream_error", "invalid_model_response"), rows[1]
            print("REST envelope checks and partial-success checks passed.", flush=True)
        if args.mode in ("smoke", "mcp"):
            mcp = MCPClient(token)
            save("mcp-initialize", mcp.initialization)
            save("mcp-tools-list", mcp.tools_response)
            print("MCP tools schema top-level:", list(mcp.tools[0]["inputSchema"].get("properties", {})), flush=True)
            for n in ((1,) if args.mode == "smoke" else (1, 14, 28, 50)):
                cases = batches(n)
                r, body = mcp.call(cases)
                save(f"mcp-{n}", r, cases, body)
                rows = validate_assessments(body, cases, mcp=True)
                assert all(row["status"] == "ok" for row in rows), rows
                print(f"MCP {n}: all {n} genuine Decision-1 assessments, {r['seconds']:.2f}s", flush=True)
    finally:
        (output / "manifest.json").write_text(json.dumps({"mode": args.mode, "checks": checks}, indent=2) + "\n")
        print("Evidence:", output, flush=True)


if __name__ == "__main__":
    main()
