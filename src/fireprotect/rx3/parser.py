from __future__ import annotations

import csv
from dataclasses import dataclass, field, replace
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Iterable

from .schema import (
    CONFIRMED_INDICES,
    TCONSTR_FIELD_COUNT,
    WritePolicy,
    field_spec,
)


class Rx38FormatError(ValueError):
    pass


class UnsafeRx38WriteError(ValueError):
    pass


_PARSED_RX38_ORIGIN = object()


def _decimal(value: str) -> Decimal | None:
    value = value.strip()
    if not value:
        return None
    try:
        return Decimal(value.replace(",", "."))
    except InvalidOperation:
        return None


def _split_tokens(line: str) -> tuple[str, ...]:
    tokens: list[str] = []
    start = 0
    quoted = False
    i = 0
    while i < len(line):
        char = line[i]
        if char == '"':
            if quoted and i + 1 < len(line) and line[i + 1] == '"':
                i += 2
                continue
            quoted = not quoted
        elif char == ";" and not quoted:
            tokens.append(line[start:i])
            start = i + 1
        i += 1
    if quoted:
        raise Rx38FormatError("Unterminated quoted field")
    tokens.append(line[start:])
    return tuple(tokens)


@dataclass(frozen=True)
class Rx38Record:
    record_type: str
    fields: tuple[str, ...]
    raw_tokens: tuple[str, ...] = ()
    original_fields: tuple[str, ...] = ()
    newline: str = "\r\n"
    line_number: int | None = None
    _origin_token: object | None = field(default=None, repr=False, compare=False)
    _source_path: Path | None = field(default=None, repr=False, compare=False)
    _authorized_compatibility_changes: frozenset[tuple[int, str]] = field(
        default_factory=frozenset,
        repr=False,
        compare=False,
    )

    def field(self, index: int) -> str:
        return self.fields[index]

    def with_confirmed_field(self, index: int, value: str) -> "Rx38Record":
        """Write a direct-safe confirmed field without claiming compatibility."""

        return self.with_typed_field(index, value)

    def with_typed_field(
        self,
        index: int,
        value: str,
        *,
        compatibility_verified: bool = False,
    ) -> "Rx38Record":
        if not isinstance(compatibility_verified, bool):
            raise TypeError("compatibility_verified must be bool")
        if self.record_type != "Tconstr":
            raise UnsafeRx38WriteError("Only Tconstr records may be edited")
        if index not in CONFIRMED_INDICES:
            spec = field_spec(index)
            raise UnsafeRx38WriteError(
                f"Field {index} ({spec.name}) is not confirmed and cannot be edited safely"
            )
        spec = field_spec(index)
        if spec.write_policy is WritePolicy.RESULT_ONLY:
            raise UnsafeRx38WriteError(
                f"Field {index} ({spec.name}) is RESULT_ONLY and cannot be written as Rx3Input"
            )
        if spec.write_policy in {
            WritePolicy.READ_ONLY,
            WritePolicy.EXPERIMENTAL,
            WritePolicy.FORBIDDEN,
        }:
            raise UnsafeRx38WriteError(
                f"Field {index} ({spec.name}) write policy is {spec.write_policy.value}"
            )
        if (
            spec.write_policy is WritePolicy.SAFE_WITH_COMPATIBILITY_CHECK
        ):
            raise UnsafeRx38WriteError(
                f"Field {index} ({spec.name}) requires adapter-owned compatibility authorization; "
                "a boolean claim is not accepted"
            )
        updated = list(self.fields)
        updated[index] = str(value)
        return replace(self, fields=tuple(updated))

    @property
    def mark(self) -> str | None:
        return self.fields[1] if len(self.fields) > 1 and self.record_type == "Tconstr" else None

    @property
    def section_name(self) -> str | None:
        return self.fields[5] if len(self.fields) > 5 and self.record_type == "Tconstr" else None

    @property
    def standard(self) -> str | None:
        return self.fields[17] if len(self.fields) > 17 and self.record_type == "Tconstr" else None

    @property
    def profile(self) -> str | None:
        return self.fields[19] if len(self.fields) > 19 and self.record_type == "Tconstr" else None

    @property
    def area_mm2(self) -> Decimal | None:
        return _decimal(self.fields[20]) if len(self.fields) > 20 and self.record_type == "Tconstr" else None

    @property
    def perimeter_mm(self) -> Decimal | None:
        return _decimal(self.fields[21]) if len(self.fields) > 21 and self.record_type == "Tconstr" else None

    @property
    def ptm_mm(self) -> Decimal | None:
        return _decimal(self.fields[22]) if len(self.fields) > 22 and self.record_type == "Tconstr" else None

    @property
    def section_factor_1_per_m(self) -> Decimal | None:
        return _decimal(self.fields[23]) if len(self.fields) > 23 and self.record_type == "Tconstr" else None


