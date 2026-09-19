"""Candidate enumeration and engineer-declared governing-result selection.

This module never selects a governing result. It enumerates every accepted
native LIRA record from an existing review bundle as a ``CANDIDATE_ONLY``
entry, emits a declaration template for an explicit engineering decision, and
validates that a filled declaration resolves to exactly one existing candidate.

No envelope rule such as ``max(abs(all_values))`` is implemented here. A
maximum over unrelated stations, load cases and combinations is not a validated
governing-result semantics. A declaration records an engineering decision, not
a mathematical proof, so this module never reports
``governing_result_selection_validated = true`` and never allows RX38 force
generation.

Every source value is preserved with its raw token, exact ``Decimal`` parse,
source and normalized units, source cell and row. Nothing is renamed to an RX3
component: the native-to-RX3 convention remains a separate, evidence-bound
concern.
"""

from __future__ import annotations

import csv
import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Sequence

from .errors import LiraFormatError, LiraMappingError

CANDIDATE_STATUS = "CANDIDATE_ONLY"
DECLARATION_KIND = "ENGINEER_GOVERNING_RESULT_DECLARATION"
DECLARATION_STATUS = "ENGINEER_DECLARED_UNVALIDATED"

BLOCKER_GOVERNING_SELECTION = "LIRA_GOVERNING_RESULT_SELECTION_UNRESOLVED"
BLOCKER_DECLARATION_NOT_EVIDENCE = (
    "GOVERNING_SELECTION_DECLARATION_IS_NOT_INDEPENDENT_EVIDENCE"
)
BLOCKER_DECLARED_CANDIDATE_UNKNOWN = "GOVERNING_SELECTION_CANDIDATE_UNKNOWN"
BLOCKER_DECLARED_ELEMENT_UNKNOWN = "GOVERNING_SELECTION_ELEMENT_UNKNOWN"
BLOCKER_DECLARED_ELEMENT_MISMATCH = "GOVERNING_SELECTION_ELEMENT_MISMATCH"
BLOCKER_DECLARED_ELEMENT_DUPLICATE = "GOVERNING_SELECTION_ELEMENT_DECLARED_TWICE"
BLOCKER_INVALID_DECLARATION = "GOVERNING_SELECTION_DECLARATION_INVALID"

WARNING_SINGLE_CANDIDATE = "SINGLE_CANDIDATE_PER_ELEMENT_PROVES_NO_SELECTION_RULE"
WARNING_COMBINATION_IDENTITY_MISSING = "SOURCE_HAS_NO_LOAD_COMBINATION_IDENTITY"

NATIVE_FORCE_COMPONENTS = ("N", "Mk", "My", "Mz", "Qy", "Qz")
NATIVE_RESULT_COMPONENTS = ("Ry", "Rz")

_CANDIDATE_FILES = (
    "manifest.json",
    "candidates.json",
    "candidates.csv",
    "selection_template.json",
    "README_SELECTION.md",
)


def _write_new(path: Path, content: str) -> None:
    with path.open("x", encoding="utf-8", newline="\n") as stream:
        stream.write(content)


def _write_json(path: Path, payload: object) -> None:
    _write_new(path, json.dumps(payload, ensure_ascii=False, indent=2) + "\n")


def _read_json(path: Path, description: str) -> Any:
    # `utf-8-sig` accepts both a BOM and plain UTF-8. The declaration file is
    # edited by hand, and Windows editors and shells commonly write a BOM; a
    # byte-order mark is not a data defect and must not block a review.
    try:
        return json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError) as exc:
        raise LiraFormatError(f"cannot read {description} {path}: {exc}") from exc


def _sort_key(value: str) -> tuple[int, int | str]:
    text = value.strip()
    try:
        return (0, int(text))
    except ValueError:
        return (1, text)


def _optional_text(value: object, *, field: str) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise LiraFormatError(f"{field} must be a string or null in forces.json")
    text = value.strip()
    return text or None


