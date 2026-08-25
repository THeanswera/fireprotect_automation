import pytest

from fireprotect.execution import ExecutionMode
from fireprotect.release import IssueReadinessStatus, evaluate_issue_readiness


@pytest.mark.parametrize("value", ("DRAFT", "validation", ExecutionMode.PRODUCTION))
def test_execution_mode_parser_is_explicit(value):
    assert isinstance(ExecutionMode.parse(value), ExecutionMode)


def test_unknown_execution_mode_is_rejected():
    with pytest.raises(ValueError, match="execution_mode"):
        ExecutionMode.parse("AUTO")


@pytest.mark.parametrize("mode", (ExecutionMode.DRAFT, ExecutionMode.VALIDATION))
def test_nonproduction_modes_can_never_be_issued(mode):
    readiness = evaluate_issue_readiness(mode=mode)
    assert readiness.status is IssueReadinessStatus.NOT_READY_FOR_ISSUE
    assert readiness.blockers[0].code.value == "NON_PRODUCTION_MODE"
