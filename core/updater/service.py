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

import logging
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from core.updater.decision import UpdateDecision, decide_update
from core.updater.download import fetch_manifest_bytes
from core.updater.manifest import ManifestVerification, VerifiedManifest, parse_and_verify_manifest
from core.updater.state import HighWaterStore, UpdaterPaths
from core.updater.status import StatusStore, UpdatePhase
from core.updater.transaction import (
    DestructiveWorkResolution,
    ExternalHelper,
    FlowResult,
    Step,
    UpdateFlow,
    recover_interrupted_update,
)
from core.updater.trust import TrustedPublishers

logger = logging.getLogger("vool.updater.service")

DEFAULT_CHECK_INTERVAL_SECONDS = 24 * 60 * 60


@dataclass(frozen=True)
class UpdateOffer:
    """What the check authority currently has on the table."""

    raw: bytes
    manifest: VerifiedManifest
    decision: UpdateDecision
    created_at: float


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
        self._fetch = fetch or (lambda url: fetch_manifest_bytes(url))
        self._clock = clock
        self._sleep = sleeper
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._offer: UpdateOffer | None = None
        self._lock = threading.Lock()

    # -- lifecycle ----------------------------------------------------------- #

    def start(self) -> threading.Thread:
        """Start checking in the background. Returns immediately; never blocks startup."""
        if self._thread is not None and self._thread.is_alive():
            return self._thread
        self._thread = threading.Thread(target=self._loop, name="vool-update-check", daemon=True)
        self._thread.start()
        return self._thread

    def stop(self) -> None:
        self._stop.set()

    def _loop(self) -> None:
        while not self._stop.is_set():
            try:
                self.check_once()
            except Exception:  # a background check NEVER takes the app down
                logger.exception("background update check failed")
            self._stop.wait(self.interval)

    # -- one check ----------------------------------------------------------- #

    def check_once(self) -> CheckOutcome:
        with self._lock:
            self.status.publish(UpdatePhase.CHECKING)
            try:
                raw = self._fetch(self.manifest_url)
            except Exception as exc:
                self.status.publish(UpdatePhase.IDLE)
                return CheckOutcome(False, f"manifest fetch failed: {exc}")
            verification = parse_and_verify_manifest(raw, self.trust)
            if not verification.ok or verification.manifest is None:
                # Verified-refused manifests still leave a trail, but no offer.
                logger.info("manifest refused: %s", verification.reason.value)
                self.status.publish(UpdatePhase.IDLE)
                return CheckOutcome(False, verification.reason.value, verification=verification)
            manifest = verification.manifest

            # Advance the high-water for OUR channel once the document is verified —
            # this is the commit point of the replay defense (see decision.retry rule).
            if manifest.channel == self.channel:
                self.high_water.record(
                    self.channel,
                    sequence=manifest.sequence,
                    version=manifest.version,
                    manifest_sha256=manifest.canonical_sha256,
                    now=self._clock(),
                )

            decision = decide_update(
                manifest,
                installed_version=self.installed_version,
                channel=self.channel,
                platform_key=self.platform,
                high_water=self.high_water.load(self.channel),
                installer_available=_installer_available(self.platform),
            )
            if decision.should_install:
                self._offer = UpdateOffer(
                    raw=raw, manifest=manifest, decision=decision, created_at=self._clock()
                )
                self.status.publish(UpdatePhase.READY, version=manifest.version)
            else:
                self._offer = None
                if decision.reason is not None and decision.reason.name == "UP_TO_DATE":
                    self.status.publish(UpdatePhase.UP_TO_DATE)
                else:
                    self.status.publish(UpdatePhase.IDLE)
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
