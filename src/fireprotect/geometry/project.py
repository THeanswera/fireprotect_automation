from __future__ import annotations

from dataclasses import replace
from typing import Mapping

from ..model import ProjectElement, Quantity, Unit, ValueProvenance
from . import GeometryResult


GEOMETRY_PROJECT_FIELDS = (
    "area",
    "full_perimeter",
    "heated_perimeter",
    "ptm",
    "protected_area",
)


def apply_geometry_result(
    element: ProjectElement,
    result: GeometryResult,
    *,
    provenance: Mapping[str, ValueProvenance],
) -> ProjectElement:
    missing = set(GEOMETRY_PROJECT_FIELDS) - set(provenance)
    extra = set(provenance) - set(GEOMETRY_PROJECT_FIELDS)
    if missing or extra:
        raise ValueError(
            f"Geometry provenance must match {GEOMETRY_PROJECT_FIELDS}; "
            f"missing={sorted(missing)}, extra={sorted(extra)}"
        )
    merged_provenance = dict(element.provenance)
    merged_provenance.update(provenance)
    return replace(
        element,
        area=Quantity.of(result.area.value, Unit.SQUARE_MILLIMETER),
        full_perimeter=Quantity.of(
            result.full_perimeter.value, Unit.MILLIMETER
        ),
        heated_perimeter=Quantity.of(
            result.heated_perimeter.value, Unit.MILLIMETER
        ),
        ptm=Quantity.of(result.ptm.value, Unit.MILLIMETER),
        protected_area=Quantity.of(
            result.protected_area_total.value, Unit.SQUARE_METER
        ),
        provenance=merged_provenance,
    )
