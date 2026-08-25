from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from enum import Enum
from typing import Protocol


def decimal_value(value: Decimal | int | str) -> Decimal:
    """Create an exact decimal and reject binary floats at API boundaries."""
    if isinstance(value, float):
        raise TypeError("Binary float is not accepted; pass Decimal, int, or str with an explicit unit")
    result = value if isinstance(value, Decimal) else Decimal(str(value).replace(",", "."))
    if not result.is_finite():
        raise ValueError("Geometry value must be finite")
    return result


@dataclass(frozen=True)
class GeometryQuantity:
    value: Decimal
    unit: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "value", decimal_value(self.value))


class Section(Protocol):
    area_mm2: Decimal

    def full_perimeter_mm(self) -> Decimal: ...


def _positive(name: str, value: Decimal | int | str) -> Decimal:
    result = decimal_value(value)
    if result <= 0:
        raise ValueError(f"{name} must be positive")
    return result


def _positive_integer(name: str, value: Decimal | int | str) -> Decimal:
    result = _positive(name, value)
    if result != result.to_integral_value():
        raise ValueError(f"{name} must be a positive integer")
    return result


@dataclass(frozen=True)
class ISection:
    height_mm: Decimal
    width_mm: Decimal
    web_thickness_mm: Decimal
    flange_thickness_mm: Decimal
    area_mm2: Decimal

    def __post_init__(self) -> None:
        for name in ("height_mm", "width_mm", "web_thickness_mm", "flange_thickness_mm", "area_mm2"):
            object.__setattr__(self, name, _positive(name, getattr(self, name)))
        if self.web_thickness_mm >= self.width_mm:
            raise ValueError("web_thickness_mm must be less than width_mm")
        if 2 * self.flange_thickness_mm >= self.height_mm:
            raise ValueError("two flanges must fit inside height_mm")

    def full_perimeter_mm(self) -> Decimal:
        # Exact sharp-corner boundary. RX3/profile-table radii affect area, not
        # this coating contour identity used by the confirmed corpus.
        return 4 * self.width_mm + 2 * self.height_mm - 2 * self.web_thickness_mm


@dataclass(frozen=True)
class ChannelSection:
    height_mm: Decimal
    width_mm: Decimal
    web_thickness_mm: Decimal
    flange_thickness_mm: Decimal
    area_mm2: Decimal

    def __post_init__(self) -> None:
        for name in ("height_mm", "width_mm", "web_thickness_mm", "flange_thickness_mm", "area_mm2"):
            object.__setattr__(self, name, _positive(name, getattr(self, name)))
        if self.web_thickness_mm >= self.width_mm:
            raise ValueError("web_thickness_mm must be less than width_mm")
        if 2 * self.flange_thickness_mm >= self.height_mm:
            raise ValueError("two flanges must fit inside height_mm")

    def full_perimeter_mm(self) -> Decimal:
        return 4 * self.width_mm + 2 * self.height_mm - 2 * self.web_thickness_mm


@dataclass(frozen=True)
class AngleSection:
    vertical_leg_mm: Decimal
    horizontal_leg_mm: Decimal
    thickness_mm: Decimal
    area_mm2: Decimal

    def __post_init__(self) -> None:
        for name in ("vertical_leg_mm", "horizontal_leg_mm", "thickness_mm", "area_mm2"):
            object.__setattr__(self, name, _positive(name, getattr(self, name)))
        if self.thickness_mm >= min(self.vertical_leg_mm, self.horizontal_leg_mm):
            raise ValueError("thickness_mm must be less than both legs")

    def full_perimeter_mm(self) -> Decimal:
        return 2 * (self.vertical_leg_mm + self.horizontal_leg_mm)


@dataclass(frozen=True)
class TSection:
    height_mm: Decimal
    width_mm: Decimal
    web_thickness_mm: Decimal
    flange_thickness_mm: Decimal
    area_mm2: Decimal

    def __post_init__(self) -> None:
        for name in ("height_mm", "width_mm", "web_thickness_mm", "flange_thickness_mm", "area_mm2"):
            object.__setattr__(self, name, _positive(name, getattr(self, name)))
        if self.web_thickness_mm >= self.width_mm or self.flange_thickness_mm >= self.height_mm:
            raise ValueError("T-section thicknesses must fit inside overall dimensions")

    def full_perimeter_mm(self) -> Decimal:
        return 2 * (self.height_mm + self.width_mm)


@dataclass(frozen=True)
class RectangularHollowSection:
    height_mm: Decimal
    width_mm: Decimal
    wall_thickness_mm: Decimal
    area_mm2: Decimal

    def __post_init__(self) -> None:
        for name in ("height_mm", "width_mm", "wall_thickness_mm", "area_mm2"):
            object.__setattr__(self, name, _positive(name, getattr(self, name)))
        if 2 * self.wall_thickness_mm >= min(self.height_mm, self.width_mm):
            raise ValueError("two walls must fit inside the hollow section")

    def full_perimeter_mm(self) -> Decimal:
        # Fireproofing is applied to the external contour of a closed section.
        return 2 * (self.height_mm + self.width_mm)


