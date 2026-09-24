"""Review workbook for one controlled LIRA bar experiment.

This is deliberately **not** the project's calculation workbook. It is a new,
self-describing file that shows what was verified, what was selected and what
is still blocked. RX3 result cells stay empty until a real calculation exists:
no critical temperature, fire resistance or utilisation is ever invented here.

The project calculation workbook keeps its own copy-only adapter
(`export_obm_workbook`); this module neither reads nor writes it.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Mapping

from openpyxl import Workbook
from openpyxl.styles import Alignment, Font
from openpyxl.utils import get_column_letter

REVIEW_KIND = "LIRA_BAR_EXPERIMENT_REVIEW_WORKBOOK"
STATUS_NOTE = (
    "УЧЕБНЫЙ ОПЫТ. НЕ ДЛЯ ВЫПУСКА. Это обзорный файл подготовки, "
    "а не расчётная книга ОБМ."
)
_UNCONFIRMED = "ожидает ручного расчёта RX3 — не заполнено"

_HEADER_FONT = Font(bold=True)
_WRAP = Alignment(wrap_text=True, vertical="top")


class ReviewWorkbookError(ValueError):
    """Raised when the review workbook cannot be produced safely."""


def _read_json(path: Path, description: str) -> Mapping[str, Any]:
    if not path.is_file():
        raise ReviewWorkbookError(f"{path}: {description} not found")
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, Mapping):
        raise ReviewWorkbookError(f"{path}: {description} must be a JSON object")
    return payload


def _write_table(
    sheet: Any, rows: list[list[object]], widths: list[int] | None = None
) -> None:
    for row in rows:
        sheet.append(row)
    if rows:
        for cell in sheet[1]:
            cell.font = _HEADER_FONT
    if widths:
        for index, width in enumerate(widths, start=1):
            sheet.column_dimensions[get_column_letter(index)].width = width


def _status_rows(
    manifest: Mapping[str, Any], prepared: Mapping[str, Any] | None
) -> list[list[object]]:
    return [
        ["Показатель", "Значение"],
        ["Статус файла", STATUS_NOTE],
        ["Идентификатор запуска", manifest.get("run_id")],
        ["Статус запуска", manifest.get("status")],
        ["Строка технического опыта", manifest.get("experiment_row_id")],
        [
            "Определяющее сочетание",
            "не назначено (governing_result_selection = null)",
        ],
        [
            "Подпись инженера",
            "НЕТ — декларация DRAFT_UNSIGNED"
            if not manifest.get("engineer_confirmed")
            else "есть",
        ],
        [
            "Файл RX3 для ручного расчёта",
            (prepared or {}).get("path") or "не подготовлен",
        ],
        ["Выпуск документации", "запрещён (NOT_READY_FOR_ISSUE)"],
        ["Источник", manifest.get("source_package")],
        [
            "Постановка опыта",
            (manifest.get("plan_reference") or {}).get("path"),
        ],
    ]


def _bar_rows(manifest: Mapping[str, Any]) -> list[list[object]]:
    bar = manifest.get("bar") or {}
    profile = manifest.get("profile") or {}
    rows: list[list[object]] = [
        ["Параметр", "Значение", "Источник"],
        ["Идентификатор стержня", bar.get("bar_id"), "декларация опыта"],
        ["Конечные элементы", ", ".join(bar.get("element_ids") or []), "модель ЛИРА"],
        ["Концевые узлы", " → ".join(bar.get("end_node_ids") or []), "модель ЛИРА"],
        [
            "Длины КЭ, м",
            ", ".join(
                f"{key} = {value}"
                for key, value in (bar.get("element_lengths_m") or {}).items()
            ),
            "координаты узлов",
        ],
        [
            "Геометрическая длина, м",
            bar.get("bar_length_m"),
            "сумма КЭ (не расчётная длина устойчивости)",
        ],
        ["Тип сечения", profile.get("kind_word"), "XLS жёсткостей"],
        ["Профиль", profile.get("designation"), "XLS жёсткостей"],
        ["Марка", profile.get("mark"), "XLS жёсткостей"],
        ["Стандарт", profile.get("standard"), "документ / заявление"],
        ["Поворот местных осей, °", profile.get("rotation_degrees"), "XLS элементов"],
        ["Плоскость изгиба", profile.get("plane"), "сообщение пользователя"],
        ["Признак схемы", profile.get("scheme_flag"), "сообщение пользователя"],
        ["Шаблон RX3", profile.get("rx3_template"), "постановка опыта"],
        ["Напряжённое состояние", profile.get("stress_state"), "постановка опыта"],
        ["Основание объединения КЭ", bar.get("join_basis"), "декларация опыта"],
    ]
    return rows


def _row_rows(experiment: Mapping[str, Any]) -> list[list[object]]:
    selected = experiment.get("selected_row") or {}
    row = selected.get("row") or {}
    rows: list[list[object]] = [
        ["Параметр", "Значение"],
        ["row_id", selected.get("row_id")],
        ["Элемент", selected.get("element_id")],
        ["Сечение (станция)", selected.get("section_station")],
        ["Группа РСУ", selected.get("rsu_group")],
        ["Критерий", selected.get("rsu_criterion")],
        ["Столбец коэффициентов", selected.get("rsu_column_number")],
        ["Состав загружений", " ".join(selected.get("load_case_membership") or [])],
        ["Основание выбора", selected.get("selection_basis")],
    ]
    terms = row.get("source_terms")
    if isinstance(terms, list) and terms:
        rows.append([])
        rows.append(
            [
                "Загружение",
                "Коэффициент",
                "N, tf",
                "Mk, tf·m",
                "My, tf·m",
                "Mz, tf·m",
                "Qy, tf",
                "Qz, tf",
                "Ячейка коэффициента",
            ]
        )
        for term in terms:
            if not isinstance(term, Mapping):
                continue
            forces = term.get("forces") or {}
            coefficient_source = term.get("coefficient_source") or {}
            rows.append(
                [
                    term.get("load_case_id"),
                    term.get("coefficient"),
                    *[
                        (forces.get(name) or {}).get("value")
                        for name in ("N", "Mk", "My", "Mz", "Qy", "Qz")
                    ],
                    coefficient_source.get("cell"),
                ]
            )
    return rows


def _force_rows(manifest: Mapping[str, Any]) -> list[list[object]]:
    rows: list[list[object]] = [
        [
            "Компонент",
            "Значение источника",
            "Единица источника",
            "Ячейка",
            "Обзорное значение",
            "Обзорная единица",
            "Поле RX3",
            "Преобразование",
            "Конвенция подтверждена",
        ]
    ]
    for item in manifest.get("components") or []:
        convention = item.get("convention") or {}
        rows.append(
            [
                item.get("component"),
                item.get("source_value"),
                item.get("source_unit"),
                item.get("source_cell"),
                item.get("review_value"),
                item.get("review_unit"),
                convention.get("target"),
                convention.get("value_transform"),
                "да" if convention.get("resolved") else "нет",
            ]
        )
    return rows


def _condition_rows(manifest: Mapping[str, Any]) -> list[list[object]]:
    rows: list[list[object]] = [["Условие", "Значение", "Кем/чем задано", "Основание"]]
    decisions = manifest.get("decisions") or {}
    design = manifest.get("design_conditions") or {}
    bar_decision = decisions.get("design_conditions") or {}
    for name, value in design.items():
        rows.append(
            [
                name,
                value if value is not None else "не задано",
                bar_decision.get("role"),
                bar_decision.get("basis"),
            ]
        )
    rows.append([])
    rows.append(["Решение", "Роль", "Основание", "Ссылка"])
    for name, record in decisions.items():
        if not isinstance(record, Mapping):
            continue
        rows.append(
            [
                name,
                record.get("role"),
                record.get("basis"),
                record.get("reference"),
            ]
        )
    return rows


def _source_rows(manifest: Mapping[str, Any]) -> list[list[object]]:
    rows: list[list[object]] = [
        ["Роль", "Файл", "SHA-256 записи", "SHA-256 сейчас", "Совпадение"]
    ]
    for name, entry in (manifest.get("source_files") or {}).items():
        if not isinstance(entry, Mapping):
            continue
        rows.append(
            [
                name,
                entry.get("path"),
                entry.get("recorded_sha256"),
                entry.get("current_sha256"),
                "да"
                if entry.get("recorded_sha256") == entry.get("current_sha256")
                else "НЕТ",
            ]
        )
    drift = manifest.get("source_drift_accepted")
    if drift:
        rows.append([])
        rows.append(["Принятое расхождение источников", "", "", "", ""])
        rows.append(["Роль", "Файл", "Записано", "Сейчас", "Состояние"])
        for item in drift:
            rows.append(
                [
                    item.get("role"),
                    item.get("path"),
                    item.get("recorded_sha256"),
                    item.get("actual_sha256"),
                    item.get("state"),
                ]
            )
    return rows


def _blocker_rows(manifest: Mapping[str, Any]) -> list[list[object]]:
    rows: list[list[object]] = [["Пункт", "Значение"]]
    rows.append(
        [
            "Блокировки передачи",
            ", ".join(manifest.get("blockers") or []) or "отсутствуют",
        ]
    )
    for name in manifest.get("missing_confirmations") or []:
        rows.append(["Не подтверждено документацией", name])
    rows.append(
        [
            "Определяющая строка",
            "не выбиралась; правило max(abs(...)) не применялось",
        ]
    )
    rows.append(
        ["Расчёт RX3", "выполняется инженером вручную; автоматический запуск запрещён"]
    )
    rows.append(
        ["Выпуск", "NOT_READY_FOR_ISSUE — нормативная проверка не завершена"]
    )
    return rows


def _result_rows(manifest: Mapping[str, Any], prepared: Mapping[str, Any] | None) -> list[list[object]]:
    rows: list[list[object]] = [
        ["Показатель", "Значение", "Источник"],
        ["Критическая температура, °C", None, _UNCONFIRMED],
        ["Собственный предел огнестойкости, мин", None, _UNCONFIRMED],
        ["Коэффициент использования по моменту", None, _UNCONFIRMED],
        ["Коэффициент использования по поперечной силе", None, _UNCONFIRMED],
        ["Требуемая толщина огнезащиты, мм", None, "индекс RX38 не подтверждён"],
        ["Расход материала", None, "нормативный источник не подтверждён"],
    ]
    if prepared:
        rows.append([])
        rows.append(["Подготовленный файл RX3", prepared.get("path"), REVIEW_KIND])
        rows.append(
            [
                "Целевая запись",
                f"позиция {prepared.get('position')}",
                "Tconstr",
            ]
        )
        rows.append(
            [
                "Отпечаток записи после подготовки",
                prepared.get("after_fingerprint"),
                "SHA-256 всех 200 полей",
            ]
        )
        rows.append(
            [
                "Расчётная длина и закрепление",
                prepared.get("effective_length_status") or "не подтверждено",
                prepared.get("effective_length_basis") or "нет основания",
            ]
        )
        rows.append(
            [
                "Расчёт запускает",
                "инженер вручную в RX3",
                "автоматический запуск запрещён",
            ]
        )
        rows.append(
            [
                "Подтверждение инженера",
                "ещё не получено",
                "calculated.rx38 сам по себе подтверждением не является",
            ]
        )
    return rows


def export_lira_bar_review(
    *,
    run_manifest: str | Path,
    output: str | Path,
) -> dict[str, object]:
    """Write one new review workbook; never overwrite an existing file."""

    manifest_path = Path(run_manifest).resolve(strict=True)
    manifest = _read_json(manifest_path, "run manifest")
    if manifest.get("kind") != "LIRA_BAR_RUN_MANIFEST":
        raise ReviewWorkbookError(
            f"{manifest_path}: kind must be 'LIRA_BAR_RUN_MANIFEST'"
        )
    target = Path(output).resolve(strict=False)
    if target.exists():
        raise ReviewWorkbookError(
            f"review workbook already exists; refusing to overwrite: {target}"
        )
    if target.suffix.lower() != ".xlsx":
        raise ReviewWorkbookError(f"{target}: output must be an .xlsx file")
    run_dir = manifest_path.parent
    experiment_path = run_dir / "experiment" / "experiment_input.json"
    experiment = (
        _read_json(experiment_path, "experiment input")
        if experiment_path.is_file()
        else {}
    )
    prepared_manifest_path = run_dir / "rx3_input" / "rx3_validation_manifest.json"
    prepared: Mapping[str, Any] | None = None
    if prepared_manifest_path.is_file():
        prepared_raw = _read_json(prepared_manifest_path, "RX3 preparation manifest")
        target_block = prepared_raw.get("target") or {}
        generated = prepared_raw.get("generated") or {}
        effective = prepared_raw.get("effective_length_and_support") or {}
        evidence = effective.get("algorithm_evidence") or {}
        source = evidence.get("source") or {}
        prepared = {
            "path": generated.get("path"),
            "position": target_block.get("position"),
            "after_fingerprint": target_block.get("after_fingerprint"),
            "effective_length_status": effective.get("status"),
            "effective_length_basis": " ".join(
                part
                for part in (
                    str(evidence.get("finding") or ""),
                    str(effective.get("not_substituted") or ""),
                    (
                        f"Источник: {source.get('path')} — {source.get('section')}, "
                        f"{source.get('bending_subsection')}."
                        if source
                        else ""
                    ),
                )
                if part
            ),
        }

    workbook = Workbook()
    status_sheet = workbook.active
    assert status_sheet is not None
    status_sheet.title = "Статус"
    _write_table(status_sheet, _status_rows(manifest, prepared), [34, 70])
    _write_table(workbook.create_sheet("Идентичность"), _bar_rows(manifest), [30, 46, 34])
    _write_table(workbook.create_sheet("Строка РСУ"), _row_rows(experiment), [22, 18, 14, 14, 14, 14, 14, 14, 20])
    _write_table(
        workbook.create_sheet("Усилия"),
        _force_rows(manifest),
        [12, 16, 16, 10, 18, 16, 32, 16, 20],
    )
    _write_table(
        workbook.create_sheet("Условия"),
        _condition_rows(manifest),
        [28, 26, 24, 60],
    )
    _write_table(workbook.create_sheet("Источники"), _source_rows(manifest), [18, 60, 66, 66, 12])
    _write_table(
        workbook.create_sheet("Блокеры"),
        _blocker_rows(manifest),
        [40, 70],
    )
    _write_table(
        workbook.create_sheet("Результат RX3"),
        _result_rows(manifest, prepared),
        [42, 34, 46],
    )
    for sheet in workbook.worksheets:
        for row in sheet.iter_rows():
            for cell in row:
                if isinstance(cell.value, str) and len(cell.value) > 60:
                    cell.alignment = _WRAP
    target.parent.mkdir(parents=True, exist_ok=True)
    workbook.save(target)
    return {
        "kind": REVIEW_KIND,
        "status": "REVIEW_WORKBOOK_WRITTEN",
        "run_manifest": str(manifest_path),
        "output": str(target),
        "sheets": list(workbook.sheetnames),
        "calculation_results_included": False,
        "release_forbidden": True,
        "issue_readiness": "NOT_READY_FOR_ISSUE",
    }


__all__ = [
    "REVIEW_KIND",
    "STATUS_NOTE",
    "ReviewWorkbookError",
    "export_lira_bar_review",
]
