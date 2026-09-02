from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from hashlib import sha256
import json
from pathlib import Path

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
from fireprotect.model import ProjectElement, ProvenanceType, ValueProvenance


FIXTURES = Path(__file__).parent / "fixtures" / "lira_review"


def _payload() -> dict[str, object]:
    return json.loads((FIXTURES / "mapping.json").read_text(encoding="utf-8"))


def _mapping() -> LiraBatchMapping:
    return LiraBatchMapping.from_dict(_payload())


def _write_csv(path: Path, row: str, *, header: str | None = None) -> None:
    path.write_text(
        (header or "Element;Mark;Section;Load case;Combination;N;Mx;My;Qx;Qy")
        + "\n"
        + row
        + "\n",
        encoding="utf-8",
    )


def _existing_element(profile: str = "30К1") -> ProjectElement:
    values = {name: None for name in ProjectElement.field_names()}
    values.update(
        {
            "project_id": "P",
            "element_id": "E-17",
            "mark": "К1",
            "element_type": "column",
            "source_file": "project.json",
            "source_type": "PROJECT",
            "source_element_id": "E-17",
            "source_row": None,
            "timestamp": datetime(2026, 9, 2, tzinfo=timezone.utc),
            "profile_name": profile,
        }
    )
    values["provenance"] = {
        name: ValueProvenance(ProvenanceType.SOURCE, file="project.json", field=name)
        for name in ("project_id", "element_id", "mark", "element_type", "profile_name")
    }
    return ProjectElement(**values)


def test_batch_mapping_is_configurable_and_keeps_explicitly_missing_concepts() -> None:
    payload = _payload()
    columns = payload["columns"]
    assert isinstance(columns, dict)
    columns["section"] = None
    units = payload["units"]
    assert isinstance(units, dict)
    columns["Qy"] = None
    units["Qy"] = None

    mapping = LiraBatchMapping.from_dict(payload)

    assert mapping.columns["section"] is None
    assert mapping.columns["Qy"] is None
    assert mapping.units["Qy"] is None

    identifier_payload = _payload()
    identifier_columns = identifier_payload["columns"]
    assert isinstance(identifier_columns, dict)
    identifier_columns["element_id"] = None
    identifier_columns["node_id"] = "Element"
    node_mapping = LiraBatchMapping.from_dict(identifier_payload)
    assert node_mapping.columns["node_id"] == "Element"


def test_ambiguous_unit_and_missing_mapped_column_fail_closed(tmp_path: Path) -> None:
    payload = _payload()
    units = payload["units"]
    assert isinstance(units, dict)
    units["N"] = None
    with pytest.raises(LiraMappingError, match="ambiguous unit"):
        LiraBatchMapping.from_dict(payload)

    source = tmp_path / "missing.csv"
    _write_csv(
        source,
        "E-17;К1;30К1;LC-2;ULS-7;-125,5;12,25;-0,32;0,75",
        header="Element;Mark;Section;Load case;Combination;N;Mx;My;Qx",
    )
    before = source.read_bytes()
    with pytest.raises(LiraMappingError, match="Qy"):
        import_lira_batch(source, _mapping())
    assert source.read_bytes() == before


def test_non_finite_value_is_rejected_with_row_audit(tmp_path: Path) -> None:
    source = tmp_path / "nonfinite.csv"
    _write_csv(source, "E-17;К1;30К1;LC-2;ULS-7;NaN;12;3;4;5")

    result = import_lira_batch(source, _mapping())

    assert result.rows_parsed == 1
    assert result.rows_accepted == 0
    assert result.rows_rejected == 1
    assert "finite" in result.rejected_rows[0].reason
    assert result.engineering_blockers[0].code == "INVALID_SOURCE_ROW"


def test_default_convention_is_unknown_and_blocks_rx38_generation() -> None:
    registry = LiraRx3ConventionRegistry.unresolved()
    available = {name: Decimal("1") for name in ("N", "Mx", "My", "Qx", "Qy")}

    assert all(
        item.verification_status is ConventionStatus.UNKNOWN
        for item in registry.components.values()
    )
    with pytest.raises(LiraConventionError, match="LIRA_RX3_FORCE_CONVENTION"):
        registry.require_rx38_generation_ready(available)


