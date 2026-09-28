# Changelog

Pre-experiment hardening in version 0.5.0:

- typed release evidence replaces boolean gate self-certification; heating
  exposure and steel temperature profiles fail closed in VALIDATION/PRODUCTION;
- LIRA XLSX numeric cells are read from exact OOXML tokens;
- Excel production templates are selected by trusted `template_id` and checked
  against formula and lookup-table fingerprints linked to technical-data
  versions;
- RX38/XLSX no-overwrite finalization has an exclusive-create fallback for
  filesystems without hard-link support;
- the post-calculation RX3 check requires an independently pinned BEFORE
  SHA-256 (CLI flag, recorded bundle hash, prepared manual command) instead of
  hashing the file under validation again;
- the post-calculation check compares the whole RX38 document, not only the
  target `Tconstr`: added, removed and edited records, non-construction records,
  encoding, BOM, blank lines, line endings and raw token spelling block the GUI
  status (`RX3_DOCUMENT_STRUCTURE_CHANGED`, `RX3_RAW_FORMATTING_CHANGED`);
- every paged LIRA force workbook pins its own summary in the evidence, and the
  reader recomputes that summary from the re-read page and compares it exactly;
  a page without a pinned summary is refused as `SUMMARY_NOT_VERIFIED`, and a
  paged entry must record the first page's integer worksheet count;
- the post-calculation check also proves record identity and order: `Tconstr`
  identity keys mask the fields a calculation may rewrite, so two selected
  targets that are indistinguishable outside those fields are refused as
  `RX3_TCONSTR_IDENTITY_AMBIGUOUS` instead of being read as two valid result
  changes, and a byte exchange of two records is refused as
  `RX3_TCONSTR_ORDER_CHANGED`;
- the terminator of every physical line is compared, empty lines included, so an
  internal CRLF -> LF rewrite can no longer hide behind unchanged record
  numbers, blank-line numbers and trailing newline;
- the pipeline takes the expected `generated.rx38` SHA-256 from the generation
  manifest (`diff_before_after.json:generated.sha256`) instead of hashing the
  file under validation again; the fresh hash is kept only as `actual_sha256`;
- the RSU residual statistics now count the print window of each compared value
  (half a unit of its own last printed digit) and the excess in units in the last
  place of a single-precision result of the published magnitude, instead of a
  uniform six-decimal envelope; exact equality stays the only acceptance rule for
  `VERIFIED`;
- the LIRA export precision cannot be changed, so the RSU reconstruction has an
  explicit second acceptance status: `VERIFIED_WITHIN_EXPORT_PRECISION` accepts a
  row whose every component stays inside the print window plus one
  single-precision ulp of the largest magnitude, keeps every printed value, cell,
  coefficient, hash and residual exact, is written into the evidence bundle,
  re-verified on read and visible on row selection; a residual beyond that bound
  still blocks the row with `RSU_RESULT_MISMATCH`. Measured on two real projects:
  43 337 residuals, none beyond the bound (largest 0.865 ulp32), while a wrong
  coefficient column starts at 4 127 ulp32.

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
