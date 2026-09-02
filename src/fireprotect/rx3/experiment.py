"""Preparation artifacts for controlled RX3 GUI experiments."""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal, InvalidOperation
from hashlib import sha256
import json
from pathlib import Path
import re
import shutil
from typing import Any, Iterable, Mapping

from ..project_io import project_element_from_dict, project_element_to_dict
from .diff import diff_records
from .parser import (
    Rx38Document,
    Rx38Record,
    construction_records,
    read_rx38,
    read_rx38_document,
)
from .profiles import ProfileCandidate, ProfileRepository
from .safety import rx38_record_fingerprint
from .schema import WritePolicy, field_spec


class Rx3ExperimentPreparationError(ValueError):
    """Raised when a fail-closed Phase A bundle cannot be prepared."""


@dataclass(frozen=True, slots=True)
class Rx3TemplateCandidate:
    path: Path
    record: Rx38Record
    record_count: int
    accepted: bool
    reasons: tuple[str, ...]
    profile_table: str | None
    profile_database_match: bool
    axial_candidates_in_file: int = 0

    @property
    def rank_key(self) -> tuple[int, int, int, int, str, int]:
        quantity_is_one = self.record.fields[15].strip().replace(",", ".") == "1"
        return (
            0 if self.accepted else 1,
            self.axial_candidates_in_file,
            self.record_count,
            0 if quantity_is_one else 1,
            self.path.name.casefold(),
            self.record.line_number or 0,
        )


@dataclass(frozen=True, slots=True)
class Rx3PhaseABundle:
    directory: Path
    template: Path
    template_summary_json: Path
    template_summary_markdown: Path
    heating_evidence_template: Path
    checklist: Path
    selection_report: Path
    project_element_draft: Path
    selected_source: Path
    selected_mark: str


@dataclass(frozen=True, slots=True)
class Rx3BendingReportReference:
    source_file: Path
    mark: str
    mx_knm: Decimal
    q_kn: Decimal
    my_knm: Decimal | None
    evidence_reference: str


@dataclass(frozen=True, slots=True)
class Rx3BendingTemplateCandidate:
    path: Path
    record: Rx38Record
    record_count: int
    accepted: bool
    reasons: tuple[str, ...]
    profile_table: str | None
    profile_database_match: bool
    report_reference: Rx3BendingReportReference | None
    section_simplicity_rank: int

    @property
    def q_to_mx_ratio(self) -> Decimal | None:
        reference = self.report_reference
        if reference is None or reference.mx_knm == 0:
            return None
        return abs(reference.q_kn / reference.mx_knm)

    @property
    def rank_key(self) -> tuple[int, int, int, Decimal, int, str, int]:
        ratio = self.q_to_mx_ratio
        return (
            0 if self.accepted else 1,
            self.section_simplicity_rank,
            0 if not self.record.fields[48].strip() else 1,
            ratio if ratio is not None else Decimal("Infinity"),
            self.record_count,
            self.path.name.casefold(),
            self.record.line_number or 0,
        )


@dataclass(frozen=True, slots=True)
class Rx3BendingPhaseABundle:
    directory: Path
    template: Path
    template_summary_json: Path
    selection_report: Path
    expected_report_values: Path
    checklist: Path
    gui_instructions: Path
    report_references: Path
    selected_source: Path
    selected_mark: str


@dataclass(frozen=True, slots=True)
class Rx3BendingMxValidationBundle:
    directory: Path
    template: Path
    generated: Path
    project_element: Path
    template_profile: Path
    heating_evidence: Path
    diff_json: Path
    diff_markdown: Path
    expected_gui: Path
    checklist: Path
    instructions: Path
    audit: Path
    generated_sha256: str


def _decimal(value: str) -> Decimal | None:
    try:
        parsed = Decimal(value.strip().replace(",", "."))
    except InvalidOperation:
        return None
    return parsed if parsed.is_finite() else None


def _sha256(path: Path) -> str:
    digest = sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _write_new(path: Path, content: str) -> None:
    try:
        with path.open("x", encoding="utf-8", newline="\n") as stream:
            stream.write(content)
    except FileExistsError as exc:
        raise Rx3ExperimentPreparationError(
            f"Refusing to overwrite Phase A artifact: {path}"
        ) from exc


def _copy_new(source: Path, destination: Path) -> None:
    try:
        with source.open("rb") as input_stream, destination.open("xb") as output_stream:
            shutil.copyfileobj(input_stream, output_stream)
    except FileExistsError as exc:
        raise Rx3ExperimentPreparationError(
            f"Refusing to overwrite Phase A artifact: {destination}"
        ) from exc


def _normal_text(value: str) -> str:
    return " ".join(value.casefold().replace("c", "с").split())


def _normal_axis(value: str) -> str:
    return " ".join(value.upper().replace("Х", "X").split())


def _pure_axial_label(value: str) -> bool:
    normalized = _normal_text(value)
    return (
        "сжатый стержень" in normalized
        and "изгиб" not in normalized
        and "растянут" not in normalized
    )


def _profile_geometry_matches(record: Rx38Record, profile: ProfileCandidate) -> bool:
    pairs = (
        (8, profile.geometry.height_mm),
        (9, profile.geometry.width_mm),
        (11, profile.geometry.web_thickness_mm),
        (13, profile.geometry.flange_thickness_mm),
    )
    for index, expected in pairs:
        actual = _decimal(record.fields[index])
        if actual is None or expected is None or abs(actual - expected) > Decimal("0.001"):
            return False
    area = _decimal(record.fields[20])
    return (
        area is not None
        and profile.geometry.area_cm2 is not None
        and abs(area - profile.geometry.area_cm2 * Decimal("100"))
        <= Decimal("0.001")
    )


def _steel_strength_aligned(record: Rx38Record) -> bool:
    match = re.search(r"(\d+(?:[.,]\d+)?)\s*$", record.fields[42].strip())
    stored = _decimal(record.fields[33])
    return bool(match and stored is not None and _decimal(match.group(1)) == stored)


def _evaluate_candidate(
    path: Path,
    record: Rx38Record,
    record_count: int,
    profiles: ProfileRepository,
) -> Rx3TemplateCandidate:
    accepted_reasons: list[str] = []
    rejected_reasons: list[str] = []

    if _pure_axial_label(record.fields[45]):
        accepted_reasons.append("stress state explicitly describes a compressed member")
    else:
        rejected_reasons.append("stress state is not an explicit pure-compression label")

    field50 = _decimal(record.fields[50])
    if field50 == 0:
        accepted_reasons.append("probable field 50 is exactly zero")
    else:
        rejected_reasons.append("probable field 50 is non-zero or non-numeric")

    axial_force = _decimal(record.fields[49])
    if axial_force is not None and axial_force != 0:
        accepted_reasons.append("N is a finite non-zero template value")
    else:
        rejected_reasons.append("N is zero or non-numeric")

    numeric_requirements = {
        14: "length",
        51: "effective length",
        55: "required R",
        141: "effective-length factor",
    }
    for index, name in numeric_requirements.items():
        value = _decimal(record.fields[index])
        if value is None or value <= 0:
            rejected_reasons.append(f"{name} is missing, non-numeric, or non-positive")
    quantity = _decimal(record.fields[15])
    if (
        quantity is None
        or quantity <= 0
        or quantity != quantity.to_integral_value()
    ):
        rejected_reasons.append("quantity is missing, non-positive, or non-integral")
    if not record.fields[48].strip():
        rejected_reasons.append("support condition is empty")
    if not record.fields[104].strip():
        rejected_reasons.append("fire regime is empty")

    if _steel_strength_aligned(record):
        accepted_reasons.append("steel grade suffix and field 33 are numerically aligned")
    else:
        rejected_reasons.append("steel grade and field 33 are not demonstrably aligned")

    for index, name in ((44, "field 44"), (54, "field 54")):
        if _decimal(record.fields[index]) is None:
            rejected_reasons.append(f"{name} has no existing numeric stale result")

    profile_result = profiles.search(record.fields[19], record.fields[17])
    profile = profile_result.candidate
    geometry_matches = profile is not None and _profile_geometry_matches(record, profile)
    if profile_result.status == "FOUND" and geometry_matches:
        accepted_reasons.append("profile and core geometry match exactly one rx3.rxdb row")
    elif profile_result.status != "FOUND":
        rejected_reasons.append(
            f"profile lookup in rx3.rxdb is {profile_result.status}"
        )
    else:
        rejected_reasons.append("profile name resolves but core geometry does not match rx3.rxdb")

    return Rx3TemplateCandidate(
        path=path,
        record=record,
        record_count=record_count,
        accepted=not rejected_reasons,
        reasons=tuple(accepted_reasons + rejected_reasons),
        profile_table=profile.table if profile is not None else None,
        profile_database_match=bool(geometry_matches),
    )


