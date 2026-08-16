"""Tick loop tests.

Run against the Firestore emulator with a frozen clock, the scripted brain and
the in-memory mailbox — so the loop under test is the real one, with only the
LLM and the network swapped out.

The two that matter most:

- ``test_a_tick_killed_halfway_leaves_the_rest_still_due`` — the Cloud Run
  failure mode the whole design is arranged around.
- ``test_a_full_run_never_creates_a_purchase_order`` — the stop condition,
  asserted against the database rather than against the state field.
"""

from datetime import UTC, datetime, timedelta
from typing import override

import pytest
from cinema_contracts import (
    ClockMode,
    EscalationReason,
    Money,
    NegotiationContext,
    NegotiationState,
    NextMove,
)
from cinema_contracts.testing import ScriptedBrain
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

PID = "proj1"
T0 = datetime(2026, 3, 1, 9, 0, tzinfo=UTC)
REAL0 = datetime(2026, 8, 12, 14, 0, tzinfo=UTC)


class _Harness:
    repo: FirestoreRepository
    clock: SimClock
    mail: InMemoryMailbox
    loop: TickLoop

    def __init__(self, client: AsyncClient, brain: ScriptedBrain | None = None) -> None:
        self.repo = FirestoreRepository(client)
        self.clock = SimClock(self.repo, FrozenRealTime(REAL0))
        self.mail = InMemoryMailbox()
        self.loop = TickLoop(self.repo, self.clock, brain or ScriptedBrain(), self.mail)

    async def setup_project(self) -> None:
        await self.repo.create_project(
            PID,
            ProjectRecord(
                title="Nasi Lemak Nights",
                # Frozen: these tests move simulated time explicitly rather than
                # depending on how long they take to run.
                clock=ClockState(
                    sim_now=T0, real_anchor=REAL0, speed=0.0, mode=ClockMode.FROZEN
                ),
                created_at=T0,
            ),
        )
        await self.repo.save_item(
            PID, "item1", ItemRecord(name="Arri SkyPanel S60", category="lighting")
        )
        await self.repo.save_supplier(
            PID,
            "sup1",
            SupplierRecord(
                name="Ah Seng Rentals", email="ahseng@example.invalid", verified=True
            ),
        )

    async def add_negotiation(
        self,
        negotiation_id: str = "neg1",
        *,
        item_id: str = "item1",
        state: NegotiationState = NegotiationState.DRAFTED,
        due: datetime | None = None,
        floor: Money | None = None,
    ) -> None:
        await self.repo.save_negotiation(
            PID,
            negotiation_id,
            NegotiationRecord(
                item_id=item_id,
                supplier_id="sup1",
                state=state,
                floor_price=floor,
                next_action_due_at=due if due is not None else T0,
                created_at=T0,
                updated_at=T0,
            ),
        )

    async def at(self, moment: datetime) -> None:
        _ = await self.clock.set_sim_now(PID, moment)

    async def state_of(self, negotiation_id: str = "neg1") -> NegotiationState:
        record = await self.repo.get_negotiation(PID, negotiation_id)
        assert record is not None
        return record.state


@pytest.fixture
async def harness(firestore: AsyncClient) -> _Harness:
    h = _Harness(firestore)
    await h.setup_project()
    return h


# --------------------------------------------------------------------------- #
# The basic arc
# --------------------------------------------------------------------------- #


async def test_the_first_tick_opens_the_conversation(harness: _Harness) -> None:
    await harness.add_negotiation()

    report = await harness.loop.run_tick(PID)

    assert report.messages_sent == 1
    assert await harness.state_of() is NegotiationState.AWAITING_REPLY

    sent = harness.mail.last_sent()
    assert sent is not None
    assert sent["to"] == "ahseng@example.invalid"
    assert "SkyPanel" in sent["body"]


async def test_the_loop_sends_what_the_brain_wrote_and_does_not_compose(
    harness: _Harness,
) -> None:
    await harness.add_negotiation()
    _ = await harness.loop.run_tick(PID)

    sent = harness.mail.last_sent()
    assert sent is not None
    move_body = sent["body"]
    assert move_body.startswith("Hi Ah Seng Rentals")


async def test_a_priced_reply_is_filed_and_becomes_a_quote(harness: _Harness) -> None:
    await harness.add_negotiation()
    _ = await harness.loop.run_tick(PID)

    thread = harness.mail.sent[0]["thread_id"]
    _ = harness.mail.deliver(thread_id=thread, body="We can do RM1,250 per day.")

    await harness.at(T0 + timedelta(hours=6))
    report = await harness.loop.run_tick(PID)

    assert report.replies_filed == 1
    record = await harness.repo.get_negotiation(PID, "neg1")
    assert record is not None
    assert record.latest_quote is not None
    assert record.latest_quote.unit_price == Money(amount=1250)
    assert record.first_quote is not None


