# Controlled experiments RX3

## Общий протокол

Каждая пара использует один и тот же шаблон, одну конструкцию и меняет ровно
один параметр. До и после сохраняются отдельными файлами; шаблон не
перезаписывается. Для каждой пары фиксируются версия RX3, дата, инженер,
скриншот/ссылка на evidence, SHA-256, экранные значения и машинный diff:

```powershell
python -m fireprotect.cli rx38-experiment-diff BASE.rx38 CHANGED.rx38 `
  --experiment-id RX3-EXP-XX `
  --target-fingerprint BEFORE_TCONSTR_SHA256 `
  --json-report RX3-EXP-XX.json `
  --markdown-report RX3-EXP-XX.md
```

Кандидаты индексов остаются `PROBABLE`, пока совокупность независимых опытов,
GUI/help/report/database evidence и review не докажет однозначную семантику.
Совместное изменение не доказывает причинность.

Каждый post-calc запуск обязан явно назвать целевые `Tconstr`. Предпочтительны
точный fingerprint BEFORE-записи или её 1-based position. Марка допустима
только при уникальном разрешении; неоднозначность останавливает проверку.
Поля 44/54 должны материально измениться у каждой цели. Любое текстовое
изменение нецелевой записи даёт `RX3_UNEXPECTED_NON_TARGET_CHANGE`.

Для CONFIRMED numeric fields отчёт хранит одновременно исходные токены и
результат сравнения через `Decimal`. Например, `30,00 → 30` имеет
`text_changed=true`, `semantic_changed=false` и классификацию
`RX3_TOKEN_NORMALIZATION`. Это правило не применяется к PROBABLE, UNKNOWN,
нечисловым полям или полям с неоднозначными единицами.

## Матрица

1. `RX3-EXP-01`: baseline pure axial.
2. `RX3-EXP-01C`: N=30 controlled axial perturbation.
3. `RX3-EXP-01D`: N=25 bidirectional controlled axial perturbation.
4. `RX3-EXP-02`: single-plane bending family; Phase A observation before any perturbation.
5. `RX3-EXP-03`: изменить только My.
6. `RX3-EXP-04`: изменить только Qx.
7. `RX3-EXP-05`: изменить только Qy.
8. `RX3-EXP-06`: изменить только support condition.
9. `RX3-EXP-07`: изменить только effective-length factor.
10. `RX3-EXP-08`: изменить только fire regime.
11. `RX3-EXP-09`: изменить только heating exposure.
12. `RX3-EXP-10`: изменить только steel grade с согласованными свойствами.
13. `RX3-EXP-11`: изменить только required R.

## Первый реальный опыт

Начинать только с `RX3-EXP-01`: один элемент, один профиль, одна марка стали,
одна длина, `Mx=My=Qx=Qy=0`, меняется только N. До подтверждения осевого пути
к изгибу и сдвигу не переходить.

Порядок GUI:

1. Открыть `template.rx38`, записать значения и screenshot.
2. Открыть `generated.rx38`; сверить mark, profile, steel, N, все четыре
   нулевых компонента, length, support, effective length, fire regime, R и
   режим critical-temperature calculation.
3. При любом расхождении остановиться.
4. Нажать «Рассчитать» и зафиксировать `DIALOG_CALCULATED`; это ещё не означает
   обновление главной таблицы.
5. Нажать «Сохранить в таблицу» и зафиксировать `TABLE_UPDATED`.
6. Выполнить Project Save As только в новый `calculated.rx38` и зафиксировать
   кандидат `FILE_PERSISTED`.
7. Запустить `validate-rx3-result` с явной целью и фактическим
   `gui_execution_evidence`.
8. Сверить fields 44/54, все неожиданные PROBABLE/UNKNOWN изменения и hashes.
9. Приложить протокол к evidence; не повышать mapping по одному опыту.

## Закрытие осевого MVP-пути

`RX3-EXP-01C` и `RX3-EXP-01D` дали повторяемый двунаправленный отклик при
изменении только field49. Для этого одного верифицированного axial-
шаблона подтверждена цепочка parser → safe writer → GUI N → Calculate →
Save to table → calculated RX38 → target-aware validator.

`AXIAL_N_PATH_MVP_STATUS = VALIDATED` имеет только этот узкий смысл. Он не
подтверждает LIRA sign convention, произвольные сжатые элементы,
другие стали/закрепления/обогрев или нормативную эквивалентность. Чистые
N-возмущения больше не планируются.

Field53 по трём состояниям равен GUI `beta_tem` после округления, а
встроенная справка связывает `beta_tem` с коэффициентом снижения E при
температуре. Поэтому field53 повышен только до `PROBABLE`. Field76 также
остаётся `PROBABLE` и не переименовывается, пока не проверены combined-stress
случаи. Field77 остаётся `UNKNOWN`.

Встроенная справка называет `beta_tem`, но локальной полной таблицы/
интерполяции E(T) в repository evidence нет. `theta_cr_beta` не обнаружена
отдельным RX38-токеном; формула не реализуется.

## RX3-EXP-02 Phase A — bending observation

`prepare-rx3-bending-phase-a` ранжирует только шаблоны с явным
одноплоскостным изгибом, N=0, ненулевым probable field50, точным
`rx3.rxdb` geometry match и явными внешними Mx/Q references. Если Q ненулевая,
опыт не называется pure Mx. Phase A копирует шаблон и готовит только
отчёт, checklist и GUI-инструкции; altered RX38 не создаётся.
