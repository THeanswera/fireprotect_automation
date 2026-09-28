from __future__ import annotations

from dataclasses import replace
from decimal import Decimal
from hashlib import sha256
import json
from pathlib import Path
import sys

import pytest

from fireprotect.cli import main
from fireprotect.lira import (
    LiraFormatError,
    LiraMappingError,
    RsuImportBundle,
    RsuValidationStatus,
    RsuXlsMapping,
    import_rsu_xls_bundle,
    prepare_rsu_review_bundle,
    read_rsu_evidence,
    read_xls_workbook,
    rsu_residual_statistics,
    rsu_row_detail,
    validate_rsu_reconstruction,
    validate_rsu_selection,
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
    """A duplicate row inside one page is refused while the page is read."""

    with pytest.raises(LiraFormatError, match="describe element '1', section '2', load case '1' twice"):
        _import(_write_bundle(
            tmp_path,
            force_rows={"1": [_force_row(1, 2, 1, 1.5, 0.5), _force_row(1, 2, 1, 1.5, 0.5)]},
        ))


def test_ambiguous_source_bundle_still_blocks(tmp_path: Path) -> None:
    """The reconstruction layer keeps its own ambiguity guard."""

    bundle = _import(_write_bundle(tmp_path))
    duplicated = replace(
        bundle, force_records=(*bundle.force_records, bundle.force_records[0])
    )
    report = validate_rsu_reconstruction(duplicated)
    assert "RSU_SOURCE_LOAD_AMBIGUOUS:1" in report.blockers


def _write_pages(
    tmp_path: Path, pages: list[dict[str, list[list[object]]]]
) -> list[Path]:
    paths: list[Path] = []
    for index, sheets in enumerate(pages, 1):
        path = tmp_path / f"forces-{index}page.xls"
        _write_table(
            path,
            [(name, [["title"], [], _force_header(), *rows]) for name, rows in sheets.items()],
        )
        paths.append(path)
    return paths


def test_paged_forces_keep_each_page_as_its_own_source(tmp_path: Path) -> None:
    pages = _write_pages(
        tmp_path,
        [
            {"1": [_force_row(1, 2, 1, 1.5, 0.5)], "2": [_force_row(1, 2, 2, 3.0, 1.0)]},
            {"3": [_force_row(1, 2, 3, 4.5, 1.5)], "4": [_force_row(1, 2, 4, -6.0, -2.0)]},
        ],
    )
    aux = _write_bundle(tmp_path / "aux")
    bundle = import_rsu_xls_bundle(
        forces_path=pages,
        published_path=aux[1],
        coefficients_path=aux[2],
        parameters_path=aux[3],
    )
    first = sha256(pages[0].read_bytes()).hexdigest()
    second = sha256(pages[1].read_bytes()).hexdigest()
    assert [book.source_file for book in bundle.forces_workbooks] == [
        str(path.resolve()) for path in pages
    ]
    assert [record.load_case_id for record in bundle.force_records] == ["1", "2", "3", "4"]
    assert [record.source_sha256 for record in bundle.force_records] == [
        first, first, second, second,
    ]
    assert [record.source_sheet for record in bundle.force_records] == ["1", "2", "3", "4"]
    assert bundle.force_records[2].vector.values["My"].source_sha256 == second
    report = validate_rsu_reconstruction(
        bundle, mutually_exclusive_sets=(frozenset({"3", "4"}),)
    )
    assert report.status is RsuValidationStatus.VERIFIED
    assert report.component_comparisons == 24


def test_overlapping_pages_are_refused(tmp_path: Path) -> None:
    pages = _write_pages(
        tmp_path,
        [
            {"1": [_force_row(1, 2, 1, 1.5, 0.5)]},
            {"1": [_force_row(1, 2, 1, 1.5, 0.5)]},
        ],
    )
    aux = _write_bundle(tmp_path / "aux")
    with pytest.raises(LiraFormatError, match="twice"):
        import_rsu_xls_bundle(
            forces_path=pages,
            published_path=aux[1],
            coefficients_path=aux[2],
            parameters_path=aux[3],
        )


def test_empty_force_page_is_refused(tmp_path: Path) -> None:
    page = tmp_path / "empty.xls"
    _write_table(page, [("1", [["title"], [], _force_header()])])
    aux = _write_bundle(tmp_path / "aux")
    with pytest.raises(LiraFormatError, match="no data rows"):
        import_rsu_xls_bundle(
            forces_path=[page],
            published_path=aux[1],
            coefficients_path=aux[2],
            parameters_path=aux[3],
        )


def test_pinned_forces_hash_is_refused_for_several_pages(tmp_path: Path) -> None:
    pages = _write_pages(
        tmp_path,
        [{"1": [_force_row(1, 2, 1, 1.5, 0.5)]}, {"2": [_force_row(1, 2, 2, 3.0, 1.0)]}],
    )
    aux = _write_bundle(tmp_path / "aux")
    with pytest.raises(LiraMappingError, match="pins exactly one workbook"):
        import_rsu_xls_bundle(
            forces_path=pages,
            published_path=aux[1],
            coefficients_path=aux[2],
            parameters_path=aux[3],
            expected_sha256={"forces": "0" * 64},
        )


def _paged_evidence(tmp_path: Path) -> tuple[Path, list[Path]]:
    pages = _write_pages(
        tmp_path,
        [
            {"1": [_force_row(1, 2, 1, 1.5, 0.5)], "2": [_force_row(1, 2, 2, 3.0, 1.0)]},
            {"3": [_force_row(1, 2, 3, 4.5, 1.5)], "4": [_force_row(1, 2, 4, -6.0, -2.0)]},
        ],
    )
    aux = _write_bundle(tmp_path / "aux")
    bundle = import_rsu_xls_bundle(
        forces_path=pages,
        published_path=aux[1],
        coefficients_path=aux[2],
        parameters_path=aux[3],
    )
    report = validate_rsu_reconstruction(
        bundle, mutually_exclusive_sets=(frozenset({"3", "4"}),)
    )
    assert report.status is RsuValidationStatus.VERIFIED
    prepared = prepare_rsu_review_bundle(bundle, report, tmp_path / "review")
    return Path(str(prepared["evidence"])), pages


def _rewrite_evidence(path: Path, mutate: object) -> None:
    payload = json.loads(path.read_text(encoding="utf-8"))
    mutate(payload)  # type: ignore[operator]
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )


def test_paged_evidence_records_every_page_and_reads_back(tmp_path: Path) -> None:
    evidence_path, pages = _paged_evidence(tmp_path)
    first = sha256(pages[0].read_bytes()).hexdigest()
    second = sha256(pages[1].read_bytes()).hexdigest()
    evidence = json.loads(evidence_path.read_text(encoding="utf-8"))
    forces = evidence["sources"]["forces"]
    assert forces["sha256"] == first
    assert forces["page_count"] == 2
    assert forces["pages"] == [
        {
            "path": str(pages[0].resolve()),
            "sha256": first,
            "sheets": ["1", "2"],
            "sheets_with_records": ["1", "2"],
            "records": 2,
            "elements_min": 1,
            "elements_max": 1,
            "load_cases": ["1", "2"],
            "section_stations": ["2"],
        },
        {
            "path": str(pages[1].resolve()),
            "sha256": second,
            "sheets": ["3", "4"],
            "sheets_with_records": ["3", "4"],
            "records": 2,
            "elements_min": 1,
            "elements_max": 1,
            "load_cases": ["3", "4"],
            "section_stations": ["2"],
        },
    ]

    bundle = read_rsu_evidence(evidence_path)
    assert bundle.status == "VERIFIED"
    assert len(bundle.rows) == 4
    assert bundle.source_files_rechecked["forces"]["sha256"] == first
    assert bundle.rows[0].source_terms[0].forces["My"].source_sha256 == first
    assert bundle.rows[2].source_terms[2].forces["My"].source_sha256 == second
    assert bundle.rows[3].source_terms[1].forces["My"].source_sha256 == second
    assert [term.load_case_id for term in bundle.rows[2].source_terms] == ["1", "2", "3"]
    assert bundle.source_recheck["force_records"] == 4


def test_single_page_evidence_keeps_the_flat_sources_shape(tmp_path: Path) -> None:
    bundle = _import(_write_bundle(tmp_path))
    prepared = prepare_rsu_review_bundle(
        bundle, validate_rsu_reconstruction(bundle), tmp_path / "review"
    )
    evidence_path = Path(str(prepared["evidence"]))
    evidence = json.loads(evidence_path.read_text(encoding="utf-8"))
    assert set(evidence["sources"]["forces"]) == {"path", "sha256", "sheets"}
    assert evidence["status"] == "VERIFIED"
    assert evidence["bounded_components"] == 0
    rechecked = read_rsu_evidence(evidence_path)
    assert rechecked.status == "VERIFIED"
    assert rechecked.exact_reconstruction is True
    assert rechecked.bounded_components == 0


def test_evidence_bundle_keeps_the_bounded_status_and_reads_back(
    tmp_path: Path,
) -> None:
    """A row verified within the export precision is usable, but never called exact."""

    bundle = _import(_three_term_n_bundle(tmp_path, -144.754105))
    report = validate_rsu_reconstruction(bundle)
    assert report.status is RsuValidationStatus.VERIFIED_WITHIN_EXPORT_PRECISION
    prepared = prepare_rsu_review_bundle(bundle, report, tmp_path / "review")
    evidence_path = Path(str(prepared["evidence"]))
    evidence = json.loads(evidence_path.read_text(encoding="utf-8"))
    assert evidence["status"] == "VERIFIED_WITHIN_EXPORT_PRECISION"
    assert evidence["blockers"] == []
    assert evidence["bounded_components"] == 1
    assert evidence["bounded_rows"] == 1
    assert evidence["matching_components"] == 5
    component = evidence["rows"][0]["reconstruction"]["N"]
    assert component["published"] == "-144.754105"
    assert component["reconstructed"] == "-144.7541130"
    assert component["difference"] == "0.0000080"
    assert component["export_precision"]["within_export_precision"] is True
    assert component["export_precision"]["budget"]["bound"] == "0.0000172587890625"

    rechecked = read_rsu_evidence(evidence_path)
    assert rechecked.status == "VERIFIED_WITHIN_EXPORT_PRECISION"
    assert rechecked.exact_reconstruction is False
    assert rechecked.bounded_components == 1
    assert rechecked.source_recheck["force_records"] == 3


