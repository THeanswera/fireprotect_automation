"""One reproducible preparation command for a controlled LIRA→RX3 bar run.

The operator supplies a small, non-numeric request: an existing verified
package, the identifier of one published RSU row, the experiment plan and an
output directory. Everything else — source paths, reading settings, decimal
values, geometry, element chain, hashes and provenance — is derived from the
re-read sources by this module. No number is retyped by hand.

Guarantees kept by construction:

* source files are re-read and their SHA-256 is compared with the ones recorded
  in the source package; any drift stops the run unless it is explicitly
  accepted, and the accepted drift is written into the run manifest;
* the physical bar is not assembled from equal marks: the element chain, its
  end nodes and its single profile are derived from the model structure and
  then re-proved by the existing chain checks;
* the row is never selected by a maximum, and no governing result is chosen;
* the produced declaration is an explicitly unsigned technical draft whose
  per-decision roles come from the conditions document; a signature is never
  invented;
* nothing is written into the source package, no file is overwritten, and a
  repeated run with identical inputs reports `RUN_ALREADY_PREPARED` instead of
  duplicating the package;
* no RX38 is created and the release gate stays closed.
"""

from __future__ import annotations

import hashlib
import json
import shutil
import tempfile
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from typing import Any, Mapping, Sequence

from .assembly import (
    LiraAssembledElement,
    assemble_lira_model,
    prepare_lira_model_bundle,
    read_element_table,
    read_node_table,
    read_stiffness_table,
)
from .errors import LiraFormatError, LiraMappingError
from .experiment_input import (
    DECLARATION_KIND,
    DECLARATION_STATUS_DRAFT,
    BarAuthorship,
    BarChain,
    BarDesignConditions,
    BarExperimentDeclaration,
    BarProfileDeclaration,
    analyse_components,
    build_bar_chain,
    prepare_bar_experiment_input,
    read_authorship,
)
from .rsu import import_rsu_xls_bundle, validate_rsu_reconstruction
from .rsu_evidence import (
    EVIDENCE_KIND,
    read_json_object,
    read_rsu_evidence,
    require_mapping,
    require_text,
    sha256_file,
)
from .rsu_link import prepare_linked_rsu_bundle
from .rsu_review import prepare_rsu_review_bundle

CONDITIONS_KIND = "LIRA_BAR_EXPERIMENT_CONDITIONS"
RUN_MANIFEST_KIND = "LIRA_BAR_RUN_MANIFEST"
STATUS_PREPARED = "RUN_PREPARED"
STATUS_ALREADY_PREPARED = "RUN_ALREADY_PREPARED"
STATUS_DRY_RUN_OK = "RUN_DRY_RUN_OK"
STATUS_DRIFT = "SOURCE_DRIFT_DETECTED"

RUN_FILES = ("run_manifest.json", "declaration.json", "README_RUN.md")

_GEOMETRY_SOURCES = ("stiffness", "elements", "nodes")
_RSU_SOURCES = ("forces", "published", "coefficients", "parameters")
_MODEL_SOURCE_NAMES = {"stiffness": "stiffness", "elements": "elements", "nodes": "nodes"}
_DESIGN_CONDITION_KEYS = (
    "effective_length_m",
    "support_condition",
    "heating_sides",
    "fire_regime",
    "required_fire_resistance_min",
)
_PROFILE_CONDITION_KEYS = (
    "standard",
    "plane",
    "scheme_flag",
    "rx3_template",
    "stress_state",
)


def _write_new(path: Path, content: str) -> None:
    with path.open("x", encoding="utf-8", newline="\n") as stream:
        stream.write(content)


def _write_json(path: Path, payload: object) -> None:
    _write_new(path, json.dumps(payload, ensure_ascii=False, indent=2) + "\n")


def _text(payload: Mapping[str, Any], field: str, context: str) -> str:
    value = payload.get(field)
    if not isinstance(value, str) or not value.strip():
        raise LiraMappingError(f"{context}: {field} must be a non-empty string")
    return value.strip()


def _optional_text(
    payload: Mapping[str, Any], field: str, context: str
) -> str | None:
    value = payload.get(field)
    if value is None:
        return None
    return _text(payload, field, context)


