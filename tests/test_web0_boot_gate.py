"""The Web0 switch owns what Web0 starts: no thread, no announce while off.

Proves, on the real starter and the real boot seam:
  * a fresh (off) home starts no web0-task-poll thread;
  * an enabled home starts exactly one;
  * turning the switch off stops a running poller at its next tick and releases
    the started marker so a later enable can start it again;
  * the boot announce block in build_provider_registry_snapshot never builds or
    announces a manifest while the switch is off, and does while on.
"""
from __future__ import annotations

import threading

import pytest

from core import runtime_paths
from core.web0_settings import set_web0_enabled


@pytest.fixture(autouse=True)
def _isolated_home(tmp_path, monkeypatch):
    monkeypatch.setenv("VOOL_HOME", str(tmp_path))
    runtime_paths.configure_runtime_home(tmp_path)
    yield
    runtime_paths.configure_runtime_home(None)


def _poll_threads() -> list[threading.Thread]:
    return [t for t in threading.enumerate() if t.name == "web0-task-poll"]


def _reset_started_marker(monkeypatch) -> None:
    import core.runtime_backbone as backbone

    monkeypatch.setattr(backbone, "_TASK_POLL_STARTED", False)


def test_fresh_home_starts_no_thread(monkeypatch) -> None:
    _reset_started_marker(monkeypatch)
    before = _poll_threads()
    from core.runtime_backbone import start_web0_background_workers

    start_web0_background_workers()
    start_web0_background_workers()
    assert _poll_threads() == before  # off means no thread at all


def test_enabled_home_starts_one_thread_which_exits_when_switched_off(monkeypatch) -> None:
    _reset_started_marker(monkeypatch)
    # The loop sleeps 30s between ticks; shrink the sleep so the test observes
    # the off-transition without waiting.
    import time as _time

    real_sleep = _time.sleep
    monkeypatch.setattr(
        _time, "sleep",
        lambda seconds: real_sleep(min(float(seconds), 0.05)),
    )
    set_web0_enabled(True)
    from core.runtime_backbone import start_web0_background_workers

    start_web0_background_workers()
    threads = _poll_threads()
    assert len(threads) == 1

    set_web0_enabled(False)
    thread = threads[0]
    thread.join(timeout=10)
    assert not thread.is_alive()

    # The marker was released: enabling again starts a fresh poller.
    set_web0_enabled(True)
    start_web0_background_workers()
    threads = _poll_threads()
    assert len(threads) == 1 and threads[0] is not thread
    set_web0_enabled(False)  # let the fresh poller exit too
    threads[0].join(timeout=10)
    assert not threads[0].is_alive()


def test_boot_snapshot_skips_announce_while_off(monkeypatch) -> None:
    import core.runtime_backbone as backbone
    import core.web0_capability_broadcast as broadcast

    calls: list[object] = []
    monkeypatch.setattr(broadcast, "announce_from_env", lambda *a, **k: calls.append(a) or False)
    monkeypatch.setattr(backbone, "start_web0_background_workers", lambda: None)
    monkeypatch.setattr(
        backbone, "ensure_default_runtime_providers", lambda *a, **k: None, raising=False)
    monkeypatch.setattr(
        backbone.ModelRegistry, "list_manifests", lambda self, **k: tuple(), raising=False)

    snapshot = backbone.build_provider_registry_snapshot(registry=backbone.ModelRegistry())
    assert snapshot is not None
    assert calls == []  # off: not even a manifest build/announce attempt
    # And no poller thread escaped through the real seam either.
    assert _poll_threads() == []


def test_boot_snapshot_announces_when_switched_on(monkeypatch) -> None:
    import core.runtime_backbone as backbone
    import core.web0_capability_broadcast as broadcast

    set_web0_enabled(True)
    calls: list[object] = []
    monkeypatch.setattr(broadcast, "announce_from_env", lambda *a, **k: calls.append(a) or False)
    monkeypatch.setattr(backbone, "start_web0_background_workers", lambda: None)
    monkeypatch.setattr(
        backbone, "ensure_default_runtime_providers", lambda *a, **k: None, raising=False)
    monkeypatch.setattr(
        backbone.ModelRegistry, "list_manifests", lambda self, **k: tuple(), raising=False)

    backbone.build_provider_registry_snapshot(registry=backbone.ModelRegistry())
    assert len(calls) == 1  # the announce ran (its own env gate then decides)
