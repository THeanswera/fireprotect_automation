import csv
from hashlib import sha256
import json
import sqlite3
from pathlib import Path

import pytest

import fireprotect.rx3.experiment as rx3_experiment
from fireprotect.execution import ExecutionMode
from fireprotect.rx3.experiment import (
    Rx3MyBiaxialPhaseABundle,
    Rx3ExperimentPreparationError,
    load_bending_report_references,
    prepare_rx3_bending_phase_a,
    prepare_rx3_bending_mx10_validation,
    prepare_rx3_bending_q3_validation,
    prepare_rx3_experiment_phase_a,
    prepare_rx3_my5_validation,
    prepare_rx3_my_biaxial_phase_a,
    rank_rx3_bending_template_candidates,
    rank_rx3_template_candidates,
    validate_rx3_bending_q3_result,
    validate_rx3_my5_result,
)
from fireprotect.rx3.diff import diff_records
from fireprotect.rx3.parser import (
    UnsafeRx38WriteError,
    construction_records,
    read_rx38,
)
from fireprotect.rx3.safety import rx38_record_fingerprint
from fireprotect.rx3.schema import WritePolicy, field_spec


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


def _bending_record(mark: str, mx: str, q: str) -> list[str]:
    record = _record(
        mark,
        "Изгибаемый стержень в одной из главных плоскостей",
        mx,
    )
    record[48] = ""
    record[49] = "0"
    record[61] = "отн. Х-X"
    record[78] = mx
    record[92] = q
    return record


def _biaxial_record(mark: str, mx: str, my: str, q: str = "0") -> list[str]:
    record = _bending_record(mark, "0", q)
    record[45] = "Изгибаемый стержень в двух главных плоскостях"
    record[19] = "20П"
    record[50] = "0"
    record[78] = mx
    record[79] = my
    record[92] = q
    record[122] = "0,5"
    return record


