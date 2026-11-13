"""Preservation-gap controls: interruption between a filesystem effect and its journal
entry, rollback truthfulness, retry-safe restoration, snapshot identity, and recovery
racing a live owner.

Each control was written before its repair; 13 of the 19 failed on the v0.8 line before
it (247124ed), and the other 6 guard paths the repair changed. They encode the laws:

  * a swap that happened without its journal entry is still recognized (the prior
    bundle on disk is the evidence) and never misclassified as "nothing was touched";
    a Linux package switch that never ran is not mistaken for one (its backup exists
    before the switch, so the current link decides);
  * a rollback whose restoration failed is an INCOMPLETE recovery — no terminal
    ROLLED_BACK step, no relaunch of an unverified app, no success-shaped receipt;
  * restoring never consumes the snapshot and never removes a destination before the
    replacement is staged, so a second recovery attempt is safe;
  * snapshot/restore identity is the declared relative path, not a basename;
  * a transaction owned by a still-living process is left alone by recovery;
  * recovery does not finalize over skipped or half-finished migrations.
"""
from __future__ import annotations

import json
import os
import signal
import subprocess
import time
import zipfile
from datetime import datetime, timezone
from pathlib import Path

import pytest

from core.updater.decision import UpdateDecisionReason, decide_update
from core.updater.download import StagedDownloader
from core.updater.health import HealthResult
from core.updater.macos import MacOSBundleInstaller
from core.updater.migrations import MigrationCatalog, MigrationStep, restore_tree, run_migrations, snapshot_tree
from core.updater.status import StatusStore
from core.updater.transaction import (
    InProcessHelper,
    SimulatedCrashError,
    Step,
    UpdateFlow,
    UpdateJournal,
    recover_interrupted_update,
)
from core.updater.trust import TrustedPublishers
from core.updater.work import WorkCoordinator

from .helpers import build_manifest, generate_publisher_keypair, manifest_bytes, sign_bytes


class _Completed:
    def __init__(self, rc: int, out: str = "", err: str = ""):
        self.returncode = rc
        self.stdout = out
        self.stderr = err


def _crash_when_appending(step_value: str):
    """A journal append that DIES ONCE, after the filesystem effect it describes
    happened. Fires a single time so the RECOVERY under test (which appends its own
    journal lines) is not killed by the same patch; the simulated process death
    already occurred.

    This is the faithful simulation of a hard kill between the effect and its journal
    line (an exception raised after the journal safely landed proves nothing).
    """
    real_append = UpdateJournal.append
    state = {"fired": False}

    def append(self, step, payload=None):
        if step.value == step_value and not state["fired"]:
            state["fired"] = True
            raise SimulatedCrashError(step)
        return real_append(self, step, payload)

    return append


def _make_bundle(root: Path, name: str, marker: str) -> Path:
    bundle = root / name
    (bundle / "Contents").mkdir(parents=True, exist_ok=True)
    (bundle / "Contents" / "marker.txt").write_text(marker, encoding="utf-8")
    return bundle


