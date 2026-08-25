"""Neutral types shared by all LIRA data sources.

These types deliberately have no dependency on ``ProjectElement``.  A future
LiraAPI adapter can produce :class:`RawTableRow` objects and reuse the same
mapping and SI conversion pipeline as the file adapters.
"""

from __future__ import annotations

from dataclasses import dataclass
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

    N: float
    Mx: float
    My: float
    Qx: float
    Qy: float
    units: ForceUnits


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
    N: float
    Mx: float
    My: float
    Qx: float
    Qy: float
    source: SourceForceValues
    source_row: int


@dataclass(frozen=True, slots=True)
class RawTableRow:
    """One header-addressable row emitted by a LIRA table source."""

    values: Mapping[str, Any]
    row_number: int


@runtime_checkable
class LiraRowSource(Protocol):
    """Extension point implemented by CSV, HTML, XLSX and future API sources."""

    def read_rows(self) -> list[RawTableRow]:
        """Return source rows with their source-specific one-based numbers."""
