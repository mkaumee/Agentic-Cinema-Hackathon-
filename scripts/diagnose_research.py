#!/usr/bin/env python3
"""Why is nothing being researched? Walk the chain and name the broken link.

    uv run python scripts/diagnose_research.py --project encoded-phalanx-505503-v8

Read-only. Writes nothing, deploys nothing, and never prints the key.

## Why this exists when two other checks already do

``check_research.py`` answers one question well — does Parallel accept the key
the agent holds. ``verify_deploy.sh`` answers another — is the deployment
configured for the real brain. Neither answers the one you actually have when
the screen sits there doing nothing, because "not searching" has at least six
causes and those two checks cover two of them.

The others are invisible from outside: the items are still ``DRAFT`` and nobody
confirmed them; they are ``RESEARCHING`` but claimed by a tick fifteen minutes
ago; Vertex is returning 429 and every reasoning call is failing; the tick is
not running at all. Each looks identical on the panel — a quiet screen — and
each has a different fix.

So this walks the chain in order and reports every link, rather than stopping
at the first thing that looks wrong. A run that finds nothing broken is also an
answer: it means the work is moving and the wait is the ``research_limit``
budget, which is three items per tick by design.

## The chain

1. **Configuration.** What ``cinema-tick`` is actually deployed with, read off
   the service. A scripted brain never searches the web at all, and that is a
   legitimate deployment rather than a fault — it is just not the one anybody
   means to demo.
2. **Vertex.** The model and location pair, probed with the same
   ``generateContent`` call ``deploy.sh`` uses. A 429 here is the whole answer:
   research is a reasoning call before it is a search, so a throttled Gemini
   stops research without any search ever being attempted.
3. **The research key.** Delegated to ``check_research.py`` — same call, same
   three-way answer, no second implementation to drift.
4. **The queue.** What is in Firestore, by status, and whether it is due. This
   is the part nothing else can see, and usually where the answer is.
5. **The tick.** What the last few passes actually reported. ``items_researched``
   on a tick that ran is the difference between "not working" and "working
   through a budget".

## Credentials

A person's, in Cloud Shell::

    gcloud auth application-default login
"""

# argparse Namespace attributes are Any by nature; values are str()'d at use.
# pyright: reportAny=false, reportUnknownMemberType=false
import argparse
import asyncio
import json
import subprocess
import urllib.error
import urllib.request
from collections import defaultdict
from datetime import UTC, datetime

from check_research import REFUSED, UNKNOWN, check, key_from_deployment
from google.cloud.firestore_v1 import AsyncClient

GREEN, YELLOW, RED, DIM, OFF = "\033[32m", "\033[33m", "\033[31m", "\033[2m", "\033[0m"

# Statuses that are waiting on the sourcing loop. An item sitting in one of
# these with a due date in the past is work the tick should be doing.
SOURCING_STATUSES = ("RESEARCHING", "SOURCING")


def ok(text: str) -> None:
    print(f"  {GREEN}✓{OFF}  {text}")


def bad(text: str) -> None:
    print(f"  {RED}✗{OFF}  {text}")


def huh(text: str) -> None:
    print(f"  {YELLOW}?{OFF}  {text}")


def note(text: str) -> None:
    print(f"     {DIM}{text}{OFF}")


def say(heading: str) -> None:
    print(f"\n{heading}")


def service_env(project: str, service: str, region: str) -> dict[str, str]:
    """The deployed environment, as a dict. Empty when the service is absent."""
    try:
        raw = subprocess.run(
            [
                "gcloud",
                "run",
                "services",
                "describe",
                service,
                f"--project={project}",
                f"--region={region}",
                "--format=json(spec.template.spec.containers[0].env)",
            ],
            capture_output=True,
            text=True,
            check=True,
        ).stdout
    except subprocess.CalledProcessError, FileNotFoundError:
        return {}
    try:
        containers = json.loads(raw)["spec"]["template"]["spec"]["containers"]
        return {e["name"]: e.get("value", "") for e in containers[0].get("env", [])}
    except KeyError, IndexError, json.JSONDecodeError:
        return {}


