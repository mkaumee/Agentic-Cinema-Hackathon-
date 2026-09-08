# httpx .json() and Starlette's app.state are untyped by nature.
# pyright: reportAny=false, reportExplicitAny=false
"""The front half: a screenplay becomes open negotiations.

This is the part that was missing until now — ``run_e2e.py`` hand-seeded items,
suppliers and negotiations, which made the pipeline look complete when nothing
actually connected the script to them.

The test worth reading is
``test_a_screenplay_becomes_negotiations_without_anything_hand_seeded``.
"""

import asyncio
from datetime import UTC, datetime, timedelta
from typing import Any, override

import httpx
import pytest
from cinema_contracts import (
    AgentBrain,
    ItemBrief,
    ItemResearch,
    Listing,
    Money,
    NegotiationState,
    SourcingRoute,
)
from cinema_contracts.testing import ScriptedBrain
from google.cloud.firestore_v1 import AsyncClient
from orchestrator.app import Services, app
from orchestrator.clock import FrozenRealTime, SimClock
from orchestrator.mail import InMemoryMailbox
from orchestrator.mailboxes import SingleMailbox
from orchestrator.records import ItemStatus
from orchestrator.repository import DueItem, FirestoreRepository
from orchestrator.settings import Settings
from orchestrator.sourcing import item_id_for, negotiation_id_for, supplier_id_for
from orchestrator.tick import TickLoop

PID = "nasi-lemak-nights"
REAL0 = datetime(2026, 8, 12, 14, 0, tzinfo=UTC)

# A send budget past anything here: these tests are about a screenplay
# becoming negotiations, not about pacing post against Gmail's per-minute
# cost limit. The pacing has its own tests in test_tick.py.
SETTINGS = Settings(
    _env_file=None,  # pyright: ignore[reportCallIssue]
    gcp_project="demo-cinema",
    send_limit=50,
)

SCRIPT = """INT. BAR - NIGHT

The MAN grabbed the cup and threw it towards the mirror.

EXT. STREET - DAY

She lights a cigarette and checks her watch.
"""


@pytest.fixture
async def api(firestore: AsyncClient) -> httpx.AsyncClient:
    repo = FirestoreRepository(firestore)
    clock = SimClock(repo, FrozenRealTime(REAL0))
    brain = ScriptedBrain()
    mail = InMemoryMailbox()
    app.state.services = Services(
        settings=SETTINGS,
        client=firestore,
        repo=repo,
        clock=clock,
        brain=brain,
        mail=mail,
        loop=TickLoop(repo, clock, brain, SingleMailbox(mail)),
    )
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    )


async def _new_project(api: httpx.AsyncClient) -> None:
    response = await api.post(
        "/projects", json={"project_id": PID, "title": "Nasi Lemak Nights"}
    )
    assert response.status_code == 201, response.text


async def _upload(api: httpx.AsyncClient) -> list[dict[str, Any]]:
    response = await api.post(f"/projects/{PID}/script", json={"text_content": SCRIPT})
    assert response.status_code == 200, response.text
    return response.json()["props"]


async def _confirm_everything(api: httpx.AsyncClient) -> list[dict[str, Any]]:
    """Producer signs off the whole list, which is what puts items in the queue."""
    props = await _upload(api)
    response = await api.post(
        f"/projects/{PID}/items/confirm",
        json={"items": [{"item_id": p["item_id"], "qty": 1} for p in props]},
    )
    assert response.status_code == 200, response.text
    return props


# --------------------------------------------------------------------------- #
# Reading the script
# --------------------------------------------------------------------------- #


async def test_uploading_a_screenplay_finds_the_props(api: httpx.AsyncClient) -> None:
    await _new_project(api)

    props = await _upload(api)

    assert {p["name"] for p in props} == {"cup", "mirror", "cigarette", "watch"}


async def test_every_prop_comes_back_with_the_line_that_justifies_it(
    api: httpx.AsyncClient,
) -> None:
    """The producer audits the list instead of trusting it."""
    await _new_project(api)

    for prop in await _upload(api):
        assert prop["lines"], f"{prop['name']} arrived with no script line"
        assert all(line.strip() for line in prop["lines"])


