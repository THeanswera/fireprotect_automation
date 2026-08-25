"""Mapping and SI-normalization pipeline for LIRA row sources."""

from __future__ import annotations

from decimal import Decimal, InvalidOperation
from typing import Any

from .errors import LiraMappingError, LiraRowError
from .mapping import LiraColumnMapping
from .types import (
    FORCE_FIELDS,
    ForceUnits,
    LiraForceRow,
    LiraRowSource,
    RawTableRow,
    SourceForceValues,
)
from .units import to_si


class LiraForceImporter:
    """Convert any :class:`LiraRowSource` to neutral SI force rows."""

    def __init__(self, mapping: LiraColumnMapping) -> None:
        if not isinstance(mapping, LiraColumnMapping):
            raise LiraMappingError("an explicit LiraColumnMapping is required")
        self.mapping = mapping

    def import_source(self, source: LiraRowSource) -> list[LiraForceRow]:
        raw_rows = source.read_rows()
        if not raw_rows:
            return []
        missing_headers = self.mapping.source_headers - set(raw_rows[0].values)
        if missing_headers:
            raise LiraMappingError(
                f"source table is missing mapped headers: {sorted(missing_headers)}"
            )
        return [self._convert_row(row) for row in raw_rows]

    def _convert_row(self, row: RawTableRow) -> LiraForceRow:
        columns = self.mapping.columns
        raw_forces = {
            field: self._number(row, columns[field], field) for field in FORCE_FIELDS
        }
        source_units = self.mapping.units
        si_forces = {
            field: to_si(field, raw_forces[field], getattr(source_units, field))
            for field in FORCE_FIELDS
        }
        source_values = SourceForceValues(
            **raw_forces,
            units=ForceUnits(**source_units.as_dict()),
        )
        return LiraForceRow(
            element_id=self._required_text(
                row, columns["element_id"], "element_id"
            ),
            section=self._required_text(row, columns["section"], "section"),
            load_case=self._required_text(
                row, columns["load_case"], "load_case"
            ),
            combination=self._required_text(
                row, columns["combination"], "combination"
            ),
            N=si_forces["N"],
            Mx=si_forces["Mx"],
            My=si_forces["My"],
            Qx=si_forces["Qx"],
            Qy=si_forces["Qy"],
            source=source_values,
            source_row=row.row_number,
        )

    @staticmethod
    def _required_text(row: RawTableRow, header: str, field: str) -> str:
        value = row.values.get(header)
        if value is None or not str(value).strip():
            raise LiraRowError(
                f"row {row.row_number}, field {field}: value is required"
            )
        if isinstance(value, float) and value.is_integer():
            return str(int(value))
        return str(value).strip()

    def _number(self, row: RawTableRow, header: str, field: str) -> Decimal:
        value: Any = row.values.get(header)
        if value is None or (isinstance(value, str) and not value.strip()):
            raise LiraRowError(
                f"row {row.row_number}, field {field}: numeric value is required"
            )
        try:
            if isinstance(value, str):
                normalized = value.strip()
                if self.mapping.thousands_separator is not None:
                    normalized = normalized.replace(self.mapping.thousands_separator, "")
                if self.mapping.decimal_separator == ",":
                    normalized = normalized.replace(",", ".")
                number = Decimal(normalized)
            else:
                number = Decimal(str(value))
        except (InvalidOperation, TypeError, ValueError) as exc:
            raise LiraRowError(
                f"row {row.row_number}, field {field}: {value!r} is not a number"
            ) from exc
        if not number.is_finite():
            raise LiraRowError(
                f"row {row.row_number}, field {field}: value must be finite"
            )
        return number
