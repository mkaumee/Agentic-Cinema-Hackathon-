"""Tests for simulated time.

Deterministic throughout: the clock's only real-time source is injected, so
nothing here depends on how long the test takes to run.
"""

from datetime import UTC, datetime, timedelta

import pytest
from cinema_contracts import ClockMode
from orchestrator.clock import (
    DEMO_SPEED,
    MAX_CATCHUP,
    ClockState,
    FrozenRealTime,
    InMemoryClockStore,
    SimClock,
    initial_state,
)

PID = "proj1"
SIM_START = datetime(2026, 3, 1, 9, 0, tzinfo=UTC)
REAL_START = datetime(2026, 8, 12, 14, 0, tzinfo=UTC)


def _clock(
    *, mode: ClockMode = ClockMode.LIVE, speed: float | None = None
) -> tuple[SimClock, FrozenRealTime, InMemoryClockStore]:
    resolved = (
        speed if speed is not None else (DEMO_SPEED if mode is ClockMode.DEMO else 1.0)
    )
    if mode is ClockMode.FROZEN:
        resolved = 0.0
    store = InMemoryClockStore(
        {
            PID: ClockState(
                sim_now=SIM_START, real_anchor=REAL_START, speed=resolved, mode=mode
            )
        }
    )
    real = FrozenRealTime(REAL_START)
    return SimClock(store, real), real, store


async def test_live_mode_advances_one_to_one() -> None:
    clock, real, _ = _clock(mode=ClockMode.LIVE)
    real.advance(timedelta(minutes=30))
    assert await clock.now(PID) == SIM_START + timedelta(minutes=30)


async def test_demo_mode_advances_six_simulated_hours_per_real_second() -> None:
    clock, real, _ = _clock(mode=ClockMode.DEMO)
    real.advance(timedelta(seconds=1))
    assert await clock.now(PID) == SIM_START + timedelta(hours=6)


async def test_five_simulated_days_replay_in_twenty_real_seconds() -> None:
    """The number the demo actually depends on."""
    clock, real, _ = _clock(mode=ClockMode.DEMO)
    real.advance(timedelta(seconds=20))
    assert await clock.now(PID) - SIM_START == timedelta(days=5)


async def test_frozen_mode_does_not_advance_on_its_own() -> None:
    clock, real, _ = _clock(mode=ClockMode.FROZEN)
    real.advance(timedelta(days=3))
    assert await clock.now(PID) == SIM_START


async def test_now_does_not_write() -> None:
    clock, real, store = _clock(mode=ClockMode.LIVE)
    real.advance(timedelta(hours=2))
    _ = await clock.now(PID)
    assert (await store.read(PID)).sim_now == SIM_START


async def test_advance_reanchors_so_the_stored_value_is_readable() -> None:
    clock, real, store = _clock(mode=ClockMode.LIVE)
    real.advance(timedelta(hours=2))

    returned = await clock.advance(PID)
    stored = await store.read(PID)

    assert returned == SIM_START + timedelta(hours=2)
    assert stored.sim_now == returned
    assert stored.real_anchor == real.utc_now()


async def test_two_advances_in_a_row_do_not_double_count() -> None:
    """A retried tick must not skip a simulated day."""
    clock, real, _ = _clock(mode=ClockMode.LIVE)
    real.advance(timedelta(hours=2))

    first = await clock.advance(PID)
    second = await clock.advance(PID)

    assert first == second == SIM_START + timedelta(hours=2)


async def test_a_missed_tick_loses_no_simulated_time() -> None:
    """Simulated time is derived, not accumulated, so gaps self-heal.

    This is what makes the tick loop safe to kill mid-run: there is no counter
    to lose and nothing to replay.
    """
    clock, real, _ = _clock(mode=ClockMode.LIVE)

    real.advance(timedelta(minutes=20))  # tick runs
    _ = await clock.advance(PID)
    real.advance(timedelta(minutes=40))  # two ticks missed entirely

    assert await clock.now(PID) == SIM_START + timedelta(hours=1)


async def test_a_long_outage_is_clamped_rather_than_jumping_decades() -> None:
    clock, real, _ = _clock(mode=ClockMode.DEMO)
    real.advance(timedelta(hours=8))  # 8h at 21600x would be ~19 simulated years
    assert await clock.now(PID) == SIM_START + MAX_CATCHUP


async def test_the_clamp_leaves_room_for_the_longest_demo() -> None:
    """A full minute of demo mode must not hit the ceiling.

    Sixty real seconds is fifteen simulated days. If MAX_CATCHUP is ever
    lowered below that, judge mode silently stops advancing partway through.
    """
    clock, real, _ = _clock(mode=ClockMode.DEMO)
    real.advance(timedelta(seconds=60))
    assert await clock.now(PID) == SIM_START + timedelta(days=15)


async def test_the_clock_never_runs_backwards_on_skew() -> None:
    clock, real, _ = _clock(mode=ClockMode.LIVE)
    real.advance(timedelta(hours=-3))
    assert await clock.now(PID) == SIM_START


async def test_switching_to_demo_banks_time_already_elapsed() -> None:
    clock, real, _ = _clock(mode=ClockMode.LIVE)
    real.advance(timedelta(hours=2))

    state = await clock.set_mode(PID, ClockMode.DEMO)

    assert state.sim_now == SIM_START + timedelta(hours=2)
    assert state.speed == DEMO_SPEED
    assert state.mode is ClockMode.DEMO

    real.advance(timedelta(seconds=1))
    assert await clock.now(PID) == SIM_START + timedelta(hours=8)


async def test_switching_to_frozen_stops_the_clock_where_it_stands() -> None:
    clock, real, _ = _clock(mode=ClockMode.DEMO)
    real.advance(timedelta(seconds=2))

    state = await clock.set_mode(PID, ClockMode.FROZEN)
    assert state.sim_now == SIM_START + timedelta(hours=12)

    real.advance(timedelta(seconds=10))
    assert await clock.now(PID) == SIM_START + timedelta(hours=12)


async def test_seeding_places_a_project_at_an_arbitrary_instant() -> None:
    clock, _, _ = _clock(mode=ClockMode.LIVE)
    mid_negotiation = SIM_START + timedelta(days=3, hours=4)

    state = await clock.set_sim_now(PID, mid_negotiation)

    assert state.sim_now == mid_negotiation
    assert await clock.now(PID) == mid_negotiation


async def test_a_new_project_starts_live_and_anchored() -> None:
    state = initial_state(SIM_START, REAL_START)
    assert state.mode is ClockMode.LIVE
    assert state.speed == 1.0
    assert state.sim_now == SIM_START
    assert state.real_anchor == REAL_START


async def test_unknown_project_raises_rather_than_inventing_a_clock() -> None:
    clock, _, _ = _clock()
    with pytest.raises(KeyError):
        _ = await clock.now("no-such-project")
