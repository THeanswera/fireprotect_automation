"""Experimental, auditable LIRA -> RX3 checkpoint -> Excel pipeline."""

from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
import json
from pathlib import Path
import re
from typing import Any, Mapping

from .decision import RequiredFireResistanceDecision
from .excel.obm import export_obm_workbook
from .lira import (
    CsvTableSource,
    ForceUnits,
    HtmlTableSource,
    LiraColumnMapping,
    LiraForceImporter,
    LiraForceRow,
    XlsxTableSource,
    apply_lira_force_row,
)
from .model import ProjectElement, Quantity
from .normative import NormativeTrace
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

    def as_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "workspace": str(self.workspace),
            "audit_json": str(self.audit_json),
            "audit_markdown": str(self.audit_markdown),
            "waiting_for": [str(path) for path in self.waiting_for],
            "excel_output": None if self.excel_output is None else str(self.excel_output),
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
    if config.get("schema_version") != 1:
        raise PipelineError("pipeline schema_version must be 1")
    base = config_file.parent
    workspace = _resolve(
        base, config.get("workspace"), field="workspace", must_exist=False
    )
    workspace.mkdir(parents=True, exist_ok=True)
    (workspace / "elements").mkdir(exist_ok=True)
    (workspace / "rx3").mkdir(exist_ok=True)

    audit: dict[str, Any] = {
        "schema_version": 1,
        "status": "STARTED",
        "source_files": [
            {"role": "pipeline_config", "path": str(config_file), "sha256": _hash(config_file)}
        ],
        "elements": [],
        "lira_results": [],
        "rx3_results": [],
        "excel": None,
        "normative_traces": [],
        "engineer_inputs": [],
        "warnings": [],
        "errors": [],
        "unverified_data": [
            "UNVERIFIED_TECHNICAL_DATA: Excel thickness/consumption tables lack a primary technical document"
        ],
        "waiting_for": [],
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
        bundles: list[tuple[Path, Path, ProjectElement]] = []
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
            if decision.normative_trace is not None:
                audit["normative_traces"].append(decision.as_dict()["normative_trace"])
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
            if not generated.exists():
                prepare_rx3_validation(
                    element_path,
                    template,
                    bundle_dir,
                    template_mark=item.get("template_mark"),
                )
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
            bundles.append((generated, calculated, element))

        waiting = tuple(calculated for _, calculated, _ in bundles if not calculated.exists())
        if waiting:
            audit["status"] = "WAITING_FOR_RX3"
            audit["waiting_for"] = [str(path) for path in waiting]
            audit["elements"] = [project_element_to_dict(item) for item in imported]
            audit["warnings"].append(
                "Pipeline stopped deliberately: RX3 must be run in the GUI"
            )
            audit_json, audit_md = _write_audit(workspace, audit)
            return PipelineRunResult(
                "WAITING_FOR_RX3", workspace, audit_json, audit_md, waiting, None
            )

        completed: list[ProjectElement] = []
        rx3_results: list[Rx3Result] = []
        for index, (generated, calculated, element) in enumerate(bundles, 1):
            validation_dir = generated.parent
            validation = validate_rx3_result_files(
                generated,
                calculated,
                json_report=validation_dir / "rx3_validation_report.json",
                markdown_report=validation_dir / "rx3_validation_report.md",
                overwrite=True,
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
            )
            audit["excel"] = excel_report.as_dict()
            audit["warnings"].extend(excel_report.warnings)
        else:
            audit["warnings"].append(
                "EXCEL_STAGE_NOT_CONFIGURED: RX3 results imported, Excel copy not created"
            )

        # File analysis cannot prove that the RX3 GUI was actually used or
        # that an engineer accepted the displayed values.
        audit["status"] = "RX3_RESULT_IMPORTED_PIPELINE_COMPLETE"
        audit["elements"] = [project_element_to_dict(item) for item in completed]
        audit_json, audit_md = _write_audit(workspace, audit)
        return PipelineRunResult(
            audit["status"], workspace, audit_json, audit_md, (), excel_output
        )
    except Exception as exc:
        audit["status"] = "FAILED"
        audit["errors"].append(f"{type(exc).__name__}: {exc}")
        audit_json, _ = _write_audit(workspace, audit)
        if isinstance(exc, PipelineError):
            raise
        raise PipelineError(f"Pipeline failed; see {audit_json}: {exc}") from exc
