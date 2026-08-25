from datetime import datetime

import pytest

from fireprotect.model import ProjectElement, Unit
from fireprotect.project_io import (
    ProjectDataError,
    project_element_from_dict,
    project_element_to_dict,
)


def _payload():
    payload = {name: None for name in ProjectElement.field_names()}
    payload.update({
        "project_id": "P", "element_id": "E", "mark": "К1", "element_type": "column",
        "source_file": "source.csv", "source_type": "LIRA_CSV",
        "source_element_id": None, "source_row": None,
        "timestamp": "2026-08-25T12:00:00+00:00",
        "length": {"value": "3,5", "unit": "m"},
        "provenance": {
            name: {"kind": "SOURCE", "file": "source.csv", "field": name}
            for name in ("project_id", "element_id", "mark", "element_type", "length")
        },
    })
    return payload


def test_json_boundary_requires_explicit_units_and_all_fields():
    element = project_element_from_dict(_payload())
    assert element.length is not None
    assert element.length.to(Unit.MILLIMETER).value == 3500
    assert isinstance(element.timestamp, datetime)


def test_json_boundary_rejects_missing_field():
    payload = _payload()
    del payload["N"]
    with pytest.raises(ProjectDataError, match="must state every field explicitly"):
        project_element_from_dict(payload)


def test_json_boundary_rejects_bare_number():
    payload = _payload()
    payload["length"] = 3.5
    with pytest.raises(ProjectDataError, match="exactly 'value' and 'unit'"):
        project_element_from_dict(payload)


def test_project_element_json_round_trip_preserves_units_and_provenance():
    element = project_element_from_dict(_payload())
    reparsed = project_element_from_dict(project_element_to_dict(element))
    assert reparsed == element
    assert reparsed.length is not None
    assert reparsed.length.unit is Unit.METER
