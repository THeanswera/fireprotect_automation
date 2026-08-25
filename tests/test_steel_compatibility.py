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
