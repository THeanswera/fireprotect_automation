# Прогресс

## Этап 1 — RX38 Schema Reverse Engineering

- **CONFIRMED:** найдено 5 RX38, из них 4 уникальных; 46 уникально
  учитываемых `Tconstr`; длина строки — 200 полей.
- **CONFIRMED:** parser сохраняет исходные токены, кавычки, decimal comma,
  newline и кодировку; safe writer разрешает менять только доказанные поля.
- **CONFIRMED:** `Rx38Construction`, `rx38_diff.py`, `group-by-profile` и
  `ProfileRepository` работают без автоматического разрешения неоднозначности.
- **CONFIRMED:** 59 полей имеют достаточное доказательство; синхронность полей
  1 и 3 марки подтверждена на всех 46 записях корпуса.
- **PROBABLE:** 14 полей имеют сильное, но пока не однозначное сопоставление и
  остаются недоступными для записи.
- **UNKNOWN:** 127 полей сохраняются как raw и не интерпретируются.

## Этап 2 — обмен через ProjectElement

- **CONFIRMED:** `ProjectElement` содержит все требуемые группы данных; каждая
  физическая величина несёт единицу и преобразуется через `Decimal`.
- **CONFIRMED:** каждое заполненное проектное поле требует provenance;
  молчаливые инженерные значения по умолчанию не используются.
- **CONFIRMED:** `NormativeTrace` и `NormativeResult` блокируют подтверждение
  нормативного результата без ссылки в production-режиме.
- **CONFIRMED:** LIRA-архитектура импортирует CSV, HTML и XLSX через полностью
  настраиваемое сопоставление колонок и явные единицы; будущий LiraAPI
  подключается через тот же `LiraRowSource`.
- **CONFIRMED:** `prepare-lira-review` создаёт batch review bundle без RX38:
  source/mapping hashes, raw OOXML tokens, exact Decimal/review-unit values,
  cell provenance, accepted/rejected native rows, statistics, candidate-only
  selections, blockers and audit. Output directory создаётся только новым;
  source не перезаписывается, `ProjectElement` не создаётся.
- **CONFIRMED:** проверенный native bar-force layout содержит
  `N/Mk/My/Mz/Qy/Qz`, отдельные `Ry/Rz`, section/station, element/load/type и
  composition. `section_station` не является profile identity. Старое
  review-сопоставление `N/Mx/My/Qx/Qy` признано непригодным для native source.
- **CONFIRMED:** при отсутствующем profile/mark identity действует blocker
  `LIRA_MEMBER_PROFILE_IDENTITY_MISSING`; native-to-RX3 convention остаётся
  `UNKNOWN`, а ranked elements имеют только статус `CANDIDATE_ONLY`.
- **CONFIRMED (контрольный набор XLS):** read-only реконструкция опубликованных
  РСУ сверила 16 полных native-векторов, 96/96 компонентов с нулевыми остатками.
  Новый review bundle сохраняет четыре SHA, строки/ячейки, коэффициенты и
  слагаемые; выбор одной строки фиксируется отдельным решением инженера.
  Пустые или дублирующиеся параметры загружений блокируют валидацию.
  Governing selection и RX38 generation остаются закрыты.
- **CONFIRMED (native XLS, контрольная модель):** `prepare-lira-model` читает
  таблицы жёсткостей/элементов/узлов и в legacy `.xls` (BIFF), и в `.xlsx`;
  конвертация не требуется. На контрольной балке собираются 2 элемента длиной
  3 м и поворотом 0°, сечение `Брус 10 X 20` сохраняется как `designation`,
  марка/стандарт не подставляются (`LIRA_STIFFNESS_NAME_WITHOUT_MARK`), обе
  строки параметров жёсткости и заголовок с единицей `(см)` сохраняются, а
  числовое BIFF-происхождение фиксируется в `manifest.json`.
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
- **UNKNOWN:** семантика `Ry/Rz`, native-to-RX3 axis/sign convention, member
  profile/mark identity и назначение нескольких служебных блоков Excel всё ещё
  отсутствуют в доказательствах.
- **BLOCKED:** запись Mx/My/Qx/Qy, материала и сторон обогрева в RX38 до
  доказательства индексов; ненулевые Mx/My/Qx/Qy теперь останавливают
  генерацию, а не остаются из шаблона с warning.
- **BLOCKED:** production-подбор толщины и расхода до идентификации нормативной
  редакции и технической документации конкретного материала.
- **BLOCKED:** выпуск результата до GUI smoke-test созданного файла в RX3 и
  пересчёта изменённой книги в Microsoft Excel.

## Этап 3 — сквозной инженерный MVP

- **CONFIRMED:** `Rx3Result` извлекает из RX38 доказанные поля, сохраняет
  PROBABLE отдельно и не приписывает семантику 127 UNKNOWN-индексам.
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
- **CONFIRMED (узкая область):** RX3-EXP-04B подтвердил
  цепочку field79 `4,3414 → 5,00` → GUI My=`5,00` → manual Calculate/Save →
  persisted field79=`5`. Mx persisted через field78=`0,51`, Q/field92 остался
  нулевым, все шесть non-target records token-identical. Field79 promoted как
  GUI My только для Кс1 / 20П biaxial; write policy остаётся `EXPERIMENTAL`,
  production закрыт. `MY_BIAXIAL_PATH_MVP_STATUS = VALIDATED`.
