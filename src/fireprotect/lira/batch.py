"""Fail-closed batch import and review-bundle preparation for LIRA exports."""

from __future__ import annotations

import csv
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from hashlib import sha256
import json
from pathlib import Path
from types import MappingProxyType
from typing import Any, Mapping, Sequence

from ..model import ProjectElement, ProvenanceType, Quantity, Unit, ValueProvenance
from ..project_io import project_element_to_dict
from ..rx3.profiles import normalize_profile_name
from .convention import LiraRx3ConventionRegistry
from .errors import LiraFormatError, LiraMappingError, LiraRowError
from .sources import CsvTableSource, HtmlTableSource, XlsxTableSource
from .types import FORCE_FIELDS, LiraRowSource, RawTableRow
from .units import to_si, validate_force_unit


BATCH_FIELDS = (
    "element_id",
    "node_id",
    "member_id",
    "mark",
    "section",
    "load_case",
    "combination",
    *FORCE_FIELDS,
)
IDENTIFIER_FIELDS = ("element_id", "node_id", "member_id")


def _json_bytes(value: object) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _sha256_path(path: Path) -> str:
    digest = sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _optional_string(value: object, *, field: str) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip():
        raise LiraMappingError(f"{field} must be a non-empty string or null")
    return value.strip()


@dataclass(frozen=True, slots=True)
class LiraBatchMapping:
    source_format: str
    columns: Mapping[str, str | None]
    units: Mapping[str, str | None]
    options: Mapping[str, Any]
    decimal_separator: str
    thousands_separator: str | None
    project_id: str | None
    element_type: str | None
    convention: LiraRx3ConventionRegistry

    def __post_init__(self) -> None:
        source_format = self.source_format.casefold()
        if source_format not in {"csv", "html", "xlsx"}:
            raise LiraMappingError("format must be csv, html or xlsx")
        object.__setattr__(self, "source_format", source_format)
        if not isinstance(self.columns, Mapping) or set(self.columns) != set(BATCH_FIELDS):
            missing = set(BATCH_FIELDS) - set(self.columns)
            unknown = set(self.columns) - set(BATCH_FIELDS)
            raise LiraMappingError(
                "columns must explicitly contain every supported concept; "
                f"missing={sorted(missing)}, unknown={sorted(unknown)}"
            )
        columns = {
            name: _optional_string(value, field=f"columns.{name}")
            for name, value in self.columns.items()
        }
        mapped = [value for value in columns.values() if value is not None]
        if len(mapped) != len(set(mapped)):
            raise LiraMappingError("each mapped concept must use a distinct source column")
        if not any(columns[name] is not None for name in IDENTIFIER_FIELDS):
            raise LiraMappingError(
                "at least one of element_id/node_id/member_id must be mapped"
            )
        if not isinstance(self.units, Mapping) or set(self.units) != set(FORCE_FIELDS):
            raise LiraMappingError("units must explicitly contain N/Mx/My/Qx/Qy")
        units = {
            name: _optional_string(value, field=f"units.{name}")
            for name, value in self.units.items()
        }
        for name in FORCE_FIELDS:
            if columns[name] is None and units[name] is not None:
                raise LiraMappingError(
                    f"units.{name} must be null when columns.{name} is null"
                )
            if columns[name] is not None and units[name] is None:
                raise LiraMappingError(
                    f"ambiguous unit for mapped force component {name}"
                )
            unit = units[name]
            if unit is not None:
                validate_force_unit(name, unit)
        if self.decimal_separator not in {".", ","}:
            raise LiraMappingError("decimal_separator must be '.' or ','")
        if self.thousands_separator is not None:
            if not self.thousands_separator:
                raise LiraMappingError("thousands_separator cannot be empty")
            if self.thousands_separator == self.decimal_separator:
                raise LiraMappingError(
                    "thousands_separator must differ from decimal_separator"
                )
        if not isinstance(self.options, Mapping):
            raise LiraMappingError("options must be an object")
        allowed_options = {
            "csv": {"encoding", "delimiter", "header_row"},
            "html": {"encoding", "table_index", "header_row"},
            "xlsx": {"sheet_name", "header_row", "data_only"},
        }[source_format]
        unknown_options = set(self.options) - allowed_options
        if unknown_options:
            raise LiraMappingError(
                f"unknown {source_format} options: {sorted(unknown_options)}"
            )
        if not isinstance(self.convention, LiraRx3ConventionRegistry):
            raise LiraMappingError("convention must be LiraRx3ConventionRegistry")
        object.__setattr__(self, "columns", MappingProxyType(columns))
        object.__setattr__(self, "units", MappingProxyType(units))
        object.__setattr__(self, "options", MappingProxyType(dict(self.options)))

    @classmethod
    def from_dict(cls, payload: object) -> LiraBatchMapping:
        if not isinstance(payload, Mapping):
            raise LiraMappingError("mapping JSON root must be an object")
        allowed = {
            "format",
            "columns",
            "units",
            "options",
            "decimal_separator",
            "thousands_separator",
            "project",
            "convention",
        }
        unknown = set(payload) - allowed
        if unknown:
            raise LiraMappingError(f"unknown mapping fields: {sorted(unknown)}")
        project = payload.get("project", {})
        if not isinstance(project, Mapping):
            raise LiraMappingError("project must be an object")
        if set(project) - {"project_id", "element_type"}:
            raise LiraMappingError("project supports only project_id and element_type")
        columns = payload.get("columns")
        units = payload.get("units")
        options = payload.get("options", {})
        if not isinstance(columns, Mapping):
            raise LiraMappingError("columns must be an object")
        if not isinstance(units, Mapping):
            raise LiraMappingError("units must be an object")
        return cls(
            source_format=str(payload.get("format", "")),
            columns=dict(columns),
            units=dict(units),
            options=dict(options) if isinstance(options, Mapping) else options,
            decimal_separator=str(payload.get("decimal_separator", ".")),
            thousands_separator=_optional_string(
                payload.get("thousands_separator"), field="thousands_separator"
            ),
            project_id=_optional_string(project.get("project_id"), field="project.project_id"),
            element_type=_optional_string(
                project.get("element_type"), field="project.element_type"
            ),
            convention=LiraRx3ConventionRegistry.from_dict(payload.get("convention")),
        )

    def as_dict(self) -> dict[str, object]:
        return {
            "format": self.source_format,
            "columns": dict(self.columns),
            "units": dict(self.units),
            "options": dict(self.options),
            "decimal_separator": self.decimal_separator,
            "thousands_separator": self.thousands_separator,
            "project": {
                "project_id": self.project_id,
                "element_type": self.element_type,
            },
            "convention": self.convention.as_dict(),
        }

    @property
    def fingerprint(self) -> str:
        return sha256(_json_bytes(self.as_dict())).hexdigest()


