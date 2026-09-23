from __future__ import annotations

from decimal import Decimal
from pathlib import Path
from typing import Mapping

import openpyxl
import pytest

from fireprotect.lira import (
    LiraElement,
    LiraFormatError,
    LiraNode,
    LiraStiffness,
    assemble_lira_model,
    element_length_m,
    parse_stiffness_name,
    prepare_lira_model_bundle,
    read_element_table,
    read_node_table,
    read_stiffness_table,
)


def _stiffness(type_id: str, name: str) -> LiraStiffness:
    kind, designation, mark = parse_stiffness_name(name)
    return LiraStiffness(
        type_id=type_id,
        raw_name=name,
        kind_word=kind,
        designation=designation,
        mark=mark,
        parameters=(),
        source_row=3,
        source_cell=f"B{3}",
    )


def _element(
    element_id: str,
    *,
    stiffness_type: str | None = "1",
    nodes: tuple[str, ...] = ("1", "2"),
    rotation: str | None = "0",
) -> LiraElement:
    return LiraElement(
        element_id=element_id,
        element_type="10",
        section_count="2",
        stiffness_type=stiffness_type,
        rotation_angle_degrees=None if rotation is None else Decimal(rotation),
        rigid_insert_start="-",
        rigid_insert_end="-",
        node_ids=nodes,
        source_row=4,
    )


def _node(
    node_id: str,
    x: str | None,
    y: str | None,
    z: str | None,
    *,
    supports: Mapping[str, str] | None = None,
) -> LiraNode:
    return LiraNode(
        node_id=node_id,
        x=None if x is None else Decimal(x),
        y=None if y is None else Decimal(y),
        z=None if z is None else Decimal(z),
        supports={"x": "+", "y": "+"} if supports is None else supports,
        source_row=4,
    )


# --------------------------------------------------------------------------
# Stiffness name parsing
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("raw", "expected"),
    (
        ("Двутавр 30К1 (К1)", ("Двутавр", "30К1", "К1")),
        ("  Двутавр 20К1 (К2)", ("Двутавр", "20К1", "К2")),
        ("Швеллер 22П (Б2)", ("Швеллер", "22П", "Б2")),
        (
            "Уголок параллельно полкам 80 x 6 (Сг1)",
            ("Уголок", "параллельно полкам 80 x 6", "Сг1"),
        ),
        ('Профиль "Молодечно" 120 x 6 (К5)', ("Профиль", '"Молодечно" 120 x 6', "К5")),
        ("Двутавр 30К1", ("Двутавр", "30К1", None)),
        ("Двутавр (К1)", ("Двутавр", None, "К1")),
        ("   ", (None, None, None)),
    ),
)
def test_parse_stiffness_name(
    raw: str, expected: tuple[str | None, str | None, str | None]
) -> None:
    assert parse_stiffness_name(raw) == expected


def test_kind_word_survives_identical_numbers() -> None:
    angle = parse_stiffness_name("Уголок параллельно полкам 80 x 6 (Сг1)")
    tube = parse_stiffness_name('Профиль "Молодечно" 80 x 6 (Рс1)')
    assert angle[0] == "Уголок"
    assert tube[0] == "Профиль"
    assert angle != tube


# --------------------------------------------------------------------------
# Length
# --------------------------------------------------------------------------


def test_length_of_a_three_four_five_element() -> None:
    start = _node("1", "0", "0", "0")
    end = _node("2", "3", "4", "0")
    assert element_length_m(start, end) == Decimal("5.000000000000000000000000000")


def test_length_includes_the_third_axis() -> None:
    start = _node("1", "0", "0", "0")
    end = _node("2", "0", "0", "4.5")
    assert element_length_m(start, end) == Decimal("4.5")


def test_length_refuses_a_missing_coordinate() -> None:
    start = _node("1", "0", "0", "0")
    end = _node("2", "0", None, "0")
    with pytest.raises(LiraFormatError, match="do not both carry coordinates"):
        element_length_m(start, end)


# --------------------------------------------------------------------------
# Assembly
# --------------------------------------------------------------------------


