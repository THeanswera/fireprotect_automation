"""Link a read-only LIRA model package to re-verified RSU evidence rows.

The join is explicit and narrow:

* exactly one model package and one RSU evidence file are compared, and the
  pair, their file hashes and the join basis are written into the produced
  control set;
* the recorded source tables of both packages are re-read with the existing
  importers and the accepted JSON is proven against them — identifiers,
  vectors, units, coefficients, membership, provenance, geometry, section
  identity and both-end supports; a hash match of a changed JSON is not
  accepted as proof of correspondence to the XLS;
* rows are joined to elements by ``element_id`` **inside this pair only** — an
  equal element number in another LIRA project proves no shared model, and the
  produced bundle records ``historical_origin_claim`` accordingly;
* every row's ``section_station`` must lie inside the element's recorded
  ``section_count``; an unusable count fails the link, it is never guessed;
* several rows for one element and station stay separate candidates and are
  never merged just because they share geometry;
* no row is selected as the governing result and no RX38 generation is opened.

RSU rows remain RSU combination rows (``load_case_membership`` plus source
terms) and are never presented as ordinary single load cases: they are stored
under ``rsu_rows`` next to, not inside, the model's ``force_candidates``.
"""

from __future__ import annotations

import csv
import json
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Mapping, Sequence

from .assembly import (
    LiraAssembledElement,
    assemble_lira_model,
    read_element_table,
    read_node_table,
    read_stiffness_table,
)
from .errors import LiraFormatError, LiraMappingError
from .rsu_evidence import (
    RSU_COMPONENTS,
    RsuEvidenceBundle,
    RsuEvidenceRow,
    parse_finite_decimal,
    read_json_object,
    read_rsu_evidence,
    require_mapping,
    require_text,
    sha256_file,
    verify_recorded_sha256,
)

MODEL_PACKAGE_STATUS = "MODEL_ASSEMBLED"
LINK_STATUS = "MODEL_RSU_LINKED"
HISTORICAL_ORIGIN_CLAIM = "NOT_INDEPENDENTLY_PROVEN"
_MODEL_SOURCE_NAMES = ("stiffness", "elements", "nodes")
_LINK_FILES = (
    "manifest.json",
    "linked_elements.json",
    "rsu_candidates.csv",
    "README_LINKED.md",
)


def _write_new(path: Path, content: str) -> None:
    with path.open("x", encoding="utf-8", newline="\n") as stream:
        stream.write(content)


def _write_json(path: Path, payload: object) -> None:
    _write_new(path, json.dumps(payload, ensure_ascii=False, indent=2) + "\n")


def _sort_key(value: str) -> tuple[int, int | str]:
    text = value.strip()
    try:
        return (0, int(text))
    except ValueError:
        return (1, text)


@dataclass(frozen=True, slots=True)
class LinkedModelElement:
    """One model element (verbatim payload) with its linked RSU rows."""

    element_id: str
    model_payload: Mapping[str, object]
    rsu_rows: tuple[RsuEvidenceRow, ...]


@dataclass(frozen=True, slots=True)
class RsuModelLinkReport:
    """The outcome of linking: counts, control-set facts and written files."""

    model_dir: Path
    evidence_path: Path
    output_dir: Path
    elements: tuple[LinkedModelElement, ...]
    rows_total: int
    components_preserved: int
    source_files_reverified: Mapping[str, Mapping[str, Mapping[str, object]]]
    elements_without_rows: tuple[str, ...]
    written_files: tuple[Path, ...]

    def as_dict(self) -> dict[str, object]:
        return {
            "status": LINK_STATUS,
            "model_dir": str(self.model_dir),
            "evidence": str(self.evidence_path),
            "output_dir": str(self.output_dir),
            "elements": len(self.elements),
            "rsu_rows": self.rows_total,
            "components_preserved": self.components_preserved,
            "elements_without_rsu_rows": list(self.elements_without_rows),
            "source_files_reverified": {
                group: {name: dict(facts) for name, facts in entries.items()}
                for group, entries in self.source_files_reverified.items()
            },
            "governing_result_selection": None,
            "governing_result_selection_validated": False,
            "rx38_force_generation_allowed": False,
            "issue_readiness": "NOT_READY_FOR_ISSUE",
            "written_files": [str(path) for path in self.written_files],
        }


