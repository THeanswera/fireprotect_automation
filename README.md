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
Неоднозначное соответствие расчётно значимого поля приводит к blocker.
Warning используется только для некритической диагностики и не заменяет
доказательство. Нормативный результат в production нельзя подтвердить без
`NormativeTrace`, допустимой редакции, даты действия и проверки SHA-256.

## Реализовано

- lossless parser и safe writer 200-позиционных записей `Tconstr`;
- полная карта RX38: 59 `confirmed`, 14 `probable`, 127 `unknown`;
- центральная модель `ProjectElement`, единицы, SI-конвертация и provenance;
- конфигурируемые адаптеры таблиц усилий ЛИРА для CSV, HTML и XLSX;
- geometry-модуль для периметров, ПТМ, section factor и площади обработки;
- `ProjectElement -> RX38` только через совместимый шаблон, с round-trip
  отчётом и сохранением неизвестных полей;
- обратный мост `RX38 -> Rx3Result -> ProjectElement`, который типизирует
  только CONFIRMED-поля и помечает их provenance как `RX3_RESULT`;
- команды подготовки ручной GUI-проверки RX3 и анализа сохранённого
  результата с разделением CONFIRMED / PROBABLE / UNKNOWN;
- типизированный copy-only экспорт в фиксированную 44-строчную Excel-книгу с
  проверкой ZIP, openpyxl, SHA-256 и точной карты 579 формул;
- экспериментальный `pipeline`, который останавливается перед RX3 и продолжает
  работу после появления `calculated.rx38`;
- режимы `DRAFT` / `VALIDATION` / `PRODUCTION` и центральный
  `IssueReadiness`; production работает fail closed;
- запрет генерации при ненулевых `Mx/My/Qx/Qy`, пока mappings не CONFIRMED;
- явные force convention, steel compatibility, template profile и строгая
  граница `Rx3Input` / `Rx3Result`;
- нормативный и технический registries; неподтверждённые таблицы Excel не
  используются для production-подбора толщины;
- GitHub Actions для Python 3.11/3.12, pytest, ruff и mypy.

Независимая нормативная верификация расчётов ещё не завершена. Текущий статус:
**NOT READY FOR ISSUE**.

## Установка и проверки

```powershell
python -m pip install -e .
python -m pytest -q
python -m pip install -e ".[dev]"
python -m ruff check src tests
python -m mypy src
```

`openpyxl>=3.1,<3.2` устанавливается как зависимость и используется для XLSX-
импорта ЛИРА и проверочных чтений. Copy-only writer изменяет OOXML напрямую,
чтобы не пересобирать рабочий шаблон.

## CLI

Safety-hardening notes:

- numeric LIRA XLSX cells are read from exact OOXML decimal tokens before any
  binary-float conversion;
- VALIDATION/PRODUCTION require typed heating exposure bound to the exact
  ProjectElement and template Tconstr fingerprint;
- steel production compatibility covers RX38 fields 82/83/84/188/189;
- Excel production runs select only `template_id`; approval, formula-map and
  lookup-table fingerprints come from `templates/excel_registry.yaml`;
- RX38/XLSX no-overwrite finalization falls back to exclusive-create copying on
  filesystems without hard-link support.

```powershell
python tools/rx38_diff.py diff FILE_A.rx38 FILE_B.rx38 --left-mark "К1" --right-mark "К1"
python tools/rx38_diff.py group-by-profile ..\rx3\*.rx38 --profile "30 К1"
python -m fireprotect.cli inspect-rx38 FILE.rx38
python -m fireprotect.cli validate-rx38 FILE.rx38
python -m fireprotect.cli lookup-profile RX3_DB.rxdb "30К1" --standard "СТО АСЧМ 20-93"
python -m fireprotect.cli rx38-create INPUT.json TEMPLATE.rx38 OUTPUT.rx38 --template-mark "К1" --mode VALIDATION --safety-context safety.json --report report.json
python -m fireprotect.cli prepare-rx3-validation INPUT.json TEMPLATE.rx38 --output-dir validation/rx3_gui_test --template-mark "К1" --mode VALIDATION --safety-context safety.json
python -m fireprotect.cli validate-rx3-result generated.rx38 calculated.rx38 --target-fingerprint BEFORE_TCONSTR_SHA256 --gui-evidence ENGINEER_CONFIRMED --evidence-reference RX3-EXP-01
python -m fireprotect.cli rx38-experiment-diff BASE.rx38 CHANGED.rx38 --experiment-id RX3-EXP-01 --target-position 1
python -m fireprotect.cli pipeline pipeline.json
```