def test_assembly_joins_stiffness_nodes_and_length() -> None:
    stiffnesses = (_stiffness("1", "Двутавр 30К1 (К1)"),)
    elements = (_element("6", nodes=("4", "58")),)
    nodes = (_node("4", "4", "0", "0"), _node("58", "4", "0", "4.5"))
    assembled = assemble_lira_model(
        stiffnesses=stiffnesses, elements=elements, nodes=nodes
    )
    assert len(assembled) == 1
    item = assembled[0]
    assert item.passed
    assert item.mark == "К1"
    assert item.kind_word == "Двутавр"
    assert item.designation == "30К1"
    assert item.length_m == Decimal("4.5")
    assert item.supports_start == {"x": "+", "y": "+"}
    assert item.supports_end == {"x": "+", "y": "+"}
    assert item.as_dict()["geometry"]["supports"] == {
        "start": {"node_id": "4", "signs": {"x": "+", "y": "+"}},
        "end": {"node_id": "58", "signs": {"x": "+", "y": "+"}},
    }
    assert item.as_dict()["status"] == "ASSEMBLED"


def test_assembly_keeps_start_and_end_node_supports_separately() -> None:
    stiffnesses = (_stiffness("1", "Двутавр 30К1 (К1)"),)
    elements = (_element("6", nodes=("4", "58")),)
    nodes = (
        _node("4", "0", "0", "0", supports={"x": "+", "z": "+"}),
        _node("58", "0", "0", "4.5", supports={"y": "-"}),
    )
    assembled = assemble_lira_model(
        stiffnesses=stiffnesses, elements=elements, nodes=nodes
    )
    item = assembled[0]
    assert item.node_ids == ("4", "58")
    assert item.supports_start == {"x": "+", "z": "+"}
    assert item.supports_end == {"y": "-"}
    payload = item.as_dict()
    assert payload["geometry"]["supports"]["start"] == {
        "node_id": "4",
        "signs": {"x": "+", "z": "+"},
    }
    assert payload["geometry"]["supports"]["end"] == {
        "node_id": "58",
        "signs": {"y": "-"},
    }


def test_assembly_blocks_an_unknown_stiffness_type() -> None:
    assembled = assemble_lira_model(
        stiffnesses=(_stiffness("1", "Двутавр 30К1 (К1)"),),
        elements=(_element("6", stiffness_type="99"),),
        nodes=(_node("1", "0", "0", "0"), _node("2", "0", "0", "1")),
    )
    assert assembled[0].blockers == ("LIRA_STIFFNESS_TYPE_NOT_FOUND",)
    assert assembled[0].mark is None
    assert assembled[0].length_m == Decimal("1.0")


def test_assembly_blocks_a_missing_stiffness_type() -> None:
    assembled = assemble_lira_model(
        stiffnesses=(),
        elements=(_element("6", stiffness_type=None),),
        nodes=(_node("1", "0", "0", "0"), _node("2", "0", "0", "1")),
    )
    assert assembled[0].blockers == ("LIRA_ELEMENT_STIFFNESS_TYPE_MISSING",)


def test_assembly_blocks_a_stiffness_name_without_a_mark() -> None:
    assembled = assemble_lira_model(
        stiffnesses=(_stiffness("1", "Двутавр 30К1"),),
        elements=(_element("6"),),
        nodes=(_node("1", "0", "0", "0"), _node("2", "0", "0", "1")),
    )
    assert assembled[0].blockers == ("LIRA_STIFFNESS_NAME_WITHOUT_MARK",)
    assert assembled[0].designation == "30К1"


def test_assembly_blocks_an_incomplete_node_list() -> None:
    assembled = assemble_lira_model(
        stiffnesses=(_stiffness("1", "Двутавр 30К1 (К1)"),),
        elements=(_element("6", nodes=("1",)),),
        nodes=(_node("1", "0", "0", "0"),),
    )
    assert "LIRA_ELEMENT_NODE_LIST_INCOMPLETE" in assembled[0].blockers
    assert assembled[0].length_m is None


def test_assembly_blocks_a_non_bar_element() -> None:
    assembled = assemble_lira_model(
        stiffnesses=(_stiffness("1", "Двутавр 30К1 (К1)"),),
        elements=(_element("6", nodes=("1", "2", "3")),),
        nodes=(_node("1", "0", "0", "0"), _node("2", "0", "0", "1"), _node("3", "0", "0", "2")),
    )
    assert "LIRA_ELEMENT_NOT_A_TWO_NODE_BAR" in assembled[0].blockers
    assert assembled[0].length_m is None


def test_assembly_blocks_a_missing_node() -> None:
    assembled = assemble_lira_model(
        stiffnesses=(_stiffness("1", "Двутавр 30К1 (К1)"),),
        elements=(_element("6", nodes=("4", "58")),),
        nodes=(_node("4", "0", "0", "0"),),
    )
    assert "LIRA_NODE_NOT_FOUND:58" in assembled[0].blockers
    assert assembled[0].length_m is None


