"""pa_beta_gate -- product completion: the macOS notification helper and the window host's bridge pump.

LABELLED: ``test_helper_app_compiles_signs_and_reads_the_macos_notification_settings`` compiles the Swift helper with
the system swiftc, signs it ad hoc and runs its selftest against the actual macOS notification center on this Mac. The
selftest only reads the app's notification settings: it never asks for permission and never posts. The pump tests use
a line-protocol helper double (a Python script speaking the helper's stdin/stdout protocol) and in-process fakes of
the served routes; they prove the relay, not macOS.
"""
from __future__ import annotations

import json
import os
import plistlib
import subprocess
import sys
import textwrap
import time

import pytest

pytestmark = [pytest.mark.pa_beta]

BUNDLE_ID = "com.parad0xlabs.vool.test.notifications"
AUTHORIZATION_NAMES = {"not_determined", "denied", "authorized", "provisional", "ephemeral"}


def test_helper_app_compiles_signs_and_reads_the_macos_notification_settings(tmp_path):
    if sys.platform != "darwin" or not os.access("/usr/bin/swiftc", os.X_OK):
        pytest.skip("needs macOS with the system Swift compiler")
    from core import notifications_macos

    app = notifications_macos.build_helper_app(tmp_path / notifications_macos.HELPER_APP_NAME, bundle_identifier=BUNDLE_ID)
    info = plistlib.loads((app / "Contents" / "Info.plist").read_bytes())
    assert info["CFBundleIdentifier"] == BUNDLE_ID and info["LSUIElement"] is True, info
    assert info["CFBundleExecutable"] == notifications_macos.HELPER_EXECUTABLE, info
    signature = subprocess.run(["/usr/bin/codesign", "-dv", str(app)], capture_output=True, text=True, timeout=60)
    assert f"Identifier={BUNDLE_ID}" in signature.stderr, signature.stderr

    completed = subprocess.run([str(notifications_macos.helper_executable(app)), "selftest"], capture_output=True, text=True, timeout=60)
    lines = completed.stdout.strip().splitlines()
    assert completed.returncode == 0 and lines, (completed.returncode, completed.stdout, completed.stderr)
    reply = json.loads(lines[-1])
    assert reply["ok"] is True and reply["bundle_identifier"] == BUNDLE_ID, reply
    assert reply["authorization"] in AUTHORIZATION_NAMES, reply


def test_helper_source_speaks_the_documented_notification_center_calls():
    """A contract on the embedded source; the compile-and-run test above is the behavioural proof on macOS."""
    from core import notifications_macos

    source = notifications_macos.swift_source()
    for call in ("UNUserNotificationCenter.current()", "requestAuthorization(options:", "getNotificationSettings",
                 "getPendingNotificationRequests", "getDeliveredNotifications", "removePendingNotificationRequests(withIdentifiers:",
                 "removeDeliveredNotifications(withIdentifiers:", "UNCalendarNotificationTrigger", "didReceive response",
                 "setNotificationCategories"):
        assert call in source, call
    assert "displayed" not in source, "the helper never reports display; macOS does not tell it"


class _FakeHelper:
    def __init__(self, events):
        self.sent, self.events, self.running = [], list(events), True

    def send(self, message):
        self.sent.append(message)

    def drain(self):
        events, self.events = self.events, []
        return events

    def alive(self):
        return self.running


def test_pump_relays_helper_events_to_the_runtime_and_the_outbox_to_the_helper():
    from installer.bundle.native_notifications import NotificationBridgePump

    helper = _FakeHelper([
        {"event": "settings", "authorization": "authorized"},
        {"event": "submitted", "identifier": "vool.n.ntf-1"},
        {"event": "response", "identifier": "vool.n.ntf-1", "action": "open"},
    ])
    reports, opened = [], []
    outbox = {"ok": True, "enabled": True, "want_authorization": False, "want_settings": False,
              "requests": [{"op": "submit", "identifier": "vool.s.rem-2.0", "kind": "scheduled", "deliver_at_utc": "2026-09-21T07:25:00+00:00"}]}

    def post_report(events):
        reports.append(list(events))
        return {"ok": True, "applied": 1, "opened": [{"notification_id": "ntf-1", "session_id": ""}]}

    pump = NotificationBridgePump(helper=helper, fetch_outbox=lambda: outbox, post_report=post_report, on_open=opened.append)
    result = pump.step()
    assert [event["event"] for event in reports[0]] == ["settings", "submitted", "response"], reports
    assert opened == [{"notification_id": "ntf-1", "session_id": ""}], opened
    [command] = helper.sent
    assert command["cmd"] == "apply" and command["requests"] == outbox["requests"] and command["authorize"] is False, command
    assert result["reported"] == 3 and result["handed"] == 1, result

    helper.events = []
    pump.step()
    assert len(reports) == 1, "no helper events, no report"


def test_pump_survives_an_unreachable_runtime_and_asks_for_permission_only_when_told():
    from installer.bundle.native_notifications import NotificationBridgePump

    helper = _FakeHelper([])

    def unreachable():
        raise OSError("connection refused")

    pump = NotificationBridgePump(helper=helper, fetch_outbox=unreachable, post_report=lambda events: {"ok": True})
    result = pump.step()
    assert result["error"] and helper.sent == [], result

    pump = NotificationBridgePump(helper=helper, fetch_outbox=lambda: {"ok": True, "enabled": True, "want_authorization": True,
                                                                        "want_settings": True, "requests": []},
                                  post_report=lambda events: {"ok": True})
    pump.step()
    [command] = helper.sent
    assert command["authorize"] is True and command["settings"] is True, command


_HELPER_DOUBLE = textwrap.dedent('''
    import json, sys
    print(json.dumps({"event": "settings", "authorization": "authorized"}), flush=True)
    for line in sys.stdin:
        message = json.loads(line)
        for request in message.get("requests", []):
            if request.get("op") == "submit":
                print(json.dumps({"event": "submitted", "identifier": request["identifier"]}), flush=True)
    print(json.dumps({"event": "stopping"}), flush=True)
''')


def test_helper_process_pipes_lines_both_ways_and_ends_when_the_host_closes_it(tmp_path):
    """The host's process wrapper against a helper double: commands go out on stdin, events come back on stdout, and
    closing stdin ends the helper -- it never outlives the window host."""
    from installer.bundle.native_notifications import HelperProcess

    script = tmp_path / "helper_double.py"
    script.write_text(_HELPER_DOUBLE, encoding="utf-8")
    process = HelperProcess([sys.executable, "-I", str(script)])
    process.start()
    try:
        process.send({"cmd": "apply", "requests": [{"op": "submit", "identifier": "vool.n.ntf-9"}]})
        seen = []
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline and not any(event.get("event") == "submitted" for event in seen):
            seen.extend(process.drain())
            time.sleep(0.05)
        assert {"event": "settings", "authorization": "authorized"} in seen, seen
        assert {"event": "submitted", "identifier": "vool.n.ntf-9"} in seen, seen
    finally:
        code = process.stop(timeout=5)
    assert code == 0 and not process.alive(), code
