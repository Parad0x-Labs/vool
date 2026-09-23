"""The install authority: one journaled transaction per update, with crash recovery.

Journal = `<data>/update_v2/transactions/<txid>/journal.jsonl`, one JSON line per step,
fsynced. The journal is the truth; the filesystem follows it.

Steps (in order):
    CREATED → MANIFEST_VERIFIED → DECIDED → DOWNLOADED → ARTIFACT_VERIFIED
    → PREFLIGHT_OK → WORK_PAUSED → ACTIVE_STATE_RECEIVED → SNAPSHOT_COMPLETE
    → HELPER_SHUTDOWN → APP_STOPPED → SWAPPED → MIGRATED → RESTARTED → HEALTHY
    → FINALIZED
Failure at any step: ABORTED (nothing user-visible changed yet) or ROLLED_BACK
(previous bundle restored + prior version relaunched). ROLLED_BACK and FINALIZED and
ABORTED are terminal.

Hard laws enforced here:
  * nothing user-visible changes before PREFLIGHT_OK;
  * the previous bundle is NEVER deleted during a transaction (retention prune happens
    only after FINALIZED), so a failed update always leaves the previous version
    runnable;
  * restart is refused while destructive work is active unless a typed resolution names
    it (work.py);
  * crash recovery (below) turns "process died mid-update" into either a clean ABORT
    (pre-swap) or an automatic rollback + relaunch of the previous version (post-swap)
    — a half-finished update never stays installed.
"""
from __future__ import annotations

import contextlib
import json
import logging
import os
import random
import shutil
import time
import zipfile
from collections.abc import Callable
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Protocol

from core.updater import platforms as platform_registry
from core.updater.decision import UpdateDecision, decide_update
from core.updater.download import DownloadReason, StagedDownloader, staged_artifact_name, verify_staged_artifact
from core.updater.health import HealthResult
from core.updater.manifest import ArtifactEntry, VerifiedManifest, parse_and_verify_manifest
from core.updater.migrations import (
    MigrationCatalog,
    MigrationReason,
    MigrationResult,
    restore_tree,
    run_migrations,
    snapshot_tree,
)
from core.updater.state import HighWaterStore, UpdaterPaths
from core.updater.status import StatusStore, UpdateFault, UpdatePhase
from core.updater.trust import TrustedPublishers
from core.updater.work import ActiveStateReceipt, DestructiveWorkResolution, WorkCoordinator, prepare_to_pause

logger = logging.getLogger("vool.updater.transaction")

#: config/state files snapshotted before any install work (user data, never the bundle)
SNAPSHOT_RELPATHS = ("config", "data_schema.json")


class Step(Enum):
    CREATED = "created"
    MANIFEST_VERIFIED = "manifest_verified"
    DECIDED = "decided"
    DOWNLOADED = "downloaded"
    ARTIFACT_VERIFIED = "artifact_verified"
    PREFLIGHT_OK = "preflight_ok"
    WORK_PAUSED = "work_paused"
    ACTIVE_STATE_RECEIVED = "active_state_received"
    SNAPSHOT_COMPLETE = "snapshot_complete"
    HELPER_SHUTDOWN = "helper_shutdown"
    APP_STOPPED = "app_stopped"
    SWAPPED = "swapped"
    MIGRATED = "migrated"
    RESTARTED = "restarted"
    HEALTHY = "healthy"
    FINALIZED = "finalized"
    ROLLED_BACK = "rolled_back"
    ABORTED = "aborted"


_TERMINAL = (Step.FINALIZED, Step.ROLLED_BACK, Step.ABORTED)
#: steps at/after which user-visible state has changed (crash here ⇒ rollback)
_SWAP_POINT = Step.SWAPPED

_ORDER = [step for step in Step]


def _reached(before: Step, after: Step) -> bool:
    return _ORDER.index(after) > _ORDER.index(before)


class ExternalHelper(Protocol):
    """The out-of-process shutdown/relaunch contract (the updater must not depend on
    the app being alive, and the app cannot restart itself after exiting)."""

    def shutdown_app(self) -> None: ...

    def wait_until_stopped(self, *, timeout: float) -> bool: ...

    def relaunch(self, app_path: Path) -> bool: ...


@dataclass
class InProcessHelper:
    """Test/plain embedding: callables instead of a real external process."""

    shutdown: Callable[[], None] = lambda: None
    relauncher: Callable[[Path], bool] = lambda path: True
    stop_waiter: Callable[[float], bool] = lambda timeout: True

    def shutdown_app(self) -> None:
        self.shutdown()

    def wait_until_stopped(self, *, timeout: float) -> bool:
        return self.stop_waiter(timeout)

    def relaunch(self, app_path: Path) -> bool:
        return self.relauncher(Path(app_path))