def test_a_bundle_claiming_the_bound_for_a_gross_error_is_refused(
    tmp_path: Path,
) -> None:
    """The recorded classification is recomputed from the sources, never trusted."""

    bundle = _import(_three_term_n_bundle(tmp_path, -144.754105))
    prepared = prepare_rsu_review_bundle(
        bundle,
        validate_rsu_reconstruction(bundle),
        tmp_path / "review",
    )
    evidence_path = Path(str(prepared["evidence"]))

    def widen(payload: dict[str, object]) -> None:
        row = payload["rows"][0]  # type: ignore[index]
        row["published_vector"]["N"]["value"] = "-140.0"
        row["reconstruction"]["N"]["published"] = "-140.0"
        row["reconstruction"]["N"]["difference"] = "-4.7541130"
        row["reconstruction"]["N"]["export_precision"] = {
            "budget": {
                "print_window": "2.0",
                "single_precision_ulp": "15.0",
                "scale": "140",
                "bound": "17.0",
            },
            "within_export_precision": True,
            "excess": "0",
            "excess_in_ulp32": "0",
        }

    _rewrite_evidence(evidence_path, widen)
    with pytest.raises(LiraFormatError, match="beyond the export-precision bound"):
        read_rsu_evidence(evidence_path)


def test_a_blocked_bundle_is_still_refused(tmp_path: Path) -> None:
    bundle = _import(_three_term_n_bundle(tmp_path, -140.0))
    report = validate_rsu_reconstruction(bundle)
    assert report.status is RsuValidationStatus.BLOCKED
    prepared = prepare_rsu_review_bundle(bundle, report, tmp_path / "review")
    evidence_path = Path(str(prepared["evidence"]))
    evidence = json.loads(evidence_path.read_text(encoding="utf-8"))
    assert evidence["status"] == "BLOCKED"
    with pytest.raises(LiraFormatError, match="globally BLOCKED bundle is rejected"):
        read_rsu_evidence(evidence_path)


def test_paged_evidence_refuses_a_page_changed_after_writing(tmp_path: Path) -> None:
    evidence_path, pages = _paged_evidence(tmp_path)
    _write_table(
        pages[1], [("3", [["title"], [], _force_header(), _force_row(1, 2, 3, 4.5, 1.5)])]
    )
    with pytest.raises(LiraMappingError, match="changed"):
        read_rsu_evidence(evidence_path)


def test_paged_evidence_refuses_a_page_missing_from_the_recorded_list(
    tmp_path: Path,
) -> None:
    evidence_path, _ = _paged_evidence(tmp_path)

    def drop_second_page(payload: dict[str, object]) -> None:
        forces = payload["sources"]["forces"]  # type: ignore[index]
        del forces["pages"][1]  # type: ignore[index]
        forces["page_count"] = 1  # type: ignore[index]

    _rewrite_evidence(evidence_path, drop_second_page)
    with pytest.raises(LiraFormatError, match="not recorded"):
        read_rsu_evidence(evidence_path)


def test_paged_evidence_refuses_a_wrong_page_count(tmp_path: Path) -> None:
    evidence_path, _ = _paged_evidence(tmp_path)

    def break_page_count(payload: dict[str, object]) -> None:
        payload["sources"]["forces"]["page_count"] = 3  # type: ignore[index]

    _rewrite_evidence(evidence_path, break_page_count)
    with pytest.raises(LiraFormatError, match="page_count"):
        read_rsu_evidence(evidence_path)


def test_paged_evidence_refuses_the_same_page_twice(tmp_path: Path) -> None:
    evidence_path, pages = _paged_evidence(tmp_path)

    def duplicate_first_page(payload: dict[str, object]) -> None:
        forces = payload["sources"]["forces"]  # type: ignore[index]
        forces["pages"][1] = dict(forces["pages"][0])  # type: ignore[index]

    _rewrite_evidence(evidence_path, duplicate_first_page)
    with pytest.raises(LiraFormatError, match="more than one page"):
        read_rsu_evidence(evidence_path)


