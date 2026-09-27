"""Whole-document structural and raw diff of two RX38 documents.

The post-calculation RX3 check must not look only at the 200 fields of the
target ``Tconstr``: a file can gain a record, lose a record, change a record
that is not a construction (for example a ``Trazdel`` line), swap two records,
switch its line endings, gain a BOM or re-quote a field without any of those
facts appearing in a field-by-field comparison of one construction.

This module compares the decoded documents as a whole:

* every record of both files, with its type, its 1-based position and its exact
  raw line (tokens are stored verbatim by the reader, so the reconstruction is
  the original line and is verified against the raw line the reader saw);
* the record sequence: the sequence of record *types* is aligned with
  :class:`difflib.SequenceMatcher` (``autojunk`` disabled, so a file with many
  identical records cannot silently degrade the alignment) and the records of
  each aligned block are then paired by position and compared as raw lines.
  Aligning on types first keeps an added or deleted record from silently
  consuming the pairing of the records around it;
* record identity and order: every ``Tconstr`` gets an identity key computed
  from its raw tokens with a caller-supplied set of fields masked out (the
  fields a calculation is allowed to rewrite).  Records that share a key are
  indistinguishable outside those fields, so no code can prove which of them
  produced which result; a pairwise byte exchange of two records is reported
  explicitly.  Both facts let the validator stop instead of reading a
  permutation as a set of legitimate result changes;
* the raw layout: encoding, BOM, line count, blank lines, trailing newline, the
  line ending of *every* physical line (including empty lines, which carry no
  record) and raw-only token changes (token text changed while the decoded
  field value stayed the same: quoting, whitespace).

Nothing in this module decides acceptance: it reports facts, and
:mod:`fireprotect.rx3.gui_validation` decides which of them block the GUI
check.  No tolerance, no numeric equivalence rule and no field-semantics
assumption is applied here.
"""

from __future__ import annotations

from dataclasses import dataclass
from difflib import SequenceMatcher
from hashlib import sha256
from typing import Any, Iterable, Sequence

from .parser import Rx38Document, Rx38Record

RX38_STRUCTURAL_DIFF_KIND = "RX38_STRUCTURAL_RAW_DIFF"


class Rx38StructuralDiffError(ValueError):
    """The structural facts cannot be trusted, so the diff must not be used."""


@dataclass(frozen=True, slots=True)
class _RecordRef:
    position: int
    record: Rx38Record
    raw_line: str


def _line_content(line: str) -> tuple[str, str]:
    if line.endswith("\r\n"):
        return line[:-2], "\r\n"
    if line.endswith(("\r", "\n")):
        return line[:-1], line[-1]
    return line, ""


def record_raw_line(record: Rx38Record, raw_lines: Sequence[str]) -> str:
    """Return the exact raw line that produced one parsed record.

    The reader stores every token verbatim, so ``";".join(raw_tokens)`` plus the
    recorded line ending must reproduce the line.  A mismatch means this module
    would report facts about a different text than the parser read, so it
    raises instead of guessing.
    """

    reconstructed = ";".join(record.raw_tokens) + record.newline
    line_number = record.line_number
    if line_number is None or not 0 < line_number <= len(raw_lines):
        return reconstructed
    line = raw_lines[line_number - 1]
    if line != reconstructed:
        raise Rx38StructuralDiffError(
            "record at line "
            f"{line_number} cannot be reconstructed from its raw tokens: "
            f"{reconstructed!r} != {line!r}"
        )
    return line


def _record_refs(document: Rx38Document) -> tuple[_RecordRef, ...]:
    refs: list[_RecordRef] = []
    for position, record in enumerate(document.records, 1):
        refs.append(
            _RecordRef(position, record, record_raw_line(record, document.raw_lines))
        )
    return tuple(refs)


def blank_line_numbers(document: Rx38Document) -> tuple[int, ...]:
    """1-based numbers of the raw lines that carry no record at all."""

    numbers: list[int] = []
    for number, line in enumerate(document.raw_lines, 1):
        if not _line_content(line)[0]:
            numbers.append(number)
    return tuple(numbers)


