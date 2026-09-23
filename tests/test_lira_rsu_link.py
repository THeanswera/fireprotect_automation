from __future__ import annotations

import csv
import hashlib
import json
from decimal import Decimal
from pathlib import Path
from typing import Callable, Mapping

import pytest

from fireprotect.lira import (
    LiraFormatError,
    LiraMappingError,
    RsuModelLinkReport,
    RsuXlsMapping,
    assemble_lira_model,
    prepare_linked_rsu_bundle,
    read_element_table,
    read_node_table,
    read_rsu_evidence,
    read_stiffness_table,
)

COMPONENTS = ("N", "Mk", "My", "Mz", "Qy", "Qz")
BIFF = "BIFF_NUMERIC_VALUE_DECODED_NO_RAW_DECIMAL_TOKEN"

_PUBLISHED_CELLS = {"N": "F", "Mk": "G", "My": "H", "Qz": "I", "Mz": "J", "Qy": "K"}
_TERM_CELLS = {"N": "C", "Mk": "D", "My": "E", "Qz": "F", "Mz": "G", "Qy": "H"}


def _unit(component: str) -> str:
    return "tf*m" if component in ("Mk", "My", "Mz") else "tf"


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(65536), b""):
            digest.update(block)
    return digest.hexdigest()


def _write_biff(path: Path, sheets: list[tuple[str, list[list[object]]]]) -> None:
    """Write a real BIFF workbook; never a text file with an .xls suffix."""

    xlwt = pytest.importorskip("xlwt")
    path.parent.mkdir(parents=True, exist_ok=True)
    workbook = xlwt.Workbook()
    for name, rows in sheets:
        sheet = workbook.add_sheet(name)
        for row_index, row in enumerate(rows):
            for column_index, value in enumerate(row):
                sheet.write(row_index, column_index, value)
    workbook.save(str(path))


# --------------------------------------------------------------------------
# RSU plan and real BIFF sources
# --------------------------------------------------------------------------


def _rsu_plan(
    elements: tuple[str, ...] = ("1", "2"),
) -> tuple[list[dict[str, object]], dict[tuple[str, str], int]]:
    """Build the numeric plan shared by the XLS and the evidence JSON."""

    pair_row: dict[tuple[str, str], int] = {}
    pair_plan: dict[tuple[str, str], dict[str, Decimal]] = {}
    idx = 0
    pair_index = 0
    for element in elements:
        for station in ("1", "2"):
            idx += 1
            pair_index += 1
            pair_row[(element, station)] = 3 + pair_index  # data rows start at 4
            pair_plan[(element, station)] = {
                "N": Decimal(10 * idx),
                "My_force": Decimal(2 * idx),
                "Qz1": Decimal("1.5") * idx,
                "Qz2": Decimal("-3.5") * idx,
                "My": Decimal("0.9") * 2 * idx,
                "Qz": Decimal("1.5") * idx + Decimal("-3.15") * idx,
            }
    rows: list[dict[str, object]] = []
    for element in elements:
        for station in ("1", "2"):
            for group in ("A1", "B1"):
                for column in (1, 2):
                    plan = pair_plan[(element, station)]
                    rows.append(
                        {
                            "element_id": element,
                            "station": station,
                            "group": group,
                            "criterion": "13",
                            "column": column,
                            "membership": ["1", "2"],
                            "N": str(plan["N"]),
                            "My": str(plan["My"]),
                            "Qz": str(plan["Qz"]),
                            "force_N": str(plan["N"]),
                            "force_My": str(plan["My_force"]),
                            "force_Qz1": str(plan["Qz1"]),
                            "force_Qz2": str(plan["Qz2"]),
                        }
                    )
    return rows, pair_row


