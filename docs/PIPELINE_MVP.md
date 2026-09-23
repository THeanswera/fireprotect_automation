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

Она использует существующие CSV/HTML/XLSX adapters и сохраняет native source
records отдельно от `ProjectElement`: `N/Mk/My/Mz/Qy/Qz` как усилия,
`Ry/Rz` как отдельные source results, `№ сечен` как `section_station`, но не
как профиль. Raw OOXML token, exact Decimal, source/review units и row/cell
provenance сохраняются без промежуточного binary float. При отсутствии profile/
mark identity accepted rows остаются review records, а создание `ProjectElement`
блокируется кодом `LIRA_MEMBER_PROFILE_IDENTITY_MISSING`.

Native LIRA labels не являются RX3 semantics. Никакое соответствие
`Mk/My/Mz/Qy/Qz` полям RX3, локальным осям или знакам не выводится автоматически.
Summary содержит distributions, component statistics и только
`CANDIDATE_ONLY` строки для будущей контролируемой проверки; ranking не является
доказательством mapping.
Default component convention — `UNKNOWN`; `ENGINEER_CONFIRMED` можно явно
зафиксировать, но только `VALIDATED` считается разрешённым convention state.
В этой итерации даже `VALIDATED` state не открывает writer: review result всегда
имеет `rx38_force_generation_allowed=false`.

`ENGINEER_CONFIRMED` может фиксировать подтверждённое инженером соответствие
локальной оси и оси сечения при ещё неизвестном `value_transform`. Такое
состояние остаётся pre-validation и никогда не считается разрешённым для
генерации. `SIGNED_LINEAR` сохраняет знак выбранного source value, а
`MAGNITUDE` применяет точный `Decimal` absolute value, сохраняя исходное
знаковое значение в provenance. Evidence scope хранит точные стандарт/профиль,
RX3 template, stress state, length, локальную исходную ось, целевую ось сечения,
угол поворота и ссылки на evidence. Даже `VALIDATED` convention применим только
при полном совпадении scope; нулевой угол не наследуется произвольными
повёрнутыми элементами.

Для `Б2 / 22П / ГОСТ 8240-97 / L=3.00 m / rotation=0 / one-plane X-X`
контролируемая цепочка валидировала `My -> field50` и `Qz -> field92` с
`MAGNITUDE`. Это преобразование только уже выбранной строки, не envelope rule.
Генерация остаётся закрыта отдельным blocker
`LIRA_GOVERNING_RESULT_SELECTION_UNRESOLVED`: station/load case/РСУ/РСН и
governing combination ещё не имеют валидированного selector. Поэтому review
summary по-прежнему содержит `rx38_force_generation_allowed=false`.

## Контроль опубликованных РСУ и выбор одной строки

Для одного набора из четырёх бинарных XLS команда ниже сверяет все шесть native
компонентов опубликованных РСУ с исходными загружениями и явными столбцами
коэффициентов. Она создаёт новый каталог с `rsu_evidence.json` (SHA четырёх
источников, полные векторы, слагаемые, коэффициенты, остатки и адреса ячеек),
`rsu_rows.csv` для просмотра и `selection_template.json`:

```powershell
python -m fireprotect.cli validate-lira-rsu `
  --forces <усилия.xls> --published <РСУ.xls> `
  --coefficients <коэффициенты.xls> --parameters <параметры.xls> `
  --output-dir <новый-каталог>
```

Инженер сверяет строку с GUI ЛИРА и заполняет в **копии** шаблона только
`declared_by`, `basis`, `lira_gui_reference`, `element_id` и `rsu_row_id`.
Числа и состав сочетания программа берёт из привязанного к SHA evidence:

```powershell
python -m fireprotect.cli validate-lira-rsu-selection `
  --evidence <новый-каталог>\rsu_evidence.json `
  --selection <заполненная-копия.json>
```

Проверка требует полного совпадения всех компонентов, актуальных SHA исходных
XLS, одного существующего ID и соответствия элемента. Её результат фиксирует
решение инженера `ENGINEER_DECLARED_UNVALIDATED`; он не доказывает общий алгоритм
governing selection и не разрешает генерацию RX38. Пустые и дублирующиеся строки
параметров загружений блокируют реконструкцию.

## Соединение четырёх выгрузок ЛИРА в одну модель

`prepare-lira-model` читает таблицы жёсткостей, элементов и узлов и
присоединяет к каждому элементу все строки усилий из review bundle. Он не
создаёт `ProjectElement`, не сопоставляет профиль с базой сечений и не считает
ПТМ.

```powershell
python -m fireprotect.cli prepare-lira-model `
  --stiffness <проект>_жесткости.xlsx `
  --elements  <проект>_элементы.xlsx `
  --nodes     <проект>_узлы.xlsx `
  --forces    <review-bundle>\forces.json `
  --output-dir <new-directory>
```

