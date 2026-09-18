"""An "exact" count may only be claimed after a COMPLETE, race-free, bounded traversal.

What this file pins
-------------------
`build_exact_file_facts` answers count requests the user explicitly marked "do not estimate", so
the word "exact" in its reply is a claim about the WALK, not just about arithmetic: every matching
file seen, every directory opened, nothing read from outside the bound workspace, and a
deterministic winner among equal-line files. Five ways that claim could silently rot, each with a
control below:

1. an unscannable nested directory (`os.scandir` raising) is silently skipped while the reply
   still says the count is exact;
2. a file vanishing between directory enumeration and stat/open crashes the turn;
3. an equal-line-count tie is decided by filesystem enumeration order;
4. a huge tree runs without any entry, byte or wall-clock bound;
5. a file swapped for a symlink between enumeration and open would be read through — outside the
   workspace.

These were written RED-first against the traversal that shipped with the original G12 repair;
they pin the typed measurement result (`measure_workspace_files`) whose fields make completeness
itself assertable: `complete`, `count`, `largest`, exclusions, and `termination_reason`.
"""
from __future__ import annotations

import os
from pathlib import Path

import pytest

import core.folder_overview as folder_overview
from core.folder_overview import build_exact_file_facts, measure_workspace_files


def _write(path: Path, lines: int) -> None:
    path.write_text("\n".join(f"l{n}" for n in range(lines)) + "\n", encoding="utf-8")


def _reversed_scandir(monkeypatch: pytest.MonkeyPatch) -> None:
    """Force directory enumeration into reverse-sorted order, over REAL files."""
    import contextlib

    real = os.scandir

    @contextlib.contextmanager
    def fake(path):
        entries = sorted(real(path), key=lambda e: e.name, reverse=True)
        yield iter(entries)  # scandir is used as a context manager yielding an iterator

    monkeypatch.setattr(folder_overview.os, "scandir", fake)


# --------------------------------------------------------------------------------- 1. unscannable


def test_an_unscannable_directory_never_yields_an_exact_count(tmp_path: Path):
    sealed = tmp_path / "sealed"
    sealed.mkdir()
    _write(sealed / "hidden.py", 3)
    _write(tmp_path / "visible.py", 2)
    os.chmod(sealed, 0)
    try:
        measurement = measure_workspace_files(str(tmp_path), "py")
        reply = build_exact_file_facts(str(tmp_path), "py")
    finally:
        os.chmod(sealed, 0o755)

    assert measurement is not None
    assert measurement.complete is False, "a directory could not be opened; the count is not exact"
    assert "unscannable" in measurement.termination_reason, measurement.termination_reason
    assert any("sealed" in directory for directory in measurement.unscannable_directories), (
        measurement.unscannable_directories
    )
    assert measurement.count == 1, measurement.count  # only visible.py; hidden.py is not claimed

    assert reply is not None
    assert "counted, not estimated" not in reply.lower(), reply


def test_an_unscannable_leaf_directory_is_named_not_swallowed(tmp_path: Path):
    deep = tmp_path / "vendor" / "locked"
    deep.mkdir(parents=True)
    _write(deep / "inside.py", 1)
    os.chmod(deep, 0)
    try:
        reply = build_exact_file_facts(str(tmp_path), "py")
    finally:
        os.chmod(deep, 0o755)

    assert reply is not None
    lowered = reply.lower()
    assert "counted, not estimated" not in lowered, reply
    assert "locked" in reply or "could not" in lowered, reply


# --------------------------------------------------------------------------------- 2. vanishing


def test_a_file_vanishing_mid_walk_does_not_crash_the_turn(tmp_path: Path, monkeypatch):
    """scandir hands out names; by the time they are stat'ed or opened they may be gone."""
    doomed = tmp_path / "doomed.py"
    _write(doomed, 4)
    _write(tmp_path / "stable.py", 2)
    real_counter = folder_overview._count_file_lines

    def vanish_then_count(path, **kwargs):
        if Path(path).name == "doomed.py":
            try:
                os.unlink(path)
            except FileNotFoundError:
                pass
        return real_counter(path, **kwargs)

    monkeypatch.setattr(folder_overview, "_count_file_lines", vanish_then_count)

    measurement = measure_workspace_files(str(tmp_path), "py")

    assert measurement is not None, "the turn must survive a vanished entry"
    assert "stable.py" in measurement.largest_relpath, measurement
    assert measurement.count == 1, measurement  # doomed.py was seen but never measured
    assert any("doomed" in entry for entry in measurement.raced_entries), measurement


# ---------------------------------------------------------------------------------- 3. ties


