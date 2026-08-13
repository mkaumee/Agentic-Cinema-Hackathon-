"""The negotiation state machine.

::

    DRAFTED -> SENT -> AWAITING_REPLY -> QUOTED -> NEGOTIATING (loops)
                            | silence                  | floor hit / rounds spent
                         CHASING -> DEAD         READY_FOR_HUMAN -> ORDERED

Two properties matter more than the diagram:

**The agent cannot reach ORDERED.** Not by policy, not by prompt, not by a
threshold. ``ORDERED`` has exactly one inbound edge — ``HUMAN_APPROVED`` from
``READY_FOR_HUMAN`` — and ``HUMAN_APPROVED`` is not in ``AGENT_EVENTS``. A test
walks the whole graph from every state using only agent-driven events and
asserts ``ORDERED`` is unreachable. That test is the claim.

**Illegal transitions raise.** There is no silent fallback to the current state.
A negotiation that ends up somewhere unexpected fails loudly on the tick that
gets it wrong, instead of quietly stalling and looking like a supplier who
never replied.

``SENT`` and ``AWAITING_REPLY`` are deliberately separate. ``SENT`` means the
message went to Gmail; ``AWAITING_REPLY`` means we recorded the thread ID and
know how to match the response. A crash between those two writes leaves a
negotiation in ``SENT``, which is exactly the signal the next tick needs to go
looking for an orphaned thread rather than sending the email twice.
"""

from enum import StrEnum

from cinema_contracts import TERMINAL_STATES, MoveAction, NegotiationState


class NegotiationEvent(StrEnum):
    """Things that happen to a negotiation.

    Split into agent-driven and human-driven below, which is what the
    unreachability proof rests on.
    """

    OPENING_SENT = "OPENING_SENT"
    SEND_CONFIRMED = "SEND_CONFIRMED"
    COUNTER_SENT = "COUNTER_SENT"
    CHASE_SENT = "CHASE_SENT"

    QUOTE_RECEIVED = "QUOTE_RECEIVED"
    REPLY_NEEDS_HUMAN = "REPLY_NEEDS_HUMAN"
    SILENCE_TIMEOUT = "SILENCE_TIMEOUT"

    AGENT_ESCALATED = "AGENT_ESCALATED"
    AGENT_WALKED_AWAY = "AGENT_WALKED_AWAY"

    HUMAN_APPROVED = "HUMAN_APPROVED"
    HUMAN_RETURNED_WITH_FLOOR = "HUMAN_RETURNED_WITH_FLOOR"
    HUMAN_CANCELLED = "HUMAN_CANCELLED"


HUMAN_EVENTS: frozenset[NegotiationEvent] = frozenset(
    {
        NegotiationEvent.HUMAN_APPROVED,
        NegotiationEvent.HUMAN_RETURNED_WITH_FLOOR,
        NegotiationEvent.HUMAN_CANCELLED,
    }
)
"""Events only a human-authenticated request may raise.

The tick loop refuses to apply any of these. They arrive through endpoints that
require a producer identity, and ``HUMAN_APPROVED`` additionally requires a
Firestore rule the agent's service account cannot satisfy.
"""

AGENT_EVENTS: frozenset[NegotiationEvent] = frozenset(NegotiationEvent) - HUMAN_EVENTS


