from __future__ import annotations

import argparse
import json
from pathlib import Path

from .execution import ExecutionMode
from .lira import (
    prepare_lira_review_bundle,
    prepare_lira_selection_bundle,
    validate_lira_governing_selection,
)
from .project_io import read_project_element_json
from .pipeline import run_pipeline, rx3_safety_context_from_dict
from .rx3.project_adapter import create_rx38_from_project_element
from .rx3.gui_validation import (
    prepare_rx3_validation,
    validate_rx3_result_files,
)
from .rx3.experiment import (
    load_bending_report_references,
    prepare_rx3_bending_phase_a,
    prepare_rx3_bending_mx10_validation,
    prepare_rx3_bending_q3_validation,
    prepare_rx3_experiment_phase_a,
    prepare_rx3_my5_validation,
    prepare_rx3_my_biaxial_phase_a,
    validate_rx3_bending_q3_result,
    validate_rx3_my5_result,
)
from .rx3.parser import Rx38Construction, construction_records, read_rx38
from .rx3.profiles import ProfileRepository, list_tables
from .rx3.safety import GuiExecutionEvidence, Rx3SafetyContext
from .validation import validate_rx38_record


def _new_report_path(path: Path, *protected: Path) -> Path:
    target = path.resolve(strict=False)
    protected_paths = {item.resolve(strict=False) for item in protected}
    if target in protected_paths:
        raise ValueError("Report path must differ from every input/output data file")
    if target.exists():
        raise ValueError(f"Report already exists; refusing to overwrite it: {target}")
    return target


def cmd_inspect_rx38(args: argparse.Namespace) -> None:
    data = []
    for record in construction_records(read_rx38(args.file)):
        construction = Rx38Construction.from_record(record)
        data.append({
            "mark": construction.mark,
            "section_type": construction.section_type,
            "standard": construction.profile_standard,
            "profile": construction.profile_name,
            "area_mm2": str(construction.area_mm2),
            "heated_perimeter_mm": str(construction.heated_perimeter_mm),
            "ptm_mm": str(construction.ptm_mm),
            "section_factor_per_m": str(construction.section_factor_per_m),
            "field_count": len(construction.raw_fields),
        })
    print(json.dumps(data, ensure_ascii=False, indent=2))


def cmd_validate_rx38(args: argparse.Namespace) -> None:
    records = construction_records(read_rx38(args.file))
    issues = [issue for record in records for issue in validate_rx38_record(record)]
    for issue in issues:
        print(f"{issue.severity}\t{issue.mark or '-'}\t{issue.message}")
    if not issues:
        print("OK: базовая структурная проверка пройдена")


def cmd_tables(args: argparse.Namespace) -> None:
    print("\n".join(list_tables(args.db)))


def cmd_lookup_profile(args: argparse.Namespace) -> None:
    result = ProfileRepository(args.db).search(args.query, args.standard)
    payload = {
        "status": result.status,
        "query": result.query,
        "candidates": [
            {
                "table": candidate.table,
                "standard": candidate.standard,
                "designation": candidate.designation,
                "geometry": {key: str(value) if value is not None else None for key, value in candidate.geometry.__dict__.items()},
                "source_record": candidate.source_record,
            }
            for candidate in result.candidates
        ],
    }
    print(json.dumps(payload, ensure_ascii=False, indent=2, default=str))


def cmd_rx38_create(args: argparse.Namespace) -> None:
    report_path = (
        None
        if args.report is None
        else _new_report_path(args.report, args.input, args.template, args.output)
    )
    element = read_project_element_json(args.input)
    context = _read_safety_context(args.safety_context, args.mode)
    report = create_rx38_from_project_element(
        element,
        args.template,
        args.output,
        template_mark=args.template_mark,
        safety_context=context,
    )
    payload = json.dumps(report.as_dict(), ensure_ascii=False, indent=2)
    if report_path is not None:
        report_path.parent.mkdir(parents=True, exist_ok=True)
        with report_path.open("x", encoding="utf-8", newline="\n") as stream:
            stream.write(payload + "\n")
    print(payload)


