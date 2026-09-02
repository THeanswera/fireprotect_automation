from __future__ import annotations

from decimal import Decimal
from hashlib import sha256
from html import escape
import json
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile

import pytest

from fireprotect.lira import (
    ConventionStatus,
    LiraBatchMapping,
    LiraConventionError,
    LiraFormatError,
    LiraMappingError,
    LiraRx3ComponentConvention,
    LiraRx3ConventionRegistry,
    import_lira_batch,
    prepare_lira_review_bundle,
)


FIXTURES = Path(__file__).parent / "fixtures" / "lira_review"
HEADERS = (
    "№ элем",
    "№ сечен",
    "N\n(т)",
    "Mk\n(т*м)",
    "My\n(т*м)",
    "Qz\n(т)",
    "Mz\n(т*м)",
    "Qy\n(т)",
    "Ry\n(т/м)",
    "Rz\n(т/м)",
    "Тип элем",
    "№ загруж",
    "Составл",
)
DEFAULT_ROWS = (
    (
        "56",
        "1",
        "-1.0617399999999999",
        "-1.5E-5",
        "-9.5680000000000001E-3",
        "0.25",
        "-0.1",
        "0",
        "0",
        "0",
        "10",
        "1",
        "-",
    ),
    (
        "56",
        "2",
        "0.14346700000000001",
        "0.008319",
        "3.1211730000000002",
        "-0.5",
        "0.170706",
        "0",
        "0",
        "0",
        "10",
        "1",
        "-",
    ),
)


def _payload() -> dict[str, object]:
    return json.loads((FIXTURES / "mapping.json").read_text(encoding="utf-8"))


def _mapping() -> LiraBatchMapping:
    return LiraBatchMapping.from_dict(_payload())


def _column_name(number: int) -> str:
    result = ""
    while number:
        number, remainder = divmod(number - 1, 26)
        result = chr(65 + remainder) + result
    return result


def _write_native_xlsx(
    path: Path,
    *,
    rows: tuple[tuple[str, ...], ...] = DEFAULT_ROWS,
    headers: tuple[str, ...] = HEADERS,
) -> None:
    header_cells = "".join(
        f'<c r="{_column_name(index)}3" t="inlineStr"><is><t>{escape(value)}</t></is></c>'
        for index, value in enumerate(headers, 1)
    )
    data_rows: list[str] = []
    for row_number, values in enumerate(rows, 4):
        cells = "".join(
            f'<c r="{_column_name(index)}{row_number}"><v>{escape(value)}</v></c>'
            for index, value in enumerate(values, 1)
        )
        data_rows.append(f'<row r="{row_number}">{cells}</row>')
    worksheet = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
        f'<sheetData><row r="3">{header_cells}</row>{"".join(data_rows)}</sheetData>'
        '</worksheet>'
    )
    workbook = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
        'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
        '<sheets><sheet name=" " sheetId="1" r:id="rId1"/></sheets></workbook>'
    )
    relationships = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        '<Relationship Id="rId1" '
        'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" '
        'Target="worksheets/sheet1.xml"/></Relationships>'
    )
    with ZipFile(path, "w", ZIP_DEFLATED) as archive:
        archive.writestr("xl/workbook.xml", workbook)
        archive.writestr("xl/_rels/workbook.xml.rels", relationships)
        archive.writestr("xl/worksheets/sheet1.xml", worksheet)


def test_mapping_models_native_lira_source_without_canonical_aliases() -> None:
    mapping = _mapping()

    assert tuple(mapping.native_forces) == ("N", "Mk", "My", "Mz", "Qy", "Qz")
    assert mapping.columns["section_station"] == "№ сечен"
    assert mapping.columns["profile"] is None
    assert mapping.worksheet_or_table == " "
    assert "Mx" not in mapping.native_forces
    assert "Qx" not in mapping.native_forces

    old_payload = _payload()
    old_payload["native_forces"] = {
        "N": "N",
        "Mx": "Mx",
        "My": "My",
        "Qx": "Qx",
        "Qy": "Qy",
        "Mz": "Mz",
    }
    with pytest.raises(LiraMappingError, match="native_forces"):
        LiraBatchMapping.from_dict(old_payload)


