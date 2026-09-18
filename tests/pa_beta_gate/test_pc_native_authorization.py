"""pa_beta_gate -- product completion: asking macOS for notification permission, once, and showing its answer.

Seen on this Mac in the native smoke (product-completion/evidence/native-smoke-01.json): with the macOS channel on and the
helper reporting ``not_determined`` for twelve minutes, the runtime kept answering ``want_authorization`` and the bridge
asked macOS again on every five-second cycle, while the helper dropped whatever macOS answered. Nobody could tell a
prompt waiting on screen from a request macOS refused to show.

The runtime now asks once per request: the helper reports ``authorization_requested`` when it asks and
``authorization_result`` (granted, and macOS's error domain, code and text) when macOS answers. An unanswered request is
not repeated for ten minutes; an answered one is not repeated until the person presses the button again. Settings shows
the answer and the way forward.

LABELLED: a scripted bridge client calls the served routes the way the window host does and posts the reports the helper
would make; the helper's own reporting is checked on its embedded source here and on this Mac in the native smoke.
"""
from __future__ import annotations

import pytest

from ._pc_calendar_rig import T0, Clock, api_get, api_post, prepare_home

pytestmark = [pytest.mark.pa_beta]

ALLOWED = {"alert": "enabled", "sound": "enabled", "notification_center": "enabled", "lock_screen": "enabled", "previews": "always",
           "alert_style": "banner"}
NOT_REGISTERED = {"alert": "not_supported", "sound": "not_supported", "notification_center": "not_supported",
                  "lock_screen": "not_supported", "previews": "always", "alert_style": "none"}


@pytest.fixture
def home(tmp_path, monkeypatch):
    return prepare_home(tmp_path, monkeypatch)


class _Bridge:
    """LABELLED scripted bridge client (see the module docstring)."""

    def outbox(self):
        status, payload = api_post("/api/notifications/native/outbox", {"bridge_id": "bridge-under-test", "helper_version": "test"})
        assert status == 200 and payload["ok"], payload
        return payload

    def report(self, *events):
        status, payload = api_post("/api/notifications/native/report", {"bridge_id": "bridge-under-test", "events": list(events)})
        assert status == 200 and payload["ok"], payload
        return payload

    def settings(self, authorization, extra):
        return self.report({"event": "settings", "authorization": authorization, **extra})


def _enable_native():
    status, saved = api_post("/api/notifications/preferences", {"preferences": {"native_notifications": True}})
    assert status == 200 and saved["ok"], saved


def _native_status():
    status, view = api_get("/api/notifications/native/status")
    assert status == 200 and view["ok"], view
    return view


def test_a_request_is_asked_once_and_the_answer_macos_gives_is_shown(home, monkeypatch):
    """ORIGINAL: the smoke's situation. The bridge asks once; while macOS has not answered, the runtime does not ask again;
    macOS's refusal is recorded with its error and a way forward; only the person's button press asks again."""
    clock = Clock(T0, monkeypatch)
    _enable_native()
    bridge = _Bridge()
    bridge.settings("not_determined", NOT_REGISTERED)
    assert bridge.outbox()["want_authorization"] is True
    bridge.report({"event": "authorization_requested"})
    assert bridge.outbox()["want_authorization"] is False, "a request macOS has not answered is not repeated"
    clock.advance(minutes=3)
    assert bridge.outbox()["want_authorization"] is False, "not repeated while macOS may still be showing its prompt"
    assert "prompt" in _native_status()["recovery"], "while waiting, Settings says macOS is asking"

    bridge.report({"event": "authorization_result", "granted": False, "error_domain": "UNErrorDomain", "error_code": 1,
                   "detail": "Notifications are not allowed for this application"},
                  {"event": "settings", "authorization": "not_determined", **NOT_REGISTERED})
    view = _native_status()
    answer = view["authorization_request"]
    assert (answer["granted"], answer["error_domain"], answer["error_code"]) == (False, "UNErrorDomain", 1), view
    assert "not allowed" in answer["detail"] and view["want_authorization"] is False, view
    assert "System Settings" in view["recovery"], view
    assert bridge.outbox()["want_authorization"] is False, "macOS answered; VOOL does not ask again on its own"

    status, asked = api_post("/api/notifications/native/authorize", {})
    assert status == 200 and asked["ok"], asked
    assert bridge.outbox()["want_authorization"] is True, "the person pressed the button: macOS is asked again"


def test_an_allowed_prompt_unblocks_sending_and_an_abandoned_request_is_asked_again_once(home, monkeypatch):
    """NOVEL: a request the helper never got an answer to (it quit with the prompt open) is asked again after ten minutes,
    once. When the person allows VOOL, the answer and the settings arrive and a test notification goes to macOS."""
    clock = Clock(T0, monkeypatch)
    _enable_native()
    bridge = _Bridge()
    bridge.settings("not_determined", NOT_REGISTERED)
    assert bridge.outbox()["want_authorization"] is True
    bridge.report({"event": "authorization_requested"})
    clock.advance(minutes=11)
    assert bridge.outbox()["want_authorization"] is True, "an unanswered request older than ten minutes is asked again"
    bridge.report({"event": "authorization_requested"})
    assert bridge.outbox()["want_authorization"] is False, "and then not again"

    bridge.report({"event": "authorization_result", "granted": True}, {"event": "settings", "authorization": "authorized", **ALLOWED})
    view = _native_status()
    assert view["authorization"] == "authorized" and view["authorization_request"]["granted"] is True and view["recovery"] == "", view
    status, sent = api_post("/api/notifications/native/test", {})
    assert status == 200 and sent["ok"], sent
    [request] = bridge.outbox()["requests"]
    assert (request["op"], request["title"]) == ("submit", "VOOL test notification"), request


def test_denied_permission_points_to_system_settings_and_is_never_asked_again(home, monkeypatch):
    """CONTROL: once the person denied VOOL, macOS shows no prompt again; the runtime never asks, and Settings names the one
    place that can change it."""
    clock = Clock(T0, monkeypatch)
    _enable_native()
    bridge = _Bridge()
    bridge.settings("denied", NOT_REGISTERED)
    for _cycle in range(3):
        assert bridge.outbox()["want_authorization"] is False
        clock.advance(minutes=15)
    view = _native_status()
    assert view["authorization"] == "denied" and "System Settings" in view["recovery"], view


def test_helper_reports_that_it_asked_and_what_macos_answered():
    """A contract on the embedded Swift source; the native smoke records the events on this Mac."""
    from core import notifications_macos

    source = notifications_macos.swift_source()
    for fragment in ('"authorization_requested"', '"authorization_result"', '"granted"', '"error_domain"', '"error_code"'):
        assert fragment in source, fragment
