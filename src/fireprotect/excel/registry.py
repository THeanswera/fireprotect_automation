from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from enum import Enum
from hashlib import sha256
import json
from pathlib import Path
from types import MappingProxyType
from typing import Any, Mapping

import yaml

from ..technical import FireproofingTechnicalEntry
from .ooxml import OoxmlReadError, worksheet_content_fingerprint
from .writer import file_sha256


class ExcelTemplateRegistryError(ValueError):
    pass


class ExcelTemplateStatus(str, Enum):
    UNVERIFIED = "UNVERIFIED"
    APPROVED = "APPROVED"


def _digest(value: object, *, field: str) -> str:
    if not isinstance(value, str):
        raise ExcelTemplateRegistryError(f"{field} must be a SHA-256 digest")
    result = value.lower()
    if len(result) != 64 or any(char not in "0123456789abcdef" for char in result):
        raise ExcelTemplateRegistryError(f"{field} must be a SHA-256 digest")
    return result


def formula_map_fingerprint(formulas: Mapping[str, str]) -> str:
    payload = json.dumps(
        sorted(formulas.items()),
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode("utf-8")
    return sha256(payload).hexdigest()


@dataclass(frozen=True, slots=True)
class ExcelTemplateEntry:
    template_id: str
    sha256: str
    workbook_identity: str
    workbook_version: str
    expected_formula_count: int
    formula_map_sha256: str
    status: ExcelTemplateStatus
    approved_by: str | None
    approved_at: date | None
    technical_data_entry_id: str
    technical_data_version: str
    lookup_sheets: tuple[str, ...]
    lookup_table_sha256: str

    def __post_init__(self) -> None:
        for name in (
            "template_id",
            "workbook_identity",
            "workbook_version",
            "technical_data_entry_id",
            "technical_data_version",
        ):
            value = getattr(self, name)
            if not isinstance(value, str) or not value.strip():
                raise ExcelTemplateRegistryError(
                    f"ExcelTemplateEntry.{name} must not be empty"
                )
        object.__setattr__(self, "sha256", _digest(self.sha256, field="sha256"))
        object.__setattr__(
            self,
            "formula_map_sha256",
            _digest(self.formula_map_sha256, field="formula_map_sha256"),
        )
        object.__setattr__(
            self,
            "lookup_table_sha256",
            _digest(self.lookup_table_sha256, field="lookup_table_sha256"),
        )
        if isinstance(self.expected_formula_count, bool) or not isinstance(
            self.expected_formula_count, int
        ):
            raise ExcelTemplateRegistryError("expected_formula_count must be int")
        if self.expected_formula_count < 0:
            raise ExcelTemplateRegistryError(
                "expected_formula_count must not be negative"
            )
        if not isinstance(self.status, ExcelTemplateStatus):
            object.__setattr__(self, "status", ExcelTemplateStatus(self.status))
        if not self.lookup_sheets or any(
            not isinstance(sheet, str) or not sheet.strip()
            for sheet in self.lookup_sheets
        ):
            raise ExcelTemplateRegistryError("lookup_sheets must not be empty")
        if self.status is ExcelTemplateStatus.APPROVED and (
            not isinstance(self.approved_by, str)
            or not self.approved_by.strip()
            or self.approved_at is None
        ):
            raise ExcelTemplateRegistryError(
                "APPROVED template requires approved_by and approved_at"
            )


@dataclass(frozen=True, slots=True)
class ExcelTemplateVerification:
    template_id: str
    status: ExcelTemplateStatus
    source_sha256: str
    expected_sha256: str
    formula_count: int
    expected_formula_count: int
    formula_map_sha256: str
    expected_formula_map_sha256: str
    lookup_table_sha256: str
    expected_lookup_table_sha256: str
    technical_data_entry_id: str | None
    expected_technical_data_entry_id: str
    technical_data_version: str | None
    expected_technical_data_version: str

    @property
    def verified(self) -> bool:
        return (
            self.status is ExcelTemplateStatus.APPROVED
            and self.source_sha256 == self.expected_sha256
            and self.formula_count == self.expected_formula_count
            and self.formula_map_sha256 == self.expected_formula_map_sha256
            and self.lookup_table_sha256 == self.expected_lookup_table_sha256
            and self.technical_data_entry_id
            == self.expected_technical_data_entry_id
            and self.technical_data_version == self.expected_technical_data_version
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            "template_id": self.template_id,
            "registry_status": self.status.value,
            "verified": self.verified,
            "source_sha256": self.source_sha256,
            "expected_sha256": self.expected_sha256,
            "formula_count": self.formula_count,
            "expected_formula_count": self.expected_formula_count,
            "formula_map_sha256": self.formula_map_sha256,
            "expected_formula_map_sha256": self.expected_formula_map_sha256,
            "lookup_table_sha256": self.lookup_table_sha256,
            "expected_lookup_table_sha256": self.expected_lookup_table_sha256,
            "technical_data_entry_id": self.technical_data_entry_id,
            "expected_technical_data_entry_id": self.expected_technical_data_entry_id,
            "technical_data_version": self.technical_data_version,
            "expected_technical_data_version": self.expected_technical_data_version,
        }


def verify_excel_template(
    entry: ExcelTemplateEntry,
    workbook_path: str | Path,
    formulas: Mapping[str, str],
    technical_entry: FireproofingTechnicalEntry | None,
) -> ExcelTemplateVerification:
    if not isinstance(entry, ExcelTemplateEntry):
        raise TypeError("entry must be ExcelTemplateEntry")
    try:
        lookup_fingerprint = worksheet_content_fingerprint(
            workbook_path, entry.lookup_sheets
        )
    except OoxmlReadError as exc:
        raise ExcelTemplateRegistryError(
            f"Cannot fingerprint workbook lookup tables: {exc}"
        ) from exc
    return ExcelTemplateVerification(
        entry.template_id,
        entry.status,
        file_sha256(workbook_path),
        entry.sha256,
        len(formulas),
        entry.expected_formula_count,
        formula_map_fingerprint(formulas),
        entry.formula_map_sha256,
        lookup_fingerprint,
        entry.lookup_table_sha256,
        None if technical_entry is None else technical_entry.entry_id,
        entry.technical_data_entry_id,
        None if technical_entry is None else technical_entry.version,
        entry.technical_data_version,
    )


def _date(value: object, *, field: str) -> date | None:
    if value is None:
        return None
    if isinstance(value, date):
        return value
    if not isinstance(value, str):
        raise ExcelTemplateRegistryError(f"{field} must be an ISO date or null")
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise ExcelTemplateRegistryError(f"{field} must be an ISO date") from exc


class ExcelTemplateRegistry:
    def __init__(self, entries: Mapping[str, ExcelTemplateEntry], source: Path):
        self.entries = MappingProxyType(dict(entries))
        self.source = source

    @classmethod
    def load(cls, path: str | Path) -> "ExcelTemplateRegistry":
        source = Path(path).resolve(strict=True)
        try:
            payload = yaml.safe_load(source.read_text(encoding="utf-8"))
        except (OSError, yaml.YAMLError) as exc:
            raise ExcelTemplateRegistryError(
                f"Cannot read Excel template registry: {exc}"
            ) from exc
        if not isinstance(payload, Mapping) or not isinstance(
            payload.get("templates"), list
        ):
            raise ExcelTemplateRegistryError(
                "Excel template registry requires a templates list"
            )
        entries: dict[str, ExcelTemplateEntry] = {}
        for raw in payload["templates"]:
            if not isinstance(raw, Mapping):
                raise ExcelTemplateRegistryError(
                    "Excel template entries must be objects"
                )
            template_id = str(raw.get("template_id", "")).strip()
            if not template_id or template_id in entries:
                raise ExcelTemplateRegistryError(
                    f"Invalid or duplicate template_id: {template_id!r}"
                )
            raw_sheets = raw.get("lookup_sheets")
            if not isinstance(raw_sheets, list):
                raise ExcelTemplateRegistryError(
                    f"{template_id}.lookup_sheets must be a list"
                )
            raw_formula_count = raw.get("expected_formula_count")
            if isinstance(raw_formula_count, bool) or not isinstance(
                raw_formula_count, int
            ):
                raise ExcelTemplateRegistryError(
                    f"{template_id}.expected_formula_count must be int"
                )
            entries[template_id] = ExcelTemplateEntry(
                template_id=template_id,
                sha256=str(raw.get("sha256", "")),
                workbook_identity=str(raw.get("workbook_identity", "")),
                workbook_version=str(raw.get("workbook_version", "")),
                expected_formula_count=raw_formula_count,
                formula_map_sha256=str(raw.get("formula_map_sha256", "")),
                status=ExcelTemplateStatus(str(raw.get("status"))),
                approved_by=raw.get("approved_by"),
                approved_at=_date(raw.get("approved_at"), field="approved_at"),
                technical_data_entry_id=str(
                    raw.get("technical_data_entry_id", "")
                ),
                technical_data_version=str(raw.get("technical_data_version", "")),
                lookup_sheets=tuple(str(sheet) for sheet in raw_sheets),
                lookup_table_sha256=str(raw.get("lookup_table_sha256", "")),
            )
        return cls(entries, source)

    def require(self, template_id: str) -> ExcelTemplateEntry:
        try:
            return self.entries[template_id]
        except KeyError as exc:
            raise ExcelTemplateRegistryError(
                f"Excel template is not registered: {template_id}"
            ) from exc
