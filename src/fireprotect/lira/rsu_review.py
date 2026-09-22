"""Auditable, read-only RSU rows and one explicit engineer declaration."""

from __future__ import annotations

import csv
from decimal import Decimal, InvalidOperation
from hashlib import sha256
import json
from pathlib import Path
from typing import Any, Mapping

from .errors import LiraFormatError, LiraMappingError
from .rsu import (
    RSU_FORCE_COMPONENTS,
    RsuCoefficient,
    RsuImportBundle,
    RsuLoadForceRecord,
    RsuSourceValue,
    RsuValidationReport,
    RsuValidationStatus,
)

DECLARATION_KIND = "RSU_ENGINEER_GOVERNING_ROW_DECLARATION"


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


def rsu_evidence(bundle: RsuImportBundle, report: RsuValidationReport) -> dict[str, Any]:
    """Keep all published vectors and exact reconstruction terms together."""

    if len(bundle.published_records) != len(report.results):
        raise LiraFormatError("RSU report does not match the imported bundle")
    sources = {
        name: {"path": book.source_file, "sha256": book.source_sha256,
               "sheets": len(book.worksheets)}
        for name, book in (
            ("forces", bundle.forces_workbook),
            ("published", bundle.published_workbook),
            ("coefficients", bundle.coefficients_workbook),
            ("parameters", bundle.parameters_workbook),
        )
    }
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
    if evidence.get("kind") != "READ_ONLY_LIRA_RSU_EVIDENCE" or evidence.get("status") != "VERIFIED":
        raise LiraMappingError("RSU evidence must be a fully verified read-only bundle")
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
    if row.get("status") != RsuValidationStatus.VERIFIED.value:
        raise LiraMappingError("selected RSU row has reconstruction blockers")
    published_vector = row.get("published_vector")
    if not isinstance(published_vector, Mapping) or set(published_vector) != set(RSU_FORCE_COMPONENTS):
        raise LiraFormatError("selected RSU row has an incomplete native force vector")
    reconstruction = row.get("reconstruction")
    if not isinstance(reconstruction, Mapping) or set(reconstruction) != set(RSU_FORCE_COMPONENTS):
        raise LiraFormatError("selected RSU row is not fully reconstructed")
    for item in reconstruction.values():
        token = item.get("difference") if isinstance(item, Mapping) else None
        try:
            difference = Decimal(token) if isinstance(token, str) else None
        except InvalidOperation:
            difference = None
        if difference is None or not difference.is_finite() or difference != 0:
            raise LiraFormatError("selected RSU row is not fully reconstructed")
    if row.get("identity", {}).get("element_id") != selected["element_id"].strip():
        raise LiraMappingError("selected RSU row belongs to another element")
    return {
        "status": "ENGINEER_DECLARED_UNVALIDATED",
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
