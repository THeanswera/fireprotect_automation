"""Caller-controlled mapping from exported columns to canonical LIRA fields."""

from __future__ import annotations

from dataclasses import dataclass
from types import MappingProxyType
from typing import Mapping

from .errors import LiraMappingError
from .types import CANONICAL_FIELDS, FORCE_FIELDS, ForceUnits
from .units import validate_force_unit


@dataclass(frozen=True, slots=True)
class LiraColumnMapping:
    """Maps canonical names to arbitrary table headers.

    No source header names are assumed.  All canonical fields and all source
    force units are mandatory at the import boundary.
    """

    columns: Mapping[str, str]
    units: ForceUnits
    decimal_separator: str = "."
    thousands_separator: str | None = None

    def __post_init__(self) -> None:
        try:
            columns = dict(self.columns)
        except (TypeError, ValueError) as exc:
            raise LiraMappingError(
                "columns must be an explicit canonical-to-source mapping"
            ) from exc
        if not isinstance(self.units, ForceUnits):
            raise LiraMappingError("units must be an explicit ForceUnits instance")
        required = set(CANONICAL_FIELDS)
        supplied = set(columns)
        missing = required - supplied
        unknown = supplied - required
        if missing or unknown:
            details = []
            if missing:
                details.append(f"missing canonical fields: {sorted(missing)}")
            if unknown:
                details.append(f"unknown canonical fields: {sorted(unknown)}")
            raise LiraMappingError("; ".join(details))

        invalid_headers = [
            name
            for name, header in columns.items()
            if not isinstance(header, str) or not header.strip()
        ]
        if invalid_headers:
            raise LiraMappingError(
                f"source headers must be non-empty strings: {sorted(invalid_headers)}"
            )
        normalized_headers = [header.strip() for header in columns.values()]
        if len(normalized_headers) != len(set(normalized_headers)):
            raise LiraMappingError("each canonical field must map to a distinct source header")

        if self.decimal_separator not in {".", ","}:
            raise LiraMappingError("decimal_separator must be '.' or ','")
        if self.thousands_separator is not None:
            if not self.thousands_separator:
                raise LiraMappingError("thousands_separator cannot be empty")
            if self.thousands_separator == self.decimal_separator:
                raise LiraMappingError(
                    "thousands_separator must differ from decimal_separator"
                )

        for field in FORCE_FIELDS:
            validate_force_unit(field, getattr(self.units, field))

        object.__setattr__(
            self,
            "columns",
            MappingProxyType(
                {name: header.strip() for name, header in columns.items()}
            ),
        )

    @property
    def source_headers(self) -> frozenset[str]:
        return frozenset(self.columns.values())