async def test_a_prop_that_gets_destroyed_is_flagged_for_the_producer(
    api: httpx.AsyncClient,
) -> None:
    await _new_project(api)

    by_name = {p["name"]: p for p in await _upload(api)}

    assert by_name["mirror"]["consumable"] is True
    assert by_name["watch"]["consumable"] is False


async def test_uploading_a_revised_draft_updates_rather_than_duplicates(
    api: httpx.AsyncClient, firestore: AsyncClient
) -> None:
    """Item ids are derived from the name, so a second draft is not a second mirror."""
    await _new_project(api)
    first = await _upload(api)
    second = await _upload(api)

    items = await FirestoreRepository(firestore).list_items(PID)

    assert [p["item_id"] for p in first] == [p["item_id"] for p in second]
    assert set(items) == {item_id_for(p["name"]) for p in first}
    assert len(items) == 4, "a revised draft should update items, not duplicate them"


# --------------------------------------------------------------------------- #
# Nothing moves until a human says so
# --------------------------------------------------------------------------- #


async def test_an_uploaded_script_starts_nothing(
    api: httpx.AsyncClient, firestore: AsyncClient
) -> None:
    """The gap where a hallucinated prop gets caught.

    Upload, then tick repeatedly. No research, no suppliers, no negotiations,
    no email — because a person has not confirmed the list yet.
    """
    await _new_project(api)
    _ = await _upload(api)

    for _ in range(3):
        body = (await api.post("/tick")).json()["projects"][0]
        assert body["items_examined"] == 0
        assert body["negotiations_opened"] == 0
        assert body["messages_sent"] == 0

    repo = FirestoreRepository(firestore)
    assert await repo.list_suppliers(PID) == {}
    assert await repo.list_negotiations(PID) == {}
    assert all(
        i.status is ItemStatus.DRAFT for i in (await repo.list_items(PID)).values()
    )


async def test_items_left_out_at_confirmation_are_abandoned_not_deleted(
    api: httpx.AsyncClient, firestore: AsyncClient
) -> None:
    """The breakdown still shows what the script asked for and what was dropped."""
    await _new_project(api)
    _ = await _upload(api)

    response = await api.post(
        f"/projects/{PID}/items/confirm",
        json={
            "items": [
                {"item_id": item_id_for("mirror"), "qty": 6, "include": True},
                {"item_id": item_id_for("watch"), "include": False},
            ]
        },
    )
    body = response.json()

    assert body["confirmed"] == [item_id_for("mirror")]
    assert body["abandoned"] == [item_id_for("watch")]

    items = await FirestoreRepository(firestore).list_items(PID)
    assert items[item_id_for("watch")].status is ItemStatus.ABANDONED
    assert items[item_id_for("mirror")].qty == 6


async def test_the_producer_sets_the_quantity_for_a_consumable(
    api: httpx.AsyncClient, firestore: AsyncClient
) -> None:
    """Six breakaway mirrors, because only a human knows the shooting schedule."""
    await _new_project(api)
    _ = await _upload(api)

    _ = await api.post(
        f"/projects/{PID}/items/confirm",
        json={"items": [{"item_id": item_id_for("mirror"), "qty": 6}]},
    )

    item = await FirestoreRepository(firestore).get_item(PID, item_id_for("mirror"))
    assert item is not None
    assert item.qty == 6
    assert item.consumable


# --------------------------------------------------------------------------- #
# The whole front half
# --------------------------------------------------------------------------- #


