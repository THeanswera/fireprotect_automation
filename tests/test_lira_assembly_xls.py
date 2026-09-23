"""Native legacy ``.xls`` (BIFF) inputs for the read-only LIRA model assembler.

These tests mirror the real control-model exports: Cyrillic headers read through
``encoding_override="cp1251"``, one-space sheet names, numeric ids decoded from
IEEE-754 doubles (so ``1`` arrives as ``Decimal("1.0")``) and a stiffness table
whose parameters continue on a second row.
"""

from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path

import pytest

from fireprotect.lira import (
    LiraFormatError,
    parse_stiffness_name,
    prepare_lira_model_bundle,
    read_element_table,
    read_node_table,
    read_stiffness_table,
)


def _write_xls(path: Path, sheet: str, rows: list[list[object]]) -> Path:
    xlwt = pytest.importorskip("xlwt")
    path.parent.mkdir(parents=True, exist_ok=True)
    workbook = xlwt.Workbook()
    worksheet = workbook.add_sheet(sheet)
    for row_index, row in enumerate(rows):
        for column_index, value in enumerate(row):
            if value is not None:
                worksheet.write(row_index, column_index, value)
    workbook.save(str(path))
    return path


_STIFFNESS_ROWS: list[list[object]] = [
    ["Таблица жесткостей"],
    ["Тип жесткости", "Имя", "Параметры\n(сечения-(см) жесткости-(т,м) расп.вес-(т,м))"],
    [1, "  Брус 10 X 20", "Ro=0,E=2.1e+007,GF=0"],
    [" ", " ", "B=10,H=20"],
]

_ELEMENT_ROWS: list[list[object]] = [
    ["Таблица элементов"],
    [None, None, None, None, None, "Жесткие вставки"],
    ["№ элем", "Тип элем", "Кол.сечений", "Тип жестк", "Угол м.осей", "AX н", "AX к", "№№ узлов"],
    [1, 10, 2, 1, 0, "-", "-", " 1,3"],
    [2, 10, 2, 1, 0, "-", "-", " 3,2"],
]

_NODE_ROWS: list[list[object]] = [
    ["Таблица узлов"],
    [None, "Координаты", None, None, "Связи"],
    ["№ узла", "X\n(м)", "Y\n(м)", "Z\n(м)", "X", "Y", "Z", "UX", "UY", "UZ"],
    [1, 0, 0, 0, "+", "-", "+", "-", "-", "-"],
    [2, 6, 0, 0, "-", "-", "+", "-", "-", "-"],
    [3, 3, 0, 0, "-", "-", "-", "-", "-", "-"],
]


def _three_biff_tables(tmp_path: Path) -> tuple[Path, Path, Path]:
    stiffness = _write_xls(tmp_path / "stiffness.xls", " ", _STIFFNESS_ROWS)
    elements = _write_xls(tmp_path / "elements.xls", " ", _ELEMENT_ROWS)
    nodes = _write_xls(tmp_path / "nodes.xls", " ", _NODE_ROWS)
    return stiffness, elements, nodes


def _read_all(
    tmp_path: Path,
) -> tuple[object, object, object]:
    stiffness, elements, nodes = _three_biff_tables(tmp_path)
    return (
        read_stiffness_table(stiffness, sheet_name=" ", header_row=2),
        read_element_table(elements, sheet_name=" ", header_row=3),
        read_node_table(nodes, sheet_name=" ", header_row=3),
    )


def test_parse_stiffness_name_keeps_birus_verbatim() -> None:
    kind, designation, mark = parse_stiffness_name("Брус 10 X 20")
    assert kind is None
    assert designation == "Брус 10 X 20"
    assert mark is None


def test_biff_stiffness_preserves_name_parameters_and_units_header(
    tmp_path: Path,
) -> None:
    stiffnesses, _, _ = _read_all(tmp_path)
    assert len(stiffnesses) == 1
    item = stiffnesses[0]
    assert item.type_id == "1"
    assert item.raw_name == "Брус 10 X 20"
    assert item.kind_word is None
    assert item.designation == "Брус 10 X 20"
    assert item.mark is None
    assert item.parameters == ("Ro=0,E=2.1e+007,GF=0", "B=10,H=20")
    assert item.parameter_headers == (
        "Параметры\n(сечения-(см) жесткости-(т,м) расп.вес-(т,м))",
    )
    assert "(см)" in item.parameter_headers[0]
    assert item.source_cell == "B3"


