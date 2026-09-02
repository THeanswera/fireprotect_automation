"""Explicit force and moment conversion to SI and native review units."""

from __future__ import annotations

from decimal import Decimal

from .errors import LiraMappingError
from .types import LIRA_NATIVE_FORCE_COMPONENTS


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
        .replace("\u00b7", "*")
        .replace("\u00d7", "*")
        .replace("\u0442\u0441", "tf")
        .replace("\u0442", "tf")
        .replace("\u043c", "m")
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


def validate_native_force_unit(component: str, unit: str) -> None:
    if component not in LIRA_NATIVE_FORCE_COMPONENTS:
        raise LiraMappingError(f"unsupported native LIRA component: {component!r}")
    semantic_dimension = "Mx" if component in {"Mk", "My", "Mz"} else "N"
    validate_force_unit(semantic_dimension, unit)


def to_review_unit(component: str, value: Decimal, unit: str) -> tuple[Decimal, str]:
    """Normalize a native LIRA force to kN or kN*m without binary floats."""

    validate_native_force_unit(component, unit)
    semantic_dimension = "Mx" if component in {"Mk", "My", "Mz"} else "N"
    normalized = to_si(semantic_dimension, value, unit) / Decimal("1000")
    target_unit = "kN*m" if component in {"Mk", "My", "Mz"} else "kN"
    return normalized, target_unit
