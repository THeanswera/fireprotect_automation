"""Auditable, read-only RSU rows and one explicit engineer declaration."""

from __future__ import annotations

import csv
from decimal import Decimal, InvalidOperation
from hashlib import sha256
import json
from pathlib import Path
from typing import Any, Mapping, Sequence

from .errors import LiraFormatError, LiraMappingError
from .rsu import (
    EXPORT_PRECISION_BOUND_STATEMENT,
    RSU_FORCE_COMPONENTS,
    RsuCoefficient,
    RsuImportBundle,
    RsuLoadForceRecord,
    RsuSourceValue,
    RsuValidationReport,
    RsuValidationStatus,
)
from .xls import XlsWorkbook

DECLARATION_KIND = "RSU_ENGINEER_GOVERNING_ROW_DECLARATION"
ROW_DETAIL_KIND = "READ_ONLY_LIRA_RSU_ROW_DETAIL"
RESIDUAL_STATISTICS_KIND = "READ_ONLY_LIRA_RSU_RESIDUAL_STATISTICS"


def rsu_residual_statistics(report: RsuValidationReport) -> dict[str, Any]:
    """Describe the reconstruction residuals under the stated export-precision rule.

    The bound counted here is the acceptance rule of
    ``RsuValidationStatus.VERIFIED_WITHIN_EXPORT_PRECISION`` (see
    :data:`fireprotect.lira.rsu.EXPORT_PRECISION_BOUND_STATEMENT`): half a unit of
    the last printed digit of each compared value plus one single-precision ulp of
    the largest magnitude involved.  ``VERIFIED`` keeps the stricter meaning of
    exact equality; a residual beyond the bound blocks the row.  No value, sign,
    cell or coefficient is changed anywhere.
    """

    components: dict[str, dict[str, Any]] = {}
    for result in report.results:
        for item in result.components:
            if item.difference == 0:
                continue
            comparison = item.export_precision
            if comparison is None:  # pragma: no cover - components always carry it
                continue
            entry = components.setdefault(
                item.component,
                {
                    "mismatches": 0,
                    "within_export_precision": 0,
                    "beyond_export_precision": 0,
                    "max_abs_difference": Decimal(0),
                    "max_excess_in_ulp32": Decimal(0),
                    "worst_observed": None,
                },
            )
            entry["mismatches"] += 1
            difference = abs(item.difference)
            entry["max_abs_difference"] = max(
                entry["max_abs_difference"], difference
            )
            if comparison.within:
                entry["within_export_precision"] += 1
                continue
            entry["beyond_export_precision"] += 1
            ratio = comparison.excess_in_ulp32
            if ratio is not None and ratio > entry["max_excess_in_ulp32"]:
                entry["max_excess_in_ulp32"] = ratio
                entry["worst_observed"] = (
                    f"element {result.published_record.element_id} "
                    f"station {result.published_record.section_station} "
                    f"residual {item.difference} "
                    f"excess {comparison.excess} = {ratio:.3f} ulp32 "
                    "beyond the export-precision bound"
                )
    return {
        "kind": RESIDUAL_STATISTICS_KIND,
        "acceptance": {
            "exact_status": RsuValidationStatus.VERIFIED.value,
            "bounded_status": (
                RsuValidationStatus.VERIFIED_WITHIN_EXPORT_PRECISION.value
            ),
            "bound": EXPORT_PRECISION_BOUND_STATEMENT,
            "exact_equality_required_for_verified": True,
            "numeric_policy_installed": True,
            "policy_scope": (
                "only the row status of the RSU reconstruction; every comparison "
                "value, sign, cell, coefficient and hash is kept exact, and no "
                "tolerance is applied to forces written anywhere else"
            ),
            "approved_by": "engineer decision (option A) recorded in docs/OPEN_QUESTIONS.md",
            "note": (
                "a residual inside the bound is usable as printed export data; a "
                "residual beyond it blocks the row, which is what a wrong mapping, "
                "coefficient or row produces by orders of magnitude"
            ),
        },
        "counts": {
            "rows": len(report.results),
            "exact_rows": sum(
                1
                for result in report.results
                if result.status is RsuValidationStatus.VERIFIED
            ),
            "within_export_precision_rows": report.bounded_rows,
            "blocked_rows": sum(
                1
                for result in report.results
                if result.status is RsuValidationStatus.BLOCKED
            ),
            "exact_components": report.matching_components,
            "within_export_precision_components": report.bounded_components,
            "compared_components": report.component_comparisons,
        },
        "components": {
            name: {
                "mismatches": entry["mismatches"],
                "within_export_precision": entry["within_export_precision"],
                "beyond_export_precision": entry["beyond_export_precision"],
                "max_abs_difference": str(entry["max_abs_difference"]),
                "max_excess_in_ulp32": f"{entry['max_excess_in_ulp32']:.3f}",
                "worst_observed": entry["worst_observed"],
            }
            for name, entry in sorted(components.items())
        },
        "overall": {
            "components_beyond_bound": sum(
                entry["beyond_export_precision"] for entry in components.values()
            ),
            "reading": (
                "residuals inside the bound are explained by the printed precision "
                "of the export; a residual beyond the bound cannot be explained that "
                "way and blocks the row"
            ),
        },
    }