def line_ending_kind(line: str) -> str:
    """Name the terminator of one physical line, ``NONE`` for the last line."""

    if line.endswith("\r\n"):
        return "CRLF"
    if line.endswith("\n"):
        return "LF"
    if line.endswith("\r"):
        return "CR"
    return "NONE"


def line_ending_kinds(document: Rx38Document) -> tuple[str, ...]:
    """The terminator of every physical line, in file order."""

    return tuple(line_ending_kind(line) for line in document.raw_lines)


def _line_ending_changes(
    before: Rx38Document, after: Rx38Document
) -> list[dict[str, Any]]:
    """Every physical line whose terminator changed, empty lines included.

    Comparing record terminators only is not enough: an empty line carries no
    record, keeps its number and its surrounding records, and a CRLF -> LF
    rewrite of that line would otherwise stay invisible.
    """

    before_lines = list(before.raw_lines)
    after_lines = list(after.raw_lines)
    changes: list[dict[str, Any]] = []
    for index in range(min(len(before_lines), len(after_lines))):
        left, right = before_lines[index], after_lines[index]
        before_kind, after_kind = line_ending_kind(left), line_ending_kind(right)
        if before_kind == after_kind:
            continue
        before_content, before_terminator = _line_content(left)
        after_content, after_terminator = _line_content(right)
        changes.append(
            {
                "line": index + 1,
                "before": before_kind,
                "after": after_kind,
                "raw_before": before_terminator,
                "raw_after": after_terminator,
                "blank_line": not before_content and not after_content,
            }
        )
    return changes


def tconstr_identity_key(
    record: Rx38Record, masked_fields: frozenset[int]
) -> str:
    """Identity of one record with the allowed-to-change fields masked out.

    The masked tokens are replaced by an empty token, so the position and the
    number of fields stay part of the identity: two records only share a key
    when they are token-identical everywhere outside ``masked_fields``.
    """

    tokens = tuple(
        "" if index in masked_fields else token
        for index, token in enumerate(record.raw_tokens)
    )
    digest = sha256()
    digest.update(record.record_type.encode("utf-8"))
    digest.update(b"\x00")
    digest.update("\x1f".join(tokens).encode("utf-8"))
    return digest.hexdigest()


