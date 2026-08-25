from datetime import date
from pathlib import Path

from fireprotect.execution import ExecutionMode
from fireprotect.normative import validate_normative_trace
from fireprotect.release import BlockerCode
from tests.test_normative_registry import trace, write_registry


def test_document_not_yet_effective_is_blocked(tmp_path: Path):
    validation = validate_normative_trace(
        trace(),
        write_registry(tmp_path, effective_from="2026-09-01"),
        calculation_date=date(2026, 8, 25),
        mode=ExecutionMode.PRODUCTION,
    )
    assert BlockerCode.NORMATIVE_DOCUMENT_NOT_EFFECTIVE in {
        blocker.code for blocker in validation.blockers
    }


def test_adoption_does_not_override_future_effective_date(tmp_path: Path):
    document = write_registry(tmp_path, effective_from="2026-09-01").require(
        "TEST_SP"
    )
    assert document.adopted_date == date(2025, 12, 1)
    assert not document.effective_on(date(2026, 8, 25))
