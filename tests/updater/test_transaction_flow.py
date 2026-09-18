"""The install-transaction state machine: every transition, failure, crash, recovery.

The harness swaps REAL directories via the real MacOSBundleInstaller rename logic (the
codesign subprocess is faked to rc=0), so an assertion on "the previous version is
runnable" means the actual on-disk app bundle, not a mock's call log.

Mission scenarios covered:
  full happy path · destructive-work refusal + explicit resolution · tampered artifact ·
  bundle verification failure · failed migration → rollback · failed health → rollback ·
  failed relaunch → rollback · failed swap → prior stays runnable · crash pre-swap →
  clean abort · crash post-swap + healthy → finalize · crash post-swap + unhealthy →
  auto rollback + relaunch · rollback idempotence · press-time re-verification ·
  press-time replay refusal · user data stays outside the bundle.
"""
from __future__ import annotations

import json
import zipfile
from dataclasses import dataclass, field
from pathlib import Path

import pytest

from core.updater.decision import UpdateDecisionReason, decide_update
from core.updater.download import StagedDownloader
from core.updater.health import HealthResult
from core.updater.macos import MacOSBundleInstaller
from core.updater.migrations import MigrationCatalog, MigrationStep
from core.updater.status import StatusStore, UpdatePhase
from core.updater.transaction import (
    InProcessHelper,
    SimulatedCrashError,
    Step,
    UpdateFlow,
    UpdateJournal,
    recover_interrupted_update,
)
from core.updater.trust import TrustedPublishers
from core.updater.work import DestructiveWorkResolution, WorkCoordinator, WorkHandle

from .helpers import build_manifest, generate_publisher_keypair, manifest_bytes


class Completed:
    def __init__(self, rc: int, out: str = "", err: str = ""):
        self.returncode = rc
        self.stdout = out
        self.stderr = err


class FakeServer:
    def __init__(self, blob: bytes):
        self.blob = blob
        self.range_requests: list[str] = []

    def open(self, url, headers=None, timeout=30.0):
        headers = headers or {}
        rng = headers.get("Range")
        if rng:
            self.range_requests.append(rng)
            start = int(rng.split("=", 1)[1].split("-", 1)[0])
            body = self.blob[start:]
        else:
            body = self.blob

        class _Resp:
            status = 206 if rng else 200
            headers = {"ETag": '"e1"'}

            def __init__(self, payload):
                self._body = payload
                self._offset = 0

            def read(self, n=-1):
                chunk = self._body[self._offset : self._offset + n if n and n > 0 else None]
                self._offset += len(chunk)
                return chunk

            def close(self):
                pass

        return _Resp(body)


@dataclass
class Harness:
    tmp: Path
    data_dir: Path
    app_path: Path
    raw_manifest: bytes
    manifest: object
    installer: MacOSBundleInstaller
    server: FakeServer
    coordinator: WorkCoordinator
    catalog: MigrationCatalog
    relaunch_log: list = field(default_factory=list)
    shutdown_log: list = field(default_factory=list)
    health_forced_fail: bool = False
    installed_version: str = "0.5.0"
    target_version: str = "0.6.0"
    status_log: list = field(default_factory=list)

    def marker(self) -> str:
        return (self.app_path / "Contents" / "marker.txt").read_text(encoding="utf-8").strip()

    def config(self) -> dict:
        return json.loads((self.data_dir / "config" / "settings.json").read_text(encoding="utf-8"))

    def health(self, *, timeout: float, expected_version: str | None = None) -> HealthResult:
        if self.health_forced_fail:
            return HealthResult(False, "forced failure for the test")
        marker = self.marker()
        reported = self.target_version if marker == "new" else self.installed_version
        ok = (expected_version is None) or reported == expected_version
        return HealthResult(ok, f"marker={marker}", reported)

    def make_flow(self, **overrides) -> UpdateFlow:
        _private_hex, public_hex = self._keys
        trust = TrustedPublishers(pinned_keys={"release-2026-01": public_hex})
        recording_store = RecordingStatusStore(self.data_dir / "update_v2" / "status.json", self.status_log)
        params = dict(
            data_dir=self.data_dir,
            app_path=self.app_path,
            platform="macos-arm64",
            installed_version=self.installed_version,
            channel="stable",
            trust=trust,
            downloader=StagedDownloader(fetch=self.server.open, chunk_size=64),
            coordinator=self.coordinator,
            migration_catalog=self.catalog,
            helper=InProcessHelper(
                shutdown=lambda: self.shutdown_log.append("shutdown"),
                relauncher=lambda path: (self.relaunch_log.append(str(path)), True)[1],
            ),
            health_probe=self.health,
            installer=self.installer,
            status_store=recording_store,
            notarization_waiver_reason="unit-test sandbox (codesign/spctl faked)",
        )
        params.update(overrides)
        return UpdateFlow(**params)

    _keys: tuple = ()