def cmd_prepare_rx3_validation(args: argparse.Namespace) -> None:
    context = _read_safety_context(args.safety_context, args.mode)
    bundle = prepare_rx3_validation(
        args.input,
        args.template,
        args.output_dir,
        template_mark=args.template_mark,
        safety_context=context,
    )
    print(
        json.dumps(
            {
                "directory": str(bundle.directory),
                "template": str(bundle.template),
                "generated": str(bundle.generated),
                "project_element": str(bundle.project_element),
                "rx3_input": str(bundle.rx3_input),
                "template_profile": str(bundle.template_profile),
                "diff_json": str(bundle.diff_json),
                "diff_markdown": str(bundle.diff_markdown),
                "instructions": str(bundle.instructions),
            },
            ensure_ascii=False,
            indent=2,
        )
    )


def cmd_prepare_rx3_phase_a(args: argparse.Namespace) -> None:
    templates = sorted(args.templates_dir.rglob("*.rx38"))
    bundle = prepare_rx3_experiment_phase_a(
        templates,
        args.rx3_db,
        args.output_dir,
        experiment_id=args.experiment_id,
        project_element_id=args.project_element_id,
        heating_sides=args.heating_sides,
    )
    print(
        json.dumps(
            {
                "directory": str(bundle.directory),
                "selected_source": str(bundle.selected_source),
                "selected_mark": bundle.selected_mark,
                "template": str(bundle.template),
                "template_summary_json": str(bundle.template_summary_json),
                "template_summary_markdown": str(bundle.template_summary_markdown),
                "heating_evidence_template": str(bundle.heating_evidence_template),
                "checklist": str(bundle.checklist),
                "selection_report": str(bundle.selection_report),
                "project_element_draft": str(bundle.project_element_draft),
                "status": "WAITING_FOR_PHASE_A_GUI_OBSERVATION",
            },
            ensure_ascii=False,
            indent=2,
        )
    )


def cmd_prepare_rx3_bending_phase_a(args: argparse.Namespace) -> None:
    templates = sorted(args.templates_dir.rglob("*.rx38"))
    references = load_bending_report_references(args.report_values)
    bundle = prepare_rx3_bending_phase_a(
        templates,
        args.rx3_db,
        references,
        args.output_dir,
        experiment_id=args.experiment_id,
    )
    print(
        json.dumps(
            {
                "directory": str(bundle.directory),
                "selected_source": str(bundle.selected_source),
                "selected_mark": bundle.selected_mark,
                "template": str(bundle.template),
                "template_summary_json": str(bundle.template_summary_json),
                "selection_report": str(bundle.selection_report),
                "expected_report_values": str(bundle.expected_report_values),
                "checklist": str(bundle.checklist),
                "gui_instructions": str(bundle.gui_instructions),
                "report_references": str(bundle.report_references),
                "status": "WAITING_FOR_BENDING_TEMPLATE_GUI_OBSERVATION",
            },
            ensure_ascii=False,
            indent=2,
        )
    )


def cmd_prepare_rx3_bending_mx10(args: argparse.Namespace) -> None:
    bundle = prepare_rx3_bending_mx10_validation(
        args.phase_a_dir,
        args.observation,
        args.output_dir,
        experiment_id=args.experiment_id,
    )
    print(
        json.dumps(
            {
                "directory": str(bundle.directory),
                "template": str(bundle.template),
                "generated": str(bundle.generated),
                "generated_sha256": bundle.generated_sha256,
                "project_element": str(bundle.project_element),
                "template_profile": str(bundle.template_profile),
                "heating_evidence": str(bundle.heating_evidence),
                "precalc_diff_json": str(bundle.diff_json),
                "precalc_diff_markdown": str(bundle.diff_markdown),
                "expected_gui": str(bundle.expected_gui),
                "checklist": str(bundle.checklist),
                "instructions": str(bundle.instructions),
                "audit": str(bundle.audit),
                "status": "WAITING_FOR_MX10_PRECALC_GUI_VERIFICATION",
            },
            ensure_ascii=False,
            indent=2,
        )
    )


def cmd_prepare_rx3_bending_q3(args: argparse.Namespace) -> None:
    bundle = prepare_rx3_bending_q3_validation(
        args.mx_validation_dir,
        args.output_dir,
        experiment_id=args.experiment_id,
    )
    print(
        json.dumps(
            {
                "directory": str(bundle.directory),
                "template": str(bundle.template),
                "generated": str(bundle.generated),
                "generated_sha256": bundle.generated_sha256,
                "project_element": str(bundle.project_element),
                "template_profile": str(bundle.template_profile),
                "heating_evidence": str(bundle.heating_evidence),
                "compatibility_evidence": str(bundle.compatibility_evidence),
                "precalc_diff_json": str(bundle.diff_json),
                "precalc_diff_markdown": str(bundle.diff_markdown),
                "expected_gui": str(bundle.expected_gui),
                "checklist": str(bundle.checklist),
                "instructions": str(bundle.instructions),
                "postcalc_observation_template": str(
                    bundle.postcalc_observation_template
                ),
                "audit": str(bundle.audit),
                "status": "WAITING_FOR_Q3_GUI_CALCULATION",
            },
            ensure_ascii=False,
            indent=2,
        )
    )


