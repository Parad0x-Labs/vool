"""Transaction approval is the owner's choice per wallet: Touch ID / Mac password, a wallet PIN of 4-8 digits, or a
wallet-only password -- and export stays device-only whatever the choice.

Operator decisions (2026-09-07). A device-approval wallet holds NO PIN-sealed copy at all: the seed is sealed only
under the Keychain user-presence unlock secret, so every approval is a Touch ID / password prompt at signing time.
A cancelled prompt is a counted refusal, never a failed payment: the proposal stays pending. Methods can be
switched without re-creating the wallet, after proving the current one.
"""
from __future__ import annotations

import json
import os
import urllib.request

import pytest

from tests.wallet._controlled_helper import install_for_runtime
from tests.wallet._device_auth_fake import FakeDeviceAuthority

DESTINATION = "9xQeWvG816bUx9EPjHmaT23yvVM2ZWbrrpZb9PusVFin"
PASSWORD = "correct horse battery"


@pytest.fixture
def fake_device(wallet_env):
    from core.wallet import device_auth

    fake = FakeDeviceAuthority()
    device_auth.set_device_authority_for_tests(fake)
    yield fake
    device_auth.set_device_authority_for_tests(None)


def _create(custody, **kw):
    return custody.create_pocket_wallet(acknowledged_warning=True, confirmation_phrase=custody.POCKET_CONFIRMATION_PHRASE, **kw).profile


def _drive(approver, proposal_id):
    from core.wallet import lifecycle

    engine = lifecycle.default_lifecycle()
    engine.prepare(proposal_id)
    return engine.approve_and_execute(proposal_id, approver=approver)


def _propose(proposals, profile):
    return proposals.propose_transaction(wallet_id=profile.wallet_id, destination=DESTINATION, amount_minor=1, asset="SOL", origin="user")


@pytest.mark.parametrize(("pin", "ok"), [("123", False), ("1234", True), ("12345678", True), ("123456789", False), ("12ab", False)])
def test_pin_policy_is_four_to_eight_digits(fake_device, pin, ok):
    from core.wallet import custody

    if ok:
        assert _create(custody, pin=pin).approval_method == custody.APPROVAL_PIN
    else:
        with pytest.raises(Exception) as exc:
            _create(custody, pin=pin)
        assert exc.value.code == "wallet_pin_invalid"


@pytest.mark.parametrize(("password", "ok"), [("short", False), ("1234567890123", False), (PASSWORD, True), ("x" * 129, False)])
def test_password_policy_is_ten_plus_characters_and_not_a_pin(fake_device, password, ok):
    from core.wallet import custody

    if ok:
        assert _create(custody, password=password, approval_method="password").approval_method == custody.APPROVAL_PASSWORD
    else:
        with pytest.raises(Exception) as exc:
            _create(custody, password=password, approval_method="password")
        assert exc.value.code == "wallet_password_invalid"


def test_password_wallet_approves_with_its_password_and_refuses_a_wrong_one(fake_device):
    from core.wallet import approval, custody, proposals
    from core.wallet.errors import WalletFault

    profile = _create(custody, password=PASSWORD, approval_method="password")
    p = _propose(proposals, profile)
    with pytest.raises(WalletFault) as exc:
        _drive(approval.PasswordApprover("wrong password here"), p.proposal_id)
    assert exc.value.code == "wallet_approval_rejected"
    assert proposals.get_proposal(p.proposal_id).state == proposals.STATE_PENDING_APPROVAL, "a wrong password is a counted attempt, not a failed payment"
    from core.wallet import lifecycle

    receipt = lifecycle.default_lifecycle().approve_and_execute(p.proposal_id, approver=approval.PasswordApprover(PASSWORD))
    assert receipt.state == proposals.STATE_CONFIRMED
    assert not custody.verify_secret(profile.wallet_id, "1234"), "a PIN never opens a password wallet"


