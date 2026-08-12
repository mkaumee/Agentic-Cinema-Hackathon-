"""Tests for the negotiation state machine.

The important one is ``test_the_agent_cannot_reach_ordered_from_anywhere``. It
walks the entire graph using only agent-driven events and asserts the purchase
state is unreachable. Everything else in the system says the agent does not
buy; this is the part that proves it.
"""

import pytest
from cinema_contracts import MoveAction, NegotiationState
from orchestrator.state_machine import (
    AGENT_EVENTS,
    HUMAN_EVENTS,
    IllegalTransitionError,
    NegotiationEvent,
    allowed_events,
    apply_event,
    event_for_move,
    is_terminal,
)


def _walk(start: NegotiationState, *events: NegotiationEvent) -> NegotiationState:
    state = start
    for event in events:
        state = apply_event(state, event)
    return state


# --------------------------------------------------------------------------- #
# The paths a negotiation actually takes
# --------------------------------------------------------------------------- #


def test_the_happy_path_ends_at_a_human_not_at_a_purchase() -> None:
    state = _walk(
        NegotiationState.DRAFTED,
        NegotiationEvent.OPENING_SENT,
        NegotiationEvent.SEND_CONFIRMED,
        NegotiationEvent.QUOTE_RECEIVED,
        NegotiationEvent.COUNTER_SENT,
        NegotiationEvent.QUOTE_RECEIVED,
        NegotiationEvent.AGENT_ESCALATED,
    )
    assert state is NegotiationState.READY_FOR_HUMAN


def test_only_a_human_turns_a_ready_negotiation_into_an_order() -> None:
    assert (
        apply_event(NegotiationState.READY_FOR_HUMAN, NegotiationEvent.HUMAN_APPROVED)
        is NegotiationState.ORDERED
    )


def test_a_producer_can_hand_it_back_with_a_floor_instead_of_approving() -> None:
    assert (
        apply_event(
            NegotiationState.READY_FOR_HUMAN,
            NegotiationEvent.HUMAN_RETURNED_WITH_FLOOR,
        )
        is NegotiationState.NEGOTIATING
    )


def test_silence_leads_to_a_chase_and_then_to_dead() -> None:
    state = _walk(
        NegotiationState.AWAITING_REPLY,
        NegotiationEvent.SILENCE_TIMEOUT,
        NegotiationEvent.CHASE_SENT,
        NegotiationEvent.SILENCE_TIMEOUT,
    )
    assert state is NegotiationState.DEAD


def test_a_ghost_who_answers_late_is_still_recoverable() -> None:
    state = _walk(
        NegotiationState.AWAITING_REPLY,
        NegotiationEvent.SILENCE_TIMEOUT,
        NegotiationEvent.CHASE_SENT,
        NegotiationEvent.QUOTE_RECEIVED,
    )
    assert state is NegotiationState.QUOTED


def test_negotiation_can_loop_without_changing_state() -> None:
    state = _walk(
        NegotiationState.QUOTED,
        NegotiationEvent.COUNTER_SENT,
        NegotiationEvent.QUOTE_RECEIVED,
        NegotiationEvent.COUNTER_SENT,
    )
    assert state is NegotiationState.NEGOTIATING


def test_an_unreadable_reply_escalates_from_every_state_that_can_receive_one() -> None:
    receiving = (
        NegotiationState.SENT,
        NegotiationState.AWAITING_REPLY,
        NegotiationState.QUOTED,
        NegotiationState.NEGOTIATING,
        NegotiationState.CHASING,
    )
    for state in receiving:
        assert (
            apply_event(state, NegotiationEvent.REPLY_NEEDS_HUMAN)
            is NegotiationState.READY_FOR_HUMAN
        )


# --------------------------------------------------------------------------- #
# The guarantee
# --------------------------------------------------------------------------- #


