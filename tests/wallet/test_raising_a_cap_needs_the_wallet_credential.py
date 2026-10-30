"""Raising a wallet spending cap needs the wallet's own approval credential; lowering one never does.

Red-team M1 (0.7.7 train, 2026-10-30): the per-launch wallet capability is unbound unless a native host sets it,
and a request with no Origin passes the caller gate, so any process on this machine (curl to loopback) could
POST /api/wallet/limits and lift a 0.1 SOL per-transfer cap to 5000 SOL with no PIN. Approval still applied,
but the cap layer was gone. A raise is now proven with the same secret a payment approval needs: a Crypto
Pilot wallet's PIN through its unlock throttle, an older pocket wallet's PIN or password, or the device prompt.
"""
from __future__ import annotations

import json
import uuid
from typing import Any

import pytest

from tests.asgi_harness import asgi_request
from tests.wallet._device_auth_fake import FakeDeviceAuthority

pytestmark = [pytest.mark.safety]

PIN = "482913"
PASSWORD = "orbit-lantern-42"
LOOPBACK_NO_ORIGIN = {"Host": "127.0.0.1", "Content-Type": "application/json"}  # what curl sends
SAME_ORIGIN = {"Host": "127.0.0.1:11435", "Origin": "http://127.0.0.1:11435", "Content-Type": "application/json"}
HUGE = 5_000_000_000_000  # 5000 SOL in lamports


@pytest.fixture
def app(wallet_env, monkeypatch):
    monkeypatch.delenv("VOOL_WALLET_UI_CAPABILITY_SHA256", raising=False)
    from apps.vool_api_server import create_app
    from core.web.api import wallet_api
    from core.web.api.runtime import RuntimeServices

    wallet_api.reset_caller_binding_for_tests()
    yield create_app(RuntimeServices(display_name="VOOL"))
    wallet_api.reset_caller_binding_for_tests()


@pytest.fixture
def fake_device(wallet_env):
    from core.wallet import device_auth

    fake = FakeDeviceAuthority()
    device_auth.set_device_authority_for_tests(fake)
    yield fake
    device_auth.set_device_authority_for_tests(None)


def _post(app, body: dict[str, Any], headers: dict[str, str] = LOOPBACK_NO_ORIGIN) -> tuple[int, dict[str, Any]]:
    status, _headers, raw = asgi_request(app, method="POST", path="/api/wallet/limits", headers=headers, body=json.dumps(body).encode())
    return status, json.loads(raw or b"{}")


def _stored(wallet_id: str) -> tuple[int, int, int]:
    from core.wallet import limits

    got = limits.load_limits(wallet_id, "SOL")
    return got.per_tx_minor, got.daily_minor, got.per_destination_daily_minor


def _pocket(**kw) -> str:
    from core.wallet import custody

    return custody.create_pocket_wallet(acknowledged_warning=True, confirmation_phrase=custody.POCKET_CONFIRMATION_PHRASE, **kw).profile.wallet_id


def _pilot(method: str = "pin", credential: str = PIN) -> str:
    from core.wallet import pilot_custody

    view = pilot_custody.create_pilot_wallet(network="solana:EtWTRABZaYq6iMfeYKouRu166VU2xqa1", method=method, credential=credential, credential_confirmation=credential,
                                             creation_key=f"caps-{uuid.uuid4().hex}")
    revealed = pilot_custody.reveal_pilot_backup(view["wallet_id"], credential=credential)
    pilot_custody.acknowledge_pilot_backup(view["wallet_id"], ack_token=revealed["ack_token"])
    return view["wallet_id"]


WALLETS = {
    "pilot_pin": (lambda: _pilot(), PIN),
    "pocket_pin": (lambda: _pocket(pin=PIN), PIN),
    "pocket_password": (lambda: _pocket(password=PASSWORD, approval_method="password"), PASSWORD),
}