def test_paged_evidence_reverifies_every_recorded_page_summary(
    tmp_path: Path,
) -> None:
    evidence_path, _ = _paged_evidence(tmp_path)
    bundle = read_rsu_evidence(evidence_path)
    recheck = bundle.source_recheck
    assert recheck["force_metadata_status"] == "PAGE_SUMMARY_RE_VERIFIED"
    pages = recheck["page_verification"]
    assert [item["page"] for item in pages] == [1, 2]
    for item in pages:
        assert item["metadata_status"] == "PAGE_SUMMARY_RE_VERIFIED"
        assert item["sha256"]["matches"] is True
        assert set(item["summary"]) == {
            "sheets",
            "sheets_with_records",
            "records",
            "elements_min",
            "elements_max",
            "load_cases",
            "section_stations",
        }
        assert all(entry["matches"] is True for entry in item["summary"].values())
    assert pages[0]["summary"]["sheets"] == {
        "recorded": ["1", "2"],
        "actual": ["1", "2"],
        "matches": True,
    }
    assert pages[1]["summary"]["load_cases"]["recorded"] == ["3", "4"]


def test_paged_evidence_refuses_substituted_page_metadata(tmp_path: Path) -> None:
    for key, value in (
        ("load_cases", ["99"]),
        ("sheets", ["9"]),
        ("records", 5),
        ("elements_min", 4),
        ("elements_max", None),
        ("section_stations", ["1"]),
    ):
        case = tmp_path / key
        case.mkdir()
        evidence_path, _ = _paged_evidence(case)

        def tamper(payload: dict[str, object], key: str = key, value: object = value) -> None:
            payload["sources"]["forces"]["pages"][1][key] = value  # type: ignore[index]

        _rewrite_evidence(evidence_path, tamper)
        with pytest.raises(LiraFormatError, match="metadata was substituted"):
            read_rsu_evidence(evidence_path)


def test_paged_evidence_refuses_a_substituted_flat_worksheet_count(
    tmp_path: Path,
) -> None:
    evidence_path, _ = _paged_evidence(tmp_path)

    def tamper(payload: dict[str, object]) -> None:
        payload["sources"]["forces"]["sheets"] = 9  # type: ignore[index]

    _rewrite_evidence(evidence_path, tamper)
    with pytest.raises(LiraFormatError, match="worksheets for the first page"):
        read_rsu_evidence(evidence_path)


def test_paged_evidence_requires_an_integer_worksheet_count(tmp_path: Path) -> None:
    """The paged shape is produced with an integer count; a wrong type is refused."""

    for value in ("2", True, None, [2], 2.0):
        case = tmp_path / f"sheets-{type(value).__name__}-{value!r}".replace(
            "[", ""
        ).replace("]", "")
        case.mkdir()
        evidence_path, _ = _paged_evidence(case)

        def tamper(payload: dict[str, object], value: object = value) -> None:
            payload["sources"]["forces"]["sheets"] = value  # type: ignore[index]

        _rewrite_evidence(evidence_path, tamper)
        with pytest.raises(LiraFormatError, match="worksheet count"):
            read_rsu_evidence(evidence_path)


def test_paged_evidence_requires_a_recorded_worksheet_count(tmp_path: Path) -> None:
    evidence_path, _ = _paged_evidence(tmp_path)

    def drop_sheets(payload: dict[str, object]) -> None:
        del payload["sources"]["forces"]["sheets"]  # type: ignore[index]

    _rewrite_evidence(evidence_path, drop_sheets)
    with pytest.raises(LiraFormatError, match="must record the worksheet count"):
        read_rsu_evidence(evidence_path)


def test_flat_evidence_without_a_worksheet_count_still_reads(tmp_path: Path) -> None:
    """The legacy flat shape keeps working when the old optional field is absent."""

    bundle = _import(_write_bundle(tmp_path))
    prepared = prepare_rsu_review_bundle(
        bundle, validate_rsu_reconstruction(bundle), tmp_path / "review"
    )
    evidence_path = Path(str(prepared["evidence"]))

    def drop_sheets(payload: dict[str, object]) -> None:
        del payload["sources"]["forces"]["sheets"]  # type: ignore[index]

    _rewrite_evidence(evidence_path, drop_sheets)
    rechecked = read_rsu_evidence(evidence_path).source_recheck
    assert rechecked["force_metadata_status"] == "SINGLE_WORKBOOK_FLAT_SOURCES_SHAPE"
    assert rechecked["page_verification"][0]["metadata_status"] == (
        "SUMMARY_NOT_PINNED_LEGACY_FLAT_SHAPE"
    )


def test_paged_evidence_refuses_a_page_without_a_pinned_summary(
    tmp_path: Path,
) -> None:
    evidence_path, _ = _paged_evidence(tmp_path)

    def drop_key(payload: dict[str, object]) -> None:
        del payload["sources"]["forces"]["pages"][0]["elements_min"]  # type: ignore[index]

    _rewrite_evidence(evidence_path, drop_key)
    with pytest.raises(LiraFormatError, match="SUMMARY_NOT_VERIFIED"):
        read_rsu_evidence(evidence_path)


def test_paged_evidence_refuses_a_swapped_page(tmp_path: Path) -> None:
    evidence_path, pages = _paged_evidence(tmp_path)
    pages[1].write_bytes(pages[0].read_bytes())
    with pytest.raises(LiraMappingError, match="changed"):
        read_rsu_evidence(evidence_path)