@dataclass(frozen=True, slots=True)
class LiraImportIssue:
    code: str
    message: str
    severity: str = "BLOCKER"
    source_row: int | None = None
    field: str | None = None

    def as_dict(self) -> dict[str, object]:
        return {
            "code": self.code,
            "severity": self.severity,
            "message": self.message,
            "source_row": self.source_row,
            "field": self.field,
        }


@dataclass(frozen=True, slots=True)
class LiraForceValue:
    mapping_field: str
    source_column: str | None
    source_cell: str | None
    raw_token: str | None
    parsed_decimal: Decimal | None
    source_unit: str | None
    si_value: Decimal | None
    si_unit: str
    conversion: str | None

    def as_dict(self) -> dict[str, str | None]:
        return {
            "mapping_field": self.mapping_field,
            "source_column": self.source_column,
            "source_cell": self.source_cell,
            "raw_token": self.raw_token,
            "parsed_decimal": (
                None if self.parsed_decimal is None else str(self.parsed_decimal)
            ),
            "source_unit": self.source_unit,
            "si_value": None if self.si_value is None else str(self.si_value),
            "si_unit": self.si_unit,
            "conversion": self.conversion,
        }


@dataclass(frozen=True, slots=True)
class LiraBatchForceRecord:
    source_row: int
    source_sheet_or_table: str | None
    identifiers: Mapping[str, str | None]
    mark: str | None
    section: str | None
    load_case: str | None
    combination: str | None
    force_values: Mapping[str, LiraForceValue]
    text_provenance: Mapping[str, Mapping[str, str | None]]

    def __post_init__(self) -> None:
        object.__setattr__(self, "identifiers", MappingProxyType(dict(self.identifiers)))
        object.__setattr__(self, "force_values", MappingProxyType(dict(self.force_values)))
        object.__setattr__(
            self,
            "text_provenance",
            MappingProxyType(
                {name: MappingProxyType(dict(value)) for name, value in self.text_provenance.items()}
            ),
        )

    @property
    def primary_identifier(self) -> str | None:
        for name in IDENTIFIER_FIELDS:
            value = self.identifiers.get(name)
            if value is not None:
                return value
        return None

    @property
    def available_forces(self) -> dict[str, Decimal | None]:
        return {
            name: self.force_values[name].si_value
            for name in FORCE_FIELDS
        }

    def as_dict(
        self,
        convention: LiraRx3ConventionRegistry,
        source_file: str,
        source_sha256: str,
        row_blockers: Sequence[str] = (),
    ) -> dict[str, object]:
        return {
            "source": {
                "file": source_file,
                "sha256": source_sha256,
                "sheet_or_table": self.source_sheet_or_table,
                "row": self.source_row,
            },
            "identifiers": dict(self.identifiers),
            "mark": self.mark,
            "section": self.section,
            "load_case": self.load_case,
            "combination": self.combination,
            "text_provenance": {
                name: dict(value) for name, value in self.text_provenance.items()
            },
            "forces": {
                name: self.force_values[name].as_dict() for name in FORCE_FIELDS
            },
            "convention": convention.as_dict(),
            "row_blockers": list(row_blockers),
        }


