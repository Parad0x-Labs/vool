"""Keep patched dependency floors in frozen installs and bootstrap paths."""

from __future__ import annotations

import json
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


@pytest.mark.parametrize(("package", "patched"), [("pypdf", "6.21.4"), ("setuptools", "84.1.0")])
def test_frozen_dependencies_meet_the_advisory_patch_floor(package, patched):
    lock = tomllib.loads((ROOT / "uv.lock").read_text())
    versions = [entry["version"] for entry in lock["package"] if entry["name"] == package]
    assert versions, f"{package} is missing from the frozen dependency graph"
    assert all(Version(version) >= Version(patched) for version in versions)


# The phone app's tooling lock. A merge that takes an older branch's mobile/package-lock.json puts
# patched releases back without touching package.json, and the scheduled Security Audit is the only
# other check that reads this file. Each row: package, first affected release, first patched release.
#   node-forge  21 advisories up to 1.4.2, e.g. GHSA-86w9-cpqp-85rv, GHSA-6889-vf6x-f3g5
#   glob        GHSA-3g3m-2fjr-6pw4, 9.0.0 - 14.0.1 (the 7.x line React Native uses is outside it)
#   minimatch   GHSA-g3hv-75wg-m249, 7.0.0 - 11.0.0 (the 3.x line is outside it)
@pytest.mark.parametrize(
    ("package", "affected_from", "patched"),
    [("node-forge", "0", "1.4.3"), ("glob", "9.0.0", "14.0.2"), ("minimatch", "7.0.0", "11.0.1")],
)
def test_mobile_tooling_lock_meets_the_advisory_patch_floor(package, affected_from, patched):
    lock = json.loads((ROOT / "mobile" / "package-lock.json").read_text())
    versions = {
        entry["version"]
        for path, entry in lock["packages"].items()
        if path.rpartition("node_modules/")[2] == package and "version" in entry
    }
    assert versions, f"{package} is missing from the mobile lock"
    exposed = sorted(v for v in versions if Version(affected_from) <= Version(v) < Version(patched))
    assert not exposed, f"mobile/package-lock.json locks {package} {exposed}; the first patched release is {patched}"


def test_pdf_decoder_installed_for_the_reader_is_patched():
    assert Version(metadata.version("pypdf")) >= Version("6.21.4")


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
