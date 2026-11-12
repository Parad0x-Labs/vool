"""The macOS app installs exactly what uv.lock pins, and a build that drifts fails.

The 0.7.7 Mac apps (both architectures) shipped 32 of their 80 Python packages at versions other
than the lock's: the build installed a hand-written list of names and loose specifiers with
`uv pip install`, resolved live against the index (starlette 1.10.0 for the lock's 1.3.1, uvicorn
0.54.0 for 0.49.0, pyobjc 13.0 for 12.2.2 ...). The build now syncs the lock's own export
(ops/macos_locked_requirements.py) with --require-hashes and then checks the installed set against
it in both directions.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
import tomllib

from ops import macos_locked_requirements as locked

REPO = Path(__file__).resolve().parents[1]
BUILD_SCRIPT = REPO / "installer" / "bundle" / "build_macos_app.sh"
MODULE = REPO / "ops" / "macos_locked_requirements.py"

H1, H2, H3 = "1" * 64, "2" * 64, "3" * 64

# Lines in the exact shape `uv export --no-header --no-annotate` writes for this lock.
EXPORT = f"""\
ckzg==2.1.5 ; platform_machine == 'x86_64' and sys_platform == 'darwin' \\
    --hash=sha256:{H1}
ckzg==2.1.8 ; platform_machine != 'x86_64' or sys_platform != 'darwin' \\
    --hash=sha256:{H2}
colorama==0.4.6 ; sys_platform == 'win32' \\
    --hash=sha256:{H1}
cryptography==50.0.1 ; platform_machine != 'x86_64' or sys_platform != 'darwin' \\
    --hash=sha256:{H1} \\
    --hash=sha256:{H2}
cryptography==50.0.2 ; platform_machine == 'x86_64' and sys_platform == 'darwin' \\
    --hash=sha256:{H3}
cytoolz==1.1.0 ; implementation_name == 'cpython' \\
    --hash=sha256:{H1}
pycparser==3.0 ; (implementation_name != 'PyPy' and platform_python_implementation != 'PyPy') or (implementation_name != 'PyPy' and sys_platform == 'win32') \\
    --hash=sha256:{H1}
pyobjc-core==12.2.2 ; sys_platform == 'darwin' \\
    --hash=sha256:{H1}
secretstorage==3.5.0 ; sys_platform == 'linux' \\
    --hash=sha256:{H1}
starlette==1.3.1 \\
    --hash=sha256:{H1}
zipp==4.1.0 ; python_full_version < '3.12' \\
    --hash=sha256:{H1}