def _write_rsu_biff(
    tmp_path: Path,
    plan: list[dict[str, object]],
    pair_row: Mapping[tuple[str, str], int],
) -> dict[str, dict[str, object]]:
    base = tmp_path / "rsu_src"
    force_headers = [
        "№ элем", "№ сечен", "N\n(т)", "Mk\n(т*м)", "My\n(т*м)",
        "Qz\n(т)", "Mz\n(т*м)", "Qy\n(т)", "№ загруж",
    ]
    force_rows: dict[str, list[list[object]]] = {"1": [], "2": []}
    seen: set[tuple[str, str]] = set()
    for entry in plan:
        key = (str(entry["element_id"]), str(entry["station"]))
        if key in seen:
            continue
        seen.add(key)
        force_rows["1"].append(
            [
                int(key[0]), int(key[1]), float(entry["force_N"]), 0.0, 0.0,
                float(entry["force_Qz1"]), 0.0, 0.0, 1,
            ]
        )
        force_rows["2"].append(
            [
                int(key[0]), int(key[1]), 0.0, 0.0, float(entry["force_My"]),
                float(entry["force_Qz2"]), 0.0, 0.0, 2,
            ]
        )
    published_headers = [
        "№ элем", "№ сечен", "№ столбца", "Группа РСУ", "Критерий",
        "N\n(т)", "Mk\n(т*м)", "My\n(т*м)", "Qz\n(т)", "Mz\n(т*м)",
        "Qy\n(т)", "№№ загруж",
    ]
    published_rows = [
        [
            int(entry["element_id"]), int(entry["station"]), int(entry["column"]),
            entry["group"], entry["criterion"], float(entry["N"]), 0.0,
            float(entry["My"]), float(entry["Qz"]), 0.0, 0.0, "1 2",
        ]
        for entry in plan
    ]
    paths = {
        "forces": base / "forces.xls",
        "published": base / "published.xls",
        "coefficients": base / "coefficients.xls",
        "parameters": base / "parameters.xls",
    }
    _write_biff(
        paths["forces"],
        [
            ("1", [["title"], [], force_headers, *force_rows["1"]]),
            ("2", [["title"], [], force_headers, *force_rows["2"]]),
        ],
    )
    _write_biff(
        paths["published"],
        [(" ", [["title"], [], published_headers, *published_rows])],
    )
    _write_biff(
        paths["coefficients"],
        [
            (
                " ",
                [
                    ["title"],
                    [],
                    ["№ загр.", "1 основ.", "2 основ."],
                    [1, 1.0, 1.0],
                    [2, 0.9, 0.9],
                ],
            )
        ],
    )
    _write_biff(
        paths["parameters"],
        [
            (
                " ",
                [
                    ["title"],
                    [],
                    ["№ загр.", "Имя загружения", "Взаимоискл."],
                    [1, "G_DOWN", ""],
                    [2, "Q_LONG_DOWN", ""],
                ],
            )
        ],
    )
    entries: dict[str, dict[str, object]] = {}
    for name, path in paths.items():
        entries[name] = {
            "path": str(path),
            "sha256": _sha256_file(path),
            "sheets": 1,
        }
    return entries


def _evidence_value(
    value: str,
    component: str,
    *,
    sheet: str,
    row: int,
    cell: str,
    sha: str,
) -> dict[str, object]:
    unit = _unit(component)
    return {
        "value": value,
        "unit": unit,
        "header": f"{component}\n{'(т*м)' if unit == 'tf*m' else '(т)'}",
        "sheet": sheet,
        "row": row,
        "cell": cell,
        "source_sha256": sha,
        "raw_token": None,
        "decimal_provenance": BIFF,
    }


def _term(
    load_case_id: str,
    coefficient: str,
    force_values: Mapping[str, str],
    *,
    force_sheet: str,
    force_row: int,
    force_sha: str,
    coefficient_sha: str,
    column: int,
) -> dict[str, object]:
    forces = {
        name: _evidence_value(
            force_values.get(name, "0.0"),
            name,
            sheet=force_sheet,
            row=force_row,
            cell=f"{_TERM_CELLS[name]}{force_row}",
            sha=force_sha,
        )
        for name in COMPONENTS
    }
    coefficient_row = 4 if load_case_id == "1" else 5
    coefficient_cell = ("B" if column == 1 else "C") + str(coefficient_row)
    return {
        "load_case_id": load_case_id,
        "force_sheet": force_sheet,
        "force_row": force_row,
        "forces": forces,
        "coefficient": coefficient,
        "coefficient_source": {
            "sheet": " ",
            "row": coefficient_row,
            "cell": coefficient_cell,
            "header": "1 основ." if column == 1 else "2 основ.",
            "source_sha256": coefficient_sha,
            "raw_token": None,
            "decimal_provenance": BIFF,
        },
    }