async def test_a_screenplay_becomes_negotiations_without_anything_hand_seeded(
    api: httpx.AsyncClient, firestore: AsyncClient
) -> None:
    """Script in, open negotiations out. The hole this phase existed to close."""
    await _new_project(api)
    props = await _upload(api)

    _ = await api.post(
        f"/projects/{PID}/items/confirm",
        json={
            "items": [
                {"item_id": p["item_id"], "qty": 1, "floor_price": {"amount": 900}}
                for p in props
            ]
        },
    )

    # Several passes, not three. Each step is its own tick so that a process
    # dying between them loses only that step — and research is bounded to a
    # few items per tick, because one item is a reasoning call plus several web
    # searches and a pass that tries to do fifty is a pass Cloud Run kills at
    # the wall. So a four-prop script is researched over several minutes rather
    # than in one go, which is the point rather than a limitation.
    for _ in range(8):
        _ = await api.post("/tick")

    repo = FirestoreRepository(firestore)
    items = await repo.list_items(PID)
    suppliers = await repo.list_suppliers(PID)
    negotiations = await repo.list_negotiations(PID)

    assert suppliers, "research produced no suppliers"
    assert negotiations, "no negotiations were opened"
    assert all(i.status is ItemStatus.NEGOTIATING for i in items.values())
    assert all(i.reference_band is not None for i in items.values())

    # And the loop took over as far as it is allowed to on its own: the opening
    # emails are written and waiting, not sent. This is the gate — the whole
    # path from a screenplay to a stranger's inbox stops here for a person.
    assert {n.state for n in negotiations.values()} == {NegotiationState.DRAFTED}
    assert all(n.draft_body for n in negotiations.values()), "written"
    assert all(n.opening_released_at is None for n in negotiations.values()), "not sent"

    # A producer reads them and releases them, and only then do they go.
    released_at = await repo.read(PID)
    for negotiation_id, record in negotiations.items():
        record.opening_released_at = released_at.sim_now
        record.next_action_due_at = released_at.sim_now
        await repo.save_negotiation(PID, negotiation_id, record)
    _ = await api.post("/tick")

    after = await repo.list_negotiations(PID)
    assert {n.state for n in after.values()} == {NegotiationState.AWAITING_REPLY}


async def test_the_floor_set_at_confirmation_reaches_every_negotiation(
    api: httpx.AsyncClient, firestore: AsyncClient
) -> None:
    """One ceiling per item, inherited by each seller approached for it."""
    await _new_project(api)
    _ = await _upload(api)
    _ = await api.post(
        f"/projects/{PID}/items/confirm",
        json={
            "items": [
                {"item_id": item_id_for("mirror"), "floor_price": {"amount": 450}}
            ]
        },
    )

    for _ in range(2):
        _ = await api.post("/tick")

    negotiations = await FirestoreRepository(firestore).list_negotiations(PID)
    assert negotiations
    assert all(n.floor_price == Money(amount=450) for n in negotiations.values())


async def test_opening_negotiations_twice_does_not_email_anyone_twice(
    api: httpx.AsyncClient, firestore: AsyncClient
) -> None:
    """Negotiation ids are derived from the item and supplier.

    A tick killed midway through opening three of them re-runs and collides on
    the ones already written, so create() refuses rather than starting a second
    conversation with the same seller.
    """
    await _new_project(api)
    _ = await _upload(api)
    _ = await api.post(
        f"/projects/{PID}/items/confirm",
        json={"items": [{"item_id": item_id_for("cup")}]},
    )

    for _ in range(4):
        _ = await api.post("/tick")

    repo = FirestoreRepository(firestore)
    negotiations = await repo.list_negotiations(PID)
    item = await repo.get_item(PID, item_id_for("cup"))
    assert item is not None

    expected = {
        negotiation_id_for(item_id_for("cup"), supplier_id_for(s.email))
        for s in (await repo.list_suppliers(PID)).values()
    }
    assert set(negotiations) == expected