def rank_rx3_template_candidates(
    template_paths: Iterable[str | Path], db_path: str | Path
) -> tuple[Rx3TemplateCandidate, ...]:
    """Rank every Tconstr without interpreting unknown RX38 positions."""

    profiles = ProfileRepository(Path(db_path).resolve(strict=True))
    candidates: list[Rx3TemplateCandidate] = []
    for supplied in template_paths:
        path = Path(supplied).resolve(strict=True)
        records = construction_records(read_rx38(path))
        candidates.extend(
            _evaluate_candidate(path, record, len(records), profiles)
            for record in records
        )
    accepted_counts = Counter(
        candidate.path for candidate in candidates if candidate.accepted
    )
    ranked = [
        Rx3TemplateCandidate(
            candidate.path,
            candidate.record,
            candidate.record_count,
            candidate.accepted,
            candidate.reasons,
            candidate.profile_table,
            candidate.profile_database_match,
            accepted_counts[candidate.path],
        )
        for candidate in candidates
    ]
    return tuple(sorted(ranked, key=lambda item: item.rank_key))


def _source(path: Path, record: Rx38Record, index: int) -> dict[str, Any]:
    return {
        "file": str(path),
        "line": record.line_number,
        "record": "Tconstr",
        "field_index": index,
    }


def _value(path: Path, record: Rx38Record, index: int, *, status: str = "CONFIRMED") -> dict[str, Any]:
    return {
        "value": record.fields[index],
        "status": status,
        "source": _source(path, record, index),
    }


def _project_payload(
    experiment_id: str,
    element_id: str,
    heating_sides: int,
    source: Path,
    record: Rx38Record,
) -> dict[str, Any]:
    def q(value: str, unit: str) -> dict[str, str]:
        return {"value": value.replace(",", "."), "unit": unit}

    effective = q(record.fields[51], "m")
    factor = record.fields[141].replace(",", ".")
    payload: dict[str, Any] = {
        "project_id": experiment_id,
        "element_id": element_id,
        "mark": record.fields[1],
        "element_type": "column",
        "source_file": str(source),
        "source_type": "RX38_CONTROLLED_TEMPLATE",
        "source_element_id": record.fields[1],
        "source_row": record.line_number,
        "timestamp": datetime.now().astimezone().isoformat(),
        "section_type": record.fields[5],
        "profile_standard": record.fields[17],
        "profile_name": record.fields[19],
        "area": q(record.fields[20], "mm2"),
        "full_perimeter": None,
        "heated_perimeter": q(record.fields[21], "mm"),
        "ptm": q(record.fields[22], "mm"),
        "length": q(record.fields[14], "m"),
        "quantity": int(Decimal(record.fields[15].replace(",", "."))),
        "steel_grade": record.fields[42],
        "Ry": q(record.fields[33], "MPa"),
        "E": q(record.fields[34], "MPa"),
        "density": q(record.fields[32], "kg/m3"),
        "load_case": experiment_id,
        "combination": experiment_id,
        "N": q(record.fields[49], "kN"),
        "Mx": q("0", "kN*m"),
        "My": q("0", "kN*m"),
        "Qx": q("0", "kN"),
        "Qy": q("0", "kN"),
        "governing_combination": experiment_id,
        "required_fire_resistance": q(record.fields[55], "min"),
        "stress_state": record.fields[45],
        "heating_sides": heating_sides,
        "support_condition": record.fields[48],
        "effective_length_parameters": {
            "buckling_length_x": effective,
            "buckling_length_y": effective,
            "factor_x": factor,
            "factor_y": factor,
        },
        "critical_temperature": None,
        "unprotected_fire_resistance": None,
        "material_id": None,
        "coating_type": None,
        "required_thickness": None,
        "specific_consumption": None,
        "protected_area": q(record.fields[25], "m2"),
        "total_consumption": None,
    }
    untraced = {
        "source_file", "source_type", "source_element_id", "source_row", "timestamp"
    }
    field_indices = {
        "section_type": 5, "profile_standard": 17, "profile_name": 19,
        "area": 20, "heated_perimeter": 21, "ptm": 22, "length": 14,
        "quantity": 15, "steel_grade": 42, "E": 34,
        "density": 32, "N": 49, "required_fire_resistance": 55,
        "stress_state": 45, "support_condition": 48,
        "effective_length_parameters": 51, "protected_area": 25,
    }
    experiment_fields = {
        "project_id", "element_id", "mark", "element_type", "load_case",
        "combination", "Mx", "My", "Qx", "Qy", "governing_combination",
        "heating_sides",
    }
    provenance: dict[str, Any] = {}
    for name, value in payload.items():
        if value is None or name in untraced or name == "provenance":
            continue
        if name in field_indices:
            index = field_indices[name]
            provenance[name] = {
                "kind": "SOURCE",
                "file": str(source),
                "row": record.line_number,
                "field": f"Tconstr[{index}]",
            }
        elif name == "Ry":
            provenance[name] = {
                "kind": "ENGINEER_INPUT",
                "file": f"{experiment_id} controlled experiment protocol",
                "field": (
                    "provisional Ry value; no semantic equivalence with "
                    "RX38 Tconstr[33] is claimed"
                ),
            }
        elif name in experiment_fields:
            provenance[name] = {
                "kind": "ENGINEER_INPUT",
                "file": f"{experiment_id} controlled experiment protocol",
                "field": name,
            }
    payload["provenance"] = provenance
    return project_element_to_dict(project_element_from_dict(payload))


def _escape(value: object) -> str:
    return str(value).replace("|", "\\|").replace("\n", " ")


def _selection_markdown(
    ranked: tuple[Rx3TemplateCandidate, ...], selected: Rx3TemplateCandidate
) -> str:
    lines = [
        "# RX3-EXP-01 — выбор шаблона",
        "",
        "Ранжирование работает fail-closed и не назначает смысл неизвестным индексам RX38.",
        "Метка чистого сжатия вместе с probable field 50 = 0 создаёт только кандидата Phase A; наблюдение в GUI обязательно.",
        "",
        "| Ранг | Файл-кандидат | Марка | Профиль | Сталь / field33 | Напряжённое состояние | N | field50 | Закрепление | Длина | R | Режим пожара | Решение | Причины |",
        "|---:|---|---|---|---|---|---:|---:|---|---:|---:|---|---|---|",
    ]
    for rank, candidate in enumerate(ranked, 1):
        r = candidate.record
        decision = "SELECTED" if candidate is selected else "ACCEPTED" if candidate.accepted else "REJECTED"
        lines.append(
            "| " + " | ".join(
                _escape(item) for item in (
                    rank, candidate.path, r.fields[1], r.fields[19],
                    f"{r.fields[42]} / {r.fields[33]}", r.fields[45], r.fields[49],
                    r.fields[50], r.fields[48], r.fields[14], r.fields[55],
                    r.fields[104], decision, "; ".join(candidate.reasons),
                )
            ) + " |"
        )
    lines.extend([
        "",
        "Порядок разрешения равенства: прохождение gates, меньше axial-кандидатов в файле, меньше конструкций всего, quantity = 1, стабильный порядок имени файла/строки.",
        "",
    ])
    return "\n".join(lines)


def _summary_markdown(summary: dict[str, Any]) -> str:
    values = summary["expected_rx3_values"]
    lines = [
        "# RX3-EXP-01 — сводка шаблона Phase A",
        "",
        f"Выбранный исходник: `{summary['template']['source_file']}`",
        f"Выбранная марка: `{summary['selection']['mark']}`",
        f"SHA-256 шаблона: `{summary['template']['sha256']}`",
        f"SHA-256 выбранной Tconstr: `{summary['selection']['template_record_sha256']}`",
        "",
        "Поля 44 и 54 — существующие результаты шаблона со статусом STALE. Это не результаты нового расчёта.",
        "Ни один индекс RX38 не назначен для Mx, My, Qx, Qy или схемы обогрева.",
        "Численное совпадение Tconstr[33] с показанными Ryn/Ry не доказывает семантическую эквивалентность field33 == Ry.",
        "",
        "| Параметр | Ожидается | Статус | Источник |",
        "|---|---|---|---|",
    ]
    for name, item in values.items():
        source = item["source"]
        source_text = source if isinstance(source, str) else f"{source['file']} line {source['line']} field {source['field_index']}"
        lines.append(
            f"| {_escape(name)} | `{_escape(item['value'])}` | {_escape(item['status'])} | {_escape(source_text)} |"
        )
    lines.append("")
    return "\n".join(lines)


