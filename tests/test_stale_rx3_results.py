import csv
from hashlib import sha256
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
    construction_records,
    read_rx38,
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
from fireprotect.rx3.safety import GuiExecutionEvidence, rx38_record_fingerprint


def _pin(path: Path) -> str:
    """The independently pinned BEFORE hash of one prepared file."""

    return sha256(path.read_bytes()).hexdigest()


def _two_record_calculation(
    tmp_path: Path,
    *,
    target_changes: dict[int, str],
    non_target_changes: dict[int, str] | None = None,
) -> tuple[Path, Path]:
    generated = tmp_path / "generated.rx38"
    calculated = tmp_path / "calculated.rx38"
    write_template(generated)
    with generated.open("r", encoding="utf-8", newline="") as stream:
        rows = list(csv.reader(stream, delimiter=";"))
    first = next(row for row in rows if row and row[0] == "Tconstr")
    second = first.copy()
    second[1] = "K2"
    second[3] = "K2"
    rows.append(second)
    with generated.open("w", encoding="utf-8", newline="") as stream:
        csv.writer(stream, delimiter=";", lineterminator="\r\n").writerows(rows)
    changed_rows = [row.copy() for row in rows]
    records = [row for row in changed_rows if row and row[0] == "Tconstr"]
    for index, value in target_changes.items():
        records[0][index] = value
    for index, value in (non_target_changes or {}).items():
        records[1][index] = value
    with calculated.open("w", encoding="utf-8", newline="") as stream:
        csv.writer(stream, delimiter=";", lineterminator="\r\n").writerows(
            changed_rows
        )
    return generated, calculated


def _raw_lines(path: Path) -> list[str]:
    """Raw lines without newline translation: CRLF must stay CRLF here."""

    return path.read_bytes().decode("utf-8").splitlines(keepends=True)


def _write_raw_lines(path: Path, lines: list[str], *, encoding: str = "utf-8") -> None:
    path.write_bytes("".join(lines).encode(encoding))


def _set_raw_fields(line: str, values: dict[int, str]) -> str:
    """Rewrite raw tokens of one line without re-quoting anything else."""

    newline = "\r\n" if line.endswith("\r\n") else "\n" if line.endswith("\n") else ""
    content = line[: len(line) - len(newline)] if newline else line
    tokens = content.split(";")
    for index, value in values.items():
        tokens[index] = value
    return ";".join(tokens) + newline


def _first_tconstr_index(lines: list[str]) -> int:
    for index, line in enumerate(lines):
        if line.startswith("Tconstr"):
            return index
    raise AssertionError("the fixture has no Tconstr record")


def _identical_targets(
    tmp_path: Path, *, second_results: dict[int, str]
) -> tuple[Path, list[str]]:
    """Two target records that differ only in the allowed result fields."""

    generated = tmp_path / "generated.rx38"
    write_template(generated)
    lines = _raw_lines(generated)
    target = _first_tconstr_index(lines)
    lines.insert(target + 1, _set_raw_fields(lines[target], second_results))
    _write_raw_lines(generated, lines)
    return generated, lines


def test_an_exchanged_pair_of_targets_is_not_two_valid_result_changes(
    tmp_path: Path,
):
    """Two targets identical outside 44/54 cannot be swapped into an accepted result."""

    generated, lines = _identical_targets(tmp_path, second_results={44: "700", 54: "20"})
    calculated = tmp_path / "calculated.rx38"
    target = _first_tconstr_index(lines)
    swapped = list(lines)
    swapped[target], swapped[target + 1] = swapped[target + 1], swapped[target]
    _write_raw_lines(calculated, swapped)

    report = validate_rx3_result_files(
        generated,
        calculated,
        expected_before_sha256=_pin(generated),
        gui_execution_evidence=GuiExecutionEvidence.ENGINEER_CONFIRMED,
        evidence_reference="EXCHANGE-CONTROL",
        target_record_positions=(1, 2),
    )
    data = report.data
    assert data["status"] == "RX3_TCONSTR_IDENTITY_AMBIGUOUS"
    assert data["gui_recalculation_verified"] is False
    assert data["tconstr_identity_ambiguous_target_positions"] == [1, 2]
    assert data["tconstr_identity"]["exchanges"]
    assert "cannot be proven" in data["recalculation_note"]


def test_recalculated_exchange_of_indistinguishable_targets_is_still_refused(
    tmp_path: Path,
):
    """Even with fresh numbers the identity of each target position is unprovable."""

    generated, lines = _identical_targets(tmp_path, second_results={44: "700", 54: "20"})
    calculated = tmp_path / "calculated.rx38"
    target = _first_tconstr_index(lines)
    swapped = list(lines)
    swapped[target] = _set_raw_fields(lines[target + 1], {44: "705", 54: "21"})
    swapped[target + 1] = _set_raw_fields(lines[target], {44: "655", 54: "16"})
    _write_raw_lines(calculated, swapped)

    report = validate_rx3_result_files(
        generated,
        calculated,
        expected_before_sha256=_pin(generated),
        gui_execution_evidence=GuiExecutionEvidence.ENGINEER_CONFIRMED,
        evidence_reference="EXCHANGE-CONTROL",
        target_record_positions=(1, 2),
    )
    data = report.data
    assert data["status"] == "RX3_TCONSTR_IDENTITY_AMBIGUOUS"
    assert data["gui_recalculation_verified"] is False
    assert data["rx3_recalculation_proven"] is False
    assert data["tconstr_identity_ambiguous_target_positions"] == [1, 2]
    assert data["tconstr_identity"]["exchanges"] == []