@dataclass
class FlowResult:
    ok: bool
    terminal: Step
    detail: str = ""
    txid: str = ""
    prior_path: Path | None = None
    receipt_path: Path | None = None

    @property
    def rolled_back(self) -> bool:
        return self.terminal is Step.ROLLED_BACK


class UpdateJournal:
    def __init__(self, tx_dir: Path):
        self.dir = Path(tx_dir)
        self.dir.mkdir(parents=True, exist_ok=True)
        self.path = self.dir / "journal.jsonl"

    def append(self, step: Step, payload: dict | None = None) -> None:
        entry = {"ts": time.time(), "step": step.value}
        if payload:
            entry.update(payload)
        with open(self.path, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(entry, sort_keys=True) + "\n")
            fh.flush()
            os.fsync(fh.fileno())

    def entries(self) -> list[dict]:
        try:
            lines = self.path.read_text(encoding="utf-8").splitlines()
        except OSError:
            return []
        out: list[dict] = []
        for line in lines:
            try:
                data = json.loads(line)
                data["step"] = Step(str(data.get("step")))
                out.append(data)
            except (ValueError, KeyError):
                continue  # a torn final line is skipped; the last COMPLETE step counts
        return out

    def last_step(self) -> Step | None:
        entries = self.entries()
        return entries[-1]["step"] if entries else None

    def payload_of(self, step: Step) -> dict:
        for entry in self.entries():
            if entry["step"] is step:
                return {k: v for k, v in entry.items() if k not in ("ts", "step")}
        return {}

    def has(self, step: Step) -> bool:
        return any(entry["step"] is step for entry in self.entries())

    def is_terminal(self) -> bool:
        return self.last_step() in _TERMINAL


class SimulatedCrashError(Exception):
    pass