@pytest.fixture()
def rig(tmp_path: Path):
    """A compact version of the transaction-flow harness, tuned for crash windows."""
    private_hex, public_hex = generate_publisher_keypair()
    data_dir = tmp_path / "data"
    (data_dir / "config").mkdir(parents=True)
    (data_dir / "config" / "settings.json").write_text(json.dumps({"setting": "original"}), encoding="utf-8")

    apps = tmp_path / "Apps"
    app_path = _make_bundle(apps, "VOOL.app", "old")

    new_bundle = _make_bundle(tmp_path, "new-bundle", "new")
    artifact_path = tmp_path / "artifact.zip"
    with zipfile.ZipFile(artifact_path, "w") as archive:
        archive.write(new_bundle / "Contents" / "marker.txt", "VOOL.app/Contents/marker.txt")
    artifact_bytes = artifact_path.read_bytes()

    manifest, _ = build_manifest(
        version="0.6.0",
        artifacts={"macos-arm64": artifact_bytes},
        private_hex=private_hex,
    )
    manifest["artifacts"]["macos-arm64"]["signature"] = sign_bytes(private_hex, artifact_bytes)
    manifest["signature"] = {
        "key_id": "release-2026-01",
        "sig": sign_bytes(
            private_hex,
            json.dumps({k: v for k, v in manifest.items() if k != "signature"}, sort_keys=True, separators=(",", ":")).encode(),
        ),
    }
    raw = manifest_bytes(manifest)

    from core.updater.manifest import parse_and_verify_manifest

    trust = TrustedPublishers(pinned_keys={"release-2026-01": public_hex})
    verification = parse_and_verify_manifest(
        raw, trust, now=datetime(2026, 9, 1, 13, 0, 0, tzinfo=timezone.utc)
    )
    assert verification.ok, verification.reason

    def fake_run(cmd):
        if cmd[0] == "codesign":
            return _Completed(0, "", "Authority=ad-hoc")
        if cmd[0] == "spctl":
            return _Completed(0, "accepted", "")
        return _Completed(0, "", "")

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

    class Rig:
        pass

    box = Rig()
    box.tmp = tmp_path
    box.data_dir = data_dir
    box.app_path = app_path
    box.raw_manifest = raw
    box.manifest = verification.manifest
    box.installer = MacOSBundleInstaller(run=fake_run)
    box.catalog = catalog
    box.coordinator = WorkCoordinator()
    box.keys = (private_hex, public_hex)
    box.health_forced_fail = False
    box.relaunch_log: list[str] = []
    box.shutdown_log: list[str] = []
    box.installed_version = "0.5.0"
    box.target_version = "0.6.0"
    box.decision = decide_update(
        verification.manifest,
        installed_version="0.5.0",
        channel="stable",
        platform_key="macos-arm64",
        high_water={"sequence": 0},
        installer_available=True,
    )
    assert box.decision.reason is UpdateDecisionReason.UPDATE_AVAILABLE

    def health(*, timeout: float, expected_version: str | None = None) -> HealthResult:
        marker = (app_path / "Contents" / "marker.txt").read_text(encoding="utf-8").strip()
        if box.health_forced_fail and marker == "new":
            # the NEW version is the broken one; the previous version must still come up
            # healthy, because a rollback checks that it really did
            return HealthResult(False, "forced failure for the test")
        reported = box.target_version if marker == "new" else box.installed_version
        ok = (expected_version is None) or reported == expected_version
        return HealthResult(ok, f"marker={marker}", reported)

    box.health = health

    class _Server:
        def __init__(self, blob: bytes):
            self.blob = blob

        def open(self, url, headers=None, timeout=30.0):
            payload = self.blob

            class _Resp:
                status = 200
                headers = {"ETag": '"e1"'}

                def __init__(self, body):
                    self._body = body
                    self._offset = 0

                def read(self, n=-1):
                    chunk = self._body[self._offset : self._offset + n if n and n > 0 else None]
                    self._offset += len(chunk)
                    return chunk

                def close(self):
                    pass

            return _Resp(payload)

    box.server = _Server(artifact_bytes)

    def make_flow(**overrides) -> UpdateFlow:
        params = dict(
            data_dir=data_dir,
            app_path=app_path,
            platform="macos-arm64",
            installed_version=box.installed_version,
            channel="stable",
            trust=trust,
            downloader=StagedDownloader(fetch=box.server.open, chunk_size=64),
            coordinator=box.coordinator,
            migration_catalog=overrides.pop("migration_catalog", catalog),
            helper=InProcessHelper(
                shutdown=lambda: box.shutdown_log.append("shutdown"),
                relauncher=lambda path: (box.relaunch_log.append(str(path)), True)[1],
            ),
            health_probe=box.health,
            installer=box.installer,
            notarization_waiver_reason="unit-test sandbox (codesign/spctl faked)",
        )
        params.update(overrides)
        return UpdateFlow(**params)

    box.make_flow = make_flow
    return box


def _recover(box, **overrides):
    params = dict(
        data_dir=box.data_dir,
        app_path=box.app_path,
        helper=InProcessHelper(shutdown=lambda: None, relauncher=lambda path: box.relaunch_log.append(str(path)) or True),
        health_probe=box.health,
        installed_version=box.installed_version,
        platform="macos-arm64",
    )
    params.update(overrides)
    return recover_interrupted_update(**params)


def _marker(box) -> str:
    return (box.app_path / "Contents" / "marker.txt").read_text(encoding="utf-8").strip()