def cmd_validate_rx3_bending_q3(args: argparse.Namespace) -> None:
    report = validate_rx3_bending_q3_result(
        args.bundle_dir,
        args.calculated,
        args.observation,
        json_report=args.json_report,
        markdown_report=args.markdown_report,
    )
    print(
        json.dumps(
            {
                "json_report": str(report.json_path),
                "markdown_report": str(report.markdown_path),
                "status": report.data["status"],
                "schema_mapping_promoted": report.data["schema_mapping_promoted"],
            },
            ensure_ascii=False,
            indent=2,
        )
    )


def cmd_prepare_rx3_my_biaxial_phase_a(args: argparse.Namespace) -> None:
    bundle = prepare_rx3_my_biaxial_phase_a(
        sorted(args.templates_dir.rglob("*.rx38")),
        args.output_dir,
        experiment_id=args.experiment_id,
        mark=args.mark,
        reference_mx_knm=args.mx_reference,
        reference_my_knm=args.my_reference,
        reference_q_kn=args.q_reference,
        tolerance=args.tolerance,
        evidence_reference=args.evidence_reference,
    )
    print(
        json.dumps(
            {
                "directory": str(bundle.directory),
                "selected_source": str(bundle.selected_source),
                "selected_mark": bundle.selected_mark,
                "source_sha256": bundle.source_sha256,
                "target_fingerprint": bundle.target_fingerprint,
                "target_position_1_based": bundle.target_position,
                "template": str(bundle.template),
                "template_summary": str(bundle.template_summary),
                "candidate_analysis_json": str(bundle.candidate_analysis_json),
                "candidate_analysis_markdown": str(
                    bundle.candidate_analysis_markdown
                ),
                "expected_gui": str(bundle.expected_gui),
                "checklist": str(bundle.checklist),
                "gui_instructions": str(bundle.gui_instructions),
                "audit": str(bundle.audit),
                "status": "WAITING_FOR_MY_BIAXIAL_GUI_SCREENSHOT",
            },
            ensure_ascii=False,
            indent=2,
        )
    )


def cmd_prepare_rx3_my5(args: argparse.Namespace) -> None:
    bundle = prepare_rx3_my5_validation(
        args.phase_a_dir,
        args.observation,
        args.output_dir,
        mode=args.mode,
        experiment_id=args.experiment_id,
    )
    print(
        json.dumps(
            {
                "directory": str(bundle.directory),
                "template": str(bundle.template),
                "generated": str(bundle.generated),
                "generated_sha256": bundle.generated_sha256,
                "precalc_diff": str(bundle.diff_json),
                "audit": str(bundle.audit),
                "status": "WAITING_FOR_MY5_PRECALC_GUI_VERIFICATION",
            },
            ensure_ascii=False,
            indent=2,
        )
    )


def cmd_validate_rx3_my5(args: argparse.Namespace) -> None:
    report = validate_rx3_my5_result(
        args.bundle_dir,
        args.calculated,
        args.observation,
        json_report=args.json_report,
        markdown_report=args.markdown_report,
    )
    print(
        json.dumps(
            {
                "json_report": str(report.json_path),
                "markdown_report": str(report.markdown_path),
                "status": report.data["status"],
                "schema_mapping_promoted": report.data["schema_mapping_promoted"],
            },
            ensure_ascii=False,
            indent=2,
        )
    )


def cmd_validate_rx3_result(args: argparse.Namespace) -> None:
    report = validate_rx3_result_files(
        args.before,
        args.after,
        json_report=args.json_report,
        markdown_report=args.markdown_report,
        overwrite=args.overwrite,
        gui_execution_evidence=GuiExecutionEvidence(args.gui_evidence),
        evidence_reference=args.evidence_reference,
        target_record_fingerprints=args.target_fingerprints or (),
        target_record_positions=args.target_positions or (),
        target_marks=args.target_marks or (),
    )
    print(
        json.dumps(
            {
                "json_report": str(report.json_path),
                "markdown_report": str(report.markdown_path),
                "status": report.data["status"],
            },
            ensure_ascii=False,
            indent=2,
        )
    )


