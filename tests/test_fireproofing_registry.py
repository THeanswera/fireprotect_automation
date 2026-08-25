from hashlib import sha256
from pathlib import Path

import pytest

from fireprotect.execution import ExecutionMode
from fireprotect.technical import (
    FireproofingTechnicalRegistry,
    TechnicalDataStatus,
    TechnicalRegistryError,
)


def write_technical_registry(
    directory: Path, *, verified: bool
) -> FireproofingTechnicalRegistry:
    evidence = directory / "manufacturer.txt"
    evidence.write_text("controlled primary manufacturer data", encoding="utf-8")
    digest = sha256(evidence.read_bytes()).hexdigest()
    status = (
        "VERIFIED_TECHNICAL_DATA"
        if verified
        else "UNVERIFIED_TECHNICAL_DATA"
    )
    registry = directory / "technical.yaml"
    registry.write_text(
        f"""schema_version: 1
solutions:
  - id: TEST_SYSTEM
    manufacturer: Test Manufacturer
    system_name: Test System
    product_name: Test Product
    component_name: null
    certificate_number: CERT-1
    certificate_valid_from: 2026-01-01
    certificate_valid_to: 2027-01-01
    technical_specification: TS-1
    technological_regulation: null
    fire_test_protocol: FP-1
    applicability_document: APP-1
    steel_profile_type: I-section
    ptm_range: controlled range
    fire_resistance: R90
    required_thickness: controlled value
    specific_consumption: controlled value
    primer_compatibility: controlled value
    coating_type: test
    application_method: test
    layer_requirements: test
    density: controlled value
    environmental_restrictions: controlled value
    source_document: manufacturer.txt
    source_page_or_table: table 1
    path: manufacturer.txt
    document_sha256: {digest}
    status: {status}
    note: controlled test only
""",
        encoding="utf-8",
    )
    return FireproofingTechnicalRegistry.load(registry)


def test_unverified_excel_tables_block_production_selection(tmp_path: Path):
    registry = write_technical_registry(tmp_path, verified=False)
    with pytest.raises(TechnicalRegistryError, match="blocked"):
        registry.require_for_selection(
            "TEST_SYSTEM", mode=ExecutionMode.PRODUCTION
        )


def test_unverified_data_can_be_inspected_in_draft(tmp_path: Path):
    entry = write_technical_registry(
        tmp_path, verified=False
    ).require_for_selection("TEST_SYSTEM", mode=ExecutionMode.DRAFT)
    assert entry.status is TechnicalDataStatus.UNVERIFIED_TECHNICAL_DATA
    assert not entry.verified_for_production


def test_verified_primary_document_and_hash_allow_selection(tmp_path: Path):
    entry = write_technical_registry(
        tmp_path, verified=True
    ).require_for_selection("TEST_SYSTEM", mode=ExecutionMode.PRODUCTION)
    assert entry.verified_for_production
