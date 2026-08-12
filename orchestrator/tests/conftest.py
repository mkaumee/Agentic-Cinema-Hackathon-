"""Fixtures for tests that need a real Firestore.

Real meaning the emulator, never a live project. Start it with
``make emulator`` (or ``firebase emulators:start --only firestore``) and these
run; without it they skip with a message rather than failing, so ``make test``
still works on a machine that has not set the emulator up yet.

``make e2e`` boots the emulator itself, which is where these are guaranteed to
actually execute.
"""

import os
from collections.abc import AsyncIterator

import httpx
import pytest
from google.auth.credentials import AnonymousCredentials
from google.cloud.firestore_v1 import AsyncClient

EMULATOR_HOST = os.environ.get("FIRESTORE_EMULATOR_HOST", "127.0.0.1:8080")
PROJECT_ID = os.environ.get("FIRESTORE_PROJECT_ID", "demo-cinema")

_WIPE_URL = (
    f"http://{EMULATOR_HOST}/emulator/v1/projects/{PROJECT_ID}"
    f"/databases/(default)/documents"
)


def _emulator_running() -> bool:
    try:
        response = httpx.get(f"http://{EMULATOR_HOST}/", timeout=1.0)
    except httpx.HTTPError:
        return False
    return response.status_code < 500


@pytest.fixture
async def firestore() -> AsyncIterator[AsyncClient]:
    """A clean database per test.

    Skipping lives here rather than in a marker each test file has to import:
    asking for this fixture is already the statement "this test needs Firestore".

    Wiped before rather than after, so a failing test leaves its data behind for
    inspection in the emulator UI on :4000.
    """
    if not _emulator_running():
        pytest.skip(
            f"Firestore emulator not reachable at {EMULATOR_HOST}. "
            f"Start it with `make emulator`."
        )

    os.environ["FIRESTORE_EMULATOR_HOST"] = EMULATOR_HOST
    _ = httpx.delete(_WIPE_URL, timeout=10.0)

    client = AsyncClient(
        project=PROJECT_ID,
        credentials=AnonymousCredentials(),
    )
    try:
        yield client
    finally:
        client.close()
