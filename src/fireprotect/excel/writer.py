"""Copy-only OOXML writer for existing Excel calculation workbooks.

The writer intentionally does not create a replacement spreadsheet.  It copies
the supplied XLSX/XLSM package and changes only explicitly mapped, already
existing cells in the copy.  Formula targets are protected by default.
"""

from __future__ import annotations

import math
import os
import posixpath
import re
import shutil
import tempfile
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from hashlib import sha256
from pathlib import Path
from typing import Any, Iterable
from xml.etree import ElementTree as ET
from zipfile import BadZipFile, ZIP_DEFLATED, ZipFile

from .mapping import (
    CellBinding,
    ColumnBinding,
    FieldResolver,
    WorkbookMapping,
    resolve_field,
)


_MAIN_NS = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
_DOCUMENT_REL_NS = (
    "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
)
_PACKAGE_REL_NS = "http://schemas.openxmlformats.org/package/2006/relationships"
_MC_NS = "http://schemas.openxmlformats.org/markup-compatibility/2006"
_X14AC_NS = "http://schemas.microsoft.com/office/spreadsheetml/2009/9/ac"
_XML_NS = "http://www.w3.org/XML/1998/namespace"

_M = f"{{{_MAIN_NS}}}"
_R = f"{{{_DOCUMENT_REL_NS}}}"
_P = f"{{{_PACKAGE_REL_NS}}}"
_CELL_RE = re.compile(r"^([A-Za-z]{1,3})([1-9][0-9]*)$")
_COLUMN_RE = re.compile(r"^[A-Za-z]{1,3}$")

ET.register_namespace("", _MAIN_NS)
ET.register_namespace("r", _DOCUMENT_REL_NS)
ET.register_namespace("mc", _MC_NS)
ET.register_namespace("x14ac", _X14AC_NS)


class ExcelExportError(ValueError):
    """Base class for safe-export failures."""


class CopyOnlyViolationError(ExcelExportError):
    """Raised when output could alias or overwrite an unintended file."""


class WorkbookMappingError(ExcelExportError):
    """Raised when a binding cannot be applied safely to the template."""


class FormulaOverwriteError(WorkbookMappingError):
    """Raised when a protected formula cell is used as an output target."""


class SourceWorkbookChangedError(ExcelExportError):
    """Raised if the source checksum changes while an export is running."""


@dataclass(frozen=True, slots=True)
class ExcelCopyResult:
    output_path: Path
    source_sha256: str
    written_cells: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class _PendingValue:
    sheet: str
    cell: str
    value: Any


def file_sha256(path: str | Path) -> str:
    """Return a streaming SHA-256 checksum for a workbook or other file."""

    digest = sha256()
    with Path(path).open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def write_mapped_copy(
    source_path: str | Path,
    output_path: str | Path,
    mapping: WorkbookMapping,
    *,
    element: object | None = None,
    elements: Iterable[object] = (),
    field_resolver: FieldResolver = resolve_field,
    overwrite: bool = False,
) -> ExcelCopyResult:
    """Write mapped values to a new copy of an existing workbook.

    Fixed ``CellBinding`` entries read from ``element``; ``ColumnBinding``
    entries read from each object in ``elements`` and advance from
    ``first_row``.  Target cells must already exist, which protects the source
    workbook's established table layout and styles.
    """

    source = Path(source_path).resolve(strict=True)
    output = Path(output_path).resolve(strict=False)
    if source == output:
        raise CopyOnlyViolationError("Source and output workbook must be different")
    if source.suffix.lower() not in {".xlsx", ".xlsm"}:
        raise ExcelExportError("Only OOXML .xlsx and .xlsm workbooks are supported")
    if output.suffix.lower() != source.suffix.lower():
        raise ExcelExportError("Output must keep the source workbook extension")
    if output.exists() and not overwrite:
        raise CopyOnlyViolationError(f"Output workbook already exists: {output}")
    if not isinstance(mapping, WorkbookMapping):
        raise TypeError("mapping must be WorkbookMapping")

    items = tuple(elements)
    pending = _resolve_values(mapping, element, items, field_resolver)
    source_checksum = file_sha256(source)
    output.parent.mkdir(parents=True, exist_ok=True)

    temp_handle = tempfile.NamedTemporaryFile(
        prefix=f".{output.stem}.", suffix=output.suffix, dir=output.parent, delete=False
    )
    temp_path = Path(temp_handle.name)
    temp_handle.close()
    try:
        if not pending:
            shutil.copy2(source, temp_path)
        else:
            _write_ooxml_copy(source, temp_path, pending)
        if file_sha256(source) != source_checksum:
            raise SourceWorkbookChangedError(
                "Source workbook changed during export; output was not installed"
            )
        if output.exists() and not overwrite:
            raise CopyOnlyViolationError(f"Output workbook already exists: {output}")
        os.replace(temp_path, output)
    finally:
        if temp_path.exists():
            temp_path.unlink()

    return ExcelCopyResult(
        output_path=output,
        source_sha256=source_checksum,
        written_cells=tuple(f"{item.sheet}!{item.cell}" for item in pending),
    )


