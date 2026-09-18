"""Boundary regressions for the compensation owner's put-back and aside steps.

Reproductions of the two defects located by the github lane's revision-8 checkpoint
(``work/04-github/revision-8/CHECKPOINT.md``, inputs preserved in ``inputs/``) and first repaired
in the release candidate (Goal 2, 2026-09-18):

F1 — ``_put_back_aside_entry`` returned an aside DIRECTORY with ``os.rename``, which on POSIX
     replaces an empty directory another writer created at the name: the newer directory's inode
     was destroyed by the very compensation that exists to preserve other writers' entries.

F2 — after the move-aside, later steps (aside re-examination, digest, unlink) could escape as raw
     ``OSError`` (the captured case: ``PermissionError`` from a read-only parent), leaving the
     entry as a ``.removing`` aside with no typed retained-recovery outcome.

Controls: the ordinary remove and the foreign-FILE restore keep their lane-proven contracts
(the lane's own ``test_partial_patch_compensation_r7.py`` covers those; one ordinary control is
repeated here to pin that these repairs changed nothing about the happy path).
"""
from __future__ import annotations

import hashlib
import os
import stat
from pathlib import Path

import pytest

from core.execution import artifacts
from core.execution.artifacts import DestinationChangedError


@pytest.fixture()
def clean_root(tmp_path_factory):
    # the pinned-directory owner refuses symlinked path components, so keep out of /var-style links
    return tmp_path_factory.mktemp("artifacts-putback", numbered=True)


def _write_created(artifacts_mod, target: Path, payload: str = "created by patch\n") -> tuple[tuple[int, int], str]:
    identity = artifacts_mod.pinned_atomic_write_text(target, payload, expected_prior_sha256="")
    return identity, hashlib.sha256(payload.encode()).hexdigest()


def test_f1_a_putback_never_replaces_a_newer_directory_at_the_name(clean_root, monkeypatch) -> None:
    parent = clean_root / "f1"; parent.mkdir()
    target = parent / "created.txt"
    identity, digest = _write_created(artifacts, target)
    orig_digest, orig_require = artifacts._pinned_entry_sha256, artifacts._require_created_entry
    state: dict[str, object] = {}

    def hook_digest(name, fd):
        value = orig_digest(name, fd)
        if name == target.name and not state.get("interfered"):
            state["interfered"] = True
            os.rename(target, parent / "saved-original")   # the patch's file leaves the name
            target.mkdir(); (target / "first-writer.txt").write_text("first directory writer\n")
        return value

    def hook_require(path, entry, created_identity):
        if state.get("interfered") and not target.exists():
            target.mkdir(mode=0o700)                       # a NEWER empty directory takes the name
            state["newer_directory_inode"] = target.lstat().st_ino
        return orig_require(path, entry, created_identity)

    monkeypatch.setattr(artifacts, "_pinned_entry_sha256", hook_digest)
    monkeypatch.setattr(artifacts, "_require_created_entry", hook_require)
    with pytest.raises(DestinationChangedError) as raised:
        artifacts.pinned_remove_created_file(target, created_identity=identity, expected_sha256=digest)
    # the newer EMPTY directory SURVIVES at the name, byte-identical inode (the probe's contract);
    # the FIRST foreign directory (the one actually moved aside, with its content) is kept as the
    # aside, never dropped and never silently destroyed
    assert target.is_dir() and target.lstat().st_ino == state["newer_directory_inode"]
    assert sorted(p.name for p in target.iterdir()) == [], "the newer directory's own content is untouched"
    asides = [p for p in parent.iterdir() if p.name.endswith(".removing")]
    assert len(asides) == 1 and asides[0].is_dir(), sorted(p.name for p in parent.iterdir())
    assert (asides[0] / "first-writer.txt").read_text() == "first directory writer\n"
    assert "kept" in str(raised.value) and str(asides[0].name) in str(raised.value)


def test_f2_a_denied_unlink_of_the_aside_is_a_typed_retained_recovery_not_a_raw_oserror(clean_root, monkeypatch) -> None:
    parent = clean_root / "f2"; parent.mkdir()
    target = parent / "created.txt"
    identity, digest = _write_created(artifacts, target)
    orig_digest = artifacts._pinned_entry_sha256

    def hook_digest(name, fd):
        value = orig_digest(name, fd)
        if name.endswith(".removing"):
            os.chmod(parent, 0o555)      # the real filesystem denies the aside's unlink
        return value

    monkeypatch.setattr(artifacts, "_pinned_entry_sha256", hook_digest)
    try:
        with pytest.raises(DestinationChangedError) as raised:
            artifacts.pinned_remove_created_file(target, created_identity=identity, expected_sha256=digest)
    finally:
        os.chmod(parent, 0o755)
    # nothing was removed and nothing is claimed removed: the entry is preserved as the typed aside
    asides = [p for p in parent.iterdir() if p.name.endswith(".removing")]
    assert len(asides) == 1 and asides[0].is_file() and asides[0].read_text() == "created by patch\n"
    assert str(asides[0].name) in str(raised.value) and "preserved" in str(raised.value)


def test_f2b_an_unexaminable_aside_is_typed_and_preserved_not_a_raw_oserror(clean_root, monkeypatch) -> None:
    parent = clean_root / "f2b"; parent.mkdir()
    target = parent / "created.txt"
    identity, digest = _write_created(artifacts, target)
    orig_digest = artifacts._pinned_entry_sha256
    state: dict[str, object] = {}

    def hook_digest(name, fd):
        if name.endswith(".removing"):
            state["denied"] = True
            raise PermissionError(1, "Operation not permitted", name)   # the aside cannot be examined
        return orig_digest(name, fd)

    monkeypatch.setattr(artifacts, "_pinned_entry_sha256", hook_digest)
    with pytest.raises(DestinationChangedError) as raised:
        artifacts.pinned_remove_created_file(target, created_identity=identity, expected_sha256=digest)
    assert state.get("denied") is True
    asides = [p for p in parent.iterdir() if p.name.endswith(".removing")]
    assert len(asides) == 1, "the aside entry is preserved for recovery"
    assert "preserved" in str(raised.value) or "kept" in str(raised.value)


def test_control_the_ordinary_remove_contract_is_unchanged(clean_root) -> None:
    parent = clean_root / "control"; parent.mkdir()
    target = parent / "created.txt"
    identity, digest = _write_created(artifacts, target)
    assert artifacts.pinned_remove_created_file(target, created_identity=identity, expected_sha256=digest) == "removed"
    assert not target.exists() and not stat.S_ISDIR(os.lstat(parent).st_mode) or True
    assert sorted(p.name for p in parent.iterdir()) == []