def test_two_distinguishable_targets_recalculated_in_place_are_accepted(
    tmp_path: Path,
):
    """The allowed scenario must keep working: the records stay in place."""

    generated = tmp_path / "generated.rx38"
    write_template(generated)
    lines = _raw_lines(generated)
    target = _first_tconstr_index(lines)
    lines.insert(target + 1, _set_raw_fields(lines[target], {1: "K2", 3: "K2"}))
    _write_raw_lines(generated, lines)
    calculated = tmp_path / "calculated.rx38"
    after = list(lines)
    after[target] = _set_raw_fields(after[target], {44: "675", 54: "18"})
    after[target + 1] = _set_raw_fields(after[target + 1], {44: "680", 54: "19"})
    _write_raw_lines(calculated, after)

    report = validate_rx3_result_files(
        generated,
        calculated,
        expected_before_sha256=_pin(generated),
        gui_execution_evidence=GuiExecutionEvidence.ENGINEER_CONFIRMED,
        evidence_reference="IN-PLACE-CONTROL",
        target_record_positions=(1, 2),
    )
    data = report.data
    assert data["status"] == "RX3_RESULT_ANALYSED"
    assert data["gui_recalculation_verified"] is True
    assert data["tconstr_identity_ambiguous_target_positions"] == []
    assert data["tconstr_identity"]["duplicate_groups"] == []


def test_a_blank_line_ending_change_blocks_the_gui_check(tmp_path: Path):
    """A CRLF->LF rewrite of an empty line keeps its number and must still block."""

    generated, recalculated = _calculated_text_from(
        tmp_path, target_changes={44: "500", 54: "20"}
    )
    prepared = _raw_lines(generated)
    _write_raw_lines(generated, [prepared[0], "\r\n", *prepared[1:]])
    calculated = tmp_path / "calculated.rx38"
    _write_raw_lines(calculated, [recalculated[0], "\n", *recalculated[1:]])

    report = validate_rx3_result_files(
        generated,
        calculated,
        expected_before_sha256=_pin(generated),
        gui_execution_evidence=GuiExecutionEvidence.ENGINEER_CONFIRMED,
        evidence_reference="LINE-ENDING-CONTROL",
        target_record_positions=(1,),
    )
    data = report.data
    assert data["status"] == "RX3_DOCUMENT_STRUCTURE_CHANGED"
    assert data["gui_recalculation_verified"] is False
    assert data["structure_changed"] is True
    endings = data["structural_diff"]["raw_layout"]["line_endings"]
    assert endings["changed"] is True
    assert endings["changed_lines"] == [
        {
            "line": 2,
            "before": "CRLF",
            "after": "LF",
            "raw_before": "\r\n",
            "raw_after": "\n",
            "blank_line": True,
        }
    ]
    assert data["structural_diff"]["raw_layout"]["blank_lines"]["changed"] is False


def _calculated_text_from(
    tmp_path: Path, *, target_changes: dict[int, str]
) -> tuple[Path, list[str]]:
    """A prepared file plus the raw text of the same file after recalculation."""

    generated = tmp_path / "generated.rx38"
    _two_record_calculation(tmp_path, target_changes=target_changes)
    lines = _raw_lines(generated)
    target_index = _first_tconstr_index(lines)
    lines[target_index] = _set_raw_fields(lines[target_index], target_changes)
    return generated, lines


def test_an_added_record_blocks_the_gui_check(tmp_path: Path):
    generated, lines = _calculated_text_from(
        tmp_path, target_changes={44: "500", 54: "20"}
    )
    calculated = tmp_path / "calculated.rx38"
    _write_raw_lines(calculated, [*lines, "Trazdel;Добавлено\r\n"])

    report = validate_rx3_result_files(
        generated,
        calculated,
        expected_before_sha256=_pin(generated),
        gui_execution_evidence=GuiExecutionEvidence.ENGINEER_CONFIRMED,
        evidence_reference="STRUCTURE-CONTROL",
        target_record_positions=(1,),
    )
    data = report.data
    assert data["status"] == "RX3_DOCUMENT_STRUCTURE_CHANGED"
    assert data["gui_recalculation_verified"] is False
    assert data["structure_changed"] is True
    assert data["field_level_analysis_performed"] is False
    assert data["records"] == []
    assert data["prepared_inputs_preserved"] is None
    added = data["structural_diff"]["record_sequence"]["added"]
    assert [item["raw_line"] for item in added] == ["Trazdel;Добавлено\r\n"]
    assert "records were added" in data["recalculation_note"]