@dataclass(frozen=True, slots=True)
class RejectedLiraRow:
    source_row: int
    source_sheet_or_table: str | None
    reason: str
    raw_values: Mapping[str, str | None]

    def as_dict(self) -> dict[str, object]:
        return {
            "source_row": self.source_row,
            "source_sheet_or_table": self.source_sheet_or_table,
            "reason": self.reason,
            "raw_values": dict(self.raw_values),
        }


@dataclass(frozen=True, slots=True)
class LiraBatchImportResult:
    source_file: str
    source_sha256: str
    source_format: str
    mapping_fingerprint: str
    rows_parsed: int
    records: tuple[LiraBatchForceRecord, ...]
    rejected_rows: tuple[RejectedLiraRow, ...]
    warnings: tuple[LiraImportIssue, ...]
    engineering_blockers: tuple[LiraImportIssue, ...]
    project_elements: tuple[ProjectElement, ...]
    audit_trail: tuple[Mapping[str, object], ...]
    convention: LiraRx3ConventionRegistry

    @property
    def rows_accepted(self) -> int:
        return len(self.records)

    @property
    def rows_rejected(self) -> int:
        return len(self.rejected_rows)

    @property
    def rx38_force_generation_allowed(self) -> bool:
        return False

    def row_blocker_codes(self, source_row: int) -> tuple[str, ...]:
        return tuple(
            issue.code
            for issue in self.engineering_blockers
            if issue.source_row == source_row
        )

    def summary_dict(self) -> dict[str, object]:
        return {
            "source_file_sha256": self.source_sha256,
            "source_format": self.source_format,
            "mapping_fingerprint": self.mapping_fingerprint,
            "rows_parsed": self.rows_parsed,
            "rows_accepted": self.rows_accepted,
            "rows_rejected": self.rows_rejected,
            "warnings": len(self.warnings),
            "engineering_blockers": len(self.engineering_blockers),
            "project_elements": len(self.project_elements),
            "rx38_force_generation_allowed": False,
            "issue_readiness": "NOT_READY_FOR_ISSUE",
        }


@dataclass(frozen=True, slots=True)
class LiraReviewBundle:
    directory: Path
    result: LiraBatchImportResult
    files: tuple[Path, ...]

    def as_dict(self) -> dict[str, object]:
        return {
            "status": "LIRA_REVIEW_BUNDLE_READY",
            "directory": str(self.directory),
            "files": [str(path) for path in self.files],
            **self.result.summary_dict(),
        }


