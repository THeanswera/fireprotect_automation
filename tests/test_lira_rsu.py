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
    read_xls_workbook,
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


def test_evidence_bundle_refuses_paged_forces(tmp_path: Path) -> None:
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
    destination = tmp_path / "review"
    with pytest.raises(LiraFormatError, match="exactly one forces workbook"):
        prepare_rsu_review_bundle(bundle, report, destination)
    assert not destination.exists()


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
    with pytest.raises(LiraMappingError, match="fully verified"):
        validate_rsu_selection(prepared["evidence"], selection_path)
