"""Declarative mappings from model-like objects to existing Excel cells."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from typing import Any, Callable, Mapping, Protocol, TypeAlias


ExcelScalar: TypeAlias = str | int | float | bool | Decimal | date | datetime | None


class FieldResolver(Protocol):
    """Resolve a dotted field path without coupling Excel code to ``model.py``."""

    def __call__(self, source: object, field_path: str, /) -> Any: ...


ValueTransform: TypeAlias = Callable[[Any], ExcelScalar]


def resolve_field(source: object, field_path: str, /) -> Any:
    """Resolve ``a.b`` through mapping keys and/or object attributes.

    ``ProjectElement`` instances, dataclasses and plain dictionaries therefore
    use the same mapping.  Unit conversion remains explicit: a caller can map
    ``length.value`` only when that magnitude is already in the workbook unit,
    or supply a binding ``transform``.
    """

    value: Any = source
    for part in field_path.split("."):
        if not part:
            raise ValueError(f"Invalid empty segment in field path {field_path!r}")
        if isinstance(value, Mapping):
            try:
                value = value[part]
            except KeyError as exc:
                raise KeyError(
                    f"Field path {field_path!r} is missing mapping key {part!r}"
                ) from exc
        else:
            try:
                value = getattr(value, part)
            except AttributeError as exc:
                raise AttributeError(
                    f"Field path {field_path!r} is missing attribute {part!r}"
                ) from exc
    return value


@dataclass(frozen=True, slots=True)
class CellBinding:
    """Map one field from the singleton source object to one existing cell."""

    field: str
    sheet: str
    cell: str
    transform: ValueTransform | None = None
    write_none: bool = False

    def __post_init__(self) -> None:
        if not self.field.strip():
            raise ValueError("CellBinding.field must not be empty")
        if not self.sheet:
            raise ValueError("CellBinding.sheet must not be empty")
        if not self.cell:
            raise ValueError("CellBinding.cell must not be empty")


@dataclass(frozen=True, slots=True)
class ColumnBinding:
    """Map a field from each source object into consecutive existing rows."""

    field: str
    sheet: str
    column: str
    first_row: int
    last_row: int | None = None
    transform: ValueTransform | None = None
    write_none: bool = False

    def __post_init__(self) -> None:
        if not self.field.strip():
            raise ValueError("ColumnBinding.field must not be empty")
        if not self.sheet:
            raise ValueError("ColumnBinding.sheet must not be empty")
        if not self.column:
            raise ValueError("ColumnBinding.column must not be empty")
        if isinstance(self.first_row, bool) or self.first_row < 1:
            raise ValueError("ColumnBinding.first_row must be a positive integer")
        if self.last_row is not None and (
            isinstance(self.last_row, bool) or self.last_row < self.first_row
        ):
            raise ValueError("ColumnBinding.last_row must be >= first_row")


@dataclass(frozen=True, slots=True)
class WorkbookMapping:
    """Complete mapping for fixed workbook cells and repeated table columns."""

    cells: tuple[CellBinding, ...] = ()
    columns: tuple[ColumnBinding, ...] = ()