def _my_phase_a_and_observation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    baseline_my: str = "4,3414",
) -> tuple[Rx3MyBiaxialPhaseABundle, Path]:
    source = tmp_path / "corpus.rx38"
    target = _biaxial_record("Кс1", "0.507", baseline_my)
    target[14] = "3,87"
    target[17] = "ГОСТ 8240-97"
    target[42] = "С245"
    target[53] = "0"
    target[55] = "60"
    target[61] = "отн. X-X"
    target[104] = "Стандартный температурный режим"
    records = [
        _bending_record(f"Б{index}", str(index), str(index))
        for index in range(1, 7)
    ] + [target]
    _rx38(source, records)
    phase_a = prepare_rx3_my_biaxial_phase_a(
        [source],
        tmp_path / "phase_a",
        reference_my_knm=baseline_my,
    )
    observation = tmp_path / "phase_a_gui_observation.json"
    observation.write_text(
        json.dumps(
            {
                "experiment_id": "RX3-EXP-04",
                "phase": "A_MY_BIAXIAL_GUI_OBSERVATION",
                "result": "PASS",
                "calculation_pressed": False,
                "stale_template_results_are_new_evidence": False,
                "template": {
                    "sha256": phase_a.source_sha256,
                    "record_fingerprint": phase_a.target_fingerprint,
                    "position_1_based": phase_a.target_position,
                },
                "selection": {
                    "mark": "Кс1",
                    "profile": "20П",
                    "profile_standard": "ГОСТ 8240-97",
                    "steel": "C245",
                    "length_m": "3.87",
                    "stress_state": (
                        "Изгибаемый стержень в двух главных плоскостях"
                    ),
                    "axis": "отн. X-X",
                },
                "actions": {
                    "Mx_knm": "0.51",
                    "My_knm": "4.34",
                    "plastic_region_enabled": True,
                    "en_classification_enabled": False,
                },
                "heating": {
                    "heating_sides": 3,
                    "active_sides": ["LEFT", "RIGHT", "BOTTOM"],
                    "inactive_sides": ["TOP"],
                    "rx38_indices_mapped": False,
                },
                "fire": {
                    "required_R_min": "60",
                    "regime": "Стандартный температурный режим",
                    "epsilon0": "0.800",
                    "epsilon": "1.000",
                    "Phi": "1.000",
                    "alpha_c": "25.00",
                    "kf": "1.000",
                },
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(
        rx3_experiment, "RX3_EXP_04_SOURCE_SHA256", phase_a.source_sha256
    )
    monkeypatch.setattr(
        rx3_experiment,
        "RX3_EXP_04_TARGET_FINGERPRINT",
        phase_a.target_fingerprint,
    )
    return phase_a, observation


def _my5_postcalc_fixture(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> tuple[object, Path, Path]:
    phase_a, phase_a_observation = _my_phase_a_and_observation(
        tmp_path, monkeypatch
    )
    bundle = prepare_rx3_my5_validation(
        phase_a.directory,
        phase_a_observation,
        tmp_path / "my5",
    )
    generated_records = [
        list(record.fields)
        for record in construction_records(read_rx38(bundle.generated))
    ]
    generated_records[6][44] = "505,26276072886"
    generated_records[6][52] = "0,517895650323622"
    generated_records[6][54] = "7,28333333333333"
    generated_records[6][76] = "126,884434329287"
    generated_records[6][78] = "0,51"
    generated_records[6][79] = "5"
    calculated = bundle.directory / "calculated_MY5.rx38"
    _rx38(calculated, generated_records)
    monkeypatch.setattr(
        rx3_experiment, "RX3_EXP_04B_GENERATED_SHA256", bundle.generated_sha256
    )
    generated_target = construction_records(read_rx38(bundle.generated))[6]
    monkeypatch.setattr(
        rx3_experiment,
        "RX3_EXP_04B_GENERATED_FINGERPRINT",
        rx38_record_fingerprint(generated_target),
    )
    observation = bundle.directory / "POSTCALC_OBSERVATION.json"
    observation.write_text(
        json.dumps(
            {
                "experiment_id": "RX3-EXP-04B",
                "protocol": "CONTROLLED_MY_PERTURBATION_PRECALC",
                "result": "PASS",
                "source_sha256": phase_a.source_sha256,
                "generated_sha256": bundle.generated_sha256,
                "original_target_fingerprint": phase_a.target_fingerprint,
                "target_generated_fingerprint": rx38_record_fingerprint(
                    generated_target
                ),
                "target_position_1_based": 7,
                "selection": {
                    "mark": "Кс1",
                    "profile": "20П",
                    "profile_standard": "ГОСТ 8240-97",
                    "steel": "C245",
                    "length_m": "3.87",
                    "stress_state": (
                        "Изгибаемый стержень в двух главных плоскостях"
                    ),
                    "axis": "отн. X-X",
                    "plastic_region_enabled": True,
                    "en_classification_enabled": False,
                    "heating_sides": 3,
                    "active_sides": ["LEFT", "RIGHT", "BOTTOM"],
                    "inactive_sides": ["TOP"],
                    "required_R_min": "60",
                    "fire_regime": "Стандартный температурный режим",
                },
                "actions": {"Mx_knm": "0.51", "My_knm": "5.00"},
                "calculation": {
                    "pressed": True,
                    "governing_gamma_tem": "0.518",
                    "beta_tem": "0.000",
                    "beta_related_theta_C": "1200.00",
                    "governing_theta_cr_C": "505.26",
                    "R0_min": "7.28",
                },
                "persistence": {
                    "save_to_table": True,
                    "save_as": True,
                    "filename": "calculated_MY5.rx38",
                },
                "evidence_reference": (
                    "RX3-EXP-04B GUI screenshots reviewed 2026-09-02"
                ),
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    return bundle, calculated, observation


def _bending_references(path: Path, source: Path, *, second_q: str = "20") -> None:
    payload = {
        "evidence_reference": "test GUI/report evidence",
        "candidates": [
            {
                "source_file": str(source),
                "mark": "Б1",
                "Mx_knm": "8",
                "Q_kn": "2",
            },
            {
                "source_file": str(source),
                "mark": "Б3",
                "Mx_knm": "10",
                "Q_kn": second_q,
            },
        ],
    }
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


def _q3_previous_evidence(
    tmp_path: Path, *, duplicate_mark: bool = False
) -> Path:
    directory = tmp_path / "mx_validation"
    directory.mkdir()
    values = (
        ("Б3", "18.73", "28.49"),
        ("Б4", "2.24", "24.39"),
        ("Б5", "1.5647", "5.6898"),
        ("Б2", "9.30", "2.32"),
        ("Б1", "8.89", "2.32"),
    )
    records = [_bending_record(*item) for item in values]
    target = records[-1]
    target[5] = "Двутавр"
    target[17] = "ГОСТ 26020-83"
    target[19] = "14Б2"
    target[104] = "Стандартный температурный режим"
    if duplicate_mark:
        records[0][1] = "Б1"
        records[0][3] = "Б1"
    template = directory / "template.rx38"
    _rx38(template, records)
    template_records = construction_records(read_rx38(template))
    baseline_targets = [record for record in template_records if record.mark == "Б1"]
    baseline_target = baseline_targets[-1]
    template_sha = sha256(template.read_bytes()).hexdigest()
    fingerprint = rx38_record_fingerprint(baseline_target)

    generated_records = [list(item) for item in records]
    generated_records[-1][50] = "10,00"
    generated = directory / "generated_MX10.rx38"
    _rx38(generated, generated_records)
    generated_target = construction_records(read_rx38(generated))[-1]
    calculated_records = [list(item) for item in generated_records]
    calculated_records[-1][50] = "10"
    calculated_records[-1][52] = "0,486623451719159"
    calculated_records[-1][44] = "518,859368817757"
    calculated_records[-1][54] = "7,11666666666667"
    calculated_records[-1][76] = "119,222745671194"
    calculated_records[-1][78] = "10"
    calculated = directory / "calculated_MX10.rx38"
    _rx38(calculated, calculated_records)

    observation_path = tmp_path / "phase_a_gui_observation.json"
    observation_path.write_text(
        json.dumps(
            {
                "experiment_id": "RX3-EXP-02",
                "result": "PASS",
                "calculation_pressed": False,
                "template": {
                    "sha256": template_sha,
                    "record_fingerprint": fingerprint,
                },
                "selection": {
                    "mark": "Б1",
                    "profile": "14Б2",
                    "stress_state": baseline_target.fields[45],
                    "axis": "отн. X-X",
                },
                "actions": {"Mx_knm": "8.89", "Q_kn": "2.32"},
                "heating": {
                    "heating_sides": 3,
                    "active_sides": ["LEFT", "RIGHT", "BOTTOM"],
                    "rx38_indices_mapped": False,
                },
                "fire": {"required_R_min": "60"},
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    (directory / "audit.json").write_text(
        json.dumps(
            {
                "experiment_id": "RX3-EXP-02B",
                "execution_mode": "VALIDATION",
                "phase_a_observation": str(observation_path),
                "template": {
                    "sha256": template_sha,
                    "record_sha256": fingerprint,
                },
                "generated": {
                    "sha256": sha256(generated.read_bytes()).hexdigest(),
                    "record_sha256": rx38_record_fingerprint(generated_target),
                },
                "precalc_diff": {
                    "target_mark": "Б1",
                    "template_record_sha256": fingerprint,
                },
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    report_records = [
        {
            "position": position,
            "is_target": position == len(template_records),
            "before_mark": record.mark,
            "after_mark": record.mark,
            "confirmed_changes": (
                [
                    {
                        "index": 50,
                        "new_token": "10",
                        "semantic_changed": False,
                    }
                ]
                if position == len(template_records)
                else []
            ),
            "probable_changes": (
                [{"index": 78, "new_token": "10", "semantic_changed": None}]
                if position == len(template_records)
                else []
            ),
        }
        for position, record in enumerate(template_records, 1)
    ]
    (directory / "rx3_validation_report.json").write_text(
        json.dumps(
            {
                "status": "RX3_RESULT_ANALYSED",
                "gui_recalculation_verified": True,
                "non_target_records_text_unchanged": True,
                "evidence_reference": "RX3-EXP-02B test evidence",
                "before": {
                    "path": str(generated),
                    "sha256": sha256(generated.read_bytes()).hexdigest(),
                },
                "after": {
                    "path": str(calculated),
                    "sha256": sha256(calculated.read_bytes()).hexdigest(),
                },
                "records": report_records,
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    return directory


def _complete_q3_observation(
    bundle_directory: Path,
    *,
    beta_tem: str | None = None,
    beta_related_theta_c: str | None = None,
) -> Path:
    template = bundle_directory / "POSTCALC_OBSERVATION_TEMPLATE.json"
    payload = json.loads(template.read_text(encoding="utf-8"))
    payload["result"] = "PASS"
    payload["calculation"].update(
        {
            "pressed": True,
            "M_utilisation": "0.433",
            "Q_utilisation": "0.036",
            "governing_gamma_tem": "0.433",
            "beta_tem": beta_tem,
            "beta_related_theta_C": beta_related_theta_c,
            "governing_theta_cr_C": "542.34",
            "R0_min": "7.62",
            "displayed_stress_load_MPa": "105.99",
        }
    )
    payload["persistence"].update({"save_to_table": True, "save_as": True})
    payload["evidence_reference"] = "RX3-EXP-03 test GUI evidence"
    path = bundle_directory / "POSTCALC_OBSERVATION.json"
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    return path


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


def test_bending_phase_a_ranks_external_q_confounding_and_never_generates(
    tmp_path: Path,
) -> None:
    database = tmp_path / "rx3.rxdb"
    _database(database)
    source = tmp_path / "bending.rx38"
    _rx38(
        source,
        [
            _bending_record("Б3", "10", "20"),
            _bending_record("Б1", "8", "2"),
        ],
    )
    reference_path = tmp_path / "report_values.json"
    _bending_references(reference_path, source)
    references = load_bending_report_references(reference_path)

    ranked = rank_rx3_bending_template_candidates(
        [source], database, references
    )
    assert ranked[0].accepted is True
    assert ranked[0].record.fields[1] == "Б1"
    assert ranked[0].q_to_mx_ratio == pytest.approx(0.25)

    output = tmp_path / "bending_phase_a"
    bundle = prepare_rx3_bending_phase_a(
        [source], database, references, output
    )
    assert bundle.selected_mark == "Б1"
    assert bundle.template.read_bytes() == source.read_bytes()
    assert not any(output.glob("generated*.rx38"))
    summary = json.loads(bundle.template_summary_json.read_text(encoding="utf-8"))
    assert summary["generation_allowed"] is False
    assert summary["calculation_allowed"] is False
    assert summary["status"] == "WAITING_FOR_BENDING_TEMPLATE_GUI_OBSERVATION"
    assert "not a pure-Mx experiment" in summary["warnings"][0]
    checklist = bundle.checklist.read_text(encoding="utf-8")
    assert "Calculate/Recalculate was not pressed" in checklist
    assert "lateral-torsional buckling" in checklist


def test_bending_phase_a_rejects_mismatched_report_reference(tmp_path: Path) -> None:
    database = tmp_path / "rx3.rxdb"
    _database(database)
    source = tmp_path / "bending.rx38"
    _rx38(source, [_bending_record("Б3", "10", "20")])
    reference_path = tmp_path / "report_values.json"
    _bending_references(reference_path, source, second_q="21")
    references = load_bending_report_references(reference_path)

    with pytest.raises(Rx3ExperimentPreparationError, match="No candidate"):
        prepare_rx3_bending_phase_a(
            [source], database, references, tmp_path / "bending_phase_a"
        )


def test_bending_mx10_bundle_is_fingerprint_bound_and_changes_only_field50(
    tmp_path: Path,
) -> None:
    source = tmp_path / "bending.rx38"
    values = (
        ("Б1", "8.89", "2.32"),
        ("Б2", "9.30", "2.32"),
        ("Б3", "18.73", "28.49"),
        ("Б4", "2.24", "24.39"),
        ("Б5", "1.5647", "5.6898"),
    )
    _rx38(source, [_bending_record(*item) for item in values])
    phase_a = tmp_path / "phase_a"
    phase_a.mkdir()
    template = phase_a / "template.rx38"
    template.write_bytes(source.read_bytes())
    target = next(
        record
        for record in construction_records(read_rx38(template))
        if record.mark == "Б1"
    )
    template_sha = sha256(template.read_bytes()).hexdigest()
    fingerprint = rx38_record_fingerprint(target)
    (phase_a / "template_summary.json").write_text(
        json.dumps(
            {
                "experiment_id": "RX3-EXP-02",
                "template": {"working_copy": "template.rx38", "sha256": template_sha},
                "selection": {
                    "mark": "Б1",
                    "template_record_sha256": fingerprint,
                },
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    (phase_a / "candidate_report_values.json").write_text(
        json.dumps(
            {
                "evidence_reference": "test GUI/report evidence",
                "candidates": [
                    {
                        "source_file": str(source),
                        "mark": mark,
                        "Mx_knm": mx,
                        "Q_kn": q,
                    }
                    for mark, mx, q in values
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    observation = tmp_path / "observation.json"
    observation.write_text(
        json.dumps(
            {
                "experiment_id": "RX3-EXP-02",
                "result": "PASS",
                "calculation_pressed": False,
                "template": {
                    "sha256": template_sha,
                    "record_fingerprint": fingerprint,
                },
                "selection": {
                    "mark": "Б1",
                    "stress_state": target.fields[45],
                    "axis": "отн. X-X",
                },
                "actions": {
                    "Mx_label": "Mx",
                    "Mx_knm": "8.89",
                    "Q_label": "Q",
                    "Q_kn": "2.32",
                    "N_input_displayed": False,
                },
                "heating": {
                    "heating_sides": 3,
                    "active_sides": ["LEFT", "RIGHT", "BOTTOM"],
                    "rx38_indices_mapped": False,
                },
                "fire": {"required_R_min": "60"},
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    bundle = prepare_rx3_bending_mx10_validation(
        phase_a, observation, tmp_path / "phase_b"
    )

    before = construction_records(read_rx38(bundle.template))
    after = construction_records(read_rx38(bundle.generated))
    all_diffs = [
        (left.mark, diff_records(left, right))
        for left, right in zip(before, after)
        if diff_records(left, right)
    ]
    assert len(all_diffs) == 1
    assert all_diffs[0][0] == "Б1"
    assert [item.index for item in all_diffs[0][1]] == [50]
    generated_target = next(record for record in after if record.mark == "Б1")
    assert generated_target.fields[50] == "10,00"
    assert generated_target.raw_tokens[92] == target.raw_tokens[92]
    assert bundle.generated_sha256 == sha256(bundle.generated.read_bytes()).hexdigest()
    assert {path.name for path in bundle.directory.iterdir()} == {
        "template.rx38",
        "generated_MX10.rx38",
        "project_element_MX10.json",
        "template_profile.json",
        "heating_evidence.json",
        "precalc_diff.json",
        "precalc_diff.md",
        "EXPECTED_RX3_GUI_VALUES.md",
        "CHECKLIST_PRECALC.md",
        "README_RUN_RX3.md",
        "audit.json",
    }
    project = json.loads(bundle.project_element.read_text(encoding="utf-8"))
    assert project["Mx"] == {"value": "10.00", "unit": "kN*m"}
    assert project["Qx"] is None and project["Qy"] is None
    audit = json.loads(bundle.audit.read_text(encoding="utf-8"))
    assert audit["schema_mapping_promoted"] is True
    assert audit["production_writer_changed"] is False
    assert audit["candidate_analysis"]["B1_Mx_candidate_indices"] == [50, 78]
    assert audit["candidate_analysis"]["B1_Q_candidate_indices"] == [92]


def test_bending_q3_bundle_is_exactly_bound_and_changes_only_field92(
    tmp_path: Path,
) -> None:
    previous = _q3_previous_evidence(tmp_path)
    bundle = prepare_rx3_bending_q3_validation(previous, tmp_path / "q3")

    before = construction_records(read_rx38(bundle.template))
    after = construction_records(read_rx38(bundle.generated))
    changed = [
        (left, right, diff_records(left, right))
        for left, right in zip(before, after)
        if diff_records(left, right)
    ]
    assert len(changed) == 1
    assert changed[0][0].mark == "Б1"
    assert [item.index for item in changed[0][2]] == [92]
    assert changed[0][0].raw_tokens[50] == changed[0][1].raw_tokens[50]
    assert changed[0][0].raw_tokens[78] == changed[0][1].raw_tokens[78]
    assert changed[0][1].fields[92] == "3,00"
    assert bundle.generated_sha256 == sha256(bundle.generated.read_bytes()).hexdigest()
    assert bundle.template.read_bytes() == (previous / "template.rx38").read_bytes()

    audit = json.loads(bundle.audit.read_text(encoding="utf-8"))
    assert audit["source"]["target_position_1_based"] == 5
    assert audit["precalc_diff"]["changed_fields"][0]["index"] == 92
    assert audit["precalc_diff"]["all_other_target_fields_token_identical"] is True
    assert audit["precalc_diff"]["all_non_target_records_token_identical"] is True
    assert audit["field92_evidence_decision"] == (
        "CONFIRMED_SCOPED_RX3_GUI_Q_INPUT"
    )
    assert audit["schema_mapping_promoted"] is True
    assert audit["production_writer_changed"] is False

    project = json.loads(bundle.project_element.read_text(encoding="utf-8"))
    assert project["Mx"] == {"value": "8.89", "unit": "kN*m"}
    assert project["Qx"] is None and project["Qy"] is None
    expected = bundle.expected_gui.read_text(encoding="utf-8")
    assert "Q: `3.00 kN`" in expected
    assert "Mx: `8.89 kN*m`" in expected


def test_bending_q3_refuses_production_and_incomplete_compatibility(
    tmp_path: Path,
) -> None:
    previous = _q3_previous_evidence(tmp_path)
    with pytest.raises(Rx3ExperimentPreparationError, match="outside VALIDATION"):
        prepare_rx3_bending_q3_validation(
            previous,
            tmp_path / "production",
            mode=ExecutionMode.PRODUCTION,
        )

    audit_path = previous / "audit.json"
    audit = json.loads(audit_path.read_text(encoding="utf-8"))
    audit["template"]["sha256"] = "0" * 64
    audit_path.write_text(json.dumps(audit), encoding="utf-8")
    with pytest.raises(Rx3ExperimentPreparationError, match="SHA-256 mismatch"):
        prepare_rx3_bending_q3_validation(previous, tmp_path / "wrong_sha")


def test_bending_q3_refuses_mismatched_gui_observation_and_duplicate_mark(
    tmp_path: Path,
) -> None:
    previous = _q3_previous_evidence(tmp_path)
    audit = json.loads((previous / "audit.json").read_text(encoding="utf-8"))
    observation_path = Path(audit["phase_a_observation"])
    observation = json.loads(observation_path.read_text(encoding="utf-8"))
    observation["selection"]["profile"] = "WRONG"
    observation_path.write_text(json.dumps(observation), encoding="utf-8")
    with pytest.raises(Rx3ExperimentPreparationError, match="profile"):
        prepare_rx3_bending_q3_validation(previous, tmp_path / "wrong_gui")

    duplicate_root = tmp_path / "duplicate"
    duplicate_root.mkdir()
    duplicate_previous = _q3_previous_evidence(duplicate_root, duplicate_mark=True)
    with pytest.raises(Rx3ExperimentPreparationError, match="exactly one Tconstr"):
        prepare_rx3_bending_q3_validation(
            duplicate_previous, duplicate_root / "q3"
        )


def test_bending_q3_bundle_never_overwrites_existing_artifacts(tmp_path: Path) -> None:
    previous = _q3_previous_evidence(tmp_path)
    output = tmp_path / "q3"
    output.mkdir()
    (output / "keep.txt").write_text("keep", encoding="utf-8")
    with pytest.raises(Rx3ExperimentPreparationError, match="new or empty"):
        prepare_rx3_bending_q3_validation(previous, output)


def test_bending_q3_postcalc_validator_classifies_token_normalization(
    tmp_path: Path,
) -> None:
    previous = _q3_previous_evidence(tmp_path)
    bundle = prepare_rx3_bending_q3_validation(previous, tmp_path / "q3")
    generated_records = [
        list(record.raw_tokens)
        for record in construction_records(read_rx38(bundle.generated))
    ]
    generated_records[-1][92] = "3"
    calculated = bundle.directory / "calculated_Q3.rx38"
    _rx38(calculated, generated_records)
    observation = _complete_q3_observation(bundle.directory)

    report = validate_rx3_bending_q3_result(
        bundle.directory, calculated, observation
    )

    field92 = report.data["field92_persistence"]
    assert field92["old_token"] == "3,00"
    assert field92["new_token"] == "3"
    assert field92["semantic_changed"] is False
    assert field92["classification"] == "RX3_TOKEN_NORMALIZATION"
    assert field92["persisted_numeric_matches_Q3"] is True
    assert report.data["field_observations"]["50"]["semantic_changed"] is False
    assert report.data["field_observations"]["78"]["semantic_changed"] is False
    assert report.data["stale_result_separation"]["before_status"] == (
        "STALE_TEMPLATE_RESULT"
    )
    assert report.data["schema_mapping_promoted"] is False
    assert report.data["production_write_allowed"] is False
    assert report.data["gui_observation"]["values"]["beta_tem"] is None
    assert report.data["gui_observation"]["values"]["beta_related_theta_C"] is None


def test_bending_q3_postcalc_accepts_finite_optional_beta_values(
    tmp_path: Path,
) -> None:
    previous = _q3_previous_evidence(tmp_path)
    bundle = prepare_rx3_bending_q3_validation(previous, tmp_path / "q3")
    records = [
        list(record.raw_tokens)
        for record in construction_records(read_rx38(bundle.generated))
    ]
    records[-1][92] = "3"
    calculated = bundle.directory / "calculated_Q3.rx38"
    _rx38(calculated, records)
    observation = _complete_q3_observation(
        bundle.directory,
        beta_tem="0.000",
        beta_related_theta_c="1200",
    )

    report = validate_rx3_bending_q3_result(
        bundle.directory, calculated, observation
    )

    assert report.data["gui_observation"]["values"]["beta_tem"] == "0.000"
    assert report.data["gui_observation"]["values"]["beta_related_theta_C"] == (
        "1200"
    )


@pytest.mark.parametrize(
    ("beta_tem", "beta_related_theta_c", "field"),
    [
        ("not-a-number", None, "beta_tem"),
        (None, "Infinity", "beta_related_theta_C"),
    ],
)
def test_bending_q3_postcalc_rejects_malformed_optional_beta_values(
    tmp_path: Path,
    beta_tem: str | None,
    beta_related_theta_c: str | None,
    field: str,
) -> None:
    previous = _q3_previous_evidence(tmp_path)
    bundle = prepare_rx3_bending_q3_validation(previous, tmp_path / "q3")
    records = [
        list(record.raw_tokens)
        for record in construction_records(read_rx38(bundle.generated))
    ]
    calculated = bundle.directory / "calculated_Q3.rx38"
    _rx38(calculated, records)
    observation = _complete_q3_observation(
        bundle.directory,
        beta_tem=beta_tem,
        beta_related_theta_c=beta_related_theta_c,
    )

    with pytest.raises(Rx3ExperimentPreparationError, match=field):
        validate_rx3_bending_q3_result(bundle.directory, calculated, observation)


def test_bending_q3_postcalc_validator_fails_on_non_target_mutation(
    tmp_path: Path,
) -> None:
    previous = _q3_previous_evidence(tmp_path)
    bundle = prepare_rx3_bending_q3_validation(previous, tmp_path / "q3")
    records = [
        list(record.raw_tokens)
        for record in construction_records(read_rx38(bundle.generated))
    ]
    records[0][44] = "503,40"
    calculated = bundle.directory / "calculated_Q3.rx38"
    _rx38(calculated, records)
    observation = _complete_q3_observation(bundle.directory)

    with pytest.raises(Rx3ExperimentPreparationError, match="non-target mutation"):
        validate_rx3_bending_q3_result(bundle.directory, calculated, observation)


def test_bending_q3_postcalc_validator_refuses_wrong_gui_or_persisted_values(
    tmp_path: Path,
) -> None:
    previous = _q3_previous_evidence(tmp_path)
    bundle = prepare_rx3_bending_q3_validation(previous, tmp_path / "q3")
    records = [
        list(record.raw_tokens)
        for record in construction_records(read_rx38(bundle.generated))
    ]
    records[-1][78] = "10"
    calculated = bundle.directory / "calculated_Q3.rx38"
    _rx38(calculated, records)
    observation = _complete_q3_observation(bundle.directory)
    with pytest.raises(Rx3ExperimentPreparationError, match="persisted Mx/Q"):
        validate_rx3_bending_q3_result(bundle.directory, calculated, observation)

    records[-1][78] = "8,89"
    _rx38(calculated, records)
    payload = json.loads(observation.read_text(encoding="utf-8"))
    payload["generated_sha256"] = "0" * 64
    observation.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(Rx3ExperimentPreparationError, match="not bound"):
        validate_rx3_bending_q3_result(bundle.directory, calculated, observation)


def test_my_biaxial_phase_a_selects_exact_target_without_mutation(
    tmp_path: Path,
) -> None:
    source = tmp_path / "corpus.rx38"
    _rx38(
        source,
        [
            _bending_record("Б1", "8.89", "2.32"),
            _biaxial_record("Кс1", "0.507", "4.3414"),
            _biaxial_record("Св2.2", "1.1637", "4.3738"),
        ],
    )

    bundle = prepare_rx3_my_biaxial_phase_a([source], tmp_path / "phase_a")

    assert bundle.selected_source == source.resolve()
    assert bundle.selected_mark == "Кс1"
    assert bundle.target_position == 2
    assert bundle.template.read_bytes() == source.read_bytes()
    copied_target = construction_records(read_rx38(bundle.template))[1]
    assert rx38_record_fingerprint(copied_target) == bundle.target_fingerprint
    assert not any(bundle.directory.glob("generated*.rx38"))
    assert {path.name for path in bundle.directory.iterdir()} == {
        "template.rx38",
        "template_summary.json",
        "candidate_field_analysis.json",
        "candidate_field_analysis.md",
        "EXPECTED_RX3_GUI_VALUES.md",
        "CHECKLIST.md",
        "GUI_OBSERVATION_INSTRUCTIONS.md",
        "audit.json",
    }

    summary = json.loads(bundle.template_summary.read_text(encoding="utf-8"))
    assert summary["status"] == "WAITING_FOR_MY_BIAXIAL_GUI_SCREENSHOT"
    assert summary["generation_allowed"] is False
    assert summary["calculation_allowed"] is False
    assert summary["heating_evidence"]["status"] == "GUI_CONFIRMATION_REQUIRED"
    analysis = json.loads(
        bundle.candidate_analysis_json.read_text(encoding="utf-8")
    )
    assert analysis["semantic_assignments_made"] is False
    assert analysis["candidate_indices"]["My"] == [79]
    assert analysis["candidate_indices"]["Mx"] == [78, 122]
    assert 92 in analysis["candidate_indices"]["Q"]
    assert analysis["ranked_candidates"]["My"][0]["index"] == 79
    assert analysis["ranked_candidates"]["My"][0]["semantic_status"] == (
        "CANDIDATE_ONLY_NOT_ASSIGNED"
    )
    audit = json.loads(bundle.audit.read_text(encoding="utf-8"))
    assert audit["source"]["record_fingerprint"] == bundle.target_fingerprint
    assert audit["mutation_performed"] is False
    assert audit["generated_rx38_created"] is False
    assert audit["schema_mapping_promoted"] is False
    assert audit["production_writer_changed"] is False


def test_my_biaxial_phase_a_rejects_ambiguous_exact_mark(tmp_path: Path) -> None:
    first = tmp_path / "first.rx38"
    second = tmp_path / "second.rx38"
    _rx38(first, [_biaxial_record("Кс1", "0.507", "4.3414")])
    _rx38(second, [_biaxial_record("Кс1", "0.507", "4.3414")])

    with pytest.raises(Rx3ExperimentPreparationError, match="resolve to one"):
        prepare_rx3_my_biaxial_phase_a(
            [first, second], tmp_path / "ambiguous_phase_a"
        )


def test_my5_validation_changes_only_field79_and_preserves_protected_fields(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    phase_a, observation = _my_phase_a_and_observation(
        tmp_path, monkeypatch
    )

    bundle = prepare_rx3_my5_validation(
        phase_a.directory,
        observation,
        tmp_path / "my5",
    )

    before_records = construction_records(read_rx38(bundle.template))
    after_records = construction_records(read_rx38(bundle.generated))
    assert len(before_records) == len(after_records) == 7
    for position, (before, after) in enumerate(
        zip(before_records, after_records), 1
    ):
        differences = diff_records(before, after)
        if position == 7:
            assert [item.index for item in differences] == [79]
            assert before.raw_tokens[79] == "4,3414"
            assert after.raw_tokens[79] == "5,00"
            assert all(
                before.raw_tokens[index] == after.raw_tokens[index]
                for index in range(200)
                if index != 79
            )
        else:
            assert differences == []
            assert before.raw_tokens == after.raw_tokens
    diff_payload = json.loads(bundle.diff_json.read_text(encoding="utf-8"))
    assert diff_payload["status"] == "PASS"
    assert diff_payload["all_other_target_fields_token_identical"] is True
    assert diff_payload["all_non_target_records_token_identical"] is True
    assert all(
        item["token_identical"]
        for group in diff_payload["protected_target_fields"].values()
        for item in group.values()
    )
    assert {path.name for path in bundle.directory.iterdir()} == {
        "template.rx38",
        "generated_MY5.rx38",
        "project_element_MY5.json",
        "template_profile.json",
        "heating_evidence.json",
        "compatibility_evidence.json",
        "precalc_diff.json",
        "precalc_diff.md",
        "EXPECTED_RX3_GUI_VALUES.md",
        "CHECKLIST_PRECALC.md",
        "README_RUN_RX3.md",
        "audit.json",
    }


def test_my5_validation_refuses_fingerprint_mismatch(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    phase_a, observation = _my_phase_a_and_observation(
        tmp_path, monkeypatch
    )
    monkeypatch.setattr(rx3_experiment, "RX3_EXP_04_TARGET_FINGERPRINT", "0" * 64)

    with pytest.raises(Rx3ExperimentPreparationError, match="fingerprint"):
        prepare_rx3_my5_validation(
            phase_a.directory,
            observation,
            tmp_path / "my5",
        )


@pytest.mark.parametrize(
    ("expected_mark", "expected_position"),
    (("wrong", 7), ("Кс1", 6)),
)
def test_my5_validation_refuses_wrong_mark_or_position(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    expected_mark: str,
    expected_position: int,
) -> None:
    phase_a, observation = _my_phase_a_and_observation(
        tmp_path, monkeypatch
    )
    monkeypatch.setattr(rx3_experiment, "RX3_EXP_04_TARGET_MARK", expected_mark)
    monkeypatch.setattr(
        rx3_experiment, "RX3_EXP_04_TARGET_POSITION", expected_position
    )

    with pytest.raises(Rx3ExperimentPreparationError, match="mark|binding|fingerprint"):
        prepare_rx3_my5_validation(
            phase_a.directory,
            observation,
            tmp_path / "my5",
        )


def test_my5_validation_refuses_wrong_baseline_my(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    phase_a, observation = _my_phase_a_and_observation(
        tmp_path,
        monkeypatch,
        baseline_my="4.2",
    )

    with pytest.raises(Rx3ExperimentPreparationError, match="baseline My"):
        prepare_rx3_my5_validation(
            phase_a.directory,
            observation,
            tmp_path / "my5",
        )


@pytest.mark.parametrize("mode", (ExecutionMode.DRAFT, ExecutionMode.PRODUCTION))
def test_my5_validation_refuses_non_validation_modes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    mode: ExecutionMode,
) -> None:
    phase_a, observation = _my_phase_a_and_observation(
        tmp_path, monkeypatch
    )

    with pytest.raises(Rx3ExperimentPreparationError, match="VALIDATION"):
        prepare_rx3_my5_validation(
            phase_a.directory,
            observation,
            tmp_path / "my5",
            mode=mode,
        )


def test_my5_validation_does_not_mutate_or_open_generic_field79_writer(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    phase_a, observation = _my_phase_a_and_observation(
        tmp_path, monkeypatch
    )
    before_spec = field_spec(79)

    prepare_rx3_my5_validation(
        phase_a.directory,
        observation,
        tmp_path / "my5",
    )

    after_spec = field_spec(79)
    assert before_spec == after_spec
    assert after_spec.confidence == "confirmed"
    assert after_spec.write_policy is WritePolicy.EXPERIMENTAL
    record = construction_records(read_rx38(phase_a.template))[6]
    with pytest.raises(UnsafeRx38WriteError, match="EXPERIMENTAL"):
        record.with_typed_field(79, "5,00", compatibility_verified=True)


def test_my5_postcalc_validator_binds_target_and_classifies_persistence(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bundle, calculated, observation = _my5_postcalc_fixture(tmp_path, monkeypatch)

    report = validate_rx3_my5_result(bundle.directory, calculated, observation)

    assert report.data["status"] == "RX3_EXP_04B_ANALYSED"
    assert report.data["changed_target_indices"] == [44, 52, 54, 76, 78, 79]
    field79 = report.data["field79_persistence"]
    assert field79["old_token"] == "5,00"
    assert field79["new_token"] == "5"
    assert field79["old_numeric"] == "5.00"
    assert field79["new_numeric"] == "5"
    assert field79["text_changed"] is True
    assert field79["semantic_changed"] is False
    assert field79["classification"] == "RX3_TOKEN_NORMALIZATION"
    assert field79["persisted_numeric_matches_MY5"] is True
    assert report.data["Mx_persistence"] == {
        "gui_Mx_knm": "0.51",
        "field50_numeric": "0",
        "field50_unchanged": True,
        "field78_numeric": "0.51",
        "field78_matches_gui_Mx": True,
        "field122_unchanged": True,
    }
    assert report.data["field_observations"]["78"]["semantic_changed"] is True
    assert report.data["field_observations"]["78"]["classification"] == (
        "RX3_PERSISTED_GUI_DISPLAY_ROUNDING"
    )
    assert report.data["Q_persistence"] == {
        "gui_Q_kn": "0",
        "field92_numeric": "0",
        "field92_unchanged": True,
    }
    assert report.data["non_target_record_count"] == 6
    assert report.data["non_target_records_token_identical"] is True
    assert report.data["schema_mapping_promoted"] is True
    assert report.data["production_write_allowed"] is False
    assert field_spec(79).confidence == "confirmed"
    assert field_spec(79).write_policy is WritePolicy.EXPERIMENTAL


def test_my5_postcalc_validator_fails_on_non_target_mutation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bundle, calculated, observation = _my5_postcalc_fixture(tmp_path, monkeypatch)
    records = [
        list(record.fields)
        for record in construction_records(read_rx38(calculated))
    ]
    records[0][44] = "999"
    _rx38(calculated, records)

    with pytest.raises(Rx3ExperimentPreparationError, match="non-target mutation"):
        validate_rx3_my5_result(bundle.directory, calculated, observation)


def test_my5_postcalc_validator_fails_on_engineering_input_mutation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bundle, calculated, observation = _my5_postcalc_fixture(tmp_path, monkeypatch)
    records = [
        list(record.fields)
        for record in construction_records(read_rx38(calculated))
    ]
    records[6][92] = "0,0"
    _rx38(calculated, records)

    with pytest.raises(
        Rx3ExperimentPreparationError, match="engineering-input mutation"
    ):
        validate_rx3_my5_result(bundle.directory, calculated, observation)


@pytest.mark.parametrize(
    "field",
    ("generated_sha256", "target_generated_fingerprint"),
)
def test_my5_postcalc_validator_refuses_wrong_observation_binding(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    field: str,
) -> None:
    bundle, calculated, observation = _my5_postcalc_fixture(tmp_path, monkeypatch)
    payload = json.loads(observation.read_text(encoding="utf-8"))
    payload[field] = "0" * 64
    observation.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(Rx3ExperimentPreparationError, match="not bound"):
        validate_rx3_my5_result(bundle.directory, calculated, observation)


@pytest.mark.parametrize(
    ("section", "field"),
    (("calculation", "pressed"), ("persistence", "save_to_table"),
     ("persistence", "save_as")),
)
def test_my5_postcalc_validator_refuses_missing_calculate_or_save_evidence(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    section: str,
    field: str,
) -> None:
    bundle, calculated, observation = _my5_postcalc_fixture(tmp_path, monkeypatch)
    payload = json.loads(observation.read_text(encoding="utf-8"))
    payload[section][field] = False
    observation.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(Rx3ExperimentPreparationError, match="evidence is incomplete"):
        validate_rx3_my5_result(bundle.directory, calculated, observation)


def test_my5_postcalc_validator_leaves_production_field79_write_blocked(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bundle, calculated, observation = _my5_postcalc_fixture(tmp_path, monkeypatch)
    before_spec = field_spec(79)

    report = validate_rx3_my5_result(bundle.directory, calculated, observation)

    assert report.data["schema_mapping_promoted"] is True
    assert field_spec(79) == before_spec
    target = construction_records(read_rx38(calculated))[6]
    with pytest.raises(UnsafeRx38WriteError, match="EXPERIMENTAL"):
        target.with_typed_field(79, "6", compatibility_verified=True)
