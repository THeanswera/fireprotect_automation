import csv
from dataclasses import replace
from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path

import pytest

from fireprotect.execution import ExecutionMode
from fireprotect.model import (
    EffectiveLengthParameters,
    ProjectElement,
    ProvenanceType,
    Quantity,
    Unit,
    ValueProvenance,
)
from fireprotect.rx3.parser import construction_records, read_rx38
from fireprotect.rx3.project_adapter import (
    DerivedFieldWritePolicy,
    Rx38EngineeringConflictError,
    Rx38ProjectAdapterError,
    Rx38TemplateMismatchError,
    create_rx38_from_project_element,
)
from fireprotect.rx3.safety import (
    ActionZeroTolerance,
    EvidenceStatus,
    ForceConventionStatus,
    LiraRx3ForceConvention,
    Rx3SafetyContext,
    Rx3TemplateEvidence,
    Rx3TemplateUseCase,
    UnverifiedRx38ActionMappingError,
    rx38_record_fingerprint,
)


def _template_fields() -> list[str]:
    fields = [""] * 200
    values = {
        0: "Tconstr", 1: "К1", 2: "0", 3: "К1", 4: "1", 5: "Двутавр",
        8: "298", 9: "299", 11: "9", 13: "14", 14: "3,3", 15: "1",
        17: "СТО АСЧМ 20-93", 19: "30 К1", 20: "11080", 21: "1774",
        22: "6,24577226606539", 23: "160,108303249097", 24: "5,8542", 25: "5,8542",
        26: "0,00018849", 27: "0,000062409", 28: "0,000062409",
        29: "0,0012651", 30: "0,0004175", 32: "7850", 33: "245", 34: "206000",
        42: "С245", 44: "650", 45: "Cжатый стержень", 48: "Шарнирное опирание по концам",
        49: "100", 50: "0", 51: "2,31", 52: "0,2", 54: "15", 55: "60",
        66: "287,0274", 67: "287,0274", 72: "Нет", 82: "25", 83: "1", 84: "1",
        85: "1", 86: "1194", 87: "3,9402", 88: "3,9402", 104: "Стандартный температурный режим",
        113: "9,27973199329983", 114: "107,761732851986", 141: "0,7",
        188: "σ 0.2% и E по данным EN 1993-1-2", 189: "1",
    }
    for index, value in values.items():
        fields[index] = value
    return fields


@pytest.fixture
def template_rx38(tmp_path: Path) -> Path:
    path = tmp_path / "template.rx38"
    with path.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.writer(stream, delimiter=";", lineterminator="\r\n")
        writer.writerow(["Trazdel", "Тест"])
        writer.writerow(_template_fields())
    return path


def _element(**overrides) -> ProjectElement:
    data = {
        "project_id": "P1", "element_id": "E1", "mark": "К-NEW", "element_type": "column",
        "source_file": "lira.csv", "source_type": "LIRA_CSV", "source_element_id": "1",
        "source_row": 2, "timestamp": datetime(2026, 8, 25, tzinfo=timezone.utc),
        "section_type": "Двутавр", "profile_standard": "СТО АСЧМ 20-93", "profile_name": "30К1",
        "area": Quantity.of("11080", Unit.SQUARE_MILLIMETER),
        "full_perimeter": Quantity.of("1774", Unit.MILLIMETER),
        "heated_perimeter": Quantity.of("1774", Unit.MILLIMETER),
        "ptm": Quantity.of("6.24577226606539", Unit.MILLIMETER),
        "length": Quantity.of("4", Unit.METER), "quantity": 2,
        "steel_grade": "С245", "Ry": Quantity.of("245", Unit.MEGAPASCAL),
        "E": Quantity.of("206", Unit.GIGAPASCAL),
        "density": Quantity.of("7850", Unit.KILOGRAM_PER_CUBIC_METER),
        "load_case": "LC1", "combination": "C1", "N": Quantity.of("200", Unit.KILONEWTON),
        "Mx": Quantity.of("0", Unit.KILONEWTON_METER),
        "My": Quantity.of("0", Unit.KILONEWTON_METER),
        "Qx": Quantity.of("0", Unit.KILONEWTON),
        "Qy": Quantity.of("0", Unit.KILONEWTON),
        "governing_combination": "C1",
        "required_fire_resistance": Quantity.of("60", Unit.MINUTE),
        "stress_state": "Cжатый стержень", "heating_sides": 4,
        "support_condition": "Шарнирное опирание по концам",
        "effective_length_parameters": EffectiveLengthParameters(
            Quantity.of("2.8", Unit.METER), Quantity.of("2.8", Unit.METER),
            Decimal("0.7"), Decimal("0.7"),
        ),
        "critical_temperature": None, "unprotected_fire_resistance": None,
        "material_id": None, "coating_type": None, "required_thickness": None,
        "specific_consumption": None,
        "protected_area": Quantity.of("14.192", Unit.SQUARE_METER),
        "total_consumption": None,
    }
    data.update(overrides)
    untraced = ProjectElement._UNTRACED_FIELDS
    provenance = {
        name: ValueProvenance(ProvenanceType.SOURCE, file="synthetic.json", field=name)
        for name, value in data.items()
        if value is not None and name not in untraced
    }
    data["provenance"] = provenance
    return ProjectElement(**data)


