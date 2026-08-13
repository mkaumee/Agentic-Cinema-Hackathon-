"""Tests for the wall-clock guard.

A guard that has never rejected anything is not evidence of anything. These
feed it the spellings people actually reach for, including the aliased ones
ruff's banned-api rules miss.
"""

from pathlib import Path

import check_no_wallclock as guard
import pytest

CAUGHT = [
    pytest.param(
        "from datetime import datetime\nx = datetime.now()\n",
        "datetime.now()",
        id="plain-datetime-now",
    ),
    pytest.param(
        "from datetime import datetime\nx = datetime.utcnow()\n",
        "datetime.utcnow()",
        id="utcnow",
    ),
    pytest.param(
        "from datetime import datetime as dt\nx = dt.now()\n",
        "dt.now()",
        id="aliased-class",
    ),
    pytest.param(
        "import datetime\nx = datetime.datetime.now()\n",
        "datetime.datetime.now()",
        id="module-qualified",
    ),
    pytest.param(
        "import datetime as d\nx = d.datetime.now()\n",
        "d.datetime.now()",
        id="aliased-module",
    ),
    pytest.param("import time\nx = time.time()\n", "time.time()", id="time-time"),
    pytest.param(
        "import time\nx = time.monotonic()\n", "time.monotonic()", id="monotonic"
    ),
    pytest.param(
        "from time import time\nx = time()\n", "time()", id="bare-time-import"
    ),
    pytest.param(
        "from time import perf_counter as pc\nx = pc()\n", "pc()", id="aliased-time"
    ),
]


@pytest.mark.parametrize(("source", "expected"), CAUGHT)
def test_guard_catches_wallclock_reads(
    source: str, expected: str, tmp_path: Path
) -> None:
    target = tmp_path / "offender.py"
    _ = target.write_text(source, encoding="utf-8")

    findings = guard.scan(target)

    assert findings, f"guard missed {expected!r}"
    assert findings[0][1] == expected


ALLOWED_THROUGH = [
    pytest.param(
        "from orchestrator.clock import SimClock\nasync def f(c: SimClock):\n"
        "    return await c.now('p')\n",
        id="the-sanctioned-clock-call",
    ),
    pytest.param(
        "from datetime import UTC, datetime\nT = datetime(2026, 3, 1, tzinfo=UTC)\n",
        id="literal-datetimes-in-tests",
    ),
    pytest.param(
        "import datetime\nd = datetime.timedelta(hours=6)\n",
        id="timedelta-is-not-a-clock-read",
    ),
    pytest.param(
        "class Thing:\n    def now(self): return 1\nThing().now()\n",
        id="unrelated-method-named-now",
    ),
]


@pytest.mark.parametrize("source", ALLOWED_THROUGH)
def test_guard_does_not_cry_wolf(source: str, tmp_path: Path) -> None:
    target = tmp_path / "innocent.py"
    _ = target.write_text(source, encoding="utf-8")

    assert guard.scan(target) == []


def test_the_real_repository_is_clean() -> None:
    """The guard, run for real. This is the assertion that protects the demo."""
    assert guard.main() == 0