def _header_row(entry: Mapping[str, Any], context: str) -> int:
    token = entry.get("header_row")
    if isinstance(token, int) and not isinstance(token, bool):
        row = token
    elif isinstance(token, str) and token.strip().isdigit():
        row = int(token.strip())
    else:
        raise LiraFormatError(
            f"{context}: header_row is missing or not a number; refusing to guess"
        )
    if row < 1:
        raise LiraFormatError(f"{context}: header_row must be >= 1")
    return row


@dataclass(frozen=True, slots=True)
class SourceFile:
    """One re-verified source table with the settings recorded earlier."""

    role: str
    path: Path
    recorded_sha256: str
    entry: Mapping[str, Any]


@dataclass(frozen=True, slots=True)
class SourcePackage:
    root: Path
    model_dir: Path
    evidence_path: Path
    geometry: Mapping[str, SourceFile]
    rsu: Mapping[str, SourceFile]


@dataclass(frozen=True, slots=True)
class RunConditions:
    """Document-derived teaching conditions, never a human signature."""

    bar_id: str
    join_basis: str
    profile: Mapping[str, str | None]
    design_conditions: BarDesignConditions
    authorship: BarAuthorship

    def as_dict(self) -> dict[str, object]:
        return {
            "bar_id": self.bar_id,
            "join_basis": self.join_basis,
            "profile": dict(self.profile),
            "design_conditions": self.design_conditions.as_dict(),
            "decisions": self.authorship.as_dict(),
        }


def read_run_conditions(path: str | Path) -> RunConditions:
    """Read the machine form of the experiment plan's chosen conditions."""

    source = Path(path).resolve(strict=True)
    root = read_json_object(source, "bar experiment conditions")
    allowed = {
        "kind",
        "bar_id",
        "join_basis",
        "profile",
        "design_conditions",
        "decisions",
    }
    if set(root) != allowed:
        raise LiraMappingError(
            f"{source}: conditions must contain exactly {sorted(allowed)}"
        )
    if root.get("kind") != CONDITIONS_KIND:
        raise LiraMappingError(f"{source}: kind must be {CONDITIONS_KIND!r}")
    profile = require_mapping(root.get("profile"), "profile", str(source))
    if set(profile) != set(_PROFILE_CONDITION_KEYS):
        raise LiraMappingError(
            f"{source}: profile must contain exactly "
            f"{sorted(_PROFILE_CONDITION_KEYS)}; geometry and rotation are read "
            "from the verified package instead of being retyped"
        )
    design = require_mapping(
        root.get("design_conditions"), "design_conditions", str(source)
    )
    if set(design) != set(_DESIGN_CONDITION_KEYS):
        raise LiraMappingError(
            f"{source}: design_conditions must contain exactly "
            f"{sorted(_DESIGN_CONDITION_KEYS)}"
        )
    authorship = read_authorship(root.get("decisions"), context=str(source))
    return RunConditions(
        bar_id=_text(root, "bar_id", str(source)),
        join_basis=_text(root, "join_basis", str(source)),
        profile={
            name: _optional_text(profile, name, str(source))
            for name in _PROFILE_CONDITION_KEYS
        },
        design_conditions=BarDesignConditions(
            **{
                name: _optional_text(design, name, str(source))
                for name in _DESIGN_CONDITION_KEYS
            }
        ),
        authorship=authorship,
    )


def conditions_template() -> dict[str, object]:
    """A conditions skeleton with every unknown left empty."""

    return {
        "kind": CONDITIONS_KIND,
        "bar_id": "",
        "join_basis": "",
        "profile": {name: None for name in _PROFILE_CONDITION_KEYS},
        "design_conditions": {name: None for name in _DESIGN_CONDITION_KEYS},
        "decisions": {
            name: {"role": "", "basis": "", "reference": None}
            for name in ("bar", "experiment_row", "profile", "design_conditions")
        },
    }