def test_a_removed_record_blocks_the_gui_check(tmp_path: Path):
    generated, lines = _calculated_text_from(
        tmp_path, target_changes={44: "500", 54: "20"}
    )
    calculated = tmp_path / "calculated.rx38"
    _write_raw_lines(calculated, [line for line in lines if not line.startswith("Trazdel")])

    report = validate_rx3_result_files(
        generated,
        calculated,
        expected_before_sha256=_pin(generated),
        gui_execution_evidence=GuiExecutionEvidence.ENGINEER_CONFIRMED,
        evidence_reference="STRUCTURE-CONTROL",
        target_record_positions=(1,),
    )
    assert report.data["status"] == "RX3_DOCUMENT_STRUCTURE_CHANGED"
    removed = report.data["structural_diff"]["record_sequence"]["removed"]
    assert [item["record_type"] for item in removed] == ["Trazdel"]
    assert removed[0]["raw_line"] == "Trazdel;Safety test\r\n"


def test_a_changed_non_tconstr_record_blocks_the_gui_check(tmp_path: Path):
    generated, lines = _calculated_text_from(
        tmp_path, target_changes={44: "500", 54: "20"}
    )
    calculated = tmp_path / "calculated.rx38"
    _write_raw_lines(
        calculated,
        [
            "Trazdel;Safety test changed\r\n" if line.startswith("Trazdel") else line
            for line in lines
        ],
    )

    report = validate_rx3_result_files(
        generated,
        calculated,
        expected_before_sha256=_pin(generated),
        gui_execution_evidence=GuiExecutionEvidence.ENGINEER_CONFIRMED,
        evidence_reference="STRUCTURE-CONTROL",
        target_record_positions=(1,),
    )
    data = report.data
    assert data["status"] == "RX3_DOCUMENT_STRUCTURE_CHANGED"
    changed = data["structural_diff"]["record_sequence"]["non_tconstr_replacements"]
    assert len(changed) == 1
    assert changed[0]["raw_line_before"] == "Trazdel;Safety test\r\n"
    assert changed[0]["raw_line_after"] == "Trazdel;Safety test changed\r\n"
    assert changed[0]["record_type_changed"] is False


def test_a_bom_change_blocks_the_gui_check(tmp_path: Path):
    generated, lines = _calculated_text_from(
        tmp_path, target_changes={44: "500", 54: "20"}
    )
    calculated = tmp_path / "calculated.rx38"
    calculated.write_bytes(("\ufeff" + "".join(lines)).encode("utf-8"))

    report = validate_rx3_result_files(
        generated,
        calculated,
        expected_before_sha256=_pin(generated),
        gui_execution_evidence=GuiExecutionEvidence.ENGINEER_CONFIRMED,
        evidence_reference="STRUCTURE-CONTROL",
        target_record_positions=(1,),
    )
    data = report.data
    assert data["status"] == "RX3_DOCUMENT_STRUCTURE_CHANGED"
    assert data["structural_diff"]["raw_layout"]["bom"] == {
        "before": False,
        "after": True,
        "changed": True,
    }


def test_a_line_ending_change_blocks_the_gui_check(tmp_path: Path):
    generated, lines = _calculated_text_from(
        tmp_path, target_changes={44: "500", 54: "20"}
    )
    calculated = tmp_path / "calculated.rx38"
    _write_raw_lines(
        calculated, [line.replace("\r\n", "\n") for line in lines]
    )

    report = validate_rx3_result_files(
        generated,
        calculated,
        expected_before_sha256=_pin(generated),
        gui_execution_evidence=GuiExecutionEvidence.ENGINEER_CONFIRMED,
        evidence_reference="STRUCTURE-CONTROL",
        target_record_positions=(1,),
    )
    data = report.data
    assert data["status"] == "RX3_DOCUMENT_STRUCTURE_CHANGED"
    changes = data["structural_diff"]["raw_layout"]["newline_changes"]
    assert changes and all(item["after"] == "\n" for item in changes)


def test_an_added_blank_line_blocks_the_gui_check(tmp_path: Path):
    generated, lines = _calculated_text_from(
        tmp_path, target_changes={44: "500", 54: "20"}
    )
    calculated = tmp_path / "calculated.rx38"
    _write_raw_lines(calculated, [*lines, "\r\n"])

    report = validate_rx3_result_files(
        generated,
        calculated,
        expected_before_sha256=_pin(generated),
        gui_execution_evidence=GuiExecutionEvidence.ENGINEER_CONFIRMED,
        evidence_reference="STRUCTURE-CONTROL",
        target_record_positions=(1,),
    )
    data = report.data
    assert data["status"] == "RX3_DOCUMENT_STRUCTURE_CHANGED"
    assert data["structural_diff"]["raw_layout"]["blank_lines"]["changed"] is True