@pytest.mark.parametrize("kind", sorted(WALLETS))
@pytest.mark.parametrize("headers", [LOOPBACK_NO_ORIGIN, SAME_ORIGIN], ids=["curl", "same_origin"])
def test_a_raise_without_the_credential_is_refused_and_nothing_is_stored(app, fake_device, kind, headers):
    make, _secret = WALLETS[kind]
    wallet_id = make()
    before = _stored(wallet_id)
    status, answer = _post(app, {"wallet_id": wallet_id, "asset": "SOL", "per_tx_minor": HUGE}, headers)
    assert status == 400 and answer["error"] in {"wallet_pin_invalid", "wallet_password_invalid"}, answer
    assert answer["fault"]["context"]["reason"] == "credential_required"
    assert _stored(wallet_id) == before


@pytest.mark.parametrize("kind", sorted(WALLETS))
def test_a_wrong_credential_is_refused_and_nothing_is_stored(app, fake_device, kind):
    make, secret = WALLETS[kind]
    wallet_id = make()
    before = _stored(wallet_id)
    wrong = "000000" if secret == PIN else "wrong-password-99"
    status, answer = _post(app, {"wallet_id": wallet_id, "asset": "SOL", "daily_minor": HUGE, "credential": wrong})
    assert status == 400 and answer["error"] in {"wallet_pin_invalid", "wallet_password_invalid"}, answer
    assert _stored(wallet_id) == before


@pytest.mark.parametrize("kind", sorted(WALLETS))
def test_the_right_credential_raises_the_cap(app, fake_device, kind):
    make, secret = WALLETS[kind]
    wallet_id = make()
    status, answer = _post(app, {"wallet_id": wallet_id, "asset": "SOL", "per_tx_minor": HUGE, "credential": secret})
    assert status == 200, answer
    assert _stored(wallet_id)[0] == HUGE


def test_a_device_approval_wallet_raises_only_after_the_device_prompt(app, fake_device):
    wallet_id = _pocket(approval_method="device")
    before = _stored(wallet_id)
    fake_device.deny_next = True
    status, answer = _post(app, {"wallet_id": wallet_id, "asset": "SOL", "per_destination_daily_minor": HUGE})
    assert status == 403 and answer["error"] == "wallet_device_auth_denied", answer
    assert _stored(wallet_id) == before
    prompts = len(fake_device.prompts)
    status, answer = _post(app, {"wallet_id": wallet_id, "asset": "SOL", "per_destination_daily_minor": HUGE})
    assert status == 200, answer
    assert len(fake_device.prompts) == prompts + 1 and _stored(wallet_id)[2] == HUGE


