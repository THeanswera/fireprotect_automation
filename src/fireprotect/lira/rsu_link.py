"""Link a read-only LIRA model package to re-verified RSU evidence rows.

The join is explicit and narrow:

* exactly one model package and one RSU evidence file are compared, and the
  pair, their file hashes and the join basis are written into the produced
  control set;
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

from .errors import LiraFormatError, LiraMappingError
from .rsu_evidence import (
    RSU_COMPONENTS,
    RsuEvidenceBundle,
    RsuEvidenceRow,
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


def _read_model_package(
    model_dir: Path,
) -> tuple[
    list[Mapping[str, object]],
    Mapping[str, Mapping[str, object]],
    Mapping[str, Any],
]:
    """Read one model package and re-verify its recorded source XLS hashes."""

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
    return result, rechecked, manifest


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
        "- Записанный статус VERIFIED не принимается на веру: каждый из шести "
        "компонентов каждой строки пересчитан по слагаемым "
        "(усилие × коэффициент) точной десятичной арифметикой и должен "
        "совпасть с опубликованным значением, включая знак.\n"
        "- `section_station` каждой строки проверен против `section_count` "
        "элемента.\n"
        "- Неизвестный элемент, повторяющийся `row_id`, неполный вектор, "
        "недопустимое сечение или изменённый источник дают явную ошибку.\n\n"
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

    elements, model_sources, model_manifest = _read_model_package(model)
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
