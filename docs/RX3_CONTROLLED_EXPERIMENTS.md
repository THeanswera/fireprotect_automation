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

Кандидаты индексов остаются `PROBABLE`, пока совокупность corpus,
GUI/help/report/database evidence, управляемой причинности и review не докажет
однозначную семантику в явно ограниченной области.
Совместное изменение не доказывает причинность.

Каждый post-calc запуск обязан явно назвать целевые `Tconstr`. Предпочтительны
точный fingerprint BEFORE-записи или её 1-based position. Марка допустима
только при уникальном разрешении; неоднозначность останавливает проверку.
Поля 44/54 должны материально измениться у каждой цели. Любое текстовое
изменение нецелевой записи даёт `RX3_UNEXPECTED_NON_TARGET_CHANGE`.
Подтверждённый field52 разрешён как расчётный result, но не заменяет
обязательные признаки пересчёта fields 44/54.

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
5. `RX3-EXP-02B`: field50 Mx=10 controlled X-X perturbation, Q fixed.
6. `RX3-EXP-03`: изменить только My.
7. `RX3-EXP-04`: изменить только Qx.
8. `RX3-EXP-05`: изменить только Qy.
9. `RX3-EXP-06`: изменить только support condition.
10. `RX3-EXP-07`: изменить только effective-length factor.
11. `RX3-EXP-08`: изменить только fire regime.
12. `RX3-EXP-09`: изменить только heating exposure.
13. `RX3-EXP-10`: изменить только steel grade с согласованными свойствами.
14. `RX3-EXP-11`: изменить только required R.

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
9. Приложить протокол к evidence; повышать mapping только при достаточной
   совокупности доказательств и отдельно фиксировать write policy.

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
одноплоскостным изгибом, N=0, ненулевым field50, точным
`rx3.rxdb` geometry match и явными внешними Mx/Q references. Если Q ненулевая,
опыт не называется pure Mx. Phase A копирует шаблон и готовит только
отчёт, checklist и GUI-инструкции; altered RX38 не создаётся.

## Закрытие Mx X-X MVP-пути

`RX3-EXP-02B` изменил только field50 с `8,89` на `10,00` перед расчётом;
field78 остался `8,89`, field92 остался `2,32`. RX3 показал Mx=`10,00 kN·m`
и Q=`2,32 kN`, затем дал причинный расчётный отклик: gamma_tem `0,433 →
0,487`, theta_cr `542,34 → 518,86 °C`, R0 `7,62 → 7,12 min`.

Generated SHA-256:
`93ce40e25aa17fdb07210a80b5e9b8cbce717fa552b063dc4d82de6e44fad202`.
Calculated SHA-256:
`923b67d3314c5e837cb88c66f51b43f2ac567b1e4df05658070bf65346ebac2b`.
Target-aware validation resolved exact BEFORE fingerprint
`ada996f66cdf964f5dcbfc935a163d6f47e79452ce838e9bab8fe3adc055470c`
to Б1 at Tconstr position 5; all six non-target records remained token-identical.

The complete generated-to-calculated change set for Б1 is
`44, 50, 52, 54, 76, 78`. Field50 changed only by Decimal-equivalent token
normalization (`10,00 → 10`); field78 synchronized (`8,89 → 10`) and is
therefore only a `PROBABLE` persisted/report copy. Field92 remained exactly
`2,32` and stays `UNKNOWN`: Q was observed but not causally written.

Field50 is `CONFIRMED` only as the active major-axis moment input for
one-plane bending, X-X, and the verified Б1 template family. Its write policy
is `EXPERIMENTAL`; no generic production writer or high-level Mx import is
enabled. My, Q writing, LIRA axes/signs, other stress states and normative
equivalence remain unverified.

Field53 remained exactly `0`, matching GUI beta_tem=`0,000`; the separate
beta-related critical temperature `1200 °C` is not assigned to an RX38 field.
Field76 became `119,222745671194`, matching displayed stress/load `119,22 MPa`,
but remains `PROBABLE` outside the observed bending state.

`MX_X_AXIS_PATH_MVP_STATUS = VALIDATED` means only the manual chain
field50 → GUI Mx → Calculate → Save to table → calculated RX38 →
target-aware validator for that narrow scope with Q fixed.

The next experiment family is one controlled Q perturbation (`2,32 → 3,00
kN`) on the same Б1 template with Mx explicitly frozen. It must be prepared
only as a separate validation step; RX3 calculation remains manual.