def _config(box) -> dict:
    return json.loads((box.data_dir / "config" / "settings.json").read_text(encoding="utf-8"))


def _tx_dirs(box) -> list[Path]:
    root = box.data_dir / "update_v2" / "transactions"
    return sorted(d for d in root.iterdir() if d.is_dir())


def _single_journal(box) -> UpdateJournal:
    dirs = _tx_dirs(box)
    assert len(dirs) == 1, f"expected exactly one transaction, got {[d.name for d in dirs]}"
    return UpdateJournal(dirs[0])


# --------------------------------------------------------------------------- #
# 1) the swap happened but its journal entry never landed
# --------------------------------------------------------------------------- #


class TestUnjournaledSwap:
    def test_crash_between_swap_effect_and_journal_recovers_as_post_swap(self, rig, monkeypatch):
        monkeypatch.setattr(UpdateJournal, "append", _crash_when_appending("swapped"))
        with pytest.raises(SimulatedCrashError):
            rig.make_flow().run(rig.raw_manifest, rig.manifest, rig.decision)
        assert _marker(rig) == "new"  # the swap effect DID happen

        results = _recover(rig)
        assert [r.action for r in results] != ["aborted"], (
            "recovery called this post-swap crash 'interrupted before the app was touched' "
            "while the never-health-checked new app is installed"
        )
        # healthy new app ⇒ finalize; the prior on disk was the evidence of the swap
        assert [r.action for r in results] == ["finalized"]
        assert _marker(rig) == "new"
        journal = _single_journal(rig)
        assert journal.is_terminal()

    def test_crash_between_swap_effect_and_journal_unhealthy_rolls_back(self, rig, monkeypatch):
        monkeypatch.setattr(UpdateJournal, "append", _crash_when_appending("swapped"))
        with pytest.raises(SimulatedCrashError):
            rig.make_flow().run(rig.raw_manifest, rig.manifest, rig.decision)
        assert _marker(rig) == "new"
        rig.health_forced_fail = True

        results = _recover(rig)
        assert [r.action for r in results] == ["rolled_back"]
        assert _marker(rig) == "old"  # prior restored even though SWAPPED never landed
        assert _config(rig)["setting"] == "original"


# --------------------------------------------------------------------------- #
# 2) an in-flow rollback whose restoration failed must stay incomplete
# --------------------------------------------------------------------------- #


class TestRollbackReceiptTruthfulness:
    def test_failed_bundle_restore_is_not_a_rolled_back_receipt(self, rig, monkeypatch):
        from core.updater.platforms import SwapOutcome

        rig.health_forced_fail = True
        monkeypatch.setattr(
            rig.installer, "restore_prior", lambda *a, **k: SwapOutcome(False, detail="simulated restore refusal")
        )
        result = rig.make_flow().run(rig.raw_manifest, rig.manifest, rig.decision)

        assert result.terminal is not Step.ROLLED_BACK, "a failed restoration was journalled as a completed rollback"
        journal = _single_journal(rig)
        assert not journal.is_terminal(), "the journal closed over an incomplete rollback"
        # the ONE relaunch is the flow's own pre-health restart of the new app; the
        # failed rollback must not add another (never relaunch an unverified seat)
        assert len(rig.relaunch_log) == 1, "an unverified app was relaunched by the failed rollback"
        if result.receipt_path is not None and result.receipt_path.exists():
            receipt = json.loads(result.receipt_path.read_text(encoding="utf-8"))
            assert receipt.get("incomplete") is True or receipt.get("fault_code") == "rollback_failed"
        status = StatusStore(rig.data_dir / "update_v2" / "status.json").load()
        assert status is not None and status.fault_code == "rollback_failed"

    def test_failed_config_restore_is_not_a_rolled_back_receipt(self, rig, monkeypatch):
        import core.updater.transaction as tx_module

        rig.health_forced_fail = True

        def exploding_restore(snapshot_dir, destinations, **_):
            raise OSError("simulated snapshot restore failure")

        monkeypatch.setattr(tx_module, "restore_tree", exploding_restore)
        result = rig.make_flow().run(rig.raw_manifest, rig.manifest, rig.decision)

        assert result.terminal is not Step.ROLLED_BACK, "a concealed config-restore failure closed the rollback"
        journal = _single_journal(rig)
        assert not journal.is_terminal()
        status = StatusStore(rig.data_dir / "update_v2" / "status.json").load()
        assert status is not None and status.fault_code == "rollback_failed"
        # the evidence (snapshot) is preserved for a retry
        tx_dir = _tx_dirs(rig)[0]
        assert (tx_dir / "config-snapshot").exists()


