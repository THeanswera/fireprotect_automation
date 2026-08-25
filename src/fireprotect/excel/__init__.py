"""Safe copy-only integration with existing Excel workbooks."""

from .mapping import (
    CellBinding,
    ColumnBinding,
    FieldResolver,
    WorkbookMapping,
    resolve_field,
)
from .project import write_project_elements_copy
from .writer import (
    CopyOnlyViolationError,
    ExcelCopyResult,
    ExcelExportError,
    FormulaOverwriteError,
    SourceWorkbookChangedError,
    WorkbookMappingError,
    file_sha256,
    write_mapped_copy,
)

__all__ = [
    "CellBinding",
    "ColumnBinding",
    "CopyOnlyViolationError",
    "ExcelCopyResult",
    "ExcelExportError",
    "FieldResolver",
    "FormulaOverwriteError",
    "SourceWorkbookChangedError",
    "WorkbookMapping",
    "WorkbookMappingError",
    "file_sha256",
    "resolve_field",
    "write_mapped_copy",
    "write_project_elements_copy",
]
