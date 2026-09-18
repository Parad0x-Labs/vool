"""Non-blocking check service + explicit user-press controller.

Pinned here: `start()` returns immediately even when the manifest fetch is slow (an
update check NEVER blocks app startup); the verified offer is visible as a persisted
plain-language "Update ready" status; the replay high-water is the check authority's
commit point; installation requires a typed UserGesture — anything else raises.
"""
from __future__ import annotations

import threading
import time
from datetime import datetime, timezone

import pytest

from core.updater.decision import UpdateDecisionReason
from core.updater.service import UpdateCheckService, UpdateController, UserGesture
from core.updater.status import StatusStore, UpdatePhase
from core.updater.transaction import FlowResult, Step
from core.updater.trust import TrustedPublishers

from .helpers import build_manifest, generate_publisher_keypair, manifest_bytes

NOW = datetime(2026, 9, 1, 13, 0, 0, tzinfo=timezone.utc)


@pytest.fixture()
def env(tmp_path):
    private_hex, public_hex = generate_publisher_keypair()
    trust = TrustedPublishers(pinned_keys={"release-2026-01": public_hex})
    manifest, artifacts = build_manifest(private_hex=private_hex)
    raw = manifest_bytes(manifest)
    data_dir = tmp_path / "data"
    service = UpdateCheckService(
        manifest_url="https://updates.example.invalid/manifest.json",
        trust=trust,
        installed_version="0.5.0",
        channel="stable",
        platform="macos-arm64",
        data_dir=data_dir,
        fetch=lambda url: raw,
        clock=lambda: 1000.0,
    )
    return service, trust, raw, manifest, artifacts, data_dir, private_hex


class TestNonBlocking:
    def test_start_returns_immediately_with_a_slow_fetch(self, env):
        service, *_ = env
        release = threading.Event()

        def slow_fetch(url):
            release.wait(10.0)
            return b""

        service._fetch = slow_fetch
        began = time.monotonic()
        thread = service.start()
        elapsed = time.monotonic() - began
        assert thread.is_alive()
        assert elapsed < 1.0  # the check runs in the background, not on the caller
        release.set()
        thread.join(timeout=5.0)

    def test_check_failure_never_raises(self, env):
        service, *_ = env

        def broken_fetch(url):
            raise OSError("network down")

        service._fetch = broken_fetch
        outcome = service.check_once()
        assert not outcome.ok
        assert "fetch failed" in outcome.detail

    def test_background_loop_publishes_offer(self, env):
        service, _, _, _, _, data_dir, _ = env
        service.interval = 3600.0
        service.start()
        deadline = time.monotonic() + 5.0
        store = StatusStore(data_dir / "update_v2" / "status.json")
        while time.monotonic() < deadline:
            status = store.load()
            if status is not None and status.phase is UpdatePhase.READY:
                break
            time.sleep(0.02)
        assert status is not None
        assert status.phase is UpdatePhase.READY
        assert "0.6.0" in status.message
        assert "ready" in status.message.lower()
        service.stop()


class TestCheckOnce:
    def test_verified_manifest_produces_offer_and_high_water(self, env):
        service, *_rest = env
        outcome = service.check_once()
        assert outcome.ok
        assert outcome.decision is not None
        assert outcome.decision.reason is UpdateDecisionReason.UPDATE_AVAILABLE
        offer = service.current_offer()
        assert offer is not None and offer.manifest.version == "0.6.0"
        high_water = service.high_water.load("stable")
        assert high_water is not None and high_water["sequence"] == 47

    def test_same_manifest_again_is_a_retry_not_a_replay(self, env):
        service, *_ = env
        assert service.check_once().ok
        outcome = service.check_once()  # same document served again
        assert outcome.ok
        assert outcome.decision.reason is UpdateDecisionReason.UPDATE_AVAILABLE

    def test_channel_mismatch_records_no_high_water_for_other_channel(self, env, tmp_path):
        service, _trust, _raw, _manifest, _, _data_dir, private_hex = env
        beta_manifest, _ = build_manifest(private_hex=private_hex, channel="beta", sequence=99)
        service._fetch = lambda url: manifest_bytes(beta_manifest)
        outcome = service.check_once()
        assert outcome.ok
        assert outcome.decision.reason is UpdateDecisionReason.CHANNEL_MISMATCH
        assert service.high_water.load("stable") is None  # never advanced by a beta doc
        assert service.current_offer() is None

    def test_unsigned_manifest_is_refused_without_offer(self, env):
        service, *_ = env
        unsigned, _ = build_manifest()
        service._fetch = lambda url: manifest_bytes(unsigned)
        outcome = service.check_once()
        assert not outcome.ok
        assert service.current_offer() is None

    def test_up_to_date_publishes_up_to_date_status(self, env):
        service, *_ = env
        service.installed_version = "0.6.0"
        # high-water already at 47 with THIS manifest's sha (retry carve-out), so the
        # decision reaches the version comparison
        service.check_once()
        # same sequence + same sha is a retry; equal version ⇒ up to date
        outcome = service.check_once()
        assert outcome.ok
        assert outcome.decision.reason is UpdateDecisionReason.UP_TO_DATE
        store = StatusStore(service.data_dir / "update_v2" / "status.json")
        assert store.load().phase is UpdatePhase.UP_TO_DATE


class TestController:
    def _controller(self, offer, flow_results: list[FlowResult]):

        calls: list = []

        def flow_factory():
            def run(raw, manifest, decision, *, resolution=None):
                calls.append((raw, manifest, decision, resolution))
                return flow_results.pop(0)

            return _FakeFlow(run)

        controller = UpdateController(offer_loader=lambda: offer, flow_factory=flow_factory)
        return controller, calls

    def test_press_requires_a_typed_gesture(self, env):
        controller, _ = self._controller(None, [])
        with pytest.raises(TypeError):
            controller.press_update("yes")  # a truthy string is not a user press
        with pytest.raises(TypeError):
            controller.press_update(True)

    def test_press_without_offer_refuses(self, env):
        controller, _ = self._controller(None, [])
        result = controller.press_update(UserGesture(pressed_at=1.0))
        assert not result.ok
        assert "no update is ready" in result.detail

    def test_press_with_offer_runs_the_flow_with_the_offer(self, env):
        service, *_ = env
        service.check_once()
        offer = service.current_offer()
        assert offer is not None
        controller, calls = self._controller(offer, [FlowResult(True, Step.FINALIZED, txid="t1")])
        result = controller.press_update(UserGesture(pressed_at=2.0, origin="test"))
        assert result.ok
        assert calls and calls[0][0] == offer.raw  # the flow got the verified bytes


class _FakeFlow:
    def __init__(self, run):
        self._run = run

    def run(self, raw, manifest, decision, *, resolution=None):
        return self._run(raw, manifest, decision, resolution=resolution)