def _identity_text(identity: Mapping[str, Any], field: str, context: str) -> str | None:
    value = identity.get(field)
    if value is not None and not isinstance(value, str):
        raise LiraFormatError(f"{context}: identity.{field} must be a string or null")
    return value


def _verify_element_against_sources(
    payload: Mapping[str, object],
    assembled: LiraAssembledElement,
    *,
    context: str,
) -> None:
    """Prove one elements.json entry against the re-read source tables."""

    identity = require_mapping(payload.get("identity"), "identity", context)
    for field in (
        "element_type",
        "stiffness_type",
        "section_count",
        "kind_word",
        "designation",
        "mark",
    ):
        json_value = _identity_text(identity, field, context)
        source_value = getattr(assembled, field)
        if json_value != source_value:
            raise LiraMappingError(
                f"{context}: identity.{field} in elements.json is {json_value!r} "
                f"but the re-read source tables produce {source_value!r}"
            )
    geometry = require_mapping(payload.get("geometry"), "geometry", context)
    json_nodes = geometry.get("node_ids")
    if not isinstance(json_nodes, list):
        raise LiraFormatError(f"{context}: geometry.node_ids must be a list")
    if list(json_nodes) != list(assembled.node_ids):
        raise LiraMappingError(
            f"{context}: geometry.node_ids in elements.json is {json_nodes!r} "
            f"but the re-read source tables produce {list(assembled.node_ids)!r}"
        )
    json_length = geometry.get("length_m")
    json_length_decimal = (
        None
        if json_length is None
        else parse_finite_decimal(
            json_length, field="length_m", context=f"{context} geometry"
        )
    )
    if (json_length_decimal is None) != (assembled.length_m is None) or (
        json_length_decimal is not None
        and assembled.length_m is not None
        and json_length_decimal != assembled.length_m
    ):
        raise LiraMappingError(
            f"{context}: geometry.length_m in elements.json is "
            f"{json_length!r} but the re-read source tables produce "
            f"{assembled.length_m}"
        )
    json_rotation = geometry.get("rotation_angle_degrees")
    json_rotation_decimal = (
        None
        if json_rotation is None
        else parse_finite_decimal(
            json_rotation,
            field="rotation_angle_degrees",
            context=f"{context} geometry",
        )
    )
    if (json_rotation_decimal is None) != (
        assembled.rotation_angle_degrees is None
    ) or (
        json_rotation_decimal is not None
        and assembled.rotation_angle_degrees is not None
        and json_rotation_decimal != assembled.rotation_angle_degrees
    ):
        raise LiraMappingError(
            f"{context}: geometry.rotation_angle_degrees in elements.json is "
            f"{json_rotation!r} but the re-read source tables produce "
            f"{assembled.rotation_angle_degrees}"
        )
    supports = geometry.get("supports")
    if not isinstance(supports, Mapping):
        raise LiraFormatError(f"{context}: geometry.supports must be an object")
    for side, signs, index in (
        ("start", assembled.supports_start, 0),
        ("end", assembled.supports_end, 1),
    ):
        block = require_mapping(supports.get(side), f"supports.{side}", context)
        expected_node = assembled.node_ids[index] if len(assembled.node_ids) > index else None
        if block.get("node_id") != expected_node:
            raise LiraMappingError(
                f"{context}: geometry.supports.{side}.node_id in elements.json "
                f"is {block.get('node_id')!r} but the re-read source tables "
                f"produce {expected_node!r}"
            )
        signs_block = require_mapping(
            block.get("signs"), f"supports.{side}.signs", context
        )
        if any(
            not isinstance(key, str) or not isinstance(value, str)
            for key, value in signs_block.items()
        ):
            raise LiraFormatError(
                f"{context}: geometry.supports.{side}.signs must map axes to "
                "sign strings"
            )
        if dict(signs_block) != dict(signs):
            raise LiraMappingError(
                f"{context}: geometry.supports.{side}.signs in elements.json "
                f"is {dict(signs_block)!r} but the re-read source tables "
                f"produce {dict(signs)!r}"
            )
    status = payload.get("status")
    expected_status = "ASSEMBLED" if assembled.passed else "BLOCKED"
    if status != expected_status:
        raise LiraMappingError(
            f"{context}: status in elements.json is {status!r} but the re-read "
            f"source tables produce {expected_status!r}"
        )
    blockers = payload.get("blockers")
    if not isinstance(blockers, list) or any(
        not isinstance(item, str) for item in blockers
    ):
        raise LiraFormatError(f"{context}: blockers must be a list of strings")
    if list(blockers) != list(assembled.blockers):
        raise LiraMappingError(
            f"{context}: blockers in elements.json are {list(blockers)!r} but "
            f"the re-read source tables produce {list(assembled.blockers)!r}"
        )
    source_rows = payload.get("source_rows")
    if not isinstance(source_rows, Mapping):
        raise LiraFormatError(f"{context}: source_rows must be an object")
    if dict(source_rows) != dict(assembled.source_rows):
        raise LiraMappingError(
            f"{context}: source_rows in elements.json are {dict(source_rows)!r} "
            f"but the re-read source tables produce "
            f"{dict(assembled.source_rows)!r}"
        )


