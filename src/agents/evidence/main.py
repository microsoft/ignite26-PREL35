import asyncio
import os
from pathlib import Path
from typing import Literal

from agent_framework import Agent, AgentContext, FunctionInvocationContext, agent_middleware
from agent_framework.foundry import FoundryChatClient
from agent_framework_foundry_hosting import ResponsesHostServer
from azure.identity import DefaultAzureCredential

from research import EvidenceResearch, ResearchBudget


async def main():
    with DefaultAzureCredential() as credential:
        research = EvidenceResearch(
            os.environ["TOOLBOX_EVIDENCE_TOOLS_MCP_ENDPOINT"],
            credential,
            os.environ["FOUNDRY_PROJECT_ENDPOINT"],
        )

        @agent_middleware
        async def research_budget(context: AgentContext, call_next):
            context.function_invocation_kwargs = {
                **(context.function_invocation_kwargs or {}),
                "research_budget": ResearchBudget(),
            }
            await call_next()

        async def research_topic(
            topic: Literal[
                "cash-structuring",
                "rapid-funds-transit",
                "money-mule-recruitment",
                "trade-document-consistency",
                "account-control-changes",
                "ownership-transparency",
                "merchant-processing-anomalies",
                "email-payment-diversion",
            ],
            context: FunctionInvocationContext,
        ) -> str:
            """Retrieve general typology context for one approved topic, never case facts or identifiers."""
            return await research.lookup(topic, context.kwargs.get("research_budget"))

        agent = Agent(
            name="evidence-agent",
            client=FoundryChatClient(
                project_endpoint=os.environ["FOUNDRY_PROJECT_ENDPOINT"],
                model=os.environ["AZURE_AI_MODEL_DEPLOYMENT_NAME"],
                credential=credential,
            ),
            instructions=Path(__file__).with_name("instructions.md").read_text(encoding="utf-8"),
            tools=[research_topic],
            middleware=[research_budget],
            default_options={"store": False, "reasoning": {"effort": "low"}},
        )
        await ResponsesHostServer(agent).run_async()


if __name__ == "__main__":
    asyncio.run(main())
