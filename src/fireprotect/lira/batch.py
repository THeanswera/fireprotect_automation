"""Lossless, fail-closed batch review for native LIRA force exports."""

from __future__ import annotations

import csv
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from hashlib import sha256
import json
from pathlib import Path
from types import MappingProxyType
from typing import Any, Mapping, Sequence

from ..model import ProjectElement
from .convention import LiraRx3ConventionRegistry
from .errors import LiraFormatError, LiraMappingError, LiraRowError
from .sources import CsvTableSource, HtmlTableSource, XlsxTableSource
from .types import (
    LIRA_NATIVE_FORCE_COMPONENTS,
    LIRA_NATIVE_RESULT_COMPONENTS,
    LiraRowSource,
    RawTableRow,
)
from .units import to_review_unit, validate_native_force_unit


LIRA_NATIVE_SOURCE_MODEL = "LIRA_NATIVE_BAR_FORCES"
IDENTIFIER_FIELDS = ("element_id", "node_id", "member_id")
SOURCE_METADATA_FIELDS = (
    *IDENTIFIER_FIELDS,
    "section_station",
    "mark",
    "profile",
    "load_case",
    "element_type",
    "composition",
    "combination",
)
BATCH_FIELDS = SOURCE_METADATA_FIELDS


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
    return value


def _explicit_mapping(
    value: object,
    expected: Sequence[str],
    *,
    field: str,
) -> dict[str, str | None]:
    if not isinstance(value, Mapping) or set(value) != set(expected):
        supplied = set(value) if isinstance(value, Mapping) else set()
        raise LiraMappingError(
            f"{field} must explicitly contain {list(expected)}; "
            f"missing={sorted(set(expected) - supplied)}, "
            f"unknown={sorted(supplied - set(expected))}"
        )
    return {
        name: _optional_string(value[name], field=f"{field}.{name}")
        for name in expected
    }


