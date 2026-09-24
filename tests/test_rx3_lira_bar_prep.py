"""The VALIDATION-only RX3 preparer is bound to sources, not to stored JSON."""

from __future__ import annotations

import hashlib
import json
from decimal import Decimal
from pathlib import Path

import pytest

from fireprotect.execution import ExecutionMode
from fireprotect.lira import prepare_bar_run
from fireprotect.rx3.lira_bar_prep import (
    CONTROLLED_TEMPLATE_SHA256,
    Rx3LiraBarPrepError,
    decimal_token,
    prepare_rx3_lira_bar_validation,
    required_fire_resistance_minutes,
)
from fireprotect.rx3.parser import read_rx38_document
from fireprotect.rx3.schema import WritePolicy, field_spec
from tests.safety_support import write_template
from tests.test_lira_bar_run import (
    _conditions_payload,
    _default_plan,
    _make_source_package,
    _plan,
)

ROW_ID = "R0001"
BEFORE_MOMENT = "14,7987"
BEFORE_Q = "14,7151"
MY_KNM = Decimal("1.8") * Decimal("9.80665")
QZ_KNM = Decimal("-1.65") * Decimal("9.80665")
PREPARED_Q_KNM = QZ_KNM.copy_abs()


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(65536), b""):
            digest.update(block)
    return digest.hexdigest()


def _template(path: Path, **overrides: str) -> None:
    values = {
        "1": "Б2",
        "3": "Б2",
        "5": "Швеллер ",
        "8": "220",
        "9": "82",
        "11": "5,4",
        "13": "9,5",
        "14": "3",
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
    }
    values.update(overrides)
    write_template(path, **values)


def _conditions(tmp_path: Path, **overrides: object) -> Path:
    payload = _conditions_payload(bar_id="Б2")
    design = {
        "effective_length_m": None,
        "support_condition": None,
        "heating_sides": "4",
        "fire_regime": "STANDARD",
        "required_fire_resistance_min": "R15",
    }
    design.update(overrides)
    payload["design_conditions"] = design
    path = tmp_path / "conditions.json"
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return path


def _prepared_run(
    tmp_path: Path, *, name: str = "run", plan: list[dict[str, object]] | None = None
) -> Path:
    package = _make_source_package(tmp_path, plan=plan)
    output = tmp_path / name
    manifest = prepare_bar_run(
        source_package=package,
        plan_reference=_plan(tmp_path),
        experiment_row_id=ROW_ID,
        output_dir=output,
        conditions_path=_conditions(tmp_path),
    )
    assert manifest["status"] == "RUN_PREPARED"
    return output


def _plan_with_axial_force(force: str) -> list[dict[str, object]]:
    plan, _ = _default_plan()
    plan[0] = {**plan[0], "force_N": force, "N": force}
    return plan


def _prepare(
    tmp_path: Path,
    run_dir: Path,
    *,
    template: Path | None = None,
    output: str = "rx3_input",
    mode: ExecutionMode = ExecutionMode.VALIDATION,
    pin: str | None = None,
) -> dict[str, object]:
    source = template or (tmp_path / "template.rx38")
    if template is None:
        _template(source)
    return prepare_rx3_lira_bar_validation(
        run_dir=run_dir,
        template_path=source,
        output_dir=tmp_path / output,
        mode=mode,
        template_sha256_pin=pin or _sha256(source),
    )


def _edit_experiment_input(run_dir: Path, mutate) -> None:
    path = run_dir / "experiment" / "experiment_input.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    mutate(payload)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )


# --------------------------------------------------------------------------
# Positive path
# --------------------------------------------------------------------------


