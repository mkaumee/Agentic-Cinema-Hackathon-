"""What actually lives in Firestore.

Kept separate from ``cinema_contracts.models`` on purpose. Those are the shapes
that cross to Role A; these are the shapes on disk. They overlap heavily today,
and conflating them would be convenient right up until one needs a field the
other must not have — a storage detail leaking into the brain's context, or a
brain-facing field forcing a migration.

Layout::

    projects/{pid}
    projects/{pid}/items/{iid}
    projects/{pid}/suppliers/{sid}
    projects/{pid}/negotiations/{nid}
    projects/{pid}/negotiations/{nid}/messages/{mid}
    purchase_orders/{iid}          <- top level, keyed by item

``purchase_orders`` is top level and keyed by item ID rather than by its own
generated ID. That is the whole guardrail: see ``repository.py``.
"""

from datetime import datetime
from enum import StrEnum
from typing import ClassVar

from cinema_contracts import (
    ExtractedQuote,
    Money,
    NegotiationState,
    ReferenceBand,
    SceneMention,
)
from pydantic import BaseModel, ConfigDict, Field

from orchestrator.clock import ClockState


class _Record(BaseModel):
    """Base for persisted documents.

    Unknown fields are allowed here, unlike the contract models. A document
    written by a newer deployment must not break an older reader mid-demo, and
    dropping an unrecognised field is better than refusing to load the
    negotiation it belongs to.
    """

    model_config: ClassVar[ConfigDict] = ConfigDict(extra="allow")

    def to_firestore(self) -> dict[str, object]:
        """Plain dict for the Firestore client.

        ``mode="python"`` deliberately: datetimes stay native so Firestore
        stores real timestamps, and ``StrEnum`` members are already strings.
        """
        return self.model_dump(mode="python", exclude_none=True)


class ItemStatus(StrEnum):
    """Where an item is in the procurement flow, for the breakdown screen."""

    DRAFT = "DRAFT"
    RESEARCHING = "RESEARCHING"
    SOURCING = "SOURCING"
    NEGOTIATING = "NEGOTIATING"
    READY_FOR_HUMAN = "READY_FOR_HUMAN"
    ORDERED = "ORDERED"
    ABANDONED = "ABANDONED"


class ProjectRecord(_Record):
    """``projects/{pid}``"""

    title: str
    clock: ClockState
    budget_baseline: Money | None = None
    created_at: datetime


class ItemRecord(_Record):
    """``projects/{pid}/items/{iid}``"""

    name: str
    category: str
    scenes: list[str] = Field(default_factory=list)
    qty: int = Field(ge=1, default=1)
    notes: str = ""

    mentions: list[SceneMention] = Field(default_factory=list)
    """The script lines this item was found in. Shown on the item detail screen
    so a producer can see why the agent thinks the shoot needs this."""

    consumable: bool = False
    """Destroyed on camera, so the quantity is per take rather than per shoot."""

    reference_band: ReferenceBand | None = None
    status: ItemStatus = ItemStatus.DRAFT
    chosen_quote: ExtractedQuote | None = None


class SupplierRecord(_Record):
    """``projects/{pid}/suppliers/{sid}``"""

    name: str
    email: str
    source_url: str = ""
    confidence: float = Field(ge=0.0, le=1.0, default=0.5)
    verified: bool = False


class NegotiationRecord(_Record):
    """``projects/{pid}/negotiations/{nid}``

    ``next_action_due_at`` is the only field the tick loop queries on, and it is
    deliberately absent rather than null once a negotiation reaches a terminal
    state. A missing field drops the document out of the index entirely, so
    finished negotiations cost nothing to skip — there is no filter to write and
    no rows to read.
    """

    item_id: str
    supplier_id: str
    state: NegotiationState = NegotiationState.DRAFTED

    floor_price: Money | None = None
    target_price: Money | None = None
    rounds_used: int = Field(ge=0, default=0)
    max_rounds: int = Field(ge=1, default=4)

    gmail_thread_id: str = ""
    last_msg_id: str = ""

    first_quote: ExtractedQuote | None = None
    latest_quote: ExtractedQuote | None = None

    next_action_due_at: datetime | None = None
    last_inbound_at: datetime | None = None
    last_outbound_at: datetime | None = None

    escalation_reason: str = ""
    latest_reasoning: str = ""
    """The brain's last explanation, shown on the item detail screen."""

    created_at: datetime
    updated_at: datetime


class MessageRecord(_Record):
    """``projects/{pid}/negotiations/{nid}/messages/{mid}``

    Append-only. The timeline is the only proof that simulated days passed, and
    it stops being evidence the moment anything can rewrite it.
    """

    direction: str
    body: str
    subject: str = ""
    sim_sent_at: datetime
    gmail_message_id: str = ""
    extracted_quote: ExtractedQuote | None = None
    needs_human: bool = False


class PurchaseOrderRecord(_Record):
    """``purchase_orders/{item_id}``

    Note there is no ``id`` field of its own: the document ID *is* the item ID.
    Storing ``item_id`` in the body too is redundant on purpose — the security
    rule checks the two agree, so a client cannot write an order whose payload
    claims a different item than the key it was filed under.
    """

    item_id: str
    project_id: str
    supplier_id: str
    negotiation_id: str
    price: Money
    approved_by: str
    approved_at: datetime