def test_assembly_blocks_incomplete_node_coordinates() -> None:
    assembled = assemble_lira_model(
        stiffnesses=(_stiffness("1", "Двутавр 30К1 (К1)"),),
        elements=(_element("6", nodes=("1", "2")),),
        nodes=(_node("1", "0", "0", "0"), _node("2", "0", None, "1")),
    )
    assert "LIRA_NODE_COORDINATES_INCOMPLETE" in assembled[0].blockers
    assert assembled[0].length_m is None


# --------------------------------------------------------------------------
# Table readers
# --------------------------------------------------------------------------


def _write_workbook(path: Path, sheet: str, rows: list[list[object]]) -> Path:
    workbook = openpyxl.Workbook()
    worksheet = workbook.active
    assert worksheet is not None
    worksheet.title = sheet
    for row in rows:
        worksheet.append(row)
    workbook.save(path)
    workbook.close()
    return path


def test_read_stiffness_table_groups_parameter_rows(tmp_path: Path) -> None:
    path = _write_workbook(
        tmp_path / "stiffness.xlsx",
        "Лист1",
        [
            ["Таблица жесткостей"],
            ["Тип жесткости", "Имя", "Параметры"],
            ["1", "  Двутавр 30К1 (К1)", "q=0.08694"],
            [" ", " ", "EF=232748"],
            [" ", " ", "EIz=1310"],
            ["2", "Швеллер 22П (Б2)", "q=0.021"],
        ],
    )
    stiffnesses = read_stiffness_table(path, sheet_name="Лист1", header_row=2)
    assert [item.type_id for item in stiffnesses] == ["1", "2"]
    assert stiffnesses[0].mark == "К1"
    assert stiffnesses[0].parameters == ("q=0.08694", "EF=232748", "EIz=1310")
    assert stiffnesses[1].kind_word == "Швеллер"


def test_read_stiffness_table_rejects_a_duplicate_type(tmp_path: Path) -> None:
    path = _write_workbook(
        tmp_path / "stiffness.xlsx",
        "Лист1",
        [
            ["Таблица жесткостей"],
            ["Тип жесткости", "Имя", "Параметры"],
            ["1", "Двутавр 30К1 (К1)", "q=1"],
            ["1", "Двутавр 20К1 (К2)", "q=2"],
        ],
    )
    with pytest.raises(LiraFormatError, match="declared twice"):
        read_stiffness_table(path, sheet_name="Лист1", header_row=2)


def test_read_element_table_parses_nodes_and_angle(tmp_path: Path) -> None:
    path = _write_workbook(
        tmp_path / "elements.xlsx",
        " ",
        [
            ["Таблица элементов"],
            [None, None, None, None, None, "Жесткие вставки"],
            [
                "№ элем",
                "Тип элем",
                "Кол.сечений",
                "Тип жестк",
                "Угол м.осей",
                "AX н",
                "AX к",
                "№№ узлов",
            ],
            ["6", "10", "2", "1", "0", "-", "-", " 4,58"],
        ],
    )
    elements = read_element_table(path, sheet_name=" ", header_row=3)
    assert len(elements) == 1
    assert elements[0].element_id == "6"
    assert elements[0].node_ids == ("4", "58")
    assert elements[0].rotation_angle_degrees == Decimal("0")
    assert elements[0].stiffness_type == "1"


def test_read_element_table_rejects_a_duplicate_element(tmp_path: Path) -> None:
    path = _write_workbook(
        tmp_path / "elements.xlsx",
        " ",
        [
            ["Таблица элементов"],
            [],
            [
                "№ элем",
                "Тип элем",
                "Кол.сечений",
                "Тип жестк",
                "Угол м.осей",
                "AX н",
                "AX к",
                "№№ узлов",
            ],
            ["6", "10", "2", "1", "0", "-", "-", " 4,58"],
            ["6", "10", "2", "1", "0", "-", "-", " 5,61"],
        ],
    )
    with pytest.raises(LiraFormatError, match="appears twice"):
        read_element_table(path, sheet_name=" ", header_row=3)


def test_read_element_table_reports_missing_columns(tmp_path: Path) -> None:
    path = _write_workbook(
        tmp_path / "elements.xlsx",
        " ",
        [
            ["Таблица элементов"],
            [],
            ["№ элем", "Тип элем"],
            ["6", "10"],
        ],
    )
    with pytest.raises(LiraFormatError, match="missing columns"):
        read_element_table(path, sheet_name=" ", header_row=3)