def test_device_wallet_has_no_pin_seal_and_approves_through_the_prompt(fake_device):
    from core.wallet import approval, custody, proposals

    profile = _create(custody, approval_method="device")
    assert profile.approval_method == custody.APPROVAL_DEVICE
    assert custody.device_bound(profile.wallet_id) and custody.has_secret_seal(profile.wallet_id) is False
    assert custody.verify_secret(profile.wallet_id, "1234") is False
    p = _propose(proposals, profile)
    receipt = _drive(approval.DeviceApprover(), p.proposal_id)
    assert receipt.state == proposals.STATE_CONFIRMED
    assert len(fake_device.prompts) == 1 and "approve" in fake_device.prompts[0].lower()


def test_a_cancelled_prompt_keeps_the_proposal_pending_and_counts(fake_device):
    from core.wallet import approval, custody, proposals
    from core.wallet.errors import WalletFault

    profile = _create(custody, approval_method="device")
    p = _propose(proposals, profile)
    fake_device.deny_next = True
    with pytest.raises(WalletFault) as exc:
        _drive(approval.DeviceApprover(), p.proposal_id)
    assert exc.value.code == "wallet_device_auth_denied"
    assert proposals.get_proposal(p.proposal_id).state == proposals.STATE_PENDING_APPROVAL
    from core.wallet import lifecycle

    assert lifecycle.default_lifecycle().approve_and_execute(p.proposal_id, approver=approval.DeviceApprover()).state == proposals.STATE_CONFIRMED


def test_a_pin_approver_does_not_open_a_device_wallet(fake_device):
    from core.wallet import approval, custody, proposals
    from core.wallet.errors import WalletFault

    profile = _create(custody, approval_method="device")
    p = _propose(proposals, profile)
    with pytest.raises(WalletFault) as exc:
        _drive(approval.PinApprover("1234"), p.proposal_id)
    assert exc.value.code == "wallet_approval_rejected"


def test_device_wallet_creation_needs_device_authentication(wallet_env):
    from core.wallet import custody, device_auth

    device_auth.set_device_authority_for_tests(FakeDeviceAuthority(available=False))
    try:
        with pytest.raises(Exception) as exc:
            _create(custody, approval_method="device")
        assert exc.value.code == "wallet_device_auth_unavailable"
    finally:
        device_auth.set_device_authority_for_tests(None)


def test_switching_methods_reuses_the_same_key(fake_device):
    from core.wallet import approval, custody, proposals

    profile = _create(custody, pin="4321")
    changed = custody.change_approval_method(profile.wallet_id, new_method="password", new_secret=PASSWORD, current_secret="4321")
    assert changed.approval_method == "password" and changed.public_key == profile.public_key
    assert custody.verify_secret(profile.wallet_id, PASSWORD) and not custody.verify_secret(profile.wallet_id, "4321")
    p = _propose(proposals, custody.get_wallet(profile.wallet_id))
    assert _drive(approval.PasswordApprover(PASSWORD), p.proposal_id).state == proposals.STATE_CONFIRMED
    with pytest.raises(Exception) as exc:
        custody.change_approval_method(profile.wallet_id, new_method="pin", new_secret="5678", current_secret="not the password")
    assert exc.value.code == "wallet_pin_invalid"
    to_device = custody.change_approval_method(profile.wallet_id, new_method="device", current_secret=PASSWORD)
    assert to_device.approval_method == "device" and custody.has_secret_seal(profile.wallet_id) is False
    # export is device-only for every method
    exported = custody.export_private_key(profile.wallet_id, target="phantom")
    assert exported["address"] == profile.public_key


def test_the_status_names_each_wallets_approval_method(fake_device):
    from core.wallet import custody
    from core.wallet.status import wallet_status

    _create(custody, password=PASSWORD, approval_method="password")
    st = wallet_status()
    assert st["approval_method"] == "password"
    assert any(a["approval_method"] == "password" for a in st["accounts"])