def _safety_context() -> Rx3SafetyContext:
    confirmed = date(2026, 8, 25)
    return Rx3SafetyContext(
        ExecutionMode.DRAFT,
        ActionZeroTolerance.strict(),
        Rx3TemplateEvidence(
            Rx3TemplateUseCase.AXIAL_ONLY,
            EvidenceStatus.VERIFIED,
            "controlled RX3 template validation",
            True,
            "test engineer",
            confirmed,
            "1",
            True,
        ),
        LiraRx3ForceConvention(
            "LIRA CSV",
            "RX3",
            "tension",
            "compression",
            "element local axes",
            "Mx->Mx, My->My",
            "Qx->Qx, Qy->Qy",
            {name: Decimal("1") for name in ("N", "Mx", "My", "Qx", "Qy")},
            "identity test convention",
            "controlled validation protocol",
            ForceConventionStatus.VERIFIED,
            True,
            "test engineer",
            confirmed,
            "1",
        ),
        None,
    )


def test_project_element_to_rx38_safe_template_round_trip(template_rx38, tmp_path):
    output = tmp_path / "output.rx38"
    report = create_rx38_from_project_element(
        _element(), template_rx38, output, template_mark="К1",
        safety_context=_safety_context(),
    )
    assert report.round_trip_valid
    assert report.unknown_fields_count == 127
    assert any(
        "Mx production writing and My/Qx/Qy mappings remain unverified" in warning
        for warning in report.warnings
    )
    assert any("STALE_TEMPLATE_RESULT" in warning for warning in report.warnings)
    changed = {change.index for change in report.changed_fields}
    assert {1, 3, 14, 15, 24, 25, 49, 51, 66, 67}.issubset(changed)
    assert 22 not in changed
    assert 23 not in changed
    assert 50 not in changed  # Mx is still unconfirmed and remains verbatim.

    original = construction_records(read_rx38(template_rx38))[0]
    result = construction_records(read_rx38(output))[0]
    assert result.fields[50] == original.fields[50] == "0"
    assert result.fields[23] == original.fields[23] == "160,108303249097"
    assert result.fields[135] == original.fields[135]
    assert len(result.fields) == 200


def test_changed_authoritative_input_recomputes_derived_fields(
    template_rx38, tmp_path
):
    output = tmp_path / "changed-perimeter.rx38"
    report = create_rx38_from_project_element(
        _element(
            heated_perimeter=Quantity.of("1600", Unit.MILLIMETER),
            ptm=Quantity.of("6.925", Unit.MILLIMETER),
            protected_area=Quantity.of("12.8", Unit.SQUARE_METER),
        ),
        template_rx38,
        output,
        template_mark="К1",
        safety_context=_safety_context(),
    )

    changed = {change.index for change in report.changed_fields}
    assert {21, 22, 23, 24, 25}.issubset(changed)
    result = construction_records(read_rx38(output))[0]
    assert Decimal(result.fields[23].replace(",", ".")) == (
        Decimal(1000) * Decimal(1600) / Decimal(11080)
    )


def test_axial_force_preserves_declared_decimal_scale(template_rx38, tmp_path):
    output = tmp_path / "n30.rx38"
    create_rx38_from_project_element(
        _element(N=Quantity.of("30.00", Unit.KILONEWTON)),
        template_rx38,
        output,
        template_mark="К1",
        safety_context=_safety_context(),
    )

    result = construction_records(read_rx38(output))[0]
    assert result.fields[49] == "30,00"


def test_verified_template_fingerprint_resolves_duplicate_mark(
    template_rx38, tmp_path
):
    duplicate_template = tmp_path / "duplicate-mark.rx38"
    with template_rx38.open("r", encoding="utf-8", newline="") as stream:
        rows = list(csv.reader(stream, delimiter=";"))
    duplicate = rows[1].copy()
    duplicate[2] = "different opaque record"
    rows.append(duplicate)
    with duplicate_template.open("w", encoding="utf-8", newline="") as stream:
        csv.writer(stream, delimiter=";", lineterminator="\r\n").writerows(rows)

    context = _safety_context()
    context = replace(
        context,
        template_evidence=replace(
            context.template_evidence,
            template_record_sha256=rx38_record_fingerprint(
                construction_records(read_rx38(template_rx38))[0]
            ),
        ),
    )
    report = create_rx38_from_project_element(
        _element(N=Quantity.of("25.00", Unit.KILONEWTON)),
        duplicate_template,
        tmp_path / "generated.rx38",
        template_mark="К1",
        safety_context=context,
    )

    assert report.target_record_position == 1
    assert report.template_record_sha256 == (
        context.template_evidence.template_record_sha256
    )