def test_read_element_table_reports_an_empty_export(tmp_path: Path) -> None:
    path = _write_workbook(
        tmp_path / "elements.xlsx",
        " ",
        [["Таблица элементов"], [], ["№ элем", "Тип элем"]],
    )
    with pytest.raises(LiraFormatError, match="header but no data rows"):
        read_element_table(path, sheet_name=" ", header_row=3)


def test_read_node_table_keeps_supports_and_comma_decimals(tmp_path: Path) -> None:
    path = _write_workbook(
        tmp_path / "nodes.xlsx",
        " ",
        [
            ["Таблица узлов"],
            [None, "Координаты"],
            ["№ узла", "X\n(м)", "Y\n(м)", "Z\n(м)", "X", "Y", "Z", "UX", "UY", "UZ"],
            ["1", "0", "6,2", "0", "+", "+", "+", "+", "+", "+"],
        ],
    )
    nodes = read_node_table(path, sheet_name=" ", header_row=3)
    assert len(nodes) == 1
    assert nodes[0].y == Decimal("6.2")
    assert nodes[0].supports["ux"] == "+"


def test_read_node_table_rejects_a_duplicate_node(tmp_path: Path) -> None:
    path = _write_workbook(
        tmp_path / "nodes.xlsx",
        " ",
        [
            ["Таблица узлов"],
            [],
            ["№ узла", "X\n(м)", "Y\n(м)", "Z\n(м)", "X", "Y", "Z", "UX", "UY", "UZ"],
            ["1", "0", "0", "0", "+", "+", "+", "+", "+", "+"],
            ["1", "0", "1", "0", "+", "+", "+", "+", "+", "+"],
        ],
    )
    with pytest.raises(LiraFormatError, match="appears twice"):
        read_node_table(path, sheet_name=" ", header_row=3)


def test_read_node_table_rejects_mixed_separators(tmp_path: Path) -> None:
    path = _write_workbook(
        tmp_path / "nodes.xlsx",
        " ",
        [
            ["Таблица узлов"],
            [],
            ["№ узла", "X\n(м)", "Y\n(м)", "Z\n(м)", "X", "Y", "Z", "UX", "UY", "UZ"],
            ["1", "0", "1.234,5", "0", "+", "+", "+", "+", "+", "+"],
        ],
    )
    with pytest.raises(LiraFormatError, match="ambiguous"):
        read_node_table(path, sheet_name=" ", header_row=3)


# --------------------------------------------------------------------------
# Bundle
# --------------------------------------------------------------------------


def _three_tables(tmp_path: Path) -> tuple[Path, Path, Path]:
    stiffness = _write_workbook(
        tmp_path / "stiffness.xlsx",
        "Лист1",
        [
            ["Таблица жесткостей"],
            ["Тип жесткости", "Имя", "Параметры"],
            ["1", "Двутавр 30К1 (К1)", "q=1"],
        ],
    )
    elements = _write_workbook(
        tmp_path / "elements.xlsx",
        " ",
        [
            ["Таблица элементов"],
            [],
            [
                "№ элем",
                "Тип элем",
                "Кол.сечений",
                "Тип жестк",
                "Угол м.осей",
                "AX н",
                "AX к",
                "№№ узлов",
            ],
            ["6", "10", "2", "1", "0", "-", "-", " 4,58"],
        ],
    )
    nodes = _write_workbook(
        tmp_path / "nodes.xlsx",
        " ",
        [
            ["Таблица узлов"],
            [],
            ["№ узла", "X\n(м)", "Y\n(м)", "Z\n(м)", "X", "Y", "Z", "UX", "UY", "UZ"],
            ["4", "4", "0", "0", "+", "+", "+", "+", "+", "+"],
            ["58", "4", "0", "4.5", "+", "+", "+", "+", "+", "+"],
        ],
    )
    return stiffness, elements, nodes


