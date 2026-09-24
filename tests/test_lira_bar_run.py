"""One-command run preparation: minimal input, verified derivation, no RX38."""

from __future__ import annotations

import hashlib
import json
import shutil
from decimal import Decimal
from pathlib import Path
from typing import Mapping

import pytest

from fireprotect.lira import (
    LiraFormatError,
    LiraMappingError,
    assemble_lira_model,
    derive_single_bar_chain,
    prepare_bar_run,
    read_element_table,
    read_node_table,
    read_stiffness_table,
)
from tests.test_lira_bar_experiment import (
    _default_plan,
    _evidence_payload,
    _make_steel_model,
    _write_biff,
    _write_evidence,
    _write_rsu_biff,
)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(65536), b""):
            digest.update(block)
    return digest.hexdigest()


def _make_source_package(
    tmp_path: Path,
    *,
    element_rows: list[list[object]] | None = None,
    plan: list[dict[str, object]] | None = None,
) -> Path:
    """Assemble a verified package shaped like a real LIRA run directory."""

    package = tmp_path / "source_pkg"
    package.mkdir()
    model, _ = _make_steel_model(tmp_path, element_rows=element_rows)
    shutil.move(str(model), str(package / "model"))
    rows = _default_plan()[0] if plan is None else plan
    sources = _write_rsu_biff(tmp_path, rows)
    evidence = _write_evidence(tmp_path, _evidence_payload(rows, sources))
    (package / "rsu").mkdir()
    shutil.copy(evidence, package / "rsu" / "rsu_evidence.json")
    return package


def _conditions_payload(
    *,
    bar_id: str = "B1",
    row_role: str = "USER_STATEMENT",
) -> dict[str, object]:
    return {
        "kind": "LIRA_BAR_EXPERIMENT_CONDITIONS",
        "bar_id": bar_id,
        "join_basis": "два КЭ одного прямого стержня, общий узел 3",
        "profile": {
            "standard": "ГОСТ 8240-97",
            "plane": "X-Z",
            "scheme_flag": "2",
            "rx3_template": "Б2",
            "stress_state": "ONE_PLANE_BENDING",
        },
        "design_conditions": {
            "effective_length_m": None,
            "support_condition": None,
            "heating_sides": None,
            "fire_regime": None,
            "required_fire_resistance_min": None,
        },
        "decisions": {
            "bar": {
                "role": "PACKAGE_EVIDENCE",
                "basis": "единственная связная цепочка КЭ в пакете",
                "reference": None,
            },
            "experiment_row": {
                "role": row_role,
                "basis": "строка технического опыта",
                "reference": None,
            },
            "profile": {
                "role": "SOURCE_DOCUMENT",
                "basis": "таблица 8.13",
                "reference": "021_ПЗ_Огнезащита.docx",
            },
            "design_conditions": {
                "role": "ASSISTANT_SELECTION",
                "basis": "учебная постановка ассистента",
                "reference": None,
            },
        },
    }


def _write_conditions(
    tmp_path: Path, payload: Mapping[str, object] | None = None
) -> Path:
    path = tmp_path / "conditions.json"
    path.write_text(
        json.dumps(payload or _conditions_payload(), ensure_ascii=False, indent=2)
        + "\n",
        encoding="utf-8",
    )
    return path


def _plan(tmp_path: Path) -> Path:
    path = tmp_path / "PLAN.md"
    path.write_text("# учебная постановка\n", encoding="utf-8")
    return path


def _prepare(
    tmp_path: Path,
    package: Path,
    *,
    output: str = "run_out",
    row_id: str = "R0001",
    conditions: Path | None = None,
    dry_run: bool = False,
    accept: bool = False,
) -> dict[str, object]:
    return prepare_bar_run(
        source_package=package,
        plan_reference=_plan(tmp_path),
        experiment_row_id=row_id,
        output_dir=tmp_path / output,
        conditions_path=conditions or _write_conditions(tmp_path),
        dry_run=dry_run,
        accept_current_sources=accept,
    )


# --------------------------------------------------------------------------
# Positive path
# --------------------------------------------------------------------------


