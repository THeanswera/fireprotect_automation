"""VALIDATION-only preparation of one RX3 input from a verified LIRA bar run.

This module is deliberately not a general RX38 writer. It performs exactly one
transformation, in the scope that was already validated by the controlled
`LIRA-RX3-22P-XX-MAGNITUDE` experiment:

* one existing ``Tconstr`` record of the controlled template, identified by
  mark, standard, profile, length *and* by the confirmed stress-state, required
  fire resistance and fire-regime fields of that record, is the only target;
* only fields 50 (``major_axis_moment_knm``) and 92 (``rx3_gui_q_input_kn``)
  may receive a new token, and only from values that were re-derived from the
  re-read LIRA tables and the bound RSU evidence through the resolved
  ``MAGNITUDE`` convention — never from the stored JSON;
* the stored run JSON is used only to detect substitution: every one of the six
  components, its units and its convention must match what the sources say, and
  the declaration must still be bound to the recorded evidence revision;
* every other record and every other field of the target record stays
  byte-identical, which is re-checked after writing;
* nothing is overwritten, and the source template is never modified.

The produced file is a *test input*: it opens no production gate, it does not
claim a signature, and it is not a permission to calculate.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Mapping

from ..execution import ExecutionMode
from ..lira.errors import LiraFormatError, LiraMappingError
from ..lira.run import VerifiedBarRun, read_verified_bar_run
from .experiment import single_plane_bending_label
from .parser import Rx38Document, Rx38FormatError, Rx38Record, read_rx38_document
from .safety import rx38_record_fingerprint
from .schema import FIELD_SPECS

PREP_KIND = "RX3_LIRA_BAR_VALIDATION_PREP"
STATUS_PREPARED = "VALIDATION_INPUT_PREPARED"
EXPERIMENT_INPUT_KIND = "LIRA_BAR_EXPERIMENT_INPUT"

# The exact validated scope. Anything else is refused instead of approximated.
REQUIRED_STANDARD = "ГОСТ 8240-97"
REQUIRED_PROFILE = "22П"
REQUIRED_TEMPLATE = "Б2"
REQUIRED_STRESS_STATE = "ONE_PLANE_BENDING"
REQUIRED_LENGTH_M = Decimal("3.00")
REQUIRED_ROTATION = Decimal("0")

# The template that was the object of the controlled experiment for this exact
# scope. A different file is refused even if its record looks similar: the
# scoped evidence belongs to this template.
CONTROLLED_TEMPLATE_SHA256 = (
    "a7511f61db14dc1587cc9acfe4cddfd0bf314a1577d59e924f2a6fba04c55413"
)

# RX3 component target -> (RX38 field index, required review unit)
TARGET_FIELDS: Mapping[str, tuple[int, str]] = {
    "FIELD50_MAX_MAJOR_AXIS_MOMENT": (50, "kN*m"),
    "FIELD92_MAX_SHEAR_Q": (92, "kN"),
}
REQUIRED_COMPONENTS = ("N", "Mk", "My", "Mz", "Qy", "Qz")
_PREP_FILES = (
    "rx3_validation_manifest.json",
    "CHECKPOINT_RX3.md",
)
_STANDARD_REGIME_FRAGMENT = "стандартн"


# Primary sources that describe the selected loading mode. The claim below is
# deliberately narrow: it is about what the shipped calculation document says
# about this algorithm, not about the program's implementation and not about
# the engineering applicability of the method.
CALCULATION_DOCUMENT = {
    "path": "rx3/doc/pages/pr.pdf",
    "sha256": "326e4b8bc87038a338ec9e3503b554624cdafc8f24bb7988575812c1ba31f615",
    "title": (
        "Проект НД для Rx3. Инструкция. Определение пределов огнестойкости "
        "стальных строительных конструкций с огнезащитным покрытием"
    ),
    "section": "Раздел 4 «Определение критической температуры»",
    "bending_subsection": (
        "подраздел «Изгибаемый стержень в одной из главных плоскостей», "
        "формулы (3) и (4)"
    ),
    "compression_subsection": (
        "подраздел «Сжатый стержень», формулы (8)–(13): λ = L / i_min, где "
        "«L — расчетная длина стержня, в зависимости от вида опирания и длины "
        "стержня L0»"
    ),
}
INTERFACE_HELP = {
    "path": "rx3/doc/pages/windowpredel.html",
    "sha256": "1e449a96d88f22fd2ece40c992861e2a5b52e331e1fadb11916821fa662c9e3f",
    "section": (
        "Окно «Расчет предела огнестойкости стальных конструкций», блок 6 "
        "«Данные для статического расчета», пп. 6.1 (список вида нагружения), "
        "6.3 (переключатель «прочность/устойчивость»), 6.4 (список видов "
        "опирания), 6.5 (коэффициент для определения расчётной длины)"
    ),
}
ENGINEERING_LIMITS = (
    "Проект НД для Rx3 не является утверждённым нормативным документом; его "
    "нормативная применимость не подтверждена.",
    "Тождество расчётного алгоритма программы RX3 и формул проекта НД не "
    "верифицировано: это приложение к справке, а не документация реализации.",
    "Выбор вида нагружения и переключателя «прочность/устойчивость» остаётся "
    "инженерным решением; запись классифицируется по подтверждённому полю 45, "
    "а поле кода 46 остаётся probable и не интерпретируется.",
    "Приложение 4 требует постоянного поперечного сечения по длине; "
    "принадлежность элемента к элементарной стержневой конструкции в составе "
    "реальной конструкции проверяет инженер.",
)
TECHNICAL_SCOPE = (
    "Перенос данных проверен: строка РСУ, единицы, знаки, происхождение, "
    "цепочка КЭ и запись шаблона.",
    "Проверено, что изменены только поля 50 и 92 одной записи, а остальные "
    "записи не изменены.",
)
ENGINEERING_NOT_ASSERTED = (
    "Корректность инженерного расчёта огнестойкости этим пакетом не "
    "доказывается.",
    "Нормативная применимость методики и пригодность результата для выпуска "
    "не подтверждены.",
    "Фактический результат RX3 не проверен: подтверждение даёт инженер.",
)


class Rx3LiraBarPrepError(ValueError):
    """Raised when the narrow preparation contract cannot be satisfied."""


def _sha256_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(65536), b""):
            digest.update(block)
    return digest.hexdigest()


def _read_json(path: Path, description: str) -> Mapping[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, Mapping):
        raise Rx3LiraBarPrepError(f"{path}: {description} must be a JSON object")
    return payload


def _decimal(token: object, *, name: str, context: str) -> Decimal:
    if isinstance(token, bool) or token is None:
        raise Rx3LiraBarPrepError(f"{context}: {name} is not a decimal value")
    try:
        value = Decimal(str(token).strip().replace(",", "."))
    except InvalidOperation as exc:
        raise Rx3LiraBarPrepError(f"{context}: {name} is not a number") from exc
    if not value.is_finite():
        raise Rx3LiraBarPrepError(f"{context}: {name} must be finite")
    return value


def decimal_token(value: Decimal) -> str:
    """Render a decimal the way the RX38 template family stores it.

    Trailing zeros are dropped so that a zero stays ``0`` and not ``0,000``;
    ``format(value, "f")`` keeps the plain notation without an exponent.
    """

    normalized = value.normalize()
    if normalized == 0:
        normalized = Decimal("0")
    return format(normalized, "f").replace(".", ",")


def _normalized(value: object) -> str:
    return " ".join(str(value).split()).casefold().replace("ё", "е")


def required_fire_resistance_minutes(token: object, *, context: str) -> int:
    """Convert a declared requirement such as ``R15`` into minutes."""

    if not isinstance(token, str) or not token.strip():
        raise Rx3LiraBarPrepError(
            f"{context}: required fire resistance is not declared"
        )
    text = token.strip().upper()
    if text.startswith("R"):
        text = text[1:].strip()
    if not text.isdigit():
        raise Rx3LiraBarPrepError(
            f"{context}: required fire resistance {token!r} is not a minute value"
        )
    return int(text)


@dataclass(frozen=True, slots=True)
class TargetRecord:
    record: Rx38Record
    position: int
    line_index: int


def _find_target(
    document: Rx38Document,
    *,
    mark: str,
    standard: str,
    profile: str,
    length_m: Decimal,
    required_minutes: int,
    fire_regime: str,
    context: str,
) -> TargetRecord:
    """Find exactly one compatible record; ambiguity is refused.

    Compatibility is not identity alone: the record must also carry the
    confirmed stress state, required fire resistance and fire regime that the
    experiment declares, so a similarly named record of another mode is never
    used.
    """

    matches: list[tuple[int, int, Rx38Record]] = []
    tconstr_position = 0
    for line_index, record in enumerate(document.records):
        if record.record_type != "Tconstr" or len(record.fields) != 200:
            continue
        tconstr_position += 1
        if not (
            record.fields[1].strip() == mark
            and record.fields[17].strip() == standard
            and record.fields[19].strip() == profile
            and _decimal(record.fields[14], name="length_m", context=context)
            == length_m
        ):
            continue
        if not single_plane_bending_label(record.fields[45]):
            raise Rx3LiraBarPrepError(
                f"{context}: record {record.fields[1].strip()!r} carries stress "
                f"state {record.fields[45]!r}, which is not the confirmed "
                "one-plane bending mode of this experiment"
            )
        record_minutes = _decimal(
            record.fields[55], name="required_fire_resistance_min", context=context
        )
        if record_minutes != required_minutes:
            raise Rx3LiraBarPrepError(
                f"{context}: record {record.fields[1].strip()!r} requires "
                f"{record_minutes} min, the experiment declares "
                f"{required_minutes} min"
            )
        if _normalized(fire_regime) != "standard":
            raise Rx3LiraBarPrepError(
                f"{context}: fire regime {fire_regime!r} is outside the "
                "supported standard regime"
            )
        if _STANDARD_REGIME_FRAGMENT not in _normalized(record.fields[104]):
            raise Rx3LiraBarPrepError(
                f"{context}: record {record.fields[1].strip()!r} carries fire "
                f"regime {record.fields[104]!r}, not the declared standard regime"
            )
        matches.append((tconstr_position, line_index, record))
    if not matches:
        raise Rx3LiraBarPrepError(
            f"{context}: no Tconstr record matches {mark!r} / {standard!r} / "
            f"{profile!r} / {length_m} m; the template is not compatible with "
            "this experiment"
        )
    if len(matches) > 1:
        raise Rx3LiraBarPrepError(
            f"{context}: {len(matches)} records match {mark!r} / {standard!r} / "
            f"{profile!r}; the target is not unique"
        )
    position, line_index, record = matches[0]
    if record.line_number is None:
        raise Rx3LiraBarPrepError(
            f"{context}: the target record has no source line number"
        )
    return TargetRecord(record=record, position=position, line_index=line_index)


def _cross_check_experiment_input(
    path: Path, verified: VerifiedBarRun
) -> None:
    """Prove the stored experiment input still describes the re-read sources.

    The preparer never takes values from this file. It reads the file only to
    detect that a component, a unit, a convention or a value was substituted
    after the run was prepared.
    """

    stored = _read_json(path, "experiment input")
    context = str(path)
    if stored.get("kind") != EXPERIMENT_INPUT_KIND:
        raise Rx3LiraBarPrepError(
            f"{context}: kind must be {EXPERIMENT_INPUT_KIND!r}"
        )
    if stored.get("status") != "EXPERIMENT_INPUT_READY":
        raise Rx3LiraBarPrepError(
            f"{context}: status is {stored.get('status')!r}; a blocked transfer is "
            "never turned into a calculation input"
        )
    stored_components = stored.get("components")
    if not isinstance(stored_components, list):
        raise Rx3LiraBarPrepError(f"{context}: components must be a list")
    stored_bar = stored.get("bar")
    stored_profile = stored.get("profile")
    stored_row = stored.get("selected_row")
    if not isinstance(stored_bar, Mapping) or not isinstance(
        stored_profile, Mapping
    ) or not isinstance(stored_row, Mapping):
        raise Rx3LiraBarPrepError(
            f"{context}: bar, profile and selected_row must be objects"
        )
    declaration = verified.declaration
    stored_elements = stored_bar.get("elements")
    if not isinstance(stored_elements, list):
        raise Rx3LiraBarPrepError(f"{context}: bar.elements must be a list")
    stored_element_ids = [
        item.get("element_id") if isinstance(item, Mapping) else None
        for item in stored_elements
    ]
    expected_identity: dict[str, tuple[object, object]] = {
        "bar.bar_id": (stored_bar.get("bar_id"), declaration.bar_id),
        "bar.elements": (
            stored_element_ids,
            list(verified.chain.ordered_element_ids),
        ),
        "bar.end_node_ids": (
            list(stored_bar.get("end_node_ids") or []),
            [verified.chain.start_node, verified.chain.end_node],
        ),
        "bar.bar_length_m": (
            _decimal(stored_bar.get("bar_length_m"), name="bar_length_m", context=context),
            verified.chain.bar_length_m,
        ),
        "profile.standard": (stored_profile.get("standard"), declaration.profile.standard),
        "profile.designation": (
            stored_profile.get("designation"),
            declaration.profile.designation,
        ),
        "profile.rotation_degrees": (
            _decimal(
                stored_profile.get("rotation_degrees"),
                name="rotation_degrees",
                context=context,
            ),
            declaration.profile.rotation_degrees,
        ),
        "profile.stress_state": (
            stored_profile.get("stress_state"),
            declaration.profile.stress_state,
        ),
        "selected_row.row_id": (stored_row.get("row_id"), verified.row.row_id),
    }
    for field, (stored_value, derived_value) in expected_identity.items():
        if stored_value != derived_value:
            raise Rx3LiraBarPrepError(
                f"{context}: {field} is {stored_value!r}, the re-read sources say "
                f"{derived_value!r}"
            )
    by_name: dict[str, Mapping[str, Any]] = {}
    for item in stored_components:
        if not isinstance(item, Mapping):
            raise Rx3LiraBarPrepError(f"{context}: malformed component entry")
        name = item.get("component")
        if not isinstance(name, str) or name not in REQUIRED_COMPONENTS:
            raise Rx3LiraBarPrepError(
                f"{context}: component {name!r} is not one of "
                f"{list(REQUIRED_COMPONENTS)}"
            )
        if name in by_name:
            raise Rx3LiraBarPrepError(
                f"{context}: component {name!r} appears more than once"
            )
        by_name[name] = item
    missing = [name for name in REQUIRED_COMPONENTS if name not in by_name]
    if missing:
        raise Rx3LiraBarPrepError(
            f"{context}: the signed vector is incomplete, missing {missing}"
        )
    derived = {str(item["component"]): item for item in verified.components}
    for name in REQUIRED_COMPONENTS:
        stored_item = by_name[name]
        derived_item = derived[name]
        for field in ("source_value", "source_unit", "review_value", "review_unit"):
            if str(stored_item.get(field)) != str(derived_item[field]):
                raise Rx3LiraBarPrepError(
                    f"{context}: component {name} field {field} is "
                    f"{stored_item.get(field)!r}, the re-read sources say "
                    f"{derived_item[field]!r}"
                )
        stored_convention = stored_item.get("convention")
        if not isinstance(stored_convention, Mapping):
            raise Rx3LiraBarPrepError(
                f"{context}: component {name} has no convention record"
            )
        derived_convention = derived_item["convention"]
        assert isinstance(derived_convention, Mapping)
        for field in ("resolved", "target", "value_transform"):
            if stored_convention.get(field) != derived_convention.get(field):
                raise Rx3LiraBarPrepError(
                    f"{context}: component {name} convention {field} is "
                    f"{stored_convention.get(field)!r}, the re-read sources say "
                    f"{derived_convention.get(field)!r}"
                )


def _requested_changes(
    verified: VerifiedBarRun, *, context: str
) -> tuple[dict[int, dict[str, object]], list[str], list[str]]:
    """Collect the field values the re-read sources actually ask for."""

    changes: dict[int, dict[str, object]] = {}
    unresolved_nonzero: list[str] = []
    unmapped_zero: list[str] = []
    for item in verified.components:
        name = str(item["component"])
        value = _decimal(
            item["source_value"], name=f"{name}.source_value", context=context
        )
        convention = item["convention"]
        assert isinstance(convention, Mapping)
        resolved = convention.get("resolved") is True
        if not resolved:
            # Unknown mappings are never written: a zero of this experiment must
            # not silently overwrite a field whose meaning is unproven.
            if value != 0:
                unresolved_nonzero.append(name)
            else:
                unmapped_zero.append(name)
            continue
        target = convention.get("target")
        if target not in TARGET_FIELDS:
            raise Rx3LiraBarPrepError(
                f"{context}: component {name} resolves to {target!r}, which is "
                "outside the validated field scope"
            )
        index, unit = TARGET_FIELDS[str(target)]
        transform = convention.get("value_transform")
        if transform != "MAGNITUDE":
            raise Rx3LiraBarPrepError(
                f"{context}: component {name} uses transform {transform!r}; only "
                "the validated MAGNITUDE transform may be prepared"
            )
        review_value = _decimal(
            item["review_value"], name=f"{name}.review_value", context=context
        )
        review_unit = item["review_unit"]
        if review_unit != unit:
            raise Rx3LiraBarPrepError(
                f"{context}: component {name} review unit is {review_unit!r}, "
                f"expected {unit!r}"
            )
        prepared = review_value.copy_abs()
        entry = {
            "field": index,
            "field_name": FIELD_SPECS[index].name,
            "unit": unit,
            "source_component": name,
            "source_value": str(value),
            "source_unit": item["source_unit"],
            "source_cell": item["source_cell"],
            "value_transform": transform,
            "review_value": str(review_value),
            "prepared_value": str(prepared),
        }
        existing = changes.get(index)
        if existing is not None and existing["prepared_value"] != str(prepared):
            raise Rx3LiraBarPrepError(
                f"{context}: two components would write different values into "
                f"field {index}"
            )
        changes[index] = entry
    return changes, unresolved_nonzero, unmapped_zero


def _render_document(
    document: Rx38Document,
    *,
    target_line: int,
    new_tokens: Mapping[int, str],
) -> bytes:
    """Replace named tokens on exactly one line, keeping every other byte."""

    lines: list[str] = []
    changed = False
    for record in document.records:
        tokens = list(record.raw_tokens)
        if record.line_number == target_line:
            if changed:
                raise Rx3LiraBarPrepError("target line is not unique")
            if record.record_type != "Tconstr" or len(tokens) != 200:
                raise Rx3LiraBarPrepError(
                    "target must be one parsed 200-field Tconstr record"
                )
            for index, token in new_tokens.items():
                if not token or any(char in token for char in ';"\r\n'):
                    raise Rx3LiraBarPrepError(
                        f"field {index}: token is not safe for verbatim insertion"
                    )
                tokens[index] = token
            changed = True
        lines.append(";".join(tokens) + record.newline)
    if not changed:
        raise Rx3LiraBarPrepError("the target record was not found while writing")
    payload = "".join(lines).encode(document.encoding)
    if document.has_bom and document.encoding == "utf-8":
        payload = b"\xef\xbb\xbf" + payload
    return payload


@dataclass(frozen=True, slots=True)
class Rx3LiraBarPrepBundle:
    """Files produced by one VALIDATION-only preparation."""

    output_dir: Path
    generated_path: Path
    manifest_path: Path
    checkpoint_path: Path
    manifest: Mapping[str, Any]


def _require_scope(
    verified: VerifiedBarRun, *, context: str
) -> Decimal:
    declaration = verified.declaration
    profile = declaration.profile
    expected = {
        "standard": REQUIRED_STANDARD,
        "designation": REQUIRED_PROFILE,
        "rx3_template": REQUIRED_TEMPLATE,
        "stress_state": REQUIRED_STRESS_STATE,
    }
    actual = {
        "standard": profile.standard,
        "designation": profile.designation,
        "rx3_template": profile.rx3_template,
        "stress_state": profile.stress_state,
    }
    for field, value in expected.items():
        if actual[field] != value:
            raise Rx3LiraBarPrepError(
                f"{context}: profile.{field} is {actual[field]!r}, expected "
                f"{value!r}; only the validated 22П / Б2 scope may be prepared"
            )
    if profile.rotation_degrees != REQUIRED_ROTATION:
        raise Rx3LiraBarPrepError(
            f"{context}: rotation {profile.rotation_degrees}° is outside the "
            "validated zero-rotation scope"
        )
    length = verified.chain.bar_length_m
    if length != REQUIRED_LENGTH_M:
        raise Rx3LiraBarPrepError(
            f"{context}: bar length {length} m is outside the validated 3.00 m scope"
        )
    return length


def _effective_length_assessment(
    verified: VerifiedBarRun, target: TargetRecord
) -> dict[str, object]:
    """State what the shipped calculation document says about this algorithm.

    Three separate questions are answered separately, because they have
    different evidence:

    * interface — what the built-in help says about the window controls;
    * algorithm — what the calculation document puts into the formulas of this
      loading mode, compared with the compression mode;
    * engineering applicability — what still depends on an engineer and is not
      established by this package.

    The earlier ``NOT_APPLICABLE`` wording is deliberately gone: it was argued
    from ``N = 0`` and from the program having calculated once with empty
    fields, and neither of those is evidence about the algorithm.
    """

    label = target.record.fields[45]
    if not single_plane_bending_label(label):
        raise Rx3LiraBarPrepError(
            f"the target record carries stress state {label!r}, which is not the "
            "one-plane bending algorithm of this experiment"
        )
    return {
        "status": "NOT_USED_BY_THIS_ALGORITHM",
        "algorithm": "Изгибаемый стержень в одной из главных плоскостей",
        "algorithm_classified_from": {
            "field": 45,
            "field_name": FIELD_SPECS[45].name,
            "value": label,
            "classifier": "single_plane_bending_label",
        },
        "interface": {
            "source": INTERFACE_HELP,
            "finding": (
                "Справка показывает вид опирания и коэффициент расчётной длины "
                "как отдельные элементы блока статического расчёта и отмечает, "
                "что доступность полей зависит от режима, но не перечисляет, "
                "для каких режимов эти поля требуются. Требования в интерфейсе "
                "не установлено."
            ),
        },
        "algorithm_evidence": {
            "source": CALCULATION_DOCUMENT,
            "finding": (
                "Для этого вида нагружения критическая температура определяется "
                "формулами (3) и (4) по максимальному моменту M, моменту "
                "сопротивления W, поперечной силе Q, моменту инерции I, "
                "статическому моменту полусечения S' и минимальной толщине "
                "t_min. Расчётная длина L и вид опирания в этих формулах не "
                "участвуют: они входят в алгоритм «Сжатый стержень» через "
                "гибкость λ = L / i_min."
            ),
            "not_covered": (
                "Документ описывает методику расчёта, а не реализацию RX3; "
                "соответствие программы формулам не проверялось."
            ),
        },
        "engineering_applicability": {
            "limits": list(ENGINEERING_LIMITS),
            "not_established_by_this_package": True,
        },
        "empirical_observations_not_used_as_evidence": [
            "осевое усилие выбранной строки равно нулю",
            "в контролируемом опыте RX3 расчёт проходил при незаполненном "
            "field48 и field51='0'",
        ],
        "not_substituted": (
            "геометрические 3 м расчётной длиной не назначались; поля 48/51/141 "
            "не читаются как семантика и не изменяются подготовщиком"
        ),
    }


def _checkpoint_text(
    manifest: Mapping[str, Any], verified: VerifiedBarRun
) -> str:
    changes = manifest["changes"]
    declaration = verified.declaration
    target = manifest["target"]
    lines = [
        "# Ручной checkpoint RX3: учебный опыт одной балки",
        "",
        f"- Файл для открытия: `{manifest['generated']['path']}`",
        f"- Исходный шаблон (не изменять): `{manifest['template']['path']}`",
        f"- Целевая запись: позиция {target['position']}, "
        f"строка {target['line_number']}, марка {target['mark']!r}",
        f"- Строка РСУ: `{verified.row.row_id}` (элемент {verified.row.element_id}, "
        f"сечение {verified.row.section_station})",
        "",
        "## Что перенесено автоматически из проверенной строки",
        "",
        "| Поле | Было | Стало | Источник |",
        "|---|---:|---:|---|",
    ]
    for change in changes:
        lines.append(
            f"| {change['field']} {change['field_name']} | {change['before_raw']} | "
            f"{change['after_raw']} | {change['source_component']} "
            f"{change['source_value']} {change['source_unit']} "
            f"({change['value_transform']}) |"
        )
    lines.extend(
        [
            "",
            "Все остальные записи файла не изменены "
            f"({manifest['non_target_records']} записей проверено).",
        ]
    )
    unmapped = manifest.get("unmapped_zero_components")
    if isinstance(unmapped, list) and unmapped:
        lines.extend(
            [
                "",
                "## Компоненты без подтверждённого поля",
                "",
                f"{', '.join(str(item) for item in unmapped)} равны нулю в выбранной",
                "строке, но их поля RX3 не подтверждены, поэтому файл их не",
                "переписывал. Если RX3 покажет по ним ненулевые значения — расчёт",
                "не выполнять и сообщить фактические значения.",
            ]
        )
    lines.extend(
        [
            "",
            "## Что уже проверено программой по подтверждённым признакам записи",
            "",
            f"1. Марка {target['mark']!r}, профиль "
            f"{declaration.profile.designation!r}, стандарт "
            f"{declaration.profile.standard!r}, длина "
            f"{verified.chain.bar_length_m} м.",
            "2. Запись классифицируется как изгибаемый стержень в одной из главных",
            "   плоскостей (не сжатый, не сжато-изогнутый).",
            f"3. Требуемый предел записи совпадает с заявленным "
            f"{declaration.design_conditions.required_fire_resistance_min}.",
            "4. Температурный режим записи — стандартный, как заявлено.",
            "5. Шаблон совпадает с контролируемым файлом этого опыта по SHA-256.",
            "",
            "## Расчётная длина и вид опирания",
            "",
        ]
    )
    decision = manifest["effective_length_and_support"]
    algorithm = decision["algorithm_evidence"]
    assert isinstance(algorithm, Mapping)
    lines.append(f"Статус: **{decision['status']}**")
    lines.append("")
    lines.append(
        "Что именно установлено: в поставляемом с программой проекте НД для Rx3"
    )
    lines.append(
        f"({algorithm['source']['path']}, SHA-256 "
        f"`{str(algorithm['source']['sha256'])[:16]}…`),"
    )
    lines.append(f"{algorithm['source']['section']},")
    lines.append(f"{algorithm['source']['bending_subsection']},")
    lines.append("критическая температура определяется по максимальному моменту,")
    lines.append("моменту сопротивления, поперечной силе, моменту инерции и")
    lines.append("минимальной толщине сечения. Расчётная длина и вид опирания в эти")
    lines.append("формулы не входят; они входят в описание сжатого стержня:")
    lines.append(f"{algorithm['source']['compression_subsection']}.")
    lines.append("")
    lines.append("Чего это не доказывает:")
    for limit in decision["engineering_applicability"]["limits"]:
        lines.append(f"- {limit}")
    lines.append("")
    lines.append(f"{decision['not_substituted']}.")
    interface = decision["interface"]
    assert isinstance(interface, Mapping)
    lines.append("")
    lines.append(f"Интерфейс: {interface['finding']}")
    lines.extend(
        [
            "",
            "Никакие значения расчётной длины или закрепления инженеру сверять не",
            "нужно: они не входят в этот алгоритм и подготовщик их не заполняет.",
            "",
            "## Область этого опыта",
            "",
            "Проверено (технический перенос данных):",
        ]
    )
    for item in manifest["experience_scope"]["technical_transfer"]:
        lines.append(f"- {item}")
    lines.append("")
    lines.append("Не доказано этим пакетом:")
    for item in manifest["experience_scope"]["engineering_calculation_not_asserted"]:
        lines.append(f"- {item}")
    lines.extend(
        [
            "",
            "## Что остаётся за инженером",
            "",
            "1. Открыть файл и убедиться, что выбран режим изгиба одной главной",
            "   плоскости, а Mx и Q такие, как в таблице выше.",
            "2. Убедиться, что методика и проект НД применимы к вашей конструкции:",
            "   пакет этого не подтверждает.",
            "3. Нажать расчёт.",
            "4. Сохранить результат как `calculated.rx38` в **новый** каталог;",
            "   `generated.rx38` и шаблон не перезаписывать.",
            "5. Сообщить путь к сохранённому файлу.",
            "",
            "## Чего делать нельзя",
            "",
            "- менять профиль, сталь, R, режим обогрева и температурный режим;",
            "- сохранять файл под именем `generated.rx38` или шаблона;",
            "- переносить значения из более ранних расчётов этой записи: они",
            "  относятся к другому набору нагрузок;",
            "- считать этот опыт разрешением на выпуск или доказательством",
            "  инженерного расчёта.",
            "",
            "## Проверка результата",
            "",
            "После сохранения запустите:",
            "",
            "```powershell",
            manifest["validation_command"],
            "```",
            "",
            "Файл `calculated.rx38` сам по себе **не является** подтверждением",
            "расчёта: подтверждение даёт инженер, который видел результат на",
            "экране. Пока такого подтверждения нет, в команду не добавляйте",
            "`--gui-evidence ENGINEER_CONFIRMED`: без фактического подтверждения",
            "это было бы ложной записью о человеческой проверке.",
            "",
            "**RX38 для выпуска не создан. Статус: NOT_READY_FOR_ISSUE.**",
            "",
        ]
    )
    return "\n".join(lines)


def prepare_rx3_lira_bar_validation(
    *,
    run_dir: str | Path,
    template_path: str | Path,
    output_dir: str | Path,
    mode: ExecutionMode = ExecutionMode.VALIDATION,
    template_sha256_pin: str | None = None,
) -> dict[str, object]:
    """Prepare one VALIDATION-only RX3 input file; never open production.

    ``template_sha256_pin`` exists so that tests can pin a synthetic template.
    The CLI never exposes it, and overriding it grants no production right: the
    scope, transform, evidence binding and the production gates stay unchanged.
    """

    if mode is not ExecutionMode.VALIDATION:
        raise Rx3LiraBarPrepError(
            f"mode {mode.value!r} is refused: this preparer is VALIDATION-only"
        )
    try:
        verified = read_verified_bar_run(run_dir)
    except (LiraFormatError, LiraMappingError) as exc:
        raise Rx3LiraBarPrepError(str(exc)) from exc
    context = str(verified.run_dir)
    length_m = _require_scope(verified, context=context)
    stored_input = verified.run_dir / "experiment" / "experiment_input.json"
    if not stored_input.is_file():
        raise Rx3LiraBarPrepError(
            f"{verified.run_dir}: experiment/experiment_input.json is missing; "
            "the prepared run cannot be cross-checked against its sources"
        )
    _cross_check_experiment_input(stored_input, verified)
    if verified.blockers:
        raise Rx3LiraBarPrepError(
            f"{context}: the experiment records blockers {verified.blockers!r}"
        )
    required_minutes = required_fire_resistance_minutes(
        verified.declaration.design_conditions.required_fire_resistance_min,
        context=context,
    )
    fire_regime = verified.declaration.design_conditions.fire_regime
    if fire_regime is None:
        raise Rx3LiraBarPrepError(
            f"{context}: the experiment does not declare a fire regime"
        )
    template = Path(template_path).resolve(strict=True)
    pin = template_sha256_pin or CONTROLLED_TEMPLATE_SHA256
    template_sha = _sha256_file(template)
    if template_sha != pin:
        raise Rx3LiraBarPrepError(
            f"{template}: this file is not the controlled template of the "
            f"validated scope (expected {pin}, found {template_sha})"
        )
    try:
        document = read_rx38_document(template)
    except Rx38FormatError as exc:
        raise Rx3LiraBarPrepError(f"{template}: {exc}") from exc
    target = _find_target(
        document,
        mark=verified.declaration.bar_id.strip(),
        standard=str(verified.declaration.profile.standard),
        profile=str(verified.declaration.profile.designation),
        length_m=length_m,
        required_minutes=required_minutes,
        fire_regime=fire_regime,
        context=str(template),
    )
    decision = _effective_length_assessment(verified, target)
    changes, unresolved, unmapped_zero = _requested_changes(
        verified, context=context
    )
    if unresolved:
        raise Rx3LiraBarPrepError(
            f"{context}: nonzero components outside the validated convention: "
            f"{unresolved}"
        )
    if not changes:
        raise Rx3LiraBarPrepError(
            f"{context}: the selected row carries no force inside the validated "
            "field scope; nothing may be prepared"
        )
    new_tokens: dict[int, str] = {}
    before: dict[int, str] = {}
    for index, entry in sorted(changes.items()):
        before[index] = target.record.raw_tokens[index]
        new_tokens[index] = decimal_token(Decimal(str(entry["prepared_value"])))
    if all(
        before[index].strip().replace(",", ".") == new_tokens[index].replace(",", ".")
        for index in new_tokens
    ):
        raise Rx3LiraBarPrepError(
            f"{template}: the target record already carries the prepared values; "
            "no change would be made"
        )

    destination = Path(output_dir).resolve(strict=False)
    if destination.exists():
        raise Rx3LiraBarPrepError(
            f"output directory already exists; refusing to overwrite: {destination}"
        )
    target_line = target.record.line_number
    assert target_line is not None
    payload = _render_document(
        document, target_line=target_line, new_tokens=new_tokens
    )

    destination.mkdir(parents=True, exist_ok=False)
    generated_path = destination / "generated.rx38"
    generated_path.write_bytes(payload)

    # Re-read the written file: prove the target changed and nothing else did.
    after_document = read_rx38_document(generated_path)
    if len(after_document.records) != len(document.records):
        raise Rx3LiraBarPrepError("the written file changed the record count")
    after_target: Rx38Record | None = None
    for index, record in enumerate(after_document.records):
        if index == target.line_index:
            after_target = record
            continue
        if list(record.raw_tokens) != list(document.records[index].raw_tokens):
            raise Rx3LiraBarPrepError(
                f"non-target record at position {index + 1} changed"
            )
    if after_target is None:
        raise Rx3LiraBarPrepError("the written file lost the target record")
    change_report: list[dict[str, object]] = []
    for index, entry in sorted(changes.items()):
        after_token = after_target.raw_tokens[index]
        after_value = _decimal(
            after_token, name=f"field{index}", context=str(generated_path)
        )
        prepared_value = Decimal(str(entry["prepared_value"]))
        if after_value != prepared_value:
            raise Rx3LiraBarPrepError(
                f"field {index} persisted as {after_value}, expected {prepared_value}"
            )
        change_report.append(
            {
                **entry,
                "before_raw": before[index],
                "before_value": str(
                    _decimal(
                        before[index],
                        name=f"field{index}",
                        context=str(template),
                    )
                ),
                "after_raw": after_token,
                "after_value": str(after_value),
            }
        )

    validation_command = (
        "py -3.12 -m fireprotect.cli validate-rx3-result "
        f"\"{generated_path}\" <calculated.rx38> "
        f"--target-fingerprint {rx38_record_fingerprint(after_target)} "
        f"--json-report \"{destination / 'rx3_result_report.json'}\" "
        f"--markdown-report \"{destination / 'rx3_result_report.md'}\""
    )
    manifest: dict[str, object] = {
        "kind": PREP_KIND,
        "status": STATUS_PREPARED,
        "mode": mode.value,
        "run": {
            "run_dir": str(verified.run_dir),
            "declaration_status": verified.declaration.declaration_status,
            "engineer_confirmed": verified.declaration.engineer_signed,
            "linked_manifest_sha256": verified.declaration.linked_manifest_sha256,
            "evidence_sha256": verified.evidence.sha256,
        },
        "experiment_row": {
            "row_id": verified.row.row_id,
            "element_id": verified.row.element_id,
            "section_station": verified.row.section_station,
            "rsu_group": verified.row.rsu_group,
            "rsu_criterion": verified.row.rsu_criterion,
            "rsu_column_number": verified.row.rsu_column_number,
            "load_case_membership": list(verified.row.load_case_membership),
        },
        "scope": {
            "standard": REQUIRED_STANDARD,
            "profile": REQUIRED_PROFILE,
            "rx3_template": REQUIRED_TEMPLATE,
            "length_m": str(length_m),
            "rotation_degrees": str(REQUIRED_ROTATION),
            "stress_state": REQUIRED_STRESS_STATE,
        },
        "template": {
            "path": str(template),
            "sha256": template_sha,
            "pinned_sha256": pin,
            "records": len(document.records),
        },
        "generated": {
            "path": str(generated_path),
            "sha256": _sha256_bytes(payload),
            "records": len(after_document.records),
        },
        "target": {
            "position": target.position,
            "line_number": target.record.line_number,
            "mark": verified.declaration.bar_id.strip(),
            "before_fingerprint": rx38_record_fingerprint(target.record),
            "after_fingerprint": rx38_record_fingerprint(after_target),
        },
        "changes": change_report,
        "unmapped_zero_components": unmapped_zero,
        "non_target_records": len(document.records) - 1,
        "non_target_records_identical": True,
        "effective_length_and_support": decision,
        "experience_scope": {
            "technical_transfer": list(TECHNICAL_SCOPE),
            "engineering_calculation_not_asserted": list(ENGINEERING_NOT_ASSERTED),
        },
        "source_binding": {
            "model_dir": str(verified.model_dir),
            "evidence_path": str(verified.evidence_path),
            "stored_input_cross_checked": True,
        },
        "calculation_gate": {
            "allowed_without_human_review": False,
            "requires_engineer_screen_review": True,
            "blocked_by_unconfirmed_inputs": [],
            "effective_length_status": decision["status"],
            "reason": (
                "Расчёт выполняет инженер в RX3 и отвечает за выбор режима и "
                "применимость методики. Расчётная длина и вид опирания не "
                "входят в описание алгоритма этого вида нагружения (проект НД "
                "для Rx3, раздел 4, формулы 3 и 4) и подготовщиком не "
                "заполняются. Пакет не является подтверждением инженерного "
                "расчёта и не разрешает выпуск."
            ),
        },
        "engineer_confirmation_required": True,
        "production_write_allowed": False,
        "release_forbidden": True,
        "issue_readiness": "NOT_READY_FOR_ISSUE",
        "validation_command": validation_command,
        "written_files": [str(destination / name) for name in _PREP_FILES],
    }
    manifest_path = destination / _PREP_FILES[0]
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    checkpoint_path = destination / _PREP_FILES[1]
    checkpoint_path.write_text(_checkpoint_text(manifest, verified), encoding="utf-8")
    return manifest


def experiment_reference(mark: str) -> str:
    """The evidence reference used for one specific LIRA bar experiment."""

    return f"LIRA-RX3-{mark.strip()}"


__all__ = [
    "CONTROLLED_TEMPLATE_SHA256",
    "EXPERIMENT_INPUT_KIND",
    "PREP_KIND",
    "REQUIRED_COMPONENTS",
    "REQUIRED_LENGTH_M",
    "REQUIRED_PROFILE",
    "REQUIRED_STANDARD",
    "REQUIRED_STRESS_STATE",
    "REQUIRED_TEMPLATE",
    "Rx3LiraBarPrepBundle",
    "Rx3LiraBarPrepError",
    "STATUS_PREPARED",
    "TARGET_FIELDS",
    "decimal_token",
    "experiment_reference",
    "prepare_rx3_lira_bar_validation",
    "required_fire_resistance_minutes",
]
