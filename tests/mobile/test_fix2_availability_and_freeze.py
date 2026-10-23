"""FIX-2: junk cannot lock the paired phone out; getting safer never depends on the legacy policy file."""
from __future__ import annotations

import json
import socket
import threading
import time
import urllib.request
from urllib.error import HTTPError

import pytest

from tests.mobile import test_sealed_transport as st
from tests.mobile.test_phone_listener_served import desktop as desktop
from tests.mobile.test_phone_listener_served import http
from tests.mobile.test_zz_secprobe import LAN, _pair
from tests.mobile.test_zz_secprobe import lan as lan

_JUNK = {"protocol": "vool.mobile.sealed.v2", "device_id": "phone:" + "f" * 20, "nonce": "00" * 24, "ciphertext": ""}


def _me(phone, pairing):
    _phone, key, shared, claim = pairing
    wire = st.action(key, shared, claim, "devices.me")
    status, response = http(phone, "/api/mobile/companion", wire)
    return status, st.opened(response, shared, wire)


def test_junk_beyond_every_old_cap_leaves_the_paired_phone_served(lan, desktop):
    base, runtime, home, value = lan
    pairing = _pair(desktop, value, activate_chat=False)
    phone = pairing[0]
    for _ in range(700):  # past the old 240-per-source and 600-global admission caps
        assert http(phone, "/api/mobile/companion", _JUNK)[0] == 401
    status, body = _me(phone, pairing)
    assert status == 200 and body.get("ok") is not False, (status, body)


def test_junk_through_the_relay_leaves_relayed_phones_served(lan, desktop):
    from core.mobile_companion.sealing import dispatch

    base, runtime, home, value = lan
    pairing = _pair(desktop, value, activate_chat=False)
    _phone, key, shared, claim = pairing
    for _ in range(300):  # one relay room is one "source"
        assert dispatch("/api/mobile/companion", dict(_JUNK), {}, runtime=runtime, model_name="vool",
                        client_host="relay").status == 401
    wire = st.action(key, shared, claim, "devices.me")
    response = dispatch("/api/mobile/companion", wire, {}, runtime=runtime, model_name="vool", client_host="relay")
    assert response.status == 200, response.body


def test_slow_drip_from_several_addresses_cannot_hold_every_slot(lan):
    base, runtime, home, value = lan
    stop = threading.Event()
    socks = []
    for source in ("127.0.0.2", "127.0.0.3", "127.0.0.4"):
        for _ in range(4):
            s = socket.create_connection((LAN, value["port"]), source_address=(source, 0))
            s.sendall(b"GET /api/mobile/info HTTP/1.1\r\n")
            socks.append(s)

    def drip():
        while not stop.is_set():
            for s in socks:
                try:
                    s.sendall(b"X")  # one byte resets a per-read timeout, never the deadline
                except OSError:
                    pass
            time.sleep(1)

    dripper = threading.Thread(target=drip, daemon=True)
    dripper.start()
    try:
        time.sleep(0.5)
        from http.client import HTTPConnection

        conn = HTTPConnection(LAN, value["port"], source_address=("127.0.0.1", 0), timeout=5)
        conn.request("GET", "/api/mobile/info")
        response = conn.getresponse()
        status = response.status
        response.read()
        conn.close()
    finally:
        stop.set()
        dripper.join(timeout=3)
        for s in socks:
            s.close()
    assert status == 200


def test_request_deadline_cuts_a_sender_that_never_finishes(lan):
    base, runtime, home, value = lan
    s = socket.create_connection((LAN, value["port"]))
    s.sendall(b"GET /api/mobile/info HTTP/1.1\r\n")
    started = time.monotonic()
    s.settimeout(1)
    closed = False
    while time.monotonic() - started < 12 and not closed:
        try:
            s.sendall(b"X")
            closed = s.recv(1) == b""
        except TimeoutError:
            continue
        except OSError:
            closed = True
    s.close()
    assert closed and time.monotonic() - started < 9


def _corrupt_legacy_policy():
    from core.wallet_spend_policy_store import policy_path

    path = policy_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("{not a signed policy")


