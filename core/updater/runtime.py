"""The boot-time update subsystem — the ONE production entry into core.updater.

`boot_update_subsystem()` is called from the real boot lifecycle (apps.vool_api_server)
and by embedders. It is:
  * ASYNC — startup recovery and manifest checks run on background threads; boot itself
    does no network and no blocking filesystem scan beyond a bounded join;
  * BOUNDED — recovery joins for at most RECOVERY_BOUND_SECONDS; a check that overruns
    its timeout returns a typed failure, never a hung caller;
  * HONEST WHEN DISABLED — no pinned publisher key or no configured feed is a
    first-class `unavailable` state with a plain-language reason the UI must show,
    never silence, never a background thread pretending to work.

One press prepares a signed transaction and hands its continuation to a detached
worker outside the replacement target. The worker survives native-host shutdown,
then swaps, relaunches, checks health and finalizes or restores the saved app/data.
The restart endpoint is retained for older prepared journals.
"""
from __future__ import annotations

import concurrent.futures
import contextlib
import logging
import os
import shlex
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, replace
from enum import Enum
from pathlib import Path

from core.updater.download import StagedDownloader
from core.updater.feed import FeedConfig, load_feed_config
from core.updater.health import HttpHealthProbe
from core.updater.macos import ExternalScriptHelper, MacProcessHelper
from core.updater.migrations import MigrationCatalog
from core.updater.platforms import installer_for, platform_key
from core.updater.service import UpdateCheckService, UpdateController, UserGesture
from core.updater.state import HighWaterStore, UpdaterPaths, _atomic_write_json, _read_json
from core.updater.status import StatusStore, UpdatePhase
from core.updater.transaction import (
    DestructiveWorkResolution,
    ExternalHelper,
    Step,
    UpdateFlow,
    recover_interrupted_update,
)
from core.updater.trust import TrustedPublishers
from core.updater.work import WorkCoordinator, WorkHandle, destructive_block

logger = logging.getLogger("vool.updater.runtime")

RECOVERY_BOUND_SECONDS = 10.0
CHECK_BOUND_SECONDS = 30.0


class UnavailableReason(str, Enum):
    DISABLED = "disabled"
    NO_TRUSTED_PUBLISHER = "no_trusted_publisher"
    NO_FEED = "no_feed_configured"
    RAW_CHECKOUT = "raw_source_checkout"

    @property
    def plain(self) -> str:
        return {
            UnavailableReason.DISABLED: "update checks are switched off for this build",
            UnavailableReason.NO_TRUSTED_PUBLISHER: (
                "this build has no release publisher key pinned, so it cannot confirm "
                "where updates would come from"
            ),
            UnavailableReason.NO_FEED: "no release feed address is configured",
            UnavailableReason.RAW_CHECKOUT: "this source checkout has no install receipt; update with git pull",
        }[self]


@dataclass
class PressOutcome:
    accepted: bool
    detail: str = ""
    txid: str = ""
    phase: str = ""

    def to_dict(self) -> dict:
        return {"accepted": self.accepted, "detail": self.detail, "txid": self.txid, "phase": self.phase}


def _installed_version() -> str:
    try:
        from core.app_version import installed_version

        return installed_version()
    except Exception:  # pragma: no cover - app_version is dependency-free in practice
        return "0.0.0"


def _active_data_dir() -> Path:
    from core.runtime_paths import active_data_dir

    return active_data_dir()


