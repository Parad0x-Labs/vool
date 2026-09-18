"""A package manager accepting a wheel is not provenance.

During this lane's preflight, two bundle pins were supplied from wheels RE-ZIPPED out of
unpacked ~/.cache/uv archive directories. Their CONTENTS were intact -- every file matched its
RECORD digest -- but their own sha256 matched nothing in uv.lock, because re-zipping changes
container bytes. Both `uv` and `pip` installed them without complaint.

These pins hold the distinction that makes the difference: content integrity is DIAGNOSTIC,
artifact identity against the lock is RELEASE.
"""
from __future__ import annotations

import base64
import hashlib
import zipfile
from pathlib import Path

import pytest

from ops import bundle_dependency_provenance as prov


def _wheel(path: Path, files: dict[str, bytes], dist: str = "demo-1.0.dist-info") -> Path:
    """A minimal but structurally real wheel, with a RECORD that matches its members."""
    lines = []
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
        for name, data in files.items():
            z.writestr(name, data)
            digest = base64.urlsafe_b64encode(hashlib.sha256(data).digest()).rstrip(b"=").decode()
            lines.append(f"{name},sha256={digest},{len(data)}")
        lines.append(f"{dist}/RECORD,,")
        z.writestr(f"{dist}/RECORD", "\n".join(lines))
    return path


def test_record_verification_accepts_an_intact_wheel(tmp_path):
    w = _wheel(tmp_path / "demo-1.0-py3-none-any.whl", {"demo/__init__.py": b"x = 1\n"})
    r = prov.verify_record(w)
    assert r["ok"] >= 1 and not r["mismatched"] and not r["missing"]


def test_record_verification_catches_a_tampered_member(tmp_path):
    w = _wheel(tmp_path / "demo-1.0-py3-none-any.whl", {"demo/__init__.py": b"x = 1\n"})
    # Rewrite one member so its bytes no longer match the RECORD digest.
    import shutil
    tampered = tmp_path / "tampered.whl"
    with zipfile.ZipFile(w) as src, zipfile.ZipFile(tampered, "w") as dst:
        for item in src.infolist():
            data = src.read(item.filename)
            if item.filename == "demo/__init__.py":
                data = b"x = 666  # tampered\n"
            dst.writestr(item, data)
    r = prov.verify_record(tampered)
    assert "demo/__init__.py" in r["mismatched"], "a tampered payload passed RECORD verification"


def test_a_wheel_whose_digest_is_not_in_the_lock_is_never_release_grade(tmp_path, monkeypatch):
    """THE CASE THAT MOTIVATED THIS TOOL: intact contents, unknown artifact."""
    wh = tmp_path / "wh"; wh.mkdir()
    _wheel(wh / "demo-1.0-py3-none-any.whl", {"demo/__init__.py": b"x = 1\n"})
    monkeypatch.setattr(prov, "lock_artifacts", lambda: {
        "demo": {"demo-1.0-py3-none-any.whl": "0" * 64, "__version__": "1.0"}})
    monkeypatch.setattr(prov, "lean_requirements", lambda: ["demo"])
    report = prov.audit(wh)
    entry = report["artifacts"][0]
    assert entry["record_verified"] is True, "contents are intact"
    assert entry["artifact_matches_lock"] is False, "but the artifact is not the pinned one"
    assert entry["grade"] == "DIAGNOSTIC"
    assert report["release_grade"] is False


def test_a_wheel_matching_the_lock_digest_is_release_grade(tmp_path, monkeypatch):
    wh = tmp_path / "wh"; wh.mkdir()
    w = _wheel(wh / "demo-1.0-py3-none-any.whl", {"demo/__init__.py": b"x = 1\n"})
    real = hashlib.sha256(w.read_bytes()).hexdigest()
    monkeypatch.setattr(prov, "lock_artifacts", lambda: {
        "demo": {"demo-1.0-py3-none-any.whl": real, "__version__": "1.0"}})
    monkeypatch.setattr(prov, "lean_requirements", lambda: ["demo"])
    report = prov.audit(wh)
    assert report["artifacts"][0]["grade"] == "RELEASE"
    assert report["release_grade"] is True