def test_the_api_creates_and_approves_by_password_and_by_device(fake_device):
    from apps.vool_api_server import create_app
    from core.wallet import custody
    from core.web.api.runtime import RuntimeServices
    from tests.wallet.test_wallet_api import _post

    app = create_app(RuntimeServices(display_name="VOOL"))
    code, body = _post(app, "/api/wallet/pocket/create", {"acknowledged_warning": True, "confirmation_phrase": custody.POCKET_CONFIRMATION_PHRASE, "approval_method": "password", "password": PASSWORD})
    assert code == 200 and body["wallet"]["approval_method"] == "password", body
    code, prop = _post(app, "/api/wallet/propose", {"destination": DESTINATION, "amount_minor": 1, "asset": "SOL"})
    assert code == 200, prop
    code, body = _post(app, "/api/wallet/approve", {"proposal_id": prop["proposal"]["proposal_id"], "password": PASSWORD})
    assert code == 200 and body["receipt"]["state"] == "confirmed", body
    code, body = _post(app, "/api/wallet/pocket/create", {"acknowledged_warning": True, "confirmation_phrase": custody.POCKET_CONFIRMATION_PHRASE, "approval_method": "device"})
    assert code == 200 and body["wallet"]["approval_method"] == "device", body
    code, prop = _post(app, "/api/wallet/propose", {"destination": DESTINATION, "amount_minor": 1, "asset": "SOL"})
    code, body = _post(app, "/api/wallet/approve", {"proposal_id": prop["proposal"]["proposal_id"], "method": "device"})
    assert code == 200 and body["receipt"]["state"] == "confirmed", body
    code, body = _post(app, "/api/wallet/approval-method", {"wallet_id": prop["proposal"]["wallet_id"], "method": "pin", "new_pin": "2468"})
    assert code == 200 and body["wallet"]["approval_method"] == "pin", body


@pytest.mark.timeout(600)
def test_served_card_shows_the_device_button_and_approves_through_it(tmp_path):
    import tests._reader_served_rig as rig
    from core.wallet import custody
    from tests.served_browser import launch_chromium
    from tests.wallet._rig import ScriptedRpc

    with rig.CapturingProvider(default="ok") as provider, ScriptedRpc() as rpc:
        daemon = rig.ServedDaemon(tmp_path / "home", provider=provider, env_extra={"VOOL_WALLET_ENABLED": "1", "VOOL_WALLET_NETWORK_ENVIRONMENT": "testnet", "VOOL_WALLET_TESTNET_RPC_URL": rpc.url, "PLAYWRIGHT_BROWSERS_PATH": os.environ.get("PLAYWRIGHT_BROWSERS_PATH", "")})
        daemon.register_provider()
        controlled = install_for_runtime(tmp_path / "home")  # the runtime finds it where it compiles its own helper; no environment seam
        try:
            daemon.start()
        except RuntimeError as exc:
            daemon.stop()
            pytest.skip(f"served daemon could not boot here: {exc}")
        manager, browser = launch_chromium()
        try:
            def post(path, body):
                req = urllib.request.Request(daemon.base_url + path, data=json.dumps(body).encode(), headers={"Content-Type": "application/json"}, method="POST")
                with urllib.request.urlopen(req, timeout=60) as r: return json.loads(r.read().decode())
            post("/api/wallet/pocket/create", {"acknowledged_warning": True, "confirmation_phrase": custody.POCKET_CONFIRMATION_PHRASE, "approval_method": "device"})
            proposed = post("/api/wallet/propose", {"destination": DESTINATION, "amount_minor": 1, "asset": "SOL"})
            pid = proposed["proposal"]["proposal_id"]
            page = browser.new_page()
            page.goto(f"{daemon.base_url}/chat", wait_until="networkidle")
            card = f'.vw-card[data-proposal="{pid}"]'
            page.wait_for_selector(card + " .vw-approve-device", timeout=30000)
            assert page.query_selector(card + " input.vw-pin") is None, "a device wallet's card has no PIN box"
            page.click(card + " .vw-approve-device")
            page.wait_for_function(f"() => /confirmed/i.test((document.querySelector('{card} .vw-result') || {{}}).textContent || '')", timeout=60000)
            assert [c["argv"][1] for c in controlled.journal()] == ["store", "read"], "creation stored the unlock secret; the approval read it back through the real helper"
        finally:
            browser.close()
            manager.stop()
            daemon.stop()
