from __future__ import annotations

from dataclasses import replace
from decimal import Decimal
from hashlib import sha256
from pathlib import Path

import pytest

from fireprotect.lira import (
    LiraFormatError,
    LiraMappingError,
    RsuImportBundle,
    RsuValidationStatus,
    RsuXlsMapping,
    import_rsu_xls_bundle,
    read_xls_workbook,
    validate_rsu_reconstruction,
)


def _write_table(path: Path, sheets: list[tuple[str, list[list[object]]]]) -> None:
    xlwt = pytest.importorskip("xlwt")
    path.parent.mkdir(parents=True, exist_ok=True)
    workbook = xlwt.Workbook()
    for name, rows in sheets:
        sheet = workbook.add_sheet(name)
        for row_index, row in enumerate(rows):
            for column_index, value in enumerate(row):
                sheet.write(row_index, column_index, value)
    workbook.save(str(path))


def _force_header() -> list[str]:
    return [
        "№ элем", "№ сечен", "N\n(т)", "Mk\n(т*м)", "My\n(т*м)",
        "Qz\n(т)", "Mz\n(т*м)", "Qy\n(т)", "№ загруж",
    ]


def _force_row(element: int, station: int, load: int, my: float, qz: float) -> list[object]:
    return [element, station, 0.0, 0.0, my, qz, 0.0, 0.0, load]


def _published_header() -> list[str]:
    return [
        "№ элем", "№ сечен", "№ столбца", "Группа РСУ", "Критерий",
        "N\n(т)", "Mk\n(т*м)", "My\n(т*м)", "Qz\n(т)",
        "Mz\n(т*м)", "Qy\n(т)", "№№ загруж",
    ]


def _published_row(group: str, column: int, membership: str, my: float, qz: float) -> list[object]:
    return [1, 2, column, group, 1, 0.0, 0.0, my, qz, 0.0, 0.0, membership]


def _write_bundle(
    tmp_path: Path,
    *,
    published_rows: list[list[object]] | None = None,
    force_rows: dict[str, list[list[object]]] | None = None,
    coefficient_rows: list[list[object]] | None = None,
) -> tuple[Path, Path, Path, Path]:
    force_rows = force_rows or {
        "1": [_force_row(1, 2, 1, 1.5, 0.5)],
        "2": [_force_row(1, 2, 2, 3.0, 1.0)],
        "3": [_force_row(1, 2, 3, 4.5, 1.5)],
        "4": [_force_row(1, 2, 4, -6.0, -2.0)],
    }
    forces = tmp_path / "forces.xls"
    _write_table(forces, [(name, [["title"], [], _force_header(), *rows]) for name, rows in force_rows.items()])
    published = tmp_path / "published.xls"
    _write_table(
        published,
        [(" ", [["title"], [], _published_header(), *(published_rows or [
            _published_row("A1", 1, "1", 1.5, 0.5),
            _published_row("A1", 2, "1 2", 4.35, 1.45),
            _published_row("B1", 2, "1 2 3", 8.4, 2.8),
            _published_row("B1", 1, "1 4", -4.5, -1.5),
        ])])],
    )
    coefficients = tmp_path / "coefficients.xls"
    _write_table(
        coefficients,
        [(" ", [["title"], [], ["№ загр.", "1 основ.", "2 основ."], *(coefficient_rows or [
            [1, 1.0, 1.0], [2, 1.0, 0.95], [3, 1.0, 0.90], [4, 1.0, 0.90],
        ])])],
    )
    parameters = tmp_path / "parameters.xls"
    _write_table(
        parameters,
        [(" ", [["title"], [], ["№ загр.", "Имя загружения", "Взаимоискл."],
            [1, "G_DOWN", ""], [2, "Q_LONG_DOWN", ""],
            [3, "Q_ALT_DOWN", 1], [4, "Q_ALT_UP", 1]])],
    )
    return forces, published, coefficients, parameters


def _import(paths: tuple[Path, Path, Path, Path]) -> RsuImportBundle:
    forces, published, coefficients, parameters = paths
    return import_rsu_xls_bundle(
        forces_path=forces,
        published_path=published,
        coefficients_path=coefficients,
        parameters_path=parameters,
    )


def test_multi_sheet_xls_preserves_sheets_cells_units_and_source(tmp_path: Path) -> None:
    paths = _write_bundle(tmp_path)
    before = paths[0].read_bytes()
    workbook = read_xls_workbook(paths[0])
    bundle = _import(paths)
    assert [sheet.name for sheet in workbook.worksheets] == ["1", "2", "3", "4"]
    assert len(bundle.force_records) == 4
    assert [record.source_sheet for record in bundle.force_records] == ["1", "2", "3", "4"]
    value = bundle.force_records[0].vector.values["My"]
    assert (value.source_row, value.source_cell, value.source_header) == (4, "E4", "My\n(т*м)")
    assert value.source_unit == "tf*m"
    assert value.source_sha256 == sha256(before).hexdigest()
    assert paths[0].read_bytes() == before


def test_biff_numeric_decimal_has_no_invented_raw_token(tmp_path: Path) -> None:
    value = _import(_write_bundle(tmp_path)).force_records[0].vector.values["My"]
    assert value.value == Decimal("1.5")
    assert value.raw_token is None
    assert value.decimal_provenance == "BIFF_NUMERIC_VALUE_DECODED_NO_RAW_DECIMAL_TOKEN"


