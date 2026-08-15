"""Research defensible prices and potential suppliers for one item."""

from typing import final

from cinema_contracts import ItemBrief, ItemResearch
from google.adk.agents import LlmAgent

from main_agent.runtime import AdkAgentRuntime

_INSTRUCTION = """You research one film-production procurement item.
Return a defensible reference price band and supplier candidates. Include source
URLs that actually support the prices. Supplier addresses are candidates only
and must remain unverified until Role B checks them. Never fabricate a source.
Your response must satisfy the configured output schema.
"""


@final
class ItemResearcher:
    """Own the ADK agent whose only concern is market and supplier research.

    Search tools can be attached here later without granting those tools to the
    quote extractor or negotiation decider.  No search tool is configured in
    this initial skeleton, so production research is not ready yet.
    """

    def __init__(self, *, model: str) -> None:
        # ADK 2.7 exposes a concrete class through ABCMeta; see parser.py.
        agent = LlmAgent(  # pyright: ignore[reportEmptyAbstractUsage]
            name="item_researcher",
            description="Research reference prices and supplier candidates.",
            model=model,
            instruction=_INSTRUCTION,
            output_schema=ItemResearch,
            mode="single_turn",
        )
        self._runtime = AdkAgentRuntime(
            app_name="cinema_item_researcher",
            agent=agent,
        )

    async def research(self, brief: ItemBrief) -> ItemResearch:
        """Return research validated against the shared contracts model."""
        response = await self._runtime.run_json(brief.model_dump_json())
        return ItemResearch.model_validate_json(response)
