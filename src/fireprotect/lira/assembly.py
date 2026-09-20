"""Assemble one LIRA model from its exported tables.

LIRA-SAPR writes forces, elements, stiffnesses and nodes into four separate
Excel tables, and only the element table carries the stiffness type that leads
to a profile. This module reads those tables, joins them by their own keys and
reports every missing or contradictory link instead of filling a gap silently.

Three rules shape the result:

* the section *kind* word is preserved. ``Уголок параллельно полкам 80 x 6``
  and ``Профиль "Молодечно" 120 x 6`` reduce to the same bare numbers as a
  rectangular tube, so discarding the kind word would silently turn an angle
  into a tube.
* nothing is guessed. A missing stiffness type, a missing node or an
  unparsable node list becomes a blocker on that element, not a default value.
* a length is arithmetic, so it may be computed, but it is computed with
  ``Decimal`` from the exported coordinates and keeps its source rows.

No profile is resolved against the section database here and no PTM is
computed: both need an explicit engineering declaration about the governing
standard and the heating scheme, which this module neither invents nor
implies.
"""

from __future__ import annotations

import csv
import hashlib
import json
import re
from collections import OrderedDict
from dataclasses import dataclass, field, replace
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Mapping, Sequence

from .errors import LiraFormatError
from .sources import XlsxTableSource
from .types import RawTableRow

# Exact headers as LIRA-SAPR writes them. A table whose headers differ is a
# different export and must be mapped deliberately, not parsed optimistically.
STIFFNESS_COLUMNS = {
    "type_id": "Тип жесткости",
    "name": "Имя",
}
ELEMENT_COLUMNS = {
    "element_id": "№ элем",
    "element_type": "Тип элем",
    "section_count": "Кол.сечений",
    "stiffness_type": "Тип жестк",
    "rotation_angle": "Угол м.осей",
    "rigid_insert_start": "AX н",
    "rigid_insert_end": "AX к",
    "nodes": "№№ узлов",
}
NODE_COLUMNS = {
    "node_id": "№ узла",
    "x": "X\n(м)",
    "y": "Y\n(м)",
    "z": "Z\n(м)",
    "support_x": "X",
    "support_y": "Y",
    "support_z": "Z",
    "support_ux": "UX",
    "support_uy": "UY",
    "support_uz": "UZ",
}

_KIND_WORDS = (
    "Двутавр",
    "Швеллер",
    "Уголок",
    "Тавр",
    "Труба",
    "Профиль",
    "Квадрат",
    "Прямоугольник",
)

_NAME_PATTERN = re.compile(r"^(?P<body>.*?)\s*\((?P<mark>[^()]*)\)\s*$")


def _write_new(path: Path, content: str) -> None:
    with path.open("x", encoding="utf-8", newline="\n") as stream:
        stream.write(content)


def _write_json(path: Path, payload: object) -> None:
    _write_new(path, json.dumps(payload, ensure_ascii=False, indent=2) + "\n")


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(65536), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _text(row: RawTableRow, header: str) -> str | None:
    value = row.values.get(header)
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _require_rows(
    rows: Sequence[RawTableRow], *, path: object, table: str
) -> Sequence[RawTableRow]:
    """Refuse a table that has a header but no data row.

    Without this guard an empty export reaches the header lookup as an index
    error, which reads as a program fault instead of an unusable export.
    """

    if not rows:
        raise LiraFormatError(
            f"{path}: {table} contains a header but no data rows"
        )
    return rows


def _decimal_from_token(token: str, *, context: str) -> Decimal:
    text = token.strip()
    if not text:
        raise LiraFormatError(f"{context}: empty numeric token")
    if "," in text and "." in text:
        raise LiraFormatError(
            f"{context}: token {token!r} mixes decimal separators and is ambiguous"
        )
    if "," in text:
        text = text.replace(",", ".")
    try:
        return Decimal(text)
    except InvalidOperation as exc:
        raise LiraFormatError(f"{context}: token {token!r} is not a number") from exc


