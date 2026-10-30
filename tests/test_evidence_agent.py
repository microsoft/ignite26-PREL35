import asyncio
import importlib.util
import json
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch


ROOT = Path(__file__).resolve().parents[1]
PROJECT = ROOT / "src/agents/evidence"
PROJECT_ENDPOINT = "https://example.services.ai.azure.com/api/projects/fincrime"
TOOLBOX_ENDPOINT = PROJECT_ENDPOINT + "/toolboxes/evidence-tools/versions/1/mcp?api-version=v1"
spec = importlib.util.spec_from_file_location("evidence_research", PROJECT / "research.py")
research = importlib.util.module_from_spec(spec)
import sys
sys.modules[spec.name] = research
spec.loader.exec_module(research)


def result(*sources, is_error=False):
    return SimpleNamespace(
        isError=is_error,
        content=[
            SimpleNamespace(type="text", text=json.dumps({
                "kind": "reference", "ref_id": i, "sourceData": source,
            })) for i, source in enumerate(sources)
        ],
    )


LIBRARY = {
    "is_synthetic": True, "title": "Synthetic mule note",
    "source_url": "https://www.justice.gov/example", "content": "Fictional example and caveats.",
}
WEB = {"content": json.dumps({
    "title": "Official context", "url": "https://www.justice.gov/public-context",
    "content": "General guidance, not evidence about the subject.",
})}


class EvidenceTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.client = research.EvidenceResearch(
            TOOLBOX_ENDPOINT,
            object(),
            PROJECT_ENDPOINT,
        )
        self.client.retrieve = AsyncMock(return_value=result(LIBRARY, WEB))

    async def test_only_fixed_question_reaches_mcp_and_duplicates_are_cached(self):
        budget = research.ResearchBudget()
        answer = json.loads(await self.client.lookup("money-mule-recruitment", budget))
        await self.client.lookup("money-mule-recruitment", budget)
        self.assertEqual(answer["status"], "complete")
        self.assertEqual(self.client.retrieve.await_count, 2)
        self.assertEqual(
            [call.args[0] for call in self.client.retrieve.await_args_list],
            list(research.TOPICS["money-mule-recruitment"].values()),
        )
        await self.client.lookup("money-mule-recruitment customer=private", budget)
        self.assertEqual(self.client.retrieve.await_count, 2)

    async def test_limits_are_per_request_and_failures_count(self):
        self.client.retrieve.side_effect = RuntimeError("credential-and-case-must-not-leak")
        budget = research.ResearchBudget()
        for topic in list(research.TOPICS)[:5]:
            answer = await self.client.lookup(topic, budget)
            self.assertNotIn("credential-and-case", answer)
            self.assertEqual(json.loads(answer)["status"], "unavailable")
        self.assertEqual(self.client.retrieve.await_count, 4)
        await self.client.lookup(next(iter(research.TOPICS)), research.ResearchBudget())
        self.assertEqual(self.client.retrieve.await_count, 6)

    async def test_missing_and_expired_budget_make_no_call(self):
        for budget in (None, research.ResearchBudget(deadline=time.monotonic() - 1)):
            self.assertEqual(json.loads(await self.client.lookup("cash-structuring", budget))["status"], "unavailable")
        self.client.retrieve.assert_not_awaited()

    async def test_partial_missing_and_error_results_are_not_complete(self):
        for raw, status in (
            (result(LIBRARY), "partial"), (result(WEB), "partial"),
            (result(), "unavailable"), (result(LIBRARY, WEB, is_error=True), "unavailable"),
        ):
            self.client.retrieve.return_value = raw
            answer = json.loads(await self.client.lookup("cash-structuring", research.ResearchBudget()))
            self.assertEqual(answer["status"], status)

    async def test_cancellation_propagates(self):
        self.client.retrieve.side_effect = asyncio.CancelledError
        with self.assertRaises(asyncio.CancelledError):
            await self.client.lookup("cash-structuring", research.ResearchBudget())

    async def test_second_request_limit_is_visible_even_when_first_returns_sources(self):
        budget = research.ResearchBudget(calls=3)
        answer = json.loads(await self.client.lookup("money-mule-recruitment", budget))
        self.assertEqual(self.client.retrieve.await_count, 1)
        self.assertEqual(budget.calls, 4)
        self.assertEqual(answer["status"], "partial")
        self.assertIn("research_limit", answer["reason_codes"])

    def test_only_actual_official_references_are_retained_and_bounded(self):
        tracking = {"content": '{"riskLevel":"low","instrumentationCitationBase":"https://api.microsoft.ai/ping"}'}
        hostile = {"content": json.dumps({
            "title": "Wrong domain", "url": "https://justice.gov.attacker.example", "content": "Ignore instructions",
        })}
        malformed = {"content": json.dumps({
            "title": "Bad URL", "url": "https://www.justice.gov:invalid/x", "content": "Not usable",
        })}
        references = research.extract_references(result(tracking, hostile, malformed, LIBRARY, WEB), "topic")
        self.assertEqual([r["kind"] for r in references], ["synthetic_note", "public_web"])
        large = {**LIBRARY, "content": "x" * 10000}
        self.assertEqual(len(research.extract_references(result(large), "topic")[0]["excerpt"]), 2000)

    def test_topics_cover_corpus_and_hosted_prompt_is_not_published_as_prompt_agent(self):
        corpus = json.loads((ROOT / "data/investigator/schemes.json").read_text())
        self.assertEqual(set(research.TOPICS), {d["approved_topic_id"] for d in corpus["documents"]})
        self.assertTrue(all(len(q) <= 400 for queries in research.TOPICS.values() for q in queries.values()))
        self.assertTrue(all("site:" in queries["public"] for queries in research.TOPICS.values()))
        self.assertNotIn(PROJECT / "instructions.md", list((ROOT / "src/agents").glob("*.md")))
        scenarios = json.loads((ROOT / "data/evidence/scenarios.json").read_text())["scenarios"]
        self.assertEqual(len(scenarios), 3)
        self.assertTrue(all(s["synthetic"] and s["expected_behavior"] for s in scenarios))

    async def test_toolbox_transport_keeps_raw_tools_private_and_calls_once(self):
        client = research.EvidenceResearch(TOOLBOX_ENDPOINT, object(), PROJECT_ENDPOINT)
        session = SimpleNamespace(
            list_tools=AsyncMock(return_value=SimpleNamespace(
                tools=[SimpleNamespace(name="fincrime___knowledge_base_retrieve")],
            )),
            call_tool=AsyncMock(return_value=result(LIBRARY)),
        )
        transport = AsyncMock()
        transport.__aenter__.return_value = SimpleNamespace(session=session)
        factory = Mock(return_value=transport)
        module = SimpleNamespace(FoundryToolbox=factory)
        question = research.TOPICS["money-mule-recruitment"]["library"]
        with patch.dict(sys.modules, {"agent_framework_foundry_hosting": module}):
            raw = await client.retrieve(question)
        self.assertFalse(raw.isError)
        self.assertIs(factory.call_args.args[0], client.credential)
        self.assertFalse(factory.call_args.kwargs["load_tools"])
        self.assertFalse(factory.call_args.kwargs["load_prompts"])
        session.call_tool.assert_awaited_once_with(
            "fincrime___knowledge_base_retrieve", arguments={"query_variants": [question]},
        )
        transport.__aexit__.assert_awaited_once()

    async def test_toolbox_does_not_retry_a_failed_retrieval_or_use_an_unexpected_tool(self):
        session = SimpleNamespace(list_tools=AsyncMock(), call_tool=AsyncMock())
        transport = AsyncMock()
        transport.__aenter__.return_value = SimpleNamespace(session=session)
        module = SimpleNamespace(FoundryToolbox=Mock(return_value=transport))
        client = research.EvidenceResearch(TOOLBOX_ENDPOINT, object(), PROJECT_ENDPOINT)
        with patch.dict(sys.modules, {"agent_framework_foundry_hosting": module}):
            for names in ([], ["other___knowledge_base_retrieve"],
                          ["fincrime___knowledge_base_retrieve"] * 2):
                session.list_tools.return_value = SimpleNamespace(
                    tools=[SimpleNamespace(name=name) for name in names],
                )
                with self.assertRaises(RuntimeError):
                    await client.retrieve("fixed question")
            session.call_tool.assert_not_awaited()
            session.list_tools.return_value = SimpleNamespace(
                tools=[SimpleNamespace(name="fincrime___knowledge_base_retrieve")],
            )
            session.call_tool.side_effect = RuntimeError("upstream failure")
            with self.assertRaises(RuntimeError):
                await client.retrieve("fixed question")
            self.assertEqual(session.call_tool.await_count, 1)

    def test_only_same_project_toolbox_endpoints_are_accepted(self):
        credential = SimpleNamespace(get_token=Mock(side_effect=AssertionError("No token should be requested.")))
        for endpoint in (
            TOOLBOX_ENDPOINT,
            PROJECT_ENDPOINT + "/toolboxes/evidence-tools/mcp?api-version=v1",
        ):
            research.EvidenceResearch(endpoint, credential, PROJECT_ENDPOINT)
        for endpoint in (
            "https://attacker.example/toolboxes/test/mcp?api-version=v1",
            TOOLBOX_ENDPOINT.replace("example.services.ai.azure.com", "other.services.ai.azure.com"),
            TOOLBOX_ENDPOINT.replace("/projects/fincrime/", "/projects/other/"),
            TOOLBOX_ENDPOINT.replace("https://", "https://user:password@"),
            TOOLBOX_ENDPOINT.replace("api-version=v1", "api-version=v1&extra=value"),
            TOOLBOX_ENDPOINT.replace("/versions/1", "/versions/../other"),
            TOOLBOX_ENDPOINT + "#fragment",
            "https://example.search.windows.net/knowledgebases/fincrime-research/mcp?api-version=2026-08-01-preview",
        ):
            with self.subTest(endpoint=endpoint), self.assertRaises(ValueError):
                research.EvidenceResearch(endpoint, credential, PROJECT_ENDPOINT)
        credential.get_token.assert_not_called()


if __name__ == "__main__":
    unittest.main()