@dataclass(frozen=True, slots=True)
class LiraCandidateValue:
    """One native component of one candidate, with its full source provenance."""

    component: str
    source_cell: str | None
    raw_token: str | None
    parsed_decimal: str | None
    source_unit: str | None
    normalized_value: str | None
    normalized_unit: str | None

    def as_dict(self) -> dict[str, object]:
        return {
            "component": self.component,
            "source_cell": self.source_cell,
            "raw_token": self.raw_token,
            "parsed_decimal": self.parsed_decimal,
            "source_unit": self.source_unit,
            "normalized_value": self.normalized_value,
            "normalized_unit": self.normalized_unit,
        }


@dataclass(frozen=True, slots=True)
class LiraGoverningCandidate:
    """One accepted native record offered as a governing-result candidate.

    A candidate is evidence of what the source file contains. It proves no
    axis, sign, combination semantics or governing-result rule.
    """

    candidate_id: str
    element_id: str
    section_station: str | None
    load_case: str | None
    composition: str | None
    combination: str | None
    profile: str | None
    mark: str | None
    element_type: str | None
    source_sheet_or_table: str | None
    source_row: int
    values: Mapping[str, LiraCandidateValue]

    @property
    def has_combination_identity(self) -> bool:
        return self.combination is not None

    def value(self, component: str) -> LiraCandidateValue | None:
        return self.values.get(component)

    def as_dict(self) -> dict[str, object]:
        return {
            "candidate_id": self.candidate_id,
            "status": CANDIDATE_STATUS,
            "identity": {
                "element_id": self.element_id,
                "section_station": self.section_station,
                "load_case": self.load_case,
                "composition": self.composition,
                "combination": self.combination,
                "profile": self.profile,
                "mark": self.mark,
                "element_type": self.element_type,
            },
            "source": {
                "sheet_or_table": self.source_sheet_or_table,
                "row": self.source_row,
            },
            "native_values": {
                component: value.as_dict()
                for component, value in sorted(self.values.items())
            },
        }


@dataclass(frozen=True, slots=True)
class LiraSelectionBundle:
    """A new, review-only candidate bundle. Nothing is selected by creating it."""

    destination: Path
    source_forces: Path
    source_forces_sha256: str
    candidates: tuple[LiraGoverningCandidate, ...]
    written_files: tuple[Path, ...]

    @property
    def unique_elements(self) -> tuple[str, ...]:
        return tuple(sorted({item.element_id for item in self.candidates}, key=_sort_key))

    def as_dict(self) -> dict[str, object]:
        return {
            "destination": str(self.destination),
            "source_forces": str(self.source_forces),
            "source_forces_sha256": self.source_forces_sha256,
            "status": CANDIDATE_STATUS,
            "candidates": len(self.candidates),
            "unique_elements": len(self.unique_elements),
            "governing_result_selection": None,
            "governing_result_selection_validated": False,
            "rx38_force_generation_allowed": False,
            "issue_readiness": "NOT_READY_FOR_ISSUE",
            "written_files": [str(path) for path in self.written_files],
        }


@dataclass(frozen=True, slots=True)
class LiraSelectionReport:
    """The outcome of checking a filled declaration against the candidates."""

    status: str
    elements_total: int
    elements_declared: int
    candidates_total: int
    resolved: tuple[Mapping[str, object], ...]
    blockers: tuple[Mapping[str, object], ...]
    warnings: tuple[Mapping[str, object], ...]

    def as_dict(self) -> dict[str, object]:
        return {
            "status": self.status,
            "declaration_status": DECLARATION_STATUS,
            "elements_total": self.elements_total,
            "elements_declared": self.elements_declared,
            "candidates_total": self.candidates_total,
            "resolved": [dict(item) for item in self.resolved],
            "blockers": [dict(item) for item in self.blockers],
            "warnings": [dict(item) for item in self.warnings],
            "governing_result_selection_validated": False,
            "rx38_force_generation_allowed": False,
            "issue_readiness": "NOT_READY_FOR_ISSUE",
        }


def _component_value(component: str, payload: object) -> LiraCandidateValue:
    if not isinstance(payload, Mapping):
        raise LiraFormatError(f"native component {component} is not an object")
    return LiraCandidateValue(
        component=component,
        source_cell=_optional_text(payload.get("source_cell"), field="source_cell"),
        raw_token=_optional_text(payload.get("raw_token"), field="raw_token"),
        parsed_decimal=_optional_text(
            payload.get("parsed_decimal"), field="parsed_decimal"
        ),
        source_unit=_optional_text(payload.get("source_unit"), field="source_unit"),
        normalized_value=_optional_text(
            payload.get("normalized_value"), field="normalized_value"
        ),
        normalized_unit=_optional_text(
            payload.get("normalized_unit"), field="normalized_unit"
        ),
    )


