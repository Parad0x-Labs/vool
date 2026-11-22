"""A workspace folder the OS holds never holds a turn (Mac gate on 991e5be60, 2026-11-22).

"list all the things we covered here" never ended: the model picked a read-only tool, and the
read-only check's before-scan of the chat's default workspace (the owner's Desktop) sat in
`os.scandir` for good, so the turn and the next one behind it never answered. Here the folder read
itself is made to hang; nothing depends on what was asked.
"""
from __future__ import annotations

import os
import threading
import time

import pytest

from tests.blackbox_coverage._ctx import ctx


@pytest.fixture
def held_folder(monkeypatch):
    from core.blackbox.coverage import scan

    gate = threading.Event()
    real = os.scandir

    def scandir(path):
        gate.wait(30)
        return real(path)

    monkeypatch.setattr(scan.os, "scandir", scandir)
    yield gate
    gate.set()


def test_a_scan_of_a_held_folder_comes_back_unfinished_in_time(workspace, store_dir, held_folder):
    from core.blackbox.coverage.scan import ScanBounds, diff_scans, scan_workspace
    from core.blackbox.store import default_store

    started = time.monotonic()
    result = scan_workspace(workspace, store=default_store(), bounds=ScanBounds(max_wait_s=0.3))
    assert time.monotonic() - started < 3.0
    assert result.finished is False and result.degraded and result.files == {}
    # While that read is still held, the next scan of the same folder does not queue behind it.
    started = time.monotonic()
    again = scan_workspace(workspace, store=default_store(), bounds=ScanBounds(max_wait_s=5.0))
    assert time.monotonic() - started < 0.5 and again.finished is False
    assert diff_scans(result, again) == []  # nothing observed, so nothing is called drift


def test_a_scan_that_finishes_is_unchanged(workspace, store_dir):
    from core.blackbox.coverage.scan import scan_workspace
    from core.blackbox.store import default_store

    (workspace / "notes.txt").write_text("hello", encoding="utf-8")
    result = scan_workspace(workspace, store=default_store())
    assert result.finished and "notes.txt" in result.files


def test_an_unknown_scope_effect_does_not_run_on_a_folder_it_cannot_read(workspace, store_dir, held_folder, monkeypatch):
    from core.blackbox.coverage import recorder, scan
    from core.runtime_execution_tools import RuntimeExecutionResult

    monkeypatch.setattr(scan, "DEFAULT_MAX_WAIT_S", 0.3)
    monkeypatch.setattr(scan.ScanBounds, "__init__", _short_wait_init(scan.ScanBounds.__init__))
    ran = []

    def handler():
        ran.append(True)
        return RuntimeExecutionResult(handled=True, ok=True, status="executed", response_text="", details={})

    started = time.monotonic()
    result = recorder.recorded_capability_mutation(
        "sandbox.run_command",
        {"command": "touch x.txt", "cwd": str(workspace)},
        handler=handler,
        source_context=ctx(workspace, turn="held-folder-turn"),
        workspace_root=workspace,
    )
    assert time.monotonic() - started < 5.0
    assert result.ok is False and result.status == "blackbox_workspace_not_read_in_time"
    assert result.details["executed"] is False
    assert ran == []


def _short_wait_init(real_init):
    def init(self, *args, **kwargs):
        kwargs.setdefault("max_wait_s", 0.3)
        real_init(self, *args, **kwargs)

    return init