def _evidence_row(
    entry: Mapping[str, object],
    row_id: str,
    *,
    row_number: int,
    force_row: int,
    published_sha: str,
    force_sha: str,
    coefficient_sha: str,
) -> dict[str, object]:
    column = int(entry["column"])
    terms = [
        _term(
            "1",
            "1.0",
            {"N": str(entry["force_N"]), "Qz": str(entry["force_Qz1"])},
            force_sheet="1",
            force_row=force_row,
            force_sha=force_sha,
            coefficient_sha=coefficient_sha,
            column=column,
        ),
        _term(
            "2",
            "0.9",
            {"My": str(entry["force_My"]), "Qz": str(entry["force_Qz2"])},
            force_sheet="2",
            force_row=force_row,
            force_sha=force_sha,
            coefficient_sha=coefficient_sha,
            column=column,
        ),
    ]
    published: dict[str, Decimal] = {name: Decimal("0") for name in COMPONENTS}
    for term in terms:
        for name in COMPONENTS:
            published[name] += Decimal(str(term["coefficient"])) * Decimal(
                str(term["forces"][name]["value"])
            )
    return {
        "row_id": row_id,
        "status": "VERIFIED",
        "blockers": [],
        "identity": {
            "element_id": str(entry["element_id"]),
            "section_station": str(entry["station"]),
            "rsu_group": str(entry["group"]),
            "rsu_criterion": str(entry["criterion"]),
            "rsu_column_number": column,
            "load_case_membership": list(entry["membership"]),
        },
        "source": {
            "sheet": " ",
            "row": row_number,
            "sha256": published_sha,
            "mapping_fingerprint": RsuXlsMapping().fingerprint,
        },
        "published_vector": {
            name: _evidence_value(
                str(published[name]),
                name,
                sheet=" ",
                row=row_number,
                cell=f"{_PUBLISHED_CELLS[name]}{row_number}",
                sha=published_sha,
            )
            for name in COMPONENTS
        },
        "reconstruction": {
            name: {
                "published": str(published[name]),
                "reconstructed": str(published[name]),
                "difference": "0",
            }
            for name in COMPONENTS
        },
        "source_terms": terms,
    }


def _evidence_payload(
    plan: list[dict[str, object]],
    pair_row: Mapping[tuple[str, str], int],
    sources: dict[str, dict[str, object]],
) -> dict[str, object]:
    published_sha = str(sources["published"]["sha256"])
    force_sha = str(sources["forces"]["sha256"])
    coefficient_sha = str(sources["coefficients"]["sha256"])
    rows = [
        _evidence_row(
            entry,
            f"R{index + 1:04d}",
            row_number=4 + index,
            force_row=pair_row[(str(entry["element_id"]), str(entry["station"]))],
            published_sha=published_sha,
            force_sha=force_sha,
            coefficient_sha=coefficient_sha,
        )
        for index, entry in enumerate(plan)
    ]
    unique_pairs = len(
        {(str(entry["element_id"]), str(entry["station"])) for entry in plan}
    )
    return {
        "kind": "READ_ONLY_LIRA_RSU_EVIDENCE",
        "status": "VERIFIED",
        "sources": sources,
        "mapping_fingerprint": RsuXlsMapping().fingerprint,
        "force_records": 2 * unique_pairs,
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
                "source_sha256": str(sources["parameters"]["sha256"]),
            },
            {
                "load_case_id": "2",
                "values": {},
                "sheet": " ",
                "row": 5,
                "source_sha256": str(sources["parameters"]["sha256"]),
            },
        ],
        "rows": rows,
        "governing_result_selection_validated": False,
        "rx38_force_generation_allowed": False,
        "issue_readiness": "NOT_READY_FOR_ISSUE",
    }


def _write_evidence(tmp_path: Path, payload: Mapping[str, object]) -> Path:
    path = tmp_path / "rsu_evidence.json"
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return path


def _edit_evidence(evidence: Path, mutate: Callable[[dict], None]) -> None:
    payload = json.loads(evidence.read_text(encoding="utf-8"))
    mutate(payload)
    evidence.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )


# --------------------------------------------------------------------------
# Model package over real BIFF sources
# --------------------------------------------------------------------------


