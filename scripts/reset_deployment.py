#!/usr/bin/env python3
"""Empty a deployment and start again. Run by a human, deliberately.

    uv run python scripts/reset_deployment.py --project my-gcp-project
    uv run python scripts/reset_deployment.py --project my-gcp-project --apply

Dry run first, always. Without ``--apply`` this counts what it would delete and
writes nothing, which is the only way to find out that the project id was wrong
before rather than after.

## What this is for

A deployment that has been demoed into accumulates productions, and every one
of them is walked on every tick. Nine idle productions with three sellers each
is a burst of Gemini calls and Gmail sends per minute against per-minute
quotas, so the loop spends its time backing off and a new negotiation waits
behind work nobody is watching.

That backlog is *data*. It survives every redeploy — a new Cloud Run revision
reads the same Firestore. Deleting and recreating the services changes the
revision numbers and nothing else, which is worth saying because it is the
obvious thing to reach for and it does not work.

## Ordering, and the tick

Pause Cloud Scheduler before running this and resume after::

    gcloud scheduler jobs pause  cinema-tick --location=us-central1
    gcloud scheduler jobs resume cinema-tick --location=us-central1

Not for safety — ``delete_project`` removes the subcollections deepest-first
precisely so a run killed halfway leaves a resumable suffix, and a tick racing
it just fails a claim. It is so the run is not competing for the quota it is
trying to free, and so the log afterwards is legible.

## What it deletes, and what it deliberately does not

Productions go through ``FirestoreRepository.delete_project``, the same cascade
the panel's delete button uses. Subcollections do not go with their parent in
Firestore, and ``due_items`` / ``due_negotiations`` are collection-group
queries that find work with no reference to which projects exist — so a project
document deleted on its own leaves rows that go on emailing suppliers for a
production nobody can see.

``purchase_orders`` is handled here with a client this script builds itself,
against the orders database, under the credentials of the person running it.
Deliberately not a method on ``OrdersRepository``: that class promises it has
no way to remove an order, the tick service never constructs it at all, and the
agent has no IAM binding on that database. Putting a delete there to serve a
reset script would trade a structural guarantee for a convenience. A human with
project-owner credentials can already do this, and having to be that human is
the point.

Firebase Auth users are deleted last, because an account with no data behind it
is a recoverable state and data with no owner is not — ``firestore.rules``
matches on ``owner_uid``, so a production whose owner is gone is invisible to
every browser and reachable only from a shell.

## Credentials

Application-default credentials belonging to a person, not the agent::

    gcloud auth application-default login
    uv run python scripts/reset_deployment.py --project your-project --apply

## Against the emulator

``FIRESTORE_EMULATOR_HOST`` and ``FIREBASE_AUTH_EMULATOR_HOST`` and this talks
to the emulator instead. Same code. Worth doing once before pointing it at a
deployment you care about.
"""

# firebase-admin ships no type information, and the Firestore async client's
# generics are loose in the same places repository.py already documents.
# pyright: reportUnknownMemberType=false, reportUnknownVariableType=false
# pyright: reportMissingTypeStubs=false, reportAny=false
# pyright: reportUnknownArgumentType=false
import argparse
import asyncio
import os
import sys

import firebase_admin
from firebase_admin import auth as firebase_auth
from firebase_admin import credentials
from google.cloud.firestore_v1 import AsyncClient
from orchestrator.repository import (
    MAILBOXES,
    OAUTH_STATES,
    PURCHASE_ORDERS,
    FirestoreRepository,
)
from orchestrator.settings import Settings

EMULATOR_PROJECT = "demo-cinema"


async def _delete_collection(client: AsyncClient, name: str) -> int:
    """Remove every document in a top-level collection. Returns how many."""
    removed = 0
    async for document in client.collection(name).list_documents():
        _ = await document.delete()
        removed += 1
    return removed


async def _count_collection(client: AsyncClient, name: str) -> int:
    return len([_ async for _ in client.collection(name).list_documents()])