# --------------------------------------------------------------------------- #
# 3) a second recovery attempt after a partially-applied rollback
# --------------------------------------------------------------------------- #


class TestRetryAfterPartialRollback:
    def test_recovery_after_crash_mid_rollback_never_destroys_restored_config(self, rig, monkeypatch):
        monkeypatch.setattr(UpdateJournal, "append", _crash_when_appending("rolled_back"))
        rig.health_forced_fail = True
        with pytest.raises(SimulatedCrashError):
            rig.make_flow().run(rig.raw_manifest, rig.manifest, rig.decision)
        # mid-rollback state: prior already restored into the seat, config restored
        assert _marker(rig) == "old"
        assert _config(rig)["setting"] == "original"

        rig.health_forced_fail = False
        results = _recover(rig)
        assert results, "recovery found nothing to do for a non-terminal journal"
        # the restored config must SURVIVE the second attempt
        assert (rig.data_dir / "config" / "settings.json").exists(), (
            "the second recovery attempt deleted the user's config without a replacement"
        )
        assert _config(rig)["setting"] == "original"
        journal = _single_journal(rig)
        assert journal.is_terminal()


# --------------------------------------------------------------------------- #
# 4) snapshot identity is the declared relative path, not a basename
# --------------------------------------------------------------------------- #


class TestSnapshotIdentity:
    def test_nested_same_basename_files_keep_their_identity(self, tmp_path):
        home = tmp_path / "home"
        for rel, payload in (
            ("alpha/settings.json", '{"owner": "alpha"}'),
            ("beta/settings.json", '{"owner": "beta"}'),
        ):
            target = home / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(payload, encoding="utf-8")
        snapshot_dir = tmp_path / "snapshot"
        snapshot_tree(
            [home / "alpha/settings.json", home / "beta/settings.json"],
            snapshot_dir,
            relative_paths=("alpha/settings.json", "beta/settings.json"),
        )
        restore_tree(
            snapshot_dir,
            [home / "alpha/settings.json", home / "beta/settings.json"],
            relative_paths=("alpha/settings.json", "beta/settings.json"),
        )
        assert (home / "alpha/settings.json").read_text(encoding="utf-8") == '{"owner": "alpha"}'
        assert (home / "beta/settings.json").read_text(encoding="utf-8") == '{"owner": "beta"}'

    def test_restore_never_consumes_the_snapshot(self, tmp_path):
        home = tmp_path / "home"
        (home / "config").mkdir(parents=True)
        (home / "config" / "settings.json").write_text("v1", encoding="utf-8")
        snapshot_dir = tmp_path / "snapshot"
        snapshot_tree([home / "config"], snapshot_dir, relative_paths=("config",))

        restore_tree(snapshot_dir, [home / "config"], relative_paths=("config",))
        first = (home / "config" / "settings.json").read_text(encoding="utf-8")
        assert first == "v1"

        # a second restore (the retry after a crash between effects) must still work
        restore_tree(snapshot_dir, [home / "config"], relative_paths=("config",))
        assert (home / "config" / "settings.json").read_text(encoding="utf-8") == "v1"
        assert (snapshot_dir / "config" / "settings.json").exists(), "the snapshot was consumed by the restore"

    def test_restore_stages_the_replacement_before_removing_the_destination(self, tmp_path):
        home = tmp_path / "home"
        (home / "config").mkdir(parents=True)
        (home / "config" / "settings.json").write_text("live", encoding="utf-8")
        snapshot_dir = tmp_path / "snapshot"
        snapshot_tree([home / "config"], snapshot_dir, relative_paths=("config",))

        # a crash DURING the copy must leave the live destination untouched
        import shutil as _shutil

        real_copytree = _shutil.copytree

        def dying_copytree(src, dst, *a, **kw):
            raise OSError("hard kill mid-copy")

        _shutil.copytree = dying_copytree
        try:
            with pytest.raises(OSError):
                restore_tree(snapshot_dir, [home / "config"], relative_paths=("config",))
        finally:
            _shutil.copytree = real_copytree
        assert (home / "config" / "settings.json").read_text(encoding="utf-8") == "live", (
            "the destination was removed before the replacement was fully staged"
        )

    def test_migration_rollback_restores_nested_same_basename_paths(self, tmp_path):
        home = tmp_path
        for rel in ("alpha/state.json", "beta/state.json"):
            (home / rel).parent.mkdir(parents=True, exist_ok=True)
            (home / rel).write_text(f'{{"where": "{rel}"}}', encoding="utf-8")
        catalog = MigrationCatalog()

        def forward(ctx):
            for rel in ("alpha/state.json", "beta/state.json"):
                (ctx.user_home / rel).write_text('{"where": "migrated"}', encoding="utf-8")

        catalog.register(
            MigrationStep(
                name="touch-both",
                from_version="0.5.0",
                to_version="0.6.0",
                affected_paths=("alpha/state.json", "beta/state.json"),
                forward=forward,
            )
        )
        catalog.register(
            MigrationStep(
                name="explode",
                from_version="0.6.0",
                to_version="0.7.0",
                affected_paths=("alpha/state.json",),
                forward=lambda ctx: (_ for _ in ()).throw(RuntimeError("boom")),
            )
        )
        result = run_migrations(
            catalog,
            from_version="0.5.0",
            to_version="0.7.0",
            user_home=home,
            snapshot_root=tmp_path / "migration-snapshots",
        )
        assert not result.ok
        assert (home / "alpha/state.json").read_text(encoding="utf-8") == '{"where": "alpha/state.json"}', (
            "a nested file was restored from another file that shared its basename"
        )
        assert (home / "beta/state.json").read_text(encoding="utf-8") == '{"where": "beta/state.json"}'


