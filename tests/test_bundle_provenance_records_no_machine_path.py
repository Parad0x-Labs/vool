"""The shipped dependency-provenance record names its wheelhouse without the builder's disk layout.

Release dry run, 2026-10-07: config/dependency-provenance.json inside the app recorded the wheelhouse as an
absolute path on the build machine, so every installed copy carried it.
"""
from __future__ import annotations

from pathlib import Path

from ops import bundle_dependency_provenance as prov


def test_a_wheelhouse_outside_the_build_root_is_named_without_its_path(tmp_path):
    wheelhouse = tmp_path / "builder-disk" / "wheels-v2"
    wheelhouse.mkdir(parents=True)
    recorded = prov.audit(wheelhouse)["wheelhouse"]
    assert not Path(recorded).is_absolute(), recorded
    assert str(tmp_path) not in recorded and "builder-disk" not in recorded, recorded
    assert recorded.endswith("wheels-v2"), recorded


def test_a_wheelhouse_inside_the_build_root_is_recorded_relative_to_it():
    wheelhouse = prov.REPO / "installer" / "bundle"
    assert prov.audit(wheelhouse)["wheelhouse"] == "installer/bundle"


def test_the_committed_intel_cryptography_record_is_the_rebuilt_wheel():
    built = {entry["file"]: entry for entry in prov.built_wheels().values()}
    entry = built["cryptography-50.0.2-cp311-abi3-macosx_14_0_x86_64.whl"]
    assert entry["sha256"] == "65031bce8e063ccf4c65a19f5008cc21bfdc47cf2d0e9b5d2eeb727b6b3fd922"
