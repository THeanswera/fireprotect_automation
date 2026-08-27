"""Artifacts and reports for the mandatory manual RX3 GUI checkpoint."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from hashlib import sha256
from itertools import combinations
import json
from pathlib import Path
import shutil
from typing import Any

from ..execution import ExecutionMode
from ..project_io import read_project_element_json
from .diff import diff_records
from .parser import construction_records, read_rx38
from .project_adapter import create_rx38_from_project_element
from .result import rx38_record_to_rx3_result
from .safety import GuiExecutionEvidence, Rx3SafetyContext
from .schema import field_spec


class Rx3GuiValidationError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class Rx3ValidationBundle:
    directory: Path
    template: Path
    generated: Path
    project_element: Path
    rx3_input: Path
    template_profile: Path
    diff_json: Path
    diff_markdown: Path
    instructions: Path


@dataclass(frozen=True, slots=True)
class Rx3ValidationReport:
    data: dict[str, Any]
    json_path: Path
    markdown_path: Path


def _sha256(path: Path) -> str:
    digest = sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _write_new(path: Path, content: str) -> None:
    if path.exists():
        raise Rx3GuiValidationError(f"Refusing to overwrite validation artifact: {path}")
    path.write_text(content, encoding="utf-8", newline="\n")


def _change_dict(change: Any) -> dict[str, Any]:
    spec = field_spec(change.index)
    return {
        "index": change.index,
        "name": spec.name,
        "confidence": spec.confidence,
        "direction": spec.direction,
        "old_value": change.old_value,
        "new_value": change.new_value,
        "evidence": spec.source,
        "comment": spec.comment,
    }


def _changes_markdown(title: str, changes: list[dict[str, Any]]) -> str:
    lines = [f"# {title}", ""]
    if not changes:
        return "\n".join(lines + ["Изменений нет.", ""])
    lines.extend(
        [
            "| Индекс | Поле | Статус | Направление | До | После |",
            "|---:|---|---|---|---|---|",
        ]
    )
    for item in changes:
        old = str(item["old_value"]).replace("|", "\\|")
        new = str(item["new_value"]).replace("|", "\\|")
        lines.append(
            f"| {item['index']} | {item['name']} | {item['confidence']} | "
            f"{item['direction']} | `{old}` | `{new}` |"
        )
    lines.append("")
    return "\n".join(lines)


def prepare_rx3_validation(
    project_json: str | Path,
    template_rx38: str | Path,
    output_directory: str | Path,
    *,
    template_mark: str | None = None,
    safety_context: Rx3SafetyContext | None = None,
) -> Rx3ValidationBundle:
    """Create a self-contained, non-destructive RX3 GUI validation bundle."""

    source_json = Path(project_json).resolve(strict=True)
    source_template = Path(template_rx38).resolve(strict=True)
    directory = Path(output_directory).resolve(strict=False)
    if directory.exists() and any(directory.iterdir()):
        raise Rx3GuiValidationError(
            f"Validation directory must be new or empty: {directory}"
        )
    directory.mkdir(parents=True, exist_ok=True)

    template = directory / "template.rx38"
    generated = directory / "generated.rx38"
    project_copy = directory / "project_element.json"
    rx3_input = directory / "rx3_input.json"
    template_profile = directory / "rx3_template_profile.json"
    diff_json = directory / "diff_before_after.json"
    diff_markdown = directory / "diff_before_after.md"
    instructions = directory / "README_VALIDATION.md"

    shutil.copy2(source_template, template)
    shutil.copy2(source_json, project_copy)
    element = read_project_element_json(project_copy)
    creation = create_rx38_from_project_element(
        element,
        template,
        generated,
        template_mark=template_mark,
        safety_context=safety_context,
    )
    changes = [
        {
            "index": item.index,
            "name": item.name,
            "confidence": field_spec(item.index).confidence,
            "direction": field_spec(item.index).direction,
            "old_value": item.old_value,
            "new_value": item.new_value,
        }
        for item in creation.changed_fields
    ]
    payload: dict[str, Any] = {
        "template": {"file": template.name, "sha256": _sha256(template)},
        "generated": {"file": generated.name, "sha256": _sha256(generated)},
        "project_element": {
            "file": project_copy.name,
            "sha256": _sha256(project_copy),
        },
        "changed_fields": changes,
        "preserved_fields_count": creation.preserved_fields_count,
        "unknown_fields_count": creation.unknown_fields_count,
        "warnings": list(creation.warnings),
        "round_trip_valid": creation.round_trip_valid,
        "safety_mode": creation.safety_mode,
        "steel_compatibility": creation.steel_compatibility,
        "template_profile": creation.template_profile,
        "stale_template_result_indices": list(
            creation.stale_template_result_indices
        ),
    }
    _write_new(diff_json, json.dumps(payload, ensure_ascii=False, indent=2) + "\n")
    _write_new(
        rx3_input,
        json.dumps(creation.rx3_input, ensure_ascii=False, indent=2) + "\n",
    )
    _write_new(
        template_profile,
        json.dumps(creation.template_profile, ensure_ascii=False, indent=2) + "\n",
    )
    _write_new(
        diff_markdown,
        _changes_markdown("Diff template.rx38 → generated.rx38", changes),
    )
    _write_new(
        instructions,
        """# Проверка RX3