class UpdateSubsystem:
    """One per process. Owns the check service, the coordinator, and the press paths."""

    def __init__(
        self,
        *,
        feed: FeedConfig | None = None,
        trust: TrustedPublishers | None = None,
        data_dir: Path | None = None,
        installed_version: str | None = None,
        platform: str | None = None,
        coordinator: WorkCoordinator | None = None,
        migration_catalog: MigrationCatalog | None = None,
        fetch: Callable[[str], bytes] | None = None,
        notarization_waiver_reason: str = "",
        health_timeout: float = 90.0,
        clock: Callable[[], float] = time.time,
        sleeper: Callable[[float], None] = time.sleep,
    ):
        self.feed = feed or load_feed_config()
        self.data_dir = Path(data_dir) if data_dir else _active_data_dir()
        self.installed_version = installed_version or _installed_version()
        self.platform = platform or platform_key()
        if not self.feed.has_target:
            source_root = Path(__file__).resolve().parents[2]
            if self.platform.startswith("linux-") and (source_root / "install_receipt.json").is_file():
                self.feed = replace(self.feed, app_path=str(source_root))
            elif self.platform.startswith("macos-"):
                for ancestor in source_root.parents:
                    if ancestor.suffix == ".app" and source_root.is_relative_to(ancestor / "Contents/Resources"):
                        self.feed = replace(self.feed, app_path=str(ancestor),
                                            helper_script=str(ancestor / "Contents/Resources/update/mac_update_helper.sh"))
                        break
        self.coordinator = coordinator or WorkCoordinator()
        self.catalog = migration_catalog or MigrationCatalog()
        self._custom_fetch = fetch
        self._clock = clock
        self.paths = UpdaterPaths.for_data_dir(self.data_dir)
        self.status = StatusStore(self.paths.status_file)
        self.high_water = HighWaterStore(self.paths.high_water_file)
        # Explicit, journaled sandbox relief ONLY: VOOL_UPDATE_SANDBOX_WAIVER=1 marks
        # a sandbox journey that cannot notarize (no Apple Developer ID on the rig).
        # Production default stays fail-closed — un-notarized bundles are refused.
        if not notarization_waiver_reason and os.environ.get("VOOL_TEST_MODE") == "1" and str(os.environ.get("VOOL_UPDATE_SANDBOX_WAIVER") or "").strip() in ("1", "true", "yes"):
            notarization_waiver_reason = (
                "sandbox journey (VOOL_UPDATE_SANDBOX_WAIVER): no Developer ID available; "
                "the production default refuses un-notarized bundles"
            )
        self.notarization_waiver_reason = notarization_waiver_reason
        self.health_timeout = float(health_timeout)
        self._press_lock = threading.Lock()
        self._started = False
        self._recovering = False
        self._paused_txid = ""
        self._install_thread: threading.Thread | None = None

        trust = trust if trust is not None else self._load_trust()
        self.trust = trust

        if str(self.feed.source) == "disabled-by-env":
            reason = UnavailableReason.DISABLED
        elif self.platform.startswith("linux-") and not (
            self.feed.has_target and (Path(self.feed.app_path) / "install_receipt.json").is_file()
        ):
            reason = UnavailableReason.RAW_CHECKOUT
        elif not trust.is_configured:
            reason = UnavailableReason.NO_TRUSTED_PUBLISHER
        elif not self.feed.has_feed:
            reason = UnavailableReason.NO_FEED
        else:
            reason = None
        self.unavailable_reason = reason
        self.configured = reason is None

        self.service: UpdateCheckService | None = None
        if self.configured:
            self.service = UpdateCheckService(
                manifest_url=self.feed.manifest_url,
                trust=self.trust,
                installed_version=self.installed_version,
                channel=self.feed.channel,
                platform=self.platform,
                data_dir=self.data_dir,
                interval_seconds=self.feed.check_interval_seconds,
                status_store=self.status,
                high_water=self.high_water,
                fetch=self._custom_fetch,
                clock=self._clock,
                sleeper=sleeper,
                check_allowed=lambda: not self._recovering and not self._press_lock.locked() and not self._worker_alive(),
            )
            self.controller = UpdateController(
                offer_loader=lambda: self.service.current_offer() if self.service else None,
                flow_factory=lambda: self.make_flow(),
            )
        else:
            self.controller = None
        self._publish_initial_status()

    # -- construction helpers ------------------------------------------------ #

    @staticmethod
    def _load_trust() -> TrustedPublishers:
        import os

        override = str(os.environ.get("VOOL_UPDATE_TRUSTED_KEYS_JSON") or "").strip()
        if override:
            with contextlib.suppress(Exception):
                return TrustedPublishers.load(config_path=Path(override))
        return TrustedPublishers.load()

    def _publish_initial_status(self) -> None:
        with contextlib.suppress(Exception):
            if not self.configured:
                assert self.unavailable_reason is not None
                self.status.publish(
                    UpdatePhase.UNAVAILABLE, reason=self.unavailable_reason.plain
                )

    # -- lifecycle ------------------------------------------------------------ #

    def start(self) -> UpdateSubsystem:
        """Bounded startup recovery (local filesystem only) + background checks.
        ONCE per process: a later boot_update_subsystem() call (the API handlers boot
        idempotently on every request) must never re-run recovery — that would treat a
        deliberately parked two-press transaction as an interrupted one and clean it."""
        if self._started:
            return self
        self._started = True
        self._recover_bounded()
        if not self.configured:
            logger.info("update subsystem UNAVAILABLE: %s", self.unavailable_reason.value)
            return self
        assert self.service is not None
        self.service.start()
        return self

    def _recover_bounded(self) -> None:
        if self._worker_alive():
            return  # the detached transaction still owns recovery/finalization
        if not self.feed.has_target:
            return  # no configured app ⇒ nothing was ever swapped by this install
        from core.updater.rollback import incomplete_rollbacks

        if incomplete_rollbacks(self):
            from core.updater.worker import start_worker

            try:
                start_worker(self, None, 'recovery-rollback-' + str(time.time_ns()), rollback_recovery=True)
            except Exception as exc:
                from core.updater.status import UpdateFault

                self.status.publish(UpdatePhase.FAILED, fault=UpdateFault.ROLLBACK_FAILED,
                                    detail=f'could not start rollback recovery: {exc}')
            return
        from core.updater.transaction import UpdateJournal

        # Recovery that can stop this host must survive its exit, just like a
        # normal update. Safe pre-swap cleanup may stay in the bounded thread.
        for directory in self.paths.transactions.iterdir() if self.paths.transactions.is_dir() else ():
            if not directory.is_dir():
                continue
            journal = UpdateJournal(directory)
            if not journal.is_terminal() and any(journal.has(step) for step in (
                Step.SWAPPED, Step.MIGRATED, Step.SWAP_PLANNED
            )):
                from core.updater.worker import start_worker

                try:
                    start_worker(self, None, "recovery-" + str(time.time_ns()), recovery=True)
                except Exception as exc:
                    from core.updater.status import UpdateFault

                    self.status.publish(UpdatePhase.FAILED, fault=UpdateFault.ROLLBACK_FAILED,
                                        detail=f"could not start recovery: {exc}")
                return
        helper = self._make_helper()
        probe = self._make_probe()
        self._recovering = True

        def recover():
            try:
                return recover_interrupted_update(
                    data_dir=self.data_dir, app_path=Path(self.feed.app_path),
                    helper=helper, health_probe=probe, installed_version=self.installed_version,
                    channel=self.feed.channel, platform=self.platform,
                )
            finally:
                self._recovering = False

        executor = concurrent.futures.ThreadPoolExecutor(max_workers=1, thread_name_prefix="vool-update-recover")
        try:
            future = executor.submit(recover)
            with contextlib.suppress(concurrent.futures.TimeoutError):
                results = future.result(timeout=RECOVERY_BOUND_SECONDS)
                for item in results or []:
                    logger.info("startup update recovery: %s %s", item.txid, item.action)
        except Exception:
            logger.exception("update recovery failed at boot; continuing")
        finally:
            with contextlib.suppress(Exception):
                executor.shutdown(wait=False)

    # -- assembly ------------------------------------------------------------- #

    def _worker_alive(self) -> bool:
        from core.updater.worker import worker_alive

        return worker_alive(self.data_dir)

    def _make_helper(self) -> ExternalHelper:
        if self.platform.startswith("linux-"):
            from core.updater.linux import LinuxProcessHelper

            override = str(os.environ.get("VOOL_UPDATE_TARGET_PIDFILE") or "")
            return LinuxProcessHelper(app_path=Path(self.feed.app_path),
                                      pid_file=Path(override) if override else self.data_dir / "vool_api.pid")
        if self.feed.helper_script and self.feed.has_target:
            return ExternalScriptHelper(
                script=Path(self.feed.helper_script),
                app_path=Path(self.feed.app_path),
                # Re-read at EVERY use: the relaunched app rewrites the pidfile at
                # boot, so after a swap the pid to wait for / shut down is the NEW
                # app's — a boot-time int goes stale exactly when rollback needs to
                # stop the app the helper just relaunched (measured in final-p1).
                target_pid=lambda: _pidfile_pid_for(Path(self.feed.app_path)),
                result_path=self.paths.root / "helper-result.json",
                relaunch_cmd=_relaunch_cmd_for(Path(self.feed.app_path), runtime_home=self.data_dir.resolve().parent,
                                              health_url=self.feed.health_url),
            )
        return MacProcessHelper(pid_file=self.data_dir / "vool_api.pid", app_path=Path(self.feed.app_path),
                                relaunch_cmd=shlex.split(_relaunch_cmd_for(Path(self.feed.app_path),
                                                        runtime_home=self.data_dir.resolve().parent,
                                                        health_url=self.feed.health_url)))

    def _make_probe(self):
        probe = HttpHealthProbe(self.feed.health_url, poll_interval=0.5, request_timeout=3.0)
        return probe.probe

    def make_flow(self, *, stop_after: Step | None = None) -> UpdateFlow:
        downloader = StagedDownloader(fetch=self._custom_fetch) if self._custom_fetch else StagedDownloader()
        installer = installer_for(self.platform)
        if self.platform.startswith("linux-") and installer is not None:
            installer.runtime_home = self.data_dir.resolve().parent
        return UpdateFlow(
            data_dir=self.data_dir,
            app_path=Path(self.feed.app_path or self.data_dir / "unconfigured-app"),
            platform=self.platform,
            installed_version=self.installed_version,
            channel=self.feed.channel,
            trust=self.trust,
            downloader=downloader,
            coordinator=self.coordinator,
            migration_catalog=self.catalog,
            helper=self._make_helper(),
            health_probe=self._make_probe(),
            installer=installer,
            status_store=self.status,
            high_water=self.high_water,
            notarization_waiver_reason=self.notarization_waiver_reason,
            allow_publisher_verified_adhoc=self.feed.allow_publisher_verified_adhoc,
            health_timeout=self.health_timeout,
            stop_after=stop_after,
        )

    # -- surfaces ------------------------------------------------------------- #

    def status_payload(self) -> dict:
        """The one shape the API and the UI chip render."""
        status = self.status.load()
        from core.updater.rollback import rollback_candidate

        candidate = rollback_candidate(self)
        payload: dict = {
            "configured": self.configured,
            "unavailable_reason": self.unavailable_reason.value if self.unavailable_reason else "",
            "unavailable_plain": self.unavailable_reason.plain if self.unavailable_reason else "",
            "installed_version": self.installed_version,
            "channel": self.feed.channel,
            "has_target": self.feed.has_target,
            "rollback_available": candidate is not None and not self._recovering and not self._worker_alive()
                                  and (not status or status.fault_code != 'rollback_failed'),
            "rollback_version": candidate['version'] if candidate else '',
            "phase": (status.phase.value if status else UpdatePhase.IDLE.value),
            "message": (status.message if status else ""),
            "target_version": (status.target_version if status else ""),
            "progress_percent": (status.progress_percent if status else None),
            "fault_code": (status.fault_code if status else ""),
            "recovery_action": (status.recovery_action if status else ""),
            "notes": "",
            "restart_pending": bool(self._paused_txid),
        }
        offer = self.service.current_offer() if self.service else None
        if offer is not None:
            payload["notes"] = offer.manifest.notes
            payload["target_version"] = offer.manifest.version
        elif payload["target_version"]:
            cached = _read_json(self.paths.root / "offer.json")
            if isinstance(cached, dict):
                payload["notes"] = str(cached.get("notes") or "")
        return payload

    def _cache_offer_notes(self, version: str, notes: str) -> None:
        with contextlib.suppress(OSError):
            _atomic_write_json(
                self.paths.root / "offer.json",
                {"version": version, "notes": notes, "recorded_at": self._clock()},
            )

    def trigger_check(self, *, timeout: float = CHECK_BOUND_SECONDS) -> dict:
        """Bounded synchronous check (the Check-again action). Never raises."""
        if not self.configured or self.service is None:
            payload = self.status_payload()
            payload["check_ok"] = False
            payload["detail"] = payload["unavailable_plain"]
            return payload
        executor = concurrent.futures.ThreadPoolExecutor(max_workers=1, thread_name_prefix="vool-update-check")
        try:
            future = executor.submit(self.service.check_once)
            outcome = future.result(timeout=max(1.0, float(timeout)))
            offer = self.service.current_offer()
            if offer is not None:
                self._cache_offer_notes(offer.manifest.version, offer.manifest.notes)
            payload = self.status_payload()
            payload["check_ok"] = outcome.ok
            payload["detail"] = outcome.detail
            payload["decision"] = outcome.decision.reason.value if outcome.decision else ""
            return payload
        except concurrent.futures.TimeoutError:
            payload = self.status_payload()
            payload["check_ok"] = False
            payload["detail"] = "the update check took too long and was abandoned"
            return payload
        except Exception as exc:
            logger.exception("update check failed")
            payload = self.status_payload()
            payload["check_ok"] = False
            payload["detail"] = f"the update check failed: {exc}"
            return payload
        finally:
            with contextlib.suppress(Exception):
                executor.shutdown(wait=False)

    # -- the two presses ------------------------------------------------------ #

    def press_install(self, gesture: UserGesture, *, resolution: DestructiveWorkResolution | None = None) -> PressOutcome:
        """One press: prepare here, continue detached through restart/health/rollback."""
        if not isinstance(gesture, UserGesture):
            raise TypeError("installing an update requires an explicit UserGesture (a user pressed Update)")
        if not self.configured or self.controller is None:
            return PressOutcome(False, detail=self.status_payload()["unavailable_plain"] or "updates are unavailable")
        if not self.feed.has_target:
            return PressOutcome(False, detail="no app path is configured for this install, so nothing can be updated")
        prior_status = self.status.load()
        if self._recovering or (prior_status and prior_status.fault_code == "rollback_failed"):
            return PressOutcome(False, detail="the previous update needs recovery before another can be installed")
        if self._worker_alive():
            return PressOutcome(False, detail="an update is already in progress")
        if not self._press_lock.acquire(blocking=False):
            return PressOutcome(False, detail="an update is already in progress")
        handed_off = False
        try:
            blocked = destructive_block(self.coordinator, resolution=resolution)
            if not blocked.ok:
                return PressOutcome(False, detail=blocked.plain_message)
            offer = self.service.current_offer() if self.service else None
            if offer is None:
                return PressOutcome(False, detail="no update is ready to install")
            def worker() -> None:
                flow = self.make_flow(stop_after=Step.SNAPSHOT_COMPLETE)
                try:
                    result = flow.run(offer.raw, offer.manifest, offer.decision, resolution=resolution)
                    if result.ok and result.terminal is Step.SNAPSHOT_COMPLETE:
                        from core.updater.worker import start_worker

                        process = start_worker(self, offer, result.txid)
                        process.wait()  # monitoring only; the detached child owns completion
                    else:
                        logger.info("install press failed: %s", result.detail)
                except Exception as exc:
                    logger.exception("could not hand off the prepared update")
                    flow._abort(f"could not start the detached updater: {exc}")
                finally:
                    self.coordinator.resume_new_work()
                    self._press_lock.release()

            self._install_thread = threading.Thread(target=worker, name="vool-update-install", daemon=True)
            self._install_thread.start()
            handed_off = True
            deadline = self._clock() + 2.0
            while self._install_thread.is_alive() and self._clock() < deadline:
                time.sleep(0.05)
            if self._install_thread.is_alive():
                return PressOutcome(True, detail="download started", phase=UpdatePhase.DOWNLOADING.value)
            return PressOutcome(True, detail="update started", phase=self.status_payload()["phase"])
        finally:
            if not handed_off:
                self._press_lock.release()

    def press_rollback(self, gesture: UserGesture) -> PressOutcome:
        """Explicit previous-code rollback; never replace current history with old data."""
        if not isinstance(gesture, UserGesture):
            raise TypeError('rollback requires an explicit UserGesture')
        if self._recovering or self._worker_alive() or not self._press_lock.acquire(blocking=False):
            return PressOutcome(False, detail='an update or recovery is already in progress')
        try:
            from core.updater.rollback import rollback_candidate
            from core.updater.work import prepare_to_pause
            from core.updater.worker import start_worker

            prior_status = self.status.load()
            if prior_status and prior_status.fault_code == 'rollback_failed':
                return PressOutcome(False, detail='incomplete recovery must be repaired before another rollback')
            candidate = rollback_candidate(self)
            if candidate is None:
                return PressOutcome(False, detail='No compatible, verified previous version is retained. Your data was not changed.')
            decision = prepare_to_pause(self.coordinator)
            if not decision.ok:
                return PressOutcome(False, detail=decision.plain_message)
            txid = 'rollback-' + str(time.time_ns())
            self.status.publish(UpdatePhase.INSTALLING)
            start_worker(self, None, txid, rollback=candidate)

            def monitor():
                while self._worker_alive():
                    time.sleep(.2)
                self.coordinator.resume_new_work()

            threading.Thread(target=monitor, daemon=True, name='vool-rollback-monitor').start()
            return PressOutcome(True, detail='Returning to the previous version; current data is kept', txid=txid,
                                phase=UpdatePhase.INSTALLING.value)
        except Exception as exc:
            self.coordinator.resume_new_work()
            return PressOutcome(False, detail=str(exc))
        finally:
            self._press_lock.release()

    def press_restart(self, gesture: UserGesture, *, resolution: DestructiveWorkResolution | None = None) -> PressOutcome:
        """Restart button: resume the parked transaction through swap + helper +
        health, finalizing or rolling back. Runs on a worker thread; the UI follows
        the status store."""
        if not isinstance(gesture, UserGesture):
            raise TypeError("restarting into an update requires an explicit UserGesture")
        if not self.configured:
            return PressOutcome(False, detail=self.status_payload()["unavailable_plain"] or "updates are unavailable")
        if not self._press_lock.acquire(blocking=False):
            return PressOutcome(False, detail="an update is already in progress")
        try:
            blocked = destructive_block(self.coordinator, resolution=resolution)
            if not blocked.ok:
                return PressOutcome(False, detail=blocked.plain_message)
            txid = self._paused_txid
            offer = self.service.current_offer() if self.service else None
            if not txid or offer is None:
                return PressOutcome(False, detail="there is no downloaded update waiting to restart into")

            def worker() -> None:
                with contextlib.suppress(Exception):
                    flow = self.make_flow()
                    flow.resume(txid, offer.raw, offer.manifest, offer.decision, resolution=resolution)
                self._paused_txid = ""

            threading.Thread(target=worker, name="vool-update-restart", daemon=True).start()
            return PressOutcome(True, detail="restarting", txid=txid, phase=UpdatePhase.RESTARTING.value)
        finally:
            self._press_lock.release()

    def press_full_from_chat(self, target_version: str = "") -> bool:
        """The in-chat 'update now' surface: one press, full flow (download → verify →
        restart → health → rollback). Returns True if it started."""
        if not self.configured or self.service is None or not self.feed.has_target:
            return False
        offer = self.service.current_offer()
        if offer is None:
            return False
        if target_version and offer.manifest.version != target_version:
            return False
        return self.press_install(UserGesture(pressed_at=self._clock(), origin="chat")).accepted

    # -- work registration (the chat dispatch registers turns) ----------------- #

    def register_turn(self, work_id: str, description: str, *, destructive: bool = False) -> WorkHandle:
        if self._worker_alive():
            from core.updater.work import UpdateInProgressError

            raise UpdateInProgressError("VOOL is finishing its update; try again after it restarts")
        handle = WorkHandle(str(work_id), description, destructive=destructive)
        self.coordinator.register(handle)
        return handle

    def complete_turn(self, work_id: str) -> None:
        self.coordinator.complete(str(work_id))


