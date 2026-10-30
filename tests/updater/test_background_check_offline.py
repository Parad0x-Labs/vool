"""A daily check nobody pressed that cannot reach the release page changes nothing the person sees.

A laptop that wakes before its Wi-Fi is back runs the scheduled check offline. Before, that replaced
"An update is ready" with "The update couldn't be installed: update check failed" (the header chip turned
red as "Update failed") and dropped the offer until the next daily check, although nothing was installed.
"""
from __future__ import annotations

from core.updater.service import UpdateCheckService
from core.updater.status import UpdatePhase
from core.updater.trust import TrustedPublishers

from .helpers import build_manifest, generate_publisher_keypair, manifest_bytes


def _service(tmp_path, fetch):
    private, public = generate_publisher_keypair()
    document, _ = build_manifest(private_hex=private, version="0.7.1", channel="beta")
    state = {"online": True}

    def reach(url):
        if not state["online"]:
            raise OSError("network is unreachable")
        fetch.append(url)
        return manifest_bytes(document)

    service = UpdateCheckService(manifest_url="fixture", trust=TrustedPublishers({"release-2026-01": public}),
                                 installed_version="0.7.0", channel="beta", platform="macos-arm64",
                                 data_dir=tmp_path, fetch=reach)
    return service, state


def test_offline_background_check_keeps_the_ready_update(tmp_path):
    calls = []
    service, state = _service(tmp_path, calls)
    assert service.check_once(preserve_failure=True).ok
    ready = service.status.load()
    assert ready.phase is UpdatePhase.READY
    state["online"] = False
    assert not service.check_once(preserve_failure=True).ok
    after = service.status.load()
    assert after.phase is UpdatePhase.READY
    assert after.message == "An update is ready: 0.7.1. Press Update to install it."
    assert service.current_offer() is not None


def test_offline_background_check_keeps_up_to_date_and_pressed_check_still_reports(tmp_path):
    calls = []
    service, state = _service(tmp_path, calls)
    service.status.publish(UpdatePhase.UP_TO_DATE)
    state["online"] = False
    assert not service.check_once(preserve_failure=True).ok
    assert service.status.load().phase is UpdatePhase.UP_TO_DATE
    # A press is the person asking: they are told the check did not reach the internet.
    assert not service.check_once().ok
    pressed = service.status.load()
    assert pressed.phase is UpdatePhase.FAILED and "check your connection" in pressed.message
