from __future__ import annotations

import csv
from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path

from fireprotect.execution import ExecutionMode
from fireprotect.model import (
    EffectiveLengthParameters,
    ProjectElement,
    ProvenanceType,
    Quantity,
    Unit,
    ValueProvenance,
)
from fireprotect.rx3.parser import Rx38Record
from fireprotect.rx3.safety import (
    ActionZeroTolerance,
    EvidenceStatus,
    ForceConventionStatus,
    HeatingExposureEvidence,
    LiraRx3ForceConvention,
    Rx3SafetyContext,
    Rx3TemplateEvidence,
    Rx3TemplateUseCase,
    SteelCalculationProperties,
    rx38_record_fingerprint,
)


def make_element(**overrides: object) -> ProjectElement:
    values = {name: None for name in ProjectElement.field_names()}
    values.update(
        project_id="P1",
        element_id="E1",
        mark="K1",
        element_type="column",
        source_file="element.json",
        source_type="PROJECT_JSON",
        source_element_id="E1",
        source_row=1,
        timestamp=datetime(2026, 8, 25, tzinfo=timezone.utc),
        section_type="I-section",
        profile_standard="STO ASCHM 20-93",
        profile_name="30K1",
        area=Quantity.of("11080", Unit.SQUARE_MILLIMETER),
        full_perimeter=Quantity.of("1774", Unit.MILLIMETER),
        heated_perimeter=Quantity.of("1774", Unit.MILLIMETER),
        ptm=Quantity.of("6.24577226606539", Unit.MILLIMETER),
        length=Quantity.of("4", Unit.METER),
        quantity=2,
        steel_grade="S245",
        Ry=Quantity.of("245", Unit.MEGAPASCAL),
        E=Quantity.of("206", Unit.GIGAPASCAL),
        density=Quantity.of("7850", Unit.KILOGRAM_PER_CUBIC_METER),
        load_case="LC1",
        combination="C1",
        N=Quantity.of("-125.5", Unit.KILONEWTON),
        Mx=Quantity.of("0", Unit.KILONEWTON_METER),
        My=Quantity.of("0", Unit.KILONEWTON_METER),
        Qx=Quantity.of("0", Unit.KILONEWTON),
        Qy=Quantity.of("0", Unit.KILONEWTON),
        governing_combination="C1",
        required_fire_resistance=Quantity.of("90", Unit.MINUTE),
        stress_state="compression",
        heating_sides=4,
        support_condition="pinned",
        effective_length_parameters=EffectiveLengthParameters(
            Quantity.of("2.8", Unit.METER),
            Quantity.of("2.8", Unit.METER),
            Decimal("0.7"),
            Decimal("0.7"),
        ),
        protected_area=Quantity.of("14.192", Unit.SQUARE_METER),
    )
    values.update(overrides)
    values["provenance"] = {
        name: ValueProvenance(
            ProvenanceType.SOURCE,
            file="element.json",
            row=1,
            field=name,
        )
        for name, value in values.items()
        if value is not None and name not in ProjectElement._UNTRACED_FIELDS
    }
    return ProjectElement(**values)


def template_evidence(*, profile_verified: bool = True) -> Rx3TemplateEvidence:
    return Rx3TemplateEvidence(
        Rx3TemplateUseCase.AXIAL_ONLY,
        EvidenceStatus.VERIFIED,
        "controlled experiment RX3-AXIAL-01",
        True,
        "test engineer",
        date(2026, 8, 25),
        "1",
        profile_verified,
        rx38_record_fingerprint(make_record()),
    )


def force_convention(
    *, verified: bool = True, n_multiplier: str = "1"
) -> LiraRx3ForceConvention:
    return LiraRx3ForceConvention(
        "LIRA CSV",
        "RX3",
        "tension",
        "compression",
        "element local axes",
        "Mx->Mx, My->My",
        "Qx->Qx, Qy->Qy",
        {
            "N": Decimal(n_multiplier),
            "Mx": Decimal("1"),
            "My": Decimal("1"),
            "Qx": Decimal("1"),
            "Qy": Decimal("1"),
        },
        "explicit test convention",
        "RX3-AXIAL-01" if verified else None,
        (
            ForceConventionStatus.VERIFIED
            if verified
            else ForceConventionStatus.UNVERIFIED
        ),
        verified,
        "test engineer" if verified else None,
        date(2026, 8, 25) if verified else None,
        "1" if verified else None,
    )