async def test_a_quote_above_the_floor_produces_a_counter(harness: _Harness) -> None:
    await harness.add_negotiation(floor=Money(amount=900))
    _ = await harness.loop.run_tick(PID)
    thread = harness.mail.sent[0]["thread_id"]
    _ = harness.mail.deliver(thread_id=thread, body="RM1,250 per day.")

    await harness.at(T0 + timedelta(hours=6))
    # One pass: the reply is filed, which marks the negotiation due now, and the
    # same pass then decides on it. A new price does not wait out a timer.
    _ = await harness.loop.run_tick(PID)

    record = await harness.repo.get_negotiation(PID, "neg1")
    assert record is not None
    assert record.state is NegotiationState.NEGOTIATING
    assert record.rounds_used == 1
    assert record.target_price == Money(amount=900)
    assert len(harness.mail.sent) == 2
    assert harness.mail.sent[1]["thread_id"] == thread


async def test_a_counter_stays_in_the_same_email_thread(harness: _Harness) -> None:
    """Threading is set from the stored thread ID, never from the subject."""
    await harness.add_negotiation(floor=Money(amount=900))
    _ = await harness.loop.run_tick(PID)
    thread = harness.mail.sent[0]["thread_id"]

    # The supplier rewrites the subject, as they do.
    _ = harness.mail.deliver(
        thread_id=thread, subject="quotation for your shoot", body="RM1,250"
    )
    await harness.at(T0 + timedelta(hours=6))
    _ = await harness.loop.run_tick(PID)

    opening, counter = harness.mail.sent[0], harness.mail.sent[1]
    assert counter["thread_id"] == thread
    assert counter["in_reply_to"] == opening["rfc822_message_id"]
    assert counter["references"] == opening["rfc822_message_id"]


async def test_in_reply_to_uses_the_header_id_not_the_transport_id(
    harness: _Harness,
) -> None:
    """The two ids are different strings and only one of them threads.

    Gmail's send returns an API handle; ``In-Reply-To`` needs the ``Message-ID``
    header. Using the handle produces a header no client matches, so replies
    fork into new threads — and our own routing is by thread_id, so nothing
    inside the system would notice.
    """
    await harness.add_negotiation(floor=Money(amount=900))
    _ = await harness.loop.run_tick(PID)
    thread = harness.mail.sent[0]["thread_id"]
    _ = harness.mail.deliver(thread_id=thread, body="RM1,250")
    await harness.at(T0 + timedelta(hours=6))
    _ = await harness.loop.run_tick(PID)

    opening, counter = harness.mail.sent[0], harness.mail.sent[1]
    assert opening["message_id"] != opening["rfc822_message_id"]
    assert counter["in_reply_to"] != opening["message_id"]
    assert counter["in_reply_to"].startswith("<")
    assert counter["in_reply_to"].endswith(">")


async def test_the_thread_root_is_recorded_once_and_kept(harness: _Harness) -> None:
    """References stays bounded: root plus latest, not the whole chain."""
    await harness.add_negotiation(floor=Money(amount=1))
    _ = await harness.loop.run_tick(PID)
    thread = harness.mail.sent[0]["thread_id"]
    root = harness.mail.sent[0]["rfc822_message_id"]

    moment = T0
    for _ in range(3):
        _ = harness.mail.deliver(thread_id=thread, body="RM1,250")
        moment += timedelta(hours=6)
        await harness.at(moment)
        _ = await harness.loop.run_tick(PID)

    record = await harness.repo.get_negotiation(PID, "neg1")
    assert record is not None
    assert record.thread_root_rfc822_id == root
    assert record.last_rfc822_id == harness.mail.sent[-1]["rfc822_message_id"]
    assert len(harness.mail.sent[-1]["references"].split()) == 2


async def test_a_quote_at_the_floor_stops_for_a_human(harness: _Harness) -> None:
    await harness.add_negotiation(floor=Money(amount=1300))
    _ = await harness.loop.run_tick(PID)
    thread = harness.mail.sent[0]["thread_id"]
    _ = harness.mail.deliver(thread_id=thread, body="RM1,250 per day.")

    await harness.at(T0 + timedelta(hours=6))
    report = await harness.loop.run_tick(PID)

    record = await harness.repo.get_negotiation(PID, "neg1")
    assert record is not None
    assert record.state is NegotiationState.READY_FOR_HUMAN
    assert record.escalation_reason == EscalationReason.GOOD_QUOTE.value
    assert record.next_action_due_at is None, "a stopped negotiation must not be due"
    assert report.escalated == 1