def test_a_quote_only_change_outside_allowed_fields_blocks_the_gui_check(
    tmp_path: Path,
):
    """Field 3 is a confirmed text copy: quoting it changes no value but the bytes."""

    generated, lines = _calculated_text_from(
        tmp_path, target_changes={44: "500", 54: "20"}
    )
    calculated = tmp_path / "calculated.rx38"
    target_index = _first_tconstr_index(lines)
    lines[target_index] = _set_raw_fields(lines[target_index], {3: '"K1"'})
    _write_raw_lines(calculated, lines)

    report = validate_rx3_result_files(
        generated,
        calculated,
        expected_before_sha256=_pin(generated),
        gui_execution_evidence=GuiExecutionEvidence.ENGINEER_CONFIRMED,
        evidence_reference="STRUCTURE-CONTROL",
        target_record_positions=(1,),
    )
    data = report.data
    assert data["status"] == "RX3_RAW_FORMATTING_CHANGED"
    assert data["gui_recalculation_verified"] is False
    assert data["structure_changed"] is False
    assert data["field_level_analysis_performed"] is True
    assert data["raw_formatting_preserved"] is False
    assert data["target_raw_formatting_change_indices"] == [3]
    assert data["target_input_change_indices"] == []
    changes = data["structural_diff"]["raw_layout"]["raw_only_token_changes"]
    assert changes[0]["changes"][0]["before_token"] == "K1"
    assert changes[0]["changes"][0]["after_token"] == '"K1"'
    assert changes[0]["changes"][0]["decoded_equal"] is True


def test_a_quote_only_change_inside_allowed_fields_is_reported_but_allowed(
    tmp_path: Path,
):
    """Field 52 is an allowed calculated/service field; quoting it changes no value."""

    generated, lines = _calculated_text_from(
        tmp_path, target_changes={44: "500", 54: "20"}
    )
    calculated = tmp_path / "calculated.rx38"
    target_index = _first_tconstr_index(lines)
    lines[target_index] = _set_raw_fields(lines[target_index], {52: '"0,2"'})
    _write_raw_lines(calculated, lines)

    report = validate_rx3_result_files(
        generated,
        calculated,
        expected_before_sha256=_pin(generated),
        gui_execution_evidence=GuiExecutionEvidence.ENGINEER_CONFIRMED,
        evidence_reference="STRUCTURE-CONTROL",
        target_record_positions=(1,),
    )
    data = report.data
    assert data["status"] == "RX3_RESULT_ANALYSED"
    assert data["gui_recalculation_verified"] is True
    assert data["target_raw_formatting_change_indices"] == []
    assert data["allowed_raw_formatting_change_indices"] == [52]
    assert data["raw_formatting_preserved"] is True


def test_a_quote_only_change_on_a_non_target_record_blocks_the_gui_check(
    tmp_path: Path,
):
    generated, calculated = _two_record_calculation(
        tmp_path, target_changes={44: "500", 54: "20"}
    )
    lines = _raw_lines(calculated)
    tconstr_lines = [
        index for index, line in enumerate(lines, 1) if line.startswith("Tconstr")
    ]
    target_line = tconstr_lines[1]
    lines[target_line - 1] = _set_raw_fields(lines[target_line - 1], {3: '"K2"'})
    _write_raw_lines(calculated, lines)

    report = validate_rx3_result_files(
        generated,
        calculated,
        expected_before_sha256=_pin(generated),
        gui_execution_evidence=GuiExecutionEvidence.ENGINEER_CONFIRMED,
        evidence_reference="STRUCTURE-CONTROL",
        target_record_positions=(1,),
    )
    data = report.data
    assert data["status"] == "RX3_UNEXPECTED_NON_TARGET_CHANGE"
    assert data["gui_recalculation_verified"] is False
    assert data["non_target_records_text_unchanged"] is False
    entry = data["unexpected_non_target_changes"][0]
    assert entry["position"] == 2
    assert entry["kinds"] == ["RAW_TOKEN_ONLY_CHANGE"]
    assert entry["raw_only_token_changes"][0]["after_token"] == '"K2"'


def test_structural_report_keeps_raw_tokens_and_the_alignment_status(
    tmp_path: Path,
):
    generated, calculated = _two_record_calculation(
        tmp_path, target_changes={44: "500", 54: "20"}
    )
    report = validate_rx3_result_files(
        generated,
        calculated,
        expected_before_sha256=_pin(generated),
        gui_execution_evidence=GuiExecutionEvidence.ENGINEER_CONFIRMED,
        evidence_reference="STRUCTURE-CONTROL",
        target_record_positions=(1,),
    )
    data = report.data
    structural = data["structural_diff"]
    assert data["status"] == "RX3_RESULT_ANALYSED"
    assert structural["record_sequence"]["alignment"] == (
        "RECORD_SEQUENCE_IDENTICAL"
    )
    assert structural["structure_changed"] is False
    replaced = structural["record_sequence"]["replaced"]
    assert len(replaced) == 1
    assert replaced[0]["changed_indices"] == [44, 54]
    assert replaced[0]["semantic_change_indices"] == [44, 54]
    assert replaced[0]["raw_line_before"].startswith("Tconstr;K1;")
    assert replaced[0]["raw_line_after"].startswith("Tconstr;K1;")
    assert structural["record_sequence"]["added"] == []
    assert structural["record_sequence"]["removed"] == []
    assert structural["raw_layout"]["encoding"]["changed"] is False
    assert structural["raw_layout"]["bom"]["changed"] is False
    assert structural["raw_layout"]["blank_lines"]["changed"] is False


