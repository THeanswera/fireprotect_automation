"""Read-only adapter for legacy binary Excel XLS (BIFF) workbooks.

Unlike OOXML, BIFF does not expose an XML ``<v>`` token.  Numeric values read by
``xlrd`` have already been decoded from the workbook's binary IEEE-754 number.
The adapter therefore never invents a raw decimal token: ``raw_token`` is
``None`` for numeric cells and ``decimal_provenance`` records the limitation.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from hashlib import sha256
from pathlib import Path
from typing import Iterator

from .errors import LiraDependencyError, LiraFormatError

# BIFF numeric cells are decoded IEEE-754 doubles: there is no raw decimal
# token (unlike an OOXML ``<v>`` element), so this limitation is recorded
# instead of inventing a token.
BIFF_NUMERIC_PROVENANCE = "BIFF_NUMERIC_VALUE_DECODED_NO_RAW_DECIMAL_TOKEN"


@dataclass(frozen=True, slots=True)
class XlsCell:
    sheet_name: str
    sheet_index: int
    row_number: int
    column_number: int
    cell_reference: str
    value: object
    raw_token: str | None
    decimal_value: Decimal | None
    decimal_provenance: str | None


@dataclass(frozen=True, slots=True)
class XlsWorksheet:
    name: str
    index: int
    row_count: int
    column_count: int
    rows: tuple[tuple[XlsCell, ...], ...]


@dataclass(frozen=True, slots=True)
class XlsWorkbook:
    source_file: str
    source_sha256: str
    biff_version: int
    encoding: str
    worksheets: tuple[XlsWorksheet, ...]

    @property
    def cell_count(self) -> int:
        return sum(len(row) for sheet in self.worksheets for row in sheet.rows)


def _file_sha256(path: Path) -> str:
    digest = sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _column_name(index: int) -> str:
    result = ""
    while index:
        index, remainder = divmod(index - 1, 26)
        result = chr(65 + remainder) + result
    return result


def _cell_value(cell: object, *, xlrd: object) -> tuple[object, str | None, Decimal | None, str | None]:
    cell_type = cell.ctype  # type: ignore[attr-defined]
    value = cell.value  # type: ignore[attr-defined]
    if cell_type in {xlrd.XL_CELL_EMPTY, xlrd.XL_CELL_BLANK}:  # type: ignore[attr-defined]
        return None, None, None, None
    if cell_type == xlrd.XL_CELL_TEXT:  # type: ignore[attr-defined]
        token = str(value)
        return token, token, None, None
    if cell_type == xlrd.XL_CELL_NUMBER:  # type: ignore[attr-defined]
        decimal_value = Decimal(str(value))
        return decimal_value, None, decimal_value, BIFF_NUMERIC_PROVENANCE
    if cell_type == xlrd.XL_CELL_BOOLEAN:  # type: ignore[attr-defined]
        token = "1" if bool(value) else "0"
        return bool(value), token, None, None
    if cell_type == xlrd.XL_CELL_ERROR:  # type: ignore[attr-defined]
        raise LiraFormatError(f"XLS cell contains error code {value!r}")
    # Dates are deliberately not converted to engineering numbers.
    return value, None, None, "BIFF_NON_TEXT_VALUE_NO_RAW_TOKEN"


def read_xls_workbook(
    path: str | Path,
    *,
    encoding_override: str | None = None,
    expected_sha256: str | None = None,
) -> XlsWorkbook:
    """Read every worksheet from one legacy BIFF XLS without modifying it."""

    source = Path(path).resolve(strict=True)
    if source.suffix.casefold() != ".xls":
        raise LiraFormatError(f"legacy XLS source must have .xls suffix: {source}")
    source_hash = _file_sha256(source)
    if expected_sha256 is not None and source_hash.casefold() != expected_sha256.casefold():
        raise LiraFormatError(
            f"XLS source SHA-256 mismatch for {source}: expected {expected_sha256}, got {source_hash}"
        )
    try:
        import xlrd  # type: ignore[import-untyped]
    except ImportError as exc:  # pragma: no cover - dependency is declared
        raise LiraDependencyError("legacy XLS import requires xlrd>=2.0,<3") from exc
    try:
        book = xlrd.open_workbook(
            source,
            on_demand=True,
            formatting_info=False,
            encoding_override=encoding_override,
        )
    except Exception as exc:
        raise LiraFormatError(f"cannot read legacy XLS {source}: {exc}") from exc
    worksheets: list[XlsWorksheet] = []
    try:
        for sheet_index in range(book.nsheets):
            sheet = book.sheet_by_index(sheet_index)
            rows: list[tuple[XlsCell, ...]] = []
            for row_index in range(sheet.nrows):
                cells: list[XlsCell] = []
                for column_index in range(sheet.ncols):
                    source_cell = sheet.cell(row_index, column_index)
                    value, raw_token, decimal_value, limitation = _cell_value(
                        source_cell, xlrd=xlrd
                    )
                    cells.append(
                        XlsCell(
                            sheet_name=sheet.name,
                            sheet_index=sheet_index,
                            row_number=row_index + 1,
                            column_number=column_index + 1,
                            cell_reference=f"{_column_name(column_index + 1)}{row_index + 1}",
                            value=value,
                            raw_token=raw_token,
                            decimal_value=decimal_value,
                            decimal_provenance=limitation,
                        )
                    )
                rows.append(tuple(cells))
            worksheets.append(
                XlsWorksheet(
                    name=sheet.name,
                    index=sheet_index,
                    row_count=sheet.nrows,
                    column_count=sheet.ncols,
                    rows=tuple(rows),
                )
            )
        return XlsWorkbook(
            source_file=str(source),
            source_sha256=source_hash,
            biff_version=book.biff_version,
            encoding=book.encoding,
            worksheets=tuple(worksheets),
        )
    finally:
        book.release_resources()


def iter_nonempty_cells(workbook: XlsWorkbook) -> Iterator[XlsCell]:
    for sheet in workbook.worksheets:
        for row in sheet.rows:
            for cell in row:
                if cell.value is not None:
                    yield cell
