"""The VALIDATION-only RX3 preparer cannot be used as a production shortcut."""

from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path

import pytest

from fireprotect.execution import ExecutionMode
from fireprotect.rx3.lira_bar_prep import (
    Rx3LiraBarPrepError,
    decimal_token,
    experiment_reference,
    prepare_rx3_lira_bar_validation,
)
from fireprotect.rx3.parser import read_rx38_document
from fireprotect.rx3.schema import WritePolicy, field_spec
from tests.safety_support import write_template

LENGTH = 3
MOMENT_KNM = Decimal("6.1781895")
BEFORE_MOMENT = "14,7987"
BEFORE_Q = "14,7151"


def _b2_template(path: Path) -> None:
    write_template(
        path,
        **{
            "1": "Б2",
            "3": "Б2",
            "5": "Швеллер ",
            "8": "220",
            "9": "82",
            "11": "5,4",
            "13": "9,5",
            "14": str(LENGTH),
            "15": "1",
            "17": "ГОСТ 8240-97",
            "19": "22П",
            "20": "2670",
            "21": "757,2",
            "42": "С245",
            "45": "Изгибаемый стержень в одной из главных плоскостей",
            "48": "",
            "50": BEFORE_MOMENT,
            "55": "15",
            "92": BEFORE_Q,
            "104": "Стандартный температурный режим",
            "188": "σ 0.2%  и  E по данным EN 1993-1-2",
        },
    )


def _component(
    name: str,
    source: str,
    unit: str,
    review: str,
    review_unit: str,
    *,
    resolved: bool,
    target: str | None,
    transform: str | None,
) -> dict[str, object]:
    return {
        "component": name,
        "source_value": source,
        "source_unit": unit,
        "source_cell": "I6" if name == "My" else "J6",
        "source_sheet": " ",
        "source_row": 6,
        "source_sha256": "0" * 64,
        "review_value": review,
        "review_unit": review_unit,
        "convention": {
            "resolved": resolved,
            "target": target,
            "value_transform": transform,
            "verification_status": "VALIDATED" if resolved else "UNKNOWN",
        },
    }


def _experiment_payload(
    *,
    stress_state: str | None = "ONE_PLANE_BENDING",
    standard: str = "ГОСТ 8240-97",
    length_m: str = "3.0",
    blocked: bool = False,
    moment: str = "0.63",
    shear: str = "0.0",
    engineers: bool = False,
) -> dict[str, object]:
    components = [
        _component("N", "0.0", "tf", "0.000", "kN", resolved=False,
                   target=None, transform=None),
        _component("Mk", "0.0", "tf*m", "0.000", "kN*m", resolved=False,
                   target=None, transform=None),
        _component(
            "My", moment, "tf*m", str(Decimal(moment) * Decimal("9.80665")),
            "kN*m", resolved=True,
            target="FIELD50_MAX_MAJOR_AXIS_MOMENT", transform="MAGNITUDE",
        ),
        _component("Mz", "0.0", "tf*m", "0.000", "kN*m", resolved=False,
                   target=None, transform=None),
        _component("Qy", "0.0", "tf", "0.000", "kN", resolved=False,
                   target=None, transform=None),
        _component(
            "Qz", shear, "tf", str(Decimal(shear) * Decimal("9.80665")), "kN",
            resolved=True, target="FIELD92_MAX_SHEAR_Q", transform="MAGNITUDE",
        ),
    ]
    return {
        "kind": "LIRA_BAR_EXPERIMENT_INPUT",
        "status": "EXPERIMENT_INPUT_BLOCKED" if blocked else "EXPERIMENT_INPUT_READY",
        "declaration_status": (
            "ENGINEER_SIGNED" if engineers else "DRAFT_UNSIGNED"
        ),
        "engineer_confirmed": engineers,
        "transfer_blocked": blocked,
        "blockers": ["LIRA_EXPERIMENT_UNSUPPORTED_COMPONENT:N"] if blocked else [],
        "missing_confirmations": ["effective_length_m", "support_condition"],
        "governing_result_selection": None,
        "bar": {
            "bar_id": "Б2",
            "element_ids": ["1", "2"],
            "end_node_ids": ["1", "2"],
            "element_lengths_m": {"1": "1.5", "2": "1.5"},
            "bar_length_m": length_m,
        },
        "profile": {
            "kind_word": "Швеллер",
            "designation": "22П",
            "mark": "Б2",
            "standard": standard,
            "rotation_degrees": "0",
            "plane": "X-Z",
            "scheme_flag": "2",
            "rx3_template": "Б2",
            "stress_state": stress_state,
            "confirmed_by": None,
            "basis": "test",
        },
        "components": components,
        "rx38_created": False,
        "release_forbidden": True,
        "issue_readiness": "NOT_READY_FOR_ISSUE",
    }


