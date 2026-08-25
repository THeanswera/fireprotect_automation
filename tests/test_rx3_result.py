from datetime import datetime, timezone
from decimal import Decimal

import pytest

from fireprotect.model import (
    ProjectElement,
    ProvenanceType,
    Quantity,
    Unit,
    ValueProvenance,
)
from fireprotect.rx3.parser import Rx38Record
from fireprotect.rx3.result import (
    Rx3ResultError,
    apply_rx3_result,
    rx38_record_to_rx3_result,
)


def _record(**changes: str) -> Rx38Record:
    fields = [""] * 200
    values = {
        0: "Tconstr",
        1: "К1",
        2: "opaque-value",
        3: "К1",
        19: "30 К1",
        20: "11080",
        21: "1774",
        22: "6,245772266",
        33: "245",
        42: "С245",
        44: "650,5",
        49: "-125,5",
        50: "12,25",
        54: "15",
        55: "90",
        72: "Материал X",
        104: "Стандартный пожар",
    }
    values.update({int(index): value for index, value in changes.items()})
    for index, value in values.items():
        fields[index] = value
    return Rx38Record(
        "Tconstr",
        tuple(fields),
        tuple(fields),
        tuple(fields),
        line_number=2,
    )


def _element() -> ProjectElement:
    values = {name: None for name in ProjectElement.field_names()}
    values.update(
        project_id="P1",
        element_id="E1",
        mark="К1",
        element_type="column",
        source_file="lira.csv",
        source_type="LIRA_CSV",
        source_element_id="E1",
        source_row=2,
        timestamp=datetime(2026, 8, 25, tzinfo=timezone.utc),
        profile_name="30К1",
        area=Quantity.of("11080", Unit.SQUARE_MILLIMETER),
        heated_perimeter=Quantity.of("1774", Unit.MILLIMETER),
        ptm=Quantity.of("6.245772266", Unit.MILLIMETER),
        steel_grade="С245",
        N=Quantity.of("-125.5", Unit.KILONEWTON),
        required_fire_resistance=Quantity.of("90", Unit.MINUTE),
    )
    values["provenance"] = {
        name: ValueProvenance(
            ProvenanceType.SOURCE, file="lira.csv", field=name
        )
        for name, value in values.items()
        if value is not None and name not in ProjectElement._UNTRACED_FIELDS
    }
    return ProjectElement(**values)


def test_rx38_result_extracts_confirmed_and_labels_probable_without_unknown_values():
    result = rx38_record_to_rx3_result(
        _record(), source_file="calculated.rx38"
    )

    assert result.mark == "К1"
    assert result.area == Quantity.of("11080", Unit.SQUARE_MILLIMETER)
    assert result.critical_temperature == Quantity.of("650.5", Unit.CELSIUS)
    assert result.fireproofing_thickness is None
    assert result.provenance["critical_temperature"].kind is ProvenanceType.RX3_RESULT
    assert result.provenance["critical_temperature"].field == (
        "Tconstr[44]:critical_temperature_c"
    )
    assert result.provenance["critical_temperature"].row == 2

    confirmed_indices = {field.index for field in result.confirmed_fields}
    probable_indices = {field.index for field in result.probable_fields}
    assert 44 in confirmed_indices
    assert 50 not in confirmed_indices and 50 in probable_indices
    assert 2 in result.unknown_indices
    assert "opaque-value" not in str(result.as_dict())


def test_apply_rx3_result_attaches_only_confirmed_outputs_with_rx3_provenance():
    result = rx38_record_to_rx3_result(
        _record(), source_file="calculated.rx38"
    )
    updated = apply_rx3_result(_element(), result)

    assert updated.critical_temperature == Quantity.of("650.5", Unit.CELSIUS)
    assert updated.unprotected_fire_resistance == Quantity.of("15", Unit.MINUTE)
    assert updated.trace_for("critical_temperature").kind is ProvenanceType.RX3_RESULT
    assert updated.Mx is None  # probable RX38 field 50 is never promoted.


def test_apply_rx3_result_blocks_identity_or_input_conflict():
    result = rx38_record_to_rx3_result(
        _record(**{"21": "1700"}), source_file="calculated.rx38"
    )
    with pytest.raises(Rx3ResultError, match="perimeter differs"):
        apply_rx3_result(_element(), result)


def test_invalid_confirmed_decimal_is_rejected():
    with pytest.raises(Rx3ResultError, match="is not decimal"):
        rx38_record_to_rx3_result(
            _record(**{"44": "not-a-number"}), source_file="calculated.rx38"
        )