def _reread_model_sources(
    manifest_path: Path,
    sources_block: Mapping[str, Any],
    json_elements: list[Mapping[str, object]],
) -> dict[str, object]:
    """Re-read the three model tables and verify the JSON elements against them.

    The recorded sheet and header row of each source are used verbatim; a
    missing or unusable setting is refused instead of guessed.
    """

    settings: dict[str, tuple[Path, str, int]] = {}
    for name in _MODEL_SOURCE_NAMES:
        entry = require_mapping(sources_block.get(name), name, str(manifest_path))
        path_token = entry.get("path")
        sheet_token = entry.get("sheet")
        header_token = entry.get("header_row")
        if not isinstance(path_token, str) or not path_token.strip():
            raise LiraFormatError(
                f"{manifest_path}: source {name!r} has no usable path"
            )
        if not isinstance(sheet_token, str) or not sheet_token:
            raise LiraFormatError(
                f"{manifest_path}: source {name!r} has no usable sheet setting; "
                "refusing to guess table settings"
            )
        if isinstance(header_token, int) and not isinstance(header_token, bool):
            header = header_token
        elif isinstance(header_token, str) and header_token.strip().isdigit():
            header = int(header_token.strip())
        else:
            header = 0
        if header < 1:
            raise LiraFormatError(
                f"{manifest_path}: source {name!r} has no usable header_row; "
                "refusing to guess table settings"
            )
        settings[name] = (Path(path_token), sheet_token, header)

    stiffnesses = read_stiffness_table(
        settings["stiffness"][0],
        sheet_name=settings["stiffness"][1],
        header_row=settings["stiffness"][2],
    )
    elements = read_element_table(
        settings["elements"][0],
        sheet_name=settings["elements"][1],
        header_row=settings["elements"][2],
    )
    nodes = read_node_table(
        settings["nodes"][0],
        sheet_name=settings["nodes"][1],
        header_row=settings["nodes"][2],
    )
    assembled = assemble_lira_model(
        stiffnesses=stiffnesses, elements=elements, nodes=nodes
    )
    if len(json_elements) != len(assembled):
        raise LiraMappingError(
            f"{manifest_path}: elements.json lists {len(json_elements)} elements "
            f"but the re-read source tables produce {len(assembled)}"
        )
    by_id = {item.element_id: item for item in assembled}
    for payload in json_elements:
        element_id = require_text(payload, "element_id", str(manifest_path))
        item = by_id.get(element_id)
        if item is None:
            raise LiraMappingError(
                f"{manifest_path}: element {element_id!r} in elements.json is "
                "not produced by the recorded source tables"
            )
        _verify_element_against_sources(
            payload, item, context=f"{manifest_path} element {element_id}"
        )
    return {
        "elements": len(assembled),
        "nodes": len(nodes),
        "stiffness_types": len(stiffnesses),
        "geometry_verified": True,
        "supports_verified": True,
        "identity_verified": True,
    }


