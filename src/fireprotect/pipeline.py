"""Experimental, auditable LIRA -> RX3 checkpoint -> Excel pipeline."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from hashlib import sha256
import json
from pathlib import Path
import re
from typing import Any, Mapping

from .decision import RequiredFireResistanceDecision
from .execution import ExecutionMode
from .excel.obm import export_obm_workbook
from .lira import (
    CsvTableSource,
    ForceUnits,
    HtmlTableSource,
    LiraColumnMapping,
    LiraForceImporter,
    LiraForceRow,
    LiraRowSource,
    XlsxTableSource,
    apply_lira_force_row,
)
from .model import ProjectElement, Quantity, Unit
from .normative import (
    NormativeRegistry,
    NormativeTrace,
    validate_normative_trace,
)
from .project_io import (
    project_element_to_dict,
    read_project_element_json,
    write_project_element_json,
)
from .rx3.gui_validation import (
    prepare_rx3_validation,
    validate_rx3_result_files,
)
from .rx3.result import Rx3Result, apply_rx3_result, read_rx3_result
from .rx3.safety import (
    ActionZeroTolerance,
    EvidenceStatus,
    ForceConventionStatus,
    GuiExecutionEvidence,
    LiraRx3ForceConvention,
    Rx3SafetyContext,
    Rx3TemplateEvidence,
    Rx3TemplateUseCase,
    SteelCalculationProperties,
)
from .release import (
    BlockerCode,
    IssueReadiness,
    ReleaseBlocker,
    evaluate_issue_readiness,
)
from .technical import FireproofingTechnicalRegistry


class PipelineError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class PipelineRunResult:
    status: str
    workspace: Path
    audit_json: Path
    audit_markdown: Path
    waiting_for: tuple[Path, ...]
    excel_output: Path | None
    issue_readiness: IssueReadiness

    def as_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "workspace": str(self.workspace),
            "audit_json": str(self.audit_json),
            "audit_markdown": str(self.audit_markdown),
            "waiting_for": [str(path) for path in self.waiting_for],
            "excel_output": None if self.excel_output is None else str(self.excel_output),
            "issue_readiness": self.issue_readiness.as_dict(),
        }


def _hash(path: Path) -> str:
    digest = sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _resolve(base: Path, value: object, *, field: str, must_exist: bool = True) -> Path:
    if not isinstance(value, str) or not value.strip():
        raise PipelineError(f"{field} must be a non-empty path string")
    path = Path(value)
    if not path.is_absolute():
        path = base / path
    return path.resolve(strict=must_exist)


def _mapping(value: object) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise PipelineError("Pipeline configuration sections must be objects")
    return value


def _load_source(config: Mapping[str, Any], base: Path):
    source_path = _resolve(base, config.get("path"), field="lira.path")
    source_format = str(config.get("format", "")).casefold()
    options = _mapping(config.get("options", {}))
    source: LiraRowSource
    if source_format == "csv":
        allowed = {"encoding", "delimiter", "header_row"}
        unknown = set(options) - allowed
        if unknown:
            raise PipelineError(f"Unknown CSV options: {sorted(unknown)}")
        source = CsvTableSource(source_path, **dict(options))
    elif source_format == "html":
        allowed = {"encoding", "table_index", "header_row"}
        unknown = set(options) - allowed
        if unknown:
            raise PipelineError(f"Unknown HTML options: {sorted(unknown)}")
        source = HtmlTableSource(source_path, **dict(options))
    elif source_format == "xlsx":
        allowed = {"sheet_name", "header_row", "data_only"}
        unknown = set(options) - allowed
        if unknown:
            raise PipelineError(f"Unknown XLSX options: {sorted(unknown)}")
        source = XlsxTableSource(source_path, **dict(options))
    else:
        raise PipelineError("lira.format must be csv, html or xlsx")

    units = _mapping(config.get("units"))
    columns = _mapping(config.get("columns"))
    column_mapping = LiraColumnMapping(
        columns={str(key): str(value) for key, value in columns.items()},
        units=ForceUnits(**{key: str(value) for key, value in units.items()}),
        decimal_separator=str(config.get("decimal_separator", ".")),
        thousands_separator=config.get("thousands_separator"),
    )
    return source_path, LiraForceImporter(column_mapping).import_source(source)


def _select_lira_row(
    rows: list[LiraForceRow], element_config: Mapping[str, Any], element: ProjectElement
) -> LiraForceRow:
    selector = str(element_config.get("lira_element_id", element.element_id))
    matches = [row for row in rows if row.element_id == selector]
    for config_name, row_name in (("load_case", "load_case"), ("combination", "combination")):
        expected = element_config.get(config_name)
        if expected is not None:
            matches = [row for row in matches if getattr(row, row_name) == str(expected)]
    if len(matches) != 1:
        raise PipelineError(
            f"LIRA selector for element {element.element_id!r} matched {len(matches)} rows; "
            "specify load_case/combination explicitly"
        )
    return matches[0]


def _decision(value: object) -> RequiredFireResistanceDecision:
    data = dict(_mapping(value))
    raw_r = _mapping(data.pop("R", None))
    trace_data = data.pop("normative_trace", None)
    trace = None if trace_data is None else NormativeTrace(**dict(_mapping(trace_data)))
    try:
        return RequiredFireResistanceDecision(
            required_fire_resistance=Quantity.of(raw_r["value"], raw_r["unit"]),
            normative_trace=trace,
            **data,
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise PipelineError(f"Invalid RequiredFireResistanceDecision: {exc}") from exc


def _date_value(value: object, *, field: str, required: bool = True) -> date | None:
    if value is None and not required:
        return None
    if not isinstance(value, str):
        raise PipelineError(f"{field} must be an ISO date")
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise PipelineError(f"{field} must be an ISO date") from exc


def _quantity_value(value: object, *, field: str) -> Quantity:
    data = _mapping(value)
    if set(data) != {"value", "unit"}:
        raise PipelineError(f"{field} must contain exactly value and unit")
    try:
        return Quantity.of(data["value"], data["unit"])
    except (TypeError, ValueError) as exc:
        raise PipelineError(f"Invalid {field}: {exc}") from exc


def _bool_value(value: object, *, field: str) -> bool:
    if not isinstance(value, bool):
        raise PipelineError(f"{field} must be bool")
    return value


def _safety_context(
    value: object,
    *,
    mode: ExecutionMode,
) -> Rx3SafetyContext:
    data = dict(_mapping(value))
    tolerance_data = data.pop("action_zero_tolerance", None)
    if tolerance_data is None:
        tolerance = ActionZeroTolerance.strict()
    else:
        raw_tolerance = _mapping(tolerance_data)
        tolerance = ActionZeroTolerance(
            _quantity_value(raw_tolerance.get("force"), field="action_zero_tolerance.force"),
            _quantity_value(raw_tolerance.get("moment"), field="action_zero_tolerance.moment"),
        )

    template_data = data.pop("template_evidence", None)
    template_evidence = None
    if template_data is not None:
        raw = dict(_mapping(template_data))
        template_evidence = Rx3TemplateEvidence(
            use_case=Rx3TemplateUseCase(str(raw.get("use_case"))),
            status=EvidenceStatus(str(raw.get("status"))),
            source=str(raw.get("source", "")),
            engineer_confirmation=_bool_value(
                raw.get("engineer_confirmation"),
                field="template_evidence.engineer_confirmation",
            ),
            confirmed_by=raw.get("confirmed_by"),
            confirmed_at=_date_value(
                raw.get("confirmed_at"), field="template_evidence.confirmed_at", required=False
            ),
            version=raw.get("version"),
            calculation_profile_verified=raw.get(
                "calculation_profile_verified", False
            ),
        )

    convention_data = data.pop("force_convention", None)
    convention = None
    if convention_data is not None:
        raw = dict(_mapping(convention_data))
        convention = LiraRx3ForceConvention(
            source_system=str(raw.get("source_system", "")),
            target_system=str(raw.get("target_system", "")),
            positive_n_meaning=str(raw.get("positive_n_meaning", "")),
            negative_n_meaning=str(raw.get("negative_n_meaning", "")),
            local_axes=str(raw.get("local_axes", "")),
            moment_mapping=str(raw.get("moment_mapping", "")),
            shear_mapping=str(raw.get("shear_mapping", "")),
            multipliers=_mapping(raw.get("multipliers")),
            rule_name=str(raw.get("rule_name", "")),
            evidence_source=raw.get("evidence_source"),
            status=ForceConventionStatus(str(raw.get("status"))),
            engineer_confirmation=_bool_value(
                raw.get("engineer_confirmation"),
                field="force_convention.engineer_confirmation",
            ),
            confirmed_by=raw.get("confirmed_by"),
            confirmed_at=_date_value(
                raw.get("confirmed_at"), field="force_convention.confirmed_at", required=False
            ),
            version=raw.get("version"),
        )

    steel_data = data.pop("steel_properties", None)
    steel = None
    if steel_data is not None:
        raw = dict(_mapping(steel_data))

        def optional_quantity(name: str) -> Quantity | None:
            item = raw.get(name)
            return None if item is None else _quantity_value(item, field=f"steel_properties.{name}")

        steel = SteelCalculationProperties(
            steel_grade=str(raw.get("steel_grade", "")),
            nominal_yield_strength=optional_quantity("nominal_yield_strength"),
            design_yield_strength=optional_quantity("design_yield_strength"),
            rx3_stored_strength_parameter=optional_quantity(
                "rx3_stored_strength_parameter"
            ),
            thickness_min=optional_quantity("thickness_min"),
            thickness_max=optional_quantity("thickness_max"),
            elastic_modulus=_quantity_value(
                raw.get("elastic_modulus"), field="steel_properties.elastic_modulus"
            ),
            density=_quantity_value(raw.get("density"), field="steel_properties.density"),
            temperature_model=raw.get("temperature_model"),
            source_document=str(raw.get("source_document", "")),
            clause_or_table=str(raw.get("clause_or_table", "")),
            material_standard=str(raw.get("material_standard", "")),
            confidence=EvidenceStatus(str(raw.get("confidence"))),
            provenance=str(raw.get("provenance", "")),
            rx3_strength_mapping_verified=raw.get(
                "rx3_strength_mapping_verified", False
            ),
        )

    controlled = data.pop("controlled_experiment", False)
    allow_unverified = data.pop("allow_unverified_force_convention", False)
    if not isinstance(controlled, bool) or not isinstance(allow_unverified, bool):
        raise PipelineError("controlled experiment flags must be bool")
    if data:
        raise PipelineError(f"Unknown rx3_safety fields: {sorted(data)}")
    return Rx3SafetyContext(
        mode,
        tolerance,
        template_evidence,
        convention,
        steel,
        controlled,
        allow_unverified,
    )


def rx3_safety_context_from_dict(
    value: object, *, mode: ExecutionMode
) -> Rx3SafetyContext:
    """Public configuration boundary shared by the CLI and pipeline."""

    return _safety_context(value, mode=mode)


def _lira_dict(row: LiraForceRow) -> dict[str, Any]:
    return {
        "element_id": row.element_id,
        "section": row.section,
        "load_case": row.load_case,
        "combination": row.combination,
        "source_row": row.source_row,
        "SI": {
            "N": {"value": str(row.N), "unit": "N"},
            "Mx": {"value": str(row.Mx), "unit": "N*m"},
            "My": {"value": str(row.My), "unit": "N*m"},
            "Qx": {"value": str(row.Qx), "unit": "N"},
            "Qy": {"value": str(row.Qy), "unit": "N"},
        },
        "source_values": {
            "N": str(row.source.N),
            "Mx": str(row.source.Mx),
            "My": str(row.source.My),
            "Qx": str(row.source.Qx),
            "Qy": str(row.source.Qy),
            "units": row.source.units.as_dict(),
        },
    }


def _safe_name(value: str) -> str:
    result = re.sub(r"[^0-9A-Za-z\u0400-\u04FF_-]+", "_", value).strip("_")
    return result or "element"


def _write_or_verify_element(element: ProjectElement, path: Path) -> None:
    if not path.exists():
        write_project_element_json(element, path)
        return
    existing = read_project_element_json(path)
    if project_element_to_dict(existing) != project_element_to_dict(element):
        raise PipelineError(
            f"Pipeline inputs changed since the validation bundle was prepared: {path}. "
            "Use a new workspace for a new calculation."
        )


def _write_audit(workspace: Path, payload: dict[str, Any]) -> tuple[Path, Path]:
    json_path = workspace / "project_audit.json"
    markdown_path = workspace / "project_audit.md"
    json_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    lines = [
        "# Project audit",
        "",
        f"Статус: **{payload['status']}**.",
        "",
        "## Источники",
        "",
    ]
    for item in payload["source_files"]:
        lines.append(f"- `{item['path']}` — SHA-256 `{item['sha256']}`")
    lines.extend(["", "## Элементы", ""])
    for item in payload["elements"]:
        lines.append(f"- `{item['element_id']}` / `{item['mark']}`")
    lines.extend(["", "## Предупреждения", ""])
    lines.extend(f"- {item}" for item in payload["warnings"] or ["Нет"])
    lines.extend(["", "## Ошибки", ""])
    lines.extend(f"- {item}" for item in payload["errors"] or ["Нет"])
    lines.extend(["", "## Неподтверждённые данные", ""])
    lines.extend(f"- {item}" for item in payload["unverified_data"] or ["Нет"])
    if payload.get("waiting_for"):
        lines.extend(["", "## Требуется действие инженера", ""])
        for item in payload["waiting_for"]:
            lines.append(f"- Рассчитать и сохранить RX3: `{item}`")
    readiness = payload.get("issue_readiness")
    if readiness:
        lines.extend(
            [
                "",
                "## Issue readiness",
                "",
                f"Статус: **{readiness['status']}**.",
                "",
            ]
        )
        blockers = readiness.get("blockers", [])
        if blockers:
            lines.extend(
                f"- `{item['code']}`: {item['message']}"
                for item in blockers
            )
        else:
            lines.append("Блокеров нет.")
    lines.append("")
    markdown_path.write_text("\n".join(lines), encoding="utf-8", newline="\n")
    return json_path, markdown_path


def run_pipeline(config_path: str | Path) -> PipelineRunResult:
    """Run until RX3 is needed, then resume when calculated.rx38 files appear."""

    config_file = Path(config_path).resolve(strict=True)
    try:
        config = json.loads(config_file.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise PipelineError(f"Cannot read pipeline config: {exc}") from exc
    config = _mapping(config)
    schema_version = config.get("schema_version")
    if schema_version not in {1, 2}:
        raise PipelineError("pipeline schema_version must be 1 or 2")
    if schema_version == 2 and "execution_mode" not in config:
        raise PipelineError("pipeline schema_version 2 requires execution_mode")
    mode = ExecutionMode.parse(config.get("execution_mode", "DRAFT"))
    calculation_date = _date_value(
        config.get("calculation_date"),
        field="calculation_date",
        required=schema_version == 2,
    )
    base = config_file.parent
    workspace = _resolve(
        base, config.get("workspace"), field="workspace", must_exist=False
    )
    workspace.mkdir(parents=True, exist_ok=True)
    (workspace / "elements").mkdir(exist_ok=True)
    (workspace / "rx3").mkdir(exist_ok=True)

    release_blockers: list[ReleaseBlocker] = []
    release_warnings: list[str] = []
    if schema_version == 1:
        release_warnings.append(
            "Legacy pipeline schema_version 1 is interpreted as DRAFT"
        )

    repository_root = Path(__file__).resolve().parents[2]
    registry_value = config.get("normative_registry")
    normative_registry_path = (
        _resolve(base, registry_value, field="normative_registry")
        if registry_value is not None
        else repository_root / "normative" / "registry.yaml"
    )
    normative_registry = NormativeRegistry.load(normative_registry_path)

    technical_value = config.get("technical_registry")
    technical_registry_path = (
        _resolve(base, technical_value, field="technical_registry")
        if technical_value is not None
        else repository_root / "technical" / "fireproofing_registry.yaml"
    )
    technical_registry = FireproofingTechnicalRegistry.load(
        technical_registry_path
    )
    technical_entry_id = str(
        config.get("fireproofing_technical_entry", "OBM_EXCEL_TABLES_UNVERIFIED")
    )
    try:
        technical_entry = technical_registry.entries[technical_entry_id]
    except KeyError as exc:
        raise PipelineError(
            f"Fireproofing technical entry is not registered: {technical_entry_id}"
        ) from exc
    if not technical_entry.verified_for_production:
        release_blockers.append(
            ReleaseBlocker(
                BlockerCode.FIREPROOFING_TECHNICAL_DATA_UNVERIFIED,
                f"{technical_entry_id} is {technical_entry.status.value}",
            )
        )

    audit: dict[str, Any] = {
        "schema_version": 2,
        "input_schema_version": schema_version,
        "execution_mode": mode.value,
        "calculation_date": (
            calculation_date.isoformat() if calculation_date else None
        ),
        "status": "STARTED",
        "source_files": [
            {"role": "pipeline_config", "path": str(config_file), "sha256": _hash(config_file)},
            {
                "role": "normative_registry",
                "path": str(normative_registry_path),
                "sha256": _hash(normative_registry_path),
            },
            {
                "role": "technical_registry",
                "path": str(technical_registry_path),
                "sha256": _hash(technical_registry_path),
            },
        ],
        "elements": [],
        "lira_results": [],
        "rx3_results": [],
        "element_audits": [],
        "excel": None,
        "normative_traces": [],
        "engineer_inputs": [],
        "warnings": [],
        "errors": [],
        "unverified_data": [
            "UNVERIFIED_TECHNICAL_DATA: Excel thickness/consumption tables lack a primary technical document"
        ],
        "waiting_for": [],
        "fireproofing_technical_data": {
            "registry": str(technical_registry_path),
            "entry_id": technical_entry_id,
            "status": technical_entry.status.value,
            "verified_for_production": technical_entry.verified_for_production,
            "source_document": technical_entry.source_document,
            "source_page_or_table": technical_entry.source_page_or_table,
            "document_sha256": technical_entry.document_sha256,
        },
        "issue_readiness": None,
    }
    try:
        lira_config = _mapping(config.get("lira"))
        lira_path, lira_rows = _load_source(lira_config, base)
        audit["source_files"].append(
            {"role": "lira_export", "path": str(lira_path), "sha256": _hash(lira_path)}
        )
        raw_elements = config.get("elements")
        if not isinstance(raw_elements, list) or not raw_elements:
            raise PipelineError("elements must be a non-empty list")

        imported: list[ProjectElement] = []
        decisions: list[RequiredFireResistanceDecision] = []
        bundles: list[
            tuple[Path, Path, ProjectElement, Rx3SafetyContext, dict[str, Any]]
        ] = []
        for index, raw in enumerate(raw_elements, 1):
            item = _mapping(raw)
            base_element_path = _resolve(
                base, item.get("project_json"), field=f"elements[{index}].project_json"
            )
            template = _resolve(
                base, item.get("rx38_template"), field=f"elements[{index}].rx38_template"
            )
            audit["source_files"].extend(
                [
                    {"role": "project_element", "path": str(base_element_path), "sha256": _hash(base_element_path)},
                    {"role": "rx38_template", "path": str(template), "sha256": _hash(template)},
                ]
            )
            element = read_project_element_json(base_element_path)
            row = _select_lira_row(lira_rows, item, element)
            if "set_governing_combination" not in item or not isinstance(
                item["set_governing_combination"], bool
            ):
                raise PipelineError(
                    f"elements[{index}].set_governing_combination must be explicit bool"
                )
            element = apply_lira_force_row(
                element,
                row,
                source_file=lira_path,
                set_governing_combination=item["set_governing_combination"],
            )
            decision = _decision(item.get("required_fire_resistance_decision"))
            if element.required_fire_resistance is None or (
                element.required_fire_resistance.si_value
                != decision.required_fire_resistance.si_value
            ):
                raise PipelineError(
                    f"Element {element.element_id}: R differs from RequiredFireResistanceDecision"
                )
            if decision.construction_type.casefold() != element.element_type.casefold():
                raise PipelineError(
                    f"Element {element.element_id}: decision construction type does not match element"
                )
            try:
                decision.require_engineer_confirmation()
            except ValueError as exc:
                raise PipelineError(
                    f"Element {element.element_id}: {exc}"
                ) from exc
            decisions.append(decision)
            audit["engineer_inputs"].append(decision.as_dict())
            if calculation_date is not None:
                normative_validation = validate_normative_trace(
                    decision.normative_trace,
                    normative_registry,
                    calculation_date=calculation_date,
                    mode=mode,
                )
                release_blockers.extend(normative_validation.blockers)
                if (
                    mode is ExecutionMode.PRODUCTION
                    and normative_validation.blockers
                ):
                    raise PipelineError(
                        f"Element {element.element_id}: normative production gate blocked: "
                        + "; ".join(
                            blocker.code.value
                            for blocker in normative_validation.blockers
                        )
                    )
            else:
                normative_validation = None
            if decision.normative_trace is not None:
                audit["normative_traces"].append(
                    decision.as_dict()["normative_trace"]
                )
            else:
                audit["unverified_data"].append(
                    "UNVERIFIED_NORMATIVE_SOURCE: required fire resistance "
                    f"for element {element.element_id} is engineer-confirmed without NormativeTrace"
                )
            imported.append(element)
            audit["lira_results"].append(_lira_dict(row))

            stem = f"{index:03d}_{_safe_name(element.element_id)}"
            element_path = workspace / "elements" / f"{stem}.json"
            _write_or_verify_element(element, element_path)
            bundle_dir = workspace / "rx3" / stem
            generated = bundle_dir / "generated.rx38"
            safety_context = _safety_context(
                item.get("rx3_safety", {}),
                mode=mode,
            )
            if not generated.exists():
                prepare_rx3_validation(
                    element_path,
                    template,
                    bundle_dir,
                    template_mark=item.get("template_mark"),
                    safety_context=safety_context,
                )
            rx3_input_path = bundle_dir / "rx3_input.json"
            template_profile_path = bundle_dir / "rx3_template_profile.json"
            if not rx3_input_path.exists() or not template_profile_path.exists():
                raise PipelineError(
                    f"Incomplete RX3 validation bundle: {bundle_dir}"
                )
            rx3_input_audit = json.loads(rx3_input_path.read_text(encoding="utf-8"))
            template_profile_audit = json.loads(
                template_profile_path.read_text(encoding="utf-8")
            )
            bundle_diff_audit = json.loads(
                (bundle_dir / "diff_before_after.json").read_text(encoding="utf-8")
            )
            if element.area is None or element.heated_perimeter is None:
                raise PipelineError(
                    f"Element {element.element_id}: geometry audit requires area and heated perimeter"
                )
            element_audit = {
                "identity": {
                    "project_id": element.project_id,
                    "element_id": element.element_id,
                    "mark": element.mark,
                },
                "lira_source": _lira_dict(row),
                "force_convention": {
                    "status": (
                        safety_context.force_convention.status.value
                        if safety_context.force_convention
                        else "NOT_PROVIDED"
                    ),
                    "transformations": rx3_input_audit.get(
                        "force_transformations", []
                    ),
                    "governing_selector": {
                        "combination": element.governing_combination,
                        "explicit": item["set_governing_combination"],
                        "proves_mathematical_maximum": False,
                    },
                },
                "geometry": {
                    "source": {
                        "area": project_element_to_dict(element)["area"],
                        "heated_perimeter": project_element_to_dict(element)[
                            "heated_perimeter"
                        ],
                        "ptm": project_element_to_dict(element)["ptm"],
                    },
                    "calculated": {
                        "ptm_mm": str(
                            element.area.to(Unit.SQUARE_MILLIMETER).value
                            / element.heated_perimeter.to(Unit.MILLIMETER).value
                        ),
                        "formula": "area_mm2 / heated_perimeter_mm",
                    },
                    "provenance": {
                        name: project_element_to_dict(element)["provenance"].get(name)
                        for name in ("area", "heated_perimeter", "ptm")
                    },
                },
                "steel": {
                    "grade": element.steel_grade,
                    "Ry": project_element_to_dict(element)["Ry"],
                    "E": project_element_to_dict(element)["E"],
                    "density": project_element_to_dict(element)["density"],
                    "compatibility": bundle_diff_audit["steel_compatibility"],
                },
                "fire_resistance_decision": {
                    **decision.as_dict(),
                    "registry_validation": (
                        None
                        if normative_validation is None
                        else {
                            "valid_for_production": normative_validation.valid_for_production,
                            "blockers": [
                                blocker.as_dict()
                                for blocker in normative_validation.blockers
                            ],
                            "evidence": dict(normative_validation.evidence),
                        }
                    ),
                },
                "rx3_template": {
                    "path": str(template),
                    "sha256": _hash(template),
                    "profile": template_profile_audit,
                },
                "rx3_generated": {
                    "path": str(generated),
                    "sha256": _hash(generated),
                    "rx3_input": rx3_input_audit,
                    "changed_fields": bundle_diff_audit["changed_fields"],
                },
                "rx3_result": None,
                "fireproofing_technical_data": audit[
                    "fireproofing_technical_data"
                ],
                "excel": None,
                "issue_readiness": None,
                "declared_gui_execution_evidence": str(
                    item.get(
                        "gui_execution_evidence",
                        GuiExecutionEvidence.NOT_PROVIDED.value,
                    )
                ),
                "gui_evidence_reference": item.get("gui_evidence_reference"),
            }
            audit["element_audits"].append(element_audit)
            calculated = (
                _resolve(
                    base,
                    item["calculated_rx38"],
                    field=f"elements[{index}].calculated_rx38",
                    must_exist=False,
                )
                if item.get("calculated_rx38")
                else bundle_dir / "calculated.rx38"
            )
            bundles.append(
                (generated, calculated, element, safety_context, element_audit)
            )

        waiting = tuple(
            calculated
            for _, calculated, _, _, _ in bundles
            if not calculated.exists()
        )
        if waiting:
            for element_audit in audit["element_audits"]:
                profile_data = element_audit["rx3_template"]["profile"]
                if not profile_data.get("verified", False):
                    release_blockers.append(
                        ReleaseBlocker(
                            BlockerCode.RX3_TEMPLATE_PROFILE_UNVERIFIED,
                            "RX3 template calculation profile is not verified",
                            element_audit["identity"]["element_id"],
                        )
                    )
            release_blockers.extend(
                (
                    ReleaseBlocker(
                        BlockerCode.RX3_GUI_RECALCULATION_UNVERIFIED,
                        "RX3 GUI calculation has not been performed",
                    ),
                    ReleaseBlocker(
                        BlockerCode.STALE_RX3_RESULT,
                        "Generated RX38 still contains stale template result fields",
                    ),
                    ReleaseBlocker(
                        BlockerCode.EXCEL_RECALCULATION_REQUIRED,
                        "Microsoft Excel recalculation has not been confirmed",
                    ),
                )
            )
            readiness = evaluate_issue_readiness(
                mode=mode,
                blockers=tuple(release_blockers),
                warnings=tuple(release_warnings),
                evidence={"stage": "WAITING_FOR_RX3"},
            )
            audit["status"] = "WAITING_FOR_RX3"
            audit["waiting_for"] = [str(path) for path in waiting]
            audit["elements"] = [project_element_to_dict(item) for item in imported]
            audit["warnings"].append(
                "Pipeline stopped deliberately: RX3 must be run in the GUI"
            )
            audit["issue_readiness"] = readiness.as_dict()
            for element_audit in audit["element_audits"]:
                element_audit["issue_readiness"] = readiness.as_dict()
            audit_json, audit_md = _write_audit(workspace, audit)
            return PipelineRunResult(
                "WAITING_FOR_RX3",
                workspace,
                audit_json,
                audit_md,
                waiting,
                None,
                readiness,
            )

        completed: list[ProjectElement] = []
        rx3_results: list[Rx3Result] = []
        validation_statuses: list[str] = []
        for index, (
            generated,
            calculated,
            element,
            safety_context,
            element_audit,
        ) in enumerate(bundles, 1):
            validation_dir = generated.parent
            try:
                gui_evidence = GuiExecutionEvidence(
                    element_audit["declared_gui_execution_evidence"]
                )
            except ValueError as exc:
                raise PipelineError(
                    f"Element {element.element_id}: invalid gui_execution_evidence"
                ) from exc
            validation = validate_rx3_result_files(
                generated,
                calculated,
                json_report=validation_dir / "rx3_validation_report.json",
                markdown_report=validation_dir / "rx3_validation_report.md",
                overwrite=True,
                gui_execution_evidence=gui_evidence,
                evidence_reference=element_audit["gui_evidence_reference"],
            )
            validation_statuses.append(validation.data["status"])
            if not validation.data["rx3_recalculation_proven"]:
                raise PipelineError(
                    f"Element {element.element_id}: calculated.rx38 does not prove recalculation of RX3 result fields 44 and 54"
                )
            if not validation.data["gui_recalculation_verified"]:
                release_blockers.append(
                    ReleaseBlocker(
                        BlockerCode.RX3_GUI_RECALCULATION_UNVERIFIED,
                        "RX3 file changed, but GUI execution evidence is insufficient",
                        element.element_id,
                    )
                )
                if safety_context.mode is ExecutionMode.PRODUCTION:
                    raise PipelineError(
                        f"Element {element.element_id}: production requires verified GUI execution evidence"
                    )
            result = read_rx3_result(calculated, mark=element.mark)
            completed_element = apply_rx3_result(element, result)
            completed.append(completed_element)
            rx3_results.append(result)
            write_project_element_json(
                completed_element,
                workspace / "elements" / f"{index:03d}_{_safe_name(element.element_id)}_rx3.json",
                overwrite=True,
            )
            audit["source_files"].append(
                {"role": "calculated_rx38", "path": str(calculated), "sha256": _hash(calculated)}
            )
            audit["rx3_results"].append(
                {
                    "result": result.as_dict(),
                    "validation_report": str(validation.json_path),
                }
            )
            element_audit["rx3_result"] = {
                "path": str(calculated),
                "sha256": _hash(calculated),
                "validation_status": validation.data["status"],
                "gui_execution_evidence": validation.data[
                    "gui_execution_evidence"
                ],
                "gui_recalculation_verified": validation.data[
                    "gui_recalculation_verified"
                ],
                "confirmed_outputs": result.as_dict(),
                "probable_changes": validation.data["records"][0][
                    "probable_changes"
                ],
                "unknown_changes": validation.data["records"][0][
                    "unknown_changes"
                ],
            }

        excel_output: Path | None = None
        excel_config = config.get("excel")
        if excel_config is not None:
            excel = _mapping(excel_config)
            excel_template = _resolve(base, excel.get("template"), field="excel.template")
            excel_output = _resolve(
                base, excel.get("output"), field="excel.output", must_exist=False
            )
            audit["source_files"].append(
                {"role": "excel_template", "path": str(excel_template), "sha256": _hash(excel_template)}
            )
            excel_report = export_obm_workbook(
                completed,
                decisions,
                excel_template,
                excel_output,
                json_report=workspace / "excel_export_audit.json",
                markdown_report=workspace / "excel_export_audit.md",
                mode=mode,
                technical_entry=technical_entry,
                verified_template_sha256=excel.get("verified_template_sha256"),
            )
            audit["excel"] = excel_report.as_dict()
            audit["warnings"].extend(excel_report.warnings)
            release_blockers.append(
                ReleaseBlocker(
                    BlockerCode.EXCEL_RECALCULATION_REQUIRED,
                    "Generated workbook has not been recalculated in Microsoft Excel",
                )
            )
            if excel_report.template_verification_status != "VERIFIED":
                release_blockers.append(
                    ReleaseBlocker(
                        BlockerCode.EXCEL_TEMPLATE_UNVERIFIED,
                        "Excel template SHA-256 is not registered as verified",
                    )
                )
            for element_audit in audit["element_audits"]:
                element_audit["excel"] = excel_report.as_dict()
        else:
            audit["warnings"].append(
                "EXCEL_STAGE_NOT_CONFIGURED: RX3 results imported, Excel copy not created"
            )
            release_blockers.extend(
                (
                    ReleaseBlocker(
                        BlockerCode.EXCEL_TEMPLATE_UNVERIFIED,
                        "Excel compatibility export is not configured",
                    ),
                    ReleaseBlocker(
                        BlockerCode.EXCEL_RECALCULATION_REQUIRED,
                        "Microsoft Excel recalculation is not confirmed",
                    ),
                )
            )

        readiness = evaluate_issue_readiness(
            mode=mode,
            blockers=tuple(release_blockers),
            warnings=tuple(release_warnings),
            evidence={
                "rx3_validation_statuses": validation_statuses,
                "technical_data_status": technical_entry.status.value,
                "excel_recalculation": "EXCEL_RECALCULATION_REQUIRED",
            },
        )
        audit["status"] = (
            "RX3_RESULT_ANALYSED"
            if all(status == "RX3_RESULT_ANALYSED" for status in validation_statuses)
            else "RX3_GUI_RECALCULATION_UNVERIFIED"
        )
        audit["elements"] = [project_element_to_dict(item) for item in completed]
        audit["issue_readiness"] = readiness.as_dict()
        for element_audit in audit["element_audits"]:
            element_audit["issue_readiness"] = readiness.as_dict()
        audit_json, audit_md = _write_audit(workspace, audit)
        return PipelineRunResult(
            audit["status"],
            workspace,
            audit_json,
            audit_md,
            (),
            excel_output,
            readiness,
        )
    except Exception as exc:
        audit["status"] = "FAILED"
        audit["errors"].append(f"{type(exc).__name__}: {exc}")
        audit_json, _ = _write_audit(workspace, audit)
        if isinstance(exc, PipelineError):
            raise
        raise PipelineError(f"Pipeline failed; see {audit_json}: {exc}") from exc