@dataclass(frozen=True, slots=True)
class LiraStiffness:
    """One stiffness type with the section name exactly as LIRA wrote it."""

    type_id: str
    raw_name: str
    kind_word: str | None
    designation: str | None
    mark: str | None
    parameters: tuple[str, ...]
    source_row: int
    source_cell: str | None

    @property
    def has_profile_identity(self) -> bool:
        return self.designation is not None

    def as_dict(self) -> dict[str, object]:
        return {
            "type_id": self.type_id,
            "raw_name": self.raw_name,
            "kind_word": self.kind_word,
            "designation": self.designation,
            "mark": self.mark,
            "parameters": list(self.parameters),
            "source": {"row": self.source_row, "cell": self.source_cell},
        }


@dataclass(frozen=True, slots=True)
class LiraNode:
    node_id: str
    x: Decimal | None
    y: Decimal | None
    z: Decimal | None
    supports: Mapping[str, str]
    source_row: int

    def as_dict(self) -> dict[str, object]:
        return {
            "node_id": self.node_id,
            "coordinates_m": {
                "x": None if self.x is None else str(self.x),
                "y": None if self.y is None else str(self.y),
                "z": None if self.z is None else str(self.z),
            },
            "supports": dict(self.supports),
            "source_row": self.source_row,
        }


@dataclass(frozen=True, slots=True)
class LiraElement:
    element_id: str
    element_type: str | None
    section_count: str | None
    stiffness_type: str | None
    rotation_angle_degrees: Decimal | None
    rigid_insert_start: str | None
    rigid_insert_end: str | None
    node_ids: tuple[str, ...]
    source_row: int

    def as_dict(self) -> dict[str, object]:
        return {
            "element_id": self.element_id,
            "element_type": self.element_type,
            "section_count": self.section_count,
            "stiffness_type": self.stiffness_type,
            "rotation_angle_degrees": (
                None
                if self.rotation_angle_degrees is None
                else str(self.rotation_angle_degrees)
            ),
            "rigid_insert_start": self.rigid_insert_start,
            "rigid_insert_end": self.rigid_insert_end,
            "node_ids": list(self.node_ids),
            "source_row": self.source_row,
        }


@dataclass(frozen=True, slots=True)
class LiraForceCandidateView:
    """One accepted force row offered for an assembled element.

    Every value is copied from the review bundle: the exact ``Decimal`` string,
    the source unit and the raw token. Nothing is converted to an RX3 component
    and no candidate is preferred over another.
    """

    candidate_id: str
    section_station: str | None
    load_case: str | None
    combination: str | None
    components: Mapping[str, str | None]
    units: Mapping[str, str | None]

    def as_dict(self) -> dict[str, object]:
        return {
            "candidate_id": self.candidate_id,
            "section_station": self.section_station,
            "load_case": self.load_case,
            "combination": self.combination,
            "components": dict(self.components),
            "units": dict(self.units),
        }


@dataclass(frozen=True, slots=True)
class LiraAssembledElement:
    """One element joined to its stiffness, its nodes, its length and forces."""

    element_id: str
    element_type: str | None
    section_count: str | None
    mark: str | None
    kind_word: str | None
    designation: str | None
    stiffness_type: str | None
    rotation_angle_degrees: Decimal | None
    node_ids: tuple[str, ...]
    length_m: Decimal | None
    supports: Mapping[str, str]
    blockers: tuple[str, ...]
    source_rows: Mapping[str, int]
    force_candidates: tuple[LiraForceCandidateView, ...] = ()

    @property
    def passed(self) -> bool:
        return not self.blockers

    def as_dict(self) -> dict[str, object]:
        return {
            "element_id": self.element_id,
            "status": "ASSEMBLED" if self.passed else "BLOCKED",
            "identity": {
                "mark": self.mark,
                "kind_word": self.kind_word,
                "designation": self.designation,
                "element_type": self.element_type,
                "stiffness_type": self.stiffness_type,
                "section_count": self.section_count,
            },
            "geometry": {
                "node_ids": list(self.node_ids),
                "length_m": None if self.length_m is None else str(self.length_m),
                "rotation_angle_degrees": (
                    None
                    if self.rotation_angle_degrees is None
                    else str(self.rotation_angle_degrees)
                ),
                "supports": dict(self.supports),
            },
            "force_candidates": [item.as_dict() for item in self.force_candidates],
            "blockers": list(self.blockers),
            "source_rows": dict(self.source_rows),
        }