def _read_model_package(
    model_dir: Path,
) -> tuple[
    list[Mapping[str, object]],
    Mapping[str, Mapping[str, object]],
    Mapping[str, Any],
    Mapping[str, object],
]:
    """Read one model package and re-verify its elements against the XLS."""

    manifest_path = model_dir / "manifest.json"
    elements_path = model_dir / "elements.json"
    manifest = read_json_object(manifest_path, "model manifest")
    if manifest.get("status") != MODEL_PACKAGE_STATUS:
        raise LiraMappingError(
            f"{manifest_path}: status must be {MODEL_PACKAGE_STATUS!r}, "
            f"got {manifest.get('status')!r}"
        )
    sources_block = require_mapping(
        manifest.get("sources"), "sources", str(manifest_path)
    )
    if set(sources_block) != set(_MODEL_SOURCE_NAMES):
        raise LiraFormatError(
            f"{manifest_path}: sources must identify exactly "
            f"{', '.join(_MODEL_SOURCE_NAMES)}"
        )
    rechecked: dict[str, Mapping[str, object]] = {}
    for name in _MODEL_SOURCE_NAMES:
        entry = require_mapping(sources_block.get(name), name, str(manifest_path))
        rechecked[name] = verify_recorded_sha256(
            entry, name=name, context=str(manifest_path)
        )
    elements_payload = read_json_object(elements_path, "model elements")
    raw_elements = elements_payload.get("elements")
    if not isinstance(raw_elements, list) or not raw_elements:
        raise LiraFormatError(f"{elements_path}: elements must be a non-empty list")
    result: list[Mapping[str, object]] = []
    seen: set[str] = set()
    for index, raw in enumerate(raw_elements):
        entry = require_mapping(raw, f"elements[{index}]", str(elements_path))
        element_id = require_text(
            entry, "element_id", f"{elements_path} elements[{index}]"
        )
        if element_id in seen:
            raise LiraFormatError(
                f"{elements_path}: element {element_id!r} appears twice"
            )
        seen.add(element_id)
        identity = require_mapping(
            entry.get("identity"), "identity", f"{elements_path} element {element_id}"
        )
        section_count = identity.get("section_count")
        if section_count is not None and not isinstance(section_count, str):
            raise LiraFormatError(
                f"{elements_path}: element {element_id} section_count must be "
                "a string or null"
            )
        geometry = require_mapping(
            entry.get("geometry"), "geometry", f"{elements_path} element {element_id}"
        )
        node_ids = geometry.get("node_ids")
        if not isinstance(node_ids, list) or any(
            not isinstance(item, str) for item in node_ids
        ):
            raise LiraFormatError(
                f"{elements_path}: element {element_id} geometry.node_ids must "
                "be a list of strings"
            )
        result.append(dict(entry))
    model_facts = _reread_model_sources(manifest_path, sources_block, result)
    return result, rechecked, manifest, model_facts


def _section_count(element_id: str, token: str | None) -> int:
    if token is None:
        raise LiraMappingError(
            f"model element {element_id} has no section_count; the RSU "
            "section_station cannot be checked against it"
        )
    try:
        value = Decimal(token)
    except InvalidOperation as exc:
        raise LiraMappingError(
            f"model element {element_id} section_count {token!r} is not a number"
        ) from exc
    if not value.is_finite() or value != value.to_integral_value() or value < 1:
        raise LiraMappingError(
            f"model element {element_id} section_count {token!r} must be a "
            "positive integer"
        )
    return int(value)


def _check_station(row: RsuEvidenceRow, count: int) -> None:
    try:
        station = Decimal(row.section_station)
    except InvalidOperation as exc:
        raise LiraMappingError(
            f"RSU row {row.row_id} section_station {row.section_station!r} "
            "is not a number"
        ) from exc
    if (
        not station.is_finite()
        or station != station.to_integral_value()
        or not 1 <= station <= count
    ):
        raise LiraMappingError(
            f"RSU row {row.row_id} section_station {row.section_station!r} lies "
            f"outside element {row.element_id}'s {count} sections"
        )


