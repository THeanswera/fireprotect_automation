"""Evidence-bounded import of calculated RX3 values from RX38 files."""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path
from types import MappingProxyType
from typing import Any, Mapping

from ..model import ProjectElement, Quantity, Unit, ValueProvenance, ProvenanceType
from .parser import Rx38Record, construction_records, read_rx38
from .profiles import normalize_profile_name
from .schema import FIELD_SPECS, TCONSTR_FIELD_COUNT, field_spec


class Rx3ResultError(ValueError):
    """RX3 result cannot be imported without guessing or losing evidence."""


@dataclass(frozen=True, slots=True)
class Rx3FieldValue:
    index: int
    name: str
    confidence: str
    value: str | int | Decimal
    unit: str | None
    direction: str
    provenance: ValueProvenance

    def as_dict(self) -> dict[str, Any]:
        return {
            "index": self.index,
            "name": self.name,
            "confidence": self.confidence,
            "value": str(self.value) if isinstance(self.value, Decimal) else self.value,
            "unit": self.unit,
            "direction": self.direction,
            "provenance": _provenance_dict(self.provenance),
        }


@dataclass(frozen=True, slots=True)
class Rx3Result:
    """Confirmed RX3 values plus separately labelled probable observations."""

    mark: str | None
    profile: str | None
    area: Quantity | None
    perimeter: Quantity | None
    ptm: Quantity | None
    steel_grade: str | None
    axial_force: Quantity | None
    critical_temperature: Quantity | None
    required_fire_resistance: Quantity | None
    unprotected_fire_resistance: Quantity | None
    fireproofing_material: str | None
    fire_regime: str | None
    fireproofing_thickness: Quantity | None
    confirmed_fields: tuple[Rx3FieldValue, ...]
    probable_fields: tuple[Rx3FieldValue, ...]
    unknown_indices: tuple[int, ...]
    provenance: Mapping[str, ValueProvenance]
    source_file: str
    source_row: int | None

    def __post_init__(self) -> None:
        populated = (
            "mark",
            "profile",
            "area",
            "perimeter",
            "ptm",
            "steel_grade",
            "axial_force",
            "critical_temperature",
            "required_fire_resistance",
            "unprotected_fire_resistance",
            "fireproofing_material",
            "fire_regime",
            "fireproofing_thickness",
        )
        missing = [
            name
            for name in populated
            if getattr(self, name) is not None and name not in self.provenance
        ]
        if missing:
            raise Rx3ResultError(
                "Missing RX3_RESULT provenance for: " + ", ".join(missing)
            )
        invalid = [
            name
            for name, trace in self.provenance.items()
            if trace.kind is not ProvenanceType.RX3_RESULT
        ]
        if invalid:
            raise Rx3ResultError(
                "Rx3Result provenance must be RX3_RESULT: " + ", ".join(invalid)
            )
        object.__setattr__(self, "provenance", MappingProxyType(dict(self.provenance)))

    def as_dict(self) -> dict[str, Any]:
        return {
            "mark": self.mark,
            "profile": self.profile,
            "area": _quantity_dict(self.area),
            "perimeter": _quantity_dict(self.perimeter),
            "ptm": _quantity_dict(self.ptm),
            "steel_grade": self.steel_grade,
            "axial_force": _quantity_dict(self.axial_force),
            "critical_temperature": _quantity_dict(self.critical_temperature),
            "required_fire_resistance": _quantity_dict(
                self.required_fire_resistance
            ),
            "unprotected_fire_resistance": _quantity_dict(
                self.unprotected_fire_resistance
            ),
            "fireproofing_material": self.fireproofing_material,
            "fire_regime": self.fire_regime,
            "fireproofing_thickness": _quantity_dict(
                self.fireproofing_thickness
            ),
            "confirmed_fields": [field.as_dict() for field in self.confirmed_fields],
            "probable_fields": [field.as_dict() for field in self.probable_fields],
            "unknown_indices": list(self.unknown_indices),
            "provenance": {
                name: _provenance_dict(trace)
                for name, trace in self.provenance.items()
            },
            "source_file": self.source_file,
            "source_row": self.source_row,
        }


