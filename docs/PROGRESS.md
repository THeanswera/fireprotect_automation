# Прогресс

## Этап 1 — RX38 Schema Reverse Engineering

- **CONFIRMED:** найдено 5 RX38, из них 4 уникальных; 46 уникально
  учитываемых `Tconstr`; длина строки — 200 полей.
- **CONFIRMED:** parser сохраняет исходные токены, кавычки, decimal comma,
  newline и кодировку; safe writer разрешает менять только доказанные поля.
- **CONFIRMED:** `Rx38Construction`, `rx38_diff.py`, `group-by-profile` и
  `ProfileRepository` работают без автоматического разрешения неоднозначности.
- **CONFIRMED:** 56 полей имеют достаточное доказательство; синхронность полей
  1 и 3 марки подтверждена на всех 46 записях корпуса.
- **PROBABLE:** 14 полей имеют сильное, но пока не однозначное сопоставление и
  остаются недоступными для записи.
- **UNKNOWN:** 128 полей сохраняются как raw и не интерпретируются.

## Этап 2 — обмен через ProjectElement

- **CONFIRMED:** `ProjectElement` содержит все требуемые группы данных; каждая
  физическая величина несёт единицу и преобразуется через `Decimal`.
- **CONFIRMED:** каждое заполненное проектное поле требует provenance;
  молчаливые инженерные значения по умолчанию не используются.
- **CONFIRMED:** `NormativeTrace` и `NormativeResult` блокируют подтверждение
  нормативного результата без ссылки в production-режиме.
- **CONFIRMED:** LIRA-архитектура импортирует CSV, HTML и XLSX через полностью
  настраиваемое сопоставление девяти колонок и явные единицы усилий; будущий
  LiraAPI подключается через тот же `LiraRowSource`.
- **CONFIRMED:** изучены все 5 листов исходной Excel-книги и 579 формул;
  `fireprotect.excel` создаёт только копию OOXML, защищает формулы и стили и
  проверяет SHA-256 исходника.
- **CONFIRMED:** единый geometry-модуль вычисляет полный/обогреваемый периметр,
  ПТМ, section factor и площадь обработки. Регрессии для двутавра, замкнутого
  профиля, швеллера и уголка совпадают с формулами исследованной книги.
- **CONFIRMED:** `rx38-create` переносит в совместимый шаблон только
  подтверждённые индексы, сохраняет неизвестные поля, выполняет round-trip и
  выдаёт отчёт об изменениях и предупреждениях. Шаблон и существующий output
  не перезаписываются.
- **PROBABLE:** единица толщины слоя в колонке `Y` Excel — мм по контексту, но
  в основном заголовке она не указана.
- **UNKNOWN:** реальный формат будущего экспорта ЛИРА и назначение нескольких
  служебных блоков Excel пока отсутствуют в исходных данных.
- **BLOCKED:** запись Mx/My/Qx/Qy, материала и сторон обогрева в RX38 до
  доказательства индексов; ненулевые Mx/My/Qx/Qy теперь останавливают
  генерацию, а не остаются из шаблона с warning.
- **BLOCKED:** production-подбор толщины и расхода до идентификации нормативной
  редакции и технической документации конкретного материала.
- **BLOCKED:** выпуск результата до GUI smoke-test созданного файла в RX3 и
  пересчёта изменённой книги в Microsoft Excel.

## Этап 3 — сквозной инженерный MVP

- **CONFIRMED:** `Rx3Result` извлекает из RX38 доказанные поля, сохраняет
  PROBABLE отдельно и не приписывает семантику 128 UNKNOWN-индексам.
- **CONFIRMED:** каждое типизированное значение RX3 имеет provenance
  `RX3_RESULT`; при обратном переносе с ProjectElement сверяются марка,
  профиль, геометрия, N, сталь и R.
- **CONFIRMED:** `prepare-rx3-validation` создаёт изолированный набор файлов
  без перезаписи; `validate-rx3-result` создаёт JSON/Markdown diff и явно
  помечает кандидаты зависимостей как непричинные наблюдения.
