"""Core, format-independent data model for fire-protection calculations.

The model deliberately keeps units and provenance at its boundaries.  Adapters
must construct :class:`Quantity` objects instead of passing unlabelled numbers,
and every populated project value must have an entry in ``provenance``.
"""

from __future__ import annotations

from dataclasses import dataclass, fields
from datetime import date as Date
from datetime import datetime
from decimal import Decimal, InvalidOperation
from enum import Enum
from types import MappingProxyType
from typing import Any, ClassVar, Mapping


class ModelValidationError(ValueError):
    """Raised when a project element is incomplete or dimensionally invalid."""


class Dimension(str, Enum):
    LENGTH = "length"
    AREA = "area"
    INVERSE_LENGTH = "inverse_length"
    FORCE = "force"
    MOMENT = "moment"
    PRESSURE = "pressure"
    TEMPERATURE = "temperature"
    TIME = "time"
    DENSITY = "density"
    MASS = "mass"
    MASS_PER_AREA = "mass_per_area"


class Unit(str, Enum):
    METER = "m"
    MILLIMETER = "mm"
    CENTIMETER = "cm"
    SQUARE_METER = "m2"
    SQUARE_CENTIMETER = "cm2"
    SQUARE_MILLIMETER = "mm2"
    PER_METER = "1/m"
    PER_MILLIMETER = "1/mm"
    NEWTON = "N"
    KILONEWTON = "kN"
    NEWTON_METER = "N*m"
    KILONEWTON_METER = "kN*m"
    PASCAL = "Pa"
    MEGAPASCAL = "MPa"
    GIGAPASCAL = "GPa"
    KELVIN = "K"
    CELSIUS = "degC"
    SECOND = "s"
    MINUTE = "min"
    HOUR = "h"
    KILOGRAM_PER_CUBIC_METER = "kg/m3"
    KILOGRAM = "kg"
    KILOGRAM_PER_SQUARE_METER = "kg/m2"
    GRAM_PER_SQUARE_METER = "g/m2"


@dataclass(frozen=True, slots=True)
class _UnitDefinition:
    dimension: Dimension
    factor_to_si: Decimal
    offset_before_scale: Decimal = Decimal("0")


_UNIT_DEFINITIONS: Mapping[Unit, _UnitDefinition] = {
    Unit.METER: _UnitDefinition(Dimension.LENGTH, Decimal("1")),
    Unit.MILLIMETER: _UnitDefinition(Dimension.LENGTH, Decimal("0.001")),
    Unit.CENTIMETER: _UnitDefinition(Dimension.LENGTH, Decimal("0.01")),
    Unit.SQUARE_METER: _UnitDefinition(Dimension.AREA, Decimal("1")),
    Unit.SQUARE_CENTIMETER: _UnitDefinition(Dimension.AREA, Decimal("0.0001")),
    Unit.SQUARE_MILLIMETER: _UnitDefinition(Dimension.AREA, Decimal("0.000001")),
    Unit.PER_METER: _UnitDefinition(Dimension.INVERSE_LENGTH, Decimal("1")),
    Unit.PER_MILLIMETER: _UnitDefinition(Dimension.INVERSE_LENGTH, Decimal("1000")),
    Unit.NEWTON: _UnitDefinition(Dimension.FORCE, Decimal("1")),
    Unit.KILONEWTON: _UnitDefinition(Dimension.FORCE, Decimal("1000")),
    Unit.NEWTON_METER: _UnitDefinition(Dimension.MOMENT, Decimal("1")),
    Unit.KILONEWTON_METER: _UnitDefinition(Dimension.MOMENT, Decimal("1000")),
    Unit.PASCAL: _UnitDefinition(Dimension.PRESSURE, Decimal("1")),
    Unit.MEGAPASCAL: _UnitDefinition(Dimension.PRESSURE, Decimal("1000000")),
    Unit.GIGAPASCAL: _UnitDefinition(Dimension.PRESSURE, Decimal("1000000000")),
    Unit.KELVIN: _UnitDefinition(Dimension.TEMPERATURE, Decimal("1")),
    Unit.CELSIUS: _UnitDefinition(
        Dimension.TEMPERATURE, Decimal("1"), Decimal("273.15")
    ),
    Unit.SECOND: _UnitDefinition(Dimension.TIME, Decimal("1")),
    Unit.MINUTE: _UnitDefinition(Dimension.TIME, Decimal("60")),
    Unit.HOUR: _UnitDefinition(Dimension.TIME, Decimal("3600")),
    Unit.KILOGRAM_PER_CUBIC_METER: _UnitDefinition(
        Dimension.DENSITY, Decimal("1")
    ),
    Unit.KILOGRAM: _UnitDefinition(Dimension.MASS, Decimal("1")),
    Unit.KILOGRAM_PER_SQUARE_METER: _UnitDefinition(
        Dimension.MASS_PER_AREA, Decimal("1")
    ),
    Unit.GRAM_PER_SQUARE_METER: _UnitDefinition(
        Dimension.MASS_PER_AREA, Decimal("0.001")
    ),
}

