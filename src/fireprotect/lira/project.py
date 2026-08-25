from __future__ import annotations

from dataclasses import replace
from pathlib import Path

from ..model import ProjectElement, ProvenanceType, Quantity, Unit, ValueProvenance
from ..rx3.profiles import normalize_profile_name
from .errors import LiraMappingError
from .types import LiraForceRow


def apply_lira_force_row(
    element: ProjectElement,
    row: LiraForceRow,
    *,
    source_file: str | Path,
    set_governing_combination: bool = False,
) -> ProjectElement:
    """Attach one already-normalized LIRA row to a ProjectElement.

    The caller must explicitly request governing-combination assignment. Merely
    importing a row does not prove that it governs the fire calculation.
    """

    identifiers = {element.element_id}
    if element.source_element_id:
        identifiers.add(element.source_element_id)
    if row.element_id not in identifiers:
        raise LiraMappingError(
            f"LIRA element {row.element_id!r} does not match ProjectElement identifiers {sorted(identifiers)!r}"
        )
    if element.profile_name is not None and (
        normalize_profile_name(row.section)
        != normalize_profile_name(element.profile_name)
    ):
        raise LiraMappingError(
            f"LIRA section {row.section!r} does not match ProjectElement profile {element.profile_name!r}"
        )

    source = str(source_file)
    updated_fields = (
        "load_case",
        "combination",
        "N",
        "Mx",
        "My",
        "Qx",
        "Qy",
    )

    provenance = dict(element.provenance)
    for field_name in updated_fields:
        provenance[field_name] = ValueProvenance(
            ProvenanceType.SOURCE,
            file=source,
            row=row.source_row,
            field=field_name,
        )
    if set_governing_combination:
        provenance["governing_combination"] = ValueProvenance(
            ProvenanceType.ENGINEER_INPUT,
            file=source,
            row=row.source_row,
            field="governing_combination",
            formula=(
                "explicit selector; records an engineering decision and does not "
                "prove a mathematical maximum"
            ),
        )
    return replace(
        element,
        load_case=row.load_case,
        combination=row.combination,
        N=Quantity.of(row.N, Unit.NEWTON),
        Mx=Quantity.of(row.Mx, Unit.NEWTON_METER),
        My=Quantity.of(row.My, Unit.NEWTON_METER),
        Qx=Quantity.of(row.Qx, Unit.NEWTON),
        Qy=Quantity.of(row.Qy, Unit.NEWTON),
        governing_combination=(
            row.combination
            if set_governing_combination
            else element.governing_combination
        ),
        provenance=provenance,
    )