async def test_research_is_retried_rather_than_abandoned_when_it_finds_nobody(
    api: httpx.AsyncClient, firestore: AsyncClient
) -> None:
    """An empty search might be transient; giving up immediately would be wrong."""

    class NoSuppliers(ScriptedBrain):
        @override
        async def research_item(self, brief: ItemBrief) -> ItemResearch:
            found = await super().research_item(brief)
            return found.model_copy(update={"supplier_candidates": []})

    repo = FirestoreRepository(firestore)
    clock = SimClock(repo, FrozenRealTime(REAL0))
    brain = NoSuppliers()
    app.state.services = Services(
        settings=SETTINGS,
        client=firestore,
        repo=repo,
        clock=clock,
        brain=brain,
        mail=InMemoryMailbox(),
        loop=TickLoop(repo, clock, brain, SingleMailbox(InMemoryMailbox())),
    )

    await _new_project(api)
    _ = await _upload(api)
    _ = await api.post(
        f"/projects/{PID}/items/confirm",
        json={"items": [{"item_id": item_id_for("cup")}]},
    )
    _ = await api.post("/tick")

    item = await repo.get_item(PID, item_id_for("cup"))
    assert item is not None
    assert item.status is ItemStatus.RESEARCHING
    assert item.next_action_due_at is not None, "it should come back and try again"
    assert item.next_action_due_at > REAL0 - timedelta(days=1)


# --------------------------------------------------------------------------- #
# Endpoint edges
# --------------------------------------------------------------------------- #


async def test_uploading_to_a_project_that_does_not_exist_is_a_404(
    api: httpx.AsyncClient,
) -> None:
    response = await api.post("/projects/ghost/script", json={"text_content": SCRIPT})
    assert response.status_code == 404


async def test_creating_the_same_project_twice_is_a_409(
    api: httpx.AsyncClient,
) -> None:
    await _new_project(api)
    again = await api.post("/projects", json={"project_id": PID, "title": "again"})
    assert again.status_code == 409


async def test_an_empty_script_is_rejected(api: httpx.AsyncClient) -> None:
    await _new_project(api)
    response = await api.post(f"/projects/{PID}/script", json={"text_content": ""})
    assert response.status_code == 422


# --------------------------------------------------------------------------- #
# Overlapping ticks
# --------------------------------------------------------------------------- #


class _CountingBrain(ScriptedBrain):
    """Counts research calls. The thing overlapping ticks would pay for twice."""

    researched: list[str]

    def __init__(self) -> None:
        super().__init__()
        self.researched = []

    @override
    async def research_item(self, brief: ItemBrief) -> ItemResearch:
        self.researched.append(brief.item_id)
        return await super().research_item(brief)


class _RendezvousRepository(FirestoreRepository):
    """Holds both ticks at the due-item read, so they genuinely race."""

    _barrier: asyncio.Barrier

    def __init__(self, client: AsyncClient, barrier: asyncio.Barrier) -> None:
        super().__init__(client)
        self._barrier = barrier

    @override
    async def due_items(self, now: datetime, *, limit: int = 25) -> list[DueItem]:
        due = await super().due_items(now, limit=limit)
        _ = await self._barrier.wait()
        return due


async def test_two_overlapping_ticks_research_an_item_once(
    api: httpx.AsyncClient, firestore: AsyncClient
) -> None:
    """Losing this race costs money rather than a supplier's goodwill.

    ``research_item`` is a slow LLM call, and two ticks arriving together would
    both make it and then write identical results — the damage invisible in the
    data and visible only on the bill.

    Note the third assertion, which was written the wrong way round first. It
    originally required that every researched item also be *counted* as
    researched, and it failed roughly one run in five with
    ``409 Transaction lock timeout`` — Firestore aborting one of two genuinely
    concurrent writes to the same document. That is not a test artifact and not
    something to retry around: it is what contention looks like, and it will
    happen on Cloud Run for the same reason it happens here.

    What the system owes us under contention is not that every item succeeds.
    It is that no item is silently dropped: each one either completes or is
    reported, and a reported one comes back when its lease runs out.
    """
    await _new_project(api)
    _ = await _confirm_everything(api)

    barrier = asyncio.Barrier(2)
    brains = [_CountingBrain(), _CountingBrain()]
    loops = [
        TickLoop(
            repo := _RendezvousRepository(firestore, barrier),
            SimClock(repo, FrozenRealTime(REAL0)),
            brain,
            SingleMailbox(InMemoryMailbox()),
        )
        for brain in brains
    ]

    reports = await asyncio.gather(*(loop.run_tick(PID) for loop in loops))

    researched = brains[0].researched + brains[1].researched
    assert len(researched) == len(set(researched)), (
        "no item may be researched twice across two overlapping ticks"
    )
    assert sum(r.claims_lost for r in reports) > 0, "the ticks did actually race"

    counted = sum(r.items_researched for r in reports)
    reported = sum(len(r.errors) for r in reports)
    assert counted + reported == len(set(researched)), (
        "every item is either finished or reported — none vanish"
    )


