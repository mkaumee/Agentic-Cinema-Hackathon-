"""Money, with the currency attached and arithmetic that refuses to guess.

The rule agreed at the start of the project: money is never a formatted string
like ``"RM880"`` and never a bare number. It is always an amount plus an
explicit currency, so neither half of the codebase has to parse or assume.

Amounts are whole major units — ringgit, not sen. Film procurement quotes are
negotiated in whole ringgit, and refusing sub-unit precision removes a class of
rounding bug for free. When a supplier writes "RM880.50", ``from_major`` rounds
half-up at the boundary and the rest of the system never sees a fraction.
"""

from decimal import ROUND_HALF_UP, Decimal
from typing import ClassVar, Literal, Self, override

from pydantic import BaseModel, ConfigDict, Field

Currency = Literal["MYR", "USD", "SGD", "EUR", "GBP"]
"""ISO 4217 codes. MYR is the project default; the rest exist so an imported
supplier quote in another currency fails loudly rather than being coerced."""


class CurrencyMismatchError(ValueError):
    """Raised when two different currencies meet in one arithmetic operation.

    Never caught and papered over. If this fires, some upstream code lost track
    of what currency it was holding, and silently picking one is how a producer
    ends up approving the wrong number.
    """

    def __init__(self, left: Currency, right: Currency) -> None:
        super().__init__(
            f"Refusing to combine {left} with {right}. "
            f"Convert explicitly before doing arithmetic."
        )


class Money(BaseModel):
    """An amount in whole major units of a named currency.

    Frozen, so a ``Money`` can be shared between a negotiation record and a
    purchase order without one mutating the other.
    """

    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True)

    amount: int = Field(ge=0, description="Whole major units. 880 means RM880.")
    currency: Currency = "MYR"

    @override
    def __hash__(self) -> int:
        """Spelled out rather than inherited from ``frozen=True``.

        Pydantic generates an equivalent hash at runtime, but the type checker
        cannot see it, and a ``Money`` is used as a dict key often enough that
        the explicit version is worth the four lines.
        """
        return hash((self.amount, self.currency))

    @classmethod
    def from_major(cls, value: Decimal | int | str, currency: Currency = "MYR") -> Self:
        """Build from a possibly-fractional major-unit value, rounding half-up.

        This is the only sanctioned way to turn a supplier's written price into
        a ``Money``. ``from_major("880.50")`` is RM881.
        """
        rounded = Decimal(str(value)).quantize(Decimal("1"), rounding=ROUND_HALF_UP)
        return cls(amount=int(rounded), currency=currency)

    def _check(self, other: Money) -> None:
        if self.currency != other.currency:
            raise CurrencyMismatchError(self.currency, other.currency)

    def __add__(self, other: Money) -> Money:
        self._check(other)
        return Money(amount=self.amount + other.amount, currency=self.currency)

    def __sub__(self, other: Money) -> Money:
        self._check(other)
        return Money(amount=self.amount - other.amount, currency=self.currency)

    def scaled_by(self, factor: Decimal | float) -> Money:
        """Multiply by a ratio — anchor multipliers, floor multipliers, discounts."""
        return Money.from_major(
            Decimal(str(self.amount)) * Decimal(str(factor)), self.currency
        )

    def __lt__(self, other: Money) -> bool:
        self._check(other)
        return self.amount < other.amount

    def __le__(self, other: Money) -> bool:
        self._check(other)
        return self.amount <= other.amount

    def __gt__(self, other: Money) -> bool:
        self._check(other)
        return self.amount > other.amount

    def __ge__(self, other: Money) -> bool:
        self._check(other)
        return self.amount >= other.amount

    @override
    def __str__(self) -> str:
        """For logs and debugging only. Never persist or send this to an LLM."""
        return f"{self.currency} {self.amount:,}"
