from __future__ import annotations

import argparse
import json
from pathlib import Path

from .project_io import read_project_element_json
from .rx3.project_adapter import create_rx38_from_project_element
from .rx3.parser import Rx38Construction, construction_records, read_rx38
from .rx3.profiles import ProfileRepository, list_tables
from .validation import validate_rx38_record


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
    element = read_project_element_json(args.input)
    report = create_rx38_from_project_element(
        element,
        args.template,
        args.output,
        template_mark=args.template_mark,
    )
    payload = json.dumps(report.as_dict(), ensure_ascii=False, indent=2)
    if args.report is not None:
        args.report.write_text(payload + "\n", encoding="utf-8")
    print(payload)


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
    command.set_defaults(func=cmd_rx38_create)

    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