def _source(mapping: LiraBatchMapping, path: Path) -> LiraRowSource:
    options = dict(mapping.options)
    if mapping.source_format == "csv":
        return CsvTableSource(path, **options)
    if mapping.source_format == "html":
        return HtmlTableSource(path, **options)
    return XlsxTableSource(path, **options)


def _raw_token(value: object) -> str | None:
    if value is None:
        return None
    if isinstance(value, bool):
        return "1" if value else "0"
    return str(value)


def _text_value(row: RawTableRow, header: str | None) -> str | None:
    if header is None:
        return None
    value = row.values.get(header)
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _decimal_value(
    row: RawTableRow,
    field: str,
    mapping: LiraBatchMapping,
) -> LiraForceValue:
    header = mapping.columns[field]
    unit = mapping.units[field]
    si_unit = "N*m" if field in {"Mx", "My"} else "N"
    if header is None:
        return LiraForceValue(field, None, None, None, None, None, None, si_unit, None)
    raw = row.values.get(header)
    token = _raw_token(raw)
    cell = row.cells.get(header, header)
    if token is None or not token.strip():
        return LiraForceValue(field, header, cell, token, None, unit, None, si_unit, None)
    if isinstance(raw, float):
        raise LiraRowError(
            f"row {row.row_number}, field {field}: binary float is not accepted"
        )
    normalized = token.strip()
    if mapping.thousands_separator is not None:
        normalized = normalized.replace(mapping.thousands_separator, "")
    if mapping.decimal_separator == ",":
        normalized = normalized.replace(",", ".")
    try:
        parsed = Decimal(normalized)
    except (InvalidOperation, ValueError) as exc:
        raise LiraRowError(
            f"row {row.row_number}, field {field}: {token!r} is not a number"
        ) from exc
    if not parsed.is_finite():
        raise LiraRowError(
            f"row {row.row_number}, field {field}: value must be finite"
        )
    if unit is None:
        raise LiraMappingError(f"ambiguous unit for mapped force component {field}")
    converted = to_si(field, parsed, unit)
    return LiraForceValue(
        field,
        header,
        cell,
        token,
        parsed,
        unit,
        converted,
        si_unit,
        f"{field}: {token} {unit} -> {converted} {si_unit}",
    )


def _record(row: RawTableRow, mapping: LiraBatchMapping) -> LiraBatchForceRecord:
    identifiers = {
        name: _text_value(row, mapping.columns[name]) for name in IDENTIFIER_FIELDS
    }
    if not any(identifiers.values()):
        raise LiraRowError(
            f"row {row.row_number}: all mapped element/node/member identifiers are empty"
        )
    text_names = ("mark", "section", "load_case", "combination")
    text_values = {
        name: _text_value(row, mapping.columns[name]) for name in text_names
    }
    text_provenance = {}
    for name in (*IDENTIFIER_FIELDS, *text_names):
        header = mapping.columns[name]
        text_provenance[name] = {
            "mapping_field": name,
            "source_column": header,
            "source_cell": (
                None if header is None else row.cells.get(header, header)
            ),
            "raw_token": (
                None if header is None else _raw_token(row.values.get(header))
            ),
        }
    return LiraBatchForceRecord(
        source_row=row.row_number,
        source_sheet_or_table=row.sheet,
        identifiers=identifiers,
        mark=text_values["mark"],
        section=text_values["section"],
        load_case=text_values["load_case"],
        combination=text_values["combination"],
        force_values={name: _decimal_value(row, name, mapping) for name in FORCE_FIELDS},
        text_provenance=text_provenance,
    )


def _trace(
    record: LiraBatchForceRecord,
    mapping: LiraBatchMapping,
    source_file: str,
    field: str,
) -> ValueProvenance:
    header = mapping.columns[field]
    cell = None if header is None else record.text_provenance[field]["source_cell"]
    return ValueProvenance(
        ProvenanceType.SOURCE,
        file=source_file,
        sheet=record.source_sheet_or_table,
        row=record.source_row,
        field=f"{header} ({cell})" if cell is not None else field,
    )


