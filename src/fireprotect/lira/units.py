"""Explicit force and moment conversion to SI."""

from __future__ import annotations

from decimal import Decimal

from .errors import LiraMappingError


_FORCE_FACTORS = {
    "n": Decimal("1"),
    "kn": Decimal("1000"),
    "mn": Decimal("1000000"),
    "kgf": Decimal("9.80665"),
    "tf": Decimal("9806.65"),
}
_MOMENT_FACTORS = {
    "n*m": Decimal("1"),
    "kn*m": Decimal("1000"),
    "mn*m": Decimal("1000000"),
    "n*mm": Decimal("0.001"),
    "kn*mm": Decimal("1"),
    "kgf*m": Decimal("9.80665"),
    "tf*m": Decimal("9806.65"),
}


def _normalize_unit(unit: str) -> str:
    if not isinstance(unit, str) or not unit.strip():
        raise LiraMappingError("force units must be explicit non-empty strings")
    return (
        unit.strip()
        .lower()
        .replace("·", "*")
        .replace("×", "*")
        .replace(" ", "")
    )


def validate_force_unit(field: str, unit: str) -> None:
    normalized = _normalize_unit(unit)
    factors = _MOMENT_FACTORS if field in {"Mx", "My"} else _FORCE_FACTORS
    if normalized not in factors:
        expected = ", ".join(sorted(factors))
        raise LiraMappingError(
            f"unsupported source unit {unit!r} for {field}; supported units: {expected}"
        )


def to_si(field: str, value: Decimal, unit: str) -> Decimal:
    """Convert an axial/shear force or moment to N or N*m."""

    validate_force_unit(field, unit)
    normalized = _normalize_unit(unit)
    factors = _MOMENT_FACTORS if field in {"Mx", "My"} else _FORCE_FACTORS
    return value * factors[normalized]