def test_prepares_only_the_two_validated_fields(tmp_path: Path) -> None:
    run_dir = _prepared_run(tmp_path)
    template = tmp_path / "template.rx38"
    _template(template)
    before = template.read_bytes()

    manifest = _prepare(tmp_path, run_dir, template=template)

    assert manifest["kind"] == "RX3_LIRA_BAR_VALIDATION_PREP"
    assert manifest["status"] == "VALIDATION_INPUT_PREPARED"
    assert manifest["mode"] == "VALIDATION"
    assert manifest["non_target_records_identical"] is True
    assert manifest["production_write_allowed"] is False
    assert manifest["release_forbidden"] is True
    assert manifest["issue_readiness"] == "NOT_READY_FOR_ISSUE"
    assert manifest["engineer_confirmation_required"] is True
    assert manifest["run"]["run_dir"] == str(run_dir)
    assert manifest["experiment_row"]["row_id"] == ROW_ID
    assert manifest["target"]["mark"] == "Б2"
    assert manifest["target"]["position"] == 1

    changed = {entry["field"]: entry for entry in manifest["changes"]}
    assert set(changed) == {50, 92}
    assert changed[50]["source_value"] == "1.8"
    assert changed[50]["after_raw"] == decimal_token(MY_KNM)
    assert Decimal(changed[50]["after_value"]) == MY_KNM
    assert changed[50]["value_transform"] == "MAGNITUDE"
    assert changed[92]["source_value"] == "-1.65"
    assert changed[92]["after_raw"] == decimal_token(PREPARED_Q_KNM)
    assert Decimal(changed[92]["after_value"]) == PREPARED_Q_KNM
    assert manifest["unmapped_zero_components"] == ["N", "Mk", "Mz", "Qy"]

    assert template.read_bytes() == before
    document = read_rx38_document(tmp_path / "rx3_input" / "generated.rx38")
    records = [item for item in document.records if item.record_type == "Tconstr"]
    assert records[0].raw_tokens[50] == decimal_token(MY_KNM)
    assert records[0].raw_tokens[92] == decimal_token(PREPARED_Q_KNM)
    assert records[0].raw_tokens[1] == "Б2"


def test_checkpoint_uses_prepared_values_and_no_invented_signature(
    tmp_path: Path,
) -> None:
    run_dir = _prepared_run(tmp_path)
    manifest = _prepare(tmp_path, run_dir)

    checkpoint = (tmp_path / "rx3_input" / "CHECKPOINT_RX3.md").read_text(
        encoding="utf-8"
    )
    assert ROW_ID in checkpoint
    assert decimal_token(MY_KNM) in checkpoint
    assert "R15" in checkpoint
    assert "НЕ применимы" not in checkpoint
    assert "NOT_APPLICABLE_FOR_THIS_RECORD" in checkpoint
    assert "ENGINEER_CONFIRMED" in checkpoint  # only as an explicit prohibition
    assert "--gui-evidence ENGINEER_CONFIRMED" not in manifest["validation_command"]
    assert "validate-rx3-result" in manifest["validation_command"]
    assert "6,1781895" not in checkpoint
    assert "14,80" not in checkpoint


def test_effective_length_question_is_resolved_from_confirmed_facts(
    tmp_path: Path,
) -> None:
    run_dir = _prepared_run(tmp_path)
    manifest = _prepare(tmp_path, run_dir)

    decision = manifest["effective_length_and_support"]
    assert decision["status"] == "NOT_APPLICABLE_FOR_THIS_RECORD"
    assert decision["not_substituted"] == (
        "геометрические 3 м расчётной длиной не назначались"
    )
    assert any("N = 0" in item for item in decision["basis"])
    assert "N = 0" in decision["limits"]
    assert manifest["calculation_gate"]["blocked_by_unconfirmed_inputs"] == []


def test_nonzero_axial_force_stops_the_preparation(tmp_path: Path) -> None:
    run_dir = _prepared_run(tmp_path, plan=_plan_with_axial_force("5"))
    with pytest.raises(Rx3LiraBarPrepError, match="blocked transfer"):
        _prepare(tmp_path, run_dir)
    assert not (tmp_path / "rx3_input").exists()


# --------------------------------------------------------------------------
# Regressions: stored JSON can never become the source of truth
# --------------------------------------------------------------------------


def test_substituted_review_value_is_rejected_before_writing(tmp_path: Path) -> None:
    run_dir = _prepared_run(tmp_path)

    def mutate(payload: dict[str, object]) -> None:
        for item in payload["components"]:  # type: ignore[index]
            if item["component"] == "My":
                item["review_value"] = "999"

    _edit_experiment_input(run_dir, mutate)
    with pytest.raises(Rx3LiraBarPrepError, match="review_value"):
        _prepare(tmp_path, run_dir)
    assert not (tmp_path / "rx3_input").exists()


def test_substituted_source_value_is_rejected(tmp_path: Path) -> None:
    run_dir = _prepared_run(tmp_path)

    def mutate(payload: dict[str, object]) -> None:
        for item in payload["components"]:  # type: ignore[index]
            if item["component"] == "My":
                item["source_value"] = "999"

    _edit_experiment_input(run_dir, mutate)
    with pytest.raises(Rx3LiraBarPrepError, match="source_value"):
        _prepare(tmp_path, run_dir)


def test_removed_component_is_rejected(tmp_path: Path) -> None:
    run_dir = _prepared_run(tmp_path)

    def mutate(payload: dict[str, object]) -> None:
        payload["components"] = [
            item
            for item in payload["components"]  # type: ignore[index]
            if item["component"] != "N"
        ]

    _edit_experiment_input(run_dir, mutate)
    with pytest.raises(Rx3LiraBarPrepError, match="incomplete"):
        _prepare(tmp_path, run_dir)
    assert not (tmp_path / "rx3_input").exists()