@dataclass(frozen=True, slots=True)
class LiraModelAssembly:
    elements: tuple[LiraAssembledElement, ...]
    stiffnesses: tuple[LiraStiffness, ...]
    nodes: tuple[LiraNode, ...]
    sources: Mapping[str, Mapping[str, str]]
    elements_without_forces: tuple[str, ...] = ()
    written_files: tuple[Path, ...] = field(default=())

    @property
    def blocked_elements(self) -> tuple[LiraAssembledElement, ...]:
        return tuple(item for item in self.elements if not item.passed)

    @property
    def force_candidates_total(self) -> int:
        return sum(len(item.force_candidates) for item in self.elements)

    @property
    def marks(self) -> tuple[str, ...]:
        return tuple(
            sorted({item.mark for item in self.elements if item.mark}, key=_mark_key)
        )

    def as_dict(self) -> dict[str, object]:
        return {
            "status": "MODEL_ASSEMBLED",
            "elements": len(self.elements),
            "elements_blocked": len(self.blocked_elements),
            "stiffness_types": len(self.stiffnesses),
            "nodes": len(self.nodes),
            "marks": list(self.marks),
            "force_candidates": self.force_candidates_total,
            "elements_without_forces": len(self.elements_without_forces),
            "sources": {name: dict(value) for name, value in self.sources.items()},
            "profile_resolution": None,
            "profile_resolution_validated": False,
            "ptm": None,
            "ptm_validated": False,
            "rx38_force_generation_allowed": False,
            "issue_readiness": "NOT_READY_FOR_ISSUE",
            "written_files": [str(path) for path in self.written_files],
        }


def _mark_key(value: str) -> tuple[int, int | str]:
    match = re.match(r"^([A-Za-zА-Яа-я]+)(\d+)$", value.strip())
    if match:
        return (0, int(match.group(2)))
    return (1, value)


def parse_stiffness_name(raw_name: str) -> tuple[str | None, str | None, str | None]:
    """Split a LIRA stiffness name into kind word, designation and mark.

    ``Двутавр 30К1 (К1)`` becomes ``("Двутавр", "30К1", "К1")``. The kind word
    is returned because the same numbers describe different sections under
    different kind words.
    """

    name = " ".join(raw_name.split())
    if not name:
        return (None, None, None)
    mark: str | None = None
    body = name
    match = _NAME_PATTERN.match(name)
    if match:
        mark = match.group("mark").strip() or None
        body = match.group("body").strip()
    if not body:
        return (None, None, mark)
    kind_word: str | None = None
    for word in _KIND_WORDS:
        if body.casefold().startswith(word.casefold()):
            kind_word = word
            body = body[len(word) :].strip()
            break
    designation = body or None
    return (kind_word, designation, mark)


def read_stiffness_table(
    path: str | Path,
    *,
    sheet_name: str,
    header_row: int,
) -> tuple[LiraStiffness, ...]:
    """Read the stiffness table, keeping its multi-row parameter blocks."""

    rows = _require_rows(
        XlsxTableSource(
            path=path, sheet_name=sheet_name, header_row=header_row
        ).read_rows(),
        path=path,
        table="stiffness table",
    )
    type_header = STIFFNESS_COLUMNS["type_id"]
    name_header = STIFFNESS_COLUMNS["name"]
    headers = list(rows[0].values.keys())
    if type_header not in headers or name_header not in headers:
        raise LiraFormatError(
            f"{path}: stiffness table is missing {type_header!r} or {name_header!r}"
        )
    parameter_headers = [item for item in headers if item not in {type_header, name_header}]

    collected: OrderedDict[str, dict[str, Any]] = OrderedDict()
    current: str | None = None
    for row in rows:
        type_id = _text(row, type_header)
        raw_name = _text(row, name_header)
        if type_id is not None:
            if type_id in collected:
                raise LiraFormatError(
                    f"{path}: stiffness type {type_id!r} is declared twice "
                    f"(rows {collected[type_id]['source_row']} and {row.row_number})"
                )
            if raw_name is None:
                raise LiraFormatError(
                    f"{path}: stiffness type {type_id!r} has no name (row {row.row_number})"
                )
            collected[type_id] = {
                "raw_name": raw_name,
                "parameters": [],
                "source_row": row.row_number,
                "source_cell": row.cells.get(name_header),
            }
            current = type_id
        if current is None:
            continue
        for header in parameter_headers:
            value = _text(row, header)
            if value is not None:
                collected[current]["parameters"].append(value)

    result: list[LiraStiffness] = []
    for type_id, payload in collected.items():
        kind_word, designation, mark = parse_stiffness_name(str(payload["raw_name"]))
        result.append(
            LiraStiffness(
                type_id=type_id,
                raw_name=str(payload["raw_name"]),
                kind_word=kind_word,
                designation=designation,
                mark=mark,
                parameters=tuple(payload["parameters"]),
                source_row=int(payload["source_row"]),
                source_cell=(
                    None
                    if payload["source_cell"] is None
                    else str(payload["source_cell"])
                ),
            )
        )
    return tuple(result)


