from dataclasses import replace

import pytest

from fireprotect.execution import ExecutionMode
from fireprotect.rx3.project_adapter import project_element_to_rx38_record
from fireprotect.rx3.safety import HeatingExposureError
from tests.safety_support import make_element, make_record, safety_context


def test_validation_blocks_without_exact_heating_exposure_evidence() -> None:
    with pytest.raises(HeatingExposureError, match="HEATING_EXPOSURE_UNVERIFIED"):
        project_element_to_rx38_record(
            make_element(),
            make_record(),
            safety_context=safety_context(with_heating_evidence=False),
        )


def test_heating_evidence_for_another_element_is_rejected() -> None:
    context = safety_context()
    assert context.heating_exposure is not None
    mismatched = replace(context.heating_exposure, project_element_id="OTHER")
    with pytest.raises(HeatingExposureError, match="HEATING_EXPOSURE_UNVERIFIED"):
        project_element_to_rx38_record(
            make_element(),
            make_record(),
            safety_context=replace(context, heating_exposure=mismatched),
        )


def test_draft_preserves_template_flags_with_explicit_warning() -> None:
    _, warnings = project_element_to_rx38_record(
        make_element(),
        make_record(),
        safety_context=safety_context(
            ExecutionMode.DRAFT, with_heating_evidence=False
        ),
    )
    assert any("HEATING_EXPOSURE_UNVERIFIED" in warning for warning in warnings)
