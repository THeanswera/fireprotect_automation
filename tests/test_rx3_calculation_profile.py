from collections import Counter

from fireprotect.rx3.safety import evaluate_calculation_profile
from fireprotect.rx3.schema import WritePolicy, field_spec
from tests.safety_support import make_record, template_evidence


def test_profile_reports_confirmed_mismatch_and_is_not_verified():
    profile = evaluate_calculation_profile(
        make_record(), {82: "30"}, template_evidence()
    )
    assert profile.confirmed_mismatches[0].index == 82
    assert not profile.verified


def test_unknown_fields_are_preserved_as_an_opaque_fingerprint():
    first = evaluate_calculation_profile(
        make_record(), {82: "25"}, template_evidence()
    )
    second = evaluate_calculation_profile(
        make_record(**{"135": "opaque"}), {82: "25"}, template_evidence()
    )
    assert first.unknown_preserved_count == second.unknown_preserved_count == 131
    assert first.unknown_fingerprint != second.unknown_fingerprint
    assert first.verified


def test_profile_evidence_must_cover_calculation_settings():
    profile = evaluate_calculation_profile(
        make_record(), {82: "25"}, template_evidence(profile_verified=False)
    )
    assert 82 in profile.unverified_calculation_settings
    assert not profile.verified


def test_schema_counts_and_write_policies_remain_explicit():
    counts = Counter(field_spec(index).confidence for index in range(200))
    assert counts == {"confirmed": 56, "probable": 13, "unknown": 131}
    assert field_spec(44).write_policy is WritePolicy.RESULT_ONLY
    assert field_spec(54).write_policy is WritePolicy.RESULT_ONLY
    assert field_spec(50).write_policy is WritePolicy.FORBIDDEN
    assert field_spec(135).write_policy is WritePolicy.FORBIDDEN
