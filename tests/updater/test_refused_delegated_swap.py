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