"""


def _names(pins):
    return {locked.norm(p.name): p.version for p in pins}


# ------------------------------------------------------------------------------------------
# The target set: markers evaluated for one Mac architecture and the embedded CPython
# ------------------------------------------------------------------------------------------


def test_the_arm64_set_takes_the_apple_silicon_side_of_every_fork():
    pins = locked.for_target(locked.parse(EXPORT), locked.target_environment("arm64", "3.11"))
    assert _names(pins) == {"ckzg": "2.1.8", "cryptography": "50.0.1", "cytoolz": "1.1.0", "pycparser": "3.0",
                            "pyobjc-core": "12.2.2", "starlette": "1.3.1", "zipp": "4.1.0"}


def test_the_intel_set_takes_the_intel_side_of_every_fork():
    pins = locked.for_target(locked.parse(EXPORT), locked.target_environment("x86_64", "3.11"))
    names = _names(pins)
    assert names["ckzg"] == "2.1.5" and names["cryptography"] == "50.0.2"
    assert "colorama" not in names and "secretstorage" not in names


def test_a_newer_embedded_python_drops_the_backports():
    pins = locked.for_target(locked.parse(EXPORT), locked.target_environment("arm64", "3.12"))
    assert "zipp" not in _names(pins)


@pytest.mark.parametrize("marker", [
    "platform_release == '24.0'",                 # a variable the target does not define
    "python_full_version < '3.11.4'",             # splits on a patch release nobody pinned
    "sys_platform == 'darwin' and",               # truncated
    "sys_platform ~= 'darwin'",                   # not a marker operator
    "sys_platform == platform_machine",           # two variables
])
def test_a_marker_that_cannot_be_decided_is_an_error_not_a_guess(marker):
    with pytest.raises(ValueError):
        locked.evaluate(marker, locked.target_environment("arm64", "3.11"))


def test_a_package_the_lock_applies_twice_on_one_target_is_refused():
    text = f"starlette==1.3.1 \\\n    --hash=sha256:{H1}\nstarlette==1.10.0 \\\n    --hash=sha256:{H2}\n"
    with pytest.raises(ValueError, match="applies starlette twice"):
        locked.for_target(locked.parse(text), locked.target_environment("arm64", "3.11"))


@pytest.mark.parametrize("line", [
    f"starlette>=1.3.1 \\\n    --hash=sha256:{H1}",   # a range, not a pin
    "starlette==1.3.1",                                 # no hash
    "starlette==1.3.1 \\\n    --hash=md5:abc",          # not sha256
])
def test_only_exact_hashed_pins_are_accepted(line):
    with pytest.raises(ValueError):
        locked.parse(line + "\n")


# ------------------------------------------------------------------------------------------
# Wheels built here: admitted at the lock's version, by digest, for their target only
# ------------------------------------------------------------------------------------------


def _record(**over):
    entry = {"file": "cryptography-50.0.2-cp311-abi3-macosx_14_0_x86_64.whl", "package": "cryptography",
             "version": "50.0.2", "sha256": "4" * 64, "target": "x86_64-apple-darwin"}
    entry.update(over)
    return entry


def test_the_intel_cryptography_wheel_joins_the_locked_digests_on_intel_only():
    intel, admitted = locked.locked_requirements("x86_64", EXPORT, records=[_record()])
    crypto = next(p for p in intel if p.name == "cryptography")
    assert crypto.version == "50.0.2" and crypto.hashes == [H3, "4" * 64]
    assert [a["package"] for a in admitted] == ["cryptography"]
    arm, admitted = locked.locked_requirements("arm64", EXPORT, records=[_record()])
    assert next(p for p in arm if p.name == "cryptography").hashes == [H1, H2] and admitted == []


def test_a_built_wheel_at_another_version_than_the_lock_stops_the_export():
    with pytest.raises(ValueError, match=re.escape("uv.lock pins cryptography==50.0.2")):
        locked.locked_requirements("x86_64", EXPORT, records=[_record(version="50.0.3")])


def test_a_built_wheel_for_a_package_the_app_does_not_ship_stops_the_export():
    with pytest.raises(ValueError, match="does not ship"):
        locked.locked_requirements("arm64", EXPORT, records=[_record(package="torch", target="any")])


# ------------------------------------------------------------------------------------------
# The drift check: installed set == pinned set, both directions
# ------------------------------------------------------------------------------------------


def _site(tmp_path: Path, dists: dict[str, str]) -> Path:
    site = tmp_path / "site-packages"
    site.mkdir()
    for name, version in dists.items():
        info = site / f"{name.replace('-', '_')}-{version}.dist-info"
        info.mkdir()
        (info / "METADATA").write_text(f"Metadata-Version: 2.4\nName: {name}\nVersion: {version}\n\nbody\n")
    return site


ARM64_SET = {"ckzg": "2.1.8", "cryptography": "50.0.1", "cytoolz": "1.1.0", "pycparser": "3.0",
             "pyobjc-core": "12.2.2", "starlette": "1.3.1", "zipp": "4.1.0"}


def _arm64_pins():
    return locked.for_target(locked.parse(EXPORT), locked.target_environment("arm64", "3.11"))


def test_an_exact_install_passes(tmp_path):
    assert locked.drift(_site(tmp_path, ARM64_SET), _arm64_pins()) == []


def test_the_0_7_7_drift_fails(tmp_path):
    """The shipped 0.7.7 apps: starlette 1.10.0 and pyobjc-core 13.0 where the lock pins 1.3.1 and 12.2.2."""
    site = _site(tmp_path, {**ARM64_SET, "starlette": "1.10.0", "pyobjc-core": "13.0"})
    assert locked.drift(site, _arm64_pins()) == [
        "pyobjc-core==13.0 is installed; the lock pins 12.2.2",
        "starlette==1.10.0 is installed; the lock pins 1.3.1",
    ]


def test_the_other_architectures_fork_is_drift(tmp_path):
    """Intel's ckzg 2.1.5 inside the arm64 app is not 'a version the lock mentions' -- it is drift."""
    site = _site(tmp_path, {**ARM64_SET, "ckzg": "2.1.5"})
    assert locked.drift(site, _arm64_pins()) == ["ckzg==2.1.5 is installed; the lock pins 2.1.8"]


