import csv
from pathlib import Path

import pytest

import fireprotect.rx3.gui_validation as gui_validation

from fireprotect.execution import ExecutionMode
from fireprotect.rx3.gui_validation import (
    Rx3GuiValidationError,
    validate_rx3_result_files,
)
from fireprotect.rx3.parser import (
    Rx38Document,
    Rx38Record,
    UnsafeRx38WriteError,
    read_rx38_document,
    write_rx38,
)
from fireprotect.rx3.project_adapter import create_rx38_from_project_element
from tests.safety_support import (
    make_element,
    make_record,
    safety_context,
    write_template,
)
from fireprotect.rx3.safety import GuiExecutionEvidence


def test_template_results_are_stale_and_excluded_from_rx3_input(tmp_path: Path):
    template = tmp_path / "template.rx38"
    output = tmp_path / "generated.rx38"
    write_template(template)
    report = create_rx38_from_project_element(
        make_element(),
        template,
        output,
        template_mark="K1",
        safety_context=safety_context(),
    )
    assert report.stale_template_result_indices == (44, 54)
    assert "critical_temperature" not in report.rx3_input
    assert "unprotected_fire_resistance" not in report.rx3_input


def test_byte_identical_calculated_file_does_not_prove_gui_run(tmp_path: Path):
    generated = tmp_path / "generated.rx38"
    calculated = tmp_path / "calculated.rx38"
    write_template(generated)
    calculated.write_bytes(generated.read_bytes())
    report = validate_rx3_result_files(generated, calculated)
    assert report.data["byte_identical"] is True
    assert report.data["status"] == "RX3_RECALCULATION_NOT_PROVEN"
    assert report.data["gui_recalculation_verified"] is False


def test_unrelated_file_change_does_not_refresh_stale_results(tmp_path: Path):
    generated = tmp_path / "generated.rx38"
    calculated = tmp_path / "calculated.rx38"
    write_template(generated)
    with generated.open("r", encoding="utf-8", newline="") as stream:
        rows = list(csv.reader(stream, delimiter=";"))
    record = next(row for row in rows if row and row[0] == "Tconstr")
    record[2] = "unrelated change"
    with calculated.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.writer(stream, delimiter=";", lineterminator="\r\n")
        writer.writerows(rows)

    report = validate_rx3_result_files(generated, calculated)
    assert report.data["byte_identical"] is False
    assert report.data["expected_result_fields_changed"] is False
    assert report.data["status"] == "RX3_RECALCULATION_NOT_PROVEN"


def test_formatting_only_result_changes_do_not_refresh_stale_values(tmp_path: Path):
    generated = tmp_path / "generated.rx38"
    calculated = tmp_path / "calculated.rx38"
    write_template(generated)
    with generated.open("r", encoding="utf-8", newline="") as stream:
        rows = list(csv.reader(stream, delimiter=";"))
    record = next(row for row in rows if row and row[0] == "Tconstr")
    record[44] = "650,0"
    record[54] = "15,0"
    with calculated.open("w", encoding="utf-8", newline="") as stream:
        csv.writer(stream, delimiter=";", lineterminator="\r\n").writerows(rows)

    report = validate_rx3_result_files(
        generated,
        calculated,
        gui_execution_evidence=GuiExecutionEvidence.ENGINEER_CONFIRMED,
        evidence_reference="controlled evidence",
    )
    assert report.data["rx3_recalculation_proven"] is False
    assert report.data["status"] == "RX3_RECALCULATION_NOT_PROVEN"


def test_production_rejects_any_non_result_rx38_change(tmp_path: Path):
    generated = tmp_path / "generated.rx38"
    calculated = tmp_path / "calculated.rx38"
    write_template(generated)
    with generated.open("r", encoding="utf-8", newline="") as stream:
        rows = list(csv.reader(stream, delimiter=";"))
    record = next(row for row in rows if row and row[0] == "Tconstr")
    record[2] = "unknown change"
    record[44] = "675"
    record[54] = "18"
    with calculated.open("w", encoding="utf-8", newline="") as stream:
        csv.writer(stream, delimiter=";", lineterminator="\r\n").writerows(rows)

    report = validate_rx3_result_files(
        generated,
        calculated,
        gui_execution_evidence=GuiExecutionEvidence.ENGINEER_CONFIRMED,
        evidence_reference="controlled evidence",
        mode=ExecutionMode.PRODUCTION,
    )
    assert report.data["gui_recalculation_verified"] is False
    assert report.data["status"] == "RX3_PRODUCTION_INPUTS_CHANGED"
    assert report.data["unsafe_production_change_indices"] == [2]


