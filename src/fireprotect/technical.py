from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from enum import Enum
from hashlib import sha256
from pathlib import Path
from types import MappingProxyType
from typing import Mapping

import yaml

from .execution import ExecutionMode


class TechnicalRegistryError(ValueError):
    pass


class TechnicalDataStatus(str, Enum):
    VERIFIED_TECHNICAL_DATA = "VERIFIED_TECHNICAL_DATA"
    UNVERIFIED_TECHNICAL_DATA = "UNVERIFIED_TECHNICAL_DATA"


@dataclass(frozen=True, slots=True)
class FireproofingTechnicalEntry:
    entry_id: str
    manufacturer: str | None
    system_name: str | None
    product_name: str | None
    component_name: str | None
    certificate_number: str | None
    certificate_valid_from: date | None
    certificate_valid_to: date | None
    technical_specification: str | None
    technological_regulation: str | None
    fire_test_protocol: str | None
    applicability_document: str | None
    steel_profile_type: str | None
    ptm_range: str | None
    fire_resistance: str | None
    required_thickness: str | None
    specific_consumption: str | None
    primer_compatibility: str | None
    coating_type: str | None
    application_method: str | None
    layer_requirements: str | None
    density: str | None
    environmental_restrictions: str | None
    source_document: str | None
    source_page_or_table: str | None
    document_path: Path | None
    document_sha256: str | None
    status: TechnicalDataStatus
    note: str | None

    @property
    def verified_for_production(self) -> bool:
        primary_references = (
            self.certificate_number,
            self.technical_specification,
            self.technological_regulation,
            self.fire_test_protocol,
            self.applicability_document,
        )
        required = (
            self.manufacturer,
            self.product_name or self.system_name,
            self.source_document,
            self.source_page_or_table,
            self.document_sha256,
            self.steel_profile_type,
            self.ptm_range,
            self.fire_resistance,
            self.required_thickness,
            self.specific_consumption,
        )
        if (
            self.status is not TechnicalDataStatus.VERIFIED_TECHNICAL_DATA
            or not all(required)
            or not any(primary_references)
            or self.document_path is None
            or not self.document_path.exists()
        ):
            return False
        return _file_hash(self.document_path) == self.document_sha256

    def verified_for_production_on(self, calculation_date: date) -> bool:
        if not isinstance(calculation_date, date):
            raise TypeError("calculation_date must be date")
        if not self.verified_for_production:
            return False
        if self.certificate_number is None:
            return True
        return (
            self.certificate_valid_from is not None
            and self.certificate_valid_to is not None
            and self.certificate_valid_from
            <= calculation_date
            <= self.certificate_valid_to
        )


def _date(value: object, *, field: str) -> date | None:
    if value is None:
        return None
    if isinstance(value, date):
        return value
    if not isinstance(value, str):
        raise TechnicalRegistryError(f"{field} must be an ISO date or null")
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise TechnicalRegistryError(f"{field} must be an ISO date") from exc


def _file_hash(path: Path) -> str:
    digest = sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


class FireproofingTechnicalRegistry:
    def __init__(self, entries: Mapping[str, FireproofingTechnicalEntry], source: Path):
        self.entries = MappingProxyType(dict(entries))
        self.source = source

    @classmethod
    def load(cls, path: str | Path) -> "FireproofingTechnicalRegistry":
        source = Path(path).resolve(strict=True)
        try:
            payload = yaml.safe_load(source.read_text(encoding="utf-8"))
        except (OSError, yaml.YAMLError) as exc:
            raise TechnicalRegistryError(f"Cannot read technical registry: {exc}") from exc
        if not isinstance(payload, Mapping) or not isinstance(payload.get("solutions"), list):
            raise TechnicalRegistryError("technical registry requires a solutions list")
        entries: dict[str, FireproofingTechnicalEntry] = {}
        for raw in payload["solutions"]:
            if not isinstance(raw, Mapping):
                raise TechnicalRegistryError("technical entries must be objects")
            entry_id = str(raw.get("id", "")).strip()
            if not entry_id or entry_id in entries:
                raise TechnicalRegistryError(f"Invalid or duplicate technical id: {entry_id!r}")
            try:
                status = TechnicalDataStatus(str(raw.get("status")))
            except ValueError as exc:
                raise TechnicalRegistryError(
                    f"Unknown technical status for {entry_id}"
                ) from exc

            def optional(name: str) -> str | None:
                value = raw.get(name)
                return str(value).strip() if value is not None else None

            raw_path = optional("path")
            document_hash = optional("document_sha256")
            document_path = (
                (source.parent / raw_path).resolve(strict=False)
                if raw_path is not None
                else None
            )

            entries[entry_id] = FireproofingTechnicalEntry(
                entry_id,
                optional("manufacturer"),
                optional("system_name"),
                optional("product_name"),
                optional("component_name"),
                optional("certificate_number"),
                _date(
                    raw.get("certificate_valid_from"),
                    field="certificate_valid_from",
                ),
                _date(
                    raw.get("certificate_valid_to"),
                    field="certificate_valid_to",
                ),
                optional("technical_specification"),
                optional("technological_regulation"),
                optional("fire_test_protocol"),
                optional("applicability_document"),
                optional("steel_profile_type"),
                optional("ptm_range"),
                optional("fire_resistance"),
                optional("required_thickness"),
                optional("specific_consumption"),
                optional("primer_compatibility"),
                optional("coating_type"),
                optional("application_method"),
                optional("layer_requirements"),
                optional("density"),
                optional("environmental_restrictions"),
                optional("source_document"),
                optional("source_page_or_table"),
                document_path,
                document_hash.lower() if document_hash is not None else None,
                status,
                optional("note"),
            )
        return cls(entries, source)

    def require_for_selection(
        self,
        entry_id: str,
        *,
        mode: ExecutionMode,
        calculation_date: date | None = None,
    ) -> FireproofingTechnicalEntry:
        if not isinstance(mode, ExecutionMode):
            raise TypeError("mode must be ExecutionMode")
        try:
            entry = self.entries[entry_id]
        except KeyError as exc:
            raise TechnicalRegistryError(
                f"Fireproofing solution is not registered: {entry_id}"
            ) from exc
        if (
            mode is ExecutionMode.PRODUCTION
            and (
                calculation_date is None
                or not entry.verified_for_production_on(calculation_date)
            )
        ):
            raise TechnicalRegistryError(
                f"Production thickness selection blocked: {entry_id} lacks verified primary technical evidence"
            )
        return entry