def test_an_unlocked_extra_and_a_missing_pin_both_fail(tmp_path):
    dists = {k: v for k, v in ARM64_SET.items() if k != "zipp"}
    dists["pip"] = "26.0"
    problems = locked.drift(_site(tmp_path, dists), _arm64_pins())
    assert problems == ["pip==26.0 is installed but not in the locked set", "zipp==4.1.0 is pinned but not installed"]


def test_an_empty_site_packages_never_passes(tmp_path):
    assert locked.drift(_site(tmp_path, {}), _arm64_pins())


def test_the_check_command_exits_nonzero_on_drift(tmp_path):
    reqs = tmp_path / "requirements.txt"
    reqs.write_text(EXPORT)
    site = _site(tmp_path, {**ARM64_SET, "starlette": "1.10.0"})
    run = subprocess.run([sys.executable, "-I", str(MODULE), "check", "--arch", "arm64", "--site-packages", str(site),
                          "--requirements", str(reqs)], capture_output=True, text=True, timeout=60)
    assert run.returncode == 1, run.stdout + run.stderr
    assert "DRIFT starlette==1.10.0 is installed; the lock pins 1.3.1" in run.stdout
    (site / "starlette-1.10.0.dist-info").rename(site / "starlette-1.3.1.dist-info")
    (site / "starlette-1.3.1.dist-info" / "METADATA").write_text("Name: starlette\nVersion: 1.3.1\n\n")
    run = subprocess.run([sys.executable, "-I", str(MODULE), "check", "--arch", "arm64", "--site-packages", str(site),
                          "--requirements", str(reqs)], capture_output=True, text=True, timeout=60)
    assert run.returncode == 0 and "PASS" in run.stdout, run.stdout + run.stderr


# ------------------------------------------------------------------------------------------
# The real repository: lock, built-wheel record and the build script agree
# ------------------------------------------------------------------------------------------


def _lock_packages() -> list[dict]:
    return tomllib.loads((REPO / "uv.lock").read_text(encoding="utf-8"))["package"]


def test_the_intel_forks_in_the_lock_match_the_recorded_intel_cryptography_build():
    """No Intel macOS wheel exists for ckzg 2.1.8 or any cryptography >= 49: the lock pins Intel to
    ckzg 2.1.5 (the last release with an Intel wheel) and to the cryptography version whose sdist the
    recorded Intel wheel is built from."""
    packages = _lock_packages()
    versions = {(p["name"], p["version"]) for p in packages}
    assert {("ckzg", "2.1.5"), ("ckzg", "2.1.8"), ("cryptography", "50.0.1"), ("cryptography", "50.0.2")} <= versions
    intel_ckzg = next(p for p in packages if p["name"] == "ckzg" and p["version"] == "2.1.5")
    assert any("macosx" in w["url"] and "x86_64" in w["url"] and "cp311" in w["url"] for w in intel_ckzg["wheels"])
    record = next(w for w in locked.built_wheels() if w["package"] == "cryptography")
    crypto = next(p for p in packages if p["name"] == "cryptography" and p["version"] == record["version"])
    assert crypto["sdist"]["hash"] == f"sha256:{record['source_sdist_sha256']}"


