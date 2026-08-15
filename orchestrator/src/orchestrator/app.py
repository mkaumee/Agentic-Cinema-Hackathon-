# Starlette's app.state is an untyped attribute bag, so services_of has to
# reach through Any to get at what lifespan put there.
# pyright: reportAny=false
"""The HTTP surface. What Cloud Scheduler calls.

Two endpoints for now: a health check, and the tick. Everything the loop needs
is assembled once in the lifespan and handed to the handler — nothing is stored
in a module global, and nothing survives between requests except what is in
Firestore. That is Hard Rule 3, and it is what lets Cloud Run reap this process
mid-tick without consequence.

``POST /tick`` with no body ticks every project, because that is how Cloud
Scheduler will call it: one schedule, no arguments. Pass ``project_id`` to tick
one, which is what you want when poking it by hand.

**Not yet, deliberately.** The tick endpoint is unauthenticated. In Phase 3 it
sits behind Cloud Run with a Scheduler OIDC token and no public ingress. It is
noted here rather than half-built, because a home-grown shared secret would
look like protection without being any.
"""

import logging
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import UTC, datetime

from cinema_contracts import AgentBrain
from cinema_contracts.testing import ScriptedBrain
from fastapi import FastAPI, HTTPException, Request
from google.cloud.firestore_v1 import AsyncClient
from pydantic import BaseModel

from orchestrator.clock import SimClock
from orchestrator.gmail import GmailTransport, build_credentials, token_store_for
from orchestrator.mail import InMemoryMailbox, MailTransport
from orchestrator.repository import FirestoreRepository
from orchestrator.settings import MailBackend, Settings
from orchestrator.tick import TickLoop, TickReport

log = logging.getLogger("orchestrator")

NEVER = datetime.min.replace(tzinfo=UTC)
"""Stand-in instant for a report about a tick that failed before it started.

Not a clock read — the tick never got as far as advancing one, and inventing a
plausible timestamp here would be a lie about simulated time.
"""


@dataclass(frozen=True, slots=True)
class Services:
    """Everything a request needs, built once at startup."""

    settings: Settings
    client: AsyncClient
    repo: FirestoreRepository
    clock: SimClock
    brain: AgentBrain
    mail: MailTransport
    loop: TickLoop


def build_mail(settings: Settings) -> MailTransport:
    """Pick a transport. Memory unless someone asked for the real thing."""
    if settings.mail_backend is MailBackend.GMAIL:
        credentials = build_credentials(
            token_store_for(settings),
            settings.oauth_client_id,
            settings.oauth_client_secret,
        )
        return GmailTransport.from_credentials(credentials, settings)
    return InMemoryMailbox()


def build_brain() -> AgentBrain:
    """The reasoning half.

    ``ScriptedBrain`` until Role A's ``main-agent`` is merged onto this branch.
    Swapping it is a one-line change here and nowhere else, which is the whole
    point of the contract.
    """
    return ScriptedBrain()


def build_services(settings: Settings | None = None) -> Services:
    resolved = settings or Settings()
    client = AsyncClient(project=resolved.gcp_project)
    repo = FirestoreRepository(client)
    clock = SimClock(repo)
    brain = build_brain()
    mail = build_mail(resolved)
    return Services(
        settings=resolved,
        client=client,
        repo=repo,
        clock=clock,
        brain=brain,
        mail=mail,
        loop=TickLoop(repo, clock, brain, mail),
    )


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None]:
    services = build_services()
    app.state.services = services
    log.info(
        "orchestrator up",
        extra={
            "mail_backend": services.settings.mail_backend.value,
            "token_backend": services.settings.token_backend.value,
            "project": services.settings.gcp_project,
        },
    )
    try:
        yield
    finally:
        services.client.close()


app = FastAPI(title="Agentic Cinema orchestrator", lifespan=lifespan)


def services_of(request: Request) -> Services:
    services = getattr(request.app.state, "services", None)
    if services is None:  # pragma: no cover - only if lifespan was skipped
        raise HTTPException(status_code=503, detail="services not initialised")
    return services


class Health(BaseModel):
    status: str
    mail_backend: str
    token_backend: str
    project: str


class TickResult(BaseModel):
    """One project's worth of work, flattened for JSON."""

    project_id: str
    sim_now: str
    replies_filed: int
    replies_skipped: int
    replies_after_stop: int
    unmatched_replies: int
    negotiations_examined: int
    messages_sent: int
    escalated: int
    errors: list[str]

    @classmethod
    def of(cls, project_id: str, report: TickReport) -> TickResult:
        return cls(
            project_id=project_id,
            sim_now=report.sim_now.isoformat(),
            replies_filed=report.replies_filed,
            replies_skipped=report.replies_skipped,
            replies_after_stop=report.replies_after_stop,
            unmatched_replies=report.unmatched_replies,
            negotiations_examined=report.negotiations_examined,
            messages_sent=report.messages_sent,
            escalated=report.escalated,
            errors=report.errors,
        )


class TickResponse(BaseModel):
    projects: list[TickResult]


@app.get("/healthz")
async def healthz(request: Request) -> Health:
    """Says which transports are wired, not just that the process is alive.

    "Up" is not the interesting question — "is this about to email a real
    supplier" is.
    """
    settings = services_of(request).settings
    return Health(
        status="ok",
        mail_backend=settings.mail_backend.value,
        token_backend=settings.token_backend.value,
        project=settings.gcp_project,
    )


@app.post("/tick")
async def tick(request: Request, project_id: str | None = None) -> TickResponse:
    """Advance the world by one pass.

    Every project unless one is named. Each is ticked independently and a
    failure in one is reported rather than raised, so a single broken project
    cannot stop the others from advancing — over a multi-day negotiation, a
    project that silently stops being ticked is a negotiation that dies.
    """
    services = services_of(request)
    project_ids = (
        [project_id]
        if project_id is not None
        else await services.repo.list_project_ids()
    )

    results: list[TickResult] = []
    for pid in project_ids:
        try:
            report = await services.loop.run_tick(
                pid, limit=services.settings.tick_limit
            )
        except Exception as exc:
            log.exception("tick failed", extra={"project_id": pid})
            results.append(
                TickResult.of(pid, TickReport(sim_now=NEVER, errors=[str(exc)]))
            )
            continue
        results.append(TickResult.of(pid, report))

    return TickResponse(projects=results)
