"""Check service + user-press controller + startup hook.

`UpdateCheckService.start()` spawns a DAEMON thread and returns immediately — an
update check never blocks app startup, and a failed check never raises into the app.
On each check it fetches the signed manifest through the one outbound door, verifies
it, records the per-channel high-water mark (the durable replay defense), decides, and
publishes the plain-language status ("Update ready" persists across restarts).

`UpdateController.press_update(gesture)` is the ONLY entry to installation, and the
gesture is a typed object — a truthy flag an automation could synthesize is not a user
press. The flow it launches re-verifies and re-decides everything anyway.
"""
from __future__ import annotations

import contextlib
import logging
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from core.updater.decision import UpdateDecision, UpdateDecisionReason, decide_update
from core.updater.download import fetch_manifest_bytes
from core.updater.manifest import ManifestVerification, VerifiedManifest, parse_and_verify_manifest
from core.updater.state import HighWaterStore, UpdaterPaths, _atomic_write_json, _read_json
from core.updater.status import StatusStore, UpdateFault, UpdatePhase
from core.updater.transaction import (
    DestructiveWorkResolution,
    ExternalHelper,
    FlowResult,
    Step,
    UpdateFlow,
    UpdateJournal,
    recover_interrupted_update,
)
from core.updater.trust import TrustedPublishers
from core.updater.versions import parse_semver

logger = logging.getLogger("vool.updater.service")

# Faults that mean the new version itself did not work (or the person undid it). After one, scheduled checks go on
# but never offer that version again; a newer one is offered. Other faults (a download that stopped, no disk space,
# work in progress, ...) keep waiting for a pressed Check, and a failed rollback always needs a person.
_SKIP_AFTER_FAULTS = frozenset(f.value for f in (
    UpdateFault.INSTALL_FAILED, UpdateFault.MIGRATION_FAILED, UpdateFault.HEALTH_CHECK_FAILED,
    UpdateFault.VERIFICATION_FAILED, UpdateFault.MANUAL_ROLLBACK, UpdateFault.UNEXPECTED,
))


def _last_attempted_version(paths: UpdaterPaths) -> str:
    """The target version of the newest update this copy decided to install, from its journals."""
    newest_ts, version = -1.0, ""
    try:
        directories = list(paths.transactions.iterdir())
    except OSError:
        return ""
    for directory in directories:
        for entry in UpdateJournal(directory).entries() if directory.is_dir() else ():
            if entry["step"] is Step.DECIDED and float(entry.get("ts") or 0) > newest_ts:
                newest_ts, version = float(entry.get("ts") or 0), str(entry.get("target_version") or "")
    return version


class SkippedVersionStore:
    """`{channel: {version, failure_at}}`: the version scheduled checks never offer again after it failed."""

    def __init__(self, path: Path):
        self.path = Path(path)

    def load(self, channel: str) -> dict:
        data = _read_json(self.path)
        entry = data.get(str(channel)) if isinstance(data, dict) else None
        return dict(entry) if isinstance(entry, dict) else {}

    def save(self, channel: str, entry: dict | None) -> None:
        data = _read_json(self.path)
        data = data if isinstance(data, dict) else {}
        if entry:
            data[str(channel)] = entry
        else:
            data.pop(str(channel), None)
        _atomic_write_json(self.path, data)

DEFAULT_CHECK_INTERVAL_SECONDS = 24 * 60 * 60


@dataclass(frozen=True)
class UpdateOffer:
    """What the check authority currently has on the table."""

    raw: bytes
    manifest: VerifiedManifest
    decision: UpdateDecision
    created_at: float
    installed_version: str = ""

    @property
    def available(self) -> bool:
        return self.decision.should_install

    @property
    def target_version(self) -> str:
        return self.manifest.version

    @property
    def changelog(self) -> list[str]:
        return [line for line in self.manifest.notes.splitlines() if line.strip()][:8]

    @property
    def asset_url(self) -> str:
        return self.decision.artifact.url if self.decision.artifact is not None else ""


@dataclass(frozen=True)
class CheckOutcome:
    ok: bool
    detail: str = ""
    decision: UpdateDecision | None = None
    verification: ManifestVerification | None = None


