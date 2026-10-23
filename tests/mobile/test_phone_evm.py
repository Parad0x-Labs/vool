"""The phone's exact native-asset proposal path on existing EVM mainnet capabilities."""
import json
import secrets

import pytest

from tests.mobile.test_phone_listener_served import desktop as desktop
from tests.mobile.test_phone_listener_served import http
from tests.mobile.test_phone_notifications import call
from tests.mobile.test_phone_payments import PIN
from tests.mobile.test_sealed_transport import pair
from tests.wallet._rig_evm_native import ScriptedEvmNativeChain


@pytest.mark.parametrize('chain_id',[1,8453])
def test_native_evm_review_propose_caps_and_revoke_before_signing(desktop,monkeypatch,chain_id):
    from core.wallet import caller_binding, chains
    caller_binding.reset_for_tests();chains.invalidate_chain_identity()
    monkeypatch.setenv('VOOL_WALLET_ENABLED','1');monkeypatch.setenv('VOOL_WALLET_X402_ALLOW_LOOPBACK','1')
    monkeypatch.delenv('VOOL_WALLET_NETWORK_ENVIRONMENT',raising=False)
    network='eip155:'+str(chain_id);base=desktop[0]
    try:
        with ScriptedEvmNativeChain(chain_id=chain_id) as rpc:
            monkeypatch.setenv('VOOL_WALLET_RPC_URLS',json.dumps({network:rpc.url}))
            status,created=http(base,'/api/wallet/setup/create',{'network':network,'method':'pin','credential':PIN,'credential_confirmation':PIN,'creation_key':secrets.token_hex(16)})
            assert status==200,created
            wallet_id=created['setup']['wallet_id'];rpc.fund(created['setup']['address'],10**18)
            status,revealed=http(base,'/api/wallet/setup/reveal',{'wallet_id':wallet_id,'credential':PIN});assert status==200
            ack=revealed['backup']['ack_token'];revealed=None
            assert http(base,'/api/wallet/setup/acknowledge',{'wallet_id':wallet_id,'ack_token':ack})[0]==200
            phone=pair(desktop)
            status,enabled=http(base,'/api/mobile/settings',{'operation':'device_rules','device_id':phone[3]['device_id'],
                'rules':{'payment_propose':True,'wallet_view':True},'credential':PIN,
                'payment_cap':{'wallet_id':wallet_id,'network':network,'asset':'ETH','per_payment_minor':5*10**15,'daily_minor':8*10**15}})
            assert status==200,enabled
            params={'destination':'0x'+'5'*40,'amount':'0.001000000000000001','idempotency_key':secrets.token_hex(16)}
            status,review=call(*phone,'wallet.preview',params);assert status==200,review
            assert review['result']['amount']==params['amount']
            status,proposal=call(*phone,'wallet.propose',{**params,'review_token':review['result']['review_token'], 'warnings_acknowledged':review['result']['warnings']});assert status==200,proposal
            assert proposal['result']['display_amount']==params['amount'] and proposal['result']['network']==network
            assert call(*phone,'wallet.approve',{'proposal_id':proposal['result']['proposal']['proposal_id'],'pin':PIN})[0]==403
            assert http(base,'/api/mobile/devices/revoke',{'device_id':phone[3]['device_id']})[0]==200
            assert call(*phone,'wallet.preview',params)[0]==401
            assert not rpc.sent
    finally:chains.invalidate_chain_identity()