async def test_an_item_lost_to_contention_comes_back(
    api: httpx.AsyncClient, firestore: AsyncClient
) -> None:
    """The other half of the promise above: reported is not the same as dropped.

    Whatever the two racing ticks failed to finish is claimed, so it is quiet
    until the lease expires and then due again. A later tick completes it, and
    every confirmed item ends up sourced.
    """
    await _new_project(api)
    props = await _confirm_everything(api)

    barrier = asyncio.Barrier(2)
    loops = [
        TickLoop(
            repo := _RendezvousRepository(firestore, barrier),
            SimClock(repo, FrozenRealTime(REAL0)),
            ScriptedBrain(),
            SingleMailbox(InMemoryMailbox()),
        )
        for _ in range(2)
    ]
    _ = await asyncio.gather(*(loop.run_tick(PID) for loop in loops))

    # Past the lease, with a single tick this time.
    repo = FirestoreRepository(firestore)
    clock = SimClock(repo, FrozenRealTime(REAL0))
    _ = await clock.set_sim_now(PID, REAL0 + timedelta(hours=1))
    settled = TickLoop(repo, clock, ScriptedBrain(), SingleMailbox(InMemoryMailbox()))
    for _ in range(3):
        _ = await settled.run_tick(PID)

    items = await repo.list_items(PID)
    assert len(items) == len(props)
    assert all(i.status is ItemStatus.NEGOTIATING for i in items.values()), {
        k: v.status for k, v in items.items()
    }


# --------------------------------------------------------------------------- #
# Two roads out of the breakdown
# --------------------------------------------------------------------------- #


def _wire(firestore: AsyncClient, brain: AgentBrain) -> InMemoryMailbox:
    """Point the tick app at a brain of the test's choosing. Returns the mailbox.

    Handed back so a test can assert on what was sent, which for the buy route
    is the whole point: the answer has to be nothing.
    """
    repo = FirestoreRepository(firestore)
    clock = SimClock(repo, FrozenRealTime(REAL0))
    mail = InMemoryMailbox()
    app.state.services = Services(
        settings=SETTINGS,
        client=firestore,
        repo=repo,
        clock=clock,
        brain=brain,
        mail=mail,
        loop=TickLoop(repo, clock, brain, SingleMailbox(mail)),
    )
    return mail


async def _confirm(api: httpx.AsyncClient, item_id: str, route: str) -> None:
    response = await api.post(
        f"/projects/{PID}/items/confirm",
        json={"items": [{"item_id": item_id, "qty": 1, "route": route}]},
    )
    assert response.status_code == 200, response.text


async def test_a_prop_routed_to_a_shop_is_never_emailed_to_anyone(
    api: httpx.AsyncClient, firestore: AsyncClient
) -> None:
    """The claim the buy route lives or dies on.

    A listing has no correspondent — only a product page — so the agent must
    not write to it, chase it, or ask the brain what to say to it. That is not
    a rule enforced somewhere later: the row is created with no due date, so
    the tick never sees it at all.

    Several ticks, because "not on the first pass" is not the claim.
    """
    mail = _wire(firestore, ScriptedBrain())
    await _new_project(api)
    _ = await _upload(api)
    await _confirm(api, item_id_for("cup"), "BUY")

    for _ in range(4):
        _ = await api.post("/tick")

    repo = FirestoreRepository(firestore)
    negotiations = await repo.list_negotiations(PID)
    assert negotiations, "the listing should still produce a decision"
    assert all(
        n.state is NegotiationState.READY_FOR_HUMAN for n in negotiations.values()
    )
    assert all(n.listing_url for n in negotiations.values()), "marked as a listing"
    assert all(n.next_action_due_at is None for n in negotiations.values())
    assert all(n.latest_quote is not None for n in negotiations.values()), "has a price"

    assert mail.sent == [], "nothing was emailed about a shop page"
    assert all(not n.draft_body for n in negotiations.values()), "and nothing drafted"

    item = await repo.get_item(PID, item_id_for("cup"))
    assert item is not None
    assert item.route is SourcingRoute.BUY
    assert item.listings, "the evidence is kept on the item too"


