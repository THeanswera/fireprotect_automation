# Changelog

Pre-experiment hardening in version 0.5.0:

- typed release evidence replaces boolean gate self-certification; heating
  exposure and steel temperature profiles fail closed in VALIDATION/PRODUCTION;
- LIRA XLSX numeric cells are read from exact OOXML tokens;
- Excel production templates are selected by trusted `template_id` and checked
  against formula and lookup-table fingerprints linked to technical-data
  versions;
- RX38/XLSX no-overwrite finalization has an exclusive-create fallback for
  filesystems without hard-link support.

## 0.5.0 — 2026-08-25

- Добавлены execution modes `DRAFT`, `VALIDATION`, `PRODUCTION` и центральный
  release gate `IssueReadiness`; это не означает production readiness.
- Ненулевые `Mx/My/Qx/Qy` теперь блокируют RX38 до подтверждения mappings;
  осевой validation path требует отдельного `AXIAL_ONLY` evidence.
- Добавлены явная LIRA -> RX3 force convention, typed steel compatibility и
  `Rx3CalculationProfile` с fingerprint неизвестных полей.
- `Rx3Input` отделён от `Rx3Result`; template outputs помечаются stale, а
  byte-identical generated/calculated не считается пересчётом RX3.
- Normative registry стал production gate по document id, статусу редакции,
  дате действия и SHA-256; добавлен registry первичной технической
  документации огнезащиты.
- Excel остаётся copy-only compatibility export и получает обязательный
  `EXCEL_RECALCULATION_REQUIRED` до пересчёта в Microsoft Excel.
- Добавлены controlled experiment CLI/protocol, negative safety suite,
  GitHub Actions для Python 3.11/3.12, ruff и mypy.

## 0.4.0 — 2026-08-25

- Добавлен двусторонний мост RX3: `Rx3Result` извлекает только
  CONFIRMED-поля RX38 с provenance `RX3_RESULT`, а PROBABLE и UNKNOWN
  остаются явно отделены.
- Добавлены `prepare-rx3-validation` и `validate-rx3-result` для
  безопасного ручного GUI-checkpoint и автоматического анализа результа.
- Реализован `RequiredFireResistanceDecision` с явным инженерным
  подтверждением и отдельной нормативной трассировкой.
- Реализован типизированный экспорт 44 элементов в исследованную
  книгу ОБМ: записываются только подтверждённые входы, проверяются ZIP,
  openpyxl, SHA-256 и точная карта 579 формул.
- Добавлен `docs/EXCEL_FORMULA_AUDIT.md`; таблицы толщины и расхода
  помечены `UNVERIFIED_TECHNICAL_DATA`, зафиксировано расхождение R90.
- Добавлен сквозной `pipeline`: импорт ЛИРА, RX38, обязательная
  остановка RX3, возобновление после `calculated.rx38`, Excel и JSON/Markdown audit.
- Исправлены доказанные утечки `float` в RX38 и ЛИРА; расчётные
  значения теперь остаются `Decimal`.

## 0.3.0 — 2026-08-25

- Добавлена центральная модель `ProjectElement` с `Decimal`-величинами,
  явными единицами и обязательным provenance каждого заполненного значения.
- Реализованы `NormativeTrace`/`NormativeResult` и production-блокировка
  нормативного результата без источника.
- Добавлены конфигурируемые адаптеры усилий ЛИРА для CSV, HTML и XLSX и
  преобразование импортированной строки в `ProjectElement`.
- Проанализированы пять листов и 579 формул рабочей Excel-книги; добавлен
  copy-only OOXML writer с защитой формул, стилей и исходного файла.
- Добавлен единый geometry-модуль и регрессии с формулами Excel для четырёх
  семейств профилей.
- Добавлены template-based `ProjectElement -> RX38`, CLI `rx38-create`,
  атомарная запись, round-trip отчёт и запрет перезаписи шаблона/output.
- Поле-копия марки RX38 подтверждено по всем 46 записям корпуса; актуальные
  счётчики схемы: 56 confirmed / 13 probable / 131 unknown.

## 0.2.0 — 2026-08-25

- Переписан RX38 parser с проверкой 200-позиционной строки и без потери исходной лексики.
- Добавлены доказательная схема полей, `Rx38Construction`, safe writer и round-trip тесты.
- Добавлены `rx38_diff.py` и `group-by-profile`.
- Реализован `ProfileRepository` с нормализацией и явным статусом `AMBIGUOUS`.
- Добавлены инвентаризация RX3, полная карта полей, реестр нормативных файлов, traceability и открытые вопросы.
- Исправлен mojibake в исполняемом коде и настроен импорт `src` для pytest.
