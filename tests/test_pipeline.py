import csv
from datetime import datetime, timezone
from decimal import Decimal
import json
from pathlib import Path
import shutil

import pytest

from fireprotect.model import (
    EffectiveLengthParameters,
    ProjectElement,
    ProvenanceType,
    Quantity,
    Unit,
    ValueProvenance,
)
from fireprotect.pipeline import PipelineError, run_pipeline
from fireprotect.project_io import write_project_element_json
from fireprotect.rx3.parser import construction_records, read_rx38
from fireprotect.rx3.safety import rx38_record_fingerprint


def _element() -> ProjectElement:
    values = {name: None for name in ProjectElement.field_names()}
    values.update(
        project_id="P1",
        element_id="17",
        mark="K-NEW",
        element_type="column",
        source_file="base.json",
        source_type="PROJECT_JSON",
        source_element_id="17",
        timestamp=datetime(2026, 8, 25, tzinfo=timezone.utc),
        section_type="I-section",
        profile_standard="STO ASCHM 20-93",
        profile_name="30K1",
        area=Quantity.of("11080", Unit.SQUARE_MILLIMETER),
        full_perimeter=Quantity.of("1774", Unit.MILLIMETER),
        heated_perimeter=Quantity.of("1774", Unit.MILLIMETER),
        ptm=Quantity.of("6.24577226606539", Unit.MILLIMETER),
        length=Quantity.of("4", Unit.METER),
        quantity=2,
        steel_grade="S245",
        Ry=Quantity.of("245", Unit.MEGAPASCAL),
        E=Quantity.of("206", Unit.GIGAPASCAL),
        density=Quantity.of("7850", Unit.KILOGRAM_PER_CUBIC_METER),
        required_fire_resistance=Quantity.of("90", Unit.MINUTE),
        stress_state="compression",
        heating_sides=4,
        support_condition="pinned",
        effective_length_parameters=EffectiveLengthParameters(
            Quantity.of("2.8", Unit.METER),
            Quantity.of("2.8", Unit.METER),
            Decimal("0.7"),
            Decimal("0.7"),
        ),
        protected_area=Quantity.of("14.192", Unit.SQUARE_METER),
    )
    values["provenance"] = {
        name: ValueProvenance(
            ProvenanceType.SOURCE, file="base.json", field=name
        )
        for name, value in values.items()
        if value is not None and name not in ProjectElement._UNTRACED_FIELDS
    }
    return ProjectElement(**values)


def _template(path: Path) -> None:
    fields = [""] * 200
    for index, value in {
        0: "Tconstr",
        1: "K1",
        3: "K1",
        5: "I-section",
        17: "STO ASCHM 20-93",
        19: "30 K1",
        20: "11080",
        32: "7850",
        33: "245",
        34: "206000",
        42: "S245",
        44: "650",
        45: "compression",
        48: "pinned",
        50: "0",
        54: "15",
        72: "None",
    }.items():
        fields[index] = value
    with path.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.writer(stream, delimiter=";", lineterminator="\r\n")
        writer.writerow(["Trazdel", "Pipeline"])
        writer.writerow(fields)


def _calculate(generated: Path, calculated: Path) -> None:
    shutil.copy2(generated, calculated)
    with calculated.open("r", encoding="utf-8", newline="") as stream:
        rows = list(csv.reader(stream, delimiter=";"))
    record = next(row for row in rows if row and row[0] == "Tconstr")
    record[44] = "675"
    record[54] = "18"
    with calculated.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.writer(stream, delimiter=";", lineterminator="\r\n")
        writer.writerows(rows)


