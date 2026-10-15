"""The Intel cryptography wheel recipe leaves no build-machine path and repacks deterministically.

A record_wheel run on the Intel runner shipped the runner's TMPDIR inside the wheel: `pip wheel
<sdist>` unpacked the sources into a random pip-req-build-* dir under TMPDIR that the rustc remaps
never named, and the cargo SBOM in dist-info recorded the same absolute paths (plus a random serial
number and a timestamp, so no two builds were byte-identical). These tests drive the recipe's own
code, extracted from .github/workflows/macos-intel.yml: the build environment block and the
repack/scrub/gate script.
"""
from __future__ import annotations

import base64
import hashlib
import json
import subprocess
import sys
import zipfile
from pathlib import Path

import yaml

from ops.bundle_dependency_provenance import verify_record

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github/workflows/macos-intel.yml"
BUILD_STEP = "Build cryptography natively for x86_64 (static OpenSSL, no-module)"
DIST_INFO = "cryptography-50.0.2.dist-info"


def _run_text() -> str:
    doc = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))
    return next(step for step in doc["jobs"]["build"]["steps"] if step.get("name") == BUILD_STEP)["run"]


def _env_block() -> str:
    run = _run_text()
    return run.split("# >>> deterministic build environment\n", 1)[1].split("# <<< deterministic build environment", 1)[0]


def _repack_script(tmp_path: Path) -> Path:
    script = tmp_path / "repack.py"
    script.write_text(_run_text().split("<<'REPACK'\n", 1)[1].split("\nREPACK", 1)[0], encoding="utf-8")
    return script


def test_the_build_environment_moves_tmpdir_under_the_build_dir_and_remaps_every_root(tmp_path: Path) -> None:
    w = tmp_path / "crypto-build"
    runner_tmp = "/var/folders/ab/cd1234/T/"
    probe = _env_block() + '\nenv | grep -E "^(TMPDIR|RUSTFLAGS|CFLAGS|CARGO_[A-Z_]+|SOURCE_DATE_EPOCH|ZERO_AR_DATE)=" | sort\n'
    done = subprocess.run(
        ["bash", "-c", "set -euo pipefail\n" + probe],
        env={"PATH": "/usr/bin:/bin", "HOME": "/Users/builder", "W": str(w), "TMPDIR": runner_tmp},
        capture_output=True, text=True,
    )
    assert done.returncode == 0, done.stderr
    env = dict(line.split("=", 1) for line in done.stdout.splitlines())
    # Everything that lands in a temp dir now lands at a fixed path inside the remapped build dir.
    assert env["TMPDIR"] == f"{w}/tmp"
    assert env["CARGO_TARGET_DIR"] == f"{w}/target"
    flags = env["RUSTFLAGS"].split()
    for mapping in (
        "/var/folders/ab/cd1234/T=/tmp",
        "/private/var/folders/ab/cd1234/T=/tmp",
        "/Users/builder=/home",
        "/Users/builder/.cargo=/cargo",
        "/Users/builder/.rustup=/rustup",
        f"{w}=/build",
    ):
        assert f"--remap-path-prefix={mapping}" in flags, mapping
        assert f"-ffile-prefix-map={mapping}" in env["CFLAGS"].split(), mapping
    # rustc applies the last matching remap: the build dir must come after the home it sits in.
    assert flags.index(f"--remap-path-prefix={w}=/build") > flags.index("--remap-path-prefix=/Users/builder=/home")
    assert env["SOURCE_DATE_EPOCH"] == "315532800" and env["ZERO_AR_DATE"] == "1"
    assert env["CARGO_INCREMENTAL"] == "0"
    assert env["CARGO_PROFILE_RELEASE_CODEGEN_UNITS"] == "1"
    assert env["CARGO_PROFILE_RELEASE_STRIP"] == "debuginfo"


def test_the_sdist_is_built_from_a_fixed_unpack_dir_not_a_tarball() -> None:
    run = _run_text()
    wheel_line = next(line for line in run.splitlines() if "-m pip wheel" in line)
    assert '"$W/src/cryptography-${CRYPTO_VERSION}" -w "$W/raw"' in wheel_line, wheel_line
    assert ".tar.gz" not in wheel_line, "pip wheel <sdist> unpacks into a random dir under TMPDIR"
    assert "extractall(sys.argv[2],filter='data')" in run
    # The environment is set before anything is built, OpenSSL included.
    assert run.index("# <<< deterministic build environment") < run.index("./Configure")


def _record(members: dict[str, bytes]) -> bytes:
    lines = []
    for name, data in members.items():
        digest = base64.urlsafe_b64encode(hashlib.sha256(data).digest()).rstrip(b"=").decode()
        lines.append(f"{name},sha256={digest},{len(data)}")
    lines.append(f"{DIST_INFO}/RECORD,,")
    return ("\n".join(lines) + "\n").encode()