def probe_vertex(project: str, model: str, location: str) -> tuple[int, str]:
    """The reasoning call, for real. Returns an HTTP status and a note.

    Same request ``deploy.sh`` makes: generateContent on v1beta1, which is what
    google-genai itself calls, so a 200 here is a statement about the code path
    that will actually run rather than about a metadata endpoint.
    """
    try:
        token = subprocess.run(
            ["gcloud", "auth", "print-access-token", f"--project={project}"],
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip()
    except (subprocess.CalledProcessError, FileNotFoundError) as exc:
        return 0, f"could not get an access token ({type(exc).__name__})"

    host = "aiplatform.googleapis.com"
    if location != "global":
        host = f"{location}-aiplatform.googleapis.com"
    url = (
        f"https://{host}/v1beta1/projects/{project}/locations/{location}"
        f"/publishers/google/models/{model}:generateContent"
    )
    body = json.dumps(
        {
            "contents": [{"role": "user", "parts": [{"text": "ping"}]}],
            "generationConfig": {"maxOutputTokens": 1},
        }
    ).encode()
    request = urllib.request.Request(
        url,
        data=body,
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            return int(response.status), ""
    except urllib.error.HTTPError as exc:
        return int(exc.code), exc.read().decode("utf-8", "replace")[:200]
    except (urllib.error.URLError, TimeoutError) as exc:
        return 0, f"{type(exc).__name__}: {exc}"


async def queue(project: str, now: datetime) -> tuple[int, int]:
    """What is in Firestore, by status and by whether it is due.

    Returns (items due now in a sourcing status, items parked in one).

    Narrower than ``due_items`` on purpose. That query has no status filter — it
    returns anything with a past due date — and ``SourcingLoop.run`` then acts
    only on ``RESEARCHING`` and ``SOURCING``, claiming and dropping the rest. So
    a negotiation due for a chase is in ``due_items`` and is not research work,
    and counting it here would report a queue that is about to move when the
    research queue is empty. This counts what sourcing would actually *do*.
    """
    client = AsyncClient(project=project)

    by_status: defaultdict[str, int] = defaultdict(int)
    due_now = parked = undated = 0
    soonest: datetime | None = None

    async for snapshot in client.collection_group("items").stream():
        data = snapshot.to_dict() or {}
        status = str(data.get("status", "?"))
        by_status[status] += 1
        if status not in SOURCING_STATUSES:
            continue
        due_at = data.get("next_action_due_at")
        if due_at is None:
            undated += 1
        elif due_at <= now:
            due_now += 1
        else:
            parked += 1
            soonest = due_at if soonest is None else min(soonest, due_at)

    if not by_status:
        huh("no items at all — nothing has been confirmed from a screenplay yet")
        return 0, 0

    for status, count in sorted(by_status.items()):
        print(f"     {status:<16} {count}")
    print()

    if by_status.get("DRAFT") and not any(by_status.get(s) for s in SOURCING_STATUSES):
        bad(f"{by_status['DRAFT']} item(s) still DRAFT — nobody pressed Confirm")
        note("DRAFT is inert by design: nothing is researched or emailed until")
        note("a person confirms the list. This is the gate working, not a fault.")
        return 0, 0

    if due_now:
        ok(f"{due_now} item(s) due for sourcing right now")
    if parked and soonest is not None:
        minutes = (soonest - now).total_seconds() / 60
        huh(f"{parked} item(s) parked — soonest due in {minutes:.1f} min")
        note("A claim lease is 15 minutes. Parked rows are normal right after a")
        note("tick claimed them, and a symptom if they never come back.")
    if undated:
        huh(f"{undated} item(s) in a sourcing status with no due date — stuck")
        note("Nothing will ever pick these up: the due queue is a range filter,")
        note("and a range filter does not match a null.")
    if not (due_now or parked or undated):
        ok("nothing waiting on the sourcing loop — research is finished")
    return due_now, parked


def ticks(project: str, service: str, minutes: int) -> None:
    """What the last few passes reported. The counters, not just that it ran."""
    try:
        raw = subprocess.run(
            [
                "gcloud",
                "logging",
                "read",
                f'resource.labels.service_name="{service}" jsonPayload.message="tick"',
                f"--project={project}",
                "--limit=5",
                f"--freshness={minutes}m",
                "--format=json",
            ],
            capture_output=True,
            text=True,
            check=True,
        ).stdout
        entries = json.loads(raw)
    except subprocess.CalledProcessError, FileNotFoundError, json.JSONDecodeError:
        huh("could not read the tick log")
        return

    if not entries:
        bad(f"no tick logged in the last {minutes} minutes")
        note("Nothing will be researched while the loop is not running:")
        note("  gcloud scheduler jobs describe cinema-tick --location=us-central1")
        return

    ok(f"{len(entries)} tick(s) in the last {minutes} minutes")
    interesting = (
        "items_researched",
        "negotiations_advanced",
        "messages_sent",
        "quota_backoffs",
        "error_count",
    )
    for entry in entries:
        payload = entry.get("jsonPayload", {})
        stamp = str(entry.get("timestamp", ""))[11:19]
        fields = " ".join(
            f"{k}={payload[k]}" for k in interesting if payload.get(k) is not None
        )
        note(f"{stamp}  {fields or '(no counters logged)'}")


async def run(args: argparse.Namespace) -> int:
    project = str(args.project)
    region = str(args.region)
    service = str(args.service)
    now = datetime.now(UTC)  # noqa: TID251 — a diagnostic, outside the product
    findings: list[str] = []

    print(f"Diagnosing research on {project!r}.")

    # -- 1 ---------------------------------------------------------------- #
    say("1. What the tick is deployed with")
    env = service_env(project, service, region)
    if not env:
        bad(f"could not read {service} — is it deployed, and is the region right?")
        return UNKNOWN

    brain = env.get("CINEMA_BRAIN_BACKEND", "scripted")
    model = env.get("CINEMA_GEMINI_MODEL", "gemini-3.7-flash")
    location = env.get("GOOGLE_CLOUD_LOCATION", "global")
    has_key = bool(env.get("PARALLEL_API_KEY"))

    if brain == "main-agent":
        ok(f"brain: main-agent ({model} in {location})")
    else:
        bad(f"brain: {brain} — the scripted fake never searches the web")
        note("Research returns canned bands with no sources and no search call.")
        note("Turn the real one on:")
        note("  BRAIN_BACKEND=main-agent PARALLEL_API_KEY=<key> \\")
        note(f"    MAIL_BACKEND=gmail make deploy PROJECT_ID={project}")
        findings.append("the deployed brain is scripted, so nothing searches")

    if has_key:
        ok("PARALLEL_API_KEY is set on the service")
    else:
        bad("PARALLEL_API_KEY is not set on the service")
        note("Research still answers — from the model's memory — so its price")
        note("bands and supplier URLs would be invented rather than sourced.")
        findings.append("no research key on the service")

    # -- 2 ---------------------------------------------------------------- #
    say("2. Vertex — because research reasons before it searches")
    status, detail = probe_vertex(project, model, location)
    if status == 200:
        ok(f"{model} answers in {location}")
    elif status == 429:
        bad("429 RESOURCE_EXHAUSTED — Vertex is throttling this project")
        note("This alone stops research: the reasoning call fails before any")
        note("search is attempted. It clears on its own; the loop backs off and")
        note("retries. Persisting means asking for a quota increase.")
        findings.append("Vertex is returning 429")
    elif status == 404:
        bad(f"404 — {model!r} is not served in {location!r} for this project")
        note("Vertex returns one 404 for 'no such model' and for 'not here', so")
        note("suspect the location first and the name second.")
        findings.append("the model/location pair does not resolve")
    elif status == 0:
        huh(f"could not probe Vertex — {detail}")
    else:
        bad(f"HTTP {status} from Vertex")
        note(detail)
        findings.append(f"Vertex returned {status}")

    # -- 3 ---------------------------------------------------------------- #
    say("3. The research key, as the agent holds it")
    if not has_key:
        huh("skipped — there is no key on the service to test")
    else:
        try:
            key = key_from_deployment(project, service, region)
        except SystemExit:
            key = ""
        if not key:
            huh("could not read the key off the service")
        else:
            result = check(key)  # prints its own three-way answer
            if result == REFUSED:
                findings.append("Parallel refused the key the agent holds")
            elif result == UNKNOWN:
                huh("not proof the key is wrong — the probe got no answer")

    # -- 4 ---------------------------------------------------------------- #
    say("4. What is actually waiting in Firestore")
    due_now, parked = await queue(project, now)

    # -- 5 ---------------------------------------------------------------- #
    say("5. What the loop reported")
    ticks(project, service, int(args.minutes))

    # -- verdict ---------------------------------------------------------- #
    say("Verdict")
    if findings:
        for finding in findings:
            bad(finding)
        print()
        print("  Fix the first one and re-run this. They compound: a scripted")
        print("  brain makes every check below it meaningless.")
        return 1

    if due_now:
        ok("configuration is sound and there is work due")
        note("If it is not moving, the tick is failing on something this cannot")
        note("see. Read the errors:  gcloud logging read \\")
        note(f"    'resource.labels.service_name=\"{service}\" severity>=WARNING' \\")
        note(f"    --project={project} --limit=20")
        return UNKNOWN

    if parked:
        ok("configuration is sound, and the queue is working through its budget")
        note("research_limit is 3 items per tick, one tick a minute — so twenty")
        note("props take about seven minutes. Slow is not the same as broken.")
        return 0

    ok("nothing is broken and nothing is waiting")
    note("Either research has finished, or nothing has been confirmed yet.")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Why is nothing being researched?")
    _ = parser.add_argument("--project", required=True, help="the GCP project")
    _ = parser.add_argument("--service", default="cinema-tick")
    _ = parser.add_argument("--region", default="us-central1")
    _ = parser.add_argument(
        "--minutes", default=15, type=int, help="how far back to read tick logs"
    )
    args = parser.parse_args(argv)
    return asyncio.run(run(args))


if __name__ == "__main__":
    raise SystemExit(main())
