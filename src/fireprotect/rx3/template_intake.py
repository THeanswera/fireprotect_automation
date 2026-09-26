"""Read-only intake of an engineer-created RX3 baseline for a new experiment.

A new controlled experiment needs one construction that the *engineer* created in
RX3 for the profile of a real project element: section data, length, steel,
heating and fire regime are engineering inputs and are never fabricated here.

This module therefore does exactly one thing: it freezes a byte-identical copy of
the file the engineer produced, proves that its single target construction
matches an *explicitly declared* identity, the RX3 assortment database and the
RX38 identity relations, and writes a GUI observation checklist.  It does not
register a controlled scope, does not write a perturbed RX38, does not start
RX3 and does not open any production gate.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

from .parser import Rx38Construction, Rx38FormatError, Rx38Record, read_rx38_document
from .profiles import ProfileRepository, normalize_profile_name, normalize_standard
from .safety import rx38_record_fingerprint
from .schema import field_spec

INTAKE_KIND = "RX3_BASELINE_OBSERVATION"
STATUS_WAITING = "WAITING_FOR_BASELINE_GUI_OBSERVATION"

DEFAULT_FIRE_REGIME_FRAGMENT = "стандартн"

# Relative tolerance for the decimal identities that RX3 stores as rounded
# tokens (reduced thickness A/P and the section factor P/A).  The absolute
# tolerance is used for the geometry values taken from the assortment database.
RELATIVE_TOLERANCE = Decimal("1e-12")
GEOMETRY_TOLERANCE = Decimal("0.001")

# Fields reported verbatim for the engineer's on-screen comparison.
OBSERVED_FIELDS: tuple[tuple[int, str], ...] = (
    (1, "mark"),
    (3, "mark_copy"),
    (5, "section_type"),
    (8, "height_mm"),
    (9, "width_mm"),
    (11, "web_thickness_mm"),
    (13, "flange_thickness_mm"),
    (14, "length_m"),
    (15, "quantity"),
    (17, "profile_standard"),
    (19, "profile_name"),
    (20, "area_mm2"),
    (21, "heated_perimeter_mm"),
    (22, "ptm_mm"),
    (23, "section_factor_per_m"),
    (26, "ix_m4"),
    (27, "iy_m4"),
    (29, "wx_m3"),
    (30, "wy_m3"),
    (32, "steel_density_kg_m3"),
    (33, "steel_yield_strength_mpa"),
    (34, "steel_elastic_modulus_mpa"),
    (42, "steel_grade"),
    (44, "critical_temperature_c"),
    (45, "stress_state"),
    (46, "stress_state_code"),
    (47, "support_condition_code"),
    (48, "support_condition"),
    (49, "axial_force_kn"),
    (50, "major_axis_moment_knm"),
    (51, "effective_length_m"),
    (52, "load_level_mu0"),
    (54, "unprotected_fire_resistance_min"),
    (55, "required_fire_resistance_min"),
    (61, "loading_axis"),
    (72, "fireproofing_material"),
    (79, "rx3_gui_minor_axis_moment_input_knm"),
    (82, "convection_coefficient"),
    (83, "flame_emissivity"),
    (84, "view_factor"),
    (85, "shadow_effect_factor"),
    (92, "rx3_gui_q_input_kn"),
    (104, "fire_regime"),
    (141, "effective_length_factor"),
    (188, "steel_temperature_model"),
)


class Rx3BaselineError(ValueError):
    """Raised when the declared baseline identity cannot be proven."""


@dataclass(frozen=True, slots=True)
class BaselineExpectation:
    """Everything the caller must declare before the file is even read."""

    mark: str
    standard: str
    designation: str
    length_m: Decimal
    stress_state: str
    required_fire_resistance_min: Decimal
    fire_regime_fragment: str = DEFAULT_FIRE_REGIME_FRAGMENT

    def __post_init__(self) -> None:
        for name in ("mark", "standard", "designation", "stress_state"):
            value = getattr(self, name)
            if not isinstance(value, str) or not value.strip():
                raise Rx3BaselineError(f"{name} must be a non-empty declaration")
        if self.length_m <= 0:
            raise Rx3BaselineError("length_m must be positive")
        if self.required_fire_resistance_min <= 0:
            raise Rx3BaselineError("required_fire_resistance_min must be positive")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _decimal(token: str) -> Decimal:
    text = token.strip().replace(",", ".")
    if not text:
        raise Rx3BaselineError("expected a decimal token, got an empty field")
    try:
        value = Decimal(text)
    except InvalidOperation as exc:  # pragma: no cover - defensive
        raise Rx3BaselineError(f"{token!r} is not a decimal token") from exc
    if not value.is_finite():
        raise Rx3BaselineError(f"{token!r} is not finite")
    return value


def _close(actual: Decimal, expected: Decimal, tolerance: Decimal) -> bool:
    return abs(actual - expected) <= tolerance


def _relative(actual: Decimal, expected: Decimal) -> bool:
    if expected == 0:
        return actual == 0
    return abs(actual - expected) <= abs(expected) * RELATIVE_TOLERANCE


def _record_fingerprint(record: Rx38Record) -> str:
    """Fingerprint any record; the 200-field Tconstr fingerprint is the official one."""

    if record.record_type == "Tconstr" and len(record.fields) == 200:
        return rx38_record_fingerprint(record)
    payload = "\0".join(record.fields).encode("utf-8")
    return "sha256:" + hashlib.sha256(payload).hexdigest()


def _target_record(document: Any, mark: str) -> tuple[int, Rx38Record]:
    matches = [
        (position, record)
        for position, record in enumerate(document.records, 1)
        if record.record_type == "Tconstr" and record.fields[1].strip() == mark
    ]
    if not matches:
        raise Rx3BaselineError(
            f"no Tconstr record carries the declared mark {mark!r}"
        )
    if len(matches) > 1:
        positions = ", ".join(str(position) for position, _ in matches)
        raise Rx3BaselineError(
            f"the declared mark {mark!r} resolves to {len(matches)} records "
            f"(positions {positions}); the baseline must be unambiguous"
        )
    return matches[0]


def _check_identity_relations(record: Rx38Record) -> list[dict[str, object]]:
    """Prove the RX38 relations that the corpus documents for Tconstr records."""

    checks: list[dict[str, object]] = []

    def add(name: str, actual: object, expected: object, ok: bool) -> None:
        checks.append(
            {
                "check": name,
                "actual": str(actual),
                "expected": str(expected),
                "status": "PASS" if ok else "FAIL",
            }
        )

    add(
        "field3_equals_field1",
        record.fields[3],
        record.fields[1],
        record.fields[3] == record.fields[1],
    )
    area = _decimal(record.fields[20])
    perimeter = _decimal(record.fields[21])
    length = _decimal(record.fields[14])
    quantity = _decimal(record.fields[15])
    ptm = _decimal(record.fields[22])
    section_factor = _decimal(record.fields[23])
    add(
        "field22_equals_area_over_perimeter",
        ptm,
        area / perimeter,
        _relative(ptm, area / perimeter),
    )
    add(
        "field23_equals_1000_over_field22",
        section_factor,
        Decimal(1000) / ptm,
        _relative(section_factor, Decimal(1000) / ptm),
    )
    contour_one = _decimal(record.fields[24])
    contour_total = _decimal(record.fields[25])
    add(
        "field24_equals_perimeter_times_length",
        contour_one,
        perimeter * length / 1000,
        _relative(contour_one, perimeter * length / 1000),
    )
    add(
        "field25_equals_field24_times_quantity",
        contour_total,
        contour_one * quantity,
        _relative(contour_total, contour_one * quantity),
    )
    add(
        "field20_equals_1000_times_field22_ratio",
        area,
        ptm * perimeter,
        _relative(area, ptm * perimeter),
    )
    density = _decimal(record.fields[32])
    mass_one = _decimal(record.fields[66])
    mass_total = _decimal(record.fields[67])
    computed_mass = area * length * density / Decimal(1000000)
    add(
        "field66_equals_area_times_length_times_density",
        mass_one,
        computed_mass,
        _relative(mass_one, computed_mass),
    )
    add(
        "field67_equals_field66_times_quantity",
        mass_total,
        mass_one * quantity,
        _relative(mass_total, mass_one * quantity),
    )
    steel = record.fields[42].strip()
    match = re.search(r"(\d+(?:[.,]\d+)?)\s*$", steel)
    stored = _decimal(record.fields[33])
    parsed = Decimal(match.group(1).replace(",", ".")) if match else None
    add(
        "steel_grade_numeric_tail_correlates_with_field33",
        stored,
        parsed if parsed is not None else "no numeric grade tail",
        parsed is not None and parsed == stored,
    )
    return checks


def _check_assortment(
    record: Rx38Record, profile_db: Path
) -> dict[str, object]:
    """Prove the record against the RX3 assortment database, or refuse."""

    repository = ProfileRepository(profile_db)
    hash_before_read = _sha256(profile_db)
    result = repository.search(record.fields[19], record.fields[17])
    if result.status == "NOT_FOUND":
        raise Rx3BaselineError(
            f"the assortment database has no row for standard {record.fields[17]!r} "
            f"and designation {record.fields[19]!r}"
        )
    if result.status == "AMBIGUOUS":
        tables = ", ".join(sorted({candidate.table for candidate in result.candidates}))
        raise Rx3BaselineError(
            f"standard {record.fields[17]!r} and designation {record.fields[19]!r} "
            f"resolve to {len(result.candidates)} assortment rows ({tables}); "
            "an ambiguous profile is refused instead of being ranked"
        )
    candidate = result.candidate
    assert candidate is not None
    geometry = candidate.geometry
    checks: list[dict[str, object]] = []

    def compare(name: str, actual: Decimal, expected: Decimal | None, factor: Decimal) -> None:
        if expected is None:
            raise Rx3BaselineError(
                f"assortment row {candidate.table}/{candidate.designation} has no "
                f"{name}; the identity cannot be proven"
            )
        checks.append(
            {
                "check": name,
                "record_value": str(actual),
                "assortment_value": str(expected),
                "factor": str(factor),
                "status": "PASS" if _relative(actual, expected * factor) else "FAIL",
            }
        )

    for index, name, expected in (
        (8, "height_mm", geometry.height_mm),
        (9, "width_mm", geometry.width_mm),
        (11, "web_thickness_mm", geometry.web_thickness_mm),
        (13, "flange_thickness_mm", geometry.flange_thickness_mm),
    ):
        if expected is None:
            raise Rx3BaselineError(f"assortment row has no {name}")
        actual = _decimal(record.fields[index])
        checks.append(
            {
                "check": name,
                "record_value": str(actual),
                "assortment_value": str(expected),
                "factor": "1",
                "status": "PASS"
                if _close(actual, expected, GEOMETRY_TOLERANCE)
                else "FAIL",
            }
        )
    compare("area_mm2", _decimal(record.fields[20]), geometry.area_cm2, Decimal(100))
    compare("ix_m4", _decimal(record.fields[26]), geometry.ix_cm4, Decimal("1e-8"))
    compare("iy_m4", _decimal(record.fields[27]), geometry.iy_cm4, Decimal("1e-8"))
    compare("wx_m3", _decimal(record.fields[29]), geometry.wx_cm3, Decimal("1e-6"))
    compare("wy_m3", _decimal(record.fields[30]), geometry.wy_cm3, Decimal("1e-6"))
    if hash_before_read != _sha256(profile_db):
        raise Rx3BaselineError(
            "the assortment database changed while it was being read: the recorded "
            "checks would belong to a different version of the database; re-run the "
            "intake on an unchanged file"
        )
    return {
        "database": str(profile_db),
        "sha256": hash_before_read,
        "sha256_after_read": _sha256(profile_db),
        "table": candidate.table,
        "standard": candidate.standard,
        "designation": candidate.designation,
        "checks": checks,
    }


def _fail(checks: list[dict[str, object]], context: str) -> None:
    failed = [item for item in checks if item["status"] != "PASS"]
    if failed:
        names = ", ".join(str(item["check"]) for item in failed)
        raise Rx3BaselineError(f"{context}: {len(failed)} identity checks failed ({names})")


def _observed(record: Rx38Record) -> dict[str, dict[str, object]]:
    observed: dict[str, dict[str, object]] = {}
    for index, name in OBSERVED_FIELDS:
        spec = field_spec(index)
        observed[name] = {
            "rx38_position": index + 1,
            "rx38_index": index,
            "schema_name": spec.name,
            "stored_token": record.fields[index],
            "units": spec.units,
            "confidence": spec.confidence,
            "write_policy": spec.write_policy.value,
        }
    return observed


def _checklist(
    expectation: BaselineExpectation,
    source: Path,
    source_sha: str,
    target_position: int,
    observed: dict[str, dict[str, object]],
    records: list[dict[str, object]],
) -> str:
    def token(name: str) -> str:
        return str(observed[name]["stored_token"])

    lines = [
        "# Контрольный лист: базовый файл RX3 для нового опыта",
        "",
        f"Файл-копия: `{source.name}` (SHA-256 `{source_sha}`)",
        f"Целевая запись: позиция {target_position} из {len(records)}",
        "",
        "## Что делает инженер",
        "",
        "1. Открыть **именно копию** из этого каталога в RX3 (исходный файл не трогать).",
        "2. Ничего не менять и **не нажимать «Рассчитать»** на этом шаге: это только",
        "   наблюдение. Расчёт будет отдельным шагом B.",
        "3. Сверить на экране значения из таблицы ниже и записать фактические.",
        "4. Сообщить: совпало/не совпало по каждому пункту, и приложить скриншот.",
        "",
        "## Что должно быть на экране (из замороженной копии)",
        "",
        "| Что | Ожидаемое значение | На экране (заполняет инженер) |",
        "|---|---|---|",
        f"| Марка | `{token('mark')}` | |",
        f"| Вид сечения | `{token('section_type').strip()}` | |",
        f"| Сортамент | `{token('profile_standard')}` | |",
        f"| Номер профиля | `{token('profile_name')}` | |",
        f"| Размеры h×b | `{token('height_mm')}` × `{token('width_mm')}` мм | |",
        f"| Стенка/полка | `{token('web_thickness_mm')}` / `{token('flange_thickness_mm')}` мм | |",
        f"| Длина | `{token('length_m')}` м | |",
        f"| Количество | `{token('quantity')}` шт | |",
        f"| Марка стали | `{token('steel_grade')}` | |",
        f"| Вид нагружения | `{token('stress_state')}` | |",
        f"| Вид опирания | `{token('support_condition')}` | |",
        f"| Коэффициент расчётной длины | `{token('effective_length_factor')}` | |",
        f"| Температурный режим | `{token('fire_regime')}` | |",
        f"| Требуемый предел огнестойкости | `{token('required_fire_resistance_min')}` мин | |",
        f"| Огнезащитный материал | `{token('fireproofing_material')}` | |",
        f"| N | `{token('axial_force_kn')}` кН | |",
        f"| Mx (поле 50) | `{token('major_axis_moment_knm')}` кН·м | |",
        f"| My (поле 79) | `{token('rx3_gui_minor_axis_moment_input_knm')}` кН·м | |",
        f"| Q (поле 92) | `{token('rx3_gui_q_input_kn')}` кН | |",
        f"| θcr (поле 44, результат) | `{token('critical_temperature_c')}` °C | |",
        f"| R0 (поле 54, результат) | `{token('unprotected_fire_resistance_min')}` мин | |",
        "",
        "## Заявленная идентичность (проверена программой)",
        "",
        f"- сортамент: `{expectation.standard}`",
        f"- профиль: `{expectation.designation}`",
        f"- длина: `{expectation.length_m}` м",
        f"- вид нагружения: `{expectation.stress_state}`",
        f"- требуемый предел огнестойкости: `{expectation.required_fire_resistance_min}` мин",
        "",
        "## Чего этот шаг не делает",
        "",
        "- не разрешает расчёт и не открывает выпуск (`NOT_READY_FOR_ISSUE`);",
        "- не подтверждает нормативную применимость и корректность результата;",
        "- не меняет исходный файл: копия побайтово идентична источнику.",
        "",
        "## Все записи файла (для последующей проверки неизменности)",
        "",
        "| Позиция | Марка | Отпечаток записи |",
        "|---|---|---|",
    ]
    for item in records:
        lines.append(f"| {item['position']} | {item['mark']} | `{item['fingerprint']}` |")
    lines.append("")
    return "\n".join(lines)


def prepare_rx3_baseline_observation(
    *,
    file: str | Path,
    expectation: BaselineExpectation,
    profile_db: str | Path = Path("rx3") / "rx3.rxdb",
    output_dir: str | Path,
) -> dict[str, Any]:
    """Freeze one engineer-created RX3 baseline and check its declared identity."""

    source = Path(file).resolve(strict=True)
    if source.suffix.casefold() != ".rx38":
        raise Rx3BaselineError(f"the baseline must be an .rx38 file: {source}")
    destination = Path(output_dir).resolve(strict=False)
    if destination.exists():
        raise Rx3BaselineError(f"output directory already exists: {destination}")
    if destination == source.parent or destination in source.parents:
        raise Rx3BaselineError("the output directory must not contain the source file")
    database = Path(profile_db).resolve(strict=True)

    source_sha = _sha256(source)
    try:
        document = read_rx38_document(source)
    except Rx38FormatError as exc:
        raise Rx3BaselineError(f"cannot read the baseline as RX38: {exc}") from exc
    position, record = _target_record(document, expectation.mark)
    try:
        construction = Rx38Construction.from_record(record)
    except Rx38FormatError as exc:
        raise Rx3BaselineError(f"the target record is not a typed construction: {exc}") from exc

    declarations: list[dict[str, object]] = [
        {
            "check": "standard",
            "expected": expectation.standard,
            "actual": construction.profile_standard,
            "status": "PASS"
            if normalize_standard(construction.profile_standard)
            == normalize_standard(expectation.standard)
            else "FAIL",
        },
        {
            "check": "designation",
            "expected": expectation.designation,
            "actual": construction.profile_name,
            "status": "PASS"
            if normalize_profile_name(construction.profile_name)
            == normalize_profile_name(expectation.designation)
            else "FAIL",
        },
        {
            "check": "length_m",
            "expected": str(expectation.length_m),
            "actual": str(construction.length_m),
            "status": "PASS" if construction.length_m == expectation.length_m else "FAIL",
        },
        {
            "check": "stress_state",
            "expected": expectation.stress_state,
            "actual": construction.stress_state,
            "status": "PASS"
            if construction.stress_state.strip() == expectation.stress_state.strip()
            else "FAIL",
        },
        {
            "check": "required_fire_resistance_min",
            "expected": str(expectation.required_fire_resistance_min),
            "actual": str(construction.required_fire_resistance_min),
            "status": "PASS"
            if construction.required_fire_resistance_min
            == expectation.required_fire_resistance_min
            else "FAIL",
        },
        {
            "check": "fire_regime",
            "expected": f"contains {expectation.fire_regime_fragment!r}",
            "actual": construction.fire_regime,
            "status": "PASS"
            if expectation.fire_regime_fragment in construction.fire_regime.lower()
            else "FAIL",
        },
    ]
    _fail(declarations, "the record does not match the declared identity")

    relations = _check_identity_relations(record)
    _fail(relations, "the record violates the RX38 identity relations")
    assortment = _check_assortment(record, database)
    assortment_checks = assortment["checks"]
    assert isinstance(assortment_checks, list)
    _fail(assortment_checks, "the record does not match the RX3 assortment database")

    records: list[dict[str, object]] = [
        {
            "position": index,
            "record_type": item.record_type,
            "mark": item.fields[1] if len(item.fields) > 1 else "",
            "fingerprint": _record_fingerprint(item),
            "line_number": item.line_number,
            "is_target": index == position,
        }
        for index, item in enumerate(document.records, 1)
    ]
    observed = _observed(record)

    destination.mkdir(parents=True, exist_ok=False)
    copy_path = destination / source.name
    copy_path.write_bytes(source.read_bytes())
    copy_sha = _sha256(copy_path)
    if copy_sha != source_sha:
        raise Rx3BaselineError("the frozen copy is not byte-identical to the source")
    if _sha256(source) != source_sha:
        raise Rx3BaselineError("the source file changed while it was being read")

    checklist_path = destination / "CHECKLIST.md"
    checklist_path.write_text(
        _checklist(expectation, copy_path, copy_sha, position, observed, records),
        encoding="utf-8",
        newline="\n",
    )
    report: dict[str, Any] = {
        "kind": INTAKE_KIND,
        "status": STATUS_WAITING,
        "source": {
            "path": str(source),
            "sha256": source_sha,
            "size": source.stat().st_size,
            "encoding": document.encoding,
            "has_bom": document.has_bom,
            "record_count": len(document.records),
        },
        "frozen_copy": {"path": str(copy_path), "sha256": copy_sha},
        "target": {
            "position": position,
            "line_number": record.line_number,
            "fingerprint": rx38_record_fingerprint(record),
            "raw_fields": list(record.fields),
        },
        "declared_identity": declarations,
        "identity_relations": relations,
        "assortment": assortment,
        "check_counts": {
            "declared_identity": len(declarations),
            "identity_relations": len(relations),
            "assortment": len(assortment_checks),
        },
        "identity_relations_scope": (
            "These checks prove the internal consistency of the stored construction and its "
            "equality with the RX3 assortment database. They are not a steel-strength "
            "verification: the numeric tail of the steel grade only correlates with the stored "
            "yield strength and says nothing about the characteristic required for the actual "
            "thickness, standard or conditions."
        ),
        "engineer_observation_recorded": False,
        "engineer_observation": None,
        "engineer_confirmation_note": (
            "The existence of this file is not an observation. The engineer must open the frozen "
            "copy in RX3, compare the displayed values with CHECKLIST.md and state the result; "
            "only that statement can be recorded as an observation, and no name is written by "
            "the program"
        ),
        "observed_fields": observed,
        "records": records,
        "next_step": (
            "engineer opens the frozen copy in RX3, compares the displayed values "
            "with CHECKLIST.md and reports the result; calculation stays closed "
            "until that observation is recorded"
        ),
        "calculation_gate": "CLOSED_UNTIL_ENGINEER_OBSERVATION",
        "controlled_scope_registered": False,
        "rx38_force_generation_allowed": False,
        "issue_readiness": "NOT_READY_FOR_ISSUE",
        "written_files": [str(copy_path), str(checklist_path)],
    }
    (destination / "baseline_observation.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    return report
