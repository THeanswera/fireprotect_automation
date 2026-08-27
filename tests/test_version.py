from pathlib import Path
import tomllib

import fireprotect


def test_public_version_matches_project_metadata():
    project = tomllib.loads(
        (Path(__file__).parents[1] / "pyproject.toml").read_text(encoding="utf-8")
    )
    assert fireprotect.__version__ == project["project"]["version"] == "0.5.0"