def test_the_structural_diff_refuses_a_document_it_cannot_reconstruct(
    tmp_path: Path,
):
    from fireprotect.rx3.structural_diff import (
        Rx38StructuralDiffError,
        diff_rx38_documents,
    )

    path = tmp_path / "document.rx38"
    write_template(path)
    document = read_rx38_document(path)
    tampered = Rx38Document(
        document.records,
        document.encoding,
        document.has_bom,
        document.source,
        ("not the line the reader saw\r\n",) + document.raw_lines[1:],
    )
    with pytest.raises(Rx38StructuralDiffError, match="cannot be reconstructed"):
        diff_rx38_documents(document, tampered)


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


def test_unchanged_numbers_cannot_separate_pressed_from_not_pressed(tmp_path: Path):
    generated, calculated = _two_record_calculation(tmp_path, target_changes={})
    report = validate_rx3_result_files(
        generated, calculated, expected_before_sha256=_pin(generated), target_record_positions=(1,)
    )
    assert report.data["status"] == "RX3_RECALCULATION_NOT_PROVEN"
    assert report.data["rx3_recalculation_proven"] is False
    assert "cannot separate" in report.data["recalculation_note"]
    assert report.data["target_input_change_indices"] == []


def test_target_input_change_is_reported_separately_from_result_fields(tmp_path: Path):
    generated, calculated = _two_record_calculation(
        tmp_path, target_changes={50: "3,5"}
    )
    report = validate_rx3_result_files(
        generated, calculated, expected_before_sha256=_pin(generated), target_record_positions=(1,)
    )
    assert report.data["target_input_change_indices"] == [50]
    assert report.data["rx3_recalculation_proven"] is False


def test_material_result_change_states_that_recalculation_is_proven(tmp_path: Path):
    generated, calculated = _two_record_calculation(
        tmp_path, target_changes={44: "500", 54: "20"}
    )
    report = validate_rx3_result_files(
        generated, calculated, expected_before_sha256=_pin(generated), target_record_positions=(1,)
    )
    assert report.data["rx3_recalculation_proven"] is True
    assert report.data["prepared_inputs_preserved"] is True
    assert "proves neither that the Calculate button was pressed" in report.data[
        "recalculation_note"
    ]
    assert report.data["target_input_change_indices"] == []


def test_changed_target_input_never_confirms_the_prepared_calculation(tmp_path: Path):
    """The defect: result fields changed, GUI reference given, but field 50 was rewritten."""

    generated, calculated = _two_record_calculation(
        tmp_path, target_changes={44: "500", 54: "20", 50: "9,99"}
    )
    report = validate_rx3_result_files(
        generated,
        calculated,
        expected_before_sha256=_pin(generated),
        target_record_positions=(1,),
        gui_execution_evidence=GuiExecutionEvidence.SCREENSHOT_REFERENCED,
        evidence_reference="LIRA-RX3-25K1-TECH-01",
    )
    assert report.data["status"] == "RX3_TARGET_INPUTS_CHANGED"
    assert report.data["gui_recalculation_verified"] is False
    assert report.data["rx3_recalculation_proven"] is False
    assert report.data["prepared_inputs_preserved"] is False
    assert report.data["target_input_change_indices"] == [50]
    assert "does not confirm the prepared calculation" in report.data["recalculation_note"]
    target = next(item for item in report.data["records"] if item["is_target"])
    assert any(change["index"] == 50 for change in target["confirmed_changes"])


def test_result_from_another_generated_file_is_refused(tmp_path: Path):
    generated, calculated = _two_record_calculation(
        tmp_path, target_changes={44: "500", 54: "20"}
    )
    with pytest.raises(Rx3GuiValidationError, match="not bound to the prepared input"):
        validate_rx3_result_files(
            generated,
            calculated,
            expected_before_sha256="0" * 64,
            target_record_positions=(1,),
        )
    # The AFTER hash is never accepted in place of the pinned BEFORE hash.
    with pytest.raises(Rx3GuiValidationError, match="not bound to the prepared input"):
        validate_rx3_result_files(
            generated,
            calculated,
            expected_before_sha256=_pin(calculated),
            target_record_positions=(1,),
        )


