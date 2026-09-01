from fireprotect.execution import ExecutionMode
from fireprotect.release import (
    BlockerCode,
    IssueReadinessStatus,
    REQUIRED_PRODUCTION_GATES,
    ReleaseBlocker,
    evaluate_issue_readiness,
)


def test_boolean_self_certification_cannot_make_production_ready():
    readiness = evaluate_issue_readiness(
        mode=ExecutionMode.PRODUCTION,
        warnings=("review note",),
        evidence={
            "reviewed": True,
            "production_gates": {
                gate: True for gate in REQUIRED_PRODUCTION_GATES
            },
        },
    )
    assert readiness.status is IssueReadinessStatus.NOT_READY_FOR_ISSUE
    assert readiness.blockers[0].code is BlockerCode.PRODUCTION_GATE_EVIDENCE_MISSING
    assert any(
        blocker.code is BlockerCode.HEATING_EXPOSURE_UNVERIFIED
        for blocker in readiness.blockers
    )
    assert not any(readiness.evidence["production_gate_evidence"].values())


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


def test_empty_blocker_list_without_gate_evidence_is_not_ready():
    readiness = evaluate_issue_readiness(mode=ExecutionMode.PRODUCTION)
    assert readiness.status is IssueReadinessStatus.NOT_READY_FOR_ISSUE
    assert readiness.blockers[0].code is BlockerCode.PRODUCTION_GATE_EVIDENCE_MISSING
