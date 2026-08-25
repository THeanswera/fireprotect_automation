"""Artifacts and reports for the mandatory manual RX3 GUI checkpoint."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from hashlib import sha256
from itertools import combinations
import json
from pathlib import Path
import shutil
from typing import Any

from ..project_io import read_project_element_json
from .diff import diff_records
from .parser import construction_records, read_rx38
from .project_adapter import create_rx38_from_project_element
from .result import rx38_record_to_rx3_result
from .schema import field_spec


class Rx3GuiValidationError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class Rx3ValidationBundle:
    directory: Path
    template: Path
    generated: Path
    project_element: Path
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
    payload = {
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
    }
    _write_new(diff_json, json.dumps(payload, ensure_ascii=False, indent=2) + "\n")
    _write_new(
        diff_markdown,
        _changes_markdown("Diff template.rx38 → generated.rx38", changes),
    )
    _write_new(
        instructions,
        """# Проверка RX3

1. Откройте `template.rx38` в RX3 и убедитесь, что исходный файл читается.
2. Откройте `generated.rx38` в RX3. Если RX3 показывает ошибку — остановитесь и сохраните текст/скриншот ошибки.
3. Сверьте марку, профиль, длину, количество, сталь, N, закрепление и требуемый R с `project_element.json`.
4. Нажмите расчёт в RX3. Не меняйте инженерные параметры без фиксации изменения.
5. Сохраните рассчитанный файл в этой папке под именем `calculated.rx38`; не перезаписывайте `template.rx38` и `generated.rx38`.
6. Выполните:

   `python -m fireprotect.cli validate-rx3-result generated.rx38 calculated.rx38`

7. Передайте `calculated.rx38`, `rx3_validation_report.json` и `rx3_validation_report.md` обратно в проект.
""",
    )
    return Rx3ValidationBundle(
        directory,
        template,
        generated,
        project_copy,
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
) -> Rx3ValidationReport:
    before_path = Path(before_rx38).resolve(strict=True)
    after_path = Path(after_rx38).resolve(strict=True)
    before = construction_records(read_rx38(before_path))
    after = construction_records(read_rx38(after_path))
    if len(before) != len(after):
        raise Rx3GuiValidationError(
            f"Tconstr count changed: before={len(before)}, after={len(after)}"
        )

    records: list[dict[str, Any]] = []
    change_sets: list[set[int]] = []
    for position, (old, new) in enumerate(zip(before, after), 1):
        changes = [_change_dict(item) for item in diff_records(old, new)]
        change_sets.append({item["index"] for item in changes})
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

    payload = {
        "status": "GUI_RESULT_ANALYSED_NOT_NORMATIVELY_VERIFIED",
        "before": {"path": str(before_path), "sha256": _sha256(before_path)},
        "after": {"path": str(after_path), "sha256": _sha256(after_path)},
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
    for target in (json_path, md_path):
        if target.exists() and not overwrite:
            raise Rx3GuiValidationError(f"Report already exists: {target}")
        target.parent.mkdir(parents=True, exist_ok=True)
    json_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
        newline="\n",
    )

    lines = [
        "# Отчёт проверки результата RX3",
        "",
        "Статус: **GUI_RESULT_ANALYSED_NOT_NORMATIVELY_VERIFIED**.",
        "",
        f"- BEFORE SHA-256: `{payload['before']['sha256']}`",
        f"- AFTER SHA-256: `{payload['after']['sha256']}`",
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