def test_paged_evidence_refuses_swapped_page_entries(tmp_path: Path) -> None:
    evidence_path, _ = _paged_evidence(tmp_path)

    def swap_entries(payload: dict[str, object]) -> None:
        pages = payload["sources"]["forces"]["pages"]  # type: ignore[index]
        pages[0], pages[1] = pages[1], pages[0]

    _rewrite_evidence(evidence_path, swap_entries)
    with pytest.raises(LiraFormatError, match="first force page sha256"):
        read_rsu_evidence(evidence_path)


def test_flat_sources_shape_rechecks_the_recorded_worksheet_count(
    tmp_path: Path,
) -> None:
    bundle = _import(_write_bundle(tmp_path))
    prepared = prepare_rsu_review_bundle(
        bundle, validate_rsu_reconstruction(bundle), tmp_path / "review"
    )
    evidence_path = Path(str(prepared["evidence"]))
    page = read_rsu_evidence(evidence_path).source_recheck["page_verification"][0]
    assert page["metadata_status"] == "FLAT_SOURCES_SHAPE_WORKSHEET_COUNT_RE_VERIFIED"
    assert page["summary"]["sheets"]["matches"] is True
    assert page["summary"]["load_cases"]["matches"] is None
    assert "not an independent check" in page["summary_note"]

    def break_sheets(payload: dict[str, object]) -> None:
        payload["sources"]["forces"]["sheets"] = 7  # type: ignore[index]

    _rewrite_evidence(evidence_path, break_sheets)
    with pytest.raises(LiraFormatError, match="worksheets"):
        read_rsu_evidence(evidence_path)


def test_paged_evidence_refuses_a_non_integer_record_count(tmp_path: Path) -> None:
    evidence_path, _ = _paged_evidence(tmp_path)

    def break_records(payload: dict[str, object]) -> None:
        payload["sources"]["forces"]["pages"][0]["records"] = "2"  # type: ignore[index]

    _rewrite_evidence(evidence_path, break_records)
    with pytest.raises(LiraFormatError, match="records must be a positive integer"):
        read_rsu_evidence(evidence_path)


def test_residual_statistics_state_the_acceptance_rule(tmp_path: Path) -> None:
    report = validate_rsu_reconstruction(_import(_write_bundle(
        tmp_path, published_rows=[_published_row("A1", 2, "1 2", 4.36, 1.45)]
    )))
    statistics = rsu_residual_statistics(report)
    assert statistics["kind"] == "READ_ONLY_LIRA_RSU_RESIDUAL_STATISTICS"
    acceptance = statistics["acceptance"]
    assert acceptance["exact_status"] == "VERIFIED"
    assert acceptance["bounded_status"] == "VERIFIED_WITHIN_EXPORT_PRECISION"
    assert acceptance["exact_equality_required_for_verified"] is True
    assert acceptance["numeric_policy_installed"] is True
    assert "only the row status" in acceptance["policy_scope"]
    assert "half a unit of the last printed digit" in acceptance["bound"]
    assert statistics["components"]["My"]["mismatches"] == 1
    assert statistics["components"]["My"]["max_abs_difference"] == "0.010"
    # The fixture prints its terms with one decimal and 4.36 with two, so the
    # 0.01 residual is inside the print window of the compared values.
    assert statistics["components"]["My"]["within_export_precision"] == 1
    assert statistics["components"]["My"]["beyond_export_precision"] == 0
    assert set(statistics["components"]) == {"My"}
    assert statistics["counts"]["exact_rows"] == 0
    assert statistics["counts"]["within_export_precision_rows"] == 1
    assert statistics["counts"]["blocked_rows"] == 0
    assert statistics["overall"]["components_beyond_bound"] == 0

    matching = validate_rsu_reconstruction(_import(_write_bundle(tmp_path / "clean")))
    assert rsu_residual_statistics(matching)["components"] == {}
    assert rsu_residual_statistics(matching)["counts"]["exact_rows"] == 4


def _three_term_n_bundle(tmp_path: Path, published_n: float) -> tuple[Path, Path, Path, Path]:
    """A three-load-case N row printed to six decimals, as the real export is."""

    force_rows = {
        "1": [[1, 2, -5.269833, 0.0, 0.0, 0.0, 0.0, 0.0, 1]],
        "2": [[1, 2, -105.088051, 0.0, 0.0, 0.0, 0.0, 0.0, 2]],
        "3": [[1, 2, -34.396229, 0.0, 0.0, 0.0, 0.0, 0.0, 3]],
    }
    published_rows = [
        [1, 2, 1, "A1", 2, published_n, 0.0, 0.0, 0.0, 0.0, 0.0, "1 2 3"]
    ]
    return _write_bundle(
        tmp_path, published_rows=published_rows, force_rows=force_rows
    )


