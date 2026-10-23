"""Phone wallet views and proposals on real served doors; local chain only, never submit."""
from __future__ import annotations

import json
import secrets
import time

import pytest

from tests.mobile.test_phone_listener_served import desktop as desktop
from tests.mobile.test_phone_listener_served import http
from tests.mobile.test_phone_notifications import call as raw_call
from tests.mobile.test_sealed_transport import pair
from tests.wallet._rig import MAINNET_GENESIS, ScriptedRpc


def call(*args):
    phone, key, shared, claim, action, *extra = args
    params = dict(extra[0]) if extra else {}
    if action == "wallet.propose" and params:
        code, value = raw_call(phone, key, shared, claim, "wallet.preview", params)
        if code == 200:
            params["review_token"] = value["result"]["review_token"]
            params["warnings_acknowledged"] = value["result"]["warnings"]
    return raw_call(phone, key, shared, claim, action, params)


PIN = "482913"


@pytest.fixture
def money(desktop, monkeypatch):
    from core.wallet import caller_binding, chains

    monkeypatch.setenv("VOOL_WALLET_ENABLED", "1")
    monkeypatch.setenv("VOOL_WALLET_X402_ALLOW_LOOPBACK", "1")
    monkeypatch.delenv("VOOL_WALLET_NETWORK_ENVIRONMENT", raising=False)
    caller_binding.reset_for_tests()
    chains.invalidate_chain_identity()
    base = desktop[0]
    with ScriptedRpc(genesis_hash=MAINNET_GENESIS) as rpc:
        monkeypatch.setenv("VOOL_WALLET_RPC_URLS", json.dumps({chains.SOLANA_MAINNET: rpc.url}))
        status, created = http(base, "/api/wallet/setup/create", {"network": chains.SOLANA_MAINNET,
            "method": "pin", "credential": PIN, "credential_confirmation": PIN, "creation_key": secrets.token_hex(16)})
        assert status == 200, created
        wallet_id = created["setup"]["wallet_id"]
        status, revealed = http(base, "/api/wallet/setup/reveal", {"wallet_id": wallet_id, "credential": PIN})
        assert status == 200
        ack = revealed["backup"]["ack_token"]
        revealed = None
        status, ready = http(base, "/api/wallet/setup/acknowledge", {"wallet_id": wallet_id, "ack_token": ack})
        assert status == 200, ready
        phone = pair(desktop, activate_chat=False)
        cap = {"wallet_id": wallet_id, "network": chains.SOLANA_MAINNET, "asset": "SOL",
               "per_payment_minor": 10_000_000, "daily_minor": 15_000_000}
        yield desktop, phone, wallet_id, cap, rpc
        assert rpc.send_count() == 0
    chains.invalidate_chain_identity()


def grant(money, **extra):
    desktop, phone, _, cap, _ = money
    return http(desktop[0], "/api/mobile/settings", {"operation": "device_rules", "device_id": phone[3]["device_id"],
        "rules": {"chat": True, "wallet_view": True, "payment_propose": True}, "credential": PIN, "payment_cap": cap, **extra})


def test_phone_defaults_and_owner_rights_are_not_phone_writable(money):
    _, phone, _, cap, _ = money
    assert not any(call(*phone, "devices.me")[1]["result"]["rules"].values())
    for action in ("wallet.status", "wallet.pending", "wallet.propose", "wallet.approve", "chats.list", "attachments.upload"):
        status, result = call(*phone, action, {"rules": {"payment_propose": True}, "payment_cap": cap})
        assert status == 403 and result["code"] == "phone_right_off_or_expired"
    assert grant(money)[0] == 200
    assert call(*phone, "wallet.status")[0] == 200
    assert call(*phone, "wallet.approve", {"pin": PIN})[0] == 403
    status, result = call(*phone, "devices.switch_off", {"rights": ["payment_propose"]})
    assert status == 200 and result["result"]["rules"]["payment_propose"] is False
    assert call(*phone, "wallet.propose", {})[0] == 403
    assert call(*phone, "devices.switch_off", {"rights": ["payment_propose"], "enable": True})[1]["result"]["rules"]["payment_propose"] is False


