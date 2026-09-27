"""Focused tests for the whole-document structural and raw RX38 diff."""

from __future__ import annotations

import csv
from pathlib import Path

import pytest

from fireprotect.rx3.parser import read_rx38_document
from fireprotect.rx3.structural_diff import (
    RX38_STRUCTURAL_DIFF_KIND,
    Rx38StructuralDiffError,
    blank_line_numbers,
    diff_rx38_documents,
    record_raw_line,
)
from tests.safety_support import template_fields, write_template


def _document_with_two_records(tmp_path: Path, name: str = "document.rx38") -> Path:
    path = tmp_path / name
    write_template(path)
    with path.open("r", encoding="utf-8", newline="") as stream:
        rows = list(csv.reader(stream, delimiter=";"))
    first = next(row for row in rows if row and row[0] == "Tconstr")
    second = first.copy()
    second[1] = "K2"
    second[3] = "K2"
    rows.append(second)
    with path.open("w", encoding="utf-8", newline="") as stream:
        csv.writer(stream, delimiter=";", lineterminator="\r\n").writerows(rows)
    return path


def _write_text(path: Path, lines: list[str], encoding: str = "utf-8") -> None:
    path.write_bytes("".join(lines).encode(encoding))


def test_identical_documents_report_no_difference(tmp_path: Path):
    path = _document_with_two_records(tmp_path)
    document = read_rx38_document(path)
    report = diff_rx38_documents(document, document)
    assert report["kind"] == RX38_STRUCTURAL_DIFF_KIND
    assert report["identical"] is True
    assert report["structure_changed"] is False
    assert report["raw_layout_changed"] is False
    assert report["record_sequence"]["alignment"] == "RECORD_SEQUENCE_IDENTICAL"
    assert report["record_sequence"]["added"] == []
    assert report["record_sequence"]["removed"] == []
    assert report["record_sequence"]["replaced"] == []
    assert report["record_sequence"]["unchanged_records"] == 3


def test_an_added_record_is_reported_with_its_raw_line(tmp_path: Path):
    path = _document_with_two_records(tmp_path)
    before = read_rx38_document(path)
    lines = path.read_bytes().decode("utf-8").splitlines(keepends=True)
    _write_text(path, [*lines, "Trazdel;Second section\r\n"])
    after = read_rx38_document(path)

    report = diff_rx38_documents(before, after)
    assert report["structure_changed"] is True
    assert report["record_sequence"]["count"] == {
        "before": 3,
        "after": 4,
        "changed": True,
    }
    added = report["record_sequence"]["added"]
    assert len(added) == 1
    assert added[0]["record_type"] == "Trazdel"
    assert added[0]["position"] == 4
    assert added[0]["raw_line"] == "Trazdel;Second section\r\n"
    assert added[0]["line_number"] == 4
    assert report["record_sequence"]["removed"] == []


def test_a_raw_only_token_change_is_separated_from_a_value_change(
    tmp_path: Path,
):
    path = _document_with_two_records(tmp_path)
    before = read_rx38_document(path)
    lines = path.read_bytes().decode("utf-8").splitlines(keepends=True)
    target = next(
        index for index, line in enumerate(lines) if line.startswith("Tconstr")
    )
    tokens = lines[target][:-2].split(";")
    tokens[3] = '"K1"'
    tokens[44] = "700"
    lines[target] = ";".join(tokens) + "\r\n"
    _write_text(path, lines)
    after = read_rx38_document(path)

    report = diff_rx38_documents(before, after)
    assert report["structure_changed"] is False
    replaced = report["record_sequence"]["replaced"]
    assert len(replaced) == 1
    assert replaced[0]["raw_only_change_indices"] == [3]
    assert replaced[0]["semantic_change_indices"] == [44]
    raw_only = report["raw_layout"]["raw_only_token_changes"]
    assert len(raw_only) == 1
    assert raw_only[0]["indices"] == [3]
    assert raw_only[0]["changes"][0]["before_token"] == "K1"
    assert raw_only[0]["changes"][0]["after_token"] == '"K1"'
    assert raw_only[0]["changes"][0]["decoded_equal"] is True
    assert report["raw_layout_changed"] is True