# --------------------------------------------------------------------------- #
# 5) a live owner's transaction is not mistaken for abandoned work
# --------------------------------------------------------------------------- #


def _process_signature(pid: int) -> str:
    from core.updater.transaction import _process_start

    return _process_start(pid)


class TestLiveOwnerLeftAlone:
    def test_alive_foreign_owner_is_left_running(self, rig):
        sleeper = subprocess.Popen(["sleep", "60"])
        try:
            with pytest.raises(SimulatedCrashError):
                rig.make_flow(crash_after=Step.DOWNLOADED).run(rig.raw_manifest, rig.manifest, rig.decision)
            journal = _single_journal(rig)
            created = journal.entries()[0]
            assert "owner_pid" in created, "the journal never recorded who owns this transaction"
            journal_path = journal.dir / "journal.jsonl"
            entries = [json.loads(line) for line in journal_path.read_text(encoding="utf-8").splitlines() if line.strip()]
            entries[0]["owner_pid"] = sleeper.pid
            entries[0]["owner_start"] = _process_signature(sleeper.pid)
            journal_path.write_text("\n".join(json.dumps(e) for e in entries) + "\n", encoding="utf-8")
            staged = rig.app_path.parent / f".vool-stage-{journal.dir.name}"
            staged.mkdir(parents=True, exist_ok=True)
            (staged / "payload.bin").write_bytes(b"staged-by-the-live-owner")

            results = _recover(rig)
            assert [r.action for r in results] == ["left_running"], (
                f"recovery acted on a live owner's transaction: {[r.action for r in results]}"
            )
            assert (staged / "payload.bin").exists(), "recovery deleted a live transaction's staged bundle"
            after = [json.loads(line) for line in journal_path.read_text(encoding="utf-8").splitlines() if line.strip()]
            assert after == entries, "recovery rewrote a live owner's journal"
        finally:
            sleeper.terminate()
            sleeper.wait(timeout=10)

    def test_dead_owner_is_recovered_normally(self, rig):
        # a foreign pid that IS alive, but whose recorded start does not match: the
        # pid number was reused by a different process — the owner is dead
        sleeper = subprocess.Popen(["sleep", "60"])
        try:
            with pytest.raises(SimulatedCrashError):
                rig.make_flow(crash_after=Step.DOWNLOADED).run(rig.raw_manifest, rig.manifest, rig.decision)
            journal = _single_journal(rig)
            journal_path = journal.dir / "journal.jsonl"
            entries = [
                json.loads(line)
                for line in journal_path.read_text(encoding="utf-8").splitlines()
                if line.strip()
            ]
            entries[0]["owner_pid"] = sleeper.pid
            entries[0]["owner_start"] = "not-the-start-of-that-pid"
            journal_path.write_text("\n".join(json.dumps(e) for e in entries) + "\n", encoding="utf-8")

            results = _recover(rig)
            assert [r.action for r in results] == ["aborted"], "a reused pid number was treated as a live owner"
        finally:
            sleeper.terminate()
            sleeper.wait(timeout=10)

    def test_exited_owner_not_yet_reaped_is_recovered_normally(self, rig):
        # The app that started the update has exited (the update itself stops it), but the
        # process that launched it has not collected its exit status yet, so the kernel still
        # lists the pid as a zombie. It runs nothing: recovery must act, not leave the update
        # "running" forever (the installed-source interrupted journey timed out this way).
        owner = subprocess.Popen(["sleep", "60"])
        try:
            with pytest.raises(SimulatedCrashError):
                rig.make_flow(crash_after=Step.DOWNLOADED).run(rig.raw_manifest, rig.manifest, rig.decision)
            journal = _single_journal(rig)
            journal_path = journal.dir / "journal.jsonl"
            entries = [json.loads(line) for line in journal_path.read_text(encoding="utf-8").splitlines() if line.strip()]
            entries[0]["owner_pid"] = owner.pid
            entries[0]["owner_start"] = _process_signature(owner.pid)
            journal_path.write_text("\n".join(json.dumps(e) for e in entries) + "\n", encoding="utf-8")

            os.kill(owner.pid, signal.SIGKILL)  # never poll()/wait() here: that would reap it
            deadline = time.monotonic() + 10
            state = ""
            while time.monotonic() < deadline:
                state = subprocess.run(["ps", "-p", str(owner.pid), "-o", "stat="],
                                       capture_output=True, text=True, check=False).stdout.strip()
                if state.startswith("Z"):
                    break
                time.sleep(0.05)
            assert state.startswith("Z"), f"the owner never became an unreaped exited process: {state!r}"

            results = _recover(rig)
            assert [r.action for r in results] == ["aborted"], (
                f"an exited owner its parent had not reaped yet was treated as alive: {[r.action for r in results]}"
            )
        finally:
            owner.kill()
            owner.wait(timeout=10)