def test_duplicated_component_is_rejected(tmp_path: Path) -> None:
    run_dir = _prepared_run(tmp_path)

    def mutate(payload: dict[str, object]) -> None:
        components = payload["components"]  # type: ignore[index]
        components.append(dict(components[0]))

    _edit_experiment_input(run_dir, mutate)
    with pytest.raises(Rx3LiraBarPrepError, match="more than once"):
        _prepare(tmp_path, run_dir)


def test_unknown_component_is_rejected(tmp_path: Path) -> None:
    run_dir = _prepared_run(tmp_path)

    def mutate(payload: dict[str, object]) -> None:
        components = payload["components"]  # type: ignore[index]
        components[0] = {**components[0], "component": "Mx"}

    _edit_experiment_input(run_dir, mutate)
    with pytest.raises(Rx3LiraBarPrepError, match="is not one of"):
        _prepare(tmp_path, run_dir)


def test_substituted_units_are_rejected(tmp_path: Path) -> None:
    run_dir = _prepared_run(tmp_path)

    def mutate(payload: dict[str, object]) -> None:
        for item in payload["components"]:  # type: ignore[index]
            if item["component"] == "My":
                item["review_unit"] = "kN"

    _edit_experiment_input(run_dir, mutate)
    with pytest.raises(Rx3LiraBarPrepError, match="review_unit"):
        _prepare(tmp_path, run_dir)


def test_substituted_convention_is_rejected(tmp_path: Path) -> None:
    run_dir = _prepared_run(tmp_path)

    def mutate(payload: dict[str, object]) -> None:
        for item in payload["components"]:  # type: ignore[index]
            if item["component"] == "My":
                item["convention"] = {
                    "resolved": True,
                    "target": "FIELD79_MINOR_AXIS_MOMENT",
                    "value_transform": "MAGNITUDE",
                    "verification_status": "VALIDATED",
                }

    _edit_experiment_input(run_dir, mutate)
    with pytest.raises(Rx3LiraBarPrepError, match="convention target"):
        _prepare(tmp_path, run_dir)


