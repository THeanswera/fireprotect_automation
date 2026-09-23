"""Prepare the input package of one controlled LIRA→RX3 experiment.

One physical bar, one explicitly selected technical-experiment RSU row, one
re-verified linked model+RSU package. This module writes no RX38 and opens no
production gate: the produced package is experiment input only, never a
permission to calculate or to release.

Narrow structural contract (checked, never assumed):

* the declared finite elements form one connected straight chain without
  branching, ordered end-to-end between the two declared end nodes;
* every element of the chain is a two-node bar with zero local-axis rotation;
* all chain elements share one profile (kind word, designation, mark), which
  must equal the declared profile;
* the selected RSU row belongs to one of the chain elements and keeps the full
  signed N/Mk/My/Mz/Qy/Qz vector with its provenance;
* the validated scoped convention (22П / ГОСТ 8240-97 / Б2 / one-plane X-X /
  L=3.00 m / rotation 0) is consulted through the existing registry: supported
  components remain My→field50 and Qz→field92 with MAGNITUDE; any nonzero
  component outside the resolved convention blocks further transfer;
* elements are never joined automatically by mark, and no row is selected via
  a maximum; the declared row is a technical-experiment row, not a governing
  result.

Element lengths, the physical bar length and the separately declared design
conditions stay distinct; unknown design conditions stay null and are listed
as missing confirmations. Profile and length agreement alone does not confirm
full RX3 compatibility.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Mapping, Sequence

from .assembly import LiraAssembledElement, read_node_table
from .convention import LiraRx3ConventionRegistry
from .errors import LiraFormatError, LiraMappingError
from .rsu_evidence import (
    RSU_COMPONENTS,
    RsuEvidenceRow,
    read_json_object,
    read_rsu_evidence,
    require_mapping,
    require_text,
    sha256_file,
)
from .rsu_link import link_rsu_rows_to_elements, read_model_package
from .units import to_review_unit

DECLARATION_KIND = "LIRA_BAR_EXPERIMENT_DECLARATION"
PACKAGE_KIND = "LIRA_BAR_EXPERIMENT_INPUT"
STATUS_READY = "EXPERIMENT_INPUT_READY"
STATUS_BLOCKED = "EXPERIMENT_INPUT_BLOCKED"

_LINK_FILES = (
    "manifest.json",
    "experiment_input.json",
    "experiment_card.md",
    "declaration_template.json",
)

_DESIGN_CONDITION_KEYS = (
    "effective_length_m",
    "support_condition",
    "heating_sides",
    "fire_regime",
    "required_fire_resistance_min",
)

# Same-line tolerance for float-export noise in node coordinates. The teaching
# scheme uses clean coordinates; this only absorbs binary export drift and
# never treats a genuinely bent chain as straight.
_COLLINEAR_TOLERANCE = Decimal("1e-9")


def _write_new(path: Path, content: str) -> None:
    with path.open("x", encoding="utf-8", newline="\n") as stream:
        stream.write(content)


def _write_json(path: Path, payload: object) -> None:
    _write_new(path, json.dumps(payload, ensure_ascii=False, indent=2) + "\n")


def _decimal_token(token: object, *, field: str, context: str) -> Decimal:
    if isinstance(token, bool) or not isinstance(token, (str, int)):
        raise LiraMappingError(f"{context}: {field} must be a decimal string")
    try:
        value = Decimal(str(token))
    except InvalidOperation as exc:
        raise LiraMappingError(f"{context}: {field} is not a number") from exc
    if not value.is_finite():
        raise LiraMappingError(f"{context}: {field} must be finite")
    return value


def _optional_design_value(
    payload: Mapping[str, Any], field: str, context: str
) -> str | None:
    value = payload.get(field)
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip():
        raise LiraMappingError(
            f"{context}: {field} must be a non-empty string or null"
        )
    return value.strip()


def _declaration_text(
    payload: Mapping[str, Any], field: str, context: str
) -> str:
    value = payload.get(field)
    if not isinstance(value, str) or not value.strip():
        raise LiraMappingError(f"{context}: {field} must be a non-empty string")
    return value.strip()


def _declaration_mapping(
    payload: object, field: str, context: str
) -> Mapping[str, Any]:
    if not isinstance(payload, Mapping):
        raise LiraMappingError(f"{context}: {field} must be a JSON object")
    return payload


@dataclass(frozen=True, slots=True)
class BarProfileDeclaration:
    kind_word: str
    designation: str
    mark: str
    standard: str
    rotation_degrees: Decimal
    plane: str
    scheme_flag: str
    rx3_template: str
    stress_state: str | None
    confirmed_by: str
    basis: str

    def as_dict(self) -> dict[str, object]:
        return {
            "kind_word": self.kind_word,
            "designation": self.designation,
            "mark": self.mark,
            "standard": self.standard,
            "rotation_degrees": str(self.rotation_degrees),
            "plane": self.plane,
            "scheme_flag": self.scheme_flag,
            "rx3_template": self.rx3_template,
            "stress_state": self.stress_state,
            "confirmed_by": self.confirmed_by,
            "basis": self.basis,
        }


@dataclass(frozen=True, slots=True)
class BarDesignConditions:
    effective_length_m: str | None
    support_condition: str | None
    heating_sides: str | None
    fire_regime: str | None
    required_fire_resistance_min: str | None

    def missing(self) -> tuple[str, ...]:
        return tuple(
            name for name in _DESIGN_CONDITION_KEYS if getattr(self, name) is None
        )

    def as_dict(self) -> dict[str, str | None]:
        return {name: getattr(self, name) for name in _DESIGN_CONDITION_KEYS}


@dataclass(frozen=True, slots=True)
class BarExperimentDeclaration:
    linked_manifest_sha256: str
    bar_id: str
    element_ids: tuple[str, ...]
    end_node_ids: tuple[str, str]
    join_basis: str
    bar_confirmed_by: str
    row_id: str
    selection_basis: str
    selected_by: str
    profile: BarProfileDeclaration
    design_conditions: BarDesignConditions


def read_bar_declaration(path: str | Path) -> BarExperimentDeclaration:
    """Read and validate one physical-bar experiment declaration."""

    source = Path(path).resolve(strict=True)
    root = read_json_object(source, "bar experiment declaration")
    allowed = {
        "declaration_kind",
        "linked_manifest_sha256",
        "bar",
        "experiment_row",
        "profile",
        "design_conditions",
    }
    if set(root) != allowed:
        raise LiraMappingError(
            f"{source}: declaration must contain exactly {sorted(allowed)}"
        )
    if root.get("declaration_kind") != DECLARATION_KIND:
        raise LiraMappingError(
            f"{source}: declaration_kind must be {DECLARATION_KIND!r}"
        )
    bar = _declaration_mapping(root.get("bar"), "bar", str(source))
    allowed_bar = {
        "bar_id",
        "element_ids",
        "end_node_ids",
        "join_basis",
        "confirmed_by",
    }
    if set(bar) != allowed_bar:
        raise LiraMappingError(
            f"{source}: bar must contain exactly {sorted(allowed_bar)}"
        )
    element_ids = bar.get("element_ids")
    if (
        not isinstance(element_ids, list)
        or not element_ids
        or any(not isinstance(item, str) or not item.strip() for item in element_ids)
    ):
        raise LiraMappingError(
            f"{source}: bar.element_ids must be a non-empty list of strings"
        )
    if len(set(element_ids)) != len(element_ids):
        raise LiraMappingError(f"{source}: bar.element_ids contains duplicates")
    end_node_ids = bar.get("end_node_ids")
    if (
        not isinstance(end_node_ids, list)
        or len(end_node_ids) != 2
        or any(not isinstance(item, str) or not item.strip() for item in end_node_ids)
        or end_node_ids[0] == end_node_ids[1]
    ):
        raise LiraMappingError(
            f"{source}: bar.end_node_ids must be exactly two distinct strings"
        )
    row = _declaration_mapping(root.get("experiment_row"), "experiment_row", str(source))
    allowed_row = {"row_id", "selection_basis", "selected_by"}
    if set(row) != allowed_row:
        raise LiraMappingError(
            f"{source}: experiment_row must contain exactly {sorted(allowed_row)}"
        )
    profile_payload = _declaration_mapping(root.get("profile"), "profile", str(source))
    allowed_profile = {
        "kind_word",
        "designation",
        "mark",
        "standard",
        "rotation_degrees",
        "plane",
        "scheme_flag",
        "rx3_template",
        "stress_state",
        "confirmed_by",
        "basis",
    }
    if set(profile_payload) != allowed_profile:
        raise LiraMappingError(
            f"{source}: profile must contain exactly {sorted(allowed_profile)}"
        )
    stress_state = profile_payload.get("stress_state")
    if stress_state is not None and (
        not isinstance(stress_state, str) or not stress_state.strip()
    ):
        raise LiraMappingError(
            f"{source}: profile.stress_state must be a non-empty string or null"
        )
    design = _declaration_mapping(
        root.get("design_conditions"), "design_conditions", str(source)
    )
    if set(design) != set(_DESIGN_CONDITION_KEYS):
        raise LiraMappingError(
            f"{source}: design_conditions must contain exactly "
            f"{sorted(_DESIGN_CONDITION_KEYS)}"
        )
    return BarExperimentDeclaration(
        linked_manifest_sha256=_declaration_text(
            root, "linked_manifest_sha256", str(source)
        ),
        bar_id=_declaration_text(bar, "bar_id", str(source)),
        element_ids=tuple(item.strip() for item in element_ids),
        end_node_ids=(end_node_ids[0].strip(), end_node_ids[1].strip()),
        join_basis=_declaration_text(bar, "join_basis", str(source)),
        bar_confirmed_by=_declaration_text(bar, "confirmed_by", str(source)),
        row_id=_declaration_text(row, "row_id", str(source)),
        selection_basis=_declaration_text(row, "selection_basis", str(source)),
        selected_by=_declaration_text(row, "selected_by", str(source)),
        profile=BarProfileDeclaration(
            kind_word=_declaration_text(profile_payload, "kind_word", str(source)),
            designation=_declaration_text(
                profile_payload, "designation", str(source)
            ),
            mark=_declaration_text(profile_payload, "mark", str(source)),
            standard=_declaration_text(profile_payload, "standard", str(source)),
            rotation_degrees=_decimal_token(
                profile_payload.get("rotation_degrees"),
                field="rotation_degrees",
                context=str(source),
            ),
            plane=_declaration_text(profile_payload, "plane", str(source)),
            scheme_flag=_declaration_text(
                profile_payload, "scheme_flag", str(source)
            ),
            rx3_template=_declaration_text(
                profile_payload, "rx3_template", str(source)
            ),
            stress_state=None if stress_state is None else str(stress_state).strip(),
            confirmed_by=_declaration_text(
                profile_payload, "confirmed_by", str(source)
            ),
            basis=_declaration_text(profile_payload, "basis", str(source)),
        ),
        design_conditions=BarDesignConditions(
            **{
                name: _optional_design_value(design, name, str(source))
                for name in _DESIGN_CONDITION_KEYS
            }
        ),
    )


def declaration_template() -> dict[str, object]:
    """A declaration skeleton with every unknown field left empty."""

    return {
        "declaration_kind": DECLARATION_KIND,
        "linked_manifest_sha256": "",
        "bar": {
            "bar_id": "",
            "element_ids": [],
            "end_node_ids": [],
            "join_basis": "",
            "confirmed_by": "",
        },
        "experiment_row": {
            "row_id": "",
            "selection_basis": "",
            "selected_by": "",
        },
        "profile": {
            "kind_word": "",
            "designation": "",
            "mark": "",
            "standard": "",
            "rotation_degrees": "",
            "plane": "",
            "scheme_flag": "",
            "rx3_template": "",
            "stress_state": None,
            "confirmed_by": "",
            "basis": "",
        },
        "design_conditions": {name: None for name in _DESIGN_CONDITION_KEYS},
    }


@dataclass(frozen=True, slots=True)
class BarChain:
    """One connected straight chain of finite elements."""

    ordered_element_ids: tuple[str, ...]
    element_lengths_m: Mapping[str, str]
    bar_length_m: Decimal
    start_node: str
    end_node: str


Vector3 = tuple[Decimal, Decimal, Decimal]


def _cross_and_dot(
    first: Vector3, second: Vector3
) -> tuple[Vector3, Decimal]:
    cross = (
        first[1] * second[2] - first[2] * second[1],
        first[2] * second[0] - first[0] * second[2],
        first[0] * second[1] - first[1] * second[0],
    )
    dot = first[0] * second[0] + first[1] * second[1] + first[2] * second[2]
    return cross, dot


def _build_bar_chain(
    assembled: Sequence[LiraAssembledElement],
    node_coordinates: Mapping[str, Vector3],
    declaration: BarExperimentDeclaration,
    *,
    context: str,
) -> BarChain:
    """Prove that the declared elements form one connected straight chain."""

    by_id = {item.element_id: item for item in assembled}
    for element_id in declaration.element_ids:
        element = by_id.get(element_id)
        if element is None:
            raise LiraMappingError(
                f"{context}: bar element {element_id!r} does not exist in the model"
            )
        if element.blockers:
            raise LiraMappingError(
                f"{context}: bar element {element_id!r} is blocked: "
                f"{'; '.join(element.blockers)}"
            )
        if len(element.node_ids) != 2:
            raise LiraMappingError(
                f"{context}: bar element {element_id!r} is not a two-node bar"
            )
        if element.length_m is None:
            raise LiraMappingError(
                f"{context}: bar element {element_id!r} has no computed length"
            )
        for node_id in element.node_ids:
            if node_id not in node_coordinates:
                raise LiraMappingError(
                    f"{context}: bar node {node_id!r} has no coordinates in the "
                    "re-read node table"
                )
    rotations = {
        by_id[element_id].rotation_angle_degrees
        for element_id in declaration.element_ids
    }
    if rotations != {Decimal("0")}:
        raise LiraMappingError(
            f"{context}: the bar chain requires zero local-axis rotation on "
            f"every element, found {sorted(str(value) for value in rotations)}"
        )
    if any(
        by_id[element_id].rotation_angle_degrees
        != declaration.profile.rotation_degrees
        for element_id in declaration.element_ids
    ):
        raise LiraMappingError(
            f"{context}: declared rotation {declaration.profile.rotation_degrees} "
            "differs from the elements"
        )
    profiles = {
        (item.mark, item.designation, item.kind_word)
        for item in (by_id[element_id] for element_id in declaration.element_ids)
    }
    if len(profiles) != 1:
        raise LiraMappingError(
            f"{context}: bar elements carry differing profiles: "
            f"{sorted(str(item) for item in profiles)}"
        )
    (mark, designation, kind_word) = next(iter(profiles))
    declared_profile = (
        declaration.profile.mark,
        declaration.profile.designation,
        declaration.profile.kind_word,
    )
    if (mark, designation, kind_word) != declared_profile:
        raise LiraMappingError(
            f"{context}: declared profile {declared_profile!r} does not match "
            f"the bar elements {(mark, designation, kind_word)!r}"
        )

    adjacency: dict[str, list[str]] = {}
    for element_id in declaration.element_ids:
        for node_id in by_id[element_id].node_ids:
            adjacency.setdefault(node_id, []).append(element_id)
    for node_id, owners in adjacency.items():
        if len(owners) > 2:
            raise LiraMappingError(
                f"{context}: node {node_id!r} belongs to {len(owners)} elements; "
                "the bar chain must not branch"
            )
    start_nodes = {
        node for node in declaration.end_node_ids if len(adjacency.get(node, ())) == 1
    }
    if start_nodes != set(declaration.end_node_ids):
        raise LiraMappingError(
            f"{context}: the declared end nodes {list(declaration.end_node_ids)} "
            "do not both terminate the chain"
        )
    start = declaration.end_node_ids[0]
    end = declaration.end_node_ids[1]
    current = start
    used: set[str] = set()
    ordered: list[LiraAssembledElement] = []
    while True:
        candidates = [eid for eid in adjacency.get(current, ()) if eid not in used]
        if not candidates:
            break
        if len(candidates) > 1:
            raise LiraMappingError(
                f"{context}: node {current!r} branches the chain"
            )
        element_id = candidates[0]
        element = by_id[element_id]
        other = (
            element.node_ids[1]
            if element.node_ids[0] == current
            else element.node_ids[0]
        )
        used.add(element_id)
        ordered.append(element)
        current = other
    if len(used) != len(declaration.element_ids):
        raise LiraMappingError(
            f"{context}: the declared elements are not one connected chain"
        )
    if current != end:
        raise LiraMappingError(
            f"{context}: the chain runs from node {start!r} to node {current!r}, "
            f"not to the declared end node {end!r}"
        )
    for node_id, owners in adjacency.items():
        if node_id in {start, end}:
            continue
        if len(owners) != 2:
            raise LiraMappingError(
                f"{context}: interior node {node_id!r} is not shared by exactly "
                "two elements"
            )
    # Straightness: consecutive element direction vectors (ordered along the
    # traversal) must be parallel and point the same way.
    directions: list[Vector3] = []
    current = start
    for element in ordered:
        other = (
            element.node_ids[1]
            if element.node_ids[0] == current
            else element.node_ids[0]
        )
        start_xyz = node_coordinates[current]
        end_xyz = node_coordinates[other]
        directions.append(
            (
                end_xyz[0] - start_xyz[0],
                end_xyz[1] - start_xyz[1],
                end_xyz[2] - start_xyz[2],
            )
        )
        current = other
    for first, second in zip(directions, directions[1:]):
        cross, dot = _cross_and_dot(first, second)
        first_norm_sq = first[0] ** 2 + first[1] ** 2 + first[2] ** 2
        second_norm_sq = second[0] ** 2 + second[1] ** 2 + second[2] ** 2
        cross_sq = cross[0] ** 2 + cross[1] ** 2 + cross[2] ** 2
        tolerance_sq = (
            _COLLINEAR_TOLERANCE
            * _COLLINEAR_TOLERANCE
            * first_norm_sq
            * second_norm_sq
        )
        if cross_sq > tolerance_sq or dot <= 0:
            raise LiraMappingError(
                f"{context}: the bar elements do not form one straight chain"
            )
    ordered_ids = tuple(element.element_id for element in ordered)
    element_lengths: dict[str, str] = {}
    bar_length = Decimal("0")
    for element in ordered:
        assert element.length_m is not None
        element_lengths[element.element_id] = str(element.length_m)
        bar_length += element.length_m
    return BarChain(
        ordered_element_ids=ordered_ids,
        element_lengths_m=element_lengths,
        bar_length_m=bar_length,
        start_node=start,
        end_node=end,
    )


def _read_node_coordinates(
    model_manifest: Mapping[str, Any], *, context: str
) -> dict[str, Vector3]:
    """Re-read the node table with the recorded settings for the chain check."""

    sources_block = require_mapping(
        model_manifest.get("sources"), "sources", context
    )
    entry = require_mapping(sources_block.get("nodes"), "nodes", context)
    path_token = entry.get("path")
    sheet_token = entry.get("sheet")
    header_token = entry.get("header_row")
    if not isinstance(path_token, str) or not path_token.strip():
        raise LiraFormatError(f"{context}: node source has no usable path")
    if not isinstance(sheet_token, str) or not sheet_token:
        raise LiraFormatError(
            f"{context}: node source has no usable sheet setting; refusing to guess"
        )
    if isinstance(header_token, int) and not isinstance(header_token, bool):
        header = header_token
    elif isinstance(header_token, str) and header_token.strip().isdigit():
        header = int(header_token.strip())
    else:
        header = 0
    if header < 1:
        raise LiraFormatError(
            f"{context}: node source has no usable header_row; refusing to guess"
        )
    nodes = read_node_table(
        Path(path_token), sheet_name=sheet_token, header_row=header
    )
    coordinates: dict[str, Vector3] = {}
    for node in nodes:
        if node.x is None or node.y is None or node.z is None:
            continue
        coordinates[node.node_id] = (node.x, node.y, node.z)
    return coordinates


def _component_analysis(
    row: RsuEvidenceRow,
    declaration: BarExperimentDeclaration,
    bar_length_m: Decimal,
) -> tuple[list[dict[str, object]], list[str]]:
    """Keep the full signed vector and mark each component against the scope."""

    registry = LiraRx3ConventionRegistry.validated_22p_one_plane_xx()
    entries: list[dict[str, object]] = []
    blockers: list[str] = []
    for name in RSU_COMPONENTS:
        value = row.published_vector[name]
        convention = registry.components[name]
        resolved = convention.resolved_for(
            profile_standard=declaration.profile.standard,
            profile_name=declaration.profile.designation,
            rx3_template=declaration.profile.rx3_template,
            stress_state=declaration.profile.stress_state,
            member_length_m=bar_length_m,
            member_rotation_degrees=declaration.profile.rotation_degrees,
        )
        if value.value != 0 and not resolved:
            blockers.append(f"LIRA_EXPERIMENT_UNSUPPORTED_COMPONENT:{name}")
        review_value, review_unit = to_review_unit(name, value.value, value.unit)
        entries.append(
            {
                "component": name,
                "source_value": str(value.value),
                "source_unit": value.unit,
                "review_value": str(review_value),
                "review_unit": review_unit,
                "source_cell": value.cell,
                "source_sheet": value.sheet,
                "source_row": value.row,
                "source_sha256": value.source_sha256,
                "convention": {
                    "resolved": resolved,
                    "target": (
                        None
                        if convention.target_rx3_component is None
                        else convention.target_rx3_component.value
                    ),
                    "value_transform": (
                        None
                        if convention.value_transform is None
                        else convention.value_transform.value
                    ),
                    "verification_status": convention.verification_status.value,
                },
            }
        )
    return entries, blockers


def _card_text(
    *,
    declaration: BarExperimentDeclaration,
    chain: BarChain,
    row: RsuEvidenceRow,
    components: Sequence[Mapping[str, object]],
    blockers: Sequence[str],
    missing_confirmations: Sequence[str],
) -> str:
    lines = [
        "# Карточка технического опыта ЛИРА→RX3",
        "",
        f"- Физическая балка: **{declaration.bar_id}**",
        f"- Конечные элементы: {', '.join(chain.ordered_element_ids)} "
        f"(длины: {', '.join(f'{eid} = {chain.element_lengths_m[eid]} м' for eid in chain.ordered_element_ids)})",
        f"- Длина физического стержня (геометрическая): {chain.bar_length_m} м",
        f"- Концевые узлы: {chain.start_node} → {chain.end_node}",
        f"- Основание объединения КЭ в стержень: {declaration.join_basis}",
        f"- Подтвердил: {declaration.bar_confirmed_by}",
        "",
        "## Выбранная строка технического опыта",
        "",
        f"- row_id: **{row.row_id}** (элемент {row.element_id}, сечение {row.section_station})",
        f"- Группа {row.rsu_group}, критерий {row.rsu_criterion}, столбец {row.rsu_column_number}",
        f"- Состав загружений: {' '.join(row.load_case_membership)}",
        f"- Основание выбора: {declaration.selection_basis} ({declaration.selected_by})",
        "- Это строка технического опыта, **не** определяющая строка: "
        "governing-выбор не выполняется.",
        "",
        "## Усилия (полный подписанный вектор)",
        "",
        "| Компонент | Источник | Обзорное значение | Конвенция |",
        "|---|---|---|---|",
    ]
    for item in components:
        convention = item["convention"]
        assert isinstance(convention, Mapping)
        convention_text = (
            "нет"
            if not convention.get("resolved")
            else f"{convention.get('target')} / {convention.get('value_transform')}"
        )
        lines.append(
            f"| {item['component']} | {item['source_value']} {item['source_unit']} "
            f"({item['source_cell']}) | {item['review_value']} {item['review_unit']} "
            f"| {convention_text} |"
        )
    lines.extend(
        [
            "",
            "## Проверенные условия",
            "",
            "- цепочка КЭ связная, прямая, без ветвлений;",
            "- поворот местных осей 0° на всех КЭ и совпадает с декларацией;",
            "- профиль (kind/designation/mark) одинаков на всех КЭ и совпадает с декларацией;",
            "- выбранная строка принадлежит заявленному стержню;",
            "- связанный пакет повторно проверен по исходным таблицам.",
            "",
            "Совпадение профиля и длины **не подтверждает** всю совместимость с RX3.",
            "",
            "## Недостающие подтверждения",
            "",
        ]
    )
    if missing_confirmations:
        for missing in missing_confirmations:
            lines.append(f"- {missing}")
    else:
        lines.append("- отсутствуют")
    lines.extend(
        [
            "",
            "## Блокировки передачи",
            "",
        ]
    )
    if blockers:
        for blocker in blockers:
            lines.append(f"- {blocker}")
    else:
        lines.append("- отсутствуют")
    lines.extend(
        [
            "",
            "**RX38 не создан. Выпуск запрещён.** Пакет — только входные данные "
            "контролируемого опыта, не разрешение на расчёт RX3.",
            "",
        ]
    )
    return "\n".join(lines) + "\n"


def prepare_bar_experiment_input(
    *,
    linked_dir: str | Path,
    declaration_path: str | Path,
    output_dir: str | Path,
) -> dict[str, object]:
    """Build the experiment input package; never write RX38."""

    linked = Path(linked_dir).resolve(strict=True)
    declaration_file = Path(declaration_path).resolve(strict=True)
    destination = Path(output_dir).resolve(strict=False)
    if destination in (linked, declaration_file):
        raise LiraFormatError("output directory must differ from both inputs")
    if destination.exists():
        raise LiraFormatError(
            f"experiment input directory already exists; refusing to overwrite: "
            f"{destination}"
        )
    declaration = read_bar_declaration(declaration_file)
    linked_manifest = read_json_object(
        linked / "manifest.json", "linked package manifest"
    )
    if linked_manifest.get("status") != "MODEL_RSU_LINKED":
        raise LiraMappingError(
            f"{linked}: manifest status must be MODEL_RSU_LINKED"
        )
    actual_linked_sha = sha256_file(linked / "manifest.json")
    if declaration.linked_manifest_sha256 != actual_linked_sha:
        raise LiraMappingError(
            f"{declaration_file}: linked_manifest_sha256 does not match "
            f"{linked / 'manifest.json'}; the declaration is bound to a "
            "different evidence revision"
        )
    basis = require_mapping(
        linked_manifest.get("link_basis"), "link_basis", str(linked)
    )
    model_package_block = require_mapping(
        basis.get("model_package"), "model_package", str(linked)
    )
    evidence_block = require_mapping(
        basis.get("rsu_evidence"), "rsu_evidence", str(linked)
    )
    model_path = Path(require_text(model_package_block, "path", str(linked)))
    evidence_path = Path(require_text(evidence_block, "path", str(linked)))
    recorded_model_files = require_mapping(
        model_package_block.get("files"), "files", str(linked)
    )
    for name in ("manifest.json", "elements.json"):
        recorded = recorded_model_files.get(name)
        actual = sha256_file(model_path / name)
        if recorded != actual:
            raise LiraMappingError(
                f"{linked}: linked package no longer matches its recorded "
                f"model package {name}"
            )

    _, _, model_manifest, _, assembled = read_model_package(model_path)
    bundle = read_rsu_evidence(evidence_path)
    # Re-verify every evidence row against the model package elements.
    link_rsu_rows_to_elements(
        tuple(item.as_dict() for item in assembled), bundle
    )
    node_coordinates = _read_node_coordinates(
        model_manifest, context=str(linked)
    )
    chain = _build_bar_chain(
        assembled, node_coordinates, declaration, context=str(linked)
    )
    row_matches = [row for row in bundle.rows if row.row_id == declaration.row_id]
    if not row_matches:
        raise LiraMappingError(
            f"{linked}: selected row {declaration.row_id!r} does not exist in "
            "the evidence"
        )
    row = row_matches[0]
    if row.element_id not in declaration.element_ids:
        raise LiraMappingError(
            f"{linked}: selected row {declaration.row_id!r} belongs to element "
            f"{row.element_id!r}, which is not part of the declared bar "
            f"{declaration.bar_id!r}"
        )

    components, blockers = _component_analysis(
        row, declaration, chain.bar_length_m
    )
    missing_confirmations: list[str] = list(
        declaration.design_conditions.missing()
    )
    if declaration.profile.stress_state is None:
        missing_confirmations.append("stress_state")
    status = STATUS_READY if not blockers else STATUS_BLOCKED

    destination.mkdir(parents=True, exist_ok=False)
    paths = {name: destination / name for name in _LINK_FILES}
    selected_row_summary = {
        "row_id": row.row_id,
        "element_id": row.element_id,
        "section_station": row.section_station,
        "rsu_group": row.rsu_group,
        "rsu_criterion": row.rsu_criterion,
        "rsu_column_number": row.rsu_column_number,
        "load_case_membership": list(row.load_case_membership),
        "selection_basis": declaration.selection_basis,
        "selected_by": declaration.selected_by,
    }
    manifest: dict[str, object] = {
        "kind": PACKAGE_KIND,
        "status": status,
        "bar": {
            "bar_id": declaration.bar_id,
            "element_ids": list(declaration.element_ids),
            "ordered_element_ids": list(chain.ordered_element_ids),
            "end_node_ids": [chain.start_node, chain.end_node],
            "element_lengths_m": dict(chain.element_lengths_m),
            "bar_length_m": str(chain.bar_length_m),
            "join_basis": declaration.join_basis,
            "confirmed_by": declaration.bar_confirmed_by,
        },
        "selected_row": selected_row_summary,
        "profile": declaration.profile.as_dict(),
        "design_conditions": declaration.design_conditions.as_dict(),
        "checks": {
            "linked_manifest_bound": True,
            "model_package_matches_linked": True,
            "sources_reverified": True,
            "chain_connected": True,
            "chain_straight": True,
            "chain_without_branching": True,
            "rotation_consistent_zero": True,
            "profile_consistent": True,
            "selected_row_in_bar": True,
        },
        "components": components,
        "blockers": blockers,
        "transfer_blocked": bool(blockers),
        "missing_confirmations": missing_confirmations,
        "governing_result_selection": None,
        "rx38_created": False,
        "release_forbidden": True,
        "issue_readiness": "NOT_READY_FOR_ISSUE",
        "linked": {
            "manifest_sha256": actual_linked_sha,
            "evidence_sha256": bundle.sha256,
            "model_manifest_sha256": sha256_file(model_path / "manifest.json"),
        },
        "written_files": [str(paths[name]) for name in _LINK_FILES],
    }
    _write_json(paths["manifest.json"], manifest)
    assembled_by_id = {item.element_id: item for item in assembled}
    _write_json(
        paths["experiment_input.json"],
        {
            "kind": PACKAGE_KIND,
            "status": status,
            "bar": {
                "bar_id": declaration.bar_id,
                "elements": [
                    assembled_by_id[element_id].as_dict()
                    for element_id in chain.ordered_element_ids
                ],
                "end_node_ids": [chain.start_node, chain.end_node],
                "element_lengths_m": dict(chain.element_lengths_m),
                "bar_length_m": str(chain.bar_length_m),
                "join_basis": declaration.join_basis,
                "confirmed_by": declaration.bar_confirmed_by,
            },
            "selected_row": {
                **selected_row_summary,
                "row": row.as_dict(),
            },
            "profile": declaration.profile.as_dict(),
            "design_conditions": declaration.design_conditions.as_dict(),
            "components": components,
            "blockers": blockers,
            "transfer_blocked": bool(blockers),
            "missing_confirmations": missing_confirmations,
            "governing_result_selection": None,
            "rx38_created": False,
            "release_forbidden": True,
            "issue_readiness": "NOT_READY_FOR_ISSUE",
        },
    )
    _write_new(
        paths["experiment_card.md"],
        _card_text(
            declaration=declaration,
            chain=chain,
            row=row,
            components=components,
            blockers=blockers,
            missing_confirmations=missing_confirmations,
        ),
    )
    _write_json(paths["declaration_template.json"], declaration_template())
    return manifest