def test_biff_numeric_ids_link_across_tables(tmp_path: Path) -> None:
    stiffness, elements, nodes = _three_biff_tables(tmp_path)
    assembly = prepare_lira_model_bundle(
        stiffness_path=stiffness,
        element_path=elements,
        node_path=nodes,
        output_dir=tmp_path / "model",
        stiffness_sheet=" ",
        element_sheet=" ",
        node_sheet=" ",
    )
    assert [item.element_id for item in assembly.elements] == ["1", "2"]
    assert [item.node_ids for item in assembly.elements] == [("1", "3"), ("3", "2")]
    for item in assembly.elements:
        assert item.stiffness_type == "1"
        assert item.length_m == Decimal("3")
        assert item.rotation_angle_degrees == Decimal("0")
        assert item.designation == "Брус 10 X 20"
        assert item.mark is None
        # the 1.0 -> "1" normalization must not leave a dangling link
        assert "LIRA_STIFFNESS_TYPE_NOT_FOUND" not in item.blockers
        assert not any(
            blocker.startswith("LIRA_NODE_NOT_FOUND") for blocker in item.blockers
        )
        assert "LIRA_STIFFNESS_NAME_WITHOUT_MARK" in item.blockers
    # element 1 starts at node 1 (X/Z constrained) and ends at node 3 (free):
    # both end nodes must keep their own support signs
    first = assembly.elements[0]
    assert first.supports_start == {
        "x": "+", "y": "-", "z": "+", "ux": "-", "uy": "-", "uz": "-"
    }
    assert first.supports_end == {
        "x": "-", "y": "-", "z": "-", "ux": "-", "uy": "-", "uz": "-"
    }
    assert first.as_dict()["geometry"]["supports"] == {
        "start": {
            "node_id": "1",
            "signs": {"x": "+", "y": "-", "z": "+", "ux": "-", "uy": "-", "uz": "-"},
        },
        "end": {
            "node_id": "3",
            "signs": {"x": "-", "y": "-", "z": "-", "ux": "-", "uy": "-", "uz": "-"},
        },
    }
    second = assembly.elements[1]
    assert second.supports_start["z"] == "-"
    assert second.supports_end["z"] == "+"


def test_biff_manifest_records_format_and_numeric_provenance(tmp_path: Path) -> None:
    stiffness, elements, nodes = _three_biff_tables(tmp_path)
    prepare_lira_model_bundle(
        stiffness_path=stiffness,
        element_path=elements,
        node_path=nodes,
        output_dir=tmp_path / "model",
        stiffness_sheet=" ",
        element_sheet=" ",
        node_sheet=" ",
    )
    manifest = json.loads((tmp_path / "model" / "manifest.json").read_text("utf-8"))
    for key in ("stiffness", "elements", "nodes"):
        source = manifest["sources"][key]
        assert source["format"] == "xls"
        assert source["numeric_provenance"] == (
            "BIFF_NUMERIC_VALUE_DECODED_NO_RAW_DECIMAL_TOKEN"
        )
        assert len(source["sha256"]) == 64


def test_biff_missing_node_link_is_blocked(tmp_path: Path) -> None:
    stiffness = _write_xls(tmp_path / "stiffness.xls", " ", _STIFFNESS_ROWS)
    elements = _write_xls(
        tmp_path / "elements.xls",
        " ",
        [
            ["Таблица элементов"],
            [None, None, None, None, None, "Жесткие вставки"],
            ["№ элем", "Тип элем", "Кол.сечений", "Тип жестк", "Угол м.осей", "AX н", "AX к", "№№ узлов"],
            [1, 10, 2, 1, 0, "-", "-", " 1,9"],
        ],
    )
    nodes = _write_xls(tmp_path / "nodes.xls", " ", _NODE_ROWS)
    assembly = prepare_lira_model_bundle(
        stiffness_path=stiffness,
        element_path=elements,
        node_path=nodes,
        output_dir=tmp_path / "model",
        stiffness_sheet=" ",
        element_sheet=" ",
        node_sheet=" ",
    )
    assert "LIRA_NODE_NOT_FOUND:9" in assembly.elements[0].blockers
    assert assembly.elements[0].length_m is None


def test_biff_duplicate_ids_are_refused(tmp_path: Path) -> None:
    nodes = _write_xls(
        tmp_path / "nodes.xls",
        " ",
        [
            ["Таблица узлов"],
            [None, "Координаты", None, None, "Связи"],
            ["№ узла", "X\n(м)", "Y\n(м)", "Z\n(м)", "X", "Y", "Z", "UX", "UY", "UZ"],
            [1, 0, 0, 0, "+", "-", "+", "-", "-", "-"],
            [1, 6, 0, 0, "-", "-", "+", "-", "-", "-"],
        ],
    )
    with pytest.raises(LiraFormatError, match="appears twice"):
        read_node_table(nodes, sheet_name=" ", header_row=3)

    elements = _write_xls(
        tmp_path / "elements.xls",
        " ",
        [
            ["Таблица элементов"],
            [None, None, None, None, None, "Жесткие вставки"],
            ["№ элем", "Тип элем", "Кол.сечений", "Тип жестк", "Угол м.осей", "AX н", "AX к", "№№ узлов"],
            [1, 10, 2, 1, 0, "-", "-", " 1,3"],
            [1, 10, 2, 1, 0, "-", "-", " 3,2"],
        ],
    )
    with pytest.raises(LiraFormatError, match="appears twice"):
        read_element_table(elements, sheet_name=" ", header_row=3)

    stiffness = _write_xls(
        tmp_path / "stiffness.xls",
        " ",
        [
            ["Таблица жесткостей"],
            ["Тип жесткости", "Имя", "Параметры"],
            [1, "Брус 10 X 20", "Ro=0"],
            [1, "Брус 20 X 30", "Ro=0"],
        ],
    )
    with pytest.raises(LiraFormatError, match="declared twice"):
        read_stiffness_table(stiffness, sheet_name=" ", header_row=2)


def test_biff_sheet_lookup_is_exact_and_fail_closed(tmp_path: Path) -> None:
    stiffness = _write_xls(tmp_path / "stiffness.xls", " ", _STIFFNESS_ROWS)
    with pytest.raises(LiraFormatError, match="not found"):
        read_stiffness_table(stiffness, sheet_name="Лист1", header_row=2)
