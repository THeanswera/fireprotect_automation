from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPOSITORY_ROOT / "src"))

from fireprotect.rx3.diff import diff_records, group_by_profile, select_construction  # noqa: E402
from fireprotect.rx3.schema import field_spec  # noqa: E402


def _print_differences(differences) -> None:
    print("index\told_value\tnew_value\tfield_name\tcomment")
    for difference in differences:
        print(
            f"{difference.index}\t{difference.old_value}\t{difference.new_value}\t"
            f"{difference.field_name}\t{difference.comment}"
        )


def command_diff(args: argparse.Namespace) -> None:
    old = select_construction(args.old_file, args.left_mark)
    new = select_construction(args.new_file, args.right_mark)
    differences = diff_records(old, new)
    if args.json:
        print(json.dumps([difference.__dict__ for difference in differences], ensure_ascii=False, indent=2))
    else:
        _print_differences(differences)


def command_group(args: argparse.Namespace) -> None:
    variations = group_by_profile(args.files, args.include_singletons)
    for variation in variations:
        if args.profile and variation.profile.casefold().replace(" ", "") != args.profile.casefold().replace(" ", ""):
            continue
        print(f"PROFILE\t{variation.profile}\trecords={len(variation.records)}")
        for path, record in variation.records:
            print(f"RECORD\t{path}\tmark={record.mark}")
        print("index\tfield_name\tconfidence\tvalues")
        for index in variation.varying_fields:
            spec = field_spec(index)
            values = " | ".join(
                f"{path.name}:{record.mark}={record.fields[index]}" for path, record in variation.records
            )
            print(f"{index}\t{spec.name}\t{spec.confidence}\t{values}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Evidence-oriented RX38 differential analysis")
    subparsers = parser.add_subparsers(dest="command")

    compare = subparsers.add_parser("diff", help="Compare two selected Tconstr records")
    compare.add_argument("old_file", type=Path)
    compare.add_argument("new_file", type=Path)
    compare.add_argument("--left-mark")
    compare.add_argument("--right-mark")
    compare.add_argument("--json", action="store_true")
    compare.set_defaults(func=command_diff)

    group = subparsers.add_parser("group-by-profile", help="Group constructions and show varying positions")
    group.add_argument("files", nargs="+", type=Path)
    group.add_argument("--profile")
    group.add_argument("--include-singletons", action="store_true")
    group.set_defaults(func=command_group)

    # Backwards-friendly two-file invocation without an explicit `diff` verb.
    if len(sys.argv) > 1 and sys.argv[1] not in {"diff", "group-by-profile", "-h", "--help"}:
        sys.argv.insert(1, "diff")
    args = parser.parse_args()
    if not hasattr(args, "func"):
        parser.print_help()
        raise SystemExit(2)
    args.func(args)


if __name__ == "__main__":
    main()