One reproducible preparation step for a single controlled LIRA bar run replaces
the manual retyping of numbers. The operator supplies an existing verified
package, one RSU row id, the experiment plan and an output directory; sources,
geometry, decimals, hashes and provenance are derived from the re-read tables:

```powershell
python -m fireprotect.cli new-lira-bar-run-conditions --output conditions.json
python -m fireprotect.cli prepare-lira-bar-run `
  --source-package validation/STEEL_B2_3M_RUN_01 `
  --row-id R0003 `
  --plan validation/STEEL_B2_3M_RUN_01/DOCUMENT_BASED_EXPERIMENT_PLAN.md `
  --conditions validation/STEEL_B2_3M_RUN_02/experiment_conditions.json `
  --output-dir validation/STEEL_B2_3M_RUN_02 `
  --dry-run
```

`--dry-run` performs every check without writing. Changed source files are
reported as `SOURCE_DRIFT_DETECTED` and stop the run until they are explicitly
accepted; the accepted drift is recorded in the run manifest. A repeated run
with identical inputs reports `RUN_ALREADY_PREPARED` instead of duplicating the
package. The prepared declaration is an explicitly unsigned
`DRAFT_UNSIGNED` document whose per-decision roles (`USER_STATEMENT`,
`SOURCE_DOCUMENT`, `PACKAGE_EVIDENCE`, `ASSISTANT_SELECTION`) are recorded
instead of an invented engineer signature.

The RX3 input for that teaching run is prepared by a VALIDATION-only step that
may write exactly two fields of one compatible record:

```powershell
python -m fireprotect.cli prepare-rx3-lira-bar `
  --run-dir validation/STEEL_B2_3M_RUN_03 `
  --template rx3/новый_5_814_89.rx38 `
  --output-dir validation/STEEL_B2_3M_RUN_03/rx3_input
```

It re-derives every value from the re-read LIRA tables and the bound RSU
evidence (`read_verified_bar_run`) and never from the stored run JSON; the JSON
is read only to detect substitution, and the whole six-component vector, its
units, its convention and the bar identity must still match the sources. The
target record is accepted only when it also carries the confirmed one-plane
bending stress state, the declared required fire resistance and the declared
fire regime, and when the template file matches the controlled SHA-256 pin.
Any other scope, another transform than the validated `MAGNITUDE`, a blocked
transfer, a non-unique target, an existing output directory and `PRODUCTION`
mode are refused; after writing, the file is re-read and only the target record
may differ. The effective-length question is answered from the primary
description of the algorithm: in the calculation document shipped with RX3
(`rx3/doc/pages/pr.pdf`, section 4, "Изгибаемый стержень в одной из главных
плоскостей", formulas 3 and 4) the critical temperature is derived from the
moment, section modulus, shear force, moment of inertia and minimum thickness;
the effective length and the support condition belong to the "Сжатый стержень"
subsection (formulas 8-13). The status is reported as
`NOT_USED_BY_THIS_ALGORITHM` with the document and its SHA-256, the geometrical
3 m are never substituted, and the engineering limits (unapproved draft
document, unverified program/formula identity, engineer's choice of mode) are
listed separately. Data transfer is not presented as proof of an engineering
fire-resistance calculation. The resume command deliberately omits
`ENGINEER_CONFIRMED`: saving the file is not evidence that a human reviewed the
result.

```powershell
python -m fireprotect.cli export-lira-bar-review `
  --run-manifest validation/STEEL_B2_3M_RUN_02/run_manifest.json `
  --output validation/STEEL_B2_3M_RUN_02/STEEL_B2_3M_RUN_02_REVIEW.xlsx
