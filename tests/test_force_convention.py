from decimal import Decimal

import pytest

from fireprotect.execution import ExecutionMode
from fireprotect.model import Quantity, Unit
from fireprotect.rx3.safety import ForceConventionError, build_rx3_input
from tests.safety_support import make_element, safety_context


def test_unverified_force_convention_blocks_production():
    with pytest.raises(ForceConventionError, match="verified"):
        build_rx3_input(
            make_element(),
            safety_context(
                ExecutionMode.PRODUCTION,
                verified_convention=False,
            ),
        )


def test_production_requires_convention_even_for_zero_n():
    with pytest.raises(ForceConventionError, match="Production requires"):
        build_rx3_input(
            make_element(N=Quantity.of("0", Unit.KILONEWTON)),
            safety_context(
                ExecutionMode.PRODUCTION,
                verified_convention=False,
            ),
        )


def test_verified_sign_change_is_explicit_in_audit():
    result, _, _ = build_rx3_input(
        make_element(),
        safety_context(ExecutionMode.PRODUCTION, n_multiplier="-1"),
    )
    assert result.axial_force.value == Decimal("125.5")
    transformation = result.force_transformations[0]
    assert transformation["source"]["value"] == "-125.5"
    assert transformation["target"]["value"] == "125.5"
    assert transformation["multiplier"] == "-1"


def test_validation_exception_is_named_and_audited():
    result, _, warnings = build_rx3_input(
        make_element(),
        safety_context(
            ExecutionMode.VALIDATION,
            verified_convention=False,
            controlled_experiment=True,
            allow_unverified_force_convention=True,
        ),
    )
    assert result.force_transformations[0]["status"] == (
        "UNVERIFIED_VALIDATION_EXCEPTION"
    )
    assert result.force_transformations[0]["rule"] == (
        "RX3_CONTROLLED_INPUT_CONVENTION"
    )
    assert "controlled VALIDATION" in warnings[0]
