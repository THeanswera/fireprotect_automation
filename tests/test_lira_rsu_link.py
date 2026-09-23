from __future__ import annotations

import csv
import hashlib
import json
from decimal import Decimal
from pathlib import Path
from typing import Mapping

import pytest

from fireprotect.lira import (
    LiraFormatError,
    LiraMappingError,
    prepare_linked_rsu_bundle,
    read_rsu_evidence,
)

COMPONENTS = ("N", "Mk", "My", "Mz", "Qy", "Qz")
BIFF = "BIFF_NUMERIC_VALUE_DECODED_NO_RAW_DECIMAL_TOKEN"
FINGERPRINT = hashlib.sha256(b"test-rsu-mapping").hexdigest()

_PUBLISHED_CELLS = {"N": "G4", "Mk": "H4", "My": "I4", "Mz": "K4", "Qy": "L4", "Qz": "J4"}
_TERM_CELLS = {"N": "C", "Mk": "D", "My": "E", "Mz": "G", "Qy": "H", "Qz": "F"}


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _unit(component: str) -> str:
    return "tf*m" if component in ("Mk", "My", "Mz") else "tf"


def _value(
    value: str,
    component: str,
    *,
    sheet: str = " ",
    row: int = 4,
    cell: str,
    sha: str,
) -> dict[str, object]:
    return {
        "value": value,
        "unit": _unit(component),
        "header": f"{component}\n({'т*м}' if _unit(component) == 'tf*m' else 'т'})",
        "sheet": sheet,
        "row": row,
        "cell": cell,
        "source_sha256": sha,
        "raw_token": None,
        "decimal_provenance": BIFF,
    }


def _forces(values: Mapping[str, str]) -> dict[str, str]:
    base = {name: "0.0" for name in COMPONENTS}
    base.update(values)
    return base


def _term(
    load_case_id: str,
    coefficient: str,
    force_values: Mapping[str, str],
    *,
    force_sheet: str,
    force_row: int,
    force_sha: str,
    coefficient_sha: str,
) -> dict[str, object]:
    forces = {
        name: _value(
            force_values[name],
            name,
            sheet=force_sheet,
            row=force_row,
            cell=f"{_TERM_CELLS[name]}{force_row}",
            sha=force_sha,
        )
        for name in COMPONENTS
    }
    return {
        "load_case_id": load_case_id,
        "force_sheet": force_sheet,
        "force_row": force_row,
        "forces": forces,
        "coefficient": coefficient,
        "coefficient_source": {
            "sheet": " ",
            "row": 4,
            "cell": "E4",
            "header": "2 основ.",
            "source_sha256": coefficient_sha,
            "raw_token": None,
            "decimal_provenance": BIFF,
        },
    }


def _row(
    row_id: str,
    element_id: str,
    station: str,
    group: str,
    criterion: str,
    column: int,
    membership: list[str],
    terms: list[dict[str, object]],
    *,
    published_sha: str,
) -> dict[str, object]:
    published: dict[str, str] = {}
    reconstruction: dict[str, dict[str, str]] = {}
    for name in COMPONENTS:
        total = sum(
            (
                Decimal(str(term["coefficient"]))
                * Decimal(str(term["forces"][name]["value"]))
                for term in terms
            ),
            Decimal("0"),
        )
        published[name] = str(total)
        reconstruction[name] = {
            "published": str(total),
            "reconstructed": str(total),
            "difference": "0",
        }
    return {
        "row_id": row_id,
        "status": "VERIFIED",
        "blockers": [],
        "identity": {
            "element_id": element_id,
            "section_station": station,
            "rsu_group": group,
            "rsu_criterion": criterion,
            "rsu_column_number": column,
            "load_case_membership": list(membership),
        },
        "source": {
            "sheet": " ",
            "row": 4,
            "sha256": published_sha,
            "mapping_fingerprint": FINGERPRINT,
        },
        "published_vector": {
            name: _value(
                published[name],
                name,
                row=4,
                cell=_PUBLISHED_CELLS[name],
                sha=published_sha,
            )
            for name in COMPONENTS
        },
        "reconstruction": reconstruction,
        "source_terms": list(terms),
    }