def write_conditions_template(output_path: str | Path) -> Path:
    """Write a blank conditions template; refuses to overwrite."""

    target = Path(output_path).resolve(strict=False)
    if target.exists():
        raise LiraFormatError(
            f"conditions template already exists; refusing to overwrite: {target}"
        )
    target.parent.mkdir(parents=True, exist_ok=True)
    _write_new(
        target,
        json.dumps(conditions_template(), ensure_ascii=False, indent=2) + "\n",
    )
    return target


def read_source_package(source_package: str | Path) -> SourcePackage:
    """Read a verified package and list every recorded source file.

    The recorded hashes are read, not trusted: the caller compares them with the
    files on disk and decides whether a difference may be accepted. Evidence
    rows are therefore not parsed here — a package whose sources changed must be
    able to report the drift instead of crashing while opening the evidence.
    """

    root = Path(source_package).resolve(strict=True)
    model_dir = root / "model"
    evidence_path = root / "rsu" / "rsu_evidence.json"
    manifest_path = model_dir / "manifest.json"
    manifest = read_json_object(manifest_path, "model package manifest")
    if manifest.get("status") != "MODEL_ASSEMBLED":
        raise LiraMappingError(
            f"{manifest_path}: status must be 'MODEL_ASSEMBLED'"
        )
    sources_block = require_mapping(
        manifest.get("sources"), "sources", str(manifest_path)
    )
    geometry: dict[str, SourceFile] = {}
    for name in _GEOMETRY_SOURCES:
        entry = require_mapping(sources_block.get(name), name, str(manifest_path))
        geometry[name] = SourceFile(
            role=name,
            path=Path(require_text(entry, "path", str(manifest_path))),
            recorded_sha256=require_text(entry, "sha256", str(manifest_path)),
            entry=entry,
        )
        if not isinstance(entry.get("sheet"), str) or not entry.get("sheet"):
            raise LiraFormatError(
                f"{manifest_path}: {name} source has no recorded sheet name; "
                "refusing to guess the reading settings"
            )
        _header_row(entry, str(manifest_path))
    raw_evidence = read_json_object(evidence_path, "RSU evidence")
    if raw_evidence.get("kind") != EVIDENCE_KIND:
        raise LiraMappingError(
            f"{evidence_path}: kind must be {EVIDENCE_KIND!r}"
        )
    rsu_block = require_mapping(
        raw_evidence.get("sources"), "sources", str(evidence_path)
    )
    rsu: dict[str, SourceFile] = {}
    for name in _RSU_SOURCES:
        entry = require_mapping(rsu_block.get(name), name, str(evidence_path))
        rsu[name] = SourceFile(
            role=name,
            path=Path(require_text(entry, "path", str(evidence_path))),
            recorded_sha256=require_text(entry, "sha256", str(evidence_path)),
            entry=entry,
        )
    return SourcePackage(
        root=root,
        model_dir=model_dir,
        evidence_path=evidence_path,
        geometry=geometry,
        rsu=rsu,
    )


def _drift_report(package: SourcePackage) -> list[dict[str, object]]:
    """List every recorded source whose current bytes differ from the record."""

    drift: list[dict[str, object]] = []
    for source in (*package.geometry.values(), *package.rsu.values()):
        if not source.path.is_file():
            drift.append(
                {
                    "role": source.role,
                    "path": str(source.path),
                    "state": "MISSING",
                    "recorded_sha256": source.recorded_sha256,
                    "actual_sha256": None,
                }
            )
            continue
        actual = sha256_file(source.path)
        if actual != source.recorded_sha256:
            drift.append(
                {
                    "role": source.role,
                    "path": str(source.path),
                    "state": "CONTENT_CHANGED",
                    "recorded_sha256": source.recorded_sha256,
                    "actual_sha256": actual,
                }
            )
    return drift