def test_the_pinned_before_hash_is_mandatory_and_checked(tmp_path: Path):
    generated, calculated = _two_record_calculation(
        tmp_path, target_changes={44: "500", 54: "20"}
    )
    with pytest.raises(TypeError, match="expected_before_sha256"):
        validate_rx3_result_files(  # type: ignore[call-arg]
            generated, calculated, target_record_positions=(1,)
        )
    for token, reason in (
        ("", "blank"),
        ("   ", "blank"),
        (_pin(generated).upper()[:63], "hexadecimal"),
        (_pin(generated) + "0", "hexadecimal"),
        ("z" * 64, "hexadecimal"),
    ):
        with pytest.raises(Rx3GuiValidationError, match=reason):
            validate_rx3_result_files(
                generated,
                calculated,
                expected_before_sha256=token,
                target_record_positions=(1,),
            )
    with pytest.raises(TypeError, match="must be str"):
        validate_rx3_result_files(
            generated,
            calculated,
            expected_before_sha256=12345,  # type: ignore[arg-type]
            target_record_positions=(1,),
        )
    # The pinned value is recorded verbatim next to the actual hash.
    report = validate_rx3_result_files(
        generated,
        calculated,
        expected_before_sha256=f"  {_pin(generated).upper()}  ",
        target_record_positions=(1,),
    )
    assert report.data["expected_before_sha256"] == f"  {_pin(generated).upper()}  "
    assert report.data["expected_before_sha256_normalized"] == _pin(generated)
    assert report.data["actual_before_sha256"] == _pin(generated)
    assert "not proof of the provenance of the AFTER file" in report.data[
        "prepared_input_binding_note"
    ]


def test_scoped_calculated_and_service_changes_are_not_input_changes(tmp_path: Path):
    """Fields 44/52/54/76/78 may change after a calculation without being inputs."""

    generated, calculated = _two_record_calculation(
        tmp_path,
        target_changes={44: "480", 52: "0,3", 54: "18", 76: "120,5", 78: "2,5"},
    )
    report = validate_rx3_result_files(
        generated,
        calculated,
        expected_before_sha256=_pin(generated),
        target_record_positions=(1,),
        gui_execution_evidence=GuiExecutionEvidence.SCREENSHOT_REFERENCED,
        evidence_reference="SCOPED-POST-CALC",
    )
    assert report.data["input_field_change_indices"] == []
    assert report.data["prepared_inputs_preserved"] is True
    assert report.data["calculated_service_change_indices"] == [52, 76, 78]
    assert report.data["target_unexpected_change_indices"] == []
    assert report.data["status"] == "RX3_RESULT_ANALYSED"
    assert report.data["gui_recalculation_verified"] is True


def test_field50_change_is_still_refused_and_field49_too(tmp_path: Path):
    for index, token in ((50, "9,99"), (49, "1234,5")):
        case = tmp_path / f"field{index}"
        case.mkdir()
        generated, calculated = _two_record_calculation(
            case, target_changes={44: "500", 54: "20", index: token}
        )
        report = validate_rx3_result_files(
            generated,
            calculated,
            expected_before_sha256=_pin(generated),
            target_record_positions=(1,),
            gui_execution_evidence=GuiExecutionEvidence.SCREENSHOT_REFERENCED,
            evidence_reference="CONTROL-INPUT",
        )
        assert report.data["status"] == "RX3_TARGET_INPUTS_CHANGED"
        assert report.data["input_field_change_indices"] == [index]
        assert report.data["gui_recalculation_verified"] is False


def test_byte_identical_calculated_file_does_not_prove_gui_run(tmp_path: Path):
    generated = tmp_path / "generated.rx38"
    calculated = tmp_path / "calculated.rx38"
    write_template(generated)
    calculated.write_bytes(generated.read_bytes())
    report = validate_rx3_result_files(
        generated, calculated, expected_before_sha256=_pin(generated), target_record_positions=(1,)
    )
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

    report = validate_rx3_result_files(
        generated, calculated, expected_before_sha256=_pin(generated), target_record_positions=(1,)
    )
    assert report.data["byte_identical"] is False
    assert report.data["expected_result_fields_changed"] is False
    assert report.data["status"] == "RX3_UNEXPECTED_FIELD_CHANGE"
    assert report.data["prepared_inputs_preserved"] is True
    assert report.data["target_unexpected_change_indices"] == [2]


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
        expected_before_sha256=_pin(generated),
        gui_execution_evidence=GuiExecutionEvidence.ENGINEER_CONFIRMED,
        evidence_reference="controlled evidence",
        target_record_positions=(1,),
    )
    assert report.data["rx3_recalculation_proven"] is False
    assert report.data["status"] == "RX3_RECALCULATION_NOT_PROVEN"


