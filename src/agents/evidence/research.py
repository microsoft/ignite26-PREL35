import asyncio
import json
import time
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlsplit


TOPICS = json.loads(Path(__file__).with_name("topics.json").read_text(encoding="utf-8"))
SEARCH_SCOPE = "https://search.azure.com/.default"
OFFICIAL_DOMAINS = ("justice.gov", "fincen.gov")


@dataclass
class ResearchBudget:
    calls: int = 0
    deadline: float = field(default_factory=lambda: time.monotonic() + 180)
    results: dict = field(default_factory=dict)


def official_url(value):
    if not isinstance(value, str):
        return False
    try:
        parsed = urlsplit(value)
        port = parsed.port
    except ValueError:
        return False
    return (
        parsed.scheme == "https"
        and not parsed.username
        and not parsed.password
        and port in (None, 443)
        and any(parsed.hostname == domain or (parsed.hostname or "").endswith("." + domain)
                for domain in OFFICIAL_DOMAINS)
    )


def extract_references(result, topic, phase="research"):
    if result.isError:
        return []
    references = []
    seen = set()
    for block in result.content:
        if block.type != "text" or not block.text or len(block.text) > 1_000_000:
            continue
        try:
            reference = json.loads(block.text)
        except (ValueError, TypeError):
            continue
        if not isinstance(reference, dict) or reference.get("kind") != "reference":
            continue
        source = reference.get("sourceData")
        if not isinstance(source, dict):
            continue
        if source.get("is_synthetic") is True:
            if source.get("approved_topic_id") and source["approved_topic_id"] != topic:
                continue
            kind = "synthetic_note"
            url = source.get("source_url")
            title = source.get("title")
            excerpt = source.get("content")
        else:
            try:
                page = json.loads(source.get("content", ""))
            except (ValueError, TypeError):
                continue
            if not isinstance(page, dict):
                continue
            kind = "public_web"
            url, title, excerpt = page.get("url"), page.get("title"), page.get("content")
        if not official_url(url) or not isinstance(title, str) or not isinstance(excerpt, str) or not excerpt:
            continue
        identity = (kind, url, title)
        if identity in seen or sum(r["kind"] == kind for r in references) >= 4:
            continue
        seen.add(identity)
        references.append({
            "citation_id": f"{topic}:{phase}:{reference['ref_id']}",
            "kind": kind,
            "title": title[:300],
            "url": url,
            "excerpt": excerpt[:2000],
        })
    return references


class EvidenceResearch:
    def __init__(self, endpoint, credential):
        parsed = urlsplit(endpoint)
        if (
            parsed.scheme != "https"
            or not (parsed.hostname or "").endswith(".search.windows.net")
            or parsed.username or parsed.password or parsed.port not in (None, 443)
            or not parsed.path.startswith("/knowledgebases/") or not parsed.path.endswith("/mcp")
            or parsed.query != "api-version=2026-08-01-preview" or parsed.fragment
        ):
            raise ValueError("Evidence research requires the configured Azure Search knowledge-base MCP endpoint.")
        self.endpoint = endpoint
        self.credential = credential

    async def retrieve(self, question):
        from agent_framework import MCPStreamableHTTPTool

        async with MCPStreamableHTTPTool(
            name="evidence-knowledge-base",
            url=self.endpoint,
            load_tools=False,
            load_prompts=False,
            request_timeout=65,
            header_provider=lambda _: {
                "Authorization": "Bearer " + self.credential.get_token(SEARCH_SCOPE).token,
            },
        ) as mcp:
            # Use the session directly: the framework's call_tool wrapper retries disconnected calls.
            return await mcp.session.call_tool(
                "knowledge_base_retrieve", arguments={"query_variants": [question]},
            )

    async def lookup(self, topic, budget):
        if topic not in TOPICS:
            return json.dumps({"status": "unavailable", "reason": "Unsupported research topic."})
        if not isinstance(budget, ResearchBudget):
            return json.dumps({"status": "unavailable", "reason": "Research budget is unavailable."})
        if topic in budget.results:
            return budget.results[topic]
        references, failures = [], []
        seen = set()
        for phase, question in TOPICS[topic].items():
            remaining = budget.deadline - time.monotonic()
            if budget.calls >= 4 or remaining <= 0:
                failures.append("research_limit")
                break
            budget.calls += 1
            try:
                async with asyncio.timeout(min(70, remaining)):
                    raw = await self.retrieve(question)
                    if raw.isError:
                        failures.append("source_error")
                    for reference in extract_references(raw, topic, phase):
                        identity = (reference["kind"], reference["url"], reference["title"])
                        if identity in seen or sum(r["kind"] == reference["kind"] for r in references) >= 4:
                            continue
                        references.append(reference)
                        seen.add(identity)
            except asyncio.CancelledError:
                raise
            except Exception as error:
                failures.append(type(error).__name__)
        kinds = {r["kind"] for r in references}
        status = "complete" if len(kinds) == 2 and not failures else "partial" if references else "unavailable"
        result = {
            "topic": topic,
            "status": status,
            "references": references,
            "reason_codes": failures,
            "limitations": (
                "Public context and synthetic notes are not evidence about the supplied scenario."
                if status == "complete" else
                "Research is incomplete. Missing, failed or excluded sources must not be treated as checked."
            ),
        }
        budget.results[topic] = json.dumps(result)
        return budget.results[topic]
