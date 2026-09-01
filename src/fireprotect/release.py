from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from enum import Enum
from typing import TYPE_CHECKING, Any, Mapping

from .execution import ExecutionMode

if TYPE_CHECKING:
    from .excel.registry import ExcelTemplateVerification
    from .model import ProjectElement
    from .normative import NormativeValidation
    from .rx3.gui_validation import Rx3ValidationReport
    from .rx3.safety import (
        HeatingExposureVerification,
        LiraRx3ForceConvention,
        Rx3CalculationProfile,
        SteelCompatibilityReport,
    )
    from .technical import FireproofingTechnicalEntry


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
    EXCEL_LOOKUP_TABLE_MISMATCH = "EXCEL_LOOKUP_TABLE_MISMATCH"
    HEATING_EXPOSURE_UNVERIFIED = "HEATING_EXPOSURE_UNVERIFIED"
    STEEL_TEMPERATURE_MODEL_UNVERIFIED = "STEEL_TEMPERATURE_MODEL_UNVERIFIED"
    PRODUCTION_GATE_EVIDENCE_MISSING = "PRODUCTION_GATE_EVIDENCE_MISSING"


REQUIRED_PRODUCTION_GATES = frozenset(
    {
        "rx3_action_mapping",
        "force_convention",
        "steel_compatibility",
        "rx3_template_profile",
        "heating_exposure",
        "normative_trace",
        "fireproofing_technical_data",
        "rx3_recalculation",
        "excel_template",
        "excel_recalculation",
    }
)


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
class TechnicalProductionEvidence:
    entry: "FireproofingTechnicalEntry"
    calculation_date: date