def _raw_wheel(path: Path, build: Path, runner_tmp: str, *, serial: str, reverse: bool, so_extra: bytes = b"") -> None:
    sbom = {
        "bomFormat": "CycloneDX",
        "serialNumber": f"urn:uuid:{serial}",
        "metadata": {"timestamp": f"2026-10-14T13:{serial[:2]}:00Z", "component": {"name": "cryptography-rust"}},
        "components": [
            {"name": "cryptography-x509", "purl": f"pkg:cargo/cryptography-x509@0.1.0?download_url=file://{build}/src/cryptography-50.0.2/src/rust/cryptography-x509"},
            {"name": "build-tmp", "description": f"{runner_tmp}/pip-req-build-x1/src/rust"},
        ],
    }
    members = {
        "cryptography/__init__.py": b"__version__ = '50.0.2'\n",
        "cryptography/hazmat/bindings/_rust.abi3.so": b"\xcf\xfa\xed\xfe /build/src/rust/src/lib.rs\x00" + so_extra,
        f"{DIST_INFO}/METADATA": b"Metadata-Version: 2.4\nName: cryptography\nVersion: 50.0.2\n",
        f"{DIST_INFO}/WHEEL": b"Wheel-Version: 1.0\nGenerator: maturin (1.15.0)\nRoot-Is-Purelib: false\nTag: cp311-abi3-macosx_14_0_x86_64\n",
        f"{DIST_INFO}/sboms/cryptography-rust.cyclonedx.json": json.dumps(sbom).encode(),
    }
    members[f"{DIST_INFO}/RECORD"] = _record(members)
    names = list(members)
    if reverse:
        names.reverse()
    with zipfile.ZipFile(path, "w") as z:
        for index, name in enumerate(names):
            info = zipfile.ZipInfo(name, date_time=(2026, 10, 14, 13, index, 0))
            info.external_attr = (0o100755 if name.endswith(".so") else 0o100644) << 16
            z.writestr(info, members[name], compress_type=zipfile.ZIP_DEFLATED)


def _repack(script: Path, src: Path, dst: Path, home: str, runner_tmp: str, build: Path) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, "-I", str(script), str(src), str(dst),
         f"{home}=/home", f"{runner_tmp}=/tmp", f"/private{runner_tmp}=/tmp", f"{build}=/build",
         "--", str(build.parent)],
        capture_output=True, text=True,
    )


def test_the_repack_scrubs_the_sbom_rewrites_record_and_is_byte_reproducible(tmp_path: Path) -> None:
    script = _repack_script(tmp_path)
    home, runner_tmp = "/Users/builder", "/var/folders/ab/cd1234/T"
    build = tmp_path / "runner-temp" / "crypto-build"
    outputs = []
    for attempt, (serial, reverse) in enumerate((("11111111-aaaa", False), ("22222222-bbbb", True))):
        raw = tmp_path / f"raw{attempt}.whl"
        _raw_wheel(raw, build, runner_tmp, serial=serial, reverse=reverse)
        out = tmp_path / f"out{attempt}" / "cryptography-50.0.2-cp311-abi3-macosx_14_0_x86_64.whl"
        out.parent.mkdir()
        done = _repack(script, raw, out, home, runner_tmp, build)
        assert done.returncode == 0, done.stderr
        outputs.append(out)

    # Two builds that differ in SBOM serial, timestamps and member order repack to the same bytes.
    assert outputs[0].read_bytes() == outputs[1].read_bytes()

    wheel = outputs[0]
    blob = wheel.read_bytes()
    with zipfile.ZipFile(wheel) as z:
        names = z.namelist()
        sbom = json.loads(z.read(f"{DIST_INFO}/sboms/cryptography-rust.cyclonedx.json"))
        so = z.read("cryptography/hazmat/bindings/_rust.abi3.so")
        infos = z.infolist()
    for root in (str(build), runner_tmp, home):
        assert root.encode() not in blob, root
    assert "serialNumber" not in sbom and "timestamp" not in sbom["metadata"]
    assert sbom["components"][0]["purl"].endswith("download_url=file:///build/src/cryptography-50.0.2/src/rust/cryptography-x509")
    assert sbom["components"][1]["description"] == "/tmp/pip-req-build-x1/src/rust"
    assert so == b"\xcf\xfa\xed\xfe /build/src/rust/src/lib.rs\x00", "binary payload bytes must pass through untouched"
    assert names[-1] == f"{DIST_INFO}/RECORD"
    assert names[:2] == ["cryptography/__init__.py", "cryptography/hazmat/bindings/_rust.abi3.so"]
    assert {info.date_time for info in infos} == {(1980, 1, 1, 0, 0, 0)}
    assert (infos[1].external_attr >> 16) & 0o777 == 0o755
    record = verify_record(wheel)
    assert record["ok"] == 5 and not record["mismatched"] and not record["missing"], record


def test_a_build_path_left_in_the_binary_refuses_the_wheel(tmp_path: Path) -> None:
    """The scrub never edits a binary: a path the compiler remaps missed fails the step."""
    script = _repack_script(tmp_path)
    home, runner_tmp = "/Users/builder", "/var/folders/ab/cd1234/T"
    build = tmp_path / "runner-temp" / "crypto-build"
    raw = tmp_path / "raw.whl"
    _raw_wheel(raw, build, runner_tmp, serial="33333333-cccc", reverse=False,
               so_extra=f"/private{runner_tmp}/pip-req-build-x1/src/rust/src/lib.rs".encode())
    out = tmp_path / "out.whl"
    done = _repack(script, raw, out, home, runner_tmp, build)
    assert done.returncode != 0
    assert "cryptography/hazmat/bindings/_rust.abi3.so" in done.stderr
    assert '"T"' in done.stderr and runner_tmp not in done.stderr
    assert not out.exists(), "a refused wheel was written"