async def test_a_shop_prop_with_no_listings_falls_back_to_writing_to_people(
    api: httpx.AsyncClient, firestore: AsyncClient
) -> None:
    """The producer asked to buy it; nobody sells it online. Ask people instead.

    Overriding their choice is only defensible because the alternative is doing
    nothing at all — there is no version of respecting it that helps them.
    """

    class NoListings(ScriptedBrain):
        @override
        async def research_item(self, brief: ItemBrief) -> ItemResearch:
            found = await super().research_item(brief)
            return found.model_copy(update={"listings": []})

    _ = _wire(firestore, NoListings())
    await _new_project(api)
    _ = await _upload(api)
    await _confirm(api, item_id_for("cup"), "BUY")

    for _ in range(2):
        _ = await api.post("/tick")

    repo = FirestoreRepository(firestore)
    item = await repo.get_item(PID, item_id_for("cup"))
    assert item is not None
    assert item.route is SourcingRoute.NEGOTIATE, "corrected by the evidence"
    assert item.status is ItemStatus.NEGOTIATING

    negotiations = await repo.list_negotiations(PID)
    assert negotiations
    assert all(not n.listing_url for n in negotiations.values())
    assert all(n.state is NegotiationState.DRAFTED for n in negotiations.values())


async def test_sellers_who_cannot_be_emailed_become_listings_not_a_retry_loop(
    api: httpx.AsyncClient, firestore: AsyncClient
) -> None:
    """The bug the buy route fixes, as much as the feature it adds.

    Research that finds three real sellers on marketplace pages with no
    scrapeable address stored as nothing at all, and the item retried every six
    hours forever — indistinguishable, on screen, from a prop nobody sells.
    """

    class ShopsOnly(ScriptedBrain):
        @override
        async def research_item(self, brief: ItemBrief) -> ItemResearch:
            found = await super().research_item(brief)
            return found.model_copy(
                update={
                    "supplier_candidates": [],
                    "listings": [
                        Listing(
                            title=f"{brief.name} on a marketplace",
                            url=f"https://shop.example.invalid/{brief.item_id}",
                            price=Money(amount=89, currency="MYR"),
                            seller="A Marketplace",
                        )
                    ],
                }
            )

    _ = _wire(firestore, ShopsOnly())
    await _new_project(api)
    _ = await _upload(api)
    # Routed to people, which is the default and what the old code always did.
    await _confirm(api, item_id_for("cup"), "NEGOTIATE")

    for _ in range(2):
        _ = await api.post("/tick")

    repo = FirestoreRepository(firestore)
    item = await repo.get_item(PID, item_id_for("cup"))
    assert item is not None
    assert item.route is SourcingRoute.BUY
    assert item.status is ItemStatus.NEGOTIATING, "not stuck retrying"
    assert item.next_action_due_at is None

    negotiations = await repo.list_negotiations(PID)
    assert negotiations, "a decision, rather than a six-hour retry forever"
    assert all(n.listing_url for n in negotiations.values())