class RecordingStatusStore(StatusStore):
    def __init__(self, path, log: list):
        super().__init__(path)
        self._log = log

    def publish(self, phase: UpdatePhase, **context) -> object:
        self._log.append(phase)
        return super().publish(phase, **context)


def _make_bundle(root: Path, name: str, marker: str) -> Path:
    bundle = root / name
    (bundle / "Contents").mkdir(parents=True, exist_ok=True)
    (bundle / "Contents" / "marker.txt").write_text(marker, encoding="utf-8")
    return bundle


@pytest.fixture()
def harness(tmp_path: Path):
    private_hex, public_hex = generate_publisher_keypair()
    data_dir = tmp_path / "data"
    (data_dir / "config").mkdir(parents=True)
    (data_dir / "config" / "settings.json").write_text(json.dumps({"setting": "original"}), encoding="utf-8")

    apps = tmp_path / "Apps"
    app_path = _make_bundle(apps, "VOOL.app", "old")

    new_bundle = _make_bundle(tmp_path, "new-bundle", "new")
    artifact_path = tmp_path / "artifact.zip"
    with zipfile.ZipFile(artifact_path, "w") as archive:
        # production shape: the artifact IS the app bundle, top-level dir included
        archive.write(new_bundle / "Contents" / "marker.txt", "VOOL.app/Contents/marker.txt")
    artifact_bytes = artifact_path.read_bytes()

    from .helpers import sign_bytes

    manifest, _ = build_manifest(
        version="0.6.0",
        artifacts={"macos-arm64": artifact_bytes},
        private_hex=private_hex,
    )
    # sign the artifact bytes with the publisher key
    manifest["artifacts"]["macos-arm64"]["signature"] = sign_bytes(private_hex, artifact_bytes)
    manifest["signature"] = {
        "key_id": "release-2026-01",
        "sig": sign_bytes(private_hex, json.dumps({k: v for k, v in manifest.items() if k != "signature"}, sort_keys=True, separators=(",", ":")).encode()),
    }
    raw = manifest_bytes(manifest)

    from datetime import datetime, timezone

    from core.updater.manifest import parse_and_verify_manifest

    trust = TrustedPublishers(pinned_keys={"release-2026-01": public_hex})
    verification = parse_and_verify_manifest(raw, trust, now=datetime(2026, 9, 1, 13, 0, 0, tzinfo=timezone.utc))
    assert verification.ok, verification.reason

    def fake_run(cmd):
        if cmd[0] == "codesign":
            return Completed(0, "", "Authority=ad-hoc")
        if cmd[0] == "spctl":
            return Completed(0, "accepted", "")
        return Completed(0, "", "")

    installer = MacOSBundleInstaller(run=fake_run)

    catalog = MigrationCatalog()

    def forward(ctx):
        settings = json.loads((ctx.user_home / "config" / "settings.json").read_text(encoding="utf-8"))
        settings["setting"] = "migrated"
        (ctx.user_home / "config" / "settings.json").write_text(json.dumps(settings), encoding="utf-8")

    catalog.register(
        MigrationStep(
            name="bump-settings",
            from_version="0.5.0",
            to_version="0.6.0",
            affected_paths=("config",),
            forward=forward,
        )
    )

    box = Harness(
        tmp=tmp_path,
        data_dir=data_dir,
        app_path=app_path,
        raw_manifest=raw,
        manifest=verification.manifest,
        installer=installer,
        server=FakeServer(artifact_bytes),
        coordinator=WorkCoordinator(),
        catalog=catalog,
    )
    box._keys = (private_hex, public_hex)
    decision = decide_update(
        verification.manifest,
        installed_version="0.5.0",
        channel="stable",
        platform_key="macos-arm64",
        high_water={"sequence": 0},
        installer_available=True,
    )
    assert decision.reason is UpdateDecisionReason.UPDATE_AVAILABLE
    box.decision = decision
    return box


