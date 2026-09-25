from __future__ import annotations

from decimal import Decimal
import json
from pathlib import Path
import sys

import pytest

from fireprotect.cli import main
from fireprotect.rx3.template_intake import (
    Rx3BaselineError,
    BaselineExpectation,
    prepare_rx3_baseline_observation,
)

REPO = Path(__file__).resolve().parents[1]
CORPUS = REPO / "rx3" / "новый_5_814_89.rx38"
RXDB = REPO / "rx3" / "rx3.rxdb"
BENDING = "Изгибаемый стержень в одной из главных плоскостей"


def _expectation(**overrides: object) -> BaselineExpectation:
    values: dict[str, object] = {
        "mark": "Б2",
        "standard": "ГОСТ 8240-97",
        "designation": "22П",
        "length_m": Decimal("3"),
        "stress_state": BENDING,
        "required_fire_resistance_min": Decimal("15"),
    }
    values.update(overrides)
    return BaselineExpectation(**values)  # type: ignore[arg-type]


def test_intake_freezes_a_corpus_record_and_checks_its_identity(tmp_path: Path) -> None:
    before = CORPUS.read_bytes()
    report = prepare_rx3_baseline_observation(
        file=CORPUS, expectation=_expectation(), profile_db=RXDB,
        output_dir=tmp_path / "baseline",
    )
    assert report["status"] == "WAITING_FOR_BASELINE_GUI_OBSERVATION"
    assert report["calculation_gate"] == "CLOSED_UNTIL_ENGINEER_OBSERVATION"
    assert report["controlled_scope_registered"] is False
    assert report["rx38_force_generation_allowed"] is False
    assert report["issue_readiness"] == "NOT_READY_FOR_ISSUE"
    assert report["target"]["position"] == 13
    assert report["source"]["record_count"] == 17
    assert report["assortment"]["table"] == "sh"
    assert all(item["status"] == "PASS" for item in report["declared_identity"])
    assert all(item["status"] == "PASS" for item in report["identity_relations"])
    assert all(item["status"] == "PASS" for item in report["assortment"]["checks"])
    copy_path = Path(str(report["frozen_copy"]["path"]))
    assert copy_path.read_bytes() == before
    assert report["frozen_copy"]["sha256"] == report["source"]["sha256"]
    assert CORPUS.read_bytes() == before
    checklist = (tmp_path / "baseline" / "CHECKLIST.md").read_text(encoding="utf-8")
    assert "не нажимать «Рассчитать»" in checklist
    assert report["observed_fields"]["major_axis_moment_knm"]["stored_token"] == "14,7987"
    written = json.loads(
        (tmp_path / "baseline" / "baseline_observation.json").read_text(encoding="utf-8")
    )
    assert written["target"]["fingerprint"] == report["target"]["fingerprint"]


def test_intake_refuses_a_wrong_declared_identity(tmp_path: Path) -> None:
    destination = tmp_path / "baseline"
    with pytest.raises(Rx3BaselineError, match="does not match the declared identity"):
        prepare_rx3_baseline_observation(
            file=CORPUS, expectation=_expectation(length_m=Decimal("2.5")),
            profile_db=RXDB, output_dir=destination,
        )
    assert not destination.exists()


def test_intake_refuses_an_unknown_mark(tmp_path: Path) -> None:
    with pytest.raises(Rx3BaselineError, match="no Tconstr record carries"):
        prepare_rx3_baseline_observation(
            file=CORPUS, expectation=_expectation(mark="НетТакой"), profile_db=RXDB,
            output_dir=tmp_path / "baseline",
        )


def test_intake_refuses_a_profile_outside_the_assortment(tmp_path: Path) -> None:
    text = CORPUS.read_text(encoding="utf-8")
    broken = text.replace("22П", "22ПНЕТ", 1)
    altered = tmp_path / "altered.rx38"
    altered.write_text(broken, encoding="utf-8")
    with pytest.raises(Rx3BaselineError, match="no row for standard"):
        prepare_rx3_baseline_observation(
            file=altered, expectation=_expectation(designation="22ПНЕТ"),
            profile_db=RXDB, output_dir=tmp_path / "baseline",
        )


def test_intake_refuses_a_geometry_that_contradicts_the_assortment(tmp_path: Path) -> None:
    lines = CORPUS.read_text(encoding="utf-8").splitlines(keepends=True)
    fields = lines[12].rstrip("\r\n").split(";")
    assert fields[1] == "Б2"
    fields[20] = "9999"  # cross-sectional area
    lines[12] = ";".join(fields) + "\n"
    altered = tmp_path / "altered.rx38"
    altered.write_text("".join(lines), encoding="utf-8")
    with pytest.raises(Rx3BaselineError, match="identity relations|assortment database"):
        prepare_rx3_baseline_observation(
            file=altered, expectation=_expectation(), profile_db=RXDB,
            output_dir=tmp_path / "baseline",
        )


def test_intake_refuses_an_existing_output_directory(tmp_path: Path) -> None:
    (tmp_path / "baseline").mkdir()
    with pytest.raises(Rx3BaselineError, match="already exists"):
        prepare_rx3_baseline_observation(
            file=CORPUS, expectation=_expectation(), profile_db=RXDB,
            output_dir=tmp_path / "baseline",
        )


def test_cli_prepares_the_baseline_observation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "fireprotect", "prepare-rx3-baseline-observation",
            "--file", str(CORPUS),
            "--mark", "Б2",
            "--standard", "ГОСТ 8240-97",
            "--designation", "22П",
            "--length-m", "3",
            "--stress-state", BENDING,
            "--required-r-min", "15",
            "--profile-db", str(RXDB),
            "--output-dir", str(tmp_path / "baseline"),
        ],
    )
    main()
    payload = json.loads(capsys.readouterr().out)
    assert payload["status"] == "WAITING_FOR_BASELINE_GUI_OBSERVATION"
    assert payload["target"]["position"] == 13
    assert (tmp_path / "baseline" / "CHECKLIST.md").is_file()
