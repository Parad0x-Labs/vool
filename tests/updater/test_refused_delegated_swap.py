"""A swap the Mac helper script refuses leaves the old app running and the update cleanly ended.

Second-opinion review of the updater (2026-10-30, F2): the flow wrote SWAPPED before it read the
script's result. When the script refused (a locked app, a permission error, "prior already
exists") nothing had moved, but the rollback treated the missing backup as lost: the app was left
stopped and every later start was stopped again by recovery. This code is also in 0.7.5 and 0.7.6.
"""
from __future__ import annotations

from core.updater.status import StatusStore, UpdatePhase
from core.updater.transaction import InProcessHelper, Step, UpdateJournal, recover_interrupted_update

from .test_transaction_flow import harness


class RefusingScript(InProcessHelper):
    """The delegated (Mac script) helper whose swap is refused before anything moves."""

    performs_swap = True

    def __init__(self, harness, detail: str):
        super().__init__(
            shutdown=lambda: harness.shutdown_log.append("script started"),
            relauncher=lambda path: (harness.relaunch_log.append(str(path)), True)[1],
        )
        self.detail = detail
        self.stops = 0

    def prepare(self, *, txid, staged_bundle) -> None:
        pass

    def wait_for_result(self, *, timeout: float):
        return False, self.detail

    def stop_running_app(self, *, timeout: float = 10.0) -> bool:
        self.stops += 1
        return True


def test_a_refused_swap_ends_the_update_with_the_old_app_running(harness):
    helper = RefusingScript(harness, "could not move the current app aside")

    result = harness.make_flow(helper=helper).run(harness.raw_manifest, harness.manifest, harness.decision)

    assert not result.ok
    assert result.terminal is Step.ROLLED_BACK  # data put back, old app started again
    assert harness.marker() == "old"
    assert harness.relaunch_log == [str(harness.app_path)]  # the old app was started again
    journal = UpdateJournal(harness.data_dir / "update_v2" / "transactions" / result.txid)
    assert not journal.has(Step.SWAPPED)
    status = StatusStore(harness.data_dir / "update_v2" / "status.json").load()
    assert status.phase is UpdatePhase.ROLLED_BACK

    later = RefusingScript(harness, "unused")
    recovered = recover_interrupted_update(
        data_dir=harness.data_dir, app_path=harness.app_path, helper=later,
        health_probe=harness.health, installed_version=harness.installed_version, platform="macos-arm64",
    )
    assert recovered == []  # nothing left to recover: the next start is not stopped again
    assert later.stops == 0


def test_the_swap_script_waits_for_a_slow_app_but_gives_up_before_the_flow(tmp_path, monkeypatch):
    """F1: the script used to give up after 60 s while the flow waited 90 s for the app to close.
    It now waits longer than before for a slow app, and still gives up just before the flow, so a
    slow close ends as a clean "did not exit in time" instead of a swap racing the rollback."""
    from core.updater import macos, relaunch_health

    started = []

    class Spawned:
        pid = 0

        def poll(self):
            return 0

    monkeypatch.setattr(macos.subprocess, "Popen", lambda command, **kwargs: (started.append(command), Spawned())[1])
    script = tmp_path / "mac_update_helper.sh"
    script.write_text("#!/bin/bash\n")
    helper = macos.ExternalScriptHelper(script=script, app_path=tmp_path / "VOOL.app", target_pid=0,
                                        result_path=tmp_path / "result.json")
    helper.prepare(txid="t1", staged_bundle=tmp_path / "staged")
    helper.shutdown_app()

    command = started[0]
    wait = float(command[command.index("--wait-seconds") + 1])
    assert 60 < wait < relaunch_health.STOP_BOUND_SECONDS


def test_the_mac_helper_stops_a_version_it_relaunched_that_never_took_the_pidfile(tmp_path):
    """F3: a rollback's stop on the Mac helper read only the pidfile, so a relaunched version still
    starting (no pidfile yet) kept running behind the restore."""
    from core.updater import macos

    helper = macos.ExternalScriptHelper(script=tmp_path / "unused.sh", app_path=tmp_path / "VOOL.app",
                                        target_pid=0, result_path=tmp_path / "result.json",
                                        relaunch_cmd="sleep 60")
    assert helper.relaunch(tmp_path / "VOOL.app")
    launched = helper._launch_process
    try:
        assert helper.stop_running_app(timeout=5.0)
        assert launched.poll() is not None
    finally:
        if launched.poll() is None:
            launched.kill()


class LateSwappingScript(RefusingScript):
    """The script is still waiting for the slow app when the flow gives up: once the rollback stops
    the app, the script would see it gone and swap the new bundle in under the rollback."""

    def __init__(self, harness):
        super().__init__(harness, "unused")
        self.app_path = harness.app_path
        self.stop_waiter = lambda timeout: False  # the app did not close inside the flow's wait
        self.script_stopped = False
        self._swap = None

    def prepare(self, *, txid, staged_bundle) -> None:
        app = self.app_path
        self._swap = (app, app.parent / f".{app.name}.prior-{txid}", staged_bundle)

    def stop_swap_script(self, *, timeout: float) -> bool:
        self.script_stopped = True
        return True

    def stop_running_app(self, *, timeout: float = 10.0) -> bool:
        if not self.script_stopped and self._swap is not None:
            app, prior, staged = self._swap
            app.rename(prior)
            staged.rename(app)
        return True


def test_a_rollback_stops_the_swap_script_before_it_can_swap_under_it(harness):
    helper = LateSwappingScript(harness)

    result = harness.make_flow(helper=helper).run(harness.raw_manifest, harness.manifest, harness.decision)

    assert helper.script_stopped
    assert result.terminal is Step.ROLLED_BACK
    assert harness.marker() == "old"


def test_stopping_the_real_swap_script_while_it_waits_means_it_never_swaps(tmp_path):
    import subprocess
    import time
    from pathlib import Path

    from core.updater import macos

    app, staged = tmp_path / "VOOL.app", tmp_path / "staged.app"
    app.mkdir()
    (app / "marker.txt").write_text("old")
    staged.mkdir()
    (staged / "marker.txt").write_text("new")
    slow_app = subprocess.Popen(["sleep", "3"])
    script = Path(macos.__file__).resolve().parents[2] / "installer/update/mac_update_helper.sh"
    helper = macos.ExternalScriptHelper(script=script, app_path=app, target_pid=slow_app.pid,
                                        result_path=tmp_path / "result.json")
    helper.prepare(txid="t1", staged_bundle=staged)
    real_kill = macos.os.kill
    macos.os.kill = lambda pid, sig: None  # leave the slow app to close on its own
    try:
        helper.shutdown_app()
    finally:
        macos.os.kill = real_kill
    try:
        assert helper.stop_swap_script(timeout=5.0)
        slow_app.wait(timeout=10)
        time.sleep(1.0)  # the script would have seen the app gone and swapped by now
        assert (app / "marker.txt").read_text() == "old"
        assert staged.exists()
    finally:
        if slow_app.poll() is None:
            slow_app.kill()