def link_rsu_rows_to_elements(
    elements: Sequence[Mapping[str, object]], bundle: RsuEvidenceBundle
) -> tuple[tuple[LinkedModelElement, ...], tuple[str, ...]]:
    """Join evidence rows to model elements by element_id inside this pair.

    Rows referencing an element absent from the model, or a section outside the
    element's recorded section count, raise. Rows are never deduplicated: two
    candidates of one element and station stay two candidates.
    """

    by_id: dict[str, Mapping[str, object]] = {}
    counts: dict[str, int] = {}
    for index, entry in enumerate(elements):
        element_id = require_text(entry, "element_id", f"model element {index}")
        if element_id in by_id:
            raise LiraFormatError(f"model element {element_id!r} appears twice")
        identity = require_mapping(
            entry.get("identity"), "identity", f"model element {element_id}"
        )
        section_count = identity.get("section_count")
        if section_count is not None and not isinstance(section_count, str):
            raise LiraFormatError(
                f"model element {element_id} section_count must be a string or null"
            )
        by_id[element_id] = entry
        counts[element_id] = _section_count(element_id, section_count)
    for row in bundle.rows:
        if row.element_id not in by_id:
            raise LiraMappingError(
                f"RSU row {row.row_id} references element {row.element_id!r}, "
                f"which is absent from the model package {bundle.path}; an equal "
                "element number in another project is not a shared-model proof"
            )
        _check_station(row, counts[row.element_id])
    grouped: dict[str, list[RsuEvidenceRow]] = {}
    for row in bundle.rows:
        grouped.setdefault(row.element_id, []).append(row)
    linked: list[LinkedModelElement] = []
    without: list[str] = []
    for entry in elements:
        element_id = str(entry["element_id"])
        rows = tuple(grouped.get(element_id, ()))
        if not rows:
            without.append(element_id)
        linked.append(
            LinkedModelElement(
                element_id=element_id,
                model_payload=dict(entry),
                rsu_rows=rows,
            )
        )
    return tuple(linked), tuple(without)


def _candidate_csv_rows(
    elements: Sequence[LinkedModelElement],
) -> tuple[list[str], list[list[str]]]:
    rows = sorted(
        (row for element in elements for row in element.rsu_rows),
        key=lambda row: (_sort_key(row.element_id), row.row_id),
    )
    units: dict[str, str] = {}
    for row in rows:
        for component in RSU_COMPONENTS:
            unit = row.published_vector[component].unit
            if component in units and units[component] != unit:
                raise LiraMappingError(
                    f"RSU rows use mixed units for {component} "
                    f"({units[component]!r} and {unit!r}); one CSV column "
                    "cannot carry two units"
                )
            units[component] = unit
    header = [
        "row_id",
        "element_id",
        "section_station",
        "rsu_group",
        "rsu_criterion",
        "rsu_column_number",
        "load_case_membership",
        "status",
        "source_sheet",
        "source_row",
    ] + [f"{component} [{units[component]}]" for component in RSU_COMPONENTS]
    csv_rows: list[list[str]] = []
    for row in rows:
        csv_rows.append(
            [
                row.row_id,
                row.element_id,
                row.section_station,
                row.rsu_group,
                row.rsu_criterion,
                str(row.rsu_column_number),
                " ".join(row.load_case_membership),
                row.status,
                row.source_sheet or "",
                str(row.source_row),
            ]
            + [
                str(row.published_vector[component].value)
                for component in RSU_COMPONENTS
            ]
        )
    return header, csv_rows