1. Откройте `template.rx38` в RX3 и убедитесь, что исходный файл читается.
2. Откройте `generated.rx38` в RX3. Если RX3 показывает ошибку — остановитесь и сохраните текст/скриншот ошибки.
3. Сверьте `project_element.json`, `rx3_input.json` и `rx3_template_profile.json`.
4. Зафиксируйте экранные значения mark, profile, steel, N, Mx, My, Qx, Qy, length, support, effective length, fire regime, R и режим critical-temperature calculation.
5. Для AXIAL_ONLY убедитесь, что Mx=My=Qx=Qy=0. При любом расхождении остановитесь.
6. Нажмите расчёт в RX3. Не меняйте инженерные параметры без фиксации изменения.
7. Сохраните рассчитанный файл в этой папке под именем `calculated.rx38`; не перезаписывайте `template.rx38` и `generated.rx38`.
8. Выполните:

   `python -m fireprotect.cli validate-rx3-result generated.rx38 calculated.rx38 --gui-evidence ENGINEER_CONFIRMED --evidence-reference EVIDENCE-ID`

9. Передайте `calculated.rx38`, evidence, `rx3_result.json`, `rx3_validation_report.json` и `rx3_validation_report.md` обратно в проект.
""",
    )
    return Rx3ValidationBundle(
        directory,
        template,
        generated,
        project_copy,
        rx3_input,
        template_profile,
        diff_json,
        diff_markdown,
        instructions,
    )


def _dependency_candidates(change_sets: list[set[int]]) -> list[dict[str, Any]]:
    occurrences: dict[int, int] = defaultdict(int)
    together: dict[tuple[int, int], int] = defaultdict(int)
    for indices in change_sets:
        for index in indices:
            occurrences[index] += 1
        for left, right in combinations(sorted(indices), 2):
            together[(left, right)] += 1
    candidates: list[dict[str, Any]] = []
    for (left, right), count in sorted(together.items()):
        if count != occurrences[left] and count != occurrences[right]:
            continue
        candidates.append(
            {
                "indices": [left, right],
                "names": [field_spec(left).name, field_spec(right).name],
                "co_change_count": count,
                "left_change_count": occurrences[left],
                "right_change_count": occurrences[right],
                "status": "CO_CHANGE_ONLY_NOT_CAUSAL",
            }
        )
    return candidates


def validate_rx3_result_files(
    before_rx38: str | Path,
    after_rx38: str | Path,
    *,
    json_report: str | Path | None = None,
    markdown_report: str | Path | None = None,
    overwrite: bool = False,
    gui_execution_evidence: GuiExecutionEvidence = GuiExecutionEvidence.NOT_PROVIDED,
    evidence_reference: str | None = None,
    mode: ExecutionMode = ExecutionMode.VALIDATION,
) -> Rx3ValidationReport:
    before_path = Path(before_rx38).resolve(strict=True)
    after_path = Path(after_rx38).resolve(strict=True)
    if not isinstance(overwrite, bool):
        raise TypeError("overwrite must be bool")
    if not isinstance(gui_execution_evidence, GuiExecutionEvidence):
        gui_execution_evidence = GuiExecutionEvidence(gui_execution_evidence)
    if not isinstance(mode, ExecutionMode):
        raise TypeError("mode must be ExecutionMode")
    before = construction_records(read_rx38(before_path))
    after = construction_records(read_rx38(after_path))
    if len(before) != len(after):
        raise Rx3GuiValidationError(
            f"Tconstr count changed: before={len(before)}, after={len(after)}"
        )

    records: list[dict[str, Any]] = []
    change_sets: list[set[int]] = []
    material_result_change_sets: list[set[int]] = []
    unsafe_change_sets: list[set[int]] = []
    expected_output_fields = {44, 54}
    for position, (old, new) in enumerate(zip(before, after), 1):
        changes = [_change_dict(item) for item in diff_records(old, new)]
        changed_indices = {item["index"] for item in changes}
        change_sets.append(changed_indices)
        material_changes: set[int] = set()
        for index in expected_output_fields:
            try:
                old_value = Decimal(old.fields[index].strip().replace(",", "."))
                new_value = Decimal(new.fields[index].strip().replace(",", "."))
            except InvalidOperation:
                continue
            if old_value.is_finite() and new_value.is_finite() and old_value != new_value:
                material_changes.add(index)
        material_result_change_sets.append(material_changes)
        unsafe_change_sets.append(changed_indices - expected_output_fields)
        records.append(
            {
                "position": position,
                "before_mark": old.mark,
                "after_mark": new.mark,
                "confirmed_changes": [
                    item for item in changes if item["confidence"] == "confirmed"
                ],
                "probable_changes": [
                    item for item in changes if item["confidence"] == "probable"
                ],
                "unknown_changes": [
                    {
                        "index": item["index"],
                        "old_value": item["old_value"],
                        "new_value": item["new_value"],
                    }
                    for item in changes
                    if item["confidence"] == "unknown"
                ],
                "rx3_result": rx38_record_to_rx3_result(
                    new, source_file=after_path
                ).as_dict(),
            }
        )

    before_hash = _sha256(before_path)
    after_hash = _sha256(after_path)
    byte_identical = before_hash == after_hash
    result_fields_changed = all(
        expected_output_fields.issubset(indices)
        for indices in material_result_change_sets
    )
    recalculation_proven = not byte_identical and result_fields_changed
    evidence_reference_valid = (
        isinstance(evidence_reference, str) and bool(evidence_reference.strip())
    )
    unsafe_production_changes = (
        mode is ExecutionMode.PRODUCTION
        and any(indices for indices in unsafe_change_sets)
    )
    gui_verified = (
        recalculation_proven
        and evidence_reference_valid
        and not unsafe_production_changes
        and gui_execution_evidence
        in {
            GuiExecutionEvidence.ENGINEER_CONFIRMED,
            GuiExecutionEvidence.SCREENSHOT_REFERENCED,
        }
    )
    status = (
        "RX3_RECALCULATION_NOT_PROVEN"
        if not recalculation_proven
        else "RX3_PRODUCTION_INPUTS_CHANGED"
        if unsafe_production_changes
        else "RX3_RESULT_ANALYSED"
        if gui_verified
        else "RX3_GUI_RECALCULATION_UNVERIFIED"
    )
    payload: dict[str, Any] = {
        "status": status,
        "before": {"path": str(before_path), "sha256": before_hash},
        "after": {"path": str(after_path), "sha256": after_hash},
        "byte_identical": byte_identical,
        "expected_result_fields": sorted(expected_output_fields),
        "expected_result_fields_changed": result_fields_changed,
        "rx3_recalculation_proven": recalculation_proven,
        "execution_mode": mode.value,
        "gui_execution_evidence": gui_execution_evidence.value,
        "evidence_reference": evidence_reference,
        "evidence_reference_valid": evidence_reference_valid,
        "gui_recalculation_verified": gui_verified,
        "unsafe_production_change_indices": sorted(
            set().union(*unsafe_change_sets) if unsafe_change_sets else set()
        ),
        "records": records,
        "dependency_candidates": _dependency_candidates(change_sets),
        "dependency_warning": (
            "Co-change is observational evidence only and does not prove field semantics or causality."
        ),
    }
    json_path = Path(json_report) if json_report else after_path.parent / "rx3_validation_report.json"
    md_path = Path(markdown_report) if markdown_report else after_path.parent / "rx3_validation_report.md"
    json_path = json_path.resolve(strict=False)
    md_path = md_path.resolve(strict=False)
    result_path = (after_path.parent / "rx3_result.json").resolve(strict=False)
    report_targets = (json_path, md_path, result_path)
    protected_inputs = {before_path, after_path}
    if any(target in protected_inputs for target in report_targets):
        raise Rx3GuiValidationError(
            "Validation reports must not overwrite before/after RX38 files"
        )
    if len(set(report_targets)) != len(report_targets):
        raise Rx3GuiValidationError("Validation report paths must be distinct")
    for target in report_targets:
        if target.exists() and not overwrite:
            raise Rx3GuiValidationError(f"Report already exists: {target}")
        target.parent.mkdir(parents=True, exist_ok=True)
    json_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    result_path.write_text(
        json.dumps(
            {
                "status": status,
                "gui_execution_evidence": gui_execution_evidence.value,
                "results": [item["rx3_result"] for item in records],
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
        newline="\n",
    )

    lines = [
        "# Отчёт проверки результата RX3",
        "",
        f"Статус: **{status}**.",
        "",
        f"- BEFORE SHA-256: `{payload['before']['sha256']}`",
        f"- AFTER SHA-256: `{payload['after']['sha256']}`",
        f"- Byte-identical: `{byte_identical}`",
        f"- Expected result fields 44/54 changed: `{result_fields_changed}`",
        f"- RX3 recalculation proven: `{recalculation_proven}`",
        f"- Execution mode: `{mode.value}`",
        f"- GUI evidence: `{gui_execution_evidence.value}`",
        f"- Evidence reference valid: `{evidence_reference_valid}`",
        f"- GUI recalculation verified: `{gui_verified}`",
        f"- Unsafe production changes: `{payload['unsafe_production_change_indices']}`",
        "",
    ]
    for record in records:
        lines.extend(
            [
                f"## Конструкция {record['position']}: {record['after_mark'] or '-'}",
                "",
            ]
        )
        for label, key in (
            ("CONFIRMED", "confirmed_changes"),
            ("PROBABLE", "probable_changes"),
            ("UNKNOWN (только raw-индексы)", "unknown_changes"),
        ):
            lines.append(f"### {label}")
            lines.append("")
            items = record[key]
            if not items:
                lines.append("Изменений нет.")
            else:
                for item in items:
                    name = item.get("name", "raw")
                    lines.append(
                        f"- `{item['index']}` {name}: `{item['old_value']}` → `{item['new_value']}`"
                    )
            lines.append("")
    lines.extend(
        [
            "## Кандидаты зависимостей",
            "",
            "Совместное изменение не доказывает смысл поля или причинность.",
            "",
        ]
    )
    if payload["dependency_candidates"]:
        for item in payload["dependency_candidates"]:
            lines.append(
                f"- {item['indices']} / {item['names']}: совместно в {item['co_change_count']} записях."
            )
    else:
        lines.append("Недостаточно совместных изменений для кандидатов.")
    lines.append("")
    md_path.write_text("\n".join(lines), encoding="utf-8", newline="\n")
    return Rx3ValidationReport(payload, json_path, md_path)
