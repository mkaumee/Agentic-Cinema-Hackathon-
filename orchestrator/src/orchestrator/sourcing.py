"""Turning confirmed items into open negotiations.

The front half of the pipeline. Everything here runs inside the tick, off the
same due-date queue that drives negotiations, for the same reason: it makes
long LLM-backed work killable without a second recovery story.

::

    DRAFT ──a human confirms──> RESEARCHING ──> SOURCING ──> NEGOTIATING
                                    │               │
                              research_item    open one negotiation
                              band + sellers   per candidate seller

Two steps rather than one, deliberately. Research is a slow LLM call and
opening negotiations fans out into several writes; splitting them means a tick
that dies partway leaves a clearly-defined amount of work done, and the next
tick picks up exactly where it stopped.

Nothing in here emails anyone. Opening a negotiation only creates a document in
``DRAFTED``; the negotiation half of the tick sends the first message on its
next pass. That keeps "who talks to sellers" in one place.
"""

import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from cinema_contracts import (
    AgentBrain,
    EscalationReason,
    ExtractedQuote,
    ItemBrief,
    Listing,
    NegotiationState,
    SourcingRoute,
    SupplierCandidate,
)

from orchestrator.records import (
    ItemRecord,
    ItemStatus,
    NegotiationRecord,
    SupplierRecord,
)
from orchestrator.repository import DueItem, FirestoreRepository

RESEARCH_RETRY_HOURS = 6.0
"""How long to wait before retrying an item whose research produced nothing."""

CLAIM_LEASE_HOURS = 0.25
"""How far ahead claiming an item parks it. See ``tick.CLAIM_LEASE_HOURS``."""

MAX_SUPPLIERS_PER_ITEM = 3
"""How many sellers to approach for one item.

Three is enough to have something to compare and to survive one going quiet.
More would multiply the mail volume without improving the decision, and every
extra negotiation is another five-day conversation to keep alive.
"""

_EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


@dataclass(slots=True)
class SourcingReport:
    """What the item half of a tick did."""

    items_examined: int = 0
    researched: int = 0
    negotiations_opened: int = 0
    suppliers_written: int = 0
    abandoned: int = 0
    claims_lost: int = 0
    errors: list[str] = field(default_factory=list)


def item_id_for(name: str) -> str:
    """A stable id derived from the prop's name.

    Deterministic so that re-uploading a revised script updates the items it
    already found rather than duplicating them — a mirror is the same mirror
    on the second draft. It also makes `purchase_orders/{item_id}` readable in
    the console, which matters when the guardrail is being demonstrated.
    """
    slug = re.sub(r"[^a-z0-9]+", "-", name.strip().lower()).strip("-")[:100]
    return slug or "unnamed-item"


def supplier_id_for(email: str) -> str:
    """A stable id derived from the address.

    Deterministic on purpose. Research runs more than once — retries, a second
    item needing the same seller — and a generated id would file the same
    company twice, then open two negotiations with one inbox.
    """
    return re.sub(r"[^a-z0-9]+", "-", email.strip().lower()).strip("-")[:120]


def shop_id_for(url: str) -> str:
    """A stable id for a shop, derived from the product page.

    Keyed by the *listing*, not by the shop, and that is the important part.
    ``supplier_id_for`` keys on the address because one company has one inbox,
    but one shop sells many things — key a listing by its seller and two
    listings from the same marketplace collide, ``create_negotiation`` refuses
    the second, and it is dropped with no error at all.

    It also sidesteps a sharper edge: ``supplier_id_for("")`` returns the empty
    string, and a Firestore document path with an empty segment is not a
    document, it is a collection. That failure surfaces far from here.
    """
    slug = re.sub(r"[^a-z0-9]+", "-", url.strip().lower()).strip("-")
    # Keep the tail rather than the head: every listing on one marketplace
    # shares a long prefix, so truncating from the front is how three distinct
    # products become one id.
    return f"shop-{slug[-110:]}" if slug else "shop-unknown"


def _route_with_evidence(
    asked: SourcingRoute,
    *,
    sellers: list[SupplierCandidate],
    listings: list[Listing],
) -> SourcingRoute:
    """The route the research actually supports, which may not be the one asked.

    The producer's choice wins whenever it can be honoured. It is only
    overridden when the road they picked turned out to be empty and the other
    one is not — at which point the alternative is doing nothing at all, so
    there is no version of respecting their choice that helps them.

    Both empty returns the asked-for route unchanged, so the caller retries on
    the road that was chosen rather than quietly switching on a failed search.
    """
    if asked is SourcingRoute.BUY and not listings and sellers:
        return SourcingRoute.NEGOTIATE
    if asked is SourcingRoute.NEGOTIATE and not sellers and listings:
        return SourcingRoute.BUY
    return asked