def _decimal_places(value: Decimal) -> int:
    exponent = value.as_tuple().exponent
    return -exponent if isinstance(exponent, int) and exponent < 0 else 0


def _row_value(value: RsuSourceValue) -> dict[str, object]:
    """One value as stored plus the number of stored decimal places."""

    detail = _value(value)
    detail["decimal_places"] = _decimal_places(value.value)
    return detail


def _coefficient_detail(item: RsuCoefficient) -> dict[str, object]:
    return {
        "load_case_id": item.load_case_id,
        "column_number": item.column_number,
        "header": item.source_header,
        "coefficient": str(item.coefficient),
        "decimal_places": _decimal_places(item.coefficient),
        "sheet": item.source_sheet,
        "row": item.source_row,
        "cell": item.source_cell,
        "source_sha256": item.source_sha256,
        "raw_token": item.raw_token,
        "decimal_provenance": item.decimal_provenance,
    }


def rsu_row_detail(
    bundle: RsuImportBundle, report: RsuValidationReport, row_ids: Sequence[str]
) -> dict[str, Any]:
    """Exact source view of explicitly requested published RSU rows.

    The view only re-uses the existing import bundle and reconstruction report:
    it never re-reads a CSV or JSON as evidence, never renumbers rows and never
    repairs a value.
    """

    if len(bundle.published_records) != len(report.results):
        raise LiraFormatError("RSU report does not match the imported bundle")
    forces: dict[tuple[str, str, str], list[RsuLoadForceRecord]] = {}
    for record in bundle.force_records:
        forces.setdefault(
            (record.element_id, record.section_station, record.load_case_id), []
        ).append(record)
    coefficients: dict[tuple[str, int], list[RsuCoefficient]] = {}
    for item in bundle.coefficients:
        coefficients.setdefault(
            (item.load_case_id, item.column_number), []
        ).append(item)
    pages = {book.source_sha256: book.source_file for book in bundle.forces_workbooks}
    by_row_id = {
        f"R{position:04d}": position for position, _ in enumerate(report.results, 1)
    }
    details: list[dict[str, Any]] = []
    for row_id in row_ids:
        position = by_row_id.get(row_id)
        if position is None:
            details.append(
                {
                    "row_id": row_id,
                    "found": False,
                    "reason": "no published RSU row carries this row_id",
                }
            )
            continue
        result = report.results[position - 1]
        published = result.published_record
        terms: list[dict[str, Any]] = []
        completeness: list[dict[str, Any]] = []
        for load_case in published.load_case_membership:
            matches = forces.get(
                (published.element_id, published.section_station, load_case), []
            )
            coefficient_rows = coefficients.get(
                (load_case, published.rsu_column_number), []
            )
            term: dict[str, Any] = {
                "load_case_id": load_case,
                "force_records": len(matches),
                "coefficient_records": len(coefficient_rows),
            }
            if len(matches) == 1:
                record = matches[0]
                term["force"] = {
                    "sheet": record.source_sheet,
                    "row": record.source_row,
                    "source_sha256": record.source_sha256,
                    "page": pages.get(record.source_sha256),
                    "values": {
                        name: _row_value(record.vector.values[name])
                        for name in RSU_FORCE_COMPONENTS
                    },
                }
            if len(coefficient_rows) == 1:
                term["coefficient"] = _coefficient_detail(coefficient_rows[0])
            if len(matches) != 1 or len(coefficient_rows) != 1:
                completeness.append(term)
            terms.append(term)
        details.append(
            {
                "row_id": row_id,
                "found": True,
                "status": result.status.value,
                "blockers": list(result.blockers),
                "identity": {
                    "element_id": published.element_id,
                    "section_station": published.section_station,
                    "rsu_group": published.rsu_group,
                    "rsu_criterion": published.rsu_criterion,
                    "rsu_column_number": published.rsu_column_number,
                    "load_case_membership": list(published.load_case_membership),
                },
                "published_source": {
                    "sheet": published.source_sheet,
                    "row": published.source_row,
                    "sha256": published.source_sha256,
                    "mapping_fingerprint": published.mapping_fingerprint,
                },
                "published_vector": {
                    name: _row_value(published.vector.values[name])
                    for name in RSU_FORCE_COMPONENTS
                },
                "terms": terms,
                "load_case_incomplete": completeness,
                "reconstruction": {
                    item.component: {
                        "published": str(item.published),
                        "reconstructed": str(item.reconstructed),
                        "difference": str(item.difference),
                        "difference_decimal_places": _decimal_places(item.difference),
                        "export_precision": (
                            None
                            if item.export_precision is None
                            else item.export_precision.as_dict()
                        ),
                    }
                    for item in result.components
                },
            }
        )
    return {
        "kind": ROW_DETAIL_KIND,
        "mapping_fingerprint": bundle.mapping_fingerprint,
        "force_pages": force_page_detail(bundle),
        "rows": details,
        "rx38_force_generation_allowed": False,
        "issue_readiness": "NOT_READY_FOR_ISSUE",
    }


