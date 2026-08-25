# Сквозной MVP: ЛИРА → RX3 → Excel

Команда `pipeline` создаёт воспроизводимый рабочий каталог, но не запускает
и не подменяет RX3. Пути в JSON разрешаются относительно самого файла
конфигурации.

## Конфигурация

```json
{
  "schema_version": 1,
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
    "template": "01_Общая ОБМ — копия.xlsx",
    "output": "pipeline_runs/first_run/result.xlsx"
  }
}
```

`format` может быть `csv`, `html` или `xlsx`; колонки и единицы задаются
явно. Каждый селектор должен найти ровно одну строку ЛИРА. Значение R в
`ProjectElement` должно точно совпадать с `RequiredFireResistanceDecision`.

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
`project_element.json`, diff и `README_VALIDATION.md`.

1. Откройте `template.rx38` в RX3 и убедитесь, что шаблон читается.
2. Откройте `generated.rx38`; при ошибке остановитесь и сохраните текст или скриншот.
3. Сверьте марку, профиль, длину, количество, сталь, N, закрепление и R с `project_element.json`.
4. Нажмите расчёт. Не меняйте инженерные параметры без фиксации.
5. Сохраните файл как `calculated.rx38` в этой же папке, не перезаписывая два исходных файла.
6. Повторите `python -m fireprotect.cli pipeline pipeline.json`.
7. Передайте `calculated.rx38`, `rx3_validation_report.json`,
   `rx3_validation_report.md`, `project_audit.json` и `project_audit.md` на инжерную проверку.

При втором запуске RX3-diff разделяет CONFIRMED, PROBABLE и сырые индексы UNKNOWN,
вносит в `ProjectElement` тольо доказанные результаты и запускает Excel-экспорт,
если он настроен. Статус завершённого прогона — `RX3_RESULT_IMPORTED_PIPELINE_COMPLETE`.
Он подтверждает импорт переданного файла, но не доказывает факт запуска GUI,
нормативную верификацию или готовность к выпуску.
