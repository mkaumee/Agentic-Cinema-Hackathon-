"""Closed vocabularies shared by both halves of the system.

These are ``StrEnum`` so they round-trip through Firestore and JSON as plain
readable strings, which matters when a judge opens the Firestore console.
"""

from enum import StrEnum


class NegotiationState(StrEnum):
    """Where a single item-supplier negotiation has got to.

        DRAFTED -> SENT -> AWAITING_REPLY -> QUOTED -> NEGOTIATING (loops)
                              | silence                    | floor hit / rounds spent
                           CHASING -> DEAD           READY_FOR_HUMAN -> ORDERED

    ``READY_FOR_HUMAN`` is where the agent always stops. ``ORDERED`` is the only
    state that writes a purchase order, and only a human-authenticated request
    can move a negotiation into it.
    """

    DRAFTED = "DRAFTED"
    SENT = "SENT"
    AWAITING_REPLY = "AWAITING_REPLY"
    QUOTED = "QUOTED"
    NEGOTIATING = "NEGOTIATING"
    CHASING = "CHASING"
    DEAD = "DEAD"
    READY_FOR_HUMAN = "READY_FOR_HUMAN"
    ORDERED = "ORDERED"


TERMINAL_STATES: frozenset[NegotiationState] = frozenset(
    {NegotiationState.DEAD, NegotiationState.ORDERED}
)
"""States the tick loop will never schedule again."""


class MoveAction(StrEnum):
    """What the brain decided to do next.

    Role B maps each of these onto a state transition and a next due time.
    Role A never writes state directly.
    """

    SEND_OPENING = "SEND_OPENING"
    """First contact. Introduce the requirement and ask for a quote."""

    COUNTER = "COUNTER"
    """Push back on the current quote with a target price."""

    ACCEPT = "ACCEPT"
    """The quote is good enough. Hands off to a human — never buys."""

    CHASE = "CHASE"
    """Silence past the expected reply window. Nudge them."""

    WALK_AWAY = "WALK_AWAY"
    """This supplier is not going to work out. Ends at DEAD."""

    ESCALATE = "ESCALATE"
    """Something needs a person. Ends at READY_FOR_HUMAN."""

    WAIT = "WAIT"
    """Nothing to do yet. Reschedule and look again later."""


class EscalationReason(StrEnum):
    """Why a negotiation stopped and asked for a human.

    Surfaced verbatim in the UI, so these read as sentences a producer
    understands rather than as error codes.
    """

    GOOD_QUOTE = "GOOD_QUOTE"
    """Negotiation succeeded and is ready to approve."""

    FLOOR_REACHED = "FLOOR_REACHED"
    """The supplier will not go below the producer's stated floor."""

    ROUNDS_EXHAUSTED = "ROUNDS_EXHAUSTED"
    """Used the agreed number of rounds without converging."""

    UNPARSEABLE_REPLY = "UNPARSEABLE_REPLY"
    """The reply could not be read as a quote. The agent must not guess."""

    PRICE_IN_ATTACHMENT = "PRICE_IN_ATTACHMENT"
    """The number is inside a PDF or image. Out of scope to extract; ask a human."""

    AMBIGUOUS_TERMS = "AMBIGUOUS_TERMS"
    """A price exists but what it covers is unclear — delivery, tax, duration."""

    SUPPLIER_QUESTION = "SUPPLIER_QUESTION"
    """The supplier asked something the agent is not authorised to answer."""

    BUDGET_EXCEEDED = "BUDGET_EXCEEDED"
    """Even the best offer is above the reference band or the item budget."""


class MessageDirection(StrEnum):
    OUTBOUND = "OUTBOUND"
    INBOUND = "INBOUND"


class ClockMode(StrEnum):
    LIVE = "LIVE"
    """Simulated time advances 1:1 with real time."""

    DEMO = "DEMO"
    """Simulated time advances at ``speed`` multiplied by real elapsed seconds."""

    FROZEN = "FROZEN"
    """Simulated time does not advance on its own. Tests drive it explicitly."""
