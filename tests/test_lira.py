from __future__ import annotations

import builtins
from pathlib import Path

import pytest

from fireprotect.lira import (
    CsvTableSource,
    ForceUnits,
    HtmlTableSource,
    LiraColumnMapping,
    LiraDependencyError,
    LiraForceImporter,
    LiraMappingError,
    LiraRowError,
    RawTableRow,
    XlsxTableSource,
)


HEADERS = {
    "element_id": "Member No.",
    "section": "Assigned shape",
    "load_case": "LC name",
    "combination": "Envelope",
    "N": "Axial",
    "Mx": "Bending A",
    "My": "Bending B",
    "Qx": "Shear A",
    "Qy": "Shear B",
}


def mapping(**kwargs: object) -> LiraColumnMapping:
    return LiraColumnMapping(
        columns=HEADERS,
        units=ForceUnits(N="kN", Mx="kN*m", My="N*m", Qx="N", Qy="tf"),
        **kwargs,
    )


def assert_converted(row: object) -> None:
    assert row.element_id == "17"
    assert row.section == "30K1"
    assert row.load_case == "LC-2"
    assert row.combination == "ULS-7"
    assert row.N == pytest.approx(-125_500.0)
    assert row.Mx == pytest.approx(12_250.0)
    assert row.My == pytest.approx(-320.0)
    assert row.Qx == pytest.approx(750.0)
    assert row.Qy == pytest.approx(2 * 9_806.65)
    assert row.source.N == pytest.approx(-125.5)
    assert row.source.units.N == "kN"


def test_csv_import_uses_arbitrary_headers_and_converts_to_si(tmp_path: Path) -> None:
    source = tmp_path / "forces.csv"
    source.write_text(
        "Member No.;Assigned shape;LC name;Envelope;Axial;Bending A;Bending B;Shear A;Shear B\n"
        "17;30K1;LC-2;ULS-7;-125,5;12,25;-320;750;2\n",
        encoding="utf-8",
    )

    result = LiraForceImporter(
        mapping(decimal_separator=",")
    ).import_source(CsvTableSource(source, delimiter=";", encoding="utf-8"))

    assert len(result) == 1
    assert result[0].source_row == 2
    assert_converted(result[0])


def test_html_import_uses_standard_library_parser(tmp_path: Path) -> None:
    source = tmp_path / "forces.html"
    cells = [
        "17",
        "30K1",
        "LC-2",
        "ULS-7",
        "-125.5",
        "12.25",
        "-320",
        "750",
        "2",
    ]
    source.write_text(
        "<html><body><table><tr>"
        + "".join(f"<th>{header}</th>" for header in HEADERS.values())
        + "</tr><tr>"
        + "".join(f"<td><span>{cell}</span></td>" for cell in cells)
        + "</tr></table></body></html>",
        encoding="utf-8",
    )

    result = LiraForceImporter(mapping()).import_source(HtmlTableSource(source))

    assert len(result) == 1
    assert result[0].source_row == 2
    assert_converted(result[0])


def test_xlsx_import_is_optional_and_uses_selected_sheet(tmp_path: Path) -> None:
    openpyxl = pytest.importorskip("openpyxl")
    source = tmp_path / "forces.xlsx"
    workbook = openpyxl.Workbook()
    worksheet = workbook.active
    worksheet.title = "Bar forces"
    worksheet.append(list(HEADERS.values()))
    worksheet.append([17, "30K1", "LC-2", "ULS-7", -125.5, 12.25, -320, 750, 2])
    workbook.save(source)
    workbook.close()

    result = LiraForceImporter(mapping()).import_source(
        XlsxTableSource(source, sheet_name="Bar forces")
    )

    assert len(result) == 1
    assert result[0].source_row == 2
    assert_converted(result[0])


def test_xlsx_reports_clear_error_when_openpyxl_is_unavailable(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    original_import = builtins.__import__

    def reject_openpyxl(name: str, *args: object, **kwargs: object) -> object:
        if name == "openpyxl":
            raise ImportError("synthetic missing dependency")
        return original_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", reject_openpyxl)

    with pytest.raises(LiraDependencyError, match="pip install openpyxl"):
        XlsxTableSource(tmp_path / "unused.xlsx").read_rows()


def test_mapping_is_mandatory_complete_and_units_are_dimension_checked() -> None:
    incomplete = dict(HEADERS)
    incomplete.pop("Qy")
    with pytest.raises(LiraMappingError, match="Qy"):
        LiraColumnMapping(
            columns=incomplete,
            units=ForceUnits(N="kN", Mx="kN*m", My="N*m", Qx="N", Qy="tf"),
        )

    with pytest.raises(LiraMappingError, match="unsupported source unit"):
        LiraColumnMapping(
            columns=HEADERS,
            units=ForceUnits(N="kN*m", Mx="kN*m", My="N*m", Qx="N", Qy="tf"),
        )


def test_missing_mapped_header_and_bad_value_include_context(tmp_path: Path) -> None:
    source = tmp_path / "bad.csv"
    source.write_text(
        "Member No.,Assigned shape,LC name,Envelope,Axial,Bending A,Bending B,Shear A\n"
        "17,30K1,LC-2,ULS-7,-125.5,12.25,oops,750\n",
        encoding="utf-8",
    )
    importer = LiraForceImporter(mapping())
    with pytest.raises(LiraMappingError, match="Shear B"):
        importer.import_source(CsvTableSource(source, encoding="utf-8"))

    class SyntheticApiSource:
        def read_rows(self) -> list[RawTableRow]:
            values = dict(
                zip(
                    HEADERS.values(),
                    [17, "30K1", "LC-2", "ULS-7", -125.5, 12.25, "oops", 750, 2],
                )
            )
            return [RawTableRow(values, 84)]

    with pytest.raises(LiraRowError, match=r"row 84, field My"):
        importer.import_source(SyntheticApiSource())


def test_protocol_accepts_future_api_source_without_domain_dependency() -> None:
    class SyntheticApiSource:
        def read_rows(self) -> list[RawTableRow]:
            values = dict(
                zip(
                    HEADERS.values(),
                    ["17", "30K1", "LC-2", "ULS-7", -125.5, 12.25, -320, 750, 2],
                )
            )
            return [RawTableRow(values, 901)]

    result = LiraForceImporter(mapping()).import_source(SyntheticApiSource())

    assert result[0].source_row == 901
    assert_converted(result[0])
