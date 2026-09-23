from __future__ import annotations

import hashlib
import json
from decimal import Decimal
from pathlib import Path
from typing import Mapping

import pytest

from fireprotect.lira import (
    LiraFormatError,
    LiraMappingError,
    RsuXlsMapping,
    assemble_lira_model,
    declaration_template,
    prepare_bar_experiment_input,
    prepare_linked_rsu_bundle,
    read_element_table,
    read_node_table,
    read_stiffness_table,
    write_declaration_template,
)

COMPONENTS = ("N", "Mk", "My", "Mz", "Qy", "Qz")
BIFF = "BIFF_NUMERIC_VALUE_DECODED_NO_RAW_DECIMAL_TOKEN"
PUBLISHED_CELLS = {"N": "F", "Mk": "G", "My": "H", "Qz": "I", "Mz": "J", "Qy": "K"}
TERM_CELLS = {"N": "C", "Mk": "D", "My": "E", "Qz": "F", "Mz": "G", "Qy": "H"}


def _unit(component: str) -> str:
    return "tf*m" if component in ("Mk", "My", "Mz") else "tf"


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(65536), b""):
            digest.update(block)
    return digest.hexdigest()


def _write_biff(path: Path, sheets: list[tuple[str, list[list[object]]]]) -> None:
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
# Steel teaching model over real BIFF sources
# --------------------------------------------------------------------------