def _resolve_values(
    mapping: WorkbookMapping,
    element: object | None,
    elements: tuple[object, ...],
    resolver: FieldResolver,
) -> tuple[_PendingValue, ...]:
    pending: list[_PendingValue] = []
    if mapping.cells and element is None:
        raise WorkbookMappingError("Cell bindings require element=...")

    for cell_binding in mapping.cells:
        value = _binding_value(element, cell_binding, resolver)
        if value is None and not cell_binding.write_none:
            continue
        pending.append(
            _PendingValue(
                sheet=cell_binding.sheet,
                cell=_normalise_cell(cell_binding.cell),
                value=value,
            )
        )

    for column_binding in mapping.columns:
        column = _normalise_column(column_binding.column)
        if column_binding.last_row is not None:
            capacity = column_binding.last_row - column_binding.first_row + 1
            if len(elements) > capacity:
                raise WorkbookMappingError(
                    f"Column {column_binding.sheet}!{column} accepts {capacity} rows, "
                    f"got {len(elements)}"
                )
        for offset, item in enumerate(elements):
            value = _binding_value(item, column_binding, resolver)
            if value is None and not column_binding.write_none:
                continue
            pending.append(
                _PendingValue(
                    sheet=column_binding.sheet,
                    cell=f"{column}{column_binding.first_row + offset}",
                    value=value,
                )
            )

    seen: set[tuple[str, str]] = set()
    for item in pending:
        key = (item.sheet, item.cell)
        if key in seen:
            raise WorkbookMappingError(
                f"More than one binding targets {item.sheet}!{item.cell}"
            )
        seen.add(key)
    return tuple(pending)


def _binding_value(
    source: object | None,
    binding: CellBinding | ColumnBinding,
    resolver: FieldResolver,
) -> Any:
    if source is None:
        raise WorkbookMappingError(
            f"Binding {binding.field!r} requires a source object"
        )
    value = resolver(source, binding.field)
    if binding.transform is not None:
        value = binding.transform(value)
    return value


def _normalise_cell(cell: str) -> str:
    match = _CELL_RE.fullmatch(cell.strip())
    if match is None:
        raise WorkbookMappingError(f"Invalid A1 cell coordinate: {cell!r}")
    column = _normalise_column(match.group(1))
    return f"{column}{int(match.group(2))}"


def _normalise_column(column: str) -> str:
    value = column.strip().upper()
    if _COLUMN_RE.fullmatch(value) is None or _column_number(value) > 16_384:
        raise WorkbookMappingError(f"Invalid Excel column: {column!r}")
    return value


def _column_number(column: str) -> int:
    number = 0
    for char in column:
        number = number * 26 + ord(char) - ord("A") + 1
    return number


def _write_ooxml_copy(
    source: Path, destination: Path, pending: tuple[_PendingValue, ...]
) -> None:
    try:
        with ZipFile(source, "r") as workbook:
            changed = _changed_parts(workbook, pending)
            with ZipFile(destination, "w", compression=ZIP_DEFLATED, allowZip64=True) as copy:
                for info in workbook.infolist():
                    copy.writestr(info, changed.get(info.filename, workbook.read(info)))
    except BadZipFile as exc:
        raise ExcelExportError(f"Invalid OOXML workbook: {source}") from exc