def _hash(path: Path) -> str:
    digest = sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _value(value: RsuSourceValue) -> dict[str, object]:
    return {
        "value": str(value.value),
        "unit": value.source_unit,
        "header": value.source_header,
        "sheet": value.source_sheet,
        "row": value.source_row,
        "cell": value.source_cell,
        "source_sha256": value.source_sha256,
        "raw_token": value.raw_token,
        "decimal_provenance": value.decimal_provenance,
    }


def _sorted_tokens(values: set[str]) -> list[str]:
    """Sort numeric-looking tokens numerically and the rest lexicographically."""

    def key(token: str) -> tuple[int, int, str]:
        try:
            return (0, int(token), "")
        except ValueError:
            return (1, 0, token)

    return sorted(values, key=key)


def force_page_summary(
    book: XlsWorkbook, records: Sequence[RsuLoadForceRecord]
) -> dict[str, object]:
    """Aggregate one force page from its own records, nothing invented.

    This is the single definition of a page summary: the evidence writer records
    it and the evidence reader recomputes it from the re-read page and compares
    every key with the recorded expectation.  A page that produced no record, or
    a record whose sheet is not in the workbook, is refused.

    Recomputing with the same function detects a doctored recorded summary or a
    substituted page file; it cannot detect a shared defect in this aggregate.
    """

    if not records:
        raise LiraFormatError(
            f"force page {book.source_file} produced no record; a page without "
            "records is refused instead of being recorded as part of the table"
        )
    sheet_names = [sheet.name for sheet in book.worksheets]
    sheets_with_records = {record.source_sheet for record in records}
    unknown_sheets = sorted(
        str(name) for name in sheets_with_records if name not in sheet_names
    )
    if unknown_sheets:
        raise LiraFormatError(
            f"force page {book.source_file} has records from sheets that are "
            f"not in the workbook: {', '.join(unknown_sheets)}"
        )
    elements: list[int] = []
    for record in records:
        try:
            elements.append(int(record.element_id))
        except ValueError:
            continue
    return {
        "sheets": sheet_names,
        "sheets_with_records": [
            name for name in sheet_names if name in sheets_with_records
        ],
        "records": len(records),
        "elements_min": min(elements) if elements else None,
        "elements_max": max(elements) if elements else None,
        "load_cases": _sorted_tokens({record.load_case_id for record in records}),
        "section_stations": _sorted_tokens(
            {record.section_station for record in records}
        ),
    }


