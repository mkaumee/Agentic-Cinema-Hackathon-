"""The four signatures. This is the whole Role A / Role B interface.

Role A implements ``AgentBrain``. Role B imports the protocol, never the
implementation, and passes a fake in tests. That is what lets both halves be
built at the same time without stubs sitting in each other's code, and what
lets the daily end-to-end run work before the real brain exists.

Everything the brain needs arrives in its arguments. The brain does not read
Firestore, does not touch Gmail, does not know what time it is, and keeps no
memory between calls. If a decision needs a fact, that fact belongs in
``NegotiationContext`` and adding it is a two-person change.

All four methods are ``async`` because all four make network-bound LLM calls.
"""

from typing import Protocol, runtime_checkable

from cinema_contracts.models import (
    BreakdownSource,
    InboundMessage,
    ItemBrief,
    ItemDraft,
    ItemResearch,
    NegotiationContext,
    NextMove,
    QuoteExtraction,
)


@runtime_checkable
class AgentBrain(Protocol):
    """The reasoning half of the system. Implemented in ``main-agent``.

    Implementations must be free of side effects beyond the LLM call itself:
    same input, same kind of output, no writes anywhere.
    """

    async def parse_breakdown(self, source: BreakdownSource) -> list[ItemDraft]:
        """Turn an uploaded shooting breakdown into a list of purchasable items.

        Called once per upload, from the Breakdown screen. Returns drafts; the
        producer confirms the list before anything is persisted as an item.

        Return an empty list if the document contains no recognisable items.
        Do not invent plausible-sounding line items to fill it out — a short
        honest list is recoverable, a padded one wastes negotiation rounds on
        things the production does not need.
        """
        ...

    async def research_item(self, brief: ItemBrief) -> ItemResearch:
        """Find what an item should cost and who might supply it.

        The reference band is shown to the producer beside real quotes, so it
        has to be defensible: populate ``source_urls`` with pages the numbers
        actually came from. A wide band with sources beats a narrow one without.

        Supplier candidates come back with ``verified=False``. Role B is
        responsible for confirming an address before opening a negotiation.
        """
        ...

    async def extract_quote(self, message: InboundMessage) -> QuoteExtraction:
        """Read a supplier's reply and say what it offered, or refuse to.

        This is the function with the sharpest failure mode. A wrong number
        extracted confidently propagates into a recommendation and then into a
        purchase order a human rubber-stamps.

        Set ``needs_human=True`` with a reason rather than guessing when:
          - the price is inside an attachment (``PRICE_IN_ATTACHMENT``)
          - the reply is not parseable as a quote (``UNPARSEABLE_REPLY``)
          - a number exists but its scope is unclear (``AMBIGUOUS_TERMS``)
          - the supplier asked a question instead of quoting (``SUPPLIER_QUESTION``)

        Escalating is always an acceptable answer. Guessing is not.
        """
        ...

    async def next_move(self, ctx: NegotiationContext) -> NextMove:
        """Decide what to do next in one negotiation, and write any email it needs.

        The brain composes the message text; Role B only addresses and sends it.

        Two constraints are absolute, and Role B enforces them again after this
        returns:

        - ``ACCEPT`` never buys anything. It routes to ``READY_FOR_HUMAN`` and
          waits for a person. There is no threshold below which the agent may
          approve its own purchase.
        - ``floor_price`` is the producer's stop condition. Do not accept above
          it, and do not keep grinding below it — return ``ACCEPT`` or
          ``ESCALATE`` with ``FLOOR_REACHED`` instead.

        Return ``WAIT`` when the right answer is to do nothing yet; the tick
        loop will come back. Returning ``WAIT`` forever is caught by
        ``max_rounds`` and escalated by Role B.
        """
        ...