@dataclass(frozen=True, slots=True)
class LiraBatchMapping:
    source_model: str
    source_format: str
    columns: Mapping[str, str | None]
    native_forces: Mapping[str, str | None]
    native_force_units: Mapping[str, str | None]
    native_results: Mapping[str, str | None]
    native_result_units: Mapping[str, str | None]
    options: Mapping[str, Any]
    decimal_separator: str
    thousands_separator: str | None
    convention: LiraRx3ConventionRegistry

    def __post_init__(self) -> None:
        if self.source_model != LIRA_NATIVE_SOURCE_MODEL:
            raise LiraMappingError(
                f"source_model must be {LIRA_NATIVE_SOURCE_MODEL!r}; canonical "
                "N/Mx/My/Qx/Qy mappings cannot represent a native LIRA row"
            )
        source_format = self.source_format.casefold()
        if source_format not in {"csv", "html", "xlsx"}:
            raise LiraMappingError("format must be csv, html or xlsx")
        object.__setattr__(self, "source_format", source_format)
        columns = _explicit_mapping(
            self.columns, SOURCE_METADATA_FIELDS, field="columns"
        )
        native_forces = _explicit_mapping(
            self.native_forces,
            LIRA_NATIVE_FORCE_COMPONENTS,
            field="native_forces",
        )
        native_force_units = _explicit_mapping(
            self.native_force_units,
            LIRA_NATIVE_FORCE_COMPONENTS,
            field="native_force_units",
        )
        native_results = _explicit_mapping(
            self.native_results,
            LIRA_NATIVE_RESULT_COMPONENTS,
            field="native_results",
        )
        native_result_units = _explicit_mapping(
            self.native_result_units,
            LIRA_NATIVE_RESULT_COMPONENTS,
            field="native_result_units",
        )
        mapped_headers = [
            header
            for group in (columns, native_forces, native_results)
            for header in group.values()
            if header is not None
        ]
        if len(mapped_headers) != len(set(mapped_headers)):
            raise LiraMappingError("each source concept must map to a distinct column")
        if not any(columns[name] is not None for name in IDENTIFIER_FIELDS):
            raise LiraMappingError(
                "at least one of element_id/node_id/member_id must be mapped"
            )
        for component in LIRA_NATIVE_FORCE_COMPONENTS:
            header = native_forces[component]
            unit = native_force_units[component]
            if header is None and unit is not None:
                raise LiraMappingError(
                    f"native_force_units.{component} must be null when the column is null"
                )
            if header is not None and unit is None:
                raise LiraMappingError(
                    f"ambiguous unit for native LIRA component {component}"
                )
            if unit is not None:
                validate_native_force_unit(component, unit)
        for component in LIRA_NATIVE_RESULT_COMPONENTS:
            header = native_results[component]
            unit = native_result_units[component]
            if header is None and unit is not None:
                raise LiraMappingError(
                    f"native_result_units.{component} must be null when the column is null"
                )
            if header is not None and unit is None:
                raise LiraMappingError(
                    f"ambiguous source unit for native result {component}"
                )
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
        object.__setattr__(self, "native_forces", MappingProxyType(native_forces))
        object.__setattr__(
            self, "native_force_units", MappingProxyType(native_force_units)
        )
        object.__setattr__(self, "native_results", MappingProxyType(native_results))
        object.__setattr__(
            self, "native_result_units", MappingProxyType(native_result_units)
        )
        object.__setattr__(self, "options", MappingProxyType(dict(self.options)))

    @classmethod
    def from_dict(cls, payload: object) -> LiraBatchMapping:
        if not isinstance(payload, Mapping):
            raise LiraMappingError("mapping JSON root must be an object")
        allowed = {
            "source_model",
            "format",
            "columns",
            "native_forces",
            "native_force_units",
            "native_results",
            "native_result_units",
            "options",
            "decimal_separator",
            "thousands_separator",
            "convention",
        }
        unknown = set(payload) - allowed
        if unknown:
            raise LiraMappingError(f"unknown mapping fields: {sorted(unknown)}")
        options = payload.get("options", {})
        if not isinstance(options, Mapping):
            raise LiraMappingError("options must be an object")
        return cls(
            source_model=str(payload.get("source_model", "")),
            source_format=str(payload.get("format", "")),
            columns=payload.get("columns", {}),
            native_forces=payload.get("native_forces", {}),
            native_force_units=payload.get("native_force_units", {}),
            native_results=payload.get("native_results", {}),
            native_result_units=payload.get("native_result_units", {}),
            options=dict(options),
            decimal_separator=str(payload.get("decimal_separator", ".")),
            thousands_separator=_optional_string(
                payload.get("thousands_separator"), field="thousands_separator"
            ),
            convention=LiraRx3ConventionRegistry.from_dict(payload.get("convention")),
        )

    @property
    def source_headers(self) -> frozenset[str]:
        return frozenset(
            header
            for group in (self.columns, self.native_forces, self.native_results)
            for header in group.values()
            if header is not None
        )

    @property
    def header_row(self) -> int:
        value = self.options.get("header_row", 1)
        if isinstance(value, bool) or not isinstance(value, int):
            raise LiraMappingError("options.header_row must be an integer")
        return value

    @property
    def worksheet_or_table(self) -> str | None:
        if self.source_format == "xlsx":
            value = self.options.get("sheet_name")
            return value if isinstance(value, str) else None
        if self.source_format == "html":
            return f"table[{self.options.get('table_index', 0)}]"
        return None

    def as_dict(self) -> dict[str, object]:
        return {
            "source_model": self.source_model,
            "format": self.source_format,
            "columns": dict(self.columns),
            "native_forces": dict(self.native_forces),
            "native_force_units": dict(self.native_force_units),
            "native_results": dict(self.native_results),
            "native_result_units": dict(self.native_result_units),
            "options": dict(self.options),
            "decimal_separator": self.decimal_separator,
            "thousands_separator": self.thousands_separator,
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
class LiraNativeValue:
    native_component: str
    source_column: str | None
    source_cell: str | None
    raw_token: str | None
    parsed_decimal: Decimal | None
    source_unit: str | None
    normalized_value: Decimal | None
    normalized_unit: str | None
    conversion: str | None

    def as_dict(self) -> dict[str, str | None]:
        return {
            "native_component": self.native_component,
            "source_column": self.source_column,
            "source_cell": self.source_cell,
            "raw_token": self.raw_token,
            "parsed_decimal": (
                None if self.parsed_decimal is None else str(self.parsed_decimal)
            ),
            "source_unit": self.source_unit,
            "normalized_value": (
                None if self.normalized_value is None else str(self.normalized_value)
            ),
            "normalized_unit": self.normalized_unit,
            "conversion": self.conversion,
        }


@dataclass(frozen=True, slots=True)
class LiraNativeForceRecord:
    source_row: int
    source_sheet_or_table: str | None
    metadata: Mapping[str, str | None]
    metadata_provenance: Mapping[str, Mapping[str, str | None]]
    native_forces: Mapping[str, LiraNativeValue]
    native_results: Mapping[str, LiraNativeValue]

    def __post_init__(self) -> None:
        if set(self.metadata) != set(SOURCE_METADATA_FIELDS):
            raise ValueError("metadata must contain every native source concept")
        if set(self.native_forces) != set(LIRA_NATIVE_FORCE_COMPONENTS):
            raise ValueError("native_forces must contain N/Mk/My/Mz/Qy/Qz")
        if set(self.native_results) != set(LIRA_NATIVE_RESULT_COMPONENTS):
            raise ValueError("native_results must contain Ry/Rz")
        object.__setattr__(self, "metadata", MappingProxyType(dict(self.metadata)))
        object.__setattr__(
            self,
            "metadata_provenance",
            MappingProxyType(
                {
                    name: MappingProxyType(dict(value))
                    for name, value in self.metadata_provenance.items()
                }
            ),
        )
        object.__setattr__(
            self, "native_forces", MappingProxyType(dict(self.native_forces))
        )
        object.__setattr__(
            self, "native_results", MappingProxyType(dict(self.native_results))
        )

    @property
    def primary_identifier(self) -> str | None:
        for name in IDENTIFIER_FIELDS:
            value = self.metadata[name]
            if value is not None:
                return value
        return None

    @property
    def section_station(self) -> str | None:
        return self.metadata["section_station"]

    @property
    def available_native_forces(self) -> dict[str, Decimal | None]:
        return {
            name: self.native_forces[name].normalized_value
            for name in LIRA_NATIVE_FORCE_COMPONENTS
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
            "metadata": dict(self.metadata),
            "metadata_provenance": {
                name: dict(value)
                for name, value in self.metadata_provenance.items()
            },
            "native_forces": {
                name: self.native_forces[name].as_dict()
                for name in LIRA_NATIVE_FORCE_COMPONENTS
            },
            "native_results": {
                name: self.native_results[name].as_dict()
                for name in LIRA_NATIVE_RESULT_COMPONENTS
            },
            "native_to_rx3_convention": convention.as_dict(),
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
class LiraConventionCandidate:
    element_id: str
    source_rows: tuple[int, ...]
    section_stations: tuple[str, ...]
    nonzero_native_components: tuple[str, ...]
    varying_native_components: tuple[str, ...]
    sign_changing_native_components: tuple[str, ...]
    classification: str = "CANDIDATE_ONLY"

    @property
    def score(self) -> int:
        return (
            100 * len(self.nonzero_native_components)
            + 10 * len(self.varying_native_components)
            + len(self.sign_changing_native_components)
        )

    def as_dict(self) -> dict[str, object]:
        return {
            "element_id": self.element_id,
            "source_rows": list(self.source_rows),
            "section_stations": list(self.section_stations),
            "nonzero_native_components": list(self.nonzero_native_components),
            "varying_native_components": list(self.varying_native_components),
            "sign_changing_native_components": list(
                self.sign_changing_native_components
            ),
            "score": self.score,
            "classification": self.classification,
            "not_evidence_for_axis_or_sign": True,
        }


@dataclass(frozen=True, slots=True)
class LiraBatchImportResult:
    source_file: str
    source_sha256: str
    source_format: str
    worksheet_or_table: str | None
    header_row: int
    mapping_fingerprint: str
    rows_parsed: int
    records: tuple[LiraNativeForceRecord, ...]
    rejected_rows: tuple[RejectedLiraRow, ...]
    warnings: tuple[LiraImportIssue, ...]
    engineering_blockers: tuple[LiraImportIssue, ...]
    project_elements: tuple[ProjectElement, ...]
    audit_trail: tuple[Mapping[str, object], ...]
    convention: LiraRx3ConventionRegistry
    distributions: Mapping[str, Mapping[str, int]]
    native_component_statistics: Mapping[str, Mapping[str, object]]
    convention_candidates: tuple[LiraConventionCandidate, ...]

    @property
    def rows_accepted(self) -> int:
        return len(self.records)

    @property
    def rows_rejected(self) -> int:
        return len(self.rejected_rows)

    @property
    def unique_elements(self) -> int:
        return len(
            {
                record.primary_identifier
                for record in self.records
                if record.primary_identifier is not None
            }
        )

    @property
    def rx38_force_generation_allowed(self) -> bool:
        return False

    def row_blocker_codes(self, source_row: int) -> tuple[str, ...]:
        return tuple(
            issue.code
            for issue in self.engineering_blockers
            if issue.source_row in {None, source_row}
        )

    def summary_dict(self) -> dict[str, object]:
        return {
            "source_file_sha256": self.source_sha256,
            "source_format": self.source_format,
            "worksheet_or_table": self.worksheet_or_table,
            "header_row": self.header_row,
            "mapping_fingerprint": self.mapping_fingerprint,
            "rows_parsed": self.rows_parsed,
            "rows_accepted": self.rows_accepted,
            "rows_rejected": self.rows_rejected,
            "unique_elements": self.unique_elements,
            "distributions": {
                name: dict(values) for name, values in self.distributions.items()
            },
            "native_component_statistics": {
                name: dict(values)
                for name, values in self.native_component_statistics.items()
            },
            "convention_candidates": [
                candidate.as_dict() for candidate in self.convention_candidates
            ],
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
            "status": "LIRA_NATIVE_REVIEW_BUNDLE_READY",
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
    token = _raw_token(row.values.get(header))
    if token is None:
        return None
    stripped = token.strip()
    return stripped or None


def _parsed_decimal(
    row: RawTableRow,
    component: str,
    header: str,
    mapping: LiraBatchMapping,
) -> tuple[str | None, Decimal | None, str]:
    raw = row.values.get(header)
    token = _raw_token(raw)
    cell = row.cells.get(header, header)
    if token is None or not token.strip():
        return token, None, cell
    if isinstance(raw, float):
        raise LiraRowError(
            f"row {row.row_number}, native component {component}: "
            "binary float is not accepted"
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
            f"row {row.row_number}, native component {component}: "
            f"{token!r} is not a number"
        ) from exc
    if not parsed.is_finite():
        raise LiraRowError(
            f"row {row.row_number}, native component {component}: value must be finite"
        )
    return token, parsed, cell


def _native_force_value(
    row: RawTableRow,
    component: str,
    mapping: LiraBatchMapping,
) -> LiraNativeValue:
    header = mapping.native_forces[component]
    unit = mapping.native_force_units[component]
    if header is None or unit is None:
        return LiraNativeValue(
            component, None, None, None, None, None, None, None, None
        )
    token, parsed, cell = _parsed_decimal(row, component, header, mapping)
    if parsed is None:
        return LiraNativeValue(
            component, header, cell, token, None, unit, None, None, None
        )
    normalized, normalized_unit = to_review_unit(component, parsed, unit)
    return LiraNativeValue(
        component,
        header,
        cell,
        token,
        parsed,
        unit,
        normalized,
        normalized_unit,
        f"{component}: {token} {unit} -> {normalized} {normalized_unit}",
    )


def _native_result_value(
    row: RawTableRow,
    component: str,
    mapping: LiraBatchMapping,
) -> LiraNativeValue:
    header = mapping.native_results[component]
    unit = mapping.native_result_units[component]
    if header is None or unit is None:
        return LiraNativeValue(
            component, None, None, None, None, None, None, None, None
        )
    token, parsed, cell = _parsed_decimal(row, component, header, mapping)
    return LiraNativeValue(
        component,
        header,
        cell,
        token,
        parsed,
        unit,
        None,
        None,
        None,
    )


def _record(row: RawTableRow, mapping: LiraBatchMapping) -> LiraNativeForceRecord:
    metadata = {
        name: _text_value(row, mapping.columns[name])
        for name in SOURCE_METADATA_FIELDS
    }
    if not any(metadata[name] for name in IDENTIFIER_FIELDS):
        raise LiraRowError(
            f"row {row.row_number}: all mapped element/node/member identifiers are empty"
        )
    metadata_provenance: dict[str, Mapping[str, str | None]] = {}
    for name in SOURCE_METADATA_FIELDS:
        header = mapping.columns[name]
        metadata_provenance[name] = {
            "mapping_field": name,
            "source_column": header,
            "source_cell": (
                None if header is None else row.cells.get(header, header)
            ),
            "raw_token": (
                None if header is None else _raw_token(row.values.get(header))
            ),
        }
    return LiraNativeForceRecord(
        source_row=row.row_number,
        source_sheet_or_table=row.sheet,
        metadata=metadata,
        metadata_provenance=metadata_provenance,
        native_forces={
            component: _native_force_value(row, component, mapping)
            for component in LIRA_NATIVE_FORCE_COMPONENTS
        },
        native_results={
            component: _native_result_value(row, component, mapping)
            for component in LIRA_NATIVE_RESULT_COMPONENTS
        },
    )


def _distribution(
    records: Sequence[LiraNativeForceRecord], field: str
) -> dict[str, int]:
    counts: dict[str, int] = {}
    for record in records:
        value = record.metadata[field]
        key = "<MISSING>" if value is None else value
        counts[key] = counts.get(key, 0) + 1
    return dict(sorted(counts.items(), key=lambda item: item[0]))


def _component_statistics(
    records: Sequence[LiraNativeForceRecord],
) -> dict[str, dict[str, object]]:
    result: dict[str, dict[str, object]] = {}
    all_components = (*LIRA_NATIVE_FORCE_COMPONENTS, *LIRA_NATIVE_RESULT_COMPONENTS)
    for component in all_components:
        values = [
            (
                record.native_forces[component]
                if component in LIRA_NATIVE_FORCE_COMPONENTS
                else record.native_results[component]
            )
            for record in records
        ]
        parsed = [value.parsed_decimal for value in values if value.parsed_decimal is not None]
        source_units = sorted(
            {value.source_unit for value in values if value.source_unit is not None}
        )
        normalized_units = sorted(
            {
                value.normalized_unit
                for value in values
                if value.normalized_unit is not None
            }
        )
        nonzero_count = sum(value != 0 for value in parsed)
        result[component] = {
            "source_column": values[0].source_column if values else None,
            "source_units": source_units,
            "normalized_units": normalized_units,
            "rows_present": len(parsed),
            "missing_count": len(values) - len(parsed),
            "nonzero_count": nonzero_count,
            "zero_count": len(parsed) - nonzero_count,
            "all_zero": bool(parsed) and nonzero_count == 0,
            "minimum_source_value": None if not parsed else str(min(parsed)),
            "maximum_source_value": None if not parsed else str(max(parsed)),
            "semantic_status": "UNKNOWN",
        }
    return result


def _numeric_identifier(value: str) -> tuple[int, int | str]:
    try:
        return 0, int(value)
    except ValueError:
        return 1, value


def _candidate_elements(
    records: Sequence[LiraNativeForceRecord],
    *,
    limit: int = 12,
) -> tuple[LiraConventionCandidate, ...]:
    grouped: dict[str, list[LiraNativeForceRecord]] = {}
    for record in records:
        identifier = record.primary_identifier
        if identifier is not None:
            grouped.setdefault(identifier, []).append(record)
    candidates: list[LiraConventionCandidate] = []
    for identifier, rows in grouped.items():
        nonzero: list[str] = []
        varying: list[str] = []
        sign_changing: list[str] = []
        for component in LIRA_NATIVE_FORCE_COMPONENTS:
            values: list[Decimal] = []
            for row in rows:
                value = row.native_forces[component].parsed_decimal
                if value is not None:
                    values.append(value)
            if any(value != 0 for value in values):
                nonzero.append(component)
            if len(set(values)) > 1:
                varying.append(component)
            if any(value < 0 for value in values) and any(value > 0 for value in values):
                sign_changing.append(component)
        if len(nonzero) < 3:
            continue
        candidates.append(
            LiraConventionCandidate(
                element_id=identifier,
                source_rows=tuple(row.source_row for row in rows),
                section_stations=tuple(
                    row.section_station or "<MISSING>" for row in rows
                ),
                nonzero_native_components=tuple(nonzero),
                varying_native_components=tuple(varying),
                sign_changing_native_components=tuple(sign_changing),
            )
        )
    candidates.sort(
        key=lambda candidate: (
            -candidate.score,
            _numeric_identifier(candidate.element_id),
        )
    )
    return tuple(candidates[:limit])


def import_lira_batch(
    source_path: str | Path,
    mapping: LiraBatchMapping,
    *,
    mapping_file: str = "mapping.json",
    existing_elements: Sequence[ProjectElement] = (),
) -> LiraBatchImportResult:
    path = Path(source_path).resolve(strict=True)
    if not path.is_file():
        raise LiraFormatError(f"LIRA input is not a file: {path}")
    rows = _source(mapping, path).read_rows()
    issues: list[LiraImportIssue] = []
    warnings: list[LiraImportIssue] = []
    records: list[LiraNativeForceRecord] = []
    rejected: list[RejectedLiraRow] = []
    if not rows:
        issues.append(
            LiraImportIssue(
                "EMPTY_SOURCE_TABLE",
                "Source table contains no data rows; no engineering values were inferred",
            )
        )
    if rows:
        missing_headers = mapping.source_headers - set(rows[0].values)
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

    if mapping.columns["profile"] is None or mapping.columns["mark"] is None:
        issues.append(
            LiraImportIssue(
                "LIRA_MEMBER_PROFILE_IDENTITY_MISSING",
                "The source has no explicit member profile and/or construction mark; "
                "section_station is not a profile and ProjectElement creation is blocked",
            )
        )
    if mapping.columns["combination"] is None:
        issues.append(
            LiraImportIssue(
                "LIRA_LOAD_COMBINATION_IDENTITY_MISSING",
                "The source has no explicit load-combination identity",
            )
        )
    available_components = {
        component: next(
            (
                record.native_forces[component].normalized_value
                for record in records
                if record.native_forces[component].normalized_value is not None
            ),
            None,
        )
        for component in LIRA_NATIVE_FORCE_COMPONENTS
    }
    unresolved = mapping.convention.unresolved_components(available_components)
    if unresolved:
        issues.append(
            LiraImportIssue(
                "LIRA_RX3_FORCE_CONVENTION",
                "RX38 generation blocked; native-to-RX3 axis/sign mapping is "
                f"unresolved for {', '.join(unresolved)}",
            )
        )
    for record in records:
        if record.section_station is None:
            issues.append(
                LiraImportIssue(
                    "LIRA_SECTION_STATION_MISSING",
                    "LIRA calculation section/station number is missing",
                    source_row=record.source_row,
                    field="section_station",
                )
            )
        missing_components = [
            component
            for component, value in record.available_native_forces.items()
            if value is None
        ]
        if missing_components:
            issues.append(
                LiraImportIssue(
                    "NATIVE_FORCE_COMPONENTS_MISSING",
                    "Unavailable native components: " + ", ".join(missing_components),
                    source_row=record.source_row,
                )
            )

    seen: dict[tuple[str, str | None, str | None], int] = {}
    for record in records:
        identifier = record.primary_identifier
        if identifier is None:
            continue
        key = (
            identifier,
            record.section_station,
            record.metadata["load_case"],
        )
        if key in seen:
            issues.append(
                LiraImportIssue(
                    "DUPLICATE_AMBIGUOUS_NATIVE_ROW",
                    "Duplicate element/section_station/load_case source identity; "
                    f"first row is {seen[key]}",
                    source_row=record.source_row,
                )
            )
        else:
            seen[key] = record.source_row

    statistics = _component_statistics(records)
    for component in (*LIRA_NATIVE_FORCE_COMPONENTS, *LIRA_NATIVE_RESULT_COMPONENTS):
        if statistics[component]["all_zero"]:
            warnings.append(
                LiraImportIssue(
                    "ALL_ZERO_NATIVE_COMPONENT_NOT_SEMANTIC_EVIDENCE",
                    f"{component} is zero in every accepted row; this establishes no "
                    "axis, sign, or RX3 semantic mapping",
                    severity="WARNING",
                    field=component,
                )
            )
    distributions = {
        "section_station": _distribution(records, "section_station"),
        "load_case": _distribution(records, "load_case"),
        "element_type": _distribution(records, "element_type"),
        "composition": _distribution(records, "composition"),
    }
    candidates = _candidate_elements(records)
    source_sha = _sha256_path(path)
    audit = (
        {
            "event": "SOURCE_HASHED",
            "source_file": str(path),
            "sha256": source_sha,
        },
        {
            "event": "NATIVE_MAPPING_FINGERPRINTED",
            "mapping_file": mapping_file,
            "fingerprint": mapping.fingerprint,
            "source_model": mapping.source_model,
        },
        {
            "event": "NATIVE_ROWS_IMPORTED_FOR_REVIEW_ONLY",
            "rows_parsed": len(rows),
            "rows_accepted": len(records),
            "rows_rejected": len(rejected),
            "project_elements_created": 0,
            "rx38_written": False,
        },
    )
    return LiraBatchImportResult(
        source_file=str(path),
        source_sha256=source_sha,
        source_format=mapping.source_format,
        worksheet_or_table=mapping.worksheet_or_table,
        header_row=mapping.header_row,
        mapping_fingerprint=mapping.fingerprint,
        rows_parsed=len(rows),
        records=tuple(records),
        rejected_rows=tuple(rejected),
        warnings=tuple(warnings),
        engineering_blockers=tuple(issues),
        project_elements=(),
        audit_trail=audit,
        convention=mapping.convention,
        distributions=MappingProxyType(
            {name: MappingProxyType(values) for name, values in distributions.items()}
        ),
        native_component_statistics=MappingProxyType(
            {name: MappingProxyType(values) for name, values in statistics.items()}
        ),
        convention_candidates=candidates,
    )


def _write_json(path: Path, payload: object) -> None:
    with path.open("x", encoding="utf-8", newline="\n") as stream:
        json.dump(payload, stream, ensure_ascii=False, indent=2)
        stream.write("\n")


def _write_review_csv(path: Path, result: LiraBatchImportResult) -> None:
    fields = [
        "element_id",
        "section_station",
        "load_case",
        "element_type",
        "composition",
        "mark",
        "profile",
        *LIRA_NATIVE_FORCE_COMPONENTS,
        *LIRA_NATIVE_RESULT_COMPONENTS,
        "source_units",
        "row_source",
        "native_to_rx3_status",
        "blockers",
    ]
    with path.open("x", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields)
        writer.writeheader()
        for record in result.records:
            native_values = {
                component: record.native_forces[component].raw_token
                for component in LIRA_NATIVE_FORCE_COMPONENTS
            }
            native_values.update(
                {
                    component: record.native_results[component].raw_token
                    for component in LIRA_NATIVE_RESULT_COMPONENTS
                }
            )
            source_units = {
                component: record.native_forces[component].source_unit
                for component in LIRA_NATIVE_FORCE_COMPONENTS
            }
            source_units.update(
                {
                    component: record.native_results[component].source_unit
                    for component in LIRA_NATIVE_RESULT_COMPONENTS
                }
            )
            writer.writerow(
                {
                    "element_id": record.primary_identifier,
                    "section_station": record.section_station,
                    "load_case": record.metadata["load_case"],
                    "element_type": record.metadata["element_type"],
                    "composition": record.metadata["composition"],
                    "mark": record.metadata["mark"],
                    "profile": record.metadata["profile"],
                    **native_values,
                    "source_units": json.dumps(
                        source_units, ensure_ascii=False, sort_keys=True
                    ),
                    "row_source": (
                        f"{record.source_sheet_or_table!r}:{record.source_row}"
                    ),
                    "native_to_rx3_status": "; ".join(
                        f"{component}="
                        f"{result.convention.components[component].verification_status.value}"
                        for component in LIRA_NATIVE_FORCE_COMPONENTS
                    ),
                    "blockers": "; ".join(
                        result.row_blocker_codes(record.source_row)
                    ),
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
            "worksheet_or_table": result.worksheet_or_table,
            "header_row": result.header_row,
            "source_overwritten": False,
        },
    )
    _write_json(paths["mapping_snapshot.json"], mapping.as_dict())
    _write_json(paths["import_summary.json"], result.summary_dict())
    _write_json(
        paths["forces.json"],
        {
            "source_model": mapping.source_model,
            "accepted_native_records": [
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
    _write_json(paths["project_elements.json"], [])
    _write_json(
        paths["blockers.json"],
        {
            "engineering_blockers": [
                issue.as_dict() for issue in result.engineering_blockers
            ],
            "warnings": [issue.as_dict() for issue in result.warnings],
        },
    )
    _write_json(paths["audit.json"], [dict(event) for event in result.audit_trail])
    with paths["README_REVIEW.md"].open("x", encoding="utf-8", newline="\n") as stream:
        stream.write(
            "# Native LIRA import review bundle\n\n"
            "This bundle is review-only. It did not create or mutate RX38 files.\n\n"
            f"- Source SHA-256: `{result.source_sha256}`\n"
            f"- Worksheet/table: `{result.worksheet_or_table!r}`\n"
            f"- Header row: {result.header_row}\n"
            f"- Rows: {result.rows_parsed} parsed / {result.rows_accepted} accepted / "
            f"{result.rows_rejected} rejected\n"
            f"- Unique elements: {result.unique_elements}\n"
            f"- Engineering blockers: {len(result.engineering_blockers)}\n"
            "- ProjectElements created: **0**\n"
            "- RX38 force generation: **BLOCKED**\n"
            "- Issue readiness: **NOT_READY_FOR_ISSUE**\n\n"
            "`section_station` is a LIRA calculation station, not a profile. "
            "Native N/Mk/My/Mz/Qy/Qz and Ry/Rz are source evidence only. "
            "No component is renamed to ProjectElement or RX3 semantics. Review "
            "`forces_review.csv` for readability and treat `forces.json` as the "
            "Decimal-safe authoritative source record. Candidate elements in "
            "`import_summary.json` are CANDIDATE_ONLY and prove no axis or sign.\n"
        )
    return LiraReviewBundle(destination, result, tuple(paths.values()))