_TRANSITIONS: dict[tuple[NegotiationState, NegotiationEvent], NegotiationState] = {
    # Nothing sent yet.
    (NegotiationState.DRAFTED, NegotiationEvent.OPENING_SENT): NegotiationState.SENT,
    (NegotiationState.DRAFTED, NegotiationEvent.AGENT_ESCALATED): (
        NegotiationState.READY_FOR_HUMAN
    ),
    (NegotiationState.DRAFTED, NegotiationEvent.AGENT_WALKED_AWAY): (
        NegotiationState.DEAD
    ),
    (NegotiationState.DRAFTED, NegotiationEvent.HUMAN_CANCELLED): (
        NegotiationState.DEAD
    ),
    # Handed to Gmail, thread not yet recorded.
    (NegotiationState.SENT, NegotiationEvent.SEND_CONFIRMED): (
        NegotiationState.AWAITING_REPLY
    ),
    (NegotiationState.SENT, NegotiationEvent.QUOTE_RECEIVED): NegotiationState.QUOTED,
    (NegotiationState.SENT, NegotiationEvent.REPLY_NEEDS_HUMAN): (
        NegotiationState.READY_FOR_HUMAN
    ),
    (NegotiationState.SENT, NegotiationEvent.HUMAN_CANCELLED): NegotiationState.DEAD,
    # Waiting on a first reply.
    (NegotiationState.AWAITING_REPLY, NegotiationEvent.QUOTE_RECEIVED): (
        NegotiationState.QUOTED
    ),
    (NegotiationState.AWAITING_REPLY, NegotiationEvent.REPLY_NEEDS_HUMAN): (
        NegotiationState.READY_FOR_HUMAN
    ),
    (NegotiationState.AWAITING_REPLY, NegotiationEvent.SILENCE_TIMEOUT): (
        NegotiationState.CHASING
    ),
    (NegotiationState.AWAITING_REPLY, NegotiationEvent.AGENT_ESCALATED): (
        NegotiationState.READY_FOR_HUMAN
    ),
    (NegotiationState.AWAITING_REPLY, NegotiationEvent.AGENT_WALKED_AWAY): (
        NegotiationState.DEAD
    ),
    (NegotiationState.AWAITING_REPLY, NegotiationEvent.HUMAN_CANCELLED): (
        NegotiationState.DEAD
    ),
    # A price is on the table.
    (NegotiationState.QUOTED, NegotiationEvent.COUNTER_SENT): (
        NegotiationState.NEGOTIATING
    ),
    (NegotiationState.QUOTED, NegotiationEvent.QUOTE_RECEIVED): (
        NegotiationState.QUOTED
    ),
    (NegotiationState.QUOTED, NegotiationEvent.REPLY_NEEDS_HUMAN): (
        NegotiationState.READY_FOR_HUMAN
    ),
    (NegotiationState.QUOTED, NegotiationEvent.AGENT_ESCALATED): (
        NegotiationState.READY_FOR_HUMAN
    ),
    (NegotiationState.QUOTED, NegotiationEvent.AGENT_WALKED_AWAY): (
        NegotiationState.DEAD
    ),
    (NegotiationState.QUOTED, NegotiationEvent.HUMAN_CANCELLED): NegotiationState.DEAD,
    # Mid-haggle.
    (NegotiationState.NEGOTIATING, NegotiationEvent.QUOTE_RECEIVED): (
        NegotiationState.QUOTED
    ),
    (NegotiationState.NEGOTIATING, NegotiationEvent.COUNTER_SENT): (
        NegotiationState.NEGOTIATING
    ),
    (NegotiationState.NEGOTIATING, NegotiationEvent.REPLY_NEEDS_HUMAN): (
        NegotiationState.READY_FOR_HUMAN
    ),
    (NegotiationState.NEGOTIATING, NegotiationEvent.SILENCE_TIMEOUT): (
        NegotiationState.CHASING
    ),
    (NegotiationState.NEGOTIATING, NegotiationEvent.AGENT_ESCALATED): (
        NegotiationState.READY_FOR_HUMAN
    ),
    (NegotiationState.NEGOTIATING, NegotiationEvent.AGENT_WALKED_AWAY): (
        NegotiationState.DEAD
    ),
    (NegotiationState.NEGOTIATING, NegotiationEvent.HUMAN_CANCELLED): (
        NegotiationState.DEAD
    ),
    # Gone quiet on us.
    (NegotiationState.CHASING, NegotiationEvent.CHASE_SENT): NegotiationState.CHASING,
    (NegotiationState.CHASING, NegotiationEvent.QUOTE_RECEIVED): (
        NegotiationState.QUOTED
    ),
    (NegotiationState.CHASING, NegotiationEvent.REPLY_NEEDS_HUMAN): (
        NegotiationState.READY_FOR_HUMAN
    ),
    (NegotiationState.CHASING, NegotiationEvent.SILENCE_TIMEOUT): (
        NegotiationState.DEAD
    ),
    (NegotiationState.CHASING, NegotiationEvent.AGENT_ESCALATED): (
        NegotiationState.READY_FOR_HUMAN
    ),
    (NegotiationState.CHASING, NegotiationEvent.AGENT_WALKED_AWAY): (
        NegotiationState.DEAD
    ),
    (NegotiationState.CHASING, NegotiationEvent.HUMAN_CANCELLED): NegotiationState.DEAD,
    # Waiting on a person. No agent event is accepted here — this is the stop.
    (NegotiationState.READY_FOR_HUMAN, NegotiationEvent.HUMAN_APPROVED): (
        NegotiationState.ORDERED
    ),
    (NegotiationState.READY_FOR_HUMAN, NegotiationEvent.HUMAN_RETURNED_WITH_FLOOR): (
        NegotiationState.NEGOTIATING
    ),
    (NegotiationState.READY_FOR_HUMAN, NegotiationEvent.HUMAN_CANCELLED): (
        NegotiationState.DEAD
    ),
    # DEAD and ORDERED are terminal and appear nowhere as a source.
}


