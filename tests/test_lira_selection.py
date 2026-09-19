from __future__ import annotations

import json
from pathlib import Path

import pytest

from fireprotect.lira import (
    LiraFormatError,
    LiraMappingError,
    prepare_lira_selection_bundle,
    read_candidates,
    validate_lira_governing_selection,
)


def _value(component: str, token: str, unit: str, normalized: str) -> dict[str, object]:
    return {
        "native_component": component,
        "source_column": component,
        "source_cell": f"{component[0]}1",
        "raw_token": token,
        "parsed_decimal": token,
        "source_unit": unit,
        "normalized_value": normalized,
        "normalized_unit": "kN" if unit == "т" else "kN*m",
        "conversion": f"{component}: {token} {unit} -> {normalized}",
    }


def _record(
    *,
    element_id: str,
    station: str,
    load_case: str,
    row: int,
    n: str,
    my: str = "0",
    qz: str = "0",
    combination: str | None = None,
    composition: str = "-",
    profile: str | None = None,
    mark: str | None = None,
) -> dict[str, object]:
    return {
        "source": {
            "file": "export.xlsx",
            "sha256": "a" * 64,
            "sheet_or_table": " ",
            "row": row,
        },
        "metadata": {
            "element_id": element_id,
            "node_id": None,
            "member_id": None,
            "section_station": station,
            "mark": mark,
            "profile": profile,
            "load_case": load_case,
            "element_type": "10",
            "composition": composition,
            "combination": combination,
        },
        "metadata_provenance": {"element_id": {"source_cell": f"A{row}"}},
        "native_forces": {
            "N": _value("N", n, "т", f"{n}"),
            "Mk": _value("Mk", "0", "т*м", "0"),
            "My": _value("My", my, "т*м", f"{my}"),
            "Mz": _value("Mz", "0", "т*м", "0"),
            "Qy": _value("Qy", "0", "т", "0.00"),
            "Qz": _value("Qz", qz, "т", f"{qz}"),
        },
        "native_results": {
            "Ry": _value("Ry", "0", "т/м", "0"),
            "Rz": _value("Rz", "0", "т/м", "0"),
        },
        "native_to_rx3_convention": {},
        "row_blockers": ["LIRA_RX3_FORCE_CONVENTION"],
    }


