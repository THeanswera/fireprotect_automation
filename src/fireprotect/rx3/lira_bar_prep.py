"""VALIDATION-only preparation of one RX3 input from a verified LIRA bar run.

This module is deliberately not a general RX38 writer. It performs exactly one
transformation, in the scope that was already validated by the controlled
`LIRA-RX3-22P-XX-MAGNITUDE` experiment:

* one existing ``Tconstr`` record of a compatible template, identified by mark,
  standard, profile and length, is the only target;
* only fields 50 (``major_axis_moment_knm``) and 92 (``rx3_gui_q_input_kn``)
  may receive a new token, and only from the values that the re-verified
  experiment input already carries through the resolved ``MAGNITUDE``
  convention (LIRA ``My`` -> field50, LIRA ``Qz`` -> field92);
* every other record and every other field of the target record stays
  byte-identical, which is re-checked after writing;
* nothing is overwritten, and the source template is never modified.

The produced file is a *test input*: it opens no production gate, it does not
claim a signature, and it is not a permission to calculate. The remaining
unconfirmed design conditions stay listed in the report so that the manual
checkpoint can refuse the calculation until a human has reviewed them.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Mapping

from ..execution import ExecutionMode
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

# RX3 component target -> (RX38 field index, required review unit)
TARGET_FIELDS: Mapping[str, tuple[int, str]] = {
    "FIELD50_MAX_MAJOR_AXIS_MOMENT": (50, "kN*m"),
    "FIELD92_MAX_SHEAR_Q": (92, "kN"),
}
_PREP_FILES = (
    "rx3_validation_manifest.json",
    "CHECKPOINT_RX3.md",
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
    context: str,
) -> TargetRecord:
    """Find exactly one compatible record; ambiguity is refused."""

    matches: list[tuple[int, int, Rx38Record]] = []
    tconstr_position = 0
    for line_index, record in enumerate(document.records):
        if record.record_type != "Tconstr" or len(record.fields) != 200:
            continue
        tconstr_position += 1
        if (
            record.fields[1].strip() == mark
            and record.fields[17].strip() == standard
            and record.fields[19].strip() == profile
            and _decimal(record.fields[14], name="length_m", context=context)
            == length_m
        ):
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


def _requested_changes(
    experiment: Mapping[str, Any], *, context: str
) -> tuple[dict[int, dict[str, object]], list[str], list[str]]:
    """Collect the field values the verified input actually asks for."""

    components = experiment.get("components")
    if not isinstance(components, list) or not components:
        raise Rx3LiraBarPrepError(f"{context}: experiment input has no components")
    changes: dict[int, dict[str, object]] = {}
    unresolved_nonzero: list[str] = []
    unmapped_zero: list[str] = []
    for item in components:
        if not isinstance(item, Mapping):
            raise Rx3LiraBarPrepError(f"{context}: malformed component entry")
        name = str(item.get("component"))
        value = _decimal(
            item.get("source_value"), name=f"{name}.source_value", context=context
        )
        convention = item.get("convention")
        if not isinstance(convention, Mapping):
            raise Rx3LiraBarPrepError(f"{context}: {name} has no convention record")
        resolved = convention.get("resolved") is True
        if not resolved:
            # Unknown mappings are never written: a zero of this experiment must
            # not silently overwrite a field whose meaning is unproven. The
            # checkpoint requires the engineer to confirm those values on screen.
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
            item.get("review_value"),
            name=f"{name}.review_value",
            context=context,
        )
        review_unit = item.get("review_unit")
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
            "source_unit": item.get("source_unit"),
            "source_cell": item.get("source_cell"),
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
    profile: Mapping[str, Any], bar: Mapping[str, Any], *, context: str
) -> Decimal:
    expected = {
        "standard": REQUIRED_STANDARD,
        "designation": REQUIRED_PROFILE,
        "rx3_template": REQUIRED_TEMPLATE,
        "stress_state": REQUIRED_STRESS_STATE,
    }
    for field, value in expected.items():
        if profile.get(field) != value:
            raise Rx3LiraBarPrepError(
                f"{context}: profile.{field} is {profile.get(field)!r}, expected "
                f"{value!r}; only the validated 22П / Б2 scope may be prepared"
            )
    rotation = _decimal(
        profile.get("rotation_degrees"),
        name="profile.rotation_degrees",
        context=context,
    )
    if rotation != REQUIRED_ROTATION:
        raise Rx3LiraBarPrepError(
            f"{context}: rotation {rotation}° is outside the validated zero-"
            "rotation scope"
        )
    length = _decimal(
        bar.get("bar_length_m"), name="bar.bar_length_m", context=context
    )
    if length != REQUIRED_LENGTH_M:
        raise Rx3LiraBarPrepError(
            f"{context}: bar length {length} m is outside the validated 3.00 m scope"
        )
    return length


def _checkpoint_text(manifest: Mapping[str, Any]) -> str:
    changes = manifest["changes"]
    lines = [
        "# Ручной checkpoint RX3: учебный опыт Б2 / R0003",
        "",
        f"- Файл для открытия: `{manifest['generated']['path']}`",
        f"- Исходный шаблон (не изменять): `{manifest['template']['path']}`",
        f"- Целевая запись: позиция {manifest['target']['position']}, "
        f"марка {manifest['target']['mark']!r}",
        "",
        "## Что уже подготовлено автоматически",
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
            "",
        ]
    )
    unmapped = manifest.get("unmapped_zero_components")
    if isinstance(unmapped, list) and unmapped:
        lines.extend(
            [
                "## Компоненты без подтверждённого поля",
                "",
                f"{', '.join(str(item) for item in unmapped)} равны нулю в выбранной",
                "строке, но их поля RX3 не подтверждены, поэтому файл их не",
                "переписывал. На экране они обязаны быть нулевыми.",
                "",
            ]
        )
    lines.extend(
        [
            "## Что инженер обязан сверить на экране RX3",
            "",
            "1. Марка и профиль: Б2, Швеллер 22П, ГОСТ 8240-97, длина 3,00 м.",
            "2. Сталь С245, температурная модель σ0.2% и E по EN 1993-1-2.",
            "3. Mx = 6,1781895 кН·м; Q = 0; N, Mk, Mz, Qy = 0.",
            "4. Требуемый предел R15 и стандартный температурный режим.",
            "5. Обогрев: полный контур, периметр 757,20 мм.",
            "6. Закрепление и расчётная длина.",
            "",
            "Пункт 6 **не подтверждён документацией**: в исходном шаблоне",
            "расчётная длина и закрепление не заданы явными значениями, которые",
            "можно было бы сверить с постановкой опыта. До визуального",
            "подтверждения этих полей расчёт запрещён.",
            "",
            "## Запрещено",
            "",
            "- менять инженерные параметры, режим обогрева, R, сталь и профиль;",
            "- сохранять файл под тем же именем, что шаблон;",
            "- использовать результаты этого файла для выпуска документации;",
            "- переносить значения из старого расчёта (Mx=14,80; Q=14,72;",
            "  θcr=617,70; 9,85 мин) — они относятся к другому набору нагрузок.",
            "",
            "## Порядок действий",
            "",
            "1. Открыть файл выше в RX3.",
            "2. Сверить пункты 1–6. При любом расхождении остановиться и сообщить",
            "   фактические значения.",
            "3. Нажать расчёт только после успешной сверки.",
            "4. Сохранить результат как `calculated.rx38` в новый каталог;",
            "   `generated.rx38` и шаблон не перезаписывать.",
            "5. Вернуться к ассистенту с путём к `calculated.rx38`.",
            "",
            f"Проверка результата: `{manifest['validation_command']}`",
            "",
            "**RX38 для выпуска не создан. Статус: NOT_READY_FOR_ISSUE.**",
            "",
        ]
    )
    return "\n".join(lines)


def prepare_rx3_lira_bar_validation(
    *,
    experiment_dir: str | Path,
    template_path: str | Path,
    output_dir: str | Path,
    mode: ExecutionMode = ExecutionMode.VALIDATION,
) -> dict[str, object]:
    """Prepare one VALIDATION-only RX3 input file; never open production."""

    if mode is not ExecutionMode.VALIDATION:
        raise Rx3LiraBarPrepError(
            f"mode {mode.value!r} is refused: this preparer is VALIDATION-only"
        )
    experiment_root = Path(experiment_dir).resolve(strict=True)
    input_path = experiment_root / "experiment_input.json"
    experiment = _read_json(input_path, "experiment input")
    context = str(input_path)
    if experiment.get("kind") != EXPERIMENT_INPUT_KIND:
        raise Rx3LiraBarPrepError(
            f"{context}: kind must be {EXPERIMENT_INPUT_KIND!r}"
        )
    if experiment.get("status") != "EXPERIMENT_INPUT_READY":
        raise Rx3LiraBarPrepError(
            f"{context}: experiment status is {experiment.get('status')!r}; a "
            "blocked transfer is never turned into a calculation input"
        )
    if experiment.get("transfer_blocked") is not False:
        raise Rx3LiraBarPrepError(f"{context}: transfer is blocked")
    if experiment.get("blockers"):
        raise Rx3LiraBarPrepError(
            f"{context}: experiment records blockers {experiment.get('blockers')!r}"
        )
    profile = experiment.get("profile")
    bar = experiment.get("bar")
    if not isinstance(profile, Mapping) or not isinstance(bar, Mapping):
        raise Rx3LiraBarPrepError(f"{context}: profile and bar must be objects")
    length_m = _require_scope(profile, bar, context=context)
    mark = bar.get("bar_id")
    if not isinstance(mark, str) or not mark.strip():
        raise Rx3LiraBarPrepError(f"{context}: bar.bar_id must be a string")

    changes, unresolved, unmapped_zero = _requested_changes(
        experiment, context=context
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

    template = Path(template_path).resolve(strict=True)
    try:
        document = read_rx38_document(template)
    except Rx38FormatError as exc:
        raise Rx3LiraBarPrepError(f"{template}: {exc}") from exc
    target = _find_target(
        document,
        mark=mark.strip(),
        standard=str(profile.get("standard")),
        profile=str(profile.get("designation")),
        length_m=length_m,
        context=str(template),
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
    generated_sha = _sha256_bytes(payload)

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
        "--gui-evidence ENGINEER_CONFIRMED --evidence-reference "
        f"{experiment_reference(mark)} "
        f"--json-report \"{destination / 'rx3_result_report.json'}\" "
        f"--markdown-report \"{destination / 'rx3_result_report.md'}\""
    )
    missing = experiment.get("missing_confirmations")
    missing_list = (
        [str(item) for item in missing] if isinstance(missing, list) else []
    )
    manifest: dict[str, object] = {
        "kind": PREP_KIND,
        "status": STATUS_PREPARED,
        "mode": mode.value,
        "experiment_input": {
            "path": str(input_path),
            "declaration_status": experiment.get("declaration_status"),
            "engineer_confirmed": experiment.get("engineer_confirmed"),
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
            "sha256": _sha256_file(template),
            "records": len(document.records),
        },
        "generated": {
            "path": str(generated_path),
            "sha256": generated_sha,
            "records": len(after_document.records),
        },
        "target": {
            "position": target.position,
            "line_number": target.record.line_number,
            "mark": mark.strip(),
            "before_fingerprint": rx38_record_fingerprint(target.record),
            "after_fingerprint": rx38_record_fingerprint(after_target),
        },
        "changes": change_report,
        "unmapped_zero_components": unmapped_zero,
        "non_target_records": len(document.records) - 1,        "non_target_records_identical": True,
        "unconfirmed_inputs": missing_list,
        "calculation_gate": {
            "allowed_without_human_review": False,
            "requires_engineer_screen_review": True,
            "unconfirmed_inputs": missing_list,
            "reason": (
                "Расчётная длина и закрепление не подтверждены документацией. "
                "Расчёт допустим только после визуальной сверки этих полей в RX3."
            ),
        },
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
    checkpoint_path.write_text(_checkpoint_text(manifest), encoding="utf-8")
    return manifest


def experiment_reference(mark: str) -> str:
    """The evidence reference used for this specific teaching experiment."""

    return f"LIRA-RX3-{mark.strip()}-R0003"


__all__ = [
    "EXPERIMENT_INPUT_KIND",
    "PREP_KIND",
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
]
