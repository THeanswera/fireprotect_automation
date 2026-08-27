from dataclasses import replace

import pytest

from fireprotect.execution import ExecutionMode
from fireprotect.model import Quantity, Unit
from fireprotect.rx3.safety import (
    SteelCompatibilityError,
    SteelCompatibilityStatus,
    evaluate_steel_compatibility,
)
from fireprotect.rx3.project_adapter import project_element_to_rx38_record
from tests.safety_support import (
    make_element,
    make_record,
    safety_context,
    verified_steel_properties,
)


def test_grade_change_with_stale_template_strength_is_blocked():
    with pytest.raises(SteelCompatibilityError, match="grade differs"):
        evaluate_steel_compatibility(
            make_element(
                steel_grade="S355",
                Ry=Quantity.of("355", Unit.MEGAPASCAL),
            ),
            make_record(),
            None,
        )


def test_same_grade_with_numeric_strength_mismatch_is_blocked():
    with pytest.raises(SteelCompatibilityError, match="Ry differs"):
        evaluate_steel_compatibility(
            make_element(Ry=Quantity.of("240", Unit.MEGAPASCAL)),
            make_record(),
            None,
        )


def test_verified_steel_mapping_allows_coherent_write_values():
    report = evaluate_steel_compatibility(
        make_element(), make_record(), verified_steel_properties()
    )
    assert report.status is SteelCompatibilityStatus.VERIFIED
    assert report.write_values == {33: "245", 42: "S245"}
    assert report.evidence["temperature_model"] == "EN 1993-1-2 test profile"


def test_verified_profile_must_match_project_design_strength():
    properties = replace(
        verified_steel_properties(),
        design_yield_strength=Quantity.of("240", Unit.MEGAPASCAL),
    )
    with pytest.raises(SteelCompatibilityError, match="design_yield_strength"):
        evaluate_steel_compatibility(make_element(), make_record(), properties)


def test_legacy_numeric_match_is_not_sufficient_in_production():
    with pytest.raises(SteelCompatibilityError, match="Production requires"):
        project_element_to_rx38_record(
            make_element(),
            make_record(),
            safety_context=safety_context(ExecutionMode.PRODUCTION),
        )


def test_verified_mapping_rejects_negative_stored_strength():
    with pytest.raises(ValueError, match="greater than zero"):
        replace(
            verified_steel_properties(),
            rx3_stored_strength_parameter=Quantity.of("-1", Unit.MEGAPASCAL),
        )


def test_verified_mapping_flag_must_be_a_real_bool():
    with pytest.raises(TypeError, match="must be bool"):
        replace(
            verified_steel_properties(),
            rx3_strength_mapping_verified="false",  # type: ignore[arg-type]
        )


def test_verified_properties_cannot_change_template_grade():
    with pytest.raises(SteelCompatibilityError, match="grade change"):
        evaluate_steel_compatibility(
            make_element(
                steel_grade="S355",
                Ry=Quantity.of("355", Unit.MEGAPASCAL),
            ),
            make_record(),
            replace(
                verified_steel_properties(),
                steel_grade="S355",
                nominal_yield_strength=Quantity.of("355", Unit.MEGAPASCAL),
                design_yield_strength=Quantity.of("355", Unit.MEGAPASCAL),
                rx3_stored_strength_parameter=Quantity.of(
                    "355", Unit.MEGAPASCAL
                ),
            ),
        )


def test_template_evidence_must_match_exact_record():
    context = safety_context(
        ExecutionMode.VALIDATION,
        steel_properties=verified_steel_properties(),
    )
    mismatched = replace(
        context.template_evidence,
        template_record_sha256="0" * 64,
    )
    with pytest.raises(ValueError, match="exact RX38 template"):
        project_element_to_rx38_record(
            make_element(),
            make_record(),
            safety_context=replace(context, template_evidence=mismatched),
        )
