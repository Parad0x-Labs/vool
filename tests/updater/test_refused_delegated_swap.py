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


def _fake_app(root, body: str):
    launcher = root / "Contents/Resources/launcher.sh"
    launcher.parent.mkdir(parents=True)
    launcher.write_text("#!/bin/bash\n" + body + "\n")
    return launcher


def _running(pid: int) -> bool:
    from core.updater import macos

    return not macos._pid_has_exited(pid)


def test_a_rollback_stops_the_new_version_the_script_started_before_it_wrote_its_pidfile(tmp_path):
    """Org-credit review N1: the script relaunches the new version and exits; a version still starting
    (no pidfile yet) was left running behind the restore and later took over the old version's seat."""
    import subprocess
    import time
    from pathlib import Path

    from core.updater import macos

    app, staged = tmp_path / "VOOL.app", tmp_path / "staged.app"
    _fake_app(app, "exit 0")
    _fake_app(staged, "while :; do sleep 0.2; done  # a new version still starting, no pidfile")
    old_app = subprocess.Popen(["sleep", "0.1"])
    old_app.wait()
    script = Path(macos.__file__).resolve().parents[2] / "installer/update/mac_update_helper.sh"
    launcher = app.resolve() / "Contents/Resources/launcher.sh"
    helper = macos.ExternalScriptHelper(script=script, app_path=app, target_pid=lambda: old_app.pid,
                                        result_path=tmp_path / "result.json", relaunch_cmd=f"bash {launcher}")
    helper.prepare(txid="t1", staged_bundle=staged)
    helper.shutdown_app()
    ok, _ = helper.wait_for_result(timeout=15.0)
    assert ok
    helper.process.wait(timeout=10)
    deadline, started = time.monotonic() + 10, []
    while not started and time.monotonic() < deadline:
        started = helper._script_group_app_pids()
        time.sleep(0.1)
    assert started, "the new version the script started was not found"
    try:
        assert helper.stop_swap_script(timeout=5.0)
        assert helper.stop_running_app(timeout=5.0)
        assert not any(_running(pid) for pid in started)
    finally:
        for pid in started:
            if _running(pid):
                macos.os.kill(pid, macos.signal.SIGKILL)


def test_an_app_that_ignores_the_request_to_quit_is_ended_so_the_rollback_can_finish(tmp_path):
    """Org-credit review N2: an app that would not close ended in "rollback failed" with the old version
    left on migrated data. Only a process proven to be this app is ended."""
    import subprocess
    import time

    from core.updater import macos

    app = tmp_path / "VOOL.app"
    launcher = _fake_app(app, "trap '' TERM\nwhile :; do sleep 0.2; done")
    stubborn = subprocess.Popen(["bash", str(launcher.resolve())], start_new_session=True)
    stranger = subprocess.Popen(["bash", "-c", "trap '' TERM; while :; do sleep 0.2; done"], start_new_session=True)
    time.sleep(0.5)
    try:
        helper = macos.ExternalScriptHelper(script=launcher, app_path=app, target_pid=lambda: stubborn.pid,
                                            result_path=tmp_path / "result.json")
        assert helper.stop_running_app(timeout=1.0)
        assert stubborn.wait(timeout=5) == -9
        other = macos.ExternalScriptHelper(script=launcher, app_path=app, target_pid=lambda: stranger.pid,
                                           result_path=tmp_path / "result.json")
        assert not other.stop_running_app(timeout=1.0)  # not this app: asked to quit, never ended
        assert stranger.poll() is None
    finally:
        for process in (stubborn, stranger):
            if process.poll() is None:
                process.kill()