def test_declaration_bound_to_another_evidence_revision_is_rejected(
    tmp_path: Path,
) -> None:
    run_dir = _prepared_run(tmp_path)
    declaration_path = run_dir / "declaration.json"
    declaration = json.loads(declaration_path.read_text(encoding="utf-8"))
    declaration["linked_manifest_sha256"] = "f" * 64
    declaration_path.write_text(
        json.dumps(declaration, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    with pytest.raises(Rx3LiraBarPrepError, match="different evidence revision"):
        _prepare(tmp_path, run_dir)
    assert not (tmp_path / "rx3_input").exists()


def test_swapped_evidence_is_rejected(tmp_path: Path) -> None:
    run_dir = _prepared_run(tmp_path)
    other = run_dir / "derived" / "rsu" / "rsu_evidence.json"
    payload = json.loads(other.read_text(encoding="utf-8"))
    payload["sources"]["forces"]["sha256"] = "0" * 64
    other.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    with pytest.raises(Rx3LiraBarPrepError, match="changed|no longer matches"):
        _prepare(tmp_path, run_dir)


# --------------------------------------------------------------------------
# Regressions: the template record must really be this experiment's mode
# --------------------------------------------------------------------------


def test_foreign_stress_state_record_is_rejected(tmp_path: Path) -> None:
    run_dir = _prepared_run(tmp_path)
    template = tmp_path / "axial.rx38"
    _template(template, **{"45": "Сжато-изгибаемый стержень в одной из плоскостей"})
    with pytest.raises(Rx3LiraBarPrepError, match="one-plane bending"):
        _prepare(tmp_path, run_dir, template=template)
    assert not (tmp_path / "rx3_input").exists()


def test_foreign_required_fire_resistance_is_rejected(tmp_path: Path) -> None:
    run_dir = _prepared_run(tmp_path)
    template = tmp_path / "r90.rx38"
    _template(template, **{"55": "90"})
    with pytest.raises(Rx3LiraBarPrepError, match="declares 15 min"):
        _prepare(tmp_path, run_dir, template=template)


def test_foreign_fire_regime_is_rejected(tmp_path: Path) -> None:
    run_dir = _prepared_run(tmp_path)
    template = tmp_path / "hc.rx38"
    _template(template, **{"104": "Углеводородный температурный режим"})
    with pytest.raises(Rx3LiraBarPrepError, match="standard regime"):
        _prepare(tmp_path, run_dir, template=template)


def test_unpinned_template_is_rejected(tmp_path: Path) -> None:
    run_dir = _prepared_run(tmp_path)
    template = tmp_path / "template.rx38"
    _template(template)
    with pytest.raises(Rx3LiraBarPrepError, match="not the controlled template"):
        prepare_rx3_lira_bar_validation(
            run_dir=run_dir,
            template_path=template,
            output_dir=tmp_path / "rx3_input",
        )
    assert not (tmp_path / "rx3_input").exists()


def test_controlled_template_pin_is_recorded(tmp_path: Path) -> None:
    run_dir = _prepared_run(tmp_path)
    manifest = _prepare(tmp_path, run_dir)
    assert manifest["template"]["pinned_sha256"] == _sha256(
        tmp_path / "template.rx38"
    )
    assert CONTROLLED_TEMPLATE_SHA256 != manifest["template"]["pinned_sha256"]


def test_ambiguous_template_target_is_rejected(tmp_path: Path) -> None:
    run_dir = _prepared_run(tmp_path)
    template = tmp_path / "two.rx38"
    _template(template)
    text = template.read_text(encoding="utf-8")
    template.write_text(text + text.splitlines()[-1] + "\r\n", encoding="utf-8")
    with pytest.raises(Rx3LiraBarPrepError, match="not unique"):
        _prepare(tmp_path, run_dir, template=template)


# --------------------------------------------------------------------------
# Refusals: no production shortcut
# --------------------------------------------------------------------------


def test_production_mode_is_refused(tmp_path: Path) -> None:
    run_dir = _prepared_run(tmp_path)
    with pytest.raises(Rx3LiraBarPrepError, match="VALIDATION-only"):
        _prepare(tmp_path, run_dir, mode=ExecutionMode.PRODUCTION)
    assert not (tmp_path / "rx3_input").exists()


def test_substituted_declaration_identity_is_rejected(tmp_path: Path) -> None:
    """A renamed bar or profile can never be re-pointed at another record."""

    run_dir = _prepared_run(tmp_path)
    declaration_path = run_dir / "declaration.json"
    declaration = json.loads(declaration_path.read_text(encoding="utf-8"))
    declaration["profile"]["mark"] = "Б3"
    declaration_path.write_text(
        json.dumps(declaration, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    with pytest.raises(Rx3LiraBarPrepError, match="profile|differing"):
        _prepare(tmp_path, run_dir)
    assert not (tmp_path / "rx3_input").exists()


def test_substituted_stored_bar_identity_is_rejected(tmp_path: Path) -> None:
    run_dir = _prepared_run(tmp_path)

    def mutate(payload: dict[str, object]) -> None:
        payload["bar"]["elements"] = payload["bar"]["elements"][:1]  # type: ignore[index]

    _edit_experiment_input(run_dir, mutate)
    with pytest.raises(Rx3LiraBarPrepError, match="bar.elements"):
        _prepare(tmp_path, run_dir)
    assert not (tmp_path / "rx3_input").exists()


def test_existing_output_directory_is_not_overwritten(tmp_path: Path) -> None:
    run_dir = _prepared_run(tmp_path)
    (tmp_path / "rx3_input").mkdir()
    with pytest.raises(Rx3LiraBarPrepError, match="refusing to overwrite"):
        _prepare(tmp_path, run_dir)


def test_unchanged_values_are_reported_instead_of_written(tmp_path: Path) -> None:
    run_dir = _prepared_run(tmp_path)
    template = tmp_path / "same.rx38"
    _template(
        template,
        **{
            "50": decimal_token(MY_KNM),
            "92": decimal_token(PREPARED_Q_KNM),
        },
    )
    with pytest.raises(Rx3LiraBarPrepError, match="already carries"):
        _prepare(tmp_path, run_dir, template=template)


def test_fire_resistance_minutes_parsing(tmp_path: Path) -> None:
    assert required_fire_resistance_minutes("R15", context="t") == 15
    assert required_fire_resistance_minutes("r90", context="t") == 90
    with pytest.raises(Rx3LiraBarPrepError, match="not declared"):
        required_fire_resistance_minutes(None, context="t")
    with pytest.raises(Rx3LiraBarPrepError, match="not a minute value"):
        required_fire_resistance_minutes("R15x", context="t")


def test_production_schema_policies_are_unchanged(tmp_path: Path) -> None:
    run_dir = _prepared_run(tmp_path)
    _prepare(tmp_path, run_dir)
    assert field_spec(50).write_policy is WritePolicy.EXPERIMENTAL
    assert field_spec(92).write_policy is WritePolicy.EXPERIMENTAL
    assert field_spec(78).write_policy is WritePolicy.FORBIDDEN
