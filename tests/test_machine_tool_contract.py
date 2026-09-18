"""machine.* results carry a typed result_type and echo the requested drive, so a consumer can
detect a wrong-drive / wrong-kind result (a C: answer cannot structurally satisfy a D: request,
and a folder cannot satisfy a file request)."""
from __future__ import annotations

from unittest import mock

import core.machine_diagnostics as md
from core.runtime_execution_tools import execute_runtime_tool


def test_disk_usage_result_is_typed_and_echoes_requested_drive(monkeypatch) -> None:
    monkeypatch.setattr(
        md, "disk_usage",
        lambda drives=None: [{"mount": (drives[0] if drives else "C:\\"), "free_gb": 10.0,
                              "total_gb": 100.0, "used_gb": 90.0, "percent_used": 90.0}],
    )
    res = execute_runtime_tool("machine.disk_usage", {"drive": "D:\\"}, source_context={})
    d = res.details
    assert d["result_type"] == "drive_space"
    assert d["requested_drive"] == "D:\\"
    assert d["reported_drive"] == "D:\\"


def test_find_largest_files_result_is_typed_with_scan_provenance(monkeypatch) -> None:
    monkeypatch.setattr(
        md, "largest_files",
        lambda root, **kw: {"root": root, "complete": True,
                            "entries": [{"name": "big.bin", "size_gb": 9.0, "kind": "file",
                                         "complete": True, "delete_safety": "review"}]},
    )
    with mock.patch("core.runtime_execution_tools._is_windows_platform", return_value=True):
        res = execute_runtime_tool("machine.find_largest", {"drive": "D:\\", "kind": "files"}, source_context={})
    d = res.details
    assert d["result_type"] == "largest_file"
    assert d["requested_drive"] == "D:\\"
    assert d["searched_roots"] == ["D:\\"]
    assert isinstance(d["elapsed_ms"], int)
    # A file request returns only files — no folder can satisfy it.
    assert all(e["kind"] == "file" for e in d["entries"])


def test_find_largest_folders_result_type(monkeypatch) -> None:
    monkeypatch.setattr(
        md, "largest_entries",
        lambda root, **kw: {"root": root, "complete": True,
                            "entries": [{"name": "Projects", "size_gb": 3.0, "kind": "folder",
                                         "complete": True, "delete_safety": "review"}]},
    )
    with mock.patch("core.runtime_execution_tools._is_windows_platform", return_value=True):
        res = execute_runtime_tool("machine.find_largest", {"drive": "C:\\", "kind": "folders"}, source_context={})
    assert res.details["result_type"] == "largest_folders"
    assert res.details["requested_drive"] == "C:\\"