def test_explicit_engineer_convention_can_be_represented_but_is_not_validated() -> None:
    registry = LiraRx3ConventionRegistry(
        {
            name: LiraRx3ComponentConvention(
                source_component=name,
                target_rx3_component=name,
                sign_multiplier=Decimal("1"),
                axis_interpretation=f"explicit engineering interpretation for {name}",
                verification_status=ConventionStatus.ENGINEER_CONFIRMED,
                evidence_reference="engineering review note",
            )
            for name in ("N", "Mx", "My", "Qx", "Qy")
        }
    )

    assert registry.as_dict()["N"]["sign_multiplier"] == "1"
    with pytest.raises(LiraConventionError, match="unresolved components"):
        registry.require_rx38_generation_ready(
            {name: Decimal("1") for name in registry.components}
        )


def test_duplicate_and_profile_mismatch_are_engineering_blockers(tmp_path: Path) -> None:
    duplicate = tmp_path / "duplicate.csv"
    duplicate.write_text(
        "Element;Mark;Section;Load case;Combination;N;Mx;My;Qx;Qy\n"
        "E-17;К1;30К1;LC-1;C1;1;2;3;4;5\n"
        "E-17;К1;35К1;LC-2;C2;1;2;3;4;5\n",
        encoding="utf-8",
    )

    result = import_lira_batch(duplicate, _mapping())

    assert result.project_elements == ()
    assert any(issue.code == "PROFILE_MISMATCH" for issue in result.engineering_blockers)

    same_profile = tmp_path / "same-profile-duplicate.csv"
    same_profile.write_text(
        "Element;Mark;Section;Load case;Combination;N;Mx;My;Qx;Qy\n"
        "E-17;К1;30К1;LC-1;C1;1;2;3;4;5\n"
        "E-17;К1;30К1;LC-2;C2;1;2;3;4;5\n",
        encoding="utf-8",
    )
    same_profile_result = import_lira_batch(same_profile, _mapping())
    assert any(
        issue.code == "DUPLICATE_AMBIGUOUS_ELEMENT_MAPPING"
        for issue in same_profile_result.engineering_blockers
    )

    single = tmp_path / "single.csv"
    _write_csv(single, "E-17;К1;30К1;LC-2;ULS-7;1;2;3;4;5")
    mismatch = import_lira_batch(
        single,
        _mapping(),
        existing_elements=(_existing_element("40К1"),),
    )
    assert any(issue.code == "PROFILE_MISMATCH" for issue in mismatch.engineering_blockers)
    assert mismatch.project_elements == ()


def test_missing_required_combination_is_preserved_as_none_and_blocks(
    tmp_path: Path,
) -> None:
    source = tmp_path / "missing-combination.csv"
    _write_csv(source, "E-17;К1;30К1;LC-2;;1;2;3;4;5")

    result = import_lira_batch(source, _mapping())

    assert result.records[0].combination is None
    assert any(
        issue.code == "MISSING_REQUIRED_COMBINATION"
        for issue in result.engineering_blockers
    )


def test_review_bundle_is_decimal_safe_complete_and_never_overwrites_source(
    tmp_path: Path,
) -> None:
    source = tmp_path / "forces.csv"
    mapping = tmp_path / "mapping.json"
    source.write_bytes((FIXTURES / "forces.csv").read_bytes())
    mapping.write_bytes((FIXTURES / "mapping.json").read_bytes())
    before = source.read_bytes()
    output = tmp_path / "review"

    bundle = prepare_lira_review_bundle(source, mapping, output)

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
    assert bundle.result.rows_accepted == 1
    assert len(bundle.result.project_elements) == 1
    assert bundle.result.rx38_force_generation_allowed is False
    forces = json.loads((output / "forces.json").read_text(encoding="utf-8"))
    axial = forces["accepted_records"][0]["forces"]["N"]
    assert axial["raw_token"] == "-125,5000"
    assert axial["parsed_decimal"] == "-125.5000"
    assert axial["si_value"] == "-125500.0000"
    assert axial["source_cell"] == "F2"
    assert forces["accepted_records"][0]["convention"]["N"][
        "verification_status"
    ] == "UNKNOWN"
    expected_source_sha = sha256(source.read_bytes()).hexdigest()
    assert bundle.result.source_sha256 == expected_source_sha
    assert len(bundle.result.mapping_fingerprint) == 64
    assert bundle.result.audit_trail[0]["sha256"] == expected_source_sha
    assert bundle.result.audit_trail[1]["fingerprint"] == (
        bundle.result.mapping_fingerprint
    )
    assert any(
        item["code"] == "LIRA_RX3_FORCE_CONVENTION"
        for item in json.loads((output / "blockers.json").read_text(encoding="utf-8"))
    )

    with pytest.raises(LiraMappingError):
        LiraBatchMapping.from_dict({**_payload(), "unexpected": True})
    with pytest.raises(LiraFormatError, match="refusing to overwrite"):
        prepare_lira_review_bundle(source, mapping, output)