def _with_compatibility_checked_field(
    record: Rx38Record,
    index: int,
    value: str,
) -> Rx38Record:
    """Apply one field after the owning adapter has completed its checks."""

    if record.record_type != "Tconstr":
        raise UnsafeRx38WriteError("Only Tconstr records may be edited")
    spec = field_spec(index)
    if (
        index not in CONFIRMED_INDICES
        or spec.write_policy is not WritePolicy.SAFE_WITH_COMPATIBILITY_CHECK
    ):
        raise UnsafeRx38WriteError(
            f"Field {index} ({spec.name}) is not compatibility-authorized"
        )
    serialized = str(value)
    updated = list(record.fields)
    updated[index] = serialized
    authorized = set(record._authorized_compatibility_changes)
    authorized.add((index, serialized))
    return replace(
        record,
        fields=tuple(updated),
        _authorized_compatibility_changes=frozenset(authorized),
    )


@dataclass(frozen=True)
class Rx38Document:
    records: tuple[Rx38Record, ...]
    encoding: str = "utf-8"
    has_bom: bool = False
    source: Path | None = None

    def replace_record(self, record_index: int, record: Rx38Record) -> "Rx38Document":
        records = list(self.records)
        records[record_index] = record
        return replace(self, records=tuple(records))


@dataclass(frozen=True)
class Rx38Construction:
    mark: str
    section_type: str
    height_mm: Decimal
    width_mm: Decimal
    web_thickness_mm: Decimal
    flange_thickness_mm: Decimal
    length_m: Decimal
    quantity: Decimal
    profile_standard: str
    profile_name: str
    area_mm2: Decimal
    heated_perimeter_mm: Decimal
    ptm_mm: Decimal
    section_factor_per_m: Decimal
    steel_density_kg_m3: Decimal
    steel_yield_strength_mpa: Decimal
    steel_elastic_modulus_mpa: Decimal
    steel_grade: str
    critical_temperature_c: Decimal
    stress_state: str
    support_condition: str | None
    axial_force_kn: Decimal
    effective_length_m: Decimal
    load_level_mu0: Decimal
    unprotected_fire_resistance_min: Decimal
    required_fire_resistance_min: Decimal
    mass_one_kg: Decimal
    mass_total_kg: Decimal
    fireproofing_material: str
    fire_regime: str
    box_heated_perimeter_mm: Decimal
    box_ptm_mm: Decimal
    box_section_factor_per_m: Decimal
    effective_length_factor: Decimal
    steel_temperature_model: str
    raw_fields: tuple[str, ...]

    @classmethod
    def from_record(cls, record: Rx38Record) -> "Rx38Construction":
        if record.record_type != "Tconstr" or len(record.fields) != TCONSTR_FIELD_COUNT:
            raise Rx38FormatError("Rx38Construction requires a 200-field Tconstr record")

        def required_decimal(index: int) -> Decimal:
            value = _decimal(record.fields[index])
            if value is None:
                raise Rx38FormatError(f"Field {index} ({field_spec(index).name}) is not numeric")
            return value

        return cls(
            mark=record.fields[1], section_type=record.fields[5],
            height_mm=required_decimal(8), width_mm=required_decimal(9),
            web_thickness_mm=required_decimal(11), flange_thickness_mm=required_decimal(13),
            length_m=required_decimal(14), quantity=required_decimal(15),
            profile_standard=record.fields[17], profile_name=record.fields[19],
            area_mm2=required_decimal(20), heated_perimeter_mm=required_decimal(21),
            ptm_mm=required_decimal(22), section_factor_per_m=required_decimal(23),
            steel_density_kg_m3=required_decimal(32), steel_yield_strength_mpa=required_decimal(33),
            steel_elastic_modulus_mpa=required_decimal(34), steel_grade=record.fields[42],
            critical_temperature_c=required_decimal(44), stress_state=record.fields[45],
            support_condition=record.fields[48] or None, axial_force_kn=required_decimal(49),
            effective_length_m=required_decimal(51), load_level_mu0=required_decimal(52),
            unprotected_fire_resistance_min=required_decimal(54), required_fire_resistance_min=required_decimal(55),
            mass_one_kg=required_decimal(66), mass_total_kg=required_decimal(67),
            fireproofing_material=record.fields[72], fire_regime=record.fields[104],
            box_heated_perimeter_mm=required_decimal(86), box_ptm_mm=required_decimal(113),
            box_section_factor_per_m=required_decimal(114), effective_length_factor=required_decimal(141),
            steel_temperature_model=record.fields[188], raw_fields=record.fields,
        )


def _decode(data: bytes) -> tuple[str, str, bool]:
    if data.startswith(b"\xef\xbb\xbf"):
        return data[3:].decode("utf-8"), "utf-8", True
    try:
        return data.decode("utf-8"), "utf-8", False
    except UnicodeDecodeError:
        return data.decode("cp1251"), "cp1251", False