def test_the_agent_cannot_reach_ordered_from_anywhere() -> None:
    """Exhaustive reachability proof over agent-driven events only.

    If someone later adds an edge that lets the agent buy something, this fails
    and names the state it started from.
    """
    for start in NegotiationState:
        seen: set[NegotiationState] = {start}
        frontier = [start]
        while frontier:
            current = frontier.pop()
            for event in AGENT_EVENTS:
                try:
                    nxt = apply_event(current, event)
                except IllegalTransitionError:
                    continue
                if nxt not in seen:
                    seen.add(nxt)
                    frontier.append(nxt)

        assert NegotiationState.ORDERED not in seen or start is (
            NegotiationState.ORDERED
        ), f"the agent can reach ORDERED starting from {start.value}"


def test_ordered_has_exactly_one_inbound_edge() -> None:
    inbound = [
        (src, event)
        for src in NegotiationState
        for event in NegotiationEvent
        if _reaches(src, event, NegotiationState.ORDERED)
    ]
    assert inbound == [
        (NegotiationState.READY_FOR_HUMAN, NegotiationEvent.HUMAN_APPROVED)
    ]


def _reaches(
    src: NegotiationState, event: NegotiationEvent, target: NegotiationState
) -> bool:
    try:
        return apply_event(src, event) is target
    except IllegalTransitionError:
        return False


def test_the_stop_state_accepts_nothing_the_agent_can_do() -> None:
    for event in AGENT_EVENTS:
        with pytest.raises(IllegalTransitionError):
            _ = apply_event(NegotiationState.READY_FOR_HUMAN, event)


def test_approval_is_not_an_agent_event() -> None:
    assert NegotiationEvent.HUMAN_APPROVED in HUMAN_EVENTS
    assert NegotiationEvent.HUMAN_APPROVED not in AGENT_EVENTS
    assert not (AGENT_EVENTS & HUMAN_EVENTS)


# --------------------------------------------------------------------------- #
# Structural sanity
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("state", [NegotiationState.DEAD, NegotiationState.ORDERED])
def test_terminal_states_accept_no_events_at_all(state: NegotiationState) -> None:
    assert is_terminal(state)
    assert allowed_events(state) == frozenset()
    for event in NegotiationEvent:
        with pytest.raises(IllegalTransitionError):
            _ = apply_event(state, event)


def test_every_non_terminal_state_has_a_way_out() -> None:
    for state in NegotiationState:
        if is_terminal(state):
            continue
        assert allowed_events(state), f"{state.value} is a dead end"


def test_every_non_terminal_state_can_be_cancelled_by_a_human() -> None:
    """A producer must always be able to stop a negotiation they started."""
    for state in NegotiationState:
        if is_terminal(state):
            continue
        assert (
            apply_event(state, NegotiationEvent.HUMAN_CANCELLED)
            is NegotiationState.DEAD
        )


def test_illegal_transitions_raise_and_say_what_was_allowed() -> None:
    with pytest.raises(IllegalTransitionError) as caught:
        _ = apply_event(NegotiationState.DRAFTED, NegotiationEvent.QUOTE_RECEIVED)

    message = str(caught.value)
    assert "QUOTE_RECEIVED is not legal in state DRAFTED" in message
    assert "OPENING_SENT" in message


def test_terminal_error_message_explains_itself() -> None:
    with pytest.raises(IllegalTransitionError) as caught:
        _ = apply_event(NegotiationState.ORDERED, NegotiationEvent.QUOTE_RECEIVED)
    assert "terminal" in str(caught.value)


# --------------------------------------------------------------------------- #
# Translating the brain's decisions
# --------------------------------------------------------------------------- #


def test_every_move_the_brain_can_return_is_mapped() -> None:
    for action in MoveAction:
        _ = event_for_move(action)  # raises KeyError if a new action is unmapped


def test_accept_escalates_rather_than_buying() -> None:
    assert event_for_move(MoveAction.ACCEPT) is NegotiationEvent.AGENT_ESCALATED
    assert event_for_move(MoveAction.ESCALATE) is NegotiationEvent.AGENT_ESCALATED


def test_wait_produces_no_transition() -> None:
    assert event_for_move(MoveAction.WAIT) is None


def test_no_move_maps_to_a_human_event() -> None:
    for action in MoveAction:
        event = event_for_move(action)
        assert event is None or event not in HUMAN_EVENTS