# --------------------------------------------------------------------------- #
# 6) recovery must not finalize over skipped or half-finished migrations
# --------------------------------------------------------------------------- #


class TestRecoveryMigrationCompletion:
    def test_post_swap_crash_runs_the_missing_migration_before_finalizing(self, rig):
        flow = rig.make_flow(crash_after=Step.SWAPPED)
        with pytest.raises(SimulatedCrashError):
            flow.run(rig.raw_manifest, rig.manifest, rig.decision)
        assert _marker(rig) == "new"
        assert _config(rig)["setting"] == "original"  # migrations never ran

        results = _recover(rig, migration_catalog=rig.catalog)
        assert [r.action for r in results] == ["finalized"]
        assert _config(rig)["setting"] == "migrated", "recovery finalized while the declared migration was skipped"
        journal = _single_journal(rig)
        assert journal.has(Step.MIGRATED)

    def test_post_swap_crash_with_failing_migration_rolls_back(self, rig):
        rig.catalog._steps.clear()

        def bad(ctx):
            raise RuntimeError("migration bug")

        rig.catalog.register(
            MigrationStep(
                name="bump-settings",
                from_version="0.5.0",
                to_version="0.6.0",
                affected_paths=("config",),
                forward=bad,
            )
        )
        flow = rig.make_flow(crash_after=Step.SWAPPED)
        with pytest.raises(SimulatedCrashError):
            flow.run(rig.raw_manifest, rig.manifest, rig.decision)

        results = _recover(rig, migration_catalog=rig.catalog)
        assert [r.action for r in results] == ["rolled_back"], "recovery finalized an update whose migration failed"
        assert _marker(rig) == "old"
        assert _config(rig)["setting"] == "original"

    def test_mid_migration_crash_rolls_back_to_the_snapshotted_state(self, rig):
        # the on-disk signature of "the process died mid-migration": the flow reached
        # the swap, the migration snapshot dirs exist, the schema never advanced, and
        # the home holds half-migrated bytes.
        flow = rig.make_flow(crash_after=Step.SWAPPED)
        with pytest.raises(SimulatedCrashError):
            flow.run(rig.raw_manifest, rig.manifest, rig.decision)
        tx_dir = _tx_dirs(rig)[0]
        steps_root = tx_dir / "migration-snapshots"
        (steps_root / "step-00-bump-settings").mkdir(parents=True, exist_ok=True)
        (rig.data_dir / "config" / "settings.json").write_text(json.dumps({"setting": "half-migrated"}), encoding="utf-8")

        results = _recover(rig, migration_catalog=rig.catalog)
        assert [r.action for r in results] == ["rolled_back"], (
            "recovery finalized (or re-ran migrations on) a half-migrated home"
        )
        assert _config(rig)["setting"] == "original", "the config snapshot was not restored over the half-migrated state"
        assert _marker(rig) == "old"


