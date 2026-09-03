from __future__ import annotations

from decimal import Decimal

import pytest

from fireprotect.lira import (
    ConventionStatus,
    LiraConventionError,
    LiraGoverningSelectionError,
    LiraMappingError,
    LiraRx3ComponentConvention,
    LiraRx3ConventionRegistry,
    Rx3ForceTarget,
    ValueTransform,
)


SCOPED_MEMBER = {
    "profile_standard": "ГОСТ 8240-97",
    "profile_name": "22П",
    "rx3_template": "Б2",
    "stress_state": "ONE_PLANE_BENDING",
    "member_length_m": Decimal("3.00"),
    "member_rotation_degrees": Decimal("0"),
}


def _transform(
    component: str,
    value: Decimal,
    *,
    parsed_value: Decimal,
    raw_token: str,
    source_unit: str,
):
    normalized_unit = "kN*m" if component == "My" else "kN"
    return LiraRx3ConventionRegistry.validated_22p_one_plane_xx().transform_selected_value(
        component,
        value,
        source_parsed_value=parsed_value,
        source_row=17,
        source_sheet_or_table="synthetic force table",
        source_raw_token=raw_token,
        source_unit=source_unit,
        normalized_unit=normalized_unit,
        **SCOPED_MEMBER,
    )


def test_negative_my_becomes_exact_magnitude_and_preserves_signed_provenance() -> None:
    source_value = Decimal("-1.234567890123456789")

    transformed = _transform(
        "My",
        source_value,
        parsed_value=Decimal("-1.234567890123456789"),
        raw_token="-1.234567890123456789",
        source_unit="kN*m",
    )

    assert transformed.source_signed_value is source_value
    assert transformed.target_rx3_value == Decimal("1.234567890123456789")
    assert isinstance(transformed.target_rx3_value, Decimal)
    assert transformed.value_transform is ValueTransform.MAGNITUDE
    assert transformed.target_rx3_component is (
        Rx3ForceTarget.FIELD50_MAX_MAJOR_AXIS_MOMENT
    )
    audit = transformed.as_dict()
    assert audit["source"] == {
        "component": "My",
        "parsed_value": "-1.234567890123456789",
        "signed_value": "-1.234567890123456789",
        "signed_unit": "kN*m",
        "raw_token": "-1.234567890123456789",
        "raw_unit": "kN*m",
        "row": 17,
        "sheet_or_table": "synthetic force table",
    }
    assert audit["target"] == {
        "component": "FIELD50_MAX_MAJOR_AXIS_MOMENT",
        "field_index": 50,
        "value": "1.234567890123456789",
        "unit": "kN*m",
    }
    assert audit["governing_result_selection"] == "CALLER_SELECTED_UNVALIDATED"


def test_positive_my_remains_the_same_positive_magnitude() -> None:
    transformed = _transform(
        "My",
        Decimal("2.500"),
        parsed_value=Decimal("2.500"),
        raw_token="2.500",
        source_unit="kN*m",
    )

    assert transformed.source_signed_value == Decimal("2.500")
    assert transformed.target_rx3_value == Decimal("2.500")


def test_source_decimal_and_normalized_signed_value_must_match_exactly() -> None:
    with pytest.raises(LiraMappingError, match="does not normalize"):
        _transform(
            "My",
            Decimal("2.5001"),
            parsed_value=Decimal("2.500"),
            raw_token="2.500",
            source_unit="kN*m",
        )


def test_negative_qz_becomes_exact_positive_q_magnitude() -> None:
    transformed = _transform(
        "Qz",
        Decimal("-0.76543210987654321"),
        parsed_value=Decimal("-0.76543210987654321"),
        raw_token="-0.76543210987654321",
        source_unit="kN",
    )

    assert transformed.source_signed_value == Decimal("-0.76543210987654321")
    assert transformed.target_rx3_value == Decimal("0.76543210987654321")
    assert transformed.target_rx3_component is Rx3ForceTarget.FIELD92_MAX_SHEAR_Q


def test_value_transforms_reject_binary_float_and_are_not_sign_multipliers() -> None:
    assert ValueTransform.SIGNED_LINEAR.apply(Decimal("-2.5")) == Decimal("-2.5")
    assert ValueTransform.MAGNITUDE.apply(Decimal("-2.5")) == Decimal("2.5")
    with pytest.raises(LiraMappingError, match="exact decimal"):
        ValueTransform.MAGNITUDE.apply(-2.5)  # type: ignore[arg-type]
    with pytest.raises(LiraMappingError, match="unknown convention.My fields"):
        LiraRx3ConventionRegistry.from_dict(
            {
                "My": {
                    "source_component": "My",
                    "target_rx3_component": "FIELD50_MAX_MAJOR_AXIS_MOMENT",
                    "sign_multiplier": -1,
                    "axis_interpretation": "synthetic",
                    "verification_status": "VALIDATED",
                    "evidence_reference": "synthetic",
                    "evidence_scope": None,
                }
            }
        )


def test_scoped_component_validation_does_not_validate_governing_selection() -> None:
    registry = LiraRx3ConventionRegistry.validated_22p_one_plane_xx()
    round_tripped = LiraRx3ConventionRegistry.from_dict(registry.as_dict())

    assert registry.components["My"].verification_status is ConventionStatus.VALIDATED
    assert registry.components["Qz"].verification_status is ConventionStatus.VALIDATED
    assert round_tripped.as_dict() == registry.as_dict()
    with pytest.raises(
        LiraGoverningSelectionError,
        match="LIRA_GOVERNING_RESULT_SELECTION_UNRESOLVED",
    ):
        registry.require_rx38_generation_ready(
            {"My": Decimal("-2.5"), "Qz": Decimal("-1.25")},
            **SCOPED_MEMBER,
        )


def test_zero_rotation_validation_does_not_apply_to_rotated_member() -> None:
    registry = LiraRx3ConventionRegistry.validated_22p_one_plane_xx()

    with pytest.raises(LiraConventionError, match="My is unresolved"):
        registry.transform_selected_value(
            "My",
            Decimal("-2.5"),
            source_parsed_value=Decimal("-2.5"),
            source_row=17,
            source_sheet_or_table="synthetic force table",
            source_raw_token="-2.5",
            source_unit="kN*m",
            normalized_unit="kN*m",
            **{
                **SCOPED_MEMBER,
                "member_rotation_degrees": Decimal("0.1"),
            },
        )


def test_unvalidated_components_and_derived_field78_remain_unavailable() -> None:
    registry = LiraRx3ConventionRegistry.validated_22p_one_plane_xx()

    for component in ("Mk", "Mz", "Qy"):
        assert registry.components[component].verification_status is (
            ConventionStatus.UNKNOWN
        )
        assert registry.components[component].target_rx3_component is None
    assert 78 not in {target.field_index for target in Rx3ForceTarget}
    with pytest.raises(LiraMappingError, match="Invalid RX3 force writer target"):
        LiraRx3ComponentConvention(
            source_component="My",
            target_rx3_component="FIELD78_PERSISTED_COPY",  # type: ignore[arg-type]
            value_transform=ValueTransform.MAGNITUDE,
            axis_interpretation="synthetic",
            verification_status=ConventionStatus.UNKNOWN,
            evidence_reference=None,
        )