_SI_UNIT: Mapping[Dimension, Unit] = {
    Dimension.LENGTH: Unit.METER,
    Dimension.AREA: Unit.SQUARE_METER,
    Dimension.INVERSE_LENGTH: Unit.PER_METER,
    Dimension.FORCE: Unit.NEWTON,
    Dimension.MOMENT: Unit.NEWTON_METER,
    Dimension.PRESSURE: Unit.PASCAL,
    Dimension.TEMPERATURE: Unit.KELVIN,
    Dimension.TIME: Unit.SECOND,
    Dimension.DENSITY: Unit.KILOGRAM_PER_CUBIC_METER,
    Dimension.MASS: Unit.KILOGRAM,
    Dimension.MASS_PER_AREA: Unit.KILOGRAM_PER_SQUARE_METER,
}


def _decimal(value: Decimal | int | float | str, *, name: str) -> Decimal:
    if isinstance(value, bool):
        raise TypeError(f"{name} must be numeric, not bool")
    try:
        converted = value if isinstance(value, Decimal) else Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise TypeError(f"{name} must be a finite decimal number") from exc
    if not converted.is_finite():
        raise ValueError(f"{name} must be finite")
    return converted


@dataclass(frozen=True, slots=True)
class Quantity:
    """A decimal magnitude carrying an explicit engineering unit."""

    value: Decimal
    unit: Unit

    def __post_init__(self) -> None:
        object.__setattr__(self, "value", _decimal(self.value, name="Quantity.value"))
        if not isinstance(self.unit, Unit):
            try:
                object.__setattr__(self, "unit", Unit(self.unit))
            except (TypeError, ValueError) as exc:
                raise ValueError(f"Unknown unit: {self.unit!r}") from exc

    @classmethod
    def of(cls, value: Decimal | int | float | str, unit: Unit | str) -> Quantity:
        """Explicit adapter-boundary constructor."""

        return cls(value=_decimal(value, name="value"), unit=Unit(unit))

    @property
    def dimension(self) -> Dimension:
        return _UNIT_DEFINITIONS[self.unit].dimension

    @property
    def si_value(self) -> Decimal:
        definition = _UNIT_DEFINITIONS[self.unit]
        return (self.value + definition.offset_before_scale) * definition.factor_to_si

    def to(self, unit: Unit | str) -> Quantity:
        target = Unit(unit)
        target_definition = _UNIT_DEFINITIONS[target]
        if target_definition.dimension is not self.dimension:
            raise ValueError(
                f"Cannot convert {self.dimension.value} from {self.unit.value} "
                f"to {target.value}"
            )
        magnitude = (
            self.si_value / target_definition.factor_to_si
            - target_definition.offset_before_scale
        )
        return Quantity(magnitude, target)

    def as_si(self) -> Quantity:
        return self.to(_SI_UNIT[self.dimension])