# --------------------------------------------------------------------------- #
# Process-wide singleton + boot
# --------------------------------------------------------------------------- #

_SUBSYSTEM: UpdateSubsystem | None = None
_BOOT_LOCK = threading.Lock()


def boot_update_subsystem(**kwargs) -> UpdateSubsystem:
    """The boot-lifecycle entry: idempotent, bounded, never raises."""
    global _SUBSYSTEM
    with _BOOT_LOCK:
        if _SUBSYSTEM is None:
            with contextlib.suppress(Exception):
                _SUBSYSTEM = UpdateSubsystem(**kwargs)
            if _SUBSYSTEM is None:  # even construction failure must be honest, not fatal
                _SUBSYSTEM = UpdateSubsystem(feed=load_feed_config(env={"VOOL_UPDATE_DISABLED": "1"}))
        started = False
        with contextlib.suppress(Exception):
            _SUBSYSTEM.start()
            started = True
        if not started:
            logger.warning("update subsystem failed to start; status will report unavailable")
        return _SUBSYSTEM


def get_update_subsystem() -> UpdateSubsystem | None:
    return _SUBSYSTEM


def reset_update_subsystem_for_tests() -> None:
    """Test-only: drop the singleton so a new configuration can boot."""
    global _SUBSYSTEM
    with _BOOT_LOCK:
        existing = _SUBSYSTEM
        if existing is not None and existing.service is not None:
            with contextlib.suppress(Exception):
                existing.service.stop()
        _SUBSYSTEM = None