def test_the_real_like_six_decimal_residual_is_usable_but_not_exact(
    tmp_path: Path,
) -> None:
    """The residual measured on the real export (8e-6 on N) is inside the bound."""

    report = validate_rsu_reconstruction(
        _import(_three_term_n_bundle(tmp_path, -144.754105))
    )
    entry = rsu_residual_statistics(report)["components"]["N"]
    assert entry["mismatches"] == 1
    assert entry["max_abs_difference"] == "0.0000080"
    assert entry["within_export_precision"] == 1
    assert entry["beyond_export_precision"] == 0
    assert report.status is RsuValidationStatus.VERIFIED_WITHIN_EXPORT_PRECISION
    assert report.results[0].status is (
        RsuValidationStatus.VERIFIED_WITHIN_EXPORT_PRECISION
    )
    difference = next(
        item for item in report.results[0].components if item.component == "N"
    )
    assert difference.published == Decimal("-144.754105")
    assert difference.reconstructed == Decimal("-144.7541130")
    assert difference.difference == Decimal("0.0000080")
    assert difference.export_precision is not None
    # 2e-6 print window + 1 ulp32 of 144.75 (1.526e-5) = 1.726e-5.
    assert difference.export_precision.budget.print_window == Decimal("0.000002")
    assert difference.export_precision.budget.bound == Decimal("0.0000172587890625")


def test_a_gross_residual_is_far_beyond_the_bound_and_blocks_the_row(
    tmp_path: Path,
) -> None:
    """A wrong value, coefficient or row is orders of magnitude beyond the bound."""

    report = validate_rsu_reconstruction(
        _import(_three_term_n_bundle(tmp_path, -140.0))
    )
    entry = rsu_residual_statistics(report)["components"]["N"]
    assert entry["beyond_export_precision"] == 1
    assert entry["within_export_precision"] == 0
    assert float(entry["max_excess_in_ulp32"]) > 1000
    assert rsu_residual_statistics(report)["overall"]["components_beyond_bound"] == 1
    assert report.status is RsuValidationStatus.BLOCKED
    assert "RSU_RESULT_MISMATCH:N" in report.blockers


def test_row_detail_reports_terms_coefficients_and_exact_differences(tmp_path: Path) -> None:
    bundle = _import(_write_bundle(tmp_path))
    report = validate_rsu_reconstruction(bundle)
    detail = rsu_row_detail(bundle, report, ("R0002", "R9999"))
    rows = {row["row_id"]: row for row in detail["rows"]}
    assert detail["kind"] == "READ_ONLY_LIRA_RSU_ROW_DETAIL"
    assert rows["R9999"]["found"] is False
    row = rows["R0002"]
    assert row["status"] == "VERIFIED"
    assert row["identity"]["load_case_membership"] == ["1", "2"]
    assert row["published_vector"]["My"]["cell"] == "H5"
    assert [term["load_case_id"] for term in row["terms"]] == ["1", "2"]
    assert row["terms"][0]["force"]["values"]["My"]["value"] == "1.5"
    assert row["terms"][0]["coefficient"]["coefficient"] == "1.0"
    assert row["reconstruction"]["My"]["difference"] == "0.000"
    assert row["load_case_incomplete"] == []
    assert detail["rx38_force_generation_allowed"] is False
    assert detail["issue_readiness"] == "NOT_READY_FOR_ISSUE"


def test_cli_reports_every_force_page(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    pages = _write_pages(
        tmp_path,
        [
            {"1": [_force_row(1, 2, 1, 1.5, 0.5)], "2": [_force_row(1, 2, 2, 3.0, 1.0)]},
            {"3": [_force_row(1, 2, 3, 4.5, 1.5)], "4": [_force_row(1, 2, 4, -6.0, -2.0)]},
        ],
    )
    aux = _write_bundle(tmp_path / "aux")
    report_path = tmp_path / "report.json"
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "fireprotect", "validate-lira-rsu",
            "--forces", *(str(path) for path in pages),
            "--published", str(aux[1]),
            "--coefficients", str(aux[2]),
            "--parameters", str(aux[3]),
            "--report", str(report_path),
        ],
    )
    main()
    payload = json.loads(capsys.readouterr().out)
    assert payload["status"] == "VERIFIED"
    assert payload["force_records"] == 4
    assert payload["published_records"] == 4
    assert payload["verified_rows"] == 4
    assert payload["blocked_rows"] == 0
    assert [page["sha256"] for page in payload["force_pages"]] == [
        sha256(path.read_bytes()).hexdigest() for path in pages
    ]
    assert [page["elements_min"] for page in payload["force_pages"]] == [1, 1]
    assert payload["component_mismatches"] == {
        "N": 0, "Mk": 0, "My": 0, "Mz": 0, "Qy": 0, "Qz": 0,
    }
    assert payload["blocker_row_counts"] == {}
    assert payload["issue_readiness"] == "NOT_READY_FOR_ISSUE"
    assert json.loads(report_path.read_text(encoding="utf-8"))["status"] == "VERIFIED"