@dataclass(frozen=True)
class CircularHollowSection:
    outer_diameter_mm: Decimal
    wall_thickness_mm: Decimal
    area_mm2: Decimal

    def __post_init__(self) -> None:
        for name in ("outer_diameter_mm", "wall_thickness_mm", "area_mm2"):
            object.__setattr__(self, name, _positive(name, getattr(self, name)))
        if 2 * self.wall_thickness_mm >= self.outer_diameter_mm:
            raise ValueError("two walls must fit inside the hollow section")

    def full_perimeter_mm(self) -> Decimal:
        pi = Decimal("3.1415926535897932384626433832795028841971693993751")
        return pi * self.outer_diameter_mm


@dataclass(frozen=True)
class ExplicitSection:
    """For composite/welded profiles whose boundary has been supplied explicitly."""

    area_mm2: Decimal
    perimeter_mm: Decimal
    description: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "area_mm2", _positive("area_mm2", self.area_mm2))
        object.__setattr__(self, "perimeter_mm", _positive("perimeter_mm", self.perimeter_mm))
        if not self.description.strip():
            raise ValueError("ExplicitSection.description is required")

    def full_perimeter_mm(self) -> Decimal:
        return self.perimeter_mm


@dataclass(frozen=True)
class HeatingExposure:
    heated_perimeter_mm: Decimal
    sides: tuple[str, ...]
    engineering_basis: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "heated_perimeter_mm", _positive("heated_perimeter_mm", self.heated_perimeter_mm))
        if not self.sides:
            raise ValueError("Heating sides require an explicit engineering decision")
        if not self.engineering_basis.strip():
            raise ValueError("Heating exposure requires an engineering basis")


@dataclass(frozen=True)
class GeometryResult:
    area: GeometryQuantity
    full_perimeter: GeometryQuantity
    heated_perimeter: GeometryQuantity
    ptm: GeometryQuantity
    section_factor: GeometryQuantity
    protected_area_one: GeometryQuantity
    protected_area_total: GeometryQuantity


def calculate_geometry(
    section: Section,
    exposure: HeatingExposure,
    *,
    length_m: Decimal | int | str,
    quantity: Decimal | int | str,
) -> GeometryResult:
    length = _positive("length_m", length_m)
    count = _positive_integer("quantity", quantity)
    area = _positive("section.area_mm2", section.area_mm2)
    full_perimeter = _positive("full_perimeter_mm", section.full_perimeter_mm())
    heated_perimeter = exposure.heated_perimeter_mm
    ptm = area / heated_perimeter
    section_factor = Decimal(1000) * heated_perimeter / area
    protected_one = heated_perimeter * length / Decimal(1000)
    protected_total = protected_one * count
    return GeometryResult(
        area=GeometryQuantity(area, "mm²"),
        full_perimeter=GeometryQuantity(full_perimeter, "mm"),
        heated_perimeter=GeometryQuantity(heated_perimeter, "mm"),
        ptm=GeometryQuantity(ptm, "mm"),
        section_factor=GeometryQuantity(section_factor, "m⁻¹"),
        protected_area_one=GeometryQuantity(protected_one, "m²"),
        protected_area_total=GeometryQuantity(protected_total, "m²"),
    )


class ComparisonStatus(str, Enum):
    MATCH = "MATCH"
    MISMATCH = "MISMATCH"


@dataclass(frozen=True)
class GeometryComparison:
    metric: str
    calculated: GeometryQuantity
    reference: GeometryQuantity
    difference: Decimal
    tolerance: Decimal
    status: ComparisonStatus
    reference_source: str


def compare_geometry_value(
    metric: str,
    calculated: GeometryQuantity,
    reference: GeometryQuantity,
    *,
    tolerance: Decimal | int | str,
    reference_source: str,
) -> GeometryComparison:
    if calculated.unit != reference.unit:
        raise ValueError(f"Cannot compare {calculated.unit} with {reference.unit}")
    allowed = decimal_value(tolerance)
    if allowed < 0:
        raise ValueError("tolerance must not be negative")
    difference = calculated.value - reference.value
    status = ComparisonStatus.MATCH if abs(difference) <= allowed else ComparisonStatus.MISMATCH
    return GeometryComparison(metric, calculated, reference, difference, allowed, status, reference_source)


# Imported last to avoid a cycle while project.py imports GeometryResult.
from .project import (  # noqa: E402
    GEOMETRY_PROJECT_FIELDS as GEOMETRY_PROJECT_FIELDS,
    apply_geometry_result as apply_geometry_result,
)
