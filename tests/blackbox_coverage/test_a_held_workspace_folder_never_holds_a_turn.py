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


# Mac gate on 91e898675 (2026-11-23, installed app): "zeig mir bitte meine erinnerungen" ran no reminder list and
# answered "the workspace folder could not be read in time": the read-only check scanned the chat's default
# workspace before VOOL's own reminder list. Which builtin read is asked is drawn at random.
BUILTIN_READS = ("operator.list_reminders", "operator.show_agenda", "contacts.search", "operator.list_calendar_events")


def _executed(intent: str, text: str):
    from core.tool_intent_executor import ToolIntentExecution

    return ToolIntentExecution(handled=True, ok=True, status="executed", response_text=text,
                               user_safe_response_text=text, mode="tool_executed", tool_name=intent,
                               details={"executed": True})


@pytest.mark.parametrize("seed", range(3))
def test_a_builtin_read_never_waits_on_a_held_folder(seed, workspace, store_dir, held_folder, monkeypatch):
    import random

    from core.blackbox.coverage import scan
    from core.tool_intent_executor import _with_blackbox_coverage
    from core.tool_registry import tool_for_intent

    monkeypatch.setattr(scan.ScanBounds, "__init__", _short_wait_init(scan.ScanBounds.__init__))
    rng = random.Random(f"builtin-read-{seed}")
    intent = rng.choice([i for i in BUILTIN_READS if (c := tool_for_intent(i)) is not None and c.read_only])
    said = f"listed {rng.getrandbits(32):08x}"

    started = time.monotonic()
    result = _with_blackbox_coverage(intent, {}, source_context=ctx(workspace, turn=f"builtin-{seed}"),
                                     dispatch=lambda: _executed(intent, said))

    assert time.monotonic() - started < 2.0
    assert result.ok and result.response_text == said, (result.status, result.response_text)


# Mac gate on e36daa6a9 (2026-11-24, installed app): a read-only tool from an installed package waited 30 s on a
# Desktop scan and was refused. Such a tool starts only inside the kernel sandbox with no writable folder (or not at
# all), so on a folder that cannot be pictured it runs and says the kernel held the claim. A connection server the
# person set up (`mcp:`) can write its own folder, so it still does not run there.
def test_a_package_read_runs_on_a_held_folder_with_the_kernel_holding_the_claim(workspace, store_dir, held_folder, monkeypatch):
    from core.blackbox.coverage import scan
    from core.tool_intent_executor import _with_blackbox_coverage
    from core.tool_registry import register, unregister
    from tests.blackbox_coverage.test_plugin_mcp_proof import _plugin_contract

    monkeypatch.setattr(scan.ScanBounds, "__init__", _short_wait_init(scan.ScanBounds.__init__))
    register(_plugin_contract(intent="demo.reader", mutation=None, side_effect_class="read_only"))
    ran = []
    started = time.monotonic()
    try:
        result = _with_blackbox_coverage("demo.reader", {}, source_context=ctx(workspace, turn="plugin-held"),
                                         dispatch=lambda: ran.append(True) or _executed("demo.reader", "read"))
    finally:
        unregister("demo.reader")

    assert time.monotonic() - started < 3.0
    assert result.ok and result.response_text == "read" and ran == [True]
    assert result.details["read_only_held_by"] == "kernel_sandbox_no_writable_folder"


def test_a_package_read_only_launch_has_no_writable_folder(tmp_path, monkeypatch):
    from core import mcp_client, plugin_executor

    seen = {}

    def capture(argv, **kwargs):
        seen.update(kwargs)
        raise plugin_executor.ConfinementUnavailableError("captured")

    monkeypatch.setattr(plugin_executor, "confined_argv", capture)
    client = mcp_client.MCPStdioClient(str(tmp_path / "server"), [], cwd=str(tmp_path), name="pkg", timeout=1,
                                       immutable_package=True, scratch_dir=str(tmp_path / "scratch"),
                                       read_only=True)
    with pytest.raises(mcp_client.MCPError):
        client.start()
    assert seen["writable_roots"] == () and seen["mode"] == "auto"


def test_a_connection_server_read_still_does_not_run_on_a_folder_it_cannot_read(workspace, store_dir, held_folder, monkeypatch):
    from core.blackbox.coverage import scan
    from core.tool_intent_executor import _with_blackbox_coverage
    from core.tool_registry import register, unregister
    from tests.blackbox_coverage.test_plugin_mcp_proof import _plugin_contract

    monkeypatch.setattr(scan.ScanBounds, "__init__", _short_wait_init(scan.ScanBounds.__init__))
    from dataclasses import replace as _replace

    register(_replace(_plugin_contract(intent="demo.reader", mutation=None, side_effect_class="read_only"),
                      source="mcp:demo"))
    ran = []
    try:
        result = _with_blackbox_coverage("demo.reader", {}, source_context=ctx(workspace, turn="plugin-held"),
                                         dispatch=lambda: ran.append(True) or _executed("demo.reader", "read"))
    finally:
        unregister("demo.reader")

    assert result.ok is False and result.status == "blackbox_workspace_not_read_in_time"
    assert ran == []