def _parse_node_ids(token: str, *, context: str) -> tuple[str, ...]:
    parts = [item.strip() for item in token.replace(";", ",").split(",")]
    return tuple(item for item in parts if item)


def read_element_table(
    path: str | Path,
    *,
    sheet_name: str,
    header_row: int,
) -> tuple[LiraElement, ...]:
    rows = _require_rows(
        XlsxTableSource(
            path=path, sheet_name=sheet_name, header_row=header_row
        ).read_rows(),
        path=path,
        table="element table",
    )
    headers = list(rows[0].values.keys())
    missing = [value for value in ELEMENT_COLUMNS.values() if value not in headers]
    if missing:
        raise LiraFormatError(f"{path}: element table is missing columns {missing}")

    result: list[LiraElement] = []
    seen: dict[str, int] = {}
    for row in rows:
        element_id = _text(row, ELEMENT_COLUMNS["element_id"])
        if element_id is None:
            raise LiraFormatError(f"{path}: row {row.row_number} has no element id")
        if element_id in seen:
            raise LiraFormatError(
                f"{path}: element {element_id!r} appears twice "
                f"(rows {seen[element_id]} and {row.row_number})"
            )
        seen[element_id] = row.row_number
        node_token = _text(row, ELEMENT_COLUMNS["nodes"])
        node_ids = (
            ()
            if node_token is None
            else _parse_node_ids(node_token, context=f"{path} row {row.row_number}")
        )
        angle_token = _text(row, ELEMENT_COLUMNS["rotation_angle"])
        angle: Decimal | None = None
        if angle_token is not None:
            angle = _decimal_from_token(
                angle_token, context=f"{path} row {row.row_number} rotation angle"
            )
        result.append(
            LiraElement(
                element_id=element_id,
                element_type=_text(row, ELEMENT_COLUMNS["element_type"]),
                section_count=_text(row, ELEMENT_COLUMNS["section_count"]),
                stiffness_type=_text(row, ELEMENT_COLUMNS["stiffness_type"]),
                rotation_angle_degrees=angle,
                rigid_insert_start=_text(row, ELEMENT_COLUMNS["rigid_insert_start"]),
                rigid_insert_end=_text(row, ELEMENT_COLUMNS["rigid_insert_end"]),
                node_ids=node_ids,
                source_row=row.row_number,
            )
        )
    return tuple(result)


def read_node_table(
    path: str | Path,
    *,
    sheet_name: str,
    header_row: int,
) -> tuple[LiraNode, ...]:
    rows = _require_rows(
        XlsxTableSource(
            path=path, sheet_name=sheet_name, header_row=header_row
        ).read_rows(),
        path=path,
        table="node table",
    )
    headers = list(rows[0].values.keys())
    missing = [value for value in NODE_COLUMNS.values() if value not in headers]
    if missing:
        raise LiraFormatError(f"{path}: node table is missing columns {missing}")

    result: list[LiraNode] = []
    seen: dict[str, int] = {}
    for row in rows:
        node_id = _text(row, NODE_COLUMNS["node_id"])
        if node_id is None:
            raise LiraFormatError(f"{path}: row {row.row_number} has no node id")
        if node_id in seen:
            raise LiraFormatError(
                f"{path}: node {node_id!r} appears twice "
                f"(rows {seen[node_id]} and {row.row_number})"
            )
        seen[node_id] = row.row_number
        coordinates: dict[str, Decimal | None] = {}
        for axis in ("x", "y", "z"):
            token = _text(row, NODE_COLUMNS[axis])
            coordinates[axis] = (
                None
                if token is None
                else _decimal_from_token(
                    token, context=f"{path} node {node_id} coordinate {axis}"
                )
            )
        supports = {
            name: value
            for name, header in (
                ("x", NODE_COLUMNS["support_x"]),
                ("y", NODE_COLUMNS["support_y"]),
                ("z", NODE_COLUMNS["support_z"]),
                ("ux", NODE_COLUMNS["support_ux"]),
                ("uy", NODE_COLUMNS["support_uy"]),
                ("uz", NODE_COLUMNS["support_uz"]),
            )
            if (value := _text(row, header)) is not None
        }
        result.append(
            LiraNode(
                node_id=node_id,
                x=coordinates["x"],
                y=coordinates["y"],
                z=coordinates["z"],
                supports=supports,
                source_row=row.row_number,
            )
        )
    return tuple(result)