def read_rx38_document(path: str | Path) -> Rx38Document:
    path = Path(path).resolve(strict=True)
    text, encoding, has_bom = _decode(path.read_bytes())
    records: list[Rx38Record] = []
    for line_number, raw_line in enumerate(text.splitlines(keepends=True), 1):
        if raw_line.endswith("\r\n"):
            content, newline = raw_line[:-2], "\r\n"
        elif raw_line.endswith(("\r", "\n")):
            content, newline = raw_line[:-1], raw_line[-1]
        else:
            content, newline = raw_line, ""
        if not content:
            continue
        try:
            row = next(csv.reader([content], delimiter=";", quotechar='"'))
        except csv.Error as exc:
            raise Rx38FormatError(f"{path}:{line_number}: {exc}") from exc
        tokens = _split_tokens(content)
        if len(tokens) != len(row):
            raise Rx38FormatError(f"{path}:{line_number}: token/field count mismatch")
        if row[0] == "Tconstr" and len(row) != TCONSTR_FIELD_COUNT:
            raise Rx38FormatError(
                f"{path}:{line_number}: Tconstr has {len(row)} fields, expected {TCONSTR_FIELD_COUNT}"
            )
        fields = tuple(row)
        records.append(
            Rx38Record(
                row[0],
                fields,
                tokens,
                fields,
                newline,
                line_number,
                _origin_token=_PARSED_RX38_ORIGIN,
                _source_path=path,
            )
        )
    return Rx38Document(tuple(records), encoding, has_bom, path)


def read_rx38(path: str | Path) -> list[Rx38Record]:
    return list(read_rx38_document(path).records)


def _serialize_token(value: str, original_token: str) -> str:
    was_quoted = len(original_token) >= 2 and original_token.startswith('"') and original_token.endswith('"')
    must_quote = was_quoted or any(char in value for char in ';"\r\n') or any(char.isspace() for char in value)
    escaped = value.replace('"', '""')
    return f'"{escaped}"' if must_quote else escaped


def write_rx38(document: Rx38Document, path: str | Path) -> None:
    destination = Path(path).resolve(strict=False)
    source_paths = {
        record._source_path.resolve(strict=False)
        for record in document.records
        if record._source_path is not None
    }
    if document.source is not None:
        source_paths.add(document.source.resolve(strict=False))
    if destination in source_paths:
        raise UnsafeRx38WriteError("Writer refuses to overwrite the source RX38")
    if destination.exists():
        raise UnsafeRx38WriteError(
            f"Writer refuses to overwrite an existing RX38: {destination}"
        )
    lines: list[str] = []
    for record in document.records:
        if record.record_type == "Tconstr" and (
            record._origin_token is not _PARSED_RX38_ORIGIN
            or not record.original_fields
        ):
            raise UnsafeRx38WriteError(
                "Safe Tconstr writing requires an immutable parser-origin baseline"
            )
        original = record.original_fields or record.fields
        if len(record.fields) != len(original):
            raise UnsafeRx38WriteError("Writer cannot add or remove positional fields")
        if record.record_type == "Tconstr" and len(record.fields) != TCONSTR_FIELD_COUNT:
            raise UnsafeRx38WriteError("Writer refuses non-200-field Tconstr records")
        changed = {i for i, (old, new) in enumerate(zip(original, record.fields)) if old != new}
        unsafe = {
            index
            for index in changed
            if index not in CONFIRMED_INDICES
            or field_spec(index).write_policy
            in {
                WritePolicy.RESULT_ONLY,
                WritePolicy.READ_ONLY,
                WritePolicy.EXPERIMENTAL,
                WritePolicy.FORBIDDEN,
            }
            or (
                field_spec(index).write_policy
                is WritePolicy.SAFE_WITH_COMPATIBILITY_CHECK
                and (index, record.fields[index])
                not in record._authorized_compatibility_changes
            )
        }
        if unsafe:
            names = ", ".join(
                f"{i}:{field_spec(i).name}:{field_spec(i).write_policy.value}"
                for i in sorted(unsafe)
            )
            raise UnsafeRx38WriteError(f"Refusing unsafe field changes: {names}")
        tokens = record.raw_tokens or tuple(_serialize_token(v, "") for v in original)
        serialized = [
            token if i not in changed else _serialize_token(record.fields[i], token)
            for i, token in enumerate(tokens)
        ]
        lines.append(";".join(serialized) + record.newline)
    text = "".join(lines)
    payload = text.encode(document.encoding)
    if document.has_bom and document.encoding == "utf-8":
        payload = b"\xef\xbb\xbf" + payload
    try:
        with destination.open("xb") as stream:
            stream.write(payload)
    except FileExistsError as exc:
        raise UnsafeRx38WriteError(
            f"Writer refuses to overwrite an existing RX38: {destination}"
        ) from exc


def construction_records(records: Iterable[Rx38Record]) -> list[Rx38Record]:
    return [record for record in records if record.record_type == "Tconstr"]
