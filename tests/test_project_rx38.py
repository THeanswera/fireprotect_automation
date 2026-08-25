import csv
from dataclasses import replace
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

import pytest

from fireprotect.model import (
    EffectiveLengthParameters,
    ProjectElement,
    ProvenanceType,
    Quantity,
    Unit,
    ValueProvenance,
)
from fireprotect.rx3.parser import construction_records, read_rx38
from fireprotect.rx3.project_adapter import (
    Rx38EngineeringConflictError,
    Rx38ProjectAdapterError,
    Rx38TemplateMismatchError,
    create_rx38_from_project_element,
)


def _template_fields() -> list[str]:
    fields = [""] * 200
    values = {
        0: "Tconstr", 1: "К1", 2: "0", 3: "К1", 4: "1", 5: "Двутавр",
        8: "298", 9: "299", 11: "9", 13: "14", 14: "3,3", 15: "1",
        17: "СТО АСЧМ 20-93", 19: "30 К1", 20: "11080", 21: "1774",
        22: "6,24577226606539", 23: "160,108303249097", 24: "5,8542", 25: "5,8542",
        26: "0,00018849", 27: "0,000062409", 28: "0,000062409",
        29: "0,0012651", 30: "0,0004175", 32: "7850", 33: "245", 34: "206000",
        42: "С245", 44: "650", 45: "Cжатый стержень", 48: "Шарнирное опирание по концам",
        49: "100", 50: "777", 51: "2,31", 52: "0,2", 54: "15", 55: "60",
        66: "287,0274", 67: "287,0274", 72: "Нет", 82: "25", 83: "1", 84: "1",
        85: "1", 86: "1194", 87: "3,9402", 88: "3,9402", 104: "Стандартный температурный режим",
        113: "9,27973199329983", 114: "107,761732851986", 141: "0,7",
        188: "σ 0.2% и E по данным EN 1993-1-2", 189: "1",
    }
    for index, value in values.items():
        fields[index] = value
    return fields


@pytest.fixture
def template_rx38(tmp_path: Path) -> Path:
    path = tmp_path / "template.rx38"
    with path.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.writer(stream, delimiter=";", lineterminator="\r\n")
        writer.writerow(["Trazdel", "Тест"])
        writer.writerow(_template_fields())
    return path


def _element(**overrides) -> ProjectElement:
    data = {
        "project_id": "P1", "element_id": "E1", "mark": "К-NEW", "element_type": "column",
        "source_file": "lira.csv", "source_type": "LIRA_CSV", "source_element_id": "1",
        "source_row": 2, "timestamp": datetime(2026, 8, 25, tzinfo=timezone.utc),
        "section_type": "Двутавр", "profile_standard": "СТО АСЧМ 20-93", "profile_name": "30К1",
        "area": Quantity.of("11080", Unit.SQUARE_MILLIMETER),
        "full_perimeter": Quantity.of("1774", Unit.MILLIMETER),
        "heated_perimeter": Quantity.of("1774", Unit.MILLIMETER),
        "ptm": Quantity.of("6.24577226606539", Unit.MILLIMETER),
        "length": Quantity.of("4", Unit.METER), "quantity": 2,
        "steel_grade": "С245", "Ry": Quantity.of("245", Unit.MEGAPASCAL),
        "E": Quantity.of("206", Unit.GIGAPASCAL),
        "density": Quantity.of("7850", Unit.KILOGRAM_PER_CUBIC_METER),
        "load_case": "LC1", "combination": "C1", "N": Quantity.of("200", Unit.KILONEWTON),
        "Mx": Quantity.of("12", Unit.KILONEWTON_METER), "My": None,
        "Qx": None, "Qy": None, "governing_combination": "C1",
        "required_fire_resistance": Quantity.of("60", Unit.MINUTE),
        "stress_state": "Cжатый стержень", "heating_sides": 4,
        "support_condition": "Шарнирное опирание по концам",
        "effective_length_parameters": EffectiveLengthParameters(
            Quantity.of("2.8", Unit.METER), Quantity.of("2.8", Unit.METER),
            Decimal("0.7"), Decimal("0.7"),
        ),
        "critical_temperature": None, "unprotected_fire_resistance": None,
        "material_id": None, "coating_type": None, "required_thickness": None,
        "specific_consumption": None,
        "protected_area": Quantity.of("14.192", Unit.SQUARE_METER),
        "total_consumption": None,
    }
    data.update(overrides)
    untraced = ProjectElement._UNTRACED_FIELDS
    provenance = {
        name: ValueProvenance(ProvenanceType.SOURCE, file="synthetic.json", field=name)
        for name, value in data.items()
        if value is not None and name not in untraced
    }
    data["provenance"] = provenance
    return ProjectElement(**data)


def test_project_element_to_rx38_safe_template_round_trip(template_rx38, tmp_path):
    output = tmp_path / "output.rx38"
    report = create_rx38_from_project_element(
        _element(), template_rx38, output, template_mark="К1"
    )
    assert report.round_trip_valid
    assert report.unknown_fields_count == 131
    assert any("Mx preserved" in warning for warning in report.warnings)
    assert any("RX3_RECALCULATION_REQUIRED" in warning for warning in report.warnings)
    changed = {change.index for change in report.changed_fields}
    assert {1, 3, 14, 15, 24, 25, 49, 51, 66, 67}.issubset(changed)
    assert 50 not in changed  # Mx is still unconfirmed and remains verbatim.

    original = construction_records(read_rx38(template_rx38))[0]
    result = construction_records(read_rx38(output))[0]
    assert result.fields[50] == original.fields[50] == "777"
    assert result.fields[135] == original.fields[135]
    assert len(result.fields) == 200


def test_profile_mismatch_is_blocked(template_rx38, tmp_path):
    with pytest.raises(Rx38TemplateMismatchError, match="Profile mismatch"):
        create_rx38_from_project_element(
            _element(profile_name="35 К1"), template_rx38, tmp_path / "bad.rx38", template_mark="К1"
        )


def test_different_effective_axes_require_engineer_decision(template_rx38, tmp_path):
    parameters = EffectiveLengthParameters(
        Quantity.of("2.8", Unit.METER), Quantity.of("3.2", Unit.METER),
        Decimal("0.7"), Decimal("0.8"),
    )
    with pytest.raises(Rx38EngineeringConflictError, match="governing axis"):
        create_rx38_from_project_element(
            _element(effective_length_parameters=parameters),
            template_rx38, tmp_path / "bad.rx38", template_mark="К1",
        )


def test_ptm_conflict_is_blocked(template_rx38, tmp_path):
    with pytest.raises(Rx38EngineeringConflictError, match="PTM conflict"):
        create_rx38_from_project_element(
            _element(ptm=Quantity.of("7", Unit.MILLIMETER)),
            template_rx38, tmp_path / "bad.rx38", template_mark="К1",
        )


def test_creation_never_overwrites_template_or_existing_output(template_rx38, tmp_path):
    existing = tmp_path / "existing.rx38"
    existing.write_text("do not replace", encoding="utf-8")

    with pytest.raises(Rx38ProjectAdapterError, match="different files"):
        create_rx38_from_project_element(
            _element(), template_rx38, template_rx38, template_mark="К1"
        )
    with pytest.raises(Rx38ProjectAdapterError, match="already exists"):
        create_rx38_from_project_element(
            _element(), template_rx38, existing, template_mark="К1"
        )
    assert existing.read_text(encoding="utf-8") == "do not replace"