def _write_model_biff(tmp_path: Path) -> dict[str, dict[str, object]]:
    base = tmp_path / "model_src"
    paths = {
        "stiffness": base / "stiffness.xls",
        "elements": base / "elements.xls",
        "nodes": base / "nodes.xls",
    }
    _write_biff(
        paths["stiffness"],
        [(" ", [["title"], ["Тип жесткости", "Имя"], [1, "Брус 10 X 20"]])],
    )
    _write_biff(
        paths["elements"],
        [
            (
                " ",
                [
                    ["title"],
                    [],
                    [
                        "№ элем", "Тип элем", "Кол.сечений", "Тип жестк",
                        "Угол м.осей", "AX н", "AX к", "№№ узлов",
                    ],
                    [1, 10, 2, 1, 0, "-", "-", "1,3"],
                    [2, 10, 2, 1, 0, "-", "-", "3,2"],
                ],
            )
        ],
    )
    _write_biff(
        paths["nodes"],
        [
            (
                " ",
                [
                    ["title"],
                    [],
                    [
                        "№ узла", "X\n(м)", "Y\n(м)", "Z\n(м)",
                        "X", "Y", "Z", "UX", "UY", "UZ",
                    ],
                    [1, 0, 0, 0, "+", "-", "+", "-", "-", "-"],
                    [2, 6, 0, 0, "-", "-", "+", "-", "-", "-"],
                    [3, 3, 0, 0, "-", "-", "-", "-", "-", "-"],
                ],
            )
        ],
    )
    entries: dict[str, dict[str, object]] = {}
    for name, path in paths.items():
        entries[name] = {
            "path": str(path),
            "sha256": _sha256_file(path),
            "sheet": " ",
            "header_row": "2" if name == "stiffness" else "3",
        }
    return entries