def element_length_m(
    start: LiraNode,
    end: LiraNode,
) -> Decimal:
    """Straight-line distance between two nodes, in metres.

    Raises when a coordinate is missing: a length built from an absent
    coordinate would be a fabricated number.
    """

    if None in (start.x, start.y, start.z, end.x, end.y, end.z):
        raise LiraFormatError(
            f"nodes {start.node_id} and {end.node_id} do not both carry coordinates"
        )
    dx = (end.x - start.x)  # type: ignore[operator]
    dy = (end.y - start.y)  # type: ignore[operator]
    dz = (end.z - start.z)  # type: ignore[operator]
    return (dx * dx + dy * dy + dz * dz).sqrt()


def attach_force_candidates(
    elements: Sequence[LiraAssembledElement],
    forces_path: str | Path | None,
) -> tuple[tuple[LiraAssembledElement, ...], tuple[str, ...]]:
    """Attach every accepted force row of a review bundle to its element.

    Returns the elements and the element ids that carry no force row. A missing
    force row is reported, never filled with zero.
    """

    if forces_path is None:
        return (tuple(elements), ())

    from .selection import read_candidates

    candidates, _ = read_candidates(forces_path)
    grouped: dict[str, list[LiraForceCandidateView]] = {}
    for item in candidates:
        grouped.setdefault(item.element_id, []).append(
            LiraForceCandidateView(
                candidate_id=item.candidate_id,
                section_station=item.section_station,
                load_case=item.load_case,
                combination=item.combination,
                components={
                    name: (None if value.normalized_value is None else value.normalized_value)
                    for name, value in sorted(item.values.items())
                },
                units={
                    name: (None if value.normalized_unit is None else value.normalized_unit)
                    for name, value in sorted(item.values.items())
                },
            )
        )

    result: list[LiraAssembledElement] = []
    missing: list[str] = []
    for element in elements:
        found = tuple(grouped.get(element.element_id, ()))
        if not found:
            missing.append(element.element_id)
        result.append(replace(element, force_candidates=found))
    return (tuple(result), tuple(missing))


def assemble_lira_model(
    *,
    stiffnesses: Sequence[LiraStiffness],
    elements: Sequence[LiraElement],
    nodes: Sequence[LiraNode],
) -> tuple[LiraAssembledElement, ...]:
    """Join elements to their stiffness type and to their nodes."""

    stiffness_by_type = {item.type_id: item for item in stiffnesses}
    node_by_id = {item.node_id: item for item in nodes}
    result: list[LiraAssembledElement] = []

    for element in elements:
        blockers: list[str] = []
        stiffness = (
            None
            if element.stiffness_type is None
            else stiffness_by_type.get(element.stiffness_type)
        )
        if element.stiffness_type is None:
            blockers.append("LIRA_ELEMENT_STIFFNESS_TYPE_MISSING")
        elif stiffness is None:
            blockers.append("LIRA_STIFFNESS_TYPE_NOT_FOUND")
        elif not stiffness.has_profile_identity:
            blockers.append("LIRA_STIFFNESS_NAME_WITHOUT_DESIGNATION")
        if stiffness is not None and stiffness.mark is None:
            blockers.append("LIRA_STIFFNESS_NAME_WITHOUT_MARK")

        if len(element.node_ids) < 2:
            blockers.append("LIRA_ELEMENT_NODE_LIST_INCOMPLETE")
        resolved_nodes = [node_by_id.get(item) for item in element.node_ids]
        for node_id, node in zip(element.node_ids, resolved_nodes):
            if node is None:
                blockers.append(f"LIRA_NODE_NOT_FOUND:{node_id}")

        length: Decimal | None = None
        if len(element.node_ids) == 2 and all(resolved_nodes):
            try:
                length = element_length_m(resolved_nodes[0], resolved_nodes[1])  # type: ignore[arg-type]
            except LiraFormatError:
                blockers.append("LIRA_NODE_COORDINATES_INCOMPLETE")
        elif len(element.node_ids) > 2:
            blockers.append("LIRA_ELEMENT_NOT_A_TWO_NODE_BAR")

        supports: dict[str, str] = {}
        if len(element.node_ids) == 2 and all(resolved_nodes):
            end_node = resolved_nodes[1]
            if end_node is not None:
                supports = dict(end_node.supports)

        result.append(
            LiraAssembledElement(
                element_id=element.element_id,
                element_type=element.element_type,
                section_count=element.section_count,
                mark=None if stiffness is None else stiffness.mark,
                kind_word=None if stiffness is None else stiffness.kind_word,
                designation=None if stiffness is None else stiffness.designation,
                stiffness_type=element.stiffness_type,
                rotation_angle_degrees=element.rotation_angle_degrees,
                node_ids=element.node_ids,
                length_m=length,
                supports=supports,
                blockers=tuple(dict.fromkeys(blockers)),
                source_rows={
                    "element": element.source_row,
                    **(
                        {}
                        if stiffness is None
                        else {"stiffness": stiffness.source_row}
                    ),
                },
            )
        )
    return tuple(result)


