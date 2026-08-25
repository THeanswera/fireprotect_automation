import csv
from pathlib import Path

import pytest

from fireprotect.rx3.gui_validation import validate_rx3_result_files
from fireprotect.rx3.parser import UnsafeRx38WriteError
from fireprotect.rx3.project_adapter import create_rx38_from_project_element
from tests.safety_support import (
    make_element,
    make_record,
    safety_context,
    write_template,
)


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


@pytest.mark.parametrize("index", (44, 54))
def test_result_only_fields_cannot_be_written_as_input(index: int):
    with pytest.raises(UnsafeRx38WriteError, match="RESULT_ONLY"):
        make_record().with_typed_field(
            index, "999", compatibility_verified=True
        )


def test_probable_field_cannot_be_written_through_typed_api():
    with pytest.raises(UnsafeRx38WriteError, match="not confirmed"):
        make_record().with_typed_field(50, "12,5", compatibility_verified=True)


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