def _rsu_sources(
    tmp_path: Path, contents: Mapping[str, bytes] | None = None
) -> dict[str, dict[str, object]]:
    contents = contents or {
        "forces": b"forces-xls-v1",
        "published": b"published-xls-v1",
        "coefficients": b"coefficients-xls-v1",
        "parameters": b"parameters-xls-v1",
    }
    base = tmp_path / "rsu_src"
    base.mkdir(parents=True, exist_ok=True)
    result: dict[str, dict[str, object]] = {}
    for name, data in contents.items():
        path = base / f"{name}.bin"
        path.write_bytes(data)
        result[name] = {
            "path": str(path),
            "sha256": _sha256_bytes(data),
            "sheets": 1,
        }
    return result


def _write_evidence(
    tmp_path: Path,
    rows: list[dict[str, object]],
    sources: dict[str, dict[str, object]] | None = None,
) -> Path:
    sources = sources or _rsu_sources(tmp_path)
    payload = {
        "kind": "READ_ONLY_LIRA_RSU_EVIDENCE",
        "status": "VERIFIED",
        "sources": sources,
        "mapping_fingerprint": FINGERPRINT,
        "force_records": 2 * len(rows),
        "published_records": len(rows),
        "component_comparisons": 6 * len(rows),
        "matching_components": 6 * len(rows),
        "blockers": [],
        "load_parameters": [
            {
                "load_case_id": "1",
                "values": {},
                "sheet": " ",
                "row": 4,
                "source_sha256": sources["parameters"]["sha256"],
            },
            {
                "load_case_id": "2",
                "values": {},
                "sheet": " ",
                "row": 5,
                "source_sha256": sources["parameters"]["sha256"],
            },
        ],
        "rows": rows,
        "governing_result_selection_validated": False,
        "rx38_force_generation_allowed": False,
        "issue_readiness": "NOT_READY_FOR_ISSUE",
    }
    path = tmp_path / "rsu_evidence.json"
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return path


def _model_element(
    element_id: str,
    *,
    section_count: str | None = "2",
    node_ids: tuple[str, ...] = ("1", "3"),
) -> dict[str, object]:
    return {
        "element_id": element_id,
        "status": "BLOCKED",
        "identity": {
            "mark": None,
            "kind_word": None,
            "designation": "Брус 10 X 20",
            "element_type": "10",
            "stiffness_type": "1",
            "section_count": section_count,
        },
        "geometry": {
            "node_ids": list(node_ids),
            "length_m": "3.0",
            "rotation_angle_degrees": "0.0",
            "supports": {
                "start": {
                    "node_id": node_ids[0],
                    "signs": {"x": "+", "y": "-", "z": "+", "ux": "-", "uy": "-", "uz": "-"},
                },
                "end": {
                    "node_id": node_ids[1],
                    "signs": {"x": "-", "y": "-", "z": "-", "ux": "-", "uy": "-", "uz": "-"},
                },
            },
        },
        "force_candidates": [],
        "blockers": ["LIRA_STIFFNESS_NAME_WITHOUT_MARK"],
        "source_rows": {"element": 4, "stiffness": 3},
    }