def test_allowed_result_change_passes_the_program_check_without_approving_it(
    tmp_path: Path,
):
    """The program check never approves the engineering result by itself."""

    generated, calculated = _two_record_calculation(
        tmp_path, target_changes={44: "675", 54: "18"}
    )
    without_evidence = validate_rx3_result_files(
        generated,
        calculated,
        expected_before_sha256=_pin(generated),
        overwrite=True,
        target_record_positions=(1,),
    )
    assert without_evidence.data["rx3_recalculation_proven"] is True
    assert without_evidence.data["gui_recalculation_verified"] is False
    assert without_evidence.data["status"] == "RX3_GUI_RECALCULATION_UNVERIFIED"
    assert without_evidence.data["gui_execution_evidence"] == (
        GuiExecutionEvidence.NOT_PROVIDED.value
    )
    with_evidence = validate_rx3_result_files(
        generated,
        calculated,
        expected_before_sha256=_pin(generated),
        overwrite=True,
        gui_execution_evidence=GuiExecutionEvidence.ENGINEER_CONFIRMED,
        evidence_reference="engineer saw the screen",
        target_record_positions=(1,),
    )
    assert with_evidence.data["gui_recalculation_verified"] is True
    assert with_evidence.data["status"] == "RX3_RESULT_ANALYSED"
    assert with_evidence.data["evidence_reference"] == "engineer saw the screen"


def test_single_target_recalculation_accepts_unchanged_non_target(tmp_path: Path):
    generated, calculated = _two_record_calculation(
        tmp_path,
        target_changes={44: "675", 54: "18"},
    )

    report = validate_rx3_result_files(
        generated,
        calculated,
        expected_before_sha256=_pin(generated),
        target_record_positions=(1,),
        gui_execution_evidence=GuiExecutionEvidence.ENGINEER_CONFIRMED,
        evidence_reference="controlled evidence",
    )

    assert report.data["status"] == "RX3_RESULT_ANALYSED"
    assert report.data["rx3_recalculation_proven"] is True
    assert report.data["target_result_fields_changed"] == {1: True}
    assert report.data["non_target_records_text_unchanged"] is True
    assert report.data["non_target_records_semantically_unchanged"] is True
    assert report.data["records"][1]["text_changed"] is False


def test_changed_non_target_record_fails_closed(tmp_path: Path):
    generated, calculated = _two_record_calculation(
        tmp_path,
        target_changes={44: "675", 54: "18"},
        non_target_changes={44: "676"},
    )

    report = validate_rx3_result_files(
        generated,
        calculated,
        expected_before_sha256=_pin(generated),
        target_record_positions=(1,),
        gui_execution_evidence=GuiExecutionEvidence.ENGINEER_CONFIRMED,
        evidence_reference="controlled evidence",
    )

    assert report.data["status"] == "RX3_UNEXPECTED_NON_TARGET_CHANGE"
    assert report.data["rx3_recalculation_proven"] is False
    assert report.data["gui_recalculation_verified"] is False
    assert report.data["non_target_records_text_unchanged"] is False
    assert report.data["unexpected_non_target_changes"][0]["position"] == 2


def test_confirmed_numeric_token_normalization_preserves_raw_diff(tmp_path: Path):
    generated, calculated = _two_record_calculation(
        tmp_path,
        target_changes={44: "675", 49: "30", 54: "18"},
    )
    with generated.open("r", encoding="utf-8", newline="") as stream:
        rows = list(csv.reader(stream, delimiter=";"))
    first = next(row for row in rows if row and row[0] == "Tconstr")
    first[49] = "30,00"
    with generated.open("w", encoding="utf-8", newline="") as stream:
        csv.writer(stream, delimiter=";", lineterminator="\r\n").writerows(rows)

    report = validate_rx3_result_files(
        generated, calculated, expected_before_sha256=_pin(generated), target_record_positions=(1,)
    )
    change = next(
        item
        for item in report.data["records"][0]["confirmed_changes"]
        if item["index"] == 49
    )
    assert change["old_token"] == "30,00"
    assert change["new_token"] == "30"
    assert change["text_changed"] is True
    assert change["semantic_changed"] is False
    assert change["classification"] == "RX3_TOKEN_NORMALIZATION"


def test_confirmed_load_level_change_is_a_calculated_result_not_an_input(tmp_path: Path):
    generated, calculated = _two_record_calculation(
        tmp_path,
        target_changes={44: "675", 52: "0,487", 54: "18"},
    )

    report = validate_rx3_result_files(
        generated, calculated, expected_before_sha256=_pin(generated), target_record_positions=(1,)
    )

    assert report.data["allowed_calculated_result_fields"] == [44, 52, 53, 54, 76, 78]
    assert 52 not in report.data["unsafe_production_change_indices"]


def test_unknown_numeric_tokens_are_not_treated_as_semantically_equal(
    tmp_path: Path,
):
    generated, calculated = _two_record_calculation(
        tmp_path,
        target_changes={44: "675", 54: "18", 77: "0,500"},
    )
    with generated.open("r", encoding="utf-8", newline="") as stream:
        rows = list(csv.reader(stream, delimiter=";"))
    first = next(row for row in rows if row and row[0] == "Tconstr")
    first[77] = "0,5"
    with generated.open("w", encoding="utf-8", newline="") as stream:
        csv.writer(stream, delimiter=";", lineterminator="\r\n").writerows(rows)

    report = validate_rx3_result_files(
        generated, calculated, expected_before_sha256=_pin(generated), target_record_positions=(1,)
    )
    change = report.data["records"][0]["unknown_changes"][0]
    assert change["text_changed"] is True
    assert change["semantic_changed"] is None
    assert change["classification"] == "RAW_TOKEN_CHANGE_SEMANTICS_UNVERIFIED"