def test_prepare_run_derives_everything_from_the_package(tmp_path: Path) -> None:
    package = _make_source_package(tmp_path)

    manifest = _prepare(tmp_path, package)

    assert manifest["kind"] == "LIRA_BAR_RUN_MANIFEST"
    assert manifest["status"] == "RUN_PREPARED"
    assert manifest["engineer_confirmed"] is False
    assert manifest["declaration_status"] == "DRAFT_UNSIGNED"
    assert manifest["experiment_row_id"] == "R0001"
    assert manifest["bar"]["element_ids"] == ["1", "2"]
    assert manifest["bar"]["end_node_ids"] == ["1", "2"]
    assert manifest["bar"]["element_lengths_m"] == {"1": "1.5", "2": "1.5"}
    assert manifest["bar"]["bar_length_m"] == "3.0"
    assert manifest["profile"]["designation"] == "22П"
    assert manifest["checks"]["sources_match_recorded_hashes"] is True
    assert manifest["governing_result_selection"] is None
    assert manifest["rx38_created"] is False
    assert manifest["release_forbidden"] is True
    assert manifest["issue_readiness"] == "NOT_READY_FOR_ISSUE"
    assert manifest["blockers"] == []

    output = tmp_path / "run_out"
    declaration = json.loads(
        (output / "declaration.json").read_text(encoding="utf-8")
    )
    assert declaration["declaration_status"] == "DRAFT_UNSIGNED"
    assert declaration["bar"]["confirmed_by"] is None
    assert declaration["experiment_row"]["selected_by"] is None
    assert declaration["profile"]["confirmed_by"] is None
    assert declaration["authorship"]["experiment_row"]["role"] == "USER_STATEMENT"
    assert declaration["linked_manifest_sha256"] == _sha256(
        output / "derived" / "linked" / "manifest.json"
    )

    experiment = json.loads(
        (output / "experiment" / "experiment_input.json").read_text(encoding="utf-8")
    )
    assert experiment["engineer_confirmed"] is False
    assert experiment["selected_row"]["row_id"] == "R0001"
    components = {item["component"]: item for item in experiment["components"]}
    assert Decimal(components["My"]["source_value"]) == Decimal("1.8")
    assert Decimal(components["Qz"]["source_value"]) == Decimal("-1.65")
    assert components["My"]["convention"]["resolved"] is True
    assert components["N"]["convention"]["resolved"] is False
    assert (output / "README_RUN.md").is_file()


def test_run_writes_no_rx38_and_does_not_touch_the_source_package(
    tmp_path: Path,
) -> None:
    package = _make_source_package(tmp_path)
    before = {
        path.relative_to(package).as_posix(): _sha256(path)
        for path in sorted(package.rglob("*"))
        if path.is_file()
    }

    manifest = _prepare(tmp_path, package)

    after = {
        path.relative_to(package).as_posix(): _sha256(path)
        for path in sorted(package.rglob("*"))
        if path.is_file()
    }
    assert before == after
    output = tmp_path / "run_out"
    assert list(output.rglob("*.rx38")) == []
    assert list(output.rglob("*.rxdb")) == []
    assert manifest["rx38_created"] is False


def test_repeat_run_reports_already_prepared_without_duplicating(
    tmp_path: Path,
) -> None:
    package = _make_source_package(tmp_path)
    conditions = _write_conditions(tmp_path)
    first = _prepare(tmp_path, package, conditions=conditions)
    assert first["status"] == "RUN_PREPARED"
    output = tmp_path / "run_out"
    hashes = {path.name: _sha256(path) for path in sorted(output.glob("*.json"))}

    second = _prepare(tmp_path, package, conditions=conditions)

    assert second["status"] == "RUN_ALREADY_PREPARED"
    assert second["request_fingerprint"] == first["request_fingerprint"]
    assert {
        path.name: _sha256(path) for path in sorted(output.glob("*.json"))
    } == hashes


def test_dry_run_checks_everything_and_writes_nothing(tmp_path: Path) -> None:
    package = _make_source_package(tmp_path)

    result = _prepare(tmp_path, package, dry_run=True)

    assert result["status"] == "RUN_DRY_RUN_OK"
    assert result["written_files"] == []
    assert not (tmp_path / "run_out").exists()
    assert result["bar"]["bar_length_m"] == "3.0"
    assert result["experiment_row_id"] == "R0001"
    components = {item["component"]: item for item in result["components"]}
    assert Decimal(components["My"]["source_value"]) == Decimal("1.8")
    assert result["missing_confirmations"] == [
        "effective_length_m",
        "support_condition",
        "heating_sides",
        "fire_regime",
        "required_fire_resistance_min",
    ]


# --------------------------------------------------------------------------
# Sources and identity of the run
# --------------------------------------------------------------------------


