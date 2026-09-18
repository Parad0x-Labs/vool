"""Plugin storage is a STATE the app reports, never a hang it disappears into.

Measured on the packaged 352ce68b app on 2026-09-10 (validation-logs/owner-execution-repair-20260910/
NATIVE_SWITCH_20260910.md): the API child listed `~/Desktop/Vool-skills-plugins/plugins` on its main
thread before binding its port, the directory open hung in the kernel, the native host never saw
`/healthz`, and after 90 s it tore the child down -- no window, no explanation. The same folder
answered in 0.1 s an hour later. Whether macOS consent gated that open is NOT established here;
what is established is that the folder held up the boot, and that the boot must not let it.

Every case below drives the real functions against a real temporary plugin root and a real probe
subprocess. Nothing is mocked except, for the STALLED cases, the probe command, which is swapped for
an interpreter that sleeps -- the parent's timeout, kill and bookkeeping are the real ones.
"""

from __future__ import annotations

import os
import stat
import sys
import threading
import time
from pathlib import Path

import pytest

from core import plugin_catalog as pc
from tests._toolchain_fixtures import PLUGIN_ID, make_plugin, reset_toolchain_state


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("VOOL_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("VOOL_PLUGIN_LIFECYCLE_PATH", str(tmp_path / "lifecycle.json"))
    empty = tmp_path / "no-native-skills"
    empty.mkdir()
    monkeypatch.setenv("VOOL_NATIVE_SKILLS_DIR", str(empty))
    reset_toolchain_state()
    yield tmp_path
    reset_toolchain_state()


def _root(home: Path, monkeypatch, name: str = "repo") -> Path:
    root = home / name
    (root / "plugins").mkdir(parents=True)
    monkeypatch.setenv("VOOL_PLUGINS_DIR", str(root))
    return root


def _registered_plugin_sources() -> set[str]:
    from core import tool_registry

    return {str(getattr(c, "source", "")) for c in tool_registry.registry_map().values()}


class _Stall:
    """Swap the probe for an interpreter that sleeps; count every probe the parent starts."""

    def __init__(self, monkeypatch) -> None:
        self.on = True
        self.calls = 0
        self.started = threading.Event()
        real = pc._plugin_probe_command

        def command(plugins_dir: Path) -> list[str]:
            self.calls += 1
            self.started.set()
            if self.on:
                return [sys.executable, "-I", "-S", "-c", "import time; time.sleep(60)", str(plugins_dir)]
            return real(plugins_dir)

        monkeypatch.setattr(pc, "_plugin_probe_command", command)


# --- accessible ------------------------------------------------------------------------------


def test_an_accessible_folder_loads_its_packs_at_boot_within_budget(home, monkeypatch) -> None:
    root = _root(home, monkeypatch)
    make_plugin(root, admit=False)

    state = pc.discover_and_register(budget_s=pc.BOOT_PROBE_BUDGET_S, reason="boot")

    assert state["state"] == pc.STORAGE_ACCESSIBLE, state
    assert state["entries"] == [PLUGIN_ID]
    assert state["loaded"] == [PLUGIN_ID], state
    assert state["errors"] == []
    assert state["elapsed_ms"] is not None and state["elapsed_ms"] < pc.BOOT_PROBE_BUDGET_S * 1000
    assert state["in_flight"] is False and state["probe_lingering"] is False
    assert f"plugin:{PLUGIN_ID}" in _registered_plugin_sources(), "the boot registers what it lists"

    catalog = pc.read_plugin_catalog()
    assert catalog["installed"] is True and catalog["reason"] == ""
    assert [p["id"] for p in catalog["plugins"]] == [PLUGIN_ID]
    assert catalog["storage"]["state"] == pc.STORAGE_ACCESSIBLE


def test_listing_never_registers_tools_by_itself(home, monkeypatch) -> None:
    """A tool offer or a census LISTS the folder; only the boot, a rescan or the flag-gated
    loader may put a pack's contracts in front of the model."""
    root = _root(home, monkeypatch)
    make_plugin(root, admit=False)

    dirs = pc.discovered_plugin_dirs()

    assert [d.name for d in dirs] == [PLUGIN_ID]
    assert f"plugin:{PLUGIN_ID}" not in _registered_plugin_sources()
    assert pc.storage_state()["loaded"] == []


def test_a_pack_installed_while_running_is_visible_on_the_next_read(home, monkeypatch) -> None:
    root = _root(home, monkeypatch)
    assert pc.discover_and_register(budget_s=1.0, reason="boot")["entries"] == []

    make_plugin(root, admit=False)
    pc.invalidate_storage_listing()  # what the skill installer does after creating a pack
    catalog = pc.read_plugin_catalog()

    assert [p["id"] for p in catalog["plugins"]] == [PLUGIN_ID]
    # This process loads packs (the boot said so): the new pack is loaded, not merely listed.
    assert pc.storage_state()["loaded"] == [PLUGIN_ID]
    assert f"plugin:{PLUGIN_ID}" in _registered_plugin_sources()


# --- missing ---------------------------------------------------------------------------------


def test_a_missing_folder_is_a_reported_state_not_an_error(home, monkeypatch) -> None:
    monkeypatch.setenv("VOOL_PLUGINS_DIR", str(home / "nowhere"))

    state = pc.discover_and_register(budget_s=pc.BOOT_PROBE_BUDGET_S, reason="boot")
    catalog = pc.read_plugin_catalog()

    assert state["state"] == pc.STORAGE_MISSING
    assert catalog["installed"] is False and catalog["plugin_count"] == 0
    assert "VOOL_PLUGINS_DIR" in catalog["reason"]
    assert catalog["storage"]["state"] == pc.STORAGE_MISSING
    assert pc.discovered_plugin_dirs() == ()


# --- denied ----------------------------------------------------------------------------------


@pytest.mark.skipif(os.geteuid() == 0, reason="root ignores directory modes")
def test_a_denied_folder_serves_without_plugins_and_recovers_when_access_is_granted(
    home, monkeypatch
) -> None:
    root = _root(home, monkeypatch)
    make_plugin(root, admit=False)
    plugins_dir = root / "plugins"
    mode = stat.S_IMODE(plugins_dir.stat().st_mode)
    plugins_dir.chmod(0)
    try:
        state = pc.discover_and_register(budget_s=pc.BOOT_PROBE_BUDGET_S, reason="boot")
        assert state["state"] == pc.STORAGE_DENIED, state
        assert state["loaded"] == [] and state["entries"] == []
        assert "denied" in state["detail"].lower() or "permission" in state["detail"].lower()
        assert f"plugin:{PLUGIN_ID}" not in _registered_plugin_sources()

        catalog = pc.read_plugin_catalog()
        assert catalog["installed"] is False
        assert "denied" in catalog["reason"].lower() and "Rescan" in catalog["reason"]
        assert "disabled" in catalog["reason"], "the reason says nothing was disabled or changed"
        assert catalog["storage"]["state"] == pc.STORAGE_DENIED
        assert pc.discovered_plugin_dirs() == ()
        # Nothing was disabled to get here: the enable store is untouched.
        assert not (home / "home" / "config" / "plugins_enabled.json").exists()
    finally:
        plugins_dir.chmod(mode)

    # Access granted: the rescan is the recovery, and it loads what the boot would have.
    recovered = pc.discover_and_register(budget_s=pc.RESCAN_PROBE_BUDGET_S, reason="rescan")
    assert recovered["state"] == pc.STORAGE_ACCESSIBLE, recovered
    assert recovered["loaded"] == [PLUGIN_ID]
    assert recovered["attempts"] == 2
    assert f"plugin:{PLUGIN_ID}" in _registered_plugin_sources()
    assert pc.read_plugin_catalog()["installed"] is True


# --- stalled ---------------------------------------------------------------------------------


def test_a_stalled_folder_is_killed_within_budget_and_the_app_keeps_serving(home, monkeypatch) -> None:
    root = _root(home, monkeypatch)
    make_plugin(root, admit=False)
    stall = _Stall(monkeypatch)
    threads_before = threading.active_count()

    started = time.monotonic()
    state = pc.discover_and_register(budget_s=0.5, reason="boot")
    elapsed = time.monotonic() - started

    assert state["state"] == pc.STORAGE_STALLED, state
    assert elapsed < 0.5 + pc._KILL_WAIT_S + 1.0, f"the boot waited {elapsed:.1f}s on a stalled folder"
    assert state["probe_lingering"] is False
    assert state["loaded"] == [] and state["entries"] == []
    pid = state["probe_pid"]
    assert isinstance(pid, int) and pid > 0
    with pytest.raises(ProcessLookupError):
        os.kill(pid, 0)  # the stalled probe is gone, not parked
    assert threading.active_count() == threads_before, "no thread of ours is left waiting on the folder"
    assert "did not answer within 0.5 s" in state["detail"]
    assert f"plugin:{PLUGIN_ID}" not in _registered_plugin_sources()

    catalog = pc.read_plugin_catalog()
    assert catalog["installed"] is False
    assert "did not answer" in catalog["reason"] and "Rescan" in catalog["reason"]
    assert catalog["storage"]["state"] == pc.STORAGE_STALLED


def test_while_stalled_reads_take_the_recorded_state_and_open_nothing(home, monkeypatch) -> None:
    root = _root(home, monkeypatch)
    make_plugin(root, admit=False)
    stall = _Stall(monkeypatch)
    pc.discover_and_register(budget_s=0.3, reason="boot")
    probes_after_boot = stall.calls
    assert probes_after_boot == 1

    from core import tool_offer_assembly
    from core.web.api.service import _plugin_storage_health_summary

    started = time.monotonic()
    catalog = pc.read_plugin_catalog()
    dirs = tool_offer_assembly._plugin_dirs()
    health = _plugin_storage_health_summary()
    elapsed = time.monotonic() - started

    assert stall.calls == probes_after_boot, "a fresh stalled result is not re-probed on every read"
    assert elapsed < 0.2, f"reads must not wait on the folder ({elapsed:.2f}s)"
    assert catalog["installed"] is False and catalog["storage"]["state"] == pc.STORAGE_STALLED
    assert dirs == ()
    assert health["state"] == pc.STORAGE_STALLED and health["attempts"] == 1


def test_a_stalled_folder_that_answers_later_recovers_on_rescan(home, monkeypatch) -> None:
    root = _root(home, monkeypatch)
    make_plugin(root, admit=False)
    stall = _Stall(monkeypatch)
    assert pc.discover_and_register(budget_s=0.3, reason="boot")["state"] == pc.STORAGE_STALLED

    stall.on = False  # the folder answers now (a dialog answered, a volume back)
    recovered = pc.discover_and_register(budget_s=pc.RESCAN_PROBE_BUDGET_S, reason="rescan")

    assert recovered["state"] == pc.STORAGE_ACCESSIBLE, recovered
    assert recovered["loaded"] == [PLUGIN_ID]
    assert f"plugin:{PLUGIN_ID}" in _registered_plugin_sources()
    from core import tool_offer_assembly

    assert [d.name for _, d in tool_offer_assembly._plugin_dirs()] == [PLUGIN_ID]


def test_a_stalled_folder_that_answers_later_recovers_on_the_next_aged_read(home, monkeypatch) -> None:
    """Without anyone pressing Rescan: a request-path read after the retry interval re-probes,
    and because this process loads packs (the boot said so) the packs load then."""
    root = _root(home, monkeypatch)
    make_plugin(root, admit=False)
    stall = _Stall(monkeypatch)
    assert pc.discover_and_register(budget_s=0.3, reason="boot")["state"] == pc.STORAGE_STALLED

    stall.on = False
    state = pc.ensure_discovered(max_age_s=0.0)

    assert state["state"] == pc.STORAGE_ACCESSIBLE
    assert state["loaded"] == [PLUGIN_ID]
    assert pc.read_plugin_catalog()["installed"] is True


def test_probes_never_stack_on_a_stalled_folder(home, monkeypatch) -> None:
    root = _root(home, monkeypatch)
    make_plugin(root, admit=False)
    stall = _Stall(monkeypatch)
    results: dict[str, object] = {}

    def boot() -> None:
        results["boot"] = pc.discover_and_register(budget_s=1.0, reason="boot")

    worker = threading.Thread(target=boot, name="boot-probe")
    worker.start()
    try:
        assert stall.started.wait(5.0)
        started = time.monotonic()
        during = pc.ensure_discovered()
        rescan = pc.discover_and_register(budget_s=5.0, reason="rescan")
        elapsed = time.monotonic() - started
    finally:
        worker.join(15.0)
    assert not worker.is_alive()

    assert elapsed < 0.5, "calls during a probe return at once; they do not queue behind it"
    assert during["in_flight"] is True and rescan["in_flight"] is True
    assert stall.calls == 1, "one probe in flight means no second probe is started"
    assert results["boot"]["state"] == pc.STORAGE_STALLED  # type: ignore[index]
    assert pc.storage_state()["in_flight"] is False


# --- the boot hook itself ----------------------------------------------------------------------


def test_the_server_boot_hook_returns_and_logs_the_state_instead_of_waiting(home, monkeypatch, caplog) -> None:
    import logging

    root = _root(home, monkeypatch)
    make_plugin(root, admit=False)
    _Stall(monkeypatch)
    from apps import vool_api_server

    started = time.monotonic()
    with caplog.at_level(logging.INFO, logger=vool_api_server.logger.name):
        with monkeypatch.context() as m:
            m.setattr(pc, "BOOT_PROBE_BUDGET_S", 0.3)
            vool_api_server._load_installed_plugins()
    elapsed = time.monotonic() - started

    assert elapsed < 0.3 + pc._KILL_WAIT_S + 1.0
    assert pc.storage_state()["state"] == pc.STORAGE_STALLED
    assert any("Plugin storage stalled" in r.getMessage() for r in caplog.records), [
        r.getMessage() for r in caplog.records
    ]
