from datetime import datetime, timezone

import pytest

from fireprotect.lira import (
    ForceUnits,
    LiraForceRow,
    LiraMappingError,
    SourceForceValues,
    apply_lira_force_row,
)
from fireprotect.model import ProjectElement, ProvenanceType, Unit, ValueProvenance


def _element() -> ProjectElement:
    values = {name: None for name in ProjectElement.field_names()}
    values.update({
        "project_id": "P", "element_id": "10", "mark": "К1", "element_type": "column",
        "source_file": "model.json", "source_type": "PROJECT",
        "source_element_id": "10", "source_row": None,
        "timestamp": datetime(2026, 8, 25, tzinfo=timezone.utc),
        "profile_name": "30 К1",
    })
    values["provenance"] = {
        name: ValueProvenance(ProvenanceType.SOURCE, file="model.json", field=name)
        for name in ("project_id", "element_id", "mark", "element_type", "profile_name")
    }
    return ProjectElement(**values)


def _row(section: str = "30К1") -> LiraForceRow:
    units = ForceUnits("kN", "kN*m", "kN*m", "kN", "kN")
    return LiraForceRow(
        element_id="10", section=section, load_case="LC", combination="C1",
        N=150000.0, Mx=12000.0, My=3000.0, Qx=4000.0, Qy=5000.0,
        source=SourceForceValues(150.0, 12.0, 3.0, 4.0, 5.0, units),
        source_row=7,
    )


def test_lira_row_populates_quantities_and_provenance_without_auto_governing():
    result = apply_lira_force_row(_element(), _row(), source_file="forces.csv")
    assert result.N is not None and result.N.to(Unit.KILONEWTON).value == 150
    assert result.Mx is not None and result.Mx.to(Unit.KILONEWTON_METER).value == 12
    assert result.governing_combination is None
    assert result.trace_for("N").row == 7
    assert result.trace_for("N").file == "forces.csv"


def test_governing_combination_requires_explicit_request():
    result = apply_lira_force_row(
        _element(), _row(), source_file="forces.csv", set_governing_combination=True
    )
    assert result.governing_combination == "C1"


def test_section_mismatch_is_not_selected_automatically():
    with pytest.raises(LiraMappingError, match="does not match"):
        apply_lira_force_row(_element(), _row("35 К1"), source_file="forces.csv")