def read_candidates(forces_path: str | Path) -> tuple[
    tuple[LiraGoverningCandidate, ...], str
]:
    """Read an existing review bundle's ``forces.json`` into candidates.

    The bundle is reused rather than re-parsed so the Decimal-safe accepted
    records, their provenance and the mapping fingerprint stay the single
    authority for what the source contained.
    """

    source = Path(forces_path).resolve(strict=True)
    payload = _read_json(source, "review bundle forces file")
    if not isinstance(payload, Mapping):
        raise LiraFormatError("forces.json must contain an object")
    records = payload.get("accepted_native_records")
    if not isinstance(records, list):
        raise LiraFormatError("forces.json has no accepted_native_records list")

    candidates: list[LiraGoverningCandidate] = []
    for index, raw in enumerate(records, start=1):
        if not isinstance(raw, Mapping):
            raise LiraFormatError("accepted_native_records entries must be objects")
        metadata = raw.get("metadata")
        source_block = raw.get("source")
        if not isinstance(metadata, Mapping) or not isinstance(source_block, Mapping):
            raise LiraFormatError(
                "each accepted record requires metadata and source objects"
            )
        element_id = _optional_text(metadata.get("element_id"), field="element_id")
        if element_id is None:
            raise LiraFormatError("an accepted record has no element_id")
        row = source_block.get("row")
        if not isinstance(row, int) or isinstance(row, bool):
            raise LiraFormatError("an accepted record has no integer source row")

        raw_forces = raw.get("native_forces")
        raw_results = raw.get("native_results")
        values: dict[str, LiraCandidateValue] = {}
        for component in NATIVE_FORCE_COMPONENTS:
            if isinstance(raw_forces, Mapping) and component in raw_forces:
                values[component] = _component_value(component, raw_forces[component])
        for component in NATIVE_RESULT_COMPONENTS:
            if isinstance(raw_results, Mapping) and component in raw_results:
                values[component] = _component_value(component, raw_results[component])

        candidates.append(
            LiraGoverningCandidate(
                candidate_id=f"C{index:04d}",
                element_id=element_id,
                section_station=_optional_text(
                    metadata.get("section_station"), field="section_station"
                ),
                load_case=_optional_text(metadata.get("load_case"), field="load_case"),
                composition=_optional_text(
                    metadata.get("composition"), field="composition"
                ),
                combination=_optional_text(
                    metadata.get("combination"), field="combination"
                ),
                profile=_optional_text(metadata.get("profile"), field="profile"),
                mark=_optional_text(metadata.get("mark"), field="mark"),
                element_type=_optional_text(
                    metadata.get("element_type"), field="element_type"
                ),
                source_sheet_or_table=_optional_text(
                    source_block.get("sheet_or_table"), field="sheet_or_table"
                ),
                source_row=row,
                values=values,
            )
        )
    return tuple(candidates), _sha256_file(source)


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(65536), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _candidate_csv_rows(
    candidates: Sequence[LiraGoverningCandidate],
) -> tuple[list[str], list[list[str]]]:
    header = [
        "candidate_id",
        "element_id",
        "section_station",
        "load_case",
        "composition",
        "combination",
        "profile",
        "mark",
        "element_type",
        "source_sheet_or_table",
        "source_row",
    ]
    for component in (*NATIVE_FORCE_COMPONENTS, *NATIVE_RESULT_COMPONENTS):
        header.append(f"{component}_raw_token")
        header.append(f"{component}_source_unit")
        header.append(f"{component}_normalized_value")
        header.append(f"{component}_normalized_unit")

    rows: list[list[str]] = []
    for candidate in candidates:
        row = [
            candidate.candidate_id,
            candidate.element_id,
            candidate.section_station or "",
            candidate.load_case or "",
            candidate.composition or "",
            candidate.combination or "",
            candidate.profile or "",
            candidate.mark or "",
            candidate.element_type or "",
            candidate.source_sheet_or_table or "",
            str(candidate.source_row),
        ]
        for component in (*NATIVE_FORCE_COMPONENTS, *NATIVE_RESULT_COMPONENTS):
            value = candidate.value(component)
            row.append((value.raw_token if value and value.raw_token else ""))
            row.append((value.source_unit if value and value.source_unit else ""))
            row.append(
                (value.normalized_value if value and value.normalized_value else "")
            )
            row.append(
                (value.normalized_unit if value and value.normalized_unit else "")
            )
        rows.append(row)
    return header, rows