def _readme_text(
    *,
    model_dir: Path,
    evidence_path: Path,
    elements: int,
    rows: int,
    components: int,
    without: Sequence[str],
) -> str:
    return (
        "# Связка модели ЛИРА и проверенных строк РСУ\n\n"
        "Этот пакет соединяет read-only сборку контрольной модели "
        f"(`{model_dir}`) с полными строками проверенных РСУ "
        f"(`{evidence_path}`) в единый read-only пакет.\n\n"
        "## Что внутри\n\n"
        "- `manifest.json` — контрольный набор: пара пакетов, их хеши, "
        "основание сопоставления (только `element_id` внутри этой пары), "
        "факты повторной проверки SHA-256 всех исходных XLS и счётчики;\n"
        "- `linked_elements.json` — каждый элемент модели целиком (identity, "
        "geometry, закрепления обоих узлов, source_rows, blockers) плюс "
        "список `rsu_rows` — все строки РСУ этого элемента;\n"
        "- `rsu_candidates.csv` — компактный список кандидатов;\n"
        "- `README_LINKED.md` — этот файл.\n\n"
        f"- Элементов: {elements}\n"
        f"- Строк РСУ: {rows}\n"
        f"- Исходных компонентов сохранено без изменения: {components}\n"
        f"- Элементов без строк РСУ: {len(without)}"
        f"{' (' + ', '.join(without) + ')' if without else ''}\n\n"
        "## Что проверено при связывании\n\n"
        "- SHA-256 семи исходных XLS пересчитаны с диска: три файла модели "
        "(жёсткости/элементы/узлы) и четыре файла РСУ (усилия/опубликованные "
        "РСУ/коэффициенты/параметры). Несовпадение — ошибка, пакет не "
        "создаётся.\n"
        "- Хеши — не доказательство содержания: все семь исходных таблиц "
        "**повторно прочитаны** существующими импортерами с явными "
        "настройками из манифестов, и каждое значение, принятое из JSON, "
        "сверено с результатом чтения исходников — идентификаторы, полные "
        "векторы, единицы, коэффициенты, составы сочетаний, ячейки, геометрия, "
        "идентичность сечения и закрепления обоих узлов.\n"
        "- Записанный статус VERIFIED не принимается на веру: каждый из шести "
        "компонентов каждой строки пересчитан по слагаемым "
        "(усилие × коэффициент) точной десятичной арифметикой и должен "
        "совпасть с опубликованным значением, включая знак; общий статус "
        "BLOCKED, записанные блокировки и противоречивые статусы строк "
        "отклоняются.\n"
        "- `section_station` каждой строки проверен против `section_count` "
        "элемента.\n"
        "- Неизвестный элемент, повторяющийся `row_id`, неполный вектор, "
        "недопустимое сечение, изменённый источник или JSON, не "
        "соответствующий исходным таблицам, дают явную ошибку.\n\n"
        "## Что НЕ сделано\n\n"
        "- Строки РСУ — это строки сочетаний с составом "
        "`load_case_membership`, а не одиночные загружения; они лежат в "
        "`rsu_rows` и не смешиваются с `force_candidates` модели.\n"
        "- Определяющая строка не выбрана: `governing_result_selection = null`.\n"
        "- Историческое происхождение экспортов не объявлено доказанным "
        f"(`historical_origin_claim = {HISTORICAL_ORIGIN_CLAIM}`); совпадение "
        "номера элемента в другом проекте не считается доказательством "
        "общей модели.\n"
        "- Пакет относится только к контрольной модели и не должен "
        "смешиваться с проектом «Мед центр Васька 2».\n"
        "- Отсутствие марки, неподтверждённый профиль и ограничения выпуска "
        "сохраняются: успешное связывание данных не означает готовность к RX3.\n"
    )