def _candidate_element(
    record: LiraBatchForceRecord,
    mapping: LiraBatchMapping,
    source_file: str,
    mapping_file: str,
    timestamp: datetime,
) -> ProjectElement | None:
    if (
        mapping.project_id is None
        or mapping.element_type is None
        or record.primary_identifier is None
        or record.mark is None
    ):
        return None
    values: dict[str, Any] = {name: None for name in ProjectElement.field_names()}
    values.update(
        {
            "project_id": mapping.project_id,
            "element_id": record.primary_identifier,
            "mark": record.mark,
            "element_type": mapping.element_type,
            "source_file": source_file,
            "source_type": f"LIRA_{mapping.source_format.upper()}",
            "source_element_id": record.primary_identifier,
            "source_row": record.source_row,
            "timestamp": timestamp,
            "profile_name": record.section,
            "load_case": record.load_case,
            "combination": record.combination,
        }
    )
    provenance: dict[str, ValueProvenance] = {
        "project_id": ValueProvenance(
            ProvenanceType.ENGINEER_INPUT,
            file=mapping_file,
            field="project.project_id",
        ),
        "element_type": ValueProvenance(
            ProvenanceType.ENGINEER_INPUT,
            file=mapping_file,
            field="project.element_type",
        ),
    }
    for name in ("element_id", "mark", "profile_name", "load_case", "combination"):
        if values[name] is not None:
            mapped_name = "section" if name == "profile_name" else name
            if name == "element_id":
                mapped_name = next(
                    key for key in IDENTIFIER_FIELDS if record.identifiers[key] is not None
                )
            provenance[name] = _trace(
                record, mapping, source_file, mapped_name
            )
    unit_by_force = {
        "N": Unit.NEWTON,
        "Mx": Unit.NEWTON_METER,
        "My": Unit.NEWTON_METER,
        "Qx": Unit.NEWTON,
        "Qy": Unit.NEWTON,
    }
    for name in FORCE_FIELDS:
        value = record.force_values[name]
        if value.si_value is not None:
            values[name] = Quantity.of(value.si_value, unit_by_force[name])
            provenance[name] = ValueProvenance(
                ProvenanceType.SOURCE,
                file=source_file,
                sheet=record.source_sheet_or_table,
                row=record.source_row,
                field=f"{value.source_column} ({value.source_cell})",
                formula=value.conversion,
            )
    values["provenance"] = provenance
    return ProjectElement(**values)


def _apply_to_existing(
    element: ProjectElement,
    record: LiraBatchForceRecord,
    source_file: str,
) -> ProjectElement:
    provenance = dict(element.provenance)
    for name, value in (("load_case", record.load_case), ("combination", record.combination)):
        if value is not None:
            source = record.text_provenance[name]
            provenance[name] = ValueProvenance(
                ProvenanceType.SOURCE,
                file=source_file,
                sheet=record.source_sheet_or_table,
                row=record.source_row,
                field=f"{source['source_column']} ({source['source_cell']})",
            )
    units = {
        "N": Unit.NEWTON,
        "Mx": Unit.NEWTON_METER,
        "My": Unit.NEWTON_METER,
        "Qx": Unit.NEWTON,
        "Qy": Unit.NEWTON,
    }
    for name in FORCE_FIELDS:
        force = record.force_values[name]
        if force.si_value is not None:
            provenance[name] = ValueProvenance(
                ProvenanceType.SOURCE,
                file=source_file,
                sheet=record.source_sheet_or_table,
                row=record.source_row,
                field=f"{force.source_column} ({force.source_cell})",
                formula=force.conversion,
            )
    profile_name = element.profile_name
    if profile_name is None and record.section is not None:
        profile_name = record.section
        source = record.text_provenance["section"]
        provenance["profile_name"] = ValueProvenance(
            ProvenanceType.SOURCE,
            file=source_file,
            sheet=record.source_sheet_or_table,
            row=record.source_row,
            field=f"{source['source_column']} ({source['source_cell']})",
        )
    def force_or_existing(name: str, existing: Quantity | None) -> Quantity | None:
        value = record.force_values[name].si_value
        return existing if value is None else Quantity.of(value, units[name])

    return replace(
        element,
        profile_name=profile_name,
        load_case=record.load_case or element.load_case,
        combination=record.combination or element.combination,
        N=force_or_existing("N", element.N),
        Mx=force_or_existing("Mx", element.Mx),
        My=force_or_existing("My", element.My),
        Qx=force_or_existing("Qx", element.Qx),
        Qy=force_or_existing("Qy", element.Qy),
        provenance=provenance,
    )


