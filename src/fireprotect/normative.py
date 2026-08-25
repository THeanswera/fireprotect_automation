from __future__ import annotations

from dataclasses import dataclass
from typing import Generic, TypeVar


T = TypeVar("T")


@dataclass(frozen=True)
class NormativeTrace:
    document_id: str
    edition: str
    clause: str
    formula_or_table: str | None
    description: str

    def __post_init__(self) -> None:
        for name in ("document_id", "edition", "clause", "description"):
            value = getattr(self, name)
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"NormativeTrace.{name} must not be empty")
        if self.formula_or_table is not None and (
            not isinstance(self.formula_or_table, str)
            or not self.formula_or_table.strip()
        ):
            raise ValueError(
                "NormativeTrace.formula_or_table must be non-empty when provided"
            )


class NormativeInputError(ValueError):
    pass


class NormativeTraceRequiredError(NormativeInputError):
    """A normative result was about to be accepted without its source trace."""


def require_normative_trace(
    trace: NormativeTrace | None, *, production: bool = True
) -> NormativeTrace | None:
    """Require traceability before a normative result is used in production.

    Exploratory calculations may explicitly pass ``production=False``.  They
    remain unconfirmed until a trace is attached.
    """

    if trace is not None and not isinstance(trace, NormativeTrace):
        raise TypeError("trace must be NormativeTrace or None")
    if production and trace is None:
        raise NormativeTraceRequiredError(
            "A normative result cannot be confirmed in production without "
            "NormativeTrace"
        )
    return trace


@dataclass(frozen=True)
class NormativeResult(Generic[T]):
    """A calculation value coupled to its normative citation."""

    value: T
    trace: NormativeTrace | None

    @property
    def confirmed(self) -> bool:
        return self.trace is not None

    def validate(self, *, production: bool = True) -> NormativeResult[T]:
        require_normative_trace(self.trace, production=production)
        return self


def require_engineering_input(name: str, value):
    """Block silent substitution of an engineering-significant value."""
    if value is None or value == "":
        raise NormativeInputError(
            f"Не задано обязательное инженерное значение: {name}. "
            "Автоматическая подстановка запрещена."
        )
    return value
