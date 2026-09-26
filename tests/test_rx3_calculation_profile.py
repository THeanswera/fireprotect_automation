from collections import Counter
from dataclasses import replace

import pytest

from fireprotect.rx3.safety import evaluate_calculation_profile
from fireprotect.rx3.schema import (
    MY_BIAXIAL_PATH_MVP_STATUS,
    WRITABLE_CONFIRMED_INDICES,
    WritePolicy,
    field_spec,
)
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
    assert first.unknown_preserved_count == second.unknown_preserved_count == 125
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
    assert counts == {"confirmed": 59, "probable": 16, "unknown": 125}
    assert field_spec(53).name == "beta_tem_modulus_reduction"
    assert field_spec(53).write_policy is WritePolicy.FORBIDDEN
    assert field_spec(53).controlled_experiment_ids == (
        "RX3-EXP-01",
        "RX3-EXP-01C",
        "RX3-EXP-01D",
        "RX3-EXP-02B",
    )
    assert field_spec(44).write_policy is WritePolicy.RESULT_ONLY
    assert field_spec(54).write_policy is WritePolicy.RESULT_ONLY
    assert field_spec(50).confidence == "confirmed"
    assert field_spec(50).write_policy is WritePolicy.EXPERIMENTAL
    assert field_spec(50).controlled_experiment_ids == (
        "RX3-EXP-02",
        "RX3-EXP-02B",
        "LIRA-RX3-22P-XX-MAGNITUDE",
    )
    assert field_spec(78).confidence == "probable"
    assert field_spec(78).write_policy is WritePolicy.FORBIDDEN
    assert field_spec(79).name == "rx3_gui_minor_axis_moment_input_knm"
    assert field_spec(79).confidence == "confirmed"
    assert field_spec(79).write_policy is WritePolicy.EXPERIMENTAL
    assert field_spec(79).controlled_experiment_ids == (
        "RX3-EXP-04",
        "RX3-EXP-04B",
    )
    assert MY_BIAXIAL_PATH_MVP_STATUS == "VALIDATED"
    assert 79 not in WRITABLE_CONFIRMED_INDICES
    assert field_spec(92).name == "rx3_gui_q_input_kn"
    assert field_spec(92).confidence == "confirmed"
    assert field_spec(92).write_policy is WritePolicy.EXPERIMENTAL
    assert field_spec(92).controlled_experiment_ids == (
        "RX3-EXP-02",
        "RX3-EXP-02B",
        "RX3-EXP-03",
        "LIRA-RX3-22P-XX-MAGNITUDE",
    )
    assert field_spec(135).write_policy is WritePolicy.FORBIDDEN


def test_profile_verified_flag_must_be_a_real_bool():
    with pytest.raises(TypeError, match="must be bool"):
        replace(
            template_evidence(),
            calculation_profile_verified="false",  # type: ignore[arg-type]
        )


def test_profile_evidence_must_be_bound_to_the_exact_template():
    evidence = replace(template_evidence(), template_record_sha256="0" * 64)
    profile = evaluate_calculation_profile(make_record(), {82: "25"}, evidence)
    assert profile.verified is False
