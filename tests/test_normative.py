import pytest

from fireprotect.normative import (
    NormativeResult,
    NormativeTrace,
    NormativeTraceRequiredError,
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
    assert require_normative_trace(_trace()) == _trace()
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


def test_traced_normative_result_can_be_confirmed() -> None:
    result = NormativeResult(value=325, trace=_trace())

    assert result.confirmed is True
    assert result.validate(production=True) is result