def _read_geometry(package: SourcePackage) -> tuple[
    tuple[LiraAssembledElement, ...],
    Mapping[str, tuple[Decimal, Decimal, Decimal]],
]:
    """Read and assemble the model, and return node coordinates for the chain."""

    entries = package.geometry
    stiffnesses = read_stiffness_table(
        entries["stiffness"].path,
        sheet_name=str(entries["stiffness"].entry["sheet"]),
        header_row=_header_row(entries["stiffness"].entry, str(entries["stiffness"].path)),
    )
    elements = read_element_table(
        entries["elements"].path,
        sheet_name=str(entries["elements"].entry["sheet"]),
        header_row=_header_row(entries["elements"].entry, str(entries["elements"].path)),
    )
    nodes = read_node_table(
        entries["nodes"].path,
        sheet_name=str(entries["nodes"].entry["sheet"]),
        header_row=_header_row(entries["nodes"].entry, str(entries["nodes"].path)),
    )
    assembled = assemble_lira_model(
        stiffnesses=stiffnesses, elements=elements, nodes=nodes
    )
    coordinates: dict[str, tuple[Decimal, Decimal, Decimal]] = {}
    for node in nodes:
        if node.x is None or node.y is None or node.z is None:
            continue
        coordinates[node.node_id] = (node.x, node.y, node.z)
    return assembled, coordinates


def derive_single_bar_chain(
    assembled: Sequence[LiraAssembledElement],
) -> tuple[tuple[str, ...], str, str]:
    """Derive the only open element chain of a model, without guessing a bar.

    Equality of marks is never used: the chain is read from the element-node
    topology. The model must be exactly one open, unbranched path; a model with
    several chains, a cycle or a branch is refused instead of being split by
    heuristic.
    """

    if not assembled:
        raise LiraMappingError("the model contains no elements")
    by_id = {item.element_id: item for item in assembled}
    adjacency: dict[str, list[str]] = {}
    for element in assembled:
        if len(element.node_ids) != 2:
            raise LiraMappingError(
                f"element {element.element_id!r} is not a two-node bar; the "
                "single-chain derivation refuses it"
            )
        if element.blockers:
            raise LiraMappingError(
                f"element {element.element_id!r} is blocked: "
                f"{'; '.join(element.blockers)}"
            )
        for node_id in element.node_ids:
            adjacency.setdefault(node_id, []).append(element.element_id)
    if any(len(owners) > 2 for owners in adjacency.values()):
        raise LiraMappingError(
            "the model branches; a single physical bar cannot be derived"
        )
    ends = sorted(node for node, owners in adjacency.items() if len(owners) == 1)
    if len(ends) != 2:
        raise LiraMappingError(
            f"the model has {len(ends)} free ends instead of two; it is not one "
            "open chain"
        )

    def walk(start: str) -> tuple[tuple[str, ...], str] | None:
        current = start
        used: list[str] = []
        seen: set[str] = set()
        while True:
            candidates = [
                element_id
                for element_id in adjacency.get(current, ())
                if element_id not in seen
            ]
            if not candidates:
                break
            if len(candidates) > 1:
                return None
            element = by_id[candidates[0]]
            if element.node_ids[0] != current:
                return None
            seen.add(element.element_id)
            used.append(element.element_id)
            current = element.node_ids[1]
        return tuple(used), current

    for start, end in ((ends[0], ends[1]), (ends[1], ends[0])):
        result = walk(start)
        if result is None:
            continue
        ordered, final = result
        if len(ordered) == len(assembled) and final == end:
            return ordered, start, end
    raise LiraMappingError(
        "the elements do not form one connected chain in the recorded source "
        "node order; orientation is never rewritten"
    )


def _profile_of(
    assembled: Sequence[LiraAssembledElement],
    ordered_ids: Sequence[str],
    conditions: RunConditions,
    *,
    context: str,
) -> BarProfileDeclaration:
    by_id = {item.element_id: item for item in assembled}
    profiles = {
        (by_id[element_id].mark, by_id[element_id].designation, by_id[element_id].kind_word)
        for element_id in ordered_ids
    }
    if len(profiles) != 1:
        raise LiraMappingError(
            f"{context}: the chain elements carry differing profiles: "
            f"{sorted(str(item) for item in profiles)}"
        )
    mark, designation, kind_word = next(iter(profiles))
    if mark is None or designation is None or kind_word is None:
        raise LiraMappingError(
            f"{context}: the chain profile is incomplete in the source "
            f"({mark!r}, {designation!r}, {kind_word!r})"
        )
    rotations = {by_id[element_id].rotation_angle_degrees for element_id in ordered_ids}
    if len(rotations) != 1 or None in rotations:
        raise LiraMappingError(
            f"{context}: the chain elements carry no single recorded rotation: "
            f"{sorted(str(value) for value in rotations)}"
        )
    (rotation,) = rotations
    assert rotation is not None
    return BarProfileDeclaration(
        kind_word=kind_word,
        designation=designation,
        mark=mark,
        standard=_required_condition(conditions, "standard", context),
        rotation_degrees=rotation,
        plane=_required_condition(conditions, "plane", context),
        scheme_flag=_required_condition(conditions, "scheme_flag", context),
        rx3_template=_required_condition(conditions, "rx3_template", context),
        stress_state=conditions.profile["stress_state"],
        confirmed_by=None,
        basis=conditions.authorship.profile.basis,
    )


