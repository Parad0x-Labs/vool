"""The outbound switch ("Go online on its own", network.outbound_enabled) is the master over update checks.

While it is off no update check goes online: not the background schedule, not after an earlier Check press
(check_consent.json present), and not when the user presses Check. The press answers with a calm refusal the
pages render, and an unreadable switch counts as off. With the switch on, the check runs as before.

The feed is a fetch double that records every request (the network door itself is proved on the real server
with a socket guard in the job's evidence); the subsystem, its background thread, the registry command and the
served /api/update/check route are the real ones.
"""
from __future__ import annotations

import copy
import json
import time

import pytest

from core.updater.trust import TrustedPublishers

from .helpers import build_manifest, generate_publisher_keypair, manifest_bytes

FEED_URL = "https://updates.example.invalid/manifest.json"


def _set_switch(monkeypatch, on: bool) -> None:
    from core import policy_engine

    policy = copy.deepcopy(policy_engine.load())
    policy.setdefault("network", {})["outbound_enabled"] = on
    policy.setdefault("system", {})["local_only_mode"] = False
    monkeypatch.setattr(policy_engine, "_POLICY_CACHE", policy)


@pytest.fixture
def updates(tmp_path, monkeypatch):
    """A configured feed in a fresh home whose user already pressed Check once (consent on disk)."""
    from core import runtime_paths
    from core.updater import feed, runtime
    from core.updater.runtime import UpdateSubsystem

    home = tmp_path / "home"
    config = tmp_path / "feed.json"
    config.write_text(json.dumps({"manifest_url": FEED_URL, "app_path": str(tmp_path / "VOOL.app"), "channel": "beta"}))
    monkeypatch.setattr(feed, "DEFAULT_CONFIG_PATH", config)
    monkeypatch.delenv("VOOL_UPDATE_MANIFEST_URL", raising=False)
    monkeypatch.delenv("VOOL_UPDATE_DISABLED", raising=False)
    monkeypatch.setattr(runtime_paths, "_VOOL_HOME_OVERRIDE", home)
    private, public = generate_publisher_keypair()
    document, _ = build_manifest(private_hex=private, version="0.7.1", channel="beta")
    calls: list[str] = []

    def fetch(url):
        calls.append(url)
        return manifest_bytes(document)

    made: list[UpdateSubsystem] = []

    def make() -> UpdateSubsystem:
        subsystem = UpdateSubsystem(feed=feed.load_feed_config(), trust=TrustedPublishers({"release-2026-01": public}),
                                    data_dir=home / "data", platform="macos-arm64", installed_version="0.7.0", fetch=fetch)
        made.append(subsystem)
        return subsystem

    consent = home / "data" / "update_v2" / "check_consent.json"
    consent.parent.mkdir(parents=True)
    consent.write_text(json.dumps({"enabled": True, "manifest_url": FEED_URL}))
    try:
        yield make, calls, consent, runtime
    finally:
        for subsystem in made:
            if subsystem.service is not None:
                subsystem.service.stop()
                if subsystem.service._thread is not None:
                    subsystem.service._thread.join(timeout=2)


def test_background_check_stays_offline_with_the_switch_off_even_after_an_earlier_check_press(updates, monkeypatch):
    make, calls, consent, _runtime = updates
    _set_switch(monkeypatch, False)
    subsystem = make()
    subsystem.start()
    time.sleep(0.5)
    assert subsystem.configured and consent.is_file()
    assert subsystem.service._thread is None, "the background check started with the outbound switch off"
    assert calls == []
    assert subsystem.status_payload()["online_access"] is False

    # Turning the switch on is what lets the same home check again: the earlier press still counts.
    _set_switch(monkeypatch, True)
    restarted = make()
    restarted.start()
    deadline = time.monotonic() + 3
    while not calls and time.monotonic() < deadline:
        time.sleep(0.05)
    assert calls == [FEED_URL]
    assert restarted.status_payload()["online_access"] is True


def test_switching_off_while_running_stops_the_next_scheduled_check(updates, monkeypatch):
    make, calls, _consent, _runtime = updates
    _set_switch(monkeypatch, True)
    subsystem = make()
    subsystem.service.interval = 0.2
    subsystem.start()
    deadline = time.monotonic() + 3
    while not calls and time.monotonic() < deadline:
        time.sleep(0.05)
    assert calls == [FEED_URL]
    _set_switch(monkeypatch, False)
    time.sleep(0.3)  # let a cycle that was already past its gate finish
    seen = len(calls)
    time.sleep(1.0)
    assert len(calls) == seen, "a scheduled check went online after the outbound switch was turned off"


def test_pressed_check_with_the_switch_off_sends_nothing_and_says_why(updates, monkeypatch):
    make, calls, consent, _runtime = updates
    consent.unlink()
    _set_switch(monkeypatch, False)
    subsystem = make()
    subsystem.start()
    answer = subsystem.trigger_check()
    assert calls == []
    assert answer["check_ok"] is False and answer["reason"] == "online_access_off"
    assert "Online access is off" in answer["detail"] and "Go online on its own" in answer["detail"]
    assert not consent.exists(), "a refused press must not record a check choice"
    assert subsystem.service._thread is None


def test_unreadable_switch_fails_closed(updates, monkeypatch):
    from core import policy_engine
    from core.updater.feed import automatic_checks_enabled

    make, calls, _consent, _runtime = updates

    def unreadable():
        raise ValueError("Policy file must contain a YAML mapping/object.")

    monkeypatch.setattr(policy_engine, "automatic_outbound_enabled", unreadable)
    subsystem = make()
    subsystem.start()
    assert not automatic_checks_enabled(feed=subsystem.feed, data_dir=subsystem.data_dir)
    assert subsystem.trigger_check()["reason"] == "online_access_off"
    assert subsystem.service._thread is None and calls == []


def test_served_check_route_answers_the_calm_refusal_and_runs_with_the_switch_on(updates, monkeypatch):
    from core.web.api.app import default_workspace_root
    from core.web.api.runtime import RuntimeServices
    from core.web.api.service import dispatch_get, dispatch_post

    make, calls, consent, runtime = updates
    consent.unlink()
    subsystem = make()
    monkeypatch.setattr(runtime, "_SUBSYSTEM", subsystem)

    def press():
        res = dispatch_post(path="/api/update/check", body={}, headers={}, runtime=RuntimeServices(display_name="N"),
                            model_name="vool", client_host="127.0.0.1", workspace_root_provider=default_workspace_root)
        return res.status, json.loads(res.body.decode("utf-8"))

    _set_switch(monkeypatch, False)
    status, payload = press()
    assert calls == []
    assert status == 409 and payload["ok"] is False and payload["reason"] == "online_access_off", payload
    assert "Online access is off" in payload["detail"]
    shown = dispatch_get(path="/api/update/status", query={}, runtime=RuntimeServices(display_name="N"),
                         model_name="vool", client_host="127.0.0.1")
    assert json.loads(shown.body.decode("utf-8"))["online_access"] is False

    _set_switch(monkeypatch, True)
    status, payload = press()
    assert calls == [FEED_URL]
    assert status == 200 and payload["check_ok"] is True, payload