def test_prepare_bundle_writes_outputs_and_resolves_nothing(tmp_path: Path) -> None:
    stiffness, elements, nodes = _three_tables(tmp_path)
    assembly = prepare_lira_model_bundle(
        stiffness_path=stiffness,
        element_path=elements,
        node_path=nodes,
        output_dir=tmp_path / "model",
    )
    assert len(assembly.elements) == 1
    assert assembly.elements[0].length_m == Decimal("4.5")
    assert assembly.marks == ("К1",)

    payload = assembly.as_dict()
    assert payload["profile_resolution"] is None
    assert payload["profile_resolution_validated"] is False
    assert payload["ptm"] is None
    assert payload["ptm_validated"] is False
    assert payload["rx38_force_generation_allowed"] is False
    assert payload["issue_readiness"] == "NOT_READY_FOR_ISSUE"

    for name in (
        "manifest.json",
        "stiffnesses.json",
        "nodes.json",
        "elements.json",
        "elements.csv",
        "README_MODEL.md",
    ):
        assert (tmp_path / "model" / name).is_file()

    manifest = (tmp_path / "model" / "manifest.json").read_text("utf-8")
    assert "MODEL_ASSEMBLED" in manifest


def test_prepare_bundle_refuses_an_existing_directory(tmp_path: Path) -> None:
    stiffness, elements, nodes = _three_tables(tmp_path)
    (tmp_path / "model").mkdir()
    with pytest.raises(LiraFormatError, match="refusing to overwrite"):
        prepare_lira_model_bundle(
            stiffness_path=stiffness,
            element_path=elements,
            node_path=nodes,
            output_dir=tmp_path / "model",
        )


# --------------------------------------------------------------------------
# Force candidates
# --------------------------------------------------------------------------


def _forces_json(tmp_path: Path, records: list[dict[str, object]]) -> Path:
    import json

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


def _force_record(
    *, element_id: str, station: str, row: int, n: str
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
            "mark": None,
            "profile": None,
            "load_case": "1",
            "element_type": "10",
            "composition": "-",
            "combination": None,
        },
        "metadata_provenance": {},
        "native_forces": {
            "N": {
                "source_cell": f"C{row}",
                "raw_token": n,
                "parsed_decimal": n,
                "source_unit": "т",
                "normalized_value": n,
                "normalized_unit": "kN",
            }
        },
        "native_results": {},
    }


def test_attach_force_candidates_keeps_every_row(tmp_path: Path) -> None:
    from fireprotect.lira import attach_force_candidates

    stiffness, elements, nodes = _three_tables(tmp_path)
    assembled = assemble_lira_model(
        stiffnesses=read_stiffness_table(stiffness, sheet_name="Лист1", header_row=2),
        elements=read_element_table(elements, sheet_name=" ", header_row=3),
        nodes=read_node_table(nodes, sheet_name=" ", header_row=3),
    )
    forces = _forces_json(
        tmp_path,
        [
            _force_record(element_id="6", station="1", row=4, n="-1.5"),
            _force_record(element_id="6", station="2", row=5, n="-2.5"),
        ],
    )
    attached, missing = attach_force_candidates(assembled, forces)
    assert missing == ()
    assert len(attached[0].force_candidates) == 2
    assert [item.section_station for item in attached[0].force_candidates] == ["1", "2"]
    assert attached[0].force_candidates[1].components["N"] == "-2.5"
    assert attached[0].force_candidates[1].units["N"] == "kN"


def test_attach_force_candidates_reports_elements_without_forces(
    tmp_path: Path,
) -> None:
    from fireprotect.lira import attach_force_candidates

    stiffness, elements, nodes = _three_tables(tmp_path)
    assembled = assemble_lira_model(
        stiffnesses=read_stiffness_table(stiffness, sheet_name="Лист1", header_row=2),
        elements=read_element_table(elements, sheet_name=" ", header_row=3),
        nodes=read_node_table(nodes, sheet_name=" ", header_row=3),
    )
    forces = _forces_json(tmp_path, [])
    attached, missing = attach_force_candidates(assembled, forces)
    assert missing == ("6",)
    assert attached[0].force_candidates == ()


def test_prepare_bundle_with_forces_records_the_join(tmp_path: Path) -> None:
    from fireprotect.lira import read_candidates

    stiffness, elements, nodes = _three_tables(tmp_path)
    forces = _forces_json(
        tmp_path, [_force_record(element_id="6", station="1", row=4, n="-1.5")]
    )
    assembly = prepare_lira_model_bundle(
        stiffness_path=stiffness,
        element_path=elements,
        node_path=nodes,
        forces_path=forces,
        output_dir=tmp_path / "model",
    )
    assert assembly.force_candidates_total == 1
    assert assembly.elements_without_forces == ()
    payload = assembly.as_dict()
    assert payload["force_candidates"] == 1
    assert payload["elements_without_forces"] == 0
    assert payload["rx38_force_generation_allowed"] is False
    assert read_candidates(forces)[0][0].candidate_id == "C0001"