def _make_steel_model(
    tmp_path: Path,
    *,
    stiffness_rows: list[list[object]] | None = None,
    element_rows: list[list[object]] | None = None,
    node_rows: list[list[object]] | None = None,
) -> tuple[Path, dict[str, dict[str, object]]]:
    base = tmp_path / "model_src"
    paths = {
        "stiffness": base / "stiffness.xls",
        "elements": base / "elements.xls",
        "nodes": base / "nodes.xls",
    }
    _write_biff(
        paths["stiffness"],
        [
            (
                " ",
                [
                    ["title"],
                    ["Тип жесткости", "Имя"],
                    *(stiffness_rows or [[1, "Швеллер 22П (Б2)"]]),
                ],
            )
        ],
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
                    *(element_rows or [
                        [1, 10, 2, 1, 0, "-", "-", "1,3"],
                        [2, 10, 2, 1, 0, "-", "-", "3,2"],
                    ]),
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
                    *(node_rows or [
                        [1, 0, 0, 0, "+", "-", "+", "-", "-", "-"],
                        [3, 1.5, 0, 0, "-", "-", "-", "-", "-", "-"],
                        [2, 3, 0, 0, "-", "-", "+", "-", "-", "-"],
                    ]),
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
    package = tmp_path / "model_pkg"
    package.mkdir()
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


# --------------------------------------------------------------------------
# Compact RSU evidence over real BIFF sources
# --------------------------------------------------------------------------


def _default_plan() -> tuple[list[dict[str, object]], dict[tuple[str, str], int]]:
    rows: list[dict[str, object]] = []
    for index, element in enumerate(("1", "2"), start=1):
        rows.append(
            {
                "element_id": element,
                "station": "2",
                "group": "B1",
                "criterion": "1",
                "column": 2,
                "membership": ["1", "2"],
                "N": "0",
                "My": str(Decimal("0.9") * 2 * index),
                "Qz": str(Decimal("1.5") * index + Decimal("-3.15") * index),
                "force_N": "0",
                "force_My": str(Decimal(2 * index)),
                "force_Qz1": str(Decimal("1.5") * index),
                "force_Qz2": str(Decimal("-3.5") * index),
            }
        )
    return rows, {("1", "2"): 4, ("2", "2"): 5}


def _write_rsu_biff(
    tmp_path: Path,
    plan: list[dict[str, object]],
) -> dict[str, dict[str, object]]:
    base = tmp_path / "rsu_src"
    force_headers = [
        "№ элем", "№ сечен", "N\n(т)", "Mk\n(т*м)", "My\n(т*м)",
        "Qz\n(т)", "Mz\n(т*м)", "Qy\n(т)", "№ загруж",
    ]
    force_rows: dict[str, list[list[object]]] = {"1": [], "2": []}
    for entry in plan:
        element = int(entry["element_id"])
        station = int(entry["station"])
        force_rows["1"].append(
            [
                element, station, float(entry["force_N"]), 0.0, 0.0,
                float(entry["force_Qz1"]), 0.0, 0.0, 1,
            ]
        )
        force_rows["2"].append(
            [
                element, station, 0.0, 0.0, float(entry["force_My"]),
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
                    [3, "Q_ALT_DOWN", ""],
                    [4, "Q_ALT_UP", ""],
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


def _value(
    value: str, component: str, *, sheet: str, row: int, cell: str, sha: str
) -> dict[str, object]:
    return {
        "value": value,
        "unit": _unit(component),
        "header": f"{component}\n{'(т*м)' if _unit(component) == 'tf*m' else '(т)'}",
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
        name: _value(
            force_values.get(name, "0.0"),
            name,
            sheet=force_sheet,
            row=force_row,
            cell=f"{TERM_CELLS[name]}{force_row}",
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
            name: _value(
                str(published[name]),
                name,
                sheet=" ",
                row=row_number,
                cell=f"{PUBLISHED_CELLS[name]}{row_number}",
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
            force_row=4 + index,
            published_sha=published_sha,
            force_sha=force_sha,
            coefficient_sha=coefficient_sha,
        )
        for index, entry in enumerate(plan)
    ]
    return {
        "kind": "READ_ONLY_LIRA_RSU_EVIDENCE",
        "status": "VERIFIED",
        "sources": sources,
        "mapping_fingerprint": RsuXlsMapping().fingerprint,
        "force_records": 2 * len(plan),
        "published_records": len(rows),
        "component_comparisons": 6 * len(rows),
        "matching_components": 6 * len(rows),
        "blockers": [],
        "load_parameters": [
            {
                "load_case_id": "1",
                "values": {"№ загр.": "1.0", "Имя загружения": "G_DOWN", "Взаимоискл.": None},
                "sheet": " ",
                "row": 4,
                "source_sha256": str(sources["parameters"]["sha256"]),
            },
            {
                "load_case_id": "2",
                "values": {"№ загр.": "2.0", "Имя загружения": "Q_LONG_DOWN", "Взаимоискл.": None},
                "sheet": " ",
                "row": 5,
                "source_sha256": str(sources["parameters"]["sha256"]),
            },
            {
                "load_case_id": "3",
                "values": {"№ загр.": "3.0", "Имя загружения": "Q_ALT_DOWN", "Взаимоискл.": None},
                "sheet": " ",
                "row": 6,
                "source_sha256": str(sources["parameters"]["sha256"]),
            },
            {
                "load_case_id": "4",
                "values": {"№ загр.": "4.0", "Имя загружения": "Q_ALT_UP", "Взаимоискл.": None},
                "sheet": " ",
                "row": 7,
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


def _make_linked(tmp_path: Path) -> tuple[Path, dict[str, dict[str, object]]]:
    package, entries = _make_steel_model(tmp_path)
    plan, _ = _default_plan()
    sources = _write_rsu_biff(tmp_path, plan)
    evidence = _write_evidence(tmp_path, _evidence_payload(plan, sources))
    linked = prepare_linked_rsu_bundle(
        model_dir=package, evidence_path=evidence, output_dir=tmp_path / "linked"
    )
    return linked.output_dir, entries


def _declaration_payload(
    linked_dir: Path,
    *,
    row_id: str = "R0001",
    element_ids: tuple[str, ...] = ("1", "2"),
    end_node_ids: tuple[str, ...] = ("1", "2"),
) -> dict[str, object]:
    return {
        "declaration_kind": "LIRA_BAR_EXPERIMENT_DECLARATION",
        "linked_manifest_sha256": _sha256_file(linked_dir / "manifest.json"),
        "bar": {
            "bar_id": "B1",
            "element_ids": list(element_ids),
            "end_node_ids": list(end_node_ids),
            "join_basis": "два КЭ одного прямого стержня, общий узел 3",
            "confirmed_by": "Инженер",
        },
        "experiment_row": {
            "row_id": row_id,
            "selection_basis": "строка технического опыта, не governing",
            "selected_by": "Инженер",
        },
        "profile": {
            "kind_word": "Швеллер",
            "designation": "22П",
            "mark": "Б2",
            "standard": "ГОСТ 8240-97",
            "rotation_degrees": "0",
            "plane": "X-Z",
            "scheme_flag": "2",
            "rx3_template": "Б2",
            "stress_state": "ONE_PLANE_BENDING",
            "confirmed_by": "Инженер",
            "basis": "учебная схема",
        },
        "design_conditions": {
            "effective_length_m": None,
            "support_condition": None,
            "heating_sides": None,
            "fire_regime": None,
            "required_fire_resistance_min": None,
        },
    }


def _write_declaration(
    tmp_path: Path, payload: Mapping[str, object]
) -> Path:
    path = tmp_path / "declaration.json"
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return path


def _run(
    tmp_path: Path, linked: Path, declaration: Path
) -> dict[str, object]:
    return prepare_bar_experiment_input(
        linked_dir=linked,
        declaration_path=declaration,
        output_dir=tmp_path / "experiment",
    )


# --------------------------------------------------------------------------
# Positive
# --------------------------------------------------------------------------


def test_prepares_ready_experiment_input(tmp_path: Path) -> None:
    linked, _ = _make_linked(tmp_path)
    declaration = _write_declaration(
        tmp_path, _declaration_payload(linked)
    )

    manifest = _run(tmp_path, linked, declaration)

    assert manifest["kind"] == "LIRA_BAR_EXPERIMENT_INPUT"
    assert manifest["status"] == "EXPERIMENT_INPUT_READY"
    assert manifest["bar"]["bar_id"] == "B1"
    assert manifest["bar"]["ordered_element_ids"] == ["1", "2"]
    assert manifest["bar"]["element_lengths_m"] == {"1": "1.5", "2": "1.5"}
    assert manifest["bar"]["bar_length_m"] == "3.0"
    assert manifest["bar"]["end_node_ids"] == ["1", "2"]
    assert manifest["selected_row"]["row_id"] == "R0001"
    assert manifest["selected_row"]["element_id"] == "1"
    assert manifest["selected_row"]["section_station"] == "2"
    for name in (
        "linked_manifest_bound",
        "model_package_matches_linked",
        "sources_reverified",
        "chain_connected",
        "chain_straight",
        "chain_without_branching",
        "rotation_consistent_zero",
        "profile_consistent",
        "selected_row_in_bar",
    ):
        assert manifest["checks"][name] is True
    assert manifest["blockers"] == []
    assert manifest["transfer_blocked"] is False
    assert manifest["missing_confirmations"] == [
        "effective_length_m",
        "support_condition",
        "heating_sides",
        "fire_regime",
        "required_fire_resistance_min",
    ]
    assert manifest["governing_result_selection"] is None
    assert manifest["rx38_created"] is False
    assert manifest["release_forbidden"] is True
    assert manifest["issue_readiness"] == "NOT_READY_FOR_ISSUE"

    components = {item["component"]: item for item in manifest["components"]}
    # Full signed vector present; only My/Qz nonzero and resolved.
    assert Decimal(components["N"]["source_value"]) == Decimal("0")
    assert Decimal(components["My"]["source_value"]) == Decimal("1.8")
    assert Decimal(components["Qz"]["source_value"]) == Decimal("-1.65")
    assert components["My"]["convention"]["resolved"] is True
    assert components["My"]["convention"]["target"] == "FIELD50_MAX_MAJOR_AXIS_MOMENT"
    assert components["My"]["convention"]["value_transform"] == "MAGNITUDE"
    assert components["Qz"]["convention"]["resolved"] is True
    assert components["Qz"]["convention"]["target"] == "FIELD92_MAX_SHEAR_Q"
    assert Decimal(components["My"]["review_value"]) == Decimal("1.8") * Decimal(
        "9.80665"
    )
    assert Decimal(components["Qz"]["review_value"]) == Decimal("-1.65") * Decimal(
        "9.80665"
    )
    assert components["My"]["review_unit"] == "kN*m"
    assert components["Qz"]["review_unit"] == "kN"

    output = tmp_path / "experiment"
    card = (output / "experiment_card.md").read_text(encoding="utf-8")
    assert "B1" in card
    assert "3.0" in card
    assert "R0001" in card
    assert "RX38 не создан" in card
    assert "не разрешение на расчёт RX3" in card
    detail = json.loads(
        (output / "experiment_input.json").read_text(encoding="utf-8")
    )
    assert len(detail["bar"]["elements"]) == 2
    assert detail["selected_row"]["row"]["row_id"] == "R0001"
    assert (
        detail["selected_row"]["row"]["published_vector"]["Qz"]["value"] == "-1.65"
    )
    template = json.loads(
        (output / "declaration_template.json").read_text(encoding="utf-8")
    )
    assert template["design_conditions"]["effective_length_m"] is None
    assert template["bar"]["element_ids"] == []


# --------------------------------------------------------------------------
# Negative
# --------------------------------------------------------------------------


def test_foreign_row_rejected(tmp_path: Path) -> None:
    linked, _ = _make_linked(tmp_path)
    declaration = _write_declaration(
        tmp_path,
        _declaration_payload(
            linked,
            row_id="R0002",  # belongs to element 2
            element_ids=("1",),
            end_node_ids=("1", "3"),
        ),
    )
    with pytest.raises(LiraMappingError, match="not part of the declared bar"):
        _run(tmp_path, linked, declaration)


def test_disconnected_elements_rejected(tmp_path: Path) -> None:
    package, _ = _make_steel_model(
        tmp_path,
        element_rows=[
            [1, 10, 2, 1, 0, "-", "-", "1,3"],
            [2, 10, 2, 1, 0, "-", "-", "4,5"],
        ],
        node_rows=[
            [1, 0, 0, 0, "+", "-", "+", "-", "-", "-"],
            [3, 1.5, 0, 0, "-", "-", "-", "-", "-", "-"],
            [4, 5, 0, 0, "-", "-", "-", "-", "-", "-"],
            [5, 6.5, 0, 0, "-", "-", "-", "-", "-", "-"],
        ],
    )
    plan, _ = _default_plan()
    sources = _write_rsu_biff(tmp_path, plan)
    evidence = _write_evidence(tmp_path, _evidence_payload(plan, sources))
    linked_report = prepare_linked_rsu_bundle(
        model_dir=package, evidence_path=evidence, output_dir=tmp_path / "linked"
    )
    declaration = _write_declaration(
        tmp_path,
        _declaration_payload(
            linked_report.output_dir, end_node_ids=("1", "5")
        ),
    )
    with pytest.raises(LiraMappingError, match="connected chain"):
        _run(tmp_path, linked_report.output_dir, declaration)


def test_inconsistent_profile_rejected(tmp_path: Path) -> None:
    package, _ = _make_steel_model(
        tmp_path,
        stiffness_rows=[[1, "Швеллер 22П (Б2)"], [2, "Уголок 80 x 6 (Сг1)"]],
        element_rows=[
            [1, 10, 2, 1, 0, "-", "-", "1,3"],
            [2, 10, 2, 2, 0, "-", "-", "3,2"],
        ],
    )
    plan, _ = _default_plan()
    sources = _write_rsu_biff(tmp_path, plan)
    evidence = _write_evidence(tmp_path, _evidence_payload(plan, sources))
    linked_report = prepare_linked_rsu_bundle(
        model_dir=package, evidence_path=evidence, output_dir=tmp_path / "linked"
    )
    declaration = _write_declaration(
        tmp_path, _declaration_payload(linked_report.output_dir)
    )
    with pytest.raises(LiraMappingError, match="differing profiles"):
        _run(tmp_path, linked_report.output_dir, declaration)


def test_inconsistent_rotation_rejected(tmp_path: Path) -> None:
    package, _ = _make_steel_model(
        tmp_path,
        element_rows=[
            [1, 10, 2, 1, 0, "-", "-", "1,3"],
            [2, 10, 2, 1, 1, "-", "-", "3,2"],
        ],
    )
    plan, _ = _default_plan()
    sources = _write_rsu_biff(tmp_path, plan)
    evidence = _write_evidence(tmp_path, _evidence_payload(plan, sources))
    linked_report = prepare_linked_rsu_bundle(
        model_dir=package, evidence_path=evidence, output_dir=tmp_path / "linked"
    )
    declaration = _write_declaration(
        tmp_path, _declaration_payload(linked_report.output_dir)
    )
    with pytest.raises(LiraMappingError, match="zero local-axis rotation"):
        _run(tmp_path, linked_report.output_dir, declaration)


def test_source_substitution_rejected(tmp_path: Path) -> None:
    linked, entries = _make_linked(tmp_path)
    Path(str(entries["nodes"]["path"])).write_bytes(b"tampered-nodes")
    declaration = _write_declaration(
        tmp_path, _declaration_payload(linked)
    )
    with pytest.raises(LiraMappingError, match="changed"):
        _run(tmp_path, linked, declaration)


def test_unsupported_nonzero_component_blocks_transfer(tmp_path: Path) -> None:
    package, _ = _make_steel_model(tmp_path)
    plan, _ = _default_plan()
    plan[0] = {
        **plan[0],
        "force_N": "5",  # nonzero axial force in load case 1
        "N": "5",
    }
    sources = _write_rsu_biff(tmp_path, plan)
    evidence = _write_evidence(tmp_path, _evidence_payload(plan, sources))
    linked_report = prepare_linked_rsu_bundle(
        model_dir=package, evidence_path=evidence, output_dir=tmp_path / "linked"
    )
    declaration = _write_declaration(
        tmp_path, _declaration_payload(linked_report.output_dir)
    )
    manifest = _run(tmp_path, linked_report.output_dir, declaration)
    assert manifest["status"] == "EXPERIMENT_INPUT_BLOCKED"
    assert manifest["transfer_blocked"] is True
    assert "LIRA_EXPERIMENT_UNSUPPORTED_COMPONENT:N" in manifest["blockers"]
    components = {item["component"]: item for item in manifest["components"]}
    assert Decimal(components["N"]["source_value"]) == Decimal("5")
    assert manifest["rx38_created"] is False


def test_missing_profile_confirmation_rejected(tmp_path: Path) -> None:
    linked, _ = _make_linked(tmp_path)
    payload = _declaration_payload(linked)
    payload["profile"]["confirmed_by"] = ""
    declaration = _write_declaration(tmp_path, payload)
    with pytest.raises(LiraMappingError, match="confirmed_by"):
        _run(tmp_path, linked, declaration)


def test_declaration_bound_to_wrong_revision_rejected(tmp_path: Path) -> None:
    linked, _ = _make_linked(tmp_path)
    payload = _declaration_payload(linked)
    payload["linked_manifest_sha256"] = "f" * 64
    declaration = _write_declaration(tmp_path, payload)
    with pytest.raises(LiraMappingError, match="different evidence revision"):
        _run(tmp_path, linked, declaration)


def test_refuses_existing_output_dir(tmp_path: Path) -> None:
    linked, _ = _make_linked(tmp_path)
    declaration = _write_declaration(
        tmp_path, _declaration_payload(linked)
    )
    output = tmp_path / "experiment"
    output.mkdir()
    with pytest.raises(LiraFormatError, match="refusing to overwrite"):
        prepare_bar_experiment_input(
            linked_dir=linked,
            declaration_path=declaration,
            output_dir=output,
        )


def test_evidence_row_id_swap_rejected(tmp_path: Path) -> None:
    """Swapping two correct row_ids changes the evidence revision.

    Both rows stay individually consistent with the unchanged XLS, so only the
    recorded evidence SHA-256 can expose the swap.
    """

    linked, _ = _make_linked(tmp_path)
    declaration = _write_declaration(
        tmp_path, _declaration_payload(linked)
    )
    linked_manifest = json.loads(
        (linked / "manifest.json").read_text(encoding="utf-8")
    )
    evidence_path = Path(linked_manifest["link_basis"]["rsu_evidence"]["path"])
    payload = json.loads(evidence_path.read_text(encoding="utf-8"))
    rows = payload["rows"]
    rows[0]["row_id"], rows[1]["row_id"] = rows[1]["row_id"], rows[0]["row_id"]
    evidence_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    output = tmp_path / "experiment"
    with pytest.raises(LiraMappingError, match="RSU evidence"):
        _run(tmp_path, linked, declaration)
    assert not output.exists()


def test_missing_recorded_evidence_sha_rejected(tmp_path: Path) -> None:
    linked, _ = _make_linked(tmp_path)
    manifest_path = linked / "manifest.json"
    payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    del payload["link_basis"]["rsu_evidence"]["sha256"]
    manifest_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    declaration = _write_declaration(
        tmp_path, _declaration_payload(linked)
    )
    with pytest.raises(LiraMappingError, match="no usable sha256"):
        _run(tmp_path, linked, declaration)


def test_reversed_element_orientation_rejected(tmp_path: Path) -> None:
    """A chain 1→3, 2→3 keeps connectivity but reverses one source element."""

    package, _ = _make_steel_model(
        tmp_path,
        element_rows=[
            [1, 10, 2, 1, 0, "-", "-", "1,3"],
            [2, 10, 2, 1, 0, "-", "-", "2,3"],
        ],
    )
    plan, _ = _default_plan()
    sources = _write_rsu_biff(tmp_path, plan)
    evidence = _write_evidence(tmp_path, _evidence_payload(plan, sources))
    linked_report = prepare_linked_rsu_bundle(
        model_dir=package, evidence_path=evidence, output_dir=tmp_path / "linked"
    )
    declaration = _write_declaration(
        tmp_path, _declaration_payload(linked_report.output_dir)
    )
    with pytest.raises(LiraMappingError, match="opposite"):
        _run(tmp_path, linked_report.output_dir, declaration)


def test_write_declaration_template_and_no_overwrite(tmp_path: Path) -> None:
    target = tmp_path / "declaration.json"
    written = write_declaration_template(target)
    assert written == target.resolve()
    payload = json.loads(target.read_text(encoding="utf-8"))
    assert payload == declaration_template()
    assert payload["declaration_kind"] == "LIRA_BAR_EXPERIMENT_DECLARATION"
    assert payload["linked_manifest_sha256"] == ""
    assert payload["bar"]["element_ids"] == []
    assert payload["bar"]["confirmed_by"] == ""
    assert payload["profile"]["confirmed_by"] == ""
    assert payload["profile"]["stress_state"] is None
    assert all(
        value is None for value in payload["design_conditions"].values()
    )
    with pytest.raises(LiraFormatError, match="refusing to overwrite"):
        write_declaration_template(target)