async def run(args: argparse.Namespace, project: str) -> int:
    client = AsyncClient(project=project)
    repo = FirestoreRepository(client)
    apply = bool(args.apply)
    verb = "Deleting" if apply else "Would delete"

    project_ids = await repo.list_project_ids()
    print(f"productions:  {len(project_ids)}")
    for pid in project_ids:
        print(f"    {pid}")

    if not args.keep_mailboxes:
        print(f"mailboxes:    {await _count_collection(client, MAILBOXES)}")
        print(f"oauth_states: {await _count_collection(client, OAUTH_STATES)}")

    orders_client: AsyncClient | None = None
    if not args.keep_orders:
        orders_client = AsyncClient(project=project, database=str(args.orders_database))
        print(
            f"orders:       {await _count_collection(orders_client, PURCHASE_ORDERS)}"
            f"  (database {args.orders_database!r})"
        )

    users: list[str] = []
    if not args.keep_accounts:
        users = [user.uid for user in firebase_auth.list_users().iterate_all()]
        print(f"accounts:     {len(users)}")

    if not apply:
        print()
        print("Dry run — nothing was written. Re-run with --apply to delete.")
        return 0

    print()
    for pid in project_ids:
        print(f"{verb} production {pid}…")
        await repo.delete_project(pid)
    print(f"  {len(project_ids)} production(s) gone.")

    if not args.keep_mailboxes:
        gone = await _delete_collection(client, MAILBOXES)
        gone += await _delete_collection(client, OAUTH_STATES)
        print(f"  {gone} mailbox/oauth document(s) gone.")

    if orders_client is not None:
        gone = await _delete_collection(orders_client, PURCHASE_ORDERS)
        print(f"  {gone} purchase order(s) gone.")

    # Last: see the module docstring. An account with no data behind it can be
    # signed into again; data with no owner is invisible to every browser.
    if users:
        for start in range(0, len(users), 1000):
            _ = firebase_auth.delete_users(users[start : start + 1000])
        print(f"  {len(users)} account(s) gone.")

    print()
    print("Empty. Resume the tick:")
    print("  gcloud scheduler jobs resume cinema-tick --location=us-central1")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Empty a deployment.")
    _ = parser.add_argument(
        "--project", default="", help="the GCP project (default: CINEMA_GCP_PROJECT)"
    )
    _ = parser.add_argument(
        "--apply", action="store_true", help="actually delete (default: dry run)"
    )
    _ = parser.add_argument(
        "--orders-database", default="", help="default: CINEMA_ORDERS_DATABASE"
    )
    _ = parser.add_argument("--keep-mailboxes", action="store_true")
    _ = parser.add_argument("--keep-orders", action="store_true")
    _ = parser.add_argument("--keep-accounts", action="store_true")
    args = parser.parse_args(argv)

    settings = Settings()
    project = str(args.project) or settings.gcp_project
    if not args.orders_database:
        args.orders_database = settings.orders_database

    emulated = bool(os.environ.get("FIRESTORE_EMULATOR_HOST"))
    if not emulated and project == EMULATOR_PROJECT:
        print(f"Refusing: {EMULATOR_PROJECT!r} is the emulator's project, not a")
        print("deployment. Name the real one:")
        print(f"    {sys.argv[0]} --project your-project-id")
        return 2

    if not firebase_admin._apps:  # pyright: ignore[reportPrivateUsage]
        if os.environ.get("FIREBASE_AUTH_EMULATOR_HOST"):
            _ = firebase_admin.initialize_app(options={"projectId": project})
        else:
            _ = firebase_admin.initialize_app(
                credentials.ApplicationDefault(), {"projectId": project}
            )

    where = "the emulator" if emulated else f"project {project!r}"
    print(f"Reset against {where}.")
    print()
    return asyncio.run(run(args, project))


if __name__ == "__main__":
    raise SystemExit(main())
