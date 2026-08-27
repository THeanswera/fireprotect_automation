from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from enum import Enum
from hashlib import sha256
from pathlib import Path
from types import MappingProxyType
from typing import Any, Generic, Mapping, TypeVar

import yaml

from .execution import ExecutionMode
from .release import BlockerCode, ReleaseBlocker


T = TypeVar("T")


@dataclass(frozen=True)
class NormativeTrace:
    document_id: str
    edition: str
    clause: str
    formula_or_table: str | None
    description: str

    def __post_init__(self) -> None:
        for name in ("document_id", "edition", "clause", "description"):
            value = getattr(self, name)
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"NormativeTrace.{name} must not be empty")
        if self.formula_or_table is not None and (
            not isinstance(self.formula_or_table, str)
            or not self.formula_or_table.strip()
        ):
            raise ValueError(
                "NormativeTrace.formula_or_table must be non-empty when provided"
            )


class NormativeInputError(ValueError):
    pass


class NormativeTraceRequiredError(NormativeInputError):
    """A normative result was about to be accepted without its source trace."""


class NormativeRegistryError(NormativeInputError):
    pass


class NormativeDocumentStatus(str, Enum):
    VERIFIED_CURRENT = "VERIFIED_CURRENT"
    IDENTIFICATION_REQUIRED = "IDENTIFICATION_REQUIRED"
    EDITION_VERIFICATION_REQUIRED = "EDITION_VERIFICATION_REQUIRED"
    UNVERIFIED = "UNVERIFIED"
    LEGACY_ONLY = "LEGACY_ONLY"


@dataclass(frozen=True, slots=True)
class NormativeDocument:
    document_id: str
    designation: str | None
    title: str
    edition: str | None
    status: NormativeDocumentStatus
    adopted_date: date | None
    effective_from: date | None
    effective_to: date | None
    path: Path | None
    sha256: str | None
    evidence: str | None

    def effective_on(self, calculation_date: date) -> bool:
        return (
            (self.effective_from is None or self.effective_from <= calculation_date)
            and (self.effective_to is None or calculation_date <= self.effective_to)
        )

    @property
    def production_allowed(self) -> bool:
        return self.status is NormativeDocumentStatus.VERIFIED_CURRENT


def _date(value: object, *, field: str) -> date | None:
    if value is None:
        return None
    if isinstance(value, date):
        return value
    if not isinstance(value, str):
        raise NormativeRegistryError(f"{field} must be an ISO date or null")
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise NormativeRegistryError(f"{field} must be an ISO date") from exc


def _file_hash(path: Path) -> str:
    digest = sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


class NormativeRegistry:
    def __init__(self, documents: Mapping[str, NormativeDocument], source: Path):
        self.documents = MappingProxyType(dict(documents))
        self.source = source

    @classmethod
    def load(cls, path: str | Path) -> "NormativeRegistry":
        source = Path(path).resolve(strict=True)
        try:
            payload = yaml.safe_load(source.read_text(encoding="utf-8"))
        except (OSError, yaml.YAMLError) as exc:
            raise NormativeRegistryError(f"Cannot read normative registry: {exc}") from exc
        if not isinstance(payload, Mapping) or not isinstance(payload.get("documents"), list):
            raise NormativeRegistryError("normative registry requires a documents list")
        documents: dict[str, NormativeDocument] = {}
        for raw in payload["documents"]:
            if not isinstance(raw, Mapping):
                raise NormativeRegistryError("normative document entries must be objects")
            document_id = str(raw.get("id", "")).strip()
            if not document_id or document_id in documents:
                raise NormativeRegistryError(f"Invalid or duplicate document id: {document_id!r}")
            raw_path = raw.get("path")
            document_path = None
            if raw_path is not None:
                document_path = (source.parent / str(raw_path)).resolve(strict=False)
            try:
                status = NormativeDocumentStatus(str(raw.get("status")))
            except ValueError as exc:
                raise NormativeRegistryError(
                    f"Unknown normative status for {document_id}"
                ) from exc
            title = str(raw.get("title", "")).strip()
            if not title:
                raise NormativeRegistryError(f"Document {document_id} has no title")
            documents[document_id] = NormativeDocument(
                document_id=document_id,
                designation=(
                    str(raw["designation"]).strip()
                    if raw.get("designation") is not None
                    else None
                ),
                title=title,
                edition=(str(raw["edition"]).strip() if raw.get("edition") else None),
                status=status,
                adopted_date=_date(raw.get("adopted_date"), field="adopted_date"),
                effective_from=_date(raw.get("effective_from"), field="effective_from"),
                effective_to=_date(raw.get("effective_to"), field="effective_to"),
                path=document_path,
                sha256=(str(raw["sha256"]).lower() if raw.get("sha256") else None),
                evidence=(str(raw["evidence"]).strip() if raw.get("evidence") else None),
            )
        return cls(documents, source)

    def require(self, document_id: str) -> NormativeDocument:
        try:
            return self.documents[document_id]
        except KeyError as exc:
            raise NormativeRegistryError(
                f"Normative document is not registered: {document_id}"
            ) from exc