def _read_safety_context(
    path: Path | None, mode: str
) -> Rx3SafetyContext:
    payload = {}
    if path is not None:
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ValueError(f"Cannot read RX3 safety context: {exc}") from exc
    return rx3_safety_context_from_dict(
        payload,
        mode=ExecutionMode.parse(mode),
    )


def cmd_rx38_experiment_diff(args: argparse.Namespace) -> None:
    report = validate_rx3_result_files(
        args.base,
        args.changed,
        json_report=args.json_report,
        markdown_report=args.markdown_report,
        overwrite=args.overwrite,
        gui_execution_evidence=GuiExecutionEvidence.HASH_ONLY,
        evidence_reference=args.experiment_id,
        target_record_fingerprints=args.target_fingerprints or (),
        target_record_positions=args.target_positions or (),
        target_marks=args.target_marks or (),
    )
    print(json.dumps(report.data, ensure_ascii=False, indent=2))


def cmd_pipeline(args: argparse.Namespace) -> None:
    result = run_pipeline(args.config)
    print(json.dumps(result.as_dict(), ensure_ascii=False, indent=2))


def cmd_prepare_lira_review(args: argparse.Namespace) -> None:
    existing = tuple(
        read_project_element_json(path) for path in (args.project_element or ())
    )
    bundle = prepare_lira_review_bundle(
        args.input,
        args.mapping,
        args.output_dir,
        existing_elements=existing,
    )
    print(json.dumps(bundle.as_dict(), ensure_ascii=False, indent=2))


def cmd_prepare_lira_selection(args: argparse.Namespace) -> None:
    bundle = prepare_lira_selection_bundle(args.forces, args.output_dir)
    print(json.dumps(bundle.as_dict(), ensure_ascii=False, indent=2))


def cmd_validate_lira_selection(args: argparse.Namespace) -> None:
    report = validate_lira_governing_selection(args.candidates, args.selection)
    payload = report.as_dict()
    if args.report is not None:
        target = _new_report_path(args.report, args.candidates, args.selection)
        target.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
    print(json.dumps(payload, ensure_ascii=False, indent=2))