# --------------------------------------------------------------------------- #
# 7) recovery restores the flow's declared snapshot set
# --------------------------------------------------------------------------- #


class TestDeclaredSnapshotSet:
    def test_recovery_restores_the_declared_relpaths(self, rig, monkeypatch):
        (rig.data_dir / "extra.yaml").write_text("favorite: before\n", encoding="utf-8")
        rig.catalog._steps.clear()

        def forward(ctx):
            (ctx.user_home / "extra.yaml").write_text("favorite: after\n", encoding="utf-8")

        rig.catalog.register(
            MigrationStep(
                name="touch-extra",
                from_version="0.5.0",
                to_version="0.6.0",
                affected_paths=("extra.yaml",),
                forward=forward,
            )
        )
        # crash after the migration effect but before the MIGRATED line, with the
        # new app unhealthy: recovery must roll back BOTH declared snapshot paths
        rig.health_forced_fail = True
        monkeypatch.setattr(UpdateJournal, "append", _crash_when_appending("migrated"))
        with pytest.raises(SimulatedCrashError):
            rig.make_flow(snapshot_relpaths=("config", "extra.yaml")).run(rig.raw_manifest, rig.manifest, rig.decision)
        assert (rig.data_dir / "extra.yaml").read_text(encoding="utf-8") == "favorite: after\n"

        results = _recover(rig)
        assert [r.action for r in results] == ["rolled_back"]
        assert _marker(rig) == "old"
        assert (rig.data_dir / "extra.yaml").read_text(encoding="utf-8") == "favorite: before\n", (
            "recovery restored only the hard-coded snapshot set, not the declared one"
        )
        assert _config(rig)["setting"] == "original"


# --------------------------------------------------------------------------- #
# 8) platform installer edges
# --------------------------------------------------------------------------- #


class TestInstallerEdges:
    def test_restore_prior_with_a_stale_displaced_does_not_raise(self, tmp_path):
        installer = MacOSBundleInstaller(run=lambda cmd: _Completed(0))
        target = _make_bundle(tmp_path / "apps", "VOOL.app", "new")
        prior = _make_bundle(tmp_path / "apps", ".VOOL.app.prior-t1", "old")
        displaced = tmp_path / "apps" / ".VOOL.app.prior-t1-displaced"
        displaced.mkdir()
        (displaced / "stale").write_text("left over from an earlier crashed restore", encoding="utf-8")

        outcome = installer.restore_prior(target, prior, txid="t1")
        assert outcome.ok, outcome.detail
        assert (target / "Contents" / "marker.txt").read_text(encoding="utf-8").strip() == "old"
        assert not displaced.exists()

    def test_prune_priors_treats_displaced_as_garbage_not_retention(self, tmp_path):
        installer = MacOSBundleInstaller(run=lambda cmd: _Completed(0))
        apps = tmp_path / "apps"
        target = _make_bundle(apps, "VOOL.app", "current")
        real_prior = _make_bundle(apps, ".VOOL.app.prior-real", "old")
        import time

        time.sleep(0.05)
        displaced = _make_bundle(apps, ".VOOL.app.prior-t9-displaced", "rejected")

        removed = installer.prune_priors(target, keep=1)
        assert not displaced.exists(), "a -displaced directory survived pruning as if it were a prior"
        assert real_prior.exists(), "a real prior was pruned because a -displaced dir counted against retention"
        assert displaced in removed, "the pruned -displaced directory was not reported as removed"