@dataclass(frozen=True, slots=True)
class NormativeValidation:
    trace: NormativeTrace | None
    document: NormativeDocument | None
    blockers: tuple[ReleaseBlocker, ...]
    evidence: Mapping[str, Any]

    @property
    def valid_for_production(self) -> bool:
        if (
            self.trace is None
            or self.document is None
            or not self.document.production_allowed
            or self.document.effective_from is None
            or self.document.path is None
            or not self.document.path.is_file()
            or not self.document.sha256
            or self.blockers
            or self.trace.document_id != self.document.document_id
            or self.trace.edition != self.document.edition
        ):
            return False
        raw_calculation_date = self.evidence.get("calculation_date")
        if not isinstance(raw_calculation_date, str):
            return False
        try:
            calculation_date = date.fromisoformat(raw_calculation_date)
        except ValueError:
            return False
        if not self.document.effective_on(calculation_date):
            return False
        expected_hash = self.evidence.get("expected_sha256")
        actual_hash = self.evidence.get("actual_sha256")
        if expected_hash != self.document.sha256 or actual_hash != expected_hash:
            return False
        try:
            return _file_hash(self.document.path) == self.document.sha256
        except OSError:
            return False


def validate_normative_trace(
    trace: NormativeTrace | None,
    registry: NormativeRegistry,
    *,
    calculation_date: date,
    mode: ExecutionMode,
) -> NormativeValidation:
    if not isinstance(mode, ExecutionMode):
        raise TypeError("mode must be ExecutionMode")
    blockers: list[ReleaseBlocker] = []
    if trace is None:
        if mode is ExecutionMode.PRODUCTION:
            blockers.append(
                ReleaseBlocker(
                    BlockerCode.NORMATIVE_TRACE_MISSING,
                    "Production fire-resistance decision has no NormativeTrace",
                )
            )
        return NormativeValidation(None, None, tuple(blockers), {})
    document = registry.documents.get(trace.document_id)
    if document is None:
        blockers.append(
            ReleaseBlocker(
                BlockerCode.NORMATIVE_DOCUMENT_NOT_FOUND,
                f"Normative document {trace.document_id!r} is absent from registry",
            )
        )
        return NormativeValidation(trace, None, tuple(blockers), {})
    if mode is ExecutionMode.PRODUCTION and not document.production_allowed:
        blockers.append(
            ReleaseBlocker(
                BlockerCode.NORMATIVE_EDITION_UNVERIFIED,
                f"Document {document.document_id} status is {document.status.value}",
            )
        )
    if not document.effective_on(calculation_date):
        blockers.append(
            ReleaseBlocker(
                BlockerCode.NORMATIVE_DOCUMENT_NOT_EFFECTIVE,
                f"Document {document.document_id} is not effective on {calculation_date.isoformat()}",
            )
        )
    if mode is ExecutionMode.PRODUCTION and document.effective_from is None:
        blockers.append(
            ReleaseBlocker(
                BlockerCode.NORMATIVE_DOCUMENT_NOT_EFFECTIVE,
                f"Document {document.document_id} has no verified effective_from date",
            )
        )
    if document.edition is None or document.edition != trace.edition:
        blockers.append(
            ReleaseBlocker(
                BlockerCode.NORMATIVE_EDITION_UNVERIFIED,
                f"Trace edition {trace.edition!r} does not match registered edition {document.edition!r}",
            )
        )
    actual_hash = None
    if document.path is not None and document.path.exists() and document.sha256:
        actual_hash = _file_hash(document.path)
        if actual_hash != document.sha256:
            blockers.append(
                ReleaseBlocker(
                    BlockerCode.NORMATIVE_SOURCE_HASH_MISMATCH,
                    f"SHA-256 mismatch for {document.document_id}",
                )
            )
    elif mode is ExecutionMode.PRODUCTION:
        missing = []
        if document.path is None:
            missing.append("path")
        elif not document.path.exists():
            missing.append("local file")
        if not document.sha256:
            missing.append("registered SHA-256")
        blockers.append(
            ReleaseBlocker(
                BlockerCode.NORMATIVE_SOURCE_HASH_MISMATCH,
                f"Normative source evidence is incomplete for {document.document_id}: "
                + ", ".join(missing),
            )
        )
    evidence = {
        "document_id": document.document_id,
        "status": document.status.value,
        "calculation_date": calculation_date.isoformat(),
        "effective_from": (
            document.effective_from.isoformat() if document.effective_from else None
        ),
        "effective_to": (
            document.effective_to.isoformat() if document.effective_to else None
        ),
        "expected_sha256": document.sha256,
        "actual_sha256": actual_hash,
    }
    return NormativeValidation(trace, document, tuple(blockers), evidence)