def _identity_facts(
    before_refs: Sequence[_RecordRef],
    after_refs: Sequence[_RecordRef],
    *,
    masked_fields: frozenset[int],
    comparable_by_position: bool,
) -> dict[str, Any]:
    """Identity keys, duplicates and byte exchanges of the ``Tconstr`` records."""

    def entries(refs: Sequence[_RecordRef]) -> list[dict[str, Any]]:
        result: list[dict[str, Any]] = []
        tconstr_position = 0
        for ref in refs:
            if ref.record.record_type != "Tconstr":
                continue
            tconstr_position += 1
            result.append(
                {
                    "tconstr_position": tconstr_position,
                    "record_position": ref.position,
                    "line_number": ref.record.line_number,
                    "key": tconstr_identity_key(ref.record, masked_fields),
                }
            )
        return result

    before_entries = entries(before_refs)
    after_entries = entries(after_refs)
    grouped: dict[str, list[int]] = {}
    for item in before_entries:
        grouped.setdefault(str(item["key"]), []).append(int(item["tconstr_position"]))
    after_grouped: dict[str, list[int]] = {}
    for item in after_entries:
        after_grouped.setdefault(str(item["key"]), []).append(
            int(item["tconstr_position"])
        )
    duplicate_groups = [
        {
            "key": key,
            "before_tconstr_positions": positions,
            "after_tconstr_positions": after_grouped.get(key, []),
            "note": (
                "these records are indistinguishable outside the masked fields, "
                "so which of them produced which result cannot be proven"
            ),
        }
        for key, positions in sorted(grouped.items())
        if len(positions) > 1
    ]

    exchanges: list[dict[str, Any]] = []
    if comparable_by_position:
        # Hash lookup instead of a quadratic scan: a record that received the
        # tokens of another record is found through the map of the records that
        # already changed.  Only ``Tconstr`` records are considered, because they
        # are the records a calculation result is bound to.
        changed_tokens: dict[tuple[str, ...], list[int]] = {}
        for index, (left, right) in enumerate(zip(before_refs, after_refs)):
            if left.record.record_type != "Tconstr":
                continue
            if right.record.record_type != "Tconstr":
                continue
            if left.raw_line == right.raw_line:
                continue
            changed_tokens.setdefault(left.record.raw_tokens, []).append(index)
        seen: set[tuple[int, int]] = set()
        for index, (left, right) in enumerate(zip(before_refs, after_refs)):
            if left.record.record_type != "Tconstr":
                continue
            if right.record.record_type != "Tconstr":
                continue
            if left.raw_line == right.raw_line:
                continue
            for partner in changed_tokens.get(right.record.raw_tokens, []):
                if partner == index or after_refs[partner].raw_line != left.raw_line:
                    continue
                pair = (min(index, partner), max(index, partner))
                if pair in seen:
                    continue
                seen.add(pair)
                exchanges.append(
                    {
                        "record_positions": [pair[0] + 1, pair[1] + 1],
                        "record_type": left.record.record_type,
                        "before_raw_lines": [
                            before_refs[pair[0]].raw_line,
                            before_refs[pair[1]].raw_line,
                        ],
                        "after_raw_lines": [
                            after_refs[pair[0]].raw_line,
                            after_refs[pair[1]].raw_line,
                        ],
                        "note": "the two records were exchanged byte for byte",
                    }
                )
        exchanges.sort(key=lambda item: item["record_positions"])
    return {
        "masked_fields": sorted(masked_fields),
        "before": before_entries,
        "after": after_entries,
        "duplicate_groups": duplicate_groups,
        "exchanges": exchanges,
        "note": (
            "identity keys mask the fields a calculation is allowed to rewrite; "
            "a duplicate key means the order and identity of those records cannot "
            "be proven from the file, and an exchange means two records changed "
            "places instead of being recalculated in place"
        ),
    }


def _document_facts(document: Rx38Document) -> dict[str, Any]:
    lines = list(document.raw_lines)
    return {
        "path": None if document.source is None else str(document.source),
        "records": len(document.records),
        "lines": len(lines) if lines else None,
        "encoding": document.encoding,
        "has_bom": document.has_bom,
        "raw_lines_recorded": bool(lines),
        "trailing_newline": (
            None if not lines else bool(lines[-1].endswith(("\r", "\n")))
        ),
        "blank_lines": list(blank_line_numbers(document)),
    }


def _token_change(
    before: Rx38Record, after: Rx38Record, index: int
) -> dict[str, Any]:
    return {
        "index": index,
        "before_token": before.raw_tokens[index],
        "after_token": after.raw_tokens[index],
        "before_decoded": before.fields[index],
        "after_decoded": after.fields[index],
        "decoded_equal": before.fields[index] == after.fields[index],
    }


def _replacement(
    before: _RecordRef, after: _RecordRef
) -> dict[str, Any]:
    old, new = before.record, after.record
    shared = min(len(old.raw_tokens), len(new.raw_tokens))
    token_changes = [
        _token_change(old, new, index)
        for index in range(shared)
        if old.raw_tokens[index] != new.raw_tokens[index]
    ]
    raw_only = [item for item in token_changes if item["decoded_equal"]]
    semantic = [item for item in token_changes if not item["decoded_equal"]]
    return {
        "before_position": before.position,
        "after_position": after.position,
        "record_type_before": old.record_type,
        "record_type_after": new.record_type,
        "record_type_changed": old.record_type != new.record_type,
        "is_tconstr": old.record_type == "Tconstr" and new.record_type == "Tconstr",
        "mark_before": old.mark,
        "mark_after": new.mark,
        "token_count_before": len(old.raw_tokens),
        "token_count_after": len(new.raw_tokens),
        "token_count_changed": len(old.raw_tokens) != len(new.raw_tokens),
        "raw_line_changed": before.raw_line != after.raw_line,
        "line_number_before": old.line_number,
        "line_number_after": new.line_number,
        "newline_before": old.newline,
        "newline_after": new.newline,
        "newline_changed": old.newline != new.newline,
        "changed_indices": [item["index"] for item in token_changes],
        "semantic_change_indices": [item["index"] for item in semantic],
        "raw_only_change_indices": [item["index"] for item in raw_only],
        "raw_token_changes": token_changes,
        "raw_line_before": before.raw_line,
        "raw_line_after": after.raw_line,
    }


