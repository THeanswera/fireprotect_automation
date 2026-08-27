import csv
import json
from datetime import date
from decimal import Decimal
from hashlib import sha256
from pathlib import Path

import pytest

from fireprotect.execution import ExecutionMode
from fireprotect.rx3.gui_validation import (
    Rx3GuiValidationError,
    prepare_rx3_validation,
    validate_rx3_result_files,
)
from fireprotect.rx3.safety import (
    ActionZeroTolerance,
    EvidenceStatus,
    ForceConventionStatus,
    LiraRx3ForceConvention,
    Rx3SafetyContext,
    Rx3TemplateEvidence,
    Rx3TemplateUseCase,
)


def _template(path: Path) -> None:
    fields = [""] * 200
    for index, value in {
        0: "Tconstr",
        1: "К1",
        2: "opaque-before",
        3: "К1",
        5: "Двутавр",
        17: "СТО АСЧМ 20-93",
        19: "30 К1",
        20: "11080",
        32: "7850",
        33: "245",
        34: "206000",
        42: "С245",
        44: "650",
        50: "0",
        54: "15",
        72: "Нет",
    }.items():
        fields[index] = value
    with path.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.writer(stream, delimiter=";", lineterminator="\r\n")
        writer.writerow(["Trazdel", "Validation"])
        writer.writerow(fields)


def _project_payload() -> dict:
    names = (
        "project_id element_id mark element_type source_file source_type "
        "source_element_id source_row timestamp section_type profile_standard "
        "profile_name area full_perimeter heated_perimeter ptm length quantity "
        "steel_grade Ry E density load_case combination N Mx My Qx Qy "
        "governing_combination required_fire_resistance stress_state heating_sides "
        "support_condition effective_length_parameters critical_temperature "
        "unprotected_fire_resistance material_id coating_type required_thickness "
        "specific_consumption protected_area total_consumption"
    ).split()
    payload = {name: None for name in names}

    def q(value: str, unit: str) -> dict[str, str]:
        return {"value": value, "unit": unit}

    payload.update(
        project_id="P1",
        element_id="E1",
        mark="К1-VALID",
        element_type="column",
        source_file="lira.csv",
        source_type="LIRA_CSV",
        source_element_id="E1",
        source_row=2,
        timestamp="2026-08-25T10:00:00+03:00",
        section_type="Двутавр",
        profile_standard="СТО АСЧМ 20-93",
        profile_name="30К1",
        area=q("11080", "mm2"),
        full_perimeter=q("1774", "mm"),
        heated_perimeter=q("1774", "mm"),
        ptm=q("6.24577226606539", "mm"),
        length=q("4", "m"),
        quantity=2,
        steel_grade="С245",
        Ry=q("245", "MPa"),
        E=q("206", "GPa"),
        density=q("7850", "kg/m3"),
        load_case="LC1",
        combination="C1",
        N=q("-125.5", "kN"),
        Mx=q("0", "kN*m"),
        My=q("0", "kN*m"),
        Qx=q("0", "kN"),
        Qy=q("0", "kN"),
        governing_combination="C1",
        required_fire_resistance=q("90", "min"),
        stress_state="Сжатый стержень",
        heating_sides=4,
        support_condition="Шарнирное опирание по концам",
        effective_length_parameters={
            "buckling_length_x": q("2.8", "m"),
            "buckling_length_y": q("2.8", "m"),
            "factor_x": "0.7",
            "factor_y": "0.7",
        },
        protected_area=q("14.192", "m2"),
    )
    untraced = {
        "source_file",
        "source_type",
        "source_element_id",
        "source_row",
        "timestamp",
    }
    payload["provenance"] = {
        name: {"kind": "SOURCE", "file": "project.json", "field": name}
        for name, value in payload.items()
        if value is not None and name not in untraced and name != "provenance"
    }
    return payload


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


def _edit_rx38(source: Path, destination: Path) -> None:
    with source.open("r", encoding="utf-8", newline="") as stream:
        rows = list(csv.reader(stream, delimiter=";"))
    record = next(row for row in rows if row and row[0] == "Tconstr")
    record[2] = "opaque-after"
    record[44] = "675"
    record[50] = "12,5"
    record[54] = "18"
    with destination.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.writer(stream, delimiter=";", lineterminator="\r\n")
        writer.writerows(rows)


def test_prepare_bundle_and_classify_manual_rx3_changes(tmp_path: Path):
    source_template = tmp_path / "source.rx38"
    source_project = tmp_path / "source.json"
    bundle_dir = tmp_path / "validation"
    _template(source_template)
    source_project.write_text(
        json.dumps(_project_payload(), ensure_ascii=False), encoding="utf-8"
    )
    template_hash = sha256(source_template.read_bytes()).hexdigest()

    bundle = prepare_rx3_validation(
        source_project, source_template, bundle_dir, template_mark="К1",
        safety_context=_safety_context(),
    )

    assert sha256(source_template.read_bytes()).hexdigest() == template_hash
    assert bundle.generated.exists()
    assert bundle.diff_json.exists() and bundle.diff_markdown.exists()
    instructions = bundle.instructions.read_text(encoding="utf-8")
    assert "calculated.rx38" in instructions
    assert "validate-rx3-result generated.rx38 calculated.rx38" in instructions

    calculated = bundle_dir / "calculated.rx38"
    _edit_rx38(bundle.generated, calculated)
    report = validate_rx3_result_files(bundle.generated, calculated)

    record = report.data["records"][0]
    assert {item["index"] for item in record["confirmed_changes"]} == {44, 54}
    assert {item["index"] for item in record["probable_changes"]} == {50}
    assert {item["index"] for item in record["unknown_changes"]} == {2}
    assert report.json_path.exists() and report.markdown_path.exists()
    assert record["rx3_result"]["critical_temperature"] == {
        "value": "675",
        "unit": "degC",
    }


def test_validation_artifacts_are_never_overwritten_implicitly(tmp_path: Path):
    source_template = tmp_path / "source.rx38"
    source_project = tmp_path / "source.json"
    _template(source_template)
    source_project.write_text(json.dumps(_project_payload()), encoding="utf-8")
    directory = tmp_path / "validation"
    directory.mkdir()
    (directory / "keep.txt").write_text("keep", encoding="utf-8")

    with pytest.raises(Rx3GuiValidationError, match="new or empty"):
        prepare_rx3_validation(source_project, source_template, directory)