- **CONFIRMED:** 579 формул разнесены по классам A/B/C/D/E: `264/132/0/180/3`.
  Таблицы толщины/расхода и лист `ТР` не признаются ни техническим,
  ни нормативным первичным источником.
- **CONFIRMED:** Excel-export изменяет только `C/J/R/X` в копии фиксированного
  44-строчного шаблона; геометрия и неоднозначные `G/H/E` должны уже
  совпадать. После записи проверяются ZIP, openpyxl и неизменность всех
  579 формул.
- **CONFIRMED:** `pipeline` связывает табличный импорт ЛИРА, ProjectElement,
  RX38, обязательную ручную паузу RX3, обратный импорт, Excel и
  `project_audit.json/.md`.
- **PROBABLE:** совместно изменяющиеся RX38-индексы выводятся как кандидаты
  зависимостей; один прогон не доказывает семантику или причинность.
- **CONFIRMED (узкая область):** RX3-EXP-02B подтвердил field50 как GUI Mx для
  one-plane X-X / Б1 и ручную цепочку до persisted RX38. Write policy остаётся
  `EXPERIMENTAL`; field78 только `PROBABLE`.
- **CONFIRMED (узкая область):** RX3-EXP-03 подтвердил field92 как RX3 GUI Q
  input только для Б1 / 14Б2 / one-plane X-X. GUI Q `2,32 → 3,00`, Q
  utilisation `0,028 → 0,037`; persisted field92=`3`, field50/78 и все
  non-target records неизменны. Write policy остаётся `EXPERIMENTAL`.
- **VALIDATED / AWAITING SCOPED PROMOTION REVIEW:** RX3-EXP-04B подтвердил
  цепочку field79 `4,3414 → 5,00` → GUI My=`5,00` → manual Calculate/Save →
  persisted field79=`5`. Mx persisted через field78=`0,51`, Q/field92 остался
  нулевым, все шесть non-target records token-identical. Validator не меняет
  schema: field79 пока остаётся `UNKNOWN/FORBIDDEN`, production закрыт.
- **UNKNOWN:** индекс толщины огнезащиты в RX38 не доказан, поэтому
  `fireproofing_thickness` остаётся `null`.
- **BLOCKED:** production-расширение за пределы верифицированного
  axial-шаблона и открытие/пересчёт копии в Microsoft Excel должны
  быть выполнены инженером в установленных GUI.

Статус проекта остаётся **NOT READY FOR ISSUE**: реализован безопасный обмен и
диагностика, но независимая нормативная верификация расчётов не завершена.

## Этап 4 — production safety gates

- **CONFIRMED:** введены `DRAFT` / `VALIDATION` / `PRODUCTION`; только
  production без blockers и с positive evidence по всем обязательным gates
  теоретически может получить `READY_FOR_ISSUE`.
- **CONFIRMED:** Mx/My/Qx/Qy fail closed, tolerance использует `Decimal`, а
  pure axial требует отдельного `AXIAL_ONLY` evidence.
- **CONFIRMED:** знак N преобразуется только через явный versioned
  `LiraRx3ForceConvention` с audit исходного и целевого значения.
- **CONFIRMED:** steel grade и field 33 проходят совместную compatibility
  проверку; result fields 44/54 имеют write policy `RESULT_ONLY`.
- **CONFIRMED:** stale template results не входят в `Rx3Input`; byte-identical
  calculated/generated даёт `RX3_RECALCULATION_NOT_PROVEN`.
- **CONFIRMED:** normative и technical registries встроены в release gate;
  непроверенные редакции и вторичные Excel-таблицы не допускаются production.
- **CONFIRMED:** CI проверяет Python 3.11/3.12, pytest, ruff и mypy.
- **VALIDATED (scoped):** два двунаправленных осевых возмущения
  подтвердили field49 и target-aware persistence path для одного
  верифицированного осевого шаблона. Это не закрывает LIRA sign
  convention, другие профили/закрепления/обогрев и нормативную
  эквивалентность.
- **BLOCKED:** реальные mappings Mx/My/Qx/Qy, sign convention, steel field 33,
  GUI smoke-test, Excel recalculation и primary manufacturer data всё ещё
  требуют внешних доказательств.