class UpdateFlow:
    """Drives one install transaction. Construct per attempt; `run()` returns a
    FlowResult and NEVER raises past a typed failure."""

    def __init__(
        self,
        *,
        data_dir: Path,
        app_path: Path,
        platform: str,
        installed_version: str,
        channel: str,
        trust: TrustedPublishers,
        downloader: StagedDownloader,
        coordinator: WorkCoordinator,
        migration_catalog: MigrationCatalog,
        helper: ExternalHelper,
        health_probe: Callable[..., HealthResult],
        installer: platform_registry.PlatformInstaller | None = None,
        status_store: StatusStore | None = None,
        high_water: HighWaterStore | None = None,
        clock: Callable[[], float] = time.time,
        crash_after: Step | None = None,
        notarization_waiver_reason: str = "",
        prior_retention: int = 2,
        health_timeout: float = 90.0,
        snapshot_relpaths: tuple[str, ...] = SNAPSHOT_RELPATHS,
        stop_after: Step | None = None,
    ):
        self.paths = UpdaterPaths.for_data_dir(Path(data_dir))
        self.data_dir = Path(data_dir)
        self.app_path = Path(app_path)
        self.platform = str(platform)
        self.installed_version = str(installed_version)
        self.channel = str(channel)
        self.trust = trust
        self.downloader = downloader
        self.coordinator = coordinator
        self.catalog = migration_catalog
        self.helper = helper
        self.health_probe = health_probe
        self.installer = installer or platform_registry.installer_for(self.platform)
        self.status = status_store or StatusStore(self.paths.status_file)
        self.high_water = high_water or HighWaterStore(self.paths.high_water_file)
        self.clock = clock
        self.crash_after = crash_after
        self.notarization_waiver_reason = str(notarization_waiver_reason or "")
        self.prior_retention = int(prior_retention)
        self.health_timeout = float(health_timeout)
        self.snapshot_relpaths = tuple(snapshot_relpaths)
        self.stop_after = stop_after
        self._txid = ""
        self._journal: UpdateJournal | None = None
        self._resuming = False

    # -- internals ----------------------------------------------------------- #

    def _publish(self, phase: UpdatePhase, **context) -> None:
        with contextlib.suppress(Exception):  # status writes never kill an update
            self.status.publish(phase, **context)

    def _step(self, step: Step, payload: dict | None = None) -> None:
        assert self._journal is not None
        if self._resuming and self._journal.has(step):
            return  # a resumed transaction never re-journals a step it already reached
        self._journal.append(step, payload)
        if self.crash_after is step:
            raise SimulatedCrashError(step)

    def _maybe_pause(self, step: Step) -> FlowResult | None:
        """Two-press UX: the install press may stop after a chosen step (the restart
        press resumes). Pauses are non-terminal by construction — recovery treats an
        unfinished journal conservatively."""
        if self.stop_after is not step:
            return None
        if step is Step.ARTIFACT_VERIFIED:
            self._publish(UpdatePhase.READY_TO_RESTART, version=self._target_version)
            return FlowResult(
                True,
                step,
                detail=f"update downloaded and verified; ready to restart into {self._target_version}",
                txid=self._txid,
            )
        return FlowResult(True, step, detail=f"paused at {step.value}", txid=self._txid)

    @property
    def _target_version(self) -> str:
        if self._journal is None:
            return ""
        return str(self._journal.payload_of(Step.DECIDED).get("target_version") or "")

    def _abort(self, detail: str, fault: UpdateFault = UpdateFault.UNEXPECTED) -> FlowResult:
        self._step(Step.ABORTED, {"detail": detail})
        self._publish(UpdatePhase.FAILED, detail=detail, fault=fault)
        return FlowResult(False, Step.ABORTED, detail=detail, txid=self._txid)

    def _fail_and_rollback(
        self, detail: str, fault: UpdateFault = UpdateFault.UNEXPECTED
    ) -> FlowResult:
        result = self.rollback_active(detail=detail, fault=fault)
        return result

    # -- rollback ------------------------------------------------------------ #

    def rollback_active(self, *, detail: str, fault: UpdateFault = UpdateFault.UNEXPECTED) -> FlowResult:
        """Roll the active transaction back: restore prior bundle + config snapshot,
        relaunch the previous version, write the failure receipt. Idempotent."""
        assert self._journal is not None
        if self._journal.is_terminal():
            return FlowResult(
                self._journal.last_step() is Step.FINALIZED,
                self._journal.last_step() or Step.ABORTED,
                detail="transaction already terminal",
                txid=self._txid,
            )
        prior = Path(self._journal.payload_of(Step.SWAPPED).get("prior_path") or "")
        previous_version = str(self._journal.payload_of(Step.DECIDED).get("installed_version") or "")
        # The delegated helper may have already RELAUNCHED the new version (its result
        # receipt precedes the relaunch), and a running new app holds the service
        # port — the prior version cannot bind while it lives. Stop whatever the
        # transaction put in the app seat before restoring the prior over it.
        # (Measured in the final-p1 journey: without this, the prior relaunch never
        # came up and the machine was left on the version that just failed health.)
        # The delegated helper is stopped with a PLAIN process stop: its
        # shutdown_app() IS the swap vehicle and must never run here.
        if getattr(self.helper, "performs_swap", False):
            stop = getattr(self.helper, "stop_running_app", None)
            if callable(stop):
                with contextlib.suppress(Exception):
                    stop(timeout=10.0)
        else:
            with contextlib.suppress(Exception):
                self.helper.shutdown_app()
            with contextlib.suppress(Exception):
                self.helper.wait_until_stopped(timeout=30.0)
        if self._journal.has(Step.SWAPPED) and prior.exists():
            if self.installer is not None:
                outcome = self.installer.restore_prior(self.app_path, prior, txid=self._txid)
                if not outcome.ok:
                    logger.error("restore_prior failed during rollback: %s", outcome.detail)
        if self._journal.has(Step.SNAPSHOT_COMPLETE):
            snapshot_dir = self.paths.transaction_dir(self._txid) / "config-snapshot"
            destinations = [self.data_dir / rel for rel in self.snapshot_relpaths]
            with contextlib.suppress(Exception):
                restore_tree(snapshot_dir, destinations)
        staged_bundle = self.app_path.parent / f".vool-stage-{self._txid}"
        with contextlib.suppress(Exception):
            if staged_bundle.exists():
                shutil.rmtree(staged_bundle)
        if self._journal.has(Step.WORK_PAUSED):
            pass  # pause is one-way per boot; the receipt records it
        with contextlib.suppress(Exception):
            self.helper.relaunch(self.app_path)
        self._step(Step.ROLLED_BACK, {"detail": detail})
        receipt = self._write_receipt(
            ok=False,
            kind="rollback",
            detail=detail,
            previous_version=previous_version or self.installed_version,
            fault=fault,
        )
        self._publish(
            UpdatePhase.ROLLED_BACK,
            previous_version=previous_version or self.installed_version,
            fault=fault,
        )
        return FlowResult(
            False,
            Step.ROLLED_BACK,
            detail=detail,
            txid=self._txid,
            prior_path=prior if prior.exists() else None,
            receipt_path=receipt,
        )

    def _write_receipt(
        self, *, ok: bool, kind: str, detail: str, previous_version: str = "", fault: UpdateFault = UpdateFault.NONE
    ) -> Path:
        target_version = str(self._journal.payload_of(Step.DECIDED).get("target_version") or "") if self._journal else ""
        payload = {
            "txid": self._txid,
            "ok": ok,
            "kind": kind,
            "detail": detail,
            "target_version": target_version,
            "previous_version": previous_version,
            "fault_code": fault.value,
            "finished_at": self.clock(),
        }
        receipt_path = self.paths.receipts / f"{self._txid}-{kind}.json"
        with contextlib.suppress(OSError):
            receipt_path.parent.mkdir(parents=True, exist_ok=True)
            tmp = receipt_path.with_suffix(".tmp")
            tmp.write_text(json.dumps(payload, sort_keys=True, indent=2) + "\n", encoding="utf-8")
            os.replace(tmp, receipt_path)
        return receipt_path

    # -- the flow ------------------------------------------------------------ #

    def run(
        self,
        raw_manifest: bytes,
        manifest: VerifiedManifest,
        decision: UpdateDecision,
        *,
        resolution: DestructiveWorkResolution | None = None,
    ) -> FlowResult:
        self._txid = f"{int(self.clock())}-{manifest.version}-{random.randbytes(4).hex()}"
        self._journal = UpdateJournal(self.paths.transaction_dir(self._txid))
        self._resuming = False
        journal = self._journal
        try:
            return self._run_inner(raw_manifest, manifest, decision, resolution=resolution)
        except SimulatedCrashError:
            raise  # simulated hard crash: journal is already on disk for recovery
        except Exception as exc:  # never raise past a typed failure
            logger.exception("update flow failed unexpectedly")
            if _reached(journal.last_step() or Step.CREATED, _SWAP_POINT) or journal.has(Step.SWAPPED):
                return self._fail_and_rollback(f"unexpected failure: {exc}")
            return self._abort(f"unexpected failure: {exc}")

    def resume(
        self,
        txid: str,
        raw_manifest: bytes,
        manifest: VerifiedManifest,
        decision: UpdateDecision,
        *,
        resolution: DestructiveWorkResolution | None = None,
    ) -> FlowResult:
        """Continue a paused transaction (the second press: Restart). The staged,
        already-verified artifact is re-verified from disk and the flow continues from
        the journaled point; steps already journaled are never duplicated."""
        self._txid = str(txid)
        self._journal = UpdateJournal(self.paths.transaction_dir(self._txid))
        self._resuming = True
        journal = self._journal
        try:
            if journal.is_terminal():
                return FlowResult(
                    journal.last_step() is Step.FINALIZED,
                    journal.last_step() or Step.ABORTED,
                    detail="transaction already terminal",
                    txid=self._txid,
                )
            if not journal.has(Step.ARTIFACT_VERIFIED):
                return FlowResult(False, Step.ABORTED, detail="nothing staged to resume", txid=self._txid)
            reverified = parse_and_verify_manifest(raw_manifest, self.trust, now=time_await(self.clock))
            if not reverified.ok or (reverified.manifest is not None and reverified.manifest.canonical_sha256 != manifest.canonical_sha256):
                return FlowResult(False, Step.ABORTED, detail="manifest failed re-verification at restart", txid=self._txid)
            artifact = manifest.artifact_for(self.platform)
            if artifact is None:
                return FlowResult(False, Step.ABORTED, detail=f"no artifact for platform {self.platform}", txid=self._txid)
            staged = self.paths.staging / staged_artifact_name(artifact)
            if not staged.exists():
                return FlowResult(False, Step.ABORTED, detail="the downloaded update is gone; start again", txid=self._txid)
            check = verify_staged_artifact(staged, artifact, self.trust.public_key_hex(manifest.key_id))
            if not check.ok:
                return FlowResult(False, Step.ABORTED, detail=check.plain_message, txid=self._txid)
            return self._tail(staged, artifact, manifest, resolution=resolution)
        except SimulatedCrashError:
            raise
        except Exception as exc:
            logger.exception("update resume failed unexpectedly")
            if journal.has(Step.SWAPPED):
                return self._fail_and_rollback(f"unexpected failure: {exc}")
            return self._abort(f"unexpected failure: {exc}")

    def _run_inner(
        self,
        raw_manifest: bytes,
        manifest: VerifiedManifest,
        decision: UpdateDecision,
        *,
        resolution: DestructiveWorkResolution | None,
    ) -> FlowResult:
        journal = self._journal
        assert journal is not None
        self._step(Step.CREATED, {"target_version": manifest.version, "channel": manifest.channel})
        if (paused := self._maybe_pause(Step.CREATED)) is not None:
            return paused

        # 1) Re-verify the manifest bytes at press time (a stale decision is not a
        #    license to skip the signature check).
        self._publish(UpdatePhase.VERIFYING)
        reverified = parse_and_verify_manifest(raw_manifest, self.trust, now=time_await(self.clock))
        if not reverified.ok or (reverified.manifest is not None and reverified.manifest.canonical_sha256 != manifest.canonical_sha256):
            return self._abort(f"manifest failed re-verification: {reverified.reason.value}", fault=UpdateFault.VERIFICATION_FAILED)
        self._step(Step.MANIFEST_VERIFIED, {"manifest_sha256": manifest.canonical_sha256})
        if (paused := self._maybe_pause(Step.MANIFEST_VERIFIED)) is not None:
            return paused

        # 2) Re-decide NOW: the world may have moved since the check.
        live = decide_update(
            manifest,
            installed_version=self.installed_version,
            channel=self.channel,
            platform_key=self.platform,
            high_water=self.high_water.load(self.channel),
            installer_available=self.installer is not None,
        )
        if not live.should_install or live.artifact is None:
            return self._abort(f"decision no longer installs: {live.reason.value}", fault=UpdateFault.NOT_APPLICABLE)
        self._step(
            Step.DECIDED,
            {
                "target_version": manifest.version,
                "installed_version": self.installed_version,
                "sequence": manifest.sequence,
                "expected_identity": manifest.expected_health_identity,
            },
        )
        if (paused := self._maybe_pause(Step.DECIDED)) is not None:
            return paused

        # 3) Download (resumable) + verify the artifact.
        artifact = live.artifact

        def progress(done: int, total: int) -> None:
            self._publish(
                UpdatePhase.DOWNLOADING,
                version=manifest.version,
                bytes_done=done,
                bytes_total=total,
                progress_percent=min(100, round(done * 100 / total)) if total else None,
            )

        self.downloader.progress = progress
        download = self.downloader.download(artifact, self.paths.staging, self.trust.public_key_hex(manifest.key_id))
        if not download.ok:
            return self._abort(download.plain_message, fault=_download_fault(download.reason))
        self._step(Step.DOWNLOADED, {"bytes": artifact.size, "sha256": artifact.sha256})
        if (paused := self._maybe_pause(Step.DOWNLOADED)) is not None:
            return paused
        self._publish(UpdatePhase.VERIFYING)
        self._step(Step.ARTIFACT_VERIFIED, {"verified": True})
        if (paused := self._maybe_pause(Step.ARTIFACT_VERIFIED)) is not None:
            return paused

        return self._tail(download.path, artifact, manifest, resolution=resolution)

    def _tail(
        self,
        staged_artifact: Path,
        artifact: ArtifactEntry,
        manifest: VerifiedManifest,
        *,
        resolution: DestructiveWorkResolution | None,
    ) -> FlowResult:
        # 4) Preflight: extract + platform bundle verification.
        self._publish(UpdatePhase.PREPARING)
        staged_bundle = self.app_path.parent / f".vool-stage-{self._txid}"
        try:
            if staged_bundle.exists():
                shutil.rmtree(staged_bundle)
            with zipfile.ZipFile(staged_artifact) as archive:
                for info in archive.infolist():
                    archive.extract(info, staged_bundle)
                    # Python's extract() does not apply the recorded mode; an app
                    # bundle whose executable lost +x would pass verification and
                    # then not run. Modes come from the archive itself.
                    recorded_mode = (info.external_attr >> 16) & 0o777
                    if recorded_mode:
                        with contextlib.suppress(OSError):
                            os.chmod(staged_bundle / info.filename, recorded_mode)
        except Exception as exc:
            with contextlib.suppress(Exception):
                shutil.rmtree(staged_bundle, ignore_errors=True)
            return self._abort(f"could not unpack the update: {exc}", fault=UpdateFault.INSTALL_FAILED)
        bundle_dir = staged_bundle
        entries = [p for p in staged_bundle.iterdir()]
        if len(entries) == 1 and entries[0].is_dir():
            bundle_dir = entries[0]
        if self.installer is None:
            return self._abort(f"no installer for platform {self.platform}", fault=UpdateFault.NOT_APPLICABLE)
        require_notarization = None
        waiver = ""
        if self.notarization_waiver_reason:
            require_notarization = False
            waiver = self.notarization_waiver_reason
        verification = self.installer.verify_bundle(bundle_dir, require_notarization=require_notarization)
        if not verification.ok:
            with contextlib.suppress(Exception):
                shutil.rmtree(staged_bundle, ignore_errors=True)
            return self._abort(
                f"the new app bundle failed platform verification: {verification.detail}",
                fault=UpdateFault.VERIFICATION_FAILED,
            )
        self._step(Step.PREFLIGHT_OK, {"notarization_waiver": waiver})

        # 5) Pause new work (destructive guard).
        pause = prepare_to_pause(self.coordinator, resolution=resolution)
        if not pause.ok:
            return self._abort(pause.plain_message, fault=UpdateFault.DESTRUCTIVE_WORK_ACTIVE)
        self._step(Step.WORK_PAUSED, {"destructive_overridden": bool(pause.destructive)})

        # 6) Active-state receipt.
        receipt = ActiveStateReceipt.capture(self._txid, self.coordinator, now=self.clock())
        receipt_path = self.paths.transaction_dir(self._txid) / "active_state.json"
        receipt_path.write_text(json.dumps(receipt.to_dict(), sort_keys=True, indent=2) + "\n", encoding="utf-8")
        self._step(Step.ACTIVE_STATE_RECEIVED, {"active_work": len(receipt.active_work)})

        # 7) Snapshot compatible state/config (outside the bundle).
        self._publish(UpdatePhase.BACKING_UP)
        snapshot_dir = self.paths.transaction_dir(self._txid) / "config-snapshot"
        sources = [self.data_dir / rel for rel in self.snapshot_relpaths]
        snapshot_tree(sources, snapshot_dir)
        self._step(Step.SNAPSHOT_COMPLETE)

        # 8) The swap path. A helper that performs the swap itself (the external
        # macOS script) gets the DELEGATED sequence: migrations run BEFORE the
        # handoff (the bash helper cannot run them), then the script — spawned
        # detached, never a child of the app being replaced — waits for the app to
        # exit, swaps atomically, and relaunches. The in-process helper keeps the
        # original order (swap, then migrate).
        delegated = bool(getattr(self.helper, "performs_swap", False))
        expected_prior = self.app_path.parent / f".{self.app_path.name}.prior-{self._txid}"

        if delegated:
            migration = self._run_migrations(manifest)
            if migration is not None:
                return migration  # a failed migration already rolled back
            self._publish(UpdatePhase.INSTALLING)
            prepare = getattr(self.helper, "prepare", None)
            if callable(prepare):
                prepare(txid=self._txid, staged_bundle=bundle_dir)
            try:
                self.helper.shutdown_app()
            except Exception as exc:
                return self._abort(f"helper could not stop the app: {exc}", fault=UpdateFault.INSTALL_FAILED)
            self._step(Step.HELPER_SHUTDOWN, {"delegated": True})
            if not self.helper.wait_until_stopped(timeout=30.0):
                return self._abort("the app did not stop in time; nothing was changed", fault=UpdateFault.INSTALL_FAILED)
            self._step(Step.APP_STOPPED, {"delegated": True})
            # The script does the swap; journal the deterministic prior location so
            # rollback and startup recovery agree with it byte-for-byte.
            self._step(Step.SWAPPED, {"delegated": True, "prior_path": str(expected_prior)})
            self._publish(UpdatePhase.RESTARTING, version=manifest.version)
            outcome, detail = self.helper.wait_for_result(timeout=max(self.health_timeout, 60.0))
            if not outcome:
                return self._fail_and_rollback(
                    f"the external updater could not finish the swap: {detail}", fault=UpdateFault.INSTALL_FAILED
                )
            self._step(Step.RESTARTED, {"delegated": True})
        else:
            self._publish(UpdatePhase.INSTALLING)
            try:
                self.helper.shutdown_app()
            except Exception as exc:
                return self._abort(f"helper could not stop the app: {exc}", fault=UpdateFault.INSTALL_FAILED)
            self._step(Step.HELPER_SHUTDOWN)
            if not self.helper.wait_until_stopped(timeout=30.0):
                return self._abort("the app did not stop in time; nothing was changed", fault=UpdateFault.INSTALL_FAILED)
            self._step(Step.APP_STOPPED)

            # 9) Atomic swap (prior kept).
            swap = self.installer.atomic_swap(bundle_dir, self.app_path, txid=self._txid)
            if not swap.ok:
                return self._fail_and_rollback(f"app swap failed: {swap.detail}", fault=UpdateFault.INSTALL_FAILED)
            self._step(Step.SWAPPED, {"prior_path": str(swap.prior_path)})

            migration = self._run_migrations(manifest)
            if migration is not None:
                return migration

            # 11) Restart + health.
            self._publish(UpdatePhase.RESTARTING)
            if not self.helper.relaunch(self.app_path):
                return self._fail_and_rollback(
                    "the app could not be restarted after the update", fault=UpdateFault.INSTALL_FAILED
                )
            self._step(Step.RESTARTED)

        health = self.health_probe(
            timeout=self.health_timeout, expected_version=manifest.expected_health_identity
        )
        if not health.ok:
            return self._fail_and_rollback(
                "the new version didn't come up healthy", fault=UpdateFault.HEALTH_CHECK_FAILED
            )
        self._step(Step.HEALTHY, {"reported_version": health.reported_version})

        # 12) Finalize: success receipt, prune old priors, DONE.
        receipt_path = self._write_receipt(ok=True, kind="success", detail="update installed", previous_version=self.installed_version)
        if self.installer is not None:
            with contextlib.suppress(Exception):
                self.installer.prune_priors(self.app_path, keep=self.prior_retention)
        self._step(Step.FINALIZED)
        self._publish(UpdatePhase.DONE, version=manifest.version)
        return FlowResult(True, Step.FINALIZED, detail="update installed", txid=self._txid, receipt_path=receipt_path)

    def _run_migrations(self, manifest: VerifiedManifest) -> FlowResult | None:
        """Transactional migrations (user data only). A catalog with no chain for this
        hop is a DECLARATION that no migration is needed (the new code owns that
        statement) — only a step that RUNS and FAILS rolls the update back. Returns a
        FlowResult only when the update must stop (rollback already happened)."""
        migration = run_migrations(
            self.catalog,
            from_version=self.installed_version,
            to_version=manifest.version,
            user_home=self.data_dir,
            snapshot_root=self.paths.transaction_dir(self._txid) / "migration-snapshots",
        )
        if migration.reason is MigrationReason.NO_PATH:
            migration = MigrationResult(True, MigrationReason.OK, applied=[])
        if not migration.ok:
            return self._fail_and_rollback(migration.plain_message, fault=UpdateFault.MIGRATION_FAILED)
        self._step(Step.MIGRATED, {"applied": migration.applied})
        return None



