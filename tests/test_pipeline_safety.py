import json
from pathlib import Path

import pytest

from fireprotect.pipeline import PipelineError, run_pipeline
from fireprotect.project_io import write_project_element_json
from tests.safety_support import make_element, write_template


def test_pipeline_blocks_nonzero_unmapped_moment_before_rx38_creation(
    tmp_path: Path,
):
    write_project_element_json(make_element(), tmp_path / "element.json")
    write_template(tmp_path / "template.rx38")
    (tmp_path / "forces.csv").write_text(
        "id;section;case;comb;N;Mx;My;Qx;Qy\n"
        "E1;30K1;LC1;C1;-125.5;12;0;0;0\n",
        encoding="utf-8",
    )
    config = {
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
                "project_json": "element.json",
                "lira_element_id": "E1",
                "load_case": "LC1",
                "combination": "C1",
                "set_governing_combination": True,
                "rx38_template": "template.rx38",
                "template_mark": "K1",
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
    config_path = tmp_path / "pipeline.json"
    config_path.write_text(json.dumps(config), encoding="utf-8")

    with pytest.raises(PipelineError, match="Mx=12000"):
        run_pipeline(config_path)
    assert not (tmp_path / "run" / "rx3" / "001_E1" / "generated.rx38").exists()
    audit = json.loads(
        (tmp_path / "run" / "project_audit.json").read_text(encoding="utf-8")
    )
    assert audit["status"] == "FAILED"
    assert "UnverifiedRx38ActionMappingError" in audit["errors"][0]