def test_wrong_pc_pin_uses_the_wallet_throttle_and_limits_cannot_expand(money):
    from core.wallet import custody, limits

    desktop, phone, wallet_id, cap, _ = money
    status, result = grant(money, credential="111111")
    assert status == 403 and result["code"] in {"wallet_pin_invalid", "wallet_password_invalid"}
    assert call(*phone, "wallet.status")[0] == 403
    from core.wallet.store import connection

    with connection() as conn:
        attempts = conn.execute("SELECT failures FROM wallet_unlock_attempts WHERE scope=?", ("wallet:"+wallet_id,)).fetchone()
    assert attempts and attempts[0] == 1
    assert custody.seal_policy(wallet_id) == custody.PILOT_SEAL_POLICY
    wide = {**cap, "per_payment_minor": limits.load_limits(wallet_id, "SOL").per_tx_minor}
    assert grant(money, payment_cap=wide)[0] == 400
    assert grant(money)[0] == 200
    assert grant(money, right_expiry={"payment_propose": time.time()-1})[0] == 400
    assert http(desktop[0], "/api/mobile/settings", {"operation": "device_rules", "device_id": phone[3]["device_id"],
        "rules": {"payment_approve": True}, "credential": PIN})[0] == 400


def test_mainnet_proposal_is_once_capped_and_declined_by_its_phone(money):
    from core.vool_wallet import b58encode
    from core.wallet import proposals

    _, phone, _, _, _ = money
    assert grant(money)[0] == 200
    params = {"destination": b58encode(bytes([71])*32), "amount_minor": 9_000_000, "idempotency_key": secrets.token_hex(16)}
    status, first = call(*phone, "wallet.propose", params)
    assert status == 200, first
    proposal_id = first["result"]["proposal"]["proposal_id"]
    status, retry = call(*phone, "wallet.propose", params)
    assert status == 200 and retry["result"]["proposal"]["proposal_id"] == proposal_id
    assert call(*phone, "wallet.propose", {**params, "idempotency_key": secrets.token_hex(16)})[0] == 403
    assert call(*phone, "wallet.propose", {**params, "amount_minor": 11_000_000, "idempotency_key": secrets.token_hex(16)})[0] == 403
    status, declined = call(*phone, "wallet.decline", {"proposal_id": proposal_id})
    assert status == 200, declined
    assert proposals.get_proposal(proposal_id).state == proposals.STATE_REJECTED
    # The wallet may supersede terminal offers for a fresh owner request. A
    # phone's uncertain retry keeps its original intent and cannot undo a refusal.
    assert call(*phone, "wallet.propose", params)[0] == 403
    assert len(proposals.list_proposals()) == 1


def test_freeze_persists_and_lost_computer_revokes_all_phones(money):
    from core.wallet import limits
    from core.web.api.mobile_companion_api import mobile_companion_service, reset_mobile_companion_service

    desktop, phone, _, _, _ = money
    other = pair(desktop)
    assert call(*phone, "wallet.freeze", {"frozen": False})[1]["result"]["frozen"] is True
    assert limits.is_frozen()
    reset_mobile_companion_service()
    assert limits.is_frozen()
    assert call(*phone, "devices.lost_computer")[0] == 200
    assert call(*other, "devices.me")[0] == 401
    assert all(d["status"] == "revoked" for d in mobile_companion_service().store.devices().values())


def test_two_concurrent_requests_share_one_daily_allowance(money):
    from concurrent.futures import ThreadPoolExecutor

    from core.vool_wallet import b58encode

    _, phone, _, _, _ = money
    assert grant(money)[0] == 200
    def request(_):
        return call(*phone, "wallet.propose", {"destination": b58encode(bytes([72])*32),
            "amount_minor": 9_000_000, "idempotency_key": secrets.token_hex(16)})[0]
    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sorted(pool.map(request, range(2))) == [200, 403]