def _required_condition(
    conditions: RunConditions, field: str, context: str
) -> str:
    value = conditions.profile[field]
    if value is None:
        raise LiraMappingError(
            f"{context}: the experiment conditions do not state {field!r}; it is "
            "not present in the LIRA tables, so it is never guessed"
        )
    return value


def _declaration_payload(
    *,
    linked_manifest_sha256: str,
    conditions: RunConditions,
    chain: BarChain,
    profile: BarProfileDeclaration,
    row_id: str,
) -> dict[str, object]:
    return {
        "declaration_kind": DECLARATION_KIND,
        "declaration_status": DECLARATION_STATUS_DRAFT,
        "linked_manifest_sha256": linked_manifest_sha256,
        "bar": {
            "bar_id": conditions.bar_id,
            "element_ids": list(chain.ordered_element_ids),
            "end_node_ids": [chain.start_node, chain.end_node],
            "join_basis": conditions.join_basis,
            "confirmed_by": None,
        },
        "experiment_row": {
            "row_id": row_id,
            "selection_basis": conditions.authorship.experiment_row.basis,
            "selected_by": None,
        },
        "profile": profile.as_dict(),
        "design_conditions": conditions.design_conditions.as_dict(),
        "authorship": conditions.authorship.as_dict(),
    }


def _request_fingerprint(
    *,
    run_id: str,
    row_id: str,
    plan_path: Path,
    conditions_path: Path | None,
    sources: Mapping[str, str],
    chain: Sequence[str],
    end_nodes: Sequence[str],
) -> str:
    payload = {
        "run_id": run_id,
        "row_id": row_id,
        "plan_sha256": sha256_file(plan_path),
        "conditions_sha256": (
            None if conditions_path is None else sha256_file(conditions_path)
        ),
        "sources_sha256": dict(sorted(sources.items())),
        "chain": list(chain),
        "end_nodes": list(end_nodes),
    }
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _chain_summary(chain: BarChain) -> dict[str, object]:
    return {
        "element_ids": list(chain.ordered_element_ids),
        "end_node_ids": [chain.start_node, chain.end_node],
        "element_lengths_m": dict(chain.element_lengths_m),
        "bar_length_m": str(chain.bar_length_m),
    }


def _readme_text(manifest: Mapping[str, Any]) -> str:
    lines = [
        "# Пакет подготовки технического опыта ЛИРА→RX3",
        "",
        f"- run_id: **{manifest['run_id']}**",
        f"- Статус: **{manifest['status']}**",
        f"- Пакет-источник: {manifest['source_package']}",
        f"- Строка РСУ: {manifest['experiment_row_id']}",
        f"- Постановка опыта: {manifest['plan_reference']['path']}",
        f"- Подпись инженера: {'есть' if manifest['engineer_confirmed'] else 'НЕТ (DRAFT)'}",
        "",
        "## Что проверено",
        "",
    ]
    for name, value in manifest["checks"].items():
        lines.append(f"- {name}: {'да' if value else 'НЕТ'}")
    lines.extend(
        [
            "",
            "## Готовые файлы",
            "",
            "- `derived/model`, `derived/rsu`, `derived/linked` — повторно собранные "
            "и перепроверенные производные пакеты;",
            "- `declaration.json` — черновая декларация опыта со статусом "
            "DRAFT_UNSIGNED и реестром ролей;",
            "- `experiment/` — входной пакет опыта (карточка, компоненты, блокеры).",
            "",
            "## Следующее действие",
            "",
            f"{manifest['next_action']}",
            "",
            "## Запрещено на этом шаге",
            "",
            "- запускать RX3 автоматически;",
            "- считать этот пакет разрешением на выпуск;",
            "- заполнять `confirmed_by`/`selected_by` без реального инженера.",
            "",
            "**RX38 не создан. `NOT_READY_FOR_ISSUE`.**",
            "",
        ]
    )
    return "\n".join(lines)