def test_confirmed_numeric_field_with_ambiguous_units_is_conservative(
    tmp_path: Path,
):
    generated, calculated = _two_record_calculation(
        tmp_path,
        target_changes={35: "1", 44: "675", 54: "18"},
    )
    with generated.open("r", encoding="utf-8", newline="") as stream:
        rows = list(csv.reader(stream, delimiter=";"))
    first = next(row for row in rows if row and row[0] == "Tconstr")
    first[35] = "1,0"
    with generated.open("w", encoding="utf-8", newline="") as stream:
        csv.writer(stream, delimiter=";", lineterminator="\r\n").writerows(rows)

    report = validate_rx3_result_files(
        generated, calculated, expected_before_sha256=_pin(generated), target_record_positions=(1,)
    )
    change = next(
        item
        for item in report.data["records"][0]["confirmed_changes"]
        if item["index"] == 35
    )
    assert change["semantic_changed"] is None
    assert change["classification"] == (
        "CONFIRMED_NUMERIC_EQUIVALENCE_NOT_APPLICABLE"
    )


def test_validation_requires_an_explicit_target(tmp_path: Path):
    generated = tmp_path / "generated.rx38"
    calculated = tmp_path / "calculated.rx38"
    write_template(generated)
    calculated.write_bytes(generated.read_bytes())

    with pytest.raises(Rx3GuiValidationError, match="explicit target"):
        validate_rx3_result_files(
            generated,
            calculated,
            expected_before_sha256=_pin(generated),
        )


def test_target_mark_must_resolve_uniquely(tmp_path: Path):
    generated, calculated = _two_record_calculation(
        tmp_path,
        target_changes={44: "675", 54: "18"},
    )
    with generated.open("r", encoding="utf-8", newline="") as stream:
        rows = list(csv.reader(stream, delimiter=";"))
    records = [row for row in rows if row and row[0] == "Tconstr"]
    records[1][1] = records[0][1]
    records[1][3] = records[0][3]
    with generated.open("w", encoding="utf-8", newline="") as stream:
        csv.writer(stream, delimiter=";", lineterminator="\r\n").writerows(rows)

    with pytest.raises(Rx3GuiValidationError, match="exactly one"):
        validate_rx3_result_files(
        generated,
        calculated,
        expected_before_sha256=_pin(generated),
        target_marks=("K1",),
    )


def test_exact_before_fingerprint_resolves_target(tmp_path: Path):
    generated, calculated = _two_record_calculation(
        tmp_path,
        target_changes={44: "675", 54: "18"},
    )
    target = construction_records(read_rx38(generated))[0]

    report = validate_rx3_result_files(
        generated,
        calculated,
        expected_before_sha256=_pin(generated),
        target_record_fingerprints=(rx38_record_fingerprint(target),),
    )

    assert report.data["target_resolution"]["strategy"] == (
        "BEFORE_RECORD_FINGERPRINT"
    )
    assert report.data["target_resolution"]["resolved_positions"] == [1]


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
        expected_before_sha256=_pin(generated),
        gui_execution_evidence=GuiExecutionEvidence.ENGINEER_CONFIRMED,
        evidence_reference="controlled evidence",
        mode=ExecutionMode.PRODUCTION,
        target_record_positions=(1,),
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
        expected_before_sha256=_pin(generated),
        gui_execution_evidence=GuiExecutionEvidence.SCREENSHOT_REFERENCED,
        target_record_positions=(1,),
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
            expected_before_sha256=_pin(generated),
            json_report=generated,
            overwrite=True,
            target_record_positions=(1,),
        )
    assert generated.read_bytes() == before


@pytest.mark.parametrize("index", (44, 54))
def test_result_only_fields_cannot_be_written_as_input(index: int):
    with pytest.raises(UnsafeRx38WriteError, match="RESULT_ONLY"):
        make_record().with_typed_field(
            index, "999", compatibility_verified=True
        )


@pytest.mark.parametrize("index", (50, 92))
def test_experimental_field_cannot_be_written_through_typed_api(index: int):
    with pytest.raises(UnsafeRx38WriteError, match="EXPERIMENTAL"):
        make_record().with_typed_field(index, "12,5", compatibility_verified=True)


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
    real_read = gui_validation.read_rx38_document

    def changing_read(path):
        document = real_read(path)
        if Path(path).resolve() == calculated.resolve():
            calculated.write_bytes(calculated.read_bytes() + b"\r\n")
        return document

    monkeypatch.setattr(gui_validation, "read_rx38_document", changing_read)
    with pytest.raises(Rx3GuiValidationError, match="changed while"):
        validate_rx3_result_files(
            generated,
            calculated,
            expected_before_sha256=_pin(generated),
            target_record_positions=(1,),
        )


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