def _write_forces(tmp_path: Path, records: list[dict[str, object]]) -> Path:
    path = tmp_path / "forces.json"
    path.write_text(
        json.dumps(
            {
                "source_model": "LIRA_NATIVE_BAR_FORCES",
                "accepted_native_records": records,
                "rejected_rows": [],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    return path


def _two_elements_two_cases(tmp_path: Path) -> Path:
    records: list[dict[str, object]] = []
    row = 4
    for element in ("6", "10"):
        for station in ("1", "2"):
            for case in ("1", "2"):
                records.append(
                    _record(
                        element_id=element,
                        station=station,
                        load_case=case,
                        row=row,
                        n=f"-{row}.5",
                    )
                )
                row += 1
    return _write_forces(tmp_path, records)


def _declaration(
    tmp_path: Path,
    elements: list[dict[str, object]],
    *,
    declared_by: str | None = "engineer",
    basis: str | None = "governing value read from the source GUI",
) -> Path:
    path = tmp_path / "selection.json"
    path.write_text(
        json.dumps(
            {
                "declaration_kind": "ENGINEER_GOVERNING_RESULT_DECLARATION",
                "declared_by": declared_by,
                "basis": basis,
                "elements": elements,
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    return path


def test_prepare_bundle_enumerates_every_candidate(tmp_path: Path) -> None:
    forces = _two_elements_two_cases(tmp_path)
    bundle = prepare_lira_selection_bundle(forces, tmp_path / "selection")

    assert len(bundle.candidates) == 8
    assert bundle.unique_elements == ("6", "10")
    assert [item.candidate_id for item in bundle.candidates] == [
        f"C{index:04d}" for index in range(1, 9)
    ]
    assert {item.section_station for item in bundle.candidates} == {"1", "2"}
    assert {item.load_case for item in bundle.candidates} == {"1", "2"}

    payload = json.loads((tmp_path / "selection" / "candidates.json").read_text("utf-8"))
    assert payload["status"] == "CANDIDATE_ONLY"
    first = payload["candidates"][0]
    assert first["status"] == "CANDIDATE_ONLY"
    assert first["native_values"]["N"]["raw_token"] == "-4.5"
    assert first["native_values"]["N"]["source_unit"] == "т"
    assert first["native_values"]["N"]["normalized_value"] == "-4.5"
    assert first["source"]["row"] == 4


def test_prepare_bundle_selects_nothing(tmp_path: Path) -> None:
    forces = _two_elements_two_cases(tmp_path)
    prepare_lira_selection_bundle(forces, tmp_path / "selection")

    manifest = json.loads((tmp_path / "selection" / "manifest.json").read_text("utf-8"))
    assert manifest["governing_result_selection"] is None
    assert manifest["governing_result_selection_validated"] is False
    assert manifest["rx38_force_generation_allowed"] is False
    assert manifest["issue_readiness"] == "NOT_READY_FOR_ISSUE"

    template = json.loads(
        (tmp_path / "selection" / "selection_template.json").read_text("utf-8")
    )
    assert template["declaration_kind"] == "ENGINEER_GOVERNING_RESULT_DECLARATION"
    assert [item["element_id"] for item in template["elements"]] == ["6", "10"]
    assert all(item["candidate_id"] is None for item in template["elements"])

    candidates = json.loads(
        (tmp_path / "selection" / "candidates.json").read_text("utf-8")
    )
    assert all("selected" not in item for item in candidates["candidates"])


def test_prepare_bundle_refuses_existing_directory(tmp_path: Path) -> None:
    forces = _two_elements_two_cases(tmp_path)
    (tmp_path / "selection").mkdir()
    with pytest.raises(LiraFormatError, match="refusing to overwrite"):
        prepare_lira_selection_bundle(forces, tmp_path / "selection")


def test_prepare_bundle_refuses_empty_bundle(tmp_path: Path) -> None:
    forces = _write_forces(tmp_path, [])
    with pytest.raises(LiraFormatError, match="no accepted native records"):
        prepare_lira_selection_bundle(forces, tmp_path / "selection")


def test_prepare_bundle_requires_element_identity(tmp_path: Path) -> None:
    record = _record(element_id="6", station="1", load_case="1", row=4, n="-1")
    metadata = record["metadata"]
    assert isinstance(metadata, dict)
    metadata["element_id"] = None
    forces = _write_forces(tmp_path, [record])
    with pytest.raises(LiraFormatError, match="no element_id"):
        read_candidates(forces)


def test_validate_resolves_declaration_to_one_candidate(tmp_path: Path) -> None:
    forces = _two_elements_two_cases(tmp_path)
    prepare_lira_selection_bundle(forces, tmp_path / "selection")
    selection = _declaration(
        tmp_path,
        [
            {"element_id": "6", "candidate_id": "C0002"},
            {"element_id": "10", "candidate_id": "C0007"},
        ],
    )
    report = validate_lira_governing_selection(
        tmp_path / "selection" / "candidates.json", selection
    )
    payload = report.as_dict()

    assert payload["elements_total"] == 2
    assert payload["elements_declared"] == 2
    assert payload["candidates_total"] == 8
    assert [item["candidate_id"] for item in payload["resolved"]] == ["C0002", "C0007"]
    assert payload["resolved"][0]["identity"]["section_station"] == "1"
    assert payload["resolved"][0]["identity"]["load_case"] == "2"
    assert payload["resolved"][1]["identity"]["element_id"] == "10"
    assert payload["resolved"][0]["declared_by"] == "engineer"


def test_validate_never_promotes_a_declaration(tmp_path: Path) -> None:
    forces = _two_elements_two_cases(tmp_path)
    prepare_lira_selection_bundle(forces, tmp_path / "selection")
    selection = _declaration(tmp_path, [{"element_id": "6", "candidate_id": "C0001"}])
    payload = validate_lira_governing_selection(
        tmp_path / "selection" / "candidates.json", selection
    ).as_dict()

    assert payload["governing_result_selection_validated"] is False
    assert payload["rx38_force_generation_allowed"] is False
    assert payload["issue_readiness"] == "NOT_READY_FOR_ISSUE"
    assert payload["status"] == "BLOCKED"
    codes = {item["code"] for item in payload["blockers"]}
    assert "LIRA_GOVERNING_RESULT_SELECTION_UNRESOLVED" in codes
    assert "GOVERNING_SELECTION_DECLARATION_IS_NOT_INDEPENDENT_EVIDENCE" in codes


def test_validate_blocks_unknown_candidate(tmp_path: Path) -> None:
    forces = _two_elements_two_cases(tmp_path)
    prepare_lira_selection_bundle(forces, tmp_path / "selection")
    selection = _declaration(tmp_path, [{"element_id": "6", "candidate_id": "C9999"}])
    payload = validate_lira_governing_selection(
        tmp_path / "selection" / "candidates.json", selection
    ).as_dict()

    codes = {item["code"] for item in payload["blockers"]}
    assert "GOVERNING_SELECTION_CANDIDATE_UNKNOWN" in codes
    assert payload["elements_declared"] == 0


def test_validate_blocks_unknown_element(tmp_path: Path) -> None:
    forces = _two_elements_two_cases(tmp_path)
    prepare_lira_selection_bundle(forces, tmp_path / "selection")
    selection = _declaration(tmp_path, [{"element_id": "777", "candidate_id": "C0001"}])
    payload = validate_lira_governing_selection(
        tmp_path / "selection" / "candidates.json", selection
    ).as_dict()

    codes = {item["code"] for item in payload["blockers"]}
    assert "GOVERNING_SELECTION_ELEMENT_UNKNOWN" in codes


def test_validate_blocks_element_mismatch(tmp_path: Path) -> None:
    forces = _two_elements_two_cases(tmp_path)
    prepare_lira_selection_bundle(forces, tmp_path / "selection")
    selection = _declaration(tmp_path, [{"element_id": "10", "candidate_id": "C0001"}])
    payload = validate_lira_governing_selection(
        tmp_path / "selection" / "candidates.json", selection
    ).as_dict()

    codes = {item["code"] for item in payload["blockers"]}
    assert "GOVERNING_SELECTION_ELEMENT_MISMATCH" in codes
    assert payload["elements_declared"] == 0


def test_validate_blocks_duplicate_element_declaration(tmp_path: Path) -> None:
    forces = _two_elements_two_cases(tmp_path)
    prepare_lira_selection_bundle(forces, tmp_path / "selection")
    selection = _declaration(
        tmp_path,
        [
            {"element_id": "6", "candidate_id": "C0001"},
            {"element_id": "6", "candidate_id": "C0002"},
        ],
    )
    payload = validate_lira_governing_selection(
        tmp_path / "selection" / "candidates.json", selection
    ).as_dict()

    codes = {item["code"] for item in payload["blockers"]}
    assert "GOVERNING_SELECTION_ELEMENT_DECLARED_TWICE" in codes
    assert payload["elements_declared"] == 1


def test_validate_blocks_candidate_reused_across_elements(tmp_path: Path) -> None:
    forces = _two_elements_two_cases(tmp_path)
    prepare_lira_selection_bundle(forces, tmp_path / "selection")
    selection = _declaration(
        tmp_path,
        [
            {"element_id": "6", "candidate_id": "C0001"},
            {"element_id": "10", "candidate_id": "C0001"},
        ],
    )
    payload = validate_lira_governing_selection(
        tmp_path / "selection" / "candidates.json", selection
    ).as_dict()

    codes = {item["code"] for item in payload["blockers"]}
    assert "GOVERNING_SELECTION_ELEMENT_MISMATCH" in codes


def test_validate_requires_declared_by_and_basis(tmp_path: Path) -> None:
    forces = _two_elements_two_cases(tmp_path)
    prepare_lira_selection_bundle(forces, tmp_path / "selection")
    candidates = tmp_path / "selection" / "candidates.json"

    missing_author = _declaration(
        tmp_path, [{"element_id": "6", "candidate_id": "C0001"}], declared_by=""
    )
    with pytest.raises(LiraMappingError, match="declared_by"):
        validate_lira_governing_selection(candidates, missing_author)

    missing_basis = _declaration(
        tmp_path, [{"element_id": "6", "candidate_id": "C0001"}], basis=None
    )
    with pytest.raises(LiraMappingError, match="basis"):
        validate_lira_governing_selection(candidates, missing_basis)


def test_validate_rejects_foreign_declaration_kind(tmp_path: Path) -> None:
    forces = _two_elements_two_cases(tmp_path)
    prepare_lira_selection_bundle(forces, tmp_path / "selection")
    path = tmp_path / "selection.json"
    path.write_text(
        json.dumps(
            {
                "declaration_kind": "SOMETHING_ELSE",
                "declared_by": "engineer",
                "basis": "basis",
                "elements": [],
            }
        ),
        encoding="utf-8",
    )
    with pytest.raises(LiraMappingError, match="declaration_kind"):
        validate_lira_governing_selection(
            tmp_path / "selection" / "candidates.json", path
        )


def test_validate_accepts_untouched_template_as_declaring_nothing(
    tmp_path: Path,
) -> None:
    forces = _two_elements_two_cases(tmp_path)
    prepare_lira_selection_bundle(forces, tmp_path / "selection")
    template = tmp_path / "selection" / "selection_template.json"
    path = tmp_path / "filled.json"
    payload = json.loads(template.read_text("utf-8"))
    payload["declared_by"] = "engineer"
    payload["basis"] = "no governing candidate declared yet"
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")

    report = validate_lira_governing_selection(
        tmp_path / "selection" / "candidates.json", path
    ).as_dict()
    assert report["elements_declared"] == 0
    assert report["status"] == "BLOCKED"


def test_validate_accepts_a_byte_order_mark(tmp_path: Path) -> None:
    forces = _two_elements_two_cases(tmp_path)
    prepare_lira_selection_bundle(forces, tmp_path / "selection")
    path = tmp_path / "selection.json"
    path.write_text(
        json.dumps(
            {
                "declaration_kind": "ENGINEER_GOVERNING_RESULT_DECLARATION",
                "declared_by": "engineer",
                "basis": "governing value read from the source GUI",
                "elements": [{"element_id": "6", "candidate_id": "C0001"}],
            }
        ),
        encoding="utf-8-sig",
    )
    payload = validate_lira_governing_selection(
        tmp_path / "selection" / "candidates.json", path
    ).as_dict()
    assert payload["elements_declared"] == 1


def test_validate_warns_when_source_has_no_combination_identity(
    tmp_path: Path,
) -> None:
    forces = _two_elements_two_cases(tmp_path)
    prepare_lira_selection_bundle(forces, tmp_path / "selection")
    selection = _declaration(tmp_path, [{"element_id": "6", "candidate_id": "C0001"}])
    payload = validate_lira_governing_selection(
        tmp_path / "selection" / "candidates.json", selection
    ).as_dict()

    codes = {item["code"] for item in payload["warnings"]}
    assert "SOURCE_HAS_NO_LOAD_COMBINATION_IDENTITY" in codes
    assert "SINGLE_CANDIDATE_PER_ELEMENT_PROVES_NO_SELECTION_RULE" not in codes


def test_validate_notes_single_candidate_sources_prove_no_rule(
    tmp_path: Path,
) -> None:
    forces = _write_forces(
        tmp_path,
        [
            _record(element_id="6", station="1", load_case="1", row=4, n="-1"),
            _record(
                element_id="7",
                station="1",
                load_case="1",
                row=5,
                n="-2",
                combination="1",
            ),
        ],
    )
    prepare_lira_selection_bundle(forces, tmp_path / "selection")
    selection = _declaration(tmp_path, [{"element_id": "6", "candidate_id": "C0001"}])
    payload = validate_lira_governing_selection(
        tmp_path / "selection" / "candidates.json", selection
    ).as_dict()

    codes = {item["code"] for item in payload["warnings"]}
    assert "SINGLE_CANDIDATE_PER_ELEMENT_PROVES_NO_SELECTION_RULE" in codes
    assert "SOURCE_HAS_NO_LOAD_COMBINATION_IDENTITY" not in codes