def prepare_bar_run(
    *,
    source_package: str | Path,
    plan_reference: str | Path,
    experiment_row_id: str,
    output_dir: str | Path,
    conditions_path: str | Path | None = None,
    dry_run: bool = False,
    accept_current_sources: bool = False,
) -> dict[str, object]:
    """Prepare one controlled run from minimal operator input."""

    package = read_source_package(source_package)
    plan_path = Path(plan_reference).resolve(strict=True)
    conditions_file = (
        None if conditions_path is None else Path(conditions_path).resolve(strict=True)
    )
    conditions = (
        None if conditions_file is None else read_run_conditions(conditions_file)
    )
    destination = Path(output_dir).resolve(strict=False)
    if destination == package.root:
        raise LiraFormatError("output directory must differ from the source package")
    row_id = experiment_row_id.strip()
    if not row_id:
        raise LiraMappingError("experiment row id must be a non-empty string")

    drift = _drift_report(package)
    if drift and not accept_current_sources:
        return {
            "kind": RUN_MANIFEST_KIND,
            "status": STATUS_DRIFT,
            "source_package": str(package.root),
            "experiment_row_id": row_id,
            "drift": drift,
            "next_action": (
                "Исходные XLS изменились после создания пакета. Проверьте, что "
                "текущая версия файлов — та, которую нужно считать, затем "
                "повторите команду с --accept-current-sources. Ничего не записано."
            ),
            "written_files": [],
            "rx38_created": False,
            "release_forbidden": True,
            "issue_readiness": "NOT_READY_FOR_ISSUE",
        }

    assembled, coordinates = _read_geometry(package)
    ordered_ids, start, end = derive_single_bar_chain(assembled)
    if conditions is None:
        raise LiraMappingError(
            f"{plan_path}: the experiment conditions are required; the LIRA "
            "tables do not carry the standard, RX3 template, plane or scheme "
            "flag, and they are never guessed"
        )
    profile = _profile_of(assembled, ordered_ids, conditions, context=str(package.root))
    probe = _probe_declaration(conditions, ordered_ids, (start, end), profile)
    probe_chain = build_bar_chain(
        assembled, coordinates, probe, context=str(package.root)
    )
    sources = {
        name: sha256_file(source.path)
        for name, source in {**package.geometry, **package.rsu}.items()
    }
    fingerprint = _request_fingerprint(
        run_id=destination.name,
        row_id=row_id,
        plan_path=plan_path,
        conditions_path=conditions_file,
        sources=sources,
        chain=ordered_ids,
        end_nodes=(start, end),
    )

    existing: Mapping[str, Any] | None = None
    manifest_path = destination / "run_manifest.json"
    if manifest_path.is_file():
        existing = read_json_object(manifest_path, "run manifest")
    if destination.exists() and existing is None:
        clashes = [
            str(destination / name)
            for name in ("derived", "experiment", "declaration.json", "README_RUN.md")
            if (destination / name).exists()
        ]
        if clashes:
            raise LiraFormatError(
                "output directory already contains files this run would create "
                f"and has no run manifest; refusing to overwrite: {clashes}"
            )
    if existing is not None:
        if existing.get("request_fingerprint") != fingerprint:
            raise LiraFormatError(
                f"{manifest_path}: this directory belongs to a different run "
                f"(recorded {existing.get('request_fingerprint')}, current "
                f"{fingerprint}); choose a new output directory"
            )
        if existing.get("status") == STATUS_PREPARED:
            return dict(existing) | {"status": STATUS_ALREADY_PREPARED}

    plan_block = {
        "path": str(plan_path),
        "sha256": sha256_file(plan_path),
    }
    if conditions_file is not None:
        plan_block["conditions_path"] = str(conditions_file)
        plan_block["conditions_sha256"] = sha256_file(conditions_file)
    authoring = {
        "declaration_status": DECLARATION_STATUS_DRAFT,
        "engineer_confirmed": False,
        "decisions": conditions.authorship.as_dict(),
    }
    checks = {
        "sources_match_recorded_hashes": not drift,
        "chain_derived_from_topology": True,
        "chain_connected": True,
        "profile_consistent": True,
        "row_belongs_to_chain": True,
        "model_rebuilt": True,
        "rsu_reverified": True,
        "linked_rebuilt": True,
    }
    summary = {
        "kind": RUN_MANIFEST_KIND,
        "run_id": destination.name,
        "source_package": str(package.root),
        "experiment_row_id": row_id,
        "plan_reference": plan_block,
        "output_dir": str(destination),
        "request_fingerprint": fingerprint,
        **authoring,
        "bar": _chain_summary(probe_chain),
        "profile": profile.as_dict(),
        "design_conditions": conditions.design_conditions.as_dict(),
        "source_drift_accepted": drift or None,
        "governing_result_selection": None,
        "rx38_created": False,
        "release_forbidden": True,
        "issue_readiness": "NOT_READY_FOR_ISSUE",
    }
    experiment_file = destination / "experiment" / "experiment_input.json"
    if dry_run:
        with tempfile.TemporaryDirectory(prefix="fireprotect-run-") as temporary:
            derived = Path(temporary) / "derived"
            row = _build_derived(package, derived, row_id)
            components, blockers = analyse_components(
                row, probe, probe_chain.bar_length_m
            )
        return dict(summary) | {
            "status": STATUS_DRY_RUN_OK,
            "components": components,
            "blockers": blockers,
            "missing_confirmations": [
                *conditions.design_conditions.missing(),
                *(["stress_state"] if profile.stress_state is None else []),
            ],
            "checks": checks,
            "next_action": (
                "Пробный прогон без записи. Повторите команду без --dry-run, "
                "чтобы создать пакет в указанном каталоге."
            ),
            "written_files": [],
        }

    destination.mkdir(parents=True, exist_ok=True)
    try:
        derived = destination / "derived"
        row = _build_derived(package, derived, row_id)
        linked_manifest_sha = sha256_file(derived / "linked" / "manifest.json")
        declaration_path = destination / "declaration.json"
        _write_json(
            declaration_path,
            _declaration_payload(
                linked_manifest_sha256=linked_manifest_sha,
                conditions=conditions,
                chain=probe_chain,
                profile=profile,
                row_id=row_id,
            ),
        )
        experiment = prepare_bar_experiment_input(
            linked_dir=derived / "linked",
            declaration_path=declaration_path,
            output_dir=destination / "experiment",
        )
    except Exception:
        # Remove only what this run created; pre-existing files stay untouched.
        for name in ("derived", "experiment", "declaration.json"):
            target = destination / name
            if target.is_dir():
                shutil.rmtree(target, ignore_errors=True)
            elif target.exists():
                target.unlink()
        raise
    manifest: dict[str, object] = dict(summary) | {
        "status": STATUS_PREPARED,
        "source_files": {
            name: {
                "path": str(source.path),
                "recorded_sha256": source.recorded_sha256,
                "current_sha256": sources[name],
            }
            for name, source in {**package.geometry, **package.rsu}.items()
        },
        "missing_confirmations": _list_field(
            experiment, "missing_confirmations"
        ),
        "components": _list_field(experiment, "components"),
        "blockers": _list_field(experiment, "blockers"),
        "checks": checks,
        "derived": {
            "model_manifest_sha256": sha256_file(derived / "model" / "manifest.json"),
            "rsu_evidence_sha256": sha256_file(
                derived / "rsu" / "rsu_evidence.json"
            ),
            "linked_manifest_sha256": linked_manifest_sha,
            "experiment_status": experiment.get("status"),
        },
        "next_action": (
            "Проверьте README_RUN.md и experiment/experiment_card.md. Затем "
            "выполните один ручной этап RX3 по NEEDS_HUMAN.md: подготовка "
            "расчётного файла и расчёт выполняются человеком, автоматически "
            "RX3 не запускается."
        ),
    }
    manifest["written_files"] = [
        str(destination / name) for name in RUN_FILES
    ] + _experiment_written_files(experiment)
    assert not experiment_file.exists() or experiment_file.is_file()
    _write_json(manifest_path, manifest)
    _write_new(destination / "README_RUN.md", _readme_text(manifest))
    return manifest


