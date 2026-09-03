# Production release gate

## Typed gate evidence hardening

`evaluate_issue_readiness` derives mandatory gates only from typed
`ProductionEvidence`. A caller-supplied `Mapping[str, bool]` is audit data and
cannot produce `READY_FOR_ISSUE`.

The gate set includes exact heating-exposure binding, steel temperature profile
compatibility, trusted Excel template identity and lookup-table content. Excel
verification is linked to the selected technical-data entry and version.

The scoped `My -> field50` and `Qz -> field92` magnitude transforms do not
select a governing LIRA result. `LIRA_GOVERNING_RESULT_SELECTION_UNRESOLVED`
therefore remains an independent generation blocker: station/load-case/РСУ/РСН
and combination semantics must be validated before any LIRA-to-RX38 force
generation. This keeps `rx38_force_generation_allowed=false` and does not alter
`IssueReadiness`, which remains `NOT_READY_FOR_ISSUE`.

## Решение

Центральный `IssueReadiness` содержит status, blockers, warnings и evidence.
Единственное условие `READY_FOR_ISSUE`: режим `PRODUCTION`, пустой список
blockers и явное положительное evidence по каждому обязательному production gate.
Текущий репозиторий и имеющиеся проектные источники дают
`NOT_READY_FOR_ISSUE`.

## Обязательные доказательства

- нулевые неподтверждённые action-компоненты или CONFIRMED mappings;
- проверенная LIRA -> RX3 force/sign convention;
- согласованные марка и расчётные свойства стали;
- проверенный `AXIAL_ONLY` template calculation profile;
- `NormativeTrace`, production-допустимая редакция, дата действия и SHA-256;
- первичная техническая документация производителя с проверенным hash;
- рассчитанный RX3-файл, не byte-identical generated, и GUI evidence;
- проверенная версия Excel template;
- фактический пересчёт копии в Microsoft Excel.

Коды blockers включают `UNVERIFIED_RX38_*_MAPPING`,
`UNVERIFIED_FORCE_CONVENTION`, `STEEL_TEMPLATE_INCOMPATIBLE`,
`LIRA_GOVERNING_RESULT_SELECTION_UNRESOLVED`,
`RX3_TEMPLATE_PROFILE_UNVERIFIED`, `NORMATIVE_*`,
`FIREPROOFING_TECHNICAL_DATA_UNVERIFIED`, `STALE_RX3_RESULT`,
`RX3_GUI_RECALCULATION_UNVERIFIED`, `EXCEL_TEMPLATE_UNVERIFIED` и
`EXCEL_RECALCULATION_REQUIRED`.

## Статусы файлов

`generated.rx38` является подготовленным входом, а не результатом.
`calculated.rx38` без достаточного GUI evidence остаётся
`RX3_GUI_RECALCULATION_UNVERIFIED`. Excel output является
`EXCEL_COMPATIBILITY_EXPORT`, а не `CANONICAL_PROJECT_DATA`; до фактического
пересчёта он имеет `EXCEL_RECALCULATION_REQUIRED`.

Ни наличие файла, ни warning, ни успешный тест не заменяют отсутствующее
инженерное, нормативное или техническое доказательство.