def test_phone_freeze_holds_when_the_legacy_policy_is_unreadable(lan, desktop):
    from core.wallet import limits

    base, runtime, home, value = lan
    _phone, key, shared, claim = pairing = _pair(desktop, value, activate_chat=False)
    _corrupt_legacy_policy()
    wire = st.action(key, shared, claim, "wallet.freeze")
    status, response = http(pairing[0], "/api/mobile/companion", wire)
    assert status == 200, st.opened(response, shared, wire)
    assert limits.is_frozen() is True
    rows = http(base, "/api/mobile/receipts?limit=200")[1]["receipts"]
    frozen = [row for row in rows if row["kind"] == "payments.frozen"]
    assert frozen and frozen[-1]["detail"].get("legacy_mirror") == "not_updated"


def test_lost_computer_revokes_every_phone_even_when_the_legacy_policy_is_unreadable(lan, desktop):
    from core.wallet import limits

    base, runtime, home, value = lan
    a = _pair(desktop, value, activate_chat=False)
    _pair(desktop, value, activate_chat=False)
    _corrupt_legacy_policy()
    wire = st.action(a[1], a[2], a[3], "devices.lost_computer")
    status, _response = http(a[0], "/api/mobile/companion", wire)
    assert status == 200
    settings = http(base, "/api/mobile/settings")[1]
    assert limits.is_frozen() is True
    assert all(device["status"] != "active" for device in settings["devices"])


def test_settings_freeze_holds_when_the_legacy_policy_is_unreadable(desktop):
    from core.wallet import limits

    base, runtime, home = desktop
    _corrupt_legacy_policy()
    status, body = http(base, "/api/mobile/settings", {"operation": "freeze"})
    assert status == 200 and body["frozen"] is True and limits.is_frozen() is True


def test_refused_unfreeze_and_refused_caller_leave_receipts(desktop):
    base, runtime, home = desktop
    assert http(base, "/api/mobile/settings", {"operation": "freeze"})[0] == 200
    status, body = http(base, "/api/mobile/settings", {"operation": "unfreeze"})
    assert status == 403
    from core.web.api.mobile_companion_api import handle_mobile_companion_post

    refused = handle_mobile_companion_post("/api/mobile/settings", {"operation": "unfreeze"}, None,
                                           runtime=runtime, model_name="", client_host="127.0.0.1")
    assert refused.status == 403 and json.loads(refused.body)["code"] == "wallet_caller_refused"
    rows = http(base, "/api/mobile/receipts?limit=200")[1]["receipts"]
    kinds = {row["kind"] for row in rows}
    assert "payments.freeze_change_refused" in kinds
    assert "security.settings_caller_refused" in kinds


@pytest.mark.parametrize("path,body", [("/api/mobile/devices/revoke", {"device_id": "phone:x"})])
def test_a_page_on_another_localhost_port_cannot_revoke(desktop, path, body):
    base, runtime, home = desktop
    request = urllib.request.Request(base + path, data=json.dumps(body).encode(),
                                     headers={"Content-Type": "text/plain", "Origin": "http://127.0.0.1:9999"})
    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            result = (response.status, json.loads(response.read()).get("code"))
    except HTTPError as error:
        result = (error.code, json.loads(error.read() or b"{}").get("code"))
    assert result == (403, "owner_caller_required")


def test_cli_and_brake_say_the_wallet_pin_is_needed(desktop, capsys):
    from apps import vool_cli
    from core import vool_agent_brake

    base, runtime, home = desktop
    assert http(base, "/api/mobile/settings", {"operation": "freeze"})[0] == 200
    import core.os_consent_gate as gate

    original = gate.require_os_user_consent
    gate.require_os_user_consent = lambda *_a, **_k: True
    try:
        assert vool_cli.cmd_spend_unfreeze() == 1
        assert "wallet PIN" in capsys.readouterr().out
        reply = vool_agent_brake.maybe_handle_agent_brake("/startx402", "s1", consent_fn=lambda _r: True)
    finally:
        gate.require_os_user_consent = original
    from core.wallet import limits

    assert limits.is_frozen() is True
    assert reply is not None and "wallet PIN" in json.dumps(reply, default=str)