class UpdateCheckService:
    def __init__(
        self,
        *,
        manifest_url: str,
        trust: TrustedPublishers,
        installed_version: str,
        channel: str,
        platform: str,
        data_dir: Path,
        interval_seconds: float = DEFAULT_CHECK_INTERVAL_SECONDS,
        status_store: StatusStore | None = None,
        high_water: HighWaterStore | None = None,
        fetch: Callable[[str], bytes] | None = None,
        clock: Callable[[], float] = time.time,
        sleeper: Callable[[float], None] = time.sleep,
        check_allowed: Callable[[], bool] | None = None,
    ):
        self.manifest_url = str(manifest_url)
        self.trust = trust
        self.installed_version = str(installed_version)
        self.channel = str(channel)
        self.platform = str(platform)
        self.data_dir = Path(data_dir)
        self.interval = float(interval_seconds)
        paths = UpdaterPaths.for_data_dir(self.data_dir)
        self.status = status_store or StatusStore(paths.status_file)
        self.high_water = high_water or HighWaterStore(paths.high_water_file)
        self._paths = paths
        self.skipped = SkippedVersionStore(paths.root / "skipped_version.json")
        self._fetch = fetch or (lambda url: fetch_manifest_bytes(url))
        self._clock = clock
        self._sleep = sleeper
        self._check_allowed = check_allowed or (lambda: True)
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._offer: UpdateOffer | None = None
        self._lock = threading.Lock()

    # -- lifecycle ----------------------------------------------------------- #

    def start(self, *, check_immediately: bool = True) -> threading.Thread:
        """Start checking in the background. Returns immediately; never blocks startup."""
        if self._thread is not None and self._thread.is_alive():
            return self._thread
        self._thread = threading.Thread(target=self._loop, kwargs={"check_immediately": check_immediately},
                                        name="vool-update-check", daemon=True)
        self._thread.start()
        return self._thread

    def stop(self) -> None:
        self._stop.set()

    def _loop(self, *, check_immediately: bool = True) -> None:
        if not check_immediately and self._stop.wait(self.interval):
            return
        while not self._stop.is_set():
            if not self._check_allowed():
                self._stop.wait(0.2)
                continue
            try:
                self.check_once(preserve_failure=True)
            except Exception:  # a background check NEVER takes the app down
                logger.exception("background update check failed")
            self._stop.wait(self.interval)

    # -- one check ----------------------------------------------------------- #

    def check_once(self, *, preserve_failure: bool = False) -> CheckOutcome:
        with self._lock:
            if not self._check_allowed():
                return CheckOutcome(False, "an update is already in progress")
            previous = self.status.load()
            failed_state = previous is not None and bool(previous.fault_code) and previous.phase in (
                UpdatePhase.FAILED, UpdatePhase.ROLLED_BACK)
            if previous is not None and previous.fault_code == "rollback_failed":
                self._offer = None
                return CheckOutcome(False, "the previous update needs attention; recovery status was retained")
            if not preserve_failure:
                self.skipped.save(self.channel, None)  # a pressed Check may retry the version that failed
            skip = self.skipped.load(self.channel) if preserve_failure else {}
            if preserve_failure and failed_state and skip.get("failure_at") != previous.updated_at:
                # First scheduled check after this failure: remember which version failed, once.
                version = ""
                if previous.fault_code in _SKIP_AFTER_FAULTS:
                    version = _last_attempted_version(self._paths) or str(
                        (self.high_water.load(self.channel) or {}).get("version") or "")
                skip = {"version": version, "failure_at": previous.updated_at} if version else {}
                if skip:
                    self.skipped.save(self.channel, skip)
            skip_semver = None
            with contextlib.suppress(ValueError, TypeError):
                skip_semver = parse_semver(str(skip.get("version") or "")) if skip else None
            if preserve_failure and failed_state and skip_semver is None:
                self._offer = None
                return CheckOutcome(False, "the previous update needs attention; recovery status was retained")
            if skip_semver is None:
                self.status.publish(UpdatePhase.CHECKING)  # the failure stays on screen (and on disk) while checking

            def keep_previous() -> None:
                if previous is not None:
                    self.status.save(previous)
            try:
                raw = self._fetch(self.manifest_url)
            except Exception as exc:
                if preserve_failure and previous is not None and previous.phase is not UpdatePhase.CHECKING and (
                        previous.phase is not UpdatePhase.READY or self._offer is not None):
                    # A scheduled check that could not reach the release page changes nothing the person sees:
                    # a verified offer stays ready (Update verifies it again), any other state stays as it was.
                    self.status.save(previous)
                    return CheckOutcome(False, f"manifest fetch failed: {exc}")
                self._offer = None
                self.status.publish(UpdatePhase.FAILED, detail="update check failed; check your connection")
                return CheckOutcome(False, f"manifest fetch failed: {exc}")
            verification = parse_and_verify_manifest(raw, self.trust)
            if not verification.ok or verification.manifest is None:
                # Verified-refused manifests still leave a trail, but no offer.
                logger.info("manifest refused: %s", verification.reason.value)
                self._offer = None
                if skip_semver is not None:  # a refused feed never hides what happened to the failed update
                    keep_previous()
                    return CheckOutcome(False, verification.reason.value, verification=verification)
                self.status.publish(UpdatePhase.FAILED, detail=verification.plain_message)
                return CheckOutcome(False, verification.reason.value, verification=verification)
            manifest = verification.manifest
            if skip_semver is not None and manifest.semver <= skip_semver:
                self._offer = None
                keep_previous()
                return CheckOutcome(False, "the version that failed to update is not offered again",
                                    verification=verification)

            decision = decide_update(
                manifest,
                installed_version=self.installed_version,
                channel=self.channel,
                platform_key=self.platform,
                high_water=self.high_water.load(self.channel),
                installer_available=_installer_available(self.platform),
            )
            # Compare against the PREVIOUS mark. Recording first would admit a
            # conflicting document at a reused sequence as its own retry.
            if manifest.channel == self.channel and decision.reason.name != "REPLAYED_MANIFEST":
                self.high_water.record(self.channel, sequence=manifest.sequence, version=manifest.version,
                                       manifest_sha256=manifest.canonical_sha256, now=self._clock())
            if decision.should_install:
                self._offer = UpdateOffer(
                    raw=raw, manifest=manifest, decision=decision, created_at=self._clock(),
                    installed_version=self.installed_version,
                )
                self.status.publish(UpdatePhase.READY, version=manifest.version)
                if skip:
                    self.skipped.save(self.channel, None)
            else:
                self._offer = None
                if skip_semver is not None:  # only a newer offer replaces what happened to the failed update
                    keep_previous()
                elif decision.reason is UpdateDecisionReason.UP_TO_DATE:
                    self.status.publish(UpdatePhase.UP_TO_DATE)
                else:
                    self.status.publish(UpdatePhase.UNAVAILABLE, reason=decision.plain_message,
                                        fault="update_not_applicable")
            return CheckOutcome(True, decision.reason.value, decision=decision, verification=verification)

    def current_offer(self) -> UpdateOffer | None:
        with self._lock:
            return self._offer