def test_result_within_export_precision_keeps_joint_vector(tmp_path: Path) -> None:
    """A residual inside the print window is usable, but it is not exact."""

    report = validate_rsu_reconstruction(_import(_write_bundle(
        tmp_path, published_rows=[_published_row("A1", 2, "1 2", 4.36, 1.45)]
    )))
    result = report.results[0]
    assert result.status is RsuValidationStatus.VERIFIED_WITHIN_EXPORT_PRECISION
    assert result.blockers == ()
    assert len(result.components) == 6
    difference = next(item for item in result.components if item.component == "My")
    assert difference.difference == Decimal("0.010")
    assert [term[0] for term in difference.source_terms] == ["1", "2"]
    assert difference.export_precision is not None
    assert difference.export_precision.within is True
    # The fixture prints the terms with one decimal and 4.36 with two:
    # 0.05 + 0.05 + 0.005 = 0.105.
    assert difference.export_precision.budget.print_window == Decimal("0.105")
    assert difference.export_precision.budget.bound == Decimal("0.105") + (
        difference.export_precision.budget.single_precision_ulp
    )
    assert report.status is RsuValidationStatus.VERIFIED_WITHIN_EXPORT_PRECISION
    assert report.bounded_components == 1
    assert report.bounded_rows == 1


def test_result_beyond_export_precision_still_blocks_the_row(tmp_path: Path) -> None:
    report = validate_rsu_reconstruction(_import(_write_bundle(
        tmp_path, published_rows=[_published_row("A1", 2, "1 2", 5.0, 1.45)]
    )))
    result = report.results[0]
    assert result.status is RsuValidationStatus.BLOCKED
    assert "RSU_RESULT_MISMATCH:My" in result.blockers
    difference = next(item for item in result.components if item.component == "My")
    assert difference.difference == Decimal("0.65")
    assert difference.export_precision is not None
    assert difference.export_precision.within is False
    assert difference.export_precision.excess > 0
    assert difference.export_precision.excess_in_ulp32 is not None
    assert difference.export_precision.excess_in_ulp32 > 1
    assert report.status is RsuValidationStatus.BLOCKED
    assert report.bounded_components == 0


def test_exact_rows_stay_verified(tmp_path: Path) -> None:
    report = validate_rsu_reconstruction(_import(_write_bundle(tmp_path)))
    assert report.status is RsuValidationStatus.VERIFIED
    assert report.matching_components == report.component_comparisons
    assert report.bounded_components == 0
    assert report.bounded_rows == 0
    assert all(item.status is RsuValidationStatus.VERIFIED for item in report.results)


def test_bundle_status_is_the_weakest_row_status(tmp_path: Path) -> None:
    rows = [
        _published_row("A1", 1, "1", 1.5, 0.5),
        _published_row("A1", 2, "1 2", 4.36, 1.45),
    ]
    report = validate_rsu_reconstruction(_import(_write_bundle(tmp_path, published_rows=rows)))
    assert report.results[0].status is RsuValidationStatus.VERIFIED
    assert report.results[1].status is RsuValidationStatus.VERIFIED_WITHIN_EXPORT_PRECISION
    assert report.status is RsuValidationStatus.VERIFIED_WITHIN_EXPORT_PRECISION

    blocked = validate_rsu_reconstruction(_import(_write_bundle(
        tmp_path / "blocked",
        published_rows=[*rows, _published_row("B1", 2, "1 2 3", 8.4, 99.0)],
    )))
    assert blocked.status is RsuValidationStatus.BLOCKED
    assert any(blocker.startswith("RSU_RESULT_MISMATCH") for blocker in blocked.blockers)


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


def test_missing_and_duplicate_load_parameters_block(tmp_path: Path) -> None:
    paths = _write_bundle(tmp_path / "missing")
    _write_table(paths[3], [(" ", [["title"], [], ["№ загр.", "Взаимоискл."]])])
    report = validate_rsu_reconstruction(_import(paths))
    assert report.status is RsuValidationStatus.BLOCKED
    assert "RSU_LOAD_PARAMETER_MISSING:1" in report.blockers
    assert report.component_comparisons == 0

    paths = _write_bundle(tmp_path / "duplicate")
    _write_table(paths[3], [(" ", [
        ["title"], [], ["№ загр.", "Взаимоискл."],
        [1, ""], [1, ""], [2, ""], [3, 1], [4, 1],
    ])])
    report = validate_rsu_reconstruction(_import(paths))
    assert report.status is RsuValidationStatus.BLOCKED
    assert "RSU_LOAD_PARAMETER_AMBIGUOUS:1" in report.blockers


