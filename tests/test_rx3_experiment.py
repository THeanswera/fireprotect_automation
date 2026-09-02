import csv
import json
import sqlite3
from pathlib import Path

import pytest

from fireprotect.rx3.experiment import (
    Rx3ExperimentPreparationError,
    prepare_rx3_experiment_phase_a,
    rank_rx3_template_candidates,
)


def _record(mark: str, stress_state: str, field50: str = "0") -> list[str]:
    fields = [""] * 200
    values = {
        0: "Tconstr", 1: mark, 3: mark, 5: "Прямоугольная труба",
        8: "120", 9: "80", 11: "4", 13: "4", 14: "3,74", 15: "1",
        17: "ГОСТ 30245-2003", 19: "120x80x4", 20: "1495", 21: "400",
        22: "3,7375", 25: "1,496", 32: "7850", 33: "245", 34: "206000",
        42: "С245", 44: "503,4", 45: stress_state,
        48: "Один конец защемлен, другой свободен", 49: "27,85", 50: field50,
        51: "7,48", 54: "7,18", 55: "60",
        104: "Стандартный температурный режим", 141: "2",
        188: "σ 0.2% и E по данным EN 1993-1-2",
    }
    for index, value in values.items():
        fields[index] = value
    return fields


def _rx38(path: Path, records: list[list[str]]) -> None:
    with path.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.writer(stream, delimiter=";", lineterminator="\r\n")
        writer.writerow(["Trazdel", "Experiment"])
        writer.writerows(records)


def _database(path: Path) -> None:
    with sqlite3.connect(path) as connection:
        connection.execute(
            "CREATE TABLE pr_tr (N_nd TEXT, nd TEXT, h REAL, b REAL, tw REAL, tf REAL, s REAL, Ix REAL, wx REAL, Iy REAL, wy REAL)"
        )
        connection.execute(
            "INSERT INTO pr_tr VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            ("120x80x4", "ГОСТ 30245-2003", 120, 80, 4, 4, 14.95, 1, 1, 1, 1),
        )


def test_phase_a_ranks_and_builds_only_observation_artifacts(tmp_path: Path) -> None:
    database = tmp_path / "rx3.rxdb"
    _database(database)
    crowded = tmp_path / "crowded.rx38"
    simple = tmp_path / "simple.rx38"
    _rx38(crowded, [_record("К1", "Cжатый стержень"), _record("К2", "Cжатый стержень")])
    _rx38(simple, [_record("К3", "Cжатый стержень"), _record("Б1", "Изгибаемый стержень", "2")])

    ranked = rank_rx3_template_candidates([crowded, simple], database)
    assert ranked[0].path == simple.resolve()
    assert ranked[0].record.fields[1] == "К3"

    output = tmp_path / "phase_a"
    bundle = prepare_rx3_experiment_phase_a([crowded, simple], database, output)

    assert bundle.selected_source == simple.resolve()
    assert bundle.template.read_bytes() == simple.read_bytes()
    assert not (output / "generated.rx38").exists()
    evidence = json.loads(bundle.heating_evidence_template.read_text(encoding="utf-8"))
    assert evidence["status"] == "UNVERIFIED"
    assert evidence["confirmed_by"] is None
    assert evidence["heating_sides"] == 4
    summary = json.loads(bundle.template_summary_json.read_text(encoding="utf-8"))
    assert summary["controlled_project_element"]["generated_rx38_allowed"] is False
    assert summary["stale_template_result_indices"] == [44, 54]
    assert summary["expected_rx3_values"]["probable_field_50"]["value"] == "0"
    assert "Mx" not in summary["expected_rx3_values"]
    project = json.loads(bundle.project_element_draft.read_text(encoding="utf-8"))
    assert project["element_id"] == "K1"
    assert project["mark"] == "К3"
    assert project["Mx"] == {"value": "0", "unit": "kN*m"}
    assert project["heating_sides"] == 4
    assert "no semantic equivalence" in project["provenance"]["Ry"]["field"]
    checklist = bundle.checklist.read_text(encoding="utf-8")
    assert "явно видно Mx" not in checklist
    assert "RX38 mappings не выводятся" in checklist


def test_phase_a_rejects_bending_only_corpus(tmp_path: Path) -> None:
    database = tmp_path / "rx3.rxdb"
    _database(database)
    source = tmp_path / "source.rx38"
    _rx38(source, [_record("Б1", "Изгибаемый стержень", "10")])

    with pytest.raises(Rx3ExperimentPreparationError, match="No candidate"):
        prepare_rx3_experiment_phase_a([source], database, tmp_path / "phase_a")


def test_phase_a_rejects_fractional_quantity_instead_of_truncating(
    tmp_path: Path,
) -> None:
    database = tmp_path / "rx3.rxdb"
    _database(database)
    source = tmp_path / "source.rx38"
    record = _record("К1", "Cжатый стержень")
    record[15] = "1,5"
    _rx38(source, [record])

    ranked = rank_rx3_template_candidates([source], database)
    assert ranked[0].accepted is False
    assert "quantity is missing, non-positive, or non-integral" in ranked[0].reasons

    with pytest.raises(Rx3ExperimentPreparationError, match="No candidate"):
        prepare_rx3_experiment_phase_a([source], database, tmp_path / "phase_a")


def test_phase_a_never_overwrites_existing_bundle(tmp_path: Path) -> None:
    database = tmp_path / "rx3.rxdb"
    _database(database)
    source = tmp_path / "source.rx38"
    _rx38(source, [_record("К1", "Cжатый стержень")])
    output = tmp_path / "phase_a"
    output.mkdir()
    (output / "keep.txt").write_text("keep", encoding="utf-8")

    with pytest.raises(Rx3ExperimentPreparationError, match="new or empty"):
        prepare_rx3_experiment_phase_a([source], database, output)
