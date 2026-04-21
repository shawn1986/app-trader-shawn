from __future__ import annotations

import importlib.metadata as metadata
import subprocess
import sys
import tomllib
from pathlib import Path

from trader_shawn.app import main


def load_pyproject() -> dict:
    return tomllib.loads(Path("pyproject.toml").read_text(encoding="utf-8"))


def test_pyproject_declares_src_layout_for_editable_installs() -> None:
    pyproject = load_pyproject()

    assert pyproject["tool"]["setuptools"]["package-dir"] == {"": "src"}
    assert pyproject["tool"]["setuptools"]["packages"]["find"]["where"] == ["src"]


def test_pyproject_exposes_cli_entrypoint() -> None:
    pyproject = load_pyproject()

    assert pyproject["project"]["scripts"] == {
        "trader-shawn": "trader_shawn.app:main",
    }


def test_pyproject_keeps_src_on_pytest_pythonpath_for_repo_root_runs() -> None:
    pyproject = load_pyproject()

    assert pyproject["tool"]["pytest"]["ini_options"]["pythonpath"] == ["src"]


def test_installed_console_entrypoint_resolves_to_app_main() -> None:
    entrypoints = [
        entrypoint
        for entrypoint in metadata.entry_points(group="console_scripts")
        if entrypoint.name == "trader-shawn"
    ]

    assert entrypoints
    assert entrypoints[0].value == "trader_shawn.app:main"
    assert entrypoints[0].load() is main


def test_module_is_runnable_from_outside_repo(tmp_path: Path) -> None:
    result = subprocess.run(
        [sys.executable, "-m", "trader_shawn.app", "--help"],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0
    assert "Usage: trader-shawn [OPTIONS] COMMAND [ARGS]..." in result.stdout