def _write_experiment(tmp_path: Path, payload: dict[str, object]) -> Path:
    directory = tmp_path / "experiment"
    directory.mkdir()
    (directory / "experiment_input.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return directory


def _prepare(
    tmp_path: Path,
    *,
    payload: dict[str, object] | None = None,
    output: str = "prep",
    template: Path | None = None,
    mode: ExecutionMode = ExecutionMode.VALIDATION,
) -> dict[str, object]:
    experiment = _write_experiment(tmp_path, payload or _experiment_payload())
    source = template or (tmp_path / "template.rx38")
    if template is None:
        _b2_template(source)
    return prepare_rx3_lira_bar_validation(
        experiment_dir=experiment,
        template_path=source,
        output_dir=tmp_path / output,
        mode=mode,
    )


# --------------------------------------------------------------------------
# Positive
# --------------------------------------------------------------------------


def test_prepares_only_the_two_validated_fields(tmp_path: Path) -> None:
    template = tmp_path / "template.rx38"
    _b2_template(template)
    before = template.read_bytes()

    manifest = _prepare(tmp_path, template=template)

    assert manifest["kind"] == "RX3_LIRA_BAR_VALIDATION_PREP"
    assert manifest["status"] == "VALIDATION_INPUT_PREPARED"
    assert manifest["mode"] == "VALIDATION"
    assert manifest["non_target_records_identical"] is True
    assert manifest["production_write_allowed"] is False
    assert manifest["release_forbidden"] is True
    assert manifest["issue_readiness"] == "NOT_READY_FOR_ISSUE"
    assert manifest["target"]["mark"] == "Б2"
    assert manifest["target"]["position"] == 1

    changed = {entry["field"]: entry for entry in manifest["changes"]}
    assert set(changed) == {50, 92}
    assert changed[50]["before_raw"] == BEFORE_MOMENT
    assert changed[50]["after_raw"] == decimal_token(MOMENT_KNM)
    assert changed[50]["after_value"] == str(MOMENT_KNM)
    assert changed[50]["value_transform"] == "MAGNITUDE"
    assert changed[50]["source_component"] == "My"
    assert changed[92]["before_raw"] == BEFORE_Q
    assert changed[92]["after_raw"] == "0"
    assert changed[92]["source_value"] == "0.0"

    # The source template is untouched.
    assert template.read_bytes() == before
    generated = tmp_path / "prep" / "generated.rx38"
    document = read_rx38_document(generated)
    records = [item for item in document.records if item.record_type == "Tconstr"]
    tokens = list(records[0].raw_tokens)
    assert tokens[50] == "6,1781895"
    assert tokens[1] == "Б2"
    assert tokens[92] == "0"  # the selected row carries no shear
    assert tokens[44] == "650"  # stale result fields stay as they were
    assert tokens[55] == "15"
    assert len(document.records) == 2


def test_zero_components_without_a_known_field_are_reported(tmp_path: Path) -> None:
    manifest = _prepare(tmp_path)
    assert manifest["unmapped_zero_components"] == ["N", "Mk", "Mz", "Qy"]
    checkpoint = (tmp_path / "prep" / "CHECKPOINT_RX3.md").read_text(
        encoding="utf-8"
    )
    assert "N, Mk, Mz, Qy" in checkpoint


def test_zero_shear_is_written_as_zero(tmp_path: Path) -> None:
    manifest = _prepare(tmp_path)
    assert [entry["field"] for entry in manifest["changes"]] == [50, 92]


def test_nonzero_shear_is_written_as_magnitude(tmp_path: Path) -> None:
    payload = _experiment_payload(shear="-1.65")
    manifest = _prepare(tmp_path, payload=payload)
    changed = {entry["field"]: entry for entry in manifest["changes"]}
    assert set(changed) == {50, 92}
    assert changed[92]["source_value"] == "-1.65"
    assert changed[92]["after_raw"] == decimal_token(Decimal("16.1809725"))
    assert changed[92]["after_value"] == "16.1809725"


def test_checkpoint_lists_unconfirmed_conditions(tmp_path: Path) -> None:
    manifest = _prepare(tmp_path)
    gate = manifest["calculation_gate"]
    assert gate["allowed_without_human_review"] is False
    assert gate["requires_engineer_screen_review"] is True
    assert gate["unconfirmed_inputs"] == [
        "effective_length_m",
        "support_condition",
    ]
    checkpoint = (tmp_path / "prep" / "CHECKPOINT_RX3.md").read_text(
        encoding="utf-8"
    )
    assert "расчёт запрещён" in checkpoint
    assert "14,80" in checkpoint
    assert "calculated.rx38" in checkpoint
    assert "validate-rx3-result" in checkpoint
    assert experiment_reference("Б2") in checkpoint


def test_validation_command_targets_the_prepared_record(tmp_path: Path) -> None:
    manifest = _prepare(tmp_path)
    command = manifest["validation_command"]
    assert f"--target-fingerprint {manifest['target']['after_fingerprint']}" in command
    assert "--target-position" not in command
    assert "--json-report" in command
    assert "ENGINEER_CONFIRMED" in command


# --------------------------------------------------------------------------
# Refusals: no production shortcut, no guessing
# --------------------------------------------------------------------------


def test_production_mode_is_refused(tmp_path: Path) -> None:
    with pytest.raises(Rx3LiraBarPrepError, match="VALIDATION-only"):
        _prepare(tmp_path, mode=ExecutionMode.PRODUCTION)
    assert not (tmp_path / "prep").exists()


def test_blocked_transfer_is_refused(tmp_path: Path) -> None:
    with pytest.raises(Rx3LiraBarPrepError, match="blocked"):
        _prepare(tmp_path, payload=_experiment_payload(blocked=True))


def test_foreign_standard_is_refused(tmp_path: Path) -> None:
    with pytest.raises(Rx3LiraBarPrepError, match="profile.standard"):
        _prepare(tmp_path, payload=_experiment_payload(standard="ГОСТ 26020-83"))


def test_missing_stress_state_is_refused(tmp_path: Path) -> None:
    with pytest.raises(Rx3LiraBarPrepError, match="stress_state"):
        _prepare(tmp_path, payload=_experiment_payload(stress_state=None))


def test_foreign_length_is_refused(tmp_path: Path) -> None:
    with pytest.raises(Rx3LiraBarPrepError, match="3.00 m scope"):
        _prepare(tmp_path, payload=_experiment_payload(length_m="6.0"))


def test_incompatible_template_is_refused(tmp_path: Path) -> None:
    template = tmp_path / "other.rx38"
    write_template(template, **{"1": "К1", "17": "ГОСТ 8240-97", "19": "22П",
                                "14": str(LENGTH)})
    with pytest.raises(Rx3LiraBarPrepError, match="no Tconstr record matches"):
        _prepare(tmp_path, template=template)
    assert not (tmp_path / "prep").exists()


def test_ambiguous_template_target_is_refused(tmp_path: Path) -> None:
    template = tmp_path / "two.rx38"
    _b2_template(template)
    text = template.read_text(encoding="utf-8")
    template.write_text(text + text.splitlines()[-1] + "\r\n", encoding="utf-8")
    with pytest.raises(Rx3LiraBarPrepError, match="not unique"):
        _prepare(tmp_path, template=template)


def test_existing_output_directory_is_not_overwritten(tmp_path: Path) -> None:
    (tmp_path / "prep").mkdir()
    with pytest.raises(Rx3LiraBarPrepError, match="refusing to overwrite"):
        _prepare(tmp_path)


def test_unchanged_values_are_reported_instead_of_written(tmp_path: Path) -> None:
    template = tmp_path / "same.rx38"
    _b2_template(template)
    text = (
        template.read_text(encoding="utf-8")
        .replace(BEFORE_MOMENT, decimal_token(MOMENT_KNM))
        .replace(BEFORE_Q, "0")
    )
    template.write_text(text, encoding="utf-8")
    with pytest.raises(Rx3LiraBarPrepError, match="already carries"):
        _prepare(tmp_path, template=template)


def test_unsupported_nonzero_component_is_refused(tmp_path: Path) -> None:
    payload = _experiment_payload()
    components = payload["components"]
    assert isinstance(components, list)
    components[0] = _component(  # N nonzero and unresolved
        "N", "5.0", "tf", "49.03", "kN", resolved=False, target=None,
        transform=None,
    )
    with pytest.raises(Rx3LiraBarPrepError, match="outside the validated"):
        _prepare(tmp_path, payload=payload)


def test_signed_linear_transform_is_refused(tmp_path: Path) -> None:
    payload = _experiment_payload()
    components = payload["components"]
    assert isinstance(components, list)
    component = dict(components[2])
    convention = dict(component["convention"])  # type: ignore[arg-type]
    convention["value_transform"] = "SIGNED_LINEAR"
    component["convention"] = convention
    components[2] = component
    with pytest.raises(Rx3LiraBarPrepError, match="only.*MAGNITUDE"):
        _prepare(tmp_path, payload=payload)


def test_unsigned_draft_may_be_prepared_but_stays_unconfirmed(
    tmp_path: Path,
) -> None:
    manifest = _prepare(tmp_path, payload=_experiment_payload())
    assert manifest["experiment_input"]["declaration_status"] == "DRAFT_UNSIGNED"
    assert manifest["experiment_input"]["engineer_confirmed"] is False
    assert manifest["production_write_allowed"] is False


def test_production_schema_policies_are_unchanged(tmp_path: Path) -> None:
    """The preparer must not promote any field out of EXPERIMENTAL."""

    _prepare(tmp_path)
    assert field_spec(50).write_policy is WritePolicy.EXPERIMENTAL
    assert field_spec(92).write_policy is WritePolicy.EXPERIMENTAL
    assert field_spec(78).write_policy is WritePolicy.FORBIDDEN