def test_explicit_verified_policy_recomputes_compatible_derived_fields(
    template_rx38, tmp_path
):
    output = tmp_path / "recomputed.rx38"
    report = create_rx38_from_project_element(
        _element(),
        template_rx38,
        output,
        template_mark="К1",
        safety_context=_safety_context(),
        derived_field_policy=DerivedFieldWritePolicy.RECOMPUTE_VERIFIED,
    )

    changed = {change.index for change in report.changed_fields}
    assert 23 in changed
    result = construction_records(read_rx38(output))[0]
    assert Decimal(result.fields[23].replace(",", ".")) == (
        Decimal(1000) * Decimal(1774) / Decimal(11080)
    )


def test_incompatible_derived_field_requires_explicit_recomputation(
    template_rx38, tmp_path
):
    incompatible_template = tmp_path / "incompatible-template.rx38"
    with template_rx38.open("r", encoding="utf-8", newline="") as stream:
        rows = list(csv.reader(stream, delimiter=";"))
    rows[1][23] = "999"
    with incompatible_template.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.writer(stream, delimiter=";", lineterminator="\r\n")
        writer.writerows(rows)

    with pytest.raises(Rx38EngineeringConflictError, match="Derived field 23"):
        create_rx38_from_project_element(
            _element(),
            incompatible_template,
            tmp_path / "blocked.rx38",
            template_mark="К1",
            safety_context=_safety_context(),
        )

    report = create_rx38_from_project_element(
        _element(),
        incompatible_template,
        tmp_path / "explicit.rx38",
        template_mark="К1",
        safety_context=_safety_context(),
        derived_field_policy=DerivedFieldWritePolicy.RECOMPUTE_VERIFIED,
    )
    assert 23 in {change.index for change in report.changed_fields}


def test_profile_mismatch_is_blocked(template_rx38, tmp_path):
    with pytest.raises(Rx38TemplateMismatchError, match="Profile mismatch"):
        create_rx38_from_project_element(
            _element(profile_name="35 К1"), template_rx38, tmp_path / "bad.rx38", template_mark="К1",
            safety_context=_safety_context(),
        )


def test_different_effective_axes_require_engineer_decision(template_rx38, tmp_path):
    parameters = EffectiveLengthParameters(
        Quantity.of("2.8", Unit.METER), Quantity.of("3.2", Unit.METER),
        Decimal("0.7"), Decimal("0.8"),
    )
    with pytest.raises(Rx38EngineeringConflictError, match="governing axis"):
        create_rx38_from_project_element(
            _element(effective_length_parameters=parameters),
            template_rx38, tmp_path / "bad.rx38", template_mark="К1",
            safety_context=_safety_context(),
        )


def test_ptm_conflict_is_blocked(template_rx38, tmp_path):
    with pytest.raises(Rx38EngineeringConflictError, match="PTM conflict"):
        create_rx38_from_project_element(
            _element(ptm=Quantity.of("7", Unit.MILLIMETER)),
            template_rx38, tmp_path / "bad.rx38", template_mark="К1",
            safety_context=_safety_context(),
        )


def test_nonzero_moment_is_blocked_without_confirmed_rx38_mapping(
    template_rx38, tmp_path
):
    with pytest.raises(UnverifiedRx38ActionMappingError, match="Mx=12"):
        create_rx38_from_project_element(
            _element(Mx=Quantity.of("12", Unit.KILONEWTON_METER)),
            template_rx38,
            tmp_path / "blocked.rx38",
            template_mark="К1",
            safety_context=_safety_context(),
        )


def test_creation_never_overwrites_template_or_existing_output(template_rx38, tmp_path):
    existing = tmp_path / "existing.rx38"
    existing.write_text("do not replace", encoding="utf-8")

    with pytest.raises(Rx38ProjectAdapterError, match="different files"):
        create_rx38_from_project_element(
            _element(), template_rx38, template_rx38, template_mark="К1"
        )
    with pytest.raises(Rx38ProjectAdapterError, match="already exists"):
        create_rx38_from_project_element(
            _element(), template_rx38, existing, template_mark="К1"
        )
    assert existing.read_text(encoding="utf-8") == "do not replace"


def test_rx38_finalization_falls_back_when_hard_links_are_unsupported(
    monkeypatch, template_rx38, tmp_path
):
    def hard_link_unsupported(*args, **kwargs):
        raise OSError("synthetic filesystem without hard links")

    monkeypatch.setattr("fireprotect.files.os.link", hard_link_unsupported)
    output = tmp_path / "fallback.rx38"
    report = create_rx38_from_project_element(
        _element(),
        template_rx38,
        output,
        safety_context=_safety_context(),
    )
    assert output.exists()
    assert report.round_trip_valid
