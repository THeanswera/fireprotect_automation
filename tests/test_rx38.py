import csv
from dataclasses import replace
from decimal import Decimal
from pathlib import Path

import pytest

from fireprotect.rx3.diff import group_by_profile
from fireprotect.rx3.parser import (
    Rx38Construction,
    UnsafeRx38WriteError,
    construction_records,
    read_rx38,
    read_rx38_document,
    write_rx38,
)


def _construction(mark: str, length: str, axial_force: str, critical_temperature: str) -> list[str]:
    fields = [""] * 200
    values = {
        0: "Tconstr", 1: mark, 2: "0", 3: mark, 4: "1", 5: "Двутавр",
        8: "300", 9: "300", 11: "10", 13: "15", 14: length, 15: "1",
        17: "СТО TEST 1-2026", 19: "30 К1", 20: "10000", 21: "2000",
        22: "5", 23: "200", 24: str(Decimal(length.replace(",", ".")) * 2).replace(".", ","),
        25: str(Decimal(length.replace(",", ".")) * 2).replace(".", ","),
        26: "0,0001", 27: "0,00005", 28: "0,00005", 29: "0,001", 30: "0,0005",
        32: "7850", 33: "245", 34: "206000", 35: "78", 36: "-0,048",
        37: "0", 38: "310", 39: "0,48", 40: "0", 41: "0,8", 42: "С245",
        44: critical_temperature, 45: "Cжатый стержень", 48: "Шарнирное опирание по концам",
        49: axial_force, 51: str(Decimal(length.replace(",", ".")) * Decimal("0.7")).replace(".", ","),
        52: "0,2", 54: "15", 55: "60", 66: "259,05", 67: "259,05", 72: "Нет",
        82: "25", 83: "1", 84: "1", 85: "1", 86: "1200", 87: "3,96", 88: "3,96",
        104: "Стандартный температурный режим", 113: "8,33333333333333", 114: "120",
        141: "0,7", 188: "σ 0.2% и E — тестовая модель", 189: "1",
    }
    for index, value in values.items():
        fields[index] = value
    return fields


@pytest.fixture
def rx38_file(tmp_path: Path) -> Path:
    path = tmp_path / "synthetic.rx38"
    with path.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.writer(stream, delimiter=";", quotechar='"', lineterminator="\r\n")
        writer.writerow(["Trazdel", "Тестовый раздел"])
        writer.writerow(_construction("К1", "3,3", "100", "650"))
        writer.writerow(_construction("К2", "4,35", "120", "625"))
        writer.writerow(_construction("К3", "4,79", "150", "600"))
    return path


def test_reads_constructions_without_losing_fields(rx38_file):
    records = construction_records(read_rx38(rx38_file))
    assert len(records) == 3
    assert records[0].mark == "К1"
    assert records[0].profile == "30 К1"
    assert records[0].area_mm2 == 10000
    assert records[0].perimeter_mm == 2000
    assert len(records[0].fields) == 200


def test_typed_construction_contains_only_evidenced_fields_and_raw_data(rx38_file):
    record = construction_records(read_rx38(rx38_file))[0]
    construction = Rx38Construction.from_record(record)
    assert construction.mark == "К1"
    assert construction.profile_name == "30 К1"
    assert str(construction.ptm_mm) == "5"
    assert construction.raw_fields == record.fields
    assert len(construction.raw_fields) == 200


def test_unchanged_round_trip_is_binary_identical(rx38_file, tmp_path):
    document = read_rx38_document(rx38_file)
    output = tmp_path / "unchanged.rx38"
    write_rx38(document, output)
    assert output.read_bytes() == rx38_file.read_bytes()


def test_safe_writer_rejects_unowned_compatibility_claim(rx38_file, tmp_path):
    document = read_rx38_document(rx38_file)
    record_index = next(i for i, record in enumerate(document.records) if record.record_type == "Tconstr")
    original = document.records[record_index]
    with pytest.raises(UnsafeRx38WriteError, match="boolean claim"):
        original.with_typed_field(14, "6,25", compatibility_verified=True)

    fields = list(original.fields)
    fields[14] = "6,25"
    changed = replace(original, fields=tuple(fields))
    with pytest.raises(UnsafeRx38WriteError, match="14:length_m"):
        write_rx38(
            document.replace_record(record_index, changed),
            tmp_path / "changed.rx38",
        )


def test_writer_preserves_original_quotes_and_quotes_new_text(rx38_file, tmp_path):
    document = read_rx38_document(rx38_file)
    record_index = next(i for i, record in enumerate(document.records) if record.record_type == "Tconstr")
    changed = document.records[record_index].with_confirmed_field(1, 'К 1 "тест"')
    output = tmp_path / "quoted.rx38"
    write_rx38(document.replace_record(record_index, changed), output)
    reparsed = read_rx38_document(output).records[record_index]
    assert reparsed.fields[1] == 'К 1 "тест"'
    assert reparsed.raw_tokens[1] == '"К 1 ""тест"""'


def test_writer_rejects_change_to_unknown_field(rx38_file, tmp_path):
    document = read_rx38_document(rx38_file)
    record_index = next(i for i, record in enumerate(document.records) if record.record_type == "Tconstr")
    original = document.records[record_index]
    fields = list(original.fields)
    fields[135] = "unsafe"
    unsafe = replace(original, fields=tuple(fields))
    with pytest.raises(UnsafeRx38WriteError, match="135:unknown_135"):
        write_rx38(document.replace_record(record_index, unsafe), tmp_path / "unsafe.rx38")


def test_group_by_profile_reports_varying_length_and_loads(rx38_file):
    groups = group_by_profile([rx38_file])
    group = next(item for item in groups if item.profile == "30 К1")
    assert len(group.records) == 3
    assert 14 in group.varying_fields
    assert 49 in group.varying_fields
    assert 51 in group.varying_fields