_MODEL_FILES = (
    "manifest.json",
    "stiffnesses.json",
    "nodes.json",
    "elements.json",
    "elements.csv",
    "README_MODEL.md",
)


def _element_csv_rows(
    elements: Sequence[LiraAssembledElement],
) -> tuple[list[str], list[list[str]]]:
    header = [
        "element_id",
        "mark",
        "kind_word",
        "designation",
        "stiffness_type",
        "element_type",
        "section_count",
        "node_start",
        "node_end",
        "length_m",
        "rotation_angle_degrees",
        "supports",
        "status",
        "blockers",
    ]
    rows: list[list[str]] = []
    for item in elements:
        rows.append(
            [
                item.element_id,
                item.mark or "",
                item.kind_word or "",
                item.designation or "",
                item.stiffness_type or "",
                item.element_type or "",
                item.section_count or "",
                item.node_ids[0] if item.node_ids else "",
                item.node_ids[1] if len(item.node_ids) > 1 else "",
                "" if item.length_m is None else str(item.length_m),
                (
                    ""
                    if item.rotation_angle_degrees is None
                    else str(item.rotation_angle_degrees)
                ),
                ";".join(f"{k}={v}" for k, v in sorted(item.supports.items())),
                "ASSEMBLED" if item.passed else "BLOCKED",
                ";".join(item.blockers),
            ]
        )
    return header, rows