def import_lira_batch(
    source_path: str | Path,
    mapping: LiraBatchMapping,
    *,
    mapping_file: str = "mapping.json",
    existing_elements: Sequence[ProjectElement] = (),
    timestamp: datetime | None = None,
) -> LiraBatchImportResult:
    path = Path(source_path).resolve(strict=True)
    if not path.is_file():
        raise LiraFormatError(f"LIRA input is not a file: {path}")
    rows = _source(mapping, path).read_rows()
    issues: list[LiraImportIssue] = []
    warnings: list[LiraImportIssue] = []
    records: list[LiraBatchForceRecord] = []
    rejected: list[RejectedLiraRow] = []
    if not rows:
        issues.append(
            LiraImportIssue(
                "EMPTY_SOURCE_TABLE",
                "Source table contains no data rows; no engineering values were inferred",
            )
        )
    if rows:
        missing_headers = {
            header
            for header in mapping.columns.values()
            if header is not None and header not in rows[0].values
        }
        if missing_headers:
            raise LiraMappingError(
                f"source table is missing mapped headers: {sorted(missing_headers)}"
            )
    for row in rows:
        try:
            records.append(_record(row, mapping))
        except LiraRowError as exc:
            rejected.append(
                RejectedLiraRow(
                    row.row_number,
                    row.sheet,
                    str(exc),
                    {name: _raw_token(value) for name, value in row.values.items()},
                )
            )
            issues.append(
                LiraImportIssue(
                    "INVALID_SOURCE_ROW", str(exc), source_row=row.row_number
                )
            )

    by_identifier: dict[str, list[LiraBatchForceRecord]] = {}
    for record in records:
        identifier = record.primary_identifier
        if identifier is not None:
            by_identifier.setdefault(identifier, []).append(record)
        if record.combination is None:
            issues.append(
                LiraImportIssue(
                    "MISSING_REQUIRED_COMBINATION",
                    "A governing/load combination is unavailable; none was invented",
                    source_row=record.source_row,
                    field="combination",
                )
            )
        if record.section is None:
            issues.append(
                LiraImportIssue(
                    "PROFILE_UNRESOLVED",
                    "Profile/section is unavailable",
                    source_row=record.source_row,
                    field="section",
                )
            )
        missing_forces = [
            name for name, value in record.available_forces.items() if value is None
        ]
        if missing_forces:
            issues.append(
                LiraImportIssue(
                    "FORCE_COMPONENTS_MISSING",
                    f"Unavailable source force components: {', '.join(missing_forces)}",
                    source_row=record.source_row,
                )
            )
        unresolved = mapping.convention.unresolved_components(record.available_forces)
        if unresolved:
            issues.append(
                LiraImportIssue(
                    "LIRA_RX3_FORCE_CONVENTION",
                    "RX38 generation blocked; unresolved axis/sign mapping for "
                    + ", ".join(unresolved),
                    source_row=record.source_row,
                )
            )

    ambiguous_ids: set[str] = set()
    for identifier, matches in by_identifier.items():
        if len(matches) > 1:
            ambiguous_ids.add(identifier)
            profiles = {
                normalize_profile_name(record.section)
                for record in matches
                if record.section is not None
            }
            code = (
                "PROFILE_MISMATCH"
                if len(profiles) > 1
                else "DUPLICATE_AMBIGUOUS_ELEMENT_MAPPING"
            )
            for record in matches:
                issues.append(
                    LiraImportIssue(
                        code,
                        f"Identifier {identifier!r} resolves to {len(matches)} source rows",
                        source_row=record.source_row,
                    )
                )

    now = timestamp or datetime.now(timezone.utc)
    if now.tzinfo is None:
        raise ValueError("timestamp must be timezone-aware")
    candidates: list[ProjectElement] = []
    existing_by_identifier: dict[str, list[ProjectElement]] = {}
    for element in existing_elements:
        for identifier in {element.element_id, element.source_element_id} - {None}:
            existing_by_identifier.setdefault(str(identifier), []).append(element)
    for record in records:
        identifier = record.primary_identifier
        if identifier is None or identifier in ambiguous_ids:
            continue
        existing = existing_by_identifier.get(identifier, [])
        if len(existing) > 1:
            issues.append(
                LiraImportIssue(
                    "DUPLICATE_AMBIGUOUS_ELEMENT_MAPPING",
                    f"Identifier {identifier!r} matches multiple ProjectElements",
                    source_row=record.source_row,
                )
            )
            continue
        if existing:
            element = existing[0]
            if record.mark is not None and record.mark != element.mark:
                issues.append(
                    LiraImportIssue(
                        "MARK_MISMATCH",
                        f"LIRA mark {record.mark!r} differs from ProjectElement "
                        f"mark {element.mark!r}",
                        source_row=record.source_row,
                        field="mark",
                    )
                )
                continue
            if (
                record.section is not None
                and element.profile_name is not None
                and normalize_profile_name(record.section)
                != normalize_profile_name(element.profile_name)
            ):
                issues.append(
                    LiraImportIssue(
                        "PROFILE_MISMATCH",
                        f"LIRA section {record.section!r} differs from "
                        f"ProjectElement profile {element.profile_name!r}",
                        source_row=record.source_row,
                        field="section",
                    )
                )
                continue
            candidates.append(_apply_to_existing(element, record, str(path)))
            continue
        candidate = _candidate_element(
            record, mapping, str(path), mapping_file, now
        )
        if candidate is None:
            issues.append(
                LiraImportIssue(
                    "PROJECT_ELEMENT_INSUFFICIENT",
                    "ProjectElement requires explicit project_id, element_type, "
                    "identifier and mark",
                    source_row=record.source_row,
                )
            )
        else:
            candidates.append(candidate)

    audit = (
        {
            "event": "SOURCE_HASHED",
            "source_file": str(path),
            "sha256": _sha256_path(path),
        },
        {
            "event": "MAPPING_FINGERPRINTED",
            "mapping_file": mapping_file,
            "fingerprint": mapping.fingerprint,
        },
        {
            "event": "ROWS_IMPORTED_FOR_REVIEW_ONLY",
            "rows_parsed": len(rows),
            "rows_accepted": len(records),
            "rows_rejected": len(rejected),
            "rx38_written": False,
        },
    )
    return LiraBatchImportResult(
        source_file=str(path),
        source_sha256=_sha256_path(path),
        source_format=mapping.source_format,
        mapping_fingerprint=mapping.fingerprint,
        rows_parsed=len(rows),
        records=tuple(records),
        rejected_rows=tuple(rejected),
        warnings=tuple(warnings),
        engineering_blockers=tuple(issues),
        project_elements=tuple(candidates),
        audit_trail=audit,
        convention=mapping.convention,
    )


