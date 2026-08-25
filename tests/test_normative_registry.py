from datetime import date
from hashlib import sha256
from pathlib import Path

from fireprotect.execution import ExecutionMode
from fireprotect.normative import (
    NormativeRegistry,
    NormativeTrace,
    validate_normative_trace,
)
from fireprotect.release import (
    BlockerCode,
    IssueReadinessStatus,
    evaluate_issue_readiness,
)


def write_registry(
    directory: Path,
    *,
    status: str = "VERIFIED_CURRENT",
    effective_from: str = "2026-01-01",
    expected_hash: str | None = None,
) -> NormativeRegistry:
    document = directory / "normative.txt"
    document.write_text("controlled normative evidence", encoding="utf-8")
    digest = expected_hash or sha256(document.read_bytes()).hexdigest()
    registry = directory / "registry.yaml"
    registry.write_text(
        f"""schema_version: 1
documents:
  - id: TEST_SP
    designation: TEST SP
    title: Controlled test document
    edition: edition-1
    status: {status}
    adopted_date: 2025-12-01
    effective_from: {effective_from}
    effective_to: null
    path: normative.txt
    sha256: {digest}
    evidence: controlled test
""",
        encoding="utf-8",
    )
    return NormativeRegistry.load(registry)


def trace() -> NormativeTrace:
    return NormativeTrace("TEST_SP", "edition-1", "5.1", "table 1", "R")


def test_missing_normative_trace_blocks_production(tmp_path: Path):
    validation = validate_normative_trace(
        None,
        write_registry(tmp_path),
        calculation_date=date(2026, 8, 25),
        mode=ExecutionMode.PRODUCTION,
    )
    assert validation.blockers[0].code is BlockerCode.NORMATIVE_TRACE_MISSING


def test_missing_trace_is_allowed_in_draft_but_issue_is_not_ready(tmp_path: Path):
    validation = validate_normative_trace(
        None,
        write_registry(tmp_path),
        calculation_date=date(2026, 8, 25),
        mode=ExecutionMode.DRAFT,
    )
    assert validation.blockers == ()
    readiness = evaluate_issue_readiness(
        mode=ExecutionMode.DRAFT, blockers=validation.blockers
    )
    assert readiness.status is IssueReadinessStatus.NOT_READY_FOR_ISSUE


def test_unverified_edition_blocks_production(tmp_path: Path):
    validation = validate_normative_trace(
        trace(),
        write_registry(tmp_path, status="EDITION_VERIFICATION_REQUIRED"),
        calculation_date=date(2026, 8, 25),
        mode=ExecutionMode.PRODUCTION,
    )
    assert BlockerCode.NORMATIVE_EDITION_UNVERIFIED in {
        blocker.code for blocker in validation.blockers
    }


def test_registered_hash_is_checked(tmp_path: Path):
    validation = validate_normative_trace(
        trace(),
        write_registry(tmp_path, expected_hash="0" * 64),
        calculation_date=date(2026, 8, 25),
        mode=ExecutionMode.PRODUCTION,
    )
    assert BlockerCode.NORMATIVE_SOURCE_HASH_MISMATCH in {
        blocker.code for blocker in validation.blockers
    }
