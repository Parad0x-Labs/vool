"""Keep patched dependency floors in frozen installs and bootstrap paths."""

from __future__ import annotations

from importlib import metadata
from pathlib import Path

import pytest
from packaging.requirements import Requirement
from packaging.version import Version

try:
    import tomllib
except ModuleNotFoundError:
    import tomli as tomllib


ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize(("package", "patched"), [("pypdf", "6.21.2"), ("setuptools", "84.1.0")])
def test_frozen_dependencies_meet_the_advisory_patch_floor(package, patched):
    lock = tomllib.loads((ROOT / "uv.lock").read_text())
    versions = [entry["version"] for entry in lock["package"] if entry["name"] == package]
    assert versions, f"{package} is missing from the frozen dependency graph"
    assert all(Version(version) >= Version(patched) for version in versions)


def test_pdf_decoder_installed_for_the_reader_is_patched():
    assert Version(metadata.version("pypdf")) >= Version("6.21.2")


def test_build_and_bootstrap_paths_cannot_select_vulnerable_setuptools():
    project = tomllib.loads((ROOT / "pyproject.toml").read_text())
    declarations = [
        Requirement(value)
        for values in (project["build-system"]["requires"], project["tool"]["uv"]["constraint-dependencies"])
        for value in values
        if Requirement(value).name == "setuptools"
    ]
    assert len(declarations) == 2
    for requirement in declarations:
        assert "84.1.0" in requirement.specifier
        assert "84.0.0" not in requirement.specifier
    for filename in (
        "installer/install_vool.sh",
        "installer/install_vool.bat",
        "installer/bundle/build_bundle.ps1",
    ):
        source = (ROOT / filename).read_text()
        assert '"setuptools>=84.1.0"' in source, filename
