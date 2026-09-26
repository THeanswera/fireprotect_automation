"""Artifacts and reports for the mandatory manual RX3 GUI checkpoint."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Sequence
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
from .project_adapter import Rx38CreationReport
from .result import rx38_record_to_rx3_result
from .safety import GuiExecutionEvidence, Rx3SafetyContext, rx38_record_fingerprint
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
    creation: Rx38CreationReport


@dataclass(frozen=True, slots=True)
class Rx3ValidationReport:
    data: dict[str, Any]
    json_path: Path
    markdown_path: Path

    @property
    def verified_for_production(self) -> bool:
        after = self.data.get("after")
        if not isinstance(after, dict):
            return False
        path_value = after.get("path")
        expected_hash = after.get("sha256")
        if not isinstance(path_value, str) or not isinstance(expected_hash, str):
            return False
        path = Path(path_value)
        return (
            self.data.get("execution_mode") == ExecutionMode.PRODUCTION.value
            and self.data.get("status") == "RX3_RESULT_ANALYSED"
            and self.data.get("gui_recalculation_verified") is True
            and path.is_file()
            and _sha256(path) == expected_hash
        )


def _sha256(path: Path) -> str:
    digest = sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _read_stable_construction_records(path: Path):
    checksum = _sha256(path)
    records = construction_records(read_rx38(path))
    if _sha256(path) != checksum:
        raise Rx3GuiValidationError(
            f"RX38 changed while it was being validated: {path}"
        )
    return records, checksum


def _write_new(path: Path, content: str) -> None:
    if path.exists():
        raise Rx3GuiValidationError(f"Refusing to overwrite validation artifact: {path}")
    path.write_text(content, encoding="utf-8", newline="\n")


def _change_dict(change: Any) -> dict[str, Any]:
    spec = field_spec(change.index)
    semantic_changed: bool | None
    classification: str
    if (
        spec.confidence == "confirmed"
        and spec.data_type == "decimal"
        and spec.units is not None
    ):
        old_numeric = _finite_decimal(change.old_value)
        new_numeric = _finite_decimal(change.new_value)
        if old_numeric is None or new_numeric is None:
            semantic_changed = None
            classification = "CONFIRMED_NUMERIC_PARSE_UNAVAILABLE"
        else:
            semantic_changed = old_numeric != new_numeric
            classification = (
                "SEMANTIC_NUMERIC_CHANGE"
                if semantic_changed
                else "RX3_TOKEN_NORMALIZATION"
            )
    elif spec.confidence == "confirmed" and spec.data_type in {
        "decimal",
        "integer",
    }:
        semantic_changed = None
        classification = "CONFIRMED_NUMERIC_EQUIVALENCE_NOT_APPLICABLE"
    elif spec.confidence == "confirmed":
        semantic_changed = True
        classification = "CONFIRMED_TEXT_CHANGE"
    else:
        semantic_changed = None
        classification = "RAW_TOKEN_CHANGE_SEMANTICS_UNVERIFIED"
    return {
        "index": change.index,
        "name": spec.name,
        "confidence": spec.confidence,
        "direction": spec.direction,
        "old_value": change.old_value,
        "new_value": change.new_value,
        "old_token": change.old_value,
        "new_token": change.new_value,
        "text_changed": True,
        "semantic_changed": semantic_changed,
        "classification": classification,
        "evidence": spec.source,
        "comment": spec.comment,
    }


def _finite_decimal(token: str) -> Decimal | None:
    try:
        value = Decimal(token.strip().replace(",", "."))
    except InvalidOperation:
        return None
    return value if value.is_finite() else None


def _record_semantic_changed(changes: list[dict[str, Any]]) -> bool | None:
    if not changes:
        return False
    semantic_values = [item["semantic_changed"] for item in changes]
    if any(value is True for value in semantic_values):
        return True
    if all(value is False for value in semantic_values):
        return False
    return None


def _resolve_target_positions(
    records: Sequence[Any],
    *,
    target_record_fingerprints: Sequence[str],
    target_record_positions: Sequence[int],
    target_marks: Sequence[str],
) -> tuple[set[int], dict[str, Any]]:
    supplied = sum(
        bool(items)
        for items in (
            target_record_fingerprints,
            target_record_positions,
            target_marks,
        )
    )
    if supplied == 0:
        raise Rx3GuiValidationError(
            "At least one explicit target record fingerprint, position, or mark is required"
        )
    if supplied > 1:
        raise Rx3GuiValidationError(
            "Use exactly one target selector strategy per validation run"
        )

    positions: list[int] = []
    if target_record_fingerprints:
        strategy = "BEFORE_RECORD_FINGERPRINT"
        fingerprints = [item.strip().lower() for item in target_record_fingerprints]
        for fingerprint in fingerprints:
            if len(fingerprint) != 64 or any(
                character not in "0123456789abcdef" for character in fingerprint
            ):
                raise Rx3GuiValidationError(
                    f"Invalid target record SHA-256 fingerprint: {fingerprint!r}"
                )
            matches = [
                position
                for position, record in enumerate(records, 1)
                if rx38_record_fingerprint(record) == fingerprint
            ]
            if len(matches) != 1:
                raise Rx3GuiValidationError(
                    "Target fingerprint must resolve to exactly one BEFORE Tconstr; "
                    f"fingerprint={fingerprint}, matches={matches}"
                )
            positions.append(matches[0])
        requested: Sequence[str | int] = fingerprints
    elif target_record_positions:
        strategy = "TCONSTR_POSITION"
        for position in target_record_positions:
            if isinstance(position, bool) or not isinstance(position, int):
                raise Rx3GuiValidationError("Target positions must be integers")
            if position < 1 or position > len(records):
                raise Rx3GuiValidationError(
                    f"Target Tconstr position {position} is outside 1..{len(records)}"
                )
            positions.append(position)
        requested = list(target_record_positions)
    else:
        strategy = "UNIQUE_MARK"
        marks = [item.strip() for item in target_marks]
        for mark in marks:
            if not mark:
                raise Rx3GuiValidationError("Target marks must not be blank")
            matches = [
                position
                for position, record in enumerate(records, 1)
                if record.mark == mark
            ]
            if len(matches) != 1:
                raise Rx3GuiValidationError(
                    "Target mark must resolve to exactly one BEFORE Tconstr; "
                    f"mark={mark!r}, matches={matches}"
                )
            positions.append(matches[0])
        requested = marks

    if len(set(positions)) != len(positions):
        raise Rx3GuiValidationError("Target selectors resolve to duplicate Tconstr records")
    return set(positions), {
        "strategy": strategy,
        "requested": requested,
        "resolved_positions": sorted(positions),
        "resolved_before_record_fingerprints": [
            rx38_record_fingerprint(records[position - 1])
            for position in sorted(positions)
        ],
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
        "heating_exposure": creation.heating_exposure,
        "stale_template_result_indices": list(
            creation.stale_template_result_indices
        ),
        "target_record": {
            "position": creation.target_record_position,
            "before_record_fingerprint": creation.output_record_sha256,
            "mark": creation.output_mark,
        },
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
6. Нажмите «Рассчитать» в RX3 и зафиксируйте состояние DIALOG_CALCULATED. Не меняйте инженерные параметры без фиксации изменения.
7. Нажмите «Сохранить в таблицу» и зафиксируйте состояние TABLE_UPDATED.
8. Выполните Project Save As в этой папке под именем `calculated.rx38`; не перезаписывайте `template.rx38` и `generated.rx38`. Это кандидат FILE_PERSISTED.
9. Выполните:

   `python -m fireprotect.cli validate-rx3-result generated.rx38 calculated.rx38 --target-fingerprint BEFORE_RECORD_SHA256 --gui-evidence ENGINEER_CONFIRMED --evidence-reference EVIDENCE-ID`

10. Передайте `calculated.rx38`, evidence, `rx3_result.json`, `rx3_validation_report.json` и `rx3_validation_report.md` обратно в проект.
11. Before any VALIDATION/PRODUCTION generation, verify that typed
    `heating_exposure` evidence names this ProjectElement, records the same
    `heating_sides`, and is bound to the SHA-256 of the exact template Tconstr.
    `heated_perimeter` alone is not RX3 heating-side evidence; RX38 heating-side
    indices remain unmapped.
""".replace("BEFORE_RECORD_SHA256", creation.output_record_sha256),
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
        creation,
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
    expected_before_sha256: str | None = None,
    mode: ExecutionMode = ExecutionMode.VALIDATION,
    target_record_fingerprints: Sequence[str] = (),
    target_record_positions: Sequence[int] = (),
    target_marks: Sequence[str] = (),
) -> Rx3ValidationReport:
    before_path = Path(before_rx38).resolve(strict=True)
    after_path = Path(after_rx38).resolve(strict=True)
    if not isinstance(overwrite, bool):
        raise TypeError("overwrite must be bool")
    if not isinstance(gui_execution_evidence, GuiExecutionEvidence):
        gui_execution_evidence = GuiExecutionEvidence(gui_execution_evidence)
    if not isinstance(mode, ExecutionMode):
        raise TypeError("mode must be ExecutionMode")
    before, before_hash = _read_stable_construction_records(before_path)
    after, after_hash = _read_stable_construction_records(after_path)
    if expected_before_sha256 is not None:
        expected = expected_before_sha256.strip().casefold()
        if not expected:
            raise Rx3GuiValidationError("expected_before_sha256 must not be blank")
        if before_hash.casefold() != expected:
            raise Rx3GuiValidationError(
                "the calculated result is not bound to the prepared input: "
                f"generated.rx38 sha256 {before_hash} does not match the pinned "
                f"{expected_before_sha256}"
            )
    if len(before) != len(after):
        raise Rx3GuiValidationError(
            f"Tconstr count changed: before={len(before)}, after={len(after)}"
        )
    target_positions, target_resolution = _resolve_target_positions(
        before,
        target_record_fingerprints=target_record_fingerprints,
        target_record_positions=target_record_positions,
        target_marks=target_marks,
    )

    records: list[dict[str, Any]] = []
    change_sets: list[set[int]] = []
    material_result_change_sets: list[set[int]] = []
    unsafe_change_sets: list[set[int]] = []
    unexpected_non_target_changes: list[dict[str, Any]] = []
    expected_output_fields = {44, 54}
    allowed_calculated_result_fields = expected_output_fields | {52}
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
        unsafe_change_sets.append(
            {
                item["index"]
                for item in changes
                if item["index"] not in allowed_calculated_result_fields
                and item["semantic_changed"] is not False
            }
        )
        is_target = position in target_positions
        if not is_target and changes:
            unexpected_non_target_changes.append(
                {
                    "position": position,
                    "before_mark": old.mark,
                    "after_mark": new.mark,
                    "changes": changes,
                }
            )
        records.append(
            {
                "position": position,
                "is_target": is_target,
                "before_mark": old.mark,
                "after_mark": new.mark,
                "before_record_fingerprint": rx38_record_fingerprint(old),
                "after_record_fingerprint": rx38_record_fingerprint(new),
                "text_changed": bool(changes),
                "semantic_changed": _record_semantic_changed(changes),
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
                        "old_token": item["old_token"],
                        "new_token": item["new_token"],
                        "text_changed": item["text_changed"],
                        "semantic_changed": item["semantic_changed"],
                        "classification": item["classification"],
                    }
                    for item in changes
                    if item["confidence"] == "unknown"
                ],
                "rx3_result": rx38_record_to_rx3_result(
                    new, source_file=after_path
                ).as_dict(),
            }
        )

    byte_identical = before_hash == after_hash
    target_result_fields_changed = {
        position: expected_output_fields.issubset(
            material_result_change_sets[position - 1]
        )
        for position in sorted(target_positions)
    }
    result_fields_changed = all(target_result_fields_changed.values())
    non_target_records_text_unchanged = not unexpected_non_target_changes
    non_target_records_semantically_unchanged = all(
        records[position - 1]["semantic_changed"] is False
        for position in range(1, len(records) + 1)
        if position not in target_positions
    )
    prepared_inputs_preserved = not any(
        unsafe_change_sets[position - 1] for position in sorted(target_positions)
    )
    recalculation_proven = (
        not byte_identical
        and result_fields_changed
        and non_target_records_text_unchanged
        and prepared_inputs_preserved
    )
    evidence_reference_valid = (
        isinstance(evidence_reference, str) and bool(evidence_reference.strip())
    )
    unsafe_production_changes = (
        mode is ExecutionMode.PRODUCTION
        and any(indices for indices in unsafe_change_sets)
    )
    gui_verified = (
        recalculation_proven
        and prepared_inputs_preserved
        and evidence_reference_valid
        and non_target_records_text_unchanged
        and not unsafe_production_changes
        and gui_execution_evidence
        in {
            GuiExecutionEvidence.ENGINEER_CONFIRMED,
            GuiExecutionEvidence.SCREENSHOT_REFERENCED,
        }
    )
    status = (
        "RX3_UNEXPECTED_NON_TARGET_CHANGE"
        if not non_target_records_text_unchanged
        else "RX3_PRODUCTION_INPUTS_CHANGED"
        if unsafe_production_changes
        else "RX3_TARGET_INPUTS_CHANGED"
        if not prepared_inputs_preserved
        else "RX3_RECALCULATION_NOT_PROVEN"
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
        "target_resolution": target_resolution,
        "expected_result_fields": sorted(expected_output_fields),
        "allowed_calculated_result_fields": sorted(
            allowed_calculated_result_fields
        ),
        "expected_result_fields_changed": result_fields_changed,
        "target_result_fields_changed": target_result_fields_changed,
        "non_target_records_text_unchanged": non_target_records_text_unchanged,
        "non_target_records_semantically_unchanged": (
            non_target_records_semantically_unchanged
        ),
        "unexpected_non_target_changes": unexpected_non_target_changes,
        "rx3_recalculation_proven": recalculation_proven,
        "execution_mode": mode.value,
        "gui_execution_evidence": gui_execution_evidence.value,
        "evidence_reference": evidence_reference,
        "evidence_reference_valid": evidence_reference_valid,
        "gui_recalculation_verified": gui_verified,
        "unsafe_production_change_indices": sorted(
            set().union(*unsafe_change_sets) if unsafe_change_sets else set()
        ),
        "target_input_change_indices": sorted(
            {
                index
                for position in sorted(target_positions)
                for index in unsafe_change_sets[position - 1]
            }
        ),
        "prepared_inputs_preserved": prepared_inputs_preserved,
        "recalculation_note": (
            "The recorded result fields 44/54 changed, so the file content changed. This "
            "proves neither that the Calculate button was pressed nor that the computation "
            "is correct: the GUI actions and the meaning of the result are recorded "
            "separately by the engineer."
            if recalculation_proven
            else "Target input fields changed after the input was prepared "
            f"({sorted({index for position in sorted(target_positions) for index in unsafe_change_sets[position - 1]})}): "
            "the file may have been recalculated, but not for the prepared input, so the "
            "result does not confirm the prepared calculation. The full diff is kept below."
            if not prepared_inputs_preserved
            else "Unchanged or partially changed result fields 44/54 prove neither that "
            "the Calculate button was pressed with unchanged numbers nor that it was not "
            "pressed at all: the file content alone cannot separate those two cases. Only "
            "an engineer-recorded GUI observation can, and it must state what the screen "
            "showed."
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
        f"- Target resolution: `{target_resolution}`",
        f"- Expected result fields 44/54 changed: `{result_fields_changed}`",
        f"- Allowed calculated result fields: `{sorted(allowed_calculated_result_fields)}`",
        f"- Non-target records text-unchanged: `{non_target_records_text_unchanged}`",
        f"- Non-target records semantically unchanged: `{non_target_records_semantically_unchanged}`",
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
                f"- Target: `{record['is_target']}`",
                f"- Text changed: `{record['text_changed']}`",
                f"- Semantic changed: `{record['semantic_changed']}`",
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
                        f"- `{item['index']}` {name}: `{item['old_value']}` → `{item['new_value']}` "
                        f"(semantic_changed=`{item['semantic_changed']}`, "
                        f"classification=`{item['classification']}`)"
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
