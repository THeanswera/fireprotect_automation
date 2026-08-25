import sqlite3
from pathlib import Path

import pytest

from fireprotect.rx3.profiles import ProfileRepository, normalize_profile_name


@pytest.fixture
def profile_db(tmp_path: Path) -> Path:
    path = tmp_path / "profiles.sqlite"
    schema = """CREATE TABLE {table} (
        ind INTEGER PRIMARY KEY, nd TEXT, N_nd TEXT, h TEXT, b TEXT, tw TEXT,
        tf TEXT, r1 TEXT, r2 TEXT, r3 TEXT, slope TEXT, s TEXT,
        ix TEXT, wx TEXT, iy TEXT, wy TEXT
    )"""
    with sqlite3.connect(path) as connection:
        for table in ("dv", "pr_tr", "ug"):
            connection.execute(schema.format(table=table))
        rows = {
            "dv": [
                (1, "ГОСТ 26020-83", "30К1", "300", "300", "10", "15", "0", "0", "0", "0", "100", "10000", "1000", "5000", "500"),
                (2, "СТО АСЧМ 20-93", "30 К1", "298", "299", "9", "14", "18", "0", "0", "0", "110,8", "18849", "1265,1", "6240,9", "417,5"),
                (3, "ГОСТ 26020-83", "18Б2", "180", "90", "5", "8", "0", "0", "0", "0", "22", "1300", "145", "100", "22"),
                (4, "СТО АСЧМ 20-93", "18 Б2", "180", "91", "5,3", "8", "9", "0", "0", "0", "22,4", "1317", "146,3", "100,8", "22,2"),
            ],
            "pr_tr": [
                (1, "ГОСТ 30245-2003", "160x160x8", "160", "160", "8", "8", "20", "20", "0", "0", "46,44", "1740", "217,5", "1740", "217,5"),
                (2, "ГОСТ Р 54157-2010", "160х160х8", "160", "160", "8", "8", "20", "0", "0", "0", "46,44", "1741", "217,6", "1741", "217,6"),
                (3, "ГОСТ 30245-2003", "120x120x6", "120", "120", "6", "6", "15", "15", "0", "0", "26", "500", "80", "500", "80"),
                (4, "EN 10219-2-2006", "120 × 120 × 6", "120", "120", "6", "6", "0", "0", "0", "0", "26", "501", "81", "501", "81"),
                (5, "EN 10219-2-2006", "100 x 100 x 10", "100", "100", "10", "10", "0", "0", "0", "0", "31", "400", "70", "400", "70"),
            ],
            "ug": [
                (1, "ГОСТ 8509-93", "100x100x10", "100", "100", "10", "10", "12", "0", "0", "0", "19", "180", "35", "180", "35"),
            ],
        }
        placeholders = ",".join("?" for _ in range(16))
        for table, values in rows.items():
            connection.executemany(f"INSERT INTO {table} VALUES ({placeholders})", values)
    return path


@pytest.mark.parametrize(
    ("variant", "canonical"),
    [
        ("30 К1", "30к1"), ("30К1", "30к1"), ("30 к1", "30к1"),
        ("160x160x8", "160x160x8"), ("160х160х8", "160x160x8"),
        ("160×160×8", "160x160x8"), ("100x100x10,0", "100x100x10.0"),
    ],
)
def test_normalization(variant, canonical):
    assert normalize_profile_name(variant) == canonical


@pytest.mark.parametrize("designation", ["30 К1", "30К1", "30 к1", "18 Б2"])
def test_rolled_profiles_without_standard_are_ambiguous(profile_db, designation):
    result = ProfileRepository(profile_db).search(designation)
    assert result.status == "AMBIGUOUS"
    assert result.candidate is None
    assert {candidate.table for candidate in result.candidates} == {"dv"}


@pytest.mark.parametrize("designation", ["160x160x8", "160х160х8", "160×160×8", "120x120x6", "100x100x10"])
def test_hollow_or_shape_profiles_report_ambiguity(profile_db, designation):
    result = ProfileRepository(profile_db).search(designation)
    assert result.status == "AMBIGUOUS"


def test_standard_resolves_ambiguous_profile(profile_db):
    result = ProfileRepository(profile_db).search("160х160х8", "ГОСТ 30245-2003")
    assert result.status == "FOUND"
    assert result.candidate is not None
    assert str(result.candidate.geometry.area_cm2) == "46.44"


@pytest.mark.parametrize("designation", ["30 К1", "18 Б2"])
def test_sto_resolves_rolled_profile(profile_db, designation):
    result = ProfileRepository(profile_db).search(designation, "СТО АСЧМ 20-93")
    assert result.status == "FOUND"
    assert result.candidate is not None
    assert result.candidate.table == "dv"
