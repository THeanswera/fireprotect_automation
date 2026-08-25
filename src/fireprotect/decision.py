"""Explicit engineer decisions that must not be inferred from workbook tables."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any

from .model import Dimension, Quantity
from .normative import NormativeTrace


class FireResistanceDecisionStatus(str, Enum):
    NEEDS_ENGINEER_CONFIRMATION = "NEEDS_ENGINEER_CONFIRMATION"
    ENGINEER_CONFIRMED_UNVERIFIED_SOURCE = (
        "ENGINEER_CONFIRMED_UNVERIFIED_SOURCE"
    )
    ENGINEER_CONFIRMED_WITH_TRACE = "ENGINEER_CONFIRMED_WITH_TRACE"


@dataclass(frozen=True, slots=True)
class RequiredFireResistanceDecision:
    required_fire_resistance: Quantity
    construction_type: str
    building_fire_resistance_degree: str
    source: str
    clause_or_table: str | None
    assignment_method: str
    engineer_confirmation: bool
    normative_trace: NormativeTrace | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.required_fire_resistance, Quantity):
            raise TypeError("required_fire_resistance must be Quantity")
        if self.required_fire_resistance.dimension is not Dimension.TIME:
            raise ValueError("required_fire_resistance must have a time unit")
        if self.required_fire_resistance.si_value <= 0:
            raise ValueError("required_fire_resistance must be positive")
        for name in (
            "construction_type",
            "building_fire_resistance_degree",
            "source",
            "assignment_method",
        ):
            value = getattr(self, name)
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"{name} must not be empty")
        if self.clause_or_table is not None and not self.clause_or_table.strip():
            raise ValueError("clause_or_table must be non-empty when provided")
        if not isinstance(self.engineer_confirmation, bool):
            raise TypeError("engineer_confirmation must be bool")
        if self.normative_trace is not None and not isinstance(
            self.normative_trace, NormativeTrace
        ):
            raise TypeError("normative_trace must be NormativeTrace or None")

    @property
    def status(self) -> FireResistanceDecisionStatus:
        if not self.engineer_confirmation:
            return FireResistanceDecisionStatus.NEEDS_ENGINEER_CONFIRMATION
        if self.normative_trace is None:
            return (
                FireResistanceDecisionStatus.ENGINEER_CONFIRMED_UNVERIFIED_SOURCE
            )
        return FireResistanceDecisionStatus.ENGINEER_CONFIRMED_WITH_TRACE

    def require_engineer_confirmation(self) -> None:
        if not self.engineer_confirmation:
            raise ValueError(
                "Required fire resistance needs explicit engineer confirmation"
            )

    def as_dict(self) -> dict[str, Any]:
        trace = self.normative_trace
        return {
            "R": {
                "value": str(self.required_fire_resistance.value),
                "unit": self.required_fire_resistance.unit.value,
            },
            "construction_type": self.construction_type,
            "building_fire_resistance_degree": self.building_fire_resistance_degree,
            "source": self.source,
            "clause_or_table": self.clause_or_table,
            "assignment_method": self.assignment_method,
            "engineer_confirmation": self.engineer_confirmation,
            "status": self.status.value,
            "normative_trace": None
            if trace is None
            else {
                "document_id": trace.document_id,
                "edition": trace.edition,
                "clause": trace.clause,
                "formula_or_table": trace.formula_or_table,
                "description": trace.description,
            },
        }