def test_native_import_preserves_raw_ooxml_tokens_and_exact_decimal_units(
    tmp_path: Path,
) -> None:
    source = tmp_path / "native.xlsx"
    _write_native_xlsx(source)

    result = import_lira_batch(source, _mapping())
    record = result.records[0]

    assert result.rows_parsed == 2
    assert result.rows_accepted == 2
    assert result.rows_rejected == 0
    assert result.worksheet_or_table == " "
    assert record.section_station == "1"
    assert record.metadata["profile"] is None
    assert not hasattr(record, "profile")
    assert not hasattr(record, "Mx")
    axial = record.native_forces["N"]
    assert axial.raw_token == "-1.0617399999999999"
    assert axial.parsed_decimal == Decimal("-1.0617399999999999")
    assert isinstance(axial.parsed_decimal, Decimal)
    assert axial.normalized_value == (
        Decimal("-1.0617399999999999") * Decimal("9.80665")
    )
    assert axial.normalized_unit == "kN"
    assert record.native_forces["Mk"].normalized_value == (
        Decimal("-1.5E-5") * Decimal("9.80665")
    )
    assert record.native_forces["Mk"].normalized_unit == "kN*m"
    assert record.native_results["Ry"].normalized_value is None
    assert record.metadata_provenance["section_station"]["source_cell"] == "B4"


def test_native_ascii_tf_units_convert_exactly_without_float(tmp_path: Path) -> None:
    source = tmp_path / "native.xlsx"
    _write_native_xlsx(source, rows=(DEFAULT_ROWS[0],))
    payload = _payload()
    payload["native_force_units"] = {
        "N": "tf",
        "Mk": "tf*m",
        "My": "tf*m",
        "Mz": "tf*m",
        "Qy": "tf",
        "Qz": "tf",
    }

    record = import_lira_batch(
        source,
        LiraBatchMapping.from_dict(payload),
    ).records[0]

    assert record.native_forces["N"].normalized_value == (
        Decimal("-1.0617399999999999") * Decimal("9.80665")
    )
    assert record.native_forces["Mk"].normalized_value == (
        Decimal("-1.5E-5") * Decimal("9.80665")
    )


def test_profile_identity_and_unknown_convention_block_project_elements(
    tmp_path: Path,
) -> None:
    source = tmp_path / "native.xlsx"
    _write_native_xlsx(source)

    result = import_lira_batch(source, _mapping())
    codes = {issue.code for issue in result.engineering_blockers}

    assert "LIRA_MEMBER_PROFILE_IDENTITY_MISSING" in codes
    assert "LIRA_LOAD_COMBINATION_IDENTITY_MISSING" in codes
    assert "LIRA_RX3_FORCE_CONVENTION" in codes
    assert result.project_elements == ()
    assert result.rx38_force_generation_allowed is False
    assert all(
        item.verification_status is ConventionStatus.UNKNOWN
        for item in result.convention.components.values()
    )


def test_statistics_zero_warnings_and_candidates_are_nonsemantic(
    tmp_path: Path,
) -> None:
    source = tmp_path / "native.xlsx"
    _write_native_xlsx(source)

    result = import_lira_batch(source, _mapping())

    assert result.unique_elements == 1
    assert result.distributions["section_station"] == {"1": 1, "2": 1}
    assert result.native_component_statistics["Qy"]["all_zero"] is True
    assert result.native_component_statistics["N"]["nonzero_count"] == 2
    zero_fields = {warning.field for warning in result.warnings}
    assert {"Qy", "Ry", "Rz"} <= zero_fields
    candidate = result.convention_candidates[0]
    assert candidate.element_id == "56"
    assert candidate.classification == "CANDIDATE_ONLY"
    assert {"N", "Mk", "My", "Mz", "Qz"} <= set(
        candidate.nonzero_native_components
    )
    assert "N" in candidate.sign_changing_native_components