def test_changed_source_blocks_the_run_and_writes_nothing(tmp_path: Path) -> None:
    package = _make_source_package(tmp_path)
    manifest = json.loads(
        (package / "model" / "manifest.json").read_text(encoding="utf-8")
    )
    node_path = Path(manifest["sources"]["nodes"]["path"])
    node_path.write_bytes(node_path.read_bytes() + b"\x00")

    result = _prepare(tmp_path, package)

    assert result["status"] == "SOURCE_DRIFT_DETECTED"
    assert result["written_files"] == []
    assert not (tmp_path / "run_out").exists()
    drift = {item["role"]: item for item in result["drift"]}
    assert drift["nodes"]["state"] == "CONTENT_CHANGED"
    assert drift["nodes"]["recorded_sha256"] != drift["nodes"]["actual_sha256"]
    assert "--accept-current-sources" in result["next_action"]


def test_accepted_drift_is_recorded_in_the_manifest(tmp_path: Path) -> None:
    package = _make_source_package(tmp_path)
    manifest = json.loads(
        (package / "model" / "manifest.json").read_text(encoding="utf-8")
    )
    node_path = Path(manifest["sources"]["nodes"]["path"])
    node_path.write_bytes(node_path.read_bytes() + b"\x00")

    result = _prepare(tmp_path, package, accept=True)

    assert result["status"] == "RUN_PREPARED"
    drift = result["source_drift_accepted"]
    assert [item["role"] for item in drift] == ["nodes"]
    assert result["checks"]["sources_match_recorded_hashes"] is False


def test_missing_source_file_is_reported(tmp_path: Path) -> None:
    package = _make_source_package(tmp_path)
    manifest = json.loads(
        (package / "model" / "manifest.json").read_text(encoding="utf-8")
    )
    Path(manifest["sources"]["elements"]["path"]).unlink()

    result = _prepare(tmp_path, package)

    assert result["status"] == "SOURCE_DRIFT_DETECTED"
    assert result["drift"][0]["state"] == "MISSING"


def test_other_run_in_the_same_directory_is_refused(tmp_path: Path) -> None:
    package = _make_source_package(tmp_path)
    conditions = _write_conditions(tmp_path)
    _prepare(tmp_path, package, conditions=conditions)

    other = _write_conditions(tmp_path, _conditions_payload(bar_id="B2"))
    with pytest.raises(LiraFormatError, match="belongs to a different run"):
        _prepare(tmp_path, package, conditions=other)


def test_output_directory_with_conflicting_files_is_refused(tmp_path: Path) -> None:
    package = _make_source_package(tmp_path)
    output = tmp_path / "run_out"
    (output / "experiment").mkdir(parents=True)
    (output / "experiment" / "experiment_input.json").write_text("{}", encoding="utf-8")
    with pytest.raises(LiraFormatError, match="refusing to overwrite"):
        _prepare(tmp_path, package)


def test_existing_directory_with_unrelated_files_is_reused(tmp_path: Path) -> None:
    """An operator may pre-create the directory and drop the plan into it."""

    package = _make_source_package(tmp_path)
    output = tmp_path / "run_out"
    output.mkdir()
    (output / "NOTES.md").write_text("заметки\n", encoding="utf-8")

    manifest = _prepare(tmp_path, package)

    assert manifest["status"] == "RUN_PREPARED"
    assert (output / "NOTES.md").read_text(encoding="utf-8") == "заметки\n"


def test_row_for_an_element_outside_the_model_is_refused(tmp_path: Path) -> None:
    """A row of another element is never attached to the derived bar."""

    package = _make_source_package(
        tmp_path, element_rows=[[1, 10, 2, 1, 0, "-", "-", "1,3"]]
    )
    with pytest.raises(LiraMappingError, match="absent from the model package"):
        _prepare(tmp_path, package, row_id="R0002")


def test_unknown_row_id_is_refused(tmp_path: Path) -> None:
    package = _make_source_package(tmp_path)
    with pytest.raises(LiraMappingError, match="does not exist"):
        _prepare(tmp_path, package, row_id="R9999")


# --------------------------------------------------------------------------
# The draft can never claim a signature
# --------------------------------------------------------------------------


def test_conditions_claiming_an_engineer_are_refused(tmp_path: Path) -> None:
    package = _make_source_package(tmp_path)
    conditions = _write_conditions(
        tmp_path, _conditions_payload(row_role="ENGINEER_CONFIRMED")
    )
    with pytest.raises(LiraMappingError, match="ENGINEER_CONFIRMED"):
        _prepare(tmp_path, package, conditions=conditions)