def test_a_corrupt_payload_is_rejected_even_if_the_digest_were_pinned(tmp_path, monkeypatch):
    wh = tmp_path / "wh"; wh.mkdir()
    w = _wheel(wh / "demo-1.0-py3-none-any.whl", {"demo/__init__.py": b"x = 1\n"})
    with zipfile.ZipFile(w) as src:
        items = [(i.filename, src.read(i.filename)) for i in src.infolist()]
    with zipfile.ZipFile(w, "w") as dst:
        for name, data in items:
            dst.writestr(name, b"corrupt" if name == "demo/__init__.py" else data)
    real = hashlib.sha256(w.read_bytes()).hexdigest()
    monkeypatch.setattr(prov, "lock_artifacts", lambda: {
        "demo": {"demo-1.0-py3-none-any.whl": real, "__version__": "1.0"}})
    monkeypatch.setattr(prov, "lean_requirements", lambda: ["demo"])
    assert prov.audit(wh)["artifacts"][0]["grade"] == "REJECTED"


def test_an_unpinned_lean_requirement_blocks_release_grade(tmp_path, monkeypatch):
    """pywebview is really in this state: installed into every bundle, absent from uv.lock."""
    wh = tmp_path / "wh"; wh.mkdir()
    w = _wheel(wh / "demo-1.0-py3-none-any.whl", {"demo/__init__.py": b"x = 1\n"})
    real = hashlib.sha256(w.read_bytes()).hexdigest()
    monkeypatch.setattr(prov, "lock_artifacts", lambda: {
        "demo": {"demo-1.0-py3-none-any.whl": real, "__version__": "1.0"}})
    monkeypatch.setattr(prov, "lean_requirements", lambda: ["demo", "pywebview"])
    report = prov.audit(wh)
    assert report["lean_requirements_absent_from_uv_lock"] == ["pywebview"]
    assert report["release_grade"] is False


def test_the_lean_list_is_read_from_the_build_script_and_holds_no_flags():
    """One source of truth, and no option flags misread as packages."""
    names = prov.lean_requirements()
    assert "zstandard" in names and "pywebview" in names and "cryptography" in names
    assert not [n for n in names if n.startswith("-")]
    for phantom in ("quiet", "no", "compile", "bytecode", "find", "links"):
        assert phantom not in names, f"shell flag {phantom!r} read as a package"


def test_the_real_repo_state_is_reported_honestly():
    """pywebview is genuinely unpinned today. If this ever fails, the hole was closed --
    update the record rather than loosening the assertion."""
    lock = prov.lock_artifacts()
    assert "pywebview" not in lock
    for pinned in ("zstandard", "xlrd", "cryptography", "solders"):
        assert pinned in lock


def test_nested_shell_expansions_do_not_swallow_the_package_line():
    """The bash-3.2 guard `${arr[@]+"${arr[@]}"}` nests a quoted expansion inside an expansion.
    Measured on the merged build script (2026-09-07): stripping to the FIRST closing brace left a
    stray quote that paired with the next quoted specifier and swallowed the whole first line of
    packages -- the lean list read `['}', 'starlette', ...]` with pydantic, cryptography, requests,
    pynacl, keyring, psutil, pyyaml and pywebview missing, and `||` read as a package."""
    body = (
        '  uv pip install --python "${embedded}" --no-compile-bytecode --quiet \\\n'
        '      ${wheelhouse_args[@]+"${wheelhouse_args[@]}"} \\\n'
        '      pydantic cryptography requests pynacl keyring psutil pyyaml \\\n'
        '      "starlette>=0.37,<2.0" "uvicorn>=0.30,<1.0" solders pywebview "pypdf==6.16.2" "xlrd==2.0.1" \\\n'
        '      zstandard \\\n'
        '      "eth-abi>=6.0" "eth-utils>=6.0" "eth-account>=0.13" \\\n'
        '    || die "lean dependency install failed"\n'
    )
    names = prov.lean_requirements_from_text(body)
    assert names == [
        "pydantic", "cryptography", "requests", "pynacl", "keyring", "psutil", "pyyaml",
        "starlette", "uvicorn", "solders", "pywebview", "pypdf", "xlrd", "zstandard",
        "eth-abi", "eth-utils", "eth-account",
    ], names