def negotiation_id_for(item_id: str, supplier_id: str) -> str:
    """One negotiation per item-supplier pair, named after the pair.

    The same trick purchase orders use. A tick killed midway through opening
    three negotiations re-runs and collides on the ones it already wrote, so
    ``create()`` refuses them rather than emailing those sellers twice.
    """
    return f"{item_id}--{supplier_id}"


def looks_like_an_address(email: str) -> bool:
    """Cheap sanity check before we commit to writing to someone.

    Not validation — that is what a bounce is for. This only catches the brain
    returning a placeholder or a company name where an address belongs, which
    would otherwise cost a simulated day waiting for a reply that cannot come.
    """
    return bool(_EMAIL.match(email.strip()))


class SourcingLoop:
    """Advances items from confirmed, through research, to open negotiations."""

    _repo: FirestoreRepository
    _brain: AgentBrain

    def __init__(self, repo: FirestoreRepository, brain: AgentBrain) -> None:
        self._repo = repo
        self._brain = brain

    async def run(self, now: datetime, *, limit: int = 25) -> SourcingReport:
        report = SourcingReport()
        for due in await self._repo.due_items(now, limit=limit):
            report.items_examined += 1
            if not await self._repo.claim_item(
                due, now + timedelta(hours=CLAIM_LEASE_HOURS)
            ):
                # Another overlapping tick has this item. Letting both through
                # would mean paying for the same research_item call twice.
                report.claims_lost += 1
                continue
            try:
                if due.record.status is ItemStatus.RESEARCHING:
                    await self._research(due, now, report)
                elif due.record.status is ItemStatus.SOURCING:
                    await self._open_negotiations(due, now, report)
            except Exception as exc:
                report.errors.append(f"{due.item_id}: {exc}")
        return report

    # ------------------------------------------------------------------ #

    async def _research(
        self, due: DueItem, now: datetime, report: SourcingReport
    ) -> None:
        """Ask the brain what this costs and how to get it, then store both.

        Two roads out of here. An item routed NEGOTIATE wants sellers with an
        address; one routed BUY wants shop pages with a price on them. The
        route was proposed at breakdown and possibly flipped by the producer,
        and this is where the evidence gets a vote: whichever road was asked
        for, if it comes back empty and the other one did not, the item takes
        the other one.

        That fallback is not a nicety. Before it, research that found three
        real sellers on marketplace pages with no scrapeable address was stored
        as nothing at all, and the item retried every six hours forever —
        indistinguishable, on screen, from a prop nobody sells.
        """
        item = due.record
        research = await self._brain.research_item(brief_of(due.item_id, item))

        sellers = [
            candidate
            for candidate in research.supplier_candidates
            if looks_like_an_address(candidate.email)
        ][:MAX_SUPPLIERS_PER_ITEM]
        # Cheapest first, because the producer is shown the best one and the
        # rest collapse behind it. Sorted here rather than on the way out so
        # the stored evidence reads in the same order as the screen.
        listings = sorted(research.listings, key=lambda listing: listing.price.amount)[
            :MAX_SUPPLIERS_PER_ITEM
        ]

        route = _route_with_evidence(item.route, sellers=sellers, listings=listings)
        found_ids = (
            await self._write_shops(due.project_id, listings)
            if route is SourcingRoute.BUY
            else await self._write_sellers(due.project_id, sellers)
        )
        report.suppliers_written += len(found_ids)

        item.route = route
        item.supplier_ids = found_ids
        item.listings = listings if route is SourcingRoute.BUY else []
        item.reference_band = research.reference_band
        item.updated_at = now

        if not found_ids:
            # Nothing on either road. Not a failure the agent can solve by
            # trying harder, but it might be a transient search problem, so it
            # gets a few retries before a human is bothered.
            item.status = ItemStatus.RESEARCHING
            item.next_action_due_at = now + timedelta(hours=RESEARCH_RETRY_HOURS)
            item.notes = f"{item.notes}\nNo seller to write to and no listing to buy."
            item.notes = item.notes.strip()
        else:
            item.status = ItemStatus.SOURCING
            item.next_action_due_at = now

        await self._repo.save_item(due.project_id, due.item_id, item)
        report.researched += 1

    async def _write_sellers(
        self, project_id: str, sellers: list[SupplierCandidate]
    ) -> list[str]:
        """People to write to. Keyed by address, so a retry refiles the same one."""
        found_ids: list[str] = []
        for candidate in sellers:
            supplier_id = supplier_id_for(candidate.email)
            await self._repo.save_supplier(
                project_id,
                supplier_id,
                SupplierRecord(
                    name=candidate.name,
                    email=candidate.email.strip().lower(),
                    source_url=candidate.source_url,
                    confidence=candidate.confidence,
                    verified=candidate.verified,
                ),
            )
            found_ids.append(supplier_id)
        return found_ids

    async def _write_shops(self, project_id: str, listings: list[Listing]) -> list[str]:
        """Shops to buy from. Keyed by the product URL, for the same reason.

        A supplier record with no email is the marker for "there is nobody here
        to write to". Everything downstream that would send mail checks the
        address first, so a shop cannot be emailed by accident even if a
        negotiation for one somehow became due.
        """
        found_ids: list[str] = []
        for listing in listings:
            supplier_id = shop_id_for(listing.url)
            await self._repo.save_supplier(
                project_id,
                supplier_id,
                SupplierRecord(
                    name=listing.seller or listing.title,
                    email="",
                    listing_url=listing.url,
                    source_url=listing.url,
                    verified=False,
                ),
            )
            found_ids.append(supplier_id)
        return found_ids

    async def _open_negotiations(
        self, due: DueItem, now: datetime, report: SourcingReport
    ) -> None:
        """Turn what research found into rows a producer will act on.

        Two shapes, and they differ in one field that changes everything.

        A seller becomes a ``DRAFTED`` negotiation due immediately — the
        negotiation half of this same tick writes the opening email, and a
        person releases it.

        A listing becomes a negotiation that is already finished: state
        ``READY_FOR_HUMAN``, the shop's price as both the first and the latest
        quote, and **no due date at all**. It never enters the due queue, so
        the brain is never asked what to say to a shop and no email is ever
        drafted for one. That is not a policy enforced later; it is the row
        simply not being there to pick up.
        """
        item = due.record
        if item.route is SourcingRoute.BUY:
            await self._open_listings(due, now, report)
        else:
            await self._open_conversations(due, now, report)

        item.updated_at = now
        if not item.supplier_ids:
            item.status = ItemStatus.ABANDONED
            item.next_action_due_at = None
            report.abandoned += 1
        else:
            item.status = ItemStatus.NEGOTIATING
            # The negotiations carry the schedule from here. Leaving the item
            # in the queue would re-run this every tick for no reason.
            item.next_action_due_at = None

        await self._repo.save_item(due.project_id, due.item_id, item)

    async def _open_conversations(
        self, due: DueItem, now: datetime, report: SourcingReport
    ) -> None:
        """One negotiation per seller, to be written to."""
        item = due.record
        for supplier_id in item.supplier_ids[:MAX_SUPPLIERS_PER_ITEM]:
            created = await self._repo.create_negotiation(
                due.project_id,
                negotiation_id_for(due.item_id, supplier_id),
                NegotiationRecord(
                    item_id=due.item_id,
                    supplier_id=supplier_id,
                    state=NegotiationState.DRAFTED,
                    floor_price=item.floor_price,
                    # Due immediately: the negotiation half of this same tick
                    # will pick it up and send the opening email.
                    next_action_due_at=now,
                    created_at=now,
                    updated_at=now,
                ),
            )
            if created:
                report.negotiations_opened += 1

    async def _open_listings(
        self, due: DueItem, now: datetime, report: SourcingReport
    ) -> None:
        """One decision per shop page, already made as far as the agent goes.

        ``floor_price`` is deliberately not carried across. A floor is an
        instruction for a negotiation, and there is no one here to negotiate
        with; storing it would leave a number on the row that looks like it
        constrains something and does not.
        """
        item = due.record
        for listing, supplier_id in zip(item.listings, item.supplier_ids, strict=False):
            quote = ExtractedQuote(
                unit_price=listing.price,
                total=listing.price,
                qty=item.qty,
                terms=listing.title,
            )
            created = await self._repo.create_negotiation(
                due.project_id,
                negotiation_id_for(due.item_id, supplier_id),
                NegotiationRecord(
                    item_id=due.item_id,
                    supplier_id=supplier_id,
                    state=NegotiationState.READY_FOR_HUMAN,
                    listing_url=listing.url,
                    first_quote=quote,
                    latest_quote=quote,
                    escalation_reason=EscalationReason.LISTING_FOUND.value,
                    latest_reasoning=(
                        f"Listed at {listing.price.currency} "
                        f"{listing.price.amount:,} by "
                        f"{listing.seller or 'the shop'}. Nothing was "
                        f"negotiated — this is the price on the page."
                    ),
                    # No due date. This is what keeps a shop out of the tick's
                    # queue and therefore out of the mail path entirely.
                    next_action_due_at=None,
                    created_at=now,
                    updated_at=now,
                ),
            )
            if created:
                report.negotiations_opened += 1


def brief_of(item_id: str, item: ItemRecord) -> ItemBrief:
    """The item as the brain sees it. Shared by sourcing and the tick loop."""
    return ItemBrief(
        item_id=item_id,
        name=item.name,
        category=item.category,
        scenes=item.scenes,
        qty=item.qty,
        consumable=item.consumable,
        notes=item.notes,
        reference_band=item.reference_band,
        route=item.route,
    )
