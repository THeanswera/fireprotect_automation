"""Explicit force and moment conversion to SI."""

from __future__ import annotations

from .errors import LiraMappingError


_FORCE_FACTORS = {
    "n": 1.0,
    "kn": 1_000.0,
    "mn": 1_000_000.0,
    "kgf": 9.80665,
    "tf": 9_806.65,
}
_MOMENT_FACTORS = {
    "n*m": 1.0,
    "kn*m": 1_000.0,
    "mn*m": 1_000_000.0,
    "n*mm": 0.001,
    "kn*mm": 1.0,
    "kgf*m": 9.80665,
    "tf*m": 9_806.65,
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


def to_si(field: str, value: float, unit: str) -> float:
    """Convert an axial/shear force or moment to N or N*m."""

    validate_force_unit(field, unit)
    normalized = _normalize_unit(unit)
    factors = _MOMENT_FACTORS if field in {"Mx", "My"} else _FORCE_FACTORS
    return value * factors[normalized]