def test_reconstructs_1_1plus2_1plus2plus3_and_1plus4(tmp_path: Path) -> None:
    report = validate_rsu_reconstruction(
        _import(_write_bundle(tmp_path)),
        mutually_exclusive_sets=(frozenset({"3", "4"}),),
    )
    assert report.status is RsuValidationStatus.VERIFIED
    assert (report.component_comparisons, report.matching_components) == (24, 24)
    assert report.rx38_force_generation_allowed is False
    assert report.issue_readiness == "NOT_READY_FOR_ISSUE"
    my_values = {
        (item.published_record.rsu_group, item.published_record.load_case_membership):
        next(component for component in item.components if component.component == "My")
        for item in report.results
    }
    assert my_values[("A1", ("1",))].reconstructed == Decimal("1.5")
    assert my_values[("A1", ("1", "2"))].reconstructed == Decimal("4.350")
    assert my_values[("B1", ("1", "2", "3"))].reconstructed == Decimal("8.400")
    assert my_values[("B1", ("1", "4"))].reconstructed == Decimal("-4.5")
    assert all(len(item.components) == 6 for item in report.results)


def test_mutual_exclusion_blocks(tmp_path: Path) -> None:
    rows = [_published_row("B1", 1, "1 3 4", 0.0, 0.0)]
    report = validate_rsu_reconstruction(
        _import(_write_bundle(tmp_path, published_rows=rows)),
        mutually_exclusive_sets=(frozenset({"3", "4"}),),
    )
    assert "RSU_MUTUALLY_EXCLUSIVE_LOADS_COMBINED" in report.blockers


def test_missing_coefficient_and_source_load_block(tmp_path: Path) -> None:
    report = validate_rsu_reconstruction(_import(_write_bundle(
        tmp_path / "a",
        published_rows=[_published_row("A1", 2, "1 2", 4.35, 1.45)],
        coefficient_rows=[[1, 1.0, 1.0], [3, 1.0, 0.9], [4, 1.0, 0.9]],
    )))
    assert "RSU_COEFFICIENT_MISSING:2" in report.blockers
    report = validate_rsu_reconstruction(_import(_write_bundle(
        tmp_path / "b",
        published_rows=[_published_row("A1", 2, "1 2", 4.35, 1.45)],
        force_rows={"1": [_force_row(1, 2, 1, 1.5, 0.5)]},
    )))
    assert "RSU_SOURCE_LOAD_MISSING:2" in report.blockers


def test_ambiguous_source_blocks(tmp_path: Path) -> None:
    report = validate_rsu_reconstruction(_import(_write_bundle(
        tmp_path,
        published_rows=[_published_row("A1", 1, "1", 1.5, 0.5)],
        force_rows={"1": [_force_row(1, 2, 1, 1.5, 0.5), _force_row(1, 2, 1, 1.5, 0.5)]},
    )))
    assert "RSU_SOURCE_LOAD_AMBIGUOUS:1" in report.blockers


def test_result_mismatch_keeps_joint_vector(tmp_path: Path) -> None:
    report = validate_rsu_reconstruction(_import(_write_bundle(
        tmp_path, published_rows=[_published_row("A1", 2, "1 2", 4.36, 1.45)]
    )))
    result = report.results[0]
    assert "RSU_RESULT_MISMATCH:My" in result.blockers
    assert len(result.components) == 6
    difference = next(item for item in result.components if item.component == "My")
    assert difference.difference == Decimal("0.010")
    assert [term[0] for term in difference.source_terms] == ["1", "2"]


def test_incomplete_vector_and_sha_mismatch_fail_closed(tmp_path: Path) -> None:
    paths = _write_bundle(tmp_path)
    _write_table(paths[0], [("1", [["title"], [], _force_header(), [1, 2, 0.0, 0.0, 1.5, 0.5, 0.0, "", 1]])])
    with pytest.raises(LiraFormatError, match="Qy must be numeric"):
        _import(paths)
    valid = _write_bundle(tmp_path / "valid")
    with pytest.raises(LiraFormatError, match="SHA-256 mismatch"):
        import_rsu_xls_bundle(
            forces_path=valid[0], published_path=valid[1],
            coefficients_path=valid[2], parameters_path=valid[3],
            expected_sha256={"forces": "0" * 64},
        )


def test_unknown_column_is_not_guessed(tmp_path: Path) -> None:
    paths = _write_bundle(tmp_path, published_rows=[_published_row("A1", 3, "1", 1.5, 0.5)])
    with pytest.raises(LiraMappingError, match="no explicit coefficient column mapping"):
        _import(paths)


def test_mapping_fingerprint_and_safety_gates(tmp_path: Path) -> None:
    mapping = RsuXlsMapping()
    bundle = _import(_write_bundle(tmp_path))
    assert bundle.mapping_fingerprint == mapping.fingerprint
    assert bundle.rx38_force_generation_allowed is False
    assert bundle.issue_readiness == "NOT_READY_FOR_ISSUE"
    assert not hasattr(bundle, "rx38")
    altered = replace(mapping, coefficient_columns={1: "1 основ."})
    assert altered.fingerprint != mapping.fingerprint
