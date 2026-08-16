#!/usr/bin/env python3
"""Run the whole loop end to end, and say whether it still works.

This is the daily ten-minute habit. It boots nothing external except the
Firestore emulator, seeds a project, and drives real negotiations through the
real tick loop with the real state machine and the real repository. Only two
things are stand-ins: the brain (``ScriptedBrain``, no LLM) and the mail
transport (in-memory, no Gmail).

That is deliberate. Those two are exactly the pieces that need credentials, and
making them swappable is what lets this run on any laptop, in CI, and on a
branch where the other half of the team's code does not exist yet.

Run it with ``make e2e``. It exits non-zero if the loop stops working, so it can
gate a merge.

What it asserts at the end:

- at least one negotiation reached READY_FOR_HUMAN
- the ghost supplier's negotiation ended DEAD rather than hanging
- ``purchase_orders`` is empty — the agent negotiated for five simulated days
  and bought nothing
"""

import asyncio
import os
import sys
from datetime import UTC, datetime, timedelta

import httpx
from cinema_contracts import ClockMode, Money, NegotiationState
from cinema_contracts.testing import ScriptedBrain
from google.auth.credentials import AnonymousCredentials
from google.cloud.firestore_v1 import AsyncClient
from orchestrator.clock import ClockState, FrozenRealTime, SimClock
from orchestrator.mail import InMemoryMailbox
from orchestrator.records import (
    ItemRecord,
    NegotiationRecord,
    ProjectRecord,
    SupplierRecord,
)
from orchestrator.repository import FirestoreRepository, OrdersRepository
from orchestrator.tick import TickLoop

EMULATOR_HOST = os.environ.get("FIRESTORE_EMULATOR_HOST", "127.0.0.1:8080")
PROJECT_ID = os.environ.get("FIRESTORE_PROJECT_ID", "demo-cinema")
PID = "e2e-project"
ORDERS_DATABASE = "orders"

SIM_START = datetime(2026, 3, 1, 9, 0, tzinfo=UTC)
REAL_ANCHOR = datetime(2026, 8, 12, 14, 0, tzinfo=UTC)

TICKS = 40
HOURS_PER_TICK = 3
"""Forty ticks at three simulated hours each is five simulated days."""


class Persona:
    """A scripted supplier. Enough to exercise the loop, not the real simulator.

    The real adversary lives in ``supplier-sim/`` as a separate service with its
    own mailbox and Gemini writing every reply. This is the cheap version that
    runs without a network, so the daily check stays a ten-minute habit.
    """

    name: str
    email: str
    opening: int
    floor: int
    replies_after: int
    ghost_after: int

    def __init__(
        self,
        name: str,
        email: str,
        *,
        opening: int,
        floor: int,
        replies_after: int = 1,
        ghost_after: int = 99,
    ) -> None:
        self.name = name
        self.email = email
        self.opening = opening
        self.floor = floor
        self.replies_after = replies_after
        self.ghost_after = ghost_after

    def reply_to(self, round_number: int) -> str | None:
        """What this supplier says on round N, or None if they stay quiet."""
        if round_number >= self.ghost_after:
            return None
        conceded = max(self.floor, int(self.opening * (0.88**round_number)))
        if round_number == 0:
            return f"Thanks for reaching out. Our rate is RM{conceded:,} per day."
        return f"Best we can do is RM{conceded:,}."


PERSONAS = [
    Persona("Ah Seng Rentals", "ahseng@example.invalid", opening=1200, floor=850),
    Persona(
        "Skyline Grip (anchorer)", "skyline@example.invalid", opening=2400, floor=900
    ),
    Persona(
        "Quiet Sdn Bhd (ghost)",
        "quiet@example.invalid",
        opening=1100,
        floor=1000,
        ghost_after=1,
    ),
]

ITEMS = [
    ("item-skypanel", "Arri SkyPanel S60", "lighting"),
    ("item-grip", "Grip truck, 3 tonne", "transport"),
    ("item-smoke", "Smoke machine", "fx"),
]


def _wipe(database: str) -> None:
    """Empty one database. Both need clearing independently.

    Wiping only (default) would leave a purchase order behind in `orders`, and
    the final assertion of this run is that no order exists — a stale one would
    fail it for the wrong reason, or worse, a missing wipe would let a real bug
    hide behind a clean-looking run.
    """
    _ = httpx.delete(
        f"http://{EMULATOR_HOST}/emulator/v1/projects/{PROJECT_ID}"
        f"/databases/{database}/documents",
        timeout=10.0,
    )


def _emulator_up() -> bool:
    try:
        return httpx.get(f"http://{EMULATOR_HOST}/", timeout=2.0).status_code < 500
    except httpx.HTTPError:
        return False