# --------------------------------------------------------------------------- #
# 9) the Linux package: its backup exists before the switch, so only the
#    current link says whether the switch ran
# --------------------------------------------------------------------------- #


class TestLinuxPackageSwapEvidence:
    @staticmethod
    def _package(tmp_path: Path) -> tuple[Path, Path, Path]:
        root = tmp_path / "home" / ".local" / "share" / "vool"
        versions = []
        for sha in ("a" * 40, "b" * 40):
            version = root / "versions" / sha
            version.mkdir(parents=True)
            (version / "SOURCE_COMMIT").write_text(sha + "\n", encoding="utf-8")
            versions.append(version)
        (root / "current").symlink_to(Path("versions") / versions[0].name)
        return root, versions[0], versions[1]

    @staticmethod
    def _died_after_planning_the_switch(data_dir: Path, prior: Path) -> UpdateJournal:
        tx_dir = data_dir / "update_v2" / "transactions" / "t-linux"
        journal = UpdateJournal(tx_dir)
        journal.append(Step.CREATED, {"target_version": "0.6.0", "channel": "stable"})
        journal.append(Step.DECIDED, {"target_version": "0.6.0", "installed_version": "0.5.0",
                                      "platform": "linux-x64-bundle"})
        snapshot = tx_dir / "config-snapshot" / "config"
        snapshot.mkdir(parents=True)
        (snapshot / "settings.json").write_text(json.dumps({"setting": "at snapshot"}), encoding="utf-8")
        journal.append(Step.SNAPSHOT_COMPLETE, {"relpaths": ["config"]})
        journal.append(Step.APP_STOPPED)
        journal.append(Step.SWAP_PLANNED, {"prior_path": str(prior)})
        return journal

    def test_died_before_the_switch_is_cleaned_up_and_keeps_later_data(self, tmp_path):
        root, old, _new = self._package(tmp_path)
        data_dir = tmp_path / "data"
        (data_dir / "config").mkdir(parents=True)
        journal = self._died_after_planning_the_switch(data_dir, old)
        # written after the snapshot: the update never touched the app, so it must not take this back
        (data_dir / "config" / "settings.json").write_text(json.dumps({"setting": "written later"}), encoding="utf-8")
        probed = []
        results = recover_interrupted_update(
            data_dir=data_dir, app_path=root,
            helper=InProcessHelper(shutdown=lambda: None, relauncher=lambda path: True),
            health_probe=lambda **kwargs: probed.append(kwargs) or HealthResult(False, "the old version answers"),
            installed_version="0.5.0", platform="linux-x64-bundle",
        )
        assert [r.action for r in results] == ["aborted"], results
        assert not probed, "a switch that never ran was health-checked as if it had"
        assert json.loads((data_dir / "config" / "settings.json").read_text(encoding="utf-8")) == {
            "setting": "written later"}, "recovery rolled the person's data back over an update that never switched"
        assert os.readlink(root / "current") == str(Path("versions") / old.name)
        assert journal.is_terminal()

    def test_died_after_the_switch_is_recognized_as_swapped(self, tmp_path):
        root, old, new = self._package(tmp_path)
        data_dir = tmp_path / "data"
        (data_dir / "config").mkdir(parents=True)
        (data_dir / "config" / "settings.json").write_text(json.dumps({"setting": "at snapshot"}), encoding="utf-8")
        journal = self._died_after_planning_the_switch(data_dir, old)
        (root / "current").unlink()
        (root / "current").symlink_to(Path("versions") / new.name)  # the switch ran; its SWAPPED line never landed
        results = recover_interrupted_update(
            data_dir=data_dir, app_path=root,
            helper=InProcessHelper(shutdown=lambda: None, relauncher=lambda path: True),
            health_probe=lambda *, timeout, expected_version=None: HealthResult(True, "new version", "0.6.0"),
            installed_version="0.6.0", platform="linux-x64-bundle",
        )
        assert [r.action for r in results] == ["finalized"], results
        assert os.readlink(root / "current") == str(Path("versions") / new.name)
        assert journal.is_terminal()
