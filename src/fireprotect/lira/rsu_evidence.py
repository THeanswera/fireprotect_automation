"""Explicit read-only import path for a published LIRA RSU evidence bundle.

The recorded ``status = VERIFIED`` is never trusted by itself. This import
re-verifies the bundle before any row may be linked to a model:

* the SHA-256 of every source XLS recorded under ``sources`` is recomputed
  from the file on disk and must match the recorded hash;
* for every row the six native components ``N/Mk/My/Mz/Qy/Qz`` are recomputed
  with exact ``Decimal`` arithmetic from the recorded source terms (load-case
  force value times the RSU coefficient column) and must equal the published
  value, including the sign;
* the four source XLS are then re-read with the existing importers and the
  reconstruction validator, and every identifier, vector value, unit,
  coefficient, membership and source location accepted from the JSON is
  compared against the re-read tables — self-consistency of the JSON is not
  accepted as proof against the sources;
* a bundle-level ``status = BLOCKED``, any recorded blocker or a row recorded
  as ``BLOCKED`` is rejected outright: a blocked reconstruction is a
  verification failure, not a lesser-quality row.

The table settings used for the re-read are explicit: the recorded
``mapping_fingerprint`` must match the fingerprint of the one mapping
configuration this module knows; anything else is refused instead of being
guessed from the data.

Rows stay RSU combination rows with their ``load_case_membership`` and their
source terms; nothing in this module turns an RSU row into an ordinary single
load case.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from pathlib import Path
from types import MappingProxyType
from typing import Any, Mapping

from .errors import LiraFormatError, LiraMappingError
from .rsu import (
    RsuCoefficient,
    RsuLoadForceRecord,
    RsuLoadParameter,
    RsuPublishedRecord,
    RsuReconstructionResult,
    RsuSourceValue,
    RsuValidationStatus,
    RsuXlsMapping,
    import_rsu_xls_bundle,
    validate_rsu_reconstruction,
)
from .types import LIRA_NATIVE_FORCE_COMPONENTS

EVIDENCE_KIND = "READ_ONLY_LIRA_RSU_EVIDENCE"
EVIDENCE_SOURCE_NAMES = ("forces", "published", "coefficients", "parameters")
RSU_COMPONENTS = LIRA_NATIVE_FORCE_COMPONENTS


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(65536), b""):
            digest.update(chunk)
    return digest.hexdigest()


def read_json_object(path: Path, description: str) -> Mapping[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError) as exc:
        raise LiraFormatError(f"cannot read {description} {path}: {exc}") from exc
    if not isinstance(payload, Mapping):
        raise LiraFormatError(f"{description} {path} must contain a JSON object")
    return payload


def require_mapping(
    payload: object, field: str, context: str
) -> Mapping[str, Any]:
    if not isinstance(payload, Mapping):
        raise LiraFormatError(f"{context}: {field} must be a JSON object")
    return payload


def require_text(payload: Mapping[str, Any], field: str, context: str) -> str:
    value = payload.get(field)
    if not isinstance(value, str) or not value.strip():
        raise LiraFormatError(f"{context}: {field} must be a non-empty string")
    return value.strip()


def optional_text(
    payload: Mapping[str, Any], field: str, context: str
) -> str | None:
    """Keep an optional provenance string verbatim (no stripping).

    Sheet names such as a single space are real source locations and must not
    be normalized away.
    """

    value = payload.get(field)
    if value is None:
        return None
    if not isinstance(value, str):
        raise LiraFormatError(f"{context}: {field} must be a string or null")
    return value


def require_positive_int(
    payload: Mapping[str, Any], field: str, context: str
) -> int:
    value = payload.get(field)
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise LiraFormatError(f"{context}: {field} must be a positive integer")
    return value


def parse_finite_decimal(token: object, *, field: str, context: str) -> Decimal:
    if isinstance(token, bool) or not isinstance(token, (str, int)):
        raise LiraFormatError(f"{context}: {field} must be a decimal string")
    try:
        value = Decimal(str(token))
    except InvalidOperation as exc:
        raise LiraFormatError(f"{context}: {field} is not a decimal number") from exc
    if not value.is_finite():
        raise LiraFormatError(f"{context}: {field} must be finite")
    return value


def verify_recorded_sha256(
    entry: Mapping[str, Any], *, name: str, context: str
) -> dict[str, object]:
    """Recompute the SHA-256 of one recorded source file and record the fact.

    A missing file or a mismatching hash raises: a changed source must never
    silently support a link or an import.
    """

    path_token = entry.get("path")
    recorded = entry.get("sha256")
    if not isinstance(path_token, str) or not path_token.strip():
        raise LiraFormatError(f"{context}: source {name!r} has no path")
    if not isinstance(recorded, str) or not recorded.strip():
        raise LiraFormatError(f"{context}: source {name!r} has no sha256")
    path = Path(path_token)
    if not path.is_file():
        raise LiraMappingError(f"{context}: source {name!r} disappeared: {path}")
    actual = sha256_file(path)
    if actual != recorded:
        raise LiraMappingError(
            f"{context}: source {name!r} changed: recorded sha256 {recorded} "
            f"does not match the file on disk ({actual})"
        )
    return {"path": str(path), "sha256": actual, "matches": True}


@dataclass(frozen=True, slots=True)
class VerifiedForceSources:
    """Verified force workbooks of one evidence bundle, in their recorded order."""

    paths: tuple[str, ...]
    hashes: frozenset[str]
    page_records: tuple[int | None, ...]
    paged: bool


def _verify_force_pages(
    entry: Mapping[str, Any], *, context: str, first_page: Mapping[str, object]
) -> VerifiedForceSources:
    """Verify every recorded force page and accept the legacy single-workbook shape.

    A paged entry keeps the flat ``path``/``sha256`` of the first page, so the
    old flat meaning stays stable, and lists every page under ``pages``.  The
    first page hash must equal the flat hash, every page file must still match
    its recorded SHA-256, and no workbook may be recorded twice.
    """

    pages = entry.get("pages")
    if pages is None:
        return VerifiedForceSources(
            paths=(str(first_page["path"]),),
            hashes=frozenset({str(first_page["sha256"])}),
            page_records=(None,),
            paged=False,
        )
    if not isinstance(pages, list) or not pages:
        raise LiraFormatError(
            f"{context}: sources.forces.pages must be a non-empty list"
        )
    page_count = entry.get("page_count")
    if (
        isinstance(page_count, bool)
        or not isinstance(page_count, int)
        or page_count != len(pages)
    ):
        raise LiraFormatError(
            f"{context}: sources.forces.page_count must equal the number of "
            f"recorded pages ({len(pages)})"
        )
    paths: list[str] = []
    hashes: list[str] = []
    counts: list[int | None] = []
    for index, raw in enumerate(pages, 1):
        page = require_mapping(raw, f"forces page {index}", context)
        verified = verify_recorded_sha256(
            page, name=f"forces page {index}", context=context
        )
        paths.append(str(verified["path"]))
        hashes.append(str(verified["sha256"]))
        recorded = page.get("records")
        if recorded is None:
            counts.append(None)
        elif isinstance(recorded, bool) or not isinstance(recorded, int):
            raise LiraFormatError(
                f"{context}: forces page {index} records must be an integer"
            )
        else:
            counts.append(recorded)
    if hashes[0] != str(first_page["sha256"]):
        raise LiraFormatError(
            f"{context}: the first force page sha256 does not match "
            "sources.forces.sha256"
        )
    if len(set(hashes)) != len(hashes):
        raise LiraFormatError(
            f"{context}: the same force workbook is recorded on more than one page"
        )
    return VerifiedForceSources(
        paths=tuple(paths),
        hashes=frozenset(hashes),
        page_records=tuple(counts),
        paged=True,
    )


@dataclass(frozen=True, slots=True)
class RsuEvidenceValue:
    """One native component value with its full source provenance."""

    component: str
    value: Decimal
    unit: str
    header: str | None
    sheet: str | None
    row: int
    cell: str | None
    source_sha256: str
    raw_token: str | None
    decimal_provenance: str | None

    def as_dict(self) -> dict[str, object]:
        return {
            "value": str(self.value),
            "unit": self.unit,
            "header": self.header,
            "sheet": self.sheet,
            "row": self.row,
            "cell": self.cell,
            "source_sha256": self.source_sha256,
            "raw_token": self.raw_token,
            "decimal_provenance": self.decimal_provenance,
        }


@dataclass(frozen=True, slots=True)
class RsuEvidenceTerm:
    """One reconstruction term: a load-case force vector times a coefficient."""

    load_case_id: str
    force_sheet: str | None
    force_row: int
    forces: Mapping[str, RsuEvidenceValue]
    coefficient: Decimal
    coefficient_sheet: str | None
    coefficient_row: int
    coefficient_cell: str | None
    coefficient_header: str | None
    coefficient_source_sha256: str
    coefficient_raw_token: str | None
    coefficient_decimal_provenance: str | None

    def as_dict(self) -> dict[str, object]:
        return {
            "load_case_id": self.load_case_id,
            "force_sheet": self.force_sheet,
            "force_row": self.force_row,
            "forces": {name: self.forces[name].as_dict() for name in RSU_COMPONENTS},
            "coefficient": str(self.coefficient),
            "coefficient_source": {
                "sheet": self.coefficient_sheet,
                "row": self.coefficient_row,
                "cell": self.coefficient_cell,
                "header": self.coefficient_header,
                "source_sha256": self.coefficient_source_sha256,
                "raw_token": self.coefficient_raw_token,
                "decimal_provenance": self.coefficient_decimal_provenance,
            },
        }


@dataclass(frozen=True, slots=True)
class RsuEvidenceRow:
    """One fully re-verified published RSU row."""

    row_id: str
    status: str
    element_id: str
    section_station: str
    rsu_group: str
    rsu_criterion: str
    rsu_column_number: int
    load_case_membership: tuple[str, ...]
    source_sheet: str | None
    source_row: int
    source_sha256: str
    mapping_fingerprint: str
    published_vector: Mapping[str, RsuEvidenceValue]
    reconstruction: Mapping[str, Mapping[str, str]]
    source_terms: tuple[RsuEvidenceTerm, ...]

    def as_dict(self) -> dict[str, object]:
        return {
            "row_id": self.row_id,
            "status": self.status,
            "blockers": [],
            "identity": {
                "element_id": self.element_id,
                "section_station": self.section_station,
                "rsu_group": self.rsu_group,
                "rsu_criterion": self.rsu_criterion,
                "rsu_column_number": self.rsu_column_number,
                "load_case_membership": list(self.load_case_membership),
            },
            "source": {
                "sheet": self.source_sheet,
                "row": self.source_row,
                "sha256": self.source_sha256,
                "mapping_fingerprint": self.mapping_fingerprint,
            },
            "published_vector": {
                name: self.published_vector[name].as_dict() for name in RSU_COMPONENTS
            },
            "reconstruction": {
                name: dict(self.reconstruction[name]) for name in RSU_COMPONENTS
            },
            "source_terms": [term.as_dict() for term in self.source_terms],
        }


@dataclass(frozen=True, slots=True)
class RsuEvidenceBundle:
    """One re-verified evidence bundle plus the re-check facts."""

    path: Path
    sha256: str
    status: str
    mapping_fingerprint: str
    sources: Mapping[str, Mapping[str, object]]
    source_files_rechecked: Mapping[str, Mapping[str, object]]
    load_parameters: tuple[Mapping[str, object], ...]
    rows: tuple[RsuEvidenceRow, ...]
    source_recheck: Mapping[str, object]

    @property
    def components_preserved(self) -> int:
        return sum(len(row.published_vector) for row in self.rows)

    @property
    def rows_verified(self) -> int:
        return len(self.rows)


def _parse_value(payload: object, *, component: str, context: str) -> RsuEvidenceValue:
    entry = require_mapping(payload, component, context)
    return RsuEvidenceValue(
        component=component,
        value=parse_finite_decimal(
            entry.get("value"), field="value", context=f"{context} {component}"
        ),
        unit=require_text(entry, "unit", f"{context} {component}"),
        header=optional_text(entry, "header", f"{context} {component}"),
        sheet=optional_text(entry, "sheet", f"{context} {component}"),
        row=require_positive_int(entry, "row", f"{context} {component}"),
        cell=optional_text(entry, "cell", f"{context} {component}"),
        source_sha256=require_text(
            entry, "source_sha256", f"{context} {component}"
        ),
        raw_token=optional_text(entry, "raw_token", f"{context} {component}"),
        decimal_provenance=optional_text(
            entry, "decimal_provenance", f"{context} {component}"
        ),
    )


def _parse_vector(payload: object, *, context: str) -> Mapping[str, RsuEvidenceValue]:
    entry = require_mapping(payload, "vector", context)
    missing = [name for name in RSU_COMPONENTS if name not in entry]
    extra = [name for name in entry if name not in RSU_COMPONENTS]
    if missing or extra:
        raise LiraFormatError(
            f"{context}: force vector must contain exactly "
            f"{', '.join(RSU_COMPONENTS)}; missing {missing}, unexpected {extra}"
        )
    return MappingProxyType(
        {
            name: _parse_value(entry[name], component=name, context=context)
            for name in RSU_COMPONENTS
        }
    )


def _parse_term(payload: object, *, context: str) -> RsuEvidenceTerm:
    entry = require_mapping(payload, "term", context)
    load_case_id = require_text(entry, "load_case_id", context)
    term_context = f"{context} term {load_case_id}"
    coefficient = parse_finite_decimal(
        entry.get("coefficient"), field="coefficient", context=term_context
    )
    coefficient_source = require_mapping(
        entry.get("coefficient_source"), "coefficient_source", term_context
    )
    return RsuEvidenceTerm(
        load_case_id=load_case_id,
        force_sheet=optional_text(entry, "force_sheet", term_context),
        force_row=require_positive_int(entry, "force_row", term_context),
        forces=_parse_vector(entry.get("forces"), context=term_context),
        coefficient=coefficient,
        coefficient_sheet=optional_text(
            coefficient_source, "sheet", term_context
        ),
        coefficient_row=require_positive_int(
            coefficient_source, "row", term_context
        ),
        coefficient_cell=optional_text(
            coefficient_source, "cell", term_context
        ),
        coefficient_header=optional_text(
            coefficient_source, "header", term_context
        ),
        coefficient_source_sha256=require_text(
            coefficient_source, "source_sha256", term_context
        ),
        coefficient_raw_token=optional_text(
            coefficient_source, "raw_token", term_context
        ),
        coefficient_decimal_provenance=optional_text(
            coefficient_source, "decimal_provenance", term_context
        ),
    )


def _parse_row(
    payload: object,
    *,
    index: int,
    context: str,
    expected_hashes: Mapping[str, str],
    expected_force_hashes: frozenset[str],
    fingerprint: str,
) -> RsuEvidenceRow:
    entry = require_mapping(payload, "row", f"{context} row {index + 1}")
    row_id = require_text(entry, "row_id", f"{context} row {index + 1}")
    row_context = f"{context} row {row_id}"
    status_token = entry.get("status")
    if status_token not in (
        RsuValidationStatus.VERIFIED.value,
        RsuValidationStatus.BLOCKED.value,
    ):
        raise LiraFormatError(f"{row_context}: unknown status {status_token!r}")
    blockers = entry.get("blockers")
    if not isinstance(blockers, list) or any(
        not isinstance(item, str) for item in blockers
    ):
        raise LiraFormatError(
            f"{row_context}: blockers must be a list of strings"
        )
    if status_token == RsuValidationStatus.BLOCKED.value:
        raise LiraFormatError(
            f"{row_context}: the row is BLOCKED "
            f"({'; '.join(blockers) or 'no reason recorded'}); a blocked "
            "reconstruction is never linked as a candidate"
        )
    if blockers:
        raise LiraFormatError(
            f"{row_context}: a VERIFIED row must record no blockers, "
            f"got {blockers!r}"
        )
    identity = require_mapping(entry.get("identity"), "identity", row_context)
    element_id = require_text(identity, "element_id", row_context)
    membership = identity.get("load_case_membership")
    if (
        not isinstance(membership, list)
        or not membership
        or any(not isinstance(item, str) or not item.strip() for item in membership)
    ):
        raise LiraFormatError(
            f"{row_context}: load_case_membership must be a non-empty list of strings"
        )
    if len(set(membership)) != len(membership):
        raise LiraFormatError(
            f"{row_context}: load_case_membership contains duplicates"
        )
    source = require_mapping(entry.get("source"), "source", row_context)
    source_sha = require_text(source, "sha256", row_context)
    if source_sha != expected_hashes["published"]:
        raise LiraFormatError(
            f"{row_context}: source sha256 does not match the published XLS"
        )
    row_fingerprint = require_text(source, "mapping_fingerprint", row_context)
    if row_fingerprint != fingerprint:
        raise LiraFormatError(
            f"{row_context}: mapping_fingerprint does not match the bundle"
        )
    published_vector = _parse_vector(
        entry.get("published_vector"), context=f"{row_context} published_vector"
    )
    reconstruction = require_mapping(
        entry.get("reconstruction"), "reconstruction", row_context
    )
    recorded: dict[str, Mapping[str, str]] = {}
    for component in RSU_COMPONENTS:
        part = reconstruction.get(component)
        if not isinstance(part, Mapping):
            raise LiraFormatError(
                f"{row_context}: reconstruction is missing component {component}"
            )
        published_token = part.get("published")
        reconstructed_token = part.get("reconstructed")
        difference_token = part.get("difference")
        if not all(
            isinstance(token, str)
            for token in (published_token, reconstructed_token, difference_token)
        ):
            raise LiraFormatError(
                f"{row_context}: reconstruction {component} must record "
                "published/reconstructed/difference strings"
            )
        difference = parse_finite_decimal(
            difference_token, field="difference", context=f"{row_context} {component}"
        )
        if difference != 0:
            raise LiraFormatError(
                f"{row_context}: recorded residual for {component} is not zero"
            )
        if parse_finite_decimal(
            published_token, field="published", context=f"{row_context} {component}"
        ) != published_vector[component].value:
            raise LiraFormatError(
                f"{row_context}: recorded reconstruction for {component} "
                "disagrees with the published vector"
            )
        recorded[component] = {
            "published": str(published_token),
            "reconstructed": str(reconstructed_token),
            "difference": str(difference_token),
        }
    terms_payload = entry.get("source_terms")
    if not isinstance(terms_payload, list) or not terms_payload:
        raise LiraFormatError(f"{row_context}: source_terms must be a non-empty list")
    terms = tuple(
        _parse_term(item, context=row_context) for item in terms_payload
    )
    if [term.load_case_id for term in terms] != [
        item.strip() for item in membership
    ]:
        raise LiraFormatError(
            f"{row_context}: source_terms must follow load_case_membership in order"
        )
    for term in terms:
        for component in RSU_COMPONENTS:
            value = term.forces[component]
            if value.source_sha256 not in expected_force_hashes:
                raise LiraFormatError(
                    f"{row_context}: term {term.load_case_id} component "
                    f"{component} points to force workbook "
                    f"{value.source_sha256} which is not recorded in "
                    "sources.forces"
                )
        if term.coefficient_source_sha256 != expected_hashes["coefficients"]:
            raise LiraFormatError(
                f"{row_context}: term {term.load_case_id} coefficient points "
                "to a different coefficients XLS"
            )
    for component in RSU_COMPONENTS:
        reconstructed_value = sum(
            (term.coefficient * term.forces[component].value for term in terms),
            Decimal("0"),
        )
        published_value = published_vector[component].value
        if reconstructed_value != published_value:
            raise LiraFormatError(
                f"{row_context}: component {component} published {published_value} "
                f"is not reproduced from the source terms ({reconstructed_value}); "
                "the recorded VERIFIED status is not trusted on its own"
            )
        if (
            parse_finite_decimal(
                recorded[component]["reconstructed"],
                field="reconstructed",
                context=f"{row_context} {component}",
            )
            != reconstructed_value
        ):
            raise LiraFormatError(
                f"{row_context}: recorded reconstructed value for {component} "
                "does not match the source terms"
            )
    return RsuEvidenceRow(
        row_id=row_id,
        status=str(status_token),
        element_id=element_id,
        section_station=require_text(
            identity, "section_station", row_context
        ),
        rsu_group=require_text(identity, "rsu_group", row_context),
        rsu_criterion=require_text(identity, "rsu_criterion", row_context),
        rsu_column_number=require_positive_int(
            identity, "rsu_column_number", row_context
        ),
        load_case_membership=tuple(item.strip() for item in membership),
        source_sheet=optional_text(source, "sheet", row_context),
        source_row=require_positive_int(source, "row", row_context),
        source_sha256=source_sha,
        mapping_fingerprint=row_fingerprint,
        published_vector=published_vector,
        reconstruction=MappingProxyType(recorded),
        source_terms=terms,
    )


def _compare_value(
    component: str,
    value: RsuEvidenceValue,
    source_value: RsuSourceValue,
    *,
    what: str,
    context: str,
) -> None:
    if value.value != source_value.value:
        raise LiraMappingError(
            f"{context}: {what} component {component} in evidence is "
            f"{value.value} but the re-read source XLS contains {source_value.value}"
        )
    if value.unit != source_value.source_unit:
        raise LiraMappingError(
            f"{context}: {what} component {component} unit in evidence is "
            f"{value.unit!r} but the re-read source XLS declares "
            f"{source_value.source_unit!r}"
        )
    if value.cell != source_value.source_cell:
        raise LiraMappingError(
            f"{context}: {what} component {component} cell in evidence is "
            f"{value.cell!r} but the re-read source XLS locates it at "
            f"{source_value.source_cell!r}"
        )
    if value.row != source_value.source_row:
        raise LiraMappingError(
            f"{context}: {what} component {component} row in evidence is "
            f"{value.row} but the re-read source XLS locates it at row "
            f"{source_value.source_row}"
        )
    if value.sheet != source_value.source_sheet:
        raise LiraMappingError(
            f"{context}: {what} component {component} sheet in evidence is "
            f"{value.sheet!r} but the re-read source XLS locates it on sheet "
            f"{source_value.source_sheet!r}"
        )
    if value.source_sha256 != source_value.source_sha256:
        raise LiraMappingError(
            f"{context}: {what} component {component} source_sha256 in evidence "
            f"is {value.source_sha256!r} but the re-read source XLS records "
            f"{source_value.source_sha256!r}"
        )
    if value.header != source_value.source_header:
        raise LiraMappingError(
            f"{context}: {what} component {component} header in evidence is "
            f"{value.header!r} but the re-read source XLS uses "
            f"{source_value.source_header!r}"
        )
    if value.raw_token != source_value.raw_token:
        raise LiraMappingError(
            f"{context}: {what} component {component} raw_token in evidence is "
            f"{value.raw_token!r} but the re-read source XLS carries "
            f"{source_value.raw_token!r}"
        )
    if value.decimal_provenance != source_value.decimal_provenance:
        raise LiraMappingError(
            f"{context}: {what} component {component} decimal_provenance in "
            f"evidence is {value.decimal_provenance!r} but the re-read source "
            f"XLS carries {source_value.decimal_provenance!r}"
        )


def _count(root: Mapping[str, Any], field: str, context: str) -> int:
    value = root.get(field)
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise LiraFormatError(f"{context}: {field} must be a non-negative integer")
    return value


def _parameter_values_equal(
    json_value: object, source_value: object, *, context: str
) -> bool:
    """Compare one JSON load-parameter value with the re-read XLS value."""

    if json_value is None:
        return source_value is None
    if not isinstance(json_value, str):
        raise LiraFormatError(
            f"{context}: load parameter values must be strings or null"
        )
    if source_value is None:
        return False
    if isinstance(source_value, str):
        return json_value == source_value
    if isinstance(source_value, Decimal):
        return (
            parse_finite_decimal(
                json_value, field="parameter value", context=context
            )
            == source_value
        )
    return False


def _recheck_against_sources(
    source: Path,
    root: Mapping[str, Any],
    sources_block: Mapping[str, Any],
    rows: tuple[RsuEvidenceRow, ...],
    parameters: tuple[Mapping[str, object], ...],
    force_sources: VerifiedForceSources,
) -> dict[str, object]:
    """Re-read the four source XLS and prove the accepted JSON rows against them.

    This is the counter-measure against a JSON bundle that was edited while its
    recorded hashes stayed valid: every number accepted from the JSON is
    compared with the value the existing importers read from the XLS itself.
    """

    mapping = RsuXlsMapping()
    recorded_fingerprint = require_text(root, "mapping_fingerprint", str(source))
    if mapping.fingerprint != recorded_fingerprint:
        raise LiraMappingError(
            f"{source}: evidence was produced with mapping fingerprint "
            f"{recorded_fingerprint}, but the only explicit table settings known "
            f"here have fingerprint {mapping.fingerprint}; refusing to guess "
            "table settings from the data"
        )
    bundle = import_rsu_xls_bundle(
        forces_path=force_sources.paths,
        published_path=str(
            require_text(sources_block["published"], "path", str(source))
        ),
        coefficients_path=str(
            require_text(sources_block["coefficients"], "path", str(source))
        ),
        parameters_path=str(
            require_text(sources_block["parameters"], "path", str(source))
        ),
        mapping=mapping,
    )
    reimported_hashes = frozenset(
        record.source_sha256 for record in bundle.force_records
    )
    if reimported_hashes != force_sources.hashes:
        raise LiraFormatError(
            f"{source}: the force pages recorded in sources.forces do not match "
            f"the records the re-read pages contain: recorded "
            f"{sorted(force_sources.hashes)}, re-read {sorted(reimported_hashes)}"
        )
    if force_sources.paged:
        reimported_counts = {
            book.source_sha256: sum(
                1
                for record in bundle.force_records
                if record.source_sha256 == book.source_sha256
            )
            for book in bundle.forces_workbooks
        }
        for index, (book, recorded_count) in enumerate(
            zip(bundle.forces_workbooks, force_sources.page_records), 1
        ):
            if recorded_count is None:
                continue
            actual_count = reimported_counts[book.source_sha256]
            if recorded_count != actual_count:
                raise LiraFormatError(
                    f"{source}: recorded {recorded_count} force records for page "
                    f"{index} ({book.source_file}) but the re-read page contains "
                    f"{actual_count}"
                )
    report = validate_rsu_reconstruction(bundle)
    if report.status is not RsuValidationStatus.VERIFIED:
        raise LiraFormatError(
            f"{source}: re-reading the source XLS does not reproduce every "
            f"published row: {'; '.join(report.blockers)}"
        )
    recorded_counts = {
        "force_records": _count(root, "force_records", str(source)),
        "published_records": _count(root, "published_records", str(source)),
        "component_comparisons": _count(root, "component_comparisons", str(source)),
        "matching_components": _count(root, "matching_components", str(source)),
    }
    actual_counts = {
        "force_records": len(bundle.force_records),
        "published_records": len(bundle.published_records),
        "component_comparisons": report.component_comparisons,
        "matching_components": report.matching_components,
    }
    for name in recorded_counts:
        if recorded_counts[name] != actual_counts[name]:
            raise LiraFormatError(
                f"{source}: recorded {name} = {recorded_counts[name]} does not "
                f"match the re-read sources ({actual_counts[name]})"
            )
    reimported_parameters: dict[str, RsuLoadParameter] = {}
    for parameter in bundle.parameters:
        if parameter.load_case_id in reimported_parameters:
            raise LiraFormatError(
                f"{source}: the re-read parameters XLS contains two rows for "
                f"load case {parameter.load_case_id!r}"
            )
        reimported_parameters[parameter.load_case_id] = parameter
    if len(parameters) != len(reimported_parameters):
        raise LiraFormatError(
            f"{source}: evidence records {len(parameters)} load parameters but "
            f"the re-read parameters XLS contains {len(reimported_parameters)}"
        )
    json_load_ids = [
        require_text(entry, "load_case_id", str(source)) for entry in parameters
    ]
    if len(set(json_load_ids)) != len(json_load_ids):
        raise LiraFormatError(
            f"{source}: load_parameters contains a duplicate load_case_id "
            f"({json_load_ids!r})"
        )
    if set(json_load_ids) != set(reimported_parameters):
        raise LiraFormatError(
            f"{source}: load_parameters load_case_ids in evidence are "
            f"{sorted(json_load_ids)!r} but the re-read parameters XLS has "
            f"{sorted(reimported_parameters)!r}"
        )
    for index, entry in enumerate(parameters):
        load_case_id = json_load_ids[index]
        matched_parameter = reimported_parameters.get(load_case_id)
        if matched_parameter is None:
            raise LiraMappingError(
                f"{source}: load parameter {load_case_id!r} in evidence does "
                "not exist in the re-read parameters XLS"
            )
        if (
            optional_text(entry, "sheet", str(source))
            != matched_parameter.source_sheet
            or require_positive_int(entry, "row", str(source))
            != matched_parameter.source_row
            or require_text(entry, "source_sha256", str(source))
            != matched_parameter.source_sha256
        ):
            raise LiraMappingError(
                f"{source}: load parameter {load_case_id!r} provenance in "
                "evidence does not match the re-read parameters XLS"
            )
        json_values = require_mapping(
            entry.get("values"), "values", str(source)
        )
        if set(json_values) != set(matched_parameter.values):
            raise LiraMappingError(
                f"{source}: load parameter {load_case_id!r} value headers in "
                f"evidence are {sorted(json_values)!r} but the re-read "
                f"parameters XLS has {sorted(matched_parameter.values)!r}"
            )
        for key, json_value in json_values.items():
            if not _parameter_values_equal(
                json_value,
                matched_parameter.values[key],
                context=f"{source} load parameter {load_case_id} {key}",
            ):
                raise LiraMappingError(
                    f"{source}: load parameter {load_case_id!r} value {key!r} "
                    f"in evidence is {json_value!r} but the re-read parameters "
                    f"XLS has {matched_parameter.values[key]!r}"
                )
    published_by_source: dict[tuple[str, int], RsuPublishedRecord] = {}
    for published_record in bundle.published_records:
        published_key = (published_record.source_sheet, published_record.source_row)
        if published_key in published_by_source:
            raise LiraFormatError(
                f"{source}: the re-read published XLS contains two rows at "
                f"sheet {published_key[0]!r} row {published_key[1]}"
            )
        published_by_source[published_key] = published_record
    force_by_key: dict[tuple[str, str, str], RsuLoadForceRecord] = {}
    for force_record in bundle.force_records:
        force_key_name = (
            force_record.element_id,
            force_record.section_station,
            force_record.load_case_id,
        )
        if force_key_name in force_by_key:
            raise LiraFormatError(
                f"{source}: the re-read forces XLS contains two rows for "
                f"element {force_key_name[0]} section {force_key_name[1]} "
                f"load case {force_key_name[2]}"
            )
        force_by_key[force_key_name] = force_record
    coefficients_by_key: dict[tuple[str, int], RsuCoefficient] = {}
    for coefficient_item in bundle.coefficients:
        coefficient_key = (
            coefficient_item.load_case_id,
            coefficient_item.column_number,
        )
        if coefficient_key in coefficients_by_key:
            raise LiraFormatError(
                f"{source}: the re-read coefficients XLS contains two rows for "
                f"load case {coefficient_key[0]} column {coefficient_key[1]}"
            )
        coefficients_by_key[coefficient_key] = coefficient_item
    results_by_source: dict[tuple[str, int], RsuReconstructionResult] = {}
    for reconstruction_result in report.results:
        reconstruction_key = (
            reconstruction_result.published_record.source_sheet,
            reconstruction_result.published_record.source_row,
        )
        results_by_source[reconstruction_key] = reconstruction_result

    used_sources: set[tuple[str, int]] = set()
    for row in rows:
        if row.source_sheet is None:
            raise LiraFormatError(
                f"{source}: row {row.row_id} has no source sheet and cannot be "
                "matched to the re-read published XLS"
            )
        source_key = (row.source_sheet, row.source_row)
        record = published_by_source.get(source_key)
        if record is None:
            raise LiraMappingError(
                f"{source}: row {row.row_id} claims sheet {row.source_sheet!r} "
                f"row {row.source_row}, which the re-read published XLS does "
                "not contain"
            )
        if source_key in used_sources:
            raise LiraFormatError(
                f"{source}: two evidence rows claim the same source location "
                f"sheet {source_key[0]!r} row {source_key[1]}"
            )
        used_sources.add(source_key)
        if record.element_id != row.element_id:
            raise LiraMappingError(
                f"{source}: row {row.row_id} element_id in evidence is "
                f"{row.element_id!r} but the re-read source XLS says "
                f"{record.element_id!r}"
            )
        if record.section_station != row.section_station:
            raise LiraMappingError(
                f"{source}: row {row.row_id} section_station in evidence is "
                f"{row.section_station!r} but the re-read source XLS says "
                f"{record.section_station!r}"
            )
        if record.rsu_group != row.rsu_group:
            raise LiraMappingError(
                f"{source}: row {row.row_id} rsu_group in evidence is "
                f"{row.rsu_group!r} but the re-read source XLS says "
                f"{record.rsu_group!r}"
            )
        if record.rsu_criterion != row.rsu_criterion:
            raise LiraMappingError(
                f"{source}: row {row.row_id} rsu_criterion in evidence is "
                f"{row.rsu_criterion!r} but the re-read source XLS says "
                f"{record.rsu_criterion!r}"
            )
        if record.rsu_column_number != row.rsu_column_number:
            raise LiraMappingError(
                f"{source}: row {row.row_id} rsu_column_number in evidence is "
                f"{row.rsu_column_number} but the re-read source XLS says "
                f"{record.rsu_column_number}"
            )
        if tuple(record.load_case_membership) != row.load_case_membership:
            raise LiraMappingError(
                f"{source}: row {row.row_id} load_case_membership in evidence "
                f"is {list(row.load_case_membership)} but the re-read source "
                f"XLS says {list(record.load_case_membership)}"
            )
        for component in RSU_COMPONENTS:
            _compare_value(
                component,
                row.published_vector[component],
                record.vector.values[component],
                what="published vector",
                context=f"{source} row {row.row_id}",
            )
        for term in row.source_terms:
            force_key = (row.element_id, row.section_station, term.load_case_id)
            matched_force = force_by_key.get(force_key)
            if matched_force is None:
                raise LiraMappingError(
                    f"{source}: row {row.row_id} term {term.load_case_id} "
                    "references a force row the re-read forces XLS does not "
                    "contain"
                )
            if (
                term.force_sheet != matched_force.source_sheet
                or term.force_row != matched_force.source_row
            ):
                raise LiraMappingError(
                    f"{source}: row {row.row_id} term {term.load_case_id} "
                    f"locates its force row at sheet {term.force_sheet!r} row "
                    f"{term.force_row}, but the re-read forces XLS has it at "
                    f"sheet {matched_force.source_sheet!r} row "
                    f"{matched_force.source_row}"
                )
            for component in RSU_COMPONENTS:
                _compare_value(
                    component,
                    term.forces[component],
                    matched_force.vector.values[component],
                    what=f"term {term.load_case_id} force",
                    context=f"{source} row {row.row_id}",
                )
            coefficient = coefficients_by_key.get(
                (term.load_case_id, row.rsu_column_number)
            )
            if coefficient is None:
                raise LiraMappingError(
                    f"{source}: row {row.row_id} term {term.load_case_id} "
                    f"column {row.rsu_column_number} has no coefficient in the "
                    "re-read coefficients XLS"
                )
            if term.coefficient != coefficient.coefficient:
                raise LiraMappingError(
                    f"{source}: row {row.row_id} term {term.load_case_id} "
                    f"coefficient in evidence is {term.coefficient} but the "
                    f"re-read coefficients XLS says {coefficient.coefficient}"
                )
            if (
                term.coefficient_sheet != coefficient.source_sheet
                or term.coefficient_row != coefficient.source_row
                or term.coefficient_cell != coefficient.source_cell
                or term.coefficient_header != coefficient.source_header
            ):
                raise LiraMappingError(
                    f"{source}: row {row.row_id} term {term.load_case_id} "
                    "coefficient provenance in evidence does not match the "
                    "re-read coefficients XLS"
                )
            if term.coefficient_raw_token != coefficient.raw_token:
                raise LiraMappingError(
                    f"{source}: row {row.row_id} term {term.load_case_id} "
                    f"coefficient raw_token in evidence is "
                    f"{term.coefficient_raw_token!r} but the re-read "
                    f"coefficients XLS carries {coefficient.raw_token!r}"
                )
            if (
                term.coefficient_decimal_provenance
                != coefficient.decimal_provenance
            ):
                raise LiraMappingError(
                    f"{source}: row {row.row_id} term {term.load_case_id} "
                    "coefficient decimal_provenance in evidence is "
                    f"{term.coefficient_decimal_provenance!r} but the re-read "
                    f"coefficients XLS carries "
                    f"{coefficient.decimal_provenance!r}"
                )
        result = results_by_source[source_key]
        components = {item.component: item for item in result.components}
        for component in RSU_COMPONENTS:
            difference = components[component]
            recorded = row.reconstruction[component]
            if parse_finite_decimal(
                recorded["published"],
                field="published",
                context=f"{source} row {row.row_id} {component}",
            ) != difference.published:
                raise LiraMappingError(
                    f"{source}: row {row.row_id} recorded reconstruction "
                    f"published value for {component} does not match the "
                    "re-read sources"
                )
            if parse_finite_decimal(
                recorded["reconstructed"],
                field="reconstructed",
                context=f"{source} row {row.row_id} {component}",
            ) != difference.reconstructed:
                raise LiraMappingError(
                    f"{source}: row {row.row_id} recorded reconstruction "
                    f"reconstructed value for {component} does not match the "
                    "re-read sources"
                )
            if parse_finite_decimal(
                recorded["difference"],
                field="difference",
                context=f"{source} row {row.row_id} {component}",
            ) != difference.difference:
                raise LiraMappingError(
                    f"{source}: row {row.row_id} recorded reconstruction "
                    f"residual for {component} does not match the re-read "
                    "sources"
                )
    return {
        "mapping_fingerprint": mapping.fingerprint,
        "published_rows": len(bundle.published_records),
        "force_records": len(bundle.force_records),
        "coefficients": len(bundle.coefficients),
        "load_parameters": len(bundle.parameters),
        "reconstruction_status": report.status.value,
        "component_comparisons": report.component_comparisons,
        "matching_components": report.matching_components,
    }


def read_rsu_evidence(path: str | Path) -> RsuEvidenceBundle:
    """Read and re-verify one RSU evidence bundle, fail-closed."""

    source = Path(path).resolve(strict=True)
    root = read_json_object(source, "RSU evidence bundle")
    if root.get("kind") != EVIDENCE_KIND:
        raise LiraMappingError(
            f"{source}: kind must be {EVIDENCE_KIND!r}, got {root.get('kind')!r}"
        )
    status_token = root.get("status")
    if status_token != RsuValidationStatus.VERIFIED.value:
        raise LiraFormatError(
            f"{source}: RSU evidence status must be VERIFIED for a read-only "
            f"link, got {status_token!r}; a globally BLOCKED bundle is rejected"
        )
    recorded_blockers = root.get("blockers")
    if not isinstance(recorded_blockers, list) or recorded_blockers:
        raise LiraFormatError(
            f"{source}: a VERIFIED RSU evidence bundle must record no blockers, "
            f"got {recorded_blockers!r}"
        )
    mapping_fingerprint = require_text(root, "mapping_fingerprint", str(source))
    sources_block = require_mapping(root.get("sources"), "sources", str(source))
    if set(sources_block) != set(EVIDENCE_SOURCE_NAMES):
        raise LiraFormatError(
            f"{source}: sources must identify exactly "
            f"{', '.join(EVIDENCE_SOURCE_NAMES)}"
        )
    rechecked: dict[str, Mapping[str, object]] = {}
    for name in EVIDENCE_SOURCE_NAMES:
        entry = require_mapping(sources_block.get(name), name, str(source))
        rechecked[name] = verify_recorded_sha256(
            entry, name=name, context=str(source)
        )
    force_sources = _verify_force_pages(
        require_mapping(sources_block["forces"], "forces", str(source)),
        context=str(source),
        first_page=rechecked["forces"],
    )
    load_parameters = root.get("load_parameters")
    if not isinstance(load_parameters, list):
        raise LiraFormatError(f"{source}: load_parameters must be a list")
    parameters = tuple(
        require_mapping(item, "load parameter", str(source))
        for item in load_parameters
    )
    raw_rows = root.get("rows")
    if not isinstance(raw_rows, list):
        raise LiraFormatError(f"{source}: rows must be a list")
    published_records = root.get("published_records")
    if (
        isinstance(published_records, bool)
        or not isinstance(published_records, int)
        or published_records != len(raw_rows)
    ):
        raise LiraFormatError(
            f"{source}: published_records does not match the number of rows"
        )
    comparisons = root.get("component_comparisons")
    if (
        isinstance(comparisons, bool)
        or not isinstance(comparisons, int)
        or comparisons != len(RSU_COMPONENTS) * len(raw_rows)
    ):
        raise LiraFormatError(
            f"{source}: component_comparisons does not match rows x components"
        )
    matching = root.get("matching_components")
    if (
        isinstance(matching, bool)
        or not isinstance(matching, int)
        or matching != comparisons
    ):
        raise LiraFormatError(
            f"{source}: matching_components must equal component_comparisons "
            "for a VERIFIED bundle"
        )
    expected_hashes = {
        name: str(rechecked[name]["sha256"]) for name in EVIDENCE_SOURCE_NAMES
    }
    rows: list[RsuEvidenceRow] = []
    seen: set[str] = set()
    for index, raw in enumerate(raw_rows):
        row = _parse_row(
            raw,
            index=index,
            context=str(source),
            expected_hashes=expected_hashes,
            expected_force_hashes=force_sources.hashes,
            fingerprint=mapping_fingerprint,
        )
        if row.row_id in seen:
            raise LiraFormatError(f"{source}: duplicate RSU row_id {row.row_id!r}")
        seen.add(row.row_id)
        rows.append(row)
    if not rows:
        raise LiraFormatError(f"{source}: the evidence bundle contains no rows")
    source_recheck = _recheck_against_sources(
        source, root, sources_block, tuple(rows), parameters, force_sources
    )
    return RsuEvidenceBundle(
        path=source,
        sha256=sha256_file(source),
        status=str(status_token),
        mapping_fingerprint=mapping_fingerprint,
        sources=MappingProxyType(
            {name: dict(sources_block[name]) for name in EVIDENCE_SOURCE_NAMES}
        ),
        source_files_rechecked=MappingProxyType(rechecked),
        load_parameters=parameters,
        rows=tuple(rows),
        source_recheck=MappingProxyType(source_recheck),
    )