def _installer_available(platform: str) -> bool:
    from core.updater.platforms import installer_for

    return installer_for(platform) is not None


@dataclass(frozen=True)
class UserGesture:
    """The explicit 'Update' press. Constructed ONLY by a user-action handler (the
    wrapper's button callback or the CLI's --i-pressed-update flag)."""

    pressed_at: float
    origin: str = "user"


class UpdateController:
    """Explicit-press gate between the offer and the install flow."""

    def __init__(
        self,
        *,
        offer_loader: Callable[[], UpdateOffer | None],
        flow_factory: Callable[[], UpdateFlow],
    ):
        self._offer_loader = offer_loader
        self._flow_factory = flow_factory

    def press_update(
        self, gesture: UserGesture, *, resolution: DestructiveWorkResolution | None = None
    ) -> FlowResult:
        if not isinstance(gesture, UserGesture):
            raise TypeError("installing an update requires an explicit UserGesture (a user pressed Update)")
        offer = self._offer_loader()
        if offer is None:
            return FlowResult(False, Step.ABORTED, detail="no update is ready to install")
        flow = self._flow_factory()
        return flow.run(offer.raw, offer.manifest, offer.decision, resolution=resolution)


def startup_recover_and_start(
    *,
    service: UpdateCheckService,
    data_dir: Path,
    app_path: Path,
    helper: ExternalHelper,
    health_probe: Callable[..., object],
    installed_version: str,
    channel: str = "stable",
    platform: str | None = None,
) -> list:
    """Startup hook: local-only crash recovery (fast, never raises into the caller),
    then the NON-BLOCKING background check. The app always starts."""
    try:
        return recover_interrupted_update(
            data_dir=data_dir,
            app_path=app_path,
            helper=helper,
            health_probe=health_probe,
            installed_version=installed_version,
            channel=channel,
            platform=platform,
        )
    except Exception:
        logger.exception("update recovery failed at startup; the app still starts")
        return []
    finally:
        service.start()


__all__ = [
    "CheckOutcome",
    "UpdateCheckService",
    "UpdateController",
    "UpdateOffer",
    "UserGesture",
    "startup_recover_and_start",
]