def force_page_detail(bundle: RsuImportBundle) -> list[dict[str, object]]:
    """Describe every imported force page from its own records, nothing invented.

    The order is the order the pages were given to the importer.
    """

    records_by_page: dict[str, list[RsuLoadForceRecord]] = {}
    for record in bundle.force_records:
        records_by_page.setdefault(record.source_sha256, []).append(record)
    pages: list[dict[str, object]] = []
    for book in bundle.forces_workbooks:
        records = records_by_page.get(book.source_sha256, [])
        pages.append(
            {
                "path": book.source_file,
                "sha256": book.source_sha256,
                **force_page_summary(book, records),
            }
        )
    return pages


def rsu_evidence(bundle: RsuImportBundle, report: RsuValidationReport) -> dict[str, Any]:
    """Keep all published vectors and exact reconstruction terms together.

    A force table exported by LIRA in several pages is recorded as a paged
    ``sources["forces"]`` entry: the first page keeps the flat ``path``/``sha256``
    meaning and every page is listed under ``pages`` with its own SHA-256 and the
    element/section/load-case coverage read from its records.  A single-page
    import keeps the previous shape byte for byte.
    """

    if len(bundle.published_records) != len(report.results):
        raise LiraFormatError("RSU report does not match the imported bundle")
    sources = {
        name: {"path": book.source_file, "sha256": book.source_sha256,
               "sheets": len(book.worksheets)}
        for name, book in (
            ("forces", bundle.forces_workbooks[0]),
            ("published", bundle.published_workbook),
            ("coefficients", bundle.coefficients_workbook),
            ("parameters", bundle.parameters_workbook),
        )
    }
    if len(bundle.forces_workbooks) > 1:
        pages = force_page_detail(bundle)
        sources["forces"]["pages"] = pages
        sources["forces"]["page_count"] = len(pages)
    forces: dict[tuple[str, str, str], list[RsuLoadForceRecord]] = {}
    for row in bundle.force_records:
        forces.setdefault((row.element_id, row.section_station, row.load_case_id), []).append(row)
    coefficients: dict[tuple[str, int], list[RsuCoefficient]] = {}
    for item in bundle.coefficients:
        coefficients.setdefault((item.load_case_id, item.column_number), []).append(item)
    rows: list[dict[str, object]] = []
    for index, result in enumerate(report.results, 1):
        published = result.published_record
        if published is not bundle.published_records[index - 1]:
            raise LiraFormatError("RSU report row order differs from the imported bundle")
        terms = []
        for load_case in published.load_case_membership:
            source_rows = forces.get((published.element_id, published.section_station, load_case), [])
            coefficient_rows = coefficients.get((load_case, published.rsu_column_number), [])
            if len(source_rows) == 1 and len(coefficient_rows) == 1:
                force, coefficient = source_rows[0], coefficient_rows[0]
                terms.append({
                    "load_case_id": load_case,
                    "force_sheet": force.source_sheet,
                    "force_row": force.source_row,
                    "forces": {name: _value(force.vector.values[name]) for name in RSU_FORCE_COMPONENTS},
                    "coefficient": str(coefficient.coefficient),
                    "coefficient_source": {
                        "sheet": coefficient.source_sheet,
                        "row": coefficient.source_row,
                        "cell": coefficient.source_cell,
                        "header": coefficient.source_header,
                        "source_sha256": coefficient.source_sha256,
                        "raw_token": coefficient.raw_token,
                        "decimal_provenance": coefficient.decimal_provenance,
                    },
                })
        rows.append({
            "row_id": f"R{index:04d}",
            "status": result.status.value,
            "blockers": list(result.blockers),
            "identity": {
                "element_id": published.element_id,
                "section_station": published.section_station,
                "rsu_group": published.rsu_group,
                "rsu_criterion": published.rsu_criterion,
                "rsu_column_number": published.rsu_column_number,
                "load_case_membership": list(published.load_case_membership),
            },
            "source": {
                "sheet": published.source_sheet,
                "row": published.source_row,
                "sha256": published.source_sha256,
                "mapping_fingerprint": published.mapping_fingerprint,
            },
            "published_vector": {
                name: _value(published.vector.values[name]) for name in RSU_FORCE_COMPONENTS
            },
            "reconstruction": {
                item.component: {
                    "published": str(item.published),
                    "reconstructed": str(item.reconstructed),
                    "difference": str(item.difference),
                    "export_precision": (
                        None
                        if item.export_precision is None
                        else item.export_precision.as_dict()
                    ),
                }
                for item in result.components
            },
            "source_terms": terms,
        })
    return {
        "kind": "READ_ONLY_LIRA_RSU_EVIDENCE",
        "status": report.status.value,
        "sources": sources,
        "mapping_fingerprint": bundle.mapping_fingerprint,
        "force_records": len(bundle.force_records),
        "published_records": len(bundle.published_records),
        "component_comparisons": report.component_comparisons,
        "matching_components": report.matching_components,
        "bounded_components": report.bounded_components,
        "bounded_rows": report.bounded_rows,
        "export_precision": {
            "bound": EXPORT_PRECISION_BOUND_STATEMENT,
            "status_verified": RsuValidationStatus.VERIFIED.value,
            "status_within_export_precision": (
                RsuValidationStatus.VERIFIED_WITHIN_EXPORT_PRECISION.value
            ),
            "note": (
                "VERIFIED means every component equals the published value exactly. "
                "VERIFIED_WITHIN_EXPORT_PRECISION means every component stays inside "
                "the stated bound of the printed export; the exact published values, "
                "cells, coefficients, hashes and residuals are kept unchanged."
            ),
        },
        "blockers": list(report.blockers),
        "load_parameters": [
            {
                "load_case_id": item.load_case_id,
                "values": {key: str(value) if value is not None else None
                           for key, value in item.values.items()},
                "sheet": item.source_sheet,
                "row": item.source_row,
                "source_sha256": item.source_sha256,
            }
            for item in bundle.parameters
        ],
        "rows": rows,
        "governing_result_selection_validated": False,
        "rx38_force_generation_allowed": False,
        "issue_readiness": "NOT_READY_FOR_ISSUE",
    }