@pytest.mark.parametrize("change", ["revoke", "cap", "expire", "freeze", "master_off"])
def test_pc_approval_rechecks_phone_controls_before_any_signer(money, monkeypatch, change):
    from core.vool_wallet import b58encode
    from core.wallet import approval, limits, signers
    from core.web.api.mobile_companion_api import mobile_companion_service, revoke_device

    desktop, phone, _, cap, _ = money
    assert grant(money)[0] == 200
    params = {"destination": b58encode(bytes([73])*32), "amount_minor": 8_000_000, "idempotency_key": secrets.token_hex(16)}
    status, result = call(*phone, "wallet.propose", params)
    assert status == 200, result
    proposal_id = result["result"]["proposal"]["proposal_id"]
    status, quote = http(desktop[0], "/api/wallet/quote", {"proposal_id": proposal_id})
    assert status == 200, quote
    original = approval.PinApprover.approve
    def asked(self, challenge):
        service = mobile_companion_service()
        if change == "revoke":
            revoke_device(service, phone[3]["device_id"])
        elif change == "cap":
            service.store.mark_device(phone[3]["device_id"], payment_cap={**cap, "per_payment_minor": 1_000_000})
        elif change == "expire":
            service.store.mark_device(phone[3]["device_id"], right_expiry={"payment_propose": time.time()-1})
        elif change == "master_off":
            assert http(desktop[0], "/api/mobile/settings", {"enabled": False})[0] == 200
        else:
            limits.set_frozen(True)
        return original(self, challenge)
    monkeypatch.setattr(approval.PinApprover, "approve", asked)
    def must_not_sign(*_args, **_kw):
        pytest.fail("A signer was reached after this phone's approval authority changed")
    # A safety fuse, not the positive behaviour under test: the real claim must
    # refuse before opening a signer or emitting any transaction bytes.
    monkeypatch.setattr(signers, "signer_for", must_not_sign)
    status, refused = http(desktop[0], "/api/wallet/approve", {"proposal_id": proposal_id,
        "quote_id": quote["quote"]["quote_id"], "quote_digest": quote["quote"]["digest"], "pin": PIN})
    assert status == 403 and refused["error"] in {"wallet_approval_rejected", "wallet_limit_exceeded"}, refused


def test_owner_controls_work_in_chromium_and_use_exact_human_amounts(money):
    import os
    from pathlib import Path

    from playwright.sync_api import sync_playwright

    desktop, phone, _, _, _ = money
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(executable_path=os.environ["VOOL_TEST_CHROMIUM"])
        try:
            page = browser.new_page(viewport={"width": 1280, "height": 1000})
            page.goto(desktop[0] + "/settings#phone")
            page.locator('[data-group="phone"]').click()
            row = page.locator('[data-device="'+phone[3]["device_id"]+'"]')
            row.locator('[data-right="wallet_view"]').check()
            row.locator('[data-right="payment_propose"]').check()
            assert row.locator('[data-right="wallet_view"]').is_checked()
            assert row.locator('[data-right="payment_approve"]').is_disabled()
            fields = row.locator('.phone-cap input')
            fields.nth(0).fill("0.01")
            fields.nth(1).fill("0.015")
            row.locator('input[type="password"]').fill(PIN)
            assert row.locator('[data-right="wallet_view"]').is_checked()
            assert row.locator('[data-right="payment_propose"]').is_checked()
            with page.expect_response(lambda response: response.url.endswith('/api/mobile/settings') and response.request.method == 'POST') as saved_response:
                row.get_by_role('button', name='Save permissions').click()
            answer = saved_response.value.json()
            assert saved_response.value.status == 200, answer
            assert answer['rules']['wallet_view'] is True and answer['rules']['payment_propose'] is True, {
                'request_rules': saved_response.value.request.post_data_json['rules'], 'saved_rules': answer['rules']}
            page.wait_for_function("document.querySelector('.phone-device input[data-right=payment_propose]').checked")
            saved = http(desktop[0], "/api/mobile/settings")[1]["devices"]
            assert call(*phone, "wallet.status")[0] == 200, [(d["rules"], d["right_expiry"]) for d in saved]
            shots = Path(os.environ["VOOL_PHONE_SHOTS_DIR"])
            page.screenshot(path=str(shots / 'phone-owner-permissions.png'), full_page=True)
            page.get_by_role('button', name='Freeze payments', exact=True).click()
            page.get_by_role('button', name='Unfreeze payments', exact=True).wait_for()
            page.get_by_role('button', name='Unfreeze payments', exact=True).click()
            form = page.locator('.phone-unfreeze')
            form.locator('input[type="password"]').fill(PIN)
            form.get_by_role('button', name='Unfreeze payments').click()
            page.get_by_role('button', name='Freeze payments', exact=True).wait_for()
        finally:
            browser.close()
    from core.vool_wallet import b58encode

    status, exact = call(*phone, "wallet.propose", {"destination": b58encode(bytes([74])*32), "amount": "0.009",
        "idempotency_key": secrets.token_hex(16)})
    assert status == 200 and exact['result']['proposal']['amount_minor'] == 9_000_000, exact
    assert exact['result']['display_amount'] == '0.009'