async def test_two_listings_on_one_marketplace_are_two_decisions(
    api: httpx.AsyncClient, firestore: AsyncClient
) -> None:
    """Keyed by the product page, not by the shop.

    Key a listing by its seller and two products from one marketplace collide,
    `create_negotiation` refuses the second, and it vanishes with no error at
    all — the producer is shown one option and told it was the best of two.
    """

    class TwoFromOneShop(ScriptedBrain):
        @override
        async def research_item(self, brief: ItemBrief) -> ItemResearch:
            found = await super().research_item(brief)
            return found.model_copy(
                update={
                    "listings": [
                        Listing(
                            title="The dearer one",
                            url="https://shop.example.invalid/a",
                            price=Money(amount=120, currency="MYR"),
                            seller="One Marketplace",
                        ),
                        Listing(
                            title="The cheaper one",
                            url="https://shop.example.invalid/b",
                            price=Money(amount=89, currency="MYR"),
                            seller="One Marketplace",
                        ),
                    ]
                }
            )

    _ = _wire(firestore, TwoFromOneShop())
    await _new_project(api)
    _ = await _upload(api)
    await _confirm(api, item_id_for("cup"), "BUY")

    for _ in range(2):
        _ = await api.post("/tick")

    repo = FirestoreRepository(firestore)
    negotiations = await repo.list_negotiations(PID)
    urls = {n.listing_url for n in negotiations.values()}
    assert urls == {
        "https://shop.example.invalid/a",
        "https://shop.example.invalid/b",
    }

    item = await repo.get_item(PID, item_id_for("cup"))
    assert item is not None
    assert [listing.price.amount for listing in item.listings] == [89, 120], (
        "cheapest first, because the screen leads with the best one"
    )


async def test_the_producer_can_overrule_the_route_before_anything_happens(
    api: httpx.AsyncClient, firestore: AsyncClient
) -> None:
    """They know the birdcage is being built. The agent only guessed.

    The toggle is on the confirmation list because that is the last moment
    before anything is researched or written to anyone.
    """
    _ = _wire(firestore, ScriptedBrain())
    await _new_project(api)
    _ = await _upload(api)

    repo = FirestoreRepository(firestore)
    proposed = await repo.get_item(PID, item_id_for("cup"))
    assert proposed is not None
    proposed.route = SourcingRoute.BUY
    await repo.save_item(PID, item_id_for("cup"), proposed)

    await _confirm(api, item_id_for("cup"), "NEGOTIATE")

    for _ in range(2):
        _ = await api.post("/tick")

    item = await repo.get_item(PID, item_id_for("cup"))
    assert item is not None
    assert item.route is SourcingRoute.NEGOTIATE, "the person's choice stood"
    negotiations = await repo.list_negotiations(PID)
    assert negotiations
    assert all(not n.listing_url for n in negotiations.values())


async def test_research_is_bounded_far_below_the_negotiation_budget(
    api: httpx.AsyncClient, firestore: AsyncClient
) -> None:
    """One number used to govern two jobs of wildly different cost.

    `tick_limit` meant "advance up to 50 negotiations" and, because it was
    handed to both, also "research up to 50 items". Researching one item is a
    reasoning call plus several web searches; fifty cannot fit in the fifty
    seconds Cloud Run allows the request. The pass was killed at the wall, the
    rows it had claimed were parked for the lease, and everything behind them
    waited — including openings a producer had already sent.
    """
    _ = _wire(firestore, ScriptedBrain())
    await _new_project(api)
    props = await _confirm_everything(api)
    assert len(props) > 2, "the script needs more props than one tick may research"

    _ = await api.post("/tick")

    repo = FirestoreRepository(firestore)
    items = await repo.list_items(PID)
    researched = [i for i in items.values() if i.reference_band is not None]
    assert len(researched) <= 3, "one pass does not try to research everything"


async def test_nothing_is_lost_to_the_smaller_budget(
    api: httpx.AsyncClient, firestore: AsyncClient
) -> None:
    """An item this pass did not reach stays due and is picked up next minute.

    The bound is only defensible because of this. A cap that dropped work would
    be trading a visible failure for an invisible one.
    """
    _ = _wire(firestore, ScriptedBrain())
    await _new_project(api)
    props = await _confirm_everything(api)

    for _ in range(6):
        _ = await api.post("/tick")

    repo = FirestoreRepository(firestore)
    items = await repo.list_items(PID)
    assert len(items) == len(props)
    assert all(i.reference_band is not None for i in items.values()), (
        "every prop was researched, just over several passes"
    )
    assert all(i.status is ItemStatus.NEGOTIATING for i in items.values())
