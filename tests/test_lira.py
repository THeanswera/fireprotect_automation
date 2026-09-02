from __future__ import annotations

from decimal import Decimal
from pathlib import Path

import pytest

from fireprotect.lira import (
    CsvTableSource,
    ForceUnits,
    HtmlTableSource,
    LiraColumnMapping,
    LiraForceImporter,
    LiraFormatError,
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
    assert row.N == Decimal("-125500")
    assert row.Mx == Decimal("12250")
    assert row.My == Decimal("-320")
    assert row.Qx == Decimal("750")
    assert row.Qy == Decimal("19613.30")
    assert row.source.N == Decimal("-125.5")
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
    assert result[0].source.raw_tokens["N"] == "-125,5"
    assert result[0].source.source_cells["N"] == "E2"
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
    assert result[0].source.raw_tokens["Mx"] == "12.25"
    assert result[0].source.source_cells["Mx"] == "F2"
    assert result[0].source_sheet == "table[0]"
    assert_converted(result[0])


def test_xlsx_import_is_optional_and_uses_selected_sheet(tmp_path: Path) -> None:
    openpyxl = pytest.importorskip("openpyxl")
    source = tmp_path / "forces.xlsx"
    workbook = openpyxl.Workbook()
    worksheet = workbook.active
    worksheet.title = "Bar forces"
    worksheet.append(list(HEADERS.values()))
    worksheet.append([17, "30K1", "LC-2", "ULS-7", "-125.5", "12.25", "-320", "750", "2"])
    workbook.save(source)
    workbook.close()

    result = LiraForceImporter(mapping()).import_source(
        XlsxTableSource(source, sheet_name="Bar forces")
    )

    assert len(result) == 1
    assert result[0].source_row == 2
    assert_converted(result[0])

    with pytest.raises(LiraFormatError, match="explicit sheet_name"):
        XlsxTableSource(source).read_rows()
    with pytest.raises(LiraFormatError, match="data_only must be bool"):
        XlsxTableSource(
            source,
            sheet_name="Bar forces",
            data_only="false",  # type: ignore[arg-type]
        ).read_rows()


def test_xlsx_numeric_cells_are_imported_from_exact_ooxml_tokens(
    tmp_path: Path,
) -> None:
    openpyxl = pytest.importorskip("openpyxl")
    source = tmp_path / "numeric-forces.xlsx"
    workbook = openpyxl.Workbook()
    worksheet = workbook.active
    worksheet.title = "Bar forces"
    worksheet.append(list(HEADERS.values()))
    worksheet.append(
        [17, "30K1", "LC-2", "ULS-7", -125.5, 12.25, -320, 750, 2]
    )
    workbook.save(source)
    workbook.close()

    raw_rows = XlsxTableSource(source, sheet_name="Bar forces").read_rows()
    assert raw_rows[0].values["Axial"] == "-125.5"
    assert raw_rows[0].values["Bending A"] == "12.25"

    result = LiraForceImporter(mapping()).import_source(
        XlsxTableSource(source, sheet_name="Bar forces")
    )
    assert result[0].source.N == Decimal("-125.5")
    assert result[0].source.Mx == Decimal("12.25")
    assert result[0].source.raw_tokens["N"] == "-125.5"
    assert result[0].source.source_cells["N"] == "E2"


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

    unicode_moment_mapping = LiraColumnMapping(
        columns=HEADERS,
        units=ForceUnits(N="kN", Mx="kN·m", My="N×m", Qx="N", Qy="tf"),
    )
    assert unicode_moment_mapping.units.Mx == "kN·m"


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
                    [17, "30K1", "LC-2", "ULS-7", "-125.5", "12.25", "oops", "750", "2"],
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
                    ["17", "30K1", "LC-2", "ULS-7", "-125.5", "12.25", "-320", "750", "2"],
                )
            )
            return [RawTableRow(values, 901)]

    result = LiraForceImporter(mapping()).import_source(SyntheticApiSource())

    assert result[0].source_row == 901
    assert_converted(result[0])