def _write_model_package(
    tmp_path: Path,
    elements: list[dict[str, object]],
    *,
    source_contents: Mapping[str, bytes] | None = None,
) -> tuple[Path, dict[str, dict[str, object]]]:
    package = tmp_path / "model_pkg"
    package.mkdir()
    source_contents = source_contents or {
        "stiffness": b"stiffness-xls-v1",
        "elements": b"elements-xls-v1",
        "nodes": b"nodes-xls-v1",
    }
    base = tmp_path / "model_src"
    base.mkdir(exist_ok=True)
    entries: dict[str, dict[str, object]] = {}
    for name, data in source_contents.items():
        path = base / f"{name}.bin"
        path.write_bytes(data)
        entries[name] = {
            "path": str(path),
            "sha256": _sha256_bytes(data),
            "sheet": " ",
            "header_row": "2",
        }
    manifest = {
        "status": "MODEL_ASSEMBLED",
        "sources": entries,
        "elements": len(elements),
        "elements_blocked": 0,
        "stiffness_types": 1,
        "nodes": 3,
        "profile_resolution": None,
        "ptm": None,
        "rx38_force_generation_allowed": False,
        "issue_readiness": "NOT_READY_FOR_ISSUE",
    }
    (package / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    (package / "elements.json").write_text(
        json.dumps({"elements": elements, "marks": []}, ensure_ascii=False, indent=2)
        + "\n",
        encoding="utf-8",
    )
    return package, entries


def _standard_rows(
    sources: dict[str, dict[str, object]],
    *,
    elements: tuple[str, ...] = ("1", "2"),
) -> list[dict[str, object]]:
    published_sha = str(sources["published"]["sha256"])
    force_sha = str(sources["forces"]["sha256"])
    coefficient_sha = str(sources["coefficients"]["sha256"])
    rows: list[dict[str, object]] = []
    idx = 0
    for element in elements:
        for station in ("1", "2"):
            for group in ("A1", "B1"):
                for column in (1, 2):
                    idx += 1
                    terms = [
                        _term(
                            "1",
                            "1.0",
                            _forces(
                                {
                                    "N": str(10 * idx),
                                    "Qz": str(Decimal("1.5") * idx),
                                }
                            ),
                            force_sheet="1",
                            force_row=4,
                            force_sha=force_sha,
                            coefficient_sha=coefficient_sha,
                        ),
                        _term(
                            "2",
                            "0.9",
                            _forces(
                                {
                                    "My": str(2 * idx),
                                    "Qz": str(Decimal("-3.5") * idx),
                                }
                            ),
                            force_sheet="2",
                            force_row=4,
                            force_sha=force_sha,
                            coefficient_sha=coefficient_sha,
                        ),
                    ]
                    rows.append(
                        _row(
                            f"R{idx:04d}",
                            element,
                            station,
                            group,
                            "13",
                            column,
                            ["1", "2"],
                            terms,
                            published_sha=published_sha,
                        )
                    )
    return rows


# --------------------------------------------------------------------------
# Positive
# --------------------------------------------------------------------------


def test_link_joins_model_and_all_rsu_rows(tmp_path: Path) -> None:
    model_pkg, model_entries = _write_model_package(
        tmp_path,
        [_model_element("1"), _model_element("2", node_ids=("3", "2"))],
    )
    sources = _rsu_sources(tmp_path)
    evidence = _write_evidence(tmp_path, _standard_rows(sources))
    output = tmp_path / "linked"

    report = prepare_linked_rsu_bundle(
        model_dir=model_pkg, evidence_path=evidence, output_dir=output
    )

    assert report.rows_total == 16
    assert report.components_preserved == 96
    assert report.elements_without_rows == ()

    manifest = json.loads((output / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["status"] == "MODEL_RSU_LINKED"
    basis = manifest["link_basis"]
    assert basis["comparison_key"] == "element_id"
    assert basis["historical_origin_claim"] == "NOT_INDEPENDENTLY_PROVEN"
    assert basis["rsu_evidence"]["path"] == str(evidence.resolve())
    assert basis["rsu_evidence"]["sha256"] == _sha256_bytes(
        evidence.read_bytes()
    )
    rechecked = basis["source_files_reverified"]
    assert set(rechecked["model"]) == {"stiffness", "elements", "nodes"}
    assert set(rechecked["rsu"]) == {
        "forces",
        "published",
        "coefficients",
        "parameters",
    }
    for group in rechecked.values():
        for facts in group.values():
            assert facts["matches"] is True
    assert manifest["rsu"]["rows"] == 16
    assert manifest["rsu"]["components_preserved"] == 96
    assert manifest["rsu"]["reconstruction_rechecked"] is True
    assert manifest["links"]["rows_per_element"] == {"1": 8, "2": 8}
    assert manifest["links"]["elements_without_rsu_rows"] == []
    assert manifest["links"]["governing_result_selection"] is None
    assert manifest["governing_result_selection_validated"] is False
    assert manifest["rx38_force_generation_allowed"] is False
    assert manifest["issue_readiness"] == "NOT_READY_FOR_ISSUE"

    linked = json.loads((output / "linked_elements.json").read_text(encoding="utf-8"))
    elements = linked["elements"]
    assert len(elements) == 2
    by_id = {str(item["element_id"]): item for item in elements}
    for element_id in ("1", "2"):
        item = by_id[element_id]
        assert len(item["rsu_rows"]) == 8
        assert item["force_candidates"] == []
        assert item["geometry"]["supports"]["start"]["node_id"] == (
            "1" if element_id == "1" else "3"
        )
        assert item["geometry"]["supports"]["start"]["signs"]["x"] == "+"
        assert item["blockers"] == ["LIRA_STIFFNESS_NAME_WITHOUT_MARK"]
        for row in item["rsu_rows"]:
            assert row["identity"]["element_id"] == element_id
            assert row["identity"]["load_case_membership"] == ["1", "2"]
            assert row["status"] == "VERIFIED"
            assert "load_case" not in row
            for component in COMPONENTS:
                assert component in row["published_vector"]
                assert component in row["source_terms"][0]["forces"]
                assert component in row["source_terms"][1]["forces"]

    # All 96 components preserved byte-for-byte (identical to the source
    # evidence strings) and numerically equal to independently derived values
    # with their original signs.
    evidence_payload = json.loads(evidence.read_text(encoding="utf-8"))
    source_rows = {
        str(row["row_id"]): row for row in evidence_payload["rows"]
    }
    checked = 0
    for item in elements:
        for row in item["rsu_rows"]:
            row_id = str(row["row_id"])
            idx = int(row_id[1:])
            assert str(row["identity"]["element_id"]) == (
                "1" if idx <= 8 else "2"
            )
            source_row = source_rows[row_id]
            for component in COMPONENTS:
                assert (
                    row["published_vector"][component]["value"]
                    == source_row["published_vector"][component]["value"]
                )
                assert Decimal(row["published_vector"][component]["value"]) == {
                    "N": Decimal(10 * idx),
                    "Mk": Decimal("0"),
                    "My": Decimal("0.9") * 2 * idx,
                    "Mz": Decimal("0"),
                    "Qy": Decimal("0"),
                    "Qz": Decimal("1.5") * idx + Decimal("-3.15") * idx,
                }[component]
                checked += 1
    assert checked == 96

    # Negative and positive signs survive into the CSV with explicit units.
    with (output / "rsu_candidates.csv").open(
        encoding="utf-8", newline=""
    ) as stream:
        reader = csv.reader(stream)
        header = next(reader)
        data = list(reader)
    assert header == [
        "row_id",
        "element_id",
        "section_station",
        "rsu_group",
        "rsu_criterion",
        "rsu_column_number",
        "load_case_membership",
        "status",
        "source_sheet",
        "source_row",
        "N [tf]",
        "Mk [tf*m]",
        "My [tf*m]",
        "Mz [tf*m]",
        "Qy [tf]",
        "Qz [tf]",
    ]
    assert len(data) == 16
    by_row_id = {row[0]: row for row in data}
    assert by_row_id["R0001"][1] == "1"
    assert by_row_id["R0001"][6] == "1 2"
    assert Decimal(by_row_id["R0001"][10]) == Decimal("10")
    assert Decimal(by_row_id["R0001"][12]) == Decimal("1.8")
    assert by_row_id["R0001"][15] == "-1.65"
    assert by_row_id["R0005"][2] == "2"
    assert Decimal(by_row_id["R0005"][10]) == Decimal("50")
    assert by_row_id["R0005"][15] == "-8.25"
    assert by_row_id["R0016"][1] == "2"
    assert by_row_id["R0016"][15] == "-26.40"


def test_read_rsu_evidence_rechecks_sources_and_reconstruction(tmp_path: Path) -> None:
    sources = _rsu_sources(tmp_path)
    evidence = _write_evidence(tmp_path, _standard_rows(sources))

    bundle = read_rsu_evidence(evidence)

    assert bundle.status == "VERIFIED"
    assert len(bundle.rows) == 16
    assert bundle.components_preserved == 96
    assert set(bundle.source_files_rechecked) == {
        "forces",
        "published",
        "coefficients",
        "parameters",
    }
    for facts in bundle.source_files_rechecked.values():
        assert facts["matches"] is True


def test_same_geometry_rows_stay_separate_candidates(tmp_path: Path) -> None:
    model_pkg, _ = _write_model_package(tmp_path, [_model_element("1")])
    sources = _rsu_sources(tmp_path)
    rows = _standard_rows(sources, elements=("1",))
    rows = [rows[0], rows[2]]  # same element and station, different groups
    assert rows[0]["identity"]["section_station"] == rows[1]["identity"]["section_station"]
    evidence = _write_evidence(tmp_path, rows, sources)
    output = tmp_path / "linked"

    report = prepare_linked_rsu_bundle(
        model_dir=model_pkg, evidence_path=evidence, output_dir=output
    )

    linked = json.loads((output / "linked_elements.json").read_text(encoding="utf-8"))
    linked_rows = linked["elements"][0]["rsu_rows"]
    assert len(linked_rows) == 2
    assert {row["row_id"] for row in linked_rows} == {"R0001", "R0003"}
    assert {row["identity"]["rsu_group"] for row in linked_rows} == {"A1", "B1"}
    assert report.rows_total == 2


def test_element_without_rsu_rows_is_reported(tmp_path: Path) -> None:
    model_pkg, _ = _write_model_package(
        tmp_path, [_model_element("1"), _model_element("2")]
    )
    sources = _rsu_sources(tmp_path)
    evidence = _write_evidence(tmp_path, _standard_rows(sources, elements=("1",)))
    output = tmp_path / "linked"

    report = prepare_linked_rsu_bundle(
        model_dir=model_pkg, evidence_path=evidence, output_dir=output
    )

    assert report.elements_without_rows == ("2",)
    linked = json.loads((output / "linked_elements.json").read_text(encoding="utf-8"))
    by_id = {str(item["element_id"]): item for item in linked["elements"]}
    assert by_id["2"]["rsu_rows"] == []
    manifest = json.loads((output / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["links"]["elements_without_rsu_rows"] == ["2"]
    assert manifest["links"]["rows_per_element"] == {"1": 8, "2": 0}


# --------------------------------------------------------------------------
# Negative
# --------------------------------------------------------------------------


def _link_or_fail(
    tmp_path: Path,
    model_pkg: Path,
    evidence: Path,
) -> None:
    prepare_linked_rsu_bundle(
        model_dir=model_pkg, evidence_path=evidence, output_dir=tmp_path / "linked"
    )


def test_unknown_element_in_evidence_raises(tmp_path: Path) -> None:
    model_pkg, _ = _write_model_package(tmp_path, [_model_element("1")])
    sources = _rsu_sources(tmp_path)
    rows = _standard_rows(sources)
    rows[0]["identity"]["element_id"] = "9"
    evidence = _write_evidence(tmp_path, rows, sources)
    with pytest.raises(LiraMappingError, match="absent from the model package"):
        _link_or_fail(tmp_path, model_pkg, evidence)


def test_duplicate_row_id_raises(tmp_path: Path) -> None:
    model_pkg, _ = _write_model_package(tmp_path, [_model_element("1")])
    sources = _rsu_sources(tmp_path)
    rows = _standard_rows(sources, elements=("1",))
    rows[1]["row_id"] = rows[0]["row_id"]
    evidence = _write_evidence(tmp_path, rows, sources)
    with pytest.raises(LiraFormatError, match="duplicate RSU row_id"):
        _link_or_fail(tmp_path, model_pkg, evidence)


@pytest.mark.parametrize("station", ("0", "3", "1.5", "два"))
def test_invalid_section_station_raises(tmp_path: Path, station: str) -> None:
    model_pkg, _ = _write_model_package(tmp_path, [_model_element("1")])
    sources = _rsu_sources(tmp_path)
    rows = _standard_rows(sources, elements=("1",))
    rows[0]["identity"]["section_station"] = station
    evidence = _write_evidence(tmp_path, rows, sources)
    with pytest.raises(LiraMappingError):
        _link_or_fail(tmp_path, model_pkg, evidence)


def test_model_without_section_count_raises(tmp_path: Path) -> None:
    model_pkg, _ = _write_model_package(
        tmp_path, [_model_element("1", section_count=None)]
    )
    sources = _rsu_sources(tmp_path)
    evidence = _write_evidence(tmp_path, _standard_rows(sources, elements=("1",)))
    with pytest.raises(LiraMappingError, match="has no section_count"):
        _link_or_fail(tmp_path, model_pkg, evidence)


def test_incomplete_published_vector_raises(tmp_path: Path) -> None:
    model_pkg, _ = _write_model_package(tmp_path, [_model_element("1")])
    sources = _rsu_sources(tmp_path)
    rows = _standard_rows(sources, elements=("1",))
    del rows[0]["published_vector"]["Qz"]
    evidence = _write_evidence(tmp_path, rows, sources)
    with pytest.raises(LiraFormatError, match="force vector must contain exactly"):
        _link_or_fail(tmp_path, model_pkg, evidence)


def test_incomplete_term_vector_raises(tmp_path: Path) -> None:
    model_pkg, _ = _write_model_package(tmp_path, [_model_element("1")])
    sources = _rsu_sources(tmp_path)
    rows = _standard_rows(sources, elements=("1",))
    del rows[0]["source_terms"][0]["forces"]["Mz"]
    evidence = _write_evidence(tmp_path, rows, sources)
    with pytest.raises(LiraFormatError, match="force vector must contain exactly"):
        _link_or_fail(tmp_path, model_pkg, evidence)


def test_changed_model_source_raises(tmp_path: Path) -> None:
    model_pkg, model_entries = _write_model_package(
        tmp_path, [_model_element("1")]
    )
    sources = _rsu_sources(tmp_path)
    evidence = _write_evidence(tmp_path, _standard_rows(sources, elements=("1",)))
    Path(str(model_entries["nodes"]["path"])).write_bytes(b"tampered-nodes")
    with pytest.raises(LiraMappingError, match="changed"):
        _link_or_fail(tmp_path, model_pkg, evidence)


def test_changed_rsu_source_raises(tmp_path: Path) -> None:
    model_pkg, _ = _write_model_package(tmp_path, [_model_element("1")])
    sources = _rsu_sources(tmp_path)
    evidence = _write_evidence(tmp_path, _standard_rows(sources, elements=("1",)))
    Path(str(sources["published"]["path"])).write_bytes(b"tampered-published")
    with pytest.raises(LiraMappingError, match="changed"):
        _link_or_fail(tmp_path, model_pkg, evidence)


def test_reconstruction_mismatch_raises(tmp_path: Path) -> None:
    model_pkg, _ = _write_model_package(tmp_path, [_model_element("1")])
    sources = _rsu_sources(tmp_path)
    rows = _standard_rows(sources, elements=("1",))
    rows[0]["published_vector"]["N"]["value"] = "999"
    rows[0]["reconstruction"]["N"]["published"] = "999"
    evidence = _write_evidence(tmp_path, rows, sources)
    with pytest.raises(LiraFormatError, match="not reproduced from the source terms"):
        _link_or_fail(tmp_path, model_pkg, evidence)


def test_blocked_row_raises(tmp_path: Path) -> None:
    model_pkg, _ = _write_model_package(tmp_path, [_model_element("1")])
    sources = _rsu_sources(tmp_path)
    rows = _standard_rows(sources, elements=("1",))
    rows[0]["status"] = "BLOCKED"
    rows[0]["blockers"] = ["RSU_SOURCE_LOAD_MISSING:1"]
    evidence = _write_evidence(tmp_path, rows, sources)
    with pytest.raises(LiraFormatError, match="BLOCKED"):
        _link_or_fail(tmp_path, model_pkg, evidence)


def test_wrong_evidence_kind_raises(tmp_path: Path) -> None:
    model_pkg, _ = _write_model_package(tmp_path, [_model_element("1")])
    sources = _rsu_sources(tmp_path)
    evidence = _write_evidence(tmp_path, _standard_rows(sources, elements=("1",)))
    payload = json.loads(evidence.read_text(encoding="utf-8"))
    payload["kind"] = "SOMETHING_ELSE"
    evidence.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    with pytest.raises(LiraMappingError, match="kind must be"):
        _link_or_fail(tmp_path, model_pkg, evidence)


def test_refuses_existing_output_dir(tmp_path: Path) -> None:
    model_pkg, _ = _write_model_package(tmp_path, [_model_element("1")])
    sources = _rsu_sources(tmp_path)
    evidence = _write_evidence(tmp_path, _standard_rows(sources, elements=("1",)))
    output = tmp_path / "linked"
    output.mkdir()
    with pytest.raises(LiraFormatError, match="refusing to overwrite"):
        prepare_linked_rsu_bundle(
            model_dir=model_pkg, evidence_path=evidence, output_dir=output
        )
