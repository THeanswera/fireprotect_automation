"""Typed ProjectElement facade over the copy-only workbook writer."""

from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path

from ..model import ProjectElement
from .mapping import WorkbookMapping
from .writer import ExcelCopyResult, write_mapped_copy


def write_project_elements_copy(
    source_path: str | Path,
    output_path: str | Path,
    elements: Iterable[ProjectElement],
    mapping: WorkbookMapping,
    *,
    overwrite: bool = False,
) -> ExcelCopyResult:
    """Write ``ProjectElement`` values into an explicitly mapped workbook copy.

    The mapping is mandatory because workbook columns are project-template
    semantics, not domain defaults. In particular, this function never guesses
    how one project quantity should be split between several Excel inputs.
    """

    items = tuple(elements)
    invalid = [index for index, item in enumerate(items) if not isinstance(item, ProjectElement)]
    if invalid:
        raise TypeError(
            "elements must contain only ProjectElement instances; invalid indexes: "
            + ", ".join(map(str, invalid))
        )
    return write_mapped_copy(
        source_path,
        output_path,
        mapping,
        elements=items,
        overwrite=overwrite,
    )