@pytest.mark.parametrize("kind", sorted(WALLETS))
def test_lowering_a_cap_needs_no_credential(app, fake_device, kind):
    make, _secret = WALLETS[kind]
    wallet_id = make()
    per_tx, daily, per_dest = _stored(wallet_id)
    status, answer = _post(app, {"wallet_id": wallet_id, "asset": "SOL", "per_tx_minor": per_tx // 2, "daily_minor": daily, "per_destination_daily_minor": 0})
    assert status == 200, answer
    assert _stored(wallet_id) == (per_tx // 2, daily, 0)


def test_lowering_one_cap_cannot_carry_a_raise_of_another(app, fake_device):
    wallet_id = _pilot()
    before = _stored(wallet_id)
    status, answer = _post(app, {"wallet_id": wallet_id, "asset": "SOL", "per_tx_minor": 1, "daily_minor": HUGE})
    assert status == 400 and answer["fault"]["context"]["reason"] == "credential_required", answer
    assert _stored(wallet_id) == before


def test_the_red_team_repro_a_5000_sol_payment_stays_over_the_cap(app, fake_device):
    """The red-team's own repro: 5000 SOL refused at the shipped per-transfer cap, then admitted after a PIN-less raise.
    Now the PIN-less raise is refused, so the same check still refuses."""
    from core.wallet import limits

    wallet_id = _pilot()
    payee = "9xQeWvG816bUx9EPjHmaT23yvVM2ZWbrrpZb9PusVFin"
    assert not limits.check_limits(wallet_id, "SOL", HUGE, payee).ok
    status, _answer = _post(app, {"wallet_id": wallet_id, "asset": "SOL", "per_tx_minor": HUGE, "daily_minor": HUGE, "per_destination_daily_minor": HUGE})
    assert status == 400
    assert not limits.check_limits(wallet_id, "SOL", HUGE, payee).ok


# --- independent review 2026-10-30 ----------------------------------------------------------------------

def test_guessing_an_older_pocket_wallets_pin_here_hits_the_durable_throttle(app, fake_device):
    """The older pocket wallet's seal counts nothing itself; this door must not be a free PIN oracle."""
    from core.wallet import pilot_custody

    wallet_id = _pocket(pin=PIN)
    before = _stored(wallet_id)
    answers = []
    for guess in range(pilot_custody.WALLET_LOCK_AFTER + 1):
        status, answer = _post(app, {"wallet_id": wallet_id, "asset": "SOL", "per_tx_minor": HUGE, "credential": f"{guess:06d}"})
        answers.append(answer["error"])
    assert answers[-1] == "wallet_unlock_throttled", answers
    # locked: even the right PIN waits, and nothing was stored
    status, answer = _post(app, {"wallet_id": wallet_id, "asset": "SOL", "per_tx_minor": HUGE, "credential": PIN})
    assert answer["error"] == "wallet_unlock_throttled" and _stored(wallet_id) == before


def test_a_write_checked_against_a_stale_read_cannot_undo_a_lowered_cap(app, fake_device, monkeypatch):
    """Read caps, owner lowers one, write the old caps back: against what is stored that write is a raise."""
    from core.wallet import limits

    wallet_id = _pilot()
    high = limits.load_limits(wallet_id, "SOL")
    limits.set_limits(wallet_id, "SOL", limits.SpendLimits(1, 1, 1))  # the owner's emergency lowering
    monkeypatch.setattr(limits, "load_limits", lambda *_a, **_k: high)  # the door's first read predates it
    status, answer = _post(app, {"wallet_id": wallet_id, "asset": "SOL"})
    assert status == 400 and answer["fault"]["context"]["reason"] == "credential_required", answer
    monkeypatch.undo()
    assert _stored(wallet_id) == (1, 1, 1)


@pytest.mark.parametrize("value", ["abc", 1e400, None, [1]])
def test_a_cap_that_is_not_a_whole_number_is_a_plain_refusal(app, fake_device, value):
    wallet_id = _pilot()
    before = _stored(wallet_id)
    raw = json.dumps({"wallet_id": wallet_id, "asset": "SOL", "per_tx_minor": value}).replace("Infinity", "1e400")
    status, _headers, body = asgi_request(app, method="POST", path="/api/wallet/limits", headers=LOOPBACK_NO_ORIGIN, body=raw.encode())
    assert status == 400 and json.loads(body)["error"] == "limits_invalid", body
    assert _stored(wallet_id) == before


def test_the_approval_method_door_is_no_free_pin_oracle_either(app, fake_device):
    """Delta review: changing the approval method checked the current PIN with an uncounted unseal."""
    from core.wallet import pilot_custody

    wallet_id = _pocket(pin=PIN)
    answers = []
    for guess in range(pilot_custody.WALLET_LOCK_AFTER + 1):
        status, _h, raw = asgi_request(app, method="POST", path="/api/wallet/approval-method", headers=LOOPBACK_NO_ORIGIN,
                                       body=json.dumps({"wallet_id": wallet_id, "method": "pin", "new_pin": "135792", "pin": f"{guess:06d}"}).encode())
        answers.append(json.loads(raw)["error"])
    assert answers[-1] == "wallet_unlock_throttled", answers
    status, _h, raw = asgi_request(app, method="POST", path="/api/wallet/approval-method", headers=LOOPBACK_NO_ORIGIN,
                                   body=json.dumps({"wallet_id": wallet_id, "method": "pin", "new_pin": "135792", "pin": PIN}).encode())
    assert json.loads(raw)["error"] == "wallet_unlock_throttled"
