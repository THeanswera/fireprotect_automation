"""Neutral types shared by all LIRA data sources.

These types deliberately have no dependency on ``ProjectElement``.  A future
LiraAPI adapter can produce :class:`RawTableRow` objects and reuse the same
mapping and SI conversion pipeline as the file adapters.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Any, Mapping, Protocol, runtime_checkable


CANONICAL_FIELDS = (
    "element_id",
    "section",
    "load_case",
    "combination",
    "N",
    "Mx",
    "My",
    "Qx",
    "Qy",
)
FORCE_FIELDS = ("N", "Mx", "My", "Qx", "Qy")


def _decimal_force(value: object, *, field: str) -> Decimal:
    if isinstance(value, bool) or isinstance(value, float):
        raise TypeError(f"{field} must be Decimal, int or str, not binary float")
    try:
        result = value if isinstance(value, Decimal) else Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError) as exc:
        raise TypeError(f"{field} must be a decimal number") from exc
    if not result.is_finite():
        raise ValueError(f"{field} must be finite")
    return result


@dataclass(frozen=True, slots=True)
class ForceUnits:
    """Units declared for the values in one source table."""

    N: str
    Mx: str
    My: str
    Qx: str
    Qy: str

    def as_dict(self) -> dict[str, str]:
        return {
            "N": self.N,
            "Mx": self.Mx,
            "My": self.My,
            "Qx": self.Qx,
            "Qy": self.Qy,
        }


@dataclass(frozen=True, slots=True)
class SourceForceValues:
    """Original numeric values and their explicitly declared units."""

    N: Decimal
    Mx: Decimal
    My: Decimal
    Qx: Decimal
    Qy: Decimal
    units: ForceUnits

    def __post_init__(self) -> None:
        for field in FORCE_FIELDS:
            object.__setattr__(
                self, field, _decimal_force(getattr(self, field), field=field)
            )
        if not isinstance(self.units, ForceUnits):
            raise TypeError("units must be ForceUnits")


@dataclass(frozen=True, slots=True)
class LiraForceRow:
    """A LIRA bar-force row normalized to SI.

    ``N``, ``Qx`` and ``Qy`` are in newtons. ``Mx`` and ``My`` are in
    newton-metres.  ``source`` retains the values and units supplied at the
    import boundary so that the conversion remains auditable.
    """

    element_id: str
    section: str
    load_case: str
    combination: str
    N: Decimal
    Mx: Decimal
    My: Decimal
    Qx: Decimal
    Qy: Decimal
    source: SourceForceValues
    source_row: int
    source_sheet: str | None = None

    def __post_init__(self) -> None:
        for field in FORCE_FIELDS:
            object.__setattr__(
                self, field, _decimal_force(getattr(self, field), field=field)
            )
        if not isinstance(self.source, SourceForceValues):
            raise TypeError("source must be SourceForceValues")
        if isinstance(self.source_row, bool) or self.source_row < 1:
            raise ValueError("source_row must be a positive integer")
        if self.source_sheet is not None and (
            not isinstance(self.source_sheet, str) or not self.source_sheet.strip()
        ):
            raise ValueError("source_sheet must be non-empty when provided")


@dataclass(frozen=True, slots=True)
class RawTableRow:
    """One header-addressable row emitted by a LIRA table source."""

    values: Mapping[str, Any]
    row_number: int
    sheet: str | None = None


@runtime_checkable
class LiraRowSource(Protocol):
    """Extension point implemented by CSV, HTML, XLSX and future API sources."""

    def read_rows(self) -> list[RawTableRow]:
        """Return source rows with their source-specific one-based numbers."""