def test_pipeline_stops_for_rx3_and_resumes_after_calculated_file(tmp_path: Path):
    project = tmp_path / "base.json"
    template = tmp_path / "template.rx38"
    lira = tmp_path / "forces.csv"
    config = tmp_path / "pipeline.json"
    write_project_element_json(_element(), project)
    _template(template)
    lira.write_text(
        "id;section;case;comb;N;Mx;My;Qx;Qy\n"
        "17;30K1;LC1;C1;-125.5;0;0;0;0\n",
        encoding="utf-8",
    )
    payload = {
        "schema_version": 2,
        "execution_mode": "VALIDATION",
        "calculation_date": "2026-08-25",
        "workspace": "run",
        "lira": {
            "format": "csv",
            "path": "forces.csv",
            "options": {"delimiter": ";"},
            "columns": {
                "element_id": "id",
                "section": "section",
                "load_case": "case",
                "combination": "comb",
                "N": "N",
                "Mx": "Mx",
                "My": "My",
                "Qx": "Qx",
                "Qy": "Qy",
            },
            "units": {
                "N": "kN",
                "Mx": "kN*m",
                "My": "kN*m",
                "Qx": "kN",
                "Qy": "kN",
            },
        },
        "elements": [
            {
                "project_json": "base.json",
                "lira_element_id": "17",
                "load_case": "LC1",
                "combination": "C1",
                "set_governing_combination": True,
                "rx38_template": "template.rx38",
                "template_mark": "K1",
                "gui_execution_evidence": "ENGINEER_CONFIRMED",
                "gui_evidence_reference": "pytest controlled GUI run",
                "rx3_safety": {
                    "template_evidence": {
                        "use_case": "AXIAL_ONLY",
                        "status": "VERIFIED",
                        "source": "controlled RX3 template validation",
                        "engineer_confirmation": True,
                        "confirmed_by": "test engineer",
                        "confirmed_at": "2026-08-25",
                        "version": "1",
                        "calculation_profile_verified": True,
                        "template_record_sha256": rx38_record_fingerprint(
                            construction_records(read_rx38(template))[0]
                        ),
                    },
                    "heating_exposure": {
                        "project_element_id": "17",
                        "heating_sides": 4,
                        "template_record_sha256": rx38_record_fingerprint(
                            construction_records(read_rx38(template))[0]
                        ),
                        "status": "VERIFIED",
                        "source": "controlled heating exposure fixture",
                        "confirmed_by": "test engineer",
                        "confirmed_at": "2026-08-25",
                        "version": "1",
                    },
                    "force_convention": {
                        "source_system": "LIRA CSV",
                        "target_system": "RX3",
                        "positive_n_meaning": "tension",
                        "negative_n_meaning": "compression",
                        "local_axes": "element local axes",
                        "moment_mapping": "Mx->Mx, My->My",
                        "shear_mapping": "Qx->Qx, Qy->Qy",
                        "multipliers": {
                            "N": "1",
                            "Mx": "1",
                            "My": "1",
                            "Qx": "1",
                            "Qy": "1"
                        },
                        "rule_name": "identity test convention",
                        "evidence_source": "controlled validation protocol",
                        "status": "VERIFIED",
                        "engineer_confirmation": True,
                        "confirmed_by": "test engineer",
                        "confirmed_at": "2026-08-25",
                        "version": "1"
                    }
                },
                "required_fire_resistance_decision": {
                    "R": {"value": "90", "unit": "min"},
                    "construction_type": "column",
                    "building_fire_resistance_degree": "II",
                    "source": "engineer assignment",
                    "clause_or_table": None,
                    "assignment_method": "explicit",
                    "engineer_confirmation": True,
                },
            }
        ],
    }
    config.write_text(json.dumps(payload), encoding="utf-8")

    first = run_pipeline(config)
    assert first.status == "WAITING_FOR_RX3"
    assert len(first.waiting_for) == 1
    assert first.audit_json.exists() and first.audit_markdown.exists()
    generated = first.workspace / "rx3" / "001_17" / "generated.rx38"
    assert generated.exists()

    _calculate(generated, first.waiting_for[0])
    second = run_pipeline(config)
    assert second.status == "RX3_RESULT_ANALYSED"
    assert not second.waiting_for
    audit = json.loads(second.audit_json.read_text(encoding="utf-8"))
    assert audit["rx3_results"][0]["result"]["critical_temperature"] == {
        "value": "675",
        "unit": "degC",
    }
    assert any("EXCEL_STAGE_NOT_CONFIGURED" in item for item in audit["warnings"])
    assert audit["elements"][0]["critical_temperature"] == {
        "value": "675",
        "unit": "degC",
    }
    assert audit["issue_readiness"]["status"] == "NOT_READY_FOR_ISSUE"
    element_audit = audit["element_audits"][0]
    assert element_audit["lira_source"]["source_values"]["units"]["N"] == "kN"
    assert element_audit["force_convention"]["transformations"][0][
        "status"
    ] == "VERIFIED"
    assert element_audit["geometry"]["calculated"]["formula"] == (
        "area_mm2 / heated_perimeter_mm"
    )
    assert element_audit["steel"]["compatibility"]["status"] == (
        "LEGACY_NUMERIC_MATCH"
    )
    assert element_audit["rx3_generated"]["changed_fields"]
    assert element_audit["rx3_result"]["gui_recalculation_verified"] is True

    legacy_payload = json.loads(json.dumps(payload))
    legacy_payload["schema_version"] = 1
    legacy_payload["execution_mode"] = "PRODUCTION"
    legacy_payload.pop("calculation_date")
    legacy_payload["workspace"] = "legacy_run"
    config.write_text(json.dumps(legacy_payload), encoding="utf-8")
    legacy = run_pipeline(config)
    legacy_audit = json.loads(legacy.audit_json.read_text(encoding="utf-8"))
    assert legacy_audit["execution_mode"] == "DRAFT"
    assert legacy.issue_readiness.blockers[0].code.value == "NON_PRODUCTION_MODE"

    production_payload = json.loads(json.dumps(payload))
    production_payload["execution_mode"] = "PRODUCTION"
    production_payload["workspace"] = "production_unverified_technical"
    config.write_text(json.dumps(production_payload), encoding="utf-8")
    with pytest.raises(PipelineError, match="technical selection gate blocked"):
        run_pipeline(config)

    changed_mode = json.loads(json.dumps(payload))
    changed_mode["execution_mode"] = "DRAFT"
    config.write_text(json.dumps(changed_mode), encoding="utf-8")
    with pytest.raises(PipelineError, match="does not match current"):
        run_pipeline(config)

    float_payload = json.loads(json.dumps(payload))
    float_payload["workspace"] = "float_run"
    float_payload["elements"][0]["required_fire_resistance_decision"]["R"][
        "value"
    ] = 90.0
    config.write_text(json.dumps(float_payload), encoding="utf-8")
    with pytest.raises(PipelineError, match="binary float"):
        run_pipeline(config)
