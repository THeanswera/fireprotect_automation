from hashlib import sha256
from pathlib import Path

import pytest

from fireprotect.excel.obm import ObmWorkbookExportError, export_obm_workbook
from fireprotect.execution import ExecutionMode
from tests.test_fireproofing_registry import write_technical_registry


def test_unverified_technical_data_blocks_before_excel_copy(tmp_path: Path):
    source = tmp_path / "source.xlsx"
    source.write_bytes(b"not opened because production gate must fail first")
    before = sha256(source.read_bytes()).hexdigest()
    entry = write_technical_registry(tmp_path, verified=False).entries["TEST_SYSTEM"]

    with pytest.raises(ObmWorkbookExportError, match="technical data"):
        export_obm_workbook(
            (),
            (),
            source,
            tmp_path / "output.xlsx",
            mode=ExecutionMode.PRODUCTION,
            technical_entry=entry,
            verified_template_sha256=before,
        )
    assert sha256(source.read_bytes()).hexdigest() == before
    assert not (tmp_path / "output.xlsx").exists()


def test_unverified_excel_template_hash_blocks_production(tmp_path: Path):
    source = tmp_path / "source.xlsx"
    source.write_bytes(b"controlled placeholder")
    entry = write_technical_registry(tmp_path, verified=True).entries["TEST_SYSTEM"]

    with pytest.raises(ObmWorkbookExportError, match="template SHA-256"):
        export_obm_workbook(
            (),
            (),
            source,
            tmp_path / "output.xlsx",
            mode=ExecutionMode.PRODUCTION,
            technical_entry=entry,
            verified_template_sha256="0" * 64,
        )
    assert not (tmp_path / "output.xlsx").exists()