def _added(after: _RecordRef) -> dict[str, Any]:
    return {
        "position": after.position,
        "record_type": after.record.record_type,
        "line_number": after.record.line_number,
        "raw_line": after.raw_line,
        "newline": after.record.newline,
    }


def _removed(before: _RecordRef) -> dict[str, Any]:
    return {
        "position": before.position,
        "record_type": before.record.record_type,
        "line_number": before.record.line_number,
        "raw_line": before.raw_line,
        "newline": before.record.newline,
    }


def diff_rx38_documents(
    before: Rx38Document,
    after: Rx38Document,
    *,
    identity_masked_fields: Iterable[int] = (),
) -> dict[str, Any]:
    """Compare two RX38 documents in full and return a machine-readable report.

    Records are aligned in two stages: first the sequence of record *types* is
    aligned, then the records of each aligned block are paired by position and
    compared as raw lines.  Aligning on types first keeps a deleted or added
    record from silently consuming the pairing of the records around it, which
    is what a plain content alignment does when one file has one record more.

    ``identity_masked_fields`` are the field indices a calculation is allowed to
    rewrite.  They are masked out of the ``Tconstr`` identity keys, so a caller
    can detect that two records are indistinguishable outside the fields they
    are allowed to change.  With the default empty mask the identity is the full
    record content.
    """

    masked_fields = frozenset(identity_masked_fields)
    if any(
        isinstance(index, bool) or not isinstance(index, int) or index < 0
        for index in masked_fields
    ):
        raise Rx38StructuralDiffError(
            "identity_masked_fields must be non-negative field indices"
        )

    before_refs = _record_refs(before)
    after_refs = _record_refs(after)
    before_facts = _document_facts(before)
    after_facts = _document_facts(after)

    added: list[dict[str, Any]] = []
    removed: list[dict[str, Any]] = []
    replaced: list[dict[str, Any]] = []
    matcher = SequenceMatcher(
        None,
        [ref.record.record_type for ref in before_refs],
        [ref.record.record_type for ref in after_refs],
        autojunk=False,
    )
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag in {"equal", "replace"}:
            paired = min(i2 - i1, j2 - j1)
            for offset in range(paired):
                left = before_refs[i1 + offset]
                right = after_refs[j1 + offset]
                if left.raw_line == right.raw_line:
                    continue
                replaced.append(_replacement(left, right))
            removed.extend(
                _removed(before_refs[index]) for index in range(i1 + paired, i2)
            )
            added.extend(
                _added(after_refs[index]) for index in range(j1 + paired, j2)
            )
            continue
        if tag == "delete":
            removed.extend(_removed(before_refs[index]) for index in range(i1, i2))
            continue
        added.extend(_added(after_refs[index]) for index in range(j1, j2))

    before_types = [ref.record.record_type for ref in before_refs]
    after_types = [ref.record.record_type for ref in after_refs]
    count_changed = len(before_refs) != len(after_refs)
    types_changed = before_types != after_types
    non_tconstr_replacements = [
        item for item in replaced if not item["is_tconstr"]
    ]
    newline_changes = [
        {
            "before_position": item["before_position"],
            "after_position": item["after_position"],
            "record_type_before": item["record_type_before"],
            "record_type_after": item["record_type_after"],
            "before": item["newline_before"],
            "after": item["newline_after"],
        }
        for item in replaced
        if item["newline_changed"]
    ]
    raw_only_token_changes = [
        {
            "before_position": item["before_position"],
            "after_position": item["after_position"],
            "record_type_before": item["record_type_before"],
            "record_type_after": item["record_type_after"],
            "indices": item["raw_only_change_indices"],
            "changes": [
                change
                for change in item["raw_token_changes"]
                if change["decoded_equal"]
            ],
        }
        for item in replaced
        if item["raw_only_change_indices"]
    ]
    blank_lines_changed = before_facts["blank_lines"] != after_facts["blank_lines"]
    line_count_changed = (
        before_facts["lines"] is not None
        and after_facts["lines"] is not None
        and before_facts["lines"] != after_facts["lines"]
    )
    trailing_newline_changed = (
        before_facts["trailing_newline"] is not None
        and after_facts["trailing_newline"] is not None
        and before_facts["trailing_newline"] != after_facts["trailing_newline"]
    )
    bom_changed = before_facts["has_bom"] != after_facts["has_bom"]
    encoding_changed = before_facts["encoding"] != after_facts["encoding"]
    line_ending_changes = _line_ending_changes(before, after)
    line_endings_changed = bool(line_ending_changes)
    identity = _identity_facts(
        before_refs,
        after_refs,
        masked_fields=masked_fields,
        comparable_by_position=not (count_changed or types_changed),
    )

    structure_changed = bool(
        count_changed
        or types_changed
        or added
        or removed
        or non_tconstr_replacements
        or bom_changed
        or encoding_changed
        or blank_lines_changed
        or line_count_changed
        or trailing_newline_changed
        or newline_changes
        or line_endings_changed
    )
    raw_layout_changed = bool(
        bom_changed
        or encoding_changed
        or blank_lines_changed
        or line_count_changed
        or trailing_newline_changed
        or newline_changes
        or line_endings_changed
        or raw_only_token_changes
    )
    unchanged_records = len(before_refs) - len(removed) - len(replaced)
    return {
        "kind": RX38_STRUCTURAL_DIFF_KIND,
        "before": before_facts,
        "after": after_facts,
        "record_sequence": {
            "count": {
                "before": len(before_refs),
                "after": len(after_refs),
                "changed": count_changed,
            },
            "types": {
                "before": before_types,
                "after": after_types,
                "changed": types_changed,
            },
            "unchanged_records": unchanged_records,
            "added": added,
            "removed": removed,
            "replaced": replaced,
            "non_tconstr_replacements": non_tconstr_replacements,
            "alignment": (
                "STRUCTURE_CHANGED"
                if structure_changed
                else "RECORD_SEQUENCE_IDENTICAL"
            ),
        },
        "raw_layout": {
            "encoding": {
                "before": before_facts["encoding"],
                "after": after_facts["encoding"],
                "changed": encoding_changed,
            },
            "bom": {
                "before": before_facts["has_bom"],
                "after": after_facts["has_bom"],
                "changed": bom_changed,
            },
            "line_count": {
                "before": before_facts["lines"],
                "after": after_facts["lines"],
                "changed": line_count_changed,
            },
            "blank_lines": {
                "before": before_facts["blank_lines"],
                "after": after_facts["blank_lines"],
                "changed": blank_lines_changed,
            },
            "trailing_newline": {
                "before": before_facts["trailing_newline"],
                "after": after_facts["trailing_newline"],
                "changed": trailing_newline_changed,
            },
            "line_endings": {
                "before_kinds": sorted(set(line_ending_kinds(before))),
                "after_kinds": sorted(set(line_ending_kinds(after))),
                "compared_lines": min(
                    len(before.raw_lines), len(after.raw_lines)
                ),
                "changed_lines": line_ending_changes,
                "changed": line_endings_changed,
                "note": (
                    "the terminator of every physical line is compared, including "
                    "empty lines that carry no record"
                ),
            },
            "newline_changes": newline_changes,
            "raw_only_token_changes": raw_only_token_changes,
        },
        "tconstr_identity": identity,
        "structure_changed": structure_changed,
        "raw_layout_changed": raw_layout_changed,
        "identical": (
            not structure_changed
            and not replaced
            and not raw_layout_changed
        ),
    }


__all__ = [
    "RX38_STRUCTURAL_DIFF_KIND",
    "Rx38StructuralDiffError",
    "blank_line_numbers",
    "diff_rx38_documents",
    "line_ending_kind",
    "line_ending_kinds",
    "record_raw_line",
    "tconstr_identity_key",
]