def _write_json(path: Path, payload: object) -> None:
    with path.open("x", encoding="utf-8", newline="\n") as stream:
        json.dump(payload, stream, ensure_ascii=False, indent=2)
        stream.write("\n")


def _write_review_csv(path: Path, result: LiraBatchImportResult) -> None:
    fields = [
        "element",
        "mark",
        "profile",
        "load_case",
        "combination",
        "N",
        "Mx",
        "My",
        "Qx",
        "Qy",
        "units",
        "row_source",
        "mapping_status",
        "axis_sign_status",
        "blockers",
    ]
    with path.open("x", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields)
        writer.writeheader()
        for record in result.records:
            values = {
                name: record.force_values[name].raw_token for name in FORCE_FIELDS
            }
            source_units = {
                name: record.force_values[name].source_unit for name in FORCE_FIELDS
            }
            writer.writerow(
                {
                    "element": record.primary_identifier,
                    "mark": record.mark,
                    "profile": record.section,
                    "load_case": record.load_case,
                    "combination": record.combination,
                    **values,
                    "units": json.dumps(source_units, ensure_ascii=False, sort_keys=True),
                    "row_source": (
                        f"{record.source_sheet_or_table or 'table'}:{record.source_row}"
                    ),
                    "mapping_status": "CONFIGURED",
                    "axis_sign_status": "; ".join(
                        f"{name}={result.convention.components[name].verification_status.value}"
                        for name in FORCE_FIELDS
                    ),
                    "blockers": "; ".join(result.row_blocker_codes(record.source_row)),
                }
            )


