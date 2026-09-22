"""Read-only LIRA-SAPR RSU import and independent reconstruction.

The module keeps native LIRA component names and never creates ``ProjectElement``
or RX38 input.  Published RSU rows are reconstructed only from explicitly linked
load-case rows and an explicitly selected coefficient column.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from enum import Enum
from hashlib import sha256
import json
from pathlib import Path
from types import MappingProxyType
from typing import Mapping, Sequence

from .errors import LiraFormatError, LiraMappingError
from .types import LIRA_NATIVE_FORCE_COMPONENTS
from .xls import XlsCell, XlsWorkbook, read_xls_workbook

RSU_FORCE_COMPONENTS = LIRA_NATIVE_FORCE_COMPONENTS


class RsuValidationStatus(str, Enum):
    VERIFIED = "VERIFIED"
    BLOCKED = "BLOCKED"


@dataclass(frozen=True, slots=True)
class RsuXlsMapping:
    force_header_row: int = 3
    rsu_header_row: int = 3
    coefficient_header_row: int = 3
    parameter_header_row: int = 3
    encoding_override: str | None = "cp1251"
    element_header: str = "№ элем"
    station_header: str = "№ сечен"
    load_case_header: str = "№ загруж"
    rsu_group_header: str = "Группа РСУ"
    rsu_criterion_header: str = "Критерий"
    rsu_column_header: str = "№ столбца"
    membership_header: str = "№№ загруж"
    coefficient_load_header: str = "№ загр."
    parameter_load_header: str = "№ загр."
    mutual_exclusion_header: str = "Взаимоискл."
    force_headers: Mapping[str, str] = field(
        default_factory=lambda: {
            "N": "N\n(т)",
            "Mk": "Mk\n(т*м)",
            "My": "My\n(т*м)",
            "Mz": "Mz\n(т*м)",
            "Qy": "Qy\n(т)",
            "Qz": "Qz\n(т)",
        }
    )
    coefficient_columns: Mapping[int, str] = field(
        default_factory=lambda: {1: "1 основ.", 2: "2 основ."}
    )

    def __post_init__(self) -> None:
        if set(self.force_headers) != set(RSU_FORCE_COMPONENTS):
            raise LiraMappingError("force_headers must contain N/Mk/My/Mz/Qy/Qz")
        if not self.coefficient_columns:
            raise LiraMappingError("coefficient_columns must not be empty")
        for name in (
            "force_header_row",
            "rsu_header_row",
            "coefficient_header_row",
            "parameter_header_row",
        ):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, int) or value < 1:
                raise LiraMappingError(f"{name} must be a positive integer")
        object.__setattr__(self, "force_headers", MappingProxyType(dict(self.force_headers)))
        object.__setattr__(
            self, "coefficient_columns", MappingProxyType(dict(self.coefficient_columns))
        )

    def as_dict(self) -> dict[str, object]:
        return {
            "force_header_row": self.force_header_row,
            "rsu_header_row": self.rsu_header_row,
            "coefficient_header_row": self.coefficient_header_row,
            "parameter_header_row": self.parameter_header_row,
            "encoding_override": self.encoding_override,
            "element_header": self.element_header,
            "station_header": self.station_header,
            "load_case_header": self.load_case_header,
            "rsu_group_header": self.rsu_group_header,
            "rsu_criterion_header": self.rsu_criterion_header,
            "rsu_column_header": self.rsu_column_header,
            "membership_header": self.membership_header,
            "coefficient_load_header": self.coefficient_load_header,
            "parameter_load_header": self.parameter_load_header,
            "mutual_exclusion_header": self.mutual_exclusion_header,
            "force_headers": dict(self.force_headers),
            "coefficient_columns": {
                str(key): value for key, value in self.coefficient_columns.items()
            },
        }

    @property
    def fingerprint(self) -> str:
        payload = json.dumps(
            self.as_dict(), ensure_ascii=False, sort_keys=True, separators=(",", ":")
        ).encode("utf-8")
        return sha256(payload).hexdigest()


@dataclass(frozen=True, slots=True)
class RsuSourceValue:
    component: str
    value: Decimal
    source_unit: str
    source_header: str
    source_sheet: str
    source_row: int
    source_cell: str
    source_sha256: str
    raw_token: str | None
    decimal_provenance: str | None


@dataclass(frozen=True, slots=True)
class RsuForceVector:
    values: Mapping[str, RsuSourceValue]

    def __post_init__(self) -> None:
        if set(self.values) != set(RSU_FORCE_COMPONENTS):
            raise LiraFormatError("force vector must contain N/Mk/My/Mz/Qy/Qz")
        object.__setattr__(self, "values", MappingProxyType(dict(self.values)))

    def decimals(self) -> dict[str, Decimal]:
        return {name: self.values[name].value for name in RSU_FORCE_COMPONENTS}


@dataclass(frozen=True, slots=True)
class RsuLoadForceRecord:
    element_id: str
    section_station: str
    load_case_id: str
    vector: RsuForceVector
    source_sheet: str
    source_row: int
    source_sha256: str
    mapping_fingerprint: str


@dataclass(frozen=True, slots=True)
class RsuPublishedRecord:
    element_id: str
    section_station: str
    rsu_group: str
    rsu_criterion: str
    rsu_column_number: int
    load_case_membership: tuple[str, ...]
    vector: RsuForceVector
    source_sheet: str
    source_row: int
    source_sha256: str
    mapping_fingerprint: str


@dataclass(frozen=True, slots=True)
class RsuCoefficient:
    load_case_id: str
    column_number: int
    coefficient: Decimal
    source_header: str
    source_sheet: str
    source_row: int
    source_cell: str
    source_sha256: str
    raw_token: str | None
    decimal_provenance: str | None


@dataclass(frozen=True, slots=True)
class RsuLoadParameter:
    load_case_id: str
    values: Mapping[str, str | Decimal | None]
    source_sheet: str
    source_row: int
    source_sha256: str


@dataclass(frozen=True, slots=True)
class RsuImportBundle:
    forces_workbook: XlsWorkbook
    published_workbook: XlsWorkbook
    coefficients_workbook: XlsWorkbook
    parameters_workbook: XlsWorkbook
    force_records: tuple[RsuLoadForceRecord, ...]
    published_records: tuple[RsuPublishedRecord, ...]
    coefficients: tuple[RsuCoefficient, ...]
    parameters: tuple[RsuLoadParameter, ...]
    mapping_fingerprint: str
    rx38_force_generation_allowed: bool = False
    issue_readiness: str = "NOT_READY_FOR_ISSUE"


@dataclass(frozen=True, slots=True)
class RsuComponentDifference:
    component: str
    published: Decimal
    reconstructed: Decimal
    difference: Decimal
    source_terms: tuple[tuple[str, Decimal, Decimal], ...]


@dataclass(frozen=True, slots=True)
class RsuReconstructionResult:
    published_record: RsuPublishedRecord
    components: tuple[RsuComponentDifference, ...]
    status: RsuValidationStatus
    blockers: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class RsuValidationReport:
    results: tuple[RsuReconstructionResult, ...]
    status: RsuValidationStatus
    component_comparisons: int
    matching_components: int
    blockers: tuple[str, ...]
    rx38_force_generation_allowed: bool = False
    issue_readiness: str = "NOT_READY_FOR_ISSUE"


def _headers(sheet: object, row_number: int) -> dict[str, int]:
    row = sheet.rows[row_number - 1]  # type: ignore[attr-defined]
    result: dict[str, int] = {}
    for index, cell in enumerate(row):
        if cell.value is None:
            continue
        header = str(cell.value).strip()
        if header in result:
            raise LiraFormatError(f"duplicate XLS header {header!r}")
        result[header] = index
    return result


def _required_column(headers: Mapping[str, int], name: str, *, source: str) -> int:
    try:
        return headers[name]
    except KeyError as exc:
        raise LiraMappingError(f"{source}: missing required header {name!r}") from exc


def _text(cell: XlsCell, *, field: str) -> str:
    if cell.value is None or not str(cell.value).strip():
        raise LiraFormatError(
            f"{cell.sheet_name}!{cell.cell_reference}: {field} is required"
        )
    value = cell.value
    if isinstance(value, Decimal) and value == value.to_integral_value():
        return str(value.to_integral_value())
    return str(value).strip()


def _decimal(cell: XlsCell, *, field: str) -> Decimal:
    if cell.decimal_value is None:
        raise LiraFormatError(
            f"{cell.sheet_name}!{cell.cell_reference}: {field} must be numeric"
        )
    if not cell.decimal_value.is_finite():
        raise LiraFormatError(
            f"{cell.sheet_name}!{cell.cell_reference}: {field} must be finite"
        )
    return cell.decimal_value


def _unit_from_header(header: str) -> str:
    if "(т*м)" in header:
        return "tf*m"
    if "(т)" in header:
        return "tf"
    raise LiraMappingError(f"cannot determine explicit source unit from {header!r}")


def _vector(
    row: tuple[XlsCell, ...],
    headers: Mapping[str, int],
    mapping: RsuXlsMapping,
    source_sha256: str,
) -> RsuForceVector:
    values: dict[str, RsuSourceValue] = {}
    for component in RSU_FORCE_COMPONENTS:
        header = mapping.force_headers[component]
        cell = row[_required_column(headers, header, source="force vector")]
        values[component] = RsuSourceValue(
            component=component,
            value=_decimal(cell, field=component),
            source_unit=_unit_from_header(header),
            source_header=header,
            source_sheet=cell.sheet_name,
            source_row=cell.row_number,
            source_cell=cell.cell_reference,
            source_sha256=source_sha256,
            raw_token=cell.raw_token,
            decimal_provenance=cell.decimal_provenance,
        )
    return RsuForceVector(values)


def _membership(token: str) -> tuple[str, ...]:
    values = tuple(part for part in token.replace("\xa0", " ").split() if part)
    if not values:
        raise LiraFormatError("RSU load-case membership is empty")
    if len(values) != len(set(values)):
        raise LiraFormatError("RSU load-case membership contains duplicates")
    return values


def import_rsu_xls_bundle(
    *,
    forces_path: str | Path,
    published_path: str | Path,
    coefficients_path: str | Path,
    parameters_path: str | Path,
    mapping: RsuXlsMapping | None = None,
    expected_sha256: Mapping[str, str] | None = None,
) -> RsuImportBundle:
    """Import all worksheets and preserve BIFF provenance for RSU validation."""

    config = mapping or RsuXlsMapping()
    hashes = expected_sha256 or {}
    books = {
        "forces": read_xls_workbook(
            forces_path,
            encoding_override=config.encoding_override,
            expected_sha256=hashes.get("forces"),
        ),
        "published": read_xls_workbook(
            published_path,
            encoding_override=config.encoding_override,
            expected_sha256=hashes.get("published"),
        ),
        "coefficients": read_xls_workbook(
            coefficients_path,
            encoding_override=config.encoding_override,
            expected_sha256=hashes.get("coefficients"),
        ),
        "parameters": read_xls_workbook(
            parameters_path,
            encoding_override=config.encoding_override,
            expected_sha256=hashes.get("parameters"),
        ),
    }
    force_records: list[RsuLoadForceRecord] = []
    for sheet in books["forces"].worksheets:
        headers = _headers(sheet, config.force_header_row)
        element_column = _required_column(headers, config.element_header, source="forces")
        station_column = _required_column(headers, config.station_header, source="forces")
        load_column = _required_column(headers, config.load_case_header, source="forces")
        for row in sheet.rows[config.force_header_row :]:
            if all(cell.value is None for cell in row):
                continue
            force_records.append(
                RsuLoadForceRecord(
                    element_id=_text(row[element_column], field="element_id"),
                    section_station=_text(row[station_column], field="section_station"),
                    load_case_id=_text(row[load_column], field="load_case_id"),
                    vector=_vector(row, headers, config, books["forces"].source_sha256),
                    source_sheet=sheet.name,
                    source_row=row[0].row_number,
                    source_sha256=books["forces"].source_sha256,
                    mapping_fingerprint=config.fingerprint,
                )
            )
    published_records: list[RsuPublishedRecord] = []
    for sheet in books["published"].worksheets:
        headers = _headers(sheet, config.rsu_header_row)
        columns = {
            name: _required_column(headers, header, source="published RSU")
            for name, header in {
                "element": config.element_header,
                "station": config.station_header,
                "group": config.rsu_group_header,
                "criterion": config.rsu_criterion_header,
                "column": config.rsu_column_header,
                "membership": config.membership_header,
            }.items()
        }
        for row in sheet.rows[config.rsu_header_row :]:
            if all(cell.value is None for cell in row):
                continue
            column_decimal = _decimal(row[columns["column"]], field="rsu_column_number")
            if column_decimal != column_decimal.to_integral_value():
                raise LiraFormatError("rsu_column_number must be an integer")
            column_number = int(column_decimal)
            if column_number not in config.coefficient_columns:
                raise LiraMappingError(
                    f"no explicit coefficient column mapping for RSU column {column_number}"
                )
            published_records.append(
                RsuPublishedRecord(
                    element_id=_text(row[columns["element"]], field="element_id"),
                    section_station=_text(row[columns["station"]], field="section_station"),
                    rsu_group=_text(row[columns["group"]], field="rsu_group"),
                    rsu_criterion=_text(row[columns["criterion"]], field="rsu_criterion"),
                    rsu_column_number=column_number,
                    load_case_membership=_membership(
                        _text(row[columns["membership"]], field="load_case_membership")
                    ),
                    vector=_vector(
                        row, headers, config, books["published"].source_sha256
                    ),
                    source_sheet=sheet.name,
                    source_row=row[0].row_number,
                    source_sha256=books["published"].source_sha256,
                    mapping_fingerprint=config.fingerprint,
                )
            )
    coefficients: list[RsuCoefficient] = []
    for sheet in books["coefficients"].worksheets:
        headers = _headers(sheet, config.coefficient_header_row)
        load_column = _required_column(
            headers, config.coefficient_load_header, source="coefficients"
        )
        mapped_columns = {
            number: _required_column(headers, header, source="coefficients")
            for number, header in config.coefficient_columns.items()
        }
        for row in sheet.rows[config.coefficient_header_row :]:
            if all(cell.value is None for cell in row):
                continue
            load_case = _text(row[load_column], field="coefficient load_case_id")
            for number, index in mapped_columns.items():
                cell = row[index]
                coefficients.append(
                    RsuCoefficient(
                        load_case_id=load_case,
                        column_number=number,
                        coefficient=_decimal(cell, field="RSU coefficient"),
                        source_header=config.coefficient_columns[number],
                        source_sheet=sheet.name,
                        source_row=cell.row_number,
                        source_cell=cell.cell_reference,
                        source_sha256=books["coefficients"].source_sha256,
                        raw_token=cell.raw_token,
                        decimal_provenance=cell.decimal_provenance,
                    )
                )
    parameters: list[RsuLoadParameter] = []
    for sheet in books["parameters"].worksheets:
        headers = _headers(sheet, config.parameter_header_row)
        load_column = _required_column(
            headers, config.parameter_load_header, source="parameters"
        )
        _required_column(
            headers, config.mutual_exclusion_header, source="parameters"
        )
        for row in sheet.rows[config.parameter_header_row :]:
            if all(cell.value is None for cell in row):
                continue
            values: dict[str, str | Decimal | None] = {}
            for header, index in headers.items():
                cell = row[index]
                values[header] = cell.value if isinstance(cell.value, (str, Decimal)) else None
            parameters.append(
                RsuLoadParameter(
                    load_case_id=_text(row[load_column], field="parameter load_case_id"),
                    values=MappingProxyType(values),
                    source_sheet=sheet.name,
                    source_row=row[0].row_number,
                    source_sha256=books["parameters"].source_sha256,
                )
            )
    return RsuImportBundle(
        forces_workbook=books["forces"],
        published_workbook=books["published"],
        coefficients_workbook=books["coefficients"],
        parameters_workbook=books["parameters"],
        force_records=tuple(force_records),
        published_records=tuple(published_records),
        coefficients=tuple(coefficients),
        parameters=tuple(parameters),
        mapping_fingerprint=config.fingerprint,
    )


def validate_rsu_reconstruction(
    bundle: RsuImportBundle,
    *,
    mutually_exclusive_sets: Sequence[frozenset[str]] | None = None,
    mutual_exclusion_header: str = "Взаимоискл.",
) -> RsuValidationReport:
    """Reconstruct every complete native force vector or block the row."""

    if mutually_exclusive_sets is None:
        groups: dict[str, set[str]] = {}
        for parameter in bundle.parameters:
            token = parameter.values.get(mutual_exclusion_header)
            if token is not None and str(token).strip():
                groups.setdefault(str(token).strip(), set()).add(parameter.load_case_id)
        mutually_exclusive_sets = tuple(
            frozenset(loads) for loads in groups.values() if len(loads) > 1
        )

    force_index: dict[tuple[str, str, str], list[RsuLoadForceRecord]] = {}
    for record in bundle.force_records:
        force_index.setdefault(
            (record.element_id, record.section_station, record.load_case_id), []
        ).append(record)
    coefficient_index: dict[tuple[str, int], list[RsuCoefficient]] = {}
    for item in bundle.coefficients:
        coefficient_index.setdefault((item.load_case_id, item.column_number), []).append(item)
    parameter_ids = {item.load_case_id for item in bundle.parameters}
    results: list[RsuReconstructionResult] = []
    report_blockers: list[str] = []
    matching_components = 0
    comparisons = 0
    for published in bundle.published_records:
        blockers: list[str] = []
        membership = set(published.load_case_membership)
        for incompatible in mutually_exclusive_sets:
            if len(membership & incompatible) > 1:
                blockers.append("RSU_MUTUALLY_EXCLUSIVE_LOADS_COMBINED")
        source_rows: list[tuple[RsuLoadForceRecord, RsuCoefficient]] = []
        for load_case in published.load_case_membership:
            matches = force_index.get(
                (published.element_id, published.section_station, load_case), []
            )
            if not matches:
                blockers.append(f"RSU_SOURCE_LOAD_MISSING:{load_case}")
                continue
            if len(matches) != 1:
                blockers.append(f"RSU_SOURCE_LOAD_AMBIGUOUS:{load_case}")
                continue
            coefficients = coefficient_index.get(
                (load_case, published.rsu_column_number), []
            )
            if not coefficients:
                blockers.append(f"RSU_COEFFICIENT_MISSING:{load_case}")
                continue
            if len(coefficients) != 1:
                blockers.append(f"RSU_COEFFICIENT_AMBIGUOUS:{load_case}")
                continue
            if parameter_ids and load_case not in parameter_ids:
                blockers.append(f"RSU_LOAD_PARAMETER_MISSING:{load_case}")
                continue
            source_rows.append((matches[0], coefficients[0]))
        components: list[RsuComponentDifference] = []
        if not blockers and len(source_rows) == len(published.load_case_membership):
            for component in RSU_FORCE_COMPONENTS:
                terms = tuple(
                    (
                        row.load_case_id,
                        row.vector.values[component].value,
                        coefficient.coefficient,
                    )
                    for row, coefficient in source_rows
                )
                reconstructed = sum(
                    (value * coefficient for _, value, coefficient in terms), Decimal("0")
                )
                actual = published.vector.values[component].value
                difference = actual - reconstructed
                comparisons += 1
                if difference == 0:
                    matching_components += 1
                else:
                    blockers.append(f"RSU_RESULT_MISMATCH:{component}")
                components.append(
                    RsuComponentDifference(
                        component=component,
                        published=actual,
                        reconstructed=reconstructed,
                        difference=difference,
                        source_terms=terms,
                    )
                )
        status = (
            RsuValidationStatus.VERIFIED
            if not blockers
            else RsuValidationStatus.BLOCKED
        )
        unique_blockers = tuple(dict.fromkeys(blockers))
        report_blockers.extend(unique_blockers)
        results.append(
            RsuReconstructionResult(
                published_record=published,
                components=tuple(components),
                status=status,
                blockers=unique_blockers,
            )
        )
    unique_report_blockers = tuple(dict.fromkeys(report_blockers))
    return RsuValidationReport(
        results=tuple(results),
        status=(
            RsuValidationStatus.VERIFIED
            if not unique_report_blockers and results
            else RsuValidationStatus.BLOCKED
        ),
        component_comparisons=comparisons,
        matching_components=matching_components,
        blockers=unique_report_blockers,
    )
