"""Reader decoder support belongs to the package, not an operator's development venv.

The dependency census: every decoder the artifact-reader registry can require at runtime
must be pinned in the canonical runtime dependencies and projected into every packaging
surface that installs the product. A decoder missing from any one surface means installs
built from that surface silently degrade a format to typed-unavailable — safe, but never
to be described as support. Pinned today:

* ``pypdf==6.19.0`` — confined PDF text fallback.
* ``xlrd==2.0.1``   — legacy XLS reader (pure Python, no native parts).
"""

from __future__ import annotations

from pathlib import Path

import pytest

try:
    import tomllib
except ModuleNotFoundError:  # Python 3.10, supported by the project.
    import tomli as tomllib


ROOT = Path(__file__).resolve().parents[1]

#: The full census: pin -> the registry format that dies without it.
CANONICAL_DECODERS = {
    "pypdf==6.19.0": "pdf",
    "xlrd==2.0.1": "xls",
}


def _read_toml(name: str) -> dict:
    return tomllib.loads((ROOT / name).read_text(encoding="utf-8"))


def _canonical_requirement(pin: str) -> str:
    name = pin.split("==")[0]
    matches = [
        item
        for item in _read_toml("pyproject.toml")["project"]["dependencies"]
        if item.startswith(name)
    ]
    assert matches == [pin], f"the tested {name} decoder must be pinned in the canonical runtime dependencies"
    return matches[0]


def test_census_covers_every_decoder_the_registry_can_require():
    """The census itself may not drift from the registry's declared requirements.

    The registry states requirements in prose. Platform tools (ffmpeg, bsdtar,
    textutil, PDFKit) are provided by the OS and are not pip-installable; the
    modules that ARE pip-installable must be exactly the census pins, named in
    the prose of the format that dies without them.
    """
    from core.artifact_readers import REGISTRY

    prose = " \n".join(
        req for spec in REGISTRY for req in spec.requires
    ).lower()
    for pin in CANONICAL_DECODERS:
        module = pin.split("==")[0]
        assert module in prose, f"the registry prose never names the pinned decoder {pin}"
    pip_named = {
        module
        for module in ("pypdf", "xlrd")
        if module in prose
    }
    assert pip_named == {pin.split("==")[0] for pin in CANONICAL_DECODERS}, (
        "a pip-installable decoder is named by the registry but missing from the census"
    )


@pytest.mark.parametrize("pin", sorted(CANONICAL_DECODERS))
def test_decoder_is_a_pinned_core_runtime_dependency_not_an_optional_extra(pin):
    _canonical_requirement(pin)


@pytest.mark.parametrize("filename", ["requirements-runtime.txt", "requirements.txt"])
@pytest.mark.parametrize("pin", sorted(CANONICAL_DECODERS))
def test_offline_installer_dependency_projection_contains_the_canonical_decoder(filename, pin):
    declarations = {
        line.split("#", 1)[0].strip()
        for line in (ROOT / filename).read_text(encoding="utf-8").splitlines()
    }
    assert _canonical_requirement(pin) in declarations


@pytest.mark.parametrize(
    ("filename", "start", "end"),
    [
        ("installer/bundle/build_macos_app.sh", 'uv pip install --python "${embedded}"', '|| die "lean dependency install failed"'),
        ("installer/bundle/build_bundle.ps1", "& $py -m pip install --no-build-isolation", "if ($LASTEXITCODE"),
    ],
)
@pytest.mark.parametrize("pin", sorted(CANONICAL_DECODERS))
def test_self_contained_bundle_install_command_contains_the_canonical_decoder(filename, start, end, pin):
    source = (ROOT / filename).read_text(encoding="utf-8")
    install_command = source.split(start, 1)[1].split(end, 1)[0]
    assert _canonical_requirement(pin) in install_command


@pytest.mark.parametrize("pin", sorted(CANONICAL_DECODERS))
def test_lock_graph_carries_the_same_pinned_runtime_dependency(pin):
    name, _, version = pin.partition("==")
    _canonical_requirement(pin)
    packages = _read_toml("uv.lock")["package"]
    project_name = _read_toml("pyproject.toml")["project"]["name"]
    project = next(item for item in packages if item["name"] == project_name)
    decoder = next((item for item in packages if item["name"] == name), None)
    assert decoder is not None, f"frozen installs must resolve the {name} decoder"
    assert decoder["version"] == version
    assert {"name": name} in project["dependencies"]
    assert {"name": name, "specifier": f"=={version}"} in project["metadata"]["requires-dist"]