def _selection_template(
    candidates: Sequence[LiraGoverningCandidate],
) -> dict[str, object]:
    elements = sorted({item.element_id for item in candidates}, key=_sort_key)
    return {
        "declaration_kind": DECLARATION_KIND,
        "declared_by": None,
        "basis": None,
        "instructions": (
            "Fill declared_by and basis, then set candidate_id for every element. "
            "The candidate_id must be copied from candidates.csv or candidates.json. "
            "Leave candidate_id null when no governing candidate is declared. "
            "This declaration records an engineering decision; it does not prove a "
            "mathematical maximum and it does not open RX38 generation."
        ),
        "elements": [
            {
                "element_id": element_id,
                "candidate_id": None,
                "gui_observed_governing": None,
                "note": None,
            }
            for element_id in elements
        ],
    }


def prepare_lira_selection_bundle(
    forces_path: str | Path,
    output_dir: str | Path,
) -> LiraSelectionBundle:
    """Create a new candidate bundle from an existing review bundle."""

    source = Path(forces_path).resolve(strict=True)
    destination = Path(output_dir).resolve(strict=False)
    if destination == source:
        raise LiraFormatError("output directory must differ from the forces file")
    if destination.exists():
        raise LiraFormatError(
            f"selection bundle directory already exists; refusing to overwrite: {destination}"
        )
    candidates, source_sha = read_candidates(source)
    if not candidates:
        raise LiraFormatError(
            "the review bundle contains no accepted native records to enumerate"
        )

    duplicate_rows = len({item.source_row for item in candidates}) != len(candidates)
    destination.mkdir(parents=True, exist_ok=False)
    paths = {name: destination / name for name in _CANDIDATE_FILES}

    elements = sorted({item.element_id for item in candidates}, key=_sort_key)
    manifest: dict[str, object] = {
        "source_forces": str(source),
        "source_forces_sha256": source_sha,
        "status": CANDIDATE_STATUS,
        "candidates": len(candidates),
        "unique_elements": len(elements),
        "source_row_identity_unique": not duplicate_rows,
        "components": {
            "forces": list(NATIVE_FORCE_COMPONENTS),
            "results": list(NATIVE_RESULT_COMPONENTS),
        },
        "governing_result_selection": None,
        "governing_result_selection_validated": False,
        "rx38_force_generation_allowed": False,
        "issue_readiness": "NOT_READY_FOR_ISSUE",
    }
    _write_json(paths["manifest.json"], manifest)
    _write_json(
        paths["candidates.json"],
        {
            "status": CANDIDATE_STATUS,
            "source_forces_sha256": source_sha,
            "candidates": [item.as_dict() for item in candidates],
        },
    )
    _write_json(paths["selection_template.json"], _selection_template(candidates))

    header, rows = _candidate_csv_rows(candidates)
    with paths["candidates.csv"].open("x", encoding="utf-8", newline="") as stream:
        writer = csv.writer(stream, lineterminator="\n")
        writer.writerow(header)
        writer.writerows(rows)

    components_seen = sorted({name for item in candidates for name in item.values})
    _write_new(
        paths["README_SELECTION.md"],
        "# LIRA governing-result candidates\n\n"
        "This bundle is review-only. It selected no governing result, created no\n"
        "`ProjectElement` and did not create or mutate any RX38 file.\n\n"
        f"- Source forces: `{source}`\n"
        f"- Source SHA-256: `{source_sha}`\n"
        f"- Candidates: {len(candidates)} ({CANDIDATE_STATUS})\n"
        f"- Unique elements: {len(elements)}\n"
        f"- Components present: {', '.join(components_seen) or 'none'}\n"
        "- Governing result declared: **none**\n"
        "- RX38 force generation: **BLOCKED**\n"
        "- Issue readiness: **NOT_READY_FOR_ISSUE**\n\n"
        "## Why nothing is selected automatically\n\n"
        "An envelope rule such as `max(abs(all_values))` over stations, load cases\n"
        "and combinations is not a validated governing-result semantics. Selecting a\n"
        "governing row is an engineering decision that must be declared explicitly\n"
        "and later bound to independent evidence (for example the governing value\n"
        "shown by the LIRA GUI for the same element).\n\n"
        "## How to declare a selection\n\n"
        "1. Read `candidates.csv` (readable) or `candidates.json` (authoritative,\n"
        "   Decimal-safe).\n"
        "2. Copy `selection_template.json` to a new file and fill `declared_by`,\n"
        "   `basis` and one `candidate_id` per element.\n"
        "3. Validate it with `validate-lira-selection`. The validation resolves the\n"
        "   declaration to exactly one existing candidate and never promotes it to\n"
        "   validated evidence.\n",
    )
    return LiraSelectionBundle(
        destination=destination,
        source_forces=source,
        source_forces_sha256=source_sha,
        candidates=candidates,
        written_files=tuple(paths[name] for name in _CANDIDATE_FILES),
    )