def _checklist(summary: dict[str, Any]) -> str:
    values = summary["expected_rx3_values"]
    order = (
        "mark", "profile", "section_type", "steel_grade", "field33_stored_strength",
        "N", "probable_field_50", "stress_state", "support_condition",
        "length", "effective_length", "effective_length_factor", "heating_exposure",
        "heated_perimeter_if_displayed", "fire_regime", "required_R",
        "critical_temperature_stale", "unprotected_fire_resistance_stale",
        "critical_temperature_calculation_mode", "steel_temperature_model",
    )
    lines = [
        "# RX3-EXP-01 CHECKLIST A — TEMPLATE OBSERVATION",
        "",
        "Откройте только `template.rx38` и выберите указанную ниже марку. Ничего не изменяйте и не запускайте расчёт.",
        "Для каждой строки запишите точный текст/значение из GUI. Любое несовпадение означает STOP.",
        "",
        "| ПАРАМЕТР | ОЖИДАЕТСЯ | НАБЛЮДАЕТСЯ В RX3 | СОВПАЛО |",
        "|---|---|---|:---:|",
    ]
    for name in order:
        lines.append(f"| {_escape(name)} | `{_escape(values[name]['value'])}` |  | [ ] |")
    lines.extend([
        "",
        "## Подтверждение инженера",
        "",
        "- [ ] Показан диалог осевого элемента; состояние — сжатый стержень.",
        "- [ ] Mx/My/Qx/Qy не трактуются как наблюдаемые GUI-поля и их RX38 mappings не выводятся из этого опыта.",
        f"- [ ] Показанная схема обогрева соответствует `{values['heating_exposure']['value']}`.",
        "- [ ] Критическая температура и огнестойкость без защиты распознаны только как существующие значения шаблона.",
        "- [ ] Ни одно значение не изменялось; Calculate/Recalculate не запускался.",
        "",
        "Инженер: ____________________    Дата: ____________________",
        "",
        "При любом несовпадении остановитесь. Не переводите evidence в VERIFIED и не готовьте Phase B.",
        "",
    ])
    return "\n".join(lines)


def prepare_rx3_experiment_phase_a(
    template_paths: Iterable[str | Path],
    db_path: str | Path,
    output_directory: str | Path,
    *,
    experiment_id: str = "RX3-EXP-01",
    project_element_id: str = "K1",
    heating_sides: int = 4,
) -> Rx3PhaseABundle:
    """Build a non-generating Phase A observation bundle."""

    if experiment_id != "RX3-EXP-01":
        raise Rx3ExperimentPreparationError(
            "Only the controlled pure-axial RX3-EXP-01 protocol is supported"
        )
    if isinstance(heating_sides, bool) or heating_sides not in {1, 2, 3, 4}:
        raise Rx3ExperimentPreparationError("heating_sides must be an integer from 1 to 4")
    paths = tuple(Path(path).resolve(strict=True) for path in template_paths)
    if not paths:
        raise Rx3ExperimentPreparationError("No RX38 template candidates were supplied")
    database = Path(db_path).resolve(strict=True)
    ranked = rank_rx3_template_candidates(paths, database)
    selected = next((candidate for candidate in ranked if candidate.accepted), None)
    if selected is None:
        raise Rx3ExperimentPreparationError("No candidate passed the Phase A selection gates")

    directory = Path(output_directory).resolve(strict=False)
    if directory.exists() and any(directory.iterdir()):
        raise Rx3ExperimentPreparationError(
            f"Phase A directory must be new or empty: {directory}"
        )
    directory.mkdir(parents=True, exist_ok=True)

    template = directory / "template.rx38"
    summary_json = directory / "template_summary.json"
    summary_md = directory / "template_summary.md"
    evidence_json = directory / "heating_evidence_template.json"
    checklist = directory / "CHECKLIST_A.md"
    selection_report = directory / "RX3_EXP_01_TEMPLATE_SELECTION.md"
    project_draft = directory / "project_element_draft.json"
    _copy_new(selected.path, template)
    if _sha256(template) != _sha256(selected.path):
        raise Rx3ExperimentPreparationError("Copied template is not byte-identical")

    record = selected.record
    fingerprint = rx38_record_fingerprint(record)
    protocol_source = f"{experiment_id} controlled ProjectElement input; no RX38 index mapping claimed"
    expected = {
        "mark": _value(selected.path, record, 1),
        "profile": _value(selected.path, record, 19),
        "section_type": _value(selected.path, record, 5),
        "steel_grade": _value(selected.path, record, 42),
        "field33_stored_strength": _value(selected.path, record, 33),
        "N": _value(selected.path, record, 49),
        "probable_field_50": _value(
            selected.path,
            record,
            50,
            status="PROBABLE_SEMANTICS_NOT_VERIFIED_BY_PHASE_A",
        ),
        "stress_state": _value(selected.path, record, 45),
        "support_condition": _value(selected.path, record, 48),
        "length": _value(selected.path, record, 14),
        "effective_length": _value(selected.path, record, 51),
        "effective_length_factor": _value(selected.path, record, 141),
        "heating_exposure": {"value": f"{heating_sides} heated sides", "status": "UNVERIFIED_GUI_CONFIRMATION_REQUIRED", "source": protocol_source},
        "heated_perimeter_if_displayed": _value(selected.path, record, 21),
        "fire_regime": _value(selected.path, record, 104),
        "required_R": _value(selected.path, record, 55),
        "critical_temperature_stale": _value(selected.path, record, 44, status="STALE_TEMPLATE_RESULT"),
        "unprotected_fire_resistance_stale": _value(selected.path, record, 54, status="STALE_TEMPLATE_RESULT"),
        "critical_temperature_calculation_mode": {"value": "OBSERVE_AND_RECORD_EXACT_GUI_VALUE", "status": "UNMAPPED_GUI_CONFIRMATION_REQUIRED", "source": "RX3-EXP-01 Phase A protocol"},
        "steel_temperature_model": _value(selected.path, record, 188),
    }
    summary: dict[str, Any] = {
        "experiment_id": experiment_id,
        "execution_mode": "VALIDATION",
        "phase": "A_TEMPLATE_OBSERVATION",
        "status": "WAITING_FOR_PHASE_A_GUI_OBSERVATION",
        "template": {
            "source_file": str(selected.path),
            "working_copy": template.name,
            "sha256": _sha256(template),
            "rx3_database": str(database),
            "rx3_database_sha256": _sha256(database),
        },
        "selection": {
            "mark": record.fields[1],
            "line": record.line_number,
            "profile_database_table": selected.profile_table,
            "template_record_sha256": fingerprint,
            "reasons": list(selected.reasons),
            "warning": "Pure axial and heating exposure remain unverified until GUI observation",
        },
        "controlled_project_element": {
            "file": project_draft.name,
            "project_element_id": project_element_id,
            "heating_sides": heating_sides,
            "force_convention": "UNVERIFIED_VALIDATION_EXCEPTION",
            "generated_rx38_allowed": False,
        },
        "expected_rx3_values": expected,
        "unmapped": [
            "Mx/My/Qx/Qy RX38 mappings",
            "RX38 heating-side indices",
            "critical-temperature calculation mode",
            "field33 == Ry semantic equivalence",
        ],
        "stale_template_result_indices": [44, 54],
    }
    project_payload = _project_payload(
        experiment_id, project_element_id, heating_sides, selected.path, record
    )
    evidence = {
        "project_element_id": project_element_id,
        "heating_sides": heating_sides,
        "template_record_sha256": fingerprint,
        "status": "UNVERIFIED",
        "source": f"{experiment_id} GUI observation",
        "confirmed_by": None,
        "confirmed_at": None,
        "version": "1",
    }
    _write_new(
        summary_json,
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n",
    )
    _write_new(summary_md, _summary_markdown(summary))
    _write_new(
        evidence_json,
        json.dumps(evidence, ensure_ascii=False, indent=2) + "\n",
    )
    _write_new(checklist, _checklist(summary))
    _write_new(selection_report, _selection_markdown(ranked, selected))
    _write_new(
        project_draft,
        json.dumps(project_payload, ensure_ascii=False, indent=2) + "\n",
    )
    return Rx3PhaseABundle(
        directory, template, summary_json, summary_md, evidence_json, checklist,
        selection_report, project_draft, selected.path, record.fields[1]
    )


def _required_text(payload: dict[str, Any], name: str) -> str:
    value = payload.get(name)
    if not isinstance(value, str) or not value.strip():
        raise Rx3ExperimentPreparationError(
            f"Bending report reference {name} must be a non-empty string"
        )
    return value.strip()


def _required_reference_decimal(payload: dict[str, Any], name: str) -> Decimal:
    value = payload.get(name)
    if isinstance(value, bool) or not isinstance(value, (str, int, float)):
        raise Rx3ExperimentPreparationError(
            f"Bending report reference {name} must be numeric"
        )
    parsed = _decimal(str(value))
    if parsed is None:
        raise Rx3ExperimentPreparationError(
            f"Bending report reference {name} must be finite"
        )
    return parsed