Формат выбирается по расширению: принимаются и `.xlsx`, и native `.xls` (BIFF);
конвертация в XLSX не требуется. В native `.xls` русские заголовки читаются
через `encoding_override="cp1251"`, а имя листа задаётся явно (обычно один
пробел, в том числе для таблицы жёсткостей, где `.xlsx`-выгрузка использует
`Лист1`). Числовые ячейки BIFF — это декодированные IEEE-754 числа без исходного
десятичного токена; это ограничение фиксируется в `manifest.json` как
`numeric_provenance`, а целочисленные идентификаторы элементов/узлов/жёсткостей
канонизируются, чтобы `1` и `1.0` не разрывали связи.

Ключевые правила:

- слово-тип сечения сохраняется отдельно. `Уголок параллельно полкам 80 x 6` и
  `Профиль "Молодечно" 80 x 6` сводятся к одинаковым числам, поэтому
  `kind_word`, `designation` и `mark` хранятся раздельно;
- длина вычисляется через `Decimal` из координат узлов и сохраняет номер
  строки источника;
- закрепления хранятся для каждого концевого узла отдельно
  (`supports_start` / `supports_end`, привязаны к номерам узлов и к началу/
  концу элемента) и не объединяются в характеристику всего стержня;
- отсутствующий тип жёсткости, отсутствующая марка, отсутствующий узел,
  неполные координаты или элемент не из двух узлов дают blocker на элементе, а
  не подстановку значения;
- `profile_resolution` и `ptm` остаются `null`, пока инженер не заявит
  стандарт и схему обогрева.

На реальном наборе «Мед центр Васька 2» собираются все 446 элементов без
блокировок, 15 марок и 892 строки усилий; `rx38_force_generation_allowed`
остаётся `false`.

## Соединение модели с проверенными строками РСУ

`link-lira-model-rsu` соединяет read-only пакет модели (`prepare-lira-model`)
с полными строками проверенных РСУ (`validate-lira-rsu` → `rsu_evidence.json`)
в один пакет «элемент + геометрия + закрепления обоих узлов + все кандидаты
РСУ»:

```powershell
python -m fireprotect.cli link-lira-model-rsu `
  --model-dir <model-package> `
  --evidence  <review>\rsu_evidence.json `
  --output-dir <new-directory>
```

Строки связываются с элементами только по `element_id` внутри явно указанной
пары пакетов; одинаковый номер элемента в другом проекте не считается
доказательством общей модели. Пара источников, их хеши и основание
сопоставления записываются в `manifest.json` как контрольный набор, а
историческое происхождение экспортов не объявляется доказанным
(`historical_origin_claim = NOT_INDEPENDENTLY_PROVEN`).

При связывании перепроверяются SHA-256 всех семи исходных XLS (три файла
модели и четыре файла РСУ), но хеш — не доказательство содержания: все семь
таблиц **повторно читаются** существующими импортерами с явными настройками
из манифестов (лист, строка заголовка, fingerprint mapping РСУ), и каждое
значение, принятое из JSON, сверяется с результатом чтения исходников —
идентификаторы, полные векторы, единицы, коэффициенты (включая `raw_token` и
`decimal_provenance`, с допустимым `null` для BIFF), состав сочетаний, ячейки,
`source_sha256` каждого опубликованного компонента, весь список параметров
загружений (состав, значения и происхождение), геометрия, идентичность сечения
и закрепления обоих узлов.
Записанный статус `VERIFIED` не принимается на веру: каждый из шести
компонентов каждой строки пересчитывается по слагаемым (усилие × коэффициент)
точной десятичной арифметикой и должен совпасть с опубликованным значением,
включая знак; общий статус `BLOCKED`, записанные блокировки и противоречивые
статусы строк отклоняются. `section_station` проверяется против
`section_count` элемента. Неизвестный элемент, повторяющийся `row_id`,
недопустимое сечение, неполный вектор, строка `BLOCKED`, изменённый источник
или JSON, не соответствующий исходным таблицам (например, согласованно
изменённое усилие при неизменных XLS), дают явную ошибку; пакет не создаётся.
Блокировки элементов модели из-за отсутствующей марки сохраняются и
связыванию не препятствуют.

Пакет содержит `manifest.json`, `linked_elements.json` (элемент целиком плюс
список `rsu_rows`), `rsu_candidates.csv` и `README_LINKED.md`. Все шесть
компонентов строки сохраняются вместе с исходными знаками, единицами,
`row_id`, группой, критерием, столбцом коэффициентов, составом загружений,
адресами исходных ячеек и слагаемыми реконструкции. Строки РСУ — это строки
сочетаний (`load_case_membership`), а не одиночные загружения: они лежат в
`rsu_rows` и не смешиваются с `force_candidates`. Разные кандидаты одного
элемента и сечения сохраняются все, дубликатами из-за общей геометрии они не
считаются. Определяющая строка не выбирается
(`governing_result_selection = null`), генерация RX38 закрыта, отсутствие
марки и ограничения выпуска сохраняются.

## Подготовка входного пакета контролируемого опыта для одной балки

`prepare-lira-bar-experiment` собирает входные данные одного контролируемого
опыта ЛИРА→RX3 для **одной физической балки** из существующего связанного
пакета (`link-lira-model-rsu`) и декларации инженера:

```powershell
python -m fireprotect.cli prepare-lira-bar-experiment `
  --linked      <linked-package> `
  --declaration <declaration.json> `
  --output-dir  <new-directory>
```