def _blocker(code: str, message: str, **extra: object) -> dict[str, object]:
    payload: dict[str, object] = {"code": code, "severity": "BLOCKER", "message": message}
    payload.update(extra)
    return payload


def _warning(code: str, message: str, **extra: object) -> dict[str, object]:
    payload: dict[str, object] = {"code": code, "severity": "WARNING", "message": message}
    payload.update(extra)
    return payload


def _require_text(payload: Mapping[str, Any], field: str, context: str) -> str:
    value = payload.get(field)
    if not isinstance(value, str) or not value.strip():
        raise LiraMappingError(f"{context}: {field} must be a non-empty string")
    return value.strip()


def validate_lira_governing_selection(
    candidates_path: str | Path,
    selection_path: str | Path,
) -> LiraSelectionReport:
    """Resolve an explicit declaration against enumerated candidates.

    The result is always ``governing_result_selection_validated = false``:
    a declaration is an engineering decision, and independent evidence is still
    required before any governing-result rule can be treated as validated.
    """

    candidate_file = Path(candidates_path).resolve(strict=True)
    selection_file = Path(selection_path).resolve(strict=True)
    candidate_payload = _read_json(candidate_file, "candidates file")
    selection_payload = _read_json(selection_file, "selection declaration file")
    if not isinstance(candidate_payload, Mapping):
        raise LiraFormatError("candidates.json must contain an object")
    if not isinstance(selection_payload, Mapping):
        raise LiraMappingError("the selection declaration must be a JSON object")

    raw_candidates = candidate_payload.get("candidates")
    if not isinstance(raw_candidates, list):
        raise LiraFormatError("candidates.json has no candidates list")

    by_id: dict[str, Mapping[str, Any]] = {}
    element_of: dict[str, str] = {}
    elements: set[str] = set()
    combinations_present = False
    for raw in raw_candidates:
        if not isinstance(raw, Mapping):
            raise LiraFormatError("candidate entries must be objects")
        candidate_id = _require_text(raw, "candidate_id", "candidates.json")
        identity = raw.get("identity")
        if not isinstance(identity, Mapping):
            raise LiraFormatError(f"candidate {candidate_id} has no identity object")
        element_id = _require_text(identity, "element_id", f"candidate {candidate_id}")
        by_id[candidate_id] = raw
        element_of[candidate_id] = element_id
        elements.add(element_id)
        if identity.get("combination"):
            combinations_present = True

    declaration_kind = selection_payload.get("declaration_kind")
    if declaration_kind != DECLARATION_KIND:
        raise LiraMappingError(
            f"declaration_kind must be {DECLARATION_KIND!r}, got {declaration_kind!r}"
        )
    declared_by = _require_text(selection_payload, "declared_by", "declaration")
    basis = _require_text(selection_payload, "basis", "declaration")
    declarations = selection_payload.get("elements")
    if not isinstance(declarations, list):
        raise LiraMappingError("the declaration must contain an elements list")

    blockers: list[dict[str, object]] = []
    warnings: list[dict[str, object]] = []
    resolved: list[dict[str, object]] = []
    seen_elements: set[str] = set()

    for index, entry in enumerate(declarations):
        context = f"declaration elements[{index}]"
        if not isinstance(entry, Mapping):
            blockers.append(
                _blocker(BLOCKER_INVALID_DECLARATION, f"{context} is not an object")
            )
            continue
        raw_element_id = entry.get("element_id")
        if not isinstance(raw_element_id, str) or not raw_element_id.strip():
            blockers.append(
                _blocker(
                    BLOCKER_INVALID_DECLARATION, f"{context} has no element_id string"
                )
            )
            continue
        declared_element = raw_element_id.strip()
        raw_candidate_id = entry.get("candidate_id")
        if raw_candidate_id is None:
            continue
        if not isinstance(raw_candidate_id, str) or not raw_candidate_id.strip():
            blockers.append(
                _blocker(
                    BLOCKER_INVALID_DECLARATION,
                    f"{context} has an unusable candidate_id",
                    element_id=declared_element,
                )
            )
            continue
        declared_candidate = raw_candidate_id.strip()

        if declared_element not in elements:
            blockers.append(
                _blocker(
                    BLOCKER_DECLARED_ELEMENT_UNKNOWN,
                    f"element {declared_element!r} has no candidate in this bundle",
                    element_id=declared_element,
                )
            )
            continue
        if declared_element in seen_elements:
            blockers.append(
                _blocker(
                    BLOCKER_DECLARED_ELEMENT_DUPLICATE,
                    f"element {declared_element!r} is declared more than once",
                    element_id=declared_element,
                )
            )
            continue
        seen_elements.add(declared_element)

        candidate = by_id.get(declared_candidate)
        if candidate is None:
            blockers.append(
                _blocker(
                    BLOCKER_DECLARED_CANDIDATE_UNKNOWN,
                    f"candidate {declared_candidate!r} does not exist in this bundle",
                    element_id=declared_element,
                )
            )
            continue
        if element_of[declared_candidate] != declared_element:
            blockers.append(
                _blocker(
                    BLOCKER_DECLARED_ELEMENT_MISMATCH,
                    f"candidate {declared_candidate!r} belongs to element "
                    f"{element_of[declared_candidate]!r}, not {declared_element!r}",
                    element_id=declared_element,
                )
            )
            continue
        resolved.append(
            {
                "element_id": declared_element,
                "candidate_id": declared_candidate,
                "identity": dict(candidate.get("identity") or {}),
                "source": dict(candidate.get("source") or {}),
                "native_values": dict(candidate.get("native_values") or {}),
                "gui_observed_governing": entry.get("gui_observed_governing"),
                "declared_by": declared_by,
                "basis": basis,
                "status": DECLARATION_STATUS,
            }
        )

    if not combinations_present:
        warnings.append(
            _warning(
                WARNING_COMBINATION_IDENTITY_MISSING,
                "no candidate carries a load-combination identity, so a governing "
                "selection across combinations cannot be evidenced from this source",
            )
        )
    per_element_counts: dict[str, int] = {}
    for element_id in element_of.values():
        per_element_counts[element_id] = per_element_counts.get(element_id, 0) + 1
    if per_element_counts and all(count == 1 for count in per_element_counts.values()):
        warnings.append(
            _warning(
                WARNING_SINGLE_CANDIDATE,
                "every element has exactly one candidate; this source cannot "
                "demonstrate that a selection rule reproduces governing behavior",
            )
        )

    blockers.append(
        _blocker(
            BLOCKER_GOVERNING_SELECTION,
            "no validated rule selects the governing LIRA station, load case, "
            "combination and station envelope; the declaration records a choice "
            "but does not close this blocker",
        )
    )
    blockers.append(
        _blocker(
            BLOCKER_DECLARATION_NOT_EVIDENCE,
            "an engineering declaration is not independent evidence; the "
            "governing value shown by the source program for the same element is "
            "still required before RX38 force generation",
        )
    )

    status = "BLOCKED" if blockers else "DECLARED_UNVALIDATED"
    return LiraSelectionReport(
        status=status,
        elements_total=len(elements),
        elements_declared=len(resolved),
        candidates_total=len(by_id),
        resolved=tuple(resolved),
        blockers=tuple(blockers),
        warnings=tuple(warnings),
    )