def test_a_line_ending_change_is_a_layout_change(tmp_path: Path):
    path = _document_with_two_records(tmp_path)
    before = read_rx38_document(path)
    lines = path.read_bytes().decode("utf-8").splitlines(keepends=True)
    _write_text(path, [line.replace("\r\n", "\n") for line in lines])
    after = read_rx38_document(path)

    report = diff_rx38_documents(before, after)
    assert report["structure_changed"] is True
    changes = report["raw_layout"]["newline_changes"]
    assert len(changes) == 3
    assert {item["before"] for item in changes} == {"\r\n"}
    assert {item["after"] for item in changes} == {"\n"}
    assert all(item["changed_indices"] == [] for item in report["record_sequence"]["replaced"])


def test_an_encoding_change_is_reported(tmp_path: Path):
    source = _document_with_two_records(tmp_path)
    lines = source.read_bytes().decode("utf-8").splitlines(keepends=True)
    lines[0] = "Trazdel;Раздел 1\r\n"
    utf8_path = tmp_path / "utf8.rx38"
    cp1251_path = tmp_path / "cp1251.rx38"
    _write_text(utf8_path, lines, "utf-8")
    _write_text(cp1251_path, lines, "cp1251")
    before = read_rx38_document(utf8_path)
    after = read_rx38_document(cp1251_path)

    assert before.encoding == "utf-8"
    assert after.encoding == "cp1251"
    report = diff_rx38_documents(before, after)
    assert report["structure_changed"] is True
    assert report["raw_layout"]["encoding"] == {
        "before": "utf-8",
        "after": "cp1251",
        "changed": True,
    }


def test_a_bom_is_reported_separately(tmp_path: Path):
    path = _document_with_two_records(tmp_path)
    before = read_rx38_document(path)
    lines = path.read_bytes().decode("utf-8").splitlines(keepends=True)
    _write_text(path, ["\ufeff", *lines])
    after = read_rx38_document(path)

    report = diff_rx38_documents(before, after)
    assert report["raw_layout"]["bom"]["changed"] is True
    assert report["structure_changed"] is True
    assert after.has_bom is True
    assert report["after"]["has_bom"] is True


def test_blank_lines_are_counted_although_they_are_not_records(tmp_path: Path):
    path = _document_with_two_records(tmp_path)
    lines = path.read_bytes().decode("utf-8").splitlines(keepends=True)
    _write_text(path, [lines[0], "\r\n", *lines[1:]])
    document = read_rx38_document(path)
    assert len(document.records) == 3
    assert blank_line_numbers(document) == (2,)
    assert document.records[1].line_number == 3


def test_a_record_that_does_not_match_its_raw_line_is_refused(tmp_path: Path):
    path = _document_with_two_records(tmp_path)
    document = read_rx38_document(path)
    record = document.records[1]
    with pytest.raises(Rx38StructuralDiffError, match="cannot be reconstructed"):
        record_raw_line(
            record, ("Trazdel;Safety test\r\n", "Tconstr;fabricated\r\n")
        )
    assert record_raw_line(record, document.raw_lines) == (
        ";".join(record.raw_tokens) + record.newline
    )


def test_the_record_sequence_types_are_reported_verbatim(tmp_path: Path):
    path = _document_with_two_records(tmp_path)
    document = read_rx38_document(path)
    report = diff_rx38_documents(document, document)
    assert report["record_sequence"]["types"]["before"] == [
        "Trazdel",
        "Tconstr",
        "Tconstr",
    ]
    assert report["before"]["records"] == 3
    assert report["before"]["lines"] == 3
    assert report["before"]["trailing_newline"] is True
    assert report["before"]["has_bom"] is False
    assert report["before"]["encoding"] == "utf-8"
    assert report["before"]["raw_lines_recorded"] is True


def test_a_template_only_document_has_no_surprise_fields():
    fields = template_fields()
    assert len(fields) == 200
