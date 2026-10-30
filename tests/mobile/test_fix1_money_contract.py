"""Served fee, recovery and bounded-right regressions using local chain nodes."""
import json
import secrets
import time

import pytest

from tests.mobile.test_phone_listener_served import desktop, http
from tests.mobile.test_phone_notifications import call
from tests.mobile.test_sealed_transport import pair
from tests.mobile.test_zz_money_probe import PIN, propose, quote_and_approve, rig


def test_positive_fee_reservation_counts_toward_daily_limit(rig):
    from core.wallet.store import connection
    base, phone, _, _, rpc, _ = rig
    status, value, _ = propose(phone, 81, 7_000_000)
    assert status == 200, value
    pid = value['result']['proposal']['proposal_id']
    status, value = quote_and_approve(base, pid)
    assert status == 200, value
    with connection() as conn:
        fee = conn.execute('SELECT fee_minor FROM mobile_payment_claims WHERE proposal_id=?', (pid,)).fetchone()[0]
    assert fee == 5000
    # The next principal fits exactly; the already reserved fee makes it exceed 15M.
    assert propose(phone, 82, 8_000_000)[0] == 403
    assert rpc.send_count() == 1


@pytest.mark.parametrize('expiry', [0, 31 * 86400])
def test_payment_right_cannot_be_permanent_or_longer_than_thirty_days(rig, expiry):
    base, phone, _, cap, _, _ = rig
    status, value = http(base, '/api/mobile/settings', {
        'operation':'device_rules','device_id':phone[3]['device_id'],
        'rules':{'payment_propose':True}, 'credential':PIN, 'payment_cap':cap,
        'right_expiry':{'payment_propose': time.time() + expiry if expiry else 0},
    })
    assert status == 400, value


def test_phone_freeze_mirrors_and_legacy_resume_refuses_without_pin(rig):
    from core.vool_agent_brake import maybe_handle_agent_brake
    from core.wallet import limits
    from core.wallet.errors import WalletFault
    from core.wallet_spend_policy_store import load_policy_and_ledger, set_frozen_mirrored
    base, phone, wid, _, _, _ = rig
    assert call(*phone, 'wallet.freeze')[0] == 200
    assert limits.is_frozen() and load_policy_and_ledger(None, allow_missing_policy=True)[0].frozen
    # CLI uses this same mirror authority; it cannot lift a protected freeze.
    with pytest.raises(WalletFault):
        set_frozen_mirrored(False)
    result = maybe_handle_agent_brake('/startx402', 'local-recovery', wallet_fn=None, consent_fn=lambda _: True)
    assert not result['success']
    assert limits.is_frozen() and load_policy_and_ledger(None, allow_missing_policy=True)[0].frozen
    assert http(base, '/api/mobile/settings', {'operation':'unfreeze','wallet_id':wid,'credential':'000000'})[0] == 403
    assert limits.is_frozen()
    assert http(base, '/api/mobile/settings', {'operation':'unfreeze','wallet_id':wid,'credential':PIN})[0] == 200
    assert not limits.is_frozen() and not load_policy_and_ledger(None, allow_missing_policy=True)[0].frozen