def _quantity_dict(value: Quantity | None) -> dict[str, str] | None:
    if value is None:
        return None
    return {"value": str(value.value), "unit": value.unit.value}


def _provenance_dict(value: ValueProvenance) -> dict[str, Any]:
    return {
        "kind": value.kind.value,
        "file": value.file,
        "sheet": value.sheet,
        "row": value.row,
        "field": value.field,
        "document": value.document,
        "clause": value.clause,
        "formula": value.formula,
        "date": value.date.isoformat() if value.date is not None else None,
    }


def _parse_field(record: Rx38Record, index: int) -> str | int | Decimal | None:
    raw = record.fields[index].strip()
    if not raw:
        return None
    spec = field_spec(index)
    if spec.data_type == "integer":
        try:
            return int(raw)
        except ValueError as exc:
            raise Rx3ResultError(
                f"RX38 field {index} ({spec.name}) is not an integer: {raw!r}"
            ) from exc
    if spec.data_type == "decimal":
        try:
            value = Decimal(raw.replace(",", "."))
        except InvalidOperation as exc:
            raise Rx3ResultError(
                f"RX38 field {index} ({spec.name}) is not decimal: {raw!r}"
            ) from exc
        if not value.is_finite():
            raise Rx3ResultError(
                f"RX38 field {index} ({spec.name}) must be finite"
            )
        return value
    return raw


def _trace(
    source_file: str,
    record: Rx38Record,
    index: int,
    timestamp: datetime | None,
) -> ValueProvenance:
    spec = field_spec(index)
    return ValueProvenance(
        ProvenanceType.RX3_RESULT,
        file=source_file,
        row=record.line_number,
        field=f"Tconstr[{index}]:{spec.name}",
        date=timestamp,
    )


def rx38_record_to_rx3_result(
    record: Rx38Record,
    *,
    source_file: str | Path,
    timestamp: datetime | None = None,
) -> Rx3Result:
    if record.record_type != "Tconstr" or len(record.fields) != TCONSTR_FIELD_COUNT:
        raise Rx3ResultError("Rx3Result requires one 200-field Tconstr record")
    source = str(source_file)
    confirmed: list[Rx3FieldValue] = []
    probable: list[Rx3FieldValue] = []
    by_index: dict[int, Rx3FieldValue] = {}
    for index, spec in sorted(FIELD_SPECS.items()):
        value = _parse_field(record, index)
        if value is None:
            continue
        trace = _trace(source, record, index, timestamp)
        field = Rx3FieldValue(
            index,
            spec.name,
            spec.confidence,
            value,
            spec.units,
            spec.direction,
            trace,
        )
        by_index[index] = field
        if spec.confidence == "confirmed":
            confirmed.append(field)
        elif spec.confidence == "probable":
            probable.append(field)

    provenance: dict[str, ValueProvenance] = {}

    def text(index: int, name: str) -> str | None:
        field = by_index.get(index)
        if field is None or field.confidence != "confirmed":
            return None
        provenance[name] = field.provenance
        return str(field.value)

    def quantity(index: int, name: str, unit: Unit) -> Quantity | None:
        field = by_index.get(index)
        if field is None or field.confidence != "confirmed":
            return None
        if not isinstance(field.value, Decimal):
            raise Rx3ResultError(
                f"Confirmed field {index} ({field.name}) is not decimal"
            )
        provenance[name] = field.provenance
        return Quantity.of(field.value, unit)

    return Rx3Result(
        mark=text(1, "mark"),
        profile=text(19, "profile"),
        area=quantity(20, "area", Unit.SQUARE_MILLIMETER),
        perimeter=quantity(21, "perimeter", Unit.MILLIMETER),
        ptm=quantity(22, "ptm", Unit.MILLIMETER),
        steel_grade=text(42, "steel_grade"),
        axial_force=quantity(49, "axial_force", Unit.KILONEWTON),
        critical_temperature=quantity(
            44, "critical_temperature", Unit.CELSIUS
        ),
        required_fire_resistance=quantity(
            55, "required_fire_resistance", Unit.MINUTE
        ),
        unprotected_fire_resistance=quantity(
            54, "unprotected_fire_resistance", Unit.MINUTE
        ),
        fireproofing_material=text(72, "fireproofing_material"),
        fire_regime=text(104, "fire_regime"),
        # No confirmed RX38 index currently represents coating thickness.
        fireproofing_thickness=None,
        confirmed_fields=tuple(confirmed),
        probable_fields=tuple(probable),
        unknown_indices=tuple(
            index
            for index in range(TCONSTR_FIELD_COUNT)
            if field_spec(index).confidence == "unknown"
        ),
        provenance=provenance,
        source_file=source,
        source_row=record.line_number,
    )