def test_every_package_the_app_declares_is_in_the_lock():
    from ops import bundle_dependency_provenance as prov

    names = {p["name"] for p in _lock_packages()}
    declared = [locked.norm(n) for n in prov.lean_requirements()]
    assert declared and not [n for n in declared if n not in names]
    for needed in ("zstandard", "eth-abi", "eth-utils", "eth-account", "x402", "pywebview",
                   "pyobjc-framework-eventkit", "pyobjc-framework-contacts", "pypdf", "xlrd"):
        assert needed in declared, f"the macOS app no longer declares {needed}"


def _dependency_step() -> str:
    src = BUILD_SCRIPT.read_text(encoding="utf-8")
    start = src.index('say "  [2/4] lean runtime deps')
    end = src.index('|| die "the installed dependencies differ from uv.lock')
    return src[start:end]


def test_the_build_installs_the_locked_export_with_hashes_and_nothing_else():
    step = _dependency_step()
    export = step.index("ops/macos_locked_requirements.py\" export")
    sync = step.index("uv pip sync --python")
    check = step.index("ops/macos_locked_requirements.py\" check")
    assert export < sync < check, "export, sync and the drift check must run in that order"
    command = step[sync:step.index('|| die "lean dependency install failed"')]
    for flag in ("--require-hashes", "--no-build", "--strict", '--python-platform "${UV_PLATFORM}"'):
        assert flag in command, f"the locked install lost {flag}"
    assert command.rstrip().rstrip("\\").rstrip().endswith('"${locked_reqs}"'), command
    # Nothing in the dependency step resolves names against the index.
    assert "uv pip install" not in re.sub(r"(?m)^\s*#.*$", "", step)


def test_the_whole_build_installs_python_packages_only_through_the_lock():
    code = re.sub(r"(?m)^\s*#.*$", "", BUILD_SCRIPT.read_text(encoding="utf-8"))
    assert "uv pip install" not in code and "pip install" not in code


# ------------------------------------------------------------------------------------------
# The real export (uv is present on the package runners and wherever the build runs)
# ------------------------------------------------------------------------------------------

needs_uv = pytest.mark.skipif(shutil.which("uv") is None, reason="uv exports the lock; the macOS package jobs run this for real")


@needs_uv
@pytest.mark.parametrize("arch", ["arm64", "x86_64"])
def test_the_real_export_is_the_lock_exactly(arch, tmp_path):
    out = tmp_path / "requirements.txt"
    report = tmp_path / "built.json"
    run = subprocess.run([sys.executable, "-I", str(MODULE), "export", "--arch", arch, "--out", str(out),
                          "--report", str(report)], capture_output=True, text=True, timeout=300, cwd=REPO)
    assert run.returncode == 0, run.stdout + run.stderr
    pins = locked.parse(out.read_text())
    lock = {(p["name"], p["version"]) for p in _lock_packages()}
    assert pins and all((locked.norm(p.name), p.version) in lock for p in pins)
    names = _names(pins)
    assert len(names) == len(pins), "a package appears twice in a single-target export"
    assert names["ckzg"] == ("2.1.8" if arch == "arm64" else "2.1.5")
    assert names["cryptography"] == ("50.0.1" if arch == "arm64" else "50.0.2")
    assert "pip" not in names and "torch" not in names and "playwright" not in names
    built = {b["package"] for b in json.loads(report.read_text())["built_wheels"]}
    assert built == ({"proxy-tools"} if arch == "arm64" else {"cryptography", "proxy-tools"})