def test_payment_refusal_is_durable_even_with_alerts_off_and_preset_off(money):
    from core.vool_wallet import b58encode

    _, phone, _, _, _ = money
    assert grant(money)[0] == 200
    assert call(*phone, "notifications.preferences.set", {"preset": "off"})[0] == 200
    status, result = call(*phone, "wallet.propose", {"destination": b58encode(bytes([75])*32), "amount": "0.009",
        "idempotency_key": secrets.token_hex(16)})
    assert status == 200, result
    proposal_id = result["result"]["proposal"]["proposal_id"]
    assert call(*phone, "wallet.decline", {"proposal_id": proposal_id})[0] == 200
    status, polled = call(*phone, "notifications.poll")
    assert status == 200, polled
    events = polled["result"]["events"]
    assert [e["event_type"] for e in events] == ["payment_refused"]
    assert polled["result"]["badge"] == 1 and events[0]["os_notification_allowed"] is False
    assert "detail" not in events[0] and events[0]["target"] == {"screen": "wallet", "id": proposal_id}
    cursor = polled["result"]["next_after"]
    assert call(*phone, "notifications.poll", {"after": cursor})[1]["result"]["events"] == []
    assert call(*phone, "notifications.feed")[1]["result"]["badge"] == 1
    assert call(*phone, "notifications.read")[1]["result"]["badge"] == 0


