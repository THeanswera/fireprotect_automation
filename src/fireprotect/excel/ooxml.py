from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path
import posixpath
import re
from xml.etree import ElementTree as ET
from zipfile import BadZipFile, ZipFile


_MAIN_NS = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
_DOCUMENT_REL_NS = (
    "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
)
_PACKAGE_REL_NS = "http://schemas.openxmlformats.org/package/2006/relationships"
_M = f"{{{_MAIN_NS}}}"
_R = f"{{{_DOCUMENT_REL_NS}}}"
_P = f"{{{_PACKAGE_REL_NS}}}"
_CELL_RE = re.compile(r"^([A-Za-z]{1,3})([1-9][0-9]*)$")


class OoxmlReadError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class RawWorksheetCell:
    coordinate: str
    value: str | bool | None
    data_type: str
    formula: str | None


def _sheet_parts(archive: ZipFile) -> dict[str, str]:
    try:
        workbook = ET.fromstring(archive.read("xl/workbook.xml"))
        relationships = ET.fromstring(
            archive.read("xl/_rels/workbook.xml.rels")
        )
    except (KeyError, ET.ParseError) as exc:
        raise OoxmlReadError("Workbook metadata is missing or invalid") from exc
    targets = {
        relation.get("Id"): relation.get("Target")
        for relation in relationships.findall(_P + "Relationship")
    }
    sheets = workbook.find(_M + "sheets")
    if sheets is None:
        raise OoxmlReadError("Workbook contains no worksheets")
    result: dict[str, str] = {}
    for sheet in sheets.findall(_M + "sheet"):
        name = sheet.get("name")
        target = targets.get(sheet.get(_R + "id"))
        if name is None or target is None:
            continue
        part = (
            target.lstrip("/")
            if target.startswith("/")
            else posixpath.normpath(posixpath.join("xl", target))
        )
        result[name] = part
    return result


def _shared_strings(archive: ZipFile) -> tuple[str, ...]:
    try:
        root = ET.fromstring(archive.read("xl/sharedStrings.xml"))
    except KeyError:
        return ()
    except ET.ParseError as exc:
        raise OoxmlReadError("Shared-string table is invalid") from exc
    return tuple("".join(node.text or "" for node in item.iter(_M + "t")) for item in root.findall(_M + "si"))


def _cell_value(
    cell: ET.Element,
    shared_strings: tuple[str, ...],
    *,
    data_only: bool,
) -> RawWorksheetCell:
    coordinate = cell.get("r", "")
    data_type = cell.get("t", "n")
    formula_node = cell.find(_M + "f")
    formula = None if formula_node is None else (formula_node.text or "")
    if formula is not None and not data_only:
        return RawWorksheetCell(coordinate, f"={formula}", data_type, formula)
    if data_type == "inlineStr":
        inline = cell.find(_M + "is")
        inline_value = (
            ""
            if inline is None
            else "".join(node.text or "" for node in inline.iter(_M + "t"))
        )
        return RawWorksheetCell(coordinate, inline_value, data_type, formula)
    value_node = cell.find(_M + "v")
    token = None if value_node is None else value_node.text
    if token is None:
        value: str | bool | None = None
    elif data_type == "s":
        try:
            value = shared_strings[int(token)]
        except (ValueError, IndexError) as exc:
            raise OoxmlReadError(
                f"Invalid shared-string index in cell {coordinate}: {token!r}"
            ) from exc
    elif data_type == "b":
        value = token == "1"
    else:
        # Numeric cells intentionally remain their exact OOXML decimal token.
        value = token
    return RawWorksheetCell(coordinate, value, data_type, formula)


def read_raw_worksheet(
    path: str | Path, sheet_name: str, *, data_only: bool = True
) -> tuple[RawWorksheetCell, ...]:
    try:
        with ZipFile(Path(path), "r") as archive:
            parts = _sheet_parts(archive)
            try:
                part = parts[sheet_name]
            except KeyError as exc:
                raise OoxmlReadError(
                    f"Worksheet {sheet_name!r} does not exist"
                ) from exc
            try:
                root = ET.fromstring(archive.read(part))
            except (KeyError, ET.ParseError) as exc:
                raise OoxmlReadError(
                    f"Worksheet part is missing or invalid: {part}"
                ) from exc
            strings = _shared_strings(archive)
            return tuple(
                _cell_value(cell, strings, data_only=data_only)
                for cell in root.iter(_M + "c")
            )
    except BadZipFile as exc:
        raise OoxmlReadError(f"Invalid OOXML workbook: {path}") from exc


def worksheet_matrix(
    path: str | Path, sheet_name: str, *, data_only: bool = True
) -> list[list[str | bool | None]]:
    cells = read_raw_worksheet(path, sheet_name, data_only=data_only)
    indexed: list[tuple[int, int, str | bool | None]] = []
    max_row = 0
    max_column = 0
    for cell in cells:
        match = _CELL_RE.fullmatch(cell.coordinate)
        if match is None:
            raise OoxmlReadError(
                f"Invalid worksheet coordinate: {cell.coordinate!r}"
            )
        column = 0
        for char in match.group(1).upper():
            column = column * 26 + ord(char) - ord("A") + 1
        row = int(match.group(2))
        indexed.append((row, column, cell.value))
        max_row = max(max_row, row)
        max_column = max(max_column, column)
    matrix: list[list[str | bool | None]] = [
        [None] * max_column for _ in range(max_row)
    ]
    for row, column, value in indexed:
        matrix[row - 1][column - 1] = value
    return matrix


def worksheet_content_fingerprint(
    path: str | Path, sheet_names: tuple[str, ...]
) -> str:
    """Fingerprint formulas and exact stored values in selected lookup sheets."""

    digest = sha256()
    for sheet_name in sheet_names:
        digest.update(b"sheet\0")
        digest.update(sheet_name.encode("utf-8"))
        digest.update(b"\0")
        for cell in read_raw_worksheet(path, sheet_name, data_only=True):
            digest.update(cell.coordinate.encode("ascii"))
            digest.update(b"\0")
            digest.update(cell.data_type.encode("ascii"))
            digest.update(b"\0")
            if cell.formula is not None:
                digest.update(cell.formula.encode("utf-8"))
            digest.update(b"\0")
            if cell.value is not None:
                digest.update(str(cell.value).encode("utf-8"))
            digest.update(b"\0")
    return digest.hexdigest()