def _write_model_package(tmp_path: Path) -> tuple[Path, dict[str, dict[str, object]]]:
    package = tmp_path / "model_pkg"
    package.mkdir()
    entries = _write_model_biff(tmp_path)
    stiffnesses = read_stiffness_table(
        entries["stiffness"]["path"], sheet_name=" ", header_row=2
    )
    elements = read_element_table(
        entries["elements"]["path"], sheet_name=" ", header_row=3
    )
    nodes = read_node_table(entries["nodes"]["path"], sheet_name=" ", header_row=3)
    assembled = assemble_lira_model(
        stiffnesses=stiffnesses, elements=elements, nodes=nodes
    )
    manifest = {
        "status": "MODEL_ASSEMBLED",
        "sources": entries,
        "elements": len(assembled),
        "elements_blocked": len([item for item in assembled if not item.passed]),
        "stiffness_types": len(stiffnesses),
        "nodes": len(nodes),
        "profile_resolution": None,
        "ptm": None,
        "rx38_force_generation_allowed": False,
        "issue_readiness": "NOT_READY_FOR_ISSUE",
    }
    (package / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    (package / "elements.json").write_text(
        json.dumps(
            {"elements": [item.as_dict() for item in assembled], "marks": []},
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    return package, entries


def _edit_elements(package: Path, mutate: Callable[[dict], None]) -> None:
    path = package / "elements.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    mutate(payload)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )


def _link(tmp_path: Path, package: Path, evidence: Path) -> RsuModelLinkReport:
    return prepare_linked_rsu_bundle(
        model_dir=package, evidence_path=evidence, output_dir=tmp_path / "linked"
    )


def _expected_numbers(element: str, station: str) -> dict[str, Decimal]:
    idx = {"1": {"1": 1, "2": 2}, "2": {"1": 3, "2": 4}}[element][station]
    return {
        "N": Decimal(10 * idx),
        "Mk": Decimal("0"),
        "My": Decimal("0.9") * 2 * idx,
        "Mz": Decimal("0"),
        "Qy": Decimal("0"),
        "Qz": Decimal("1.5") * idx + Decimal("-3.15") * idx,
    }


# --------------------------------------------------------------------------
# Positive
# --------------------------------------------------------------------------


def test_link_joins_model_and_all_rsu_rows(tmp_path: Path) -> None:
    package, _ = _write_model_package(tmp_path)
    plan, pair_row = _rsu_plan()
    sources = _write_rsu_biff(tmp_path, plan, pair_row)
    evidence = _write_evidence(tmp_path, _evidence_payload(plan, pair_row, sources))

    report = _link(tmp_path, package, evidence)

    assert report.rows_total == 16
    assert report.components_preserved == 96
    assert report.elements_without_rows == ()

    output = tmp_path / "linked"
    manifest = json.loads((output / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["status"] == "MODEL_RSU_LINKED"
    basis = manifest["link_basis"]
    assert basis["comparison_key"] == "element_id"
    assert basis["historical_origin_claim"] == "NOT_INDEPENDENTLY_PROVEN"
    assert basis["rsu_evidence"]["path"] == str(evidence.resolve())
    assert basis["rsu_evidence"]["sha256"] == _sha256_file(evidence)
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
    assert basis["source_reread"]["model"]["elements"] == 2
    assert basis["source_reread"]["model"]["nodes"] == 3
    assert basis["source_reread"]["model"]["stiffness_types"] == 1
    assert basis["source_reread"]["model"]["geometry_verified"] is True
    assert basis["source_reread"]["model"]["supports_verified"] is True
    assert basis["source_reread"]["rsu"]["reconstruction_status"] == "VERIFIED"
    assert basis["source_reread"]["rsu"]["published_rows"] == 16
    assert basis["source_reread"]["rsu"]["force_records"] == 8
    assert basis["source_reread"]["rsu"]["matching_components"] == 96
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
        assert item["geometry"]["supports"]["start"]["signs"]["x"] == (
            "+" if element_id == "1" else "-"
        )
        assert item["geometry"]["supports"]["end"]["signs"]["z"] == (
            "-" if element_id == "1" else "+"
        )
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

    # All 96 components preserved byte-for-byte (identical to the evidence
    # strings) and numerically equal to independently derived values.
    evidence_payload = json.loads(evidence.read_text(encoding="utf-8"))
    source_rows = {
        str(row["row_id"]): row for row in evidence_payload["rows"]
    }
    checked = 0
    for item in elements:
        for row in item["rsu_rows"]:
            row_id = str(row["row_id"])
            source_row = source_rows[row_id]
            expected = _expected_numbers(
                str(row["identity"]["element_id"]),
                str(row["identity"]["section_station"]),
            )
            for component in COMPONENTS:
                assert (
                    row["published_vector"][component]["value"]
                    == source_row["published_vector"][component]["value"]
                )
                assert (
                    Decimal(row["published_vector"][component]["value"])
                    == expected[component]
                )
                checked += 1
    assert checked == 96

    # Negative and positive signs survive into the CSV with explicit units.
    with (output / "rsu_candidates.csv").open(encoding="utf-8", newline="") as stream:
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
    assert Decimal(by_row_id["R0005"][10]) == Decimal("20")
    assert by_row_id["R0005"][15] == "-3.30"
    assert by_row_id["R0016"][1] == "2"
    assert by_row_id["R0016"][15] == "-6.60"


def test_read_rsu_evidence_rechecks_sources_and_reconstruction(
    tmp_path: Path,
) -> None:
    plan, pair_row = _rsu_plan()
    sources = _write_rsu_biff(tmp_path, plan, pair_row)
    evidence = _write_evidence(tmp_path, _evidence_payload(plan, pair_row, sources))

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
    assert bundle.source_recheck["reconstruction_status"] == "VERIFIED"
    assert bundle.source_recheck["published_rows"] == 16
    assert bundle.source_recheck["matching_components"] == 96


def test_same_geometry_rows_stay_separate_candidates(tmp_path: Path) -> None:
    package, _ = _write_model_package(tmp_path)
    plan, pair_row = _rsu_plan()
    plan = [plan[0], plan[2]]  # same element and station, different groups
    assert plan[0]["station"] == plan[1]["station"]
    sources = _write_rsu_biff(tmp_path, plan, pair_row)
    evidence = _write_evidence(tmp_path, _evidence_payload(plan, pair_row, sources))

    report = _link(tmp_path, package, evidence)

    linked = json.loads(
        (tmp_path / "linked" / "linked_elements.json").read_text(encoding="utf-8")
    )
    linked_rows = linked["elements"][0]["rsu_rows"]
    assert len(linked_rows) == 2
    assert {row["row_id"] for row in linked_rows} == {"R0001", "R0002"}
    assert {row["identity"]["rsu_group"] for row in linked_rows} == {"A1", "B1"}
    assert report.rows_total == 2


def test_element_without_rsu_rows_is_reported(tmp_path: Path) -> None:
    package, _ = _write_model_package(tmp_path)
    plan, pair_row = _rsu_plan(elements=("1",))
    sources = _write_rsu_biff(tmp_path, plan, pair_row)
    evidence = _write_evidence(tmp_path, _evidence_payload(plan, pair_row, sources))

    report = _link(tmp_path, package, evidence)

    assert report.elements_without_rows == ("2",)
    linked = json.loads(
        (tmp_path / "linked" / "linked_elements.json").read_text(encoding="utf-8")
    )
    by_id = {str(item["element_id"]): item for item in linked["elements"]}
    assert by_id["2"]["rsu_rows"] == []
    manifest = json.loads(
        (tmp_path / "linked" / "manifest.json").read_text(encoding="utf-8")
    )
    assert manifest["links"]["elements_without_rsu_rows"] == ["2"]
    assert manifest["links"]["rows_per_element"] == {"1": 8, "2": 0}


# --------------------------------------------------------------------------
# Regression: JSON edited while the real BIFF sources stay unchanged
# --------------------------------------------------------------------------


def test_consistent_json_force_tamper_rejected(tmp_path: Path) -> None:
    """Scenario 1: My doubled consistently in JSON; the XLS files are intact.

    The recorded hashes still match and the JSON stays internally consistent,
    so only the re-read of the source tables can expose the edit.
    """

    package, _ = _write_model_package(tmp_path)
    plan, pair_row = _rsu_plan()
    sources = _write_rsu_biff(tmp_path, plan, pair_row)
    evidence = _write_evidence(tmp_path, _evidence_payload(plan, pair_row, sources))

    def mutate(payload: dict) -> None:
        row = next(r for r in payload["rows"] if r["row_id"] == "R0007")
        for term in row["source_terms"]:
            my = term["forces"]["My"]
            my["value"] = str(Decimal(my["value"]) * 2)
        vector = row["published_vector"]["My"]
        vector["value"] = str(Decimal(vector["value"]) * 2)
        reconstruction = row["reconstruction"]["My"]
        reconstruction["published"] = str(Decimal(reconstruction["published"]) * 2)
        reconstruction["reconstructed"] = str(
            Decimal(reconstruction["reconstructed"]) * 2
        )

    _edit_evidence(evidence, mutate)
    with pytest.raises(LiraMappingError, match="re-read source XLS"):
        _link(tmp_path, package, evidence)


@pytest.mark.parametrize("tamper", ("length", "supports"))
def test_model_json_tamper_rejected(tmp_path: Path, tamper: str) -> None:
    """Scenario 2: geometry edited in elements.json; the XLS files are intact."""

    package, _ = _write_model_package(tmp_path)
    plan, pair_row = _rsu_plan()
    sources = _write_rsu_biff(tmp_path, plan, pair_row)
    evidence = _write_evidence(tmp_path, _evidence_payload(plan, pair_row, sources))

    def mutate(payload: dict) -> None:
        element = next(e for e in payload["elements"] if e["element_id"] == "1")
        if tamper == "length":
            element["geometry"]["length_m"] = "30.0"
        else:
            element["geometry"]["supports"]["start"]["signs"]["z"] = "-"

    _edit_elements(package, mutate)
    with pytest.raises(LiraMappingError, match="length_m|supports.start.signs"):
        _link(tmp_path, package, evidence)


def test_global_blocked_evidence_rejected(tmp_path: Path) -> None:
    """Scenario 3: bundle-level BLOCKED with VERIFIED rows is rejected."""

    package, _ = _write_model_package(tmp_path)
    plan, pair_row = _rsu_plan()
    sources = _write_rsu_biff(tmp_path, plan, pair_row)
    evidence = _write_evidence(tmp_path, _evidence_payload(plan, pair_row, sources))

    def mutate(payload: dict) -> None:
        payload["status"] = "BLOCKED"

    _edit_evidence(evidence, mutate)
    with pytest.raises(LiraFormatError, match="globally BLOCKED"):
        _link(tmp_path, package, evidence)


def test_verified_bundle_with_recorded_blockers_rejected(tmp_path: Path) -> None:
    package, _ = _write_model_package(tmp_path)
    plan, pair_row = _rsu_plan()
    sources = _write_rsu_biff(tmp_path, plan, pair_row)
    evidence = _write_evidence(tmp_path, _evidence_payload(plan, pair_row, sources))

    def mutate(payload: dict) -> None:
        payload["blockers"] = ["RSU_RESULT_MISMATCH:My"]

    _edit_evidence(evidence, mutate)
    with pytest.raises(LiraFormatError, match="must record no blockers"):
        _link(tmp_path, package, evidence)


# --------------------------------------------------------------------------
# Negative
# --------------------------------------------------------------------------


def test_unknown_element_in_evidence_raises(tmp_path: Path) -> None:
    package, _ = _write_model_package(tmp_path)
    plan, pair_row = _rsu_plan()
    sources = _write_rsu_biff(tmp_path, plan, pair_row)
    evidence = _write_evidence(tmp_path, _evidence_payload(plan, pair_row, sources))

    def mutate(payload: dict) -> None:
        payload["rows"][0]["identity"]["element_id"] = "9"

    _edit_evidence(evidence, mutate)
    with pytest.raises(LiraMappingError, match="element_id"):
        _link(tmp_path, package, evidence)


def test_duplicate_row_id_raises(tmp_path: Path) -> None:
    package, _ = _write_model_package(tmp_path)
    plan, pair_row = _rsu_plan()
    sources = _write_rsu_biff(tmp_path, plan, pair_row)
    evidence = _write_evidence(tmp_path, _evidence_payload(plan, pair_row, sources))

    def mutate(payload: dict) -> None:
        payload["rows"][1]["row_id"] = payload["rows"][0]["row_id"]

    _edit_evidence(evidence, mutate)
    with pytest.raises(LiraFormatError, match="duplicate RSU row_id"):
        _link(tmp_path, package, evidence)


@pytest.mark.parametrize("station", ("0", "3", "1.5", "два"))
def test_invalid_section_station_raises(tmp_path: Path, station: str) -> None:
    package, _ = _write_model_package(tmp_path)
    plan, pair_row = _rsu_plan()
    sources = _write_rsu_biff(tmp_path, plan, pair_row)
    evidence = _write_evidence(tmp_path, _evidence_payload(plan, pair_row, sources))

    def mutate(payload: dict) -> None:
        payload["rows"][0]["identity"]["section_station"] = station

    _edit_evidence(evidence, mutate)
    with pytest.raises(LiraMappingError, match="section_station"):
        _link(tmp_path, package, evidence)


def test_model_without_section_count_raises(tmp_path: Path) -> None:
    package, _ = _write_model_package(tmp_path)
    plan, pair_row = _rsu_plan()
    sources = _write_rsu_biff(tmp_path, plan, pair_row)
    evidence = _write_evidence(tmp_path, _evidence_payload(plan, pair_row, sources))

    def mutate(payload: dict) -> None:
        element = next(e for e in payload["elements"] if e["element_id"] == "1")
        element["identity"]["section_count"] = None

    _edit_elements(package, mutate)
    with pytest.raises(LiraMappingError, match="section_count"):
        _link(tmp_path, package, evidence)


def test_incomplete_published_vector_raises(tmp_path: Path) -> None:
    package, _ = _write_model_package(tmp_path)
    plan, pair_row = _rsu_plan()
    sources = _write_rsu_biff(tmp_path, plan, pair_row)
    evidence = _write_evidence(tmp_path, _evidence_payload(plan, pair_row, sources))

    def mutate(payload: dict) -> None:
        del payload["rows"][0]["published_vector"]["Qz"]

    _edit_evidence(evidence, mutate)
    with pytest.raises(LiraFormatError, match="force vector must contain exactly"):
        _link(tmp_path, package, evidence)


def test_incomplete_term_vector_raises(tmp_path: Path) -> None:
    package, _ = _write_model_package(tmp_path)
    plan, pair_row = _rsu_plan()
    sources = _write_rsu_biff(tmp_path, plan, pair_row)
    evidence = _write_evidence(tmp_path, _evidence_payload(plan, pair_row, sources))

    def mutate(payload: dict) -> None:
        del payload["rows"][0]["source_terms"][0]["forces"]["Mz"]

    _edit_evidence(evidence, mutate)
    with pytest.raises(LiraFormatError, match="force vector must contain exactly"):
        _link(tmp_path, package, evidence)


def test_changed_model_source_raises(tmp_path: Path) -> None:
    package, entries = _write_model_package(tmp_path)
    plan, pair_row = _rsu_plan()
    sources = _write_rsu_biff(tmp_path, plan, pair_row)
    evidence = _write_evidence(tmp_path, _evidence_payload(plan, pair_row, sources))
    Path(str(entries["nodes"]["path"])).write_bytes(b"tampered-nodes")
    with pytest.raises(LiraMappingError, match="changed"):
        _link(tmp_path, package, evidence)


def test_changed_rsu_source_raises(tmp_path: Path) -> None:
    package, _ = _write_model_package(tmp_path)
    plan, pair_row = _rsu_plan()
    sources = _write_rsu_biff(tmp_path, plan, pair_row)
    evidence = _write_evidence(tmp_path, _evidence_payload(plan, pair_row, sources))
    Path(str(sources["published"]["path"])).write_bytes(b"tampered-published")
    with pytest.raises(LiraMappingError, match="changed"):
        _link(tmp_path, package, evidence)


def test_reconstruction_mismatch_raises(tmp_path: Path) -> None:
    package, _ = _write_model_package(tmp_path)
    plan, pair_row = _rsu_plan()
    sources = _write_rsu_biff(tmp_path, plan, pair_row)
    evidence = _write_evidence(tmp_path, _evidence_payload(plan, pair_row, sources))

    def mutate(payload: dict) -> None:
        row = payload["rows"][0]
        row["published_vector"]["N"]["value"] = "999"
        row["reconstruction"]["N"]["published"] = "999"

    _edit_evidence(evidence, mutate)
    with pytest.raises(LiraFormatError, match="not reproduced from the source terms"):
        _link(tmp_path, package, evidence)


def test_blocked_row_raises(tmp_path: Path) -> None:
    package, _ = _write_model_package(tmp_path)
    plan, pair_row = _rsu_plan()
    sources = _write_rsu_biff(tmp_path, plan, pair_row)
    evidence = _write_evidence(tmp_path, _evidence_payload(plan, pair_row, sources))

    def mutate(payload: dict) -> None:
        payload["rows"][0]["status"] = "BLOCKED"
        payload["rows"][0]["blockers"] = ["RSU_SOURCE_LOAD_MISSING:1"]

    _edit_evidence(evidence, mutate)
    with pytest.raises(LiraFormatError, match="BLOCKED"):
        _link(tmp_path, package, evidence)


def test_wrong_evidence_kind_raises(tmp_path: Path) -> None:
    package, _ = _write_model_package(tmp_path)
    plan, pair_row = _rsu_plan()
    sources = _write_rsu_biff(tmp_path, plan, pair_row)
    evidence = _write_evidence(tmp_path, _evidence_payload(plan, pair_row, sources))

    def mutate(payload: dict) -> None:
        payload["kind"] = "SOMETHING_ELSE"

    _edit_evidence(evidence, mutate)
    with pytest.raises(LiraMappingError, match="kind must be"):
        _link(tmp_path, package, evidence)


def test_unknown_mapping_fingerprint_raises(tmp_path: Path) -> None:
    package, _ = _write_model_package(tmp_path)
    plan, pair_row = _rsu_plan()
    sources = _write_rsu_biff(tmp_path, plan, pair_row)
    evidence = _write_evidence(tmp_path, _evidence_payload(plan, pair_row, sources))

    def mutate(payload: dict) -> None:
        payload["mapping_fingerprint"] = "f" * 64
        for row in payload["rows"]:
            row["source"]["mapping_fingerprint"] = "f" * 64

    _edit_evidence(evidence, mutate)
    with pytest.raises(LiraMappingError, match="refusing to guess"):
        _link(tmp_path, package, evidence)


def test_refuses_existing_output_dir(tmp_path: Path) -> None:
    package, _ = _write_model_package(tmp_path)
    plan, pair_row = _rsu_plan()
    sources = _write_rsu_biff(tmp_path, plan, pair_row)
    evidence = _write_evidence(tmp_path, _evidence_payload(plan, pair_row, sources))
    output = tmp_path / "linked"
    output.mkdir()
    with pytest.raises(LiraFormatError, match="refusing to overwrite"):
        prepare_linked_rsu_bundle(
            model_dir=package, evidence_path=evidence, output_dir=output
        )
