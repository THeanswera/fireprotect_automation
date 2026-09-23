"""Explicit read-only import path for a published LIRA RSU evidence bundle.

The recorded ``status = VERIFIED`` is never trusted by itself. This import
re-verifies the bundle before any row may be linked to a model:

* the SHA-256 of every source XLS recorded under ``sources`` is recomputed
  from the file on disk and must match the recorded hash;
* for every row the six native components ``N/Mk/My/Mz/Qy/Qz`` are recomputed
  with exact ``Decimal`` arithmetic from the recorded source terms (load-case
  force value times the RSU coefficient column) and must equal the published
  value, including the sign;
* a row recorded as ``BLOCKED`` is never imported: a blocked reconstruction is
  a verification failure, not a lesser-quality row.

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
from .rsu import RsuValidationStatus
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
            if value.source_sha256 != expected_hashes["forces"]:
                raise LiraFormatError(
                    f"{row_context}: term {term.load_case_id} component "
                    f"{component} points to a different forces XLS"
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


def read_rsu_evidence(path: str | Path) -> RsuEvidenceBundle:
    """Read and re-verify one RSU evidence bundle, fail-closed."""

    source = Path(path).resolve(strict=True)
    root = read_json_object(source, "RSU evidence bundle")
    if root.get("kind") != EVIDENCE_KIND:
        raise LiraMappingError(
            f"{source}: kind must be {EVIDENCE_KIND!r}, got {root.get('kind')!r}"
        )
    status_token = root.get("status")
    if status_token not in (
        RsuValidationStatus.VERIFIED.value,
        RsuValidationStatus.BLOCKED.value,
    ):
        raise LiraFormatError(f"{source}: unknown RSU evidence status {status_token!r}")
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
            fingerprint=mapping_fingerprint,
        )
        if row.row_id in seen:
            raise LiraFormatError(f"{source}: duplicate RSU row_id {row.row_id!r}")
        seen.add(row.row_id)
        rows.append(row)
    if not rows:
        raise LiraFormatError(f"{source}: the evidence bundle contains no rows")
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
    )