async def test_a_supplier_who_keeps_emailing_after_the_stop_changes_nothing(
    harness: _Harness,
) -> None:
    """The message is recorded; the negotiation stays stopped.

    Suppliers do carry on emailing after we have gone quiet on them. The
    timeline should show it, but nothing may restart, and the number the
    producer is about to approve must not move under them.
    """
    await harness.add_negotiation(floor=Money(amount=1300))
    _ = await harness.loop.run_tick(PID)
    thread = harness.mail.sent[0]["thread_id"]
    _ = harness.mail.deliver(thread_id=thread, body="RM1,250 per day.")
    await harness.at(T0 + timedelta(hours=6))
    _ = await harness.loop.run_tick(PID)

    _ = harness.mail.deliver(thread_id=thread, body="Actually we can do RM990.")
    await harness.at(T0 + timedelta(hours=12))
    report = await harness.loop.run_tick(PID)

    record = await harness.repo.get_negotiation(PID, "neg1")
    assert record is not None
    assert report.replies_after_stop == 1
    assert record.state is NegotiationState.READY_FOR_HUMAN
    assert record.latest_quote is not None
    assert record.latest_quote.unit_price == Money(amount=1250), (
        "the price under review must not change while a human is deciding"
    )
    assert len(await harness.repo.list_messages(PID, "neg1")) == 3


# --------------------------------------------------------------------------- #
# The stop condition
# --------------------------------------------------------------------------- #


async def test_a_full_run_never_creates_a_purchase_order(
    harness: _Harness, orders_firestore: AsyncClient
) -> None:
    """Drive a negotiation all the way to its stop and check the database.

    Asserted against ``purchase_orders`` rather than against the state field,
    because the state field is what the agent writes and the collection is what
    actually costs money.
    """
    await harness.add_negotiation(floor=Money(amount=1300))

    moment = T0
    for _ in range(10):
        await harness.at(moment)
        _ = await harness.loop.run_tick(PID)
        if harness.mail.sent:
            _ = harness.mail.deliver(
                thread_id=harness.mail.sent[-1]["thread_id"], body="RM1,250"
            )
        moment += timedelta(hours=6)

    assert await harness.state_of() is NegotiationState.READY_FOR_HUMAN

    # Checked against the orders database, which the tick loop has no client
    # for and no method to reach. If this ever fails, the separation is gone.
    orders = OrdersRepository(orders_firestore)
    assert await orders.get_purchase_order("item1") is None
    assert await orders.total_ordered() is None


async def test_an_attachment_escalates_rather_than_guessing(harness: _Harness) -> None:
    await harness.add_negotiation()
    _ = await harness.loop.run_tick(PID)
    thread = harness.mail.sent[0]["thread_id"]
    _ = harness.mail.deliver(
        thread_id=thread,
        body="Please see attached for our rates.",
        has_attachments=True,
        attachment_filenames=["rates.pdf"],
    )

    await harness.at(T0 + timedelta(hours=6))
    _ = await harness.loop.run_tick(PID)

    record = await harness.repo.get_negotiation(PID, "neg1")
    assert record is not None
    assert record.state is NegotiationState.READY_FOR_HUMAN
    assert record.escalation_reason == EscalationReason.PRICE_IN_ATTACHMENT.value
    assert record.latest_quote is None


async def test_an_unreadable_reply_escalates(harness: _Harness) -> None:
    await harness.add_negotiation()
    _ = await harness.loop.run_tick(PID)
    _ = harness.mail.deliver(
        thread_id=harness.mail.sent[0]["thread_id"], body="who is this?"
    )

    await harness.at(T0 + timedelta(hours=6))
    _ = await harness.loop.run_tick(PID)

    record = await harness.repo.get_negotiation(PID, "neg1")
    assert record is not None
    assert record.state is NegotiationState.READY_FOR_HUMAN
    assert record.escalation_reason == EscalationReason.UNPARSEABLE_REPLY.value


# --------------------------------------------------------------------------- #
# Surviving being killed
# --------------------------------------------------------------------------- #


class _BrainThatDiesOnThirdCall(ScriptedBrain):
    """Stands in for Cloud Run reaping the instance mid-pass."""

    calls: int

    def __init__(self) -> None:
        super().__init__()
        self.calls = 0

    @override
    async def next_move(self, ctx: NegotiationContext) -> NextMove:
        self.calls += 1
        if self.calls == 3:
            raise RuntimeError("instance reaped")
        return await super().next_move(ctx)


