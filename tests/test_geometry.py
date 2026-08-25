from decimal import Decimal

import pytest

from fireprotect.geometry import (
    AngleSection,
    ChannelSection,
    ComparisonStatus,
    GeometryQuantity,
    HeatingExposure,
    ISection,
    RectangularHollowSection,
    calculate_geometry,
    compare_geometry_value,
    apply_geometry_result,
)
from fireprotect.model import ProjectElement, ProvenanceType, Unit, ValueProvenance
from datetime import datetime, timezone


def test_i_section_regression_against_confirmed_rx38_geometry():
    section = ISection(
        height_mm=Decimal("298"), width_mm=Decimal("299"),
        web_thickness_mm=Decimal("9"), flange_thickness_mm=Decimal("14"),
        area_mm2=Decimal("11080"),
    )
    exposure = HeatingExposure(Decimal("1774"), ("all contour surfaces",), "RX3 project input")
    result = calculate_geometry(section, exposure, length_m="3,3", quantity=1)
    assert result.full_perimeter.value == Decimal("1774")
    assert result.ptm.value == Decimal("6.245772266065388951521984216")
    assert result.protected_area_one.value == Decimal("5.8542")
    assert result.protected_area_total.value == Decimal("5.8542")


def test_closed_section_uses_external_contour():
    section = RectangularHollowSection("160", "160", "8", "4644")
    exposure = HeatingExposure("640", ("top", "right", "bottom", "left"), "Engineer-confirmed four-side exposure")
    result = calculate_geometry(section, exposure, length_m="7,75", quantity=8)
    assert result.full_perimeter.value == Decimal("640")
    assert result.ptm.value == Decimal("7.25625")
    assert result.section_factor.value == Decimal("137.8122308354866494401378122")
    assert result.protected_area_total.value == Decimal("39.68000")


@pytest.mark.parametrize(
    ("section", "expected_perimeter", "expected_ptm"),
    (
        (
            ChannelSection("220", "82", "5.4", "9.5", "2670"),
            Decimal("757.2"),
            Decimal("3.526148969889"),
        ),
        (
            AngleSection("80", "80", "6", "938"),
            Decimal("320"),
            Decimal("2.93125"),
        ),
    ),
)
def test_excel_profile_family_regressions(section, expected_perimeter, expected_ptm):
    """Values are transcribed from the analysed workbook rows 30 and 34."""

    result = calculate_geometry(
        section,
        HeatingExposure(
            expected_perimeter,
            ("full contour as stored by workbook",),
            "Regression comparison with 01_Общая ОБМ workbook",
        ),
        length_m="1",
        quantity="1",
    )
    assert result.full_perimeter.value == expected_perimeter
    assert result.ptm.value.quantize(Decimal("0.000000000001")) == expected_ptm


def test_heating_exposure_cannot_be_defaulted():
    with pytest.raises(ValueError, match="engineering decision"):
        HeatingExposure("1000", (), "")


def test_binary_float_is_rejected_at_boundary():
    with pytest.raises(TypeError, match="Binary float"):
        ISection(298.0, "299", "9", "14", "11080")


def test_regression_mismatch_is_reported_not_resolved():
    comparison = compare_geometry_value(
        "ptm",
        GeometryQuantity(Decimal("6.24"), "mm"),
        GeometryQuantity(Decimal("6.10"), "mm"),
        tolerance="0.01",
        reference_source="Excel Sheet1!A1",
    )
    assert comparison.status is ComparisonStatus.MISMATCH
    assert comparison.difference == Decimal("0.14")
    assert comparison.reference_source == "Excel Sheet1!A1"


def test_geometry_result_updates_project_with_explicit_provenance():
    values = {name: None for name in ProjectElement.field_names()}
    values.update({
        "project_id": "P", "element_id": "E", "mark": "К1", "element_type": "column",
        "source_file": "source", "source_type": "TEST", "source_element_id": None,
        "source_row": None, "timestamp": datetime(2026, 8, 25, tzinfo=timezone.utc),
    })
    values["provenance"] = {
        name: ValueProvenance(ProvenanceType.SOURCE, file="source", field=name)
        for name in ("project_id", "element_id", "mark", "element_type")
    }
    element = ProjectElement(**values)
    section = RectangularHollowSection("160", "160", "8", "4644")
    result = calculate_geometry(
        section,
        HeatingExposure("640", ("all",), "engineer input"),
        length_m="7.75",
        quantity=8,
    )
    traces = {
        name: ValueProvenance(
            ProvenanceType.CALCULATED,
            file="geometry",
            field=name,
            formula="fireprotect.geometry.calculate_geometry",
        )
        for name in ("area", "full_perimeter", "heated_perimeter", "ptm", "protected_area")
    }
    updated = apply_geometry_result(element, result, provenance=traces)
    assert updated.ptm is not None
    assert updated.ptm.to(Unit.MILLIMETER).value == Decimal("7.25625")
    assert updated.protected_area is not None
    assert updated.protected_area.to(Unit.SQUARE_METER).value == Decimal("39.68000")
