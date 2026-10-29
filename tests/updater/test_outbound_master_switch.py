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


@pytest.fixture
def dialled(monkeypatch):
    """Every URL the outbound door was about to dispatch (after every gate, just before urllib).

    The request is recorded and then refused right there as a network failure, so nothing leaves this computer:
    the door's own opener is not covered by the suite's urlopen fence."""
    from urllib.error import URLError

    from core import remote_fetch_policy

    urls: list[str] = []

    def record(url, *args, **kwargs):
        urls.append(str(url))
        raise URLError("refused at the outbound door by the test")

    monkeypatch.setattr(remote_fetch_policy, "note_remote_fetch_attempt", record)
    return urls


def test_cli_apply_with_the_switch_off_fetches_no_manifest(tmp_path, monkeypatch, capsys, dialled):
    from core import runtime_paths
    from installer import update_cli

    monkeypatch.setattr(runtime_paths, "_VOOL_HOME_OVERRIDE", tmp_path / "home")
    release_feed = "https://github.com/Parad0x-Labs/vool/releases/latest/download/manifest.json"
    args = update_cli.build_parser().parse_args([
        "apply", "--manifest-url", release_feed, "--app-path", str(tmp_path / "VOOL.app"), "--i-pressed-update",
        "--data-dir", str(tmp_path / "data"), "--platform", "macos-arm64"])
    _set_switch(monkeypatch, False)
    assert update_cli.cmd_apply(args) == 1
    assert dialled == [], "the apply path fetched the manifest with the outbound switch off"
    assert "Online access is off" in capsys.readouterr().out

    _set_switch(monkeypatch, True)
    update_cli.cmd_apply(args)  # the suite's network fence refuses it one layer below the door
    assert dialled == [release_feed]


def test_install_press_with_the_switch_off_downloads_nothing_and_restart_stays_local(updates, monkeypatch, dialled):
    from core.updater.service import UserGesture

    make, calls, _consent, _runtime = updates
    _set_switch(monkeypatch, True)
    subsystem = make()
    subsystem.start()
    assert subsystem.trigger_check()["check_ok"] and subsystem.service.current_offer() is not None
    _set_switch(monkeypatch, False)
    outcome = subsystem.press_install(UserGesture(pressed_at=time.time()))
    assert outcome.accepted is False and "did not download the update" in outcome.detail, outcome
    time.sleep(0.3)
    assert dialled == [] and subsystem._install_thread is None
    # Restart into an already downloaded update is local and keeps its own answer.
    restart = subsystem.press_restart(UserGesture(pressed_at=time.time()))
    assert "Online access is off" not in restart.detail


def test_vool_update_cli_reports_a_refusal_not_a_failed_check(updates, monkeypatch, capsys):
    from apps.vool_cli import cmd_update

    make, calls, consent, runtime = updates
    consent.unlink()
    _set_switch(monkeypatch, False)
    monkeypatch.setattr(runtime, "_SUBSYSTEM", make())
    assert cmd_update(json_mode=True) == 1
    out = capsys.readouterr().out
    answer = json.loads(out[out.index("{"):])
    assert answer["error"] == "online_access_off" and "Online access is off" in answer["detail"]
    assert cmd_update() == 1
    printed = capsys.readouterr().out
    assert "Online access is off" in printed and "didn't complete" not in printed
    assert calls == []


def test_scheduled_check_starts_when_the_switch_is_turned_on_after_boot(updates, monkeypatch):
    from core.web.api.runtime import RuntimeServices
    from core.web.api.service import dispatch_get

    make, calls, _consent, runtime = updates
    _set_switch(monkeypatch, False)
    subsystem = make()
    subsystem.start()
    monkeypatch.setattr(runtime, "_SUBSYSTEM", subsystem)

    def poll():
        return dispatch_get(path="/api/update/status", query={}, runtime=RuntimeServices(display_name="N"),
                            model_name="vool", client_host="127.0.0.1")

    poll()
    time.sleep(0.3)
    assert subsystem.service._thread is None and calls == []
    _set_switch(monkeypatch, True)
    poll()  # the chip's poll, no restart
    deadline = time.monotonic() + 3
    while not calls and time.monotonic() < deadline:
        time.sleep(0.05)
    assert calls == [FEED_URL]
    assert subsystem.service._thread is not None and subsystem.service._thread.is_alive()


@pytest.mark.parametrize("source", ["auto_check", "env_override"])
def test_shipped_auto_check_and_feed_override_stay_offline_with_the_switch_off(tmp_path, monkeypatch, source):
    from core.updater.feed import automatic_checks_enabled, load_feed_config

    config = tmp_path / "feed.json"
    if source == "auto_check":
        config.write_text(json.dumps({"manifest_url": FEED_URL, "auto_check": True}))
        feed = load_feed_config(config_path=config, env={})
    else:
        config.write_text("{}")
        feed = load_feed_config(config_path=config, env={"VOOL_UPDATE_MANIFEST_URL": FEED_URL})
    _set_switch(monkeypatch, True)
    assert automatic_checks_enabled(feed=feed, data_dir=tmp_path) is True
    _set_switch(monkeypatch, False)
    assert automatic_checks_enabled(feed=feed, data_dir=tmp_path) is False


def test_the_shipped_feed_checks_on_its_own_once_online_is_on_and_never_before(tmp_path, monkeypatch):
    """0.7.3 users never heard of 0.7.4: the shipped feed waited for a Check press nobody made. With the
    shipped configuration and a fresh home (no press on record), the switch alone decides."""
    from dataclasses import replace

    from core.updater.feed import load_feed_config
    from core.updater.runtime import UpdateSubsystem

    shipped = load_feed_config(env={})
    assert shipped.source == "config" and shipped.manifest_url.startswith("https://github.com/")
    private, public = generate_publisher_keypair()
    document, _ = build_manifest(private_hex=private, version="0.7.1", channel=shipped.channel)
    calls: list[str] = []

    def fetch(url):
        calls.append(url)
        return manifest_bytes(document)

    def make() -> UpdateSubsystem:
        subsystem = UpdateSubsystem(feed=replace(shipped, app_path=str(tmp_path / "VOOL.app")),
                                    trust=TrustedPublishers({"release-2026-01": public}), data_dir=tmp_path / "data",
                                    platform="macos-arm64", installed_version="0.7.0", fetch=fetch)
        return subsystem.start()

    _set_switch(monkeypatch, False)
    quiet = make()
    time.sleep(0.5)
    assert quiet.service._thread is None and calls == []
    assert quiet.status_payload()["online_access"] is False

    _set_switch(monkeypatch, True)
    checking = make()
    try:
        deadline = time.monotonic() + 3
        while not (checking.service.current_offer() and calls) and time.monotonic() < deadline:
            time.sleep(0.05)
        assert calls == [shipped.manifest_url]
        assert checking.status_payload()["phase"] == "ready" and checking.status_payload()["target_version"] == "0.7.1"
        assert not (tmp_path / "data" / "update_v2" / "check_consent.json").exists(), "no Check press was needed"
    finally:
        checking.service.stop()
        if checking.service._thread is not None:
            checking.service._thread.join(timeout=2)
