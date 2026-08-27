import pytest
from datetime import date
from hashlib import sha256
from pathlib import Path

from fireprotect.normative import (
    NormativeResult,
    NormativeDocument,
    NormativeDocumentStatus,
    NormativeTrace,
    NormativeTraceRequiredError,
    NormativeValidation,
    require_normative_trace,
)


def _trace() -> NormativeTrace:
    return NormativeTrace(
        document_id="SP 16.13330.2017",
        edition="2022",
        clause="5.2",
        formula_or_table="table 5",
        description="Design resistance of steel",
    )


def test_normative_trace_validates_required_citation_fields() -> None:
    assert require_normative_trace(_trace(), production=False) == _trace()
    with pytest.raises(NormativeTraceRequiredError, match="registry/date/hash"):
        require_normative_trace(_trace())
    with pytest.raises(TypeError, match="must be bool"):
        require_normative_trace(_trace(), production="false")  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="clause"):
        NormativeTrace(
            document_id="SP 16.13330.2017",
            edition="2022",
            clause=" ",
            formula_or_table=None,
            description="Description",
        )


def test_production_rejects_untraced_normative_result() -> None:
    result = NormativeResult(value=325, trace=None)

    assert result.confirmed is False
    assert result.validate(production=False) is result
    with pytest.raises(NormativeTraceRequiredError):
        result.validate(production=True)


def test_trace_alone_cannot_claim_production_confirmation() -> None:
    result = NormativeResult(value=325, trace=_trace())

    assert result.confirmed is False
    with pytest.raises(NormativeTraceRequiredError, match="registry/date/hash"):
        result.validate(production=True)


def test_registry_validated_normative_result_can_be_confirmed(tmp_path: Path) -> None:
    trace = _trace()
    source = tmp_path / "standard.txt"
    source.write_text("controlled normative source", encoding="utf-8")
    source_hash = sha256(source.read_bytes()).hexdigest()
    document = NormativeDocument(
        trace.document_id,
        trace.document_id,
        "Controlled test document",
        trace.edition,
        NormativeDocumentStatus.VERIFIED_CURRENT,
        date(2022, 1, 1),
        date(2022, 1, 1),
        None,
        source,
        source_hash,
        "controlled test evidence",
    )
    validation = NormativeValidation(
        trace,
        document,
        (),
        {
            "calculation_date": "2026-08-25",
            "expected_sha256": source_hash,
            "actual_sha256": source_hash,
        },
    )
    result = NormativeResult(325, trace, validation)

    assert result.confirmed is True
    assert result.validate(production=True) is result


def test_fabricated_normative_validation_without_source_is_not_confirmed() -> None:
    trace = _trace()
    document = NormativeDocument(
        trace.document_id,
        trace.document_id,
        "Missing controlled source",
        trace.edition,
        NormativeDocumentStatus.VERIFIED_CURRENT,
        date(2022, 1, 1),
        date(2022, 1, 1),
        None,
        None,
        "a" * 64,
        "self-declared evidence",
    )
    validation = NormativeValidation(
        trace,
        document,
        (),
        {
            "calculation_date": "2026-08-25",
            "expected_sha256": "a" * 64,
            "actual_sha256": "a" * 64,
        },
    )
    assert not NormativeResult(325, trace, validation).confirmed
