from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any, Mapping

from .execution import ExecutionMode


class IssueReadinessStatus(str, Enum):
    READY_FOR_ISSUE = "READY_FOR_ISSUE"
    NOT_READY_FOR_ISSUE = "NOT_READY_FOR_ISSUE"


class BlockerCode(str, Enum):
    NON_PRODUCTION_MODE = "NON_PRODUCTION_MODE"
    UNVERIFIED_RX38_MX_MAPPING = "UNVERIFIED_RX38_MX_MAPPING"
    UNVERIFIED_RX38_MY_MAPPING = "UNVERIFIED_RX38_MY_MAPPING"
    UNVERIFIED_RX38_QX_MAPPING = "UNVERIFIED_RX38_QX_MAPPING"
    UNVERIFIED_RX38_QY_MAPPING = "UNVERIFIED_RX38_QY_MAPPING"
    UNVERIFIED_FORCE_CONVENTION = "UNVERIFIED_FORCE_CONVENTION"
    STEEL_TEMPLATE_INCOMPATIBLE = "STEEL_TEMPLATE_INCOMPATIBLE"
    STEEL_STRENGTH_MAPPING_UNVERIFIED = "STEEL_STRENGTH_MAPPING_UNVERIFIED"
    RX3_TEMPLATE_PROFILE_UNVERIFIED = "RX3_TEMPLATE_PROFILE_UNVERIFIED"
    UNKNOWN_CRITICAL_RX3_SETTING = "UNKNOWN_CRITICAL_RX3_SETTING"
    NORMATIVE_TRACE_MISSING = "NORMATIVE_TRACE_MISSING"
    NORMATIVE_DOCUMENT_NOT_FOUND = "NORMATIVE_DOCUMENT_NOT_FOUND"
    NORMATIVE_DOCUMENT_NOT_EFFECTIVE = "NORMATIVE_DOCUMENT_NOT_EFFECTIVE"
    NORMATIVE_EDITION_UNVERIFIED = "NORMATIVE_EDITION_UNVERIFIED"
    NORMATIVE_SOURCE_HASH_MISMATCH = "NORMATIVE_SOURCE_HASH_MISMATCH"
    FIREPROOFING_TECHNICAL_DATA_UNVERIFIED = (
        "FIREPROOFING_TECHNICAL_DATA_UNVERIFIED"
    )
    STALE_RX3_RESULT = "STALE_RX3_RESULT"
    RX3_GUI_RECALCULATION_UNVERIFIED = "RX3_GUI_RECALCULATION_UNVERIFIED"
    EXCEL_RECALCULATION_REQUIRED = "EXCEL_RECALCULATION_REQUIRED"
    EXCEL_TEMPLATE_UNVERIFIED = "EXCEL_TEMPLATE_UNVERIFIED"


@dataclass(frozen=True, slots=True)
class ReleaseBlocker:
    code: BlockerCode
    message: str
    element_id: str | None = None

    def as_dict(self) -> dict[str, str | None]:
        return {
            "code": self.code.value,
            "message": self.message,
            "element_id": self.element_id,
        }


@dataclass(frozen=True, slots=True)
class IssueReadiness:
    status: IssueReadinessStatus
    blockers: tuple[ReleaseBlocker, ...]
    warnings: tuple[str, ...]
    evidence: Mapping[str, Any]

    def as_dict(self) -> dict[str, Any]:
        return {
            "status": self.status.value,
            "blockers": [item.as_dict() for item in self.blockers],
            "warnings": list(self.warnings),
            "evidence": dict(self.evidence),
        }


def evaluate_issue_readiness(
    *,
    mode: ExecutionMode,
    blockers: tuple[ReleaseBlocker, ...] = (),
    warnings: tuple[str, ...] = (),
    evidence: Mapping[str, Any] | None = None,
) -> IssueReadiness:
    """Return the issue status without converting missing proof to a warning."""

    if not isinstance(mode, ExecutionMode):
        raise TypeError("mode must be ExecutionMode")
    collected = list(blockers)
    if mode is not ExecutionMode.PRODUCTION:
        collected.insert(
            0,
            ReleaseBlocker(
                BlockerCode.NON_PRODUCTION_MODE,
                f"{mode.value} workflows cannot be issued",
            ),
        )
    status = (
        IssueReadinessStatus.READY_FOR_ISSUE
        if not collected
        else IssueReadinessStatus.NOT_READY_FOR_ISSUE
    )
    return IssueReadiness(status, tuple(collected), warnings, evidence or {})