def safety_context(
    mode: ExecutionMode = ExecutionMode.VALIDATION,
    *,
    with_template_evidence: bool = True,
    verified_convention: bool = True,
    n_multiplier: str = "1",
    controlled_experiment: bool = False,
    allow_unverified_force_convention: bool = False,
    steel_properties: SteelCalculationProperties | None = None,
    with_heating_evidence: bool = True,
) -> Rx3SafetyContext:
    return Rx3SafetyContext(
        mode,
        ActionZeroTolerance.strict(),
        template_evidence() if with_template_evidence else None,
        force_convention(
            verified=verified_convention, n_multiplier=n_multiplier
        ),
        steel_properties,
        controlled_experiment,
        allow_unverified_force_convention,
        (
            HeatingExposureEvidence(
                project_element_id="E1",
                heating_sides=4,
                template_record_sha256=rx38_record_fingerprint(make_record()),
                status=EvidenceStatus.VERIFIED,
                source="controlled heating exposure fixture",
                confirmed_by="test engineer",
                confirmed_at=date(2026, 8, 25),
                version="1",
            )
            if with_heating_evidence
            else None
        ),
    )


def verified_steel_properties() -> SteelCalculationProperties:
    return SteelCalculationProperties(
        steel_grade="S245",
        nominal_yield_strength=Quantity.of("245", Unit.MEGAPASCAL),
        design_yield_strength=Quantity.of("245", Unit.MEGAPASCAL),
        rx3_stored_strength_parameter=Quantity.of("245", Unit.MEGAPASCAL),
        thickness_min=None,
        thickness_max=None,
        elastic_modulus=Quantity.of("206", Unit.GIGAPASCAL),
        density=Quantity.of("7850", Unit.KILOGRAM_PER_CUBIC_METER),
        temperature_model="EN 1993-1-2",
        source_document="controlled steel mapping protocol",
        clause_or_table="RX3-STEEL-01",
        material_standard="test standard",
        confidence=EvidenceStatus.VERIFIED,
        provenance="controlled experiment",
        rx3_strength_mapping_verified=True,
        temperature_model_code=1,
        thermal_coefficients={
            82: Decimal("25"),
            83: Decimal("1"),
            84: Decimal("1"),
        },
    )


def template_fields(**overrides: str) -> list[str]:
    fields = [""] * 200
    defaults = {
        0: "Tconstr",
        1: "K1",
        3: "K1",
        5: "I-section",
        14: "4",
        15: "2",
        17: "STO ASCHM 20-93",
        19: "30 K1",
        20: "11080",
        21: "1774",
        22: "6,24577226606539",
        23: "160,108303249097",
        24: "7,096",
        25: "14,192",
        32: "7850",
        33: "245",
        34: "206000",
        42: "S245",
        44: "650",
        45: "compression",
        48: "pinned",
        49: "-100",
        50: "0",
        51: "2,8",
        52: "0,2",
        54: "15",
        55: "90",
        66: "347,912",
        67: "695,824",
        72: "None",
        82: "25",
        83: "1",
        84: "1",
        104: "standard fire",
        141: "0,7",
        188: "EN 1993-1-2",
        189: "1",
    }
    for index, value in defaults.items():
        fields[index] = value
    for raw_index, value in overrides.items():
        fields[int(raw_index)] = value
    return fields


def make_record(**overrides: str) -> Rx38Record:
    fields = tuple(template_fields(**overrides))
    return Rx38Record("Tconstr", fields, original_fields=fields)


def write_template(path: Path, **overrides: str) -> None:
    with path.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.writer(stream, delimiter=";", lineterminator="\r\n")
        writer.writerow(["Trazdel", "Safety test"])
        writer.writerow(template_fields(**overrides))
