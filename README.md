# FireProtect Automation

Безопасный каркас расчётного конвейера:

```text
ЛИРА (CSV / HTML / XLSX)
    -> ProjectElement + provenance + явные единицы
    -> RX38 из совместимого шаблона
    -> ручной расчёт в GUI RX3
    -> Rx3Result -> ProjectElement
    -> проверенная копия существующей Excel-книги
    -> project_audit.json / project_audit.md
```

Проект не подставляет типовые инженерные значения вместо отсутствующих.
Неоднозначное соответствие профиля, сторон обогрева, расчётной длины или поля
RX38 приводит к ошибке либо явному warning. Нормативный результат в
production-режиме нельзя подтвердить без `NormativeTrace`.

## Реализовано

- lossless parser и safe writer 200-позиционных записей `Tconstr`;
- полная карта RX38: 56 `confirmed`, 13 `probable`, 131 `unknown`;
- центральная модель `ProjectElement`, единицы, SI-конвертация и provenance;
- конфигурируемые адаптеры таблиц усилий ЛИРА для CSV, HTML и XLSX;
- geometry-модуль для периметров, ПТМ, section factor и площади обработки;
- `ProjectElement -> RX38` только через совместимый шаблон, с round-trip
  отчётом и сохранением неизвестных полей;
- обратный мост `RX38 -> Rx3Result -> ProjectElement`, который типизирует
  только CONFIRMED-поля и помечает их provenance как `RX3_RESULT`;
- команды подготовки ручной GUI-проверки RX3 и анализа сохранённого
  результа с разделением CONFIRMED / PROBABLE / UNKNOWN;
- типизированный copy-only экспорт в фиксированную 44-строчную Excel-книгу с
  проверкой ZIP, openpyxl, SHA-256 и точной карты 579 формул;
- экспериментальный `pipeline`, который останавливается перед RX3 и продолжает
  работу после появления `calculated.rx38`.

Независимая нормативная верификация расчётов ещё не завершена. Текущий статус:
**NOT READY FOR ISSUE**.

## Установка и проверки

```powershell
python -m pip install -e .
python -m pytest -q
```

`openpyxl>=3.1,<3.2` устанавливается как зависимость и используется для XLSX-
импорта ЛИРА и проверочных чтений. Copy-only writer изменяет OOXML напрямую,
чтобы не пересобирать рабочий шаблон.

## CLI

```powershell
python tools/rx38_diff.py diff FILE_A.rx38 FILE_B.rx38 --left-mark "К1" --right-mark "К1"
python tools/rx38_diff.py group-by-profile ..\rx3\*.rx38 --profile "30 К1"
python -m fireprotect.cli inspect-rx38 FILE.rx38
python -m fireprotect.cli validate-rx38 FILE.rx38
python -m fireprotect.cli lookup-profile RX3_DB.rxdb "30К1" --standard "СТО АСЧМ 20-93"
python -m fireprotect.cli rx38-create INPUT.json TEMPLATE.rx38 OUTPUT.rx38 --template-mark "К1" --report report.json
python -m fireprotect.cli prepare-rx3-validation INPUT.json TEMPLATE.rx38 --output-dir validation/rx3_gui_test --template-mark "К1"
python -m fireprotect.cli validate-rx3-result generated.rx38 calculated.rx38
python -m fireprotect.cli pipeline pipeline.json
```

JSON для `rx38-create` обязан явно перечислять все поля `ProjectElement`:
физические величины задаются объектами `{"value": "...", "unit": "..."}`,
неизвестные значения — `null`, а каждому заполненному проектному полю нужна
запись в `provenance`.

## Документация

- [карта RX38](docs/RX38_SCHEMA.md);
- [карта Excel-книги](docs/EXCEL_DATA_MAP.md);
- [аудит 579 формул Excel](docs/EXCEL_FORMULA_AUDIT.md);
- [запуск сквозного MVP](docs/PIPELINE_MVP.md);
- [текущий прогресс](docs/PROGRESS.md);
- [открытые вопросы](docs/OPEN_QUESTIONS.md);
- [нормативная прослеживаемость](normative/traceability.md).

## Непубликуемые исходные данные

Оригинальные RX38, поставка RX3, SQLite-базы, нормативные PDF, Excel и
проектная документация остаются локальными и исключены `.gitignore`. Тесты
создают синтетические fixtures во временных каталогах.