def _download_fault(reason: DownloadReason) -> UpdateFault:
    """The download failure taxonomy mapped onto the user-visible fault catalog."""
    return {
        DownloadReason.DISK_SPACE: UpdateFault.DISK_SPACE,
        DownloadReason.FETCH_FAILED: UpdateFault.DOWNLOAD_FAILED,
        DownloadReason.REFUSED: UpdateFault.DOWNLOAD_BLOCKED,
        DownloadReason.SIZE_MISMATCH: UpdateFault.VERIFICATION_FAILED,
        DownloadReason.HASH_MISMATCH: UpdateFault.VERIFICATION_FAILED,
        DownloadReason.ARTIFACT_SIGNATURE_INVALID: UpdateFault.VERIFICATION_FAILED,
    }.get(reason, UpdateFault.DOWNLOAD_FAILED)


def time_await(clock: Callable[[], float]):
    """datetime 'now' adapter built from the flow's clock (skew checks stay testable)."""
    from datetime import datetime, timezone

    return datetime.fromtimestamp(clock(), tz=timezone.utc)


# --------------------------------------------------------------------------- #
# Crash recovery
# --------------------------------------------------------------------------- #


@dataclass
class RecoveryResult:
    txid: str
    action: str  # "aborted" | "rolled_back" | "rollback_failed" | "finalized" | "already_terminal" | "left_running"
    detail: str = ""
    prior_path: Path | None = None