def prepare_lira_review_bundle(
    source_path: str | Path,
    mapping_path: str | Path,
    output_dir: str | Path,
    *,
    existing_elements: Sequence[ProjectElement] = (),
) -> LiraReviewBundle:
    source = Path(source_path).resolve(strict=True)
    mapping_file = Path(mapping_path).resolve(strict=True)
    destination = Path(output_dir).resolve(strict=False)
    if destination in {source, mapping_file}:
        raise LiraFormatError("output directory must differ from source and mapping files")
    if destination.exists():
        raise LiraFormatError(
            f"review bundle directory already exists; refusing to overwrite: {destination}"
        )
    try:
        raw_mapping = json.loads(mapping_file.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise LiraMappingError(f"cannot read mapping JSON {mapping_file}: {exc}") from exc
    mapping = LiraBatchMapping.from_dict(raw_mapping)
    result = import_lira_batch(
        source,
        mapping,
        mapping_file=str(mapping_file),
        existing_elements=existing_elements,
    )
    destination.mkdir(parents=True, exist_ok=False)
    paths = {
        name: destination / name
        for name in (
            "source_manifest.json",
            "mapping_snapshot.json",
            "import_summary.json",
            "forces.json",
            "forces_review.csv",
            "project_elements.json",
            "blockers.json",
            "audit.json",
            "README_REVIEW.md",
        )
    }
    _write_json(
        paths["source_manifest.json"],
        {
            "source_file": result.source_file,
            "source_file_sha256": result.source_sha256,
            "source_format": result.source_format,
            "source_overwritten": False,
        },
    )
    _write_json(paths["mapping_snapshot.json"], mapping.as_dict())
    _write_json(paths["import_summary.json"], result.summary_dict())
    _write_json(
        paths["forces.json"],
        {
            "accepted_records": [
                record.as_dict(
                    result.convention,
                    result.source_file,
                    result.source_sha256,
                    result.row_blocker_codes(record.source_row),
                )
                for record in result.records
            ],
            "rejected_rows": [row.as_dict() for row in result.rejected_rows],
        },
    )
    _write_review_csv(paths["forces_review.csv"], result)
    _write_json(
        paths["project_elements.json"],
        [project_element_to_dict(element) for element in result.project_elements],
    )
    _write_json(
        paths["blockers.json"],
        [issue.as_dict() for issue in result.engineering_blockers],
    )
    _write_json(paths["audit.json"], [dict(event) for event in result.audit_trail])
    with paths["README_REVIEW.md"].open("x", encoding="utf-8", newline="\n") as stream:
        stream.write(
            "# LIRA import review bundle\n\n"
            "This bundle is review-only. It did not create or mutate RX38 files.\n\n"
            f"- Source SHA-256: `{result.source_sha256}`\n"
            f"- Mapping fingerprint: `{result.mapping_fingerprint}`\n"
            f"- Rows: {result.rows_parsed} parsed / {result.rows_accepted} accepted / "
            f"{result.rows_rejected} rejected\n"
            f"- Engineering blockers: {len(result.engineering_blockers)}\n"
            "- RX38 force generation: **BLOCKED**\n"
            "- Issue readiness: **NOT_READY_FOR_ISSUE**\n\n"
            "Review `forces_review.csv` for readability and treat `forces.json` as "
            "the Decimal-safe canonical record. Resolve every item in `blockers.json`, "
            "especially `LIRA_RX3_FORCE_CONVENTION`, before any future generation step.\n"
        )
    return LiraReviewBundle(destination, result, tuple(paths.values()))