def test_draft_package_stays_out_of_the_release_gate(tmp_path: Path) -> None:
    package = _make_source_package(tmp_path)
    manifest = _prepare(tmp_path, package)
    assert manifest["engineer_confirmed"] is False
    assert manifest["release_forbidden"] is True
    assert manifest["issue_readiness"] == "NOT_READY_FOR_ISSUE"
    readme = (tmp_path / "run_out" / "README_RUN.md").read_text(encoding="utf-8")
    assert "НЕТ (DRAFT)" in readme
    assert "RX38 не создан" in readme


# --------------------------------------------------------------------------
# Chain derivation is structural, never mark-based
# --------------------------------------------------------------------------


def _assemble(
    tmp_path: Path,
    *,
    element_rows: list[list[object]],
    node_rows: list[list[object]],
):
    base = tmp_path / "chain_src"
    _write_biff(
        base / "stiffness.xls",
        [(" ", [["t"], ["Тип жесткости", "Имя"], [1, "Швеллер 22П (Б2)"]])],
    )
    _write_biff(
        base / "elements.xls",
        [
            (
                " ",
                [
                    ["t"],
                    [],
                    [
                        "№ элем", "Тип элем", "Кол.сечений", "Тип жестк",
                        "Угол м.осей", "AX н", "AX к", "№№ узлов",
                    ],
                    *element_rows,
                ],
            )
        ],
    )
    _write_biff(
        base / "nodes.xls",
        [
            (
                " ",
                [
                    ["t"],
                    [],
                    [
                        "№ узла", "X\n(м)", "Y\n(м)", "Z\n(м)",
                        "X", "Y", "Z", "UX", "UY", "UZ",
                    ],
                    *node_rows,
                ],
            )
        ],
    )
    stiffnesses = read_stiffness_table(
        base / "stiffness.xls", sheet_name=" ", header_row=2
    )
    elements = read_element_table(base / "elements.xls", sheet_name=" ", header_row=3)
    nodes = read_node_table(base / "nodes.xls", sheet_name=" ", header_row=3)
    return assemble_lira_model(
        stiffnesses=stiffnesses, elements=elements, nodes=nodes
    )


def test_two_separate_chains_are_not_joined(tmp_path: Path) -> None:
    assembled = _assemble(
        tmp_path,
        element_rows=[
            [1, 10, 2, 1, 0, "-", "-", "1,3"],
            [2, 10, 2, 1, 0, "-", "-", "4,5"],
        ],
        node_rows=[
            [1, 0, 0, 0, "+", "-", "+", "-", "-", "-"],
            [3, 1.5, 0, 0, "-", "-", "-", "-", "-", "-"],
            [4, 5, 0, 0, "-", "-", "-", "-", "-", "-"],
            [5, 6.5, 0, 0, "-", "-", "+", "-", "-", "-"],
        ],
    )
    with pytest.raises(LiraMappingError, match="free ends"):
        derive_single_bar_chain(assembled)


def test_branching_model_is_not_split_by_heuristic(tmp_path: Path) -> None:
    assembled = _assemble(
        tmp_path,
        element_rows=[
            [1, 10, 2, 1, 0, "-", "-", "1,3"],
            [2, 10, 2, 1, 0, "-", "-", "3,2"],
            [3, 10, 2, 1, 0, "-", "-", "3,4"],
        ],
        node_rows=[
            [1, 0, 0, 0, "+", "-", "+", "-", "-", "-"],
            [3, 1.5, 0, 0, "-", "-", "-", "-", "-", "-"],
            [2, 3, 0, 0, "-", "-", "+", "-", "-", "-"],
            [4, 1.5, 1, 0, "-", "-", "-", "-", "-", "-"],
        ],
    )
    with pytest.raises(LiraMappingError, match="branches"):
        derive_single_bar_chain(assembled)


def test_reversed_element_orientation_is_not_rewritten(tmp_path: Path) -> None:
    assembled = _assemble(
        tmp_path,
        element_rows=[
            [1, 10, 2, 1, 0, "-", "-", "3,1"],
            [2, 10, 2, 1, 0, "-", "-", "3,2"],
        ],
        node_rows=[
            [1, 0, 0, 0, "+", "-", "+", "-", "-", "-"],
            [3, 1.5, 0, 0, "-", "-", "-", "-", "-", "-"],
            [2, 3, 0, 0, "-", "-", "+", "-", "-", "-"],
        ],
    )
    with pytest.raises(LiraMappingError, match="orientation is never rewritten"):
        derive_single_bar_chain(assembled)