```

That workbook is a new review file, never the project calculation book: RX3
result cells stay empty until a real calculation exists.

Phase A of the controlled pure-axial experiment is deliberately non-generating:

```powershell
python -m fireprotect.cli prepare-rx3-phase-a `
  --templates-dir rx3 `
  --rx3-db rx3/rx3.rxdb `
  --output-dir validation/RX3-EXP-01_A_TEMPLATE_OBSERVATION `
  --experiment-id RX3-EXP-01 `
  --project-element-id K1 `
  --heating-sides 4
```

The command ranks every local `Tconstr`, copies the selected RX38 byte-for-byte,
and creates summaries, an `UNVERIFIED` heating-evidence template, a draft
`ProjectElement`, and `CHECKLIST_A.md`. It never creates `generated.rx38`.

The next bending-family checkpoint is also observation-only. It requires an
explicit JSON file binding existing GUI/report values to an exact source file
and mark; the command uses those values for ranking without promoting RX38
force mappings:

```json
{
  "evidence_reference": "existing RX3 GUI/report evidence",
  "candidates": [
    {
      "source_file": "rx3/example.rx38",
      "mark": "B1",
      "Mx_knm": "8.89",
      "Q_kn": "2.32"
    }
  ]
}
```

```powershell
python -m fireprotect.cli prepare-rx3-bending-phase-a `
  --templates-dir rx3 `
  --rx3-db rx3/rx3.rxdb `
  --report-values BENDING_REPORT_VALUES.json `
  --output-dir validation/RX3-EXP-02_A_BENDING_OBSERVATION
```

This command copies the selected template and creates a candidate comparison,
expected-value report, checklist, and GUI instructions. It never creates an
altered RX38 and never starts RX3 calculation. A non-zero Q reference is
reported as a confounder, so the observation is not labelled pure Mx.

The fingerprint-bound validation-only Mx perturbation is prepared separately:

```powershell
python -m fireprotect.cli prepare-rx3-bending-mx10 `
  --phase-a-dir validation/RX3-EXP-02_A_BENDING_OBSERVATION `
  --observation validation/RX3-EXP-02_A_BENDING_OBSERVATION/phase_a_gui_observation.json `
  --output-dir validation/RX3-EXP-02B_MX10
```

`RX3-EXP-02B` confirmed field 50 as the active GUI Mx input only for the
verified one-plane X-X / Б1 template family. Its schema write policy remains
`EXPERIMENTAL`; the production writer and LIRA axis/sign mapping were not opened.
Field 78 is only a probable post-calc persisted copy. Field 92 is an RX3 GUI
Q input confirmed only in the verified one-plane X-X / Б1 / 14Б2
scope. Both fields 50 and 92 remain `EXPERIMENTAL`; production and ordinary
typed writers remain blocked.

`RX3-EXP-03` prepares the next single-variable check from the completed
RX3-EXP-02B evidence directory:

```powershell
python -m fireprotect.cli prepare-rx3-bending-q3 `
  --mx-validation-dir validation/RX3-EXP-02B_MX10 `
  --output-dir validation/RX3-EXP-03_Q3
```

The command requires the exact baseline SHA/fingerprint, unique Б1 position,
14Б2 profile, one-plane X-X state, heating evidence, and completed manual
RX3-EXP-02B report. It changes only field92 from `2,32` to `3,00`, creates
`generated_Q3.rx38` without overwrite, and stops before RX3. This is a
VALIDATION-only experimental exception. RX3-EXP-03 later confirmed field92 as
the scoped GUI Q input through GUI Q `2.32 → 3.00`, Q utilisation `0.028 →
0.037`, persisted numeric Q=3.00 and invariant non-target records. It does not
confirm Qx/Qy/LIRA axis/sign mappings or production compatibility.

The final planned force-family Phase A is non-mutating:

```powershell
python -m fireprotect.cli prepare-rx3-my-biaxial-phase-a `
  --templates-dir rx3 `
  --output-dir validation/RX3-EXP-04_A_MY_BIAXIAL_OBSERVATION
```

It selects exact mark Кс1, fingerprints the source record, ranks Mx/My/Q raw
numeric candidates across the bending corpus, creates only a copied template
and observation reports, and stops for a screenshot without Calculate or Save.

After the externally reviewed Phase A screenshot passes, the validation-only
My perturbation is prepared separately:

