"""The mail seam.

``MailTransport`` is what the tick loop talks to. The Gmail implementation and
the in-memory one both satisfy it, which is what lets the whole loop run end to
end today, before any OAuth consent screen exists.

Note what a transport does *not* do: it does not know what time it is. Inbound
messages come back without a timestamp, and the tick loop stamps them with
``clock.now()`` on arrival. A transport that read a real clock would put
wall-clock times into the timeline and desynchronise it from every ``due_at``
in Firestore the moment demo mode ran.

Threading rules, which live here because getting them wrong is silent:

- Every reply carries ``In-Reply-To`` and ``References``. Without them Gmail
  starts a new thread and the conversation fragments across the supplier's
  inbox, which makes us look like a broken mailing list rather than a buyer.
- Inbound mail is matched by thread ID and never by subject. Suppliers rewrite
  subjects — "Re: quote" becomes "quotation for your shoot" — and a subject
  match files their reply against the wrong negotiation.
"""

from typing import ClassVar, Protocol

from pydantic import BaseModel, ConfigDict, Field


class RawInbound(BaseModel):
    """A received message, before the system knows when it arrived.

    Deliberately has no timestamp. See the module docstring.
    """

    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True)

    message_id: str
    thread_id: str
    from_email: str
    subject: str
    body: str
    has_attachments: bool = False
    attachment_filenames: list[str] = Field(default_factory=list)


class SentMessage(BaseModel):
    """What came back from handing a message to the transport."""

    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True)

    message_id: str
    thread_id: str


class MailTransport(Protocol):
    """Send mail, and collect what has arrived since the last poll."""

    async def send(
        self,
        *,
        to: str,
        subject: str,
        body: str,
        thread_id: str = "",
        in_reply_to: str = "",
    ) -> SentMessage:
        """Send a message, starting a thread or continuing one.

        When ``thread_id`` is given the message must land in that thread, with
        ``In-Reply-To`` and ``References`` set from ``in_reply_to``.
        """
        ...

    async def poll(self) -> list[RawInbound]:
        """Everything unread, oldest first, marked read as a side effect.

        Polled rather than pushed. Pub/Sub push needs a verified domain and buys
        nothing at this scale.
        """
        ...


class InMemoryMailbox:
    """A transport with no network behind it.

    Used by the tick-loop tests and by ``make e2e``. Also the thing that makes
    the daily end-to-end habit possible on a laptop with no credentials: the
    loop exercised here is the same loop, with one object swapped.

    A ``responder`` may be attached to answer outgoing mail, which is how the
    e2e run gets a supplier to negotiate against without standing up the
    simulator service.
    """

    sent: list[dict[str, str]]
    _inbox: list[RawInbound]
    _counter: int

    def __init__(self) -> None:
        self.sent = []
        self._inbox = []
        self._counter = 0

    def _next_id(self, prefix: str) -> str:
        self._counter += 1
        return f"{prefix}-{self._counter}"

    async def send(
        self,
        *,
        to: str,
        subject: str,
        body: str,
        thread_id: str = "",
        in_reply_to: str = "",
    ) -> SentMessage:
        message_id = self._next_id("msg")
        resolved_thread = thread_id or self._next_id("thread")
        self.sent.append(
            {
                "to": to,
                "subject": subject,
                "body": body,
                "thread_id": resolved_thread,
                "in_reply_to": in_reply_to,
                "message_id": message_id,
            }
        )
        return SentMessage(message_id=message_id, thread_id=resolved_thread)

    async def poll(self) -> list[RawInbound]:
        delivered = self._inbox
        self._inbox = []
        return delivered

    # -- test-side helpers, not part of MailTransport ---------------------- #

    def deliver(
        self,
        *,
        thread_id: str,
        body: str,
        from_email: str = "supplier@example.invalid",
        subject: str = "Re: quote",
        has_attachments: bool = False,
        attachment_filenames: list[str] | None = None,
    ) -> RawInbound:
        """Queue an inbound reply for the next poll."""
        message = RawInbound(
            message_id=self._next_id("in"),
            thread_id=thread_id,
            from_email=from_email,
            subject=subject,
            body=body,
            has_attachments=has_attachments,
            attachment_filenames=attachment_filenames or [],
        )
        self._inbox.append(message)
        return message

    def last_sent(self) -> dict[str, str] | None:
        return self.sent[-1] if self.sent else None