def test_equal_line_counts_are_broken_by_relative_path_not_enumeration_order(
    tmp_path: Path, monkeypatch
):
    _write(tmp_path / "zeta.py", 5)
    _write(tmp_path / "alpha.py", 5)
    _reversed_scandir(monkeypatch)  # zeta.py is enumerated FIRST

    measurement = measure_workspace_files(str(tmp_path), "py")

    assert measurement.complete is True
    assert measurement.largest_relpath == "alpha.py", measurement.largest_relpath


def test_the_tie_break_survives_nested_and_mixed_case_paths(tmp_path: Path, monkeypatch):
    nested = tmp_path / "pkg"
    nested.mkdir()
    _write(nested / "Beta.py", 7)
    _write(tmp_path / "zeta.py", 7)
    _reversed_scandir(monkeypatch)

    measurement = measure_workspace_files(str(tmp_path), "py")

    assert measurement.largest_relpath == "pkg/Beta.py", measurement.largest_relpath


# -------------------------------------------------------------------------------- 4. budgets


def test_an_entry_budget_terminates_predictably_and_refuses_exactness(tmp_path: Path):
    for index in range(20):
        _write(tmp_path / f"f{index:02d}.py", 1)

    measurement = measure_workspace_files(str(tmp_path), "py", max_entries=5)

    assert measurement.complete is False
    assert "entry budget" in measurement.termination_reason, measurement.termination_reason
    assert measurement.count <= 5, measurement.count
    reply = build_exact_file_facts(str(tmp_path), "py", max_entries=5)
    assert reply is not None
    assert "counted, not estimated" not in reply.lower(), reply
    assert "entry budget" in reply.lower(), reply


def test_a_time_budget_terminates_predictably(tmp_path: Path):
    for index in range(200):
        _write(tmp_path / f"g{index:03d}.py", 1)

    measurement = measure_workspace_files(str(tmp_path), "py", max_seconds=0.0)

    assert measurement.complete is False
    assert "time budget" in measurement.termination_reason, measurement.termination_reason
    reply = build_exact_file_facts(str(tmp_path), "py", max_seconds=0.0)
    assert "time budget" in reply.lower(), reply


def test_a_byte_budget_terminates_predictably_and_refuses_exactness(tmp_path: Path):
    _write(tmp_path / "big.py", 4000)
    _write(tmp_path / "small.py", 1)

    measurement = measure_workspace_files(str(tmp_path), "py", max_total_bytes=64)

    assert measurement.complete is False
    assert "byte budget" in measurement.termination_reason, measurement.termination_reason
    reply = build_exact_file_facts(str(tmp_path), "py", max_total_bytes=64)
    assert "byte budget" in reply.lower(), reply


# --------------------------------------------------------------------------- 5. symlink swap


def test_a_symlink_swap_never_reads_outside_the_workspace(tmp_path: Path, monkeypatch):
    """The file scanned at enumeration time must be the file opened, and it must be INSIDE."""
    outside = tmp_path / "outside"
    outside.mkdir()
    secret = outside / "secret.txt"
    secret.write_text("TOP-SECRET-OUTSIDE-CONTENT", encoding="utf-8")

    project = tmp_path / "project"
    project.mkdir()
    victim = project / "victim.py"
    _write(victim, 2)
    _write(project / "keeper.py", 9)

    real_counter = folder_overview._count_file_lines

    def swap_then_count(path, **kwargs):
        if Path(path).name == "victim.py":
            try:
                os.unlink(path)
            except FileNotFoundError:
                pass
            os.symlink(secret, path)
        return real_counter(path, **kwargs)

    monkeypatch.setattr(folder_overview, "_count_file_lines", swap_then_count)

    measurement = measure_workspace_files(str(project), "py")
    reply = build_exact_file_facts(str(project), "py")

    assert "TOP-SECRET-OUTSIDE-CONTENT" not in (reply or ""), reply
    assert measurement.largest_relpath == "keeper.py", measurement
    assert measurement.complete is False, "a raced entry means the walk was not complete"
    assert any("victim" in entry for entry in measurement.raced_entries), measurement
    assert "victim" in measurement.termination_reason or "swapped" in measurement.termination_reason, (
        measurement.termination_reason
    )


# -------------------------------------------------------------------------------- completeness


def test_a_normal_repository_still_yields_a_complete_exact_measurement(tmp_path: Path):
    (tmp_path / "a.py").write_text("one\n", encoding="utf-8")
    sub = tmp_path / "deep"
    sub.mkdir()
    _write(sub / "b.py", 6)

    measurement = measure_workspace_files(str(tmp_path), "py")

    assert measurement.complete is True
    assert measurement.count == 2
    assert measurement.largest_relpath == "deep/b.py"
    assert measurement.largest_lines == 6
    assert measurement.termination_reason == ""
    reply = build_exact_file_facts(str(tmp_path), "py")
    assert "counted, not estimated" in reply.lower(), reply