class ProvenanceType(str, Enum):
    SOURCE = "SOURCE"
    CALCULATED = "CALCULATED"
    ENGINEER_INPUT = "ENGINEER_INPUT"
    NORMATIVE_TABLE = "NORMATIVE_TABLE"
    RX3_RESULT = "RX3_RESULT"


@dataclass(frozen=True, slots=True)
class ValueProvenance:
    """Origin of one value in :class:`ProjectElement`."""

    kind: ProvenanceType
    file: str | None = None
    sheet: str | None = None
    row: int | None = None
    field: str | None = None
    document: str | None = None
    clause: str | None = None
    formula: str | None = None
    date: Date | datetime | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.kind, ProvenanceType):
            try:
                object.__setattr__(self, "kind", ProvenanceType(self.kind))
            except (TypeError, ValueError) as exc:
                raise ValueError(f"Unknown provenance type: {self.kind!r}") from exc
        for name in ("file", "sheet", "field", "document", "clause", "formula"):
            value = getattr(self, name)
            if value is not None and (not isinstance(value, str) or not value.strip()):
                raise ValueError(f"ValueProvenance.{name} must be a non-empty string")
        if self.row is not None and (
            isinstance(self.row, bool) or not isinstance(self.row, int) or self.row < 1
        ):
            raise ValueError("ValueProvenance.row must be a positive integer")
        if self.date is not None and not isinstance(self.date, Date):
            raise TypeError("ValueProvenance.date must be a date or datetime")
        if self.kind is ProvenanceType.CALCULATED and self.formula is None:
            raise ValueError("CALCULATED provenance requires formula")
        if self.kind is ProvenanceType.NORMATIVE_TABLE and (
            self.document is None or self.clause is None
        ):
            raise ValueError(
                "NORMATIVE_TABLE provenance requires document and clause"
            )


# A concise alias for callers that prefer the domain term.
Provenance = ValueProvenance


@dataclass(frozen=True, slots=True)
class EffectiveLengthParameters:
    """Explicit stability parameters; no engineering value is defaulted."""

    buckling_length_x: Quantity | None
    buckling_length_y: Quantity | None
    factor_x: Decimal | None
    factor_y: Decimal | None

    def __post_init__(self) -> None:
        for name in ("buckling_length_x", "buckling_length_y"):
            value = getattr(self, name)
            if value is not None:
                _require_quantity(name, value, Dimension.LENGTH, positive=True)
        for name in ("factor_x", "factor_y"):
            value = getattr(self, name)
            if value is not None:
                if isinstance(value, float):
                    raise TypeError(
                        f"{name} must be Decimal, not a bare float at an import boundary"
                    )
                converted = _decimal(value, name=name)
                if converted <= 0:
                    raise ModelValidationError(f"{name} must be greater than zero")
                object.__setattr__(self, name, converted)


def _require_quantity(
    name: str,
    value: object,
    dimension: Dimension,
    *,
    positive: bool = False,
    nonnegative: bool = False,
) -> Quantity:
    if not isinstance(value, Quantity):
        raise TypeError(
            f"ProjectElement.{name} must be Quantity with an explicit unit; "
            f"bare {type(value).__name__} is forbidden"
        )
    if value.dimension is not dimension:
        raise ModelValidationError(
            f"ProjectElement.{name} expects {dimension.value}, got "
            f"{value.dimension.value} ({value.unit.value})"
        )
    if positive and value.si_value <= 0:
        raise ModelValidationError(f"ProjectElement.{name} must be greater than zero")
    if nonnegative and value.si_value < 0:
        raise ModelValidationError(f"ProjectElement.{name} must not be negative")
    return value


