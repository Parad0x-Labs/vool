"""A phone app and VOOL on the computer from different releases never fail quietly.

Each side carries one number for the language it speaks (core/mobile_companion/compat.py and
mobile/src/protocol/compat.ts). Older sides send nothing and count as 1. These checks run on the
real served listener: the pairing code, the claim, every request, and the Settings page data.
"""

from __future__ import annotations

import hashlib
import hmac
import secrets
from datetime import datetime, timezone

from tests.mobile.test_phone_listener_served import desktop as desktop  # noqa: F401  (fixture)
from tests.mobile.test_phone_listener_served import enable, http
from tests.mobile.test_sealed_transport import canonical, opened, pair


def _wire(key, shared, claim, name="devices.me", params=None, **extra):
    from core.device_link.protocol import request_signature_payload
    from core.mobile_companion.sealing import VERSION, seal

    params = params or {}
    nonce = secrets.token_hex(16)
    timestamp = datetime.now(timezone.utc).isoformat()
    proof = {"kind": "vool.mobile.request.v1", "device_id": claim["device_id"], "action": name,
             "params_sha256": hashlib.sha256(canonical(params)).hexdigest(), "nonce": nonce,
             "timestamp": timestamp, "grant_id": claim["grant_id"]}
    payload = request_signature_payload(grant_id=claim["grant_id"], verb="vool.action",
                                        params={"action": name, **params}, nonce=nonce, timestamp=timestamp)
    body = {"device_id": claim["device_id"], "grant_id": claim["grant_id"], "action": name, "params": params,
            "nonce": nonce, "timestamp": timestamp, "device_signature": key.sign(canonical(proof)).signature.hex(),
            "signature": hmac.new(claim["grant_secret"].encode(), canonical(payload), hashlib.sha256).hexdigest(), **extra}
    return {**seal({"kind": VERSION + ":request", "body": body}, shared, "request"), "device_id": claim["device_id"]}


def _call(phone, key, shared, claim, name="devices.me", params=None, **extra):
    wire = _wire(key, shared, claim, name, params, **extra)
    status, response = http(phone, "/api/mobile/companion", wire)
    return status, opened(response, shared, wire)


def _device(base, device_id):
    status, settings = http(base, "/api/mobile/settings")
    assert status == 200
    return next(d for d in settings["devices"] if d["device_id"] == device_id)


def test_the_pairing_code_and_every_answer_carry_the_computers_numbers(desktop):
    from core.mobile_companion import compat

    base, runtime, _home = desktop
    enable(runtime)
    status, start = http(base, "/api/mobile/pairing/start", {})
    assert status == 200 and start["qr_uri"].endswith(f"&api={compat.COMPANION_API}&minapp={compat.MIN_APP_API}")
    phone, key, shared, claim = pair(desktop)
    assert claim["compat"] == compat.describe()
    status, body = _call(phone, key, shared, claim, "devices.me", app_api=compat.NEWEST_APP_API)
    assert status == 200 and body["result"]["compat"] == compat.describe()
    status, body = _call(phone, key, shared, claim, "status.snapshot", app_api=compat.NEWEST_APP_API)
    assert status == 200 and body["result"]["compat"] == compat.describe()


def test_settings_says_when_a_phone_runs_an_older_app_and_stops_saying_it_after_the_update(desktop):
    from core.mobile_companion import compat

    base, _runtime, _home = desktop
    phone, key, shared, claim = pair(desktop)
    assert _call(phone, key, shared, claim)[0] == 200  # an app from before the check sends no number
    row = _device(base, claim["device_id"])
    assert row["app_api"] == 1 and row["app_update_available"] is (compat.NEWEST_APP_API > 1)
    assert _call(phone, key, shared, claim, app_api=compat.NEWEST_APP_API)[0] == 200
    row = _device(base, claim["device_id"])
    assert row["app_api"] == compat.NEWEST_APP_API and row["app_update_available"] is False
    # A made-up number is never read as a newer app.
    assert _call(phone, key, shared, claim, app_api="999")[0] == 200
    assert _device(base, claim["device_id"])["app_api"] == 1


def test_an_app_too_old_for_this_computer_is_told_to_update_instead_of_failing(desktop, monkeypatch):
    from core.mobile_companion import compat

    phone, key, shared, claim = pair(desktop)
    monkeypatch.setattr(compat, "MIN_APP_API", 3)
    status, body = _call(phone, key, shared, claim, "chats.list")
    assert status == 426 and body["code"] == "app_update_required"
    status, body = _call(phone, key, shared, claim, "chats.list", app_api=3)
    assert status == 200, body


def test_an_app_too_old_is_told_before_its_pairing_code_is_used_up(desktop, monkeypatch):
    from core.mobile_companion import compat

    monkeypatch.setattr(compat, "MIN_APP_API", 3)
    try:
        pair(desktop)
    except AssertionError as refused:
        assert "426" in str(refused)
    else:
        raise AssertionError("an app below the minimum was paired")
    monkeypatch.setattr(compat, "MIN_APP_API", 1)
    pair(desktop)  # the computer still pairs a current app after the refusal


def test_a_request_this_computer_does_not_know_reads_as_update_your_computer(desktop):
    phone, key, shared, claim = pair(desktop)
    status, body = _call(phone, key, shared, claim, "future.thing", app_api=99)
    assert status == 403 and body["code"] == "action_not_offered"