def test_shared_payment_draft_keeps_identity_after_a_lost_committed_reply(money):
    import subprocess
    from pathlib import Path

    from nacl.signing import VerifyKey

    from core.vool_wallet import b58encode
    from core.wallet import proposals

    _, phone, _, _, _ = money
    assert grant(money)[0] == 200
    url, key, _, claim = phone
    credentials = {"baseUrl": url, "deviceId": claim["device_id"], "grantId": claim["grant_id"], "grantSecret": claim["grant_secret"],
        "deviceSeedHex": key.encode().hex(), "desktopFingerprint": claim["desktop_fingerprint"], "pairedAt": "2026-10-20T00:00:00Z",
        "protocol": "vool.mobile.sealed.v2",
        "desktopEncryptionKey": VerifyKey(bytes.fromhex(claim["desktop_public_key"])).to_curve25519_public_key().encode().hex()}
    result = subprocess.run(["node", "e2e/payment-journey.mjs"], cwd=Path(__file__).resolve().parents[2] / "mobile",
        input=json.dumps({"credentials": credentials, "destination": b58encode(bytes([76])*32)}), capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stdout + result.stderr
    proof = json.loads(result.stdout)
    assert proof["attempts"] == 4
    assert len(proposals.list_proposals(state=proposals.STATE_PENDING_APPROVAL)) == 1
    assert proposals.get_proposal(proof["proposal_id"]).amount_minor == 9_000_000


def test_wallet_contact_lookup_exposes_only_payment_endpoints(money):
    from core.contacts import store
    from core.vool_wallet import b58encode
    from core.wallet import chains

    _, phone, _, _, _ = money
    assert grant(money)[0] == 200
    store.create_contact(display_name="Payment Recipient", actor=store.ACTOR_OWNER, notes="Private contact note",
        endpoints=[{"kind": "wallet", "value": b58encode(bytes([77])*32), "chain_network": chains.SOLANA_MAINNET},
                   {"kind": "email", "value": "fixture@example.invalid"}])
    status, answer = call(*phone, "wallet.contacts", {"search": "Payment"})
    assert status == 200, answer
    body = json.dumps(answer)
    assert "Private contact note" not in body and "fixture@example.invalid" not in body
    assert answer["result"]["contacts"][0]["display_name"] == "Payment Recipient"


def test_an_old_unapproved_request_still_reserves_its_phone_allowance(money):
    from core.vool_wallet import b58encode
    from core.wallet.store import connection

    _, phone, _, _, _ = money
    assert grant(money)[0] == 200
    params = {"destination": b58encode(bytes([78])*32), "amount": "0.009", "idempotency_key": secrets.token_hex(16)}
    status, first = call(*phone, "wallet.propose", params)
    assert status == 200
    proposal_id = first["result"]["proposal"]["proposal_id"]
    with connection() as conn:
        conn.execute("UPDATE mobile_payment_claims SET created_at=? WHERE proposal_id=?", (time.time()-2*86400, proposal_id))
    status, refused = call(*phone, "wallet.propose", {**params, "idempotency_key": secrets.token_hex(16)})
    assert status == 403, refused
    assert call(*phone, "wallet.decline", {"proposal_id": proposal_id})[0] == 200
    assert call(*phone, "wallet.propose", {**params, "idempotency_key": secrets.token_hex(16)})[0] == 200


def test_pc_payment_right_pin_attempts_lock_at_the_wallets_existing_threshold(money):
    from core.wallet import pilot_custody

    _, phone, _, _, _ = money
    for _ in range(pilot_custody.WALLET_LOCK_AFTER):
        status, refused = grant(money, credential="111111")
        assert status == 403 and refused["code"] == "wallet_pin_invalid", refused
    status, refused = grant(money)
    assert status == 403 and refused["code"] == "wallet_unlock_throttled", refused
    assert call(*phone, "wallet.status")[0] == 403


def test_nonfinite_permission_expiry_is_refused_and_stored_corruption_fails_closed(money):
    from core.mobile_companion.policy import enabled

    assert grant(money)[0] == 200
    for value in (float('nan'), float('inf'), -float('inf')):
        assert grant(money, right_expiry={'payment_propose': value})[0] == 400
        assert not enabled({'rules': {'payment_propose': True}, 'right_expiry': {'payment_propose': value}}, 'payment_propose')
    assert not enabled({'rules': {'payment_propose': True}, 'right_expiry': {'payment_propose': 'later'}}, 'payment_propose')


def test_receipt_projection_preserves_exact_amount_and_phone_origin(money):
    from core.vool_wallet import b58encode

    _, phone, _, _, _ = money
    assert grant(money)[0] == 200
    status, result = call(*phone, 'wallet.propose', {'destination': b58encode(bytes([73])*32),
        'amount': '0.009', 'idempotency_key': secrets.token_hex(16)})
    assert status == 200, result
    proposal_id = result['result']['proposal']['proposal_id']
    assert call(*phone, 'wallet.decline', {'proposal_id': proposal_id})[0] == 200
    status, result = call(*phone, 'wallet.receipts')
    assert status == 200, result
    row = next(r for r in result['result']['receipts'] if r['proposal_id'] == proposal_id)
    assert row['amount_minor'] == '9000000'
    assert row['display_amount'] == '0.009'
    assert row['via'] == 'mobile' and row['device_id'] == phone[3]['device_id']


def test_payment_record_and_badge_do_not_force_banners_when_noise_is_muted(money, monkeypatch):
    from datetime import datetime, timezone

    from core.mobile_companion import notifications
    from tests.mobile.test_phone_notifications import record
    desktop,phone,_,_,_=money
    assert grant(money,rules={'wallet_view':True,'payment_propose':True,'alerts':True})[0]==200
    assert call(*phone,'notifications.preferences.set',{'preset':'off'})[0]==200
    record('payment_done')
    status,response=call(*phone,'notifications.poll',{'after':0})
    first=response['result'];assert status==200 and first['badge']==1
    assert first['events'][0]['os_notification_allowed'] is False
    assert call(*phone,'notifications.preferences.set',{'types':{'payment_done':{'enabled':True}}})[0]==200
    record('payment_done')
    second=call(*phone,'notifications.poll',{'after':first['next_after']})[1]['result']
    assert second['events'][0]['os_notification_allowed'] is True and second['badge']==2
    monkeypatch.setattr(notifications,'_now',lambda:datetime(2026,10,20,23,30,tzinfo=timezone.utc))
    assert call(*phone,'notifications.preferences.set',{'quiet':{'enabled':True,'start':'22:00','end':'07:00','payments_through':False}})[0]==200
    record('payment_done')
    third=call(*phone,'notifications.poll',{'after':second['next_after']})[1]['result']
    assert third['badge']==3 and len(third['events'])==1
    assert third['events'][0]['os_notification_allowed'] is False


def test_missed_terminal_payment_is_not_lost_after_more_than_one_day_offline(money):
    from datetime import datetime, timedelta, timezone

    from core.vool_wallet import b58encode
    from core.wallet.store import connection
    from core.web.api.mobile_companion_api import mobile_companion_service
    _,phone,_,_,_=money
    assert grant(money)[0]==200
    mobile_companion_service().store.mark_device(phone[3]['device_id'],paired_at=(datetime.now(timezone.utc)-timedelta(days=3)).isoformat())
    status,result=call(*phone,'wallet.propose',{'destination':b58encode(bytes([82])*32),'amount':'0.008','idempotency_key':secrets.token_hex(16)})
    assert status==200,result
    proposal_id=result['result']['proposal']['proposal_id']
    assert call(*phone,'wallet.decline',{'proposal_id':proposal_id})[1]['result']['cancelled']
    # Advance the fixture's persisted timestamps, not the payment's real state:
    # this is a genuine canonical decline whose notification was never polled.
    old=(datetime.now(timezone.utc)-timedelta(days=2)).isoformat()
    with connection() as conn:
        conn.execute('UPDATE wallet_proposals SET created_at=?,updated_at=? WHERE proposal_id=?',(old,old,proposal_id))
    status,result=call(*phone,'notifications.poll',{'after':0})
    assert status==200
    events=result['result']['events']
    assert any(e['event_type']=='payment_refused' and e['target']['id']==proposal_id for e in events)
    assert result['result']['badge']==1


def test_failed_wallet_read_is_not_recorded_as_a_failed_payment(money):
    from core.web.api.mobile_companion_api import mobile_companion_service
    _,phone,_,_,_=money
    assert grant(money)[0]==200
    assert call(*phone,'wallet.balances',{'wallet_id':'missing-account'})[0]==403
    status,result=call(*phone,'notifications.poll',{'after':0})
    assert status==200 and result['result']['events']==[] and result['result']['badge']==0
    kinds=[row['kind'] for row in mobile_companion_service().store.receipts()[0]]
    assert 'wallet.view_refused' in kinds and 'payment.refused' not in kinds