class IllegalTransitionError(RuntimeError):
    """Raised when an event arrives that the current state has no edge for.

    Deliberately not caught anywhere in the tick loop. A negotiation reaching an
    unexpected state is a bug we want visible on the tick that causes it, not
    one that presents later as a supplier who mysteriously stopped replying.
    """

    state: NegotiationState
    event: NegotiationEvent

    def __init__(self, state: NegotiationState, event: NegotiationEvent) -> None:
        allowed = sorted(e.value for e in allowed_events(state))
        super().__init__(
            f"{event.value} is not legal in state {state.value}. "
            f"Allowed here: {allowed or 'nothing — this state is terminal'}."
        )
        self.state = state
        self.event = event


def allowed_events(state: NegotiationState) -> frozenset[NegotiationEvent]:
    """Every event with an outbound edge from this state."""
    return frozenset(event for (src, event) in _TRANSITIONS if src == state)


def apply_event(state: NegotiationState, event: NegotiationEvent) -> NegotiationState:
    """Advance a negotiation, or raise.

    Pure. Persisting the result is the caller's job, and doing so in the same
    Firestore write as everything else the tick changed is what keeps a killed
    tick from leaving a half-applied transition.
    """
    try:
        return _TRANSITIONS[(state, event)]
    except KeyError:
        raise IllegalTransitionError(state, event) from None


def is_terminal(state: NegotiationState) -> bool:
    """Terminal negotiations are never scheduled again by the tick loop."""
    return state in TERMINAL_STATES


_MOVE_EVENTS: dict[MoveAction, NegotiationEvent | None] = {
    MoveAction.SEND_OPENING: NegotiationEvent.OPENING_SENT,
    MoveAction.COUNTER: NegotiationEvent.COUNTER_SENT,
    MoveAction.CHASE: NegotiationEvent.CHASE_SENT,
    MoveAction.ACCEPT: NegotiationEvent.AGENT_ESCALATED,
    MoveAction.ESCALATE: NegotiationEvent.AGENT_ESCALATED,
    MoveAction.WALK_AWAY: NegotiationEvent.AGENT_WALKED_AWAY,
    MoveAction.WAIT: None,
}


def event_for_move(action: MoveAction) -> NegotiationEvent | None:
    """Translate the brain's decision into a state-machine event.

    ``ACCEPT`` maps to ``AGENT_ESCALATED``, the same as ``ESCALATE``. The brain
    accepting a price means "this one is good, a human should sign it" — it
    does not mean buy, and there is no edge here that could make it mean buy.

    ``WAIT`` maps to nothing: the negotiation keeps its state and only its next
    due time changes.
    """
    return _MOVE_EVENTS[action]
