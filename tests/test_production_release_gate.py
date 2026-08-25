from fireprotect.execution import ExecutionMode
from fireprotect.release import (
    BlockerCode,
    IssueReadinessStatus,
    ReleaseBlocker,
    evaluate_issue_readiness,
)


def test_production_is_ready_only_when_blocker_list_is_empty():
    readiness = evaluate_issue_readiness(
        mode=ExecutionMode.PRODUCTION,
        warnings=("review note",),
        evidence={"reviewed": True},
    )
    assert readiness.status is IssueReadinessStatus.READY_FOR_ISSUE
    assert readiness.blockers == ()


def test_single_engineering_blocker_forces_not_ready():
    readiness = evaluate_issue_readiness(
        mode=ExecutionMode.PRODUCTION,
        blockers=(
            ReleaseBlocker(
                BlockerCode.EXCEL_RECALCULATION_REQUIRED,
                "Microsoft Excel recalculation is not confirmed",
            ),
        ),
    )
    assert readiness.status is IssueReadinessStatus.NOT_READY_FOR_ISSUE
    assert readiness.blockers[0].code is BlockerCode.EXCEL_RECALCULATION_REQUIRED