@dataclass(frozen=True, slots=True)
class ProjectElement:
    """Canonical exchange model used by LIRA, RX3, Excel and documents.

    Optional engineering fields intentionally have no constructor defaults: an
    adapter has to state that a value is unavailable by passing ``None``.  This
    prevents a missing input from silently turning into a design assumption.
    """

    # Identity
    project_id: str
    element_id: str
    mark: str
    element_type: str

    # Source
    source_file: str
    source_type: str
    source_element_id: str | None
    source_row: int | None
    timestamp: datetime

    # Geometry
    section_type: str | None
    profile_standard: str | None
    profile_name: str | None
    area: Quantity | None
    full_perimeter: Quantity | None
    heated_perimeter: Quantity | None
    ptm: Quantity | None
    length: Quantity | None
    quantity: int | None

    # Steel
    steel_grade: str | None
    Ry: Quantity | None
    E: Quantity | None
    density: Quantity | None

    # Forces
    load_case: str | None
    combination: str | None
    N: Quantity | None
    Mx: Quantity | None
    My: Quantity | None
    Qx: Quantity | None
    Qy: Quantity | None
    governing_combination: str | None

    # Fire resistance
    required_fire_resistance: Quantity | None
    stress_state: str | None
    heating_sides: int | None
    support_condition: str | None
    effective_length_parameters: EffectiveLengthParameters | None
    critical_temperature: Quantity | None
    unprotected_fire_resistance: Quantity | None

    # Fireproofing
    material_id: str | None
    coating_type: str | None
    required_thickness: Quantity | None
    specific_consumption: Quantity | None
    protected_area: Quantity | None
    total_consumption: Quantity | None

    # Per-value audit trail. Keys are ProjectElement field names.
    provenance: Mapping[str, ValueProvenance]

    _UNTRACED_FIELDS: ClassVar[frozenset[str]] = frozenset(
        {
            "source_file",
            "source_type",
            "source_element_id",
            "source_row",
            "timestamp",
            "provenance",
        }
    )
    _QUANTITY_FIELDS: ClassVar[Mapping[str, tuple[Dimension, bool, bool]]] = {
        "area": (Dimension.AREA, True, False),
        "full_perimeter": (Dimension.LENGTH, True, False),
        "heated_perimeter": (Dimension.LENGTH, True, False),
        # RX3 "PTM" is the reduced thickness A/P (a length), not the
        # reciprocal section factor P/A.
        "ptm": (Dimension.LENGTH, True, False),
        "length": (Dimension.LENGTH, True, False),
        "Ry": (Dimension.PRESSURE, True, False),
        "E": (Dimension.PRESSURE, True, False),
        "density": (Dimension.DENSITY, True, False),
        "N": (Dimension.FORCE, False, False),
        "Mx": (Dimension.MOMENT, False, False),
        "My": (Dimension.MOMENT, False, False),
        "Qx": (Dimension.FORCE, False, False),
        "Qy": (Dimension.FORCE, False, False),
        "required_fire_resistance": (Dimension.TIME, True, False),
        "critical_temperature": (Dimension.TEMPERATURE, True, False),
        "unprotected_fire_resistance": (Dimension.TIME, True, False),
        "required_thickness": (Dimension.LENGTH, False, True),
        "specific_consumption": (Dimension.MASS_PER_AREA, False, True),
        "protected_area": (Dimension.AREA, False, True),
        "total_consumption": (Dimension.MASS, False, True),
    }

    def __post_init__(self) -> None:
        for name in (
            "project_id",
            "element_id",
            "mark",
            "element_type",
            "source_file",
            "source_type",
        ):
            value = getattr(self, name)
            if not isinstance(value, str) or not value.strip():
                raise ModelValidationError(f"ProjectElement.{name} must not be empty")

        if self.source_element_id is not None and (
            not isinstance(self.source_element_id, str)
            or not self.source_element_id.strip()
        ):
            raise ModelValidationError(
                "ProjectElement.source_element_id must be non-empty when provided"
            )
        if self.source_row is not None and (
            isinstance(self.source_row, bool)
            or not isinstance(self.source_row, int)
            or self.source_row < 1
        ):
            raise ModelValidationError(
                "ProjectElement.source_row must be a positive integer"
            )
        if not isinstance(self.timestamp, datetime):
            raise TypeError("ProjectElement.timestamp must be datetime")

        for name, (dimension, positive, nonnegative) in self._QUANTITY_FIELDS.items():
            value = getattr(self, name)
            if value is not None:
                _require_quantity(
                    name,
                    value,
                    dimension,
                    positive=positive,
                    nonnegative=nonnegative,
                )

        for name in ("quantity", "heating_sides"):
            value = getattr(self, name)
            if value is not None and (
                isinstance(value, bool) or not isinstance(value, int) or value < 1
            ):
                raise ModelValidationError(
                    f"ProjectElement.{name} must be a positive integer"
                )
        if self.heating_sides is not None and self.heating_sides > 4:
            raise ModelValidationError("ProjectElement.heating_sides cannot exceed 4")
        if self.effective_length_parameters is not None and not isinstance(
            self.effective_length_parameters, EffectiveLengthParameters
        ):
            raise TypeError(
                "ProjectElement.effective_length_parameters must be "
                "EffectiveLengthParameters"
            )

        if self.full_perimeter is not None and self.heated_perimeter is not None:
            if self.heated_perimeter.si_value > self.full_perimeter.si_value:
                raise ModelValidationError(
                    "heated_perimeter cannot exceed full_perimeter"
                )

        if not isinstance(self.provenance, Mapping):
            raise TypeError("ProjectElement.provenance must be a mapping")
        known_fields = {field.name for field in fields(self)} - {"provenance"}
        unknown = set(self.provenance) - known_fields
        if unknown:
            raise ModelValidationError(
                f"Unknown provenance fields: {', '.join(sorted(unknown))}"
            )
        missing = [
            field.name
            for field in fields(self)
            if field.name not in self._UNTRACED_FIELDS
            and getattr(self, field.name) is not None
            and field.name not in self.provenance
        ]
        if missing:
            raise ModelValidationError(
                "Missing provenance for populated fields: " + ", ".join(missing)
            )
        invalid = [
            name
            for name, value in self.provenance.items()
            if not isinstance(value, ValueProvenance)
        ]
        if invalid:
            raise TypeError(
                "Provenance entries must be ValueProvenance: "
                + ", ".join(sorted(invalid))
            )
        object.__setattr__(self, "provenance", MappingProxyType(dict(self.provenance)))

    def trace_for(self, field_name: str) -> ValueProvenance:
        """Return a value's provenance, failing explicitly when it has no value."""

        if field_name not in {field.name for field in fields(self)}:
            raise KeyError(f"Unknown ProjectElement field: {field_name}")
        try:
            return self.provenance[field_name]
        except KeyError as exc:
            raise KeyError(f"No provenance recorded for {field_name}") from exc

    def require_fields(self, *field_names: str) -> ProjectElement:
        """Validate inputs required by a calculation/export stage."""

        known = {field.name for field in fields(self)} - {"provenance"}
        unknown = set(field_names) - known
        if unknown:
            raise KeyError(f"Unknown ProjectElement fields: {', '.join(sorted(unknown))}")
        missing = [name for name in field_names if getattr(self, name) is None]
        if missing:
            raise ModelValidationError(
                "Required engineering inputs are unavailable: " + ", ".join(missing)
            )
        return self

    @classmethod
    def field_names(cls) -> tuple[str, ...]:
        return tuple(field.name for field in fields(cls) if field.name != "provenance")


def quantity(value: Decimal | int | float | str, unit: Unit | str) -> Quantity:
    """Small explicit-unit helper useful in import adapters."""

    return Quantity.of(value, unit)