- **VALIDATED (exact controlled scope):** `LIRA-RX3-22P-XX-MAGNITUDE`
  establishes LIRA `My -> field50` and `Qz -> field92` with the explicit
  `MAGNITUDE` transform only for Б2 / 22П / ГОСТ 8240-97 / L=3.00 m / zero
  rotation / one-plane X-X and an already selected source row. Signed source
  values remain auditable. This does not validate an envelope selector;
  `LIRA_GOVERNING_RESULT_SELECTION_UNRESOLVED` keeps
  `rx38_force_generation_allowed=false`. Field50/92 remain `EXPERIMENTAL`,
  field78 remains a non-writable `PROBABLE` persisted copy, and Mk/Mz/Qy/Ry/Rz
  remain unresolved.
- **CONFIRMED:** `prepare-lira-bar-run` сводит подготовку одного опыта к
  четырём входным значениям (пакет, `row_id`, постановка, каталог вывода).
  Источники, настройки чтения, геометрия, точные десятичные значения, хеши и
  происхождение выводятся программой. Есть `--dry-run` без записи, повторный
  запуск даёт `RUN_ALREADY_PREPARED` без дублирования, изменённые исходные XLS
  дают `SOURCE_DRIFT_DETECTED` и требуют явного `--accept-current-sources`.
- **CONFIRMED:** декларация опыта теперь имеет два явных состояния:
  `ENGINEER_SIGNED` (непустые `confirmed_by`/`selected_by`) и
  `DRAFT_UNSIGNED`, где подписи обязаны быть `null`, а каждое решение несёт
  роль (`USER_STATEMENT`, `SOURCE_DOCUMENT`, `PACKAGE_EVIDENCE`,
  `ASSISTANT_SELECTION`). Роль `ENGINEER_CONFIRMED` в черновике запрещена.
  Production-гейты и `IssueReadiness` не изменены.
- **CONFIRMED (узкая область):** `prepare-rx3-lira-bar` — VALIDATION-only
  подготовка расчётного файла RX3 для записи Б2 / 22П / ГОСТ 8240-97 / 3.00 м
  / rotation 0 / one-plane X-X. Последний переход привязан к источникам:
  значения, единицы и конвенция выводятся заново из перечитанных XLS и
  привязанного evidence, сохранённый JSON служит только детектором подмены
  (полнота, уникальность и имена шести компонентов, их значения, единицы,
  конвенция, идентичность стержня и профиля). Совместимость записи шаблона
  проверяется по подтверждённой классификации напряжённого состояния,
  требуемому пределу и режиму пожара, а файл шаблона — по контролируемому
  SHA-256. Меняются ровно поля 50 и 92; после записи файл перечитывается, и
  доказывается неизменность остальных записей. `PRODUCTION` отклоняется,
  `field50/92` остаются `EXPERIMENTAL`, `rx38_force_generation_allowed=false`.
- **CONFIRMED (узкая область):** вопрос расчётной длины и закрепления разрешён
  как `NOT_APPLICABLE_FOR_THIS_RECORD` для этой записи: строка не содержит
  осевого усилия, запись классифицируется как одноосный изгиб, а
  контролируемый опыт `LIRA-RX3-22P-XX-MAGNITUDE` на этой же записи прошёл при
  незаполненном `field48` и `field51='0'` без их изменения. Геометрические 3 м
  расчётной длиной не назначались; для сжатых и сжато-изогнутых элементов
  вывод не действует.
- **CONFIRMED:** `export-lira-bar-review` создаёт новую обзорную книгу
  учебного опыта (идентичность, полный вектор с единицами и ячейками,
  источники, роли решений, блокеры). Ячейки результата RX3 пустые до
  фактического расчёта; это не расчётная книга ОБМ.
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
- **BLOCKED:** governing LIRA result selection across stations, load cases,
  РСУ/РСН and combinations; native N/Mk/Mz/Qy and Ry/Rz mappings; arbitrary
  rotations/profiles/stress states; steel field 33,
  GUI smoke-test, Excel recalculation и primary manufacturer data всё ещё
  требуют внешних доказательств.

## RX3 force-mapping stop point

| Усилие RX3 | Field | Подтверждённая область | Production write |
|---|---:|---|---|
| N | 49 | narrow axial compression path | blocked pending LIRA sign/convention evidence |
| Mx | 50 | one-plane X-X / Б1 family only | blocked; not universal Mx |
| Q | 92 | one-plane X-X / Б1 / 14Б2 family only | blocked; not a LIRA Qx/Qy mapping |
| My | 79 | biaxial Кс1 / 20П family only | blocked; not a LIRA My mapping |

Общий reverse engineering силовых полей RX3 для MVP остановлен. RX3-EXP-04B
является прямым контрпримером универсальности field50: в biaxial Кс1 field50=`0`,
field78=`0,507` до save при GUI Mx=`0,51`, затем field78=`0,51` после save.
Поэтому field78 остаётся `PROBABLE` persisted/display copy, а глобальные Mx/X/Y
семантики не установлены. Exact 22П / Б2 mapping теперь подтверждён для
`My/Qz -> field50/92 / MAGNITUDE`, но только для заранее выбранной строки.
Следующий минимальный validation должен установить governing-result selection
между stations/load cases/РСУ/РСН/combinations; новых широких RX38
экспериментов или production writer для MVP не планируется.
