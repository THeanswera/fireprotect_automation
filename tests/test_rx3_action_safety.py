from decimal import Decimal

import pytest

from fireprotect.execution import ExecutionMode
from fireprotect.model import Quantity, Unit
from fireprotect.rx3.safety import (
    ActionZeroTolerance,
    Rx3SafetyContext,
    TemplateProfileError,
    UnverifiedRx38ActionMappingError,
    build_rx3_input,
)
from tests.safety_support import (
    force_convention,
    make_element,
    template_evidence,
)


@pytest.mark.parametrize(
    ("component", "value", "unit"),
    (
        ("Mx", "0.0001", Unit.KILONEWTON_METER),
        ("My", "-1", Unit.KILONEWTON_METER),
        ("Qx", "0.0001", Unit.KILONEWTON),
        ("Qy", "-1", Unit.KILONEWTON),
    ),
)
def test_significant_unmapped_action_blocks_generation(component, value, unit):
    element = make_element(**{component: Quantity.of(value, unit)})
    context = Rx3SafetyContext(
        ExecutionMode.VALIDATION,
        ActionZeroTolerance.strict(),
        template_evidence(),
        force_convention(),
        None,
    )

    with pytest.raises(UnverifiedRx38ActionMappingError, match=component):
        build_rx3_input(element, context)


def test_decimal_tolerance_is_explicit_and_not_binary_float():
    tolerance = ActionZeroTolerance(
        Quantity.of(Decimal("0.001"), Unit.KILONEWTON),
        Quantity.of(Decimal("0.001"), Unit.KILONEWTON_METER),
    )
    assert not tolerance.significant(
        "Mx", Quantity.of("0.001", Unit.KILONEWTON_METER)
    )
    assert tolerance.significant(
        "Mx", Quantity.of("0.0010001", Unit.KILONEWTON_METER)
    )


def test_validation_requires_separate_axial_only_template_evidence():
    context = Rx3SafetyContext(
        ExecutionMode.VALIDATION,
        ActionZeroTolerance.strict(),
        None,
        force_convention(),
        None,
    )
    with pytest.raises(TemplateProfileError, match="AXIAL_ONLY"):
        build_rx3_input(make_element(), context)


def test_pure_axial_verified_input_is_created_without_result_fields():
    rx3_input, blockers, warnings = build_rx3_input(
        make_element(critical_temperature=Quantity.of("650", Unit.CELSIUS)),
        Rx3SafetyContext(
            ExecutionMode.VALIDATION,
            ActionZeroTolerance.strict(),
            template_evidence(),
            force_convention(),
            None,
        ),
    )
    assert blockers == () and warnings == ()
    assert set(rx3_input.as_dict()) == {
        "mark",
        "N",
        "Mx",
        "My",
        "Qx",
        "Qy",
        "force_transformations",
    }


def test_production_rejects_nonzero_action_tolerance():
    with pytest.raises(ValueError, match="exact-zero"):
        Rx3SafetyContext(
            ExecutionMode.PRODUCTION,
            ActionZeroTolerance(
                Quantity.of("1", Unit.KILONEWTON),
                Quantity.of("1", Unit.KILONEWTON_METER),
            ),
            template_evidence(),
            force_convention(),
            None,
        )


def test_string_production_mode_cannot_bypass_rx3_context():
    with pytest.raises(TypeError, match="ExecutionMode"):
        Rx3SafetyContext(
            "PRODUCTION",  # type: ignore[arg-type]
            ActionZeroTolerance.strict(),
            template_evidence(),
            force_convention(),
            None,
        )
