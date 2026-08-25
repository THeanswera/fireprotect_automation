from __future__ import annotations

import re
import sqlite3
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Literal

ProfileStatus = Literal["FOUND", "NOT_FOUND", "AMBIGUOUS"]


def normalize_profile_name(value: str) -> str:
    normalized = value.casefold().translate(str.maketrans({"х": "x", "×": "x", ",": "."}))
    return re.sub(r"\s+", "", normalized)


def normalize_standard(value: str) -> str:
    return re.sub(r"\s+", "", value.casefold().replace("\xa0", " "))


def _decimal(value: object) -> Decimal | None:
    if value is None or str(value).strip() == "":
        return None
    try:
        return Decimal(str(value).replace(",", "."))
    except InvalidOperation:
        return None


@dataclass(frozen=True)
class ProfileGeometry:
    height_mm: Decimal | None
    width_mm: Decimal | None
    web_thickness_mm: Decimal | None
    flange_thickness_mm: Decimal | None
    radius_1_mm: Decimal | None
    radius_2_mm: Decimal | None
    radius_3_mm: Decimal | None
    area_cm2: Decimal | None
    ix_cm4: Decimal | None
    wx_cm3: Decimal | None
    iy_cm4: Decimal | None
    wy_cm3: Decimal | None


@dataclass(frozen=True)
class ProfileCandidate:
    table: str
    standard: str
    designation: str
    geometry: ProfileGeometry
    source_record: dict[str, object]


@dataclass(frozen=True)
class ProfileSearchResult:
    status: ProfileStatus
    query: str
    candidates: tuple[ProfileCandidate, ...]

    @property
    def candidate(self) -> ProfileCandidate | None:
        return self.candidates[0] if self.status == "FOUND" else None


# Backwards-compatible representation used by the initial CLI.
@dataclass(frozen=True)
class ProfileHit:
    table: str
    values: dict[str, object]


def _quote_identifier(identifier: str) -> str:
    return '"' + identifier.replace('"', '""') + '"'


class ProfileRepository:
    def __init__(self, db_path: str | Path):
        self.db_path = Path(db_path)

    def tables(self) -> list[str]:
        return list_tables(self.db_path)

    def search(self, designation: str, standard: str | None = None) -> ProfileSearchResult:
        query = normalize_profile_name(designation)
        if not query:
            return ProfileSearchResult("NOT_FOUND", designation, ())
        standard_query = normalize_standard(standard) if standard else None
        candidates: list[ProfileCandidate] = []
        with sqlite3.connect(self.db_path) as connection:
            connection.row_factory = sqlite3.Row
            for table in self.tables():
                columns = table_columns(self.db_path, table)
                if "N_nd" not in columns or "nd" not in columns:
                    continue
                rows = connection.execute(f"SELECT * FROM {_quote_identifier(table)}").fetchall()
                for row in rows:
                    raw = dict(row)
                    if normalize_profile_name(str(raw.get("N_nd", ""))) != query:
                        continue
                    if standard_query and normalize_standard(str(raw.get("nd", ""))) != standard_query:
                        continue
                    candidates.append(self._candidate(table, raw))
        status: ProfileStatus = "NOT_FOUND" if not candidates else "FOUND" if len(candidates) == 1 else "AMBIGUOUS"
        return ProfileSearchResult(status, designation, tuple(candidates))

    @staticmethod
    def _candidate(table: str, row: dict[str, object]) -> ProfileCandidate:
        lower = {key.casefold(): value for key, value in row.items()}
        geometry = ProfileGeometry(
            height_mm=_decimal(lower.get("h")), width_mm=_decimal(lower.get("b")),
            web_thickness_mm=_decimal(lower.get("tw")), flange_thickness_mm=_decimal(lower.get("tf")),
            radius_1_mm=_decimal(lower.get("r1")), radius_2_mm=_decimal(lower.get("r2")),
            radius_3_mm=_decimal(lower.get("r3")), area_cm2=_decimal(lower.get("s")),
            ix_cm4=_decimal(lower.get("ix")), wx_cm3=_decimal(lower.get("wx")),
            iy_cm4=_decimal(lower.get("iy")), wy_cm3=_decimal(lower.get("wy")),
        )
        return ProfileCandidate(table, str(row["nd"]), str(row["N_nd"]), geometry, row)


def list_tables(db_path: str | Path) -> list[str]:
    with sqlite3.connect(db_path) as connection:
        rows = connection.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name").fetchall()
    return [row[0] for row in rows if row[0] != "sqlite_sequence"]


def table_columns(db_path: str | Path, table: str) -> list[str]:
    with sqlite3.connect(db_path) as connection:
        rows = connection.execute(f"PRAGMA table_info({_quote_identifier(table)})").fetchall()
    return [row[1] for row in rows]


def find_profile(db_path: str | Path, query: str) -> list[ProfileHit]:
    result = ProfileRepository(db_path).search(query)
    return [ProfileHit(candidate.table, candidate.source_record) for candidate in result.candidates]