class TestHappyPath:
    def test_full_flow_installs_and_finalizes(self, harness):
        result = harness.make_flow().run(harness.raw_manifest, harness.manifest, harness.decision)
        assert result.ok, result.detail
        assert result.terminal is Step.FINALIZED
        assert harness.marker() == "new"
        assert harness.config()["setting"] == "migrated"
        assert harness.relaunch_log  # the app was restarted
        # the previous bundle is retained (rollback-able) until pruning
        priors = list((harness.app_path.parent).glob(".VOOL.app.prior-*"))
        assert len(priors) == 1
        # user data stayed outside the bundle
        assert not (harness.app_path / "update_v2").exists()
        # success receipt written
        assert result.receipt_path is not None and result.receipt_path.exists()
        receipt = json.loads(result.receipt_path.read_text(encoding="utf-8"))
        assert receipt["ok"] is True and receipt["kind"] == "success"
        # journal is complete and terminal
        journal = UpdateJournal(harness.data_dir / "update_v2" / "transactions" / result.txid)
        steps = [e["step"] for e in journal.entries()]
        assert steps[0] is Step.CREATED and steps[-1] is Step.FINALIZED
        for expected in (Step.MANIFEST_VERIFIED, Step.DECIDED, Step.DOWNLOADED, Step.ARTIFACT_VERIFIED,
                         Step.PREFLIGHT_OK, Step.WORK_PAUSED, Step.ACTIVE_STATE_RECEIVED, Step.SNAPSHOT_COMPLETE,
                         Step.HELPER_SHUTDOWN, Step.APP_STOPPED, Step.SWAPPED, Step.MIGRATED, Step.RESTARTED,
                         Step.HEALTHY):
            assert expected in steps

    def test_status_shows_plain_language_lifecycle(self, harness):
        harness.make_flow().run(harness.raw_manifest, harness.manifest, harness.decision)
        store = StatusStore(harness.data_dir / "update_v2" / "status.json")
        final = store.load()
        assert final is not None
        assert final.phase is UpdatePhase.DONE
        assert "0.6.0" in final.message
        assert "installed" in final.message.lower()
        seen = harness.status_log
        assert UpdatePhase.DOWNLOADING in seen
        assert UpdatePhase.VERIFYING in seen
        assert UpdatePhase.RESTARTING in seen

    def test_active_state_receipt_written_before_shutdown(self, harness):
        harness.coordinator.register(WorkHandle("chat", "answering a question"))
        result = harness.make_flow().run(harness.raw_manifest, harness.manifest, harness.decision)
        assert result.ok
        receipt = json.loads(
            (harness.data_dir / "update_v2" / "transactions" / result.txid / "active_state.json").read_text(encoding="utf-8")
        )
        assert receipt["active_work"][0]["id"] == "chat"
        assert receipt["txid"] == result.txid


class TestDestructiveWork:
    def test_destructive_work_blocks_the_install(self, harness):
        harness.coordinator.register(WorkHandle("wipe", "deleting old files", destructive=True))
        result = harness.make_flow().run(harness.raw_manifest, harness.manifest, harness.decision)
        assert not result.ok
        assert result.terminal is Step.ABORTED
        assert harness.marker() == "old"  # nothing changed
        assert "deleting old files" in result.detail
        assert harness.relaunch_log == []  # never restarted

    def test_explicit_resolution_proceeds(self, harness):
        harness.coordinator.register(WorkHandle("wipe", "deleting old files", destructive=True))
        resolution = DestructiveWorkResolution(operator_ack="operator accepted", work_ids=("wipe",))
        result = harness.make_flow().run(harness.raw_manifest, harness.manifest, harness.decision, resolution=resolution)
        assert result.ok, result.detail
        assert harness.marker() == "new"