```powershell
python -m fireprotect.cli prepare-rx3-my5 `
  --phase-a-dir validation/RX3-EXP-04_A_MY_BIAXIAL_OBSERVATION `
  --observation validation/RX3-EXP-04_A_MY_BIAXIAL_OBSERVATION/phase_a_gui_observation.json `
  --output-dir validation/RX3-EXP-04B_MY5 `
  --mode VALIDATION
```

The command accepts only the exact Кс1 fingerprint at position 7 and changes
only field79 `4,3414 → 5,00`. At that historical preparation checkpoint field79
was `UNKNOWN/FORBIDDEN`; it is now scoped-confirmed for Кс1 / 20П GUI My with
`EXPERIMENTAL` write policy. DRAFT, PRODUCTION and the ordinary typed writer
remain blocked. The generated file is
for the pre-calc screenshot checkpoint only—no Calculate or Save action.

After the manual Calculate / Save sequence, the exact result is checked with:

```powershell
python -m fireprotect.cli validate-rx3-my5 `
  --bundle-dir validation/RX3-EXP-04B_MY5 `
  --calculated validation/RX3-EXP-04B_MY5/calculated_MY5.rx38 `
  --observation validation/RX3-EXP-04B_MY5/POSTCALC_OBSERVATION.json
```

RX3-EXP-04B passed this validator: field79 persisted numerically as `5.00`
with token normalization `5,00 → 5`, field78 persisted GUI-rounded Mx=`0.51`,
field92 remained zero, and all six non-target records were token-identical.
The approved scoped promotion records this evidence without opening the
production writer or any LIRA axis/sign mapping.

## LIRA batch review (no RX38 force writing)

The current MVP entrypoint imports a configurable CSV/HTML/XLSX source table and
creates a local review bundle only:

```powershell
python -m fireprotect.cli prepare-lira-review `
  --input <lira-export.xlsx-or-csv> `
  --mapping <mapping.json> `
  --output-dir <new-review-directory>
```

`tests/fixtures/lira_review/mapping.json` is a synthetic configuration matching
the verified native LIRA-SAPR bar-force layout. Native source components are
`N/Mk/My/Mz/Qy/Qz`; `Ry/Rz` are retained separately as source results. The
calculation-section value (`section_station`) is not treated as a member profile.
Unavailable profile/mark identity is explicit `null` and raises
`LIRA_MEMBER_PROFILE_IDENTITY_MISSING`, so accepted native rows never become
`ProjectElement` records.

Every source concept is explicit: exact headers (including embedded newlines),
unavailable columns as `null`, source units, parsing options, and a native-to-RX3
convention registry. The default convention is `UNKNOWN`; no `Mk→Mx`, `My→My`,
`Qy/Qz→Qx/Qy`, axis, or sign mapping is inferred. The bundle therefore reports
`LIRA_RX3_FORCE_CONVENTION` and never writes RX38 forces. JSON retains exact
OOXML numeric tokens, `Decimal` values, review-unit conversions, source cells,
SHA-256, distributions, component statistics, and candidate-only rows. Candidate
ranking is not semantic evidence. `forces_review.csv` is human-readable only.

Post-calc validation always requires an explicit target. Prefer the exact
BEFORE-record fingerprint or a 1-based `Tconstr` position; `--target-mark` is
accepted only when the mark resolves uniquely. Required result changes are
checked only on targets, while any text change in a non-target record fails
closed. For confirmed numeric fields with unambiguous units the audit preserves
the raw token change and separately classifies `Decimal`-equivalent normalization.

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
- [нормативная прослеживаемость](normative/traceability.md);
- [модель безопасности](docs/SAFETY_MODEL.md);
- [controlled experiments RX3](docs/RX3_CONTROLLED_EXPERIMENTS.md);
- [production release gate](docs/PRODUCTION_RELEASE_GATE.md);
- [RX3 validation evidence](README_VALIDATION.md).

## Непубликуемые исходные данные

Оригинальные RX38, поставка RX3, SQLite-базы, нормативные PDF, Excel и
проектная документация остаются локальными и исключены `.gitignore`. Тесты
создают синтетические fixtures во временных каталогах.
