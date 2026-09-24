"""Safe copy-only integration with existing Excel workbooks."""

from .mapping import (
    CellBinding,
    ColumnBinding,
    FieldResolver,
    WorkbookMapping,
    resolve_field,
)
from .project import write_project_elements_copy
from .registry import (
    ExcelTemplateEntry,
    ExcelTemplateRegistry,
    ExcelTemplateRegistryError,
    ExcelTemplateStatus,
    ExcelTemplateVerification,
    formula_map_fingerprint,
    verify_excel_template,
)
from .obm import (
    ExcelCellChange,
    ObmWorkbookExportError,
    ObmWorkbookExportReport,
    export_obm_workbook,
)
from .review import (
    REVIEW_KIND,
    STATUS_NOTE,
    ReviewWorkbookError,
    export_lira_bar_review,
)
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
    "ExcelTemplateEntry",
    "ExcelTemplateRegistry",
    "ExcelTemplateRegistryError",
    "ExcelTemplateStatus",
    "ExcelTemplateVerification",
    "FieldResolver",
    "ExcelCellChange",
    "FormulaOverwriteError",
    "SourceWorkbookChangedError",
    "ObmWorkbookExportError",
    "ObmWorkbookExportReport",
    "REVIEW_KIND",
    "ReviewWorkbookError",
    "STATUS_NOTE",
    "WorkbookMapping",
    "WorkbookMappingError",
    "file_sha256",
    "formula_map_fingerprint",
    "export_lira_bar_review",
    "export_obm_workbook",
    "resolve_field",
    "write_mapped_copy",
    "write_project_elements_copy",
    "verify_excel_template",
]