def _pidfile_pid_for(app_path: Path) -> int:
    """The pid whose exit gates the external helper: the running daemon's pidfile
    (VOOL_UPDATE_TARGET_PIDFILE overrides for sandbox installs of a cloned app)."""
    import os

    from core.updater.macos import _pid_belongs_to_app

    override = str(os.environ.get("VOOL_UPDATE_TARGET_PIDFILE") or "").strip()
    paths = [Path(override)] if override else []
    with contextlib.suppress(Exception):
        from core.runtime_paths import active_data_dir

        paths.append(active_data_dir() / "vool_api.pid")
    for pidfile in paths:
        try:
            pid = int(pidfile.read_text(encoding="utf-8").strip() or "0")
        except (OSError, ValueError):
            continue
        if _pid_belongs_to_app(pid, app_path):
            return pid
    return 0


def _relaunch_cmd_for(app_path: Path, *, runtime_home: Path | None = None, health_url: str = "") -> str:
    """How the external helper brings the app back up: the bundle's own launcher."""
    # Run the bundle's own executable. LaunchServices can queue `open` while the
    # old native host is exiting, long past the transaction's health deadline.
    # Direct exec preserves the native window and makes restart owned/bounded.
    command = ["env"]
    if runtime_home is not None:
        command += [f"VOOL_HOME={runtime_home}"]
    if health_url:
        command += [f"VOOL_NATIVE_API_URL={health_url.removesuffix('/healthz')}"]
    command.append(str(app_path / "Contents/MacOS/VOOL"))
    return shlex.join(command)


__all__ = [
    "PressOutcome",
    "UnavailableReason",
    "UpdateSubsystem",
    "boot_update_subsystem",
    "get_update_subsystem",
    "reset_update_subsystem_for_tests",
]