def read_rx3_results(path: str | Path) -> tuple[Rx3Result, ...]:
    source = Path(path).resolve(strict=True)
    timestamp = datetime.fromtimestamp(source.stat().st_mtime, timezone.utc)
    return tuple(
        rx38_record_to_rx3_result(
            record, source_file=source, timestamp=timestamp
        )
        for record in construction_records(read_rx38(source))
    )


def read_rx3_result(path: str | Path, *, mark: str | None = None) -> Rx3Result:
    results = read_rx3_results(path)
    matches = results if mark is None else tuple(item for item in results if item.mark == mark)
    if len(matches) != 1:
        raise Rx3ResultError(
            f"Expected one RX3 result for mark {mark!r}, found {len(matches)}"
        )
    return matches[0]


def apply_rx3_result(element: ProjectElement, result: Rx3Result) -> ProjectElement:
    """Validate RX3 identity/input echoes and attach confirmed output values."""

    conflicts: list[str] = []
    if result.mark is not None and result.mark != element.mark:
        conflicts.append(f"mark {result.mark!r} != {element.mark!r}")
    if (
        result.profile is not None
        and element.profile_name is not None
        and normalize_profile_name(result.profile)
        != normalize_profile_name(element.profile_name)
    ):
        conflicts.append(f"profile {result.profile!r} != {element.profile_name!r}")

    comparisons = (
        ("area", result.area, element.area, Unit.SQUARE_MILLIMETER, Decimal("0.001")),
        (
            "perimeter",
            result.perimeter,
            element.heated_perimeter,
            Unit.MILLIMETER,
            Decimal("0.001"),
        ),
        ("ptm", result.ptm, element.ptm, Unit.MILLIMETER, Decimal("0.001")),
        ("N", result.axial_force, element.N, Unit.KILONEWTON, Decimal("0.001")),
        (
            "required_fire_resistance",
            result.required_fire_resistance,
            element.required_fire_resistance,
            Unit.MINUTE,
            Decimal("0.001"),
        ),
    )
    for name, rx3_value, project_value, unit, tolerance in comparisons:
        if rx3_value is not None and project_value is not None:
            difference = abs(rx3_value.to(unit).value - project_value.to(unit).value)
            if difference > tolerance:
                conflicts.append(
                    f"{name} differs by {difference} {unit.value}"
                )
    if (
        result.steel_grade is not None
        and element.steel_grade is not None
        and result.steel_grade.casefold() != element.steel_grade.casefold()
    ):
        conflicts.append(
            f"steel_grade {result.steel_grade!r} != {element.steel_grade!r}"
        )
    if conflicts:
        raise Rx3ResultError("RX3 result conflicts with ProjectElement: " + "; ".join(conflicts))

    updates: dict[str, Any] = {}
    traces = dict(element.provenance)
    for project_name, result_name in (
        ("critical_temperature", "critical_temperature"),
        ("unprotected_fire_resistance", "unprotected_fire_resistance"),
    ):
        value = getattr(result, result_name)
        if value is not None:
            updates[project_name] = value
            traces[project_name] = result.provenance[result_name]
    return replace(element, provenance=traces, **updates)