class TestVerificationFailures:
    def test_tampered_artifact_aborts_before_anything_changes(self, harness):
        harness.server.blob = harness.server.blob[:-1] + b"X"  # corrupt the served artifact
        result = harness.make_flow().run(harness.raw_manifest, harness.manifest, harness.decision)
        assert not result.ok
        assert result.terminal is Step.ABORTED
        assert harness.marker() == "old"
        assert not harness.relaunch_log

    def test_bundle_platform_verification_failure_aborts(self, harness):
        def failing_codesign(cmd):
            return Completed(1, "", "code object is not signed at all")

        flow = harness.make_flow(installer=MacOSBundleInstaller(run=failing_codesign))
        result = flow.run(harness.raw_manifest, harness.manifest, harness.decision)
        assert not result.ok
        assert result.terminal is Step.ABORTED
        assert harness.marker() == "old"
        assert not list(harness.app_path.parent.glob(".vool-stage-*"))  # staging cleaned

    def test_press_time_manifest_tamper_is_caught(self, harness):
        forged = json.loads(harness.raw_manifest.decode("utf-8"))
        forged["version"] = "99.0.0"  # not re-signed
        raw_forged = json.dumps(forged).encode("utf-8")
        result = harness.make_flow().run(raw_forged, harness.manifest, harness.decision)
        assert not result.ok
        assert "re-verification" in result.detail
        assert harness.marker() == "old"

    def test_press_time_replay_is_refused(self, harness):
        # a DIFFERENT manifest already advanced the high-water to this sequence
        flow = harness.make_flow()
        flow.high_water.record(
            "stable", sequence=47, version="0.6.0", manifest_sha256="f" * 64, now=1.0
        )
        result = flow.run(harness.raw_manifest, harness.manifest, harness.decision)
        assert not result.ok
        assert "replayed" in result.detail
        assert harness.marker() == "old"


class TestRollback:
    def test_failed_migration_rolls_everything_back(self, harness):
        def bad_forward(ctx):
            raise RuntimeError("migration bug")

        harness.catalog._steps.clear()
        harness.catalog.register(
            MigrationStep(
                name="bump-settings",
                from_version="0.5.0",
                to_version="0.6.0",
                affected_paths=("config",),
                forward=bad_forward,
            )
        )
        result = harness.make_flow().run(harness.raw_manifest, harness.manifest, harness.decision)
        assert not result.ok
        assert result.terminal is Step.ROLLED_BACK
        assert harness.marker() == "old"  # previous bundle restored
        assert harness.config()["setting"] == "original"  # config snapshot restored
        assert harness.relaunch_log  # previous version restarted
        receipt = json.loads(result.receipt_path.read_text(encoding="utf-8"))
        assert receipt["kind"] == "rollback" and receipt["ok"] is False

    def test_failed_health_check_rolls_back_and_restarts_prior(self, harness):
        harness.health_forced_fail = True
        result = harness.make_flow().run(harness.raw_manifest, harness.manifest, harness.decision)
        assert not result.ok
        assert result.terminal is Step.ROLLED_BACK
        assert harness.marker() == "old"
        assert harness.config()["setting"] == "original"
        assert harness.relaunch_log  # prior relaunched
        store = StatusStore(harness.data_dir / "update_v2" / "status.json")
        assert store.load().phase is UpdatePhase.ROLLED_BACK
        assert "put back" in store.load().message

    def test_failed_relaunch_rolls_back(self, harness):
        flow = harness.make_flow(
            helper=InProcessHelper(shutdown=lambda: None, relauncher=lambda path: False)
        )
        result = flow.run(harness.raw_manifest, harness.manifest, harness.decision)
        assert not result.ok
        assert result.terminal is Step.ROLLED_BACK
        assert harness.marker() == "old"

    def test_failed_swap_leaves_previous_version_runnable(self, harness, monkeypatch):
        import core.updater.macos as macos_module

        real_rename = macos_module.os.rename
        state = {"failed": False}

        def flaky(src, dst):
            if str(dst).endswith("VOOL.app") and not state["failed"]:
                state["failed"] = True
                raise OSError("simulated disk failure during swap")
            return real_rename(src, dst)

        monkeypatch.setattr(macos_module.os, "rename", flaky)
        result = harness.make_flow().run(harness.raw_manifest, harness.manifest, harness.decision)
        assert not result.ok
        assert harness.marker() == "old"  # swap's own undo restored it
        assert harness.relaunch_log  # and the app was brought back up

    def test_rollback_is_idempotent(self, harness):
        harness.health_forced_fail = True
        flow = harness.make_flow()
        first = flow.run(harness.raw_manifest, harness.manifest, harness.decision)
        assert first.terminal is Step.ROLLED_BACK
        again = flow.rollback_active(detail="double rollback attempt")
        assert again.terminal is Step.ROLLED_BACK  # no second rollback, no crash
        assert harness.marker() == "old"