def recover_interrupted_update(
    *,
    data_dir: Path,
    app_path: Path,
    helper: ExternalHelper,
    health_probe: Callable[..., HealthResult],
    installed_version: str,
    channel: str = "stable",
    platform: str | None = None,
    status_store: StatusStore | None = None,
    clock: Callable[[], float] = time.time,
) -> list[RecoveryResult]:
    """Startup recovery: find journals that never reached a terminal step.

    Pre-swap leftovers are aborted + cleaned (the previous version never changed).
    Post-swap leftovers get a health check against the NEW version: healthy ⇒ finalize
    (the crash was cosmetic), unhealthy ⇒ automatic rollback + relaunch of the previous
    version. A transaction whose owning process is still alive is left alone.
    """
    data_dir = Path(data_dir)
    app_path = Path(app_path)
    paths = UpdaterPaths.for_data_dir(data_dir)
    status = status_store or StatusStore(paths.status_file)
    results: list[RecoveryResult] = []
    platform_key = platform or platform_registry.platform_key()
    installer = platform_registry.installer_for(platform_key)

    tx_root = paths.transactions
    for tx_dir in sorted(tx_root.iterdir()) if tx_root.is_dir() else []:
        if not tx_dir.is_dir():
            continue
        journal = UpdateJournal(tx_dir)
        if journal.is_terminal():
            continue
        txid = tx_dir.name
        last = journal.last_step()
        if last is None:
            continue
        decided = journal.payload_of(Step.DECIDED)
        target_version = str(decided.get("target_version") or "")

        if not journal.has(Step.SWAPPED):
            staged_bundle = app_path.parent / f".vool-stage-{txid}"
            with contextlib.suppress(Exception):
                if staged_bundle.exists():
                    shutil.rmtree(staged_bundle)
            journal.append(Step.ABORTED, {"detail": "recovered at startup: interrupted before the app was touched"})
            status.publish(
                UpdatePhase.FAILED,
                detail="an unfinished update was cleaned up; the app was not changed",
                fault=UpdateFault.STALE_UPDATE_CLEANED_UP,
            )
            results.append(RecoveryResult(txid, "aborted", "interrupted before swap"))
            continue

        # Swap had happened: verify the new version actually works, against the
        # exact identity the transaction pinned when it was decided.
        expected_identity = str(decided.get("expected_identity") or target_version)
        health = health_probe(timeout=30.0, expected_version=expected_identity)
        if health.ok:
            journal.append(Step.HEALTHY, {"reported_version": health.reported_version, "recovered": True})
            journal.append(Step.FINALIZED, {"recovered": True})
            status.publish(UpdatePhase.DONE, version=target_version)
            results.append(RecoveryResult(txid, "finalized", "new version healthy after crash recovery"))
            continue

        prior_payload = journal.payload_of(Step.SWAPPED).get("prior_path") or ""
        prior = Path(prior_payload) if prior_payload else None
        # stop the (possibly helper-relaunched) new version first: it holds the
        # service port the prior version needs (see rollback_active). A delegated
        # helper gets a plain stop — its shutdown_app() runs the swap script.
        if getattr(helper, "performs_swap", False):
            stop = getattr(helper, "stop_running_app", None)
            if callable(stop):
                with contextlib.suppress(Exception):
                    stop(timeout=10.0)
        else:
            with contextlib.suppress(Exception):
                helper.shutdown_app()
            with contextlib.suppress(Exception):
                helper.wait_until_stopped(timeout=30.0)
        try:
            if installer is None:
                raise RuntimeError(f"no recovery installer for {platform_key}")
            if prior is None or not prior.exists():
                raise RuntimeError("the prior app backup is unavailable")
            restore = installer.restore_prior(app_path, prior, txid=txid)
            if not restore.ok:
                raise RuntimeError(restore.detail or "the prior app could not be restored")
            snapshot_dir = tx_dir / "config-snapshot"
            if snapshot_dir.exists():
                destinations = [data_dir / rel for rel in SNAPSHOT_RELPATHS]
                restore_tree(snapshot_dir, destinations)
        except Exception as exc:
            detail = f"Recovery rollback incomplete: {exc}"
            logger.error("%s", detail)
            status.publish(UpdatePhase.FAILED, fault=UpdateFault.ROLLBACK_FAILED, detail=detail)
            # Preserve the journal and backups; never relaunch an unverified
            # app or mark a rollback complete when restoration did not finish.
            results.append(RecoveryResult(txid, "rollback_failed", detail, prior_path=prior))
            continue
        with contextlib.suppress(Exception):
            helper.relaunch(app_path)
        journal.append(Step.ROLLED_BACK, {"detail": "automatic rollback during startup recovery"})
        status.publish(
            UpdatePhase.ROLLED_BACK,
            previous_version=str(decided.get("installed_version") or ""),
            fault=UpdateFault.HEALTH_CHECK_FAILED,
        )
        results.append(
            RecoveryResult(txid, "rolled_back", "new version unhealthy after crash", prior_path=prior if prior and prior.exists() else None)
        )
    return results


__all__ = [
    "SNAPSHOT_RELPATHS",
    "ExternalHelper",
    "FlowResult",
    "InProcessHelper",
    "RecoveryResult",
    "Step",
    "UpdateFlow",
    "UpdateJournal",
    "recover_interrupted_update",
]