async def test_a_tick_killed_halfway_leaves_the_rest_still_due(
    firestore: AsyncClient,
) -> None:
    """Work completed before the kill is persisted; the rest is picked up next tick.

    This is the failure the whole design is arranged around — it works on a
    laptop and only shows up during judging.
    """
    harness = _Harness(firestore, brain=_BrainThatDiesOnThirdCall())
    await harness.setup_project()
    for index in range(5):
        await harness.add_negotiation(
            f"neg{index}", due=T0 - timedelta(hours=index + 1)
        )

    with pytest.raises(RuntimeError, match="instance reaped"):
        _ = await harness.loop.run_tick(PID)

    # Two negotiations got their opening mail out before the process died.
    assert len(harness.mail.sent) == 2

    remaining = await harness.repo.due_negotiations(T0)
    assert len(remaining) == 3, "unprocessed negotiations must still be due"

    # A fresh loop, as a new instance would be, finishes the job.
    recovered = _Harness(firestore)
    report = await recovered.loop.run_tick(PID)
    assert report.messages_sent == 3
    assert not await recovered.repo.due_negotiations(T0)


async def test_a_redelivered_reply_does_not_burn_a_round(harness: _Harness) -> None:
    await harness.add_negotiation(floor=Money(amount=900))
    _ = await harness.loop.run_tick(PID)
    thread = harness.mail.sent[0]["thread_id"]

    reply = harness.mail.deliver(thread_id=thread, body="RM1,250")
    await harness.at(T0 + timedelta(hours=6))
    _ = await harness.loop.run_tick(PID)

    # Gmail hands us the same message again after a killed tick.
    harness.mail._inbox.append(reply)  # pyright: ignore[reportPrivateUsage]
    report = await harness.loop.run_tick(PID)

    assert report.replies_skipped == 1
    assert report.replies_filed == 0
    messages = await harness.repo.list_messages(PID, "neg1")
    assert sum(1 for m in messages if m.gmail_message_id == reply.message_id) == 1


# --------------------------------------------------------------------------- #
# Scheduling
# --------------------------------------------------------------------------- #


async def test_the_agent_does_not_email_twice_before_getting_an_answer(
    harness: _Harness,
) -> None:
    """While the ball is in the supplier's court, the loop stays quiet.

    Two things break without this. A supplier gets three emails in a row and
    stops taking us seriously; and, less obviously, the silence window becomes
    unreachable — every new outbound resets last_outbound_at, so a supplier who
    stopped replying is never noticed and never dies.
    """
    await harness.add_negotiation()
    _ = await harness.loop.run_tick(PID)
    assert len(harness.mail.sent) == 1

    for hours in (6, 12, 24, 36):
        await harness.at(T0 + timedelta(hours=hours))
        _ = await harness.loop.run_tick(PID)

    assert len(harness.mail.sent) == 1, "the supplier was emailed more than once"
    assert await harness.state_of() is NegotiationState.AWAITING_REPLY


async def test_a_supplier_who_answers_once_then_vanishes_ends_dead(
    harness: _Harness,
) -> None:
    """The ghost persona, end to end. It must not hang in NEGOTIATING forever."""
    await harness.add_negotiation(floor=Money(amount=200))
    _ = await harness.loop.run_tick(PID)
    thread = harness.mail.sent[0]["thread_id"]
    _ = harness.mail.deliver(thread_id=thread, body="RM1,100")

    moment = T0
    for _ in range(40):
        moment += timedelta(hours=6)
        await harness.at(moment)
        _ = await harness.loop.run_tick(PID)

    assert await harness.state_of() is NegotiationState.DEAD


async def test_silence_past_the_window_moves_to_chasing(harness: _Harness) -> None:
    await harness.add_negotiation()
    _ = await harness.loop.run_tick(PID)

    await harness.at(T0 + timedelta(hours=60))
    _ = await harness.loop.run_tick(PID)

    assert await harness.state_of() is NegotiationState.CHASING


async def test_a_terminal_negotiation_is_never_examined_again(
    harness: _Harness,
) -> None:
    await harness.add_negotiation(state=NegotiationState.DEAD)

    report = await harness.loop.run_tick(PID)

    assert report.negotiations_examined == 0
    assert report.messages_sent == 0


async def test_mail_for_an_unknown_thread_is_counted_not_crashed(
    harness: _Harness,
) -> None:
    await harness.add_negotiation()
    _ = harness.mail.deliver(thread_id="thread-we-never-started", body="RM500")

    report = await harness.loop.run_tick(PID)

    assert report.unmatched_replies == 1
    assert not report.errors


async def test_the_next_check_is_clamped_into_something_sane(
    harness: _Harness,
) -> None:
    await harness.add_negotiation()
    _ = await harness.loop.run_tick(PID)

    record = await harness.repo.get_negotiation(PID, "neg1")
    assert record is not None
    assert record.next_action_due_at is not None
    ahead = (record.next_action_due_at - T0).total_seconds() / 3600.0
    assert 1.0 <= ahead <= 72.0
