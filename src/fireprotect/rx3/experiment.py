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
from typing import Any, Iterable

from ..project_io import project_element_from_dict, project_element_to_dict
from .parser import Rx38Record, construction_records, read_rx38
from .profiles import ProfileCandidate, ProfileRepository
from .safety import rx38_record_fingerprint


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
