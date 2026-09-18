"""Adversarial paths for the machine read tools (spec Section 16): every failure mode must be
HONEST — an empty/partial result or a "won't guess" message — never a fabricated figure or file.
Deterministic via injected walk/getsize/usage functions; touches no real filesystem.
"""
from __future__ import annotations

import os
from unittest import mock

import core.machine_diagnostics as md
from core.runtime_execution_tools import execute_runtime_tool


def test_largest_files_skips_a_file_deleted_mid_scan() -> None:
    # getsize raises for a file that vanished between listing and sizing -> skipped, no crash.
    tree = [("R", [], ["keep.bin", "gone.bin", "small.bin"])]
    sizes = {"keep.bin": 100, "small.bin": 5}

    def getsize(path: str) -> int:
        name = os.path.basename(path)
        if name == "gone.bin":
            raise OSError("deleted before verification")
        return sizes[name]

    res = md.largest_files("R", top=5, walk_fn=lambda r: iter(tree), getsize_fn=getsize, monotonic_fn=lambda: 0.0)
    assert [e["name"] for e in res["entries"]] == ["keep.bin", "small.bin"]  # gone.bin skipped, not invented


def test_largest_files_reports_two_way_tie() -> None:
    tree = [("R", [], ["a.bin", "b.bin"])]
    res = md.largest_files("R", top=5, walk_fn=lambda r: iter(tree), getsize_fn=lambda p: 42, monotonic_fn=lambda: 0.0)
    assert len(res["entries"]) == 2
    assert res["entries"][0]["size_gb"] == res["entries"][1]["size_gb"]


def test_largest_files_unreadable_root_is_honest_empty_not_fabricated() -> None:
    def boom(root: str):
        raise OSError("permission denied")

    res = md.largest_files("R", top=5, walk_fn=boom, getsize_fn=lambda p: 1, monotonic_fn=lambda: 0.0)
    assert res["entries"] == []
    assert res["complete"] is False  # honest partial, never a guessed listing


def test_find_largest_executor_on_unreadable_says_it_wont_guess(monkeypatch) -> None:
    monkeypatch.setattr(md, "largest_files", lambda root, **kw: {"root": root, "entries": [], "error": "unreadable", "complete": False})
    with mock.patch("core.runtime_execution_tools._is_windows_platform", return_value=True):
        res = execute_runtime_tool("machine.find_largest", {"drive": "Z:\\", "kind": "files"}, source_context={})
    assert not res.ok
    assert "won't guess" in res.response_text


def test_disk_usage_unreadable_drive_is_drive_specific_and_honest(monkeypatch) -> None:
    # A scoped ask that reads nothing -> honest, drive-specific message, never a guessed figure.
    monkeypatch.setattr(md, "disk_usage", lambda drives=None: [])
    res = execute_runtime_tool("machine.disk_usage", {"drive": "Z:\\"}, source_context={})
    assert not res.ok
    assert "Z:\\" in res.response_text and "couldn't read" in res.response_text.lower()


def test_disk_usage_skips_one_unreadable_drive_but_reports_the_rest() -> None:
    def flaky(drive: str):
        if drive == "D:\\":
            raise PermissionError("access denied")

        class _U:
            total = 100 * 1024 ** 3
            free = 40 * 1024 ** 3
            used = 60 * 1024 ** 3
        return _U()

    rows = md.disk_usage(drives=["C:\\", "D:\\"], usage_fn=flaky)
    assert [r["mount"] for r in rows] == ["C:\\"]  # D: silently dropped, C: still reported