def prepare_linked_rsu_bundle(
    *,
    model_dir: str | Path,
    evidence_path: str | Path,
    output_dir: str | Path,
) -> RsuModelLinkReport:
    """Write a new unified package; never overwrite an existing directory."""

    model = Path(model_dir).resolve(strict=True)
    evidence = Path(evidence_path).resolve(strict=True)
    destination = Path(output_dir).resolve(strict=False)
    if destination in (model, evidence):
        raise LiraFormatError("output directory must differ from both inputs")
    if destination.exists():
        raise LiraFormatError(
            f"linked bundle directory already exists; refusing to overwrite: "
            f"{destination}"
        )

    elements, model_sources, model_manifest, model_facts = _read_model_package(
        model
    )
    bundle = read_rsu_evidence(evidence)
    linked, without = link_rsu_rows_to_elements(elements, bundle)

    model_files = {
        "manifest.json": sha256_file(model / "manifest.json"),
        "elements.json": sha256_file(model / "elements.json"),
    }
    destination.mkdir(parents=True, exist_ok=False)
    paths = {name: destination / name for name in _LINK_FILES}

    link_basis: dict[str, object] = {
        "comparison_key": "element_id",
        "scope": (
            "Rows are joined to elements only inside this explicitly named pair "
            "of packages. An equal element id in another LIRA project proves no "
            "shared model and is never used as a join."
        ),
        "historical_origin_claim": HISTORICAL_ORIGIN_CLAIM,
        "model_package": {"path": str(model), "files": model_files},
        "rsu_evidence": {"path": str(evidence), "sha256": bundle.sha256},
        "source_files_reverified": {
            "model": {name: dict(value) for name, value in model_sources.items()},
            "rsu": {
                name: dict(value)
                for name, value in bundle.source_files_rechecked.items()
            },
        },
        "source_reread": {
            "model": dict(model_facts),
            "rsu": dict(bundle.source_recheck),
        },
    }
    blocked_model_elements = [
        element
        for element in linked
        if element.model_payload.get("status") != "ASSEMBLED"
    ]
    manifest: dict[str, object] = {
        "status": LINK_STATUS,
        "link_basis": link_basis,
        "model": {
            "elements": len(linked),
            "elements_blocked": len(blocked_model_elements),
            "stiffness_types": model_manifest.get("stiffness_types"),
            "nodes": model_manifest.get("nodes"),
            "profile_resolution": None,
            "profile_resolution_validated": False,
            "ptm": None,
            "ptm_validated": False,
        },
        "rsu": {
            "rows": len(bundle.rows),
            "rows_verified": bundle.rows_verified,
            "component_comparisons": len(RSU_COMPONENTS) * len(bundle.rows),
            "components_preserved": bundle.components_preserved,
            "reconstruction_rechecked": True,
            "mapping_fingerprint": bundle.mapping_fingerprint,
        },
        "links": {
            "elements_with_rows": len(linked) - len(without),
            "rows_linked": len(bundle.rows),
            "rows_per_element": {
                element.element_id: len(element.rsu_rows) for element in linked
            },
            "elements_without_rsu_rows": list(without),
            "governing_result_selection": None,
        },
        "governing_result_selection_validated": False,
        "rx38_force_generation_allowed": False,
        "issue_readiness": "NOT_READY_FOR_ISSUE",
        "written_files": [str(paths[name]) for name in _LINK_FILES],
    }
    _write_json(paths["manifest.json"], manifest)
    _write_json(
        paths["linked_elements.json"],
        {
            "status": LINK_STATUS,
            "link_basis": link_basis,
            "elements": [
                {
                    **element.model_payload,
                    "rsu_rows": [row.as_dict() for row in element.rsu_rows],
                }
                for element in linked
            ],
        },
    )
    header, csv_rows = _candidate_csv_rows(linked)
    with paths["rsu_candidates.csv"].open("x", encoding="utf-8", newline="") as stream:
        writer = csv.writer(stream, lineterminator="\n")
        writer.writerow(header)
        writer.writerows(csv_rows)
    _write_new(
        paths["README_LINKED.md"],
        _readme_text(
            model_dir=model,
            evidence_path=evidence,
            elements=len(linked),
            rows=len(bundle.rows),
            components=bundle.components_preserved,
            without=without,
        ),
    )
    return RsuModelLinkReport(
        model_dir=model,
        evidence_path=evidence,
        output_dir=destination,
        elements=linked,
        rows_total=len(bundle.rows),
        components_preserved=bundle.components_preserved,
        source_files_reverified={
            "model": model_sources,
            "rsu": bundle.source_files_rechecked,
        },
        elements_without_rows=without,
        written_files=tuple(paths[name] for name in _LINK_FILES),
    )