Декларация (`declaration_template.json` создаётся командой) содержит:
идентификатор физического стержня, список КЭ и концевые узлы, основание
объединения и автора; явно выбранный `row_id` строки технического опыта,
привязанный к SHA-256 манифеста связанного пакета; сведения о профиле и
отдельно заданные расчётные условия (неизвестные остаются `null`).

Первый запуск — отдельная команда, которая создаёт только шаблон и не
готовит никакой пакет (существующий файл не перезаписывается):

```powershell
python -m fireprotect.cli new-lira-bar-experiment-declaration `
  --output declaration.json
```

Последовательность: получить шаблон → заполнить подтверждённые сведения и
авторов (неизвестные оставить `null`) → вписать `linked_manifest_sha256` из
связанного пакета (при несовпадении команда подготовки печатает фактический
хеш) → `prepare-lira-bar-experiment`.

Проверки перед записью пакета: связанный пакет повторно проверяется по
исходным таблицам, причём **SHA-256 evidence сверяется с записью
`link_basis.rsu_evidence.sha256`** — отсутствующий или несовпадающий хеш даёт
отказ до создания выходного каталога (подмена строк в evidence при
неизменных XLS отклоняется); заявленные КЭ образуют одну связную прямую
цепочку без ветвлений между заявленными концевыми узлами, причём **исходное
направление node_ids каждого КЭ должно совпадать с направлением
начало→конец физической балки** — противоположно ориентированный КЭ
отклоняется, узлы не переставляются и знаки усилий не меняются; поворот осей
0° на всех КЭ; профиль (kind/designation/mark) одинаков на всех КЭ и
совпадает с декларацией; выбранная строка принадлежит заявленному стержню.
Длины КЭ, геометрическая длина стержня (сумма КЭ) и расчётные длины хранятся
раздельно. Полный подписанный вектор N/Mk/My/Mz/Qy/Qz сохраняется с
происхождением; обзорные значения считаются существующим `to_review_unit`,
конвенция My→field50 и Qz→field92 (MAGNITUDE) берётся из существующего
реестра без дублирования формул; ненулевой компонент вне подтверждённой
конвенции даёт blocker
`LIRA_EXPERIMENT_UNSUPPORTED_COMPONENT:<имя>` и `transfer_blocked = true`.
Элементы не объединяются автоматически по марке, максимум не выбирается,
governing-строка не назначается.

Результат: `manifest.json`, `experiment_input.json` (машиночитаемый),
`experiment_card.md` (короткая карточка) и `declaration_template.json`.
В пакете всегда `rx38_created = false` и `release_forbidden = true`:
это входные данные опыта, а не разрешение на расчёт RX3 и не выпуск.

## Перечисление кандидатов governing result

`prepare-lira-selection` превращает уже существующий review bundle в новый
read-only набор кандидатов. Он не выбирает расчётную строку: правило вида
`max(abs(all_values))` не реализовано, потому что envelope по несвязанным
station, load case и сочетаниям не является доказанной семантикой.

```powershell
python -m fireprotect.cli prepare-lira-selection `
  --forces <review-bundle>\forces.json `
  --output-dir <new-directory>
```

Набор содержит `manifest.json`, `candidates.json` (Decimal-safe, авторитетный),
`candidates.csv` (читаемый), `selection_template.json` и `README_SELECTION.md`.
Каждая запись имеет статус `CANDIDATE_ONLY` и сохраняет raw token, точный
`Decimal`, исходные и нормализованные единицы, ячейку и строку источника.
Каталог создаётся только новым; source bundle не перезаписывается.

Инженер заполняет `selection_template.json` (`declared_by`, `basis` и один
`candidate_id` на элемент) и проверяет его:

```powershell
python -m fireprotect.cli validate-lira-selection `
  --candidates <new-directory>\candidates.json `
  --selection <filled.json>
```

Проверка разрешает объявление ровно в одного существующего кандидата и
блокирует неизвестный элемент, неизвестного кандидата, несовпадение элемента,
повторное объявление элемента и неоднозначность. Она **никогда** не выдаёт
`governing_result_selection_validated = true` и не открывает RX38: объявление
инженера — это решение, а не независимое доказательство. Blocker
`LIRA_GOVERNING_RESULT_SELECTION_UNRESOLVED` остаётся активным, а отсутствие
`combination` в источнике или единственный кандидат на элемент выдаются как
warnings, потому что такой источник не может подтвердить правило выбора.

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