def prepare_rsu_review_bundle(
    bundle: RsuImportBundle, report: RsuValidationReport, output_dir: str | Path
) -> dict[str, object]:
    """Create a fresh review directory; never alter a source XLS or an existing bundle."""

    destination = Path(output_dir).resolve(strict=False)
    if destination.exists():
        raise LiraFormatError(f"RSU review directory already exists: {destination}")
    evidence = rsu_evidence(bundle, report)
    destination.mkdir(parents=True, exist_ok=False)
    evidence_path = destination / "rsu_evidence.json"
    with evidence_path.open("x", encoding="utf-8", newline="\n") as stream:
        json.dump(evidence, stream, ensure_ascii=False, indent=2)
        stream.write("\n")
    evidence_sha = _hash(evidence_path)
    template = {
        "declaration_kind": DECLARATION_KIND,
        "evidence_sha256": evidence_sha,
        "declared_by": None,
        "basis": None,
        "lira_gui_reference": None,
        "element_id": None,
        "rsu_row_id": None,
    }
    template_path = destination / "selection_template.json"
    with template_path.open("x", encoding="utf-8", newline="\n") as stream:
        json.dump(template, stream, ensure_ascii=False, indent=2)
        stream.write("\n")
    csv_path = destination / "rsu_rows.csv"
    with csv_path.open("x", encoding="utf-8-sig", newline="") as stream:
        writer = csv.writer(stream)
        writer.writerow((
            "row_id", "status", "element_id", "section_station", "rsu_group",
            "rsu_criterion", "rsu_column_number", "load_case_membership", "source_sheet",
            "source_row", *(f"{name} [tf*m]" if name in {"Mk", "My", "Mz"}
                            else f"{name} [tf]" for name in RSU_FORCE_COMPONENTS),
        ))
        for row in evidence["rows"]:
            identity = row["identity"]
            writer.writerow((
                row["row_id"], row["status"], identity["element_id"],
                identity["section_station"], identity["rsu_group"],
                identity["rsu_criterion"], identity["rsu_column_number"],
                " ".join(identity["load_case_membership"]),
                row["source"]["sheet"], row["source"]["row"],
                *(row["published_vector"][name]["value"] for name in RSU_FORCE_COMPONENTS),
            ))
    instructions_path = destination / "README_SELECTION.md"
    with instructions_path.open("x", encoding="utf-8", newline="\n") as stream:
        stream.write(
            "# Выбор одной строки РСУ\n\n"
            "Сверьте `rsu_rows.csv` с опубликованной таблицей и GUI ЛИРА. "
            "Скопируйте `selection_template.json` в отдельный файл и заполните "
            "`declared_by`, `basis`, `lira_gui_reference`, `element_id`, `rsu_row_id`. "
            "Проверяйте копию командой `validate-lira-rsu-selection --evidence "
            "rsu_evidence.json --selection <заполненный файл>`. "
            "Результат фиксирует решение инженера; автоматический выбор governing "
            "row и генерация RX38 остаются закрыты.\n"
        )
    return {
        "status": report.status.value,
        "evidence": str(evidence_path),
        "evidence_sha256": evidence_sha,
        "rows_csv": str(csv_path),
        "selection_template": str(template_path),
        "instructions": str(instructions_path),
        "published_records": len(report.results),
        "rx38_force_generation_allowed": False,
        "issue_readiness": "NOT_READY_FOR_ISSUE",
    }


