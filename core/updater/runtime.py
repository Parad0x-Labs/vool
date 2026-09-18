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

The two-press contract: `press_install` (the Update button) downloads and verifies,
then parks at READY_TO_RESTART; `press_restart` (the Restart button) resumes the SAME
journaled transaction through swap + external helper + health + finalize-or-rollback.
Both require a typed UserGesture — the web API creates it from a real UI click, the
in-chat surface from an explicit "update now", the CLI from --i-pressed-update.
"""
from __future__ import annotations

import concurrent.futures
import contextlib
import logging
import os
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
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

    @property
    def plain(self) -> str:
        return {
            UnavailableReason.DISABLED: "update checks are switched off for this build",
            UnavailableReason.NO_TRUSTED_PUBLISHER: (
                "this build has no release publisher key pinned, so it cannot confirm "
                "where updates would come from"
            ),
            UnavailableReason.NO_FEED: "no release feed address is configured",
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
        if not notarization_waiver_reason and str(os.environ.get("VOOL_UPDATE_SANDBOX_WAIVER") or "").strip() in ("1", "true", "yes"):
            notarization_waiver_reason = (
                "sandbox journey (VOOL_UPDATE_SANDBOX_WAIVER): no Developer ID available; "
                "the production default refuses un-notarized bundles"
            )
        self.notarization_waiver_reason = notarization_waiver_reason
        self.health_timeout = float(health_timeout)
        self._press_lock = threading.Lock()
        self._started = False
        self._paused_txid = ""
        self._install_thread: threading.Thread | None = None

        trust = trust if trust is not None else self._load_trust()
        self.trust = trust

        if str(self.feed.source) == "disabled-by-env":
            reason = UnavailableReason.DISABLED
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
        if not self.configured:
            logger.info("update subsystem UNAVAILABLE: %s", self.unavailable_reason.value)
            return self
        self._recover_bounded()
        assert self.service is not None
        self.service.start()
        return self

    def _recover_bounded(self) -> None:
        if not self.feed.has_target:
            return  # no configured app ⇒ nothing was ever swapped by this install
        helper = self._make_helper()
        probe = self._make_probe()
        executor = concurrent.futures.ThreadPoolExecutor(max_workers=1, thread_name_prefix="vool-update-recover")
        try:
            future = executor.submit(
                recover_interrupted_update,
                data_dir=self.data_dir,
                app_path=Path(self.feed.app_path),
                helper=helper,
                health_probe=probe,
                installed_version=self.installed_version,
                channel=self.feed.channel,
                platform=self.platform,
            )
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

    def _make_helper(self) -> ExternalHelper:
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
                relaunch_cmd=_relaunch_cmd_for(Path(self.feed.app_path)),
            )
        return MacProcessHelper()

    def _make_probe(self):
        probe = HttpHealthProbe(self.feed.health_url, poll_interval=0.5, request_timeout=3.0)
        return probe.probe

    def make_flow(self, *, stop_after: Step | None = None) -> UpdateFlow:
        downloader = StagedDownloader(fetch=self._custom_fetch) if self._custom_fetch else StagedDownloader()
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
            installer=installer_for(self.platform),
            status_store=self.status,
            high_water=self.high_water,
            notarization_waiver_reason=self.notarization_waiver_reason,
            health_timeout=self.health_timeout,
            stop_after=stop_after,
        )

    # -- surfaces ------------------------------------------------------------- #

    def status_payload(self) -> dict:
        """The one shape the API and the UI chip render."""
        status = self.status.load()
        payload: dict = {
            "configured": self.configured,
            "unavailable_reason": self.unavailable_reason.value if self.unavailable_reason else "",
            "unavailable_plain": self.unavailable_reason.plain if self.unavailable_reason else "",
            "installed_version": self.installed_version,
            "channel": self.feed.channel,
            "has_target": self.feed.has_target,
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
        """Update button: download + verify, then park at READY_TO_RESTART."""
        if not isinstance(gesture, UserGesture):
            raise TypeError("installing an update requires an explicit UserGesture (a user pressed Update)")
        if not self.configured or self.controller is None:
            return PressOutcome(False, detail=self.status_payload()["unavailable_plain"] or "updates are unavailable")
        if not self.feed.has_target:
            return PressOutcome(False, detail="no app path is configured for this install, so nothing can be updated")
        if not self._press_lock.acquire(blocking=False):
            return PressOutcome(False, detail="an update is already in progress")
        try:
            blocked = destructive_block(self.coordinator, resolution=resolution)
            if not blocked.ok:
                return PressOutcome(False, detail=blocked.plain_message)
            offer = self.service.current_offer() if self.service else None
            if offer is None:
                return PressOutcome(False, detail="no update is ready to install")
            tx_holder = {"txid": ""}

            def worker() -> None:
                with contextlib.suppress(Exception):
                    flow = self.make_flow(stop_after=Step.ARTIFACT_VERIFIED)
                    result = flow.run(offer.raw, offer.manifest, offer.decision, resolution=resolution)
                    tx_holder["txid"] = result.txid
                    if result.ok and result.terminal is Step.ARTIFACT_VERIFIED:
                        self._paused_txid = result.txid
                    elif not result.ok:
                        logger.info("install press failed: %s", result.detail)

            self._install_thread = threading.Thread(target=worker, name="vool-update-install", daemon=True)
            self._install_thread.start()
            deadline = self._clock() + 2.0
            while self._install_thread.is_alive() and self._clock() < deadline:
                time.sleep(0.05)
            if self._install_thread.is_alive():
                return PressOutcome(True, detail="download started", phase=UpdatePhase.DOWNLOADING.value)
            return PressOutcome(bool(self._paused_txid), detail="download finished", txid=self._paused_txid)
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
        if not self._press_lock.acquire(blocking=False):
            return False

        def worker() -> None:
            try:
                flow = self.make_flow()
                flow.run(offer.raw, offer.manifest, offer.decision)
            finally:
                self._press_lock.release()

        threading.Thread(target=worker, name="vool-update-chat", daemon=True).start()
        return True

    # -- work registration (the chat dispatch registers turns) ----------------- #

    def register_turn(self, work_id: str, description: str, *, destructive: bool = False) -> WorkHandle:
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

    override = str(os.environ.get("VOOL_UPDATE_TARGET_PIDFILE") or "").strip()
    if override:
        with contextlib.suppress(Exception):
            return int(Path(override).read_text(encoding="utf-8").strip() or "0")
    with contextlib.suppress(Exception):
        from core.runtime_paths import active_data_dir

        pidfile = active_data_dir() / "vool_api.pid"
        if pidfile.exists():
            return int(pidfile.read_text(encoding="utf-8").strip() or "0")
    return 0


def _relaunch_cmd_for(app_path: Path) -> str:
    """How the external helper brings the app back up: the bundle's own launcher."""
    return f"open -n {app_path}"


__all__ = [
    "PressOutcome",
    "UnavailableReason",
    "UpdateSubsystem",
    "boot_update_subsystem",
    "get_update_subsystem",
    "reset_update_subsystem_for_tests",
]
