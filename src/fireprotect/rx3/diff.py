from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from .parser import Rx38Record, construction_records, read_rx38
from .profiles import normalize_profile_name
from .schema import field_spec


@dataclass(frozen=True)
class FieldDifference:
    index: int
    old_value: str
    new_value: str
    field_name: str
    comment: str


def diff_records(old: Rx38Record, new: Rx38Record) -> list[FieldDifference]:
    if old.record_type != "Tconstr" or new.record_type != "Tconstr":
        raise ValueError("Both records must be Tconstr")
    maximum = max(len(old.fields), len(new.fields))
    differences: list[FieldDifference] = []
    for index in range(maximum):
        old_value = old.fields[index] if index < len(old.fields) else "<missing>"
        new_value = new.fields[index] if index < len(new.fields) else "<missing>"
        if old_value == new_value:
            continue
        spec = field_spec(index)
        comment = f"{spec.confidence}: {spec.purpose}"
        if spec.comment:
            comment += f"; {spec.comment}"
        differences.append(FieldDifference(index, old_value, new_value, spec.name, comment))
    return differences


def select_construction(path: str | Path, selector: str | None) -> Rx38Record:
    records = construction_records(read_rx38(path))
    if selector is None:
        if len(records) != 1:
            raise ValueError(f"{path}: contains {len(records)} constructions; specify --left-mark/--right-mark")
        return records[0]
    matches = [record for record in records if record.mark == selector]
    if len(matches) != 1:
        raise ValueError(f"{path}: selector {selector!r} matched {len(matches)} constructions")
    return matches[0]


@dataclass(frozen=True)
class ProfileVariation:
    profile: str
    records: tuple[tuple[Path, Rx38Record], ...]
    varying_fields: tuple[int, ...]


def group_by_profile(paths: Iterable[str | Path], include_singletons: bool = False) -> list[ProfileVariation]:
    groups: dict[str, list[tuple[Path, Rx38Record]]] = defaultdict(list)
    display_names: dict[str, str] = {}
    expanded_paths: list[Path] = []
    for value in paths:
        path = Path(value)
        if path.is_dir():
            expanded_paths.extend(sorted(path.rglob("*.rx38")))
        elif any(char in path.name for char in "*?["):
            expanded_paths.extend(sorted(path.parent.glob(path.name)))
        else:
            expanded_paths.append(path)
    for path in expanded_paths:
        for record in construction_records(read_rx38(path)):
            key = normalize_profile_name(record.profile or "")
            if not key:
                continue
            display_names.setdefault(key, record.profile or key)
            groups[key].append((path, record))
    result: list[ProfileVariation] = []
    for key, records in sorted(groups.items()):
        if len(records) < 2 and not include_singletons:
            continue
        field_count = max(len(record.fields) for _, record in records)
        varying = tuple(
            index for index in range(field_count)
            if len({record.fields[index] if index < len(record.fields) else "<missing>" for _, record in records}) > 1
        )
        result.append(ProfileVariation(display_names[key], tuple(records), varying))
    return result
