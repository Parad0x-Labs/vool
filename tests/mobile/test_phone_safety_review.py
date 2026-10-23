"""A proposal cannot skip or reuse another exact-intent review."""
import secrets
import time

from tests.mobile.test_phone_listener_served import desktop as desktop
from tests.mobile.test_phone_listener_served import http
from tests.mobile.test_phone_notifications import call as raw
from tests.mobile.test_phone_payments import grant
from tests.mobile.test_phone_payments import money as money


def test_review_is_exact_expires_and_never_approves(money):
    from core.vool_wallet import b58encode
    from core.wallet import proposals
    from core.web.api.mobile_companion_api import mobile_companion_service
    desktop, phone, _, _, rpc = money
    assert grant(money)[0] == 200
    params = {'destination': b58encode(bytes([84])*32), 'amount':'0.009', 'idempotency_key':secrets.token_hex(16)}
    assert raw(*phone, 'wallet.propose', params)[0] == 403
    assert not proposals.list_proposals()
    status, preview = raw(*phone, 'wallet.preview', params)
    assert status == 200, preview
    facts = preview['result']; assert set(facts['warnings']) == {'new_payee','unusual_amount'}
    assert facts['destination'] == params['destination'] and facts['amount'] == '0.009' and facts['fees'] == 'review_on_computer'
    confirmed = {**params, 'review_token': facts['review_token'], 'warnings_acknowledged':facts['warnings']}
    for changed in [{'amount':'0.008'}, {'destination':b58encode(bytes([85])*32)}, {'idempotency_key':secrets.token_hex(16)}, {'review_token':'wrong'}]:
        assert raw(*phone, 'wallet.propose', {**confirmed, **changed})[0] == 403
        assert not proposals.list_proposals()
    service = mobile_companion_service(); device = service.store.device(phone[3]['device_id'])
    reviews = device['payment_reviews']; reviews[params['idempotency_key']]['expires_at'] = time.time()-1
    service.store.mark_device(device['device_id'],payment_reviews=reviews)
    assert raw(*phone,'wallet.propose',confirmed)[0] == 403
    status, refreshed = raw(*phone,'wallet.preview',params); assert status == 200
    confirmed['review_token'] = refreshed['result']['review_token']
    status, result = raw(*phone,'wallet.propose',confirmed);assert status == 200,result
    proposal_id = result['result']['proposal']['proposal_id']
    assert raw(*phone,'wallet.propose',confirmed)[1]['result']['proposal']['proposal_id'] == proposal_id
    assert len(proposals.list_proposals()) == 1
    assert proposals.get_proposal(proposal_id).state == proposals.STATE_PENDING_APPROVAL
    assert raw(*phone,'wallet.approve',{'proposal_id':proposal_id})[0] == 403
    assert rpc.send_count() == 0


def test_pair_and_revoke_tell_other_phone_even_when_noise_is_off(desktop):
    from tests.mobile.test_sealed_transport import pair
    first = pair(desktop)
    second = pair(desktop)
    status, notice = raw(*first,'notifications.poll',{'after':0})
    assert status == 200,notice
    events = notice['result']['events']
    assert len(events) == 1 and events[0]['security_notice'] == 'device_paired'
    assert events[0]['os_notification_allowed'] is False and 'audience_device_ids' not in events[0]
    assert notice['result']['badge'] == 1
    assert raw(*second,'notifications.poll',{'after':0})[1]['result']['events'] == []
    assert http(desktop[0],'/api/mobile/devices/revoke',{'device_id':second[3]['device_id']})[0] == 200
    cursor=notice['result']['next_after']
    status, revoked = raw(*first,'notifications.poll',{'after':cursor})
    assert status == 200 and [e['security_notice'] for e in revoked['result']['events']] == ['device_revoked']
    status, feed = raw(*first,'notifications.feed',{'records':True})
    assert status == 200 and [e['security_notice'] for e in feed['result']['events']] == ['device_paired','device_revoked']


def test_freeze_unfreeze_invalidates_reviews_and_accepted_requests_before_signer(money, monkeypatch):
    import pytest

    from core.vool_wallet import b58encode
    from core.wallet import signers
    from tests.mobile.test_phone_payments import PIN
    desktop, phone, wallet_id, _, rpc = money
    assert grant(money)[0] == 200
    params={'destination':b58encode(bytes([86])*32),'amount':'0.008','idempotency_key':secrets.token_hex(16)}
    status, review=raw(*phone,'wallet.preview',params);assert status == 200,review
    def cycle():
        assert raw(*phone,'wallet.freeze')[0] == 200
        status,value=http(desktop[0],'/api/mobile/settings',{'operation':'unfreeze','wallet_id':wallet_id,'credential':PIN})
        assert status == 200,value
    cycle()
    assert raw(*phone,'wallet.propose',{**params,'review_token':review['result']['review_token'], 'warnings_acknowledged':review['result']['warnings']})[0] == 403
    status, review=raw(*phone,'wallet.preview',params);assert status == 200,review
    confirmed={**params,'review_token':review['result']['review_token'], 'warnings_acknowledged':review['result']['warnings']}
    status,result=raw(*phone,'wallet.propose',confirmed);assert status == 200,result
    proposal_id=result['result']['proposal']['proposal_id']
    status, quote=http(desktop[0],'/api/wallet/quote',{'proposal_id':proposal_id});assert status == 200,quote
    cycle()
    assert raw(*phone,'wallet.preview',params)[0] == 403
    assert raw(*phone,'wallet.propose',confirmed)[0] == 403
    def must_not_sign(*args,**kwargs):pytest.fail('An invalidated phone request reached the signer')
    monkeypatch.setattr(signers,'signer_for',must_not_sign)
    status,refused=http(desktop[0],'/api/wallet/approve',{'proposal_id':proposal_id,'quote_id':quote['quote']['quote_id'],
        'quote_digest':quote['quote']['digest'],'pin':PIN})
    assert status == 403,refused
    assert rpc.send_count() == 0