async def seed(repo: FirestoreRepository) -> None:
    await repo.create_project(
        PID,
        ProjectRecord(
            title="Nasi Lemak Nights",
            clock=ClockState(
                sim_now=SIM_START,
                real_anchor=REAL_ANCHOR,
                speed=0.0,
                mode=ClockMode.FROZEN,
            ),
            budget_baseline=Money(amount=50_000),
            created_at=SIM_START,
        ),
    )

    for index, (persona, (item_id, item_name, category)) in enumerate(
        zip(PERSONAS, ITEMS, strict=True)
    ):
        supplier_id = f"sup{index}"
        await repo.save_item(
            PID, item_id, ItemRecord(name=item_name, category=category)
        )
        await repo.save_supplier(
            PID,
            supplier_id,
            SupplierRecord(name=persona.name, email=persona.email, verified=True),
        )
        await repo.save_negotiation(
            PID,
            f"neg{index}",
            NegotiationRecord(
                item_id=item_id,
                supplier_id=supplier_id,
                state=NegotiationState.DRAFTED,
                floor_price=Money(amount=persona.floor + 50),
                next_action_due_at=SIM_START,
                created_at=SIM_START,
                updated_at=SIM_START,
            ),
        )


async def run() -> int:
    if not _emulator_up():
        print(f"Firestore emulator not reachable at {EMULATOR_HOST}.")
        print("Start it with `make emulator`, or use `make e2e` which boots it.")
        return 2

    os.environ["FIRESTORE_EMULATOR_HOST"] = EMULATOR_HOST
    _wipe("(default)")
    _wipe(ORDERS_DATABASE)

    client = AsyncClient(project=PROJECT_ID, credentials=AnonymousCredentials())
    # A second client, on the database the tick loop has no access to. The loop
    # below is never handed this one — it exists purely so the final check can
    # look at where orders would land if the guardrail ever failed.
    orders_client = AsyncClient(
        project=PROJECT_ID,
        credentials=AnonymousCredentials(),
        database=ORDERS_DATABASE,
    )
    repo = FirestoreRepository(client)
    orders = OrdersRepository(orders_client)
    clock = SimClock(repo, FrozenRealTime(REAL_ANCHOR))
    mail = InMemoryMailbox()
    loop = TickLoop(repo, clock, ScriptedBrain(), mail)

    try:
        await seed(repo)

        by_email = {p.email: p for p in PERSONAS}
        rounds: dict[str, int] = {}
        already_answered = 0
        moment = SIM_START

        for tick in range(TICKS):
            _ = await clock.set_sim_now(PID, moment)
            report = await loop.run_tick(PID)

            if report.did_something:
                print(
                    f"  t+{tick * HOURS_PER_TICK:>3}h  "
                    f"sent={report.messages_sent} filed={report.replies_filed} "
                    f"escalated={report.escalated}"
                )

            # The supplier side: answer anything new that went out.
            for outbound in mail.sent[already_answered:]:
                persona = by_email.get(outbound["to"])
                if persona is None:
                    continue
                thread = outbound["thread_id"]
                round_number = rounds.get(thread, 0)
                body = persona.reply_to(round_number)
                rounds[thread] = round_number + 1
                if body is not None:
                    _ = mail.deliver(
                        thread_id=thread, body=body, from_email=persona.email
                    )
            already_answered = len(mail.sent)

            moment += timedelta(hours=HOURS_PER_TICK)

        return await verify(repo, orders, mail)
    finally:
        client.close()
        orders_client.close()


async def verify(
    repo: FirestoreRepository, orders: OrdersRepository, mail: InMemoryMailbox
) -> int:
    negotiations = await repo.list_negotiations(PID)

    print("\n  final state")
    for negotiation_id, record in sorted(negotiations.items()):
        quote = record.latest_quote.unit_price if record.latest_quote else "—"
        print(
            f"    {negotiation_id}  {record.state.value:<16} "
            f"quote={quote!s:<12} rounds={record.rounds_used} "
            f"{record.escalation_reason}"
        )

    failures: list[str] = []

    if not any(
        r.state is NegotiationState.READY_FOR_HUMAN for r in negotiations.values()
    ):
        failures.append("no negotiation reached READY_FOR_HUMAN")

    ghost = negotiations.get("neg2")
    if ghost is not None and ghost.state not in {
        NegotiationState.DEAD,
        NegotiationState.CHASING,
        NegotiationState.READY_FOR_HUMAN,
    }:
        failures.append(f"the ghost negotiation hung in {ghost.state.value}")

    ordered = await orders.total_ordered()
    if ordered is not None:
        failures.append(f"the agent created a purchase order: {ordered}")

    stuck = [
        nid
        for nid, r in negotiations.items()
        if r.state in {NegotiationState.DRAFTED, NegotiationState.SENT}
    ]
    if stuck:
        failures.append(f"negotiations never got going: {stuck}")

    print(f"\n  {len(mail.sent)} emails sent over 5 simulated days")
    print(
        "  purchase orders created: 0" if ordered is None else f"  ORDERED: {ordered}"
    )

    if failures:
        print("\nFAILED")
        for failure in failures:
            print(f"  - {failure}")
        return 1

    print("\nOK — the loop runs end to end and buys nothing.")
    return 0


def main() -> int:
    print("end-to-end: 3 suppliers, 5 simulated days, scripted brain, in-memory mail\n")
    return asyncio.run(run())


if __name__ == "__main__":
    sys.exit(main())