@dataclass(frozen=True, slots=True)
class ProductionEvidence:
    """Typed artifacts from which release gates are derived fail-closed."""

    action_elements: tuple["ProjectElement", ...] = ()
    force_conventions: tuple["LiraRx3ForceConvention", ...] = ()
    steel_compatibility: tuple["SteelCompatibilityReport", ...] = ()
    rx3_template_profiles: tuple["Rx3CalculationProfile", ...] = ()
    heating_exposures: tuple["HeatingExposureVerification", ...] = ()
    normative_validations: tuple["NormativeValidation", ...] = ()
    technical_data: tuple[TechnicalProductionEvidence, ...] = ()
    rx3_validations: tuple["Rx3ValidationReport", ...] = ()
    excel_templates: tuple["ExcelTemplateVerification", ...] = ()

    def verified_gates(self) -> dict[str, bool]:
        from .excel.registry import ExcelTemplateVerification
        from .model import ProjectElement, Quantity
        from .normative import NormativeValidation
        from .rx3.gui_validation import Rx3ValidationReport
        from .rx3.safety import (
            HeatingExposureVerification,
            LiraRx3ForceConvention,
            Rx3CalculationProfile,
            SteelCompatibilityReport,
        )
        from .technical import FireproofingTechnicalEntry

        def complete(items: tuple[object, ...], kind: type[object]) -> bool:
            return bool(items) and all(isinstance(item, kind) for item in items)

        element_count = len(self.action_elements)

        def complete_for_elements(
            items: tuple[object, ...], kind: type[object]
        ) -> bool:
            return len(items) == element_count and complete(items, kind)

        actions_valid = complete(self.action_elements, ProjectElement) and all(
            all(
                isinstance(getattr(element, component), Quantity)
                and getattr(element, component).si_value == 0
                for component in ("Mx", "My", "Qx", "Qy")
            )
            for element in self.action_elements
        )
        force_valid = complete_for_elements(
            self.force_conventions, LiraRx3ForceConvention
        ) and all(item.verified for item in self.force_conventions)
        steel_valid = complete_for_elements(
            self.steel_compatibility, SteelCompatibilityReport
        ) and all(item.verified for item in self.steel_compatibility)
        profiles_valid = complete_for_elements(
            self.rx3_template_profiles, Rx3CalculationProfile
        ) and all(item.production_verified for item in self.rx3_template_profiles)
        heating_valid = complete_for_elements(
            self.heating_exposures, HeatingExposureVerification
        ) and all(item.verified for item in self.heating_exposures)
        normative_valid = complete_for_elements(
            self.normative_validations, NormativeValidation
        ) and all(item.valid_for_production for item in self.normative_validations)
        technical_valid = bool(self.technical_data) and all(
            isinstance(item, TechnicalProductionEvidence)
            and isinstance(item.entry, FireproofingTechnicalEntry)
            and item.entry.verified_for_production_on(item.calculation_date)
            for item in self.technical_data
        )
        rx3_valid = complete_for_elements(
            self.rx3_validations, Rx3ValidationReport
        ) and all(item.verified_for_production for item in self.rx3_validations)
        excel_template_valid = complete(
            self.excel_templates, ExcelTemplateVerification
        ) and all(item.verified for item in self.excel_templates)
        return {
            "rx3_action_mapping": actions_valid,
            "force_convention": force_valid,
            "steel_compatibility": steel_valid,
            "rx3_template_profile": profiles_valid,
            "heating_exposure": heating_valid,
            "normative_trace": normative_valid,
            "fireproofing_technical_data": technical_valid,
            "rx3_recalculation": rx3_valid,
            "excel_template": excel_template_valid,
            # Excel recalculation remains an explicitly open production blocker;
            # no run-config or hash-only evidence type is accepted yet.
            "excel_recalculation": False,
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
    production_evidence: ProductionEvidence | None = None,
) -> IssueReadiness:
    """Return the issue status without converting missing proof to a warning."""

    if not isinstance(mode, ExecutionMode):
        raise TypeError("mode must be ExecutionMode")
    evidence_payload = dict(evidence or {})
    collected = list(blockers)
    if mode is not ExecutionMode.PRODUCTION:
        collected.insert(
            0,
            ReleaseBlocker(
                BlockerCode.NON_PRODUCTION_MODE,
                f"{mode.value} workflows cannot be issued",
            ),
        )
    else:
        verified_state = (
            production_evidence.verified_gates()
            if isinstance(production_evidence, ProductionEvidence)
            else {gate: False for gate in REQUIRED_PRODUCTION_GATES}
        )
        evidence_payload["production_gate_evidence"] = verified_state
        verified_gates = {
            gate for gate, verified in verified_state.items() if verified
        }
        missing_gates = sorted(REQUIRED_PRODUCTION_GATES - verified_gates)
        if missing_gates:
            collected.append(
                ReleaseBlocker(
                    BlockerCode.PRODUCTION_GATE_EVIDENCE_MISSING,
                    "Required production gates lack positive evidence: "
                    + ", ".join(missing_gates),
                )
            )
        existing_codes = {blocker.code for blocker in collected}
        if (
            "heating_exposure" in missing_gates
            and BlockerCode.HEATING_EXPOSURE_UNVERIFIED not in existing_codes
        ):
            collected.append(
                ReleaseBlocker(
                    BlockerCode.HEATING_EXPOSURE_UNVERIFIED,
                    "Heating exposure is not bound to each ProjectElement and exact RX38 template record",
                )
            )
        if (
            "steel_compatibility" in missing_gates
            and BlockerCode.STEEL_TEMPERATURE_MODEL_UNVERIFIED
            not in existing_codes
        ):
            collected.append(
                ReleaseBlocker(
                    BlockerCode.STEEL_TEMPERATURE_MODEL_UNVERIFIED,
                    "Steel temperature model and thermal coefficients lack verified template compatibility",
                )
            )
    status = (
        IssueReadinessStatus.READY_FOR_ISSUE
        if not collected
        else IssueReadinessStatus.NOT_READY_FOR_ISSUE
    )
    return IssueReadiness(status, tuple(collected), warnings, evidence_payload)