def validate_rsu_selection(
    evidence_path: str | Path, selection_path: str | Path
) -> dict[str, object]:
    """Resolve one engineer-declared row while leaving the calculation gate closed."""

    source = Path(evidence_path).resolve(strict=True)
    declaration = Path(selection_path).resolve(strict=True)
    try:
        evidence = json.loads(source.read_text(encoding="utf-8-sig"))
        selected = json.loads(declaration.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError) as exc:
        raise LiraFormatError(f"cannot read RSU evidence or selection: {exc}") from exc
    if not isinstance(evidence, Mapping) or not isinstance(selected, Mapping):
        raise LiraMappingError("RSU evidence and selection must be JSON objects")
    if evidence.get("kind") != "READ_ONLY_LIRA_RSU_EVIDENCE" or evidence.get(
        "status"
    ) not in {
        RsuValidationStatus.VERIFIED.value,
        RsuValidationStatus.VERIFIED_WITHIN_EXPORT_PRECISION.value,
    }:
        raise LiraMappingError(
            "RSU evidence must be a re-verified read-only bundle (VERIFIED or "
            "VERIFIED_WITHIN_EXPORT_PRECISION)"
        )
    if selected.get("declaration_kind") != DECLARATION_KIND:
        raise LiraMappingError("invalid RSU declaration_kind")
    expected_fields = {
        "declaration_kind", "evidence_sha256", "declared_by", "basis",
        "lira_gui_reference", "element_id", "rsu_row_id",
    }
    if set(selected) != expected_fields:
        raise LiraMappingError("RSU selection must contain exactly one row declaration")
    if selected.get("evidence_sha256") != _hash(source):
        raise LiraMappingError("RSU evidence SHA-256 differs from the declaration")
    sources = evidence.get("sources")
    if not isinstance(sources, Mapping) or set(sources) != {
        "forces", "published", "coefficients", "parameters"
    }:
        raise LiraFormatError("RSU evidence must identify all four source XLS files")
    for name, item in sources.items():
        if not isinstance(item, Mapping) or not isinstance(item.get("path"), str):
            raise LiraFormatError(f"invalid RSU source entry: {name}")
        path = Path(item["path"])
        if not path.is_file() or _hash(path) != item.get("sha256"):
            raise LiraMappingError(f"RSU source changed or disappeared: {name}")
    for field in ("declared_by", "basis", "lira_gui_reference", "element_id", "rsu_row_id"):
        value = selected.get(field)
        if not isinstance(value, str) or not value.strip():
            raise LiraMappingError(f"RSU selection requires {field}")
    rows = evidence.get("rows")
    if not isinstance(rows, list) or len(rows) != evidence.get("published_records"):
        raise LiraFormatError("RSU evidence rows are incomplete")
    ids = [row.get("row_id") for row in rows if isinstance(row, Mapping)]
    if len(ids) != len(rows) or len(set(ids)) != len(rows):
        raise LiraFormatError("RSU evidence row IDs are missing or duplicated")
    matches = [row for row in rows if row["row_id"] == selected["rsu_row_id"].strip()]
    if len(matches) != 1:
        raise LiraMappingError("selected RSU row ID does not resolve uniquely")
    row = matches[0]
    if row.get("status") not in {
        RsuValidationStatus.VERIFIED.value,
        RsuValidationStatus.VERIFIED_WITHIN_EXPORT_PRECISION.value,
    }:
        raise LiraMappingError("selected RSU row has reconstruction blockers")
    row_exact = row.get("status") == RsuValidationStatus.VERIFIED.value
    published_vector = row.get("published_vector")
    if not isinstance(published_vector, Mapping) or set(published_vector) != set(RSU_FORCE_COMPONENTS):
        raise LiraFormatError("selected RSU row has an incomplete native force vector")
    reconstruction = row.get("reconstruction")
    if not isinstance(reconstruction, Mapping) or set(reconstruction) != set(RSU_FORCE_COMPONENTS):
        raise LiraFormatError("selected RSU row is not fully reconstructed")
    for component, item in reconstruction.items():
        if not isinstance(item, Mapping):
            raise LiraFormatError("selected RSU row is not fully reconstructed")
        token = item.get("difference")
        try:
            difference = Decimal(token) if isinstance(token, str) else None
        except InvalidOperation:
            difference = None
        if difference is None or not difference.is_finite():
            raise LiraFormatError("selected RSU row is not fully reconstructed")
        if difference == 0:
            continue
        if row_exact:
            raise LiraFormatError(
                "a row recorded as exactly reconstructed carries a non-zero "
                f"difference in {component}"
            )
        precision = item.get("export_precision")
        if not isinstance(precision, Mapping) or (
            precision.get("within_export_precision") is not True
        ):
            raise LiraFormatError(
                f"selected RSU row difference in {component} is not recorded as "
                "inside the export-precision bound"
            )
    if row.get("identity", {}).get("element_id") != selected["element_id"].strip():
        raise LiraMappingError("selected RSU row belongs to another element")
    return {
        "status": "ENGINEER_DECLARED_UNVALIDATED",
        "evidence_status": evidence.get("status"),
        "row_status": row.get("status"),
        "exact_reconstruction": row_exact,
        "evidence_sha256": _hash(source),
        "selection_sha256": _hash(declaration),
        "declared_by": selected["declared_by"].strip(),
        "basis": selected["basis"].strip(),
        "lira_gui_reference": selected["lira_gui_reference"].strip(),
        "selected_row": row,
        "governing_result_selection_validated": False,
        "rx38_force_generation_allowed": False,
        "issue_readiness": "NOT_READY_FOR_ISSUE",
    }