def _list_field(payload: Mapping[str, object], field: str) -> list[object]:
    value = payload.get(field)
    if not isinstance(value, (list, tuple)):
        return []
    return list(value)


def _build_derived(
    package: SourcePackage, derived: Path, row_id: str
) -> Any:
    """Rebuild model, re-verified RSU evidence and the linked package."""

    model_dir = derived / "model"
    rsu_dir = derived / "rsu"
    linked_dir = derived / "linked"
    prepare_lira_model_bundle(
        stiffness_path=package.geometry["stiffness"].path,
        element_path=package.geometry["elements"].path,
        node_path=package.geometry["nodes"].path,
        output_dir=model_dir,
        stiffness_sheet=str(package.geometry["stiffness"].entry["sheet"]),
        stiffness_header_row=_header_row(
            package.geometry["stiffness"].entry,
            str(package.geometry["stiffness"].path),
        ),
        element_sheet=str(package.geometry["elements"].entry["sheet"]),
        element_header_row=_header_row(
            package.geometry["elements"].entry,
            str(package.geometry["elements"].path),
        ),
        node_sheet=str(package.geometry["nodes"].entry["sheet"]),
        node_header_row=_header_row(
            package.geometry["nodes"].entry,
            str(package.geometry["nodes"].path),
        ),
    )
    bundle = import_rsu_xls_bundle(
        forces_path=package.rsu["forces"].path,
        published_path=package.rsu["published"].path,
        coefficients_path=package.rsu["coefficients"].path,
        parameters_path=package.rsu["parameters"].path,
    )
    report = validate_rsu_reconstruction(bundle)
    if report.status.value != "VERIFIED":
        raise LiraMappingError(
            f"RSU reconstruction status is {report.status.value!r}; the run is "
            f"refused: {'; '.join(report.blockers)}"
        )
    prepare_rsu_review_bundle(bundle, report, rsu_dir)
    prepare_linked_rsu_bundle(
        model_dir=model_dir,
        evidence_path=rsu_dir / "rsu_evidence.json",
        output_dir=linked_dir,
    )
    fresh = read_rsu_evidence(rsu_dir / "rsu_evidence.json")
    matches = [row for row in fresh.rows if row.row_id == row_id]
    if not matches:
        raise LiraMappingError(
            f"{rsu_dir / 'rsu_evidence.json'}: selected row {row_id!r} does not "
            "exist in the re-read sources"
        )
    return matches[0]


def _experiment_written_files(experiment: Mapping[str, object]) -> list[str]:
    written = experiment.get("written_files")
    if not isinstance(written, (list, tuple)):
        return []
    return [str(item) for item in written]


def _probe_declaration(
    conditions: RunConditions,
    ordered_ids: Sequence[str],
    end_nodes: tuple[str, str],
    profile: BarProfileDeclaration,
) -> BarExperimentDeclaration:
    """Build an in-memory declaration used only for the existing chain checks."""

    return BarExperimentDeclaration(
        declaration_status=DECLARATION_STATUS_DRAFT,
        linked_manifest_sha256="0" * 64,
        bar_id=conditions.bar_id,
        element_ids=tuple(ordered_ids),
        end_node_ids=end_nodes,
        join_basis=conditions.join_basis,
        bar_confirmed_by=None,
        row_id="",
        selection_basis=conditions.authorship.experiment_row.basis,
        selected_by=None,
        profile=profile,
        design_conditions=conditions.design_conditions,
        authorship=conditions.authorship,
    )