def test_gui_evidence_requires_a_reference(tmp_path: Path):
    generated = tmp_path / "generated.rx38"
    calculated = tmp_path / "calculated.rx38"
    write_template(generated)
    with generated.open("r", encoding="utf-8", newline="") as stream:
        rows = list(csv.reader(stream, delimiter=";"))
    record = next(row for row in rows if row and row[0] == "Tconstr")
    record[44] = "675"
    record[54] = "18"
    with calculated.open("w", encoding="utf-8", newline="") as stream:
        csv.writer(stream, delimiter=";", lineterminator="\r\n").writerows(rows)

    report = validate_rx3_result_files(
        generated,
        calculated,
        gui_execution_evidence=GuiExecutionEvidence.SCREENSHOT_REFERENCED,
    )
    assert report.data["gui_recalculation_verified"] is False


def test_validation_reports_cannot_overwrite_rx38_inputs(tmp_path: Path):
    generated = tmp_path / "generated.rx38"
    calculated = tmp_path / "calculated.rx38"
    write_template(generated)
    calculated.write_bytes(generated.read_bytes())
    before = generated.read_bytes()

    with pytest.raises(Rx3GuiValidationError, match="must not overwrite"):
        validate_rx3_result_files(
            generated,
            calculated,
            json_report=generated,
            overwrite=True,
        )
    assert generated.read_bytes() == before


@pytest.mark.parametrize("index", (44, 54))
def test_result_only_fields_cannot_be_written_as_input(index: int):
    with pytest.raises(UnsafeRx38WriteError, match="RESULT_ONLY"):
        make_record().with_typed_field(
            index, "999", compatibility_verified=True
        )


def test_probable_field_cannot_be_written_through_typed_api():
    with pytest.raises(UnsafeRx38WriteError, match="not confirmed"):
        make_record().with_typed_field(50, "12,5", compatibility_verified=True)


def test_legacy_confirmed_helper_cannot_claim_compatibility():
    with pytest.raises(UnsafeRx38WriteError, match="compatibility"):
        make_record().with_confirmed_field(33, "355")


def test_compatibility_claim_flag_must_be_a_real_bool():
    with pytest.raises(TypeError, match="must be bool"):
        make_record().with_typed_field(
            33,
            "355",
            compatibility_verified="true",  # type: ignore[arg-type]
        )


def test_raw_writer_rejects_a_fabricated_tconstr_baseline(tmp_path: Path):
    fields = make_record().fields
    record = Rx38Record("Tconstr", fields, original_fields=fields)
    with pytest.raises(UnsafeRx38WriteError, match="parser-origin"):
        write_rx38(Rx38Document((record,)), tmp_path / "unsafe.rx38")


def test_low_level_writer_cannot_overwrite_its_source(tmp_path: Path):
    source = tmp_path / "source.rx38"
    write_template(source)
    document = read_rx38_document(source)
    before = source.read_bytes()
    with pytest.raises(UnsafeRx38WriteError, match="source RX38"):
        write_rx38(document, source)
    with pytest.raises(UnsafeRx38WriteError, match="source RX38"):
        write_rx38(Rx38Document(document.records), source)
    assert source.read_bytes() == before


def test_low_level_writer_cannot_overwrite_another_existing_rx38(tmp_path: Path):
    source = tmp_path / "source.rx38"
    destination = tmp_path / "another_source.rx38"
    write_template(source)
    write_template(destination)
    document = read_rx38_document(source)
    before = destination.read_bytes()

    with pytest.raises(UnsafeRx38WriteError, match="existing RX38"):
        write_rx38(document, destination)
    assert destination.read_bytes() == before


def test_validation_rejects_rx38_changed_while_reading(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    generated = tmp_path / "generated.rx38"
    calculated = tmp_path / "calculated.rx38"
    write_template(generated)
    calculated.write_bytes(generated.read_bytes())
    real_read = gui_validation.read_rx38

    def changing_read(path):
        records = real_read(path)
        if Path(path).resolve() == calculated.resolve():
            calculated.write_bytes(calculated.read_bytes() + b"\r\n")
        return records

    monkeypatch.setattr(gui_validation, "read_rx38", changing_read)
    with pytest.raises(Rx3GuiValidationError, match="changed while"):
        validate_rx3_result_files(generated, calculated)


def test_safe_write_preserves_unknown_field_value():
    record = make_record(**{"135": "opaque;value"})
    updated = record.with_typed_field(1, "K2")
    assert updated.fields[135] == record.fields[135]


def test_original_template_is_unchanged(tmp_path: Path):
    template = tmp_path / "template.rx38"
    write_template(template)
    before = template.read_bytes()
    create_rx38_from_project_element(
        make_element(),
        template,
        tmp_path / "generated.rx38",
        template_mark="K1",
        safety_context=safety_context(),
    )
    assert template.read_bytes() == before