def _changed_parts(
    workbook: ZipFile, pending: tuple[_PendingValue, ...]
) -> dict[str, bytes]:
    try:
        workbook_xml = ET.fromstring(workbook.read("xl/workbook.xml"))
        relationships = ET.fromstring(workbook.read("xl/_rels/workbook.xml.rels"))
    except (KeyError, ET.ParseError) as exc:
        raise ExcelExportError("Workbook is missing valid workbook metadata") from exc

    relation_targets = {
        relation.get("Id"): relation.get("Target")
        for relation in relationships.findall(_P + "Relationship")
    }
    sheet_parts: dict[str, str] = {}
    sheets = workbook_xml.find(_M + "sheets")
    if sheets is None:
        raise ExcelExportError("Workbook contains no worksheets")
    for sheet in sheets.findall(_M + "sheet"):
        relation_id = sheet.get(_R + "id")
        target = relation_targets.get(relation_id)
        if target is None:
            continue
        part = (
            target.lstrip("/")
            if target.startswith("/")
            else posixpath.normpath(posixpath.join("xl", target))
        )
        sheet_parts[sheet.get("name", "")] = part

    grouped: dict[str, list[_PendingValue]] = {}
    for item in pending:
        try:
            part = sheet_parts[item.sheet]
        except KeyError as exc:
            raise WorkbookMappingError(f"Worksheet not found: {item.sheet!r}") from exc
        grouped.setdefault(part, []).append(item)

    changed: dict[str, bytes] = {}
    for part, values in grouped.items():
        try:
            root = ET.fromstring(workbook.read(part))
        except (KeyError, ET.ParseError) as exc:
            raise ExcelExportError(f"Worksheet part is missing or invalid: {part}") from exc
        cells = {cell.get("r"): cell for cell in root.iter(_M + "c")}
        merged = tuple(
            merge.get("ref", "") for merge in root.findall(f".//{_M}mergeCell")
        )
        for item in values:
            cell = cells.get(item.cell)
            if cell is None:
                raise WorkbookMappingError(
                    f"Mapped cell does not exist in template: {item.sheet}!{item.cell}"
                )
            _reject_non_anchor_merged_cell(item, merged)
            formula = cell.find(_M + "f")
            if formula is not None:
                raise FormulaOverwriteError(
                    f"Formula target is protected: {item.sheet}!{item.cell}"
                )
            _set_cell_value(cell, item.value)
        changed[part] = ET.tostring(root, encoding="utf-8", xml_declaration=True)

    calc = workbook_xml.find(_M + "calcPr")
    if calc is None:
        calc = ET.SubElement(workbook_xml, _M + "calcPr")
    calc.set("calcMode", "auto")
    calc.set("fullCalcOnLoad", "1")
    calc.set("forceFullCalc", "1")
    changed["xl/workbook.xml"] = ET.tostring(
        workbook_xml, encoding="utf-8", xml_declaration=True
    )
    return changed


def _reject_non_anchor_merged_cell(
    item: _PendingValue, merged_ranges: tuple[str, ...]
) -> None:
    item_col, item_row = _split_coordinate(item.cell)
    for merged_range in merged_ranges:
        if ":" not in merged_range:
            continue
        start, end = merged_range.split(":", 1)
        start_col, start_row = _split_coordinate(start)
        end_col, end_row = _split_coordinate(end)
        if (
            _column_number(start_col)
            <= _column_number(item_col)
            <= _column_number(end_col)
            and start_row <= item_row <= end_row
            and item.cell != start
        ):
            raise WorkbookMappingError(
                f"Cannot write non-anchor merged cell {item.sheet}!{item.cell} "
                f"inside {merged_range}"
            )


def _split_coordinate(cell: str) -> tuple[str, int]:
    match = _CELL_RE.fullmatch(cell)
    if match is None:
        raise WorkbookMappingError(f"Invalid A1 cell coordinate: {cell!r}")
    return match.group(1).upper(), int(match.group(2))


def _set_cell_value(cell: ET.Element, value: Any) -> None:
    for tag in (_M + "v", _M + "is"):
        child = cell.find(tag)
        if child is not None:
            cell.remove(child)

    if value is None:
        cell.attrib.pop("t", None)
        return
    if isinstance(value, bool):
        cell.set("t", "b")
        ET.SubElement(cell, _M + "v").text = "1" if value else "0"
        return
    if isinstance(value, Decimal):
        if not value.is_finite():
            raise WorkbookMappingError("Excel numeric values must be finite")
        cell.attrib.pop("t", None)
        ET.SubElement(cell, _M + "v").text = format(value, "f")
        return
    if isinstance(value, int):
        cell.attrib.pop("t", None)
        ET.SubElement(cell, _M + "v").text = str(value)
        return
    if isinstance(value, float):
        # Generic workbook callers may supply external-library floats. Domain
        # engineering adapters emit Decimal before reaching this boundary.
        if not math.isfinite(value):
            raise WorkbookMappingError("Excel numeric values must be finite")
        cell.attrib.pop("t", None)
        ET.SubElement(cell, _M + "v").text = repr(value)
        return
    if isinstance(value, (datetime, date)):
        cell.set("t", "d")
        ET.SubElement(cell, _M + "v").text = value.isoformat()
        return
    if isinstance(value, str):
        cell.set("t", "inlineStr")
        inline = ET.SubElement(cell, _M + "is")
        text = ET.SubElement(inline, _M + "t")
        if value[:1].isspace() or value[-1:].isspace():
            text.set(f"{{{_XML_NS}}}space", "preserve")
        text.text = value
        return
    raise WorkbookMappingError(
        f"Unsupported Excel scalar type {type(value).__name__}; "
        "use a binding transform"
    )
