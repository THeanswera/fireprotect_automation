from fireprotect.decision import (
    FireResistanceDecisionStatus,
    RequiredFireResistanceDecision,
)
from fireprotect.model import Quantity, Unit
from fireprotect.normative import NormativeTrace


def _decision(**changes):
    data = {
        "required_fire_resistance": Quantity.of("90", Unit.MINUTE),
        "construction_type": "column",
        "building_fire_resistance_degree": "II",
        "source": "engineer assignment",
        "clause_or_table": None,
        "assignment_method": "explicit project decision",
        "engineer_confirmation": False,
    }
    data.update(changes)
    return RequiredFireResistanceDecision(**data)


def test_required_fire_resistance_needs_explicit_confirmation():
    decision = _decision()
    assert decision.status is FireResistanceDecisionStatus.NEEDS_ENGINEER_CONFIRMATION


def test_confirmation_does_not_claim_normative_verification_without_trace():
    decision = _decision(engineer_confirmation=True)
    assert decision.status is (
        FireResistanceDecisionStatus.ENGINEER_CONFIRMED_UNVERIFIED_SOURCE
    )


def test_trace_is_preserved_separately_from_engineer_confirmation():
    decision = _decision(
        engineer_confirmation=True,
        normative_trace=NormativeTrace(
            "SP-ID", "2026", "5.4", "table 1", "Required R assignment"
        ),
    )
    assert decision.status is FireResistanceDecisionStatus.ENGINEER_CONFIRMED_WITH_TRACE
    assert decision.as_dict()["normative_trace"]["clause"] == "5.4"