def test_two_stations_are_not_duplicates_but_repeated_station_and_load_is(
    tmp_path: Path,
) -> None:
    source = tmp_path / "stations.xlsx"
    _write_native_xlsx(source)
    result = import_lira_batch(source, _mapping())
    assert not any(
        issue.code == "DUPLICATE_AMBIGUOUS_NATIVE_ROW"
        for issue in result.engineering_blockers
    )

    duplicate = tmp_path / "duplicate.xlsx"
    _write_native_xlsx(duplicate, rows=(DEFAULT_ROWS[0], DEFAULT_ROWS[0]))
    duplicate_result = import_lira_batch(duplicate, _mapping())
    assert any(
        issue.code == "DUPLICATE_AMBIGUOUS_NATIVE_ROW"
        for issue in duplicate_result.engineering_blockers
    )


def test_nonfinite_and_missing_mapped_header_fail_closed(tmp_path: Path) -> None:
    nonfinite = tmp_path / "nonfinite.xlsx"
    bad_row = list(DEFAULT_ROWS[0])
    bad_row[2] = "NaN"
    _write_native_xlsx(nonfinite, rows=(tuple(bad_row),))

    result = import_lira_batch(nonfinite, _mapping())
    assert result.rows_accepted == 0
    assert result.rows_rejected == 1
    assert "finite" in result.rejected_rows[0].reason
    assert any(
        issue.code == "INVALID_SOURCE_ROW"
        for issue in result.engineering_blockers
    )

    missing = tmp_path / "missing.xlsx"
    _write_native_xlsx(
        missing,
        headers=HEADERS[:-1],
        rows=(DEFAULT_ROWS[0][:-1],),
    )
    with pytest.raises(LiraMappingError, match="Составл"):
        import_lira_batch(missing, _mapping())


def test_default_registry_rejects_old_aliases_and_blocks_rx38() -> None:
    registry = LiraRx3ConventionRegistry.unresolved()
    available = {
        name: Decimal("1") for name in ("N", "Mk", "My", "Mz", "Qy", "Qz")
    }

    with pytest.raises(LiraMappingError, match="Unknown LIRA force component"):
        LiraRx3ComponentConvention(
            source_component="Mx",
            target_rx3_component=None,
            sign_multiplier=None,
            axis_interpretation=None,
            verification_status=ConventionStatus.UNKNOWN,
            evidence_reference=None,
        )
    with pytest.raises(LiraConventionError, match="LIRA_RX3_FORCE_CONVENTION"):
        registry.require_rx38_generation_ready(available)


def test_review_bundle_is_complete_decimal_safe_and_exclusive(tmp_path: Path) -> None:
    source = tmp_path / "native.xlsx"
    _write_native_xlsx(source)
    before = source.read_bytes()
    output = tmp_path / "review"

    bundle = prepare_lira_review_bundle(
        source,
        FIXTURES / "mapping.json",
        output,
    )

    expected = {
        "source_manifest.json",
        "mapping_snapshot.json",
        "import_summary.json",
        "forces.json",
        "forces_review.csv",
        "project_elements.json",
        "blockers.json",
        "audit.json",
        "README_REVIEW.md",
    }
    assert {path.name for path in bundle.files} == expected
    assert source.read_bytes() == before
    assert bundle.result.source_sha256 == sha256(before).hexdigest()
    assert bundle.result.project_elements == ()
    payload = json.loads((output / "forces.json").read_text(encoding="utf-8"))
    axial = payload["accepted_native_records"][0]["native_forces"]["N"]
    assert axial["raw_token"] == "-1.0617399999999999"
    assert axial["parsed_decimal"] == "-1.0617399999999999"
    assert "Mx" not in payload["accepted_native_records"][0]["native_forces"]
    blockers = json.loads((output / "blockers.json").read_text(encoding="utf-8"))
    assert any(
        item["code"] == "LIRA_MEMBER_PROFILE_IDENTITY_MISSING"
        for item in blockers["engineering_blockers"]
    )

    with pytest.raises(LiraFormatError, match="refusing to overwrite"):
        prepare_lira_review_bundle(source, FIXTURES / "mapping.json", output)
