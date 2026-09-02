# Сквозной MVP: ЛИРА → RX3 → Excel

## Текущая безопасная точка входа: batch review

До доказательства реальной LIRA↔RX3 sign/local-axis convention используется
только review-команда:

```powershell
python -m fireprotect.cli prepare-lira-review `
  --input <export.xlsx|export.csv|export.html> `
  --mapping <mapping.json> `
  --output-dir <new-directory>
```

Она использует существующие CSV/HTML/XLSX adapters и `ProjectElement`, сохраняет
raw numeric token + Decimal + source unit + SI conversion + row/cell provenance,
создаёт hashes, audit, blockers и human-readable CSV, но никогда не пишет RX38.
Default component convention — `UNKNOWN`; `ENGINEER_CONFIRMED` можно явно
зафиксировать, но только `VALIDATED` считается разрешённым convention state.
В этой итерации даже `VALIDATED` state не открывает writer: review result всегда
имеет `rx38_force_generation_allowed=false`.

Ниже описан более широкий исторически реализованный pipeline. Его RX38 stage
остаётся закрытым production gates и не должен использовать scoped fields
49/50/79/92 как универсальное LIRA mapping.

Команда `pipeline` создаёт воспроизводимый рабочий каталог, но не запускает
и не подменяет RX3. Пути в JSON разрешаются относительно самого файла
конфигурации.

## Конфигурация

```json
{
  "schema_version": 2,
  "execution_mode": "VALIDATION",
  "calculation_date": "2026-08-25",
  "workspace": "pipeline_runs/first_run",
  "lira": {
    "format": "csv",
    "path": "exports/forces.csv",
    "options": {"encoding": "utf-8-sig", "delimiter": ";", "header_row": 1},
    "decimal_separator": ",",
    "thousands_separator": " ",
    "columns": {
      "element_id": "Элемент",
      "section": "Сечение",
      "load_case": "Загружение",
      "combination": "Сочетание",
      "N": "N", "Mx": "Mx", "My": "My", "Qx": "Qx", "Qy": "Qy"
    },
    "units": {"N": "kN", "Mx": "kN*m", "My": "kN*m", "Qx": "kN", "Qy": "kN"}
  },
  "elements": [
    {
      "project_json": "elements/K1.json",
      "lira_element_id": "17",
      "load_case": "LC1",
      "combination": "C1",
      "set_governing_combination": true,
      "rx38_template": "templates/K1.rx38",
      "template_mark": "K1",
      "gui_execution_evidence": "ENGINEER_CONFIRMED",
      "gui_evidence_reference": "RX3-EXP-01",
      "rx3_safety": {
        "template_evidence": {
          "use_case": "AXIAL_ONLY",
          "status": "VERIFIED",
          "source": "controlled experiment RX3-EXP-01",
          "engineer_confirmation": true,
          "confirmed_by": "engineer name",
          "confirmed_at": "2026-08-25",
          "version": "1",
          "calculation_profile_verified": true,
          "template_record_sha256": "<sha256 of the exact 200-field Tconstr record>"
        },
        "force_convention": {
          "source_system": "LIRA export version",
          "target_system": "RX3 version",
          "positive_n_meaning": "documented meaning",
          "negative_n_meaning": "documented meaning",
          "local_axes": "documented axes",
          "moment_mapping": "Mx/My evidence description",
          "shear_mapping": "Qx/Qy evidence description",
          "multipliers": {"N": "1", "Mx": "1", "My": "1", "Qx": "1", "Qy": "1"},
          "rule_name": "verified rule id",
          "evidence_source": "controlled protocol id",
          "status": "VERIFIED",
          "engineer_confirmation": true,
          "confirmed_by": "engineer name",
          "confirmed_at": "2026-08-25",
          "version": "1"
        }
      },
      "required_fire_resistance_decision": {
        "R": {"value": "90", "unit": "min"},
        "construction_type": "column",
        "building_fire_resistance_degree": "II",
        "source": "engineer assignment pending primary-source verification",
        "clause_or_table": null,
        "assignment_method": "explicit engineer input",
        "engineer_confirmation": true
      }
    }
  ],
  "excel": {
    "template_id": "OBM_WORKBOOK_UNVERIFIED",
    "template": "01_Общая ОБМ — копия.xlsx",
    "output": "pipeline_runs/first_run/result.xlsx"
  }
}
```

The run selects only `template_id`; it cannot supply a verified SHA. Approval,
formula-map identity, technical-data version and lookup fingerprint come from
the repository-owned `templates/excel_registry.yaml`. The current OBM snapshot
remains `UNVERIFIED` until primary technical data are reviewed.

`format` может быть `csv`, `html` или `xlsx`; колонки и единицы задаются
явно. Каждый селектор должен найти ровно одну строку ЛИРА. Значение R в
`ProjectElement` должно точно совпадать с `RequiredFireResistanceDecision`.
Schema v2 требует `execution_mode` и `calculation_date`. Показанные evidence
значения являются формой конфигурации, а не готовыми production-данными: их
можно помечать `VERIFIED` только после фактического controlled protocol.
Для `VALIDATION` и `PRODUCTION` поле `template_record_sha256` должно связывать
evidence с точно тем 200-польным `Tconstr`, который используется при генерации.
`set_governing_combination=true` фиксирует явный инженерный selector и не
доказывает математический максимум сочетаний.

Текущий Excel-экспорт намеренно узок: он применим только к исследованному
44-строчному шаблону. Профиль, площадь, периметр, число элементов и стороны
обогрева должны совпасть с фиксированной строкой. Записываются только `C`
(марка), `J` (длина), `R` (площадь) и `X` (R). Пересчёт формул выполняет Microsoft Excel.

## Первый запуск и GUI-checkpoint

```powershell
python -m fireprotect.cli pipeline pipeline.json
```

Первое выполнение завершается статусом `WAITING_FOR_RX3`. Для каждого элемента в
`workspace/rx3/NNN_element/` создаются `template.rx38`, `generated.rx38`,
`project_element.json`, `rx3_input.json`, `rx3_template_profile.json`, diff и
`README_VALIDATION.md`.

1. Откройте `template.rx38` в RX3 и убедитесь, что шаблон читается.
2. Откройте `generated.rx38`; при ошибке остановитесь и сохраните текст или скриншот.
3. Сверьте марку, профиль, сталь, N, Mx/My/Qx/Qy, длину, закрепление,
   effective length, fire regime, R и critical-temperature mode с bundle.
4. Нажмите расчёт. Не меняйте инженерные параметры без фиксации.
5. Сохраните файл как `calculated.rx38` в этой же папке, не перезаписывая два исходных файла.
6. Повторите `python -m fireprotect.cli pipeline pipeline.json`.
7. Передайте `calculated.rx38`, `rx3_validation_report.json`,
   `rx3_validation_report.md`, `project_audit.json` и `project_audit.md` на инжерную проверку.

При втором запуске RX3-diff разделяет CONFIRMED, PROBABLE и сырые индексы
UNKNOWN, вносит в `ProjectElement` только доказанные результаты и запускает
Excel-export, если он настроен. Возможные статусы: `RX3_RESULT_ANALYSED`,
`RX3_GUI_RECALCULATION_UNVERIFIED` и `RX3_RECALCULATION_NOT_PROVEN`.
Byte-identical calculated/generated блокируется. Финальный
`issue_readiness.status` остаётся `NOT_READY_FOR_ISSUE`, пока не закрыты все
нормативные, технические, GUI и Excel blockers.