def require_normative_trace(
    trace: NormativeTrace | None,
    *,
    production: bool = True,
    registry_validation: NormativeValidation | None = None,
) -> NormativeTrace | None:
    """Require traceability before a normative result is used in production.

    Exploratory calculations may explicitly pass ``production=False``.  They
    remain unconfirmed until a trace is attached.
    """

    if not isinstance(production, bool):
        raise TypeError("production must be bool")
    if trace is not None and not isinstance(trace, NormativeTrace):
        raise TypeError("trace must be NormativeTrace or None")
    if registry_validation is not None:
        if not isinstance(registry_validation, NormativeValidation):
            raise TypeError("registry_validation must be NormativeValidation or None")
        if registry_validation.trace != trace:
            raise ValueError("registry_validation does not match trace")
    if production and (
        trace is None
        or registry_validation is None
        or not registry_validation.valid_for_production
    ):
        raise NormativeTraceRequiredError(
            "A production normative result requires registry/date/hash-validated NormativeTrace"
        )
    return trace


@dataclass(frozen=True)
class NormativeResult(Generic[T]):
    """A calculation value coupled to its normative citation."""

    value: T
    trace: NormativeTrace | None
    registry_validation: NormativeValidation | None = None

    def __post_init__(self) -> None:
        if self.trace is not None and not isinstance(self.trace, NormativeTrace):
            raise TypeError("trace must be NormativeTrace or None")
        if self.registry_validation is not None:
            if not isinstance(self.registry_validation, NormativeValidation):
                raise TypeError("registry_validation must be NormativeValidation or None")
            if self.registry_validation.trace != self.trace:
                raise ValueError("registry_validation does not match NormativeResult.trace")

    @property
    def confirmed(self) -> bool:
        return (
            self.registry_validation is not None
            and self.registry_validation.valid_for_production
        )

    def validate(self, *, production: bool = True) -> NormativeResult[T]:
        require_normative_trace(
            self.trace,
            production=production,
            registry_validation=self.registry_validation,
        )
        if production and not self.confirmed:
            raise NormativeTraceRequiredError(
                "A production normative result requires successful registry/date/hash validation"
            )
        return self


NormativeCalculationResult = NormativeResult


@dataclass(frozen=True, slots=True)
class ComparisonResult(Generic[T]):
    normative: NormativeCalculationResult[T]
    rx3_value: T
    difference: T | None
    status: str
    trace: NormativeTrace


def require_engineering_input(name: str, value):
    """Block silent substitution of an engineering-significant value."""
    if value is None or value == "":
        raise NormativeInputError(
            f"Не задано обязательное инженерное значение: {name}. "
            "Автоматическая подстановка запрещена."
        )
    return value
