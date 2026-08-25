# Controlled experiments RX3

## Общий протокол

Каждая пара использует один и тот же шаблон, одну конструкцию и меняет ровно
один параметр. До и после сохраняются отдельными файлами; шаблон не
перезаписывается. Для каждой пары фиксируются версия RX3, дата, инженер,
скриншот/ссылка на evidence, SHA-256, экранные значения и машинный diff:

```powershell
python -m fireprotect.cli rx38-experiment-diff BASE.rx38 CHANGED.rx38 `
  --experiment-id RX3-EXP-XX `
  --json-report RX3-EXP-XX.json `
  --markdown-report RX3-EXP-XX.md
```

Кандидаты индексов остаются `PROBABLE`, пока совокупность независимых опытов,
GUI/help/report/database evidence и review не докажет однозначную семантику.
Совместное изменение не доказывает причинность.

## Матрица

1. `RX3-EXP-01`: baseline pure axial.
2. `RX3-EXP-02`: изменить только N.
3. `RX3-EXP-03`: изменить только Mx.
4. `RX3-EXP-04`: изменить только My.
5. `RX3-EXP-05`: изменить только Qx.
6. `RX3-EXP-06`: изменить только Qy.
7. `RX3-EXP-07`: изменить только support condition.
8. `RX3-EXP-08`: изменить только effective-length factor.
9. `RX3-EXP-09`: изменить только fire regime.
10. `RX3-EXP-10`: изменить только heating exposure.
11. `RX3-EXP-11`: изменить только steel grade с согласованными свойствами.
12. `RX3-EXP-12`: изменить только required R.

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
4. Выполнить расчёт и сохранить только как `calculated.rx38`.
5. Запустить `validate-rx3-result` с фактическим `gui_execution_evidence`.
6. Сверить fields 44/54, все неожиданные PROBABLE/UNKNOWN изменения и hashes.
7. Приложить протокол к evidence; не повышать mapping по одному опыту.