class TestCrashRecovery:
    def _crashed_flow(self, harness, crash_after: Step):
        flow = harness.make_flow(crash_after=crash_after)
        with pytest.raises(SimulatedCrashError):
            flow.run(harness.raw_manifest, harness.manifest, harness.decision)

    def test_crash_before_swap_recovers_to_clean_abort(self, harness):
        self._crashed_flow(harness, Step.DOWNLOADED)
        assert harness.marker() == "old"
        results = recover_interrupted_update(
            data_dir=harness.data_dir,
            app_path=harness.app_path,
            helper=InProcessHelper(shutdown=lambda: None, relauncher=lambda path: True),
            health_probe=harness.health,
            installed_version="0.5.0",
        )
        assert [r.action for r in results] == ["aborted"]
        assert harness.marker() == "old"
        # the interrupted transaction is now terminal
        journal_dir = harness.data_dir / "update_v2" / "transactions"
        journals = [UpdateJournal(d) for d in journal_dir.iterdir() if d.is_dir()]
        assert all(j.is_terminal() for j in journals)

    def test_crash_after_swap_healthy_finalizes(self, harness):
        self._crashed_flow(harness, Step.SWAPPED)
        assert harness.marker() == "new"  # the swap did happen before the crash
        results = recover_interrupted_update(
            data_dir=harness.data_dir,
            app_path=harness.app_path,
            helper=InProcessHelper(shutdown=lambda: None, relauncher=lambda path: True),
            health_probe=harness.health,  # reads the marker: new ⇒ healthy
            installed_version="0.5.0",
        )
        assert [r.action for r in results] == ["finalized"]
        assert harness.marker() == "new"
        journals = [UpdateJournal(d) for d in (harness.data_dir / "update_v2" / "transactions").iterdir() if d.is_dir()]
        assert all(j.is_terminal() for j in journals)

    def test_crash_after_swap_unhealthy_auto_rolls_back(self, harness):
        self._crashed_flow(harness, Step.SWAPPED)
        harness.health_forced_fail = True
        results = recover_interrupted_update(
            data_dir=harness.data_dir,
            app_path=harness.app_path,
            helper=InProcessHelper(shutdown=lambda: None, relauncher=lambda path: True),
            health_probe=harness.health,
            installed_version="0.5.0",
        )
        assert [r.action for r in results] == ["rolled_back"]
        assert harness.marker() == "old"  # previous version restored
        assert harness.config()["setting"] == "original"
        store = StatusStore(harness.data_dir / "update_v2" / "status.json")
        assert store.load().phase is UpdatePhase.ROLLED_BACK

    def test_terminal_transactions_are_left_alone(self, harness):
        result = harness.make_flow().run(harness.raw_manifest, harness.manifest, harness.decision)
        assert result.ok
        results = recover_interrupted_update(
            data_dir=harness.data_dir,
            app_path=harness.app_path,
            helper=InProcessHelper(shutdown=lambda: None, relauncher=lambda path: True),
            health_probe=harness.health,
            installed_version="0.6.0",
        )
        assert results == []
        assert harness.marker() == "new"


class TestNoDeclaredMigration:
    def test_hop_without_declared_steps_proceeds_without_touching_user_data(self, harness):
        harness.catalog._steps.clear()  # no migration declared for 0.5.0 → 0.6.0
        result = harness.make_flow().run(harness.raw_manifest, harness.manifest, harness.decision)
        assert result.ok, result.detail
        assert harness.marker() == "new"
        assert harness.config()["setting"] == "original"  # user data untouched