def test_rsu_review_bundle_and_explicit_single_row_selection(tmp_path: Path) -> None:
    paths = _write_bundle(tmp_path / "input")
    bundle = _import(paths)
    report = validate_rsu_reconstruction(bundle)
    prepared = prepare_rsu_review_bundle(bundle, report, tmp_path / "review")
    evidence_path = Path(str(prepared["evidence"]))
    evidence = json.loads(evidence_path.read_text(encoding="utf-8"))
    assert len(evidence["rows"]) == 4
    assert evidence["sources"]["forces"]["sha256"] == sha256(paths[0].read_bytes()).hexdigest()
    row = evidence["rows"][1]
    assert row["row_id"] == "R0002"
    assert row["identity"]["load_case_membership"] == ["1", "2"]
    assert set(row["published_vector"]) == {"N", "Mk", "My", "Mz", "Qy", "Qz"}
    assert row["published_vector"]["My"]["value"] == "4.35"
    assert row["published_vector"]["My"]["raw_token"] is None
    assert row["published_vector"]["My"]["decimal_provenance"] == (
        "BIFF_NUMERIC_VALUE_DECODED_NO_RAW_DECIMAL_TOKEN"
    )
    assert row["reconstruction"]["My"]["difference"] == "0.000"
    assert len(row["source_terms"]) == 2
    assert row["source_terms"][0]["forces"]["My"]["cell"] == "E4"
    assert evidence["rows"][3]["published_vector"]["My"]["value"] == "-4.5"
    assert prepared["rx38_force_generation_allowed"] is False

    template = json.loads(Path(str(prepared["selection_template"])).read_text(encoding="utf-8"))
    template.update({
        "declared_by": "Engineer A",
        "basis": "Selected in LIRA from the published RSU table",
        "lira_gui_reference": "LIRA screenshot 2026-09-22",
        "element_id": "1",
        "rsu_row_id": "R0002",
    })
    selection_path = tmp_path / "selection.json"
    selection_path.write_text(json.dumps(template, ensure_ascii=False), encoding="utf-8")
    selection = validate_rsu_selection(evidence_path, selection_path)
    assert selection["selected_row"]["identity"] == row["identity"]
    assert selection["governing_result_selection_validated"] is False
    assert selection["rx38_force_generation_allowed"] is False
    assert selection["issue_readiness"] == "NOT_READY_FOR_ISSUE"
    with pytest.raises(LiraFormatError, match="already exists"):
        prepare_rsu_review_bundle(bundle, report, tmp_path / "review")


def test_rsu_selection_rejects_drift_and_wrong_identity(tmp_path: Path) -> None:
    paths = _write_bundle(tmp_path / "input")
    bundle = _import(paths)
    prepared = prepare_rsu_review_bundle(
        bundle, validate_rsu_reconstruction(bundle), tmp_path / "review"
    )
    evidence_path = Path(str(prepared["evidence"]))
    template = json.loads(Path(str(prepared["selection_template"])).read_text(encoding="utf-8"))
    template.update({
        "declared_by": "Engineer A", "basis": "LIRA result",
        "lira_gui_reference": "screenshot", "element_id": "9", "rsu_row_id": "R0002",
    })
    selection_path = tmp_path / "selection.json"
    selection_path.write_text(json.dumps(template), encoding="utf-8")
    with pytest.raises(LiraMappingError, match="another element"):
        validate_rsu_selection(evidence_path, selection_path)

    template["element_id"] = "1"
    template["rsu_row_id"] = "R9999"
    selection_path.write_text(json.dumps(template), encoding="utf-8")
    with pytest.raises(LiraMappingError, match="does not resolve uniquely"):
        validate_rsu_selection(evidence_path, selection_path)

    template["rsu_row_id"] = "R0002"
    template["rows"] = ["R0003"]
    selection_path.write_text(json.dumps(template), encoding="utf-8")
    with pytest.raises(LiraMappingError, match="exactly one row declaration"):
        validate_rsu_selection(evidence_path, selection_path)
    del template["rows"]

    template["lira_gui_reference"] = None
    selection_path.write_text(json.dumps(template), encoding="utf-8")
    with pytest.raises(LiraMappingError, match="lira_gui_reference"):
        validate_rsu_selection(evidence_path, selection_path)

    template["lira_gui_reference"] = "screenshot"
    selection_path.write_text(json.dumps(template), encoding="utf-8")
    paths[0].write_bytes(paths[0].read_bytes() + b"changed")
    with pytest.raises(LiraMappingError, match="source changed"):
        validate_rsu_selection(evidence_path, selection_path)


def test_rsu_selection_rejects_blocked_reconstruction(tmp_path: Path) -> None:
    paths = _write_bundle(tmp_path / "input")
    _write_table(paths[3], [(" ", [["title"], [], ["№ загр.", "Взаимоискл."]])])
    bundle = _import(paths)
    prepared = prepare_rsu_review_bundle(
        bundle, validate_rsu_reconstruction(bundle), tmp_path / "review"
    )
    template = json.loads(Path(str(prepared["selection_template"])).read_text(encoding="utf-8"))
    template.update({
        "declared_by": "Engineer A", "basis": "LIRA result",
        "lira_gui_reference": "screenshot", "element_id": "1", "rsu_row_id": "R0002",
    })
    selection_path = tmp_path / "selection.json"
    selection_path.write_text(json.dumps(template), encoding="utf-8")
    with pytest.raises(LiraMappingError, match="re-verified read-only bundle"):
        validate_rsu_selection(prepared["evidence"], selection_path)