def load_bending_report_references(
    path: str | Path,
) -> tuple[Rx3BendingReportReference, ...]:
    """Load explicit report/GUI values without assigning RX38 field semantics."""

    reference_path = Path(path).resolve(strict=True)
    try:
        payload = json.loads(reference_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise Rx3ExperimentPreparationError(
            f"Cannot read bending report references: {exc}"
        ) from exc
    if not isinstance(payload, dict):
        raise Rx3ExperimentPreparationError(
            "Bending report references must be a JSON object"
        )
    evidence_reference = _required_text(payload, "evidence_reference")
    raw_candidates = payload.get("candidates")
    if not isinstance(raw_candidates, list) or not raw_candidates:
        raise Rx3ExperimentPreparationError(
            "Bending report references must contain a non-empty candidates list"
        )

    result: list[Rx3BendingReportReference] = []
    seen: set[tuple[Path, str]] = set()
    for raw in raw_candidates:
        if not isinstance(raw, dict):
            raise Rx3ExperimentPreparationError(
                "Every bending report candidate must be a JSON object"
            )
        source_file = Path(_required_text(raw, "source_file")).resolve(strict=True)
        mark = _required_text(raw, "mark")
        key = (source_file, mark)
        if key in seen:
            raise Rx3ExperimentPreparationError(
                f"Duplicate bending report reference for {source_file} / {mark}"
            )
        seen.add(key)
        mx_knm = _required_reference_decimal(raw, "Mx_knm")
        q_kn = _required_reference_decimal(raw, "Q_kn")
        if mx_knm == 0:
            raise Rx3ExperimentPreparationError(
                f"Bending report reference Mx_knm must be non-zero for {mark}"
            )
        my_raw = raw.get("My_knm")
        my_knm = (
            None
            if my_raw is None
            else _required_reference_decimal(raw, "My_knm")
        )
        result.append(
            Rx3BendingReportReference(
                source_file,
                mark,
                mx_knm,
                q_kn,
                my_knm,
                evidence_reference,
            )
        )
    return tuple(result)


def _single_plane_bending_label(value: str) -> bool:
    normalized = _normal_text(value)
    return (
        "изгибаемый стержень" in normalized
        and "в одной из главных плоскостей" in normalized
        and "сжат" not in normalized
        and "растянут" not in normalized
    )


def _section_simplicity_rank(value: str) -> int:
    normalized = _normal_text(value)
    if normalized == "двутавр":
        return 0
    if normalized == "прямоугольная труба":
        return 1
    if normalized == "швеллер":
        return 2
    return 3


def _evaluate_bending_candidate(
    path: Path,
    record: Rx38Record,
    record_count: int,
    profiles: ProfileRepository,
    references: dict[tuple[Path, str], Rx3BendingReportReference],
) -> Rx3BendingTemplateCandidate:
    accepted_reasons: list[str] = []
    rejected_reasons: list[str] = []

    if _single_plane_bending_label(record.fields[45]):
        accepted_reasons.append("stress state is explicit single-plane bending")
    else:
        rejected_reasons.append("stress state is not explicit single-plane bending")

    axial_force = _decimal(record.fields[49])
    if axial_force == 0:
        accepted_reasons.append("confirmed axial N field is exactly zero")
    else:
        rejected_reasons.append("confirmed axial N field is non-zero or non-numeric")

    probable_moment = _decimal(record.fields[50])
    if probable_moment is not None and probable_moment != 0:
        accepted_reasons.append("field 50 is finite and non-zero")
    else:
        rejected_reasons.append("field 50 is zero or non-numeric")

    for index, name in ((14, "length"), (55, "required R")):
        value = _decimal(record.fields[index])
        if value is None or value <= 0:
            rejected_reasons.append(f"{name} is missing, non-numeric, or non-positive")
    quantity = _decimal(record.fields[15])
    if (
        quantity is None
        or quantity <= 0
        or quantity != quantity.to_integral_value()
    ):
        rejected_reasons.append("quantity is missing, non-positive, or non-integral")
    if not record.fields[104].strip():
        rejected_reasons.append("fire regime is empty")
    if record.fields[48].strip():
        accepted_reasons.append(
            "confirmed support text is populated and ranks behind empty bending support state"
        )
    else:
        accepted_reasons.append("confirmed compression support text is empty")

    if _steel_strength_aligned(record):
        accepted_reasons.append("steel grade suffix and field 33 are numerically aligned")
    else:
        rejected_reasons.append("steel grade and field 33 are not demonstrably aligned")
    for index, name in ((44, "field 44"), (54, "field 54")):
        if _decimal(record.fields[index]) is None:
            rejected_reasons.append(f"{name} has no existing numeric stale result")

    profile_result = profiles.search(record.fields[19], record.fields[17])
    profile = profile_result.candidate
    geometry_matches = profile is not None and _profile_geometry_matches(record, profile)
    if profile_result.status == "FOUND" and geometry_matches:
        accepted_reasons.append("profile and core geometry match exactly one rx3.rxdb row")
    elif profile_result.status != "FOUND":
        rejected_reasons.append(
            f"profile lookup in rx3.rxdb is {profile_result.status}"
        )
    else:
        rejected_reasons.append("profile name resolves but core geometry does not match rx3.rxdb")

    reference = references.get((path, record.fields[1]))
    if reference is None:
        rejected_reasons.append("no explicit GUI/report Mx and Q reference was supplied")
    else:
        if probable_moment == reference.mx_knm:
            accepted_reasons.append(
                "external Mx reference equals field 50; Phase A alone does not promote its mapping"
            )
        else:
            rejected_reasons.append(
                "external Mx reference does not equal field 50"
            )
        raw_q_candidate = _decimal(record.fields[92])
        if raw_q_candidate == reference.q_kn:
            accepted_reasons.append(
                "external Q reference equals raw field 92; field 92 remains unmapped"
            )
        else:
            rejected_reasons.append(
                "external Q reference does not equal raw field 92"
            )
        if reference.my_knm is None:
            accepted_reasons.append("My has no external reference and requires GUI observation")
        elif reference.my_knm == 0:
            accepted_reasons.append("external My reference is exactly zero")
        else:
            rejected_reasons.append("external My reference is non-zero")
        if reference.q_kn != 0:
            accepted_reasons.append(
                "Q is non-zero, so the candidate is not described as pure Mx"
            )

    return Rx3BendingTemplateCandidate(
        path,
        record,
        record_count,
        not rejected_reasons,
        tuple(accepted_reasons + rejected_reasons),
        profile.table if profile is not None else None,
        bool(geometry_matches),
        reference,
        _section_simplicity_rank(record.fields[5]),
    )


def rank_rx3_bending_template_candidates(
    template_paths: Iterable[str | Path],
    db_path: str | Path,
    report_references: Iterable[Rx3BendingReportReference],
) -> tuple[Rx3BendingTemplateCandidate, ...]:
    """Rank observation-only bending candidates without assigning force mappings."""

    profiles = ProfileRepository(Path(db_path).resolve(strict=True))
    reference_map = {
        (item.source_file.resolve(strict=True), item.mark): item
        for item in report_references
    }
    candidates: list[Rx3BendingTemplateCandidate] = []
    for supplied in template_paths:
        path = Path(supplied).resolve(strict=True)
        records = construction_records(read_rx38(path))
        candidates.extend(
            _evaluate_bending_candidate(
                path,
                record,
                len(records),
                profiles,
                reference_map,
            )
            for record in records
        )
    return tuple(sorted(candidates, key=lambda item: item.rank_key))


def _bending_selection_markdown(
    ranked: tuple[Rx3BendingTemplateCandidate, ...],
    selected: Rx3BendingTemplateCandidate,
) -> str:
    lines = [
        "# RX3-EXP-02 — template selection for bending observation",
        "",
        "This is an objective Phase A ranking only. It does not confirm Mx, My, Qx or Qy RX38 mappings and does not authorize generation.",
        "Because the supplied bending references contain non-zero Q, no candidate is described as pure Mx.",
        "",
        "| Rank | File | Source line | Mark | Profile | Section | N | field50 | report Mx | report Q | report My | abs(Q/Mx) | DB geometry | Decision | Reasons |",
        "|---:|---|---:|---|---|---|---:|---:|---:|---:|---:|---:|---|---|---|",
    ]
    for rank, candidate in enumerate(ranked, 1):
        record = candidate.record
        reference = candidate.report_reference
        decision = (
            "SELECTED"
            if candidate is selected
            else "ACCEPTED"
            if candidate.accepted
            else "REJECTED"
        )
        lines.append(
            "| "
            + " | ".join(
                _escape(item)
                for item in (
                    rank,
                    candidate.path,
                    record.line_number,
                    record.fields[1],
                    record.fields[19],
                    record.fields[5],
                    record.fields[49],
                    record.fields[50],
                    reference.mx_knm if reference else "NOT SUPPLIED",
                    reference.q_kn if reference else "NOT SUPPLIED",
                    (
                        reference.my_knm
                        if reference and reference.my_knm is not None
                        else "NOT SUPPLIED"
                    ),
                    candidate.q_to_mx_ratio or "NOT AVAILABLE",
                    (
                        f"FOUND / {candidate.profile_table}"
                        if candidate.profile_database_match
                        else "NO EXACT MATCH"
                    ),
                    decision,
                    "; ".join(candidate.reasons),
                )
            )
            + " |"
        )
    lines.extend(
        [
            "",
            "Tie-break order: all fail-closed gates, simpler section family, empty confirmed compression-support text, lower externally reported abs(Q/Mx), smaller source project, stable filename and line order.",
            "",
        ]
    )
    return "\n".join(lines)


def _bending_expected_values(
    selected: Rx3BendingTemplateCandidate,
) -> str:
    record = selected.record
    reference = selected.report_reference
    if reference is None:
        raise Rx3ExperimentPreparationError("Selected bending candidate has no report reference")
    my_value = str(reference.my_knm) if reference.my_knm is not None else "NOT SUPPLIED"
    return "\n".join(
        [
            "# RX3-EXP-02 — expected existing values",
            "",
            f"Selected mark: `{record.fields[1]}`",
            f"Profile: `{record.fields[19]}` / `{record.fields[17]}`",
            f"Stress state: `{record.fields[45]}`",
            "",
            "| Value | Expected | Evidence status |",
            "|---|---:|---|",
            f"| N | `{record.fields[49]} kN` | CONFIRMED field49; must remain zero |",
            f"| Mx reference | `{reference.mx_knm} kN*m` | EXTERNAL GUI/REPORT REFERENCE; equals field50, Phase A alone does not promote mapping |",
            f"| Q reference | `{reference.q_kn} kN` | EXTERNAL GUI/REPORT REFERENCE; equals raw field92, mapping remains UNKNOWN |",
            f"| My reference | `{my_value}` | {'EXTERNAL ZERO REFERENCE' if reference.my_knm == 0 else 'NOT PROVIDED; OBSERVE IN GUI'} |",
            f"| Length | `{record.fields[14]} m` | CONFIRMED field14 |",
            f"| Loading axis text | `{record.fields[61]}` | PROBABLE field61; observe exact GUI axis |",
            f"| Required R | `{record.fields[55]} min` | CONFIRMED field55 |",
            f"| Fire regime | `{record.fields[104]}` | CONFIRMED field104 |",
            f"| Existing theta_cr | `{record.fields[44]} C` | STALE_TEMPLATE_RESULT; do not calculate |",
            f"| Existing R0 | `{record.fields[54]} min` | STALE_TEMPLATE_RESULT; do not calculate |",
            "",
            f"Reference provenance: `{reference.evidence_reference}`.",
            "No RX38 token is assigned to My, Q, lateral-torsional buckling, W/Wpl selection, stability selection or unbraced length by this bundle.",
            "",
        ]
    )


def _bending_checklist(selected: Rx3BendingTemplateCandidate) -> str:
    record = selected.record
    return "\n".join(
        [
            "# RX3-EXP-02 CHECKLIST A — BENDING TEMPLATE GUI OBSERVATION",
            "",
            "Open only `template.rx38`, select the exact mark below, and open the calculation dialog. Do not edit values, calculate, save to table, or save the project.",
            "",
            f"- [ ] Selected mark is `{record.fields[1]}` and profile is `{record.fields[19]}`.",
            f"- [ ] Stress state text is exactly `{record.fields[45]}`.",
            "- [ ] Record the exact bending-mode dialog title and loading-mode text.",
            "- [ ] Record every displayed moment label, axis designation, value and unit.",
            "- [ ] Record whether the displayed moment is named Mx, My, Mmax or something else.",
            "- [ ] Record whether Q is displayed, its exact label/value/unit, and any indication that it is used by the fire-resistance strength calculation.",
            "- [ ] Record whether My is displayed and whether it is exactly zero.",
            "- [ ] Record the axis selector and its exact selected value.",
            "- [ ] Record the W/Wpl or elastic/plastic section-modulus selection.",
            "- [ ] Record every strength/stability checkbox and its selected state.",
            "- [ ] Record lateral-torsional buckling controls, if present.",
            "- [ ] Record support, unbraced-length and effective-length inputs, if present.",
            "- [ ] Record every additional coefficient or option visible in the bending dialog.",
            "- [ ] Capture one screenshot showing the complete dialog and selected mark.",
            "- [ ] Confirm Calculate/Recalculate was not pressed.",
            "- [ ] Confirm Save to table was not pressed and the project was not saved.",
            "",
            "If any expected identity/value differs, stop and report the mismatch. Do not infer RX38 mappings from the observation.",
            "",
        ]
    )


def _bending_gui_instructions(selected: Rx3BendingTemplateCandidate) -> str:
    record = selected.record
    return "\n".join(
        [
            "# RX3-EXP-02 — exact Phase A user actions",
            "",
            "1. Verify the SHA-256 recorded in `template_summary.json`, then open `template.rx38` in the original RX3.",
            f"2. Select mark `{record.fields[1]}` and verify profile `{record.fields[19]}` before opening its calculation dialog.",
            "3. Observe the dialog only and complete `CHECKLIST.md`; do not change any field and do not press Calculate/Recalculate.",
            "4. Capture the full dialog, including moment/Q fields, axes, W/Wpl, stability and unbraced-length controls.",
            "5. Close without Save to table or project save, then return the completed checklist and screenshot/report text.",
            "",
            "Stop boundary: no altered Mx RX38 is created in Phase A.",
            "",
        ]
    )


def prepare_rx3_bending_phase_a(
    template_paths: Iterable[str | Path],
    db_path: str | Path,
    report_references: Iterable[Rx3BendingReportReference],
    output_directory: str | Path,
    *,
    experiment_id: str = "RX3-EXP-02",
) -> Rx3BendingPhaseABundle:
    """Prepare a non-generating bending observation bundle."""

    if experiment_id != "RX3-EXP-02":
        raise Rx3ExperimentPreparationError(
            "Only the controlled bending-observation RX3-EXP-02 protocol is supported"
        )
    paths = tuple(Path(path).resolve(strict=True) for path in template_paths)
    if not paths:
        raise Rx3ExperimentPreparationError("No RX38 template candidates were supplied")
    references = tuple(report_references)
    if not references:
        raise Rx3ExperimentPreparationError("No bending GUI/report references were supplied")
    database = Path(db_path).resolve(strict=True)
    ranked = rank_rx3_bending_template_candidates(paths, database, references)
    selected = next((candidate for candidate in ranked if candidate.accepted), None)
    if selected is None:
        raise Rx3ExperimentPreparationError(
            "No candidate passed the bending Phase A selection gates"
        )

    directory = Path(output_directory).resolve(strict=False)
    if directory.exists() and any(directory.iterdir()):
        raise Rx3ExperimentPreparationError(
            f"Phase A directory must be new or empty: {directory}"
        )
    directory.mkdir(parents=True, exist_ok=True)

    template = directory / "template.rx38"
    summary_path = directory / "template_summary.json"
    selection_path = directory / "RX3_EXP_02_TEMPLATE_SELECTION.md"
    expected_path = directory / "EXPECTED_REPORT_VALUES.md"
    checklist_path = directory / "CHECKLIST.md"
    instructions_path = directory / "GUI_OBSERVATION_INSTRUCTIONS.md"
    references_path = directory / "candidate_report_values.json"
    _copy_new(selected.path, template)
    if _sha256(template) != _sha256(selected.path):
        raise Rx3ExperimentPreparationError("Copied template is not byte-identical")

    reference = selected.report_reference
    if reference is None:
        raise Rx3ExperimentPreparationError("Selected bending candidate lost its reference")
    summary = {
        "experiment_id": experiment_id,
        "execution_mode": "VALIDATION",
        "phase": "A_BENDING_TEMPLATE_OBSERVATION",
        "status": "WAITING_FOR_BENDING_TEMPLATE_GUI_OBSERVATION",
        "generation_allowed": False,
        "calculation_allowed": False,
        "template": {
            "source_file": str(selected.path),
            "working_copy": template.name,
            "sha256": _sha256(template),
            "rx3_database": str(database),
            "rx3_database_sha256": _sha256(database),
        },
        "selection": {
            "mark": selected.record.fields[1],
            "line": selected.record.line_number,
            "position": construction_records(read_rx38(selected.path)).index(selected.record) + 1,
            "profile": selected.record.fields[19],
            "profile_database_table": selected.profile_table,
            "template_record_sha256": rx38_record_fingerprint(selected.record),
            "reasons": list(selected.reasons),
        },
        "external_report_reference": {
            "Mx_knm": str(reference.mx_knm),
            "Q_kn": str(reference.q_kn),
            "My_knm": str(reference.my_knm) if reference.my_knm is not None else None,
            "evidence_reference": reference.evidence_reference,
        },
        "warnings": [
            "Q is non-zero; this is not a pure-Mx experiment",
            "Phase A alone does not promote field50; field92 remains UNKNOWN",
            "My/Qx/Qy mappings remain unassigned",
            "stale template fields 44/54 are not new calculation results",
        ],
        "required_observations": [
            "exact bending dialog and loading mode",
            "moment labels, values, units and axis designation",
            "Q display and apparent use",
            "lateral-torsional buckling settings",
            "support and unbraced/effective length inputs",
            "W/Wpl selection",
            "strength/stability selection",
            "additional coefficients",
        ],
    }
    normalized_references = {
        "evidence_reference": reference.evidence_reference,
        "candidates": [
            {
                "source_file": str(item.source_file),
                "mark": item.mark,
                "Mx_knm": str(item.mx_knm),
                "Q_kn": str(item.q_kn),
                "My_knm": str(item.my_knm) if item.my_knm is not None else None,
            }
            for item in references
        ],
    }
    _write_new(summary_path, json.dumps(summary, ensure_ascii=False, indent=2) + "\n")
    _write_new(selection_path, _bending_selection_markdown(ranked, selected))
    _write_new(expected_path, _bending_expected_values(selected))
    _write_new(checklist_path, _bending_checklist(selected))
    _write_new(instructions_path, _bending_gui_instructions(selected))
    _write_new(
        references_path,
        json.dumps(normalized_references, ensure_ascii=False, indent=2) + "\n",
    )
    return Rx3BendingPhaseABundle(
        directory,
        template,
        summary_path,
        selection_path,
        expected_path,
        checklist_path,
        instructions_path,
        references_path,
        selected.path,
        selected.record.fields[1],
    )


def _require_mapping(value: object, name: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise Rx3ExperimentPreparationError(f"{name} must be a JSON object")
    return value


def _require_observation_text(value: object, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise Rx3ExperimentPreparationError(f"{name} must be a non-empty string")
    return value.strip()


def _read_json_mapping(path: Path, name: str) -> Mapping[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise Rx3ExperimentPreparationError(f"Cannot read {name}: {exc}") from exc
    return _require_mapping(value, name)


def _candidate_indices(record: Rx38Record, target: Decimal) -> tuple[int, ...]:
    return tuple(
        index
        for index, raw in enumerate(record.fields)
        if _decimal(raw) == target
    )


def _raw_token_fingerprint(raw_token: str) -> str:
    return sha256(raw_token.encode("utf-8")).hexdigest()


def _render_experimental_field_change(
    document: Rx38Document,
    *,
    target_line: int | None,
    field_index: int | None,
    new_raw_token: str | None,
) -> bytes:
    lines: list[str] = []
    changed = False
    for record in document.records:
        tokens = list(record.raw_tokens)
        if (
            target_line is not None
            and record.line_number == target_line
            and field_index is not None
        ):
            if changed:
                raise Rx3ExperimentPreparationError(
                    "Experimental target line is not unique"
                )
            if record.record_type != "Tconstr" or len(tokens) != 200:
                raise Rx3ExperimentPreparationError(
                    "Experimental target must be one parsed 200-field Tconstr"
                )
            if new_raw_token is None or any(
                char in new_raw_token for char in ';"\r\n'
            ):
                raise Rx3ExperimentPreparationError(
                    "Experimental decimal token is not safe for verbatim insertion"
                )
            tokens[field_index] = new_raw_token
            changed = True
        lines.append(";".join(tokens) + record.newline)
    if target_line is not None and not changed:
        raise Rx3ExperimentPreparationError("Experimental target line was not found")
    payload = "".join(lines).encode(document.encoding)
    if document.has_bom and document.encoding == "utf-8":
        payload = b"\xef\xbb\xbf" + payload
    return payload


def _bending_project_payload(
    source: Path,
    record: Rx38Record,
    *,
    target_mx: Decimal,
    heating_sides: int,
) -> dict[str, Any]:
    def q(raw: str | Decimal, unit: str) -> dict[str, str]:
        return {"value": str(raw).replace(",", "."), "unit": unit}

    payload: dict[str, Any] = {
        "project_id": "RX3-EXP-02B",
        "element_id": "RX3-EXP-02B-B1",
        "mark": record.fields[1],
        "element_type": "beam",
        "source_file": str(source),
        "source_type": "RX38_CONTROLLED_VALIDATION_EXPERIMENT",
        "source_element_id": record.fields[1],
        "source_row": record.line_number,
        "timestamp": datetime.now().astimezone().isoformat(),
        "section_type": record.fields[5],
        "profile_standard": record.fields[17],
        "profile_name": record.fields[19],
        "area": q(record.fields[20], "mm2"),
        "full_perimeter": None,
        "heated_perimeter": q(record.fields[21], "mm"),
        "ptm": q(record.fields[22], "mm"),
        "length": q(record.fields[14], "m"),
        "quantity": int(Decimal(record.fields[15].replace(",", "."))),
        "steel_grade": record.fields[42],
        "Ry": None,
        "E": q(record.fields[34], "MPa"),
        "density": q(record.fields[32], "kg/m3"),
        "load_case": "RX3-EXP-02B",
        "combination": "RX3-EXP-02B_MX10",
        "N": q(record.fields[49], "kN"),
        "Mx": q(target_mx, "kN*m"),
        "My": None,
        "Qx": None,
        "Qy": None,
        "governing_combination": "RX3-EXP-02B_MX10",
        "required_fire_resistance": q(record.fields[55], "min"),
        "stress_state": record.fields[45],
        "heating_sides": heating_sides,
        "support_condition": None,
        "effective_length_parameters": None,
        "critical_temperature": None,
        "unprotected_fire_resistance": None,
        "material_id": None,
        "coating_type": None,
        "required_thickness": None,
        "specific_consumption": None,
        "protected_area": q(record.fields[25], "m2"),
        "total_consumption": None,
    }
    field_indices = {
        "section_type": 5,
        "profile_standard": 17,
        "profile_name": 19,
        "area": 20,
        "heated_perimeter": 21,
        "ptm": 22,
        "length": 14,
        "quantity": 15,
        "steel_grade": 42,
        "E": 34,
        "density": 32,
        "N": 49,
        "required_fire_resistance": 55,
        "stress_state": 45,
        "protected_area": 25,
    }
    experiment_fields = {
        "project_id",
        "element_id",
        "mark",
        "element_type",
        "load_case",
        "combination",
        "Mx",
        "governing_combination",
        "heating_sides",
    }
    provenance: dict[str, Any] = {}
    for name, value in payload.items():
        if value is None or name in {
            "source_file",
            "source_type",
            "source_element_id",
            "source_row",
            "timestamp",
        }:
            continue
        if name in field_indices:
            index = field_indices[name]
            provenance[name] = {
                "kind": "SOURCE",
                "file": str(source),
                "row": record.line_number,
                "field": f"Tconstr[{index}]",
            }
        elif name in experiment_fields:
            provenance[name] = {
                "kind": "ENGINEER_INPUT",
                "file": "RX3-EXP-02B controlled validation protocol",
                "field": name,
            }
    payload["provenance"] = provenance
    return project_element_to_dict(project_element_from_dict(payload))


def prepare_rx3_bending_mx10_validation(
    phase_a_directory: str | Path,
    observation_path: str | Path,
    output_directory: str | Path,
    *,
    experiment_id: str = "RX3-EXP-02B",
) -> Rx3BendingMxValidationBundle:
    """Prepare the exact fingerprint-bound Mx=10 validation artifact.

    This is deliberately separate from the production RX38 writer. It permits
    one field-50 perturbation only after validating the completed RX3-EXP-02
    Phase A observation and the five-record bending corpus. It never starts RX3.
    """

    if experiment_id != "RX3-EXP-02B":
        raise Rx3ExperimentPreparationError(
            "Only the controlled RX3-EXP-02B Mx=10 validation protocol is supported"
        )
    phase_a = Path(phase_a_directory).resolve(strict=True)
    observation_file = Path(observation_path).resolve(strict=True)
    summary = _read_json_mapping(phase_a / "template_summary.json", "Phase A summary")
    observation = _read_json_mapping(observation_file, "Phase A GUI observation")
    if summary.get("experiment_id") != "RX3-EXP-02":
        raise Rx3ExperimentPreparationError("Phase A summary is not RX3-EXP-02")
    if observation.get("experiment_id") != "RX3-EXP-02":
        raise Rx3ExperimentPreparationError("GUI observation is not RX3-EXP-02")
    if observation.get("result") != "PASS":
        raise Rx3ExperimentPreparationError("Phase A GUI observation must be PASS")
    if observation.get("calculation_pressed") is not False:
        raise Rx3ExperimentPreparationError(
            "Phase A evidence must confirm that calculation was not pressed"
        )

    summary_template = _require_mapping(summary.get("template"), "summary.template")
    summary_selection = _require_mapping(summary.get("selection"), "summary.selection")
    observed_template = _require_mapping(
        observation.get("template"), "observation.template"
    )
    observed_selection = _require_mapping(
        observation.get("selection"), "observation.selection"
    )
    observed_actions = _require_mapping(
        observation.get("actions"), "observation.actions"
    )
    observed_heating = _require_mapping(
        observation.get("heating"), "observation.heating"
    )
    observed_fire = _require_mapping(observation.get("fire"), "observation.fire")

    template_source = phase_a / _require_observation_text(
        summary_template.get("working_copy"), "summary.template.working_copy"
    )
    template_source = template_source.resolve(strict=True)
    template_sha = _sha256(template_source)
    expected_template_sha = _require_observation_text(
        summary_template.get("sha256"), "summary.template.sha256"
    )
    if template_sha != expected_template_sha or observed_template.get("sha256") != template_sha:
        raise Rx3ExperimentPreparationError("Template SHA-256 is not bound consistently")

    mark = _require_observation_text(summary_selection.get("mark"), "summary.selection.mark")
    if observed_selection.get("mark") != mark:
        raise Rx3ExperimentPreparationError("Observed mark differs from Phase A selection")
    records = construction_records(read_rx38(template_source))
    targets = [record for record in records if record.mark == mark]
    if len(targets) != 1:
        raise Rx3ExperimentPreparationError("Phase A target mark is not unique")
    target = targets[0]
    fingerprint = rx38_record_fingerprint(target)
    expected_fingerprint = _require_observation_text(
        summary_selection.get("template_record_sha256"),
        "summary.selection.template_record_sha256",
    )
    if (
        fingerprint != expected_fingerprint
        or observed_template.get("record_fingerprint") != fingerprint
    ):
        raise Rx3ExperimentPreparationError(
            "GUI evidence is not bound to the exact selected Tconstr"
        )

    observed_mx = _decimal(str(observed_actions.get("Mx_knm", "")))
    observed_q = _decimal(str(observed_actions.get("Q_kn", "")))
    if observed_actions.get("Mx_label") != "Mx" or observed_mx != Decimal("8.89"):
        raise Rx3ExperimentPreparationError("GUI evidence must identify Mx = 8.89 kN*m")
    if observed_actions.get("Q_label") != "Q" or observed_q != Decimal("2.32"):
        raise Rx3ExperimentPreparationError("GUI evidence must identify Q = 2.32 kN")
    if observed_actions.get("N_input_displayed") is not False:
        raise Rx3ExperimentPreparationError("Bending observation unexpectedly displays N input")
    if observed_selection.get("stress_state") != target.fields[45]:
        raise Rx3ExperimentPreparationError("Observed stress state differs from the template")
    observed_axis = _require_observation_text(
        observed_selection.get("axis"), "observation.selection.axis"
    )
    if _normal_axis(observed_axis) != "ОТН. X-X" or _normal_axis(target.fields[61]) != _normal_axis(observed_axis):
        raise Rx3ExperimentPreparationError("Experiment requires the exact X-X bending axis")
    if observed_heating.get("heating_sides") != 3:
        raise Rx3ExperimentPreparationError("Experiment requires observed three-side heating")
    sides = observed_heating.get("active_sides")
    if not isinstance(sides, list) or set(sides) != {"LEFT", "RIGHT", "BOTTOM"}:
        raise Rx3ExperimentPreparationError("Observed heating sides are not LEFT/RIGHT/BOTTOM")
    if observed_heating.get("rx38_indices_mapped") is not False:
        raise Rx3ExperimentPreparationError("Heating-side RX38 indices must remain unmapped")
    if _decimal(str(observed_fire.get("required_R_min", ""))) != Decimal("60"):
        raise Rx3ExperimentPreparationError("Experiment requires the observed R60 state")

    mx_candidates = _candidate_indices(target, observed_mx)
    q_candidates = _candidate_indices(target, observed_q)
    references = load_bending_report_references(phase_a / "candidate_report_values.json")
    expected_marks = {"Б1", "Б2", "Б3", "Б4", "Б5"}
    if {item.mark for item in references} != expected_marks:
        raise Rx3ExperimentPreparationError("Bending corpus must contain exactly Б1-Б5")
    mx_consistent = set(range(200))
    q_consistent = set(range(200))
    cross_rows: list[dict[str, Any]] = []
    for reference in references:
        source_records = construction_records(read_rx38(reference.source_file))
        matches = [record for record in source_records if record.mark == reference.mark]
        if len(matches) != 1:
            raise Rx3ExperimentPreparationError(
                f"Corpus mark is not unique: {reference.mark}"
            )
        record = matches[0]
        if not _single_plane_bending_label(record.fields[45]):
            raise Rx3ExperimentPreparationError(
                f"Corpus record is not one-plane bending: {reference.mark}"
            )
        row_mx = set(_candidate_indices(record, reference.mx_knm))
        row_q = set(_candidate_indices(record, reference.q_kn))
        mx_consistent &= row_mx
        q_consistent &= row_q
        cross_rows.append(
            {
                "mark": reference.mark,
                "reference_Mx_knm": str(reference.mx_knm),
                "field50_raw": record.fields[50],
                "field78_raw": record.fields[78],
                "reference_Q_kn": str(reference.q_kn),
                "field92_raw": record.fields[92],
                "Mx_candidate_indices": sorted(row_mx),
                "Q_candidate_indices": sorted(row_q),
            }
        )
    if 50 not in mx_candidates or 50 not in mx_consistent:
        raise Rx3ExperimentPreparationError("Field 50 failed the Mx consistency gates")
    if 92 not in q_candidates or 92 not in q_consistent:
        raise Rx3ExperimentPreparationError("Field 92 failed the Q preservation gates")
    spec50 = field_spec(50)
    if (
        spec50.confidence != "confirmed"
        or spec50.name != "major_axis_moment_knm"
        or spec50.write_policy is not WritePolicy.EXPERIMENTAL
    ):
        raise Rx3ExperimentPreparationError("Field 50 schema state changed; re-review required")

    directory = Path(output_directory).resolve(strict=False)
    if directory.exists() and any(directory.iterdir()):
        raise Rx3ExperimentPreparationError(
            f"Phase B directory must be new or empty: {directory}"
        )
    directory.mkdir(parents=True, exist_ok=True)
    template = directory / "template.rx38"
    generated = directory / "generated_MX10.rx38"
    project_path = directory / "project_element_MX10.json"
    profile_path = directory / "template_profile.json"
    heating_path = directory / "heating_evidence.json"
    diff_json_path = directory / "precalc_diff.json"
    diff_markdown_path = directory / "precalc_diff.md"
    expected_path = directory / "EXPECTED_RX3_GUI_VALUES.md"
    checklist_path = directory / "CHECKLIST_PRECALC.md"
    instructions_path = directory / "README_RUN_RX3.md"
    audit_path = directory / "audit.json"

    _copy_new(template_source, template)
    document = read_rx38_document(template)
    if _render_experimental_field_change(
        document, target_line=None, field_index=None, new_raw_token=None
    ) != template.read_bytes():
        raise Rx3ExperimentPreparationError(
            "Parser token rendering is not byte-identical to the template"
        )
    copied_targets = [
        record
        for record in construction_records(document.records)
        if rx38_record_fingerprint(record) == fingerprint
    ]
    if len(copied_targets) != 1 or copied_targets[0].mark != mark:
        raise Rx3ExperimentPreparationError("Copied experimental target is not unique")
    generated_payload = _render_experimental_field_change(
        document,
        target_line=copied_targets[0].line_number,
        field_index=50,
        new_raw_token="10,00",
    )
    try:
        with generated.open("xb") as stream:
            stream.write(generated_payload)
    except FileExistsError as exc:
        raise Rx3ExperimentPreparationError(
            f"Refusing to overwrite Phase B artifact: {generated}"
        ) from exc

    before_records = construction_records(read_rx38(template))
    after_records = construction_records(read_rx38(generated))
    if len(before_records) != len(after_records):
        raise Rx3ExperimentPreparationError("Generated RX38 changed record count")
    changed_records: list[tuple[Rx38Record, Rx38Record, list[Any]]] = []
    for before, after in zip(before_records, after_records):
        differences = diff_records(before, after)
        if differences:
            changed_records.append((before, after, differences))
    if len(changed_records) != 1:
        raise Rx3ExperimentPreparationError("Generated RX38 must change one Tconstr only")
    before_target, after_target, differences = changed_records[0]
    if before_target.mark != mark or len(differences) != 1 or differences[0].index != 50:
        raise Rx3ExperimentPreparationError("Generated RX38 is not an exact field-50-only change")
    if _decimal(after_target.fields[50]) != Decimal("10.00"):
        raise Rx3ExperimentPreparationError("Generated Mx token is not Decimal 10.00")
    if before_target.raw_tokens[92] != after_target.raw_tokens[92]:
        raise Rx3ExperimentPreparationError("Q raw token changed; experiment is invalid")

    q_token = before_target.raw_tokens[92]
    q_token_sha = _raw_token_fingerprint(q_token)
    generated_sha = _sha256(generated)
    generated_fingerprint = rx38_record_fingerprint(after_target)
    project_payload = _bending_project_payload(
        template, before_target, target_mx=Decimal("10.00"), heating_sides=3
    )
    profile_payload = {
        "experiment_id": experiment_id,
        "template_sha256": _sha256(template),
        "template_record_sha256": fingerprint,
        "mark": mark,
        "section": before_target.fields[5],
        "standard": before_target.fields[17],
        "profile": before_target.fields[19],
        "geometry_mm": {
            "b": before_target.fields[9],
            "h": before_target.fields[8],
            "tf": before_target.fields[13],
            "tw": before_target.fields[11],
        },
        "length_m": before_target.fields[14],
        "quantity": before_target.fields[15],
        "steel": before_target.fields[42],
        "density_kg_m3": before_target.fields[32],
        "stress_state": before_target.fields[45],
        "axis": before_target.fields[61],
        "field50_raw": before_target.fields[50],
        "field78_duplicate_raw": before_target.fields[78],
        "field92_Q_candidate_raw": before_target.fields[92],
    }
    heating_payload = {
        "experiment_id": experiment_id,
        "status": "ENGINEER_CONFIRMED_GUI_OBSERVATION",
        "scope": "exact template Tconstr only",
        "template_record_sha256": fingerprint,
        "mark": mark,
        "heating_sides": 3,
        "active_sides": ["LEFT", "RIGHT", "BOTTOM"],
        "inactive_sides": ["TOP"],
        "rx38_heating_side_indices": None,
        "mapping_status": "UNMAPPED",
        "evidence_reference": str(observation_file),
    }
    diff_payload = {
        "experiment_id": experiment_id,
        "status": "PASS",
        "template_sha256": _sha256(template),
        "generated_sha256": generated_sha,
        "target_mark": mark,
        "template_record_sha256": fingerprint,
        "generated_record_sha256": generated_fingerprint,
        "changed_record_count": 1,
        "changed_field_count": 1,
        "changed_fields": [
            {
                "index": 50,
                "schema_name": spec50.name,
                "semantic_intent": "Mx perturbation for validation only",
                "old_raw_token": before_target.raw_tokens[50],
                "new_raw_token": after_target.raw_tokens[50],
                "old_decimal_knm": str(_decimal(before_target.fields[50])),
                "new_decimal_knm": str(_decimal(after_target.fields[50])),
            }
        ],
        "all_other_target_fields_token_identical": True,
        "all_non_target_records_token_identical": True,
        "Q_preservation": {
            "candidate_index": 92,
            "raw_token_before": q_token,
            "raw_token_after": after_target.raw_tokens[92],
            "raw_token_sha256_before": q_token_sha,
            "raw_token_sha256_after": _raw_token_fingerprint(after_target.raw_tokens[92]),
            "token_identical": True,
        },
    }
    audit_payload = {
        "experiment_id": experiment_id,
        "phase": "MX_PERTURBATION_PRECALC",
        "status": "WAITING_FOR_MX10_PRECALC_GUI_VERIFICATION",
        "execution_mode": "VALIDATION",
        "calculation_started": False,
        "phase_a_observation": str(observation_file),
        "template": {
            "path": str(template),
            "sha256": _sha256(template),
            "record_sha256": fingerprint,
        },
        "generated": {
            "path": str(generated),
            "sha256": generated_sha,
            "record_sha256": generated_fingerprint,
        },
        "candidate_analysis": {
            "B1_Mx_candidate_indices": list(mx_candidates),
            "B1_Q_candidate_indices": list(q_candidates),
            "cross_corpus_Mx_consistent_indices": sorted(mx_consistent),
            "cross_corpus_Q_consistent_indices": sorted(q_consistent),
            "records": cross_rows,
        },
        "field50_evidence_decision": (
            "CONFIRMED_SEMANTICS / EXPERIMENTAL_WRITE_ONLY: RX3-EXP-02B "
            "separated active GUI field50 from the field78 persisted copy; scope is "
            "one-plane bending / X-X / verified B1 template family"
        ),
        "Q_evidence_decision": (
            "OBSERVATIONAL_CANDIDATE_ONLY: field92 is the sole exact candidate, "
            "but Q semantics and directional mapping remain unconfirmed"
        ),
        "Mx_write_safety_decision": (
            "EXPERIMENTAL_WRITE_ALLOWED / VALIDATION_ONLY / RX3-EXP-02B / "
            "exact fingerprint / mark Б1 / one-plane bending / axis X-X"
        ),
        "production_writer_changed": False,
        "schema_mapping_promoted": True,
        "precalc_diff": diff_payload,
        "stale_template_results": [
            {"index": 44, "raw_value": before_target.fields[44]},
            {"index": 54, "raw_value": before_target.fields[54]},
        ],
    }

    diff_markdown = "\n".join(
        [
            "# RX3-EXP-02B pre-calc diff",
            "",
            "Result: `PASS` — exactly one Tconstr field changed.",
            "",
            "| Mark | Field | Schema name | Before | After | Intent |",
            "|---|---:|---|---:|---:|---|",
            f"| {mark} | 50 | {spec50.name} | `{before_target.raw_tokens[50]}` | `10,00` | validation-only Mx 8.89 -> 10.00 kN*m |",
            "",
            f"Q candidate field 92 stayed token-identical: `{q_token}` (SHA-256 `{q_token_sha}`).",
            "All other 199 target fields and all non-target records are token-identical.",
            "Fields 44 and 54 remain stale template results; no calculation has been run.",
            "",
        ]
    )
    expected_gui = "\n".join(
        [
            "# RX3-EXP-02B expected GUI values before Calculate",
            "",
            f"- Mark: `{mark}`",
            f"- Profile: `{before_target.fields[19]}` / `{before_target.fields[17]}`",
            f"- Steel: `{before_target.fields[42]}`",
            f"- Length: `{before_target.fields[14]} m`; quantity: `{before_target.fields[15]}`",
            f"- Stress state: `{before_target.fields[45]}`",
            "- Axis selector: `отн. X-X`",
            "- Mx: `10.00 kN*m`",
            "- Q: `2.32 kN`",
            "- Heating: LEFT + RIGHT + BOTTOM active; TOP inactive",
            "- Required fire resistance: `R60`",
            f"- Fire regime: `{before_target.fields[104]}`",
            "- epsilon0=0.800; epsilon=1.000; Phi=1.000; alpha_c=25.00; kf=1.000",
            "- Plastic-region mode: enabled",
            "- EN classification: disabled",
            "- W/Wpl selection: unchanged from the template",
            "- `use tred*`: disabled",
            "",
            "Existing theta_cr/R0 shown before Calculate may be stale template results. Do not interpret them as new output.",
            "",
        ]
    )
    checklist = "\n".join(
        [
            "# RX3-EXP-02B CHECKLIST — PRE-CALC GUI",
            "",
            f"- [ ] Generated SHA-256 equals `{generated_sha}`.",
            f"- [ ] Selected mark is `{mark}`; profile is `{before_target.fields[19]}`.",
            "- [ ] Stress state is one-plane bending; axis is `отн. X-X`.",
            "- [ ] Mx is exactly `10.00 kN*m`.",
            "- [ ] Q is exactly `2.32 kN`.",
            "- [ ] LEFT, RIGHT and BOTTOM heating arrows are active; TOP is inactive.",
            "- [ ] R60, standard fire regime and all thermal coefficients are unchanged.",
            "- [ ] Plastic-region, W/Wpl and EN-classification states are unchanged.",
            "- [ ] Calculate/Recalculate has NOT been pressed.",
            "- [ ] Project/table has NOT been saved.",
            "",
            "STOP on any mismatch. Do not calculate.",
            "",
        ]
    )
    instructions = "\n".join(
        [
            "# RX3-EXP-02B — manual pre-calc verification",
            "",
            f"1. Verify `generated_MX10.rx38` SHA-256 is `{generated_sha}`.",
            "2. Open only `generated_MX10.rx38` in the original RX3 and select mark `Б1`.",
            "3. Open the calculation dialog and compare every item in `EXPECTED_RX3_GUI_VALUES.md`.",
            "4. Complete `CHECKLIST_PRECALC.md` and capture the full dialog; do not press Calculate/Recalculate.",
            "5. Close without Save to table or project save and return the observation/screenshot.",
            "",
            "Stop boundary: this bundle is for pre-calc GUI verification only.",
            "",
        ]
    )

    _write_new(project_path, json.dumps(project_payload, ensure_ascii=False, indent=2) + "\n")
    _write_new(profile_path, json.dumps(profile_payload, ensure_ascii=False, indent=2) + "\n")
    _write_new(heating_path, json.dumps(heating_payload, ensure_ascii=False, indent=2) + "\n")
    _write_new(diff_json_path, json.dumps(diff_payload, ensure_ascii=False, indent=2) + "\n")
    _write_new(diff_markdown_path, diff_markdown)
    _write_new(expected_path, expected_gui)
    _write_new(checklist_path, checklist)
    _write_new(instructions_path, instructions)
    _write_new(audit_path, json.dumps(audit_payload, ensure_ascii=False, indent=2) + "\n")
    return Rx3BendingMxValidationBundle(
        directory,
        template,
        generated,
        project_path,
        profile_path,
        heating_path,
        diff_json_path,
        diff_markdown_path,
        expected_path,
        checklist_path,
        instructions_path,
        audit_path,
        generated_sha,
    )
