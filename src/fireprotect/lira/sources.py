"""CSV, HTML and optional XLSX sources for LIRA force tables."""

from __future__ import annotations

import csv
from dataclasses import dataclass
from html.parser import HTMLParser
from pathlib import Path
from typing import Any, Sequence

from ..excel.ooxml import OoxmlReadError, worksheet_matrix
from .errors import LiraFormatError
from .types import RawTableRow


def _validate_headers(headers: Sequence[Any], *, source: str) -> list[str]:
    result = ["" if value is None else str(value).strip() for value in headers]
    if not result or not any(result):
        raise LiraFormatError(f"{source}: header row is empty")
    if any(not header for header in result):
        raise LiraFormatError(f"{source}: header names must not be empty")
    if len(result) != len(set(result)):
        raise LiraFormatError(f"{source}: duplicate header names are not supported")
    return result


def _rows_from_matrix(
    matrix: Sequence[Sequence[Any]],
    *,
    header_index: int,
    source: str,
    sheet: str | None = None,
) -> list[RawTableRow]:
    if header_index < 0:
        raise LiraFormatError(f"{source}: header index cannot be negative")
    if len(matrix) <= header_index:
        raise LiraFormatError(f"{source}: header row {header_index + 1} does not exist")
    headers = _validate_headers(matrix[header_index], source=source)
    rows: list[RawTableRow] = []
    for row_number, row in enumerate(matrix[header_index + 1 :], header_index + 2):
        values = list(row)
        if not any(value is not None and str(value).strip() for value in values):
            continue
        if len(values) > len(headers) and any(
            value is not None and str(value).strip() for value in values[len(headers) :]
        ):
            raise LiraFormatError(
                f"{source}: row {row_number} has more values than the header"
            )
        values.extend([None] * (len(headers) - len(values)))
        row_values = dict(zip(headers, values))
        cells = {
            header: f"{_column_name(column)}{row_number}"
            for column, header in enumerate(headers, 1)
        }
        rows.append(RawTableRow(row_values, row_number, sheet, cells))
    return rows


def _column_name(index: int) -> str:
    result = ""
    while index:
        index, remainder = divmod(index - 1, 26)
        result = chr(ord("A") + remainder) + result
    return result


@dataclass(frozen=True, slots=True)
class CsvTableSource:
    path: str | Path
    encoding: str = "utf-8-sig"
    delimiter: str = ","
    header_row: int = 1

    def read_rows(self) -> list[RawTableRow]:
        if self.header_row < 1:
            raise LiraFormatError("CSV header_row must be one-based")
        with Path(self.path).open("r", encoding=self.encoding, newline="") as stream:
            matrix = list(csv.reader(stream, delimiter=self.delimiter))
        return _rows_from_matrix(
            matrix,
            header_index=self.header_row - 1,
            source=str(self.path),
        )


class _HtmlTableParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.tables: list[list[list[str]]] = []
        self._table_depth = 0
        self._current_table: list[list[str]] | None = None
        self._current_row: list[str] | None = None
        self._cell_parts: list[str] | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        tag = tag.lower()
        if tag == "table":
            self._table_depth += 1
            if self._table_depth == 1:
                self._current_table = []
        elif self._table_depth == 1 and tag == "tr":
            self._current_row = []
        elif self._table_depth == 1 and tag in {"th", "td"} and self._current_row is not None:
            self._cell_parts = []

    def handle_data(self, data: str) -> None:
        if self._cell_parts is not None:
            self._cell_parts.append(data)

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        if self._table_depth == 1 and tag in {"th", "td"} and self._cell_parts is not None:
            if self._current_row is None:
                raise LiraFormatError("HTML cell closed outside a table row")
            self._current_row.append(" ".join("".join(self._cell_parts).split()))
            self._cell_parts = None
        elif self._table_depth == 1 and tag == "tr" and self._current_row is not None:
            if self._current_table is None:
                raise LiraFormatError("HTML row closed outside a table")
            if self._current_row:
                self._current_table.append(self._current_row)
            self._current_row = None
        elif tag == "table" and self._table_depth:
            if self._table_depth == 1:
                if self._current_table is None:
                    raise LiraFormatError("HTML table state is inconsistent")
                self.tables.append(self._current_table)
                self._current_table = None
            self._table_depth -= 1


@dataclass(frozen=True, slots=True)
class HtmlTableSource:
    path: str | Path
    encoding: str = "utf-8"
    table_index: int = 0
    header_row: int = 1

    def read_rows(self) -> list[RawTableRow]:
        if self.table_index < 0:
            raise LiraFormatError("HTML table_index cannot be negative")
        if self.header_row < 1:
            raise LiraFormatError("HTML header_row must be one-based")
        parser = _HtmlTableParser()
        parser.feed(Path(self.path).read_text(encoding=self.encoding))
        parser.close()
        if self.table_index >= len(parser.tables):
            raise LiraFormatError(
                f"{self.path}: table index {self.table_index} does not exist"
            )
        return _rows_from_matrix(
            parser.tables[self.table_index],
            header_index=self.header_row - 1,
            source=str(self.path),
            sheet=f"table[{self.table_index}]",
        )


@dataclass(frozen=True, slots=True)
class XlsxTableSource:
    path: str | Path
    sheet_name: str | None = None
    header_row: int = 1
    data_only: bool = True

    def read_rows(self) -> list[RawTableRow]:
        if isinstance(self.header_row, bool) or not isinstance(self.header_row, int):
            raise LiraFormatError("XLSX header_row must be an integer")
        if self.header_row < 1:
            raise LiraFormatError("XLSX header_row must be one-based")
        if not isinstance(self.data_only, bool):
            raise LiraFormatError("XLSX data_only must be bool")
        if (
            self.sheet_name is None
            or not isinstance(self.sheet_name, str)
            or not self.sheet_name.strip()
        ):
            raise LiraFormatError("XLSX import requires an explicit sheet_name")

        try:
            matrix = worksheet_matrix(
                self.path,
                self.sheet_name,
                data_only=self.data_only,
            )
        except OoxmlReadError as exc:
            raise LiraFormatError(f"{self.path}: {exc}") from exc
        return _rows_from_matrix(
            matrix,
            header_index=self.header_row - 1,
            source=f"{self.path}:{self.sheet_name}",
            sheet=self.sheet_name,
        )