def prepare_lira_model_bundle(
    *,
    stiffness_path: str | Path,
    element_path: str | Path,
    node_path: str | Path,
    output_dir: str | Path,
    forces_path: str | Path | None = None,
    stiffness_sheet: str = "Лист1",
    stiffness_header_row: int = 2,
    element_sheet: str = " ",
    element_header_row: int = 3,
    node_sheet: str = " ",
    node_header_row: int = 3,
) -> LiraModelAssembly:
    """Write a new, review-only assembly bundle for one LIRA model."""

    stiffness_file = Path(stiffness_path).resolve(strict=True)
    element_file = Path(element_path).resolve(strict=True)
    node_file = Path(node_path).resolve(strict=True)
    destination = Path(output_dir).resolve(strict=False)
    inputs = {stiffness_file, element_file, node_file}
    if destination in inputs:
        raise LiraFormatError("output directory must differ from every input file")
    if destination.exists():
        raise LiraFormatError(
            f"model bundle directory already exists; refusing to overwrite: {destination}"
        )

    stiffnesses = read_stiffness_table(
        stiffness_file, sheet_name=stiffness_sheet, header_row=stiffness_header_row
    )
    elements = read_element_table(
        element_file, sheet_name=element_sheet, header_row=element_header_row
    )
    nodes = read_node_table(
        node_file, sheet_name=node_sheet, header_row=node_header_row
    )
    assembled = assemble_lira_model(
        stiffnesses=stiffnesses, elements=elements, nodes=nodes
    )
    assembled, without_forces = attach_force_candidates(assembled, forces_path)

    sources = {
        "stiffness": {
            "path": str(stiffness_file),
            "sha256": _sha256_file(stiffness_file),
            "sheet": stiffness_sheet,
            "header_row": str(stiffness_header_row),
        },
        "elements": {
            "path": str(element_file),
            "sha256": _sha256_file(element_file),
            "sheet": element_sheet,
            "header_row": str(element_header_row),
        },
        "nodes": {
            "path": str(node_file),
            "sha256": _sha256_file(node_file),
            "sheet": node_sheet,
            "header_row": str(node_header_row),
        },
    }

    destination.mkdir(parents=True, exist_ok=False)
    paths = {name: destination / name for name in _MODEL_FILES}

    blocked = [item for item in assembled if not item.passed]
    force_total = sum(len(item.force_candidates) for item in assembled)
    _write_json(
        paths["manifest.json"],
        {
            "status": "MODEL_ASSEMBLED",
            "sources": sources,
            "elements": len(assembled),
            "elements_blocked": len(blocked),
            "stiffness_types": len(stiffnesses),
            "nodes": len(nodes),
            "force_candidates": force_total,
            "elements_without_forces": len(without_forces),
            "profile_resolution": None,
            "profile_resolution_validated": False,
            "ptm": None,
            "ptm_validated": False,
            "rx38_force_generation_allowed": False,
            "issue_readiness": "NOT_READY_FOR_ISSUE",
        },
    )
    _write_json(
        paths["stiffnesses.json"],
        {"stiffnesses": [item.as_dict() for item in stiffnesses]},
    )
    _write_json(paths["nodes.json"], {"nodes": [item.as_dict() for item in nodes]})
    _write_json(
        paths["elements.json"],
        {
            "elements": [item.as_dict() for item in assembled],
            "marks": sorted({item.mark for item in assembled if item.mark}, key=_mark_key),
        },
    )
    header, rows = _element_csv_rows(assembled)
    with paths["elements.csv"].open("x", encoding="utf-8", newline="") as stream:
        writer = csv.writer(stream, lineterminator="\n")
        writer.writerow(header)
        writer.writerows(rows)

    lengths = [item.length_m for item in assembled if item.length_m is not None]
    _write_new(
        paths["README_MODEL.md"],
        "# LIRA model assembly\n\n"
        "This bundle joins four exported LIRA tables into one element list. It\n"
        "created no `ProjectElement`, resolved no profile against the section\n"
        "database and computed no PTM.\n\n"
        f"- Elements: {len(assembled)} ({len(blocked)} blocked)\n"
        f"- Stiffness types: {len(stiffnesses)}\n"
        f"- Nodes: {len(nodes)}\n"
        f"- Elements with a computed length: {len(lengths)}\n"
        f"- Force candidates attached: {force_total}\n"
        f"- Elements without any force row: {len(without_forces)}\n"
        f"- Marks: {', '.join(sorted({item.mark for item in assembled if item.mark}, key=_mark_key)) or 'none'}\n"
        "- Profile resolution: **not performed** (needs the governing standard)\n"
        "- PTM: **not computed** (needs the heating scheme and its basis)\n"
        "- RX38 force generation: **BLOCKED**\n"
        "- Issue readiness: **NOT_READY_FOR_ISSUE**\n\n"
        "## Why the kind word is kept\n\n"
        "`Уголок параллельно полкам 80 x 6` and `Профиль \"Молодечно\" 120 x 6`\n"
        "reduce to the same bare numbers as a rectangular tube. Dropping the kind\n"
        "word would silently turn an angle into a tube, so `kind_word`,\n"
        "`designation` and `mark` are stored separately.\n\n"
        "## Blocked elements\n\n"
        "An element is blocked when its stiffness type, its mark, one of its nodes\n"
        "or one of its coordinates is missing, or when it is not a two-node bar.\n"
        "Nothing is substituted. Read `elements.csv` for the reason of each block.\n",
    )
    return LiraModelAssembly(
        elements=assembled,
        stiffnesses=stiffnesses,
        nodes=nodes,
        sources=sources,
        elements_without_forces=without_forces,
        written_files=tuple(paths[name] for name in _MODEL_FILES),
    )