@pytest.mark.parametrize('boundary', [True, False])
def test_evm_maximum_gas_is_reserved_or_refused_at_pc_claim(desktop, monkeypatch, boundary):
    from core.wallet import caller_binding, chains
    from core.wallet.store import connection
    from tests.wallet._rig_evm_native import ScriptedEvmNativeChain
    monkeypatch.setenv('VOOL_WALLET_ENABLED','1')
    monkeypatch.setenv('VOOL_WALLET_X402_ALLOW_LOOPBACK','1')
    monkeypatch.delenv('VOOL_WALLET_NETWORK_ENVIRONMENT', raising=False)
    caller_binding.reset_for_tests(); chains.invalidate_chain_identity()
    base = desktop[0]; network = 'eip155:1'
    try:
        with ScriptedEvmNativeChain(chain_id=1) as rpc:
            monkeypatch.setenv('VOOL_WALLET_RPC_URLS', json.dumps({network:rpc.url}))
            status, value = http(base,'/api/wallet/setup/create',{'network':network,'method':'pin','credential':PIN,'credential_confirmation':PIN,'creation_key':secrets.token_hex(16)})
            assert status == 200, value
            wid = value['setup']['wallet_id']; rpc.fund(value['setup']['address'],10**18)
            status, reveal = http(base,'/api/wallet/setup/reveal',{'wallet_id':wid,'credential':PIN}); assert status == 200
            assert http(base,'/api/wallet/setup/acknowledge',{'wallet_id':wid,'ack_token':reveal['backup']['ack_token']})[0] == 200
            phone = pair(desktop)
            cap = {'wallet_id':wid,'network':network,'asset':'ETH','per_payment_minor':5*10**15,'daily_minor':8*10**15}
            assert http(base,'/api/mobile/settings',{'operation':'device_rules','device_id':phone[3]['device_id'],'rules':{'payment_propose':True},'payment_cap':cap,'credential':PIN})[0] == 200
            params = {'destination':'0x'+'5'*40,'amount_minor':cap['per_payment_minor']-1000 if boundary else 10**15,'idempotency_key':secrets.token_hex(16)}
            status, review = call(*phone,'wallet.preview',params); assert status == 200, review
            status, value = call(*phone,'wallet.propose',{**params,'review_token':review['result']['review_token'],'warnings_acknowledged':review['result']['warnings']}); assert status == 200, value
            pid = value['result']['proposal']['proposal_id']
            status, quote = http(base,'/api/wallet/quote',{'proposal_id':pid}); assert status == 200, quote
            maximum = quote['quote']['fields']['fee_max_minor']; assert maximum > 0
            status, value = http(base,'/api/wallet/approve',{'proposal_id':pid,'quote_id':quote['quote']['quote_id'],'quote_digest':quote['quote']['digest'],'pin':PIN})
            if boundary:
                assert status == 403 and not rpc.sent, value
            else:
                assert status == 200 and len(rpc.sent) == 1, value
                with connection() as conn:
                    assert conn.execute('SELECT fee_minor FROM mobile_payment_claims WHERE proposal_id=?',(pid,)).fetchone()[0] == maximum
    finally:
        chains.invalidate_chain_identity()


def test_unknown_network_fee_is_refused_without_submission(rig):
    base, phone, _, _, rpc, _ = rig
    rpc.fee_for_message = None
    status, value, _ = propose(phone, 83, 1_000_000)
    if status == 200:
        pid = value['result']['proposal']['proposal_id']
        assert quote_and_approve(base, pid)[0] != 200
    assert rpc.send_count() == 0


def test_unknown_pc_payment_blocks_phone_duplicate_until_canonical_reconciliation(rig, monkeypatch):
    from core.vool_wallet import b58encode
    from core.wallet import lifecycle, settlement

    base, phone, wid, cap, rpc, _ = rig
    monkeypatch.setattr(lifecycle, '_CONFIRM_BUDGET_SECONDS', 0.5)
    rpc.send_mode = 'accept_then_500'; rpc.status_mode = 'none'
    status, value = http(base, '/api/wallet/propose', {
        'wallet_id':wid,'network':cap['network'],'asset':'SOL',
        'destination':b58encode(bytes([78])*32),'amount_minor':6_000_000,'idempotency_key':secrets.token_hex(16),
    })
    assert status == 200, value
    pid = value['proposal']['proposal_id']
    assert quote_and_approve(base,pid)[0] == 200
    assert propose(phone,78,6_000_000)[0] == 403
    # Only the real observer's positive chain evidence releases the duplicate guard.
    rpc.status_mode = 'confirmed'; rpc.send_mode = 'ok'
    assert settlement.observe_once(pid)['state'] == 'confirmed'
    assert propose(phone,78,6_000_000)[0] == 200
    assert rpc.send_count() == 1