def main() -> None:
    parser = argparse.ArgumentParser(prog="fireprotect")
    subparsers = parser.add_subparsers(required=True)

    command = subparsers.add_parser("inspect-rx38")
    command.add_argument("file", type=Path)
    command.set_defaults(func=cmd_inspect_rx38)

    command = subparsers.add_parser("validate-rx38")
    command.add_argument("file", type=Path)
    command.set_defaults(func=cmd_validate_rx38)

    command = subparsers.add_parser("rx3-tables")
    command.add_argument("db", type=Path)
    command.set_defaults(func=cmd_tables)

    command = subparsers.add_parser("lookup-profile")
    command.add_argument("db", type=Path)
    command.add_argument("query")
    command.add_argument("--standard")
    command.set_defaults(func=cmd_lookup_profile)

    command = subparsers.add_parser(
        "rx38-create",
        help="Create RX38 from ProjectElement JSON using a compatible safe template",
    )
    command.add_argument("input", type=Path)
    command.add_argument("template", type=Path)
    command.add_argument("output", type=Path)
    command.add_argument("--template-mark")
    command.add_argument("--report", type=Path)
    command.add_argument("--mode", choices=[item.value for item in ExecutionMode], default="DRAFT")
    command.add_argument("--safety-context", type=Path)
    command.set_defaults(func=cmd_rx38_create)

    command = subparsers.add_parser(
        "prepare-rx3-validation",
        help="Create a self-contained folder for the manual RX3 GUI checkpoint",
    )
    command.add_argument("input", type=Path)
    command.add_argument("template", type=Path)
    command.add_argument(
        "--output-dir", type=Path, default=Path("validation/rx3_gui_test")
    )
    command.add_argument("--template-mark")
    command.add_argument("--mode", choices=[item.value for item in ExecutionMode], default="VALIDATION")
    command.add_argument("--safety-context", type=Path)
    command.set_defaults(func=cmd_prepare_rx3_validation)

    command = subparsers.add_parser(
        "prepare-rx3-phase-a",
        help="Rank local RX38 templates and prepare a non-generating GUI observation bundle",
    )
    command.add_argument("--templates-dir", type=Path, required=True)
    command.add_argument("--rx3-db", type=Path, required=True)
    command.add_argument(
        "--output-dir",
        type=Path,
        default=Path("validation/RX3-EXP-01_A_TEMPLATE_OBSERVATION"),
    )
    command.add_argument("--experiment-id", default="RX3-EXP-01")
    command.add_argument("--project-element-id", default="K1")
    command.add_argument("--heating-sides", type=int, default=4)
    command.set_defaults(func=cmd_prepare_rx3_phase_a)

    command = subparsers.add_parser(
        "prepare-rx3-bending-phase-a",
        help="Rank bending templates and prepare a non-generating GUI observation bundle",
    )
    command.add_argument("--templates-dir", type=Path, required=True)
    command.add_argument("--rx3-db", type=Path, required=True)
    command.add_argument("--report-values", type=Path, required=True)
    command.add_argument(
        "--output-dir",
        type=Path,
        default=Path("validation/RX3-EXP-02_A_BENDING_OBSERVATION"),
    )
    command.add_argument("--experiment-id", default="RX3-EXP-02")
    command.set_defaults(func=cmd_prepare_rx3_bending_phase_a)

    command = subparsers.add_parser(
        "prepare-rx3-bending-mx10",
        help="Prepare the fingerprint-bound RX3-EXP-02B Mx=10 pre-calc bundle",
    )
    command.add_argument("--phase-a-dir", type=Path, required=True)
    command.add_argument("--observation", type=Path, required=True)
    command.add_argument(
        "--output-dir",
        type=Path,
        default=Path("validation/RX3-EXP-02B_MX10"),
    )
    command.add_argument("--experiment-id", default="RX3-EXP-02B")
    command.set_defaults(func=cmd_prepare_rx3_bending_mx10)

    command = subparsers.add_parser(
        "prepare-rx3-bending-q3",
        help="Prepare the exact RX3-EXP-03 field92 Q=3 validation bundle",
    )
    command.add_argument("--mx-validation-dir", type=Path, required=True)
    command.add_argument(
        "--output-dir",
        type=Path,
        default=Path("validation/RX3-EXP-03_Q3"),
    )
    command.add_argument("--experiment-id", default="RX3-EXP-03")
    command.set_defaults(func=cmd_prepare_rx3_bending_q3)

    command = subparsers.add_parser(
        "validate-rx3-bending-q3",
        help="Validate manual RX3-EXP-03 calculation and persisted Q behavior",
    )
    command.add_argument("--bundle-dir", type=Path, required=True)
    command.add_argument("--calculated", type=Path, required=True)
    command.add_argument("--observation", type=Path, required=True)
    command.add_argument("--json-report", type=Path)
    command.add_argument("--markdown-report", type=Path)
    command.set_defaults(func=cmd_validate_rx3_bending_q3)

    command = subparsers.add_parser(
        "prepare-rx3-my-biaxial-phase-a",
        help="Prepare non-mutating RX3-EXP-04 My/biaxial GUI observation bundle",
    )
    command.add_argument("--templates-dir", type=Path, required=True)
    command.add_argument(
        "--output-dir",
        type=Path,
        default=Path("validation/RX3-EXP-04_A_MY_BIAXIAL_OBSERVATION"),
    )
    command.add_argument("--experiment-id", default="RX3-EXP-04")
    command.add_argument("--mark", default="Кс1")
    command.add_argument("--mx-reference", default="0.51")
    command.add_argument("--my-reference", default="4.34")
    command.add_argument("--q-reference", default="0")
    command.add_argument("--tolerance", default="0.02")
    command.add_argument(
        "--evidence-reference",
        default=(
            "User-provided existing RX3 GUI/report reference for RX3-EXP-04 Phase A"
        ),
    )
    command.set_defaults(func=cmd_prepare_rx3_my_biaxial_phase_a)

    command = subparsers.add_parser(
        "prepare-rx3-my5",
        help="Prepare the exact RX3-EXP-04B field79 My=5 pre-calc bundle",
    )
    command.add_argument("--phase-a-dir", type=Path, required=True)
    command.add_argument("--observation", type=Path, required=True)
    command.add_argument(
        "--output-dir",
        type=Path,
        default=Path("validation/RX3-EXP-04B_MY5"),
    )
    command.add_argument("--experiment-id", default="RX3-EXP-04B")
    command.add_argument(
        "--mode",
        choices=[item.value for item in ExecutionMode],
        default=ExecutionMode.VALIDATION.value,
    )
    command.set_defaults(func=cmd_prepare_rx3_my5)

    command = subparsers.add_parser(
        "validate-rx3-my5",
        help="Validate manual RX3-EXP-04B calculation and persisted My behavior",
    )
    command.add_argument("--bundle-dir", type=Path, required=True)
    command.add_argument("--calculated", type=Path, required=True)
    command.add_argument("--observation", type=Path, required=True)
    command.add_argument("--json-report", type=Path)
    command.add_argument("--markdown-report", type=Path)
    command.set_defaults(func=cmd_validate_rx3_my5)

    command = subparsers.add_parser(
        "validate-rx3-result",
        help="Classify RX38 changes after a manual RX3 calculation",
    )
    command.add_argument("before", type=Path)
    command.add_argument("after", type=Path)
    command.add_argument("--json-report", type=Path)
    command.add_argument("--markdown-report", type=Path)
    command.add_argument("--overwrite", action="store_true")
    targets = command.add_mutually_exclusive_group(required=True)
    targets.add_argument(
        "--target-fingerprint",
        dest="target_fingerprints",
        action="append",
        help="SHA-256 fingerprint of an exact BEFORE Tconstr; repeat for multiple targets",
    )
    targets.add_argument(
        "--target-position",
        dest="target_positions",
        action="append",
        type=int,
        help="1-based BEFORE Tconstr position; repeat for multiple targets",
    )
    targets.add_argument(
        "--target-mark",
        dest="target_marks",
        action="append",
        help="Exact mark that uniquely resolves in BEFORE; repeat for multiple targets",
    )
    command.add_argument(
        "--gui-evidence",
        choices=[item.value for item in GuiExecutionEvidence],
        default=GuiExecutionEvidence.NOT_PROVIDED.value,
    )
    command.add_argument("--evidence-reference")
    command.set_defaults(func=cmd_validate_rx3_result)

    command = subparsers.add_parser(
        "rx38-experiment-diff",
        help="Create a machine-readable controlled differential RX38 report",
    )
    command.add_argument("base", type=Path)
    command.add_argument("changed", type=Path)
    command.add_argument("--experiment-id", required=True)
    command.add_argument("--json-report", type=Path)
    command.add_argument("--markdown-report", type=Path)
    command.add_argument("--overwrite", action="store_true")
    targets = command.add_mutually_exclusive_group(required=True)
    targets.add_argument(
        "--target-fingerprint",
        dest="target_fingerprints",
        action="append",
    )
    targets.add_argument(
        "--target-position",
        dest="target_positions",
        action="append",
        type=int,
    )
    targets.add_argument(
        "--target-mark",
        dest="target_marks",
        action="append",
    )
    command.set_defaults(func=cmd_rx38_experiment_diff)

    command = subparsers.add_parser(
        "pipeline",
        help="Run the experimental LIRA -> RX3 checkpoint -> Excel pipeline",
    )
    command.add_argument("config", type=Path)
    command.set_defaults(func=cmd_pipeline)

    command = subparsers.add_parser(
        "prepare-lira-review",
        help="Import a LIRA table into a review-only bundle without writing RX38 forces",
    )
    command.add_argument("--input", type=Path, required=True)
    command.add_argument("--mapping", type=Path, required=True)
    command.add_argument("--output-dir", type=Path, required=True)
    command.add_argument(
        "--project-element",
        type=Path,
        action="append",
        help="Optional existing ProjectElement JSON; repeat for multiple elements",
    )
    command.set_defaults(func=cmd_prepare_lira_review)

    command = subparsers.add_parser(
        "prepare-lira-selection",
        help="Enumerate governing-result candidates from a review bundle",
    )
    command.add_argument("--forces", type=Path, required=True)
    command.add_argument("--output-dir", type=Path, required=True)
    command.set_defaults(func=cmd_prepare_lira_selection)

    command = subparsers.add_parser(
        "validate-lira-selection",
        help="Resolve an explicit governing-result declaration against candidates",
    )
    command.add_argument("--candidates", type=Path, required=True)
    command.add_argument("--selection", type=Path, required=True)
    command.add_argument("--report", type=Path)
    command.set_defaults(func=cmd_validate_lira_selection)

    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
